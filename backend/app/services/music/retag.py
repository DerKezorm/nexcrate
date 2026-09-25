"""The retag preview of an album (decisions 27 and 29): which fields of which file would change,
then writing them.

* Only an album version of nexcrate's own with files in its album folder; a version a connection feeds is never
  touched (its files are Lidarr's), and neither is a file outside the album folder or behind a link.
* The comparison covers every field nexcrate writes, the MusicBrainz ids included (research T5). The cover is not
  compared in the preview: it would need the archive for every look. Writing embeds it when the switch allows.
* Writing takes the same way as filing: the link rule, the formats nexcrate writes, nothing scrubbed. Each file's
  outcome lands in ``track_files.tags_state``; the history says how many were written.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ...db import SessionLocal
from ...models import HistoryEntry, Release, ReleaseTrack, Title, TrackFile, Version, VersionDefinition, utcnow
from .. import folders
from ..downloads import files
from . import covers, paths, tag_writing
from . import musicbrainz as mb

logger = logging.getLogger("nexcrate.music")

#: How many files one preview reads at most; more is refused (a box of many media is still far below).
MAX_FILES = 1_000


class RetagRefused(Exception):
    """``album_fed_by_source``, ``album_without_files``, ``album_folder_missing``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Planned:
    track_file_id: int
    name: str
    path: Path
    wanted: dict[str, list[str]]


def _folder(version: Version) -> Path:
    if not version.root_folder or not version.relative_path:
        raise RetagRefused("album_folder_missing")
    try:
        root, _mount = folders.visible(version.root_folder)
    except folders.NotVisible as exc:
        raise RetagRefused("album_folder_missing") from exc
    parts = [part for part in version.relative_path.replace("\\", "/").split("/") if part]
    folder = root.joinpath(*parts)
    if not parts or files.is_link(folder) or not folder.is_dir() or not files.strictly_inside(folder, root):
        raise RetagRefused("album_folder_missing")
    return folder


def _plan(db: OrmSession, title_id: int) -> tuple[Version, list[Planned]]:
    title = db.get(Title, title_id)
    version = db.scalar(select(Version).where(Version.title_id == title_id).order_by(Version.id))
    if title is None or title.kind != "album" or version is None:
        raise RetagRefused("album_without_files")
    if version.source_id is not None:
        raise RetagRefused("album_fed_by_source")
    rows = list(db.scalars(select(TrackFile).where(TrackFile.version_id == version.id).order_by(TrackFile.id)))
    if not rows or version.actual_release_id is None:
        raise RetagRefused("album_without_files")
    folder = _folder(version)
    release = db.get(Release, version.actual_release_id)
    if release is None:
        raise RetagRefused("album_without_files")
    album = tag_writing.album_tags(db, release, title)
    tracks = {row.id: row for row in db.scalars(select(ReleaseTrack).where(ReleaseTrack.release_id == release.id))}
    planned: list[Planned] = []
    for row in rows[:MAX_FILES]:
        # Since M6 a taken-over file may lie in a medium folder (``CD 01/…``).
        name = paths.display(row.relative_path)
        path = paths.file_in(folder, row.relative_path)
        track = tracks.get(row.track_id) if row.track_id is not None else None
        if track is None or path is None or files.is_link(path):
            continue
        planned.append(Planned(row.id, name, path, tag_writing.wanted(album, track)))
    return version, planned


def preview(title_id: int) -> dict[str, Any]:
    """``GET /api/music/albums/{id}/tags``: per file the fields that would change and whether it can be written."""
    with SessionLocal() as db:
        version, planned = _plan(db, title_id)
        enabled = tag_writing.write_enabled(db)
        covers_on = mb.covers_enabled(db)
        states = {
            row.id: row.tags_state for row in db.scalars(select(TrackFile).where(TrackFile.version_id == version.id))
        }
    out: list[dict[str, Any]] = []
    for item in planned:
        changes, refusal, _cover = tag_writing.preview_changes(item.path, item.wanted)
        out.append(
            {
                "track_file_id": item.track_file_id,
                "file": item.name,
                "tags_state": states.get(item.track_file_id),
                "refusal": refusal,
                "changes": [
                    {"field": change.field, "before": change.before, "after": change.after} for change in changes
                ],
            }
        )
    return {
        "title_id": title_id,
        "write_enabled": enabled,
        "cover": covers_on,
        "files": out,
        "changed": sum(1 for item in out if item["changes"] and item["refusal"] is None),
    }


def write(title_id: int) -> dict[str, Any]:
    """``POST /api/music/albums/{id}/tags``: write every file whose fields differ, with the cover when allowed."""
    with SessionLocal() as db:
        version, planned = _plan(db, title_id)
        covers_on = mb.covers_enabled(db)
        release_mbid = db.scalar(select(Release.mbid).where(Release.id == version.actual_release_id))
        group_mbid = db.scalar(select(Title.mbid).where(Title.id == title_id))
    cover = None
    if covers_on:
        try:
            cover = asyncio.run(covers.artwork(release_mbid, group_mbid, covers.EMBED_SIZE))
        except Exception:  # noqa: BLE001 - a cover never stops writing tags
            cover = None
    outcomes: dict[int, str] = {}
    for item in planned:
        changes, refusal, current_cover = tag_writing.preview_changes(item.path, item.wanted)
        cover_differs = tag_writing.cover_differs(current_cover, cover)
        if refusal is not None:
            outcomes[item.track_file_id] = (
                "linked" if refusal == "linked" else "format" if refusal == "format" else "failed"
            )
            continue
        if not changes and not cover_differs:
            continue
        outcomes[item.track_file_id] = tag_writing.state_of(
            item.path, item.wanted, cover if cover_differs else None, enabled=True
        )
    moment = utcnow()
    written = sum(1 for state in outcomes.values() if state == "written")
    with SessionLocal() as db:
        for track_file_id, state in outcomes.items():
            row = db.get(TrackFile, track_file_id)
            if row is None:
                continue
            row.tags_state = state
            if state == "written":
                row.tags_written_at = moment
                path = next(item.path for item in planned if item.track_file_id == track_file_id)
                try:
                    row.size = path.stat().st_size
                except OSError:
                    logger.info("Album %d: a retagged file could not be measured", title_id)
            row.updated_at = moment
        stored = db.get(Version, version.id)
        if stored is not None and written:
            failed = sum(1 for state in outcomes.values() if state == "failed")
            definition = db.get(VersionDefinition, stored.version_definition_id)
            db.add(
                HistoryEntry(
                    title_id=title_id,
                    version_id=stored.id,
                    version_definition_id=stored.version_definition_id,
                    version_label=definition.label if definition is not None else "",
                    event="tags_written",
                    at=moment,
                    detail=f"written={written} failed={failed}",
                )
            )
        db.commit()
    logger.info("Album %d: tags written into %d files, %d looked at", title_id, written, len(planned))
    return preview(title_id) | {"written": written}
