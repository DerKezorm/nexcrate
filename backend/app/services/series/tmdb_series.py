"""TMDB for series (S1.2): the search, the details with every season, ``/find``.

What TMDB says (read 15.09.2026, the facts of the plan):

* ``append_to_response`` takes 20 items at most. The details come with four extras; the seasons follow in calls of up to
  20 (``season/1,season/2,...``), each appended season under the key ``season/<n>``.
* Alternative titles of a series come as ``results``, those of a movie as ``titles``.
* No language fallback: a missing text is empty. Every season is asked in ``en-US`` too, in the same fetch: empty
  names and overviews are filled from it, and each episode keeps its English name (``name_en``). Matching checks
  Sonarr's titles against it ("Befund: falsche Brücken").
* Keywords of a series come as ``results``, those of a movie as ``keywords``. They only serve to recognise anime
  (A1) and are not stored.
* ``air_date`` is ``YYYY-MM-DD``. An episode's external numbers need one call per episode and are not fetched.
* Season answers carry the crew and guest stars of every episode. ⚠️ They are trimmed to the fields nexcrate keeps
  before they go into the cache: a daily show would otherwise store megabytes per call.

Log lines carry ids and counts, never titles.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

from ..releases.languages import normalize_iso
from ..schreibweisen import nfc
from ..tmdb import (
    DETAIL_TTL,
    FALLBACK_LOCALE,
    GENRE_TTL,
    SEARCH_TTL,
    SearchPage,
    SearchResult,
    _cached,
    _dict,
    _get,
    _int,
    _list,
    _text,
    cache_get,
    cache_key,
    cache_put,
    poster_file_of,
    year_of,
)
from . import anime

logger = logging.getLogger("nexcrate.tmdb")

APPEND_LIMIT = 20
EXTRAS = ("external_ids", "alternative_titles", "translations", "episode_groups", "keywords")
#: TMDB's types of a series whose releases are named by date (decision 20).
DAILY_TYPES = frozenset({"News", "Talk Show"})
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_IMDB = re.compile(r"^tt\d{7,10}$")
#: The fields of an episode nexcrate keeps from a season answer.
_EPISODE_FIELDS = ("id", "name", "overview", "air_date", "episode_number", "season_number", "runtime", "episode_type")
_SEASON_FIELDS = ("id", "name", "overview", "air_date", "season_number")


def date_of(value: Any) -> str | None:
    return value if isinstance(value, str) and _DATE.match(value) else None


@dataclass(frozen=True)
class EpisodeData:
    tmdb_id: int
    season_number: int
    episode_number: int
    name: str
    overview: str | None
    air_date: str | None
    runtime: int | None
    episode_type: str | None
    #: TMDB's English name, for every episode (specials are searched by it, S3 decision 21; matching checks Sonarr's
    #: titles against it, plan S6). None when the season was not asked in English.
    name_en: str | None = None


@dataclass(frozen=True)
class SeasonData:
    tmdb_id: int
    number: int
    name: str
    overview: str | None
    air_date: str | None
    episode_count: int
    #: False when TMDB sent no details for the season: its stored episodes then stay as they are.
    episodes_known: bool
    episodes: tuple[EpisodeData, ...] = ()


@dataclass(frozen=True)
class SeriesData:
    tmdb_id: int
    tvdb_id: int | None
    imdb_id: str | None
    title: str
    original_title: str | None
    year: int | None
    runtime: int | None
    genres: list[str]
    overview: str | None
    poster_file: str | None
    #: Alternative and translated names, for the search keys.
    other_titles: list[str]
    original_language: str | None
    status: str | None
    tmdb_type: str | None
    networks: list[str]
    first_air_date: str | None
    last_air_date: str | None
    next_air_date: str | None
    episode_groups: list[dict[str, Any]]
    seasons: list[SeasonData]
    #: TMDB's English name of the series, for queries (S3, decision 14); None when TMDB has none.
    title_en: str | None = None
    #: TMDB's keywords and genre ids say anime (A1).
    looks_anime: bool = False

    @property
    def proposed_type(self) -> str:
        return anime.proposed(self.looks_anime, "daily" if self.tmdb_type in DAILY_TYPES else "standard")

    def episode_total(self) -> int:
        return sum(len(season.episodes) for season in self.seasons)


def chunks(numbers: list[int], size: int = APPEND_LIMIT) -> list[list[int]]:
    return [numbers[index : index + size] for index in range(0, len(numbers), size)]


def _trim_seasons(data: Any, numbers: list[int]) -> dict[str, Any]:
    """Only the appended seasons, and of those only the fields kept."""
    trimmed: dict[str, Any] = {}
    source = _dict(data)
    for number in numbers:
        season = source.get(f"season/{number}")
        if not isinstance(season, dict):
            continue
        kept = {name: season[name] for name in _SEASON_FIELDS if name in season}
        kept["episodes"] = [
            {name: episode[name] for name in _EPISODE_FIELDS if name in episode}
            for episode in map(_dict, _list(season.get("episodes")))
        ]
        trimmed[f"season/{number}"] = kept
    return trimmed


async def _seasons(token: str, tmdb_id: int, numbers: list[int], locale: str, refresh: bool) -> dict[str, Any]:
    """One call for up to 20 seasons, trimmed before it is cached."""
    append = ",".join(f"season/{number}" for number in numbers)
    key = cache_key("tv_seasons", tmdb_id, locale, append)
    if not refresh:
        hit = await asyncio.to_thread(cache_get, key)
        if hit is not None:
            return _dict(hit)
    data = await _get(token, f"/tv/{tmdb_id}", {"language": locale, "append_to_response": append}, title=True)
    trimmed = _trim_seasons(data, numbers)
    await asyncio.to_thread(cache_put, key, trimmed, DETAIL_TTL)
    return trimmed


async def genre_names(token: str) -> dict[int, str]:
    """TMDB's series genres by id, in English, cached 30 days."""
    data = _dict(
        await _cached(
            token, cache_key("genres_tv", FALLBACK_LOCALE), GENRE_TTL, "/genre/tv/list", {"language": FALLBACK_LOCALE}
        )
    )
    names: dict[int, str] = {}
    for genre in map(_dict, _list(data.get("genres"))):
        genre_id, name = _int(genre.get("id")), _text(genre.get("name"))
        if genre_id is not None and name:
            names[genre_id] = name
    return names


async def search_series(token: str, query: str, *, year: int | None, page: int, locale: str) -> SearchPage:
    text = nfc(query).strip()
    params: dict[str, Any] = {"query": text, "include_adult": "false", "language": locale, "page": page}
    if year:
        params["first_air_date_year"] = year
    data = _dict(
        await _cached(token, cache_key("tv_search", locale, year or "", page, text), SEARCH_TTL, "/search/tv", params)
    )
    items = [_dict(item) for item in _list(data.get("results"))]
    english: dict[int, dict[str, Any]] = {}
    incomplete = any(not _text(item.get("name")) or not _text(item.get("overview")) for item in items)
    if locale != FALLBACK_LOCALE and incomplete:
        fallback = _dict(
            await _cached(
                token,
                cache_key("tv_search", FALLBACK_LOCALE, year or "", page, text),
                SEARCH_TTL,
                "/search/tv",
                {**params, "language": FALLBACK_LOCALE},
            )
        )
        for item in map(_dict, _list(fallback.get("results"))):
            tmdb_id = _int(item.get("id"))
            if tmdb_id is not None:
                english[tmdb_id] = item
    results: list[SearchResult] = []
    for item in items:
        tmdb_id = _int(item.get("id"))
        if tmdb_id is None or tmdb_id <= 0:
            continue
        other = english.get(tmdb_id, {})
        original = _text(item.get("original_name")) or None
        results.append(
            SearchResult(
                tmdb_id=tmdb_id,
                title=_text(item.get("name")) or _text(other.get("name")) or original or f"TMDB {tmdb_id}",
                original_title=original,
                year=year_of(item.get("first_air_date")),
                overview=_text(item.get("overview")) or _text(other.get("overview")) or None,
                poster_file=poster_file_of(item.get("poster_path")),
            )
        )
    return SearchPage(
        page=_int(data.get("page")) or page,
        total_pages=max(0, _int(data.get("total_pages")) or 0),
        total=max(0, _int(data.get("total_results")) or 0),
        results=results,
    )


async def find_series(token: str, *, tvdb_id: int | None = None, imdb_id: str | None = None, locale: str) -> int | None:
    """The TMDB number of a series by its TVDB number, else by its IMDb number; None when TMDB knows neither."""
    lookups: list[tuple[str, str]] = []
    if tvdb_id is not None and tvdb_id > 0:
        lookups.append(("tvdb_id", str(tvdb_id)))
    imdb = (imdb_id or "").strip().lower()
    if _IMDB.match(imdb):
        lookups.append(("imdb_id", imdb))
    for source, value in lookups:
        data = _dict(
            await _cached(
                token,
                cache_key("tv_find", source, value),
                DETAIL_TTL,
                f"/find/{value}",
                {"external_source": source, "language": locale},
            )
        )
        for item in map(_dict, _list(data.get("tv_results"))):
            found = _int(item.get("id"))
            if found is not None and found > 0:
                return found
    return None


def _episode(
    value: dict[str, Any], english: dict[int, dict[str, Any]], *, english_complete: bool = False
) -> EpisodeData | None:
    tmdb_id = _int(value.get("id"))
    season_number = _int(value.get("season_number"))
    episode_number = _int(value.get("episode_number"))
    if tmdb_id is None or tmdb_id <= 0 or season_number is None or season_number < 0 or episode_number is None:
        return None
    other = english.get(tmdb_id, {})
    runtime = _int(value.get("runtime"))
    kind = _text(value.get("episode_type"))
    return EpisodeData(
        tmdb_id=tmdb_id,
        season_number=season_number,
        episode_number=episode_number,
        name=(_text(value.get("name")) or _text(other.get("name")))[:1024],
        overview=_text(value.get("overview")) or _text(other.get("overview")) or None,
        air_date=date_of(value.get("air_date")),
        runtime=runtime if runtime is not None and runtime > 0 else None,
        episode_type=kind[:16] or None,
        name_en=(_text(other.get("name"))[:1024] or None) if english_complete else None,
    )


def _season(
    listed: dict[str, Any],
    detail: dict[str, Any] | None,
    english: dict[str, Any] | None,
    *,
    english_complete: bool = False,
) -> SeasonData | None:
    tmdb_id = _int(listed.get("id"))
    number = _int(listed.get("season_number"))
    if tmdb_id is None or tmdb_id <= 0 or number is None or number < 0:
        return None
    by_id: dict[int, dict[str, Any]] = {}
    for item in map(_dict, _list(_dict(english).get("episodes"))):
        item_id = _int(item.get("id"))
        if item_id is not None:
            by_id[item_id] = item
    episodes: list[EpisodeData] = []
    seen: set[int] = set()
    if detail is not None:
        for item in map(_dict, _list(detail.get("episodes"))):
            episode = _episode(item, by_id, english_complete=english_complete)
            if episode is not None and episode.tmdb_id not in seen:
                seen.add(episode.tmdb_id)
                episodes.append(episode)
    count = _int(listed.get("episode_count"))
    name = _text(listed.get("name")) or _text(_dict(detail).get("name")) or _text(_dict(english).get("name"))
    return SeasonData(
        tmdb_id=tmdb_id,
        number=number,
        name=name[:1024],
        overview=_text(listed.get("overview")) or _text(_dict(english).get("overview")) or None,
        air_date=date_of(listed.get("air_date")) or date_of(_dict(detail).get("air_date")),
        episode_count=count if count is not None and count >= 0 else len(episodes),
        episodes_known=detail is not None,
        episodes=tuple(episodes),
    )


async def fetch_episode_group(token: str, group_id: str, *, refresh: bool = False) -> dict[str, Any]:
    """The contents of a TMDB episode group: ``groups`` with ``order``, ``name`` and ``episodes`` (id and order).
    Raises ``TmdbError``. Only the fields the numbering reads are kept (S3, decision 11)."""
    data = _dict(
        await _cached(
            token,
            cache_key("tv_episode_group", group_id),
            DETAIL_TTL,
            f"/tv/episode_group/{group_id}",
            {},
            refresh=refresh,
        )
    )
    parts = []
    for part in map(_dict, _list(data.get("groups"))):
        episodes = [
            {"id": _int(item.get("id")), "order": _int(item.get("order"))}
            for item in map(_dict, _list(part.get("episodes")))
            if _int(item.get("id")) is not None
        ]
        parts.append({"name": _text(part.get("name"))[:200], "order": _int(part.get("order")), "episodes": episodes})
    return {"id": group_id, "groups": parts}


async def proposed_type_of(token: str, tmdb_id: int, locale: str) -> str:
    """The type TMDB suggests for a series, from its details alone (B5). Raises ``TmdbError``.

    The same request, and the same cache entry, as the details ``fetch_series`` asks for first: a run over the
    library costs one request per series, and a refresh after it none for these.
    """
    details = _dict(
        await _cached(
            token,
            cache_key("tv", tmdb_id, locale),
            DETAIL_TTL,
            f"/tv/{tmdb_id}",
            {"language": locale, "append_to_response": ",".join(EXTRAS)},
            title=True,
        )
    )
    genre_ids = [
        genre_id for genre_id in (_int(_dict(item).get("id")) for item in _list(details.get("genres"))) if genre_id
    ]
    keywords = [_text(_dict(item).get("name")) for item in _list(_dict(details.get("keywords")).get("results"))]
    looks = anime.looks_like_anime(keywords, genre_ids, normalize_iso(details.get("original_language")))
    return anime.proposed(looks, "daily" if _text(details.get("type")) in DAILY_TYPES else "standard")


async def fetch_series(token: str, tmdb_id: int, locale: str, *, refresh: bool = False) -> SeriesData:
    """A series with every season and episode, in the account language with the ``en-US`` fill. Raises ``TmdbError``."""
    details = _dict(
        await _cached(
            token,
            cache_key("tv", tmdb_id, locale),
            DETAIL_TTL,
            f"/tv/{tmdb_id}",
            {"language": locale, "append_to_response": ",".join(EXTRAS)},
            title=True,
            refresh=refresh,
        )
    )
    listed = [
        item
        for item in map(_dict, _list(details.get("seasons")))
        if (_int(item.get("season_number")) or 0) >= 0 and _int(item.get("id")) is not None
    ]
    numbers = sorted({number for number in (_int(item.get("season_number")) for item in listed) if number is not None})
    local: dict[int, dict[str, Any]] = {}
    for chunk in chunks(numbers):
        answer = await _seasons(token, tmdb_id, chunk, locale, refresh)
        for number in chunk:
            found = answer.get(f"season/{number}")
            if isinstance(found, dict):
                local[number] = found

    title, overview = _text(details.get("name")), _text(details.get("overview"))
    english_seasons: dict[int, dict[str, Any]] = {}
    if locale != FALLBACK_LOCALE:
        if not title or not overview:
            english = _dict(
                await _cached(
                    token,
                    cache_key("tv", tmdb_id, FALLBACK_LOCALE, "plain"),
                    DETAIL_TTL,
                    f"/tv/{tmdb_id}",
                    {"language": FALLBACK_LOCALE},
                    title=True,
                    refresh=refresh,
                )
            )
            title = title or _text(english.get("name"))
            overview = overview or _text(english.get("overview"))
        # Every season is asked in English: specials are searched by their English name (S3, decision 21), and
        # matching checks Sonarr's titles against the English names (plan S6, "Befund: falsche Brücken"). Up to 20
        # seasons per call, as for the account language.
        for chunk in chunks(sorted(local)):
            answer = await _seasons(token, tmdb_id, chunk, FALLBACK_LOCALE, refresh)
            for number in chunk:
                found = answer.get(f"season/{number}")
                if isinstance(found, dict):
                    english_seasons[number] = found

    seasons: list[SeasonData] = []
    for item in listed:
        number = _int(item.get("season_number"))
        english_season = english_seasons.get(number or 0) if number is not None else None
        if locale == FALLBACK_LOCALE and number is not None:
            # In English the account's own texts are the English ones.
            english_season = local.get(number)
        season = _season(
            item,
            local.get(number) if number is not None else None,
            english_season,
            # Every season asked in English keeps its English names: a placeholder shows them (decision 49).
            english_complete=english_season is not None,
        )
        if season is not None and all(existing.tmdb_id != season.tmdb_id for existing in seasons):
            seasons.append(season)

    names = await genre_names(token)
    genres: list[str] = []
    genre_ids = [
        genre_id for genre_id in (_int(_dict(item).get("id")) for item in _list(details.get("genres"))) if genre_id
    ]
    keywords = [_text(_dict(item).get("name")) for item in _list(_dict(details.get("keywords")).get("results"))]
    for genre in map(_dict, _list(details.get("genres"))):
        genre_id = _int(genre.get("id"))
        name = names.get(genre_id) if genre_id is not None else None
        if name and name not in genres:
            genres.append(name)
    others: list[str] = []
    alternatives = _list(_dict(details.get("alternative_titles")).get("results"))
    translations = _list(_dict(details.get("translations")).get("translations"))
    for text in [
        *(_text(_dict(item).get("title")) for item in alternatives),
        *(_text(_dict(_dict(item).get("data")).get("name")) for item in translations),
    ]:
        if text and text not in others:
            others.append(text)
    title_en = _english_title(translations) or (title if locale.startswith("en") else None)
    external = _dict(details.get("external_ids"))
    tvdb_id = _int(external.get("tvdb_id"))
    imdb_id = _text(external.get("imdb_id")).lower()
    runtime = next((value for value in map(_int, _list(details.get("episode_run_time"))) if value and value > 0), None)
    if runtime is None:
        last_runtime = _int(_dict(details.get("last_episode_to_air")).get("runtime"))
        runtime = last_runtime if last_runtime and last_runtime > 0 else None
    networks: list[str] = []
    for network in map(_dict, _list(details.get("networks"))):
        name = _text(network.get("name"))
        if name and name not in networks:
            networks.append(name[:200])
    groups: list[dict[str, Any]] = []
    for group in map(_dict, _list(_dict(details.get("episode_groups")).get("results"))):
        group_id = group.get("id")
        if not isinstance(group_id, str) or not group_id or len(group_id) > 64:
            continue
        groups.append(
            {
                "id": group_id,
                "name": _text(group.get("name"))[:200],
                "type": _int(group.get("type")),
                "episode_count": _int(group.get("episode_count")),
                "group_count": _int(group.get("group_count")),
            }
        )
    original = _text(details.get("original_name")) or None
    status = _text(details.get("status"))
    kind = _text(details.get("type"))
    logger.info(
        "TMDB series %d: %d seasons, %d with episodes, %d filled from en-US",
        tmdb_id,
        len(seasons),
        len(local),
        len(english_seasons),
    )
    return SeriesData(
        tmdb_id=tmdb_id,
        tvdb_id=tvdb_id if tvdb_id is not None and tvdb_id > 0 else None,
        imdb_id=imdb_id if _IMDB.match(imdb_id) else None,
        title=(title or original or f"TMDB {tmdb_id}")[:1024],
        original_title=original[:1024] if original else None,
        year=year_of(details.get("first_air_date")),
        runtime=runtime,
        genres=genres,
        overview=overview or None,
        poster_file=poster_file_of(details.get("poster_path")),
        other_titles=others,
        original_language=normalize_iso(details.get("original_language")),
        status=status[:32] or None,
        tmdb_type=kind[:32] or None,
        networks=networks,
        first_air_date=date_of(details.get("first_air_date")),
        last_air_date=date_of(details.get("last_air_date")),
        next_air_date=date_of(_dict(details.get("next_episode_to_air")).get("air_date")),
        episode_groups=groups,
        seasons=seasons,
        title_en=title_en[:1024] if title_en else None,
        looks_anime=anime.looks_like_anime(keywords, genre_ids, normalize_iso(details.get("original_language"))),
    )


def _english_title(translations: list[Any]) -> str | None:
    """The English name among TMDB's translations: US first, then GB, then any English."""
    found: dict[str, str] = {}
    for item in map(_dict, translations):
        if _text(item.get("iso_639_1")).lower() != "en":
            continue
        name = _text(_dict(item.get("data")).get("name"))
        if name:
            found.setdefault(_text(item.get("iso_3166_1")).upper(), name)
    return found.get("US") or found.get("GB") or next(iter(found.values()), None)
