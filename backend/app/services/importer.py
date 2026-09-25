"""Import one source: read Radarr, merge into the library, compute the states.

One run per source at a time. ``start`` answers at once and runs the import in a thread of its
own; the database layer is synchronous, and the thread keeps the event loop free. The
background job ``source_import`` imports every source every 15 minutes and skips a source whose
import is still running.

A run, in order:

1. Fetch status, quality profiles, root folders, movies and queue. Any error ends the run
   before the library is touched: a Sonarr answering at the address must not empty it.
2. Upsert titles by TMDB id and the versions of the source's version definition. A version the
   owner added in that definition, which no source feeds, is taken over: it gets the source and
   its data, keeps ``added_by = owner`` and its history. Its ``release.nex`` entry goes after the
   run's commit, while the file is unchanged (L2); the file stays.
3. For this source's versions whose movie is gone: an owner's version stays, loses the source and
   is wanted again; an imported version is removed. Then every title left without versions goes.
4. Compute the state of each version (precedence: problem, downloading, upgrade, available,
   wanted, unmonitored).
5. Write history: ``added`` when a version first appears, ``imported`` when a file appears.
6. Record the run with its counts, or with the error code that ended it.

Log lines carry ids and counts only, never titles, names or addresses.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import logging
import secrets
import threading
import time
from collections import defaultdict
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import case, delete, exists, func, select, update
from sqlalchemy.orm import Session as OrmSession

from .. import crypto
from ..db import SessionLocal
from ..models import AlternateTitle, HistoryEntry, ImportRun, Source, Title, Version, VersionDefinition, utcnow
from . import companions, images, logs, media, tags
from .radarr import (
    MediaManagement,
    Movie,
    NamingConfig,
    QualityProfile,
    QueueItem,
    RadarrClient,
    RadarrError,
    RootFolder,
    SourceUrlInvalid,
    SystemStatus,
    last_write,
)
from .schreibweisen import search_text, sort_key

logger = logging.getLogger("nexcrate.import")

JOB_NAME = "source_import"
INTERVAL_SECONDS = 15 * 60
#: Import runs kept per source. Every 15 minutes are 96 a day.
RUNS_KEPT = 100
#: ⚠️ A Lidarr connection is read in full, track by track: 277 s for 314 artists on the owner's instance (measured
#: 18.09.2026), a third of every quarter of an hour. The schedule reads it once an hour after a good run; a failed
#: run is tried again with the next tick, and the owner's own "import now" is never held back.
SCHEDULED_EVERY = {"lidarr": timedelta(hours=1)}
#: The job's ticks are not exact: a run that started 59 minutes and 58 seconds ago counts as an hour ago.
SCHEDULE_SLACK = timedelta(minutes=1)
#: SQLite takes at most this many values in one IN list, with room to spare.
_CHUNK = 500

#: Problem codes, most severe first. Radarr's own sentences are never stored: they come
#: translated and can contain paths.
PROBLEM_CODES = ("import_blocked", "download_error", "import_pending", "download_warning")
#: Queue statuses that mean "on its way" (the download client's view).
DOWNLOADING_STATUSES = frozenset({"downloading", "queued", "paused"})
#: Tracked download states that mean "on its way" (Radarr's view).
DOWNLOADING_STATES = frozenset({"downloading", "importing"})


class ImportRunning(Exception):
    """An import of this source is already running."""


class SourceMissing(Exception):
    """The source does not exist."""


class SourceTakenOver(Exception):
    """The source was taken over; nexcrate never reads it again."""


# --- States -------------------------------------------------------------------- #


@dataclass(frozen=True)
class StateResult:
    state: str
    progress: float | None = None
    problem_code: str | None = None


def problem_codes(item: QueueItem) -> list[str]:
    """What is stuck about a queue item: import blocked or pending, or a warning or error."""
    state = (item.tracked_download_state or "").casefold()
    status = (item.tracked_download_status or "").casefold()
    codes: list[str] = []
    if state == "importblocked":
        codes.append("import_blocked")
    if state == "importpending":
        codes.append("import_pending")
    if status == "error":
        codes.append("download_error")
    if status == "warning":
        codes.append("download_warning")
    return codes


def is_downloading(item: QueueItem) -> bool:
    status = (item.status or "").casefold()
    state = (item.tracked_download_state or "").casefold()
    return status in DOWNLOADING_STATUSES or state in DOWNLOADING_STATES


def progress_of(item: QueueItem) -> float | None:
    """``1 - sizeleft / size`` in percent, one decimal. None without a size."""
    if item.size is None or item.size <= 0:
        return None
    left = item.sizeleft if item.sizeleft is not None else item.size
    return round(min(100.0, max(0.0, (1 - left / item.size) * 100)), 1)


def compute_state(*, monitored: bool, has_file: bool, cutoff_not_met: bool, queue: list[QueueItem]) -> StateResult:
    """The state of a version. The first rule that applies wins."""
    codes = [code for item in queue for code in problem_codes(item)]
    if codes:
        return StateResult("problem", None, min(codes, key=PROBLEM_CODES.index))
    downloading = [item for item in queue if is_downloading(item)]
    if downloading:
        progresses = [value for value in map(progress_of, downloading) if value is not None]
        return StateResult("downloading", max(progresses) if progresses else None)
    if has_file:
        return StateResult("upgrade" if cutoff_not_met else "available")
    return StateResult("wanted" if monitored else "unmonitored")


# --- Running control --------------------------------------------------------------- #

_lock = threading.Lock()
_running: set[int] = set()
_threads: list[threading.Thread] = []
#: One heavy writer at a time. An import and a takeover job both write for minutes; SQLite has a single writer, and
#: two of them at once made the loser give up with "database is locked" after its wait (the owner's instance,
#: 20.09.2026: four takeovers undone one after another, their imports and the background jobs all writing). Waiting
#: here costs a run its start, a lost race costs the whole run.
_heavy = threading.Lock()


@contextmanager
def heavy_writer(what: str) -> Iterator[None]:
    """Let only one import or takeover job write at a time; the others wait. Never held by a request."""
    if _heavy.acquire(blocking=False):
        waited = 0.0
    else:
        logger.info("%s waits for the import or takeover that is writing", what)
        started = time.monotonic()
        _heavy.acquire()
        waited = time.monotonic() - started
        logger.info("%s waited %.1f s for the one before it", what, waited)
    try:
        yield
    finally:
        _heavy.release()


def is_running(source_id: int) -> bool:
    with _lock:
        return source_id in _running


def _release(source_id: int) -> None:
    with _lock:
        _running.discard(source_id)


def release(source_id: int) -> None:
    """Give up the claim of ``begin``. For a takeover, which holds the claim longer than its import."""
    _release(source_id)


def claim(source_id: int) -> None:
    """Claim the source without a run, as undoing a takeover does before Radarr answered. Raises ``ImportRunning``.

    ``start(source_id, claimed=True)`` hands the claim to the import; ``release`` gives it up.
    """
    with _lock:
        if source_id in _running:
            raise ImportRunning(source_id)
        _running.add(source_id)


def begin(source_id: int, *, claimed: bool = False) -> ImportRun:
    """Claim the source and record a running import. With ``claimed`` the caller holds the claim already.

    Raises ``ImportRunning``, ``SourceMissing`` or ``SourceTakenOver``; on a failure the claim is given up.
    """
    if not claimed:
        claim(source_id)
    try:
        with SessionLocal() as db:
            source = db.get(Source, source_id)
            if source is None:
                raise SourceMissing(source_id)
            if source.taken_over_at is not None:
                raise SourceTakenOver(source_id)
            run = ImportRun(source_id=source_id, status="running", started_at=utcnow())
            db.add(run)
            db.commit()
            return run
    except BaseException:
        _release(source_id)
        raise


def start(source_id: int, *, claimed: bool = False) -> ImportRun:
    """Begin an import and run it in a thread of its own. Answers at once. ``claimed``: see ``begin``."""
    run = begin(source_id, claimed=claimed)
    # The thread keeps the request id of the request that started it, so one search finds every line.
    context = contextvars.copy_context()
    thread = threading.Thread(
        target=context.run, args=(execute, source_id, run.id), name=f"import-source-{source_id}", daemon=True
    )
    with _lock:
        _threads[:] = [existing for existing in _threads if existing.is_alive()]
        _threads.append(thread)
    try:
        thread.start()
    except BaseException:
        _finish_failed(run.id, "import_failed")
        _release(source_id)
        raise
    return run


def wait_idle(timeout: float = 30.0) -> bool:
    """Wait for the imports started with ``start``. True when none is left running."""
    deadline = time.monotonic() + timeout
    with _lock:
        threads = list(_threads)
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    with _lock:
        _threads[:] = [thread for thread in _threads if thread.is_alive()]
        return not _threads


def _is_due(db: OrmSession, source_id: int, app: str) -> bool:
    """Whether the schedule reads this source now (``SCHEDULED_EVERY``)."""
    every = SCHEDULED_EVERY.get(app)
    if every is None:
        return True
    last_good = db.scalar(
        select(func.max(ImportRun.started_at)).where(ImportRun.source_id == source_id, ImportRun.status == "done")
    )
    return last_good is None or utcnow() - last_good >= every - SCHEDULE_SLACK


def import_all_sources() -> int:
    """The background job: import every source in turn. Returns how many runs it made."""
    token = logs.bind_request(secrets.token_hex(4))
    try:
        with SessionLocal() as db:
            # ⚠️ A taken-over source is never read again; ``begin`` refuses it as well.
            rows = db.execute(select(Source.id, Source.app).where(Source.taken_over_at.is_(None)).order_by(Source.id))
            source_ids = [source_id for source_id, app in rows.tuples().all() if _is_due(db, source_id, app)]
        made = 0
        for source_id in source_ids:
            try:
                run = begin(source_id)
            except ImportRunning:
                logger.info("Scheduled import of source %d skipped, an import is already running", source_id)
                continue
            except SourceMissing, SourceTakenOver:
                continue
            made += 1
            execute(source_id, run.id, scheduled=True)
        return made
    finally:
        logs.unbind_request(token)


def mark_interrupted() -> int:
    """Runs still marked as running after a restart never finish. Called at start."""
    with _lock:
        busy = set(_running)
    statement = update(ImportRun).where(ImportRun.status == "running")
    if busy:
        statement = statement.where(ImportRun.source_id.not_in(busy))
    with SessionLocal() as db:
        result = db.execute(statement.values(status="failed", error_code="import_interrupted", finished_at=utcnow()))
        db.commit()
    count = int(getattr(result, "rowcount", 0) or 0)
    if count:
        logger.warning("%d import runs were interrupted by a restart and are marked as failed", count)
    return count


# --- One run ------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Fetched:
    status: SystemStatus
    profiles: list[QualityProfile]
    root_folders: list[RootFolder]
    movies: list[Movie]
    skipped: int
    queue: list[QueueItem]
    #: Tag id to name; None when Radarr did not answer for them: then the tags of this run stay as they are.
    tags: dict[int, str] | None = None


async def _fetch_from(radarr: RadarrClient) -> Fetched:
    status = await radarr.system_status()
    profiles = await radarr.quality_profiles()
    root_folders = await radarr.root_folders()
    movies, skipped = await radarr.movies()
    queue = await radarr.queue()
    try:
        names: dict[int, str] | None = await radarr.tags()
    except RadarrError:
        names = None
    return Fetched(status, profiles, root_folders, movies, skipped, queue, names)


async def fetch(url: str, api_key: str) -> Fetched:
    """Everything a run needs from Radarr. The status comes first: it proves the app is Radarr."""
    async with RadarrClient(url, api_key) as radarr:
        return await _fetch_from(radarr)


async def fetch_for_takeover(
    url: str, api_key: str
) -> tuple[Fetched, NamingConfig | None, str | None, MediaManagement | None]:
    """What ``fetch`` reads, then Radarr's naming and media management.

    A failure of either of the two gives no value instead; the code of a failed naming is kept.
    """
    naming: NamingConfig | None = None
    naming_error: str | None = None
    media: MediaManagement | None = None
    async with RadarrClient(url, api_key) as radarr:
        fetched = await _fetch_from(radarr)
        try:
            naming = await radarr.naming()
        except RadarrError as exc:
            naming_error = exc.code
        try:
            media = await radarr.media_management()
        except RadarrError:
            media = None
    return fetched, naming, naming_error, media


@dataclass(frozen=True)
class Read:
    """One read of Radarr for a begun run: the data, or the code that ended it (the run is marked failed already)."""

    fetched: Fetched | None
    error_code: str | None = None
    error_values: dict[str, object] = field(default_factory=dict)
    #: Only for a takeover: Radarr's naming or the code of its failure, and its media management.
    naming: NamingConfig | None = None
    naming_error: str | None = None
    media: MediaManagement | None = None


def execute(source_id: int, run_id: int, *, scheduled: bool = False) -> None:
    """Run a begun import to its end. Always releases the source. ``scheduled``: the job's run, not the owner's; a
    Lidarr connection then reads only what changed (``lidarr_import.run``).

    Waits for an import or takeover that is still writing (``heavy_writer``)."""
    try:
        with heavy_writer(f"The import of source {source_id}"):
            _execute(source_id, run_id, scheduled=scheduled)
    except Exception:
        logger.exception("Import of source %d failed unexpectedly", source_id)
        _finish_failed(run_id, "import_failed")
    finally:
        _release(source_id)


def _execute(source_id: int, run_id: int, *, scheduled: bool = False) -> None:
    started = time.perf_counter()
    with SessionLocal() as db:
        app = db.scalar(select(Source.app).where(Source.id == source_id))
    if app == "sonarr":
        # A Sonarr connection reads series (S1.7). Imported here: it builds on this module.
        from .series import sonarr_import

        sonarr_import.run(source_id, run_id)
        return
    if app == "lidarr":
        # A Lidarr connection reads music (M1.6). Imported here for the same reason.
        from .music import lidarr_import

        lidarr_import.run(source_id, run_id, scheduled=scheduled)
        return
    read = read_source(source_id, run_id)
    if read.fetched is not None:
        save_read(source_id, run_id, read.fetched, started)


def read_source(source_id: int, run_id: int, *, for_takeover: bool = False) -> Read:
    """Read Radarr for a begun run. A failure is recorded on the run and comes back with its code."""
    with SessionLocal() as db:
        source = db.get(Source, source_id)
        if source is None:
            logger.info("Import of source %d stopped, the source was deleted", source_id)
            return Read(None, "not_found")
        url, stored_key = source.url, source.api_key
    logger.info("Import of source %d started", source_id)

    api_key = crypto.decrypt(stored_key)
    if not api_key:
        logger.warning("Import of source %d failed, the stored API key cannot be read", source_id)
        _finish_failed(run_id, "source_key_missing")
        return Read(None, "source_key_missing")
    naming: NamingConfig | None = None
    naming_error: str | None = None
    media: MediaManagement | None = None
    try:
        if for_takeover:
            fetched, naming, naming_error, media = asyncio.run(fetch_for_takeover(url, api_key))
        else:
            fetched = asyncio.run(fetch(url, api_key))
    except RadarrError as exc:
        logger.warning("Import of source %d failed: %s", source_id, exc.code)
        values = {key: value for key, value in exc.detail.items() if key not in ("code", "message")}
        _finish_failed(run_id, exc.code, values)
        return Read(None, exc.code, values)
    except SourceUrlInvalid:
        logger.warning("Import of source %d failed, the stored address is not valid", source_id)
        _finish_failed(run_id, "source_url_invalid")
        return Read(None, "source_url_invalid")
    if fetched.skipped:
        logger.warning("Source %d: %d movies without a TMDB id were skipped", source_id, fetched.skipped)
    if naming_error:
        logger.info("Source %d: reading the naming failed: %s", source_id, naming_error)
    return Read(fetched, naming=naming, naming_error=naming_error, media=media)


def save_read(source_id: int, run_id: int, fetched: Fetched, started: float | None = None) -> Outcome | None:
    """Merge a read into the library and finish the run as done. None when the source or the run is gone."""
    started = time.perf_counter() if started is None else started
    with SessionLocal() as db:
        # A write first: it takes SQLite's write lock before anything is read. A transaction that
        # reads first and writes later fails outright when another write came in between.
        db.execute(update(ImportRun).where(ImportRun.id == run_id).values(status="running"))
        source = db.get(Source, source_id)
        run = db.get(ImportRun, run_id)
        if source is None or run is None or source.taken_over_at is not None:
            db.rollback()
            logger.info("Import of source %d stopped, the source was deleted or taken over", source_id)
            return None
        outcome = apply(db, source, fetched)
        run.status = "done"
        run.finished_at = utcnow()
        run.titles_new = outcome.titles_new
        run.titles_updated = outcome.titles_updated
        run.versions_total = outcome.versions_total
        run.versions_removed = outcome.versions_removed
        run.error_code = None
        db.commit()

    if outcome.companion_removals:
        # After the commit: the versions belong to Radarr now, and a file claiming nexcrate's ownership would mislead.
        companions.remove(outcome.companion_removals)
    images.forget(outcome.removed_title_ids)
    prune_runs(source_id)
    logger.info(
        "Import of source %d done in %dms: %d titles new, %d updated, %d versions, %d versions removed, "
        "%d titles removed, %d queue items",
        source_id,
        (time.perf_counter() - started) * 1000,
        outcome.titles_new,
        outcome.titles_updated,
        outcome.versions_total,
        outcome.versions_removed,
        len(outcome.removed_title_ids),
        len(fetched.queue),
    )
    return outcome


def _finish_failed(run_id: int, code: str, values: dict[str, object] | None = None) -> None:
    with SessionLocal() as db:
        db.execute(
            update(ImportRun)
            .where(ImportRun.id == run_id)
            .values(status="failed", error_code=code, error_values=values or None, finished_at=utcnow())
        )
        db.commit()


def fail_running_run(run_id: int, code: str) -> None:
    """Mark a run failed when it still runs: for a takeover that ended before its read finished the run."""
    with SessionLocal() as db:
        db.execute(
            update(ImportRun)
            .where(ImportRun.id == run_id, ImportRun.status == "running")
            .values(status="failed", error_code=code, finished_at=utcnow())
        )
        db.commit()


def prune_runs(source_id: int, keep: int | None = None) -> None:
    """Keep the newest ``RUNS_KEPT`` runs of a source."""
    kept = RUNS_KEPT if keep is None else keep
    with SessionLocal() as db:
        old = select(ImportRun.id).where(ImportRun.source_id == source_id).order_by(ImportRun.id.desc()).offset(kept)
        db.execute(delete(ImportRun).where(ImportRun.id.in_(old)))
        db.commit()


# --- Merging ---------------------------------------------------------------------------- #


@dataclass
class Outcome:
    titles_new: int = 0
    titles_updated: int = 0
    versions_total: int = 0
    versions_removed: int = 0
    removed_title_ids: list[int] = field(default_factory=list)
    #: The ``release.nex`` entries of owner's versions this run took over, removed after the commit.
    companion_removals: list[companions.Removal] = field(default_factory=list)


def _upgrade_target(profile: QualityProfile) -> str | None:
    """The cutoff as the interface shows it: a group by its qualities, joined with ", ", never by its own name."""
    if len(profile.cutoff_qualities) > 1:
        return ", ".join(profile.cutoff_qualities)[:200]
    return profile.cutoff_name[:200] if profile.cutoff_name else None


def apply(db: OrmSession, source: Source, fetched: Fetched, now: datetime | None = None) -> Outcome:
    """Merge what one source delivered into the library. The caller commits."""
    now = now or utcnow()
    definition = db.get(VersionDefinition, source.version_id)
    if definition is None:
        raise RuntimeError("the version definition of the source is missing")
    profiles = {profile.id: profile for profile in fetched.profiles}
    roots = sorted((folder.path for folder in fetched.root_folders), key=len, reverse=True)
    queue_by_movie: dict[int, list[QueueItem]] = defaultdict(list)
    for item in fetched.queue:
        if item.movie_id is not None:
            queue_by_movie[item.movie_id].append(item)

    titles = {title.tmdb_id: title for title in db.scalars(select(Title).where(Title.kind == "movie"))}
    versions = {
        version.title_id: version for version in db.scalars(select(Version).where(Version.source_id == source.id))
    }
    # The owner's versions in this definition that no source feeds: the import takes them over.
    unfed = {
        version.title_id: version
        for version in db.scalars(
            select(Version).where(Version.version_definition_id == definition.id, Version.source_id.is_(None))
        )
    }
    # A poster of a taken-over source gives way to the poster of a source nexcrate still reads.
    taken_over = set(db.scalars(select(Source.id).where(Source.taken_over_at.is_not(None))))
    alternates: dict[int, dict[str, AlternateTitle]] = defaultdict(dict)
    for row in db.scalars(select(AlternateTitle).where(AlternateTitle.source_id == source.id)):
        if row.text in alternates[row.title_id]:
            db.delete(row)
        else:
            alternates[row.title_id][row.text] = row

    outcome = Outcome()
    companions_on = companions.enabled(db)
    seen_tmdb: set[int] = set()
    tag_names: dict[int, list[str]] = {}
    for movie in fetched.movies:
        if movie.tmdb_id in seen_tmdb:
            continue
        seen_tmdb.add(movie.tmdb_id)
        title = titles.get(movie.tmdb_id)
        is_new = title is None
        if title is None:
            title = Title(
                kind="movie", tmdb_id=movie.tmdb_id, title=movie.title, added=movie.added or now, updated_at=now
            )
            db.add(title)
            db.flush()
            titles[movie.tmdb_id] = title
            outcome.titles_new += 1
        changed = _merge_title(title, movie, source.id, taken_over)
        if fetched.tags is not None:
            tag_names[title.id] = [fetched.tags[tag_id] for tag_id in movie.tags if tag_id in fetched.tags]
        changed = _merge_alternates(db, title, movie, source.id, alternates.pop(title.id, {})) or changed
        version = versions.pop(title.id, None)
        if version is None:
            version = unfed.pop(title.id, None)
            if version is not None:
                # An owner's version with a file of nexcrate's own: its release.nex entry goes with the file fields,
                # collected before ``_merge_version`` overwrites them (L2).
                if companions_on and companions.owned(version):
                    outcome.companion_removals.extend(companions.plan_removal(db, [version]))
                    companions.clear_state(version)
                version.source_id = source.id
        changed = (
            _merge_version(
                db, title, movie, source, definition, version, profiles, roots, queue_by_movie[movie.id], now
            )
            or changed
        )
        outcome.versions_total += 1
        if changed and not is_new:
            title.updated_at = now
            outcome.titles_updated += 1

    for version in versions.values():
        if version.added_by == "owner":
            detach(version, now)
        else:
            db.delete(version)
            outcome.versions_removed += 1
    for rows in alternates.values():
        for row in rows.values():
            db.delete(row)
    db.flush()
    if fetched.tags is not None:
        # Radarr's tags of its movies, mirrored; the owner's own tags stay.
        tags.sync_titles(db, source.id, tag_names)

    # A source that no longer has a title gives up its claims on it; the next import of another
    # source takes over.
    # ⚠️ A list, not an EXISTS per title: for the EXISTS SQLite took the index on ``source_id`` and walked every version
    # of the source once per title. On the owner's instance (4,000 movies) each of the two statements below took
    # 2.7 s, inside the write lock of every import (measured 18.09.2026).
    no_version_here = Title.id.not_in(select(Version.title_id).where(Version.source_id == source.id))
    db.execute(
        update(Title).where(Title.meta_source_id == source.id, no_version_here).values(meta_source_id=None),
        execution_options={"synchronize_session": False},
    )
    db.execute(
        update(Title).where(Title.poster_source_id == source.id, no_version_here).values(**poster_gone()),
        execution_options={"synchronize_session": False},
    )
    outcome.removed_title_ids = remove_orphans(db)
    return outcome


def poster_gone() -> dict[str, object]:
    """The poster columns when the poster source goes: the TMDB poster, if the title had one."""
    return {
        "poster_source_id": None,
        "poster_url": None,
        "poster_key": None,
        "poster_origin": case((Title.tmdb_poster_path.is_not(None), "tmdb"), else_=None),
    }


#: The columns a source fills in a version. Everything else belongs to the version itself.
FED_FIELDS = (
    "radarr_movie_id",
    "monitored",
    "has_file",
    "file_ref",
    "quality",
    "cutoff_not_met",
    "size",
    "languages",
    "release_group",
    "relative_path",
    "profile_name",
    "root_folder",
    "upgrade_to",
    "state",
    "progress",
    "problem_code",
    "source_movie_path",
    "release_title",
    "media_info",
    "minimum_availability",
)


def _unfed_values() -> dict[str, object]:
    return {
        "radarr_movie_id": None,
        "monitored": True,
        "has_file": False,
        "file_ref": None,
        "quality": None,
        "cutoff_not_met": False,
        "size": 0,
        "languages": [],
        "release_group": None,
        "relative_path": None,
        "profile_name": None,
        "root_folder": None,
        "upgrade_to": None,
        "state": "wanted",
        "progress": None,
        "problem_code": None,
        "source_movie_path": None,
        "release_title": None,
        "media_info": None,
        "minimum_availability": None,
    }


def detach(version: Version, moment: datetime) -> None:
    """An owner's version that no source feeds any more: without the source's data, wanted again."""
    version.source_id = None
    _set(version, _unfed_values())
    version.updated_at = moment


def release_source(db: OrmSession, source_id: int, moment: datetime) -> tuple[int, int]:
    """A source is deleted: its imported versions go, the owner's versions it fed stay. Returns both counts."""
    removed = detached = 0
    for version in list(db.scalars(select(Version).where(Version.source_id == source_id))):
        if version.added_by == "owner":
            detach(version, moment)
            detached += 1
        else:
            db.delete(version)
            removed += 1
    db.flush()
    return removed, detached


def move_source(db: OrmSession, source: Source, definition: VersionDefinition, moment: datetime) -> None:
    """A source feeds another version definition from now on.

    Its imported versions move along. An owner's version it fed stays in its own definition, no
    longer fed, and the source's data goes into the new slot. A slot the owner already filled in the
    new definition is taken over, as an import would do.
    """
    slots = {
        version.title_id: version
        for version in db.scalars(select(Version).where(Version.version_definition_id == definition.id))
    }
    for version in list(db.scalars(select(Version).where(Version.source_id == source.id))):
        target = slots.get(version.title_id)
        if version.added_by != "owner" and target is None:
            version.version_definition_id = definition.id
            version.updated_at = moment
            continue
        data = {name: getattr(version, name) for name in FED_FIELDS}
        data["languages"] = list(version.languages or [])
        if target is None:
            target = Version(
                title_id=version.title_id,
                version_definition_id=definition.id,
                added_by="import",
                created_at=moment,
                **data,
            )
            db.add(target)
        elif target.source_id is None:
            _set(target, data)
        else:
            # One definition is fed by one source; a slot fed by another cannot exist.
            continue
        target.source_id = source.id
        target.updated_at = moment
        if version.added_by == "owner":
            detach(version, moment)
        else:
            db.delete(version)
    db.flush()


def remove_orphans(db: OrmSession) -> list[int]:
    """Delete every movie or series without a version. Their alternate titles and history go with them.

    ⚠️ Albums stay: an artist's whole catalogue lives as titles without a version (decision 2).
    """
    orphan_ids = list(
        db.scalars(
            select(Title.id).where(
                Title.kind.in_(("movie", "series")), ~exists().where(Version.title_id == Title.id)
            )
        )
    )
    for index in range(0, len(orphan_ids), _CHUNK):
        chunk = orphan_ids[index : index + _CHUNK]
        db.execute(delete(Title).where(Title.id.in_(chunk)), execution_options={"synchronize_session": False})
    return orphan_ids


def count_versions(db: OrmSession, source_id: int) -> int:
    return int(db.scalar(select(func.count(Version.id)).where(Version.source_id == source_id)) or 0)


def _set(target: object, values: dict[str, object]) -> bool:
    changed = False
    for name, value in values.items():
        if getattr(target, name) != value:
            setattr(target, name, value)
            changed = True
    return changed


def _merge_title(title: Title, movie: Movie, source_id: int, taken_over: set[int] | None = None) -> bool:
    changed = False
    if title.meta_source_id is None:
        title.meta_source_id = source_id
    if title.meta_source_id == source_id:
        changed = _set(
            title,
            {
                "title": movie.title[:1024],
                "original_title": movie.original_title[:1024] if movie.original_title else None,
                "imdb_id": movie.imdb_id[:32] if movie.imdb_id else None,
                "year": movie.year,
                "runtime": movie.runtime,
                "genres": list(movie.genres),
                "overview": movie.overview,
                # Every run, existing titles included: the release checker needs it for German DL.
                "original_language": movie.original_language,
                # For auto tags; what Radarr does not send stays as it is.
                **({"studios": [movie.studio]} if movie.studio else {}),
                **({"keywords": list(movie.keywords)} if movie.keywords is not None else {}),
            },
        )
        changed = (
            _set(
                title,
                {
                    "sort_key": sort_key(title.title)[:1024],
                    "search_keys": search_text([title.title, title.original_title]),
                },
            )
            or changed
        )
    if movie.added is not None and movie.added < title.added:
        title.added = movie.added
        changed = True

    usable = bool(movie.poster_url) and len(movie.poster_url or "") <= 1024
    replaces_taken = usable and title.poster_source_id is not None and title.poster_source_id in (taken_over or set())
    if title.poster_source_id in (None, source_id) or replaces_taken:
        if movie.poster_url and usable:
            key = last_write(movie.poster_url) or hashlib.sha256(movie.poster_url.encode("utf-8")).hexdigest()[:16]
            poster: dict[str, object] = {
                "poster_source_id": source_id,
                "poster_url": movie.poster_url,
                "poster_key": key,
                "poster_origin": "source",
            }
        else:
            poster = {
                "poster_source_id": None,
                "poster_url": None,
                "poster_key": None,
                "poster_origin": "tmdb" if title.tmdb_poster_path else None,
            }
        changed = _set(title, poster) or changed
    return changed


def _merge_alternates(
    db: OrmSession, title: Title, movie: Movie, source_id: int, existing: dict[str, AlternateTitle]
) -> bool:
    wanted: list[str] = []
    names: Iterable[str | None] = movie.alternate_titles
    if title.meta_source_id != source_id:
        # A second source in another language: its own titles become searchable too.
        names = [*movie.alternate_titles, movie.title, movie.original_title]
    for name in names:
        if name and name[:1024] not in wanted:
            wanted.append(name[:1024])
    changed = False
    for text, row in existing.items():
        if text not in wanted:
            db.delete(row)
            changed = True
    for text in wanted:
        if text not in existing:
            db.add(AlternateTitle(title_id=title.id, source_id=source_id, text=text, search_keys=search_text([text])))
            changed = True
    return changed


def _root_of(path: str | None, roots: list[str]) -> str | None:
    if not path:
        return None
    for root in roots:
        trimmed = root.rstrip("/\\")
        if path == trimmed or path.startswith((trimmed + "/", trimmed + "\\")):
            return root
    return None


def _file_ref(movie: Movie) -> str | None:
    if movie.movie_file is not None and movie.movie_file.id:
        return str(movie.movie_file.id)
    if movie.movie_file_id:
        return str(movie.movie_file_id)
    return None


def _merge_version(
    db: OrmSession,
    title: Title,
    movie: Movie,
    source: Source,
    definition: VersionDefinition,
    version: Version | None,
    profiles: dict[int, QualityProfile],
    roots: list[str],
    queue: list[QueueItem],
    now: datetime,
) -> bool:
    file = movie.movie_file if movie.has_file else None
    profile = profiles.get(movie.quality_profile_id) if movie.quality_profile_id is not None else None
    cutoff_not_met = bool(file and file.cutoff_not_met)
    result = compute_state(
        monitored=movie.monitored, has_file=movie.has_file, cutoff_not_met=cutoff_not_met, queue=queue
    )
    values: dict[str, object] = {
        "radarr_movie_id": movie.id,
        "monitored": movie.monitored,
        "has_file": movie.has_file,
        "file_ref": _file_ref(movie) if movie.has_file else None,
        "quality": file.quality[:100] if file and file.quality else None,
        "cutoff_not_met": cutoff_not_met,
        "size": file.size if file else 0,
        "languages": list(file.languages) if file else [],
        "release_group": file.release_group[:200] if file and file.release_group else None,
        "relative_path": file.relative_path[:2048] if file and file.relative_path else None,
        "profile_name": profile.name[:200] if profile and profile.name else None,
        "root_folder": (movie.root_folder_path or _root_of(movie.path, roots) or "")[:1024] or None,
        "upgrade_to": _upgrade_target(profile) if cutoff_not_met and profile else None,
        "state": result.state,
        "progress": result.progress,
        "problem_code": result.problem_code,
        # Kept by every import: a takeover works from them when Radarr does not answer any more.
        "source_movie_path": (movie.path or "")[:4096] or None,
        "release_title": ((file.release_name or "")[:1024] or None) if file else None,
        "minimum_availability": (movie.minimum_availability or "")[:32] or None,
    }
    # Radarr's own media data, in nexcrate's shape (as Sonarr's for an episode file): the naming needs codec, HDR and
    # audio after the takeover, and nothing reads the file itself before then. Media data nexcrate measured itself is
    # better and stays; a version fed by Radarr has none unless a takeover was undone.
    taken_media = media.from_arr(file.media_info, "radarr") if file and file.media_info else None
    if version is None or not isinstance(version.media_info, dict) or version.media_info.get("tool") in media.ARR_TOOLS:
        values["media_info"] = taken_media
    if version is None:
        version = Version(
            title_id=title.id,
            version_definition_id=definition.id,
            source_id=source.id,
            created_at=now,
            updated_at=now,
            **values,
        )
        db.add(version)
        db.flush()
        _history(db, title, version, definition, "added", movie.added or now, None)
        if movie.has_file:
            _history(
                db, title, version, definition, "imported", (file.date_added if file else None) or now, version.quality
            )
        return True

    had_file, previous_ref = version.has_file, version.file_ref
    changed = _set(version, {"version_definition_id": definition.id, **values})
    new_ref = version.file_ref
    if movie.has_file and (
        not had_file or (new_ref is not None and previous_ref is not None and new_ref != previous_ref)
    ):
        _history(
            db, title, version, definition, "imported", (file.date_added if file else None) or now, version.quality
        )
    if changed:
        version.updated_at = now
    return changed


def _history(
    db: OrmSession,
    title: Title,
    version: Version,
    definition: VersionDefinition,
    event: str,
    at: datetime,
    detail: str | None,
) -> None:
    db.add(
        HistoryEntry(
            title_id=title.id,
            version_id=version.id,
            version_definition_id=definition.id,
            version_label=definition.label,
            event=event,
            at=at,
            detail=detail,
        )
    )
