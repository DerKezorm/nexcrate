"""``release.nex`` next to every movie nexcrate owns (L1 and L2).

One file per movie folder, visible, JSON, with the numbers and facts a rebuild needs: the TMDB and IMDb number, the
title's name and year, and one entry per version in that folder. It never names a path above the folder, a key, an
address, a user name, a profile, a score or media data. A random installation id tells two installations apart.

Reading: the file is input from outside; whoever can write to the share can write it. Size limit, JSON only, duplicate
keys refused, numbers and names checked; no path from the file is followed. Outcomes: ``ours``, ``other_installation``,
``newer_format``, ``broken``, ``foreign``, ``missing``.

Writing: built from the database only, for a version nexcrate owns (no source, a file, a ``file_ref`` of nexcrate's),
into the folder that holds the version's video, strictly inside a visible root folder. The existing file is read first;
only a file of this installation, unchanged since nexcrate wrote it, is merged and overwritten. Written under a
temporary dot name, created exclusively, synced, then renamed over the old file. One writer per folder in the process.
Nothing here raises to the caller: a failure becomes the version's ``companion_state``.

Log lines carry ids, counts and codes, never titles, names or paths.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import stat
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from .. import __version__
from ..db import SessionLocal, get_setting, set_setting
from ..models import Download, HistoryEntry, Title, Version, VersionDefinition, utcnow
from . import folders, schreibweisen
from .downloads import files
from .releases import parser as release_parser
from .releases import qualities
from .subtitles import records as subtitle_records

logger = logging.getLogger("nexcrate.companions")

FILE_NAME = "release.nex"
#: The fallback of decision 1, should a media server react to the visible name. Not used unless the bench says so.
DOT_FILE_NAME = ".release.nex"
PARTIAL_NAME = ".release.nex.nexcrate-partial"
FORMAT = "nexcrate-release"
FORMAT_VERSION = 1
MAX_BYTES = 64 * 1024
MAX_ENTRIES = 10
SETTING_INSTALLATION = "installation_id"
SETTING_ENABLED = "companions_enabled"
#: ``file_ref`` prefixes of files nexcrate owns: filed away by itself, taken over from Radarr, found on disk.
OWN_FILE_REFS = ("nexcrate:", "taken:", "disk:")

# Read outcomes.
OURS = "ours"
OTHER_INSTALLATION = "other_installation"
NEWER_FORMAT = "newer_format"
BROKEN = "broken"
FOREIGN = "foreign"
MISSING = "missing"

# Version states (``versions.companion_state``).
STATES = (
    "written",
    "current",
    "missing",
    "outdated",
    "other_installation",
    "newer_format",
    "broken",
    "foreign",
    "changed",
    "not_writable",
    "no_space",
    "folder_missing",
    "file_missing",
    "failed",
)
#: States "replace" may act on (decision 5).
REPLACEABLE = frozenset({FOREIGN, BROKEN, NEWER_FORMAT, OTHER_INSTALLATION, "changed"})
#: What is written when nothing is wrong: the answer of ``write_version`` when the switch is off.
OFF = "off"

QUALITY_FROM = ("name", "media", "radarr")
CAME_FROM_KINDS = ("download", "takeover", "disk")
_INSTALLATION_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"
_INSTALLATION_RE = re.compile(r"^[a-z2-7]{16}$")
_IMDB_RE = re.compile(r"^tt\d{7,10}$")
_TMDB_MAX = 2147483647
_TEXT_MAX = 1024

_locks_guard = threading.Lock()
_locks: dict[Path, threading.Lock] = {}


def _lock_for(folder: Path) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(folder)
        if lock is None:
            lock = _locks[folder] = threading.Lock()
        return lock


# --- Settings ------------------------------------------------------------------------------ #


def installation_id(db: OrmSession | None = None) -> str:
    """The random id of this installation, created once and kept in the settings. No secret."""
    if db is None:
        with SessionLocal() as session:
            return installation_id(session)
    stored = get_setting(db, SETTING_INSTALLATION)
    if _INSTALLATION_RE.match(stored):
        return stored
    value = "".join(secrets.choice(_INSTALLATION_ALPHABET) for _ in range(16))
    set_setting(db, SETTING_INSTALLATION, value)
    db.commit()
    return value


def enabled(db: OrmSession) -> bool:
    """The switch "Begleitdateien schreiben", on by default (decision 10)."""
    return get_setting(db, SETTING_ENABLED, "1") != "0"


def set_enabled(db: OrmSession, value: bool) -> None:
    """Stage the switch; the caller commits."""
    set_setting(db, SETTING_ENABLED, "1" if value else "0")


# --- Reading ------------------------------------------------------------------------------- #


class _DuplicateKey(ValueError):
    pass


def _pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: set[str] = set()
    for key, _value in pairs:
        if key in seen:
            raise _DuplicateKey(key)
        seen.add(key)
    return dict(pairs)


class _Invalid(ValueError):
    pass


def _text(value: Any, limit: int = _TEXT_MAX, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise _Invalid("text")
    text = schreibweisen.nfc(value)
    if "\x00" in text:
        raise _Invalid("text")
    if required and not text.strip():
        raise _Invalid("text")
    return text[:limit]


def _int(value: Any, low: int, high: int, *, required: bool = False) -> int | None:
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise _Invalid("int")
    return value


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise _Invalid("bool")
    return value


def _file_name(value: Any) -> str:
    name = _text(value, required=True)
    assert name is not None
    if "/" in name or "\\" in name or name in (".", "..") or ".." in name.split("/"):
        raise _Invalid("file")
    return name


def _time(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 40:
        raise _Invalid("time")
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise _Invalid("time") from exc
    return value


def _movie(value: Any, *, lenient: bool = False) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _Invalid("movie")
    movie = {
        "tmdb_id": _int(value.get("tmdb_id"), 1, _TMDB_MAX, required=True),
        "imdb_id": None,
        "title": None,
        "original_title": None,
        "year": None,
    }
    imdb = value.get("imdb_id")
    if imdb is not None:
        if not isinstance(imdb, str) or not _IMDB_RE.match(imdb):
            if not lenient:
                raise _Invalid("imdb")
        else:
            movie["imdb_id"] = imdb
    try:
        movie["title"] = _text(value.get("title"))
        movie["original_title"] = _text(value.get("original_title"))
        movie["year"] = _int(value.get("year"), 1800, 2200)
    except _Invalid:
        if not lenient:
            raise
    return movie


def _subtitle(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _Invalid("subtitle")
    return {
        "file": _file_name(value.get("file")),
        "language": _text(value.get("language"), 8),
        "forced": _bool(value.get("forced")),
        "sdh": _bool(value.get("sdh")),
    }


def _came_from(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("kind") not in CAME_FROM_KINDS:
        raise _Invalid("came_from")
    kind = value["kind"]
    came: dict[str, Any] = {"kind": kind}
    if kind == "download":
        came["protocol"] = _text(value.get("protocol"), 16)
        came["indexer"] = _text(value.get("indexer"), 100)
        came["grabbed_at"] = _time(value.get("grabbed_at"))
        came["imported_at"] = _time(value.get("imported_at"))
    elif kind == "takeover":
        came["imported_at"] = _time(value.get("imported_at"))
    else:
        came["found_at"] = _time(value.get("found_at"))
    return came


def _entry(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _Invalid("entry")
    languages = value.get("languages", [])
    if not isinstance(languages, list) or not all(isinstance(item, str) for item in languages):
        raise _Invalid("languages")
    subtitles = value.get("subtitles", [])
    if not isinstance(subtitles, list):
        raise _Invalid("subtitles")
    quality = value.get("quality")
    if quality is not None and not isinstance(quality, str):
        raise _Invalid("quality")
    quality_from = value.get("quality_from", "name")
    if quality_from not in QUALITY_FROM:
        raise _Invalid("quality_from")
    return {
        "version": _text(value.get("version"), 64, required=True),
        "file": _file_name(value.get("file")),
        "size_bytes": _int(value.get("size_bytes"), 0, 2**62, required=True),
        "quality": quality if quality in qualities.BY_NAME else "Unknown",
        "quality_from": quality_from,
        "release_title": _text(value.get("release_title")),
        "release_group": _text(value.get("release_group"), 200),
        "edition": _text(value.get("edition"), 200),
        "languages": [schreibweisen.nfc(item)[:64] for item in languages[:32]],
        "subtitles": [_subtitle(item) for item in subtitles[:64]],
        "came_from": _came_from(value.get("came_from")),
        "monitored": _bool(value.get("monitored"), True),
        "minimum_availability": _text(value.get("minimum_availability"), 32),
    }


@dataclass
class ReadResult:
    """What a folder's ``release.nex`` says."""

    outcome: str
    #: The validated document for ``ours`` and ``other_installation``; the numbers only for ``newer_format``.
    movie: dict[str, Any] | None = None
    entries: list[dict[str, Any]] = field(default_factory=list)
    installation: str | None = None
    format_version: int | None = None
    written_at: str | None = None
    sha256: str | None = None
    size: int = 0
    #: Why the file is ``broken``, a short code for the log.
    reason: str | None = None

    @property
    def readable(self) -> bool:
        return self.outcome in (OURS, OTHER_INSTALLATION, NEWER_FORMAT)

    def entry_for(self, label: str) -> dict[str, Any] | None:
        for entry in self.entries:
            if entry["version"] == label:
                return entry
        return None


def _regular_file(path: Path) -> os.stat_result | None:
    try:
        info = os.lstat(path)
    except OSError:
        return None
    return info if stat.S_ISREG(info.st_mode) else None


def read(folder: Path, installation: str | None = None, name: str = FILE_NAME) -> ReadResult:
    """Read the folder's ``release.nex``. Never raises; a link, a folder or a broken file is an outcome."""
    path = folder / name
    info = _regular_file(path)
    if info is None:
        return ReadResult(MISSING if not path.is_symlink() and not path.exists() else FOREIGN)
    if info.st_size > MAX_BYTES:
        return ReadResult(FOREIGN, size=info.st_size)
    try:
        raw = path.read_bytes()
    except OSError:
        return ReadResult(FOREIGN, size=info.st_size)
    digest = hashlib.sha256(raw).hexdigest()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return ReadResult(FOREIGN, sha256=digest, size=info.st_size)
    try:
        data = json.loads(text, object_pairs_hook=_pairs_hook)
    except _DuplicateKey:
        return ReadResult(BROKEN, sha256=digest, size=info.st_size, reason="duplicate_key")
    except ValueError:
        return ReadResult(FOREIGN, sha256=digest, size=info.st_size)
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        return ReadResult(FOREIGN, sha256=digest, size=info.st_size)
    version = data.get("format_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        return ReadResult(BROKEN, sha256=digest, size=info.st_size, reason="format_version")
    written_by = data.get("written_by") if isinstance(data.get("written_by"), dict) else {}
    written_id = written_by.get("installation") if isinstance(written_by.get("installation"), str) else None
    written_at = data.get("written_at") if isinstance(data.get("written_at"), str) else None
    if version > FORMAT_VERSION:
        try:
            movie = _movie(data.get("movie"), lenient=True)
        except _Invalid as exc:
            return ReadResult(BROKEN, sha256=digest, size=info.st_size, reason=str(exc))
        return ReadResult(
            NEWER_FORMAT, movie=movie, installation=written_id, format_version=version, written_at=written_at,
            sha256=digest, size=info.st_size,
        )  # fmt: skip
    try:
        movie = _movie(data.get("movie"))
        raw_entries = data.get("entries", [])
        if not isinstance(raw_entries, list) or len(raw_entries) > MAX_ENTRIES:
            raise _Invalid("entries")
        entries = [_entry(item) for item in raw_entries]
        if len({entry["version"] for entry in entries}) != len(entries):
            raise _Invalid("entries")
    except _Invalid as exc:
        return ReadResult(BROKEN, sha256=digest, size=info.st_size, reason=str(exc))
    outcome = OURS if installation is not None and written_id == installation else OTHER_INSTALLATION
    return ReadResult(
        outcome, movie=movie, entries=entries, installation=written_id, format_version=version,
        written_at=written_at, sha256=digest, size=info.st_size,
    )  # fmt: skip


# --- Building ------------------------------------------------------------------------------ #


def _iso(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def owned(version: Version) -> bool:
    """A version nexcrate owns with a file: no source, ``has_file``, a ``file_ref`` of nexcrate's."""
    return (
        version.source_id is None
        and bool(version.has_file)
        and isinstance(version.file_ref, str)
        and version.file_ref.startswith(OWN_FILE_REFS)
    )


def _latest_event(db: OrmSession, version: Version, events: tuple[str, ...]) -> HistoryEntry | None:
    return db.scalars(
        select(HistoryEntry)
        .where(HistoryEntry.version_id == version.id, HistoryEntry.event.in_(events))
        .order_by(HistoryEntry.at.desc(), HistoryEntry.id.desc())
        .limit(1)
    ).first()


def _came_from_of(db: OrmSession, version: Version) -> dict[str, Any] | None:
    ref = version.file_ref or ""
    if ref.startswith("nexcrate:"):
        download = None
        try:
            download = db.get(Download, int(ref.split(":", 1)[1]))
        except ValueError:
            download = None
        if download is not None:
            return {
                "kind": "download",
                "protocol": download.protocol,
                "indexer": download.indexer_name or None,
                "grabbed_at": _iso(download.grabbed_at),
                "imported_at": _iso(download.imported_at),
            }
        entry = _latest_event(db, version, ("imported",))
        return {"kind": "download", "protocol": None, "indexer": None, "grabbed_at": None,
                "imported_at": _iso(entry.at if entry else None)}  # fmt: skip
    if ref.startswith("taken:"):
        entry = _latest_event(db, version, ("imported", "taken_over"))
        return {"kind": "takeover", "imported_at": _iso(entry.at if entry else version.created_at)}
    if ref.startswith("disk:"):
        entry = _latest_event(db, version, ("found_on_disk", "restored", "imported"))
        return {"kind": "disk", "found_at": _iso(entry.at if entry else version.created_at)}
    return None


def _parts_of(version: Version) -> list[str]:
    return [part for part in (version.relative_path or "").replace("\\", "/").split("/") if part]


def _file_of(version: Version) -> str | None:
    parts = _parts_of(version)
    return parts[-1] if len(parts) >= 2 else None


def folder_name_of(version: Version) -> str | None:
    """The movie folder's name from the database only, never a path: the part of ``relative_path`` before the file."""
    parts = _parts_of(version)
    return parts[-2] if len(parts) >= 2 else None


def entry_of(db: OrmSession, version: Version, definition: VersionDefinition) -> dict[str, Any] | None:
    """The version's entry, from the database only. None when the version has no file name to speak of."""
    file_name = _file_of(version)
    if file_name is None:
        return None
    release_title = version.release_title or None
    edition = None
    group = version.release_group or None
    if release_title:
        try:
            parsed = release_parser.parse_movie(release_title)
            edition = parsed.edition
            group = group or parsed.group
        except ValueError, KeyError, IndexError, AttributeError:  # a name the parser cannot read gives no edition
            edition = None
    quality_from = version.quality_from
    if quality_from not in QUALITY_FROM:
        quality_from = "radarr" if (version.file_ref or "").startswith("taken:") else "name"
    return {
        "version": definition.label,
        "file": file_name,
        "size_bytes": int(version.size or 0),
        "quality": version.quality if version.quality in qualities.BY_NAME else "Unknown",
        "quality_from": quality_from,
        "release_title": release_title,
        "release_group": group,
        "edition": edition,
        "languages": list(version.languages or []),
        "subtitles": [
            {
                "file": row["file"].replace("\\", "/").rsplit("/", 1)[-1],
                "language": row["language"],
                "forced": row["forced"],
                "sdh": row["sdh"],
            }
            for row in subtitle_records.listed(db, version)
        ],
        "came_from": _came_from_of(db, version),
        "monitored": bool(version.monitored),
        "minimum_availability": version.minimum_availability,
    }


def document(title: Title, entries: list[dict[str, Any]], installation: str, written_at: datetime) -> dict[str, Any]:
    """The whole file, keys in the fixed order (decision 3)."""
    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "written_at": _iso(written_at),
        "written_by": {"app": "nexcrate", "version": __version__, "installation": installation},
        "movie": {
            "tmdb_id": title.tmdb_id,
            "imdb_id": title.imdb_id if title.imdb_id and _IMDB_RE.match(title.imdb_id) else None,
            "title": title.title or None,
            "original_title": title.original_title or None,
            "year": title.year,
        },
        "entries": sorted(entries, key=lambda entry: entry["version"]),
    }


def encode(doc: dict[str, Any]) -> bytes:
    """Two spaces, UTF-8 without BOM, LF, a final newline."""
    return (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _same_content(existing: ReadResult, movie: dict[str, Any], entries: list[dict[str, Any]]) -> bool:
    """Equal apart from the time and the app version: an unchanged file is never written again."""
    return existing.movie == movie and sorted(existing.entries, key=lambda e: e["version"]) == sorted(
        entries, key=lambda e: e["version"]
    )


# --- The folder ---------------------------------------------------------------------------- #


@dataclass
class Located:
    folder: Path | None
    video: Path | None
    #: ``folder_missing`` or ``file_missing`` when something is not there; None when both are.
    problem: str | None


def locate(version: Version) -> Located:
    """The movie folder of a version nexcrate owns and its video, resolved, strictly inside a visible root folder.

    Never follows a link at the folder or the video.
    """
    parts = [part for part in (version.relative_path or "").replace("\\", "/").split("/") if part]
    if len(parts) < 2 or any(part in (".", "..") for part in parts):
        return Located(None, None, "folder_missing")
    try:
        root, _mount = folders.visible(version.root_folder)
    except folders.NotVisible:
        return Located(None, None, "folder_missing")
    folder_candidate = root.joinpath(*parts[:-1])
    folder = files.resolved(folder_candidate)
    inside = folder is not None and files.strictly_inside(folder, root)
    if folder is None or files.is_link(folder_candidate) or not folder.is_dir() or not inside:
        return Located(None, None, "folder_missing")
    video_candidate = folder / parts[-1]
    video = files.resolved(video_candidate)
    if video is None or files.is_link(video_candidate) or not video.is_file() or video.parent != folder:
        return Located(folder, None, "file_missing")
    return Located(folder, video, None)


# --- Writing ------------------------------------------------------------------------------- #


class _WriteFailed(Exception):
    def __init__(self, state: str) -> None:
        super().__init__(state)
        self.state = state


def _state_of(exc: OSError) -> str:
    import errno

    if exc.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
        return "not_writable"
    if exc.errno == errno.ENOSPC:
        return "no_space"
    return "failed"


def writable(folder: Path) -> bool:
    """The folder's rights, without a test file (L3): the check must leave the tree byte for byte as it was, and a
    folder watcher would see a test file. A read-only mount answers False here as well."""
    return os.access(folder, os.W_OK | os.X_OK)


def write_bytes(folder: Path, data: bytes, name: str = FILE_NAME) -> None:
    """The safe write of decision 6. Raises ``_WriteFailed`` with the state."""
    partial = folder / PARTIAL_NAME
    target = folder / name
    try:
        if _regular_file(partial) is not None and not partial.is_symlink():
            partial.unlink()
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            try:
                partial.unlink()
            except OSError:
                pass
            raise
        os.replace(partial, target)
    except OSError as exc:
        raise _WriteFailed(_state_of(exc)) from exc


def remove_file(folder: Path, name: str = FILE_NAME) -> bool:
    """Unlink the folder's file when it is a regular file. False when nothing was removed."""
    path = folder / name
    if _regular_file(path) is None or path.is_symlink():
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True


def recycle_file(folder: Path, root: str | Path | None, moment: datetime, name: str = FILE_NAME) -> bool:
    """Move the folder's ``release.nex`` into ``.nexcrate-recycle/<date>/<movie folder>/`` of the root folder.

    "Replace" of decision 5. True when the way is clear: the file was moved, or there was none. False for a link or a
    folder under the name, a root folder nexcrate does not see, or a move that failed. Nothing else on disk is touched.
    """
    path = folder / name
    resolved_root = files.resolved(root) if root else None
    with _lock_for(folder):
        if not os.path.lexists(path):
            return True
        if _regular_file(path) is None or path.is_symlink() or resolved_root is None:
            return False
        try:
            files.recycle(path, resolved_root, moment)
        except files.FileProblem, OSError:
            return False
    return True


def _store(db: OrmSession, version: Version, state: str, sha256: str | None, moment: datetime | None) -> None:
    version.companion_state = state
    if state in ("written", "current"):
        version.companion_sha256 = sha256
        if state == "written" or version.companion_written_at is None:
            version.companion_written_at = moment
    version.updated_at = utcnow()


def _hash_holders(db: OrmSession, version: Version, entries: list[dict[str, Any]]) -> list[Version]:
    """The versions of this title whose entries stand in the same file."""
    labels = {entry["version"] for entry in entries}
    rows = db.scalars(
        select(Version, VersionDefinition)
        .join(VersionDefinition, VersionDefinition.id == Version.version_definition_id)
        .where(Version.title_id == version.title_id, VersionDefinition.label.in_(labels))
    ).all()
    return [row for row in rows if row.id != version.id]


Classified = tuple[str, Located, ReadResult | None, bytes | None, dict[str, Any] | None]


def classify(db: OrmSession, version: Version, installation: str, located: Located | None = None) -> Classified:
    """The state a version's file is in, and what would be written. Writes nothing on disk.

    Returns (state, located, existing, text, movie). ``state`` is ``current``, ``missing`` or ``outdated`` when a write
    could follow; otherwise a state that stops the write. ``located`` saves a second look at the disk when the caller
    located the folder already.
    """
    located = locate(version) if located is None else located
    if located.problem is not None:
        return located.problem, located, None, None, None
    assert located.folder is not None
    title = db.get(Title, version.title_id)
    definition = db.get(VersionDefinition, version.version_definition_id)
    if title is None or definition is None:
        return "failed", located, None, None, None
    entry = entry_of(db, version, definition)
    if entry is None:
        return "file_missing", located, None, None, None
    existing = read(located.folder, installation)
    if existing.outcome in (OTHER_INSTALLATION, NEWER_FORMAT, BROKEN, FOREIGN):
        return existing.outcome, located, existing, None, None
    entries = [entry]
    if existing.outcome == OURS:
        if version.companion_sha256 is not None and existing.sha256 != version.companion_sha256:
            return "changed", located, existing, None, None
        entries.extend(other for other in existing.entries if other["version"] != entry["version"])
    doc = document(title, entries, installation, utcnow())
    if existing.outcome == OURS and _same_content(existing, doc["movie"], doc["entries"]):
        return "current", located, existing, None, doc["movie"]
    if existing.outcome == MISSING:
        return "missing", located, existing, encode(doc), doc["movie"]
    return "outdated", located, existing, encode(doc), doc["movie"]


def _write_locked(db: OrmSession, version: Version, installation: str, located: Located | None = None) -> str:
    state, located, existing, text, _movie = classify(db, version, installation, located)
    moment = utcnow()
    if state == "current":
        assert existing is not None
        _store(db, version, "current", existing.sha256, existing_time(existing) or moment)
        return "current"
    if state not in ("missing", "outdated"):
        _store(db, version, state, None, None)
        return state
    assert located.folder is not None and text is not None
    if not writable(located.folder):
        _store(db, version, "not_writable", None, None)
        return "not_writable"
    try:
        write_bytes(located.folder, text)
    except _WriteFailed as exc:
        _store(db, version, exc.state, None, None)
        return exc.state
    digest = hashlib.sha256(text).hexdigest()
    _store(db, version, "written", digest, moment)
    written = read(located.folder, installation)
    for holder in _hash_holders(db, version, written.entries):
        holder.companion_sha256 = digest
        if holder.companion_state in (None, "written", "current", "outdated", "missing"):
            holder.companion_state = "current"
    return "written"


def existing_time(existing: ReadResult) -> datetime | None:
    if not existing.written_at:
        return None
    try:
        parsed = datetime.fromisoformat(existing.written_at)
        # Stored through ``UTCDateTime``, which refuses a naive value.
        return parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


def write_version(version_id: int, db: OrmSession | None = None) -> str:
    """Write or refresh the version's entry. Returns the state stored on the version, or ``off``.

    Opens its own session when none is given and commits. With a session the caller commits. Never raises.
    """
    if db is None:
        with SessionLocal() as session:
            state = write_version(version_id, session)
            session.commit()
            return state
    version = db.get(Version, version_id)
    if version is None or not owned(version):
        return "failed" if version is None else "not_owned"
    if not enabled(db):
        return OFF
    installation = installation_id(db)
    try:
        located = locate(version)
        if located.folder is None:
            state = located.problem or "folder_missing"
            _store(db, version, state, None, None)
        else:
            # The same lock ``remove`` takes: the resolved movie folder, so an import and a removal never overlap.
            with _lock_for(located.folder):
                state = _write_locked(db, version, installation, located)
    except Exception:  # the write never reaches the caller
        logger.exception("Version %d: writing release.nex failed", version_id)
        version.companion_state = "failed"
        state = "failed"
    if state in ("written", "current"):
        logger.debug("Version %d: release.nex %s", version_id, state)
    else:
        logger.info("Version %d: release.nex %s", version_id, state)
    return state


def write_versions(version_ids: Iterable[int]) -> dict[str, int]:
    """Write several versions, one after another, each in its own transaction. Counts per state."""
    counts: dict[str, int] = {}
    for version_id in version_ids:
        state = write_version(version_id)
        counts[state] = counts.get(state, 0) + 1
    logger.info("release.nex written for %d versions: %s", sum(counts.values()), _codes(counts))
    return counts


def _codes(counts: dict[str, int]) -> str:
    return ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))


def check_version(db: OrmSession, version: Version, installation: str) -> str:
    """The backfill's check (L3): classify, store the state on the version, write nothing on disk."""
    if not owned(version):
        return "not_owned"
    state, located, existing, _text, _movie = classify(db, version, installation)
    if state in ("missing", "outdated") and located.folder is not None and not writable(located.folder):
        state = "not_writable"
    if state == "current" and existing is not None:
        _store(db, version, "current", existing.sha256, existing_time(existing))
    else:
        version.companion_state = state
        version.updated_at = utcnow()
    return state


# --- Removing entries (L2: undo, an import taking over, removing a version or a title) ------- #


@dataclass(frozen=True)
class Removal:
    """What to remove after a commit: the folder, the entry's label and the hash nexcrate stored last."""

    folder: Path
    label: str
    sha256: str | None
    version_id: int


def plan_removal(db: OrmSession, versions: Iterable[Version]) -> list[Removal]:
    """Collect before the transaction that hands the versions away; ``remove`` acts after its commit.

    Only versions with a stored hash: nexcrate removes what it knows it wrote, and only while the file is unchanged
    (decision 8). A version without a hash never had an entry written by this database, or its state was cleared
    already; ``remove`` could not tell a changed file from an unchanged one for it.
    """
    removals: list[Removal] = []
    for version in versions:
        if not owned(version) or version.companion_sha256 is None:
            continue
        located = locate(version)
        if located.folder is None:
            continue
        definition = db.get(VersionDefinition, version.version_definition_id)
        if definition is None:
            continue
        removals.append(Removal(located.folder, definition.label, version.companion_sha256, version.id))
    return removals


def remove(removals: Iterable[Removal], installation: str | None = None) -> dict[str, int]:
    """Remove the entries; the file when no entry is left. Only files of this installation, unchanged since written.

    Counts: ``removed`` (an entry or the file), ``kept`` (changed or not nexcrate's), ``missing``, ``failed``.
    """
    installation = installation or installation_id()
    counts = {"removed": 0, "kept": 0, "missing": 0, "failed": 0}
    by_folder: dict[Path, list[Removal]] = {}
    for removal in removals:
        by_folder.setdefault(removal.folder, []).append(removal)
    for folder, group in by_folder.items():
        with _lock_for(folder):
            existing = read(folder, installation)
            if existing.outcome == MISSING:
                counts["missing"] += len(group)
                continue
            stored = {item.sha256 for item in group if item.sha256 is not None}
            if existing.outcome != OURS or (stored and existing.sha256 not in stored):
                counts["kept"] += len(group)
                continue
            labels = {item.label for item in group}
            left = [entry for entry in existing.entries if entry["version"] not in labels]
            try:
                if left:
                    doc = {
                        "format": FORMAT,
                        "format_version": FORMAT_VERSION,
                        "written_at": _iso(utcnow()),
                        "written_by": {"app": "nexcrate", "version": __version__, "installation": installation},
                        "movie": existing.movie,
                        "entries": sorted(left, key=lambda entry: entry["version"]),
                    }
                    text = encode(doc)
                    write_bytes(folder, text)
                    assert existing.movie is not None
                    labels_left = {entry["version"] for entry in left}
                    _rehash_remaining(existing.movie["tmdb_id"], labels_left, hashlib.sha256(text).hexdigest())
                elif not remove_file(folder):
                    counts["failed"] += len(group)
                    continue
            except _WriteFailed:
                counts["failed"] += len(group)
                continue
            counts["removed"] += len(group)
    logger.info("release.nex entries removed for %d versions: %s", sum(counts.values()), _codes(counts))
    return counts


def _rehash_remaining(tmdb_id: int, labels: set[str], digest: str) -> None:
    """Versions whose entries stay in a rewritten file learn its new hash; else their next write sees ``changed``."""
    with SessionLocal() as db:
        rows = db.scalars(
            select(Version)
            .join(Title, Title.id == Version.title_id)
            .join(VersionDefinition, VersionDefinition.id == Version.version_definition_id)
            .where(
                Title.kind == "movie",
                Title.tmdb_id == tmdb_id,
                VersionDefinition.label.in_(labels),
                Version.companion_sha256.is_not(None),
            )
        ).all()
        for row in rows:
            row.companion_sha256 = digest
        db.commit()


def clear_state(version: Version) -> None:
    """After a version was handed to Radarr: nexcrate keeps no companion state for it."""
    version.companion_state = None
    version.companion_written_at = None
    version.companion_sha256 = None
