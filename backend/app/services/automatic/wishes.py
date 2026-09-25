"""Search wishes of other programs (answer 3, changed on 22.09.2026).

A program that requests a title with ``search_now``, or asks ``/api/v1/.../search``, sets ``titles.search_wish_at``.
Since 22.09.2026 a wish searches **at once**, as "search automatically now" in nexcrate does, for movies, series and
albums, **also with the automatic of its kind off** (the owner's answer): its own runner here, not the automatic's
order, budget, 80 percent stop or hourly cap. At most ``MAX_RUNNING`` wished searches run at the same time; the rest
start right after, oldest wish first (the owner's choice of 22.09.2026, for a program that asks 50 titles at once).
Pauses of an indexer and all of its request limit still hold (``budget.rss_stop``), and so does its grab limit when
loading. The search may then load with the switch off. After the search the wish is fulfilled; a title that wants
nothing any more loses its wish.
"""

from __future__ import annotations

import logging
import threading

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session as OrmSession

from ...db import SessionLocal
from ...models import Title
from ..search import jobs as search_jobs
from . import planning

logger = logging.getLogger("nexcrate.automatic")

#: The kinds a wish counts for: every kind since 22.09.2026 (albums waited before and were never searched with the
#: music switch off, the owner's finding 21 in erfahrungen-nexbeat.md).
KINDS = ("movie", "series", "album")
#: Wished searches at the same time; the rest start as one ends (the owner's answer of 22.09.2026).
MAX_RUNNING = 5
JOB_NAME = "search_wishes"
#: How often the runner looks for wishes; a request also starts it at once (``kick``).
INTERVAL_SECONDS = 5.0
_run_lock = threading.Lock()
#: Only for the tests, which replace ``kick``.
kicks: list[int] = []


def wished(db: OrmSession, title_id: int) -> bool:
    return db.scalar(select(Title.search_wish_at).where(Title.id == title_id)) is not None


def early(title: Title, reason: str | None) -> bool:
    """Whether a wish lets the title start before its planned time: not while it waits for the budget."""
    return title.search_wish_at is not None and title.kind in KINDS and reason != "limit"


def due(now: object) -> object:
    """The condition of a wished title that may start now."""
    return and_(
        Title.search_wish_at.is_not(None),
        Title.kind.in_(KINDS),
        Title.next_search_reason.in_(planning.SEARCHING_REASONS),
        or_(Title.next_search_reason != "limit", Title.next_search_at <= now),
    )


def fulfil(title_id: int) -> None:
    with SessionLocal() as db:
        db.execute(update(Title).where(Title.id == title_id).values(search_wish_at=None))
        db.commit()


def clear_unwanted() -> int:
    """Wishes of titles the plan says want nothing: they would never start. Returns how many went."""
    with SessionLocal() as db:
        result = db.execute(
            update(Title)
            .where(
                Title.search_wish_at.is_not(None),
                or_(Title.next_search_reason.is_(None), Title.next_search_reason.not_in(planning.SEARCHING_REASONS)),
            )
            .values(search_wish_at=None)
        )
        db.commit()
    return int(result.rowcount or 0)


def _wished_ids() -> list[int]:
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(Title.id)
                .where(Title.search_wish_at.is_not(None), Title.kind.in_(KINDS))
                .order_by(Title.search_wish_at, Title.id)
            )
        )


def _drop(title_id: int) -> None:
    with SessionLocal() as db:
        db.execute(update(Title).where(Title.id == title_id).values(search_wish_at=None))
        db.commit()


def start_wished() -> int:
    """Start wished searches up to ``MAX_RUNNING`` at once, oldest wish first. Returns how many started.

    A wish that wants nothing any more goes; one whose indexers are all paused or used up waits for the next look.
    """
    from . import scheduler

    if not _run_lock.acquire(blocking=False):
        return 0
    try:
        started = 0
        waiting = []
        running = 0
        for title_id in _wished_ids():
            if search_jobs.running_search(title_id) is not None:
                running += 1
            else:
                waiting.append(title_id)
        for title_id in waiting:
            if running >= MAX_RUNNING:
                break
            try:
                search_id = scheduler.search_now(title_id, switch=False)
            except scheduler.NothingWanted:
                _drop(title_id)
                logger.info("Title %d: the wish of a program is dropped, the title wants nothing", title_id)
                continue
            except scheduler.TitleMissing:
                continue
            except search_jobs.SearchRunning:
                running += 1
                continue
            except (search_jobs.NoIndexers, search_jobs.SearchBusy):
                continue
            running += 1
            started += 1
            logger.info("Title %d: a program's wish searches now (%s)", title_id, search_id)
        return started
    finally:
        _run_lock.release()


def run_job() -> None:
    """The background job: every few seconds, and at once after a request (``kick``)."""
    start_wished()


def kick() -> None:
    """A request set a wish: start it now, in a thread of its own, so the answer does not wait for the indexers."""
    threading.Thread(target=start_wished, name="search-wishes", daemon=True).start()
