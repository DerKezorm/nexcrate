"""Proposals for a folder nobody knows (L4, "Proposals").

* **Numbers in the name**, ignoring case, as Radarr reads them but also without a year: ``tmdb-<n>`` or ``tmdbid-<n>``
  inside any brackets (Plex writes ``{tmdb-<n>}``, Jellyfin ``[tmdbid-<n>]``) or bare, and an IMDb number ``tt`` with 7
  to 10 digits standing alone.
* **Title and year** through the spelling keys: the library first (titles sharing a key with the year at most one year
  away), then TMDB's search with title and year, and without the year when nothing came; results sharing a key with
  their title or original title rank first, the same year before one year off. At most 5 proposals.
* **Unambiguous** (decision 18): a number that names exactly one movie, or exactly one candidate that shares a key and
  has the same year.
* A proposal: ``tmdb_id``, ``imdb_id``, ``title``, ``year``, ``poster_file``, ``from`` (``companion``, ``name_number``,
  ``nfo_number``, ``title_year``, ``library``), ``title_id``, ``unambiguous``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ...models import AlternateTitle, Title
from .. import images, schreibweisen, tmdb
from .walk import Numbers

MAX_PROPOSALS = 5
YEAR_TOLERANCE = 1
_TMDB_MAX = 2147483647
_TMDB_IN_NAME = re.compile(r"(?<![a-z0-9])tmdb(?:id)?\s*[-=:_ ]\s*(\d{1,10})(?![0-9])", re.IGNORECASE)
_IMDB_IN_NAME = re.compile(r"(?<![a-z0-9])(tt\d{7,10})(?![0-9])", re.IGNORECASE)


def numbers_in_name(name: str) -> Numbers:
    """The TMDB and IMDb numbers a folder or file name carries."""
    found = Numbers()
    tmdb_match = _TMDB_IN_NAME.search(name)
    if tmdb_match:
        number = int(tmdb_match.group(1))
        if 1 <= number <= _TMDB_MAX:
            found.tmdb_id, found.tmdb_from = number, "name"
    imdb_match = _IMDB_IN_NAME.search(name)
    if imdb_match:
        value = imdb_match.group(1)
        found.imdb_id, found.imdb_from = "tt" + value[2:], "name"
    return found


def imdb_number(value: str | None) -> int | None:
    """An IMDb id as a number, so leading zeros do not matter."""
    if not value:
        return None
    digits = value.strip().lower().removeprefix("tt")
    return int(digits) if digits.isdigit() else None


def proposal(
    *,
    tmdb_id: int,
    imdb_id: str | None,
    title: str,
    year: int | None,
    poster_file: str | None,
    source: str,
    title_id: int | None,
    unambiguous: bool,
    poster_url: str | None = None,
) -> dict[str, Any]:
    if poster_url is None and poster_file:
        poster_url = f"/api/tmdb/poster/w185/{poster_file}"
    return {
        "tmdb_id": tmdb_id,
        "imdb_id": imdb_id,
        "title": title,
        "year": year,
        "poster_file": poster_file,
        "poster_url": poster_url,
        "from": source,
        "title_id": title_id,
        "unambiguous": unambiguous,
    }


def from_title(title: Title, source: str, *, unambiguous: bool) -> dict[str, Any]:
    return proposal(
        tmdb_id=title.tmdb_id,
        imdb_id=title.imdb_id,
        title=title.title,
        year=title.year,
        poster_file=title.tmdb_poster_path,
        source=source,
        title_id=title.id,
        unambiguous=unambiguous,
        poster_url=images.poster_url(title),
    )


# --- The library ------------------------------------------------------------------------------------------- #


@dataclass
class Library:
    """The titles of the library, read once per scan: by TMDB number, by IMDb number, and by spelling key."""

    by_tmdb: dict[int, Title]
    by_imdb: dict[int, Title]
    #: Every key of a title, its original title, its TMDB titles and its alternate titles.
    by_key: dict[str, list[Title]]

    @classmethod
    def load(cls, db: OrmSession) -> Library:
        titles = list(db.scalars(select(Title).where(Title.kind == "movie")))
        by_tmdb = {title.tmdb_id: title for title in titles}
        by_imdb: dict[int, Title] = {}
        by_key: dict[str, list[Title]] = {}
        for title in titles:
            number = imdb_number(title.imdb_id)
            if number is not None:
                by_imdb.setdefault(number, title)
            for text in (title.search_keys, title.tmdb_search_keys):
                for key in (text or "").split(schreibweisen.SEPARATOR):
                    if key:
                        holders = by_key.setdefault(key, [])
                        if title not in holders:
                            holders.append(title)
        rows = db.execute(select(AlternateTitle.title_id, AlternateTitle.search_keys)).all()
        titles_by_id = {title.id: title for title in titles}
        for title_id, text in rows:
            title = titles_by_id.get(title_id)
            if title is None:
                continue
            for key in (text or "").split(schreibweisen.SEPARATOR):
                if key:
                    holders = by_key.setdefault(key, [])
                    if title not in holders:
                        holders.append(title)
        return cls(by_tmdb=by_tmdb, by_imdb=by_imdb, by_key=by_key)

    def by_name(self, parsed_title: str | None, parsed_year: int | None) -> list[Title]:
        """Titles sharing a key with the parsed title, the year at most one off; the same year first."""
        if not parsed_title:
            return []
        found: list[Title] = []
        for key in schreibweisen.keys(parsed_title):
            for title in self.by_key.get(key, []):
                if title in found:
                    continue
                off = abs(title.year - parsed_year) if parsed_year is not None and title.year is not None else 0
                if off > YEAR_TOLERANCE:
                    continue
                found.append(title)
        found.sort(key=lambda title: (0 if parsed_year is not None and title.year == parsed_year else 1, title.id))
        return found


def library_proposals(library: Library, parsed_title: str | None, parsed_year: int | None) -> list[dict[str, Any]]:
    """Decision 18 for the library: unambiguous when exactly one title shares a key and has the same year."""
    titles = library.by_name(parsed_title, parsed_year)
    same_year = [title for title in titles if parsed_year is not None and title.year == parsed_year]
    return [
        from_title(title, "library", unambiguous=len(same_year) == 1 and title is same_year[0])
        for title in titles[:MAX_PROPOSALS]
    ]


def number_proposals(library: Library, numbers: Numbers) -> tuple[list[dict[str, Any]], Numbers]:
    """Proposals from numbers the library knows; the numbers it does not know stay for TMDB."""
    found: list[dict[str, Any]] = []
    pending = Numbers()
    if numbers.tmdb_id is not None:
        title = library.by_tmdb.get(numbers.tmdb_id)
        if title is not None:
            found.append(from_title(title, f"{numbers.tmdb_from}_number", unambiguous=True))
        else:
            pending.tmdb_id, pending.tmdb_from = numbers.tmdb_id, numbers.tmdb_from
    if numbers.imdb_id is not None:
        title = library.by_imdb.get(imdb_number(numbers.imdb_id) or -1)
        if title is not None:
            if not any(item["tmdb_id"] == title.tmdb_id for item in found):
                found.append(from_title(title, f"{numbers.imdb_from}_number", unambiguous=True))
        else:
            pending.imdb_id, pending.imdb_from = numbers.imdb_id, numbers.imdb_from
    return found, pending


# --- TMDB (the phase after the scan) ------------------------------------------------------------------------ #


def _shares_key(parsed_title: str | None, *names: str | None) -> bool:
    if not parsed_title:
        return False
    wanted = set(schreibweisen.keys(parsed_title))
    return any(wanted & set(schreibweisen.keys(name)) for name in names if name)


async def tmdb_number_proposals(token: str, locale: str, numbers: Numbers, library: Library) -> list[dict[str, Any]]:
    """The proposals of the numbers TMDB has to answer for. Raises ``tmdb.TmdbError`` for anything but a 404."""
    found: list[dict[str, Any]] = []
    if numbers.tmdb_id is not None:
        try:
            data = await tmdb.fetch_movie(token, numbers.tmdb_id, locale)
        except tmdb.TmdbError as exc:
            if exc.code != "not_found":
                raise
        else:
            known = library.by_tmdb.get(data.tmdb_id)
            found.append(
                proposal(
                    tmdb_id=data.tmdb_id,
                    imdb_id=data.imdb_id,
                    title=data.title,
                    year=data.year,
                    poster_file=data.poster_file,
                    source=f"{numbers.tmdb_from}_number",
                    title_id=known.id if known is not None else None,
                    unambiguous=True,
                )
            )
    if numbers.imdb_id is not None and not found:
        result = await tmdb.find_by_imdb(token, numbers.imdb_id, locale)
        if result is not None:
            known = library.by_tmdb.get(result.tmdb_id)
            found.append(
                proposal(
                    tmdb_id=result.tmdb_id,
                    imdb_id=numbers.imdb_id,
                    title=result.title,
                    year=result.year,
                    poster_file=result.poster_file,
                    source=f"{numbers.imdb_from}_number",
                    title_id=known.id if known is not None else None,
                    unambiguous=True,
                )
            )
    return found


async def tmdb_title_proposals(
    token: str, locale: str, parsed_title: str, parsed_year: int | None, library: Library
) -> list[dict[str, Any]]:
    """TMDB's search with title and year, without the year when nothing came, ranked as the plan says."""
    page = await tmdb.search_movies(token, parsed_title, year=parsed_year, page=1, locale=locale)
    results = list(page.results)
    if not results and parsed_year is not None:
        page = await tmdb.search_movies(token, parsed_title, year=None, page=1, locale=locale)
        results = list(page.results)

    def rank(result: tmdb.SearchResult) -> tuple[int, int, int]:
        shares = 0 if _shares_key(parsed_title, result.title, result.original_title) else 1
        if parsed_year is None or result.year is None:
            year_rank = 1
        elif result.year == parsed_year:
            year_rank = 0
        elif abs(result.year - parsed_year) <= YEAR_TOLERANCE:
            year_rank = 1
        else:
            year_rank = 2
        return shares, year_rank, result.tmdb_id

    results.sort(key=rank)
    exact = [
        result
        for result in results
        if _shares_key(parsed_title, result.title, result.original_title)
        and parsed_year is not None
        and result.year == parsed_year
    ]
    found: list[dict[str, Any]] = []
    for result in results[:MAX_PROPOSALS]:
        known = library.by_tmdb.get(result.tmdb_id)
        found.append(
            proposal(
                tmdb_id=result.tmdb_id,
                imdb_id=None,
                title=result.title,
                year=result.year,
                poster_file=result.poster_file,
                source="title_year",
                title_id=known.id if known is not None else None,
                unambiguous=len(exact) == 1 and result is exact[0],
            )
        )
    return found


def best_number(proposals: list[dict[str, Any]], companion_tmdb: int | None, numbers: Numbers) -> int | None:
    """The best known TMDB number of a row: the companion's, a number found, else an unambiguous proposal's."""
    if companion_tmdb is not None:
        return companion_tmdb
    if numbers.tmdb_id is not None:
        return numbers.tmdb_id
    unambiguous = [item for item in proposals if item.get("unambiguous")]
    if len(unambiguous) == 1:
        return int(unambiguous[0]["tmdb_id"])
    return None
