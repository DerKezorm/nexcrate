"""The files of a finished download and where they go: choosing the video, the transfer, the recycle folder (step 3).

No database here: ``importing`` hands in the paths it has checked and gets back what happened.

* Walking a download never follows a link or a junction; a link is neither imported nor looked into.
* ⚠️ Dangerous (``.arj .lnk .lzh .ps1 .scr .vbs .zipx``) and executable (``.bat .cmd .exe .sh``) files are looked for
  everywhere in the download, left-out folders included: one of them refuses the whole download.
* Left out when choosing: dot folders and files starting with ``._``; ``@eadir``, ``.@__thumb`` and ``plex versions``;
  ``.DS_Store`` and ``Thumbs.db``; extras folders; names ending like a trailer; the word "sample" in a name. Videos by
  Radarr's extension list; the largest wins. Archives where a video would count are listed for ``unpacking``.
* A folder whose name starts with ``_UNPACK_`` or ``_FAILED_`` means the client still works on it.
* Every transfer lands under ``<file name>.nexcrate-partial`` first and is renamed afterwards; a copy is compared in
  size. A torrent is hardlinked, a Usenet download renamed; when that is impossible both are copied, and the Usenet
  source goes after the rename. A copy needs the file size plus 100 MB free.
* ⚠️ Every path written to or deleted is checked after resolving links: inside the version folder, or inside the job
  folder of the download.
* A path a client reports is a text in the client's world. ``map_remote`` turns it into a local path with the
  confirmed mappings; ``propose`` looks for the job's name below nexcrate's mount points.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("nexcrate.import")

MIB = 1024 * 1024
VIDEO_EXTENSIONS = frozenset(
    {
        ".webm", ".m4v", ".3gp", ".nsv", ".ty", ".strm", ".rm", ".rmvb", ".m3u", ".ifo", ".mov", ".qt", ".divx",
        ".xvid", ".bivx", ".nrg", ".pva", ".wmv", ".asf", ".asx", ".ogm", ".ogv", ".m2v", ".avi", ".bin", ".dat",
        ".dvr-ms", ".mpg", ".mpeg", ".mp4", ".avc", ".vp3", ".svq3", ".nuv", ".viv", ".dv", ".fli", ".flv", ".wpl",
        ".img", ".iso", ".vob", ".mkv", ".mk3d", ".ts", ".wtv", ".m2ts",
    }
)  # fmt: skip
#: Playlists and pointers among the video extensions: text of a few hundred bytes, never a video of their own. Every
#: music release carries an ``.m3u``; counted as a video, it kept the owner's album job folders and their SABnzbd jobs
#: after the import (22.09.2026).
POINTER_EXTENSIONS = frozenset({".m3u", ".wpl", ".asx", ".strm"})
#: Archives and their volumes, with ``.001`` of split 7z and zip archives and ``.r01`` to ``.r99`` of old RAR sets.
ARCHIVE_EXTENSIONS = (
    ".tar.bz2", ".tar.gz", ".7z", ".bz2", ".gz", ".r00", ".rar", ".tar", ".tb2", ".tbz2", ".tgz", ".zip", ".001",
    *(f".r{number:02d}" for number in range(1, 100)),
)  # fmt: skip
DANGEROUS_EXTENSIONS = frozenset({".arj", ".lnk", ".lzh", ".ps1", ".scr", ".vbs", ".zipx"})
EXECUTABLE_EXTENSIONS = frozenset({".bat", ".cmd", ".exe", ".sh"})
LEFT_OUT_FOLDERS = frozenset(
    {
        "@eadir", ".@__thumb", "plex versions", "extras", "extrafanart", "behind the scenes", "deleted scenes",
        "featurettes", "interviews", "other", "scenes", "sample", "samples", "shorts", "trailers",
    }
)  # fmt: skip
LEFT_OUT_FILES = frozenset({".ds_store", "thumbs.db"})
LEFT_OUT_ENDINGS = (
    "-trailer", "-other", "-behindthescenes", "-deleted", "-featurette", "-interview", "-scene", "-short",
)  # fmt: skip
WORKING_PREFIXES = ("_UNPACK_", "_FAILED_")
PARTIAL_SUFFIX = ".nexcrate-partial"
RECYCLE_FOLDER = ".nexcrate-recycle"
RECYCLE_DAYS = 7
SPACE_RESERVE_BYTES = 100 * MIB
#: A download with more entries than this is not walked further.
MAX_ENTRIES = 50_000
#: How deep ``propose`` looks below a mount point, and how many folders it visits at most.
PROPOSE_DEPTH = 6
PROPOSE_MAX_FOLDERS = 20_000
COPY_CHUNK = 8 * MIB
_SAMPLE = re.compile(r"(?<![a-z0-9])sample(?![a-z0-9])", re.IGNORECASE)
#: Extras in a series pack by name (decision 17).
_EXTRA_NAME = re.compile(
    r"(?<![a-z0-9])(?:extras?|featurettes?|bonus|behind[ ._-]?the[ ._-]?scenes)(?![a-z0-9])", re.IGNORECASE
)
#: A part of a movie split over several files: CD1, Part2, Disc1, pt2 (decision 38).
_PART = re.compile(r"(?<![a-z0-9])(?P<kind>cd|disc|disk|part|pt)[ ._-]?(?P<number>\d{1,2})(?![0-9])", re.IGNORECASE)
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
#: A volume of a split 7z or zip set: ``.7z.001``, ``.zip.002`` and so on.
_SPLIT_VOLUME = re.compile(r"\.(?:7z|zip)\.\d{3}$", re.IGNORECASE)


class FileProblem(Exception):
    """What went wrong, as a problem code of a download with its values."""

    def __init__(self, code: str, **values: Any) -> None:
        super().__init__(code)
        self.code = code
        self.values = values


# --- Paths -------------------------------------------------------------------------------------------------------- #


def resolved(path: str | Path) -> Path | None:
    try:
        return Path(os.path.realpath(path, strict=True))
    except (OSError, ValueError):
        return None


def is_link(path: Path) -> bool:
    try:
        return path.is_symlink() or path.is_junction()
    except OSError:
        return True


def inside(path: Path, folder: Path) -> bool:
    """Whether ``path`` is ``folder`` or lies in it, both resolved. ⚠️ A path that does not exist is not inside."""
    first, second = resolved(path), resolved(folder)
    return first is not None and second is not None and (first == second or first.is_relative_to(second))


def strictly_inside(path: Path, folder: Path) -> bool:
    first, second = resolved(path), resolved(folder)
    return first is not None and second is not None and first != second and first.is_relative_to(second)


def remote_parts(path: str) -> tuple[str, list[str]] | None:
    """A client's path as its root (``/``, ``C:`` or empty) and its names; None with ``..`` or a NUL in it."""
    if not path or "\x00" in path:
        return None
    text = path.strip()
    root = ""
    drive = re.match(r"^([A-Za-z]:)[\\/]", text)
    if drive:
        root, text = drive.group(1), text[3:]
    elif text.startswith(("/", "\\")):
        root = "/"
    names = [name for name in re.split(r"[\\/]", text) if name and name != "."]
    if any(name == ".." for name in names):
        return None
    return root, names


def join_remote(root: str, names: list[str]) -> str:
    if root and root != "/":
        return root + "\\" + "\\".join(names)
    return root + "/".join(names)


def map_remote(reported: str, mappings: list[dict[str, str]]) -> Path | None:
    """The local path of a reported path through the longest matching mapping, or None."""
    parts = remote_parts(reported)
    if parts is None:
        return None
    best: tuple[int, Path] | None = None
    for mapping in mappings:
        remote = remote_parts(str(mapping.get("remote") or ""))
        local = str(mapping.get("local") or "")
        if remote is None or not local or remote[0] != parts[0]:
            continue
        length = len(remote[1])
        if parts[1][:length] == remote[1] and (best is None or length > best[0]):
            best = (length, Path(local, *parts[1][length:]))
    return best[1] if best is not None else None


def below_working_folder(path: Path) -> bool:
    """A folder of the path starts with ``_UNPACK_`` or ``_FAILED_``: the client still works on it."""
    return any(part.upper().startswith(WORKING_PREFIXES) for part in path.parts)


def propose(reported: str, mounts: list[Path]) -> dict[str, str] | None:
    """A mapping for a reported path nexcrate does not see: its name found once below the mount points, or None.

    The proposal is the longest pair of prefixes that differ, with at least one name left in the remote one.
    """
    parts = remote_parts(reported)
    if parts is None or not parts[1]:
        return None
    root, names = parts
    wanted = names[-1]
    matches: list[Path] = []
    visited = 0
    for mount in mounts:
        stack: list[tuple[Path, int]] = [(mount, 0)]
        while stack and visited < PROPOSE_MAX_FOLDERS and len(matches) < 2:
            folder, depth = stack.pop()
            visited += 1
            try:
                entries = list(os.scandir(folder))
            except OSError:
                continue
            for entry in entries:
                if entry.name.startswith(".") or entry.is_symlink() or entry.is_junction():
                    continue
                path = Path(entry.path)
                if entry.name == wanted and path not in matches:
                    matches.append(path)
                if depth < PROPOSE_DEPTH and entry.is_dir(follow_symlinks=False):
                    stack.append((path, depth + 1))
    if len(matches) != 1:
        return None
    local_parts = list(matches[0].parts)
    same = 0
    while (
        same < len(names) - 1
        and same < len(local_parts) - 1
        and names[len(names) - 1 - same] == local_parts[len(local_parts) - 1 - same]
    ):
        same += 1
    return {
        "remote": join_remote(root, names[: len(names) - same]),
        "local": str(Path(*local_parts[: len(local_parts) - same])),
    }


# --- Choosing the file -------------------------------------------------------------------------------------------- #


def extension_of(name: str) -> str:
    lowered = name.lower()
    for compound in (".tar.bz2", ".tar.gz"):
        if lowered.endswith(compound):
            return compound
    return os.path.splitext(lowered)[1]


def is_archive(name: str) -> bool:
    """An archive or one of its volumes, split 7z and zip volumes such as ``.7z.002`` included."""
    return extension_of(name) in ARCHIVE_EXTENSIONS or _SPLIT_VOLUME.search(name) is not None


def is_sample(name: str) -> bool:
    return _SAMPLE.search(os.path.splitext(name)[0]) is not None


def _left_out_file(name: str) -> bool:
    lowered = name.lower()
    stem = os.path.splitext(lowered)[0]
    return lowered.startswith("._") or lowered in LEFT_OUT_FILES or stem.endswith(LEFT_OUT_ENDINGS) or is_sample(name)


def _left_out_folder(name: str) -> bool:
    return name.startswith(".") or name.lower() in LEFT_OUT_FOLDERS


@dataclass
class Scan:
    #: Candidate videos with their size.
    videos: list[tuple[Path, int]] = field(default_factory=list)
    dangerous: bool = False
    archives: bool = False
    #: Archives and volumes where a video would count (not in left-out folders, not named like a sample), with size.
    archive_files: list[tuple[Path, int]] = field(default_factory=list)
    #: Videos left out, with size and ``sample``, ``extra`` or ``extra_name`` (a series name like ``Bonus``, which still
    #: counts when it reads an episode).
    skipped_videos: list[tuple[Path, int, str]] = field(default_factory=list)
    #: Files looked at.
    entries: int = 0


def _skip_reason(name: str, *, candidate: bool, series: bool) -> str | None:
    """Why a video is left out: ``sample``, ``extra`` (folder or ending), ``extra_name`` (a name like ``Featurette``
    in a series download, which the episode reading may still keep); None for a video that counts."""
    if is_sample(name):
        return "sample"
    if not candidate or _left_out_file(name):
        return "extra"
    if series and _EXTRA_NAME.search(os.path.splitext(name)[0]):
        return "extra_name"
    return None


def _look_at(scan: Scan, path: Path, name: str, *, candidate: bool, series: bool = False) -> None:
    extension = extension_of(name)
    if extension in DANGEROUS_EXTENSIONS or extension in EXECUTABLE_EXTENSIONS:
        scan.dangerous = True
        return
    if is_archive(name):
        scan.archives = True
        if candidate and not _left_out_file(name):
            try:
                scan.archive_files.append((path, path.lstat().st_size))
            except OSError:
                logger.info("A file of a download could not be read")
        return
    if extension not in VIDEO_EXTENSIONS:
        return
    reason = _skip_reason(name, candidate=candidate, series=series)
    try:
        size = path.lstat().st_size
    except OSError:
        logger.info("A file of a download could not be read")
        return
    if reason is None:
        scan.videos.append((path, size))
    else:
        scan.skipped_videos.append((path, size, reason))


def scan(root: Path, *, series: bool = False) -> Scan:
    """Walk a download: dangerous files anywhere, archives, and the videos that may be imported. Links are skipped.

    ``series``: names of extras (``EXTRAS``, ``Featurette``) are left out too (decision 17).
    """
    result = Scan()
    if is_link(root):
        return result
    if root.is_file():
        result.entries = 1
        _look_at(result, root, root.name, candidate=True, series=series)
        return result
    seen = 0
    for folder, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(folder)
        relative = here.relative_to(root).parts if here != root else ()
        candidate = not any(_left_out_folder(part) for part in relative)
        dirnames[:] = [name for name in dirnames if not is_link(here / name)]
        for name in filenames:
            seen += 1
            result.entries = seen
            if seen > MAX_ENTRIES:
                logger.warning("A download has more than %d files; the rest is not looked at", MAX_ENTRIES)
                return result
            path = here / name
            if is_link(path):
                continue
            _look_at(result, path, name, candidate=candidate, series=series)
    return result


def choose(found: Scan) -> Path:
    """The largest video; of equal sizes the first by path."""
    return min(found.videos, key=lambda item: (-item[1], str(item[0])))[0]


def part_of(name: str) -> tuple[str, int] | None:
    """The part a video name says, as ``("cd", 1)``; ``disk`` counts as ``disc``, ``pt`` as ``part``."""
    found = _PART.search(os.path.splitext(name)[0])
    if found is None:
        return None
    kind = found.group("kind").lower()
    return {"disk": "disc", "pt": "part"}.get(kind, kind), int(found.group("number"))


def multi_part(videos: list[tuple[Path, int]]) -> bool:
    """At least two videos with the same kind of part and different numbers: a movie in pieces (decision 38)."""
    numbers: dict[str, set[int]] = {}
    for path, _size in videos:
        part = part_of(path.name)
        if part is not None:
            numbers.setdefault(part[0], set()).add(part[1])
    return any(len(found) >= 2 for found in numbers.values())


def several(videos: list[tuple[Path, int]]) -> bool:
    """No clear largest video: the largest is not at least twice the second (decision 38)."""
    if len(videos) < 2:
        return False
    sizes = sorted((size for _path, size in videos), reverse=True)
    return sizes[0] < 2 * sizes[1]


# --- Transfer ----------------------------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Placed:
    partial: Path
    #: hardlink, copy or move.
    transfer: str
    size_bytes: int
    #: The source was copied, not linked or renamed: a Usenet source is deleted after the rename.
    copied: bool


def ensure_space(folder: Path, size: int) -> None:
    try:
        free = shutil.disk_usage(folder).free
    except OSError:
        return
    if free < size + SPACE_RESERVE_BYTES:
        raise FileProblem("no_space", needed_bytes=size + SPACE_RESERVE_BYTES, free_bytes=int(free))


def _copy(source: Path, partial: Path, size: int) -> None:
    try:
        with source.open("rb") as reader, partial.open("xb") as writer:
            shutil.copyfileobj(reader, writer, COPY_CHUNK)
    except OSError as exc:
        _remove_partial(partial)
        raise FileProblem("import_failed", reason="copy_failed") from exc
    if partial.stat().st_size != size:
        _remove_partial(partial)
        raise FileProblem("import_failed", reason="size_mismatch")


def _remove_partial(partial: Path) -> None:
    try:
        if partial.is_file() and not is_link(partial) and partial.name.endswith(PARTIAL_SUFFIX):
            partial.unlink()
    except OSError:
        logger.warning("A temporary file of an import could not be removed")


def place(source: Path, target_dir: Path, file_name: str, *, protocol: str, link: bool = True) -> Placed:
    """The source under the temporary name in the target folder: hardlink or copy for a torrent, rename or copy else.

    ``link=False``: a torrent is copied, never linked; its file gets tags written (decision 18).
    """
    partial = _free_partial(target_dir, file_name)
    size = source.stat().st_size
    if protocol == "torrent" and not link:
        ensure_space(target_dir, size)
        _copy(source, partial, size)
        return Placed(partial, "copy", size, copied=True)
    if protocol == "torrent":
        try:
            os.link(source, partial)
            return Placed(partial, "hardlink", size, copied=False)
        except OSError:
            logger.info("A hardlink is not possible here; the file is copied")
        ensure_space(target_dir, size)
        _copy(source, partial, size)
        return Placed(partial, "copy", size, copied=True)
    try:
        os.rename(source, partial)
        return Placed(partial, "move", size, copied=False)
    except OSError:
        logger.info("A rename is not possible here; the file is copied and the source deleted afterwards")
    ensure_space(target_dir, size)
    _copy(source, partial, size)
    return Placed(partial, "move", size, copied=True)


def _free_partial(target_dir: Path, file_name: str) -> Path:
    """The temporary name in the target folder; a file left there by an earlier try goes, anything else refuses."""
    partial = target_dir / (file_name + PARTIAL_SUFFIX)
    if os.path.lexists(partial):
        if partial.is_file() and not is_link(partial):
            partial.unlink()
        else:
            raise FileProblem("import_failed", reason="destination_exists")
    return partial


def place_unpacked(source: Path, target_dir: Path, file_name: str) -> Placed:
    """An unpacked video under the temporary name: renamed, as the unpack folder lies in the folder it is filed into.

    Copied only when a rename is impossible, for example a movie folder on a file system of its own.
    """
    partial = _free_partial(target_dir, file_name)
    size = source.stat().st_size
    try:
        os.rename(source, partial)
        return Placed(partial, "unpacked", size, copied=False)
    except OSError:
        logger.info("A rename is not possible here; the unpacked file is copied")
    ensure_space(target_dir, size)
    _copy(source, partial, size)
    return Placed(partial, "unpacked", size, copied=True)


def undo(placed: Placed, source: Path) -> None:
    """After a failure between placing and the rename: a renamed source goes back, anything else placed goes."""
    try:
        if placed.transfer == "move" and not placed.copied:
            os.rename(placed.partial, source)
        else:
            _remove_partial(placed.partial)
    except OSError:
        logger.warning("An import could not be undone; the file stays under its temporary name")


def recycle(old_file: Path, version_folder: Path, moment: datetime) -> Path:
    """Move a version's old file into ``.nexcrate-recycle/<date>/`` of its version folder, keeping its folder name."""
    old, folder = resolved(old_file), resolved(version_folder)
    if old is None or folder is None or old == folder or not old.is_relative_to(folder):
        raise FileProblem("import_failed", reason="recycle_outside")
    root = folder / RECYCLE_FOLDER / moment.strftime("%Y-%m-%d")
    target = root / old.relative_to(folder)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not strictly_inside(target.parent, folder):
        raise FileProblem("import_failed", reason="recycle_outside")
    counter = 2
    while os.path.lexists(target):
        target = target.with_name(f"{old.stem} ({counter}){old.suffix}")
        counter += 1
    os.rename(old, target)
    return target


def clean_recycle(version_folder: Path, today: datetime, days: int = RECYCLE_DAYS) -> int:
    """Delete the day folders of ``.nexcrate-recycle`` older than ``days`` days. Returns how many went."""
    folder = resolved(version_folder)
    root = folder / RECYCLE_FOLDER if folder is not None else None
    if root is None or not root.is_dir() or is_link(root) or not strictly_inside(root, folder):
        return 0
    limit = (today - timedelta(days=days)).date()
    removed = 0
    for entry in os.scandir(root):
        if not _DAY.fullmatch(entry.name):
            continue
        try:
            day = date.fromisoformat(entry.name)
        except ValueError:
            continue
        path = Path(entry.path)
        if day >= limit or entry.is_symlink() or entry.is_junction() or not entry.is_dir(follow_symlinks=False):
            continue
        if not strictly_inside(path, root):
            continue
        shutil.rmtree(path)
        removed += 1
    return removed


def remove_job_folder(
    folder: Path, *, category: str, mount: Path, keep: Path, known: Collection[Path] = ()
) -> bool:
    """Delete a SABnzbd job folder after its import. Returns whether it went.

    Only a folder that is a direct child of a folder named like the category, lies strictly inside a mount point, holds
    no other video (samples do not count) and does not hold ``keep``, the version folder. ``known`` are videos of this
    download it did not use, which a series download may leave (decision 30).
    ⚠️ Never through a link: ``rmtree`` removes links inside without following them.
    """
    if is_link(folder) or not folder.is_dir():
        return False
    job = resolved(folder)
    if job is None or job.parent.name.casefold() != category.casefold():
        return False
    if not strictly_inside(job, mount) or inside(keep, job):
        return False
    own = {resolved(path) for path in known}
    videos = [path for path, _size in scan(job).videos if path.suffix.lower() not in POINTER_EXTENSIONS]
    if any(resolved(path) not in own for path in videos):
        return False
    shutil.rmtree(job)
    return True
