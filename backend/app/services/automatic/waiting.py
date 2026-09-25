"""Releases that wait out the delay of their version, and the job that loads them once the wait is over
(the design notes; the rule itself is ``services/delay.py``).

**The gate** (``gate``) stands in the three load steps of the automatic (``scheduler._load``,
``series_loading._load_takes``, ``album_loading._load``), so it holds for planned searches, RSS and replacements alike.
A search the owner started himself ("search automatically now") passes, and so does every load he clicks: those never
come through a load step.

As in Radarr, Sonarr and Lidarr the releases of a version are gone through best first. The first one that has to wait
stops the version: it is kept here, and the ones ranked after it are kept as its fallbacks, so a worse release is not
loaded while a better one waits. Once the oldest of them has waited its delay, the best one known loads.

**Kept how:** the release as the feed gave it, encrypted, because its link carries the indexer's key or a passkey.
When the wait is over the kept releases of a title are judged again as RSS judges fetched releases
(``search.jobs.keep_found…``), against the files and the blocklist as they are then, and loaded through the same load
step. The indexer is not asked a second time: a release from RSS is no longer in the feed by then.

**Gone when:** a download for the title and version starts (for a series: one that shares an episode), the release
does not fit any more when it is judged again, the owner takes it off, or its title, version or indexer goes.

Log lines carry ids and counts only.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from collections.abc import Collection, Iterable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ... import crypto
from ...db import SessionLocal
from ...models import Indexer, PendingRelease, Title, VersionDefinition
from .. import delay as delay_rules
from .. import indexers
from .. import tags as tag_store
from ..search import jobs as search_jobs
from ..search import runner as search_runner
from . import clock

logger = logging.getLogger("nexcrate.automatic.waiting")

JOB_NAME = "waiting_releases"
INTERVAL_SECONDS = 60.0
#: The code a version's summary carries while its best release waits.
WAITING = "waiting_for_delay"
#: A release that is due but could not load for a passing reason (a running download, no client, a grab limit) is
#: looked at again after this long, not every minute.
RETRY = timedelta(minutes=15)
#: How many releases one load step keeps per version: the one that waits and its fallbacks.
MAX_KEPT = 10
#: Titles per round of the job.
TITLES_PER_ROUND = 20


# --- The release as it is kept -------------------------------------------------------------------------------------- #


def _pack(release: indexers.Release) -> str:
    values = dataclasses.asdict(release)
    values["published_at"] = release.published_at.isoformat() if release.published_at is not None else None
    return crypto.encrypt(json.dumps(values, ensure_ascii=False, separators=(",", ":")))


def _unpack(payload: str) -> indexers.Release | None:
    try:
        values = json.loads(crypto.decrypt(payload))
        if not isinstance(values, dict):
            return None
        known = {item.name for item in dataclasses.fields(indexers.Release)}
        values = {name: value for name, value in values.items() if name in known}
        published = values.get("published_at")
        values["published_at"] = datetime.fromisoformat(published) if isinstance(published, str) else None
        return indexers.Release(**values)
    except Exception:  # noqa: BLE001 - a row that cannot be read is dropped by the caller, never a crash of the job
        return None


# --- Reading -------------------------------------------------------------------------------------------------------- #


def _shares(row: PendingRelease, episode_ids: Collection[int] | None) -> bool:
    """Whether a kept release counts for these episodes: always for a movie or an album, for a series when it shares
    one (Sonarr keys its waiting releases the same way)."""
    if not episode_ids or not row.episode_ids:
        return True
    return not set(row.episode_ids).isdisjoint(episode_ids)


def _rows(db: OrmSession, title_id: int, definition_id: int) -> list[PendingRelease]:
    return list(
        db.scalars(
            select(PendingRelease)
            .where(PendingRelease.title_id == title_id, PendingRelease.version_definition_id == definition_id)
            .order_by(PendingRelease.id)
        )
    )


def _since(row: PendingRelease) -> datetime:
    return delay_rules.clock_start(row.published_at, row.first_seen_at)


def oldest_since(
    db: OrmSession, title_id: int, definition_id: int, episode_ids: Collection[int] | None = None
) -> datetime | None:
    starts = [_since(row) for row in _rows(db, title_id, definition_id) if _shares(row, episode_ids)]
    return min(starts) if starts else None


def rule_of(db: OrmSession, definition_id: int, title_id: int | None = None) -> delay_rules.Rule:
    """The version's rule for a title: a rule for one of the title's tags first."""
    stored = db.scalar(select(VersionDefinition.delay).where(VersionDefinition.id == definition_id))
    return delay_rules.for_title(stored, tag_store.title_tag_ids(db, title_id))


# --- The gate ------------------------------------------------------------------------------------------------------- #


def found_release(search: search_jobs.Search, release_key: str) -> tuple[Any, indexers.Release] | None:
    """The indexer and the release behind a key of a kept search."""
    for state in search.indexers:
        release = state.releases.get(release_key)
        if release is not None:
            return state.info, release
    return None


def gate(
    search: search_jobs.Search,
    release_key: str,
    definition_id: int,
    *,
    highest_quality: bool,
    score: int,
    now: datetime,
    episode_ids: Collection[int] | None = None,
    as_automatic: bool = False,
) -> datetime | None:
    """None when the automatic may load the release now, else the moment from which it may. ``as_automatic`` asks
    what the automatic would do, whoever started the search: for the hint in the owner's own search."""
    if search.user_invoked and not as_automatic:
        return None
    found = found_release(search, release_key)
    if found is None:
        return None
    info, release = found
    with SessionLocal() as db:
        rule = rule_of(db, definition_id, search.title_id)
        if not rule.waits_at_all:
            return None
        known = db.scalar(
            select(PendingRelease.first_seen_at).where(
                PendingRelease.title_id == search.title_id,
                PendingRelease.version_definition_id == definition_id,
                PendingRelease.indexer_id == info.indexer_id,
                PendingRelease.release_key == release_key,
            )
        )
        facts = delay_rules.Facts(
            protocol=info.protocol,
            published_at=release.published_at,
            first_seen_at=known or now,
            highest_quality=highest_quality,
            score=score,
            oldest_waiting_since=oldest_since(db, search.title_id, definition_id, episode_ids),
        )
    return delay_rules.waits_until(rule, facts, now)


@dataclasses.dataclass(frozen=True)
class Kept:
    """What a load step hands over of one release to keep."""

    release_key: str
    quality: str | None = None
    score: int | None = None
    episode_ids: tuple[int, ...] = ()
    episode_codes: tuple[str, ...] = ()


def keep(search: search_jobs.Search, definition_id: int, releases: Iterable[Kept], now: datetime) -> int:
    """Keep the release that waits and its fallbacks, best first; returns how many are kept for the version now.

    A release already kept keeps its row and the moment it was first seen. The moments the rows are due are worked out
    again afterwards, because an older release moves the clock of every other one.
    """
    kept = 0
    with SessionLocal() as db:
        if db.get(Title, search.title_id) is None or db.get(VersionDefinition, definition_id) is None:
            return 0
        existing = {(row.indexer_id, row.release_key): row for row in _rows(db, search.title_id, definition_id)}
        for wanted in list(releases)[:MAX_KEPT]:
            found = found_release(search, wanted.release_key)
            if found is None:
                continue
            info, release = found
            if db.get(Indexer, info.indexer_id) is None:
                continue
            row = existing.get((info.indexer_id, wanted.release_key))
            if row is None:
                row = PendingRelease(
                    title_id=search.title_id,
                    version_definition_id=definition_id,
                    indexer_id=info.indexer_id,
                    release_key=wanted.release_key,
                    first_seen_at=now,
                    due_at=now,
                    payload="",
                )
                db.add(row)
                existing[(info.indexer_id, wanted.release_key)] = row
            row.indexer_name = info.name[:100]
            row.release_title = release.title[:1024]
            row.protocol = info.protocol
            row.size_bytes = release.size_bytes
            row.quality = wanted.quality[:64] if wanted.quality else None
            row.score = wanted.score
            row.episode_ids = sorted(wanted.episode_ids) or None
            row.episode_codes = list(wanted.episode_codes) or None
            row.origin = search.origin if search.origin in ("search", "rss", "replacement") else "search"
            row.published_at = release.published_at
            row.payload = _pack(release)
            kept += 1
        db.flush()
        _set_due(db, search.title_id, definition_id)
        db.commit()
    if kept:
        logger.info("Title %d: %d releases wait for version %d", search.title_id, kept, definition_id)
    return kept


def _set_due(db: OrmSession, title_id: int, definition_id: int) -> None:
    """The moment each kept release of a version may load: the delay of its protocol after the oldest release that
    counts for it, itself included."""
    rule = rule_of(db, definition_id, title_id)
    rows = _rows(db, title_id, definition_id)
    for row in rows:
        starts = [_since(other) for other in rows if _shares(other, row.episode_ids)]
        row.due_at = min([*starts, _since(row)]) + timedelta(minutes=rule.minutes(row.protocol))


def rule_changed(db: OrmSession, definition_id: int) -> None:
    """After the owner changed the rule of a version: every release that waits for it gets its moment again. A rule
    that waits no longer makes them all due at once."""
    titles = set(
        db.scalars(select(PendingRelease.title_id).where(PendingRelease.version_definition_id == definition_id))
    )
    for title_id in sorted(titles):
        _set_due(db, title_id, definition_id)


# --- Going ---------------------------------------------------------------------------------------------------------- #


def forget_loaded(db: OrmSession, title_id: int, definition_id: int, episode_ids: Collection[int] | None = None) -> int:
    """A download for the title and version starts: what waited for it is done with. For a series only what shares an
    episode with the download. The caller commits."""
    gone = 0
    for row in _rows(db, title_id, definition_id):
        if _shares(row, episode_ids):
            db.delete(row)
            gone += 1
    return gone


def discard(row_id: int) -> bool:
    with SessionLocal() as db:
        row = db.get(PendingRelease, row_id)
        if row is None:
            return False
        db.delete(row)
        db.commit()
    return True


# --- When the wait is over ----------------------------------------------------------------------------------------- #


def run_job() -> None:
    try:
        run_round()
    except Exception:
        logger.exception("Background job %s failed", JOB_NAME)


def run_round() -> int:
    """Judge and load the titles with a release that is due. Returns how many titles were looked at."""
    now = clock.now()
    with SessionLocal() as db:
        due = list(
            db.scalars(
                select(PendingRelease.title_id)
                .where(PendingRelease.due_at <= now)
                .group_by(PendingRelease.title_id)
                .order_by(PendingRelease.title_id)
                .limit(TITLES_PER_ROUND)
            )
        )
    for title_id in due:
        try:
            process_title(title_id, now)
        except Exception:
            logger.exception("The waiting releases of title %d could not be judged", title_id)
            _put_off(title_id, now)
    return len(due)


def _put_off(title_id: int, now: datetime) -> None:
    with SessionLocal() as db:
        for row in db.scalars(
            select(PendingRelease).where(PendingRelease.title_id == title_id, PendingRelease.due_at <= now)
        ):
            row.due_at = now + RETRY
        db.commit()


def _states(db: OrmSession, rows: list[PendingRelease]) -> list[search_runner.IndexerState]:
    """The kept releases as the indexer states of a search; a row that cannot be read any more is removed."""
    moment = indexers.now()
    by_indexer: dict[int, dict[str, indexers.Release]] = {}
    for row in rows:
        release = _unpack(row.payload)
        if release is None:
            logger.warning("Waiting release %d cannot be read any more and is removed", row.id)
            db.delete(row)
            continue
        by_indexer.setdefault(row.indexer_id, {})[row.release_key] = release
    states: list[search_runner.IndexerState] = []
    for indexer_id in sorted(by_indexer):
        indexer = db.get(Indexer, indexer_id)
        if indexer is None:
            continue
        info = dataclasses.replace(search_jobs._indexer_info(indexer, moment), api_key="")
        states.append(search_runner.IndexerState(info=info, state="done", releases=by_indexer[indexer_id]))
    return states


def process_title(title_id: int, now: datetime) -> int:
    """Judge every kept release of a title again and load what the load step takes. Returns how many loads started.

    Afterwards: a release that is no candidate any more is removed (it does not fit, is not better than the file, or
    is blocked), one that still is but did not load is looked at again after ``RETRY``.
    """
    from . import rss  # late: rss imports the load steps, and those import this module

    with SessionLocal() as db:
        title = db.get(Title, title_id)
        rows = list(db.scalars(select(PendingRelease).where(PendingRelease.title_id == title_id)))
        if title is None or not rows:
            return 0
        kind = title.kind
        states = _states(db, rows)
        episode_ids = sorted({episode_id for row in rows for episode_id in (row.episode_ids or [])})
        origin = min(rows, key=lambda row: row.id).origin
        db.commit()
    if not states:
        return 0
    loaded, still = rss.judge_kept(kind, title_id, states, episode_ids, origin, now)
    with SessionLocal() as db:
        left = list(db.scalars(select(PendingRelease).where(PendingRelease.title_id == title_id)))
        removed = 0
        for row in left:
            if (row.version_definition_id, row.release_key) not in still:
                db.delete(row)
                removed += 1
            elif row.due_at <= now:
                row.due_at = now + RETRY
        db.commit()
    logger.info(
        "Title %d: %d waiting releases judged again, %d loads started, %d removed", title_id, len(rows), loaded, removed
    )
    return loaded


# --- What the owner's own search says about waiting ----------------------------------------------------------- #


def annotate(search_id: str, body: dict[str, Any]) -> None:
    """Adds ``would_wait_until`` to every release and version of a search answer that fits: the moment until which the
    automatic would let it wait, or null. The owner's load never waits; this only tells him what the automatic would
    have done with the same release. Changes the answer in place, so it must be a copy (``jobs.snapshot``)."""
    search = search_jobs.kept(search_id)
    if search is None:
        return
    now = clock.now()
    album = body.get("kind") == "album"
    decision = next(iter(body.get("versions") or []), None) if album else None
    for release in body.get("releases") or []:
        if album:
            verdict = release.get("verdict") or {}
            release["would_wait_until"] = None
            if decision is None or decision.get("version_id") is None or not verdict.get("accepted"):
                continue
            release["would_wait_until"] = gate(
                search,
                release["release_key"],
                int(decision["version_id"]),
                highest_quality=not verdict.get("for_now"),
                score=0,
                now=now,
                as_automatic=True,
            )
            continue
        for placed in release.get("versions") or []:
            placed["would_wait_until"] = None
            result = placed.get("result") or placed.get("series_result") or {}
            if placed.get("rank") is None or not result.get("accepted"):
                continue
            placed["would_wait_until"] = gate(
                search,
                release["release_key"],
                int(placed["version_id"]),
                highest_quality=bool(placed.get("highest_quality")),
                score=int(result.get("score") or 0),
                now=now,
                as_automatic=True,
            )


# --- The owner does not want to wait ------------------------------------------------------------------------------ #


class NotWaiting(Exception):
    """The row is gone: loaded, removed, or never there."""


async def load_now(row_id: int) -> int:
    """Load one waiting release at once, as a load the owner clicks: no delay, and the checks of ``loading.grab``.
    Returns the download. Raises ``NotWaiting`` and ``loading.LoadError``."""
    from ..downloads import loading

    with SessionLocal() as db:
        row = db.get(PendingRelease, row_id)
        title = db.get(Title, row.title_id) if row is not None else None
        if row is None or title is None:
            raise NotWaiting
        kind, title_id, definition_id, key = title.kind, row.title_id, row.version_definition_id, row.release_key
        episode_ids = list(row.episode_ids or [])
        codes = frozenset(row.episode_codes or [])
        states = _states(db, [row])
        db.commit()
    if not states:
        raise NotWaiting
    if kind == "series":
        search = search_jobs.keep_found_series(title_id, origin="manual", states=states, episode_ids=episode_ids)
    elif kind == "album":
        search = search_jobs.keep_found_album(title_id, origin="manual", states=states)
    else:
        search = search_jobs.keep_found(title_id, origin="manual", states=states)
    if search is None:
        raise NotWaiting
    try:
        only = (codes or None) if kind == "series" else None
        return await loading.grab(search.search_id, key, definition_id, [], only=only)
    finally:
        search_jobs.drop(search.search_id)
