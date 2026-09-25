"""Upgrades held back: paused for what was there before, and at most so many a day (the owner's wish of 24.09.2026,
the design notes).

An owner's SABnzbd had loaded upgrades of files already there for two days. Two settings bound the automatic upgrades:

* **Paused** (``upgrades_paused_since``, an ISO moment, empty for not paused): no automatic upgrade of a file that was
  there at that moment. A file that arrives later is upgraded as usual, and so is every file once the pause ends. His
  words: the pause holds "only for what was there before the click; future movies and series are watched and upgraded
  as usual". When a file arrived: an episode file's ``added_at``; for a movie or an album version the newest history
  event that brought a file (``ARRIVALS``). A file without a known arrival counts as there before.
* **Per day** (``upgrades_per_day``, 0 for no limit): automatic grabs of an upgrade in the last 24 hours; with as many
  as allowed, no further upgrade is searched or loaded until the oldest leaves the window. Missing titles never count
  and are never held.

What counts as an upgrade (``loading.is_upgrade``): a movie or album version that has a file, a series release that
fills no episode. The owner's own loads (origin ``manual``) are never held and never counted: he chose them.
Planning asks ``Guard.blocks`` for every file that could be upgraded (a blocked file is not wanted), and the final
load asks it again (``loading.grab``), for RSS and packs that planning never saw.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from ...db import get_setting, set_setting
from ...models import HistoryEntry

SETTING_PAUSED_SINCE = "upgrades_paused_since"
SETTING_PER_DAY = "upgrades_per_day"
#: The window of the daily limit.
WINDOW = timedelta(hours=24)
#: The most a day may be set to.
PER_DAY_MAX = 10_000
#: History events that bring a movie's or an album's file.
ARRIVALS = ("imported", "taken_over", "found_on_disk", "restored", "album_filed", "file_restored")
#: Origins of automatic loads; ``manual`` is the owner's.
AUTOMATIC = ("search", "rss", "replacement")


def aware(moment: datetime | None) -> datetime | None:
    """SQLite gives naive times back; nexcrate stores UTC."""
    if moment is None:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


@dataclass(frozen=True)
class Guard:
    paused_since: datetime | None = None
    per_day: int = 0
    #: Automatic upgrade grabs in the last 24 hours.
    used: int = 0

    @property
    def limit_reached(self) -> bool:
        return self.per_day > 0 and self.used >= self.per_day

    def blocks(self, arrived: datetime | None) -> bool:
        """Whether an automatic upgrade of a file that arrived then is held back now."""
        if self.limit_reached:
            return True
        if self.paused_since is None:
            return False
        moment = aware(arrived)
        return moment is None or moment <= self.paused_since


def paused_since(db: OrmSession) -> datetime | None:
    stored = get_setting(db, SETTING_PAUSED_SINCE, "")
    try:
        return aware(datetime.fromisoformat(stored)) if stored else None
    except ValueError:
        return None


def per_day(db: OrmSession) -> int:
    stored = get_setting(db, SETTING_PER_DAY, "0")
    return int(stored) if stored.isdigit() else 0


def used(db: OrmSession, now: datetime) -> int:
    """Automatic upgrade grabs in the last 24 hours, from their history lines."""
    count = 0
    for data in db.scalars(
        select(HistoryEntry.data).where(HistoryEntry.event == "grabbed", HistoryEntry.at >= now - WINDOW)
    ):
        if isinstance(data, dict) and data.get("upgrade") is True and data.get("origin") in AUTOMATIC:
            count += 1
    return count


def load(db: OrmSession, now: datetime) -> Guard:
    limit = per_day(db)
    return Guard(paused_since=paused_since(db), per_day=limit, used=used(db, now) if limit > 0 else 0)


def arrivals(db: OrmSession, version_ids: Collection[int]) -> dict[int, datetime]:
    """Version id to the newest history event that brought it a file."""
    found: dict[int, datetime] = {}
    ids = sorted(set(version_ids))
    for start in range(0, len(ids), 900):
        chunk = ids[start : start + 900]
        for version_id, moment in db.execute(
            select(HistoryEntry.version_id, func.max(HistoryEntry.at))
            .where(HistoryEntry.version_id.in_(chunk), HistoryEntry.event.in_(ARRIVALS))
            .group_by(HistoryEntry.version_id)
        ).tuples():
            if version_id is not None and moment is not None:
                found[int(version_id)] = aware(moment) or moment
    return found


def pause(db: OrmSession, now: datetime) -> None:
    """Pause from now on; a pause already running keeps its moment. The caller commits."""
    if paused_since(db) is None:
        set_setting(db, SETTING_PAUSED_SINCE, now.isoformat())


def resume(db: OrmSession) -> None:
    set_setting(db, SETTING_PAUSED_SINCE, "")


def set_per_day(db: OrmSession, value: int) -> None:
    set_setting(db, SETTING_PER_DAY, str(max(0, min(PER_DAY_MAX, value))))
