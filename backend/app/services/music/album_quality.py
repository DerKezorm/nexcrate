"""The quality step of an album on disk, and whether the music profile could improve it (the design notes,
decision 22). Only shown: nothing is searched for because of it, that comes with M3 and M5.

The step of an album is the worst step of its files (``track_files.quality``, as the connection names it). It is kept
in ``versions.quality``; ``versions.cutoff_not_met`` says that it lies below the profile's target, and the state
follows: ``upgrade`` instead of ``available``. Without a profile nothing is below anything.

Judged where it can change: when a connection writes the files of an album, when the profile is saved or removed, and
once after every start, in the thread that judges the owner's own files (albums imported before M2 have no step yet).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session as OrmSession

from ...db import SessionLocal
from ...models import Title, TrackFile, Version, utcnow
from ..profiles import store as profile_store
from ..releases import music_decision as md
from ..releases import music_qualities as mq
from . import store

logger = logging.getLogger("nexcrate.music")

#: Versions per transaction: the write lock is held for one chunk at a time.
CHUNK = 100
#: The pause between two chunks, as in ``judging``: 3,249 albums held the lock for 33 s in chunks of 500 (22.09.2026).
YIELD_SECONDS = 0.25
#: Replaced in the tests, like ``judging.sleep``.
sleep = time.sleep
#: The states a file decides. ``downloading`` comes from the connection's queue and ``problem`` from a finding.
_FILE_STATES = ("available", "upgrade", "incomplete")

_lock = threading.Lock()


def rules_of(db: OrmSession) -> dict[str, Any] | None:
    """The rules of the music profile, or None: no music version yet, no profile, or rules this code cannot read."""
    definition = store.definition(db)
    if definition is None:
        return None
    profile = profile_store.of_version(db, definition.id)
    return profile.rules if profile is not None and md.usable(profile.rules) else None


def step_of_files(qualities: Iterable[str | None]) -> str | None:
    """The step of an album with these files; None without files."""
    steps = [mq.step_of_lidarr(quality) for quality in qualities]
    return mq.lowest(steps) if steps else None


def apply(version: Version, step: str | None, rules: dict[str, Any] | None) -> bool:
    """Write step, verdict and state onto the version. True when anything changed."""
    before = (version.quality, bool(version.cutoff_not_met), version.state)
    version.quality = step
    version.cutoff_not_met = bool(version.has_file) and step is not None and md.upgrade_possible(step, rules)
    if version.state in _FILE_STATES:
        version.state = store.state_of(version)
    return before != (version.quality, version.cutoff_not_met, version.state)


def judge_all() -> tuple[int, int]:
    """Every album version with a file, in chunks. Returns how many were judged and how many changed."""
    judged = changed = 0
    after = 0
    with _lock:
        while True:
            moment = utcnow()
            with SessionLocal() as db:
                # A write first, though it matches no row: it takes SQLite's write lock before anything is read, so
                # files a connection writes meanwhile are never judged by what stood there before.
                db.execute(
                    update(Version).where(Version.id < 1).values(cutoff_not_met=Version.cutoff_not_met),
                    execution_options={"synchronize_session": False},
                )
                rules = rules_of(db)
                rows = list(
                    db.scalars(
                        select(Version)
                        .where(
                            Version.id > after,
                            Version.has_file.is_(True),
                            Version.title_id.in_(select(Title.id).where(Title.kind == "album")),
                        )
                        .order_by(Version.id)
                        .limit(CHUNK)
                    )
                )
                if not rows:
                    db.rollback()
                    return judged, changed
                after = rows[-1].id
                qualities: dict[int, list[str | None]] = defaultdict(list)
                found = db.execute(
                    select(TrackFile.version_id, TrackFile.quality)
                    .where(TrackFile.version_id.in_([row.id for row in rows]))
                    .distinct()
                )
                for version_id, quality in found.tuples():
                    qualities[version_id].append(quality)
                for row in rows:
                    judged += 1
                    if apply(row, step_of_files(qualities.get(row.id, [])), rules):
                        row.updated_at = moment
                        changed += 1
                db.commit()
            # Let a waiting writer in between two chunks; why, is at ``judging.YIELD_SECONDS``.
            sleep(YIELD_SECONDS)


def after_profile_change() -> None:
    """The albums follow the new rules. A failure is logged and left to the next start, which judges everything."""
    started = time.perf_counter()
    try:
        judged, changed = judge_all()
    except Exception:
        logger.exception("Judging the albums after a change of the music profile failed")
        return
    logger.info(
        "Music profile changed: %d albums judged in %dms, %d changed",
        judged,
        (time.perf_counter() - started) * 1000,
        changed,
    )
