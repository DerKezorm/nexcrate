"""The request budget per indexer and the escalation after failures (C4, decisions 7 and 8).

**The allowance a day for planned searches,** counted in requests, not titles (decision 17):

* the known limit: the ``apiMax`` of an indexer that sent ``newznab:apilimits``, else the daily limit the owner set;
* from a known limit a fifth stays free for the owner's own searches, and RSS reserves ``min(96, max(1, floor(limit *
  0.8 / 3)))`` syncs a day, one every ``max(15 minutes, 24 hours / reserve)``; planned searches get what is left,
  ``max(0, limit * 0.8 - reserve)``. RSS and planned searches both read ``rss_reserve``;
* without a known limit, RSS syncs every 15 minutes and planned searches get one request every two minutes.

A sync's first page is the reserve's; every further request of a sync is charged to the bucket below.

**A token bucket per indexer,** in memory: refilled continuously from that allowance, holding at most an hour of it and
at least the largest plan of one search, starting empty after a restart and when the switch goes on, so nothing is
caught up at once. A planned search takes its planned requests before it starts; afterwards the requests it really sent
are charged, also below zero, which delays the next one. A grab is no request of the bucket: it counts against
``grabMax`` only, as indexers count API calls and grabs apart. ``ready_at`` names a time at which ``take`` succeeds: it
rounds up to the microsecond, and ``take`` allows float noise of ``TOLERANCE``.

**Stop points:** a planned search leaves an indexer out while a request limit pauses it (``paused_until``), while the
escalation pauses it, and from 80 percent of ``apiMax`` until ``apiNextAvailable``. RSS stops the same way, but only
once all of ``apiMax`` is used. Automatic grabs stop while a request
limit pauses the indexer and while ``grabCurrent`` reached ``grabMax``, until ``grabNextAvailable``. A counter whose
next time the indexer did not send holds for a day after it was seen.

**Escalation,** as in Radarr (facts A3): 1, 5, 15 and 30 minutes, 1, 3, 6, 12 and 24 hours. The first failure is level
1, every further failure a level up, and one success a level down. A connection failure pauses by the current level
without rising. In the first 15 minutes after a start no level rises past 1 and no pause lasts longer than 5 minutes. A
request limit keeps its exact Retry-After and changes no level; a key that cannot be read and nexcrate's own breakage
count nothing. The escalation pauses automatic work only.

Log lines carry ids and counts only.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

#: Without a known limit: one planned request every two minutes.
DEFAULT_PER_DAY = 720.0
#: RSS at most: one sync every 15 minutes.
RSS_SYNCS_PER_DAY = 96
RSS_INTERVAL = timedelta(minutes=15)
#: RSS reserves at most a third of what the owner's fifth leaves.
RSS_PART = 3
OWNER_SHARE = 0.2
SEARCH_STOP_SHARE = 0.8
#: The largest plan of one search on one indexer: fresh caps, then six title queries.
LARGEST_PLAN = 7
BUCKET_SECONDS = 3600.0
DAY_SECONDS = 86400.0
#: How long a counter without a next time holds, and how long a bucket without any allowance makes a title wait.
UNKNOWN_NEXT = timedelta(days=1)
#: Float noise a bucket may lack when it is read at exactly the time ``ready_at`` named.
TOLERANCE = 1e-9
LIMIT_MAX = 100_000

ESCALATION_SECONDS = (0, 60, 300, 900, 1800, 3600, 3 * 3600, 6 * 3600, 12 * 3600, 24 * 3600)
START_WINDOW = timedelta(minutes=15)
START_WINDOW_LONGEST = timedelta(minutes=5)
CONNECTION_CODES = frozenset({"indexer_unreachable"})
#: Failures that change nothing: a request limit pauses by its Retry-After, the others are no answer of the indexer.
UNCOUNTED_CODES = frozenset({"indexer_limit_reached", "indexer_key_missing", "internal_error"})


@dataclass(frozen=True)
class Standing:
    """What the budget reads of one indexer."""

    indexer_id: int
    daily_limit: int | None = None
    api_current: int | None = None
    api_max: int | None = None
    grab_current: int | None = None
    grab_max: int | None = None
    api_next_at: datetime | None = None
    grab_next_at: datetime | None = None
    limits_seen_at: datetime | None = None
    paused_until: datetime | None = None
    automatic_paused_until: datetime | None = None

    @classmethod
    def of(cls, row: Any) -> Standing:
        """From an ``Indexer`` row."""
        return cls(
            indexer_id=row.id,
            daily_limit=row.daily_limit,
            api_current=row.api_current,
            api_max=row.api_max,
            grab_current=row.grab_current,
            grab_max=row.grab_max,
            api_next_at=row.api_next_at,
            grab_next_at=row.grab_next_at,
            limits_seen_at=row.limits_seen_at,
            paused_until=row.paused_until,
            automatic_paused_until=row.automatic_paused_until,
        )


# --- The allowance ------------------------------------------------------------------------------------------ #


def known_limit(standing: Standing) -> int | None:
    """``apiMax`` when the indexer sent one, else the owner's daily limit, else None."""
    for value in (standing.api_max, standing.daily_limit):
        if value is not None and value > 0:
            return value
    return None


def rss_reserve(standing: Standing) -> int | None:
    """RSS syncs a day reserved from a known limit; None without one."""
    known = known_limit(standing)
    if known is None:
        return None
    # The small addition keeps a share such as 4.0 from flooring to 3 through a rounding error.
    part = known * (1 - OWNER_SHARE) / RSS_PART + 1e-9
    return min(RSS_SYNCS_PER_DAY, max(1, math.floor(part)))


def rss_interval(standing: Standing) -> timedelta:
    """How often RSS syncs the indexer: every 15 minutes, less often when a known limit reserves fewer syncs."""
    reserve = rss_reserve(standing)
    if reserve is None:
        return RSS_INTERVAL
    return max(RSS_INTERVAL, timedelta(seconds=DAY_SECONDS / reserve))


def per_day(standing: Standing) -> float:
    """Requests a day for planned searches."""
    known = known_limit(standing)
    reserve = rss_reserve(standing)
    if known is None or reserve is None:
        return DEFAULT_PER_DAY
    return max(0.0, known * (1 - OWNER_SHARE) - reserve)


def capacity(rate_per_day: float) -> float:
    if rate_per_day <= 0:
        return 0.0
    return max(float(LARGEST_PLAN), rate_per_day * BUCKET_SECONDS / DAY_SECONDS)


# --- The buckets ---------------------------------------------------------------------------------------------- #


@dataclass
class _Bucket:
    tokens: float
    updated_at: datetime


_lock = threading.Lock()
_buckets: dict[int, _Bucket] = {}
_started_at: datetime | None = None


def reset() -> None:
    """Every bucket empty again: after a restart, and when the switch goes on."""
    with _lock:
        _buckets.clear()


def mark_started(moment: datetime | None) -> None:
    """The start of the app, for the escalation's first 15 minutes; None forgets it."""
    global _started_at
    with _lock:
        _started_at = moment


def started_at() -> datetime | None:
    with _lock:
        return _started_at


def _refilled(standing: Standing, now: datetime) -> _Bucket:
    """The indexer's bucket, refilled up to now; a new bucket starts empty. Call with ``_lock`` held."""
    rate = per_day(standing)
    bucket = _buckets.get(standing.indexer_id)
    if bucket is None:
        bucket = _buckets[standing.indexer_id] = _Bucket(tokens=0.0, updated_at=now)
        return bucket
    elapsed = (now - bucket.updated_at).total_seconds()
    if elapsed > 0:
        # A lower allowance than before (new apilimits) also lowers what the bucket may hold.
        bucket.tokens = min(capacity(rate), bucket.tokens + elapsed * rate / DAY_SECONDS)
        bucket.updated_at = now
    return bucket


def tokens(standing: Standing, now: datetime) -> float:
    with _lock:
        return _refilled(standing, now).tokens


def take(standing: Standing, cost: int, now: datetime) -> bool:
    """Take ``cost`` requests when the bucket holds them."""
    with _lock:
        bucket = _refilled(standing, now)
        if cost > bucket.tokens + TOLERANCE:
            return False
        bucket.tokens -= cost
        return True


def charge(standing: Standing, amount: float, now: datetime) -> None:
    """Charge requests sent beyond what was taken, or give back what was taken and not sent (a negative amount)."""
    if not amount:
        return
    with _lock:
        bucket = _refilled(standing, now)
        room = capacity(per_day(standing))
        bucket.tokens = min(room, max(-max(room, float(LARGEST_PLAN)), bucket.tokens - amount))


def ready_at(standing: Standing, cost: int, now: datetime) -> datetime:
    """When the bucket will hold ``cost`` requests; a day from now when it never will."""
    rate = per_day(standing)
    with _lock:
        missing = cost - _refilled(standing, now).tokens
    if missing <= 0:
        return now
    if rate <= 0 or cost > capacity(rate):
        return now + UNKNOWN_NEXT
    # Up to the microsecond: a timedelta rounds to the nearest one, and rounding down names a time the bucket is short.
    return now + timedelta(microseconds=math.ceil(missing * DAY_SECONDS / rate * 1_000_000))


# --- Stop points ------------------------------------------------------------------------------------------------- #


def _counter_stop(
    current: int | None, threshold: float | None, next_at: datetime | None, seen_at: datetime | None, now: datetime
) -> datetime | None:
    if current is None or threshold is None or threshold <= 0 or current < threshold:
        return None
    end = next_at if next_at is not None else (seen_at + UNKNOWN_NEXT if seen_at is not None else None)
    return end if end is not None and end > now else None


def _automatic_stop(standing: Standing, now: datetime, share: float) -> datetime | None:
    ends = [moment for moment in (standing.paused_until, standing.automatic_paused_until) if moment and moment > now]
    threshold = standing.api_max * share if standing.api_max else None
    counter = _counter_stop(standing.api_current, threshold, standing.api_next_at, standing.limits_seen_at, now)
    if counter is not None:
        ends.append(counter)
    return max(ends) if ends else None


def search_stop(standing: Standing, now: datetime) -> datetime | None:
    """Until when planned searches leave the indexer out; None when they may ask it."""
    return _automatic_stop(standing, now, SEARCH_STOP_SHARE)


def rss_stop(standing: Standing, now: datetime) -> datetime | None:
    """Until when RSS leaves the indexer out: a request limit, the escalation, or all of ``apiMax`` used until
    ``apiNextAvailable``. The 80 percent stop of planned searches does not hold RSS back."""
    return _automatic_stop(standing, now, 1.0)


def grab_stop(standing: Standing, now: datetime) -> datetime | None:
    """Until when automatic grabs leave the indexer's releases alone; None when they may load one."""
    ends = [standing.paused_until] if standing.paused_until and standing.paused_until > now else []
    counter = _counter_stop(
        standing.grab_current,
        float(standing.grab_max) if standing.grab_max else None,
        standing.grab_next_at,
        standing.limits_seen_at,
        now,
    )
    if counter is not None:
        ends.append(counter)
    return max(ends) if ends else None


# --- Escalation ----------------------------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Escalation:
    level: int
    initial_failure_at: datetime | None
    paused_until: datetime | None


def after_request(
    current: Escalation, code: str | None, *, now: datetime, started: datetime | None
) -> Escalation | None:
    """The escalation after an indexer answered a search: ``code`` None for a success. None when nothing changes."""
    top = len(ESCALATION_SECONDS) - 1
    level = max(0, min(current.level, top))
    if code is None:
        lower = max(0, level - 1)
        return Escalation(
            level=lower, initial_failure_at=current.initial_failure_at if lower else None, paused_until=None
        )
    if code in UNCOUNTED_CODES:
        return None
    in_window = started is not None and timedelta(0) <= now - started < START_WINDOW
    if code in CONNECTION_CODES:
        new, first = level, current.initial_failure_at
    else:
        new = level if in_window and level > 0 else min(level + 1, top)
        first = current.initial_failure_at or now
    seconds = ESCALATION_SECONDS[new]
    pause = now + timedelta(seconds=seconds) if seconds else None
    if pause is not None and in_window:
        pause = min(pause, now + START_WINDOW_LONGEST)
    return Escalation(level=new, initial_failure_at=first, paused_until=pause)
