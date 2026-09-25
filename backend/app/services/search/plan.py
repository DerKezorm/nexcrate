"""The queries one indexer is asked with, how answers are paged and how releases are merged.

As Radarr's interactive search ("Searching"), with nexcrate's spellings:

1. **By number:** when the caps offer movie search and list ``tmdbid`` or ``imdbid`` among its parameters, and the
   title has that number, one ``t=movie`` with every number the caps list. The IMDb number goes without ``tt``, with
   at least seven digits.
2. **By title,** only when step 1 was not possible or found no release, and only when the caps list ``q`` for the
   plain search: ``t=search`` with the title and, when it differs as written, the original title. Each text gets the
   year unless the indexer removes it (or the title has none) and goes out in its distinct spellings: as written,
   form (a), form (b) (``schreibweisen.query_spellings``). A query already sent to this indexer is not sent again,
   so at most three per text and six per indexer.
3. **Pages:** ``limit`` is 100, or the caps' ``limit_max`` when that is lower. A full page is followed by the next
   ``offset``; a short page, 1000 releases of one query or 30 pages end it (Radarr's limits).
4. **Merging:** one release per indexer and guid, else per indexer, title and size. The key gives neither away.

Missing or broken caps mean the defaults of ``services/indexers.py``: movie search with ``q`` and ``imdbid``, the
plain search with ``q``, 100 per page.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from .. import indexers, schreibweisen
from .model import TitleInfo

PAGE_LIMIT = 100
MAX_RELEASES_PER_QUERY = 1000
MAX_PAGES = 30
#: What the API shows as the text of the search by number.
ID_QUERY_TEXT = "ids"


@dataclass(frozen=True)
class Query:
    #: id or title.
    kind: str
    #: ``ids``, or the title query as sent.
    text: str
    #: ``t`` and the search parameters, without categories and paging.
    params: dict[str, str | int]


def caps_in_use(caps: dict[str, Any] | None) -> dict[str, Any]:
    return caps if caps else indexers.DEFAULT_CAPS


def page_size(caps: dict[str, Any] | None) -> int:
    limit_max = caps_in_use(caps).get("limit_max")
    if isinstance(limit_max, int) and not isinstance(limit_max, bool) and 0 < limit_max < PAGE_LIMIT:
        return limit_max
    return PAGE_LIMIT


def id_query(caps: dict[str, Any] | None, title: TitleInfo) -> Query | None:
    """The search by number, or None when the caps or the title do not allow one."""
    used = caps_in_use(caps)
    if not used.get("movie_search"):
        return None
    listed = set(used.get("movie_params") or [])
    params: dict[str, str | int] = {"t": "movie"}
    if "tmdbid" in listed and title.tmdb_id:
        params["tmdbid"] = title.tmdb_id
    if "imdbid" in listed and title.imdb_id:
        params["imdbid"] = f"{title.imdb_id:07d}"
    return Query("id", ID_QUERY_TEXT, params) if len(params) > 1 else None


def _texts(title: TitleInfo) -> list[str]:
    texts = [title.title]
    original = title.original_title
    if original and not schreibweisen.folds_alike(original, title.title):
        texts.append(original)
    return texts


def title_queries(caps: dict[str, Any] | None, title: TitleInfo, remove_year: bool) -> list[Query]:
    """The searches by title in order, without repeats; empty when the caps offer no plain search with ``q``."""
    if "q" not in (caps_in_use(caps).get("search_params") or []):
        return []
    year = f" {title.year}" if title.year and not remove_year else ""
    queries: list[Query] = []
    for text in _texts(title):
        for spelling in schreibweisen.query_spellings(text):
            query = f"{spelling}{year}"
            if all(existing.text != query for existing in queries):
                queries.append(Query("title", query, {"t": "search", "q": query}))
    return queries


def page_params(query: Query, categories: tuple[int, ...] | list[int], offset: int, limit: int) -> dict[str, str | int]:
    """The parameters of one page, in Radarr's order. The client adds the key."""
    params: dict[str, str | int] = {"t": query.params["t"]}
    if categories:
        params["cat"] = ",".join(str(category) for category in categories)
    params.update({"extended": 1, "offset": offset, "limit": limit})
    params.update({name: value for name, value in query.params.items() if name != "t"})
    return params


def release_key(indexer_id: int, release: indexers.Release) -> str:
    """One key per indexer and guid, else per indexer, title and size. A hash, so it gives neither away."""
    if release.guid:
        basis = f"{indexer_id}\x00guid\x00{release.guid}"
    else:
        basis = f"{indexer_id}\x00title\x00{release.title}\x00{release.size_bytes}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]
