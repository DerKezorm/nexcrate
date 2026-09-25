"""Assigning and restoring album folders from the page "Ordner" (decisions 18 and 19).

**Assign** takes an album of the library (the page adds an unknown one over MusicBrainz first): its one version, or a
new one, gets this folder as its album folder, and the folder is read as after a takeover (``album_read``): files
linked by their tags and names, the rest unclear. A version a Lidarr connection feeds, a version with files in another
folder, and a folder another album has are refused.

**Restore** does the same for a folder with a ``release.nex``, without guessing: the release it names becomes the
target, and every file it lists is linked to the tracks it names by their MusicBrainz ids. What the file does not
list is read afterwards like any other file.

Before reading, an album whose releases are not loaded yet gets them from MusicBrainz on the owner's lane; without
them every file would stay unclear. Log lines carry ids and counts.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ...db import SessionLocal
from ...models import (
    DiskFolder,
    DiskRoot,
    HistoryEntry,
    Release,
    ReleaseTrack,
    Title,
    TrackFile,
    Version,
    VersionDefinition,
    utcnow,
)
from .. import companions, companions_album, folders
from ..music import album_read, loading, paths, store, tags
from ..music import musicbrainz as mb
from . import roots as root_service
from .jobs import DiskJob

logger = logging.getLogger("nexcrate.disk")

#: Refusals that are a conflict in a job, not a skip.
CONFLICTS = frozenset({"album_folder_known", "album_has_folder", "album_fed_by_source"})


class Refused(Exception):
    """``not_found``, ``album_folder_known``, ``album_fed_by_source``, ``album_has_folder``, ``album_not_in_library``,
    ``folder_changed``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def is_album_row(db: OrmSession, row: DiskFolder) -> bool:
    root = db.get(DiskRoot, row.root_id)
    return root is not None and root.kind == "album"


def album_rows(row_ids: list[int] | None, state: str) -> list[int]:
    """The rows below music roots: the given ones, or every row in ``state`` that is not ignored."""
    with SessionLocal() as db:
        statement = (
            select(DiskFolder.id)
            .join(DiskRoot, DiskRoot.id == DiskFolder.root_id)
            .where(DiskRoot.kind == "album", DiskFolder.ignored.is_(False))
            .order_by(DiskFolder.id)
        )
        if row_ids is None:
            statement = statement.where(DiskFolder.state == state)
        else:
            statement = statement.where(DiskFolder.id.in_(row_ids))
        return list(db.scalars(statement))


def _row_and_root(db: OrmSession, row_id: int) -> tuple[DiskFolder, DiskRoot]:
    row = db.get(DiskFolder, row_id)
    root = db.get(DiskRoot, row.root_id) if row is not None else None
    if row is None or root is None or root.kind != "album":
        raise Refused("not_found")
    if row.state in ("library", "lidarr"):
        raise Refused("album_folder_known")
    return row, root


def _check(db: OrmSession, row: DiskFolder, root: DiskRoot, title: Title) -> Version | None:
    """The album's version when it may take the folder, None when there is none yet; raises ``Refused``."""
    version = store.album_version(db, title.id)
    if version is not None and version.source_id is not None:
        raise Refused("album_fed_by_source")
    same_place = version is not None and (version.root_folder, version.relative_path) == (root.path, row.relative_path)
    if version is not None and version.has_file and version.relative_path and not same_place:
        raise Refused("album_has_folder")
    other = db.scalar(
        select(Version.id).where(
            Version.source_id.is_(None),
            Version.root_folder == root.path,
            Version.relative_path == row.relative_path,
            Version.title_id != title.id,
        )
    )
    if other is not None:
        raise Refused("album_folder_known")
    return version


def check(row_id: int, title_id: int) -> None:
    """The checks of ``assign`` without writing, for the route before it loads releases."""
    with SessionLocal() as db:
        row, root = _row_and_root(db, row_id)
        title = db.get(Title, title_id)
        if title is None or title.kind != "album":
            raise Refused("not_found")
        _check(db, row, root, title)


def load_releases(title_id: int) -> None:
    """The album's releases with their tracks, when they are not loaded yet. A MusicBrainz failure is left to the
    reading: the files stay unclear then and "read the folder again" links them later."""
    with SessionLocal() as db:
        title = db.get(Title, title_id)
        if title is None or title.releases_state == "tracks" or not title.mbid:
            return
        try:
            loading.load_releases_of(db, title, lane=mb.OWNER)
        except mb.MusicBrainzError as exc:
            logger.info("Album %d: releases not loaded before reading its folder: %s", title_id, exc.code)


def _place(db: OrmSession, row: DiskFolder, root: DiskRoot, title: Title, event: str) -> Version:
    moment = utcnow()
    version = _check(db, row, root, title)
    if version is None:
        definition = store.ensure_definition(db, store.account_language(db))
        version = store.add_version(db, title, definition, moment)
    version.root_folder = root.path
    version.relative_path = row.relative_path
    version.own_since = version.own_since or moment
    version.updated_at = moment
    db.add(
        HistoryEntry(
            title_id=title.id,
            version_id=version.id,
            version_definition_id=version.version_definition_id,
            version_label=_label(db, version),
            event=event,
            at=moment,
            detail=None,
        )
    )
    row.state = "library"
    row.title_id = title.id
    row.version_id = version.id
    row.source_id = None
    row.proposals = None
    db.flush()
    # The root's counts follow at once, not only with the next scan.
    root.last_counts = root_service.counts_of(db, root.id)
    return version


def _label(db: OrmSession, version: Version) -> str:
    definition = db.get(VersionDefinition, version.version_definition_id)
    return definition.label if definition is not None else ""


def assign(row_id: int, title_id: int) -> int:
    """Give the folder to this album and read it. Returns the title id."""
    with SessionLocal() as db:
        row, root = _row_and_root(db, row_id)
        title = db.get(Title, title_id)
        if title is None or title.kind != "album":
            raise Refused("not_found")
        version = _place(db, row, root, title, "found_on_disk")
        version_id = version.id
        db.commit()
    album_read.read_version(version_id)
    companions_album.write_version(version_id, adopt=True)
    logger.info("Album folder %d assigned to album %d", row_id, title_id)
    return title_id


# --- Restoring from release.nex --------------------------------------------------------------------------------- #


def companion_title(row_id: int) -> int:
    """The album a restorable row's ``release.nex`` names, in the library; raises ``album_not_in_library``."""
    with SessionLocal() as db:
        row, _root = _row_and_root(db, row_id)
        mbid = (row.numbers or {}).get("release_group") if (row.numbers or {}).get("from") == "companion" else None
        if not mbid:
            raise Refused("folder_changed")
        title = store.find_album(db, str(mbid))
        if title is None:
            raise Refused("album_not_in_library")
        return title.id


def restore(row_id: int) -> int:
    """Restore the folder from its ``release.nex``. Returns the title id."""
    title_id = companion_title(row_id)
    load_releases(title_id)
    with SessionLocal() as db:
        row, root = _row_and_root(db, row_id)
        title = db.get(Title, title_id)
        assert title is not None
        folder = _folder(root, row)
        read = companions_album.read(folder, companions.installation_id(db)) if folder is not None else {}
        if read.get("outcome") not in (companions.OURS, companions.OTHER_INSTALLATION):
            raise Refused("folder_changed")
        version = _place(db, row, root, title, "restored")
        block = read.get("album") or {}
        target = block.get("target_release") if isinstance(block.get("target_release"), dict) else {}
        release = (
            db.scalar(select(Release).where(Release.title_id == title.id, Release.mbid == target.get("mbid")))
            if target.get("mbid")
            else None
        )
        if release is not None:
            version.target_release_id = release.id
            version.target_set_by = "owner"
        linked = _link_entries(db, version, folder, read.get("entries") or [])
        version_id = version.id
        db.commit()
    counts = album_read.read_version(version_id)
    companions_album.write_version(version_id, adopt=True)
    logger.info(
        "Album folder %d restored into album %d: %d files from release.nex, %d read", row_id, title_id, linked,
        counts["linked"] + counts["unclear"],
    )  # fmt: skip
    return title_id


def _folder(root: DiskRoot, row: DiskFolder) -> Any:
    try:
        base, _mount = folders.visible(root.path)
    except folders.NotVisible:
        return None
    folder = base.joinpath(*paths.parts_of(row.relative_path))
    return folder if folder.is_dir() else None


def _link_entries(db: OrmSession, version: Version, folder: Any, entries: list[Any]) -> int:
    """A file row per entry whose file lies there, linked to the tracks it names by their ids."""
    stored = db.scalars(select(TrackFile.relative_path).where(TrackFile.version_id == version.id))
    known = {paths.display(name) for name in stored}
    mbids = {
        str(track.get("track_mbid"))
        for entry in entries
        if isinstance(entry, dict)
        for track in entry.get("tracks") or []
        if isinstance(track, dict) and track.get("track_mbid")
    }
    by_mbid = {
        track.mbid: track.id
        for track in db.scalars(
            select(ReleaseTrack)
            .join(Release, Release.id == ReleaseTrack.release_id)
            .where(Release.title_id == version.title_id, ReleaseTrack.mbid.in_(mbids))
        )
    }
    moment = utcnow()
    linked = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = paths.display(str(entry.get("file") or ""))
        path = paths.file_in(folder, name) if name else None
        if path is None or name in known or not path.is_file():
            continue
        ids = [
            by_mbid[str(track.get("track_mbid"))]
            for track in entry.get("tracks") or []
            if isinstance(track, dict) and str(track.get("track_mbid")) in by_mbid
        ]
        read = tags.read(path)
        db.add(
            TrackFile(
                version_id=version.id,
                track_id=ids[0] if ids else None,
                track_ids=ids or None,
                relative_path=name[:2048],
                size=path.stat().st_size,
                quality=tags.quality_name(read.audio) if read.audio else None,
                unclear=not ids,
                added_at=moment,
                updated_at=moment,
            )
        )
        known.add(name)
        linked += 1
    db.flush()
    return linked


# --- Many at once --------------------------------------------------------------------------------------------- #


def _unambiguous_title(row: DiskFolder) -> int | None:
    found = [item for item in row.proposals or [] if isinstance(item, dict) and item.get("unambiguous")]
    if len(found) != 1 or not isinstance(found[0].get("title_id"), int):
        return None
    return int(found[0]["title_id"])


def _one(kind: str, row_id: int) -> tuple[str, str | None]:
    if kind == "restore":
        restore(row_id)
        return "restored", None
    with SessionLocal() as db:
        row = db.get(DiskFolder, row_id)
        title_id = _unambiguous_title(row) if row is not None else None
    if title_id is None:
        return "skipped", "not_unambiguous"
    load_releases(title_id)
    assign(row_id, title_id)
    return "assigned", None


def run_many(job: DiskJob, kind: str, row_ids: list[int]) -> tuple[dict[str, int], dict[str, int]]:
    """``restore`` or ``assign`` over album rows: the counts per outcome and the reasons of what was not done."""
    counts: dict[str, int] = {}
    reasons: dict[str, int] = {}
    for index, row_id in enumerate(row_ids, start=1):
        reason: str | None = None
        try:
            outcome, reason = _one(kind, row_id)
        except Refused as exc:
            outcome, reason = ("conflict" if exc.code in CONFLICTS else "skipped"), exc.code
        except Exception:
            logger.exception("Album folder %d could not be handled", row_id)
            outcome, reason = "failed", "internal_error"
        counts[outcome] = counts.get(outcome, 0) + 1
        if reason and outcome not in ("restored", "assigned"):
            reasons[reason] = reasons.get(reason, 0) + 1
        job.progress(index)
    logger.info("Album folders (%s): %s", kind, ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))
    return counts, reasons
