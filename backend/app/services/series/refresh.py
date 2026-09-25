"""Refreshing series from TMDB (decision 18), part of the TMDB refresh job.

Running series (``Returning Series``, ``In Production``, ``Planned``, ``Pilot``, or no status) are due after one day,
ended and canceled ones after 30 days. Every series is refreshed, whether a Sonarr connection feeds it or not: TMDB is
the anchor, Sonarr delivers no series data. A refresh fetches every season, then TheXEM's numbers of the series and,
once a day, TheXEM's scene names (decision 15).

A series TMDB no longer knows keeps its data. Any other TMDB error stops the round; the next one goes on.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, or_, select

from ...db import SessionLocal
from ...models import Title, utcnow
from .. import tmdb
from ..automatic import clock, planning
from . import numbering, store, tmdb_series, watching, xem

logger = logging.getLogger("nexcrate.tmdb")

RUNNING_AFTER = timedelta(days=1)
ENDED_AFTER = timedelta(days=30)
ENDED_STATUSES = ("Ended", "Canceled")


def due_series(limit: int) -> list[tuple[int, int]]:
    moment = tmdb.now()
    ended = Title.series_status.in_(ENDED_STATUSES)
    with SessionLocal() as db:
        rows = db.execute(
            select(Title.id, Title.tmdb_id)
            .where(
                Title.kind == "series",
                or_(
                    Title.tmdb_refreshed_at.is_(None),
                    and_(ended, Title.tmdb_refreshed_at < moment - ENDED_AFTER),
                    and_(~ended | Title.series_status.is_(None), Title.tmdb_refreshed_at < moment - RUNNING_AFTER),
                ),
            )
            .order_by(Title.tmdb_refreshed_at.asc().nulls_first(), Title.id)
            .limit(limit)
        ).all()
    return [(row.id, row.tmdb_id) for row in rows]


def _apply(title_id: int, data: tmdb_series.SeriesData | None) -> bool:
    moment = utcnow()
    with SessionLocal() as db:
        title = db.get(Title, title_id)
        if title is None or title.kind != "series":
            return False
        if data is None:
            title.tmdb_refreshed_at = moment
        else:
            store.apply_series(db, title, data, moment, watching.today())
            # New episodes from TMDB are planned at once (decision 24). Not in apply_series: the
            # Sonarr import calls it per series, and the round plans those versions anyway.
            db.flush()
            planning.replan(db, [title.id], clock.now())
        db.commit()
    return data is not None


def _chosen_group(title_id: int) -> str | None:
    with SessionLocal() as db:
        title = db.get(Title, title_id)
        return title.episode_group_id if title is not None else None


def _store_group(title_id: int, group_id: str, places: dict[int, tuple[int, int]]) -> None:
    with SessionLocal() as db:
        title = db.get(Title, title_id)
        if title is None or title.episode_group_id != group_id:
            return
        numbering.store_group(db, title, group_id, places, utcnow())
        db.commit()


async def _group(token: str, title_id: int) -> None:
    """A chosen TMDB episode group follows TMDB's episodes as well (S3, decision 11). A failure keeps the numbers."""
    group_id = await asyncio.to_thread(_chosen_group, title_id)
    if group_id is None:
        return
    try:
        group = await tmdb_series.fetch_episode_group(token, group_id, refresh=True)
    except tmdb.TmdbError as exc:
        logger.info("TMDB episode group of title %d not refreshed: %s", title_id, exc.code)
        return
    await asyncio.to_thread(_store_group, title_id, group_id, numbering.group_places(group))


def _rename_tba(title_id: int) -> None:
    """Files named with TBA get their title once TMDB has one (decision 27). Never stops the
    round."""
    from ..downloads import tba

    try:
        tba.rename_title(title_id)
    except Exception:
        logger.exception("Renaming the TBA files of title %d failed", title_id)


async def _xem(step: Coroutine[Any, Any, object]) -> None:
    """TheXEM's numbers follow TMDB's episodes (S3). Its own failures are states; anything else is logged and never
    stops the TMDB round."""
    try:
        await step
    except Exception:
        logger.exception("TheXEM step failed")


async def refresh_series(token: str, limit: int) -> int:
    due = await asyncio.to_thread(due_series, limit)
    if not due:
        return 0
    locale = await asyncio.to_thread(tmdb.account_locale)
    refreshed = 0
    for title_id, tmdb_id in due:
        try:
            data = await tmdb_series.fetch_series(token, tmdb_id, locale, refresh=True)
        except tmdb.TmdbError as exc:
            if exc.code == "not_found":
                logger.warning(
                    "TMDB no longer knows series %d of title %d; the title keeps its data", tmdb_id, title_id
                )
                await asyncio.to_thread(_apply, title_id, None)
                continue
            logger.warning("TMDB series refresh stopped after %d titles: %s", refreshed, exc.code)
            break
        if await asyncio.to_thread(_apply, title_id, data):
            refreshed += 1
            await _group(token, title_id)
            await _xem(xem.refresh_title(title_id, force=True))
            await asyncio.to_thread(_rename_tba, title_id)
    if refreshed:
        await _xem(xem.refresh_names())
    logger.info("TMDB series refresh: %d of %d due series refreshed", refreshed, len(due))
    return refreshed
