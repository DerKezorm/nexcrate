"""What the owner does with an album download nexcrate could not file by itself (M4.6).

* ``files_of`` is the dialog "Von Hand zuordnen": the audio files with what nexcrate read and proposes, the releases of
  the album with loaded tracks, the tracks of one release with what the album holds for each (decision 31). Files are
  named by a number (``key``), never by a path the browser could send back.
* ``assign`` files what the owner chose: a track, "fits no track, file anyway" (``loose``, decision 32), or nothing.
  Each track once, only tracks of the chosen release. The release can be changed only while no file of the download
  lies in the album folder. ``confirm: ["not_better"]`` replaces an album whose files are not worse (decision 20). The
  choice holds for this download only; the target release stays as it is (decision 33).
* ``finish`` is "Rest nicht ablegen" (decision 34): the download is ``imported`` with what is filed; afterwards
  SABnzbd's job folder goes. A torrent keeps its files.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select, update

from ...db import SessionLocal
from ...meldungen import meldung
from ...models import (
    Artist,
    Download,
    DownloadAudioFile,
    Release,
    ReleaseMedium,
    ReleaseTrack,
    Title,
    TrackFile,
)
from ..music import file_matching as fm
from . import album_import, importing, store
from .actions import ActionError, not_found, read

logger = logging.getLogger("nexcrate.downloads")

#: Problems whose card has "assign by hand".
ASSIGNABLE = ("files_unassigned", "album_not_better")
#: Problems whose card has "file nothing more".
FINISHABLE = (*ASSIGNABLE, "import_failed", "album_tracks_missing", "no_audio", "import_stalled")
#: Rows the owner may still decide.
OPEN_DECISIONS = (fm.OPEN, fm.OTHER_ALBUM, "filed", "not_filed")
_TAG_FIELDS = ("title", "artist", "album", "tracknumber", "discnumber")


def _not_assignable() -> ActionError:
    return ActionError(meldung("download_not_assignable", "This download has no files waiting for an assignment."), 409)


def _busy() -> ActionError:
    return ActionError(
        meldung("download_busy", "nexcrate is still working on this download. Please try again in a moment."), 409
    )


def _invalid(code: str, text: str, **values: Any) -> ActionError:
    return ActionError(meldung(code, text, **values), 422)


def _album_row(db: Any, download_id: int) -> Download:
    row = db.get(Download, download_id)
    if row is None or row.scope != store.ALBUM_SCOPE:
        raise not_found()
    return row


def files_of(download_id: int, release_id: int | None = None) -> dict[str, Any]:
    """``GET /api/downloads/{id}/album-files``; ``release_id`` shows the tracks of another release of the album."""
    with SessionLocal() as db:
        row = _album_row(db, download_id)
        title = db.get(Title, row.title_id)
        artist = db.get(Artist, title.artist_id) if title is not None and title.artist_id else None
        version = store.version_of(db, row.title_id, row.version_definition_id)
        releases = list(
            db.scalars(
                select(Release)
                .where(Release.title_id == row.title_id, Release.tracks_loaded.is_(True))
                .order_by(Release.date.nulls_last(), Release.id)
            )
        )
        by_id = {item.id: item for item in releases}
        shown = release_id if release_id in by_id else row.release_id
        if shown not in by_id:
            shown = version.target_release_id if version is not None and version.target_release_id in by_id else None
        rows = list(
            db.scalars(
                select(DownloadAudioFile).where(DownloadAudioFile.download_id == row.id).order_by(DownloadAudioFile.id)
            )
        )
        others = {
            item.id: item.title
            for item in db.scalars(
                select(Title).where(Title.id.in_({item.other_title_id for item in rows if item.other_title_id}))
            )
        }
        tracks: list[dict[str, Any]] = []
        if shown is not None:
            media = {
                item.id: item for item in db.scalars(select(ReleaseMedium).where(ReleaseMedium.release_id == shown))
            }
            held: dict[int, dict[str, Any]] = {}
            if version is not None:
                for item in db.scalars(select(TrackFile).where(TrackFile.version_id == version.id)):
                    for track_id in item.track_ids or []:
                        held[track_id] = {"quality": item.quality, "file": item.relative_path.rsplit("/", 1)[-1]}
            for track in db.scalars(select(ReleaseTrack).where(ReleaseTrack.release_id == shown)):
                medium = media.get(track.medium_id)
                tracks.append(
                    {
                        "id": track.id,
                        "medium": medium.position if medium is not None else 1,
                        "position": track.position,
                        "number": track.number,
                        "name": track.name,
                        "length_ms": track.length_ms,
                        "held": held.get(track.id),
                    }
                )
            tracks.sort(key=lambda item: (item["medium"], item["position"]))
        placed_any = any(item.track_file_id is not None for item in rows)
        return {
            "download_id": row.id,
            "kind": "album",
            "album": {"id": row.title_id, "title": title.title if title is not None else ""},
            "artist": artist.name if artist is not None else None,
            "version": {"id": row.version_definition_id, "label": row.version_label},
            "problem": {"code": row.problem_code, "values": dict(row.problem_values or {})}
            if row.problem_code
            else None,
            "release_id": shown,
            "read_release_id": row.release_id,
            "target_release_id": version.target_release_id if version is not None else None,
            "release_fixed": placed_any,
            "releases": [
                {
                    "id": item.id,
                    "name": item.name,
                    "date": item.date,
                    "country": item.country,
                    "formats": list(item.formats or []),
                    "media_count": item.media_count,
                    "track_count": item.track_count,
                    "disambiguation": item.disambiguation,
                }
                for item in releases
            ],
            "tracks": tracks,
            "files": [
                {
                    "key": item.id,
                    "path": item.path,
                    "size_bytes": item.size,
                    "duration_ms": (item.audio or {}).get("duration_ms"),
                    "codec": (item.audio or {}).get("codec"),
                    "bit_depth": (item.audio or {}).get("bit_depth"),
                    "bitrate": (item.audio or {}).get("bitrate"),
                    "tags": {name: ((item.tags or {}).get(name) or [None])[0] for name in _TAG_FIELDS},
                    "reading": item.reading,
                    "decision": item.decision,
                    "placed": item.track_file_id is not None,
                    "track_id": item.track_id,
                    "via": item.via,
                    "proposal": list(item.proposal or []),
                    "other_album": (
                        {"id": item.other_title_id, "title": others.get(item.other_title_id, "")}
                        if item.other_title_id
                        else None
                    ),
                }
                for item in rows
            ],
        }


def assign(
    download_id: int,
    release_id: int | None,
    chosen: list[tuple[int, int | None, bool]],
    confirm: list[str],
) -> dict[str, Any]:
    """``POST /api/downloads/{id}/album-assign``: ``chosen`` holds per file its key, a track id, and whether it is filed
    without a track. Checks, then files in the import's thread."""
    moment = store.now()
    with SessionLocal() as db:
        row = _album_row(db, download_id)
        if row.state != "problem" or row.problem_code not in ASSIGNABLE:
            raise _not_assignable()
        if importing.running(download_id):
            raise _busy()
        problem_code, problem_values = row.problem_code, row.problem_values
        rows = {
            item.id: item
            for item in db.scalars(select(DownloadAudioFile).where(DownloadAudioFile.download_id == row.id))
        }
        placed = [item for item in rows.values() if item.track_file_id is not None]
        release = release_id if release_id is not None else row.release_id
        found = db.get(Release, release) if release is not None else None
        if found is None or found.title_id != row.title_id or not found.tracks_loaded:
            raise _invalid("release_not_of_album", "The release does not belong to this album.")
        if placed and release != row.release_id:
            raise _invalid(
                "release_fixed", "Files of this download lie in the album folder already; the release stays."
            )
        tracks = set(db.scalars(select(ReleaseTrack.id).where(ReleaseTrack.release_id == release)))
        held = {item.track_id for item in placed if item.track_id is not None}
        seen: set[int] = set()
        mapping: dict[int, int | str | None] = {}
        for key, track_id, loose in chosen:
            item = rows.get(key)
            if item is None or item.track_file_id is not None or item.decision not in OPEN_DECISIONS:
                raise _not_assignable()
            if loose:
                mapping[key] = album_import.LOOSE
                continue
            if track_id is None:
                mapping[key] = None
                continue
            if track_id not in tracks:
                raise _invalid("track_not_in_release", "A track does not belong to the chosen release.")
            if track_id in seen or track_id in held:
                raise _invalid("track_twice", "A track is chosen for more than one file.")
            seen.add(track_id)
            mapping[key] = track_id
        if not mapping:
            raise _invalid("invalid_input", "The input is not valid.", fields=["files"])
        claimed = db.execute(
            update(Download)
            .where(Download.id == row.id, Download.state == "problem")
            .values(state="importing", problem_code=None, problem_values=None, updated_at=moment)
        )
        if not getattr(claimed, "rowcount", 0):
            raise _not_assignable()
        db.commit()
    manual = album_import.Manual(release_id=release, chosen=mapping, confirm=frozenset(confirm))
    logger.info("Album download %d: the owner assigned %d files", download_id, len(mapping))
    if not importing.request(download_id, work=lambda: album_import.run(download_id, manual=manual)):
        with SessionLocal() as db:
            db.execute(
                update(Download)
                .where(Download.id == download_id, Download.state == "importing")
                .values(state="problem", problem_code=problem_code, problem_values=problem_values, updated_at=moment)
            )
            db.commit()
        raise _busy()
    return read(download_id)


def finish(download_id: int) -> dict[str, Any]:
    """``POST /api/downloads/{id}/finish`` for an album: the rest is not filed; the download is ``imported``."""
    moment = store.now()
    with SessionLocal() as db:
        row = _album_row(db, download_id)
        if row.state != "problem" or row.problem_code not in FINISHABLE:
            raise _not_assignable()
        if importing.running(download_id):
            raise _busy()
        left = 0
        filed = 0
        for item in db.scalars(select(DownloadAudioFile).where(DownloadAudioFile.download_id == row.id)):
            if item.track_file_id is not None:
                filed += 1
            else:
                item.decision, item.target = "not_filed", None
                left += 1
        row.state, row.problem_code, row.problem_values = "imported", None, None
        row.open_count = 0
        row.imported_at = moment
        row.updated_at = moment
        store.add_history(db, row, "album_filed", f"filed={filed} not_filed={left} download={row.id}", moment)
        store.follow(db, row, moment)
        db.commit()
    logger.info("Album download %d: the rest is not filed (%d files)", download_id, left)
    importing.request(download_id, work=lambda: album_import.clean_after_owner(download_id))
    return read(download_id)
