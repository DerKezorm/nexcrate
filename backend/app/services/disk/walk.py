"""Walking a scanned folder (L4, "Walking").

* The root is listed with ``os.scandir``, never following a link or junction. A directory that is no link, not hidden
  and not a left-out name is a candidate; a regular video file is a file without a folder; anything else is ignored.
* Left-out names, compared without case: every name with a leading dot, Radarr's list, the NAS names Jellyfin and Plex
  leave out, and ``plex versions``.
* A candidate holds videos by the rules of ``files.scan`` (left-out folders, samples, trailer endings), largest first,
  at most 20 stored; its ``release.nex`` is read through ``companions.read``; its ``.nfo`` files are read for numbers
  with ``defusedxml``. A ``BDMV`` or ``VIDEO_TS`` subfolder makes it a disc folder. No video of its own but subfolders
  with videos: a group folder, and its subfolders are candidates, one level only.
* At most 50,000 candidates per root; a folder that cannot be listed is ``unreadable``; names are kept as on disk.
* ⚠️ ``release.nex`` and ``.nfo`` are input from outside: size limits, ``defusedxml`` only, numbers checked, no path
  from a file is ever followed.
"""

from __future__ import annotations

import logging
import os
import re
import stat
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from defusedxml import ElementTree as SafeElementTree

from .. import companions, schreibweisen
from ..downloads import files
from ..releases import parser as release_parser

logger = logging.getLogger("nexcrate.disk")

#: Compared without case. Every dot name is left out as well.
LEFT_OUT_NAMES = frozenset(
    {
        "$recycle.bin", "system volume information", "recycler", "lost+found", "@eadir", "eadir", "#recycle",
        "@recycle", "#snapshot", "plex versions",
    }
)  # fmt: skip
DISC_FOLDERS = frozenset({"bdmv", "video_ts"})
MAX_CANDIDATES = 50_000
MAX_VIDEOS = 20
NFO_MAX_BYTES = 64 * 1024
NFO_MAX_FILES = 5
_TMDB_MAX = 2147483647
_IMDB_TEXT = re.compile(r"(?<![a-z0-9])(tt\d{7,10})(?!\d)", re.IGNORECASE)
_TMDB_URL = re.compile(r"themoviedb\.org/movie/(\d{1,10})", re.IGNORECASE)
#: Number tags in a folder name, in any brackets or bare, taken out before the title is read.
_NUMBER_TAGS = re.compile(
    r"[\[\{\(]\s*(?:tmdb(?:id)?|imdb(?:id)?)\s*[-=:_ ]\s*[a-z0-9]+\s*[\]\}\)]"
    r"|(?<![a-z0-9])(?:tmdb(?:id)?)\s*[-=:_ ]\s*\d+(?![0-9])"
    r"|(?<![a-z0-9])tt\d{7,10}(?![0-9])",
    re.IGNORECASE,
)


@dataclass
class Numbers:
    """A TMDB and an IMDb number with where each came from (``name`` or ``nfo``)."""

    tmdb_id: int | None = None
    tmdb_from: str | None = None
    imdb_id: str | None = None
    imdb_from: str | None = None

    def take(self, other: Numbers) -> None:
        if self.tmdb_id is None and other.tmdb_id is not None:
            self.tmdb_id, self.tmdb_from = other.tmdb_id, other.tmdb_from
        if self.imdb_id is None and other.imdb_id is not None:
            self.imdb_id, self.imdb_from = other.imdb_id, other.imdb_from

    @property
    def empty(self) -> bool:
        return self.tmdb_id is None and self.imdb_id is None

    def out(self) -> dict[str, Any]:
        return {
            "tmdb_id": self.tmdb_id,
            "tmdb_from": self.tmdb_from,
            "imdb_id": self.imdb_id,
            "imdb_from": self.imdb_from,
        }


@dataclass
class Candidate:
    """One entry below a root before matching."""

    #: The folder below the root as on disk, ``group/movie`` for a movie folder inside a group folder.
    relative_path: str
    #: ``folder``, ``file``, ``disc``.
    kind: str
    path: Path
    #: ``file``, ``disc``, ``no_video``, ``unreadable``, or None for a movie folder the matching decides.
    fixed_state: str | None = None
    #: Largest first, at most 20: name, size_bytes, modified_at, name_quality.
    videos: list[dict[str, Any]] = field(default_factory=list)
    #: Every video name found, for the library check, in NFC.
    video_names: set[str] = field(default_factory=set)
    #: Sizes by NFC name.
    video_sizes: dict[str, int] = field(default_factory=dict)
    companion: companions.ReadResult | None = None
    nfo: Numbers = field(default_factory=Numbers)
    parsed_title: str | None = None
    parsed_year: int | None = None

    @property
    def name(self) -> str:
        return self.relative_path.replace("\\", "/").rsplit("/", 1)[-1]

    @property
    def largest(self) -> dict[str, Any] | None:
        return self.videos[0] if self.videos else None


@dataclass
class Walked:
    candidates: list[Candidate] = field(default_factory=list)
    truncated: bool = False


def left_out(name: str) -> bool:
    return name.startswith(".") or name.casefold() in LEFT_OUT_NAMES


def _is_link(entry: os.DirEntry[str]) -> bool:
    try:
        return entry.is_symlink() or entry.is_junction()
    except OSError:
        return True


def _entries(folder: Path) -> list[os.DirEntry[str]] | None:
    try:
        with os.scandir(folder) as scanner:
            return sorted(scanner, key=lambda entry: entry.name)
    except OSError, ValueError:
        return None


def _video_file(entry: os.DirEntry[str]) -> bool:
    try:
        if _is_link(entry) or not entry.is_file(follow_symlinks=False):
            return False
    except OSError:
        return False
    return files.extension_of(entry.name) in files.VIDEO_EXTENSIONS and not files._left_out_file(entry.name)


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def video_entry(path: Path, size: int) -> dict[str, Any]:
    try:
        modified = _iso(path.lstat().st_mtime)
    except OSError:
        modified = None
    return {
        "name": path.name,
        "size_bytes": int(size),
        "modified_at": modified,
        "name_quality": release_parser.parse_movie(path.name).quality.name,
    }


def _fill_videos(candidate: Candidate, found: list[tuple[Path, int]]) -> None:
    ordered = sorted(found, key=lambda item: (-item[1], str(item[0])))
    candidate.videos = [video_entry(path, size) for path, size in ordered[:MAX_VIDEOS]]
    for path, size in ordered:
        key = schreibweisen.nfc(path.name)
        candidate.video_names.add(key)
        candidate.video_sizes.setdefault(key, int(size))


def _subfolders(entries: list[os.DirEntry[str]]) -> list[os.DirEntry[str]]:
    result = []
    for entry in entries:
        if left_out(entry.name) or entry.name.casefold() in files.LEFT_OUT_FOLDERS or _is_link(entry):
            continue
        try:
            if entry.is_dir(follow_symlinks=False):
                result.append(entry)
        except OSError:
            continue
    return result


def _has_disc(entries: list[os.DirEntry[str]]) -> bool:
    for entry in entries:
        try:
            if entry.name.casefold() in DISC_FOLDERS and not _is_link(entry) and entry.is_dir(follow_symlinks=False):
                return True
        except OSError:
            continue
    return False


# --- .nfo -------------------------------------------------------------------------------------------------- #


def _tmdb_number(text: str | None) -> int | None:
    value = (text or "").strip()
    if not value.isdigit():
        return None
    number = int(value)
    return number if 1 <= number <= _TMDB_MAX else None


def _imdb_number(text: str | None) -> str | None:
    value = (text or "").strip()
    return value if re.fullmatch(r"tt\d{7,10}", value, re.IGNORECASE) else None


def nfo_numbers(raw: bytes) -> Numbers:
    """The TMDB and IMDb numbers of a ``.nfo`` text: XML elements, or a search of a text that is no XML."""
    found = Numbers()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    root = None
    try:
        root = SafeElementTree.fromstring(text.strip())
    except Exception:  # noqa: BLE001 - defusedxml raises its own family of errors for forbidden and broken XML
        root = None
    if root is not None:
        for element in root.iter():
            tag = str(element.tag).rsplit("}", 1)[-1].casefold()
            value = element.text
            if tag == "uniqueid":
                kind = str(element.attrib.get("type", "")).casefold()
                if kind == "tmdb" and found.tmdb_id is None:
                    found.tmdb_id, found.tmdb_from = _tmdb_number(value), "nfo"
                elif kind == "imdb" and found.imdb_id is None:
                    found.imdb_id, found.imdb_from = _imdb_number(value), "nfo"
            elif tag == "tmdbid" and found.tmdb_id is None:
                found.tmdb_id, found.tmdb_from = _tmdb_number(value), "nfo"
            elif tag in ("imdbid", "imdb", "id") and found.imdb_id is None:
                found.imdb_id, found.imdb_from = _imdb_number(value), "nfo"
        if found.tmdb_id is None:
            found.tmdb_from = None
        if found.imdb_id is None:
            found.imdb_from = None
        if not found.empty:
            return found
    imdb = _IMDB_TEXT.search(text)
    if imdb:
        found.imdb_id, found.imdb_from = imdb.group(1)[:2].lower() + imdb.group(1)[2:], "nfo"
    tmdb = _TMDB_URL.search(text)
    number = _tmdb_number(tmdb.group(1)) if tmdb else None
    if number is not None:
        found.tmdb_id, found.tmdb_from = number, "nfo"
    return found


def read_nfo_files(folder: Path, entries: list[os.DirEntry[str]]) -> Numbers:
    """The numbers of the folder's ``.nfo`` files, at most five, each at most 64 KiB, the first number of each kind."""
    found = Numbers()
    read = 0
    for entry in entries:
        if not entry.name.casefold().endswith(".nfo") or _is_link(entry):
            continue
        try:
            info = entry.stat(follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_size > NFO_MAX_BYTES:
                continue
            raw = (folder / entry.name).read_bytes()
        except OSError:
            continue
        read += 1
        found.take(nfo_numbers(raw))
        if read >= NFO_MAX_FILES or (found.tmdb_id is not None and found.imdb_id is not None):
            break
    return found


# --- Candidates -------------------------------------------------------------------------------------------- #


def inspect(candidate: Candidate, installation: str, *, group_allowed: bool) -> list[Candidate]:
    """Fill a folder candidate: videos, ``release.nex``, ``.nfo`` numbers, disc, or its children for a group folder.

    Returns the candidates that stand for it: itself, or the movie folders of a group folder.
    """
    entries = _entries(candidate.path)
    if entries is None:
        candidate.fixed_state = "unreadable"
        return [candidate]
    if _has_disc(entries):
        candidate.kind, candidate.fixed_state = "disc", "disc"
        _fill_videos(candidate, files.scan(candidate.path).videos)
        candidate.companion = companions.read(candidate.path, installation)
        return [candidate]
    scanned = files.scan(candidate.path)
    own = [item for item in scanned.videos if item[0].parent == candidate.path]
    if not own and scanned.videos and group_allowed:
        children: list[Candidate] = []
        for entry in _subfolders(entries):
            child = Candidate(
                relative_path=f"{candidate.relative_path}/{entry.name}", kind="folder", path=candidate.path / entry.name
            )
            children.extend(inspect(child, installation, group_allowed=False))
        return children
    if scanned.videos:
        _fill_videos(candidate, scanned.videos)
    else:
        candidate.fixed_state = "no_video"
    candidate.companion = companions.read(candidate.path, installation)
    candidate.nfo = read_nfo_files(candidate.path, entries)
    _parse_names(candidate)
    return [candidate]


def strip_numbers(name: str) -> str:
    """The name without its TMDB and IMDb tags, so the title read from it does not carry them."""
    return " ".join(_NUMBER_TAGS.sub(" ", name).split()) or name


def _parse_names(candidate: Candidate) -> None:
    parsed = release_parser.parse_movie(strip_numbers(candidate.name))
    title, year = parsed.title, parsed.year
    largest = candidate.largest
    if largest is not None and (year is None or not title):
        from_video = release_parser.parse_movie(strip_numbers(str(largest["name"])))
        year = year if year is not None else from_video.year
        title = title or from_video.title
    candidate.parsed_title = (title or None) and title[:512]
    candidate.parsed_year = year


def walk(root: Path, installation: str, excluded: set[Path]) -> Walked:
    """Every candidate below a root, inspected. ``excluded`` holds other roots inside this one, left out."""
    result = Walked()
    entries = _entries(root)
    if entries is None:
        return result
    for entry in entries:
        if left_out(entry.name) or _is_link(entry):
            continue
        path = root / entry.name
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
        except OSError:
            continue
        if is_dir:
            if path in excluded:
                continue
            first = Candidate(relative_path=entry.name, kind="folder", path=path)
            found = inspect(first, installation, group_allowed=True)
            found = [item for item in found if item.path not in excluded]
        elif _video_file(entry):
            try:
                size = entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
            candidate = Candidate(relative_path=entry.name, kind="file", path=path, fixed_state="file")
            _fill_videos(candidate, [(path, size)])
            _parse_names(candidate)
            found = [candidate]
        else:
            continue
        for item in found:
            if len(result.candidates) >= MAX_CANDIDATES:
                result.truncated = True
                logger.warning("A scanned folder holds more than %d candidates; the rest is not listed", MAX_CANDIDATES)
                return result
            result.candidates.append(item)
    return result
