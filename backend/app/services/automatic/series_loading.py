"""Planned searches of series and what they load (S5.4 and S5.5, decisions 9 to 16 and 26).

**Starting** (``start_planned``): the title's due seasons as ``series_planning.asked`` picks them, at most five and no
more than the starts left in the hour, since a series search counts one start per season it covers. Every enabled
indexer with ``automatic_search`` and series categories is asked or the title waits, as for movies. The requests a
search plans on one indexer: per target the search by number, the title queries unless decision 11 leaves them out
(the indexer's search by number found the series within ``ID_SEEN_DAYS``), the query of a special by its name, and 1
for stale caps; never more than the bucket can hold, as paging is charged afterwards anyway.

**Loading** (``load``), per version that wants an episode: what the search would take (the set of S4, only fitting
releases that are not blocked, never one that needs a confirmation), release after release through ``loading.grab``
for the episodes the set took it for. The owner's pack rule: a season pack that fills no episode and replaces fewer
than half of the episodes it holds for the version is not loaded; the season shows "only as a pack" with its size. A
release that cannot be loaded (its file, the client, a blocked hash, a pack left out, grabs stopped at the indexer,
another version took it) is left out and the set is formed again without it (``search.jobs.rejudge_series``), at most
``ATTEMPTS_PER_VERSION`` failures and ``PACK_ROUNDS`` packs per version; any other refusal ends the version.

**Afterwards** (``after_search``): the searched episodes get the search time, the indexers whose search by number found
releases are remembered for the title, the summary per season is merged into the title's, and the title is planned
again.

Log lines carry ids, counts and codes only.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import update

from ...db import SessionLocal
from ...models import Episode, Indexer, Title
from .. import indexers
from ..downloads import loading
from ..search import jobs as search_jobs
from ..search import series as series_search
from ..series import anime, watching
from . import budget, planning, scheduler, series_planning, settings, upgrade_guard, waiting, wishes

logger = logging.getLogger("nexcrate.automatic")

ID_SEEN_DAYS = 30
ATTEMPTS_PER_VERSION = 3
PACK_ROUNDS = 5
SUMMARY_CODES = 5
#: Load refusals that belong to the release or its indexer: the set is formed again without it.
RELEASE_CODES = scheduler.RELEASE_CODES | {
    "release_no_gain",
    "release_no_episodes",
    "episodes_downloading",
    "no_client_for_protocol",
    scheduler.GRAB_STOPPED,
}
_CODE = re.compile(r"^S(\d+)E\d+$")


# --- Starting ------------------------------------------------------------------------------------------------------- #


def seen_indexers(value: object, now: datetime) -> frozenset[int]:
    """The indexers whose search by number found the series within ``ID_SEEN_DAYS``."""
    found: set[int] = set()
    for key, moment in value.items() if isinstance(value, dict) else []:
        times = planning.parse_times([moment])
        if times and timedelta(0) <= now - times[0] < timedelta(days=ID_SEEN_DAYS) and str(key).isdigit():
            found.add(int(key))
    return frozenset(found)


def planned_cost(row: Indexer, series: series_search.SeriesSearch, skip_titles: bool, now: datetime) -> int:
    """The requests a planned series search plans on one indexer (decision 12)."""
    stale = (
        row.caps_checked_at is None
        or row.caps_checked_at < now - indexers.CAPS_MAX_AGE
        or not indexers.caps_know_series(row.caps)
    )
    queries = 0
    # B3: the standard form only where the indexer asks it for an anime series; the number counted through always.
    # ⚠️ Before B3 the anime queries were missing here, so the budget of an anime series was counted too low.
    standard = series.series_type != "anime" or bool(row.anime_standard_format_search)
    for target in series.targets:
        if standard:
            by_number = series_search.id_query(row.caps, series, target)
            if by_number is not None:
                queries += 1
            if by_number is None or not skip_titles:
                queries += len(series_search.title_queries(row.caps, series, target))
            if series_search.name_query(row.caps, series, target) is not None:
                queries += 1
        queries += len(series_search.anime_queries(row.caps, series, target))
    return max(1, queries) + (1 if stale else 0)


def _series_indexers(db: Any) -> list[Indexer]:
    return [row for row in scheduler._automatic_indexers(db) if row.series_categories]


def start_planned(title_id: int, now: datetime, left: int) -> int:
    """Start the planned search of a due series. Returns the starts it counts: the seasons it covers, 0 when it did
    not start."""
    with SessionLocal() as db:
        title = db.get(Title, title_id)
        fact = series_planning.load_facts(db, [title_id], now).get(title_id)
        if title is None or fact is None:
            return 0
        plan = series_planning.title_plan(fact, now)
        if not plan.wanted or plan.next_at is None:
            return 0
        if plan.next_at > now and not wishes.early(title, plan.reason):
            return 0
        if plan.reason not in planning.SEARCHING_REASONS:
            return 0
        daily = title.series_type == "daily"
        asked = series_planning.asked(
            fact, plan.seasons, now, limit=max(1, min(series_planning.MAX_SEASONS, left)), daily=daily
        )
        if asked is None:
            return 0
        covered = [season for season in plan.seasons if season.season in asked.covered]
        if not any(season.missing for season in covered) and upgrade_guard.load(db, now).limit_reached:
            # Only upgrades, and the daily limit is reached: the series stays due (upgrade_guard).
            return 0
        try:
            series = series_search.load(db, title_id, asked.scope(), watching.today())
        except series_search.ScopeInvalid:
            return 0
        if series is None:
            return 0
        seen = seen_indexers(title.id_search_seen, now)
        candidates = [
            (row.id, budget.Standing.of(row), planned_cost(row, series, row.id in seen, now))
            for row in _series_indexers(db)
        ]
        replacing = any(season.reason == "replacement" and season.season in asked.covered for season in plan.seasons)
    if not candidates:
        return 0
    chosen = scheduler.reserve(title_id, candidates, now)
    if chosen is None:
        return 0
    origin = "replacement" if replacing else "search"
    try:
        search_id = search_jobs.start_automatic(
            title_id,
            origin=origin,
            indexer_ids=list(chosen),
            reserved=chosen,
            after=scheduler.after_search,
            scope=asked.scope(),
            searched=asked.searched,
            covered=asked.covered,
            skip_titles=seen & set(chosen),
        )
    except search_jobs.SearchRunning, search_jobs.SearchBusy:
        scheduler.give_back(candidates, chosen, now)
        return 0
    logger.info(
        "Automatic search %s started for series %d (%s): %d seasons, %d episodes, %d of %d indexers",
        search_id,
        title_id,
        origin,
        len(asked.covered),
        len(asked.searched),
        len(chosen),
        len(candidates),
    )
    return len(asked.covered)


def search_now(title_id: int, *, switch: bool = True) -> str:
    """Search every season of a series that wants something at once (decision 26): no limit of seasons, no budget.
    Raises the scheduler's ``TitleMissing``, ``AutomaticOff``, ``NothingWanted`` and the search jobs' exceptions."""
    now = scheduler.clock.now()
    with SessionLocal() as db:
        title = db.get(Title, title_id)
        if title is None:
            raise scheduler.TitleMissing
        if switch and not settings.load_enabled(db, "series"):
            raise scheduler.AutomaticOff
        fact = series_planning.load_facts(db, [title_id], now).get(title_id)
        plan = series_planning.title_plan(fact, now) if fact is not None else None
        asked = (
            series_planning.asked(
                fact, plan.seasons, now, limit=None, due_only=False, daily=title.series_type == "daily"
            )
            if fact is not None and plan is not None
            else None
        )
        if asked is None:
            raise scheduler.NothingWanted
        running = search_jobs.running_search(title_id)
        if running is not None:
            raise search_jobs.SearchRunning(running)
        usable = scheduler.usable_now(_series_indexers(db), now, switch=switch)
        seen = seen_indexers(title.id_search_seen, now)
    if not usable:
        raise search_jobs.NoIndexers
    search_id = search_jobs.start_automatic(
        title_id,
        user_invoked=True,
        origin="search",
        indexer_ids=usable,
        reserved={},
        after=scheduler.after_search,
        scope=asked.scope(),
        searched=asked.searched,
        covered=asked.covered,
        skip_titles=seen & set(usable),
    )
    logger.info(
        "Automatic search %s started at once for series %d: %d seasons, %d indexers",
        search_id,
        title_id,
        len(asked.covered),
        len(usable),
    )
    return search_id


# --- Loading -------------------------------------------------------------------------------------------------------- #


@dataclass
class Outcome:
    """What loading did for one version."""

    #: Per loaded release: its key, the codes it fills and replaces.
    loaded: list[tuple[str, list[str], list[str]]] = field(default_factory=list)
    #: The code that stopped a release or the version, the last one.
    code: str | None = None
    #: Packs left out by the owner's rule: season, size, episodes they would replace.
    pack_only: list[dict[str, Any]] = field(default_factory=list)


def season_of(code: str) -> int | None:
    found = _CODE.match(code or "")
    return int(found.group(1)) if found else None


def pack_left_out(release: dict[str, Any], placed: dict[str, Any], take: dict[str, Any]) -> dict[str, Any] | None:
    """The owner's pack rule (decision 15): a season pack that fills nothing and replaces fewer than half of the
    episodes it holds for the version. Returns what the season line shows, or None when the pack may load."""
    parsed = release.get("parsed_series") or {}
    # A batch of an anime series counted through is a pack as well (decision 10).
    batch = parsed.get("form") == "anime" and not parsed.get("refused") and len(parsed.get("absolute") or []) > 1
    if (parsed.get("release_type") != "season_pack" and not batch) or take.get("fills"):
        return None
    result = placed.get("series_result") or {}
    held = [row for row in result.get("episodes") or [] if row.get("state") != "not_watched"]
    replaces = list(take.get("replaces") or [])
    if len(replaces) * 2 >= len(held):
        return None
    seasons = sorted({season for season in map(season_of, replaces) if season is not None})
    return {
        "season": seasons[0] if seasons else parsed.get("season"),
        "size": release.get("size_bytes"),
        "episodes": len(replaces),
    }


def _placed(release: dict[str, Any] | None, version_id: int) -> dict[str, Any] | None:
    if release is None:
        return None
    return next((item for item in release.get("versions") or [] if item.get("version_id") == version_id), None)


def _fresh(search: search_jobs.Search, excluded: Collection[str]) -> dict[str, Any] | None:
    if not search_jobs.rejudge_series(search.search_id, excluded):
        return None
    body = search_jobs.snapshot(search.search_id)
    if body is not None:
        loading.decorate_series(body, search_jobs.info_hashes(search.search_id))
    return body


def wanting_versions(title_id: int, now: datetime) -> set[int]:
    with SessionLocal() as db:
        fact = series_planning.load_facts(db, [title_id], now).get(title_id)
    if fact is None or anime.not_searched("series", fact.series_type):
        return set()
    return {version.definition_id for version in fact.versions if any(map(version.wants, version.episodes))}


def load(search: search_jobs.Search, body: dict[str, Any], now: datetime) -> tuple[dict[int, Outcome], dict[str, Any]]:
    """Load per wanting version. Returns the outcomes and the answer as it stands after the last forming of the set."""
    with SessionLocal() as db:
        # A program's wish may load with the switch off (answer 3).
        enabled = (
            settings.load_enabled(db, "series")
            or wishes.wished(db, search.title_id)
            # A replacement after a failure too (the owner's answer of 22.09.2026).
            or search.origin == "replacement"
        )
    wanting = wanting_versions(search.title_id, now)
    outcomes: dict[int, Outcome] = {}
    taken: set[str] = set()
    formed_again = False
    for version_id in [entry["version_id"] for entry in body.get("versions") or []]:
        if version_id not in wanting:
            continue
        outcome = outcomes[version_id] = Outcome()
        if not enabled:
            outcome.code = "automatic_off"
            continue
        if formed_again:
            # Formed without another version's releases before: this version starts from the whole answer.
            body = _fresh(search, ()) or body
            formed_again = False
        excluded: set[str] = set()
        failures = packs = elsewhere = 0
        while True:
            entry = next((item for item in body.get("versions") or [] if item["version_id"] == version_id), None)
            if entry is None:
                break
            if entry.get("load_block"):
                outcome.code = entry["load_block"]
                break
            left_out, why = _load_takes(search, body, entry, version_id, outcome, taken, now)
            if left_out is None:
                break
            excluded.add(left_out)
            if why == "pack":
                packs += 1
            elif why == "taken":
                # Another version loads it: no failure of this one, but never more rounds than releases in the answer.
                elsewhere += 1
            else:
                failures += 1
            if failures >= ATTEMPTS_PER_VERSION or packs >= PACK_ROUNDS or elsewhere > len(taken):
                break
            again = _fresh(search, excluded)
            if again is None:
                break
            body, formed_again = again, True
    return outcomes, body


def _kept(release: dict[str, Any], placed: dict[str, Any], take: dict[str, Any]) -> waiting.Kept:
    result = placed.get("series_result") or {}
    episodes = loading.load_episodes(result)
    return waiting.Kept(
        release_key=str(take["release_key"]),
        quality=(release.get("parsed_series") or {}).get("quality") or None,
        score=int(result.get("score") or 0),
        episode_ids=tuple(sorted(episodes)),
        episode_codes=(*take.get("fills", []), *take.get("replaces", [])),
    )


def _load_takes(
    search: search_jobs.Search,
    body: dict[str, Any],
    entry: dict[str, Any],
    version_id: int,
    outcome: Outcome,
    taken: set[str],
    now: datetime,
) -> tuple[str | None, str]:
    """Load the set's releases in order. Returns the key of a release to leave out and why: ``failure``, ``pack`` (left
    out by the rule) or ``taken`` (another version loads it); ``(None, "")`` when the set is done or the version
    stops."""
    releases = {release["release_key"]: release for release in body.get("releases") or []}
    for take in entry.get("takes") or []:
        key = str(take["release_key"])
        release = releases.get(key)
        placed = _placed(release, version_id)
        if release is None or placed is None:
            return key, "failure"
        if key in taken:
            return key, "taken"
        pack = pack_left_out(release, placed, take)
        if pack is not None:
            outcome.pack_only.append(pack)
            return key, "pack"
        if not placed.get("can_load"):
            outcome.code = placed.get("load_block") or outcome.code
            return (key, "failure") if outcome.code in RELEASE_CODES else (None, "")
        wanted = _kept(release, placed, take)
        until = waiting.gate(
            search,
            key,
            version_id,
            highest_quality=bool(placed.get("highest_quality")),
            score=int(wanted.score or 0),
            now=now,
            episode_ids=wanted.episode_ids,
        )
        if until is not None:
            # This release of the set has to wait. It is kept, its episodes are left alone, and the rest of the set
            # goes on: another episode need not wait for this one.
            waiting.keep(search, version_id, [wanted], now)
            outcome.code = waiting.WAITING
            continue
        standing = scheduler._standing(int(release["indexer_id"]))
        if standing is None:
            return key, "failure"
        if budget.grab_stop(standing, now) is not None:
            outcome.code = scheduler.GRAB_STOPPED
            return key, "failure"
        codes = frozenset([*take.get("fills", []), *take.get("replaces", [])])
        try:
            download_id = asyncio.run(loading.grab(search.search_id, key, version_id, [], only=codes))
        except loading.LoadError as exc:
            outcome.code = exc.code
            logger.info(
                "Automatic search %s: series version %d could not load a release: %s",
                search.search_id,
                version_id,
                exc.code,
            )
            return (key, "failure") if exc.code in RELEASE_CODES else (None, "")
        scheduler._count_grab(int(release["indexer_id"]))
        taken.add(key)
        outcome.loaded.append((key, list(take.get("fills") or []), list(take.get("replaces") or [])))
        logger.info(
            "Automatic search %s: series version %d loads download %d for %d episodes",
            search.search_id,
            version_id,
            download_id,
            len(codes),
        )
    return None, ""


# --- The summary ---------------------------------------------------------------------------------------------------- #


def _in_season(codes: Collection[str], season: int) -> list[str]:
    return [code for code in codes if season_of(code) == season]


def summary(
    search: search_jobs.Search,
    body: dict[str, Any],
    outcomes: dict[int, Outcome],
    now: datetime,
    seasons: Collection[int],
    previous: object,
) -> dict[str, Any]:
    """``search_summary`` of a series: counts, codes and sizes per season and version, merged into the one before."""
    at = planning.format_time(now)
    written: dict[int, dict[str, Any]] = {}
    for season in sorted(set(seasons)):
        versions: list[dict[str, Any]] = []
        for entry in body.get("versions") or []:
            if not entry.get("has_profile") or entry.get("fed_by"):
                continue
            outcome = outcomes.get(entry["version_id"], Outcome())
            loaded = [item for item in outcome.loaded if _in_season([*item[1], *item[2]], season)]
            pack = next((item for item in outcome.pack_only if item.get("season") == season), None)
            versions.append(
                {
                    "version_id": entry["version_id"],
                    "label": entry["label"],
                    "loaded": len(loaded),
                    "filled": sum(len(_in_season(item[1], season)) for item in loaded),
                    "replaced": sum(len(_in_season(item[2], season)) for item in loaded),
                    "not_found": len(_in_season(entry.get("not_found") or [], season)),
                    "no_fit": len(_in_season(entry.get("no_fit") or [], season)),
                    "codes": [item["code"] for item in entry.get("nothing_fits") or []][:SUMMARY_CODES],
                    "pack_only": {"size": pack.get("size"), "episodes": pack.get("episodes")} if pack else None,
                    "load_code": outcome.code,
                }
            )
        written[season] = {"season": season, "at": at, "versions": versions}
    kept: dict[int, dict[str, Any]] = {}
    if isinstance(previous, dict) and isinstance(previous.get("seasons"), list):
        for item in previous["seasons"]:
            if isinstance(item, dict) and isinstance(item.get("season"), int):
                kept[item["season"]] = item
    kept.update(written)
    return {
        "at": at,
        "origin": search.origin,
        "releases": sum(1 for release in body.get("releases") or [] if release.get("belongs")),
        "indexers": [
            {"id": state["indexer_id"], "name": state["name"], "code": state["error_code"]}
            for state in body.get("indexers") or []
        ],
        "seasons": [kept[season] for season in sorted(kept)],
    }


def loaded_seasons(outcomes: dict[int, Outcome]) -> set[int]:
    """The seasons something was loaded for or left out as a pack."""
    found: set[int] = set()
    for outcome in outcomes.values():
        for _key, fills, replaces in outcome.loaded:
            found.update(season for season in map(season_of, [*fills, *replaces]) if season is not None)
        found.update(item["season"] for item in outcome.pack_only if isinstance(item.get("season"), int))
    return found


# --- After the search ----------------------------------------------------------------------------------------------- #


def _id_hits(search: search_jobs.Search) -> set[int]:
    return {
        state.info.indexer_id
        for state in search.indexers
        if any(query.kind == "id" and query.releases > 0 for query in state.queries)
    }


def after_search(search: search_jobs.Search, now: datetime) -> None:
    """The step after a planned series search, once the budget is charged: load, write, plan again."""
    body = search_jobs.snapshot(search.search_id)
    if body is None:
        return
    loading.decorate_series(body, search_jobs.info_hashes(search.search_id))
    outcomes, body = load(search, body, now)
    hits = _id_hits(search)
    with SessionLocal() as db:
        title = db.get(Title, search.title_id)
        if title is None:
            return
        if search.searched:
            db.execute(
                update(Episode)
                .where(Episode.title_id == title.id, Episode.id.in_(list(search.searched)))
                .values(last_search_at=now),
                execution_options={"synchronize_session": False},
            )
        if hits:
            seen = dict(title.id_search_seen) if isinstance(title.id_search_seen, dict) else {}
            seen.update({str(indexer_id): planning.format_time(now) for indexer_id in sorted(hits)})
            # A new dict: a JSON column changed in place is not written.
            title.id_search_seen = seen
        title.last_search_at = now
        stopped = any(outcome.code == scheduler.GRAB_STOPPED for outcome in outcomes.values())
        written = summary(search, body, outcomes, now, search.covered, title.search_summary)
        title.search_summary = scheduler.hold_for_grab_limit(written, stopped, body, now)
        db.flush()
        planning.replan(db, [title.id], now)
        db.commit()
    logger.info(
        "Automatic search %s of series %d: %d seasons, %d episodes searched, %d releases loaded",
        search.search_id,
        search.title_id,
        len(search.covered),
        len(search.searched),
        sum(len(outcome.loaded) for outcome in outcomes.values()),
    )
