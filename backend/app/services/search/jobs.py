"""Searches: kept in memory, one worker thread each, and the thin layer to the database.

* ``start`` claims a slot and runs the owner's search in a thread of its own with its own event loop, like the imports,
  and answers at once with the search id. The thread keeps the request id of the request that started it.
* Refused: a title with a running search (``SearchRunning`` with its id, so the page can follow it), no enabled
  indexer (``NoIndexers``), ``MAX_RUNNING`` running searches (``SearchBusy``).
* A finished search stays readable for 30 minutes; at most ``MAX_KEPT`` are kept, the oldest finished goes first.
  There is one worker process (see the Dockerfile), so memory is enough.
* **Two lanes** (step 3c): the owner's searches (``manual``) and automatic ones (``automatic``, ``start_automatic``).
  Each lane counts against caps of its own, so an automatic search never pushes the owner's results out and never
  blocks his next search. An automatic search asks only the indexers it is given, runs its ``after`` step in its thread
  once it is done (the load decision), and is dropped then. The owner who starts a search of a title whose automatic
  search runs gets that search's id, and the search stays in his lane afterwards. ``find_release`` finds a release of
  either lane the same way, so ``grab`` is one path; the release carries the search's origin for the download.
  ``keep_found`` keeps a finished automatic search made from releases RSS already fetched, without a request.
* The worker reads the title with its alternative titles, its versions with profiles and files, and the enabled
  indexers, whose keys it decrypts itself and forgets when the indexers are done. Afterwards it writes back per
  indexer what the test search of step 2a writes (the last error code, a pause after a limit, fresh caps) and, since
  step 3c, the latest ``newznab:apilimits`` and the escalation of ``automatic.budget``. An indexer that ran out of the
  search's time, or could not be asked, gets nothing written.
* An answer carries no key and no link. ⚠️ The releases in the indexer states keep their download link in memory
  (step 3), so a release can be loaded while its search is kept; ``find_release`` hands it to the loading, and nothing
  else reads it.
"""

from __future__ import annotations

import asyncio
import contextvars
import copy
import dataclasses
import logging
import secrets
import threading
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import object_session

from ... import crypto
from ...db import SessionLocal
from ...models import AlternateTitle, Indexer, Title, Version, VersionDefinition, utcnow
from .. import delay as delay_rules
from .. import indexers, judging
from .. import tags as tag_store
from ..automatic import budget
from ..downloads import retention as download_retention
from ..profiles import store as profile_store
from ..series import watching, xem
from . import album as album_search
from . import options, report, runner
from . import series as series_search
from .model import IndexerInfo, TitleInfo, VersionInfo, title_info

logger = logging.getLogger("nexcrate.search")

MANUAL = "manual"
AUTOMATIC = "automatic"
#: The owner's lane.
MAX_RUNNING = 20
MAX_KEPT = 20
#: The automatic lane: its own cap. Its searches are dropped right after their load decision.
AUTOMATIC_MAX_RUNNING = 20
KEEP_FINISHED = timedelta(minutes=30)


def now() -> datetime:
    return utcnow()


class SearchRunning(Exception):
    """A search for this title is running already."""

    def __init__(self, search_id: str) -> None:
        super().__init__(search_id)
        self.search_id = search_id


class SearchBusy(Exception):
    """Too many searches are running."""


class NoIndexers(Exception):
    """No indexer is enabled."""


@dataclass
class Search:
    search_id: str
    title_id: int
    started_at: datetime
    #: running or done.
    state: str = "running"
    finished_at: datetime | None = None
    indexers: list[runner.IndexerState] = field(default_factory=list)
    versions: list[dict[str, Any]] = field(default_factory=list)
    releases: list[dict[str, Any]] = field(default_factory=list)
    #: Guards the fields above while the worker changes them.
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    #: manual or automatic.
    lane: str = MANUAL
    #: manual, search, rss or replacement: a download loaded from this search records it.
    origin: str = "manual"
    #: The owner followed this automatic search: it stays in his lane once its load decision is done.
    adopted: bool = False
    #: The owner started this automatic search himself ("search automatically now"): it never waits out a delay,
    #: as a search by hand does not in Radarr (``UserInvokedSearch``).
    user_invoked: bool = False
    #: The indexers an automatic search asks; None for every enabled one.
    indexer_ids: frozenset[int] | None = None
    #: Requests taken from the budget per indexer before the start.
    reserved: dict[int, int] = field(default_factory=dict)
    #: Runs in the search's thread once it is done.
    after: Callable[[Search], None] | None = field(default=None, repr=False)
    #: A series search (S3): its scope as asked, then as the answer shows it; None for a movie.
    scope: dict[str, Any] | None = None
    #: The codes of what a series search asks, in order.
    targets: list[str] = field(default_factory=list)
    #: An automatic series search (S5): the scope as the scheduler or RSS made it, the episodes that get the search
    #: time, the seasons it covers, and the indexers that ask by number only.
    asked_scope: dict[str, Any] | None = None
    searched: tuple[int, ...] = ()
    covered: tuple[int, ...] = ()
    skip_titles: frozenset[int] = frozenset()
    #: An album search (M3): ``{"kind": "album", "aliases": bool}``; None for a movie or a series. Loaded from with
    #: M4 (decision 1).
    album_scope: dict[str, Any] | None = None
    #: A movie that is in no library, asked before a request (N25): its title, the versions to
    #: judge by and the indexers, made from TMDB; ``title_id`` is then below 0. Nothing loads from such a search.
    preloaded: tuple[TitleInfo, list[VersionInfo], list[IndexerInfo]] | None = field(default=None, repr=False)


_lock = threading.Lock()
_searches: dict[str, Search] = {}
_threads: list[threading.Thread] = []


# --- The registry ------------------------------------------------------------------------------ #


def _expire() -> None:
    """Drop searches finished longer ago than ``KEEP_FINISHED``. Call with ``_lock`` held."""
    moment = now()
    for search_id, search in list(_searches.items()):
        if search.finished_at is not None and moment - search.finished_at > KEEP_FINISHED:
            del _searches[search_id]


def _spawn(search: Search) -> None:
    """Keep the search and start its thread. Call with ``_lock`` held."""
    _searches[search.search_id] = search
    context = contextvars.copy_context()
    thread = threading.Thread(
        target=context.run, args=(_execute, search), name=f"search-{search.search_id}", daemon=True
    )
    _threads[:] = [existing for existing in _threads if existing.is_alive()]
    _threads.append(thread)
    try:
        thread.start()
    except BaseException:
        del _searches[search.search_id]
        raise


def _make_room(keep: Search | None = None) -> bool:
    """Drop the oldest finished searches of the owner's lane until one more fits. Call with ``_lock`` held."""
    while sum(1 for search in _searches.values() if search.lane == MANUAL and search is not keep) >= MAX_KEPT:
        finished = [
            search
            for search in _searches.values()
            if search.lane == MANUAL and search.state != "running" and search is not keep
        ]
        if not finished:
            return False
        oldest = min(finished, key=lambda search: search.finished_at or search.started_at)
        del _searches[oldest.search_id]
    return True


def start(
    title_id: int,
    *,
    has_indexers: bool,
    scope: dict[str, Any] | None = None,
    album_scope: dict[str, Any] | None = None,
) -> str:
    """Claim a slot and start the owner's search in its own thread. Raises ``SearchRunning``, ``NoIndexers``,
    ``SearchBusy``. A running automatic search of the title answers ``SearchRunning`` and is kept for the owner."""
    with _lock:
        _expire()
        running = [search for search in _searches.values() if search.state == "running"]
        for search in running:
            if search.title_id == title_id:
                if search.lane == AUTOMATIC:
                    search.adopted = True
                raise SearchRunning(search.search_id)
        if not has_indexers:
            raise NoIndexers
        if sum(1 for search in running if search.lane == MANUAL) >= MAX_RUNNING:
            raise SearchBusy
        if not _make_room():
            raise SearchBusy
        search = Search(
            search_id=secrets.token_hex(8), title_id=title_id, started_at=now(), scope=scope, album_scope=album_scope
        )
        _spawn(search)
    return search.search_id


def start_preview(preloaded: tuple[TitleInfo, list[VersionInfo], list[IndexerInfo]]) -> str:
    """A search of a movie in no library, in the owner's lane and within its limits (N25).
    Raises ``SearchRunning`` (the same movie is searched already), ``NoIndexers`` and ``SearchBusy``."""
    title, _versions, infos = preloaded
    with _lock:
        _expire()
        running = [search for search in _searches.values() if search.state == "running"]
        for search in running:
            if search.title_id == title.title_id:
                raise SearchRunning(search.search_id)
        if not infos:
            raise NoIndexers
        if sum(1 for search in running if search.lane == MANUAL) >= MAX_RUNNING or not _make_room():
            raise SearchBusy
        search = Search(
            search_id=secrets.token_hex(8), title_id=title.title_id, started_at=now(), preloaded=preloaded
        )
        _spawn(search)
    return search.search_id


def start_automatic(
    title_id: int,
    *,
    origin: str,
    indexer_ids: Collection[int] | None,
    reserved: dict[int, int] | None = None,
    after: Callable[[Search], None] | None = None,
    scope: dict[str, Any] | None = None,
    searched: Collection[int] = (),
    covered: Collection[int] = (),
    skip_titles: Collection[int] = (),
    album_scope: dict[str, Any] | None = None,
    user_invoked: bool = False,
) -> str:
    """Start a search of the automatic lane. Raises ``SearchRunning`` for a title with any running search and
    ``SearchBusy`` when the lane is full; the owner's lane is never touched. A series search gives its scope (S5), an
    album search its album scope (Music M5)."""
    with _lock:
        _expire()
        running = [search for search in _searches.values() if search.state == "running"]
        for search in running:
            if search.title_id == title_id:
                raise SearchRunning(search.search_id)
        if sum(1 for search in running if search.lane == AUTOMATIC) >= AUTOMATIC_MAX_RUNNING:
            raise SearchBusy
        search = Search(
            search_id=secrets.token_hex(8),
            title_id=title_id,
            started_at=now(),
            lane=AUTOMATIC,
            origin=origin,
            indexer_ids=frozenset(indexer_ids) if indexer_ids is not None else None,
            reserved=dict(reserved or {}),
            after=after,
            scope=dict(scope) if scope is not None else None,
            asked_scope=dict(scope) if scope is not None else None,
            searched=tuple(searched),
            covered=tuple(covered),
            skip_titles=frozenset(skip_titles),
            album_scope=dict(album_scope) if album_scope is not None else None,
            user_invoked=user_invoked,
        )
        _spawn(search)
    return search.search_id


def running_search(title_id: int) -> str | None:
    """The id of a running search of the title, of either lane."""
    with _lock:
        for search in _searches.values():
            if search.state == "running" and search.title_id == title_id:
                return search.search_id
    return None


def running_count(lane: str) -> int:
    with _lock:
        return sum(1 for search in _searches.values() if search.state == "running" and search.lane == lane)


def keep_found(title_id: int, *, origin: str, states: list[runner.IndexerState]) -> Search | None:
    """Keep a finished search of the automatic lane made from releases already fetched (RSS), without a request: the
    title and its versions are read and the releases judged as a search judges them. None when the title is gone or no
    movie. It runs nothing and counts against no cap; the caller drops it with ``drop`` after its load decision."""
    loaded = load(title_id, only=())
    if loaded is None:
        return None
    title, versions, _infos = loaded
    decided, found = report.evaluate(title, versions, states, indexers.now(), options.prefer_indexer_flags())
    moment = now()
    search = Search(
        search_id=secrets.token_hex(8),
        title_id=title_id,
        started_at=moment,
        state="done",
        finished_at=moment,
        indexers=states,
        versions=decided,
        releases=found,
        lane=AUTOMATIC,
        origin=origin,
        indexer_ids=frozenset(state.info.indexer_id for state in states),
    )
    with _lock:
        _expire()
        _searches[search.search_id] = search
    return search


def keep_found_album(title_id: int, *, origin: str, states: list[runner.IndexerState]) -> Search | None:
    """``keep_found`` for an album (Music M5, decision 13): the releases RSS matched to it, judged as an album search
    judges them. None when the album is gone."""
    loaded = load_album(title_id, only=())
    if loaded is None:
        return None
    album, _infos = loaded
    decided, found = album_search.evaluate(album, states, indexers.now())
    moment = now()
    search = Search(
        search_id=secrets.token_hex(8),
        title_id=title_id,
        started_at=moment,
        state="done",
        finished_at=moment,
        indexers=states,
        versions=decided,
        releases=found,
        lane=AUTOMATIC,
        origin=origin,
        indexer_ids=frozenset(state.info.indexer_id for state in states),
        album_scope={"kind": "album", "aliases": False},
    )
    with _lock:
        _expire()
        _searches[search.search_id] = search
    return search


def keep_found_series(
    title_id: int, *, origin: str, states: list[runner.IndexerState], episode_ids: Collection[int]
) -> Search | None:
    """``keep_found`` for a series (S5, decision 21): the releases RSS fetched, judged for these episodes as a series
    search judges them. None when the series is gone or none of the episodes is left."""
    scope = {"kind": series_search.RSS, "episodes": sorted(set(episode_ids))}
    try:
        with SessionLocal() as db:
            series = series_search.load(db, title_id, scope, watching.today())
    except series_search.ScopeInvalid:
        return None
    if series is None:
        return None
    decided, found = series_search.evaluate(series, states, indexers.now())
    moment = now()
    search = Search(
        search_id=secrets.token_hex(8),
        title_id=title_id,
        started_at=moment,
        state="done",
        finished_at=moment,
        indexers=states,
        versions=decided,
        releases=found,
        lane=AUTOMATIC,
        origin=origin,
        indexer_ids=frozenset(state.info.indexer_id for state in states),
        scope=dict(series.scope),
        asked_scope=scope,
    )
    with _lock:
        _expire()
        _searches[search.search_id] = search
    return search


def rejudge_series(search_id: str, excluded: Collection[str]) -> bool:
    """Judge a finished series search of the automatic lane again without the releases ``excluded`` (S5, decision 16).
    The series is read again, so episodes a download of this search holds by now count as covered. False when the
    search is gone, no automatic series search, or the series no longer fits its scope."""
    with _lock:
        search = _searches.get(search_id)
    if search is None or search.asked_scope is None or search.state != "done":
        return False
    try:
        with SessionLocal() as db:
            series = series_search.load(db, search.title_id, search.asked_scope, watching.today())
    except series_search.ScopeInvalid:
        return False
    if series is None:
        return False
    left_out = set(excluded)
    with search.lock:
        states = [
            dataclasses.replace(
                state, releases={key: release for key, release in state.releases.items() if key not in left_out}
            )
            for state in search.indexers
        ]
    decided, found = series_search.evaluate(series, states, indexers.now())
    with search.lock:
        search.versions, search.releases = decided, found
    return True


def drop(search_id: str) -> None:
    """Forget a kept search of the automatic lane; the owner's searches stay."""
    with _lock:
        search = _searches.get(search_id)
        if search is not None and search.lane == AUTOMATIC:
            del _searches[search_id]


def kept(search_id: str) -> Search | None:
    """The kept search itself, or None when it does not exist or expired. For what needs the releases as the feed
    gave them, such as their publish dates."""
    with _lock:
        _expire()
        return _searches.get(search_id)


def snapshot(search_id: str) -> dict[str, Any] | None:
    """The search as the API shows it, or None when it does not exist or expired."""
    with _lock:
        _expire()
        search = _searches.get(search_id)
    if search is None:
        return None
    with search.lock:
        return {
            "search_id": search.search_id,
            "title_id": search.title_id,
            "kind": "album" if search.album_scope is not None else "series" if search.scope is not None else "movie",
            "scope": copy.deepcopy(search.album_scope if search.album_scope is not None else search.scope),
            "targets": list(search.targets),
            "state": search.state,
            "started_at": search.started_at,
            "finished_at": search.finished_at,
            "indexers": [
                {
                    "indexer_id": state.info.indexer_id,
                    "name": state.info.name,
                    "state": state.state,
                    "error_code": state.error_code,
                    "queries": [
                        {"kind": query.kind, "text": query.text, "releases": query.releases} for query in state.queries
                    ],
                    "releases": len(state.releases),
                    "took_ms": state.took_ms,
                }
                for state in search.indexers
            ],
            # ⚠️ Deep copies: every read adds the blocklist and moves ranks (loading.decorate), never in the kept search.
            "versions": copy.deepcopy(search.versions),
            "releases": copy.deepcopy(search.releases),
        }


class SearchGone(Exception):
    """The search does not exist, expired or still runs."""


class ReleaseMissing(Exception):
    """The search has no release with this key that belongs to the movie."""


@dataclass(frozen=True)
class FoundRelease:
    title_id: int
    #: The release as the answer shows it, a copy.
    release: dict[str, Any]
    indexer_id: int
    indexer_name: str
    #: usenet or torrent.
    protocol: str
    #: ⚠️ The download link with its key or passkey: for the fetch only, never in a repr, an answer or a log line.
    link: str | None = field(repr=False)
    info_hash: str | None = None
    #: The origin of the search it came from, for the download.
    origin: str = "manual"
    #: The scope of a series search (S4); None for a movie.
    scope: dict[str, Any] | None = None
    #: A release of an album search (decision 1): ``verdict`` instead of ``versions``.
    album: bool = False


def series_takes(search_id: str, definition_id: int) -> list[tuple[str, frozenset[str]]]:
    """What a finished series search would take for a version, in order (S4, decision 3): each release key with the
    codes of the episodes it was taken for, never those an earlier release of the set covers (decision 2).

    Raises ``SearchGone``; an empty list for a movie search or a version without takes.
    """
    with _lock:
        _expire()
        search = _searches.get(search_id)
    if search is None:
        raise SearchGone
    with search.lock:
        if search.state != "done":
            raise SearchGone
        if search.scope is None:
            return []
        decision = next((item for item in search.versions if item.get("version_id") == definition_id), None)
        return [
            (str(take["release_key"]), frozenset([*take.get("fills", []), *take.get("replaces", [])]))
            for take in (decision or {}).get("takes") or []
        ]


def find_release(search_id: str, release_key: str) -> FoundRelease:
    """A release of a finished search that belongs to the movie, with its link.

    Raises ``SearchGone`` or ``ReleaseMissing``.
    """
    with _lock:
        _expire()
        search = _searches.get(search_id)
        origin = search.origin if search is not None else "manual"
    if search is None:
        raise SearchGone
    with search.lock:
        if search.state != "done":
            raise SearchGone
        album = search.album_scope is not None
        # A release of an album search loads only when it is this album (decision 2): it has a
        # verdict. One of another album or of none never has.
        entry = next(
            (
                item
                for item in search.releases
                if item.get("release_key") == release_key
                and (item.get("verdict") is not None if album else item.get("belongs"))
            ),
            None,
        )
        if entry is None:
            raise ReleaseMissing
        for state in search.indexers:
            if state.info.indexer_id != entry["indexer_id"]:
                continue
            release = state.releases.get(release_key)
            if release is not None:
                return FoundRelease(
                    title_id=search.title_id,
                    release=copy.deepcopy(entry),
                    indexer_id=state.info.indexer_id,
                    indexer_name=state.info.name,
                    protocol=state.info.protocol,
                    link=release.link,
                    info_hash=release.info_hash,
                    origin=origin,
                    scope=copy.deepcopy(search.scope),
                    album=album,
                )
    raise ReleaseMissing


def info_hashes(search_id: str) -> dict[str, str]:
    """Release key to the info hash a Torznab feed gave, for the blocklist; empty when the search is gone."""
    with _lock:
        search = _searches.get(search_id)
    if search is None:
        return {}
    with search.lock:
        return {
            key: release.info_hash
            for state in search.indexers
            for key, release in state.releases.items()
            if release.info_hash
        }


def wait_idle(timeout: float = 30.0) -> bool:
    """Wait for the searches started with ``start`` and ``start_automatic``. True when none is left running."""
    deadline = time.monotonic() + timeout
    with _lock:
        threads = list(_threads)
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    with _lock:
        _threads[:] = [thread for thread in _threads if thread.is_alive()]
        return not _threads


def reset() -> None:
    """Forget every search. For the tests, after ``wait_idle``."""
    with _lock:
        _searches.clear()


# --- The worker ---------------------------------------------------------------------------------- #


def _execute(search: Search) -> None:
    started = time.perf_counter()
    try:
        asyncio.run(_run(search))
    except Exception:
        logger.exception("Search %s of title %d failed unexpectedly", search.search_id, search.title_id)
    finally:
        with search.lock:
            search.state = "done"
            search.finished_at = now()
        logger.info("Search %s finished in %dms", search.search_id, int((time.perf_counter() - started) * 1000))
    try:
        if search.after is not None:
            search.after(search)
    except Exception:
        logger.exception("The step after search %s of title %d failed unexpectedly", search.search_id, search.title_id)
    finally:
        if search.lane == AUTOMATIC:
            _settle(search)


def _settle(search: Search) -> None:
    """An automatic search after its load decision: dropped, or kept in the owner's lane when he followed it."""
    with _lock:
        if _searches.get(search.search_id) is not search:
            return
        if not search.adopted:
            del _searches[search.search_id]
            return
        search.lane, search.origin = MANUAL, "manual"
        _make_room(keep=search)


async def _run_series(search: Search, scope: dict[str, Any]) -> None:
    """A series search (S3): TheXEM's numbers first when due, then the series' queries and the series decisions."""
    try:
        await xem.refresh_title(search.title_id)
    except Exception:
        logger.exception("Search %s: TheXEM step failed", search.search_id)
    try:
        loaded = load_series(search.title_id, scope, only=search.indexer_ids)
    except series_search.ScopeInvalid:
        logger.info("Search %s: the scope no longer fits title %d", search.search_id, search.title_id)
        return
    if loaded is None:
        logger.info("Search %s: series %d is gone", search.search_id, search.title_id)
        return
    series, infos = loaded
    states = [runner.IndexerState(info=info) for info in infos]
    with search.lock:
        search.indexers = states
        search.scope = dict(series.scope)
        search.targets = [target.code for target in series.targets]
    try:
        await runner.run_indexers(states, series, search.lock, search.skip_titles)
    finally:
        with search.lock:
            for state in states:
                state.info = dataclasses.replace(state.info, api_key="")
    record(states)
    decided, found = series_search.evaluate(series, states, indexers.now())
    with search.lock:
        search.versions, search.releases = decided, found
    logger.info(
        "Search %s of series %d (%s): %d targets, %d indexers, %d releases, %d of this series",
        search.search_id,
        series.title_id,
        series.scope.get("kind"),
        len(series.targets),
        len(states),
        len(found),
        sum(1 for entry in found if entry["belongs"]),
    )


async def _run_album(search: Search, scope: dict[str, Any]) -> None:
    """An album search (M3): the album's queries, every release matched to an album of the artist and judged."""
    loaded = load_album(search.title_id, aliases=bool(scope.get("aliases")), only=search.indexer_ids)
    if loaded is None:
        logger.info("Search %s: album %d is gone", search.search_id, search.title_id)
        return
    album, infos = loaded
    states = [runner.IndexerState(info=info) for info in infos]
    with search.lock:
        search.indexers = states
    try:
        await runner.run_indexers(states, album, search.lock)
    finally:
        with search.lock:
            for state in states:
                state.info = dataclasses.replace(state.info, api_key="")
    record(states)
    decided, found = album_search.evaluate(album, states, indexers.now())
    with search.lock:
        search.versions, search.releases = decided, found
    logger.info(
        "Search %s of album %d: %d indexers, %d releases, %d of this album",
        search.search_id,
        album.title_id,
        len(states),
        len(found),
        sum(1 for entry in found if entry["verdict"] is not None),
    )


async def _run(search: Search) -> None:
    if search.album_scope is not None:
        await _run_album(search, search.album_scope)
        return
    if search.scope is not None:
        await _run_series(search, search.scope)
        return
    loaded = search.preloaded or load(search.title_id, only=search.indexer_ids)
    if loaded is None:
        logger.info("Search %s: title %d is gone", search.search_id, search.title_id)
        return
    title, versions, infos = loaded
    states = [runner.IndexerState(info=info) for info in infos]
    with search.lock:
        search.indexers = states
    try:
        await runner.run_indexers(states, title, search.lock)
    finally:
        with search.lock:
            for state in states:
                # The keys are not needed any more.
                state.info = dataclasses.replace(state.info, api_key="")
    record(states)
    decided, found = report.evaluate(title, versions, states, indexers.now(), options.prefer_indexer_flags())
    with search.lock:
        search.versions, search.releases = decided, found
    logger.info(
        "Search %s of title %d: %d indexers, %d releases, %d of them of this movie, %d versions decided",
        search.search_id,
        title.title_id,
        len(states),
        len(found),
        sum(1 for entry in found if entry["belongs"]),
        sum(1 for entry in decided if entry["has_profile"]),
    )


# --- The database -------------------------------------------------------------------------------- #


def _indexer_info(row: Indexer, moment: datetime) -> IndexerInfo:
    key = ""
    error_code = None
    if row.api_key:
        key = crypto.decrypt(row.api_key) or ""
        if not key:
            error_code = "indexer_key_missing"
    stale = row.caps_checked_at is None or row.caps_checked_at < moment - indexers.CAPS_MAX_AGE
    return IndexerInfo(
        indexer_id=row.id,
        name=row.name,
        kind=row.kind,
        url=row.url,
        api_key=key,
        categories=tuple(row.categories or []),
        series_categories=tuple(row.series_categories or []),
        music_categories=tuple(row.music_categories) if row.music_categories is not None else None,
        anime_categories=tuple(row.anime_categories) if row.anime_categories is not None else None,
        anime_standard_format_search=bool(row.anime_standard_format_search),
        caps=row.caps,
        caps_stale=stale,
        paused_until=row.paused_until,
        priority=row.priority,
        minimum_seeders=row.minimum_seeders if row.kind == "torznab" else None,
        retention_days=download_retention.days(object_session(row)) if row.kind == "newznab" else None,
        multi_languages=tuple(row.multi_languages or []),
        remove_year=bool(row.remove_year),
        error_code=error_code,
    )


def load(
    title_id: int, only: Collection[int] | None = None
) -> tuple[TitleInfo, list[VersionInfo], list[IndexerInfo]] | None:
    """The title, its versions and the enabled indexers (of ``only`` when given); None when the title is gone or no
    movie."""
    with SessionLocal() as db:
        title = db.get(Title, title_id)
        if title is None or title.kind != "movie":
            return None
        alternatives = list(db.scalars(select(AlternateTitle.text).where(AlternateTitle.title_id == title.id)))
        info = title_info(
            title_id=title.id,
            title=title.title,
            original_title=title.original_title,
            year=title.year,
            tmdb_id=title.tmdb_id,
            imdb_id=title.imdb_id,
            original_language=title.original_language,
            runtime_min=title.runtime,
            alternative_titles=alternatives,
            stored_keys=[title.search_keys, title.tmdb_search_keys],
        )
        rows = (
            db.execute(
                select(Version, VersionDefinition.label, VersionDefinition.delay)
                .join(VersionDefinition, VersionDefinition.id == Version.version_definition_id)
                .where(Version.title_id == title.id)
                .order_by(VersionDefinition.id)
            )
            .tuples()
            .all()
        )
        ids = [version.version_definition_id for version, _label, _delay in rows]
        stored = profile_store.by_versions(db, ids)
        versions: list[VersionInfo] = []
        # The delay rule for this title's tags.
        title_tags = tag_store.title_tag_ids(db, title_id)
        for version, label, stored_delay in rows:
            profile = stored.get(version.version_definition_id)
            rules = profile.rules if profile is not None and isinstance(profile.rules, dict) else None
            # Searching is a movie matter until S3; a series profile is no rule set for this engine.
            usable = rules is not None and rules.get("kind") == "movie"
            versions.append(
                VersionInfo(
                    version_id=version.version_definition_id,
                    label=label,
                    has_profile=profile is not None,
                    rules=rules if usable else None,
                    # The release name the file came with judges it, as Radarr judges by its scene name; the stored
                    # judgement names the file the same way (``judging``).
                    current_file=judging.current_file(version) if version.has_file else None,
                    delay=delay_rules.for_title(stored_delay, title_tags),
                )
            )
        moment = indexers.now()
        # Tags: an indexer with tags is asked only for titles sharing one, as in the apps.
        statement = select(Indexer).where(
            Indexer.enabled.is_(True), tag_store.indexer_condition(tag_store.title_tag_ids(db, title_id))
        )
        if only is not None:
            statement = statement.where(Indexer.id.in_(sorted(only)))
        infos = [_indexer_info(row, moment) for row in db.scalars(statement.order_by(Indexer.id))]
    return info, versions, infos


def load_preview(
    title: TitleInfo, definition_ids: list[int]
) -> tuple[TitleInfo, list[VersionInfo], list[IndexerInfo]]:
    """The inputs of a search of a movie in no library: the versions to judge by, without a file, and every enabled
    indexer (N25)."""
    with SessionLocal() as db:
        rows = list(
            db.scalars(
                select(VersionDefinition)
                .where(VersionDefinition.id.in_(definition_ids), VersionDefinition.kind == "movie")
                .order_by(VersionDefinition.id)
            )
        )
        stored = profile_store.by_versions(db, [row.id for row in rows])
        versions: list[VersionInfo] = []
        for row in rows:
            profile = stored.get(row.id)
            rules = profile.rules if profile is not None and isinstance(profile.rules, dict) else None
            usable = rules is not None and rules.get("kind") == "movie"
            versions.append(
                VersionInfo(
                    version_id=row.id,
                    label=row.label,
                    has_profile=profile is not None,
                    rules=rules if usable else None,
                    current_file=None,
                    delay=delay_rules.from_stored(row.delay),
                )
            )
        moment = indexers.now()
        # A title nexcrate does not have carries no tags: only indexers without a tag.
        infos = [
            _indexer_info(row, moment)
            for row in db.scalars(
                select(Indexer)
                .where(Indexer.enabled.is_(True), tag_store.indexer_condition(set()))
                .order_by(Indexer.id)
            )
        ]
    return title, versions, infos


def load_series(
    title_id: int, scope: dict[str, Any], only: Collection[int] | None = None
) -> tuple[series_search.SeriesSearch, list[IndexerInfo]] | None:
    """The series with its targets and versions, and the enabled indexers; None when the title is gone or no series.
    Raises ``series_search.ScopeInvalid``."""
    with SessionLocal() as db:
        found = series_search.load(db, title_id, scope, watching.today())
        if found is None:
            return None
        moment = indexers.now()
        statement = select(Indexer).where(
            Indexer.enabled.is_(True), tag_store.indexer_condition(tag_store.title_tag_ids(db, title_id))
        )
        if only is not None:
            statement = statement.where(Indexer.id.in_(sorted(only)))
        infos = [_indexer_info(row, moment) for row in db.scalars(statement.order_by(Indexer.id))]
    return found, infos


def load_album(
    title_id: int, *, aliases: bool = False, only: Collection[int] | None = None
) -> tuple[album_search.AlbumSearch, list[IndexerInfo]] | None:
    """The album with what its search needs, and the enabled indexers; None when the title is gone or no album."""
    with SessionLocal() as db:
        found = album_search.load(db, title_id, aliases=aliases)
        if found is None:
            return None
        moment = indexers.now()
        # An album counts with its artist's tags, as in Lidarr.
        statement = select(Indexer).where(
            Indexer.enabled.is_(True), tag_store.indexer_condition(tag_store.title_tag_ids(db, title_id))
        )
        if only is not None:
            statement = statement.where(Indexer.id.in_(sorted(only)))
        infos = [_indexer_info(row, moment) for row in db.scalars(statement.order_by(Indexer.id))]
    return found, infos


def _store_limits(row: Indexer, limits: indexers.ApiLimits, moment: datetime) -> None:
    row.api_current, row.api_max = limits.api_current, limits.api_max
    row.grab_current, row.grab_max = limits.grab_current, limits.grab_max
    row.api_next_at, row.grab_next_at = limits.api_next_at, limits.grab_next_at
    row.limits_seen_at = moment


def record(states: list[runner.IndexerState]) -> None:
    """Per indexer that was asked: the last error code, a pause after a limit, fresh caps, the latest
    ``newznab:apilimits`` and the escalation."""
    moment = indexers.now()
    started = budget.started_at()
    with SessionLocal() as db:
        for state in states:
            if state.state not in ("done", "failed") or state.info.error_code is not None:
                continue
            row = db.get(Indexer, state.info.indexer_id)
            if row is None:
                continue
            failure = state.error_code if state.state == "failed" else None
            row.last_error_code = failure
            if state.paused_until is not None:
                row.paused_until = state.paused_until
            if state.caps_fetched:
                row.caps = state.fresh_caps
                row.caps_checked_at = moment
            if state.limits is not None:
                _store_limits(row, state.limits, moment)
            current = budget.Escalation(
                level=row.escalation_level or 0,
                initial_failure_at=row.initial_failure_at,
                paused_until=row.automatic_paused_until,
            )
            changed = budget.after_request(current, failure, now=moment, started=started)
            if changed is not None and changed != current:
                if changed.level > current.level:
                    logger.info("Indexer %d: automatic work pauses at escalation level %d", row.id, changed.level)
                row.escalation_level = changed.level
                row.initial_failure_at = changed.initial_failure_at
                row.automatic_paused_until = changed.paused_until
        db.commit()
