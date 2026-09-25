"""The album block of the title page (decision 40): the credit, the target release with its
reasons, the suggestion, the tracks of the target with what lies on disk, and the releases without their tracks."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ...models import (
    Artist,
    Download,
    DownloadAudioFile,
    Release,
    ReleaseMedium,
    ReleaseTrack,
    Title,
    TrackFile,
    Version,
)
from . import kinds, paths, same_song


def _release_head(release: Release | None) -> dict[str, Any] | None:
    if release is None:
        return None
    return {
        "id": release.id,
        "mbid": release.mbid,
        "name": release.name,
        "date": release.date,
        "country": release.country,
        "formats": list(release.formats or []),
        "media_count": release.media_count,
        "track_count": release.track_count,
        "labels": list(release.labels or []),
        "disambiguation": release.disambiguation,
        "audio_only": release.audio_only,
        "tracks_loaded": release.tracks_loaded,
        "gone": release.mb_gone_at is not None,
    }


def _credit(db: OrmSession, title: Title) -> list[dict[str, Any]]:
    parts = list(title.artist_credit or [])
    mbids = [str(part.get("mbid") or "") for part in parts if part.get("mbid")]
    known = (
        dict(db.execute(select(Artist.mbid, Artist.id).where(Artist.mbid.in_(mbids))).tuples().all()) if mbids else {}
    )
    if not parts and title.artist_id is not None:
        artist = db.get(Artist, title.artist_id)
        if artist is not None:
            return [{"mbid": artist.mbid, "name": artist.name, "join": "", "artist_id": artist.id}]
    return [
        {
            "mbid": str(part.get("mbid") or ""),
            "name": str(part.get("name") or part.get("artist_name") or ""),
            "join": str(part.get("join") or ""),
            "artist_id": known.get(str(part.get("mbid") or "")),
        }
        for part in parts
    ]


def _origins(db: OrmSession, file_ids: set[int]) -> dict[int, dict[str, Any]]:
    """Per track file of nexcrate's own downloads: the download, its release name, the file's path in it and ``via``."""
    if not file_ids:
        return {}
    found: dict[int, dict[str, Any]] = {}
    rows = db.execute(
        select(DownloadAudioFile, Download.release_title, Download.imported_at)
        .join(Download, Download.id == DownloadAudioFile.download_id)
        .where(DownloadAudioFile.track_file_id.in_(file_ids))
        .order_by(DownloadAudioFile.id)
    ).tuples()
    for row, release_title, imported_at in rows:
        # The newest download that filed the file wins.
        found[row.track_file_id] = {
            "download_id": row.download_id,
            "download_release": release_title,
            "download_path": row.path,
            "via": row.via,
            "filed_at": imported_at.isoformat() if imported_at is not None else None,
        }
    return found


def _tracks_of(db: OrmSession, version: Version | None, release: Release | None) -> list[dict[str, Any]]:
    if release is None:
        return []
    media = {row.id: row for row in db.scalars(select(ReleaseMedium).where(ReleaseMedium.release_id == release.id))}
    tracks = list(
        db.scalars(
            select(ReleaseTrack)
            .where(ReleaseTrack.release_id == release.id)
            .order_by(ReleaseTrack.medium_id, ReleaseTrack.position)
        )
    )
    files: dict[int, TrackFile] = {}
    if version is not None:
        for row in db.scalars(select(TrackFile).where(TrackFile.version_id == version.id)):
            if row.track_id is not None:
                files[row.track_id] = row
            for covered in row.track_ids or []:
                # One file that holds several tracks (B3): each of them is present.
                files[covered] = row
    # Files of another release than the target (an album "Vorerst", the design notes, decision 21) count for the
    # target's track with the same recording.
    # Since 19.09.2026 also by a name unique in its release (``same_song``): a remaster may give a track another
    # recording.
    by_song: dict[str, TrackFile] = {}
    keys: dict[int, frozenset[str]] = {}
    foreign = [track_id for track_id in files if track_id not in {track.id for track in tracks}]
    if foreign:
        release_ids = set(db.scalars(select(ReleaseTrack.release_id).where(ReleaseTrack.id.in_(foreign))))
        others: dict[int, list[ReleaseTrack]] = {}
        for row in db.scalars(select(ReleaseTrack).where(ReleaseTrack.release_id.in_(release_ids))):
            others.setdefault(row.release_id, []).append(row)
        keys = same_song.keys_by_track([tracks, *others.values()])
        for track_id in foreign:
            for key in keys.get(track_id, frozenset()):
                by_song.setdefault(key, files[track_id])
    origins = _origins(db, {file.id for file in files.values()})
    items = []
    for track in tracks:
        medium = media.get(track.medium_id)
        file = files.get(track.id) or next(
            (by_song[key] for key in sorted(keys.get(track.id, frozenset())) if key in by_song), None
        )
        items.append(
            {
                "id": track.id,
                "medium": medium.position if medium is not None else 1,
                "medium_format": medium.format if medium is not None else None,
                "position": track.position,
                "number": track.number,
                "name": track.name,
                "length_ms": track.length_ms,
                "artist_credit": track.artist_credit,
                "present": file is not None,
                "file": (
                    {
                        "id": file.id,
                        "relative_path": file.relative_path,
                        "size": file.size,
                        "quality": file.quality,
                        "tags_state": file.tags_state,
                        # Where the file came from (the owner's wish of 19.09.2026): a download of nexcrate with the
                        # file's name there and how it was matched, or a Lidarr import.
                        "source": (
                            "lidarr"
                            if file.source_file_id is not None
                            else ("download" if file.id in origins else None)
                        ),
                        **origins.get(file.id, {}),
                    }
                    if file is not None
                    else None
                ),
            }
        )
    items.sort(key=lambda item: (item["medium"], item["position"]))
    return items


def _unclear_of(db: OrmSession, version: Version | None) -> list[dict[str, Any]]:
    if version is None:
        return []
    return [
        {"id": row.id, "relative_path": paths.display(row.relative_path), "size": row.size, "quality": row.quality}
        for row in db.scalars(
            select(TrackFile)
            .where(TrackFile.version_id == version.id, TrackFile.unclear.is_(True))
            .order_by(TrackFile.relative_path)
        )
    ]


def album_block(db: OrmSession, title: Title, versions: list[Version]) -> dict[str, Any]:
    """Everything the album page needs beyond the common title fields."""
    version = versions[0] if versions else None
    artist = db.get(Artist, title.artist_id) if title.artist_id is not None else None
    target = db.get(Release, version.target_release_id) if version is not None and version.target_release_id else None
    suggestion = (
        db.get(Release, version.target_suggestion_id) if version is not None and version.target_suggestion_id else None
    )
    actual = db.get(Release, version.actual_release_id) if version is not None and version.actual_release_id else None
    releases = list(
        db.scalars(
            select(Release)
            .where(Release.title_id == title.id)
            .order_by(Release.date.nulls_last(), Release.country, Release.id)
        )
    )
    return {
        "mbid": title.mbid,
        "artist_id": title.artist_id,
        "artist_name": artist.name if artist is not None else None,
        "credit": _credit(db, title),
        "primary_type": title.primary_type,
        "secondary_types": list(title.secondary_types or []),
        "group": kinds.group_of(title.primary_type, title.secondary_types),
        "first_release_date": title.first_release_date,
        "disambiguation": title.release_group_disambiguation,
        "releases_state": title.releases_state or "none",
        "releases_refreshed_at": title.releases_refreshed_at,
        "mb_gone_at": title.mb_gone_at,
        "version_id": version.id if version is not None else None,
        "monitored": bool(version is not None and version.monitored),
        "target": _release_head(target),
        "target_set_by": version.target_set_by if version is not None else None,
        "target_reason": version.target_reason if version is not None else None,
        "suggestion": _release_head(suggestion),
        "actual": _release_head(actual),
        "track_counts": version.track_counts if version is not None else None,
        "tracks": _tracks_of(db, version, target),
        "releases": [_release_head(release) for release in releases],
        # Music M6 (decision 15): files of the album folder without a track, and whether the
        # folder can be read again (an own version with an album folder).
        "unclear_files": _unclear_of(db, version),
        "can_read_folder": bool(version is not None and version.source_id is None and version.relative_path),
        "files_read_at": version.files_read_at if version is not None else None,
    }
