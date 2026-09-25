"""Filing a finished download into its version's folder ("Importing").

* ``request`` starts the import of one completed download in a thread of its own; the download is claimed
  (``completed`` to ``importing``) in the database first, so no import runs twice. After a restart an import that was
  running is ``completed`` again (``mark_interrupted``).
* **Checks first:** the version still exists and no source feeds it, its folder is visible; otherwise the problem
  ``import_failed`` with a reason.
* **The files:** the reported path through the client's mappings, or as reported, but only inside a visible mount
  point. Otherwise a proposal from below the mount points and the problem ``path_not_found``. A folder ``_UNPACK_`` or
  ``_FAILED_`` sends the download back to ``completed``: the next round tries again.
* **The file:** see ``files``. A dangerous file refuses the download: its release goes on the blocklist.
* **The target:** ``<destination>/<folder name>/<file name>``, the folder name built from the version's pattern. A
  version with a current file (``file_ref`` starting with ``nexcrate:`` or ``taken:``, its ``root_folder`` visible, the
  file below it) is upgraded into the folder of that file, as in Radarr, however deep Radarr's folder format put it.
  Without a current file the destination is the version's ``root_folder`` when it is set, visible and writable (a
  taken-over movie without a file keeps the folder of its Radarr root folder), otherwise the version folder; the
  folders of a file that is gone are used again below that destination. Unpacking uses the same destination. Another
  file under the target name is ``import_failed`` with ``destination_exists``.
* **Replacing:** the new file under its temporary name first, then the old file into the recycle folder of the folder
  it lies in, then the rename.
* **Subtitles** (C9): once the video is in place, the recorded subtitles of the replaced file go
  into the recycle folder with it, and with the switch on the download's subtitles that belong to the movie are placed
  next to it (``subtitles.placing``). Neither ever fails the import; what fails is counted in the log.
* **Afterwards:** the version has the file, judged by its profile (``judging.judge``), a history entry ``imported``, the
  download is ``imported``. Then, after the commit and in the import thread, the version's ``release.nex`` is written
  or, for an upgrade, rewritten (L2); a failing write never undoes the import. For
  SABnzbd the job folder is deleted when it lies strictly inside the category folder, and then the job leaves SABnzbd's
  history. A torrent stays.
* ⚠️ Every path written to or deleted is checked after resolving links (``files``); a file of a version fed by a source
  is never touched.
"""

from __future__ import annotations

import asyncio
import contextvars
import dataclasses
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select, update

from ... import crypto
from ...db import SessionLocal, database_locked
from ...models import Download, DownloadClient, DownloadFile, Title, Version, VersionDefinition
from .. import companions, downloaders, folder_rules, folders, judging, media, naming, recycle, releases
from ..automatic import replacement
from ..mediaservers import notify as mediaserver_notify
from ..rename import guard as rename_guard
from ..subtitles import placing as subtitle_placing
from ..subtitles import records as subtitle_records
from ..subtitles import settings as subtitle_settings
from . import album_import, files, series_import, store, unpacking

#: ``file_ref`` prefixes of a file nexcrate knows where it lies: filed away by nexcrate, taken over from Radarr, or
#: found on disk and assigned or restored (the library from disk).
KNOWN_FILE_REFS = ("nexcrate:", "taken:", "disk:")
#: Problems that refuse the download: its release goes on the blocklist and the history says ``failed``. A movie in
#: several parts (CD1, CD2) too: one file would be half a movie (decision 38).
REFUSED = ("dangerous_file", "encrypted", "multi_part")
#: The path prefix of a video that came out of the download's archives, in ``download_files``.
UNPACKED_PREFIX = "unpacked:"

logger = logging.getLogger("nexcrate.import")

JOB_RECYCLE = "recycle_cleanup"
RECYCLE_INTERVAL_SECONDS = 24 * 3600

_lock = threading.Lock()
_running: set[int] = set()
_threads: list[threading.Thread] = []
#: How many imports write at once; the others wait in their thread. ⚠️ Without a limit (23.09.2026, after a
#: restart) about ten imports wrote at once, each briefly, and SQLite let two of them wait 30 s and give up after
#: their video had moved: it lay in the library unrecorded. Radarr and Sonarr file one download after the other.
PARALLEL_IMPORTS = 2
_gate = threading.BoundedSemaphore(PARALLEL_IMPORTS)

#: A locked database is a passing trouble (the owner's finding of 22.09.2026: download 178 met the lock once, became
#: "import failed" and waited nine hours for a click that imported it at once). The import tries again by itself, with
#: a pause that doubles from ``BUSY_FIRST_PAUSE_SECONDS`` up to ``BUSY_LONGEST_PAUSE_SECONDS``; only after
#: ``BUSY_GIVE_UP_SECONDS`` does it become a problem for the owner, with the reason ``database_busy``.
BUSY_FIRST_PAUSE_SECONDS = 15.0
BUSY_LONGEST_PAUSE_SECONDS = 600.0
BUSY_GIVE_UP_SECONDS = 30 * 60.0
#: How often the import thread tries to hand its download back to the next round when the lock stays.
BUSY_HAND_BACK_TRIES = 3
#: Download id to (tries, first failure, next try), on the monotonic clock.
_busy: dict[int, tuple[int, float, float]] = {}


def clock() -> float:
    return time.monotonic()


def waits(download_id: int) -> bool:
    """Whether an import that met a locked database still pauses before its next try."""
    with _lock:
        entry = _busy.get(download_id)
    return entry is not None and clock() < entry[2]


class Problem(Exception):
    def __init__(self, code: str, **values: object) -> None:
        super().__init__(code)
        self.code = code
        self.values = dict(values)
        #: For ``several_videos``: the videos to choose from as ``(path relative to the download, size)``. Kept in
        #: ``download_files``, never in the values: the answer and the log carry no paths.
        self.candidates: list[tuple[str, int]] = []


class StillUnpacking(Exception):
    """The client still works on the files."""


# --- The threads ------------------------------------------------------------------------------------ #


def running(download_id: int) -> bool:
    with _lock:
        return download_id in _running


def request(download_id: int, work: Callable[[], None] | None = None) -> bool:
    """Start the import of a completed download unless one runs for it. Returns whether a thread started.

    ``work`` runs instead of the import, in the same guard: the owner's assignment of a series download.
    """
    with _lock:
        if download_id in _running:
            return False
        _running.add(download_id)
        thread = threading.Thread(
            target=contextvars.copy_context().run,
            args=(_execute, download_id, work),
            name=f"import-download-{download_id}",
            daemon=True,
        )
        _threads[:] = [existing for existing in _threads if existing.is_alive()]
        _threads.append(thread)
    try:
        thread.start()
    except BaseException:
        with _lock:
            _running.discard(download_id)
        raise
    return True


def wait_idle(timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    with _lock:
        threads = list(_threads)
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    with _lock:
        _threads[:] = [thread for thread in _threads if thread.is_alive()]
        return not _threads


def mark_interrupted() -> int:
    """An import that was running when nexcrate stopped is ``completed`` again, and unpack folders go. Called at start.

    Every ``.nexcrate-unpack`` in the version folders and in the ``root_folder`` of versions no source feeds is deleted,
    except the folders of imports running now; the next round unpacks again.
    """
    with _lock:
        busy = set(_running)
    statement = update(Download).where(Download.state == "importing")
    if busy:
        statement = statement.where(Download.id.not_in(busy))
    with SessionLocal() as db:
        result = db.execute(statement.values(state="completed", updated_at=store.now()))
        db.commit()
    count = int(getattr(result, "rowcount", 0) or 0)
    if count:
        logger.warning("%d imports were interrupted by a restart and wait for the next round", count)
    removed = unpacking.clear_leftovers(_own_folders(), keep=busy)
    if removed:
        logger.info("%d unpack folders of interrupted imports deleted", removed)
    return count


def _execute(download_id: int, work: Callable[[], None] | None = None) -> None:
    try:
        with _gate:
            if work is not None:
                work()
            else:
                run(download_id)
    except Exception as exc:
        if database_locked(exc):
            _met_lock(download_id)
        else:
            logger.exception("The import of download %d failed unexpectedly", download_id)
            _record_problem(download_id, Problem("import_failed", reason="unexpected"))
    else:
        with _lock:
            _busy.pop(download_id, None)
    finally:
        with _lock:
            _running.discard(download_id)


def _met_lock(download_id: int) -> None:
    """The import met a locked database: the next round of the tracking tries again after a pause, and after
    ``BUSY_GIVE_UP_SECONDS`` the owner gets a problem. A movie moved before the lock is taken over then (``_adopt``)."""
    moment = clock()
    with _lock:
        tries, first, _next = _busy.get(download_id, (0, moment, moment))
        tries += 1
        pause = min(BUSY_LONGEST_PAUSE_SECONDS, BUSY_FIRST_PAUSE_SECONDS * 2 ** (tries - 1))
        _busy[download_id] = (tries, first, moment + pause)
    if moment - first >= BUSY_GIVE_UP_SECONDS:
        logger.warning(
            "Download %d: the database stayed locked for %d minutes; the import waits for the owner",
            download_id,
            int((moment - first) // 60),
        )
        try:
            _record_problem(download_id, Problem("import_failed", reason="database_busy"))
        except Exception as exc:
            if not database_locked(exc):
                raise
            logger.warning("Download %d: the problem could not be written either; the next round tries", download_id)
            _hand_back(download_id)
            return
        with _lock:
            _busy.pop(download_id, None)
        return
    logger.info(
        "Download %d met a locked database; the import tries again in %d s (try %d)", download_id, int(pause), tries
    )
    _hand_back(download_id)


def _hand_back(download_id: int) -> None:
    """``importing`` back to ``completed`` for the next round, in this thread, however long the lock stays: a few
    tries of SQLite's own wait. What stays ``importing`` a restart hands back (``mark_interrupted``)."""
    for attempt in range(BUSY_HAND_BACK_TRIES):
        try:
            with SessionLocal() as db:
                db.execute(
                    update(Download)
                    .where(Download.id == download_id, Download.state == "importing")
                    .values(state="completed", updated_at=store.now())
                )
                db.commit()
            return
        except Exception as exc:  # noqa: BLE001 - only a locked database is waited for; anything else is logged
            if not database_locked(exc) or attempt == BUSY_HAND_BACK_TRIES - 1:
                logger.warning("Download %d stays in its import until the database is free again", download_id)
                return


# --- What an import knows ---------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Context:
    download_id: int
    title_id: int
    definition_id: int | None
    protocol: str
    client_download_id: str
    reported_path: str | None
    mappings: list[dict[str, str]]
    #: ⚠️ With the decrypted secret; None when the client is gone.
    target: downloaders.Target | None = field(repr=False)
    version_exists: bool
    version_fed: bool
    version_folder: str | None
    #: The version's current file, when nexcrate knows where it lies: an upgrade goes into its folder.
    current_file: Path | None
    #: The folders of a file that is gone, below ``root_folder``; used again only when that folder is the destination.
    current_folder_parts: tuple[str, ...]
    movie: naming.MovieFacts
    release: naming.ReleaseFacts
    naming: naming.Naming
    #: The visible ``root_folder`` the current file lies below; the old file goes into its recycle folder.
    current_root: Path | None = None
    #: The version's ``root_folder``: without a current file the destination, when nexcrate sees it and may write there.
    root_folder: str | None = None
    #: Whether the download's subtitle files are placed next to the movie (C9).
    subtitles_enabled: bool = True
    #: The recorded subtitles of the current file as ``(id, relative_path)``; they go into the recycle folder with it.
    current_subtitles: tuple[tuple[int, str], ...] = ()
    #: The video the owner chose of several (decision 39), relative to the download; None to choose by size.
    chosen: str | None = None


@dataclass(frozen=True)
class Outcome:
    folder: Path
    relative_path: str
    size_bytes: int
    transfer: str
    job: Path
    #: The subtitle files placed next to the movie.
    subtitles: tuple[subtitle_placing.Placed, ...] = ()
    #: The rows of the old file's subtitles that left: into the recycle folder, or gone already.
    subtitles_gone: tuple[int, ...] = ()
    #: The media tool's answer, read before the movie moved; None: recording reads the filed movie.
    media_read: media.Read | None = None


def _claim(download_id: int) -> bool:
    with SessionLocal() as db:
        result = db.execute(
            update(Download)
            .where(Download.id == download_id, Download.state == "completed")
            .values(state="importing", updated_at=store.now())
        )
        db.commit()
    return bool(getattr(result, "rowcount", 0))


def _current(
    root_folder: str | None, relative_path: str | None
) -> tuple[Path | None, Path | None, tuple[str, ...]]:
    """The current file, the ``root_folder`` it lies below, and the folders of ``relative_path`` below that folder.

    The file counts when ``root_folder`` is a folder nexcrate sees and ``relative_path`` names a regular file in a
    folder below it (``Movie (2003)/Movie (2003).mkv``, or deeper when Radarr's folder format had a slash). Without the
    file its folders come back all the same; ``_file`` uses them again only when ``root_folder`` is the destination.
    """
    if not root_folder or not relative_path:
        return None, None, ()
    parts = [part for part in relative_path.replace("\\", "/").split("/") if part]
    if len(parts) < 2 or any(part in (".", "..") for part in parts):
        return None, None, ()
    try:
        root, _mount = folders.visible(root_folder)
    except folders.NotVisible:
        return None, None, ()
    candidate = root.joinpath(*parts)
    path = files.resolved(candidate)
    regular = path is not None and not files.is_link(candidate) and path.is_file()
    if path is not None and regular and files.strictly_inside(path.parent, root):
        return path, root, tuple(parts[:-1])
    return None, None, tuple(parts[:-1])


def _load(download_id: int) -> Context | None:
    with SessionLocal() as db:
        row = db.get(Download, download_id)
        if row is None:
            return None
        title = db.get(Title, row.title_id)
        client = db.get(DownloadClient, row.client_id) if row.client_id is not None else None
        version = store.version_of(db, row.title_id, row.version_definition_id)
        definition = db.get(VersionDefinition, row.version_definition_id) if row.version_definition_id else None
        target = None
        if client is not None:
            target = downloaders.Target(
                kind=client.kind,
                url=client.url,
                username=client.username or "",
                secret=crypto.decrypt(client.secret) if client.secret else "",
                category=client.category,
            )
        # The library rules of the version choose where a new title goes.
        folder = folder_rules.folder_for(db, definition, title)
        current: Path | None = None
        current_root: Path | None = None
        current_parts: tuple[str, ...] = ()
        if version is not None and version.has_file and (version.file_ref or "").startswith(KNOWN_FILE_REFS):
            current, current_root, current_parts = _current(version.root_folder, version.relative_path)
        current_subtitles = subtitle_records.current_rows(db, version) if version is not None and current else []
        chosen = db.scalar(
            select(DownloadFile.path).where(DownloadFile.download_id == row.id, DownloadFile.decision == "chosen")
        )
        return Context(
            download_id=row.id,
            title_id=row.title_id,
            definition_id=row.version_definition_id,
            protocol=row.protocol,
            client_download_id=row.client_download_id,
            reported_path=row.reported_path,
            mappings=[dict(mapping) for mapping in (client.path_mappings or [])] if client is not None else [],
            target=target,
            version_exists=version is not None,
            version_fed=version is not None and version.source_id is not None,
            version_folder=folder,
            current_file=current,
            current_folder_parts=current_parts,
            movie=naming.MovieFacts(
                title=title.title if title is not None else "",
                original_title=title.original_title if title is not None else None,
                year=title.year if title is not None else None,
                tmdb_id=title.tmdb_id if title is not None else None,
                imdb_id=title.imdb_id if title is not None else None,
            ),
            release=naming.ReleaseFacts(release_title=row.release_title, formats=tuple(row.formats or [])),
            naming=naming.for_version(db, definition),
            current_root=current_root,
            root_folder=version.root_folder if version is not None else None,
            subtitles_enabled=subtitle_settings.load_enabled(db),
            current_subtitles=tuple(current_subtitles),
            chosen=chosen,
        )


# --- The import ------------------------------------------------------------------------------------------ #


def _local_job(context: Context) -> Path:
    reported = context.reported_path
    if not reported:
        raise Problem("path_not_found", proposal=None)
    mapped = files.map_remote(reported, context.mappings)
    candidates: list[str | Path] = [mapped] if mapped is not None else []
    if files.remote_parts(reported) is not None:
        candidates.append(reported)
    for candidate in candidates:
        path = folders.visible_path(candidate)
        if path is not None:
            return path
    raise Problem("path_not_found", proposal=files.propose(reported, folders.mount_points()))


def writable_root(root_folder: str | None) -> Path | None:
    """The version's ``root_folder`` resolved, when nexcrate sees it and a file can be created in it; else None."""
    if not root_folder:
        return None
    try:
        root, _mount = folders.visible(root_folder)
    except folders.NotVisible:
        return None
    return root if folders.is_writable(root) else None


def destination(context: Context) -> Path:
    """The folder a version without a current file is filed and unpacked into (finding 13).

    Its ``root_folder`` when that is set, visible and writable: a taken-over movie without a file goes into the folder
    of its Radarr root folder. Otherwise the version folder, as before. Raises ``Problem``.
    """
    root = writable_root(context.root_folder)
    if root is not None:
        return root
    if not context.version_folder:
        raise Problem("import_failed", reason="no_folder")
    try:
        folder, _mount = folders.visible(context.version_folder)
    except folders.NotVisible as exc:
        raise Problem("import_failed", reason="folder_not_visible") from exc
    return folder


def file_away(context: Context) -> Outcome:
    """Everything on disk. Raises ``Problem`` or ``StillUnpacking``; changes nothing in the database."""
    if not context.version_exists or context.definition_id is None:
        raise Problem("import_failed", reason="version_gone")
    if context.version_fed:
        raise Problem("import_failed", reason="version_fed_by_source")
    if context.current_file is not None and context.current_root is not None:
        # An upgrade goes where the current file lies, as in Radarr; the old file into the recycle folder there.
        folder = context.current_root
    else:
        folder = destination(context)
    job = _local_job(context)
    if files.below_working_folder(job):
        raise StillUnpacking
    found = files.scan(job)
    if found.dangerous:
        raise Problem("dangerous_file")
    # Before unpacking: a job inside the destination would be unpacked into itself.
    if files.strictly_inside(folder, job) or files.inside(job, folder):
        raise Problem("import_failed", reason="source_in_version_folder")
    if not unpacking.wanted(found):
        if not found.videos:
            raise Problem("no_video")
        source = _pick(context, found, job, job)
        return _file(context, folder, job, job, source, videos=len(found.videos))
    # A clip next to the archives is a video of the download too: then only subtitles named after the movie belong.
    job_videos = len(found.videos)
    try:
        # Next to the movie's destination, so the video moves by rename on one file system.
        unpacked = unpacking.unpack(unpacking.archive_sets(found.archive_files), folder, context.download_id)
    except files.FileProblem as exc:
        raise Problem(exc.code, **exc.values) from exc
    try:
        found = files.scan(unpacked)
        if found.dangerous:
            raise Problem("dangerous_file")
        if not found.videos:
            raise Problem("no_video")
        source = _pick(context, found, job, unpacked)
        return _file(context, folder, job, unpacked, source, videos=job_videos + len(found.videos))
    finally:
        # The chosen video left by rename; the rest goes, and everything after a failure.
        unpacking.remove(folder, context.download_id)


def _named(path: Path, job: Path, origin: Path) -> str:
    """A video's path relative to the download, with the prefix of the unpack folder."""
    base = origin if origin.is_dir() else origin.parent
    try:
        text = path.relative_to(base).as_posix()
    except ValueError:
        text = path.name
    return (UNPACKED_PREFIX if origin != job else "") + text


def _pick(context: Context, found: files.Scan, job: Path, origin: Path) -> Path:
    """The video to file (decision 38): the owner's choice, else the largest one.

    Parts of one movie with different numbers refuse the download (``multi_part``); two videos where the largest is
    not twice the second ask the owner (``several_videos``).
    """
    if context.chosen is not None:
        for path, _size in found.videos:
            if _named(path, job, origin) == context.chosen:
                return path
        raise Problem("import_failed", reason="chosen_gone")
    if files.multi_part(found.videos):
        raise Problem("multi_part")
    if files.several(found.videos):
        problem = Problem("several_videos", count=len(found.videos))
        problem.candidates = [(_named(path, job, origin), size) for path, size in found.videos]
        raise problem
    return files.choose(found)


def _file(context: Context, folder: Path, job: Path, origin: Path, source: Path, videos: int = 1) -> Outcome:
    """The chosen video into its folder, then the subtitles.

    ``origin`` is the job folder, or the unpack folder the video came out of; ``videos`` how many videos the download
    holds.
    """
    unpacked = origin != job
    if not unpacked and files.below_working_folder(source):
        raise StillUnpacking
    if not files.inside(source, origin):
        raise Problem("import_failed", reason="source_outside")

    release = dataclasses.replace(context.release, original_filename=Path(source.name).stem)
    if context.current_file is not None:
        # The folder of the current file, however deep Radarr's folder format put it.
        target_dir = context.current_file.parent
    else:
        target_dir = folder / naming.folder_name(context.naming, context.movie)
        # The folders of a file that is gone, again, when they lie below this destination.
        if context.current_folder_parts and context.root_folder and files.resolved(context.root_folder) == folder:
            again = folder.joinpath(*context.current_folder_parts)
            if files.inside(again.parent, folder):
                target_dir = again
        try:
            target_dir.mkdir(exist_ok=True)
        except OSError as exc:
            raise Problem("import_failed", reason="folder_not_writable") from exc
    if files.is_link(target_dir) or not files.strictly_inside(target_dir, folder):
        raise Problem("import_failed", reason="destination_outside")
    target_dir = files.resolved(target_dir) or target_dir
    # The media data before the movie moves: the name carries the file's quality, codec and audio as Radarr's does
    # (the owner's answer of 17.09.2026), and recording stores the same without reading again.
    answer = _read_media(source)
    if answer.error_code == media.MEDIA_TRUNCATED:
        # Nothing has moved yet: a cut off movie is never filed and never replaces the one there.
        raise Problem("file_truncated")
    if answer.media is not None:
        decision = media.quality_of(context.release.release_title, target_dir.name, answer.media)
        release = dataclasses.replace(release, quality=decision.quality, media=answer.media)
    file_name = naming.file_name(context.naming, context.movie, release, source.suffix)
    target = target_dir / file_name
    if os.path.lexists(target) and (context.current_file is None or files.resolved(target) != context.current_file):
        raise Problem("import_failed", reason="destination_exists")

    # The marker a stop leaves: the next round takes the movie over where it arrived (``_adopt``).
    _mark_placing(context.download_id, _named(source, job, origin), source.stat().st_size, target.relative_to(folder))
    try:
        if unpacked:
            placed = files.place_unpacked(source, target_dir, file_name)
        else:
            placed = files.place(source, target_dir, file_name, protocol=context.protocol)
    except files.FileProblem as exc:
        raise Problem(exc.code, **exc.values) from exc
    except OSError as exc:
        raise Problem("import_failed", reason="transfer_failed") from exc
    recycled: Path | None = None
    try:
        if context.current_file is not None and os.path.lexists(context.current_file):
            recycled = files.recycle(context.current_file, folder, store.now())
        os.replace(placed.partial, target)
    except (OSError, files.FileProblem) as exc:
        if recycled is not None and context.current_file is not None:
            try:
                os.rename(recycled, context.current_file)
            except OSError:
                logger.warning("Download %d: the replaced movie stays in the recycle folder", context.download_id)
        files.undo(placed, source)
        reason = "transfer_failed"
        if isinstance(exc, files.FileProblem):
            reason = str(exc.values.get("reason", reason))
        raise Problem("import_failed", reason=reason) from exc
    if placed.copied and context.protocol == "usenet" and files.inside(source, job):
        try:
            source.unlink()
        except OSError:
            logger.warning("Download %d: the copied source file could not be deleted", context.download_id)
    # The movie is in place: the old file's subtitles follow the old file, then the new ones come. Neither fails it.
    gone = _recycle_subtitles(context, folder) if context.current_file is not None else ()
    subtitles = _place_subtitles(context, folder, job, origin, source, target, videos)
    return Outcome(
        folder=folder,
        relative_path=target.relative_to(folder).as_posix(),
        size_bytes=placed.size_bytes,
        transfer=placed.transfer,
        job=job,
        subtitles=subtitles,
        subtitles_gone=gone,
        media_read=answer,
    )


def _read_media(path: Path) -> media.Read:
    """The media tool's answer for one video; a file the tool does not read gives ``media_unreadable``."""
    return media.read(path) if media.readable(path) else media.Read(None, media.MEDIA_UNREADABLE)


# --- Subtitles (C9) ------------------------------------------------------------------------ #


def _recycle_subtitles(context: Context, folder: Path) -> tuple[int, ...]:
    """The recorded subtitles of the replaced file into the recycle folder, as the file went. Returns the rows that go.

    ⚠️ Never fails the import: the new movie is in place already. A subtitle that cannot be moved stays with its row.
    """
    if not context.current_subtitles:
        return ()
    try:
        recycled = subtitle_placing.recycle(context.current_subtitles, folder, store.now())
    except Exception:  # noqa: BLE001 - the movie is in place; its old subtitles must not undo the import
        logger.warning("Download %d: the subtitle files of the old file could not be handled", context.download_id)
        return ()
    if recycled.moved or recycled.missing:
        logger.info(
            "Download %d: %d subtitle files of the old file went into the recycle folder, %d were gone already",
            context.download_id,
            recycled.moved,
            recycled.missing,
        )
    if recycled.failed:
        logger.warning(
            "Download %d: %d subtitle files of the old file could not be moved and stay",
            context.download_id,
            recycled.failed,
        )
    return recycled.gone


def _place_subtitles(
    context: Context, folder: Path, job: Path, origin: Path, source: Path, target: Path, videos: int
) -> tuple[subtitle_placing.Placed, ...]:
    """With the switch on, the download's subtitle files that belong to the movie, next to it.

    ⚠️ Never fails the import: the movie is in place already. What fails or is past the limits is counted in the log.
    """
    if not context.subtitles_enabled:
        return ()
    origins = [(job, False)] if origin == job else [(job, False), (origin, True)]
    try:
        # A word equal to a release group never counts as a language: the release's group, and the video file's.
        names = (context.release.release_title, source.stem)
        groups = {group for name in names if (group := releases.parse(name).movie.group)}
        result = subtitle_placing.place(
            origins=origins,
            video_name=source.stem,
            videos=videos,
            groups=groups,
            target=target,
            folder=folder,
            protocol=context.protocol,
        )
    except Exception:  # noqa: BLE001 - the movie is in place; a subtitle must never undo its import
        logger.warning(
            "Download %d: the subtitle files could not be placed; the movie is imported", context.download_id
        )
        return ()
    if result.placed:
        logger.info("Download %d: %d subtitle files placed next to the movie", context.download_id, len(result.placed))
    if result.failed:
        logger.warning("Download %d: %d subtitle files could not be placed", context.download_id, result.failed)
    if result.skipped:
        logger.info(
            "Download %d: %d subtitle files skipped, more than %d files or larger than %d MB",
            context.download_id,
            result.skipped,
            subtitle_placing.MAX_FILES,
            subtitle_placing.MAX_BYTES // files.MIB,
        )
    return result.placed


def _record_problem(download_id: int, problem: Problem) -> None:
    moment = store.now()
    with SessionLocal() as db:
        row = db.get(Download, download_id)
        if row is None or row.state != "importing":
            return
        row.state, row.problem_code, row.problem_values = "problem", problem.code, dict(problem.values)
        row.updated_at = moment
        if problem.code in REFUSED:
            store.block(db, row, problem.code, moment)
            store.add_history(db, row, "failed", problem.code, moment)
            # With automatic loading on: the next fitting release, at most three times a day.
            replacement.after_failure(db, row, moment)
        if problem.candidates:
            db.execute(delete(DownloadFile).where(DownloadFile.download_id == row.id))
            for path, size in problem.candidates[: series_import.MAX_VIDEOS]:
                db.add(DownloadFile(download_id=row.id, path=path[:1024], size=size, decision="candidate"))
        store.follow(db, row, moment)
        db.commit()
    reason = problem.values.get("reason")
    logger.info(
        "Download %d could not be imported: %s%s", download_id, problem.code, f" ({reason})" if reason else ""
    )


def _back_to_completed(download_id: int) -> None:
    with SessionLocal() as db:
        db.execute(
            update(Download)
            .where(Download.id == download_id, Download.state == "importing")
            .values(state="completed", updated_at=store.now())
        )
        db.commit()
    logger.info("Download %d is still being unpacked; the next round tries again", download_id)


def _record_import(context: Context, outcome: Outcome) -> bool:
    moment = store.now()
    with SessionLocal() as db:
        row = db.get(Download, context.download_id)
        if row is None or row.state != "importing":
            logger.warning("Download %d changed while it was imported; the file stays in place", context.download_id)
            return False
        version = store.version_of(db, row.title_id, row.version_definition_id)
        version_id = version.id if version is not None and version.source_id is None else None
        replaced = None
        if version is not None and version.has_file:
            replaced = {"quality": version.quality, "size_bytes": version.size}
        if version is not None and version.source_id is None:
            version.has_file = True
            version.file_ref = f"nexcrate:{row.id}"
            version.quality = row.quality
            version.release_group = releases.parse(row.release_title).movie.group
            version.size = outcome.size_bytes
            version.relative_path = outcome.relative_path
            version.root_folder = str(outcome.folder)
            version.languages = list(row.languages or [])
            version.upgrade_to = None
            # The search judges the file by the name of the release it came from.
            version.release_title = (row.release_title or "")[:1024] or None
            version.source_movie_path = None
            version.updated_at = moment
            # The filed video's media data: the measured resolution decides the quality, known audio languages win
            # over the name's (decisions 22 to 24). The download keeps the release's.
            # Read before anything is written, so the write lock is not held while the tool runs.
            _apply_media(version, row, outcome, moment)
            # The old file's subtitles that left go, the placed ones come (C9).
            subtitle_records.record(db, version, row.id, outcome.subtitles, outcome.subtitles_gone, moment)
            # Judged by the profile as every stored file is (finding 15), not by ``below_target`` alone. After a flush:
            # it takes the write lock, so the profile is read as it stands when this commits.
            db.flush()
            version.cutoff_not_met = judging.judge(db, version)
        row.state, row.problem_code, row.problem_values = "imported", None, None
        row.imported_path = outcome.relative_path
        row.transfer = outcome.transfer
        row.imported_at = moment
        row.progress, row.remaining_seconds = 100.0, 0
        row.updated_at = moment
        detail = subtitle_records.imported_detail(row.quality, len(outcome.subtitles))
        written = {"quality": row.quality, "size_bytes": outcome.size_bytes, "replaced": replaced}
        store.add_history(db, row, "imported", detail, moment, written)
        db.execute(delete(DownloadFile).where(DownloadFile.download_id == row.id))
        store.follow_version(db, row.title_id, row.version_definition_id, moment)
        # A file that can still be upgraded: the next search is brought forward (C4).
        replacement.after_import(db, row, version, moment)
        db.commit()
    logger.info("Download %d imported by %s", context.download_id, outcome.transfer)
    if version_id is not None:
        _write_companion(context.download_id, version_id)
    return True


def _apply_media(version: Version, row: Download, outcome: Outcome, moment: datetime) -> None:
    """Store what the media tool says about the filed video on the version: read before it moved, else now (a movie
    taken over after a stop). Never raises."""
    parts = [part for part in outcome.relative_path.replace("\\", "/").split("/") if part]
    path = Path(outcome.folder).joinpath(*parts)
    folder_name = parts[-2] if len(parts) >= 2 else None
    result = outcome.media_read if outcome.media_read is not None else _read_media(path)
    if result.media is None:
        version.quality_from = "name"
        version.media_info, version.media_read_at = None, None
        logger.info("Download %d: no media data for the filed file (%s); the name decides", row.id, result.error_code)
        return
    decision = media.quality_of(row.release_title, folder_name, result.media)
    version.quality, version.quality_from = decision.quality, decision.quality_from
    version.media_info, version.media_read_at = result.media, moment
    version.languages = media.file_languages(result.media, list(row.languages or []))


def _movie_folder(outcome: Outcome) -> Path:
    """The folder the filed movie lies in: what a media server is told to read again."""
    parts = [part for part in outcome.relative_path.replace("\\", "/").split("/") if part]
    return Path(outcome.folder).joinpath(*parts).parent


def _write_companion(download_id: int, version_id: int) -> None:
    """The version's ``release.nex`` after filing away, after the commit (L2).

    ⚠️ Never fails the import: the movie is imported already. A write that fails is the version's ``companion_state``.
    """
    try:
        state = companions.write_version(version_id)
    except Exception:  # noqa: BLE001 - the movie is imported; release.nex must never undo that
        logger.warning("Download %d: writing release.nex failed unexpectedly; the movie is imported", download_id)
        return
    if state not in ("written", "current", companions.OFF):
        logger.info("Download %d: release.nex %s", download_id, state)


async def _clean_usenet(context: Context, outcome: Outcome) -> None:
    """A Usenet client (SABnzbd, NZBGet): the job folder goes, then the job leaves the history.

    A folder nexcrate may not delete stays, and so does the job, for the owner to look at.
    """
    target = context.target
    if target is None or not downloaders.is_usenet(target.kind):
        return
    folder = outcome.job if outcome.job.is_dir() else outcome.job.parent
    mount = folders.containing_mount(files.resolved(folder) or folder)
    removed = mount is not None and files.remove_job_folder(
        folder, category=target.category, mount=mount, keep=outcome.folder
    )
    if not removed:
        logger.info("Download %d: the job folder is not one to delete; it stays in the client", context.download_id)
        return
    async with downloaders.open_client(target) as client:
        await client.remove_imported(context.client_download_id)
    logger.info("Download %d: the job folder is deleted and the job left the client", context.download_id)


def _scope(download_id: int) -> str | None:
    with SessionLocal() as db:
        return db.scalar(select(Download.scope).where(Download.id == download_id))


def _mark_placing(download_id: int, named: str, size: int, target: Path) -> None:
    """Before the movie moves: a row ``placing`` with the video and its size, the target in ``imported_path``."""
    with SessionLocal() as db:
        row = db.get(Download, download_id)
        if row is None or row.state != "importing":
            return
        db.execute(_placing_rows(download_id))
        db.add(DownloadFile(download_id=download_id, path=named[:1024], size=size, decision="placing"))
        row.imported_path = target.as_posix()[:4096]
        row.updated_at = store.now()
        db.commit()


def _placing_rows(download_id: int) -> Any:
    return delete(DownloadFile).where(DownloadFile.download_id == download_id, DownloadFile.decision == "placing")


def _same_size(path: Path | None, size: int) -> bool:
    try:
        return path is not None and path.is_file() and not files.is_link(path) and path.stat().st_size == size
    except OSError:
        return False


def _adopt(context: Context) -> Outcome | None:
    """A round after a stop between moving the movie and recording it ("As built"): the movie is
    taken over where it arrived instead of being refused as ``destination_exists`` or lost as ``no_video``.

    The target has the video's size: it is the movie. Only the temporary name is there with that size and the video is
    gone from the job: the rename was done, it is finished. Otherwise a temporary copy goes and the round files as
    usual.
    """
    with SessionLocal() as db:
        row = db.get(Download, context.download_id)
        placing = db.scalar(
            select(DownloadFile).where(
                DownloadFile.download_id == context.download_id, DownloadFile.decision == "placing"
            )
        )
        if row is None or placing is None:
            return None
        marker, named, size = row.imported_path, placing.path, placing.size
    try:
        upgrade = context.current_file is not None and context.current_root is not None
        folder = context.current_root if upgrade else destination(context)
    except Problem:
        folder = None
    try:
        job: Path | None = _local_job(context)
    except Problem:
        job = None
    source: Path | None = None
    if job is not None and not named.startswith(UNPACKED_PREFIX):
        source = job.joinpath(*[part for part in named.split("/") if part]) if job.is_dir() else job
    source_there = source is not None and source.is_file()
    parts = [part for part in (marker or "").replace("\\", "/").split("/") if part]
    target = folder.joinpath(*parts) if folder is not None and parts else None
    if target is not None and folder is not None and files.inside(target.parent, folder):
        partial = target.with_name(target.name + files.PARTIAL_SUFFIX)
        if not _same_size(target, size) and _same_size(partial, size) and not source_there:
            try:
                os.replace(partial, target)
            except OSError:
                logger.info("Download %d: a temporary movie of a stopped round stays unfinished", context.download_id)
        if _same_size(target, size):
            if (
                context.current_file is not None
                and os.path.lexists(context.current_file)
                and files.resolved(context.current_file) != files.resolved(target)
            ):
                try:
                    files.recycle(context.current_file, folder, store.now())
                except (OSError, files.FileProblem):
                    logger.warning("Download %d: the replaced movie stays in place", context.download_id)
            gone = _recycle_subtitles(context, folder) if context.current_file is not None else ()
            subtitles: tuple[subtitle_placing.Placed, ...] = ()
            if job is not None:
                subtitles = _place_subtitles(context, folder, job, job, job / Path(named).name, target, 1)
            logger.info("Download %d: a movie placed before a stop is taken over", context.download_id)
            return Outcome(
                folder=folder,
                relative_path=target.relative_to(folder).as_posix(),
                size_bytes=size,
                transfer="hardlink" if context.protocol == "torrent" else "move",
                job=job or folder,
                subtitles=subtitles,
                subtitles_gone=gone,
            )
        if source_there and os.path.lexists(partial) and not files.is_link(partial):
            try:
                partial.unlink()
            except OSError:
                logger.info("Download %d: a temporary copy of a stopped round stays", context.download_id)
    with SessionLocal() as db:
        db.execute(_placing_rows(context.download_id))
        db.commit()
    return None


def run(download_id: int) -> None:
    """One import, in the calling thread. A series download is filed by ``series_import``, an album download by
    ``album_import``."""
    try:
        with rename_guard.filing(_title_of(download_id)):
            _run(download_id)
    except rename_guard.Busy:
        # The title's files are being renamed (decision 19): the next round files it.
        logger.info("Download %d waits: its title is being renamed", download_id)


def _title_of(download_id: int) -> int | None:
    with SessionLocal() as db:
        return db.scalar(select(Download.title_id).where(Download.id == download_id))


def _run(download_id: int) -> None:
    scope = _scope(download_id)
    if scope == store.ALBUM_SCOPE:
        album_import.run(download_id)
        return
    if scope is not None:
        series_import.run(download_id)
        return
    if not _claim(download_id):
        return
    context = _load(download_id)
    if context is None:
        return
    try:
        outcome = _adopt(context) or file_away(context)
    except StillUnpacking:
        _back_to_completed(download_id)
        return
    except Problem as problem:
        _record_problem(download_id, problem)
        return
    if not _record_import(context, outcome):
        return
    # After the commit, in a thread of its own: a media server never holds up or fails an import.
    mediaserver_notify.request("movie", [_movie_folder(outcome)])
    if context.protocol == "usenet" and outcome.job != outcome.folder:
        try:
            asyncio.run(_clean_usenet(context, outcome))
        except (downloaders.ClientError, OSError) as exc:
            logger.info("Download %d: cleaning up in the client failed: %s", download_id, type(exc).__name__)


# --- The recycle folders -------------------------------------------------------------------------------------- #


def _own_folders() -> list[Path]:
    """The folders nexcrate files into, resolved, where recycle and unpack folders lie.

    Every version folder, and every ``root_folder`` of a version no source feeds, with a file or without one: an upgrade
    of a file outside the version folder is unpacked and recycled there, and a version without a file is filed and
    unpacked into its ``root_folder`` (finding 13).
    """
    with SessionLocal() as db:
        paths = {folder for folder in db.scalars(select(VersionDefinition.folder)) if folder}
        roots = db.scalars(
            select(Version.root_folder)
            .where(Version.source_id.is_(None), Version.root_folder.is_not(None))
            .distinct()
        )
        paths |= {root for root in roots if root}
    found: list[Path] = []
    for path in sorted(paths):
        try:
            folder, _mount = folders.visible(path)
        except folders.NotVisible:
            continue
        if folder not in found:
            found.append(folder)
    return found


def clean_recycle() -> int:
    """The daily job: day folders of ``.nexcrate-recycle`` older than the recycle time go, in ``_own_folders``."""
    with SessionLocal() as db:
        days = recycle.load_days(db)
    removed = 0
    today = store.now()
    for folder in _own_folders():
        try:
            removed += files.clean_recycle(folder, today, days)
        except OSError:
            logger.warning("A recycle folder could not be cleaned")
    if removed:
        logger.info("%d old recycle folders deleted", removed)
    # The bin's rows whose file went with a day folder go too.
    from .. import recycle_bin

    recycle_bin.forget_missing()
    return removed
