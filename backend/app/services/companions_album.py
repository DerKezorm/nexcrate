"""``release.nex`` in every album folder nexcrate files into (M4.7, decision 37).

The same file as next to a movie or in a season folder, with an ``album`` block instead of ``movie`` or ``series``:
the MusicBrainz ids of the album and its artist, the target and the actual release, and one entry per file with its
medium, position, recording and track id. ``format_version`` stays 1: a reader tells the kind by the block. The rules
of L1 and L2 hold: built from the database only, written safely under a temporary name, a file changed by hand (its
hash differs from the one written) is never overwritten, a file of another installation neither, one writer per
folder. Restoring from it follows with M6. Nothing here raises to the caller: a failure becomes the version's
``companion_state``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from .. import __version__
from ..db import SessionLocal
from ..models import Artist, Release, ReleaseMedium, ReleaseTrack, Title, TrackFile, Version, utcnow
from . import companions, folders
from .downloads import files
from .music import paths

logger = logging.getLogger("nexcrate.companions")

MAX_BYTES = 1024 * 1024
#: ``write_version``'s answer for albums sharing one folder (Lidarr filed them flat into the artist folder).
SHARED = "shared"
MAX_ENTRIES = 1_000


def _release_block(release: Release | None) -> dict[str, Any] | None:
    if release is None:
        return None
    return {"mbid": release.mbid, "name": release.name, "country": release.country, "date": release.date}


def album_block(db: OrmSession, title: Title, version: Version) -> dict[str, Any]:
    artist = db.get(Artist, title.artist_id) if title.artist_id is not None else None
    return {
        "mbid": title.mbid,
        "title": title.title or None,
        "first_release_date": title.first_release_date,
        "artist": {"mbid": artist.mbid, "name": artist.name} if artist is not None else None,
        "target_release": _release_block(
            db.get(Release, version.target_release_id) if version.target_release_id else None
        ),
        "actual_release": _release_block(
            db.get(Release, version.actual_release_id) if version.actual_release_id else None
        ),
    }


def _covered(row: TrackFile) -> list[int]:
    """The tracks a file holds: ``track_ids`` when it covers several, else its one ``track_id``. Files taken over from
    Lidarr, read from the album folder or linked by the owner carry only ``track_id``."""
    return list(row.track_ids or ([row.track_id] if row.track_id is not None else []))


def entries_of(db: OrmSession, version: Version) -> list[dict[str, Any]]:
    """One entry per file of the album version, from the database only; never a path above the album folder."""
    rows = list(db.scalars(select(TrackFile).where(TrackFile.version_id == version.id).order_by(TrackFile.id)))
    track_ids = {track_id for row in rows for track_id in _covered(row)}
    tracks = {row.id: row for row in db.scalars(select(ReleaseTrack).where(ReleaseTrack.id.in_(track_ids)))}
    media = {
        row.id: row.position
        for row in db.scalars(select(ReleaseMedium).where(ReleaseMedium.id.in_({t.medium_id for t in tracks.values()})))
    }
    entries: list[dict[str, Any]] = []
    for row in rows:
        # The path inside the album folder, a medium folder of a taken-over album kept.
        name = paths.display(row.relative_path)
        covered = [tracks[track_id] for track_id in _covered(row) if track_id in tracks]
        entries.append(
            {
                "file": name,
                "size": row.size,
                "quality": row.quality,
                "tracks": [
                    {
                        "medium": media.get(track.medium_id, 1),
                        "position": track.position,
                        "track_mbid": track.mbid,
                        "recording_mbid": track.recording_mbid,
                    }
                    for track in covered
                ],
            }
        )
    return sorted(entries, key=lambda entry: str(entry["file"]))[:MAX_ENTRIES]


def read(folder: Path, installation: str) -> dict[str, Any]:
    """``missing``, ``ours`` (with ``entries``, ``album`` and ``sha256``), ``other_installation``, ``newer_format``,
    ``broken`` or ``foreign``. Never raises; input from outside is checked, no path followed."""
    path = folder / companions.FILE_NAME
    try:
        info = path.lstat()
    except OSError:
        return {"outcome": companions.MISSING}
    if path.is_symlink() or not path.is_file() or info.st_size > MAX_BYTES:
        return {"outcome": companions.FOREIGN}
    try:
        raw = path.read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except OSError, UnicodeDecodeError, ValueError:
        return {"outcome": companions.FOREIGN}
    digest = hashlib.sha256(raw).hexdigest()
    if not isinstance(data, dict) or data.get("format") != companions.FORMAT:
        return {"outcome": companions.FOREIGN, "sha256": digest}
    format_version = data.get("format_version")
    if isinstance(format_version, bool) or not isinstance(format_version, int) or format_version < 1:
        return {"outcome": companions.BROKEN, "sha256": digest}
    if format_version > companions.FORMAT_VERSION:
        return {"outcome": companions.NEWER_FORMAT, "sha256": digest}
    if not isinstance(data.get("album"), dict) or not isinstance(data.get("entries"), list):
        return {"outcome": companions.BROKEN, "sha256": digest}
    written_by = data.get("written_by") if isinstance(data.get("written_by"), dict) else {}
    outcome = companions.OURS if written_by.get("installation") == installation else companions.OTHER_INSTALLATION
    entries = [entry for entry in data["entries"][:MAX_ENTRIES] if isinstance(entry, dict)]
    return {"outcome": outcome, "sha256": digest, "entries": entries, "album": data["album"]}


def _folder(version: Version) -> Path | None:
    if not version.root_folder or not version.relative_path:
        return None
    try:
        root, _mount = folders.visible(version.root_folder)
    except folders.NotVisible:
        return None
    parts = [part for part in version.relative_path.replace("\\", "/").split("/") if part]
    folder = root.joinpath(*parts)
    if not parts or files.is_link(folder) or not folder.is_dir() or not files.strictly_inside(folder, root):
        return None
    return folder


def _store(version: Version, state: str, digest: str | None) -> str:
    version.companion_state = state
    if state in ("written", "current"):
        version.companion_sha256 = digest
        if state == "written" or version.companion_written_at is None:
            version.companion_written_at = utcnow()
    return state


def write(db: OrmSession, version: Version, installation: str, *, adopt: bool = False) -> str:
    """``adopt``: a file of another installation in a folder the owner just gave to an album or restored from it
    becomes this installation's (as for movies, the design notes, decision 4)."""
    title = db.get(Title, version.title_id)
    folder = _folder(version)
    if title is None:
        return _store(version, "failed", None)
    if folder is None:
        return _store(version, "folder_missing", None)
    with companions._lock_for(folder):
        existing = read(folder, installation)
        outcome = existing["outcome"]
        if adopt and outcome == companions.OTHER_INSTALLATION:
            outcome = companions.MISSING
        if outcome in (companions.OTHER_INSTALLATION, companions.NEWER_FORMAT, companions.BROKEN, companions.FOREIGN):
            return _store(version, outcome, None)
        if outcome == companions.OURS and version.companion_sha256 and existing["sha256"] != version.companion_sha256:
            return _store(version, "changed", None)
        entries = entries_of(db, version)
        if not entries:
            return _store(version, "file_missing", None)
        album = album_block(db, title, version)
        if outcome == companions.OURS and existing.get("album") == album and existing["entries"] == entries:
            return _store(version, "current", existing["sha256"])
        doc = {
            "format": companions.FORMAT,
            "format_version": companions.FORMAT_VERSION,
            "written_at": companions._iso(utcnow()),
            "written_by": {"app": "nexcrate", "version": __version__, "installation": installation},
            "album": album,
            "entries": entries,
        }
        data = companions.encode(doc)
        if len(data) > MAX_BYTES:
            return _store(version, "failed", None)
        if not companions.writable(folder):
            return _store(version, "not_writable", None)
        try:
            companions.write_bytes(folder, data)
        except companions._WriteFailed as exc:
            return _store(version, exc.state, None)
    return _store(version, "written", hashlib.sha256(data).hexdigest())


def write_version(version_id: int, *, adopt: bool = False) -> str:
    """Write or refresh the album folder's file, committed. Returns the state; never raises."""
    try:
        with SessionLocal() as db:
            version = db.get(Version, version_id)
            if version is None or version.source_id is not None or not companions.enabled(db):
                return companions.OFF
            if paths.sharing(db, version):
                # One file cannot describe two albums; restoring such a folder goes by the tags.
                return SHARED
            state = write(db, version, companions.installation_id(db), adopt=adopt)
            db.commit()
    except Exception:  # the write never reaches the caller
        logger.exception("Album version %d: writing release.nex failed", version_id)
        return "failed"
    if state not in ("written", "current"):
        logger.info("Album version %d: release.nex %s", version_id, state)
    return state
