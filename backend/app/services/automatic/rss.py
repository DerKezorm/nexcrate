"""RSS: the newest releases of every indexer, paged as in Radarr, matched to the titles that want something and loaded
(C5, decisions 7, 9, 10, 14 and 17).

**Every minute** (``run_job``), only with the switch on; off, the job returns at once, without a request or a log line.

1. **Which indexers:** enabled, with ``automatic_search``, not paused by a request limit (``paused_until``) or the
   escalation (``automatic_paused_until``), and not with all of ``apiMax`` used until ``apiNextAvailable``
   (``budget.rss_stop``). The 80 percent stop of planned searches does not hold RSS back. An indexer is due once its
   last sync lies ``budget.rss_interval`` back: every 15 minutes, less often when a known limit reserves fewer syncs.
2. **The request,** built as the search builds its pages (``search.plan``): ``t=movie`` when the caps offer movie
   search, else ``t=search``; the indexer's categories, its default categories when it has none; no query; ``offset``
   and ``limit`` with the page size of the caps, at most 100. Stale caps are fetched first, as a search does. Answers,
   error documents, ``newznab:apilimits``, the pause after a request limit and the escalation are read and written back
   as for a search (``search.jobs.record``), so RSS failures count like search failures.
3. **Paging as Radarr:** the first sync of an indexer reads one page. A later sync pages back until a page holds the
   newest release of the sync before, by its release key or by a publish date at or before it, or until a page is not
   full. 30 pages or 1000 releases without reaching it leave a gap: ``rss_gap_at`` and a warning. The newest release of
   the first page becomes ``rss_newest_at`` and ``rss_newest_key`` (the release key of the search, never the link), and
   ``rss_last_at`` is the time of the sync. A failed page keeps the releases before it for matching and moves the
   newest release nowhere; the sync still counts for the interval, so a broken indexer is not asked every minute. The
   first request of a sync is RSS's reserve; every further request is charged to the indexer's bucket.
4. **Matching** (decision 9), against the titles with a version that wants something (``planning.load_facts``), looked
   up once per round: the feed's ``imdb`` attribute, then ``tmdbid``, counted only when the year read from the name is
   missing, equals the title's year, or equals the year of its first premiere (TMDB type 1); otherwise the title read
   from the name and its year through every spelling key of the title, the original title and the alternative titles
   (``search.model.title_info``), where Radarr matches one spelling. By name the year must be the title's own, as for a
   search, and a release whose numbers name another movie does not match. A release that fits two titles matches none.
5. **Judging and loading** (decisions 10 and 14), title after title: a search of the automatic lane with origin ``rss``
   made from the title's matched releases without a request (``search.jobs.keep_found``), judged with the engine and
   the ranking of a search, loaded through the scheduler's load step (only a fitting release that is not blocked, never
   one that needs a confirmation, one download per version), and dropped right after. The title's ``search_summary``
   is written only when something loaded; ``last_search_at`` and the planned schedule stay as they are.

**Series** (decisions 17 to 21). The job runs while either switch is on and loads only for the
kinds switched on. **One request per indexer:** both on and the indexer has series categories, ``t=search`` with the
movie and the series categories (measured at a Newznab and a Torznab indexer: the union of both feeds); only movies,
the request above; only series, ``t=tvsearch`` in the series categories, ``t=search`` without tv-search, and no request
at an indexer without series categories. A sync of another form than the one before (``indexers.rss_form``, none
stored counts as the movie form) reads one page, as a first sync. **Matching a series:** among the series with an
episode that wants something (``series_planning``, before its air date and without one too): the feed's ``tvdbid``,
then ``imdb``, then the title read from the name through the spelling keys of the series, its aliases and TheXEM's
names, with a year within one of the series' year. A number that names another series or a movie of the library
matches nothing, and a series release never matches a movie by the number of a series of the library. A release that
fits two series matches none. **Judging and loading:** a series search of the automatic lane made from the matched
releases for the wanting episodes (``search.jobs.keep_found_series``), loaded as a planned search loads
(``series_loading.load``: the set, the owner's pack rule, forming the set again). The summary per season is written only
when something loaded; the episodes' search times stay.

Log lines carry ids, counts and codes only, never titles, release names, paths or links: one line per indexer per sync,
and the warning about a gap.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import secrets
import threading
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ...db import SessionLocal
from ...models import AlternateTitle, Indexer, Title, TitleAlias, Version, XemName
from .. import indexers, logs, releases, schreibweisen
from .. import tags as tag_store
from ..downloads import loading
from ..music import hit_matching
from ..releases import parser as release_parser
from ..releases.music_parser import parse_album
from ..search import album as album_search
from ..search import jobs as search_jobs
from ..search import plan as search_plan
from ..search import runner as search_runner
from ..search import series as series_search
from ..search.model import TitleInfo, imdb_number, title_info
from ..series import anime
from . import anchors, budget, clock, planning, scheduler, series_loading, series_planning, settings

logger = logging.getLogger("nexcrate.automatic")

JOB_NAME = "rss_sync"
INTERVAL_SECONDS = 60.0
ORIGIN = "rss"
#: Radarr's limits of one sync.
MAX_PAGES = 30
MAX_RELEASES = 1000
#: How long one round may ask its indexers. A sync still paging then keeps what it has and moves no newest release.
ROUND_TIMEOUT_SECONDS = 300.0
FACTS_CHUNK = 500
PREMIERE_TYPE = 1
#: The forms of the request: what the sync asks for. Since Music M5 (decision 12) music joins
#: them; the column holds eight characters, so the combined forms have short names.
MOVIE, SERIES, BOTH = "movie", "series", "both"
MUSIC, MOVIE_MUSIC, SERIES_MUSIC, ALL = "music", "mo+mu", "se+mu", "all"
#: The kinds each form asks for.
FORM_PARTS: dict[str, frozenset[str]] = {
    MOVIE: frozenset({"movie"}),
    SERIES: frozenset({"series"}),
    BOTH: frozenset({"movie", "series"}),
    MUSIC: frozenset({"music"}),
    MOVIE_MUSIC: frozenset({"movie", "music"}),
    SERIES_MUSIC: frozenset({"series", "music"}),
    ALL: frozenset({"movie", "series", "music"}),
}
#: The kind of a title an album switch stands for.
ALBUM = "album"
#: A year in a series release's name may be this far from the series' first year.
YEAR_TOLERANCE = 1

_round_lock = threading.Lock()


class _OutOfTime(Exception):
    """The round's time is over before the next request."""


@dataclass
class Sync:
    """One indexer's sync in a round."""

    indexer_id: int
    #: The indexer as a search asks it, with the releases found by their key, in the order of the feed.
    state: search_runner.IndexerState
    #: The newest release of the sync before; both None before the first sync.
    known_key: str | None = None
    known_at: datetime | None = None
    pages: int = 0
    #: Releases on the pages read, before merging.
    fetched: int = 0
    #: The newest release of the first page.
    newest_key: str | None = None
    newest_at: datetime | None = None
    #: Paging ended as planned: at the newest release of the sync before, at a page not full, or at a cap.
    finished: bool = False
    #: Ended at 30 pages or 1000 releases without reaching the newest release of the sync before.
    gap: bool = False
    #: What the request asks for: movie, series or both.
    form: str = MOVIE

    @property
    def first(self) -> bool:
        return self.known_key is None and self.known_at is None


# --- The round ------------------------------------------------------------------------------------------------------ #


def run_job() -> None:
    """The background job."""
    token = logs.bind_request(secrets.token_hex(4))
    try:
        run_round()
    finally:
        logs.unbind_request(token)


def run_round() -> int:
    """One round: sync every due indexer, match, judge and load. Returns how many indexers were synced."""
    if not _round_lock.acquire(blocking=False):
        return 0
    try:
        return _round(clock.now())
    finally:
        _round_lock.release()


def _round(now: datetime) -> int:
    with SessionLocal() as db:
        kinds = settings.load_kinds(db)
        if not kinds:
            return 0
        moment = indexers.now()
        syncs: list[Sync] = []
        for row in due_indexers(db, now):
            form = form_of(row, kinds)
            if form is None:
                continue
            # Another form than the sync before: its newest release says nothing about this feed.
            same = form == (row.rss_form or MOVIE)
            syncs.append(
                Sync(
                    indexer_id=row.id,
                    # The indexer as a search reads it: the key decrypted, stale caps, a stored pause.
                    state=search_runner.IndexerState(info=search_jobs._indexer_info(row, moment)),
                    known_key=row.rss_newest_key if same else None,
                    known_at=row.rss_newest_at if same else None,
                    form=form,
                )
            )
    if not syncs:
        return 0
    try:
        asyncio.run(read_all(syncs))
    finally:
        for sync in syncs:
            # The keys are not needed any more.
            sync.state.info = dataclasses.replace(sync.state.info, api_key="")
    fetched = any(sync.state.releases for sync in syncs)
    library = library_numbers() if fetched else Numbers()
    lookups = wanted_lookups(now, library) if fetched and MOVIE in kinds else Lookups()
    series_lookups = wanted_series(now, library) if fetched and SERIES in kinds else SeriesLookups()
    album_lookups = wanted_albums(now) if fetched and ALBUM in kinds else AlbumLookups()
    for sync in syncs:
        write_back(sync, now)
        parts = FORM_PARTS.get(sync.form, FORM_PARTS[MOVIE])
        matched = lookups.match_all(sync.state.releases) if "movie" in parts else {}
        series_matched = series_lookups.match_all(sync.state.releases) if "series" in parts else {}
        album_matched = album_lookups.match_all(sync.state.releases) if "music" in parts else {}
        # Tags: RSS reads every indexer; what an indexer with tags delivers counts only for
        # titles sharing one, as the apps' IndexerTagSpecification rejects the rest.
        allowed = _allowed_titles(sync.indexer_id, {*matched, *series_matched, *album_matched})
        matched = {title_id: found for title_id, found in matched.items() if title_id in allowed}
        series_matched = {title_id: found for title_id, found in series_matched.items() if title_id in allowed}
        album_matched = {title_id: found for title_id, found in album_matched.items() if title_id in allowed}
        loaded = 0
        for title_id, found in matched.items():
            try:
                loaded += judge_and_load(sync, title_id, found, now)
            except Exception:
                logger.exception("RSS sync of indexer %d: title %d could not be judged", sync.indexer_id, title_id)
        for title_id, found in series_matched.items():
            try:
                loaded += judge_and_load_series(sync, title_id, found, series_lookups.wanting[title_id], now)
            except Exception:
                logger.exception("RSS sync of indexer %d: series %d could not be judged", sync.indexer_id, title_id)
        for title_id, found in album_matched.items():
            try:
                loaded += judge_and_load_album(sync, title_id, found, now)
            except Exception:
                logger.exception("RSS sync of indexer %d: album %d could not be judged", sync.indexer_id, title_id)
        log_sync(sync, len(matched) + len(series_matched) + len(album_matched), loaded)
    return len(syncs)


def _music_categories(row: Indexer) -> tuple[int, ...]:
    if row.music_categories is not None:
        return tuple(row.music_categories)
    return tuple(album_search.default_music_categories(row.caps))


def _anime_categories(row: Indexer) -> tuple[int, ...]:
    if row.anime_categories is not None:
        return tuple(row.anime_categories)
    return tuple(indexers.default_anime_categories(row.caps))


def form_of(row: Indexer, kinds: Collection[str]) -> str | None:
    """What one sync of the indexer asks for (decision 17; music since M5); None when it has nothing to ask."""
    movie = MOVIE in kinds
    series = SERIES in kinds and bool(row.series_categories or _anime_categories(row))
    music = ALBUM in kinds and bool(_music_categories(row))
    wanted = frozenset(
        part for part, on in (("movie", movie), ("series", series), ("music", music)) if on
    )
    return next((form for form, parts in FORM_PARTS.items() if parts == wanted), None)


def _allowed_titles(indexer_id: int, title_ids: set[int]) -> set[int]:
    if not title_ids:
        return set()
    with SessionLocal() as db:
        return tag_store.indexer_allows(db, indexer_id, title_ids)


def due_indexers(db: OrmSession, now: datetime) -> list[Indexer]:
    """The enabled indexers with automatic search whose sync is due and that no stop point holds back, by id."""
    due: list[Indexer] = []
    rows = db.scalars(
        select(Indexer).where(Indexer.enabled.is_(True), Indexer.automatic_search.is_(True)).order_by(Indexer.id)
    )
    for row in rows:
        standing = budget.Standing.of(row)
        if budget.rss_stop(standing, now) is not None:
            continue
        last = row.rss_last_at
        # A last sync ahead of now (the clock went back) does not hold the indexer back.
        if last is not None and last <= now < last + budget.rss_interval(standing):
            continue
        due.append(row)
    return due


# --- Reading the feed ----------------------------------------------------------------------------------------------- #


def feed_query(caps: dict[str, Any] | None, form: str = MOVIE) -> search_plan.Query:
    """No query text. Movies: ``t=movie`` when the caps offer movie search; series: ``t=tvsearch`` when they offer
    tv-search; any other form, music too, ``t=search``."""
    used = search_plan.caps_in_use(caps)
    function = "search"
    if form == MOVIE and used.get("movie_search"):
        function = "movie"
    elif form == SERIES and used.get("tv_search"):
        function = "tvsearch"
    return search_plan.Query("rss", "", {"t": function})


def feed_categories(info: Any, caps: dict[str, Any] | None, form: str) -> tuple[int, ...]:
    """The categories of the kinds the form asks for, in the order movie, series, music, each once: the indexer's movie
    categories (its defaults without any), its series categories, its music categories (the defaults without any)."""
    parts = FORM_PARTS.get(form, FORM_PARTS[MOVIE])
    lists: list[tuple[int, ...]] = []
    if "movie" in parts:
        lists.append(tuple(info.categories) or tuple(indexers.default_categories(caps)))
    if "series" in parts:
        lists.append(tuple(info.series_categories))
        # Anime lies in its own categories (A6); one feed request asks both.
        lists.append(series_search.anime_categories(info))
    if "music" in parts:
        lists.append(tuple(album_search.music_categories(info)))
    joined: list[int] = []
    for categories in lists:
        joined.extend(category for category in categories if category not in joined)
    return tuple(joined)


def newest(releases: list[indexers.Release]) -> indexers.Release | None:
    """The release with the latest publish date, the first of equal ones; without any date, the first release."""
    found: indexers.Release | None = None
    for release in releases:
        if release.published_at is None:
            continue
        if found is None or found.published_at is None or release.published_at > found.published_at:
            found = release
    return found if found is not None else (releases[0] if releases else None)


def _reaches(sync: Sync, key: str, release: indexers.Release) -> bool:
    """Whether a release is the newest release of the sync before, or not newer than it."""
    if sync.known_key is not None and key == sync.known_key:
        return True
    return sync.known_at is not None and release.published_at is not None and release.published_at <= sync.known_at


def _in_time(deadline: float) -> None:
    if indexers.clock() >= deadline:
        raise _OutOfTime


async def read(sync: Sync, deadline: float) -> None:
    """Page one indexer's feed. A failure stays with the sync, with the codes of a search."""
    state = sync.state
    info = state.info
    state.state = "searching"
    try:
        if info.error_code is not None:
            state.state, state.error_code = "failed", info.error_code
            return
        target = indexers.Target(url=info.url, kind=info.kind, api_key=info.api_key, paused_until=info.paused_until)
        async with indexers.IndexerClient(target) as client:
            caps = info.caps
            if info.caps_stale:
                _in_time(deadline)
                state.requests += 1
                caps = await client.caps()
                state.fresh_caps = caps
            query = feed_query(caps, sync.form)
            categories = feed_categories(info, caps, sync.form)
            limit = search_plan.page_size(caps)
            while True:
                _in_time(deadline)
                state.requests += 1
                feed = await client.feed(search_plan.page_params(query, categories, sync.pages * limit, limit))
                sync.pages += 1
                sync.fetched += len(feed.releases)
                if feed.limits is not None:
                    state.limits = feed.limits
                reached = False
                for release in feed.releases:
                    key = search_plan.release_key(info.indexer_id, release)
                    state.releases.setdefault(key, release)
                    reached = reached or _reaches(sync, key, release)
                if sync.pages == 1:
                    top = newest(feed.releases)
                    if top is not None:
                        sync.newest_key = search_plan.release_key(info.indexer_id, top)
                        sync.newest_at = top.published_at
                if sync.first or reached or feed.items < limit:
                    break
                if sync.pages >= MAX_PAGES or sync.fetched >= MAX_RELEASES:
                    sync.gap = True
                    break
        state.state = "done"
        sync.finished = True
    except indexers.IndexerError as exc:
        state.state, state.error_code, state.paused_until = "failed", exc.code, exc.paused_until
    except _OutOfTime:
        state.state, state.error_code = "timeout", search_runner.TIMEOUT_CODE
    except asyncio.CancelledError:
        state.state, state.error_code = "timeout", search_runner.TIMEOUT_CODE
        raise


async def read_all(syncs: list[Sync]) -> None:
    """Every due indexer at once, one request after the other within one, within ``ROUND_TIMEOUT_SECONDS``."""
    deadline = indexers.clock() + ROUND_TIMEOUT_SECONDS
    tasks = [asyncio.ensure_future(read(sync, deadline)) for sync in syncs]
    _done, pending = await asyncio.wait(tasks, timeout=ROUND_TIMEOUT_SECONDS)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    for task, sync in zip(tasks, syncs, strict=True):
        if task.cancelled() or task.exception() is None:
            continue
        logger.error("RSS sync of indexer %d broke off", sync.indexer_id, exc_info=task.exception())
        sync.state.state, sync.state.error_code = "failed", search_runner.BROKEN_CODE
    for sync in syncs:
        if sync.state.state in ("waiting", "searching"):
            sync.state.state, sync.state.error_code = "timeout", search_runner.TIMEOUT_CODE


def write_back(sync: Sync, now: datetime) -> None:
    """What a search writes back (the code, a pause, fresh caps, ``apilimits``, the escalation), the RSS state of the
    indexer, and the charge for every request beyond the first."""
    search_jobs.record([sync.state])
    with SessionLocal() as db:
        row = db.get(Indexer, sync.indexer_id)
        if row is None:
            return
        row.rss_last_at = now
        if sync.finished and sync.newest_key is not None:
            row.rss_newest_key, row.rss_newest_at = sync.newest_key, sync.newest_at
        elif sync.finished and sync.form != (row.rss_form or MOVIE):
            # An empty feed of another form: the newest release of the form before says nothing about this one.
            row.rss_newest_key, row.rss_newest_at = None, None
        if sync.finished:
            row.rss_form = sync.form
        if sync.gap:
            row.rss_gap_at = now
        standing = budget.Standing.of(row)
        db.commit()
    beyond = sync.state.requests - 1
    if beyond > 0:
        budget.charge(standing, beyond, now)


def log_sync(sync: Sync, matched: int, loaded: int) -> None:
    state = sync.state
    if sync.gap:
        logger.warning(
            "RSS sync of indexer %d did not reach the sync before within %d pages and %d releases; a gap may remain",
            sync.indexer_id,
            sync.pages,
            sync.fetched,
        )
    if state.state == "done":
        logger.info(
            "RSS sync of indexer %d: %d pages, %d releases, %d titles matched, %d loaded",
            sync.indexer_id,
            sync.pages,
            sync.fetched,
            matched,
            loaded,
        )
        return
    logger.info(
        "RSS sync of indexer %d stopped with %s: %d pages, %d releases, %d titles matched, %d loaded",
        sync.indexer_id,
        state.error_code,
        sync.pages,
        sync.fetched,
        matched,
        loaded,
    )


# --- Matching ------------------------------------------------------------------------------------------------------- #


@dataclass
class Lookups:
    """The titles with a version that wants something, by IMDb number, by TMDB number, and by spelling key and year."""

    infos: dict[int, TitleInfo] = field(default_factory=dict)
    premiere_years: dict[int, int] = field(default_factory=dict)
    by_imdb: dict[int, list[int]] = field(default_factory=dict)
    by_tmdb: dict[int, list[int]] = field(default_factory=dict)
    by_name: dict[tuple[str, int], list[int]] = field(default_factory=dict)
    #: The numbers of every series of the library: a release that carries one is no movie's.
    series_numbers: Numbers | None = None

    def add(self, info: TitleInfo, premiere_year: int | None = None) -> None:
        self.infos[info.title_id] = info
        if premiere_year is not None:
            self.premiere_years[info.title_id] = premiere_year
        if info.imdb_id is not None:
            self.by_imdb.setdefault(info.imdb_id, []).append(info.title_id)
        if info.tmdb_id is not None:
            self.by_tmdb.setdefault(info.tmdb_id, []).append(info.title_id)
        if info.year is not None:
            for key in info.keys:
                self.by_name.setdefault((key, info.year), []).append(info.title_id)

    def _year_fits(self, title_id: int, year: int | None) -> bool:
        return year is None or year == self.infos[title_id].year or year == self.premiere_years.get(title_id)

    def _numbers_differ(self, title_id: int, release: indexers.Release) -> bool:
        info = self.infos[title_id]
        tmdb = release.tmdb_id is not None and info.tmdb_id is not None and release.tmdb_id != info.tmdb_id
        imdb = release.imdb_id is not None and info.imdb_id is not None and release.imdb_id != info.imdb_id
        return tmdb or imdb

    def match(self, release: indexers.Release) -> int | None:
        """The one title the release belongs to, or None."""
        if not self.infos:
            return None
        numbers = self.series_numbers
        if numbers is not None and (
            (release.tvdb_id is not None and release.tvdb_id in numbers.series_tvdb)
            or (release.imdb_id is not None and release.imdb_id in numbers.series_imdb)
        ):
            return None
        movie = release_parser.parse_movie(release.title)
        found: set[int] = set()
        for table, number in ((self.by_imdb, release.imdb_id), (self.by_tmdb, release.tmdb_id)):
            if number is not None:
                found = {title_id for title_id in table.get(number, ()) if self._year_fits(title_id, movie.year)}
            if found:
                break
        if not found and movie.year is not None:
            named = {
                title_id
                for key in schreibweisen.keys(movie.title)
                for title_id in self.by_name.get((key, movie.year), ())
            }
            found = {title_id for title_id in named if not self._numbers_differ(title_id, release)}
        return next(iter(found)) if len(found) == 1 else None

    def match_all(self, releases: dict[str, indexers.Release]) -> dict[int, dict[str, indexers.Release]]:
        """Title id to its matched releases by key, in the order found; titles by id."""
        matched: dict[int, dict[str, indexers.Release]] = {}
        for key, release in releases.items():
            title_id = self.match(release)
            if title_id is not None:
                matched.setdefault(title_id, {})[key] = release
        return dict(sorted(matched.items()))


def premiere_year(release_dates: object) -> int | None:
    """The year of the title's first premiere (TMDB type 1) in any country."""
    premieres = [item.at for item in anchors.entries(release_dates) if item.type == PREMIERE_TYPE]
    return min(premieres).year if premieres else None


@dataclass(frozen=True)
class Numbers:
    """The numbers of every title of the library by kind."""

    movie_imdb: frozenset[int] = frozenset()
    series_imdb: frozenset[int] = frozenset()
    series_tvdb: frozenset[int] = frozenset()


def library_numbers() -> Numbers:
    movie_imdb: set[int] = set()
    series_imdb: set[int] = set()
    series_tvdb: set[int] = set()
    with SessionLocal() as db:
        numbers = select(Title.kind, Title.imdb_id, Title.tvdb_id).where(Title.kind.in_(("movie", "series")))
        for kind, imdb, tvdb in db.execute(numbers).tuples():
            number = imdb_number(imdb)
            if kind == "series":
                if number is not None:
                    series_imdb.add(number)
                if tvdb:
                    series_tvdb.add(int(tvdb))
            elif number is not None:
                movie_imdb.add(number)
    return Numbers(frozenset(movie_imdb), frozenset(series_imdb), frozenset(series_tvdb))


def wanted_lookups(now: datetime, library: Numbers | None = None) -> Lookups:
    """The lookups over every movie with a version that wants something."""
    lookups = Lookups(series_numbers=library)
    with SessionLocal() as db:
        candidates = list(
            db.scalars(
                select(Version.title_id)
                .join(Title, Title.id == Version.title_id)
                .where(Title.kind == "movie", Version.source_id.is_(None), Version.monitored.is_(True))
                .distinct()
                .order_by(Version.title_id)
            )
        )
        for start in range(0, len(candidates), FACTS_CHUNK):
            chunk = candidates[start : start + FACTS_CHUNK]
            facts = planning.load_facts(db, chunk, now)
            wanting = [
                title_id
                for title_id in chunk
                if (fact := facts.get(title_id)) is not None and any(version.wants for version in fact.versions)
            ]
            if not wanting:
                continue
            alternatives: dict[int, list[str]] = {}
            texts = db.execute(
                select(AlternateTitle.title_id, AlternateTitle.text).where(AlternateTitle.title_id.in_(wanting))
            )
            for title_id, text in texts.tuples():
                alternatives.setdefault(title_id, []).append(text)
            for title in db.scalars(select(Title).where(Title.id.in_(wanting)).order_by(Title.id)):
                info = title_info(
                    title_id=title.id,
                    title=title.title,
                    original_title=title.original_title,
                    year=title.year,
                    tmdb_id=title.tmdb_id,
                    imdb_id=title.imdb_id,
                    alternative_titles=alternatives.get(title.id, []),
                    stored_keys=[title.search_keys, title.tmdb_search_keys],
                )
                lookups.add(info, premiere_year(title.release_dates))
    return lookups


@dataclass(frozen=True)
class SeriesInfo:
    title_id: int
    year: int | None
    tvdb_id: int | None
    imdb_id: int | None
    keys: frozenset[str]


@dataclass
class SeriesLookups:
    """The series with an episode that wants something, by TVDB number, IMDb number and spelling key (decision 19)."""

    infos: dict[int, SeriesInfo] = field(default_factory=dict)
    #: Series id to its wanting episodes.
    wanting: dict[int, tuple[int, ...]] = field(default_factory=dict)
    by_tvdb: dict[int, list[int]] = field(default_factory=dict)
    by_imdb: dict[int, list[int]] = field(default_factory=dict)
    by_key: dict[str, list[int]] = field(default_factory=dict)
    library: Numbers = field(default_factory=Numbers)

    def add(self, info: SeriesInfo, episodes: Collection[int]) -> None:
        self.infos[info.title_id] = info
        self.wanting[info.title_id] = tuple(sorted(episodes))
        if info.tvdb_id is not None:
            self.by_tvdb.setdefault(info.tvdb_id, []).append(info.title_id)
        if info.imdb_id is not None:
            self.by_imdb.setdefault(info.imdb_id, []).append(info.title_id)
        for key in info.keys:
            self.by_key.setdefault(key, []).append(info.title_id)

    def _numbers_differ(self, title_id: int, release: indexers.Release) -> bool:
        info = self.infos[title_id]
        tvdb = release.tvdb_id is not None and info.tvdb_id is not None and release.tvdb_id != info.tvdb_id
        imdb = release.imdb_id is not None and info.imdb_id is not None and release.imdb_id != info.imdb_id
        return tvdb or imdb

    def match(self, release: indexers.Release) -> int | None:
        """The one series the release belongs to, or None."""
        if not self.infos:
            return None
        if release.imdb_id is not None and release.imdb_id in self.library.movie_imdb:
            return None
        for table, number in ((self.by_tvdb, release.tvdb_id), (self.by_imdb, release.imdb_id)):
            if number is None:
                continue
            found = set(table.get(number, ()))
            if found:
                found = {title_id for title_id in found if not self._numbers_differ(title_id, release)}
                return next(iter(found)) if len(found) == 1 else None
        known = self.library.series_tvdb if release.tvdb_id is not None else frozenset()
        if release.tvdb_id is not None and release.tvdb_id in known:
            # A series of the library that wants nothing.
            return None
        if release.imdb_id is not None and release.imdb_id in self.library.series_imdb:
            return None
        read = releases.parse_series(release.title).series
        named = {title_id for key in read.title_keys for title_id in self.by_key.get(key, ())}
        found = {
            title_id
            for title_id in named
            if not self._numbers_differ(title_id, release)
            and (
                read.year is None
                or self.infos[title_id].year is None
                or abs(read.year - (self.infos[title_id].year or 0)) <= YEAR_TOLERANCE
            )
        }
        return next(iter(found)) if len(found) == 1 else None

    def match_all(self, found: dict[str, indexers.Release]) -> dict[int, dict[str, indexers.Release]]:
        matched: dict[int, dict[str, indexers.Release]] = {}
        for key, release in found.items():
            title_id = self.match(release)
            if title_id is not None:
                matched.setdefault(title_id, {})[key] = release
        return dict(sorted(matched.items()))


def wanted_series(now: datetime, library: Numbers | None = None) -> SeriesLookups:
    """The lookups over every series with an episode that wants something, aired or not."""
    lookups = SeriesLookups(library=library or Numbers())
    with SessionLocal() as db:
        candidates = list(
            db.scalars(
                select(Version.title_id)
                .join(Title, Title.id == Version.title_id)
                .where(Title.kind == "series", Version.source_id.is_(None))
                .distinct()
                .order_by(Version.title_id)
            )
        )
        for start in range(0, len(candidates), FACTS_CHUNK):
            chunk = candidates[start : start + FACTS_CHUNK]
            facts = series_planning.load_facts(db, chunk, now)
            wanting: dict[int, set[int]] = {}
            for title_id, fact in facts.items():
                if anime.not_searched("series", fact.series_type):
                    continue
                episodes = {
                    episode.episode_id
                    for version in fact.versions
                    for episode in version.episodes
                    if version.wants(episode)
                }
                if episodes:
                    wanting[title_id] = episodes
            if not wanting:
                continue
            keys: dict[int, set[str]] = {title_id: set() for title_id in wanting}
            for title_id, stored in db.execute(
                select(TitleAlias.title_id, TitleAlias.search_keys).where(TitleAlias.title_id.in_(list(wanting)))
            ).tuples():
                keys[title_id].update(item for item in (stored or "").split(schreibweisen.SEPARATOR) if item)
            titles = list(db.scalars(select(Title).where(Title.id.in_(list(wanting))).order_by(Title.id)))
            tvdb_ids = [title.tvdb_id for title in titles if title.tvdb_id is not None]
            xem_keys: dict[int, set[str]] = {}
            if tvdb_ids:
                for tvdb_id, stored in db.execute(
                    select(XemName.tvdb_id, XemName.search_keys).where(XemName.tvdb_id.in_(tvdb_ids))
                ).tuples():
                    xem_keys.setdefault(tvdb_id, set()).update(
                        item for item in (stored or "").split(schreibweisen.SEPARATOR) if item
                    )
            for title in titles:
                found = keys[title.id] | xem_keys.get(title.tvdb_id or 0, set())
                for text in (title.title, title.title_en, title.original_title):
                    found.update(schreibweisen.keys(text))
                for stored in (title.search_keys, title.tmdb_search_keys):
                    found.update(item for item in (stored or "").split(schreibweisen.SEPARATOR) if item)
                lookups.add(
                    SeriesInfo(
                        title_id=title.id,
                        year=title.year,
                        tvdb_id=title.tvdb_id,
                        imdb_id=imdb_number(title.imdb_id),
                        keys=frozenset(found),
                    ),
                    wanting[title.id],
                )
    return lookups


# --- Judging and loading -------------------------------------------------------------------------------------------- #


def judge_and_load(sync: Sync, title_id: int, found: dict[str, indexers.Release], now: datetime) -> int:
    """One search of the automatic lane made from a title's matched releases, its load decision, and the summary when
    something loaded. Returns how many versions loaded."""
    fetched = sync.state
    state = search_runner.IndexerState(
        info=fetched.info, state=fetched.state, error_code=fetched.error_code, releases=dict(found)
    )
    search = search_jobs.keep_found(title_id, origin=ORIGIN, states=[state])
    if search is None:
        return 0
    try:
        body = search_jobs.snapshot(search.search_id)
        if body is None:
            return 0
        loading.decorate(body, search_jobs.info_hashes(search.search_id))
        # The scheduler's load step: what a planned search would take, never with a confirmation.
        outcomes = scheduler._load(search, body, now)
        loaded = sum(1 for done, _code in outcomes.values() if done)
        if loaded:
            written = scheduler.summary(search, body, outcomes, now)
            with SessionLocal() as db:
                title = db.get(Title, title_id)
                if title is not None:
                    title.search_summary = written
                    db.commit()
        return loaded
    finally:
        search_jobs.drop(search.search_id)


def judge_kept(
    kind: str,
    title_id: int,
    states: list[search_runner.IndexerState],
    episodes: Collection[int],
    origin: str,
    now: datetime,
) -> tuple[int, set[tuple[int, str]]]:
    """Releases that waited out a delay (``waiting``), judged and loaded as RSS judges fetched releases, without a
    request. Returns how many loads started, and version and release key of what a version may still take afterwards:
    the rest has stopped fitting and goes."""
    from . import album_loading

    if kind == "series":
        search = search_jobs.keep_found_series(title_id, origin=origin, states=states, episode_ids=episodes)
    elif kind == "album":
        search = search_jobs.keep_found_album(title_id, origin=origin, states=states)
    else:
        search = search_jobs.keep_found(title_id, origin=origin, states=states)
    if search is None:
        return 0, set()
    try:
        body = search_jobs.snapshot(search.search_id)
        if body is None:
            return 0, set()
        if kind == "series":
            loading.decorate_series(body, search_jobs.info_hashes(search.search_id))
            outcomes, body = series_loading.load(search, body, now)
            loaded = sum(len(outcome.loaded) for outcome in outcomes.values())
            still = {
                (int(entry["version_id"]), str(take["release_key"]))
                for entry in body.get("versions") or []
                for take in entry.get("takes") or []
            }
        elif kind == "album":
            album_loading.prepare(body, search.search_id, title_id)
            done, _code = album_loading.load_found(search, body, now)
            loaded = 1 if done else 0
            decision = next(iter(body.get("versions") or []), None)
            version_id = int(decision["version_id"]) if decision and decision.get("version_id") is not None else 0
            still = {(version_id, release["release_key"]) for release in album_loading.candidates(body)}
        else:
            loading.decorate(body, search_jobs.info_hashes(search.search_id))
            outcomes_movie = scheduler._load(search, body, now)
            loaded = sum(1 for done, _code in outcomes_movie.values() if done)
            still = scheduler.candidate_keys(title_id, body, now)
        return loaded, still
    finally:
        search_jobs.drop(search.search_id)


class AlbumLookups:
    """The albums that want something, found by the artist's name in a release's name, then matched exactly with the
    album search's matching (decision 13): only ``this`` album counts, never another album of
    the artist, a sampler or another artist."""

    def __init__(self) -> None:
        self.albums: dict[int, album_search.AlbumSearch] = {}
        self._by_artist: dict[str, list[int]] = {}

    def add(self, album: album_search.AlbumSearch) -> None:
        if album.artist.various:
            # A sampler is never taken from RSS (decision 13).
            return
        self.albums[album.title_id] = album
        for name in album.artist.names:
            for key in schreibweisen.keys(name):
                self._by_artist.setdefault(key, []).append(album.title_id)

    def match(self, release: indexers.Release) -> int | None:
        forms = schreibweisen.keys(release.title)
        if not forms:
            return None
        padded = [f" {form} " for form in forms]
        candidates = sorted(
            {
                title_id
                for key, title_ids in self._by_artist.items()
                if any(f" {key} " in form for form in padded)
                for title_id in title_ids
            }
        )
        if not candidates:
            return None
        parsed = parse_album(release.title)
        for title_id in candidates:
            album = self.albums[title_id]
            hit = hit_matching.match(parsed, album.artist, album.albums, album.title_id)
            if hit.kind == hit_matching.THIS and hit.album is not None and hit.album.title_id == title_id:
                return title_id
        return None

    def match_all(self, releases: dict[str, indexers.Release]) -> dict[int, dict[str, indexers.Release]]:
        found: dict[int, dict[str, indexers.Release]] = {}
        if not self.albums:
            return found
        for key, release in releases.items():
            title_id = self.match(release)
            if title_id is not None:
                found.setdefault(title_id, {})[key] = release
        return found


def wanted_albums(now: datetime) -> AlbumLookups:
    """The lookups over every album whose own version wants something (Music M5)."""
    from . import album_planning

    lookups = AlbumLookups()
    with SessionLocal() as db:
        candidates = list(
            db.scalars(
                select(Version.title_id)
                .join(Title, Title.id == Version.title_id)
                .where(Title.kind == "album", Version.source_id.is_(None), Version.monitored.is_(True))
                .distinct()
                .order_by(Version.title_id)
            )
        )
        for start in range(0, len(candidates), FACTS_CHUNK):
            chunk = candidates[start : start + FACTS_CHUNK]
            facts = album_planning.load_facts(db, chunk, now)
            for title_id in chunk:
                fact = facts.get(title_id)
                if fact is None or not any(version.wants for version in fact.versions):
                    continue
                album = album_search.load(db, title_id)
                if album is not None:
                    lookups.add(album)
    return lookups


def judge_and_load_album(sync: Sync, title_id: int, found: dict[str, indexers.Release], now: datetime) -> int:
    """An album search of the automatic lane made from an album's matched releases, loaded as a planned search loads,
    and the summary when it loaded. Returns 1 when it loaded, else 0."""
    from . import album_loading

    fetched = sync.state
    state = search_runner.IndexerState(
        info=fetched.info, state=fetched.state, error_code=fetched.error_code, releases=dict(found)
    )
    search = search_jobs.keep_found_album(title_id, origin=ORIGIN, states=[state])
    if search is None:
        return 0
    try:
        body = search_jobs.snapshot(search.search_id)
        if body is None:
            return 0
        album_loading.prepare(body, search.search_id, title_id)
        loaded, code = album_loading.load_found(search, body, now)
        if loaded:
            written = album_loading.summary(search, body, loaded, code, now)
            with SessionLocal() as db:
                title = db.get(Title, title_id)
                if title is not None:
                    title.search_summary = written
                    db.commit()
        return 1 if loaded else 0
    finally:
        search_jobs.drop(search.search_id)


def judge_and_load_series(
    sync: Sync, title_id: int, found: dict[str, indexers.Release], episodes: Collection[int], now: datetime
) -> int:
    """A series search of the automatic lane made from a series' matched releases for its wanting episodes, loaded as a
    planned search loads, and the summary of the seasons that loaded. Returns how many releases loaded."""
    fetched = sync.state
    state = search_runner.IndexerState(
        info=fetched.info, state=fetched.state, error_code=fetched.error_code, releases=dict(found)
    )
    search = search_jobs.keep_found_series(title_id, origin=ORIGIN, states=[state], episode_ids=episodes)
    if search is None:
        return 0
    try:
        body = search_jobs.snapshot(search.search_id)
        if body is None:
            return 0
        loading.decorate_series(body, search_jobs.info_hashes(search.search_id))
        outcomes, body = series_loading.load(search, body, now)
        loaded = sum(len(outcome.loaded) for outcome in outcomes.values())
        if loaded:
            seasons = series_loading.loaded_seasons(outcomes)
            with SessionLocal() as db:
                title = db.get(Title, title_id)
                if title is not None:
                    title.search_summary = series_loading.summary(
                        search, body, outcomes, now, seasons, title.search_summary
                    )
                    db.flush()
                    planning.replan(db, [title_id], now)
                    db.commit()
        return loaded
    finally:
        search_jobs.drop(search.search_id)
