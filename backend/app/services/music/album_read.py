"""Reading an album folder (decisions 14 to 16).

One core for every way in: after a takeover, "read the folder again" on the album page, and assigning a folder from
disk. It looks at the audio files in the album folder and one level below (medium folders such as ``CD 01``), links,
dot names and nexcrate's own staging and recycle folders left out:

* A file a row of the version knows already (same path) stays as it is; a row whose file is gone goes.
* A new file is matched as filing matches a download (M4: the id in the tag, medium and position with the length, the
  name with the length), against the release the album has, else every release of the album. A clear match is
  linked, without renaming and without writing tags; the rest is **unclear**, and so is a file for a song the album
  holds already.

The version's ``files_read_at`` is set, the tracks are counted again. Log lines carry ids and counts, never paths.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...db import SessionLocal
from ...models import Release, ReleaseTrack, Title, TrackFile, Version, utcnow
from .. import folders
from ..downloads import files
from . import album_quality, paths, same_song, store, tags
from . import file_matching as fm

logger = logging.getLogger("nexcrate.music")

#: Folders of nexcrate itself inside an album folder, never read.
OWN_PREFIXES = (".nexcrate-",)


def album_folder(version: Version) -> Path | None:
    """The version's album folder as nexcrate sees it, or None when there is none."""
    if not version.root_folder or not version.relative_path:
        return None
    try:
        root, _mount = folders.visible(version.root_folder)
    except folders.NotVisible:
        return None
    parts = paths.parts_of(version.relative_path)
    folder = root.joinpath(*parts)
    if not parts or files.is_link(folder) or not folder.is_dir() or not files.strictly_inside(folder, root):
        return None
    return folder


def audio_in(folder: Path) -> list[str]:
    """The audio files of the folder and of its medium folders (``CD 01``), as paths relative to it, sorted. Other
    folders are not entered: below an artist folder Lidarr filed flat into lie the folders of other albums."""
    found: list[str] = []
    for entry in sorted(os.scandir(folder), key=lambda item: item.name):
        if entry.name.startswith(".") or entry.is_symlink():
            continue
        if entry.is_file() and tags.is_audio(entry.name):
            found.append(entry.name)
        elif entry.is_dir() and not entry.name.startswith(OWN_PREFIXES) and paths.MEDIUM_FOLDER.match(entry.name):
            for inner in sorted(os.scandir(entry.path), key=lambda item: item.name):
                if inner.name.startswith(".") or inner.is_symlink():
                    continue
                if inner.is_file() and tags.is_audio(inner.name):
                    found.append(f"{entry.name}/{inner.name}")
    return found


def read_version(version_id: int) -> dict[str, int]:
    """Read one album folder; counts ``linked``, ``unclear``, ``gone`` and ``known``. Never raises for a missing
    folder: the version counts as read."""
    from ..downloads import album_import

    counts = {"linked": 0, "unclear": 0, "gone": 0, "known": 0}
    with SessionLocal() as db:
        version = db.get(Version, version_id)
        if version is None or version.source_id is not None:
            return counts
        title = db.get(Title, version.title_id)
        folder = album_folder(version)
        if title is None or folder is None:
            version.files_read_at = utcnow()
            db.commit()
            return counts
        rows = list(db.scalars(select(TrackFile).where(TrackFile.version_id == version.id)))
        known = {paths.display(row.relative_path): row for row in rows}
        # A known file stays while it lies there, in whatever folder Lidarr put it; only a missing one goes.
        present: set[str] = set()
        for name, row in known.items():
            path = paths.file_in(folder, name)
            if path is not None and path.is_file():
                present.add(name)
            else:
                db.delete(row)
                counts["gone"] += 1
        counts["known"] = len(present)
        shared = paths.sharing(db, version)
        if shared:
            # Albums sharing a folder: a new file could be any of theirs, so none is taken in (the owner sorts them
            # from disk). The files each of them knows stay theirs.
            fresh: list[str] = []
            logger.info("Album version %d shares its folder with %d others; new files left", version.id, len(shared))
        else:
            fresh = [name for name in audio_in(folder) if name not in known]
        editions = album_import._editions(db, title.id)
        held_ids = {
            track_id
            for row in rows
            if paths.display(row.relative_path) in present
            for track_id in (row.track_ids or ([row.track_id] if row.track_id is not None else []))
        }
        keys = same_song.keys_by_track(edition.tracks for edition in editions)
        held = same_song.union(keys, held_ids)
        audio: list[fm.AudioFile] = []
        reads: dict[int, tags.Read] = {}
        for index, name in enumerate(fresh):
            path = paths.file_in(folder, name)
            if path is None:
                continue
            read = tags.read(path)
            reads[index] = read
            audio.append(
                fm.AudioFile(
                    key=index,
                    path=name,
                    duration_ms=read.audio.duration_ms if read.audio is not None else None,
                    tags=read.tags or {},
                )
            )
        decisions: dict[int, fm.Decision] = {}
        if audio and editions:
            result = fm.match(
                audio,
                editions,
                target_id=version.target_release_id,
                chosen_id=version.actual_release_id,
                album_name=title.title,
            )
            if result.edition is not None:
                decisions = {decision.key: decision for decision in result.edition.decisions}
                # The release the files are, when nothing named one yet (a folder from disk): the album page then
                # offers it as the target when the files cover it ("Diese Ausgabe als Ziel").
                if version.actual_release_id is None and result.edition.filed:
                    version.actual_release_id = result.edition.edition_id
        moment = utcnow()
        for file in audio:
            decision = decisions.get(file.key)
            track_id = decision.track_id if decision is not None and decision.decision == fm.FILED else None
            if track_id is not None and keys.get(track_id, frozenset()) & held:
                # A second file for a song the album holds: the owner decides (decision 15).
                track_id = None
            path = paths.file_in(folder, file.path)
            read = reads.get(file.key)
            db.add(
                TrackFile(
                    version_id=version.id,
                    track_id=track_id,
                    relative_path=file.path[:2048],
                    size=path.stat().st_size if path is not None else 0,
                    quality=tags.quality_name(read.audio) if read is not None and read.audio else None,
                    unclear=track_id is None,
                    added_at=moment,
                    updated_at=moment,
                )
            )
            if track_id is not None:
                held |= keys.get(track_id, frozenset())
                counts["linked"] += 1
            else:
                counts["unclear"] += 1
        db.flush()
        version.files_read_at = moment
        store._count_tracks(db, version)
        qualities = list(db.scalars(select(TrackFile.quality).where(TrackFile.version_id == version.id)))
        album_quality.apply(version, album_quality.step_of_files(qualities), album_quality.rules_of(db))
        version.updated_at = moment
        db.commit()
    if counts["linked"] or counts["unclear"] or counts["gone"]:
        logger.info(
            "Album version %d read: %d files known, %d linked, %d unclear, %d gone",
            version_id,
            counts["known"],
            counts["linked"],
            counts["unclear"],
            counts["gone"],
        )
    return counts


# --- The owner settles an unclear file (decision 15) ------------------------------------------------------------ #


class Refused(Exception):
    """``not_found``, ``album_fed_by_source``, ``album_folder_missing``, ``track_not_in_album``, ``track_has_file``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _own_version(db: Session, title_id: int) -> Version:
    version = db.scalar(select(Version).where(Version.title_id == title_id).order_by(Version.id))
    if version is None:
        raise Refused("not_found")
    if version.source_id is not None:
        raise Refused("album_fed_by_source")
    return version


def read_title(title_id: int) -> dict[str, int]:
    """``POST /api/music/albums/{id}/read``: read the album folder again."""
    with SessionLocal() as db:
        version = _own_version(db, title_id)
        if album_folder(version) is None:
            raise Refused("album_folder_missing")
        version_id = version.id
    counts = read_version(version_id)
    from .. import companions_album

    companions_album.write_version(version_id)
    return counts


def settle(title_id: int, file_id: int, track_id: int | None) -> int:
    """Link an unclear file to a track of the album, or leave it without one (``track_id`` None: it stays on disk and
    counts as settled). A track that holds a file already is refused. Returns how many unclear files are left."""
    with SessionLocal() as db:
        version = _own_version(db, title_id)
        row = db.get(TrackFile, file_id)
        if row is None or row.version_id != version.id or not row.unclear:
            raise Refused("not_found")
        if track_id is not None:
            track = db.get(ReleaseTrack, track_id)
            release = db.get(Release, track.release_id) if track is not None else None
            if track is None or release is None or release.title_id != title_id:
                raise Refused("track_not_in_album")
            rows = list(db.scalars(select(TrackFile).where(TrackFile.version_id == version.id)))
            held_ids = {
                held for other in rows for held in (other.track_ids or ([other.track_id] if other.track_id else []))
            }
            editions_keys = same_song.keys_by_track(
                [
                    list(db.scalars(select(ReleaseTrack).where(ReleaseTrack.release_id == release_id)))
                    for release_id in set(
                        db.scalars(select(ReleaseTrack.release_id).where(ReleaseTrack.id.in_(held_ids | {track_id})))
                    )
                ]
            )
            if editions_keys.get(track_id, frozenset()) & same_song.union(editions_keys, held_ids):
                raise Refused("track_has_file")
            row.track_id = track_id
        row.unclear = False
        row.updated_at = utcnow()
        db.flush()
        store._count_tracks(db, version)
        left = int(
            db.scalar(
                select(func.count(TrackFile.id)).where(TrackFile.version_id == version.id, TrackFile.unclear.is_(True))
            )
            or 0
        )
        version_id = version.id
        db.commit()
    from .. import companions_album

    companions_album.write_version(version_id)
    logger.info("Album version %d: an unclear file settled, %d left", version_id, left)
    return left
