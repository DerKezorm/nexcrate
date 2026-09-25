"""The plan of a series: which episodes want something, when each season is searched next, and how
(S5.2 to S5.4, decisions 2 to 10).

**An episode wants something in a version** (decision 2) when no source feeds the version, its profile has series rules,
its definition has a folder, the series is searched (anime only since A6 of the design notes), the version
watches the episode, no running download of the
version holds it (``downloads.store.downloading_episodes``, problems that need the owner included), no problem at the
place holds the whole version (decision 3: ``path_not_found``, ``no_space``, ``client_error``), and it has no file or
its file can still be upgraded. Specials only when watched, as they never are from a rule.

**Files first** (decisions 18 and 23): a version of nexcrate's own wants nothing until its
series folder is read (``versions.files_read_at``), so nexcrate never loads what already lies on disk. While it has
unclear files (a file of its own with no episode that the owner did not leave out), an episode without a file wants
something only when it aired on or after the day the version became nexcrate's (``versions.own_since``); upgrades go
on.

**The anchor of an episode** (decision 4) is the day after its air date, 00:00 in the container's time zone: from then
on it has aired, as ``series.watching.aired`` counts. Without an air date it is never searched by plan (decision 5).

**Its next search** (decisions 6 and 7), in whole days since the anchor, as for movies: never searched since the anchor
(``episodes.last_search_at``), at the anchor plus the spread; else the last search plus the interval of its tier plus
the spread. An episode whose file can be upgraded goes at half the rate; of two versions the one missing it sets
the rate. The spread comes from a hash of the title, the season and the base time.

**A season** is due once its first searchable episode (wanting and aired) is due; the search then asks for every
searchable episode of the season. A replacement after a failure of the season (``versions.series_replacements``) that
no search followed makes it due at that time, reason ``replacement``; after three in 24 hours with a failure no search
followed, the season shows ``replacement_limit`` on its schedule (decision 22). Wanting episodes that have not aired
yet wait with ``air_date``, episodes without a date show ``no_date``.

**The title** takes the earliest season with its reason (decision 8); a stored ``limit`` stays while its time lies after
the plan's.

**What one search asks** (decisions 9 and 10): at most ``MAX_SEASONS`` due seasons, replacements first, then seasons
with missing episodes, then upgrades, then the earliest. A season that has aired completely with at least two searchable
episodes, or with more than ``MAX_EPISODES``, is asked as a season; otherwise its episodes one by one. A daily show asks
its episodes of the last ``DAILY_DAYS`` days one by one (at most five) and older ones through the season. Specials go
one by one, at most five.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session as OrmSession

from ...models import (
    Download,
    DownloadEpisode,
    Episode,
    EpisodeFile,
    EpisodeVersion,
    Title,
    Version,
    VersionDefinition,
)
from ..downloads import store
from ..profiles import store as profile_store
from ..series import anime
from . import planning, settings, upgrade_guard

MAX_SEASONS = 5
MAX_EPISODES = 5
DAILY_DAYS = 14
#: Problems at the place: every further load of the version would fail the same way (decision 3).
PLACE_PROBLEMS = ("path_not_found", "no_space", "client_error")
#: Of two seasons due at the same time, the reason shown.
_PRIORITY = {**planning._PRIORITY, "air_date": 5}
_SEARCHING = frozenset(planning.SEARCHING_REASONS)


@dataclass(frozen=True)
class EpisodeFacts:
    """One episode in one version."""

    episode_id: int
    season: int
    air_date: str | None
    watched: bool
    has_file: bool
    cutoff_not_met: bool
    held: bool = False
    last_search_at: datetime | None = None
    #: Part 1 of a double is the episode's file and no part 2 stands beside it (decided on 25.09.2026): the
    #: episode counts as missing and its season is asked as a pack, the only release whose numbers show the halves.
    second_part_missing: bool = False

    @property
    def whole_file(self) -> bool:
        return self.has_file and not self.second_part_missing


@dataclass(frozen=True)
class VersionFacts:
    definition_id: int
    label: str
    own: bool
    #: The profile has series rules.
    has_rules: bool
    has_folder: bool
    #: A problem at the place holds the whole version.
    place_problem: bool = False
    episodes: tuple[EpisodeFacts, ...] = ()
    #: Replacements after a failure: time and season.
    replacements: tuple[tuple[datetime, int], ...] = ()
    #: The newest failed or refused download of the last 24 hours per season.
    last_failures: dict[int, datetime] = field(default_factory=dict)
    #: The series folder was read (decision 23).
    files_read: bool = True
    #: With unclear files: the day the version became nexcrate's, as an ISO date; missing episodes that aired before it
    #: wait (decision 18). None without unclear files.
    missing_from: str | None = None
    #: Unclear files of the version.
    unclear: int = 0

    @property
    def can_want(self) -> bool:
        return self.own and self.has_rules and self.has_folder and not self.place_problem and self.files_read

    def wants(self, episode: EpisodeFacts) -> bool:
        if not self.can_want or not episode.watched or episode.held:
            return False
        if episode.whole_file:
            return episode.cutoff_not_met
        return self.missing_from is None or (episode.air_date is not None and episode.air_date >= self.missing_from)


@dataclass(frozen=True)
class SeriesFacts:
    title_id: int
    series_type: str | None
    last_search_at: datetime | None
    stored_next_at: datetime | None
    stored_reason: str | None
    versions: tuple[VersionFacts, ...] = ()
    #: Season number to whether every regular episode of it has aired as a pack counts it (on or before today).
    complete: dict[int, bool] = field(default_factory=dict)
    #: The last search found a release that only the indexer's grab limit held back: when the indexer loads again.
    grab_free_at: datetime | None = None


@dataclass(frozen=True)
class SeasonPlan:
    season: int
    next_at: datetime | None
    reason: str
    #: Wanting episodes, of all versions.
    wanting: tuple[int, ...]
    #: Wanting episodes that have aired: what a search of the season asks for.
    searchable: tuple[int, ...]
    #: Of the searchable, those a version has no file for, and those only upgrades.
    missing: tuple[int, ...]
    upgrades: tuple[int, ...]
    #: Wanting episodes without an air date.
    no_date: int
    last_at: datetime | None
    #: Per searchable episode its own next time.
    due_at: dict[int, datetime] = field(default_factory=dict)

    def due(self, now: datetime) -> bool:
        return self.next_at is not None and self.next_at <= now and self.reason in _SEARCHING


@dataclass(frozen=True)
class Plan:
    wanted: bool
    next_at: datetime | None
    reason: str
    seasons: tuple[SeasonPlan, ...] = ()


# --- Times ---------------------------------------------------------------------------------------------------------- #


def anchor_at(air_date: str | None) -> datetime | None:
    """The day after the air date, 00:00 in the container's time zone, in UTC; None without a readable date."""
    if not air_date:
        return None
    try:
        day = date.fromisoformat(air_date[:10])
    except ValueError:
        return None
    following = day + timedelta(days=1)
    # A naive time is read in the local zone by ``astimezone``: the container's ``TZ``.
    return datetime(following.year, following.month, following.day).astimezone().astimezone(UTC)


def spread_fraction(title_id: int, season: int, base: datetime) -> float:
    """A fraction from 0 to below 1, the same for the same title, season and base time."""
    digest = hashlib.sha256(f"{title_id}|{season}|{planning.format_time(base)}".encode()).digest()
    return int.from_bytes(digest[:4], "big") / 2**32


def _spread(title_id: int, season: int, base: datetime, step: timedelta) -> datetime:
    return base + step * planning.SPREAD_SHARE * spread_fraction(title_id, season, base)


def episode_next(title_id: int, episode: EpisodeFacts, upgrade: bool, now: datetime) -> tuple[datetime | None, str]:
    """The next search of one wanting episode: its time and reason (``schedule``, ``air_date`` or ``no_date``)."""
    anchor = anchor_at(episode.air_date)
    if anchor is None:
        return None, "no_date"
    first_step = planning.interval(0, upgrade)
    if now < anchor:
        return _spread(title_id, episode.season, anchor, first_step), "air_date"
    last = episode.last_search_at
    if last is None or last < anchor:
        return _spread(title_id, episode.season, anchor, first_step), "schedule"
    step = planning.interval((last - anchor).days, upgrade)
    return _spread(title_id, episode.season, last, step) + step, "schedule"


# --- The plan ------------------------------------------------------------------------------------------------------- #


def season_plans(series: SeriesFacts, now: datetime) -> list[SeasonPlan]:
    """The plan of every season in which a version wants an episode, by season number."""
    if anime.not_searched("series", series.series_type):
        return []
    # Episode id to its facts, and per episode the versions that want it with whether they have a file.
    facts: dict[int, EpisodeFacts] = {}
    wanting_in: dict[int, list[bool]] = defaultdict(list)
    for version in series.versions:
        for episode in version.episodes:
            if version.wants(episode):
                facts.setdefault(episode.episode_id, episode)
                wanting_in[episode.episode_id].append(episode.whole_file)
    by_season: dict[int, list[int]] = defaultdict(list)
    for episode_id in sorted(wanting_in, key=lambda item: (facts[item].season, facts[item].air_date or "", item)):
        by_season[facts[episode_id].season].append(episode_id)

    plans: list[SeasonPlan] = []
    for season, episode_ids in sorted(by_season.items()):
        due_at: dict[int, datetime] = {}
        waiting: list[datetime] = []
        searchable: list[int] = []
        missing: list[int] = []
        no_date = 0
        for episode_id in episode_ids:
            episode = facts[episode_id]
            upgrade = all(wanting_in[episode_id])
            next_at, reason = episode_next(series.title_id, episode, upgrade, now)
            if reason == "no_date":
                no_date += 1
            elif reason == "air_date" and next_at is not None:
                waiting.append(next_at)
            elif next_at is not None:
                due_at[episode_id] = next_at
                searchable.append(episode_id)
                if not upgrade:
                    missing.append(episode_id)
        season_last = max(
            (facts[item].last_search_at for item in episode_ids if facts[item].last_search_at is not None),
            default=None,
        )
        if searchable:
            next_time: datetime | None = min(due_at.values())
            reason = "schedule"
            granted = [
                moment
                for version in series.versions
                for moment, replaced in version.replacements
                if replaced == season and (season_last is None or moment > season_last)
            ]
            if granted:
                next_time, reason = max(granted), "replacement"
            elif _replacement_limit(series, season, season_last, now):
                reason = "replacement_limit"
        elif waiting:
            next_time, reason = min(waiting), "air_date"
        else:
            next_time, reason = None, "no_date"
        plans.append(
            SeasonPlan(
                season=season,
                next_at=next_time,
                reason=reason,
                wanting=tuple(episode_ids),
                searchable=tuple(searchable),
                missing=tuple(missing),
                upgrades=tuple(item for item in searchable if item not in missing),
                no_date=no_date,
                last_at=season_last,
                due_at=due_at,
            )
        )
    return plans


def _replacement_limit(series: SeriesFacts, season: int, season_last: datetime | None, now: datetime) -> bool:
    window = now - planning.REPLACEMENT_WINDOW
    for version in series.versions:
        recent = [moment for moment, replaced in version.replacements if replaced == season and moment > window]
        failure = version.last_failures.get(season)
        if (
            len(recent) >= planning.REPLACEMENTS_PER_DAY
            and failure is not None
            and (season_last is None or failure > season_last)
        ):
            return True
    return False


def title_plan(series: SeriesFacts, now: datetime) -> Plan:
    seasons = season_plans(series, now)
    if not seasons:
        return Plan(wanted=False, next_at=None, reason="nothing_wanted")
    timed = [plan for plan in seasons if plan.next_at is not None]
    if not timed:
        return Plan(wanted=True, next_at=None, reason="no_date", seasons=tuple(seasons))
    first = min(timed, key=lambda plan: (plan.next_at, _PRIORITY[plan.reason], plan.season))
    next_at, reason = first.next_at, first.reason
    if next_at is not None:
        next_at, reason = planning.grab_limit_plan(next_at, reason, series.grab_free_at)
    stored_limit = series.stored_reason == "limit" and series.stored_next_at is not None and series.stored_next_at > now
    if stored_limit and reason in _SEARCHING and next_at is not None and next_at <= series.stored_next_at:
        next_at, reason = series.stored_next_at, "limit"
    return Plan(wanted=True, next_at=next_at, reason=reason, seasons=tuple(seasons))


# --- What one search asks ------------------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Asked:
    """The scope of a planned series search: seasons asked as seasons, episodes asked one by one, and every episode
    that gets the search time."""

    seasons: tuple[int, ...]
    episodes: tuple[int, ...]
    searched: tuple[int, ...]
    #: The seasons the search covers, for the budget's starts and the summary.
    covered: tuple[int, ...]

    def scope(self) -> dict[str, Any]:
        return {"kind": "planned", "seasons": list(self.seasons), "episodes": list(self.episodes)}


def _order(plan: SeasonPlan) -> tuple[int, datetime, int]:
    rank = 0 if plan.reason == "replacement" else (1 if plan.missing else 2)
    return rank, plan.next_at or datetime.max.replace(tzinfo=UTC), plan.season


def asked(
    series: SeriesFacts,
    plans: Collection[SeasonPlan],
    now: datetime,
    *,
    limit: int | None = MAX_SEASONS,
    due_only: bool = True,
    daily: bool = False,
) -> Asked | None:
    """What a search of these season plans asks (decisions 9 and 10); None when nothing is to be asked."""
    chosen = sorted((plan for plan in plans if plan.searchable and (plan.due(now) or not due_only)), key=_order)
    if limit is not None:
        chosen = chosen[:limit]
    seasons: list[int] = []
    episodes: list[int] = []
    searched: list[int] = []
    covered: list[int] = []
    today = now.astimezone().date()
    facts = _episode_facts(series)
    for plan in chosen:
        ids = sorted(plan.searchable, key=lambda item: (plan.due_at[item], item))
        if plan.season == 0:
            picked = ids[:MAX_EPISODES]
            episodes.extend(picked)
            searched.extend(picked)
        elif daily:
            recent = [
                item
                for item in ids
                if _within(facts[item].air_date, today, DAILY_DAYS) and not facts[item].second_part_missing
            ][:MAX_EPISODES]
            older = [
                item
                for item in ids
                if not _within(facts[item].air_date, today, DAILY_DAYS) or facts[item].second_part_missing
            ]
            episodes.extend(recent)
            searched.extend(recent)
            if older:
                seasons.append(plan.season)
                searched.extend(older)
        elif (
            (series.complete.get(plan.season, False) and len(ids) >= 2)
            or len(ids) > MAX_EPISODES
            or any(facts[item].second_part_missing for item in ids)
        ):
            seasons.append(plan.season)
            searched.extend(ids)
        else:
            episodes.extend(ids)
            searched.extend(ids)
        covered.append(plan.season)
    if not searched:
        return None
    return Asked(tuple(seasons), tuple(episodes), tuple(searched), tuple(covered))


def _episode_facts(series: SeriesFacts) -> dict[int, EpisodeFacts]:
    found: dict[int, EpisodeFacts] = {}
    for version in series.versions:
        for episode in version.episodes:
            found.setdefault(episode.episode_id, episode)
    return found


def _within(air_date: str | None, today: date, days: int) -> bool:
    try:
        aired = date.fromisoformat((air_date or "")[:10])
    except ValueError:
        return False
    return (today - aired).days <= days


# --- The database --------------------------------------------------------------------------------------------------- #


def parse_replacements(values: object) -> tuple[tuple[datetime, int], ...]:
    """``series_replacements`` as stored; anything unreadable is left out."""
    found: list[tuple[datetime, int]] = []
    for value in values if isinstance(values, list) else []:
        if not isinstance(value, dict) or not isinstance(value.get("season"), int):
            continue
        moments = planning.parse_times([value.get("at")])
        if moments:
            found.append((moments[0], int(value["season"])))
    return tuple(sorted(found))


def format_replacements(values: Collection[tuple[datetime, int]]) -> list[dict[str, Any]]:
    return [{"at": planning.format_time(moment), "season": season} for moment, season in sorted(values)]


def load_facts(db: OrmSession, title_ids: Collection[int], now: datetime) -> dict[int, SeriesFacts]:
    """What the plan reads of these series, in a few queries for all of them."""
    ids = sorted(set(title_ids))
    if not ids:
        return {}
    titles = list(db.scalars(select(Title).where(Title.id.in_(ids), Title.kind == "series")))
    ids = [title.id for title in titles]
    if not ids:
        return {}
    rows = (
        db.execute(
            select(Version, VersionDefinition.label, VersionDefinition.folder)
            .join(VersionDefinition, VersionDefinition.id == Version.version_definition_id)
            .where(Version.title_id.in_(ids))
            .order_by(Version.title_id, VersionDefinition.id)
        )
        .tuples()
        .all()
    )
    definitions = sorted({version.version_definition_id for version, _label, _folder in rows})
    series_rules = {
        version_id
        for version_id, profile in profile_store.by_versions(db, definitions).items()
        if isinstance(profile.rules, dict) and profile.rules.get("kind") == "series"
    }

    today = now.astimezone().date().isoformat()
    episodes: dict[int, tuple[int, int, str | None, datetime | None]] = {}
    complete: dict[int, dict[int, bool]] = defaultdict(dict)
    for episode_id, title_id, season, air_date, last in db.execute(
        select(Episode.id, Episode.title_id, Episode.season_number, Episode.air_date, Episode.last_search_at).where(
            Episode.title_id.in_(ids), Episode.tmdb_gone_at.is_(None)
        )
    ).tuples():
        episodes[episode_id] = (title_id, season, air_date, planning._aware(last))
        if season > 0:
            out = air_date is not None and air_date <= today
            complete[title_id][season] = complete[title_id].get(season, True) and out

    version_ids = [version.id for version, _label, _folder in rows]
    links: dict[int, list[tuple[int, bool, bool, bool, bool]]] = defaultdict(list)
    # Doubles: the episodes whose part 2 stands beside part 1, per version.
    seconds: set[tuple[int, int]] = set()
    if version_ids:
        seconds = {
            (version_id, episode_id)
            for version_id, episode_id in db.execute(
                select(EpisodeFile.version_id, EpisodeFile.part_of_episode_id).where(
                    EpisodeFile.version_id.in_(version_ids), EpisodeFile.part == 2
                )
            ).tuples()
            if episode_id is not None
        }
    # Paused upgrades (upgrade_guard): an episode file that was there when they were paused is not wanted.
    paused = upgrade_guard.Guard(paused_since=upgrade_guard.paused_since(db))
    if version_ids:
        for version_id, episode_id, watched, file_id, cutoff, added, part in db.execute(
            select(
                EpisodeVersion.version_id,
                EpisodeVersion.episode_id,
                EpisodeVersion.watched,
                EpisodeVersion.episode_file_id,
                EpisodeFile.cutoff_not_met,
                EpisodeFile.added_at,
                EpisodeFile.part,
            )
            .join(EpisodeFile, EpisodeFile.id == EpisodeVersion.episode_file_id, isouter=True)
            .where(EpisodeVersion.version_id.in_(version_ids))
        ).tuples():
            wanted = bool(cutoff) and not (file_id is not None and paused.blocks(added))
            half = part == 1 and (version_id, episode_id) not in seconds
            links[version_id].append((episode_id, bool(watched), file_id is not None, wanted, half))

    held: set[tuple[int, int, int]] = set()
    for episode_id, row in db.execute(
        select(DownloadEpisode.episode_id, Download)
        .join(Download, Download.id == DownloadEpisode.download_id)
        .where(
            Download.title_id.in_(ids),
            Download.state.in_(store.UNFINISHED_STATES),
            DownloadEpisode.state == "expected",
        )
    ).tuples():
        if store.blocks_loading(row) and row.version_definition_id is not None:
            held.add((row.title_id, row.version_definition_id, episode_id))
    place: set[tuple[int, int]] = {
        (title_id, definition_id)
        for title_id, definition_id in db.execute(
            select(Download.title_id, Download.version_definition_id).where(
                Download.title_id.in_(ids),
                Download.state == "problem",
                Download.problem_code.in_(PLACE_PROBLEMS),
                Download.scope.is_not(None),
            )
        ).tuples()
        if definition_id is not None
    }
    failed = or_(
        Download.state == "failed",
        and_(Download.state == "problem", Download.problem_code.in_(planning.REFUSED_PROBLEMS)),
    )
    failures: dict[tuple[int, int], dict[int, datetime]] = defaultdict(dict)
    for title_id, definition_id, updated, season in db.execute(
        select(Download.title_id, Download.version_definition_id, Download.updated_at, Episode.season_number)
        .join(DownloadEpisode, DownloadEpisode.download_id == Download.id)
        .join(Episode, Episode.id == DownloadEpisode.episode_id)
        .where(
            Download.title_id.in_(ids),
            Download.version_definition_id.is_not(None),
            failed,
            Download.updated_at > now - planning.REPLACEMENT_WINDOW,
        )
    ).tuples():
        moment = planning._aware(updated)
        if moment is None or definition_id is None:
            continue
        known = failures[(title_id, definition_id)].get(season)
        if known is None or moment > known:
            failures[(title_id, definition_id)][season] = moment

    unclear = unclear_counts(db, [version.id for version, _label, _folder in rows if version.source_id is None])

    by_title: dict[int, list[VersionFacts]] = defaultdict(list)
    for version, label, folder in rows:
        definition_id = version.version_definition_id
        facts: list[EpisodeFacts] = []
        for episode_id, watched, has_file, cutoff, half in links.get(version.id, []):
            known_episode = episodes.get(episode_id)
            if known_episode is None:
                continue
            _title_id, season, air_date, last = known_episode
            facts.append(
                EpisodeFacts(
                    episode_id=episode_id,
                    season=season,
                    air_date=air_date,
                    watched=watched,
                    has_file=has_file,
                    cutoff_not_met=cutoff,
                    held=(version.title_id, definition_id, episode_id) in held,
                    last_search_at=last,
                    second_part_missing=half,
                )
            )
        by_title[version.title_id].append(
            VersionFacts(
                definition_id=definition_id,
                label=label,
                own=version.source_id is None,
                has_rules=definition_id in series_rules,
                has_folder=bool(folder),
                place_problem=(version.title_id, definition_id) in place,
                episodes=tuple(sorted(facts, key=lambda item: item.episode_id)),
                replacements=parse_replacements(version.series_replacements),
                last_failures=dict(failures.get((version.title_id, definition_id), {})),
                files_read=version.files_read_at is not None,
                missing_from=_day_of(version.own_since) if unclear.get(version.id) else None,
                unclear=unclear.get(version.id, 0),
            )
        )
    return {
        title.id: SeriesFacts(
            title_id=title.id,
            series_type=title.series_type,
            last_search_at=title.last_search_at,
            stored_next_at=title.next_search_at,
            stored_reason=title.next_search_reason,
            versions=tuple(by_title.get(title.id, [])),
            complete=dict(complete.get(title.id, {})),
            grab_free_at=planning.grab_free_at(title.search_summary),
        )
        for title in titles
    }


def _day_of(moment: datetime | None) -> str:
    """The local day of a moment; without one a day in the far future, so every missing episode waits instead of
    being wanted (a version that became nexcrate's own without a time is the unknown case)."""
    aware = planning._aware(moment)
    return aware.astimezone().date().isoformat() if aware is not None else "9999-12-31"


def unclear_counts(db: OrmSession, version_ids: Collection[int]) -> dict[int, int]:
    """Per version its unclear files: files of its own with no episode that the owner did not leave out."""
    ids = sorted(set(version_ids))
    if not ids:
        return {}
    linked = select(EpisodeVersion.episode_file_id).where(
        EpisodeVersion.version_id.in_(ids), EpisodeVersion.episode_file_id.is_not(None)
    )
    counts: dict[int, int] = defaultdict(int)
    for (version_id,) in db.execute(
        select(EpisodeFile.version_id).where(
            EpisodeFile.version_id.in_(ids),
            EpisodeFile.left_out.is_(False),
            EpisodeFile.id.not_in(linked),
            EpisodeFile.part_of_episode_id.is_(None),
        )
    ).tuples():
        counts[version_id] += 1
    return dict(counts)


# --- What the series page shows ------------------------------------------------------------------------------------ #


def _summary_seasons(value: object) -> dict[int, dict[str, Any]]:
    if not isinstance(value, dict) or not isinstance(value.get("seasons"), list):
        return {}
    return {
        item["season"]: item
        for item in value["seasons"]
        if isinstance(item, dict) and isinstance(item.get("season"), int) and isinstance(item.get("versions"), list)
    }


def summary_head(value: object) -> dict[str, Any] | None:
    """The top of a series summary in the shape of a movie's, without versions; None for another shape."""
    if not isinstance(value, dict) or not isinstance(value.get("seasons"), list):
        return None
    if not {"at", "origin", "releases", "indexers"} <= set(value) or not isinstance(value["indexers"], list):
        return None
    return {key: value[key] for key in ("at", "origin", "releases", "indexers")} | {"versions": []}


def search_plan(db: OrmSession, title: Title, now: datetime) -> dict[str, Any]:
    """``search_plan`` of a series (S5.9): the title's plan and a line per season that wants something, with the last
    result of that season. With the series switch off, a season or title whose time came shows ``off``."""
    enabled = settings.load_enabled(db, "series")
    fact = load_facts(db, [title.id], now).get(title.id)
    plan = title_plan(fact, now) if fact is not None else Plan(wanted=False, next_at=None, reason="nothing_wanted")

    def shown(reason: str, next_at: datetime | None) -> str:
        due = next_at is not None and next_at <= now and reason in _SEARCHING
        return "off" if not enabled and due else reason

    results = _summary_seasons(title.search_summary)
    # What holds an own version back (decisions 18 and 23).
    held: list[dict[str, Any]] = []
    for version in fact.versions if fact is not None else ():
        if not version.own:
            continue
        if not version.files_read:
            held.append({"version_id": version.definition_id, "reason": "reading_files", "count": 0, "since": None})
        elif version.unclear:
            held.append(
                {
                    "version_id": version.definition_id,
                    "reason": "unclear_files",
                    "count": version.unclear,
                    "since": version.missing_from,
                }
            )
    return {
        "automatic": enabled,
        "wanted": plan.wanted,
        "last_at": title.last_search_at,
        "next_at": plan.next_at,
        "reason": shown(plan.reason, plan.next_at),
        "anchor": {"date": None, "kind": "none", "country": None},
        "summary": summary_head(title.search_summary),
        "held": held,
        "seasons": [
            {
                "season": season.season,
                "next_at": season.next_at,
                "reason": shown(season.reason, season.next_at),
                "missing": len(season.missing),
                "upgrades": len(season.upgrades),
                "waiting": len(season.wanting) - len(season.searchable) - season.no_date,
                "no_date": season.no_date,
                "last_at": season.last_at,
                "result": results.get(season.season),
            }
            for season in plan.seasons
        ],
    }
