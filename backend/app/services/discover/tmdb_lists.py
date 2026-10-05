"""Discover's lists of movies and series, from TMDB's ``/discover/movie`` and ``/discover/tv``.

⚠️ Movies are only what one can have already: every movie list but the classics asks for a digital or physical
release (``with_release_type=4|5``) on or before today. Without ``region`` TMDB takes such a release in any country,
with it only in that country (developer.themoviedb.org, "Region support"); a movie running in cinemas only is never
part of it, since nobody can download it yet.

Series leave out news, reality, soap and talk shows (``without_genres``): popular as they are, nobody looks for them
here. Lists sorted by rating carry a floor of votes, else a title with one vote of ten heads the list.

Pages are cached for ``PAGE_TTL``; ``today`` is a module attribute so that tests can fix the date.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from ..tmdb import (
    FALLBACK_LOCALE,
    GENRE_TTL,
    _cached,
    _dict,
    _int,
    _list,
    _text,
    cache_key,
    poster_file_of,
    year_of,
)

Kind = Literal["movie", "series"]

MOVIE_LISTS = ("fresh", "popular", "acclaimed", "classics")
SERIES_LISTS = ("new", "popular", "ended", "classics")
LISTS: dict[str, tuple[str, ...]] = {"movie": MOVIE_LISTS, "series": SERIES_LISTS}

#: How many titles a list shows.
WANTED = 20
#: How many pages of twenty a list reads at most to fill up after leaving out the library and the hidden ones.
MAX_PAGES = 5
PAGE_TTL = timedelta(hours=6)

#: Digital (4) or physical (5), TMDB's release types.
AVAILABLE = "4|5"
#: How far back "fresh" and "new" reach.
FRESH_DAYS = 120
#: How far back "acclaimed" reaches.
ACCLAIMED_DAYS = 365
#: How old a movie of "fresh" or "acclaimed" may be at most, by its first release anywhere. Without it a new disc of an
#: old movie counts as new (measured 05.10.2026 against TMDB: a movie of 2001 among the acclaimed ones of the year).
NEW_MOVIE_DAYS = 730
#: TMDB's genres News, Reality, Soap and Talk for series.
SERIES_LEFT_OUT = "10763,10764,10766,10767"
#: TMDB's series status "Ended".
STATUS_ENDED = "3"

_CODE = re.compile(r"^[A-Za-z]{2}$")


def today() -> date:
    return datetime.now(UTC).date()


@dataclass(frozen=True)
class Filters:
    region: str | None = None
    genre: int | None = None
    language: str | None = None
    country: str | None = None


@dataclass(frozen=True)
class Candidate:
    tmdb_id: int
    title: str
    original_title: str | None
    year: int | None
    overview: str | None
    poster_file: str | None
    rating: float | None
    votes: int


def clean_code(value: str | None) -> str | None:
    """A two letter code as TMDB wants it, or None. Raises ValueError for anything else."""
    text = (value or "").strip()
    if not text:
        return None
    if not _CODE.match(text):
        raise ValueError(text)
    return text


def params_for(kind: Kind, name: str, filters: Filters, day: date) -> dict[str, Any]:
    """The query of one list without language and page. Raises ValueError for a list the kind lacks."""
    if name not in LISTS[kind]:
        raise ValueError(name)
    params: dict[str, Any] = {"include_adult": "false"}
    if filters.genre is not None:
        params["with_genres"] = str(filters.genre)
    if filters.language:
        params["with_original_language"] = filters.language.lower()
    if kind == "movie":
        params["include_video"] = "false"
        if name == "classics":
            params.update({
                "primary_release_date.lte": "1995-12-31", "vote_count.gte": 1000, "sort_by": "vote_average.desc",
            })  # fmt: skip
            return params
        params.update({"with_release_type": AVAILABLE, "release_date.lte": day.isoformat()})
        if filters.region:
            params["region"] = filters.region.upper()
        if name == "fresh":
            params["release_date.gte"] = (day - timedelta(days=FRESH_DAYS)).isoformat()
            params["primary_release_date.gte"] = (day - timedelta(days=NEW_MOVIE_DAYS)).isoformat()
            params["sort_by"] = "popularity.desc"
        elif name == "popular":
            params["sort_by"] = "popularity.desc"
            params["vote_count.gte"] = 50
        else:  # acclaimed
            params["release_date.gte"] = (day - timedelta(days=ACCLAIMED_DAYS)).isoformat()
            params["primary_release_date.gte"] = (day - timedelta(days=NEW_MOVIE_DAYS)).isoformat()
            params["vote_count.gte"] = 200
            params["sort_by"] = "vote_average.desc"
        return params
    params["without_genres"] = SERIES_LEFT_OUT
    if filters.country:
        params["with_origin_country"] = filters.country.upper()
    if name == "new":
        params.update({
            "first_air_date.gte": (day - timedelta(days=FRESH_DAYS)).isoformat(),
            "first_air_date.lte": day.isoformat(), "sort_by": "popularity.desc",
        })  # fmt: skip
    elif name == "popular":
        params.update({"first_air_date.lte": day.isoformat(), "vote_count.gte": 100, "sort_by": "popularity.desc"})
    elif name == "ended":
        params.update({"with_status": STATUS_ENDED, "vote_count.gte": 300, "sort_by": "vote_average.desc"})
    else:  # classics
        params.update({"first_air_date.lte": "2009-12-31", "vote_count.gte": 300, "sort_by": "vote_average.desc"})
    return params


def _rating(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(float(value), 1) if 0 < value <= 10 else None


def _candidate(kind: Kind, item: dict[str, Any]) -> Candidate | None:
    tmdb_id = _int(item.get("id"))
    if tmdb_id is None or tmdb_id <= 0:
        return None
    series = kind == "series"
    original = _text(item.get("original_name" if series else "original_title")) or None
    title = _text(item.get("name" if series else "title")) or original or f"TMDB {tmdb_id}"
    return Candidate(
        tmdb_id=tmdb_id,
        title=title,
        original_title=original,
        year=year_of(item.get("first_air_date" if series else "release_date")),
        overview=_text(item.get("overview")) or None,
        poster_file=poster_file_of(item.get("poster_path")),
        rating=_rating(item.get("vote_average")),
        votes=max(0, _int(item.get("vote_count")) or 0),
    )


def _untranslated(kind: Kind, item: dict[str, Any], locale: str) -> bool:
    """TMDB has no text in the account language: no overview, or the original title of a title from another language
    (measured 05.10.2026: a Chinese movie came with its Chinese title under de-DE)."""
    if not _text(item.get("overview")):
        return True
    title = _text(item.get("name" if kind == "series" else "title"))
    original = _text(item.get("original_name" if kind == "series" else "original_title"))
    return title == original and _text(item.get("original_language")) != locale.split("-")[0]


def _with_english(kind: Kind, item: dict[str, Any], english: dict[str, Any]) -> dict[str, Any]:
    """The English title where the account language has only the original one, the English overview where it has none,
    like the search does."""
    merged = dict(item)
    field, original_field = ("name", "original_name") if kind == "series" else ("title", "original_title")
    if _text(item.get(field)) in ("", _text(item.get(original_field))) and _text(english.get(field)):
        merged[field] = english[field]
    if not _text(item.get("overview")) and _text(english.get("overview")):
        merged["overview"] = english["overview"]
    return merged


async def _page_data(token: str, kind: Kind, params: dict[str, Any], page: int, locale: str) -> dict[str, Any]:
    query = {**params, "language": locale, "page": page}
    path = "/discover/tv" if kind == "series" else "/discover/movie"
    key = cache_key("discover", kind, *(f"{name}={query[name]}" for name in sorted(query)))
    return _dict(await _cached(token, key, PAGE_TTL, path, query))


async def page_of(
    token: str, kind: Kind, params: dict[str, Any], page: int, locale: str
) -> tuple[list[Candidate], int]:
    """One page of a list and how many pages there are. Without a text in the account language the English one fills
    in, from a second call that is cached the same way. Raises ``tmdb.TmdbError``."""
    data = await _page_data(token, kind, params, page, locale)
    items = [_dict(item) for item in _list(data.get("results"))]
    if locale != FALLBACK_LOCALE and any(_untranslated(kind, item, locale) for item in items):
        fallback = await _page_data(token, kind, params, page, FALLBACK_LOCALE)
        english = {_int(item.get("id")): item for item in map(_dict, _list(fallback.get("results")))}
        items = [
            _with_english(kind, item, english[_int(item.get("id"))])
            if _untranslated(kind, item, locale) and _int(item.get("id")) in english
            else item
            for item in items
        ]
    found = [_candidate(kind, item) for item in items]
    return [item for item in found if item is not None], max(0, _int(data.get("total_pages")) or 0)


async def fill(
    token: str,
    kind: Kind,
    name: str,
    filters: Filters,
    locale: str,
    left_out: Callable[[list[int]], Awaitable[set[int]]],
) -> tuple[list[Candidate], bool]:
    """Up to ``WANTED`` titles of a list, without those ``left_out`` names. The flag says TMDB had no more pages."""
    params = params_for(kind, name, filters, today())
    chosen: list[Candidate] = []
    seen: set[int] = set()
    page = 1
    exhausted = False
    while len(chosen) < WANTED and page <= MAX_PAGES:
        items, total_pages = await page_of(token, kind, params, page, locale)
        fresh = [item for item in items if item.tmdb_id not in seen]
        seen.update(item.tmdb_id for item in fresh)
        skip = await left_out([item.tmdb_id for item in fresh])
        chosen.extend(item for item in fresh if item.tmdb_id not in skip)
        if page >= total_pages:
            exhausted = True
            break
        page += 1
    return chosen[:WANTED], exhausted


async def genres(token: str, kind: Kind, locale: str) -> list[tuple[int, str]]:
    """TMDB's genres of a kind in the account language, sorted by name; series without the ones left out."""
    path = "/genre/tv/list" if kind == "series" else "/genre/movie/list"
    data = _dict(
        await _cached(token, cache_key("discover_genres", kind, locale), GENRE_TTL, path, {"language": locale})
    )
    left_out = {int(code) for code in SERIES_LEFT_OUT.split(",")} if kind == "series" else set()
    found: list[tuple[int, str]] = []
    for genre in map(_dict, _list(data.get("genres"))):
        genre_id, label = _int(genre.get("id")), _text(genre.get("name"))
        if genre_id is not None and label and genre_id not in left_out:
            found.append((genre_id, label))
    return sorted(found, key=lambda entry: entry[1].casefold())
