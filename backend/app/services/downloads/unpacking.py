"""Unpacking a download that holds archives instead of a video (T3).

* **When** (``wanted``): a download holds no video but archives, or its archives together are larger than its largest
  video. Only archives where a video would count: not in left-out folders, not named like a sample.
* **First volumes** (``archive_sets``): of ``name.partN.rar`` only part 1; otherwise ``name.rar``; ``name.r00`` only
  without ``name.rar``; ``name.7z.001`` and ``name.zip.001``; ``.7z``, ``.zip``, ``.tar``, ``.tar.gz``, ``.tgz``,
  ``.tar.bz2``, ``.tbz2``. Every other volume belongs to its first in the same folder. Volumes without their first are
  ``packed`` with ``incomplete``; nothing to unpack at all (a single ``.gz``) is ``unsupported``. Several first volumes
  (CD1 and CD2) go one after another into the same folder.
* **The listing first**, for every set before anything is written: encrypted is the problem ``encrypted``; a dangerous
  or executable name ``dangerous_file``; an absolute name, a ``..`` part, a link or anything but files and folders
  ``packed`` with ``unsafe``; an archive inside ``nested``; more than 1000 files, or more than 5 times the size of a
  set's volumes, ``too_large``; less free space than the unpacked size plus 100 MB ``no_space``.
* **Extracting** into ``<destination>/.nexcrate-unpack/<download id>/``, the destination being the folder the movie is
  filed into, so the video moves by rename. Each listing and each extraction is one child process (``unpack_worker``):
  stdin closed, no terminal, an argument list, a small environment, and 2 minutes plus 1 minute per GB of the set's
  volumes. On a timeout the child and the tools it started are killed (``broken``). While a tool writes, the folder is
  measured: past the limits it is stopped as well (``too_large``), because a listing can lie.
* **Afterwards** the folder is walked without following links: a link, a file with a second hard link, anything but a
  file or folder, a path outside is ``unsafe``; an archive ``nested``; then the limits once more.
* ⚠️ After every failure and timeout the unpack folder goes: every tool leaves partial files.
* ``step_of`` tells the download answer that a download is being unpacked; in memory, only while unpacking.
* Logs carry counts and codes, never names or paths.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Collection, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import files

logger = logging.getLogger("nexcrate.import")

UNPACK_FOLDER = ".nexcrate-unpack"
STEP_UNPACKING = "unpacking"
MAX_FILES = 1000
MAX_RATIO = 5
GIB = 1024**3
TIMEOUT_BASE_SECONDS = 120
TIMEOUT_PER_GIB_SECONDS = 60
#: How often a running child is looked at: its timeout, and the size of what it wrote.
POLL_SECONDS = 0.5
WORKER = Path(__file__).with_name("unpack_worker.py")
#: The reasons of the problem ``packed``.
REASONS = ("unsupported", "incomplete", "broken", "unsafe", "nested", "too_large")
_TOOL_REASONS = ("unsupported", "incomplete", "broken", "unsafe")
_KINDS = ("file", "dir", "link", "other")

_PART_RAR = re.compile(r"^(?P<base>.+)\.part(?P<number>\d+)\.rar$")
_OLD_VOLUME = re.compile(r"^(?P<base>.+)\.r(?P<number>\d{2})$")
_SPLIT = re.compile(r"^(?P<base>.+\.(?:7z|zip))\.(?P<number>\d{3})$")
_TAR_ENDINGS = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2")
_DRIVE = re.compile(r"^[A-Za-z]:")

_steps: dict[int, str] = {}
_steps_lock = threading.Lock()


def packed(reason: str) -> files.FileProblem:
    return files.FileProblem("packed", reason=reason)


# --- What to unpack ------------------------------------------------------------------------------------------ #


@dataclass(frozen=True)
class ArchiveSet:
    #: rar, 7z, zip or tar: which way the child unpacks it.
    kind: str
    first: Path
    volumes: tuple[Path, ...]
    size_bytes: int


def wanted(found: files.Scan) -> bool:
    """No video but archives, or archives larger together than the largest video (a clip next to the packed movie)."""
    if not found.archive_files:
        return False
    if not found.videos:
        return True
    return sum(size for _path, size in found.archive_files) > max(size for _path, size in found.videos)


def _sets_in_folder(entries: dict[str, tuple[Path, int]]) -> tuple[list[ArchiveSet], bool]:
    """The sets of one folder, by names in lower case, and whether volumes lack their first."""
    claimed: set[str] = set()
    sets: list[ArchiveSet] = []
    lost = False

    def make(kind: str, names: list[str]) -> None:
        claimed.update(names)
        chosen = [entries[name] for name in names]
        sets.append(ArchiveSet(kind, chosen[0][0], tuple(path for path, _size in chosen), sum(s for _p, s in chosen)))

    def numbered(pattern: re.Pattern[str]) -> dict[str, dict[int, str]]:
        groups: dict[str, dict[int, str]] = {}
        for name in sorted(entries):
            match = pattern.match(name)
            if match is not None and name not in claimed:
                groups.setdefault(match["base"], {})[int(match["number"])] = name
        return groups

    for numbers in numbered(_PART_RAR).values():
        if 1 in numbers:
            make("rar", [numbers[number] for number in sorted(numbers)])
        else:
            claimed.update(numbers.values())
            lost = True
    old = numbered(_OLD_VOLUME)
    for name in sorted(entries):
        if name not in claimed and name.endswith(".rar"):
            volumes = old.pop(name[: -len(".rar")], {})
            make("rar", [name, *(volumes[number] for number in sorted(volumes))])
    for numbers in old.values():
        # name.r00 is the first volume only when there is no name.rar.
        if 0 in numbers:
            make("rar", [numbers[number] for number in sorted(numbers)])
        else:
            claimed.update(numbers.values())
            lost = True
    for numbers in numbered(_SPLIT).values():
        if 1 in numbers:
            make("7z", [numbers[number] for number in sorted(numbers)])
        else:
            claimed.update(numbers.values())
            lost = True
    for name in sorted(entries):
        if name in claimed:
            continue
        if name.endswith(".7z"):
            make("7z", [name])
        elif name.endswith(".zip"):
            make("zip", [name])
        elif name.endswith(_TAR_ENDINGS):
            make("tar", [name])
    return sets, lost


def archive_sets(archives: Iterable[tuple[Path, int]]) -> list[ArchiveSet]:
    """The sets to unpack, in the order of their first volumes. Raises ``FileProblem`` ``packed``."""
    by_folder: dict[Path, dict[str, tuple[Path, int]]] = {}
    for path, size in archives:
        by_folder.setdefault(path.parent, {})[path.name.lower()] = (path, size)
    found: list[ArchiveSet] = []
    lost = False
    for entries in by_folder.values():
        sets, missing_first = _sets_in_folder(entries)
        found.extend(sets)
        lost = lost or missing_first
    if lost:
        raise packed("incomplete")
    if not found:
        raise packed("unsupported")
    return sorted(found, key=lambda item: (str(item.first.parent).lower(), item.first.name.lower()))


def timeout_for(size_bytes: int) -> float:
    return TIMEOUT_BASE_SECONDS + TIMEOUT_PER_GIB_SECONDS * max(0, size_bytes) / GIB


# --- The child process ----------------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Answer:
    #: The child's JSON, None when it gave none (killed, crashed, not started).
    data: dict[str, Any] | None
    timed_out: bool = False
    #: The reason ``watch`` gave for stopping the child.
    stopped: str | None = None


def worker_command(action: str, kind: str, archive: Path, folder: Path | None = None) -> list[str]:
    """``python -I <unpack_worker.py> ...``: by its path and isolated, so nothing of nexcrate is imported."""
    command = [sys.executable, "-I", str(WORKER), action, kind, str(archive)]
    if folder is not None:
        command.append(str(folder))
    return command


def _environment() -> dict[str, str]:
    """A small environment: nexcrate's own variables, its secret key among them, never reach a tool."""
    environment = {"PATH": os.environ.get("PATH", os.defpath), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
    for name in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR"):
        if name in os.environ:
            environment[name] = os.environ[name]
    return environment


def _kill(process: subprocess.Popen[bytes]) -> None:
    """The child and, on Linux, every tool in its process group. Only while the child is not reaped yet."""
    killpg = getattr(os, "killpg", None)
    if killpg is not None:
        with contextlib.suppress(OSError):
            killpg(process.pid, getattr(signal, "SIGKILL", 9))
    with contextlib.suppress(OSError):
        process.kill()
    try:
        process.communicate(timeout=10)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        logger.warning("An unpacking process did not end after it was killed")


def _answer(output: bytes) -> dict[str, Any] | None:
    for line in reversed(output.decode("utf-8", "replace").splitlines()):
        if line.strip().startswith("{"):
            try:
                value = json.loads(line)
            except ValueError:
                return None
            return value if isinstance(value, dict) else None
    return None


def run_worker(command: list[str], *, timeout: float, watch: Callable[[], str | None] | None = None) -> Answer:
    """Run one child and read its answer. ⚠️ On the timeout, or when ``watch`` names a reason, the child is killed.

    The child gets no stdin and no terminal (a new session on Linux, which also puts its tools into one process group).
    """
    options: dict[str, Any] = {}
    if os.name == "posix":
        options["start_new_session"] = True
    else:
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_environment(),
            **options,
        )
    except OSError:
        logger.warning("The unpacking process could not be started")
        return Answer(None)
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        try:
            output, _errors = process.communicate(timeout=max(0.05, min(POLL_SECONDS, remaining)))
            return Answer(_answer(output or b""))
        except subprocess.TimeoutExpired:
            if time.monotonic() >= deadline:
                _kill(process)
                return Answer(None, timed_out=True)
            reason = watch() if watch is not None else None
            if reason is not None:
                _kill(process)
                return Answer(None, stopped=reason)


def _refused(data: dict[str, Any] | None) -> files.FileProblem | None:
    """The problem of a child's answer, None when it did its work."""
    if data is None:
        return packed("broken")
    if data.get("ok") is True:
        return None
    reason = data.get("reason")
    if reason == "encrypted":
        return files.FileProblem("encrypted")
    return packed(reason if reason in _TOOL_REASONS else "broken")


# --- The listing ----------------------------------------------------------------------------------------------- #


def step_of(download_id: int) -> str | None:
    """``unpacking`` while the download is being unpacked, else None."""
    with _steps_lock:
        return _steps.get(download_id)


@contextlib.contextmanager
def _stepping(download_id: int) -> Iterator[None]:
    with _steps_lock:
        _steps[download_id] = STEP_UNPACKING
    try:
        yield
    finally:
        with _steps_lock:
            _steps.pop(download_id, None)


@dataclass(frozen=True)
class Listing:
    #: (name, size, kind), kind being file, dir, link or other.
    entries: tuple[tuple[str, int, str], ...]
    encrypted: bool
    more: bool


def _log_refused(download_id: int, action: str, data: dict[str, Any] | None) -> None:
    """Codes only: the reason and the class of the exception, never the child's names."""
    reason = data.get("reason") if data else None
    error = data.get("error") if data else None
    logger.info(
        "Download %d: an archive could not be %s (%s, %s)",
        download_id,
        action,
        reason if reason in (*_TOOL_REASONS, "encrypted") else "no answer",
        error if isinstance(error, str) and re.fullmatch(r"[A-Za-z0-9_]{1,64}", error) else "-",
    )


def unsafe_name(name: str) -> bool:
    """An absolute name, a drive, a ``..`` part, a NUL, or no name at all."""
    text = name.replace("\\", "/")
    if not text.strip("/") or "\x00" in text or text.startswith("/") or _DRIVE.match(text):
        return True
    return ".." in text.split("/")


def _list(archive: ArchiveSet, download_id: int) -> Listing:
    answer = run_worker(worker_command("list", archive.kind, archive.first), timeout=timeout_for(archive.size_bytes))
    if answer.timed_out:
        logger.info("Download %d: listing an archive took too long and was stopped", download_id)
    problem = _refused(answer.data)
    if problem is not None:
        _log_refused(download_id, "listed", answer.data)
        raise problem
    data = answer.data or {}
    entries: list[tuple[str, int, str]] = []
    for entry in data.get("entries") or []:
        if not (isinstance(entry, list) and len(entry) == 3 and isinstance(entry[0], str)):
            raise packed("broken")
        if not isinstance(entry[1], int) or entry[2] not in _KINDS:
            raise packed("broken")
        entries.append((entry[0], entry[1], entry[2]))
    return Listing(tuple(entries), data.get("encrypted") is True, data.get("more") is True)


def _check(listing: Listing, archive: ArchiveSet) -> tuple[int, int]:
    """The files and bytes a set gives. Raises ``FileProblem``: encrypted, dangerous, unsafe, nested, size, in order."""
    if listing.encrypted:
        raise files.FileProblem("encrypted")
    dangerous = unsafe = nested = False
    count = size = 0
    for name, entry_size, kind in listing.entries:
        base = name.replace("\\", "/").rsplit("/", 1)[-1]
        extension = files.extension_of(base)
        if kind != "dir" and (extension in files.DANGEROUS_EXTENSIONS or extension in files.EXECUTABLE_EXTENSIONS):
            dangerous = True
        if unsafe_name(name) or kind in ("link", "other"):
            unsafe = True
        if kind == "file":
            nested = nested or files.is_archive(base)
            count += 1
            size += max(0, entry_size)
    if dangerous:
        raise files.FileProblem("dangerous_file")
    if unsafe:
        raise packed("unsafe")
    if nested:
        raise packed("nested")
    if listing.more or count > MAX_FILES or size > MAX_RATIO * archive.size_bytes:
        raise packed("too_large")
    return count, size


# --- The unpack folder ------------------------------------------------------------------------------------------ #


def _unlink(path: Path) -> None:
    """A file, a link or a Windows junction itself, never what it points to."""
    try:
        os.unlink(path)
    except (IsADirectoryError, PermissionError):
        os.rmdir(path)


def _open_up(folder: Path) -> None:
    """Owner rights on every folder below: an archive may leave read-only folders nothing could be deleted from."""
    for current, dirnames, _names in os.walk(folder, followlinks=False):
        for name in dirnames:
            path = Path(current, name)
            if not files.is_link(path):
                with contextlib.suppress(OSError):
                    os.chmod(path, stat.S_IRWXU)


def _delete(path: Path, root: Path) -> None:
    """One entry below ``root``: a folder with everything in it without following links, or a file or link itself."""
    if not os.path.lexists(path):
        return
    if files.is_link(path) or not path.is_dir():
        _unlink(path)
        return
    if files.strictly_inside(path, root):
        _open_up(path)
        shutil.rmtree(path)


def _drop_if_empty(root: Path) -> None:
    with contextlib.suppress(OSError):
        root.rmdir()


def _unpack_root(destination: Path) -> Path | None:
    """``.nexcrate-unpack`` in the destination, when it is a real folder there."""
    root = destination / UNPACK_FOLDER
    if files.is_link(root) or not root.is_dir() or not files.strictly_inside(root, destination):
        return None
    return root


def remove(destination: Path, download_id: int) -> None:
    """The unpack folder of a download goes, and ``.nexcrate-unpack`` with it when nothing else is left in there."""
    try:
        root = _unpack_root(destination)
        if root is None:
            return
        _delete(root / str(download_id), root)
        _drop_if_empty(root)
    except OSError:
        logger.warning("Download %d: its unpack folder could not be deleted", download_id)


def clear_leftovers(destinations: Iterable[Path], keep: Collection[int] = ()) -> int:
    """At start: every unpack folder in these folders goes, except those of imports running now. Returns how many."""
    removed = 0
    for destination in destinations:
        try:
            root = _unpack_root(destination)
            if root is None:
                continue
            for entry in sorted(root.iterdir()):
                if entry.name.isdigit() and int(entry.name) in keep:
                    continue
                _delete(entry, root)
                removed += 1
            _drop_if_empty(root)
        except OSError:
            logger.warning("A leftover unpack folder could not be deleted")
    return removed


def _prepare(destination: Path, download_id: int) -> Path:
    try:
        (destination / UNPACK_FOLDER).mkdir(exist_ok=True)
    except OSError as exc:
        raise files.FileProblem("import_failed", reason="folder_not_writable") from exc
    root = _unpack_root(destination)
    if root is None:
        raise files.FileProblem("import_failed", reason="destination_outside")
    folder = root / str(download_id)
    try:
        # A leftover of an earlier round goes first.
        _delete(folder, root)
        folder.mkdir()
    except OSError as exc:
        raise files.FileProblem("import_failed", reason="folder_not_writable") from exc
    return folder


# --- Extracting ---------------------------------------------------------------------------------------------------- #


def _over_limits(folder: Path, allowed_bytes: int) -> str | None:
    """Asked while a tool writes: more files or bytes than the archives may give."""
    count = total = 0
    for current, _dirnames, names in os.walk(folder, followlinks=False):
        for name in names:
            try:
                total += os.lstat(os.path.join(current, name)).st_size
            except OSError:
                continue
            count += 1
            if count > MAX_FILES or total > allowed_bytes:
                return "too_large"
    return None


def check_folder(folder: Path, allowed_bytes: int) -> None:
    """The unpack folder after extracting, walked without following links. Raises ``FileProblem`` ``packed``."""
    root = files.resolved(folder)
    if root is None:
        raise packed("broken")
    count = total = 0
    for current, dirnames, names in os.walk(root, followlinks=False):
        here = Path(current)
        for name in dirnames:
            if files.is_link(here / name) or not files.strictly_inside(here / name, root):
                raise packed("unsafe")
        for name in names:
            path = here / name
            try:
                info = path.lstat()
            except OSError as exc:
                raise packed("broken") from exc
            regular = stat.S_ISREG(info.st_mode) and not files.is_link(path)
            if not regular or info.st_nlink > 1 or not files.strictly_inside(path, root):
                raise packed("unsafe")
            if files.is_archive(name):
                raise packed("nested")
            count += 1
            total += info.st_size
    if count > MAX_FILES or total > allowed_bytes:
        raise packed("too_large")


def _extract(archive: ArchiveSet, folder: Path, allowed_bytes: int, download_id: int) -> None:
    answer = run_worker(
        worker_command("extract", archive.kind, archive.first, folder),
        timeout=timeout_for(archive.size_bytes),
        watch=lambda: _over_limits(folder, allowed_bytes),
    )
    if answer.stopped is not None:
        logger.info("Download %d: unpacking wrote more than the archive may give and was stopped", download_id)
        raise packed(answer.stopped if answer.stopped in REASONS else "too_large")
    if answer.timed_out:
        logger.info("Download %d: unpacking took too long and was stopped", download_id)
    problem = _refused(answer.data)
    if problem is not None:
        _log_refused(download_id, "unpacked", answer.data)
        raise problem


def unpack(sets: list[ArchiveSet], destination: Path, download_id: int) -> Path:
    """Every set into the download's unpack folder in ``destination``, which is returned.

    Raises ``FileProblem``. ⚠️ After any failure the unpack folder is gone.
    """
    with _stepping(download_id):
        total_files = total_bytes = 0
        for archive in sets:
            count, size = _check(_list(archive, download_id), archive)
            total_files += count
            total_bytes += size
            if total_files > MAX_FILES:
                raise packed("too_large")
        files.ensure_space(destination, total_bytes)
        try:
            folder = _prepare(destination, download_id)
            allowed_bytes = 0
            for archive in sets:
                allowed_bytes += MAX_RATIO * archive.size_bytes
                _extract(archive, folder, allowed_bytes, download_id)
            check_folder(folder, allowed_bytes)
        except BaseException:
            remove(destination, download_id)
            raise
    logger.info("Download %d: %d archives unpacked", download_id, len(sets))
    return folder
