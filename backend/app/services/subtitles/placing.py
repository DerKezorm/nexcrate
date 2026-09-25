"""The subtitle files of a download next to its movie, and the old file's into the recycle folder (step 3c, C9).

No database here: ``importing`` hands in the paths it has checked and records what came back.

* **Where:** only in the download's own folder (a job folder or a torrent's folder; a download that is one file holds
  no subtitle) and in its unpack folder, walked without following links, leaving out the folders and files choosing the
  video leaves out (``Sample``, extras, dot folders, names with "sample") and folders the client still works in.
* **Limits:** at most 50 files of at most 10 MB each, in the order of their paths; the rest is skipped and counted.
* **Which belong:** a file whose name starts with the chosen video's name, case ignored; any other one when the
  download holds exactly one video (samples and extras do not count).
* **One subtitle:** a single file, or an ``.idx`` and a ``.sub`` of the same name in the same folder, with one name.
* **Names** (see ``names``): subtitles whose name starts with the video's name first, then by path. A name that an
  earlier subtitle of the same import or any file in the movie folder already has gets the next number, from 2 on.
* **Transfer** as the video's: from a torrent hardlinked, else copied; from Usenet renamed, else copied and the source
  deleted; out of the unpack folder renamed. First under ``<name>.nexcrate-partial``, then renamed without replacing
  anything. A subtitle that fails is taken back and counted, and never fails the movie.
* **Upgrade:** ``recycle`` moves recorded subtitles of the old file into the recycle folder. A link, anything but a
  regular file, or a path outside the folder stays where it is.
* ⚠️ Every source must lie in its folder after resolving links, every name is one plain file name, and nothing already in
  the movie folder is ever replaced.
"""

from __future__ import annotations

import errno
import logging
import os
import stat
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..downloads import files
from . import names

logger = logging.getLogger("nexcrate.import")

MAX_FILES = 50
MAX_BYTES = 10 * files.MIB
#: The highest copy number tried; past it the subtitle counts as failed.
MAX_COPY = 99
PAIR = (".idx", ".sub")


@dataclass(frozen=True)
class Candidate:
    path: Path
    #: The job folder or the unpack folder it was found in.
    origin: Path
    #: Out of the unpack folder: renamed, never linked.
    unpacked: bool
    #: The place in the order of the paths.
    order: int


@dataclass(frozen=True)
class Found:
    candidates: tuple[Candidate, ...] = ()
    #: Files past the limits.
    skipped: int = 0


@dataclass(frozen=True)
class Subtitle:
    #: One file, or an ``.idx`` and its ``.sub``.
    files: tuple[Candidate, ...]
    reading: names.Reading
    #: The name starts with the chosen video's name.
    prefixed: bool


@dataclass(frozen=True)
class Placed:
    #: Relative to the folder the movie was filed into, as ``versions.relative_path``.
    relative_path: str
    language: str | None
    forced: bool
    sdh: bool


@dataclass(frozen=True)
class Result:
    placed: tuple[Placed, ...] = ()
    #: Files that could not be placed.
    failed: int = 0
    #: Files past the limits.
    skipped: int = 0


@dataclass(frozen=True)
class Recycled:
    #: The rows that go: moved into the recycle folder, or not there any more.
    gone: tuple[int, ...] = ()
    moved: int = 0
    missing: int = 0
    #: Files that stay where they are.
    failed: int = 0


# --- Finding ----------------------------------------------------------------------------------------------------- #


def _stem(name: str) -> str:
    extension = files.extension_of(name)
    return name[: -len(extension)] if extension else name


def _left_out_folder(name: str) -> bool:
    return files._left_out_folder(name) or name.upper().startswith(files.WORKING_PREFIXES)


def _walk(origin: Path) -> list[tuple[str, Path]]:
    """The subtitle files below one folder with their path relative to it, sorted; links and left-out entries not."""
    if files.is_link(origin) or not origin.is_dir():
        return []
    found: list[tuple[str, Path]] = []
    seen = 0
    for folder, dirnames, filenames in os.walk(origin, followlinks=False):
        here = Path(folder)
        dirnames[:] = [name for name in dirnames if not _left_out_folder(name) and not files.is_link(here / name)]
        for name in filenames:
            seen += 1
            if seen > files.MAX_ENTRIES:
                return sorted(found, key=lambda item: item[0].casefold())
            if files.extension_of(name) not in names.EXTENSIONS or files._left_out_file(name):
                continue
            path = here / name
            if not files.is_link(path):
                found.append((path.relative_to(origin).as_posix(), path))
    return sorted(found, key=lambda item: item[0].casefold())


def find(origins: Sequence[tuple[Path, bool]], max_files: int = MAX_FILES) -> Found:
    """The subtitle files within the limits. ``origins``: each folder, and whether it is the unpack folder."""
    candidates: list[Candidate] = []
    skipped = 0
    for origin, unpacked in origins:
        for _relative, path in _walk(origin):
            try:
                info = path.lstat()
            except OSError:
                skipped += 1
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES or len(candidates) >= max_files:
                skipped += 1
                continue
            candidates.append(Candidate(path, origin, unpacked, len(candidates)))
    return Found(tuple(candidates), skipped)


def _grouped(candidates: Sequence[Candidate]) -> list[tuple[Candidate, ...]]:
    """Each subtitle's files: an ``.idx`` with the ``.sub`` of the same name in the same folder, else one file."""
    pairs: dict[tuple[Path, str], list[Candidate]] = {}
    for candidate in candidates:
        if files.extension_of(candidate.path.name) in PAIR:
            key = (candidate.path.parent, _stem(candidate.path.name).casefold())
            pairs.setdefault(key, []).append(candidate)
    grouped: list[tuple[Candidate, ...]] = []
    taken: set[int] = set()
    for members in pairs.values():
        if sorted(files.extension_of(member.path.name) for member in members) == list(PAIR):
            grouped.append(tuple(sorted(members, key=lambda member: files.extension_of(member.path.name))))
            taken.update(member.order for member in members)
    grouped.extend((candidate,) for candidate in candidates if candidate.order not in taken)
    return sorted(grouped, key=lambda members: min(member.order for member in members))


def plan(candidates: Sequence[Candidate], *, video_name: str, videos: int, groups: Collection[str]) -> list[Subtitle]:
    """The subtitles that belong to the movie, in the order they are named. ``video_name`` without its extension."""
    chosen: list[Subtitle] = []
    for members in _grouped(candidates):
        stem = _stem(members[0].path.name)
        # A subtitle in a folder named like the video belongs to it: Subs/<video>/2_German.srt (S4, decision 29). Not
        # the download's own folder, which is often named like its video.
        first = members[0]
        named = first.path.parent.name.casefold() == video_name.casefold()
        in_named_folder = named and first.path.parent != first.origin
        prefixed = in_named_folder or names.without_prefix(stem, video_name) is not None
        if not prefixed and videos != 1:
            continue
        chosen.append(Subtitle(members, names.read(stem, video_name=video_name, groups=groups), prefixed))
    return sorted(chosen, key=lambda subtitle: (not subtitle.prefixed, min(item.order for item in subtitle.files)))


# --- Placing ----------------------------------------------------------------------------------------------------- #


def _names_for(subtitle: Subtitle, movie_name: str, target_dir: Path, taken: set[str]) -> dict[str, str] | None:
    """A name per extension, free in this import and in the movie folder; None when no number up to 99 is free."""
    extensions = sorted({files.extension_of(item.path.name) for item in subtitle.files})
    for copy in (None, *range(2, MAX_COPY + 1)):
        chosen = {extension: names.file_name(movie_name, subtitle.reading, extension, copy) for extension in extensions}
        if all(name.casefold() not in taken and not os.path.lexists(target_dir / name) for name in chosen.values()):
            taken.update(name.casefold() for name in chosen.values())
            return chosen
    return None


def _check(candidate: Candidate, name: str, target_dir: Path) -> None:
    """⚠️ The source lies in its folder after resolving links; the name is one plain file name in the target folder."""
    if files.is_link(candidate.path) or not files.inside(candidate.path, candidate.origin):
        raise files.FileProblem("import_failed", reason="source_outside")
    plain = bool(name) and name not in (".", "..") and not any(mark in name for mark in ("/", "\\", "\x00"))
    if not plain or (target_dir / name).parent != target_dir:
        raise files.FileProblem("import_failed", reason="destination_outside")


def rename_without_replacing(partial: Path, target: Path) -> None:
    """``partial`` under the name ``target``. ⚠️ A file already there raises ``FileExistsError`` and stays as it is.

    A second hard link under the new name, then the temporary name goes: that never replaces a file on any system.
    Where the file system has no hard links, the name is checked and the file renamed.
    """
    try:
        os.link(partial, target)
    except FileExistsError:
        raise
    except OSError:
        if os.path.lexists(target):
            raise FileExistsError(errno.EEXIST, "the name is taken") from None
        os.rename(partial, target)
        return
    try:
        os.unlink(partial)
    except OSError:
        logger.warning("A temporary subtitle file could not be removed")


def _take_back(candidate: Candidate, moved: files.Placed, target: Path | None) -> None:
    """Undo one file after a failure: under its temporary name (``target`` None), or already under its final name."""
    if target is None:
        files.undo(moved, candidate.path)
        return
    try:
        if moved.transfer == "move" and not moved.copied:
            os.rename(target, candidate.path)
        else:
            os.unlink(target)
    except OSError:
        logger.warning("A subtitle file could not be taken back after a failure")


def _transfer(subtitle: Subtitle, chosen: dict[str, str], target_dir: Path, protocol: str) -> list[Path]:
    """Every file of one subtitle under its name. Raises; after a failure nothing of it is left in the target folder."""
    staged: list[tuple[Candidate, files.Placed, Path]] = []
    renamed = 0
    try:
        for candidate in subtitle.files:
            name = chosen[files.extension_of(candidate.path.name)]
            _check(candidate, name, target_dir)
            if candidate.unpacked:
                moved = files.place_unpacked(candidate.path, target_dir, name)
            else:
                moved = files.place(candidate.path, target_dir, name, protocol=protocol)
            staged.append((candidate, moved, target_dir / name))
        for _candidate, moved, target in staged:
            rename_without_replacing(moved.partial, target)
            renamed += 1
    except BaseException:
        for index, (candidate, moved, target) in enumerate(staged):
            _take_back(candidate, moved, target if index < renamed else None)
        raise
    for candidate, moved, _target in staged:
        if moved.copied and protocol == "usenet" and not candidate.unpacked:
            try:
                candidate.path.unlink()
            except OSError:
                logger.warning("A copied subtitle source could not be deleted")
    return [target for _candidate, _moved, target in staged]


def place(
    *,
    origins: Sequence[tuple[Path, bool]],
    video_name: str,
    videos: int,
    groups: Collection[str],
    target: Path,
    folder: Path,
    protocol: str,
    max_files: int = MAX_FILES,
    per_video: int = MAX_FILES,
) -> Result:
    """The download's subtitles that belong to the movie ``target``, next to it.

    ``origins``: the job folder and, after unpacking, the unpack folder. ``video_name``: the chosen video's name without
    its extension. ``videos``: how many videos the download holds. ``folder``: the folder the movie was filed into, for
    the relative paths.
    """
    found = find(origins, max_files)
    subtitles = plan(found.candidates, video_name=video_name, videos=videos, groups=groups)[:per_video]
    target_dir = target.parent
    movie_name = Path(target.name).stem
    taken = {target.name.casefold()}
    placed: list[Placed] = []
    failed = 0
    for subtitle in subtitles:
        chosen = _names_for(subtitle, movie_name, target_dir, taken)
        if chosen is None:
            failed += len(subtitle.files)
            continue
        try:
            written = _transfer(subtitle, chosen, target_dir, protocol)
        except OSError, files.FileProblem:
            failed += len(subtitle.files)
            continue
        reading = subtitle.reading
        placed.extend(
            Placed(path.relative_to(folder).as_posix(), reading.language, reading.forced, reading.sdh)
            for path in written
        )
    return Result(tuple(placed), failed, found.skipped)


# --- Upgrade ------------------------------------------------------------------------------------------------------ #


def recycle(rows: Iterable[tuple[int, str]], folder: Path, moment: datetime) -> Recycled:
    """Recorded subtitles of an old file, ``(id, path relative to folder)``, into the recycle folder of ``folder``."""
    gone: list[int] = []
    moved = missing = failed = 0
    for row_id, relative in rows:
        parts = relative.replace("\\", "/").split("/")
        if not relative or any(part in ("", ".", "..") for part in parts):
            failed += 1
            continue
        path = folder.joinpath(*parts)
        if not os.path.lexists(path):
            gone.append(row_id)
            missing += 1
            continue
        if files.is_link(path) or not path.is_file() or not files.strictly_inside(path, folder):
            failed += 1
            continue
        try:
            files.recycle(path, folder, moment)
        except OSError, files.FileProblem:
            failed += 1
            continue
        gone.append(row_id)
        moved += 1
    return Recycled(tuple(gone), moved, missing, failed)
