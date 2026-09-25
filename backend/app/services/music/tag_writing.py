"""The tags nexcrate writes into filed music, and the retag preview (decisions 24 to 30).

* **What:** Picard's fields (``tags.FIELDS``) for the **actual** release, built from the database only (decision 25).
  A field MusicBrainz leaves empty is left out, so a value the download had stays; ``compilation`` is always set.
* **When:** right after a file is filed and before it gets its final name, or from the preview. Only with the switch
  on, only into a file with one link (research T1), only in the formats ``tags.WRITABLE``.
* **Never** for a file of a version a connection feeds: those files are Lidarr's.
* ``state_of`` names what came of a file for ``track_files.tags_state``.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import mutagen
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ...db import get_setting, set_setting
from ...models import Artist, Release, ReleaseMedium, ReleaseTrack, Title
from ...models.music import VARIOUS_ARTISTS_MBID
from . import tags

logger = logging.getLogger("nexcrate.music")

SETTING_WRITE = "music_write_tags"


def write_enabled(db: OrmSession) -> bool:
    """Ab Werk an (decision 24)."""
    return get_setting(db, SETTING_WRITE, "1") != "0"


def set_write_enabled(db: OrmSession, value: bool) -> None:
    set_setting(db, SETTING_WRITE, "1" if value else "0")


# --- What to write ------------------------------------------------------------------------------------------------ #


def credit_text(credit: list[dict[str, Any]] | None) -> str:
    """``A feat. B``: the names with MusicBrainz's join phrases."""
    return "".join(f"{item.get('name') or ''}{item.get('join') or ''}" for item in credit or []).strip()


def _credit_names(credit: list[dict[str, Any]] | None) -> list[str]:
    return [str(item["name"]) for item in credit or [] if item.get("name")]


def _credit_ids(credit: list[dict[str, Any]] | None) -> list[str]:
    return [str(item["mbid"]) for item in credit or [] if item.get("mbid")]


@dataclass(frozen=True)
class AlbumTags:
    """What every track of one release shares, read once."""

    release: Release
    title: Title
    artist_sort: str | None
    media: dict[int, ReleaseMedium]


def album_tags(db: OrmSession, release: Release, title: Title) -> AlbumTags:
    media = {row.id: row for row in db.scalars(select(ReleaseMedium).where(ReleaseMedium.release_id == release.id))}
    sort: str | None = None
    credit = title.artist_credit or []
    if len(credit) == 1 and credit[0].get("mbid"):
        sort = db.scalar(select(Artist.sort_name).where(Artist.mbid == credit[0]["mbid"]))
    return AlbumTags(release=release, title=title, artist_sort=sort, media=media)


def wanted(album: AlbumTags, track: ReleaseTrack) -> dict[str, list[str]]:
    """The fields for one track of the album's actual release (decision 25)."""
    release, title = album.release, album.title
    medium = album.media.get(track.medium_id)
    track_credit = track.artist_credit or title.artist_credit or []
    album_credit = title.artist_credit or []
    types = [value.lower() for value in [title.primary_type, *(title.secondary_types or [])] if value]
    labels = release.labels or []
    compilation = any(item.get("mbid") == VARIOUS_ARTISTS_MBID for item in album_credit)
    values: dict[str, list[str]] = {
        "title": [track.name],
        "artist": [credit_text(track_credit)],
        "artists": _credit_names(track_credit),
        "album": [release.name],
        "albumartist": [credit_text(album_credit)],
        "albumartistsort": [album.artist_sort or ""],
        "tracknumber": [str(track.position)],
        "totaltracks": [str(medium.track_count)] if medium is not None and medium.track_count else [],
        "discnumber": [str(medium.position)] if medium is not None else [],
        "totaldiscs": [str(release.media_count)] if release.media_count else [],
        "date": [release.date or ""],
        "originaldate": [title.first_release_date or ""],
        "releasecountry": [release.country or ""],
        "releasestatus": [release.status or ""],
        "releasetype": types,
        "media": [medium.format] if medium is not None and medium.format else [],
        "label": [str(item["name"]) for item in labels if item.get("name")],
        "catalognumber": [str(item["catalog_number"]) for item in labels if item.get("catalog_number")],
        "barcode": [release.barcode or ""],
        "compilation": ["1"] if compilation else [],
        "musicbrainz_trackid": [track.recording_mbid or ""],
        "musicbrainz_releasetrackid": [track.mbid],
        "musicbrainz_albumid": [release.mbid],
        "musicbrainz_releasegroupid": [title.mbid or ""],
        "musicbrainz_artistid": _credit_ids(track_credit),
        "musicbrainz_albumartistid": _credit_ids(album_credit),
    }
    # A field MusicBrainz leaves empty stays as the download had it; "compilation" is nexcrate's to say.
    return {
        name: [value for value in found if value]
        for name, found in values.items()
        if name == "compilation" or any(value for value in found)
    }


# --- Writing -------------------------------------------------------------------------------------------------------- #


def state_of(path: Path, fields: dict[str, list[str]], cover: tuple[bytes, str] | None, *, enabled: bool) -> str:
    """Write the fields into one file and name what came of it (``models.music.TAG_STATES``). Never raises."""
    if not enabled:
        return "off"
    try:
        tags.write(path, fields, cover)
    except tags.WriteRefused as exc:
        return "linked" if exc.code == "linked" else "format" if exc.code == "format" else "failed"
    except OSError, mutagen.MutagenError, ValueError, TypeError, KeyError:
        logger.info("Writing the tags of a filed music file failed")
        return "failed"
    return "written"


# --- The preview ------------------------------------------------------------------------------------------------ # #


def cover_differs(current_sha256: str | None, cover: tuple[bytes, str] | None) -> bool:
    if cover is None:
        return False
    return current_sha256 != hashlib.sha256(cover[0]).hexdigest()


def preview_changes(path: Path, fields: dict[str, list[str]]) -> tuple[list[tags.Change], str | None, str | None]:
    """The fields that would change in one file, whether it could be written (``linked``, ``format``, ``unreadable``),
    and the hash of its front cover."""
    read = tags.read(path)
    if read.audio is None:
        return [], tags.UNREADABLE, None
    refusal = None
    if read.writable is None:
        refusal = "format"
    elif tags.links(path) != 1:
        refusal = "linked"
    return tags.differences(read.tags, fields), refusal, read.cover_sha256
