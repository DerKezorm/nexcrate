"""Asking the indexers for one title: in parallel across indexers, one request after the other within one.

* The pacing of ``services/indexers.py`` holds: one request per indexer every 2 seconds. A pause after a request
  limit, stored or new, makes the indexer fail with ``indexer_limit_reached``; during a pause no request goes out.
* A failure stays with its indexer, with the codes of step 2a; the other indexers go on.
* The whole search has ``SEARCH_TIMEOUT_SECONDS``. The clock of ``services/indexers.py`` is looked at before every
  request, and a hard stop cancels whatever still waits for an answer when the time is over. Such indexers are
  ``timeout`` with the code ``indexer_timeout`` and keep what they found so far.
* Nothing here touches the database: ``jobs`` hands in the indexers and writes back caps, pauses and codes.
* Log lines: counts per query and indexer. Release titles only in the trace mode. Never the key: the client adds it
  to the query and ``http_log`` masks it.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .. import indexers, logs
from . import album as album_search
from . import plan
from . import series as series_search
from .model import IndexerInfo, TitleInfo

logger = logging.getLogger("nexcrate.search")

SEARCH_TIMEOUT_SECONDS = 180.0
#: The code of an indexer that ran out of the search's time.
TIMEOUT_CODE = "indexer_timeout"
#: The code of an indexer whose run broke on something unexpected; the log has the details.
BROKEN_CODE = "internal_error"
_NOT_FETCHED: Any = ...


class _OutOfTime(Exception):
    """The search's time is over before the next request."""


@dataclass
class QueryState:
    #: id or title.
    kind: str
    text: str
    releases: int = 0


@dataclass
class IndexerState:
    info: IndexerInfo
    #: waiting, searching, done, failed, timeout, or skipped (a series search at an indexer without series categories).
    state: str = "waiting"
    error_code: str | None = None
    queries: list[QueryState] = field(default_factory=list)
    #: The distinct releases by their key, in the order found.
    releases: dict[str, indexers.Release] = field(default_factory=dict)
    took_ms: int = 0
    #: Caps fetched again during the search, or ``...`` when they were not.
    fresh_caps: Any = _NOT_FETCHED
    #: Set when a request limit pauses the indexer.
    paused_until: datetime | None = None
    #: Requests sent to the indexer, caps included: the budget of step 3c charges them.
    requests: int = 0
    #: The latest ``newznab:apilimits`` an answer carried.
    limits: indexers.ApiLimits | None = None

    @property
    def caps_fetched(self) -> bool:
        return self.fresh_caps is not _NOT_FETCHED


def _in_time(deadline: float) -> None:
    if indexers.clock() >= deadline:
        raise _OutOfTime


async def _run_query(
    client: indexers.IndexerClient,
    state: IndexerState,
    query: plan.Query,
    limit: int,
    deadline: float,
    lock: threading.Lock,
    title_id: int,
    categories: tuple[int, ...] | None = None,
) -> int:
    """Every page of one query. Returns how many releases the query gave, before merging."""
    entry = QueryState(kind=query.kind, text=query.text)
    with lock:
        state.queries.append(entry)
    offset = count = pages = 0
    while True:
        _in_time(deadline)
        with lock:
            state.requests += 1
        asked = state.info.categories if categories is None else categories
        feed = await client.feed(plan.page_params(query, asked, offset, limit))
        pages += 1
        with lock:
            for release in feed.releases:
                state.releases.setdefault(plan.release_key(state.info.indexer_id, release), release)
            count += len(feed.releases)
            entry.releases = count
            if feed.limits is not None:
                state.limits = feed.limits
        if logs.current_mode() == "trace":
            for release in feed.releases:
                logger.debug("Indexer %d offers %s", state.info.indexer_id, release.title)
        if feed.items < limit or count >= plan.MAX_RELEASES_PER_QUERY or pages >= plan.MAX_PAGES:
            break
        offset += limit
    logger.info(
        "Search of title %d: indexer %d, %s query, %d releases on %d pages",
        title_id,
        state.info.indexer_id,
        query.kind,
        count,
        pages,
    )
    return count


async def _run_series(
    client: indexers.IndexerClient,
    state: IndexerState,
    caps: Any,
    series: series_search.SeriesSearch,
    deadline: float,
    lock: threading.Lock,
    skip_titles: bool = False,
) -> None:
    """Per target: by number, else or when that found nothing by title; a special also by its name.

    ``skip_titles``: an automatic search of a series whose search by number found it at this indexer lately asks no
    title queries where a search by number is possible (decision 11).
    """
    limit = plan.page_size(caps)
    categories = series_search.categories_for(series, state.info)
    # B3: an anime series is asked in the standard form only where the indexer says so; by its number counted
    # through it is asked always. For every other series the standard form is the only one there is.
    standard = series.series_type != "anime" or state.info.anime_standard_format_search
    for target in series.targets:
        if standard:
            by_number = series_search.id_query(caps, series, target)
            found = 0
            if by_number is not None:
                found = await _run_query(client, state, by_number, limit, deadline, lock, series.title_id, categories)
            if by_number is None or (found == 0 and not skip_titles):
                for query in series_search.title_queries(caps, series, target):
                    await _run_query(client, state, query, limit, deadline, lock, series.title_id, categories)
            by_name = series_search.name_query(caps, series, target)
            if by_name is not None:
                await _run_query(client, state, by_name, limit, deadline, lock, series.title_id, categories)
        # Anime: always by the number counted through as well, as groups name either (A3).
        for query in series_search.anime_queries(caps, series, target):
            await _run_query(client, state, query, limit, deadline, lock, series.title_id, categories)


async def _run_album(
    client: indexers.IndexerClient,
    state: IndexerState,
    caps: Any,
    album: album_search.AlbumSearch,
    deadline: float,
    lock: threading.Lock,
) -> None:
    """``t=music`` where the caps offer it; free text when that was not possible or found no release of the album.
    At most ``album_search.MAX_QUERIES`` in all (decisions 5 to 9)."""
    limit = plan.page_size(caps)
    categories = album_search.music_categories(state.info)
    by_music = None if album.aliases else album_search.music_query(caps, album)
    room = album_search.MAX_QUERIES
    if by_music is not None:
        await _run_query(client, state, by_music, limit, deadline, lock, album.title_id, categories)
        room -= 1
        with lock:
            found = list(state.releases.values())
        if album_search.fits_any(album, found):
            return
    for query in album_search.text_queries(caps, album, room):
        await _run_query(client, state, query, limit, deadline, lock, album.title_id, categories)


async def run_indexer(
    state: IndexerState,
    title: TitleInfo | series_search.SeriesSearch | album_search.AlbumSearch,
    deadline: float,
    lock: threading.Lock,
    skip_titles: bool = False,
) -> None:
    info = state.info
    started = time.perf_counter()
    series = title if isinstance(title, series_search.SeriesSearch) else None
    album = title if isinstance(title, album_search.AlbumSearch) else None
    with lock:
        state.state = "searching"
    try:
        if info.error_code is not None:
            with lock:
                state.state, state.error_code = "failed", info.error_code
            return
        if series is not None and not series_search.categories_for(series, info):
            with lock:
                state.state, state.error_code = "skipped", series_search.SKIPPED_CODE
            return
        if album is not None and not album_search.music_categories(info):
            with lock:
                state.state, state.error_code = "skipped", album_search.SKIPPED_CODE
            return
        target = indexers.Target(url=info.url, kind=info.kind, api_key=info.api_key, paused_until=info.paused_until)
        async with indexers.IndexerClient(target) as client:
            caps = info.caps
            if (
                info.caps_stale
                or (series is not None and not indexers.caps_know_series(caps))
                or (album is not None and not indexers.caps_know_music(caps))
            ):
                _in_time(deadline)
                with lock:
                    state.requests += 1
                caps = await client.caps()
                with lock:
                    state.fresh_caps = caps
            if series is not None:
                await _run_series(client, state, caps, series, deadline, lock, skip_titles)
            elif album is not None:
                await _run_album(client, state, caps, album, deadline, lock)
            else:
                assert isinstance(title, TitleInfo)
                limit = plan.page_size(caps)
                by_number = plan.id_query(caps, title)
                found = 0
                if by_number is not None:
                    found = await _run_query(client, state, by_number, limit, deadline, lock, title.title_id)
                if by_number is None or found == 0:
                    for query in plan.title_queries(caps, title, info.remove_year):
                        await _run_query(client, state, query, limit, deadline, lock, title.title_id)
        with lock:
            state.state = "done"
    except indexers.IndexerError as exc:
        with lock:
            state.state, state.error_code, state.paused_until = "failed", exc.code, exc.paused_until
        logger.info("Search of title %d: indexer %d failed with %s", title.title_id, info.indexer_id, exc.code)
    except _OutOfTime:
        with lock:
            state.state, state.error_code = "timeout", TIMEOUT_CODE
    except asyncio.CancelledError:
        with lock:
            state.state, state.error_code = "timeout", TIMEOUT_CODE
        raise
    finally:
        with lock:
            state.took_ms = int((time.perf_counter() - started) * 1000)


async def run_indexers(
    states: list[IndexerState],
    title: TitleInfo | series_search.SeriesSearch | album_search.AlbumSearch,
    lock: threading.Lock,
    skip_titles: frozenset[int] = frozenset(),
) -> None:
    """Every indexer at once, within ``SEARCH_TIMEOUT_SECONDS``; a series search within its own time (S3). The indexers
    in ``skip_titles`` ask a series by number only, where they can (S5, decision 11)."""
    if not states:
        return
    timed = isinstance(title, series_search.SeriesSearch | album_search.AlbumSearch)
    limit = title.search_seconds if timed else SEARCH_TIMEOUT_SECONDS
    deadline = indexers.clock() + limit
    tasks = [
        asyncio.ensure_future(run_indexer(state, title, deadline, lock, state.info.indexer_id in skip_titles))
        for state in states
    ]
    _done, pending = await asyncio.wait(tasks, timeout=limit)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    for task, state in zip(tasks, states, strict=True):
        if task.cancelled() or task.exception() is None:
            continue
        logger.error(
            "Search of title %d: indexer %d broke off",
            title.title_id,
            state.info.indexer_id,
            exc_info=task.exception(),
        )
        with lock:
            state.state, state.error_code = "failed", BROKEN_CODE
    with lock:
        for state in states:
            if state.state in ("waiting", "searching"):
                state.state, state.error_code = "timeout", TIMEOUT_CODE
        late = sum(state.state == "timeout" for state in states)
    if late:
        logger.warning("Search of title %d: %d indexers ran out of the %d seconds", title.title_id, late, int(limit))
