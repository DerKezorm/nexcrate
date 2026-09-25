"""Searching a series: what to ask the indexers, which release belongs, and what nexcrate would take
(S3.3 to S3.5).

The movie search of step 2c runs the indexers; this module gives it the series' queries and judges the answers.
``load`` reads the database once; everything else is pure.

**Scope.** ``series`` searches every regular season in which a version watches an aired episode, plus the watched
specials one by one (decision 19). When no version watches an aired episode, it searches every regular season that
has aired, so the owner sees what exists; nothing is taken then, as nothing is watched (changed after the owner's test,
16.09.2026). ``season`` searches one TMDB season, ``episode`` one episode.

**One release twice:** an indexer that lists the same release under two ids (same name, same size) shows it once, the
newest of them (the owner's decision, 16.09.2026).

**Queries** per indexer and target, as Sonarr builds them (decisions 16 to 22):

1. By number: ``t=tvsearch`` with every id the caps list and the series has (``tvdbid``, ``tmdbid``, ``imdbid``),
   ``season`` and, for an episode, ``ep``. Only when the caps offer tv-search with ``season`` (and ``ep`` for an
   episode). A daily episode asks ``season=YYYY`` and ``ep=MM/DD``; season 0 goes out as ``00``.
2. By title, only when step 1 was not possible or found nothing: the owner's search titles, the title, the English
   title, the original title and TheXEM's names, in the three spellings of ``schreibweisen``, at most
   ``MAX_TITLE_QUERIES``. With tv-search and ``q`` and ``season`` in its parameters: ``t=tvsearch`` with them;
   otherwise ``t=search`` with the numbers in the text ("Title S02E05", "Title S02", "Title 2026 09 16").
3. A special also asks ``t=search`` with the series title and the episode's English name.

The numbers asked come from the first scheme that knows the episode among ``owner``, ``scene`` and ``tvdb``, else
TMDB's own (decision 16): releases count by scene or TVDB. A TMDB season whose episodes lie in several release
seasons asks each of them, and, as Sonarr does, also its TVDB and its own season number when those differ (measured
at B2, 16.09.2026); at most ``MAX_RELEASE_SEASONS``.

**Belonging** (decision 26): a release whose ``tvdbid``, ``tmdbid`` or ``imdb`` equals the series' number belongs; one
naming another series does not, whatever its name says. Without comparable numbers the title read from the name must
share a spelling key with a title of the series (its titles, TMDB's alternative titles, the search titles, TheXEM's
names), and a year in the name must be within one year of the series' year.

**Deciding** (decisions 28 and 29): the series engine of S2 judges every belonging release for every version with a
series profile. The order is Sonarr's: quality, score, a pack before single releases, fewer episodes, the lower first
episode, then indexer priority, seeders or age, and size as in the movie search. "Would take" is a set: releases in
that order join when they fill or replace an episode no earlier one covers. Releases of episodes outside the search
never join. Another reading of a release (``match.ambiguous``) is a hint only: the first numbering in the order
decides, as releases count by scene or TVDB (changed after the owner's indexer, 16.09.2026).

As Sonarr checks the blocklist and its queue before taking (after the review, 17.09.2026): a release on the title's
blocklist never joins, and an episode a running download of the version holds counts as covered, so the set takes the
others instead of refusing a whole pack.

**Anime** (A3): an anime series is asked by season and episode as above and, like Sonarr with
anime categories, by its number counted through: ``t=tvsearch`` with the series' ids and the number as ``q``, then
``t=search`` with the title and the number (``Title 05``, ``Title 148``), at most ``MAX_ANIME_QUERIES`` per target
(the order Sonarr asks in, measured at the bench on 22.09.2026). A season asks the title alone as well, which brings the
batches and the weekly releases named by that number. Every query of an anime series goes to the series categories
and the indexer's anime categories (``categories_for``). The names are read for an anime series (``anime=True``).
The number asked is the scene's count through when the episode has one, else the series' own, as Sonarr asks
(``SceneAbsoluteEpisodeNumber ?? AbsoluteEpisodeNumber``, measured at the bench on 23.09.2026: Frieren's S02E02 is
asked as ``02`` with the scene names of season 2, not as 30; the design notes, B6).

**A pack for a few episodes** (the owner's answer of 17.09.2026): a season pack that would bring fewer than half of its
episodes gives way to releases of fewer episodes ranked after it, when those bring every one of them and are no worse:
the same quality or better, and the same score or more once the pack's bonus for being one ("Season Pack") is left out.
Without such releases the pack stays. The decision names the packs left out (``packs_left_out``). Sonarr asks for a
single missing episode on its own.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select

from ...models import (
    Episode,
    EpisodeFile,
    EpisodeVersion,
    Source,
    Title,
    TitleAlias,
    Version,
    VersionDefinition,
    XemName,
)
from .. import delay as delay_rules
from .. import indexers, releases, schreibweisen
from .. import tags as tag_store
from ..downloads import store as download_store
from ..profiles import store as profile_store
from ..releases import decision as engine
from ..releases import qualities_series as qualities
from ..series import release_match, watching
from ..series.parts import as_whole
from ..series.store import code as episode_code
from . import plan, ranking
from .model import IndexerInfo, imdb_number

#: Title queries per indexer and target, over all texts and spellings.
MAX_TITLE_QUERIES = 12
#: Queries by the number counted through, or by the title alone, per indexer and target of an anime series.
MAX_ANIME_QUERIES = 6
#: Release seasons one TMDB season is asked in.
MAX_RELEASE_SEASONS = 3
#: The time of a season, and of a whole series search at most (decision 23).
SECONDS_PER_TARGET = 180.0
MAX_SECONDS = 900.0
#: A year in the name may be this far from the series' first year.
YEAR_TOLERANCE = 1
SERIES = "series"
SEASON = "season"
EPISODE = "episode"
#: Scopes of the automatic searches: seasons asked as seasons plus episodes one by one, and
#: releases RSS fetched for the wanting episodes, without a query.
PLANNED = "planned"
RSS = "rss"
SKIPPED_CODE = "indexer_no_series_categories"
#: The id parameters a series query may carry, in Sonarr's order.
ID_PARAMS = ("tvdbid", "imdbid", "tmdbid")


class ScopeInvalid(Exception):
    """The scope does not fit the series."""


# --- What the search knows ------------------------------------------------------------------------ #


@dataclass(frozen=True)
class Target:
    #: season or episode.
    kind: str
    #: What the owner sees: ``S02``, ``S02E07``, a date for a daily episode.
    code: str
    #: The numbers releases give: season (or year) and episode.
    season: int
    episode: int | None = None
    #: A daily episode's air date, ``YYYY-MM-DD``.
    air_date: str | None = None
    #: A special's English name, for the query by name.
    special_name: str | None = None
    #: The number an anime series counts the episode by (A3); None otherwise.
    absolute: int | None = None


@dataclass(frozen=True)
class VersionState:
    #: The version definition, as everywhere in the API.
    version_id: int
    label: str
    has_profile: bool
    rules: dict[str, Any] | None
    #: The name of the source that feeds the version, or None.
    fed_by: str | None
    #: Episode id to whether the version watches it and the file it has.
    watched: dict[int, bool] = field(default_factory=dict)
    files: dict[int, releases.CurrentEpisodeFile] = field(default_factory=dict)
    #: Episodes a running download of the version holds: covered for what would be taken.
    held: frozenset[int] = frozenset()
    #: The delay rule of the version: a protocol switched off does not fit, the preferred one wins a tie.
    delay: delay_rules.Rule = delay_rules.DEFAULT


@dataclass(frozen=True)
class SeriesSearch:
    title_id: int
    title: str
    title_en: str | None
    original_title: str | None
    year: int | None
    tmdb_id: int | None
    tvdb_id: int | None
    imdb_id: int | None
    original_language: str | None
    runtime_min: int | None
    series_type: str | None
    #: The scope as the API shows it.
    scope: dict[str, Any]
    targets: tuple[Target, ...]
    #: Episodes the search is for; releases of others stay in the list but never join what nexcrate would take.
    episode_ids: frozenset[int]
    numbering: release_match.Numbering
    #: Texts for title queries, in order: search titles, title, English title, original title, TheXEM's names.
    texts: tuple[str, ...]
    #: Every spelling key of every title of the series.
    keys: frozenset[str]
    versions: tuple[VersionState, ...]
    #: ``YYYY-MM-DD``: episodes before it have aired.
    today: str
    #: The title's blocklist as ``(protocol, release title, info hash)``: such a release never joins what is taken.
    blocklist: tuple[tuple[str, str, str | None], ...] = ()

    @property
    def kind(self) -> str:
        return SERIES

    @property
    def search_seconds(self) -> float:
        seasons = sum(1 for target in self.targets if target.kind == SEASON) or 1
        return min(MAX_SECONDS, SECONDS_PER_TARGET * seasons)

    @property
    def known_titles(self) -> tuple[str, ...]:
        return tuple(text for text in (self.title, self.title_en, self.original_title, *self.texts) if text)


# --- Loading ------------------------------------------------------------------------------------ #


def _release_numbers(numbering: release_match.Numbering) -> dict[int, tuple[int, int]]:
    """Episode id to the numbers releases give it: owner, scene, tvdb, else TMDB's (decision 16)."""
    found: dict[int, tuple[int, int]] = {}
    for scheme in ("owner", "scene", "tvdb"):
        for numbers, episode_id in numbering.schemes.get(scheme, release_match.Scheme()).by_number.items():
            found.setdefault(episode_id, numbers)
    for episode_id, episode in numbering.episodes.items():
        found.setdefault(episode_id, (episode.season, episode.episode))
    return found


def _texts(title: Title, aliases: list[str], xem_names: list[tuple[str, int | None]], seasons: set[int]) -> list[str]:
    ordered: list[str] = []
    candidates = [
        *aliases,
        title.title,
        title.title_en,
        title.original_title,
        *(text for text, season in xem_names if season is None or season in seasons),
    ]
    for text in candidates:
        if text and all(not schreibweisen.folds_alike(text, existing) for existing in ordered):
            ordered.append(text)
    return ordered


def _targets(
    title: Title,
    numbering: release_match.Numbering,
    scope: dict[str, Any],
    versions: list[VersionState],
    today: str,
    special_names: dict[int, str],
) -> tuple[list[Target], set[int]]:
    numbers = _release_numbers(numbering)
    episodes = numbering.episodes
    daily = title.series_type == "daily"
    counted = {episode_id: number for number, episode_id in numbering.by_absolute.items()}
    # As Sonarr: the scene's number counted through goes first (B6).
    for number, places in numbering.by_scene_absolute.items():
        for _season, episode_id in places:
            counted[episode_id] = number

    def episode_target(episode_id: int) -> Target:
        episode = episodes[episode_id]
        if daily and episode.air_date:
            return Target(
                kind=EPISODE,
                code=episode.air_date,
                season=int(episode.air_date[:4]),
                episode=None,
                air_date=episode.air_date,
            )
        season, number = numbers[episode_id]
        return Target(
            kind=EPISODE,
            code=episode_code(season, number),
            season=season,
            episode=number,
            special_name=special_names.get(episode_id) if episode.special else None,
            absolute=counted.get(episode_id) if title.series_type == "anime" else None,
        )

    def season_targets(season: int) -> list[Target]:
        members = [episode_id for episode_id, episode in episodes.items() if episode.season == season]
        if daily:
            years = sorted({int(episodes[item].air_date[:4]) for item in members if episodes[item].air_date})
            return [Target(kind=SEASON, code=str(year), season=year) for year in years[-MAX_RELEASE_SEASONS:]]
        counted: dict[int, int] = defaultdict(int)
        for episode_id in members:
            counted[numbers[episode_id][0]] += 1
        chosen = sorted(counted, key=lambda value: (-counted[value], value))[:MAX_RELEASE_SEASONS]
        # As Sonarr: a season whose release numbers differ is also asked by its TVDB and its own number (B2).
        own = [season]
        tvdb = numbering.schemes.get("tvdb")
        if tvdb is not None:
            tvdb_season = {episode_id: key[0] for key, episode_id in tvdb.by_number.items()}
            own = sorted({tvdb_season[item] for item in members if item in tvdb_season}) + own
        for value in own:
            if value not in chosen and len(chosen) < MAX_RELEASE_SEASONS:
                chosen.append(value)
        return [Target(kind=SEASON, code=f"S{value:02d}", season=value) for value in sorted(chosen)]

    kind = scope["kind"]
    if kind == PLANNED:
        targets: list[Target] = []
        wanted: set[int] = set()
        for season in scope.get("seasons") or []:
            members = {episode_id for episode_id, episode in episodes.items() if episode.season == season}
            if members:
                targets.extend(season_targets(season))
                wanted.update(members)
        for episode_id in scope.get("episodes") or []:
            if episode_id in episodes and episode_id not in wanted:
                targets.append(episode_target(episode_id))
                wanted.add(episode_id)
        if not targets:
            raise ScopeInvalid
        return targets, wanted
    if kind == RSS:
        listed = {episode_id for episode_id in scope.get("episodes") or [] if episode_id in episodes}
        if not listed:
            raise ScopeInvalid
        return [], listed
    if kind == EPISODE:
        episode_id = scope["episode_id"]
        if episode_id not in episodes:
            raise ScopeInvalid
        return [episode_target(episode_id)], {episode_id}
    if kind == SEASON:
        season = scope["season"]
        members = {episode_id for episode_id, episode in episodes.items() if episode.season == season}
        if not members:
            raise ScopeInvalid
        if season == 0:
            return [episode_target(episode_id) for episode_id in sorted(members)], members
        return season_targets(season), members

    targets: list[Target] = []
    wanted: set[int] = set()
    watched_aired = {
        episode_id
        for version in versions
        for episode_id, on in version.watched.items()
        if on and episode_id in episodes and watching.aired(episodes[episode_id].air_date, today)
    }
    if not watched_aired:
        aired_seasons = {
            episode.season
            for episode in episodes.values()
            if episode.season > 0 and watching.aired(episode.air_date, today)
        }
        for season in sorted(aired_seasons):
            targets.extend(season_targets(season))
            wanted.update(episode_id for episode_id, episode in episodes.items() if episode.season == season)
        return targets, wanted
    for season in sorted({episodes[episode_id].season for episode_id in watched_aired}):
        members = {episode_id for episode_id, episode in episodes.items() if episode.season == season}
        if season == 0:
            specials = sorted(members & watched_aired)
            targets.extend(episode_target(episode_id) for episode_id in specials)
            wanted.update(specials)
            continue
        targets.extend(season_targets(season))
        wanted.update(members)
    return targets, wanted


def _file(row: EpisodeFile, covered: int, second_part_missing: bool = False) -> releases.CurrentEpisodeFile:
    return releases.CurrentEpisodeFile(
        release_title=row.release_title,
        name=row.relative_path,
        quality=row.quality,
        release_type=row.release_type,
        size_bytes=as_whole(row.size, row.part),
        episode_count=max(1, covered),
        file_id=row.id,
        languages=releases.stored_languages(row.languages),
        second_part_missing=second_part_missing,
    )


def load(db: Any, title_id: int, scope: dict[str, Any], today: str | None = None) -> SeriesSearch | None:
    """The series, the scope's targets and the versions; None when the title is gone or no series. Raises
    ``ScopeInvalid``."""
    title = db.get(Title, title_id)
    if title is None or title.kind != "series":
        return None
    on = today or watching.today()
    numbering = release_match.load(db, title)
    special_names = {
        episode_id: name
        for episode_id, name in db.execute(
            select(Episode.id, Episode.name_en).where(Episode.title_id == title.id, Episode.season_number == 0)
        ).all()
        if name
    }

    versions: list[VersionState] = []
    rows = db.execute(
        select(Version, VersionDefinition.label, VersionDefinition.delay)
        .join(VersionDefinition, VersionDefinition.id == Version.version_definition_id)
        .where(Version.title_id == title.id)
        .order_by(VersionDefinition.label, Version.id)
    ).all()
    definition_ids = [version.version_definition_id for version, _label, _delay in rows]
    profiles = profile_store.by_versions(db, definition_ids)
    for version, label, stored_delay in rows:
        profile = profiles.get(version.version_definition_id)
        rules = profile_store.series_rules(profile, title.series_type)
        watched: dict[int, bool] = {}
        file_of: dict[int, int] = {}
        covered: dict[int, int] = defaultdict(int)
        for row in db.scalars(select(EpisodeVersion).where(EpisodeVersion.version_id == version.id)):
            watched[row.episode_id] = bool(row.watched)
            if row.episode_file_id is not None:
                file_of[row.episode_id] = row.episode_file_id
                covered[row.episode_file_id] += 1
        stored = {row.id: row for row in db.scalars(select(EpisodeFile).where(EpisodeFile.version_id == version.id))}
        seconds = {row.part_of_episode_id for row in stored.values() if row.part == 2}
        fed_by = None
        if version.source_id is not None:
            fed_by = db.scalar(select(Source.name).where(Source.id == version.source_id))
        versions.append(
            VersionState(
                version_id=version.version_definition_id,
                label=label,
                has_profile=profile is not None,
                rules=rules if rules is not None and rules.get("kind") == "series" else None,
                fed_by=fed_by,
                watched=watched,
                files={
                    episode_id: _file(
                        stored[file_id],
                        covered[file_id],
                        stored[file_id].part == 1 and episode_id not in seconds,
                    )
                    for episode_id, file_id in file_of.items()
                    if file_id in stored
                },
                held=frozenset(download_store.downloading_episodes(db, title.id, version.version_definition_id)),
                delay=delay_rules.for_title(stored_delay, tag_store.title_tag_ids(db, title.id)),
            )
        )

    targets, wanted = _targets(title, numbering, scope, versions, on, special_names)
    seasons = {target.season for target in targets}
    aliases = list(db.scalars(select(TitleAlias.text).where(TitleAlias.title_id == title.id).order_by(TitleAlias.id)))
    xem_names: list[tuple[str, int | None]] = []
    keys: set[str] = set()
    if title.tvdb_id is not None:
        for row in db.scalars(select(XemName).where(XemName.tvdb_id == title.tvdb_id).order_by(XemName.id)):
            xem_names.append((row.text, row.season))
            keys.update(key for key in row.search_keys.split(schreibweisen.SEPARATOR) if key)
    texts = _texts(title, aliases, xem_names, seasons)
    for text in (title.title, title.title_en, title.original_title, *aliases):
        keys.update(schreibweisen.keys(text))
    for stored_keys in (title.search_keys, title.tmdb_search_keys):
        keys.update(key for key in (stored_keys or "").split(schreibweisen.SEPARATOR) if key)
    shown = dict(scope)
    if scope["kind"] == EPISODE:
        episode = numbering.episodes[scope["episode_id"]]
        shown["code"] = episode.code
    elif scope["kind"] == SEASON:
        shown["code"] = f"S{scope['season']:02d}"
    return SeriesSearch(
        title_id=title.id,
        title=title.title,
        title_en=title.title_en,
        original_title=title.original_title,
        year=title.year,
        tmdb_id=title.tmdb_id if title.tmdb_id and title.tmdb_id > 0 else None,
        tvdb_id=title.tvdb_id,
        imdb_id=imdb_number(title.imdb_id),
        original_language=title.original_language,
        runtime_min=title.runtime,
        series_type=title.series_type,
        scope=shown,
        targets=tuple(targets),
        episode_ids=frozenset(wanted),
        numbering=numbering,
        texts=tuple(texts),
        keys=frozenset(keys),
        versions=tuple(versions),
        today=on,
        blocklist=tuple(
            (entry.protocol, entry.release_title, entry.info_hash) for entry in download_store.blocklist(db, title.id)
        ),
    )


# --- Queries (pure) ------------------------------------------------------------------------------ #


def _numbers(target: Target, params: set[str]) -> dict[str, str | int] | None:
    """``season`` and ``ep`` for a target, or None when the caps do not take them."""
    if "season" not in params:
        return None
    season = "00" if target.season == 0 else target.season
    if target.kind == SEASON:
        return {"season": season}
    if "ep" not in params:
        return None
    if target.air_date is not None:
        return {"season": target.season, "ep": f"{target.air_date[5:7]}/{target.air_date[8:10]}"}
    return {"season": season, "ep": target.episode if target.episode is not None else 0}


def _text_suffix(target: Target) -> str:
    if target.air_date is not None:
        return f"{target.air_date[:4]} {target.air_date[5:7]} {target.air_date[8:10]}"
    if target.kind == SEASON:
        return f"S{target.season:02d}"
    return episode_code(target.season, target.episode or 0)


def id_query(caps: dict[str, Any] | None, series: SeriesSearch, target: Target) -> plan.Query | None:
    used = plan.caps_in_use(caps)
    if not used.get("tv_search"):
        return None
    listed = set(used.get("tv_params") or [])
    numbers = _numbers(target, listed)
    if numbers is None:
        return None
    params: dict[str, str | int] = {"t": "tvsearch"}
    if "tvdbid" in listed and series.tvdb_id:
        params["tvdbid"] = series.tvdb_id
    if "imdbid" in listed and series.imdb_id:
        params["imdbid"] = f"{series.imdb_id:07d}"
    if "tmdbid" in listed and series.tmdb_id:
        params["tmdbid"] = series.tmdb_id
    if len(params) == 1:
        return None
    params.update(numbers)
    return plan.Query("id", target.code, params)


def title_queries(caps: dict[str, Any] | None, series: SeriesSearch, target: Target) -> list[plan.Query]:
    used = plan.caps_in_use(caps)
    tv_params = set(used.get("tv_params") or [])
    numbers = _numbers(target, tv_params) if used.get("tv_search") and "q" in tv_params else None
    plain = "q" in (used.get("search_params") or [])
    queries: list[plan.Query] = []
    for text in series.texts:
        for spelling in schreibweisen.query_spellings(text):
            if len(queries) >= MAX_TITLE_QUERIES:
                return queries
            if numbers is not None:
                query = plan.Query("title", f"{spelling}, {target.code}", {"t": "tvsearch", "q": spelling, **numbers})
            elif plain:
                words = f"{spelling} {_text_suffix(target)}"
                query = plan.Query("title", words, {"t": "search", "q": words})
            else:
                return queries
            if all(existing.params != query.params for existing in queries):
                queries.append(query)
    return queries


def anime_queries(caps: dict[str, Any] | None, series: SeriesSearch, target: Target) -> list[plan.Query]:
    """An anime series' queries by its number counted through (an episode) or by the title alone (a season)."""
    if series.series_type != "anime" or target.kind == EPISODE and target.absolute is None:
        return []
    used = plan.caps_in_use(caps)
    if "q" in (used.get("search_params") or []):
        mode = "search"
    elif used.get("tv_search") and "q" in (used.get("tv_params") or []):
        mode = "tvsearch"
    else:
        return []
    suffix = f" {target.absolute:02d}" if target.absolute is not None else ""
    queries: list[plan.Query] = []
    tv_params = set(used.get("tv_params") or [])
    if target.absolute is not None and used.get("tv_search") and "q" in tv_params:
        # As Sonarr asks first (measured 22.09.2026): the series' ids with the number as the text.
        ids: dict[str, str | int] = {}
        if "tvdbid" in tv_params and series.tvdb_id:
            ids["tvdbid"] = series.tvdb_id
        if "imdbid" in tv_params and series.imdb_id:
            ids["imdbid"] = f"{series.imdb_id:07d}"
        if "tmdbid" in tv_params and series.tmdb_id:
            ids["tmdbid"] = series.tmdb_id
        if ids:
            number = f"{target.absolute:02d}"
            queries.append(plan.Query("id", f"{target.code}, {number}", {"t": "tvsearch", **ids, "q": number}))
    for text in series.texts:
        for spelling in schreibweisen.query_spellings(text):
            if len(queries) >= MAX_ANIME_QUERIES:
                return queries
            words = f"{spelling}{suffix}"
            query = plan.Query("title", words, {"t": mode, "q": words})
            if all(existing.params != query.params for existing in queries):
                queries.append(query)
    return queries


def anime_categories(info: IndexerInfo) -> tuple[int, ...]:
    """The anime categories of an indexer: its own choice, else the default from its caps."""
    if info.anime_categories is not None:
        return info.anime_categories
    return tuple(indexers.default_anime_categories(info.caps))


def categories_for(series: SeriesSearch, info: IndexerInfo) -> tuple[int, ...]:
    """The categories a series asks at an indexer: the series categories, for an anime series with its anime ones."""
    if series.series_type != "anime":
        return info.series_categories
    return tuple(dict.fromkeys((*info.series_categories, *anime_categories(info))))


def name_query(caps: dict[str, Any] | None, series: SeriesSearch, target: Target) -> plan.Query | None:
    """A special by the series title and its English name (decision 21)."""
    if target.special_name is None or "q" not in (plan.caps_in_use(caps).get("search_params") or []):
        return None
    words = next(iter(schreibweisen.query_spellings(f"{series.title_en or series.title} {target.special_name}")), None)
    return plan.Query("title", words, {"t": "search", "q": words}) if words else None


# --- Belonging (pure) ---------------------------------------------------------------------------- #


def belongs(series: SeriesSearch, release: indexers.Release, parsed: releases.ParsedRelease) -> bool:
    comparable = [
        (release.tvdb_id, series.tvdb_id),
        (release.tmdb_id, series.tmdb_id),
        (release.imdb_id, series.imdb_id),
    ]
    compared = [(found, own) for found, own in comparable if found is not None and own is not None]
    if compared:
        return any(found == own for found, own in compared) and all(found == own for found, own in compared)
    read = parsed.series
    if read.year is not None and series.year is not None and abs(read.year - series.year) > YEAR_TOLERANCE:
        return False
    return not series.keys.isdisjoint(read.title_keys)


# --- Deciding (pure) ----------------------------------------------------------------------------- #


_aired_for_pack = watching.out_for_pack


def _context(
    series: SeriesSearch,
    version: VersionState,
    found: release_match.Match,
    parsed: releases.ParsedRelease,
    release: indexers.Release,
    info: IndexerInfo,
) -> releases.SeriesContext:
    episodes: list[releases.EpisodeInfo] = []
    not_aired: list[str] = []
    for episode_id in found.episode_ids:
        episode = series.numbering.episodes.get(episode_id)
        if episode is None:
            continue
        has_aired = watching.aired(episode.air_date, series.today)
        if not _aired_for_pack(episode.air_date, series.today) and not episode.special:
            not_aired.append(episode.code)
        episodes.append(
            releases.EpisodeInfo(
                episode_id=episode.id,
                code=episode.code,
                runtime_min=episode.runtime,
                aired=has_aired,
                watched=version.watched.get(episode_id, False),
                special=episode.special,
                file=version.files.get(episode_id),
            )
        )
    if parsed.series.release_type == "season_pack" and found.episode_ids:
        season = release_match.season_of(series.numbering, found)
        for episode in series.numbering.episodes.values():
            if (
                season is not None
                and episode.season == season
                and not episode.special
                and not _aired_for_pack(episode.air_date, series.today)
                and episode.code not in not_aired
            ):
                not_aired.append(episode.code)
    return releases.SeriesContext(
        original_language=series.original_language,
        runtime_min=series.runtime_min,
        size_bytes=release.size_bytes,
        indexer_flags=indexers.as_sonarr(release.indexer_flags),
        multi_languages=info.multi_languages,
        episodes=tuple(episodes),
        with_series=True,
        not_aired=tuple(not_aired) if parsed.series.release_type == "season_pack" else (),
        title_matches=not series.keys.isdisjoint(parsed.series.title_keys) if parsed.series.title_keys else True,
    )


@dataclass
class _Candidate:
    key: str
    order: int
    protocol: str
    priority: int
    seeders: int | None
    peers: int | None
    age_hours: float | None
    size_bytes: int | None
    quality_rank: int
    score: int
    pack: bool
    episode_count: int
    first_episode: int
    placed: dict[str, Any]
    in_scope: bool
    #: On the title's blocklist: ranked and shown, never taken.
    blocked: bool = False
    #: What it should weigh by the preferred size of its quality; None without one or without a known runtime.
    preferred_bytes: int | None = None
    #: 0 for the protocol the version's delay rule prefers, 1 for the other.
    protocol_step: int = 0


def _blocked(series: SeriesSearch, protocol: str, title: str, info_hash: str | None) -> bool:
    wanted = schreibweisen.nfc(title).casefold()
    for entry_protocol, entry_title, entry_hash in series.blocklist:
        if info_hash and entry_hash and entry_hash.lower() == info_hash.lower():
            return True
        if entry_protocol == protocol and schreibweisen.nfc(entry_title).casefold() == wanted:
            return True
    return False


def _sort_key(candidate: _Candidate) -> tuple[Any, ...]:
    """Sonarr's order (S2 decision 29) with the movie search's indexer, seeder, age and size steps. The protocol
    the version's delay rule prefers comes third, where Sonarr's comparer has it."""
    torrent = candidate.protocol == "torrent"
    return (
        -candidate.quality_rank,
        -candidate.score,
        candidate.protocol_step,
        0 if candidate.pack else 1,
        candidate.episode_count,
        candidate.first_episode,
        candidate.priority,
        -ranking.magnitude(candidate.seeders) if torrent else 0,
        -ranking.magnitude(candidate.peers) if torrent else 0,
        -ranking.age_bucket(candidate.age_hours) if not torrent else 0,
        -ranking.size_step(candidate.size_bytes, candidate.preferred_bytes),
        candidate.order,
    )


def distinct(found: dict[str, indexers.Release]) -> dict[str, indexers.Release]:
    """One release per name and size of one indexer, the newest; the order found stays."""
    newest: dict[tuple[str, int | None], str] = {}
    for key, release in found.items():
        same = (release.title, release.size_bytes)
        kept = newest.get(same)
        if kept is None:
            newest[same] = key
            continue
        before = found[kept].published_at
        if release.published_at is not None and (before is None or release.published_at > before):
            newest[same] = key
    chosen = set(newest.values())
    return {key: release for key, release in found.items() if key in chosen}


def _age_hours(release: indexers.Release, moment: datetime) -> float | None:
    if release.published_at is None:
        return None
    return (moment - release.published_at).total_seconds() / 3600


def _codes(series: SeriesSearch, ids: list[int]) -> list[str]:
    return [series.numbering.episodes[episode_id].code for episode_id in sorted(ids, key=_order_of(series))]


def _order_of(series: SeriesSearch) -> Any:
    def key(episode_id: int) -> tuple[int, int, int]:
        episode = series.numbering.episodes[episode_id]
        return episode.season, episode.episode, episode.id

    return key


def evaluate(
    series: SeriesSearch, states: list[Any], moment: datetime
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The ``versions`` and ``releases`` of a series search's answer."""
    candidates: dict[int, list[_Candidate]] = {version.version_id: [] for version in series.versions}
    #: Per version the in-scope releases that do not fit: the episodes each matched and its reasons.
    rejected: dict[int, list[tuple[frozenset[int], list[str]]]] = {
        version.version_id: [] for version in series.versions
    }
    orders = {
        version.version_id: engine.order_of(version.rules, qualities.WEIGHTS)
        for version in series.versions
        if version.rules is not None
    }
    belonging: list[dict[str, Any]] = []
    others: list[dict[str, Any]] = []
    order = 0
    answer: list[tuple[IndexerInfo, str, indexers.Release, releases.ParsedRelease, bool]] = []
    for state in states:
        for key, release in distinct(state.releases).items():
            parsed = releases.parse_series(
                release.title,
                series.original_language,
                state.info.multi_languages,
                titles=series.known_titles,
                anime=series.series_type == "anime",
            )
            answer.append((state.info, key, release, parsed, belongs(series, release, parsed)))
    # How the release groups of this answer count, read from all its releases of the series before any is matched.
    counting = release_match.group_counting(series.numbering, [item[3].series for item in answer if item[4]])
    for info, key, release, parsed, fits_series in answer:
        order += 1
        age = _age_hours(release, moment)
        entry: dict[str, Any] = {
            "release_key": key,
            "title": release.title,
            "indexer_id": info.indexer_id,
            "indexer": info.name,
            "protocol": info.protocol,
            "size_bytes": release.size_bytes,
            "age_hours": round(age, 1) if age is not None else None,
            "seeders": release.seeders,
            "peers": release.peers,
            "grabs": release.grabs,
            "flags": indexers.flag_names(release.indexer_flags),
            "belongs": fits_series,
            "not_this_series": None if fits_series else {"parsed_title": parsed.series.series_title},
            "parsed": parsed.as_dict(),
            "match": None,
            "in_scope": False,
            "versions": [],
        }
        if not fits_series:
            others.append(entry)
            continue
        found = release_match.match(series.numbering, parsed.series, counting=counting)
        in_scope = bool(set(found.episode_ids) & series.episode_ids)
        entry["in_scope"] = in_scope
        entry["match"] = {
            "via": found.via,
            "ambiguous": found.ambiguous,
            "other": found.other,
            "missing": list(found.missing),
            "notes": list(found.notes),
            "episodes": _codes(series, list(found.episode_ids)),
        }
        belonging.append(entry)
        too_few = ranking.seeders_rejection(info.protocol, release.seeders, info.minimum_seeders)
        too_old = ranking.retention_rejection(info.protocol, age, info.retention_days)
        # A name counted through (anime) has its numbers in ``absolute``.
        episodes = parsed.series.episodes or parsed.series.absolute or ()
        for version in series.versions:
            if version.rules is None:
                entry["versions"].append({"version_id": version.version_id, "result": None, "rank": None})
                continue
            context = _context(series, version, found, parsed, release, info)
            result = releases.evaluate_series(version.rules, release.title, context, parsed)
            switched_off = delay_rules.protocol_rejection(version.delay, info.protocol)
            for refused in (too_few, too_old, switched_off):
                if refused is not None:
                    result["rejections"].append(dict(refused))
                    result["accepted"] = False
                    result["would_take"] = False
            quality_rank = engine.rank_of(orders[version.version_id], parsed.quality.name)
            placed: dict[str, Any] = {
                "version_id": version.version_id,
                "result": result,
                "rank": None,
                # For the delay rule: a release of the best allowed quality need not wait for a better one.
                "highest_quality": 0 <= orders[version.version_id].highest_allowed <= quality_rank,
            }
            entry["versions"].append(placed)
            if not result["accepted"]:
                if in_scope:
                    codes = [item["code"] for item in result["rejections"]]
                    rejected[version.version_id].append((frozenset(found.episode_ids), codes))
                continue
            candidates[version.version_id].append(
                _Candidate(
                    key=key,
                    order=order,
                    protocol=info.protocol,
                    priority=info.priority,
                    seeders=release.seeders,
                    peers=release.peers,
                    age_hours=age,
                    size_bytes=release.size_bytes,
                    quality_rank=quality_rank,
                    score=int(result["score"]),
                    pack=parsed.series.is_pack,
                    episode_count=len(episodes),
                    first_episode=episodes[0] if episodes else 0,
                    placed=placed,
                    in_scope=in_scope,
                    blocked=_blocked(series, info.protocol, release.title, release.info_hash),
                    preferred_bytes=releases.preferred_series_bytes(version.rules, parsed, context),
                    protocol_step=delay_rules.protocol_step(version.delay, info.protocol),
                )
            )

    decisions = [
        _decide(series, version, candidates[version.version_id], rejected[version.version_id])
        for version in series.versions
    ]
    return decisions, belonging + others


def _wanted(series: SeriesSearch, by_code: dict[str, int], candidate: _Candidate) -> list[int]:
    """The episodes of the search a release would fill or replace."""
    return [
        by_code[row["code"]]
        for row in candidate.placed["result"]["episodes"]
        if row["state"] in ("fills", "replaces") and by_code.get(row["code"]) in series.episode_ids
    ]


def _release_type_formats(rules: dict[str, Any]) -> frozenset[str]:
    """The formats that score a release by its type, TRaSH's "Season Pack": what a pack gets for being one."""
    names: set[str] = set()
    for item in rules.get("formats") or []:
        specifications = item.get("specifications") if isinstance(item, dict) else None
        if any(
            isinstance(spec, dict) and spec.get("implementation") == "ReleaseTypeSpecification"
            for spec in specifications or []
        ):
            names.add(str(item.get("name")))
    return frozenset(names)


def _plain_score(candidate: _Candidate, by_type: frozenset[str]) -> int:
    matched = candidate.placed["result"].get("matched") or []
    bonus = sum(int(entry.get("score") or 0) for entry in matched if entry.get("name") in by_type)
    return candidate.score - bonus


def _smaller_instead(
    pack: _Candidate,
    size: int,
    new: list[int],
    later: list[tuple[_Candidate, list[int]]],
    by_type: frozenset[str],
) -> bool:
    """Whether a pack gives way to smaller releases (the owner's answer of 17.09.2026): it brings fewer than half of its
    episodes, and releases ranked after it that are no pack, of the same quality or better and with the same score or
    more once the pack's bonus for being one is left out, bring every one of them. Otherwise the pack stays."""
    if len(new) * 2 >= size:
        return False
    bar = _plain_score(pack, by_type)
    remaining = set(new)
    for other, wanted in later:
        if other.pack or other.quality_rank < pack.quality_rank or _plain_score(other, by_type) < bar:
            continue
        remaining -= set(wanted)
        if not remaining:
            return True
    return False


def _decide(
    series: SeriesSearch,
    version: VersionState,
    found: list[_Candidate],
    rejected: list[tuple[frozenset[int], list[str]]],
) -> dict[str, Any]:
    ranked = sorted(found, key=_sort_key)
    for place, candidate in enumerate(ranked, start=1):
        candidate.placed["rank"] = place
    decision: dict[str, Any] = {
        "version_id": version.version_id,
        "label": version.label,
        "has_profile": version.has_profile,
        "fed_by": version.fed_by,
        "would_take": None,
        "takes": [],
        "not_found": [],
        "no_fit": [],
        "keeps_current": False,
        "nothing_fits": [],
        "packs_left_out": [],
    }
    if version.rules is None:
        return decision
    # An episode a running download holds is on its way: the set takes what else is needed.
    covered: set[int] = set(version.held)
    by_code = {episode.code: episode.id for episode in series.numbering.episodes.values()}
    usable = [
        (candidate, _wanted(series, by_code, candidate))
        for candidate in ranked
        if candidate.in_scope and not candidate.blocked
    ]
    by_type = _release_type_formats(version.rules)
    for position, (candidate, wanted) in enumerate(usable):
        rows = candidate.placed["result"]["episodes"]
        new = [episode_id for episode_id in wanted if episode_id not in covered]
        if not new:
            continue
        if candidate.pack and _smaller_instead(candidate, len(rows), new, usable[position + 1 :], by_type):
            decision["packs_left_out"].append({"release_key": candidate.key, "brings": len(new), "episodes": len(rows)})
            continue
        states = {by_code[row["code"]]: row["state"] for row in rows if row["code"] in by_code}
        decision["takes"].append(
            {
                "release_key": candidate.key,
                "fills": _codes(series, [item for item in new if states.get(item) == "fills"]),
                "replaces": _codes(series, [item for item in new if states.get(item) == "replaces"]),
                "covered_elsewhere": _codes(series, [item for item in wanted if item in covered]),
            }
        )
        covered.update(new)
    missing = [
        episode_id
        for episode_id in series.episode_ids
        if version.watched.get(episode_id)
        and episode_id not in version.files
        and episode_id not in covered
        and watching.aired(series.numbering.episodes[episode_id].air_date, series.today)
    ]
    # An episode some release names, though none of them fits or may be taken, is not "not found" (the run from zero,
    # 17.09.2026): it has no fitting release, and the reasons are those releases' reasons.
    unfit: set[int] = set()
    reasons: list[list[str]] = []
    for episode_ids, codes in rejected:
        if episode_ids & set(missing):
            unfit.update(episode_ids & set(missing))
            reasons.append(codes)
    for candidate in ranked:
        if not candidate.in_scope or not candidate.blocked:
            continue
        blocked = set(_wanted(series, by_code, candidate)) & set(missing)
        if blocked:
            unfit.update(blocked)
            reasons.append(["blocklisted"])
    decision["not_found"] = _codes(series, [episode_id for episode_id in missing if episode_id not in unfit])
    decision["no_fit"] = _codes(series, [episode_id for episode_id in missing if episode_id in unfit])
    has_files = any(episode_id in version.files for episode_id in series.episode_ids)
    decision["keeps_current"] = not decision["takes"] and has_files
    if not ranked or not any(candidate.in_scope for candidate in ranked):
        reasons = [codes for _episode_ids, codes in rejected]
    counts: dict[str, int] = defaultdict(int)
    for codes in reasons:
        for code in set(codes):
            counts[code] += 1
    decision["nothing_fits"] = [
        {"code": code, "count": count} for code, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    return decision
