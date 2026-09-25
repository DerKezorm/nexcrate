"""Taking over a Radarr connection: the check, the takeover, and the jobs that run them (T1).

* **Jobs** live in memory like searches: at most one running job per source, readable for 30 minutes after it ended,
  lost on a restart. A job holds the source's import claim (``importer.begin``) from its start to its end, so no
  scheduled import runs meanwhile; its read of Radarr is an import run of its own.
* **Reading:** Radarr is read and merged as an import does, with its naming and media management. When Radarr does not
  answer, the stored state serves, provided an import kept the movie folder of every version with a file (imports keep
  it since migration 5); otherwise the job ends with ``takeover_needs_import`` and Radarr's code as ``reason``. A source
  that was never imported ends with Radarr's code itself.
* **Mapping** a Radarr root folder to a folder nexcrate sees, by files and never by folder names (names repeat: two
  folders called ``Movies``, the same movie folders in a Full-HD and a 4K Radarr). Up to 20 sample files spread over the
  root folder's files. Every sample's path, unchanged and on every mount point with every tail, names candidate folders
  where it lies as a regular file (``lstat``, no link) of exactly Radarr's size. A candidate passes with at most one
  sample missing and at least 5 matching (every sample when there are fewer than 5); a proposal stands only when exactly
  one candidate passes. A mapping the owner chose (the longest matching remote prefix) wins; its samples are counted.
* **Names** are compared in NFC. A name not found as it is is looked for once in a listing of its folder, compared in
  NFC: a NAS share can hold NFD names. The names found on disk are what the version keeps.
* **Root folders without files** (finding 13) are listed and never block. Unless the owner chose
  a folder, one derives its folder from another root folder's mapping: the remote prefix that mapping replaces
  (``/data`` of ``/data/Movies`` seen as ``/media/Movies``) leads ``/data/Kinderfilme`` to ``/media/Kinderfilme`` when
  exactly one such folder exists (``derived``); otherwise it has none.
* **Files:** every file under a mapped root folder is looked at: found (the size on disk is taken, another size is
  counted as well) or missing. Files under an unmapped root folder are not looked at; that root folder blocks when it
  holds files.
* **The takeover** tests everything again, refuses a blocker the owner did not accept, and writes in one transaction.
  A taken-over version's ``root_folder`` is the mapped root folder (for a movie outside every root folder the folder
  above its movie folder), ``relative_path`` the movie folder below it and Radarr's relative path. A version without a
  found file keeps the mapped root folder when that folder exists, so its next file goes there. Nothing on disk
  changes before the transaction, not even a write test: a folder is only proposed by its rights. Afterwards the
  phase ``companions`` writes a ``release.nex`` for every version with a found file (the design notes,
  L2); it never fails the takeover. Undoing a takeover removes those entries again after its commit.
* Log lines carry ids, counts and codes, never titles, names or paths.
"""

from __future__ import annotations

import contextvars
import copy
import logging
import os
import stat
import threading
import time
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import exists, func, select, update
from sqlalchemy.orm import Session as OrmSession

from .. import crypto
from ..db import SessionLocal
from ..models import HistoryEntry, ImportRun, Source, Title, Version, VersionDefinition, utcnow
from . import companion_jobs, companions, folders, importer, judging, keep_as_is, naming, tags
from .downloads import files, store
from .profiles import store as profile_store
from .radarr import MediaManagement, NamingConfig
from .schreibweisen import nfc

logger = logging.getLogger("nexcrate.takeover")

KINDS = ("check", "takeover")
KEEP_FINISHED = timedelta(minutes=30)
SAMPLES_PER_ROOT = 20
#: With this many samples or fewer every one has to match; with more, one may miss.
ALL_MUST_MATCH = 5
MISSING_EXAMPLES = 20
#: A version in one of these states at the last read had an item in Radarr's queue.
QUEUE_STATES = ("downloading", "problem")
#: Radarr's recycle bin cleanup when the field is missing.
RADARR_RECYCLE_DAYS = 7
_CHUNK = 500


class TakeoverRunning(Exception):
    """A check or a takeover of this source runs already."""


class MappingInvalid(Exception):
    """A mapping of the request: a remote path that is not absolute or given twice, or a local folder nexcrate does not
    see."""

    def __init__(self, remote: str) -> None:
        super().__init__(remote)
        self.remote = remote


class JobFailed(Exception):
    """Ends a job with an error code and its values; nothing is changed."""

    def __init__(self, code: str, **values: object) -> None:
        super().__init__(code)
        self.code = code
        self.values = dict(values)


# --- The request ----------------------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Mapping:
    #: The remote prefix as Radarr names it, normalised.
    remote: str
    #: The folder nexcrate sees, resolved.
    local: str
    #: ``/`` or a drive such as ``C:``.
    root: str
    names: tuple[str, ...]


@dataclass(frozen=True)
class Request:
    mappings: tuple[Mapping, ...] = ()
    accept_missing: bool = False
    accept_queue: bool = False
    take_naming: bool = False
    #: A folder already checked for a version without one; checked again when saving.
    folder: str | None = None
    #: What the connection brought is not upgraded (``keep_as_is``, 24.09.2026).
    keep_as_is: bool = False


def mappings_from(pairs: list[tuple[str, str]]) -> tuple[Mapping, ...]:
    """The owner's mappings, checked: an absolute remote path given once, a local folder nexcrate sees."""
    checked: list[Mapping] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for remote_raw, local_raw in pairs:
        parts = files.remote_parts(nfc(remote_raw))
        if parts is None or not parts[0]:
            raise MappingInvalid(remote_raw)
        root, names = parts
        remote = files.join_remote(root, names)
        key = (root.casefold(), tuple(names))
        if key in seen:
            raise MappingInvalid(remote)
        seen.add(key)
        try:
            local, _mount = folders.visible(local_raw)
        except folders.NotVisible as exc:
            raise MappingInvalid(remote) from exc
        checked.append(Mapping(remote=remote, local=str(local), root=root, names=tuple(names)))
    return tuple(checked)


# --- The jobs -------------------------------------------------------------------------------------------------- #


@dataclass
class Job:
    id: int
    source_id: int
    kind: str
    started_at: datetime
    #: running, done or failed.
    state: str = "running"
    #: reading, mapping, files or saving while running; None afterwards.
    phase: str | None = "reading"
    done: int | None = None
    total: int | None = None
    finished_at: datetime | None = None
    error_code: str | None = None
    error_values: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def set(self, **values: Any) -> None:
        with self.lock:
            for name, value in values.items():
                setattr(self, name, value)


_lock = threading.Lock()
_jobs: dict[int, Job] = {}
_threads: list[threading.Thread] = []
_counter = {"last": 0}


def now() -> datetime:
    return utcnow()


def _expire() -> None:
    """Forget jobs that ended longer ago than ``KEEP_FINISHED``. Call with ``_lock`` held."""
    moment = now()
    for job_id, job in list(_jobs.items()):
        if job.finished_at is not None and moment - job.finished_at > KEEP_FINISHED:
            del _jobs[job_id]


def is_running(source_id: int) -> bool:
    with _lock:
        return any(job.source_id == source_id and job.state == "running" for job in _jobs.values())


def start(source_id: int, kind: str, request: Request) -> dict[str, Any]:
    """Claim the source and run the job in a thread of its own. Answers at once with the running job.

    Raises ``TakeoverRunning``, and from ``importer.begin`` ``ImportRunning``, ``SourceMissing``, ``SourceTakenOver``.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    with _lock:
        _expire()
        if any(job.source_id == source_id and job.state == "running" for job in _jobs.values()):
            raise TakeoverRunning(source_id)
        run = importer.begin(source_id)
        _counter["last"] += 1
        job = Job(id=_counter["last"], source_id=source_id, kind=kind, started_at=now())
        _jobs[job.id] = job
        thread = threading.Thread(
            target=contextvars.copy_context().run,
            args=(_execute, job, request, run.id),
            name=f"takeover-{job.id}",
            daemon=True,
        )
        _threads[:] = [existing for existing in _threads if existing.is_alive()]
        _threads.append(thread)
        try:
            thread.start()
        except BaseException:
            del _jobs[job.id]
            importer.fail_running_run(run.id, "import_failed")
            importer.release(source_id)
            raise
    logger.info("Takeover %s %d of source %d started", kind, job.id, source_id)
    return snapshot(job)


def snapshot(job: Job) -> dict[str, Any]:
    with job.lock:
        return {
            "id": job.id,
            "source_id": job.source_id,
            "kind": job.kind,
            "state": job.state,
            "phase": job.phase,
            "progress": {"done": job.done or 0, "total": job.total} if job.total is not None else None,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "error_code": job.error_code,
            "error_values": copy.deepcopy(job.error_values) if job.error_values is not None else None,
            "result": copy.deepcopy(job.result),
        }


def latest(source_id: int) -> dict[str, Any] | None:
    """The newest job of a source that runs or ended less than 30 minutes ago."""
    with _lock:
        _expire()
        found = [job for job in _jobs.values() if job.source_id == source_id]
    if not found:
        return None
    return snapshot(max(found, key=lambda job: job.id))


def wait_idle(timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    with _lock:
        threads = list(_threads)
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    with _lock:
        _threads[:] = [thread for thread in _threads if thread.is_alive()]
        return not _threads


def reset() -> None:
    """Forget every job. For the tests, after ``wait_idle``."""
    with _lock:
        _jobs.clear()


def _execute(job: Job, request: Request, run_id: int) -> None:
    started = time.perf_counter()
    fields: dict[str, Any]
    try:
        # Waits for an import or takeover that is still writing: two of them at once lost writes to SQLite's lock.
        with importer.heavy_writer(f"The takeover job {job.id} of source {job.source_id}"):
            fields = {"state": "done", "result": _work(job, request, run_id)}
    except JobFailed as exc:
        fields = {"state": "failed", "error_code": exc.code, "error_values": dict(exc.values)}
    except Exception:
        logger.exception("Takeover %s %d of source %d failed unexpectedly", job.kind, job.id, job.source_id)
        fields = {"state": "failed", "error_code": "takeover_failed", "error_values": {}}
    finally:
        try:
            importer.fail_running_run(run_id, "import_failed")
        except Exception:
            logger.exception("The import run of takeover %d could not be closed", job.id)
        importer.release(job.source_id)
    job.set(phase=None, finished_at=now(), **fields)
    logger.info(
        "Takeover %s %d of source %d %s in %dms%s",
        job.kind,
        job.id,
        job.source_id,
        fields["state"],
        (time.perf_counter() - started) * 1000,
        f": {fields['error_code']}" if fields["state"] == "failed" else "",
    )


# --- What the job looks at ------------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Item:
    """One version of the source, with its paths as Radarr names them."""

    version_id: int
    title_id: int
    has_file: bool
    size: int
    state: str
    #: The root folder the movie counts under, normalised; None without a stored movie folder.
    root: str | None
    root_kind: str
    root_names: tuple[str, ...]
    #: The movie folder's names below the root folder, the movie folder last (several with a slash in the format).
    movie_names: tuple[str, ...]
    #: Radarr's relative path of the file below the movie folder.
    file_names: tuple[str, ...]

    @property
    def locatable(self) -> bool:
        return self.root is not None and bool(self.movie_names) and bool(self.file_names)

    @property
    def below_root(self) -> tuple[str, ...]:
        return (*self.movie_names, *self.file_names)

    @property
    def remote_names(self) -> tuple[str, ...]:
        return (*self.root_names, *self.movie_names, *self.file_names)


def item_of(version: Version) -> Item:
    movie = files.remote_parts(version.source_movie_path) if version.source_movie_path else None
    relative = files.remote_parts(version.relative_path) if version.relative_path else None
    root: str | None = None
    kind = ""
    root_names: tuple[str, ...] = ()
    movie_names: tuple[str, ...] = ()
    if movie is not None and movie[1]:
        kind, names = movie
        stored = files.remote_parts(version.root_folder) if version.root_folder else None
        below = len(names) - 1
        if (
            stored is not None
            and stored[0] == kind
            and len(stored[1]) < len(names)
            and [nfc(name) for name in names[: len(stored[1])]] == [nfc(name) for name in stored[1]]
        ):
            below = len(stored[1])
        # A movie folder outside every root folder counts under its parent folder.
        root_names, movie_names = tuple(names[:below]), tuple(names[below:])
        root = files.join_remote(kind, list(root_names))
    return Item(
        version_id=version.id,
        title_id=version.title_id,
        has_file=bool(version.has_file),
        size=int(version.size or 0),
        state=version.state,
        root=root,
        root_kind=kind,
        root_names=root_names,
        movie_names=movie_names,
        file_names=tuple(relative[1]) if relative is not None else (),
    )


class Sight:
    """What nexcrate sees, read once per job: its mount points, its data directory, and folder listings."""

    def __init__(self) -> None:
        self.mounts = folders.mount_points()
        self.data = folders.data_dir()
        self._listings: dict[Path, dict[str, str] | None] = {}

    def visible(self, candidate: Path) -> Path | None:
        """The resolved path when it exists inside a mount point and away from the data directory."""
        path = folders.resolved(candidate)
        if path is None or not any(path == mount or path.is_relative_to(mount) for mount in self.mounts):
            return None
        data = self.data
        if data is not None and (path == data or path.is_relative_to(data) or data.is_relative_to(path)):
            return None
        return path

    def folder(self, candidate: Path) -> Path | None:
        path = self.visible(candidate)
        return path if path is not None and path.is_dir() else None

    def _listing(self, folder: Path) -> dict[str, str] | None:
        if folder not in self._listings:
            try:
                names = os.listdir(folder)
            except OSError, ValueError:
                self._listings[folder] = None
            else:
                listing: dict[str, str] = {}
                for name in sorted(names):
                    listing.setdefault(unicodedata.normalize("NFC", name), name)
                self._listings[folder] = listing
        return self._listings[folder]

    def lookup(self, base: Path, names: Sequence[str]) -> Path | None:
        """The path below ``base`` with these names compared in NFC, spelled as on disk; None when a name is missing."""
        current = base
        for name in names:
            candidate = current / name
            try:
                exists_as_is = os.path.lexists(candidate)
            except ValueError:
                return None
            if exists_as_is:
                current = candidate
                continue
            listing = self._listing(current)
            actual = listing.get(unicodedata.normalize("NFC", name)) if listing is not None else None
            if actual is None:
                return None
            current = current / actual
        return current

    def regular_size(self, path: Path | None) -> int | None:
        """The size of a regular file that is no link and lies where nexcrate sees; None for anything else."""
        if path is None:
            return None
        try:
            status = os.lstat(path)
        except OSError, ValueError:
            return None
        if not stat.S_ISREG(status.st_mode) or self.visible(path) is None:
            return None
        return status.st_size


def candidate_roots(sight: Sight, item: Item) -> list[Path]:
    """The local folders under which a sample lies as a regular file of Radarr's size.

    The path unchanged first, then every mount point with every tail of the path, longest tail first.
    """
    names = item.remote_names
    below = len(item.below_root)
    bases: list[tuple[Path, tuple[str, ...]]] = []
    if item.root_kind:
        bases.append((Path(files.join_remote(item.root_kind, [])), names))
    bases.extend((mount, names[start:]) for mount in sight.mounts for start in range(len(names)))
    found: list[Path] = []
    for base, tail in bases:
        if below < 1 or len(tail) < below:
            continue
        path = sight.lookup(base, tail)
        if path is None or sight.regular_size(path) != item.size:
            continue
        root = sight.folder(path.parents[below - 1])
        if root is not None and root not in found:
            found.append(root)
    return found


def matching(sight: Sight, local: Path, samples: list[Item]) -> int:
    """How many samples lie below ``local`` as regular files of exactly Radarr's size."""
    return sum(1 for item in samples if sight.regular_size(sight.lookup(local, item.below_root)) == item.size)


def required_matches(samples: int) -> int:
    """At most one sample may miss and at least 5 must match; with 5 samples or fewer every one."""
    return samples if samples <= ALL_MUST_MATCH else samples - 1


@dataclass
class RootState:
    remote: str
    kind: str
    names: tuple[str, ...]
    local: Path | None = None
    found_by: str = "none"
    movies: int = 0
    files: int = 0
    samples: int = 0
    samples_matched: int = 0

    def out(self) -> dict[str, Any]:
        return {
            "remote": self.remote,
            "local": str(self.local) if self.local is not None else None,
            "found_by": self.found_by,
            "movies": self.movies,
            "files": self.files,
            "samples": self.samples,
            "samples_matched": self.samples_matched,
        }


def spread(items: list[Item], count: int = SAMPLES_PER_ROOT) -> list[Item]:
    """Up to ``count`` items spread evenly from the first to the last."""
    if len(items) <= count:
        return list(items)
    step = (len(items) - 1) / (count - 1)
    return [items[round(index * step)] for index in range(count)]


def chosen_local(root: RootState, mappings: tuple[Mapping, ...]) -> Path | None:
    """The local folder of a root folder through the owner's longest matching mapping, or None."""
    best: Mapping | None = None
    for mapping in mappings:
        if mapping.root.casefold() != root.kind.casefold() or len(mapping.names) > len(root.names):
            continue
        if [nfc(name) for name in root.names[: len(mapping.names)]] != [nfc(name) for name in mapping.names]:
            continue
        if best is None or len(mapping.names) > len(best.names):
            best = mapping
    if best is None:
        return None
    return Path(best.local, *root.names[len(best.names) :])


def map_roots(sight: Sight, items: list[Item], mappings: tuple[Mapping, ...]) -> dict[str, RootState]:
    roots: dict[str, RootState] = {}
    for item in items:
        if item.root is None:
            continue
        state = roots.setdefault(item.root, RootState(remote=item.root, kind=item.root_kind, names=item.root_names))
        state.movies += 1
        if item.has_file and item.locatable:
            state.files += 1
    for state in roots.values():
        with_files = [item for item in items if item.root == state.remote and item.has_file and item.locatable]
        samples = spread(sorted(with_files, key=lambda item: item.version_id))
        state.samples = len(samples)
        chosen = chosen_local(state, mappings)
        if chosen is not None:
            state.local = sight.folder(chosen) or chosen
            state.found_by = "chosen"
            state.samples_matched = matching(sight, state.local, samples)
            continue
        if not samples:
            continue
        candidates: list[Path] = []
        for item in samples:
            candidates.extend(root for root in candidate_roots(sight, item) if root not in candidates)
        counted = {root: matching(sight, root, samples) for root in candidates}
        need = required_matches(len(samples))
        passing = [root for root, count in counted.items() if count >= need]
        state.samples_matched = max(counted.values(), default=0)
        # ⚠️ Exactly one: a copy of the library elsewhere passes as well, and then nobody can tell which is meant.
        if len(passing) == 1:
            state.local = passing[0]
            state.samples_matched = counted[passing[0]]
            unchanged = sight.folder(Path(state.remote)) if state.kind else None
            state.found_by = "same_path" if unchanged == passing[0] else "found"
    derive_roots(sight, roots)
    return roots


#: The ways a root folder gets a mapping that a root folder without files may derive its folder from.
MAPPED = ("same_path", "found", "chosen")


def prefixes(state: RootState) -> tuple[tuple[str, ...], Path] | None:
    """The remote and the local prefix of a mapped root folder: both paths without the names they end in alike.

    ``/data/Movies`` seen as ``/media/Movies`` gives ``/data`` and ``/media``. Names are compared in NFC; the anchor
    of the local path (``/`` or a drive) never counts as a name.
    """
    if state.local is None:
        return None
    local = state.local.parts
    shared = 0
    while (
        shared < len(state.names)
        and shared < len(local) - 1
        and nfc(state.names[-1 - shared]) == nfc(local[-1 - shared])
    ):
        shared += 1
    return state.names[: len(state.names) - shared], Path(*local[: len(local) - shared])


def derived_local(sight: Sight, state: RootState, sources: Sequence[RootState]) -> Path | None:
    """The folder a root folder without files gets through the mappings of other root folders, or None.

    A mapped root folder whose remote prefix begins this path names a folder below its local prefix, spelled as on
    disk; it counts when it is a folder nexcrate sees.
    ⚠️ Exactly one: two mappings that lead to two existing folders prove nothing.
    """
    names = [nfc(name) for name in state.names]
    found: list[Path] = []
    for source in sources:
        pair = prefixes(source)
        if pair is None or source.kind.casefold() != state.kind.casefold():
            continue
        prefix, local = pair
        if len(prefix) > len(names) or names[: len(prefix)] != [nfc(name) for name in prefix]:
            continue
        path = sight.lookup(local, state.names[len(prefix) :])
        folder = sight.folder(path) if path is not None else None
        if folder is not None and folder not in found:
            found.append(folder)
    return found[0] if len(found) == 1 else None


def derive_roots(sight: Sight, roots: dict[str, RootState]) -> None:
    """Root folders without files the owner did not map take the folder ``derived_local`` gives."""
    sources = [state for state in roots.values() if state.found_by in MAPPED and state.local is not None]
    for state in roots.values():
        if state.files == 0 and state.found_by == "none":
            local = derived_local(sight, state, sources)
            if local is not None:
                state.local, state.found_by = local, "derived"


@dataclass(frozen=True)
class Found:
    #: The mapped root folder as nexcrate sees it.
    root_folder: str
    #: The movie folder below the root folder and Radarr's relative path, spelled as on disk.
    relative_path: str
    size: int


@dataclass
class Files:
    found: dict[int, Found] = field(default_factory=dict)
    missing: list[Item] = field(default_factory=list)
    other_size: int = 0


def look_at_files(job: Job, sight: Sight, items: list[Item], roots: dict[str, RootState]) -> Files:
    """Every file of the source outside an unmapped root folder: found or missing."""
    wanted = [
        item for item in items if item.has_file and not (item.root is not None and roots[item.root].local is None)
    ]
    job.set(phase="files", done=0, total=len(wanted))
    locals_seen: dict[str, Path | None] = {}
    result = Files()
    for index, item in enumerate(wanted, start=1):
        found: Found | None = None
        state = roots.get(item.root) if item.root is not None else None
        if state is not None and state.local is not None and item.locatable:
            if state.remote not in locals_seen:
                locals_seen[state.remote] = sight.folder(state.local)
            local = locals_seen[state.remote]
            path = sight.lookup(local, item.below_root) if local is not None else None
            size = sight.regular_size(path)
            if local is not None and path is not None and size is not None:
                found = Found(root_folder=str(local), relative_path=path.relative_to(local).as_posix(), size=size)
        if found is None:
            result.missing.append(item)
        else:
            result.found[item.version_id] = found
            if found.size != item.size:
                result.other_size += 1
        job.set(done=index)
    return result


def target_folders(
    sight: Sight, items: list[Item], roots: dict[str, RootState], found: dict[int, Found]
) -> dict[int, str]:
    """The folder each version without a found file keeps as its ``root_folder`` (finding 13).

    nexcrate's folder of its Radarr root folder, when that root folder has a mapping and the folder exists; a movie
    outside every root folder counts under its parent folder and takes that folder's mapping. Versions without such a
    folder are left out: their next file goes into the version folder.
    """
    seen: dict[str, Path | None] = {}
    targets: dict[int, str] = {}
    for item in items:
        state = roots.get(item.root) if item.root is not None else None
        if item.version_id in found or state is None or state.local is None:
            continue
        if state.remote not in seen:
            seen[state.remote] = sight.folder(state.local)
        folder = seen[state.remote]
        if folder is not None:
            targets[item.version_id] = str(folder)
    return targets


def source_naming(config: NamingConfig) -> dict[str, Any]:
    """Radarr's naming as nexcrate would take it, the first code nexcrate's check gives per pattern, and notes.

    The folder pattern is always Radarr's. The file pattern is Radarr's only when Radarr renames; without renaming (its
    default) Radarr keeps the release name, which is what ``{Original Title}`` renders.
    """
    folder = nfc(config.movie_folder)
    file = nfc(config.movie_file) if config.rename_movies else "{Original Title}"
    problems = {
        "movie_folder": naming.problem_of(folder, "movie_folder"),
        "movie_file": naming.problem_of(file, "movie_file"),
    }
    notes: list[dict[str, Any]] = []
    if not config.rename_movies:
        notes.append({"code": "radarr_no_rename", "values": {}})
    for which, pattern in (("movie_folder", folder), ("movie_file", file)):
        if "/" in pattern or "\\" in pattern:
            notes.append({"code": "slash_in_pattern", "values": {"pattern": which}})
    if config.colon_replacement and config.colon_replacement != "smart":
        notes.append({"code": "colon_format", "values": {"format": config.colon_replacement}})
    if not config.replace_illegal_characters:
        notes.append({"code": "illegal_characters_kept", "values": {}})
    return {
        "movie_folder": folder,
        "movie_file": file,
        "rename_movies": config.rename_movies,
        "colon_replacement": config.colon_replacement,
        "problems": problems,
        "can_take": problems["movie_folder"] is None and problems["movie_file"] is None,
        "notes": notes,
    }


def media_notes(media: MediaManagement | None) -> list[dict[str, Any]]:
    """What Radarr's media management did that nexcrate does differently. Never a blocker; empty without a read."""
    if media is None:
        return []
    notes: list[dict[str, Any]] = []
    if media.recycle_bin:
        days = media.recycle_bin_cleanup_days if media.recycle_bin_cleanup_days is not None else RADARR_RECYCLE_DAYS
        notes.append({"code": "radarr_recycle_bin", "values": {"days": days}})
    if media.import_extra_files:
        notes.append({"code": "radarr_extra_files", "values": {}})
    if not media.copy_using_hardlinks:
        notes.append({"code": "radarr_hardlinks_off", "values": {}})
    if media.file_date and media.file_date.casefold() != "none":
        notes.append({"code": "radarr_file_date", "values": {}})
    if media.set_permissions_linux:
        notes.append({"code": "radarr_permissions", "values": {}})
    return notes


def _version_brief(db: OrmSession, source: Source, roots: dict[str, RootState]) -> dict[str, Any]:
    definition = db.get(VersionDefinition, source.version_id)
    if definition is None:
        raise JobFailed("not_found")
    rules = judging.usable_rules(profile_store.of_version(db, definition.id))
    proposal = None
    if definition.folder is None:
        mapped = [state for state in roots.values() if state.local is not None and state.files > 0]
        if mapped:
            best = max(mapped, key=lambda state: state.files)
            proposal = folders.may_propose(db, definition.id, best.local) if best.local is not None else None
    return {
        "id": definition.id,
        "label": definition.label,
        "has_profile": rules is not None,
        "folder": definition.folder,
        "folder_proposal": proposal,
    }


# --- The work -------------------------------------------------------------------------------------------------- #


def _work(job: Job, request: Request, run_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        taken = list(db.scalars(select(Version.id).where(Version.source_id == job.source_id)))
    result = _take_over(job, request, run_id)
    if request.keep_as_is:
        # Every version the connection fed is nexcrate's now: what it brought stays as it is (keep_as_is).
        with SessionLocal() as db:
            kept = keep_as_is.apply(db, taken, utcnow())
            db.commit()
        logger.info(
            "Takeover of source %d keeps what it brought: %d movies, %d albums, %d episodes",
            job.source_id,
            kept.movies,
            kept.albums,
            kept.episodes,
        )
        result = {**result, "kept": {"movies": kept.movies, "albums": kept.albums, "episodes": kept.episodes}}
    return result


def _take_over(job: Job, request: Request, run_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        app = db.scalar(select(Source.app).where(Source.id == job.source_id))
    if app == "sonarr":
        # Series differ in what they read and save (Ü1); imported here, it imports this module.
        from .series import takeover_series

        return takeover_series.work(job, request, run_id)
    if app == "lidarr":
        # Music likewise.
        from .music import takeover_music

        return takeover_music.work(job, request, run_id)
    read = importer.read_source(job.source_id, run_id, for_takeover=True)
    queue = 0
    if read.fetched is not None:
        if importer.save_read(job.source_id, run_id, read.fetched) is None:
            raise JobFailed("not_found")
        movie_ids = {movie.id for movie in read.fetched.movies}
        queue = sum(1 for entry in read.fetched.queue if entry.movie_id is not None and entry.movie_id in movie_ids)
    elif read.error_code == "not_found":
        raise JobFailed("not_found")

    with SessionLocal() as db:
        source = db.get(Source, job.source_id)
        if source is None:
            raise JobFailed("not_found")
        if source.taken_over_at is not None:
            raise JobFailed("source_taken_over")
        items = [
            item_of(version)
            for version in db.scalars(select(Version).where(Version.source_id == source.id).order_by(Version.id))
        ]
        if read.fetched is not None:
            data_from = "fresh"
            read_at = db.scalar(select(ImportRun.finished_at).where(ImportRun.id == run_id))
        else:
            last = db.scalar(
                select(ImportRun)
                .where(ImportRun.source_id == source.id, ImportRun.status == "done")
                .order_by(ImportRun.id.desc())
                .limit(1)
            )
            if last is None:
                raise JobFailed(read.error_code or "takeover_needs_import", **read.error_values)
            if any(item.has_file and item.root is None for item in items):
                raise JobFailed("takeover_needs_import", reason=read.error_code)
            data_from, read_at = "stored", last.finished_at
            queue = sum(1 for item in items if item.state in QUEUE_STATES)

    job.set(phase="mapping")
    sight = Sight()
    roots = map_roots(sight, items, request.mappings)
    looked = look_at_files(job, sight, items, roots)
    with SessionLocal() as db:
        source = db.get(Source, job.source_id)
        if source is None:
            raise JobFailed("not_found")
        version = _version_brief(db, source, roots)
    naming_out = source_naming(read.naming) if read.naming is not None else None
    unmapped = sorted(
        (state for state in roots.values() if state.files > 0 and state.local is None), key=lambda state: state.remote
    )
    blockers = [
        name
        for name, applies in (
            ("root_unmapped", bool(unmapped)),
            ("files_missing", bool(looked.missing)),
            ("queue_active", queue > 0),
        )
        if applies
    ]
    result: dict[str, Any] = {
        "data_from": data_from,
        "read_at": read_at,
        "movies": len(items),
        "with_file": sum(1 for item in items if item.has_file),
        "roots": [state.out() for state in sorted(roots.values(), key=lambda state: state.remote)],
        "files_found": len(looked.found),
        "files_missing": len(looked.missing),
        "files_other_size": looked.other_size,
        "missing_examples": ["/".join(item.below_root) for item in looked.missing if item.locatable][:MISSING_EXAMPLES],
        "queue": queue,
        "version": version,
        "naming": naming_out,
        "notes": media_notes(read.media),
        "blockers": blockers,
        "taken": None,
        "companions": None,
    }
    logger.info(
        "Takeover %s %d of source %d: %s data, %d versions, %d files found, %d missing, %d of other size, %d root "
        "folders, %d unmapped, %d queue items",
        job.kind,
        job.id,
        job.source_id,
        data_from,
        len(items),
        len(looked.found),
        len(looked.missing),
        looked.other_size,
        len(roots),
        len(unmapped),
        queue,
    )
    if job.kind == "check":
        return result

    # The blocking conditions again, now against the owner's acceptance.
    if unmapped:
        raise JobFailed("takeover_root_unmapped", remote=unmapped[0].remote)
    if looked.missing and not request.accept_missing:
        raise JobFailed("takeover_files_missing", count=len(looked.missing))
    if queue and not request.accept_queue:
        raise JobFailed("takeover_queue_active", count=queue)
    patterns: tuple[str, str] | None = None
    if request.take_naming:
        if naming_out is None:
            if read.fetched is None:
                raise JobFailed(read.error_code or "takeover_needs_import", **read.error_values)
            raise JobFailed(read.naming_error or "radarr_http_error")
        for problem in naming_out["problems"].values():
            if problem is not None:
                raise JobFailed(problem["code"], **problem["values"])
        patterns = (naming_out["movie_folder"], naming_out["movie_file"])
    job.set(phase="saving")
    targets = target_folders(sight, items, roots, looked.found)
    result["taken"] = save(job.source_id, request, looked.found, patterns, targets)
    result["companions"] = write_companions(job, sorted(looked.found))
    return result


def write_companions(job: Job, version_ids: list[int]) -> dict[str, int]:
    """The phase ``companions``: a ``release.nex`` for every taken-over version with a file, after the commit.

    One version after another, a pause after each written file. Counts per state. ⚠️ Never fails the takeover: the
    versions are nexcrate's own already; what could not be written is the version's ``companion_state``.
    """
    counts: dict[str, int] = {}
    job.set(phase="companions", done=0, total=len(version_ids))
    try:
        with companion_jobs.takeover_phase():
            for index, version_id in enumerate(version_ids, start=1):
                state = companions.write_version(version_id)
                counts[state] = counts.get(state, 0) + 1
                job.set(done=index)
                if state == "written" and companion_jobs.WRITE_PAUSE_SECONDS > 0:
                    time.sleep(companion_jobs.WRITE_PAUSE_SECONDS)
    except Exception:  # the takeover is committed; release.nex must never undo that
        logger.exception("Takeover %d of source %d: writing release.nex stopped unexpectedly", job.id, job.source_id)
    logger.info(
        "Takeover %d of source %d: release.nex for %d versions: %s",
        job.id,
        job.source_id,
        len(version_ids),
        ", ".join(f"{key}={value}" for key, value in sorted(counts.items())),
    )
    return counts


def _original_languages(db: OrmSession, title_ids: set[int]) -> dict[int, str | None]:
    ordered = sorted(title_ids)
    languages: dict[int, str | None] = {}
    for index in range(0, len(ordered), _CHUNK):
        chunk = ordered[index : index + _CHUNK]
        rows = db.execute(select(Title.id, Title.original_language).where(Title.id.in_(chunk))).tuples().all()
        languages.update(dict(rows))
    return languages


def _handed_to_fill(db: OrmSession, title_ids: set[int]) -> int:
    """Titles the TMDB fill takes: no source owns their data or feeds them, and they have no TMDB data yet."""
    fed = exists().where(Version.title_id == Title.id, Version.source_id.is_not(None))
    ordered = sorted(title_ids)
    count = 0
    for index in range(0, len(ordered), _CHUNK):
        chunk = ordered[index : index + _CHUNK]
        count += int(
            db.scalar(
                select(func.count(Title.id)).where(
                    Title.id.in_(chunk), Title.meta_source_id.is_(None), Title.tmdb_refreshed_at.is_(None), ~fed
                )
            )
            or 0
        )
    return count


def _failed_from(exc: HTTPException) -> JobFailed:
    detail = dict(exc.detail) if isinstance(exc.detail, dict) else {"code": "invalid_input"}
    code = str(detail.pop("code", "invalid_input"))
    detail.pop("message", None)
    return JobFailed(code, **detail)


def save(
    source_id: int,
    request: Request,
    found: dict[int, Found],
    patterns: tuple[str, str] | None,
    targets: dict[int, str] | None = None,
) -> dict[str, int]:
    """The takeover's one transaction. Returns the counts of ``taken``.

    ``targets`` holds the folder a version without a found file keeps as its ``root_folder`` (``target_folders``).
    """
    targets = targets or {}
    moment = now()
    with SessionLocal() as db:
        # A write first: it takes SQLite's write lock before anything is read.
        db.execute(update(Source).where(Source.id == source_id).values(updated_at=moment))
        source = db.get(Source, source_id)
        if source is None:
            db.rollback()
            raise JobFailed("not_found")
        if source.taken_over_at is not None:
            db.rollback()
            raise JobFailed("source_taken_over")
        definition = db.get(VersionDefinition, source.version_id)
        if definition is None:
            db.rollback()
            raise JobFailed("not_found")
        if request.folder and definition.folder is None:
            try:
                definition.folder = folders.check_version_folder(db, definition, request.folder)
            except HTTPException as exc:
                db.rollback()
                raise _failed_from(exc) from exc
        rules = judging.usable_rules(profile_store.of_version(db, definition.id))
        versions = list(db.scalars(select(Version).where(Version.source_id == source.id).order_by(Version.id)))
        languages = _original_languages(db, {version.title_id for version in versions})
        with_file = missing = 0
        for version in versions:
            located = found.get(version.id)
            had_file = bool(version.has_file)
            version.source_id = None
            version.radarr_movie_id = None
            version.profile_name = None
            version.upgrade_to = None
            version.source_movie_path = None
            version.progress = None
            version.problem_code = None
            # ``monitored`` and ``minimum_availability`` stay as Radarr had them.
            if located is not None:
                version.has_file = True
                version.file_ref = f"taken:{version.file_ref or 0}"[:64]
                version.root_folder = located.root_folder
                version.relative_path = located.relative_path
                version.size = located.size
                # Judged by nexcrate's rules through the one judgement of stored files (finding 15), which names the
                # file as the search does; without a profile nothing to judge.
                version.cutoff_not_met = judging.verdict(rules, version, languages.get(version.title_id))
                with_file += 1
            else:
                missing += 1 if had_file else 0
                version.has_file = False
                version.file_ref = None
                version.quality = None
                version.size = 0
                version.languages = []
                version.release_group = None
                version.relative_path = None
                # The folder of its root folder when that has one: its next file goes there (finding 13).
                version.root_folder = targets.get(version.id)
                version.release_title = None
                version.media_info = None
                version.cutoff_not_met = False
            if version.has_file:
                version.state = "upgrade" if version.cutoff_not_met else "available"
            else:
                version.state = "wanted" if version.monitored else "unmonitored"
            version.updated_at = moment
            db.add(
                HistoryEntry(
                    title_id=version.title_id,
                    version_id=version.id,
                    version_definition_id=definition.id,
                    version_label=definition.label,
                    event="taken_over",
                    at=moment,
                    detail=source.name[:1024],
                )
            )
        db.flush()
        # Titles whose data came from this connection lose that tie, so TMDB's data takes over.
        db.execute(
            update(Title).where(Title.meta_source_id == source.id).values(meta_source_id=None),
            execution_options={"synchronize_session": False},
        )
        titles = _handed_to_fill(db, {version.title_id for version in versions})
        # Counted before the commit: the rows expire with it.
        kept = sum(1 for version in versions if not version.has_file and version.root_folder)
        # The key stays, encrypted, for undoing the takeover; nothing reads a taken-over connection until then.
        source.taken_over_at = moment
        # The connection's tags become the owner's.
        tags.release(db, source.id)
        source.updated_at = moment
        if patterns is not None:
            naming.set_version(definition, *patterns)
        db.commit()
    logger.info(
        "Source %d taken over: %d versions, %d with a file, %d files missing, %d without a file keep a folder, %d "
        "titles for TMDB",
        source_id,
        len(versions),
        with_file,
        missing,
        kept,
        titles,
    )
    return {"versions": len(versions), "with_file": with_file, "missing": missing, "titles": titles}


# --- Undoing a takeover ("Changes after the owner's live test", finding 12) ---------------- #


class NotTakenOver(Exception):
    """The source is not taken over."""


class DownloadsActive(Exception):
    """nexcrate has pending downloads for the version the source fed."""

    def __init__(self, count: int) -> None:
        super().__init__(count)
        self.count = count


@dataclass(frozen=True)
class UndoTarget:
    url: str
    #: ``radarr``, ``sonarr`` or ``lidarr``: the client that tests the connection.
    app: str
    #: ⚠️ The stored key, decrypted; empty when none is stored or it cannot be read.
    stored_key: str = field(repr=False)
    #: Pending downloads of the version the source fed.
    pending: int = 0


def claim_for_undo(source_id: int) -> UndoTarget:
    """Claim the source as an import does and read what undoing needs; the caller holds the claim afterwards.

    Raises ``importer.SourceMissing``, ``NotTakenOver``, ``TakeoverRunning`` or ``importer.ImportRunning``, and then
    holds no claim.
    """
    with SessionLocal() as db:
        source = db.get(Source, source_id)
        if source is None:
            raise importer.SourceMissing(source_id)
        if source.taken_over_at is None:
            raise NotTakenOver(source_id)
    with _lock:
        if any(job.source_id == source_id and job.state == "running" for job in _jobs.values()):
            raise TakeoverRunning(source_id)
        importer.claim(source_id)
    try:
        with SessionLocal() as db:
            source = db.get(Source, source_id)
            if source is None:
                raise importer.SourceMissing(source_id)
            if source.taken_over_at is None:
                raise NotTakenOver(source_id)
            return UndoTarget(
                url=source.url,
                app=source.app,
                stored_key=crypto.decrypt(source.api_key),
                pending=store.pending_count(db, source.version_id),
            )
    except BaseException:
        importer.release(source_id)
        raise


def undo(source_id: int, key: str | None) -> ImportRun:
    """Undo the takeover in one transaction, then start the import that feeds the versions again.

    Runs with the claim of ``claim_for_undo``, after Radarr answered, and hands the claim to the import run. ``key``
    is a key the owner gave, stored encrypted; None keeps the stored one. Raises ``importer.SourceMissing``,
    ``NotTakenOver`` or ``DownloadsActive`` (a load started meanwhile), with nothing changed and the claim given up.
    """
    moment = now()
    try:
        with SessionLocal() as db:
            # A write first: it takes SQLite's write lock before anything is read.
            db.execute(update(Source).where(Source.id == source_id).values(updated_at=moment))
            source = db.get(Source, source_id)
            if source is None:
                db.rollback()
                raise importer.SourceMissing(source_id)
            if source.taken_over_at is None:
                db.rollback()
                raise NotTakenOver(source_id)
            pending = store.pending_count(db, source.version_id)
            if pending:
                db.rollback()
                raise DownloadsActive(pending)
            name = source.name[:1024]
            taken = select(HistoryEntry.version_id).where(
                HistoryEntry.event == "taken_over",
                HistoryEntry.version_definition_id == source.version_id,
                HistoryEntry.detail == name,
                HistoryEntry.version_id.is_not(None),
            )
            definition = db.get(VersionDefinition, source.version_id)
            undone = list(db.scalars(select(Version).where(Version.id.in_(taken)).order_by(Version.id)))
            # The release.nex entries of the versions handed back, collected before anything changes and removed
            # after the commit (L2). Switch off: nothing is removed, the states stay.
            # An album's release.nex is the album's own file: ``takeover_music`` removes it, not the movie way.
            removals = companions.plan_removal(db, undone) if companions.enabled(db) and source.app != "lidarr" else []
            series_removals: list[Any] = []
            album_removals: list[Any] = []
            if source.app == "sonarr":
                from .series import takeover_series

                series_removals = takeover_series.prepare_undo(db, undone)
            if source.app == "lidarr":
                from .music import takeover_music

                album_removals = takeover_music.prepare_undo(db, undone, source.id)
            for version in undone:
                if removals:
                    companions.clear_state(version)
                db.add(
                    HistoryEntry(
                        title_id=version.title_id,
                        version_id=version.id,
                        version_definition_id=version.version_definition_id,
                        version_label=definition.label if definition is not None else "",
                        event="takeover_undone",
                        at=moment,
                        detail=name,
                    )
                )
            source.taken_over_at = None
            if key:
                source.api_key = crypto.encrypt(key)
            source.updated_at = moment
            db.commit()
    except BaseException:
        importer.release(source_id)
        raise
    # The old check and takeover of this source are history now: opening "take over" again starts afresh.
    forget_finished(source_id)
    logger.info("Takeover of source %d undone for %d versions; an import feeds them again", source_id, len(undone))
    if removals:
        _remove_companions(source_id, removals)
    if series_removals:
        from .series import takeover_series

        _in_thread(f"takeover-undo-seasons-{source_id}", takeover_series.remove_entries, series_removals)
    if album_removals:
        from .music import takeover_music

        _in_thread(f"takeover-undo-albums-{source_id}", takeover_music.remove_entries, album_removals)
    return importer.start(source_id, claimed=True)


def _remove_companions(source_id: int, removals: list[companions.Removal]) -> None:
    """Remove the entries in a thread of its own, while the import runs; ``wait_idle`` waits for it as well."""
    _in_thread(f"takeover-undo-companions-{source_id}", companions.remove, removals)


def _in_thread(name: str, target: Any, removals: Any) -> None:
    thread = threading.Thread(
        target=contextvars.copy_context().run,
        args=(target, removals),
        name=name,
        daemon=True,
    )
    with _lock:
        _threads[:] = [existing for existing in _threads if existing.is_alive()]
        _threads.append(thread)
    thread.start()


def forget_finished(source_id: int) -> int:
    """Forget the finished checks and takeovers of a source; a running one stays. Returns how many went."""
    with _lock:
        finished = [job_id for job_id, job in _jobs.items() if job.source_id == source_id and job.state != "running"]
        for job_id in finished:
            del _jobs[job_id]
    return len(finished)
