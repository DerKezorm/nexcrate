"""The change marker of the library (N13).

A program fetches the whole library once and afterwards only what changed since a number. Radarr and Sonarr have
nothing like it, so a program pulls their whole library every minute.

**How.** A background job renders every title the way ``/api/v1/titles`` lists it and hashes that. A title whose hash
differs from the stored one gets the next running number; a title that went away keeps its row as a gravestone with a
number of its own. So the number moves exactly when what a program would read has changed, whoever changed it and by
which way: an import, a download, the owner, or a statement that never touches the ORM.

⚠️ Deliberately no database trigger and no ``updated_at``: both hang on every writer remembering them, and a marker
that misses one change makes a program show a stale badge for good, with nobody noticing. The price is a delay: a
change shows after the next look, some seconds later. The same look writes ``title.*`` and ``version.*`` events into
the feed, in the same transaction as the numbers.

⚠️ The job only reads the big tables and writes its own small one in one short transaction, so nothing waits for it
(N37). After a look that took long the next one waits in proportion.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session as OrmSession

from ... import db as database
from ...models import Artist, Title, TitleChange, utcnow
from . import TITLE_KINDS, events, titles

logger = logging.getLogger("nexcrate.api_v1")

JOB_NAME = "api_title_changes"
INTERVAL_SECONDS = 10
#: After a look the next one waits this many times as long as the look took, at least the interval.
REST_FACTOR = 20
#: Titles rendered at once.
CHUNK = 2000
PAGE_DEFAULT = 500
PAGE_MAX = 2000
#: How long a gravestone stays. A program that comes back later than this starts over (``marker_too_old``).
GRAVESTONE_DAYS = 90

SETTING_SEQ = "api_change_seq"
#: The highest number of a gravestone that was cleared away: a marker below it may have missed a removal.
SETTING_FLOOR = "api_change_floor"

_not_before = 0.0


def reset() -> None:
    global _not_before
    _not_before = 0.0


def fingerprint(item: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(item, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _number(db: OrmSession, key: str) -> int:
    stored = database.get_setting(db, key, "0")
    return int(stored) if stored.isdigit() else 0


def look(db: OrmSession, moment: datetime | None = None) -> int:
    """Compare every title with its stored hash and number what changed. Returns how many numbers it gave; commits."""
    now = moment or utcnow()
    known = {row.title_id: row for row in db.scalars(select(TitleChange))}
    live_ids = list(
        db.scalars(select(Title.id).where(Title.kind.in_(TITLE_KINDS), titles.NAMEABLE).order_by(Title.id))
    )
    # Artists stand under the negative of their row number; the collective one is no artist.
    live_ids += [-artist_id for artist_id in db.scalars(select(Artist.id).where(Artist.is_various.is_(False)))]
    seq = _number(db, SETTING_SEQ)
    given = 0

    # Removals first: a title that was removed and added again between two looks has a new row number, and a program
    # must read "gone" before "there".
    live = set(live_ids)
    # The very first look numbers a library that was there before: that is no news for the event feed.
    seeding = not known
    filled = 0
    for title_id, row in known.items():
        if title_id not in live and row.removed_at is None:
            seq += 1
            given += 1
            row.seq = seq
            row.removed_at = now
            row.fingerprint = ""
            events.title_removed(db, row.kind, row.ref, seq, now)

    for start in range(0, len(live_ids), CHUNK):
        chunk = live_ids[start : start + CHUNK]
        for title_id, item in titles.items(db, chunk).items():
            digest = fingerprint(item)
            row = known.get(title_id)
            versions = events.version_ids(item)
            if row is not None and row.removed_at is None and row.fingerprint == digest:
                if row.versions is None:
                    # The first look after V3: what the event feed compares the versions with next time.
                    row.versions = versions
                    filled += 1
                continue
            seq += 1
            given += 1
            if not seeding:
                is_new = row is None or row.removed_at is not None
                events.title_seen(db, item, title_id, seq, None if row is None else row.versions, is_new, now)
            if row is None:
                db.add(
                    TitleChange(
                        title_id=title_id,
                        kind=item["kind"],
                        ref=item["ref"],
                        seq=seq,
                        fingerprint=digest,
                        versions=versions,
                    )
                )
            else:
                row.kind, row.ref = item["kind"], item["ref"]
                row.seq, row.fingerprint, row.removed_at, row.versions = seq, digest, None, versions

    cleared = _clear_gravestones(db, now)
    if given or cleared or filled:
        database.set_setting(db, SETTING_SEQ, str(seq))
        db.commit()
    else:
        db.rollback()
    return given


def _clear_gravestones(db: OrmSession, now: datetime) -> int:
    limit = now - timedelta(days=GRAVESTONE_DAYS)
    highest = db.scalar(select(func.max(TitleChange.seq)).where(TitleChange.removed_at < limit))
    if highest is None:
        return 0
    database.set_setting(db, SETTING_FLOOR, str(max(int(highest), _number(db, SETTING_FLOOR))))
    result = db.execute(delete(TitleChange).where(TitleChange.removed_at < limit))
    return int(result.rowcount or 0)


def run_job() -> None:
    """One look, unless the last one took so long that it is not due yet."""
    global _not_before
    if time.monotonic() < _not_before:
        return
    started = time.monotonic()
    with database.SessionLocal() as db:
        given = look(db)
    took = time.monotonic() - started
    _not_before = time.monotonic() + max(0.0, took * REST_FACTOR - INTERVAL_SECONDS)
    if given:
        logger.debug("The library marker moved for %d titles in %.2fs", given, took)


class MarkerTooOld(Exception):
    """The marker lies below a removal that was cleared away; the program has to start over at 0."""


def since(db: OrmSession, after: int, kind: str | None, limit: int) -> dict[str, Any]:
    """What changed after a number, oldest change first: titles as listed, and what went away.

    ``next_after`` is the number to ask with next; ``more`` says whether to ask again at once. ``after=0`` is the
    whole library. Raises ``MarkerTooOld``.
    """
    if 0 < after < _number(db, SETTING_FLOOR):
        raise MarkerTooOld
    size = max(1, min(limit, PAGE_MAX))
    conditions = [TitleChange.seq > after]
    if kind:
        conditions.append(TitleChange.kind == kind)
    if after == 0:
        # Whoever starts over needs no gravestones.
        conditions.append(TitleChange.removed_at.is_(None))
    rows = list(db.scalars(select(TitleChange).where(*conditions).order_by(TitleChange.seq).limit(size + 1)))
    more = len(rows) > size
    rows = rows[:size]
    rendered = titles.items(db, [row.title_id for row in rows if row.removed_at is None])
    changed: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for row in rows:
        if row.removed_at is not None:
            removed.append({"kind": row.kind, "ref": row.ref, "seq": row.seq})
            continue
        item = rendered.get(row.title_id)
        if item is None:
            # Gone since the last look; the next look gives it its gravestone.
            continue
        changed.append({"seq": row.seq, **item})
    return {
        "items": changed,
        "removed": removed,
        "next_after": rows[-1].seq if rows else after,
        "more": more,
        "latest": _number(db, SETTING_SEQ),
    }
