"""The job every minute, the load decision after an automatic search, and "search automatically now".

the design notes, C1, C4, C6, C8, decisions 1, 6 to 10, 13 and 14.

**Every minute** (``run_job``), with the switch on or off:

1. **Plan** (``planning``, the database only): titles never planned, titles due within the minute, and a rolling chunk
   of the titles with a version no source feeds or with a plan stored.
2. With the switch off that is all: nothing reaches an indexer or a download client.
3. **Start** due titles, at most ``MAX_RUNNING`` automatic searches at once and at most ``MAX_STARTS_PER_HOUR`` starts
   an hour, in this order (decision 21): first titles whose reason is ``replacement`` (a failed download waits for the
   next release), then titles with an own, monitored version without a file (missing), then the rest (upgrades); within
   each group by the next time, then id. The due query sorts this way, so the batch of ``DUE_BATCH`` follows it and many
   due upgrades never hide a missing title. The order only decides which title is tried first; whether it wants
   something (profile, folder, a blocking download) is checked when it starts. ``limit`` is no rank of its own: a title
   waiting for the budget ranks by its versions until planning gives it its reason back. A planned search, a replacement
   too, asks every enabled indexer with ``automatic_search`` or waits (decision 19): a search without one of them would
   count as the title's search, and that indexer would not be asked again until the next planned time. Left out, while
   the search goes on, are only an indexer under a stop point (a request limit, the escalation, the 80 percent stop) and
   one whose planned allowance is 0. Every other indexer has to hold the requests the plan needs: an id search 1, else
   its title queries, fresh caps 1 more. Taking is all or none: when one bucket is short, what was taken is given back
   and the title waits, reason ``limit``, until the latest time a short bucket holds its requests; with every indexer
   left out, until the first stop point ends. Without any indexer for automatic search the titles stay due.

**After the search,** in its thread: the requests really sent are charged. Then, per version that wants something, the
release it would take, as the owner's search answer shows it with the blocklist, is loaded through ``loading.grab``
without any confirmation: a release that does not fit or is blocked is never taken. A release that cannot be loaded
(its file, or the client refused it) gives way to the next fitting one, at most ``ATTEMPTS_PER_VERSION`` per version;
any other refusal ends that version. No release goes to two versions, and no grab goes to an indexer whose grabs are
stopped. A grab counts one grab with ``apilimits`` and no request of the bucket: indexers count API calls and grabs
apart. Then the summary and the last search time are written, the title is planned again, and the search is dropped
unless the owner followed it.

**Albums** share it the same way with a third switch; ``album_loading`` starts their search
and loads what it found.

**Series** share this job, the order, the caps and the budget: their switch is a second one,
a series search counts one start per season it covers, and ``series_loading`` plans its scope, starts it and loads what
it found. Movies and series wait in one queue.

**A program's wish** (``wishes.py``): since 22.09.2026 not in this order any more. Its own runner
starts it at once like "search automatically now", with the switch off too, at most five at a time; it may load, and
the wish is fulfilled after the search.

**Search automatically now** runs that search at once, with the switch on and something wanted, on every enabled
indexer with ``automatic_search`` that no request limit pauses: it is the owner's own search, not held back by the
budget, the escalation or the 80 percent stop, and it is charged to the buckets afterwards.

Log lines carry ids, counts and codes, never titles, release names, paths or links.
"""

from __future__ import annotations

import asyncio
import logging
import math
import secrets
import threading
from collections.abc import Collection
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, exists, or_, select

from ...db import SessionLocal
from ...models import Episode, EpisodeVersion, Indexer, Title, Version
from .. import indexers, logs
from ..downloads import loading
from ..search import jobs as search_jobs
from ..search import plan as search_plan
from ..search.model import TitleInfo, title_info
from . import budget, clock, planning, settings, upgrade_guard, waiting, wishes

logger = logging.getLogger("nexcrate.automatic")

JOB_NAME = "automatic_search"
INTERVAL_SECONDS = 60.0
MAX_RUNNING = 2
MAX_STARTS_PER_HOUR = 30
PLAN_CHUNK = 500
DUE_BATCH = 50
ATTEMPTS_PER_VERSION = 3
SUMMARY_CODES = 5
#: Load refusals that belong to the release: the next fitting release may still load.
RELEASE_CODES = frozenset(
    {
        "release_fetch_failed",
        "release_file_invalid",
        "release_blocklisted",
        "release_not_fitting",
        "release_not_found",
        "client_refused",
    }
)
#: The load code of a version whose grabs are stopped at the indexer.
GRAB_STOPPED = "indexer_limit_reached"

PLANNED_KINDS = ("movie", "series", "album")

_lock = threading.Lock()
_starts: list[datetime] = []
_cursor = 0


class TitleMissing(Exception):
    """The title does not exist."""


class AutomaticOff(Exception):
    """The switch is off."""


class NothingWanted(Exception):
    """No version of the title wants anything."""


def reset() -> None:
    """Forget the starts of the last hour and the rolling position. For the tests."""
    global _cursor
    with _lock:
        _starts.clear()
        _cursor = 0


def run_job() -> None:
    """The background job."""
    token = logs.bind_request(secrets.token_hex(4))
    try:
        run_round()
    finally:
        logs.unbind_request(token)


def run_round() -> int:
    """One round: plan, and start what is due of the kinds switched on. Returns how many searches started."""
    now = clock.now()
    with SessionLocal() as db:
        kinds = settings.load_kinds(db)
    plan_round(now)
    wishes.clear_unwanted()
    # With every switch off a program's wish still starts (answer 3).
    return start_due(now, kinds)


# --- Planning ------------------------------------------------------------------------------------------------------ #


def plan_round(now: datetime) -> tuple[int, int]:
    """Plan the titles of this round. Returns how many were planned and how many plans changed."""
    global _cursor
    soon = now + timedelta(seconds=INTERVAL_SECONDS)
    with SessionLocal() as db:
        fresh = list(
            db.scalars(
                select(Title.id)
                .where(Title.kind.in_(PLANNED_KINDS), Title.next_search_reason.is_(None))
                .order_by(Title.id)
                .limit(PLAN_CHUNK)
            )
        )
        due = list(
            db.scalars(
                select(Title.id)
                .where(Title.kind.in_(PLANNED_KINDS), Title.next_search_at.is_not(None), Title.next_search_at <= soon)
                .order_by(Title.next_search_at, Title.id)
                .limit(PLAN_CHUNK)
            )
        )
        with _lock:
            after = _cursor
        own = exists().where(Version.title_id == Title.id, Version.source_id.is_(None))
        stored = and_(Title.next_search_reason.is_not(None), Title.next_search_reason != "nothing_wanted")
        rolling = list(
            db.scalars(
                select(Title.id)
                .where(Title.kind.in_(PLANNED_KINDS), Title.id > after, or_(own, stored))
                .order_by(Title.id)
                .limit(PLAN_CHUNK)
            )
        )
    with _lock:
        _cursor = rolling[-1] if len(rolling) == PLAN_CHUNK else 0
    ids = sorted(set(fresh) | set(due) | set(rolling))
    planned = changed = 0
    for start in range(0, len(ids), PLAN_CHUNK):
        # Read without the write lock; only the titles whose plan changed are planned again under it (``plans``).
        with SessionLocal() as db:
            found = planning.plans(db, ids[start : start + PLAN_CHUNK], now)
        planned += len(found)
        moved = planning.differing(found)
        if moved:
            with SessionLocal() as db:
                _counted, written = planning.replan(db, moved, now)
                db.commit()
            changed += written
    if changed:
        logger.info("Automatic searching: %d titles planned, %d plans changed", planned, changed)
    return planned, changed


# --- Starting ----------------------------------------------------------------------------------------------------- #


def _title_info(title: Title) -> TitleInfo:
    return title_info(
        title_id=title.id,
        title=title.title,
        original_title=title.original_title,
        year=title.year,
        tmdb_id=title.tmdb_id,
        imdb_id=title.imdb_id,
    )


def planned_cost(row: Indexer, info: TitleInfo, now: datetime) -> int:
    """The requests a search plans on one indexer: an id search 1, else its title queries; fresh caps 1 more."""
    stale = row.caps_checked_at is None or row.caps_checked_at < now - indexers.CAPS_MAX_AGE
    if search_plan.id_query(row.caps, info) is not None:
        queries = 1
    else:
        queries = len(search_plan.title_queries(row.caps, info, bool(row.remove_year)))
    return max(1, queries) + (1 if stale else 0)


def _automatic_indexers(db: Any) -> list[Indexer]:
    return list(
        db.scalars(
            select(Indexer).where(Indexer.enabled.is_(True), Indexer.automatic_search.is_(True)).order_by(Indexer.id)
        )
    )


def _due_order(today: str) -> tuple[Any, ...]:
    """The order due titles start in (decision 21, S5 decision 13): a replacement, then a title with an own, monitored
    version without a file, or a series with an aired, watched episode without a file in an own version (missing), then
    the rest (upgrades); within each group by the next time, then id."""
    movie_missing = exists().where(
        Version.title_id == Title.id,
        Version.source_id.is_(None),
        Version.monitored.is_(True),
        Version.has_file.is_(False),
    )
    series_missing = exists().where(
        Version.title_id == Title.id,
        Version.source_id.is_(None),
        EpisodeVersion.version_id == Version.id,
        EpisodeVersion.watched.is_(True),
        EpisodeVersion.episode_file_id.is_(None),
        Episode.id == EpisodeVersion.episode_id,
        Episode.tmdb_gone_at.is_(None),
        Episode.air_date < today,
    )
    missing = or_(
        # An album is missing like a movie: an own, monitored version without a file.
        and_(Title.kind.in_(("movie", "album")), movie_missing),
        and_(Title.kind == "series", series_missing),
    )
    rank = case(
        (Title.next_search_reason == "replacement", 0),
        (Title.search_wish_at.is_not(None), 1),
        (missing, 2),
        else_=3,
    )
    return rank, Title.next_search_at, Title.id


def start_due(now: datetime, kinds: Collection[str] = ("movie",)) -> int:
    """Start the due titles of these kinds the caps and the budget allow. Returns how many searches started."""
    with _lock:
        _starts[:] = [moment for moment in _starts if timedelta(0) <= now - moment < timedelta(hours=1)]
        left = MAX_STARTS_PER_HOUR - len(_starts)
    slots = min(MAX_RUNNING - search_jobs.running_count(search_jobs.AUTOMATIC), left)
    if slots <= 0:
        return 0
    with SessionLocal() as db:
        due = list(
            db.scalars(
                select(Title.id)
                .where(
                    or_(
                        and_(
                            Title.kind.in_(sorted(kinds)),
                            Title.next_search_at.is_not(None),
                            Title.next_search_at <= now,
                            Title.next_search_reason.in_(planning.SEARCHING_REASONS),
                        ),
                        # A replacement after a failure also with the switch off (the owner's answer of 22.09.2026).
                        and_(Title.next_search_at <= now, Title.next_search_reason == "replacement"),
                    )
                )
                .order_by(*_due_order(now.astimezone().date().isoformat()))
                .limit(DUE_BATCH)
            )
        )
        usable = bool(_automatic_indexers(db)) if due else False
    if not due:
        return 0
    if not usable:
        logger.info(
            "Automatic searching: %d titles are due, but no enabled indexer is set for automatic search", len(due)
        )
        return 0
    started = 0
    for title_id in due:
        if started >= slots:
            break
        if search_jobs.running_search(title_id) is not None:
            continue
        with _lock:
            left = MAX_STARTS_PER_HOUR - len(_starts)
        if left <= 0:
            break
        counted = _start_planned(title_id, now, left)
        if counted:
            started += 1
            with _lock:
                _starts.extend([now] * counted)
    return started


def _start_planned(title_id: int, now: datetime, left: int = MAX_STARTS_PER_HOUR) -> int:
    """Start a due title's planned search. Returns the starts it counts, 0 when it did not start."""
    with SessionLocal() as db:
        title = db.get(Title, title_id)
        if title is not None and title.kind == "series":
            from . import series_loading

            return series_loading.start_planned(title_id, now, left)
        if title is not None and title.kind == "album":
            from . import album_loading

            return album_loading.start_planned(title_id, now, left)
        fact = planning.load_facts(db, [title_id], now).get(title_id)
        if title is None or fact is None:
            return 0
        plan = planning.title_plan(fact, now)
        searching = plan.reason in planning.SEARCHING_REASONS
        due = plan.next_at is not None and (plan.next_at <= now or wishes.early(title, plan.reason))
        if not plan.wanted or not due or not searching:
            return 0
        if only_upgrades(fact) and upgrade_guard.load(db, now).limit_reached:
            # The daily limit of upgrades: the title stays due and starts once the window frees (upgrade_guard).
            return 0
        info = _title_info(title)
        rows = _automatic_indexers(db)
        candidates = [(row.id, budget.Standing.of(row), planned_cost(row, info, now)) for row in rows]
    if not candidates:
        return 0
    chosen = reserve(title_id, candidates, now)
    if chosen is None:
        return 0
    origin = "replacement" if plan.reason == "replacement" else "search"
    try:
        search_id = search_jobs.start_automatic(
            title_id, origin=origin, indexer_ids=list(chosen), reserved=chosen, after=after_search
        )
    except search_jobs.SearchRunning, search_jobs.SearchBusy:
        give_back(candidates, chosen, now)
        return 0
    logger.info(
        "Automatic search %s started for title %d (%s) with %d of %d indexers",
        search_id,
        title_id,
        origin,
        len(chosen),
        len(candidates),
    )
    return 1


def only_upgrades(fact: planning.TitleFacts) -> bool:
    """Every version the title wants has a file: its search can only bring upgrades."""
    wanting = [version for version in fact.versions if version.wants]
    return bool(wanting) and all(version.has_file for version in wanting)


def reserve(title_id: int, candidates: list[Planned], now: datetime) -> dict[int, int] | None:
    """Take the planned requests of every indexer that is not stopped, or make the title wait (decision 19). Returns
    the requests taken per indexer, or None when the title waits. A plan larger than a bucket can hold takes what it
    holds: paging is charged afterwards anyway (S5, decision 12)."""
    asked: list[Planned] = []
    ends: list[datetime] = []
    for indexer_id, standing, cost in candidates:
        stop = budget.search_stop(standing, now)
        if stop is not None:
            ends.append(stop)
        elif budget.per_day(standing) <= 0:
            # No allowance for planned searches at all: it never holds a request, so waiting for it would never end.
            ends.append(now + budget.UNKNOWN_NEXT)
        else:
            room = max(1, math.floor(budget.capacity(budget.per_day(standing))))
            asked.append((indexer_id, standing, min(cost, room)))
    if not asked:
        _wait_for_limit(title_id, min(ends))
        logger.info(
            "Title %d waits: all %d indexers are paused, stopped or have no allowance", title_id, len(candidates)
        )
        return None
    short = take_all(asked, now)
    if short:
        until = max(budget.ready_at(standing, cost, now) for _indexer_id, standing, cost in short)
        _wait_for_limit(title_id, until)
        logger.info("Title %d waits for the budget of %d of %d indexers", title_id, len(short), len(asked))
        return None
    return {indexer_id: cost for indexer_id, _standing, cost in asked}


def give_back(candidates: list[Planned], chosen: dict[int, int], now: datetime) -> None:
    """Give back what ``reserve`` took when the search could not start."""
    for indexer_id, standing, _cost in candidates:
        if indexer_id in chosen:
            budget.charge(standing, -chosen[indexer_id], now)


#: An indexer a planned search asks: its id, what the budget reads of it, and the requests the plan needs.
Planned = tuple[int, budget.Standing, int]


def take_all(asked: list[Planned], now: datetime) -> list[Planned]:
    """Take the planned requests of every indexer, or of none (decision 19). Returns the indexers whose bucket is short;
    when there is one, what was taken from the others is given back."""
    taken: list[Planned] = []
    short: list[Planned] = []
    for planned in asked:
        _indexer_id, standing, cost = planned
        if budget.take(standing, cost, now):
            taken.append(planned)
        else:
            short.append(planned)
    if short:
        for _indexer_id, standing, cost in taken:
            budget.charge(standing, -cost, now)
    return short


def _wait_for_limit(title_id: int, until: datetime) -> None:
    with SessionLocal() as db:
        title = db.get(Title, title_id)
        if title is not None:
            title.next_search_at, title.next_search_reason = until, "limit"
            db.commit()


# --- Search automatically now --------------------------------------------------------------------------------------- #


def usable_now(rows: list[Indexer], now: datetime, *, switch: bool) -> list[int]:
    """The indexers a search at once asks: not paused. A program's wish (``switch`` False) also leaves out an indexer
    whose whole request limit is used (``budget.rss_stop``): its limits still hold (the owner's answer, 22.09.2026)."""
    found = []
    for row in rows:
        if row.paused_until is not None and row.paused_until > now:
            continue
        if not switch and budget.rss_stop(budget.Standing.of(row), now) is not None:
            continue
        found.append(row.id)
    return found


def search_now(title_id: int, *, switch: bool = True) -> str:
    """Start the planned search of a title at once. Raises ``TitleMissing``, ``AutomaticOff``, ``NothingWanted`` and the
    search jobs' ``SearchRunning``, ``NoIndexers`` and ``SearchBusy``. A program's wish passes ``switch=False``: it
    searches with the automatic of the kind off too."""
    now = clock.now()
    with SessionLocal() as db:
        title = db.get(Title, title_id)
        if title is None:
            raise TitleMissing
        series = title.kind == "series"
        album = title.kind == "album"
    if series:
        from . import series_loading

        return series_loading.search_now(title_id, switch=switch)
    if album:
        from . import album_loading

        return album_loading.search_now(title_id, switch=switch)
    with SessionLocal() as db:
        if switch and not settings.load_enabled(db):
            raise AutomaticOff
        fact = planning.load_facts(db, [title_id], now).get(title_id)
        if fact is None or not planning.title_plan(fact, now).wanted:
            raise NothingWanted
        running = search_jobs.running_search(title_id)
        if running is not None:
            raise search_jobs.SearchRunning(running)
        usable = usable_now(_automatic_indexers(db), now, switch=switch)
    if not usable:
        raise search_jobs.NoIndexers
    search_id = search_jobs.start_automatic(
        title_id, origin="search", indexer_ids=usable, reserved={}, after=after_search, user_invoked=True
    )
    logger.info("Automatic search %s started at once for title %d with %d indexers", search_id, title_id, len(usable))
    return search_id


# --- After the search ----------------------------------------------------------------------------------------------- #


def _standing(indexer_id: int) -> budget.Standing | None:
    with SessionLocal() as db:
        row = db.get(Indexer, indexer_id)
        return budget.Standing.of(row) if row is not None else None


def _count_grab(indexer_id: int) -> None:
    with SessionLocal() as db:
        row = db.get(Indexer, indexer_id)
        if row is not None and row.grab_max is not None:
            row.grab_current = (row.grab_current or 0) + 1
            db.commit()


def candidates(
    releases: list[dict[str, Any]], version_id: int, has_file: bool
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """The releases a version may take with their entry for the version, in the order of its ranks: fitting, not
    blocked, and better than its file when it has one. The first one is the answer's ``would_take``; whether it can load
    (``can_load``, ``load_block``) is left to the caller, so a refusal keeps its code."""
    placed: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    for release in releases:
        if not release.get("belongs") or release.get("blocklisted"):
            continue
        for entry in release.get("versions") or []:
            if entry.get("version_id") != version_id or entry.get("rank") is None:
                continue
            result = entry.get("result") or {}
            upgrade = result.get("upgrade")
            if not result.get("accepted") or (has_file and not (upgrade and upgrade.get("better"))):
                continue
            placed.append((int(entry["rank"]), release, entry))
    return [(release, entry) for _rank, release, entry in sorted(placed, key=lambda item: item[0])]


def _kept(release: dict[str, Any], placed: dict[str, Any]) -> waiting.Kept:
    result = placed.get("result") or {}
    return waiting.Kept(
        release_key=release["release_key"],
        quality=(release.get("parsed") or {}).get("quality") or None,
        score=int(result.get("score") or 0),
    )


def candidate_keys(title_id: int, body: dict[str, Any], now: datetime) -> set[tuple[int, str]]:
    """Version and release key of everything a wanting movie version may still take, for the waiting releases."""
    with SessionLocal() as db:
        fact = planning.load_facts(db, [title_id], now).get(title_id)
    wanting = {version.definition_id: version for version in (fact.versions if fact else ()) if version.wants}
    return {
        (version_id, release["release_key"])
        for version_id, version in wanting.items()
        for release, _placed in candidates(body.get("releases") or [], version_id, version.has_file)
    }


def _load(search: search_jobs.Search, body: dict[str, Any], now: datetime) -> dict[int, tuple[bool, str | None]]:
    """Load per wanting version; returns version id to whether it loaded and the code that stopped it."""
    title_id = search.title_id
    with SessionLocal() as db:
        # A program's wish may load with the switch off (answer 3).
        # A replacement after a failure too (the owner's answer of 22.09.2026, as Radarr's "Redownload failed").
        enabled = settings.load_enabled(db) or wishes.wished(db, title_id) or search.origin == "replacement"
        fact = planning.load_facts(db, [title_id], now).get(title_id)
    wanting = {version.definition_id: version for version in (fact.versions if fact else ()) if version.wants}
    releases = body.get("releases") or []
    outcomes: dict[int, tuple[bool, str | None]] = {}
    taken: set[str] = set()
    for entry in body.get("versions") or []:
        version_id = entry["version_id"]
        version = wanting.get(version_id)
        if version is None or fact is None or fact.kind != "movie":
            continue
        if not enabled:
            outcomes[version_id] = (False, "automatic_off")
            continue
        if entry.get("load_block"):
            outcomes[version_id] = (False, entry["load_block"])
            continue
        loaded, code, attempts = False, None, 0
        ranked = candidates(releases, version_id, version.has_file)
        for place, (release, placed) in enumerate(ranked):
            if attempts >= ATTEMPTS_PER_VERSION:
                break
            if release["release_key"] in taken:
                continue
            if not placed.get("can_load"):
                # The owner's search answer names the same code on its load button.
                code = placed.get("load_block") or code
                continue
            until = waiting.gate(
                search,
                release["release_key"],
                version_id,
                highest_quality=bool(placed.get("highest_quality")),
                score=int((placed.get("result") or {}).get("score") or 0),
                now=now,
            )
            if until is not None:
                # The best release has to wait: it is kept with the ones ranked after it, and nothing worse loads.
                behind = [_kept(*pair) for pair in ranked[place:] if pair[0]["release_key"] not in taken]
                waiting.keep(search, version_id, behind, now)
                code = waiting.WAITING
                break
            standing = _standing(int(release["indexer_id"]))
            if standing is None:
                continue
            if budget.grab_stop(standing, now) is not None:
                code = GRAB_STOPPED
                continue
            attempts += 1
            # A grab is no request of the bucket: indexers count grabs apart (``grab_current``, ``grab_stop``).
            try:
                download_id = asyncio.run(loading.grab(search.search_id, release["release_key"], version_id, []))
            except loading.LoadError as exc:
                code = exc.code
                logger.info(
                    "Automatic search %s: version %d could not load a release: %s", search.search_id, version_id, code
                )
                if code in RELEASE_CODES:
                    continue
                break
            _count_grab(int(release["indexer_id"]))
            taken.add(release["release_key"])
            loaded, code = True, None
            logger.info("Automatic search %s: version %d loads download %d", search.search_id, version_id, download_id)
            break
        outcomes[version_id] = (loaded, code)
    return outcomes


def best_release(entry: dict[str, Any], belonging: list[dict[str, Any]]) -> tuple[str | None, list[str]]:
    """The best release title of one version and its codes: what it would take with none, else the best ranked or the
    highest scored release with its rejection codes, the upgrade check's reason, and ``blocklisted``."""
    version_id = entry["version_id"]
    would = entry.get("would_take")
    scored: list[tuple[tuple[int, int, int, int, int], str, list[str]]] = []
    for order, release in enumerate(belonging):
        placed = next((item for item in release.get("versions") or [] if item.get("version_id") == version_id), None)
        result = placed.get("result") if placed is not None else None
        if placed is None or result is None:
            continue
        if would is not None and release["release_key"] == would:
            return release["title"], []
        codes = [rejection["code"] for rejection in result.get("rejections") or []]
        upgrade = result.get("upgrade")
        if result.get("accepted") and upgrade and not upgrade.get("better") and upgrade.get("reason"):
            codes.append(upgrade["reason"])
        if release.get("blocklisted"):
            codes.append("blocklisted")
        rank = placed.get("rank")
        key = (
            0 if rank is not None else 1,
            int(rank or 0),
            0 if result.get("accepted") else 1,
            -int(result.get("score") or 0),
            order,
        )
        scored.append((key, release["title"], codes))
    if not scored:
        return None, []
    _key, title, codes = min(scored, key=lambda item: item[0])
    return title, list(dict.fromkeys(codes))


def summary(
    search: search_jobs.Search, body: dict[str, Any], outcomes: dict[int, tuple[bool, str | None]], now: datetime
) -> dict[str, Any]:
    """``search_summary``: counts, codes and release titles, never a link."""
    belonging = [release for release in body.get("releases") or [] if release.get("belongs")]
    versions: list[dict[str, Any]] = []
    for entry in body.get("versions") or []:
        if not entry.get("has_profile"):
            continue
        best, codes = best_release(entry, belonging)
        loaded, load_code = outcomes.get(entry["version_id"], (False, entry.get("load_block")))
        versions.append(
            {
                "version_id": entry["version_id"],
                "label": entry["label"],
                "best_title": best,
                "codes": codes[:SUMMARY_CODES],
                "loaded": loaded,
                "load_code": load_code,
            }
        )
    return {
        "at": planning.format_time(now),
        "origin": search.origin,
        "releases": len(belonging),
        "indexers": [
            {"id": state["indexer_id"], "name": state["name"], "code": state["error_code"]}
            for state in body.get("indexers") or []
        ],
        "versions": versions,
    }


#: When an indexer names no time for its next grabs and nexcrate has not seen one either, the release is searched again
#: after this pause, not with the title's schedule.
GRAB_LIMIT_FALLBACK = timedelta(hours=1)


def grab_limit_until(indexer_ids: set[int], now: datetime) -> datetime:
    """When the first of these indexers loads again: its ``grab_stop``, or ``GRAB_LIMIT_FALLBACK`` from now."""
    ends: list[datetime] = []
    with SessionLocal() as db:
        rows = list(db.scalars(select(Indexer).where(Indexer.id.in_(sorted(indexer_ids))))) if indexer_ids else []
    for row in rows:
        end = budget.grab_stop(budget.Standing.of(row), now)
        if end is not None:
            ends.append(end)
    return min(ends) if ends else now + GRAB_LIMIT_FALLBACK


def hold_for_grab_limit(summary: dict[str, Any], stopped: bool, body: dict[str, Any], now: datetime) -> dict[str, Any]:
    """The summary of a search in which only the indexer's grab limit held a fitting release back gets
    ``grab_free_at``: the plan searches again then, not with the schedule (finding 6 of 22.09.2026, Full Metal Jacket:
    a month later). Written before planning, which reads it (``planning.grab_limit_plan``)."""
    if not stopped:
        return summary
    indexer_ids = {int(release["indexer_id"]) for release in body.get("releases") or [] if release.get("indexer_id")}
    until = grab_limit_until(indexer_ids, now)
    logger.info("A release was held back by the indexer's grab limit; the title is searched again at %s", until)
    return {**summary, "grab_free_at": planning.format_time(until)}


def _held_by_limit(value: object) -> bool:
    """Whether a stored search summary names a version, season or album that only the grab limit kept from loading."""
    if isinstance(value, dict):
        if value.get("load_code") == GRAB_STOPPED and not value.get("loaded"):
            return True
        return any(_held_by_limit(item) for item in value.values())
    if isinstance(value, list):
        return any(_held_by_limit(item) for item in value)
    return False


def repair_grab_limits(now: datetime) -> int:
    """At the start: a title whose last search found a release the grab limit held back, and which waits for its
    schedule, is searched again now (finding 6 of 22.09.2026: 287 such titles waited a month). Returns how many."""
    repaired = 0
    with SessionLocal() as db:
        rows = db.scalars(
            select(Title).where(
                Title.next_search_reason.in_(("schedule", "replacement_limit")),
                Title.next_search_at.is_not(None),
                Title.next_search_at > now,
            )
        )
        for title in rows:
            if isinstance(title.search_summary, dict) and _held_by_limit(title.search_summary):
                # A new dict: a JSON column changed in place is not written.
                title.search_summary = {**title.search_summary, "grab_free_at": planning.format_time(now)}
                title.next_search_at, title.next_search_reason = now, "limit"
                repaired += 1
        if repaired:
            db.commit()
    return repaired


def after_search(search: search_jobs.Search) -> None:
    """The step after an automatic search, in its thread: charge, load, write the summary, plan the title again."""
    now = clock.now()
    states = list(search.indexers)
    sent = {state.info.indexer_id: state.requests for state in states}
    with SessionLocal() as db:
        ids = sorted(set(sent) | set(search.reserved))
        rows = list(db.scalars(select(Indexer).where(Indexer.id.in_(ids)))) if ids else []
    for row in rows:
        budget.charge(budget.Standing.of(row), sent.get(row.id, 0) - search.reserved.get(row.id, 0), now)
    if search.scope is not None:
        from . import series_loading

        series_loading.after_search(search, now)
        wishes.fulfil(search.title_id)
        return
    if search.album_scope is not None:
        from . import album_loading

        album_loading.after_search(search, now)
        return
    body = search_jobs.snapshot(search.search_id)
    if body is None:
        return
    loading.decorate(body, search_jobs.info_hashes(search.search_id))
    outcomes = _load(search, body, now)
    written = summary(search, body, outcomes, now)
    with SessionLocal() as db:
        title = db.get(Title, search.title_id)
        if title is None:
            return
        title.last_search_at = now
        title.search_summary = hold_for_grab_limit(
            written, any(code == GRAB_STOPPED for _loaded, code in outcomes.values()), body, now
        )
        title.search_wish_at = None
        db.flush()
        planning.replan(db, [title.id], now)
        db.commit()
    logger.info(
        "Automatic search %s of title %d: %d releases of the movie, %d versions loaded",
        search.search_id,
        search.title_id,
        written["releases"],
        sum(1 for loaded, _code in outcomes.values() if loaded),
    )
