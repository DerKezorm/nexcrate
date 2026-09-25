"""The type TMDB suggests for every series in the library, and a run that asks for it (B5).

A1 recognises anime when a series is added; the series that were there before kept their type (standard, from Sonarr
or from the add before A1). Here every series gets TMDB's suggestion stored as ``titles.type_proposed``: anime by the
keyword or Animation in Japanese (``anime.looks_like_anime``), daily for TMDB's types News and Talk Show, standard
otherwise. Every TMDB refresh writes it; the run fills it for the series that have none yet, or for all on request.

The proposals are the series whose stored type differs from the suggestion. A series a live Sonarr connection feeds is
left out: it takes Sonarr's type on every run (decision 2 of this plan). Nothing is changed here; the owner takes the
proposals he wants through ``PUT /api/library/series-type``.

Log lines carry ids and counts, never titles.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from ...db import SessionLocal
from ...models import Title
from .. import library, tmdb
from . import tmdb_series

logger = logging.getLogger("nexcrate.series")


@dataclass
class Run:
    state: str = "idle"
    done: int = 0
    total: int = 0
    failed: int = 0
    #: Why a run stopped early: ``tmdb_token_missing`` or a TMDB error code.
    problem: str | None = None


_lock = threading.Lock()
_run = Run()


def state() -> dict[str, Any]:
    with _lock:
        return {
            "state": _run.state,
            "done": _run.done,
            "total": _run.total,
            "failed": _run.failed,
            "problem": _run.problem,
        }


def _same(stored: str | None, proposed: str | None) -> bool:
    return (stored or "standard") == (proposed or "standard")


def proposals(db: Any) -> list[dict[str, Any]]:
    """The series whose type differs from TMDB's suggestion, by title; a series Sonarr feeds is left out."""
    rows = list(
        db.scalars(
            select(Title)
            .where(Title.kind == "series", Title.type_proposed.is_not(None))
            .order_by(Title.sort_key, Title.id)
        )
    )
    differing = [row for row in rows if not _same(row.series_type, row.type_proposed)]
    fed = library.fed_series_ids(db, [row.id for row in differing])
    return [
        {
            "title_id": row.id,
            "title": row.title,
            "year": row.year,
            "series_type": row.series_type or "standard",
            "proposed": row.type_proposed,
        }
        for row in differing
        if row.id not in fed
    ]


def unknown_count(db: Any) -> int:
    """Series without a suggestion yet: added before B5 and not refreshed since."""
    return len(list(db.scalars(select(Title.id).where(Title.kind == "series", Title.type_proposed.is_(None)))))


def claim(everything: bool) -> list[tuple[int, int]] | None:
    """The series the run asks for, with the run marked as running; None when one is running already. The caller
    starts ``work`` in the server's event loop, where TMDB's shared client lives."""
    with SessionLocal() as db:
        statement = select(Title.id, Title.tmdb_id).where(Title.kind == "series", Title.tmdb_id.is_not(None))
        if not everything:
            statement = statement.where(Title.type_proposed.is_(None))
        wanted = [(int(title_id), int(tmdb_id)) for title_id, tmdb_id in db.execute(statement.order_by(Title.id))]
    with _lock:
        if _run.state == "running":
            return None
        _run.state, _run.done, _run.total, _run.failed, _run.problem = "running", 0, len(wanted), 0, None
    return wanted


def _store(title_id: int, proposed: str) -> None:
    with SessionLocal() as db:
        title = db.get(Title, title_id)
        if title is not None and title.type_proposed != proposed:
            title.type_proposed = proposed
            db.commit()


async def work(wanted: list[tuple[int, int]]) -> None:
    stored, token = await asyncio.to_thread(tmdb.token_state)
    locale = await asyncio.to_thread(tmdb.account_locale)
    if not stored or not token:
        with _lock:
            _run.state, _run.problem = "done", "tmdb_token_missing"
        logger.info("Type proposals: no TMDB token, nothing asked")
        return
    for title_id, tmdb_id in wanted:
        try:
            proposed = await tmdb_series.proposed_type_of(token, tmdb_id, locale)
            await asyncio.to_thread(_store, title_id, proposed)
        except tmdb.TmdbError as exc:
            with _lock:
                _run.failed += 1
            if exc.code != "not_found":
                with _lock:
                    _run.state, _run.problem = "done", exc.code
                logger.info("Type proposals stopped at a TMDB error: %s", exc.code)
                return
        with _lock:
            _run.done += 1
    with _lock:
        _run.state = "done"
    logger.info("Type proposals: %d series asked, %d unknown to TMDB", len(wanted), _run.failed)
