"""The tables of music (M1.0): artists, releases, media, tracks, track files.

An album is a ``Title`` with ``kind = "album"``, one per MusicBrainz release group, and an album version a ``Version``
of it, as for movies and series. An artist is not a title: it has no version, no state and no download. Below them:

* ``artists``: one row per MusicBrainz artist in the library, with the state of its background loading.
* ``album_artists``: every further artist of an album's credit, so a joint album shows on both artist pages.
* ``releases``: the official releases of an album, as MusicBrainz cuts them; ``release_media`` their media and
  ``release_tracks`` the tracks per medium, each with the recording id that ties the same recording across releases.
* ``track_files``: one file per track of a version, with the track of the actual release when known.
* ``source_unmapped_files``: files a Lidarr connection has but could not map, as a count and a list for the owner.

Dates are plain ``YYYY-MM-DD`` texts as MusicBrainz gives them, sometimes only ``YYYY`` or ``YYYY-MM``.

⚠️ Ids are never reused (``sqlite_autoincrement``): history and later downloads point at a release for good.
⚠️ MusicBrainz merges entries. A merged id answers with the surviving id; the old one goes to ``mbid_old`` and must
never count as deleted (decision 22).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, utcnow

#: The steps of loading an artist from MusicBrainz (decision 19): waiting, browsing its release groups, loading the
#: releases and tracks of the albums that need them, done, or failed with ``load_error``.
LOAD_STATES = ("queued", "groups", "releases", "ready", "failed")
#: The rule for albums that appear later (decision 24): ``all`` watches new studio albums and EPs, ``none`` nothing.
MONITOR_NEW = ("all", "none")
#: Who brought an artist: the owner, or an import from Lidarr.
ARTIST_ADDED_BY = ("owner", "import")
#: The state of an album's releases (decision 6): none loaded, only the heads, with tracks, or failed.
RELEASES_STATES = ("none", "heads", "tracks", "failed")
#: Who chose the target release of an album version (decision 7): the rule, the owner, or the source that feeds it.
TARGET_SET_BY = ("rule", "owner", "source")
#: Where the tags of a filed file come from (decision 30): as the download had them, written by
#: nexcrate, not written because the file has a second link, because its format is read only, because writing is
#: switched off, or because writing failed.
TAG_STATES = ("download", "written", "linked", "format", "off", "failed")
#: MusicBrainz's id of the collective artist for compilations (decision 35). Never a search hit, never browsed.
VARIOUS_ARTISTS_MBID = "89ad4ac3-39f7-470e-963a-56509c546377"


class Artist(Base):
    """One MusicBrainz artist in the library, with its loading state and its rule for new albums."""

    __tablename__ = "artists"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mbid: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    #: Earlier ids MusicBrainz merged into this one.
    mbid_old: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    name: Mapped[str] = mapped_column(String(1024))
    sort_name: Mapped[str] = mapped_column(String(1024), default="")
    disambiguation: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    #: MusicBrainz's type as it comes: ``Person``, ``Group``, ``Orchestra``, ``Choir``, ``Character``, ``Other``.
    artist_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    country: Mapped[str | None] = mapped_column(String(8), nullable=True)
    begin_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ended: Mapped[bool] = mapped_column(Boolean, default=False)
    #: MusicBrainz's aliases: name, sort name, locale, primary, type. Kept for the search and the second line.
    aliases: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    #: The primary alias in the language of the interface, shown as a second line (decision 36).
    alias_display: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    #: Comparison keys of name and aliases, ``|key|key|``.
    search_keys: Mapped[str] = mapped_column(Text, default="")
    #: Form (a) of the sort name, see ``schreibweisen.sort_key``.
    sort_key: Mapped[str] = mapped_column(String(1024), default="", index=True)
    #: The collective artist for compilations: never browsed, never added by hand.
    is_various: Mapped[bool] = mapped_column(Boolean, default=False)
    #: ``all`` or ``none``, see ``MONITOR_NEW``.
    monitor_new: Mapped[str] = mapped_column(String(8), default="all")
    #: MusicBrainz's count of release groups, from the first page of browsing.
    groups_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: See ``LOAD_STATES``.
    load_state: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    load_error: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Progress of the current step: pages while browsing groups, albums while loading releases.
    load_done: Mapped[int] = mapped_column(Integer, default=0)
    load_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Lower runs first (decision 20): 0 the owner just added it, 1 an album page waits, 2 an import, 3 a refresh.
    load_priority: Mapped[int] = mapped_column(Integer, default=0)
    #: When the release groups were last browsed, and when they are due again (decision 21).
    groups_refreshed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    groups_due_at: Mapped[datetime | None] = mapped_column(nullable=True, index=True)
    #: When MusicBrainz last answered 404 for the id; the artist stays (decision 22).
    mb_gone_at: Mapped[datetime | None] = mapped_column(nullable=True)
    #: ``owner`` or ``import``, see ``ARTIST_ADDED_BY``.
    added_by: Mapped[str] = mapped_column(String(16), default="owner")
    added: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
    #: The artist folder below the music version's folder, fixed once a file lies in it (the design notes,
    #: decision 16). ⚠️ From migration 16.
    folder: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # ⚠️ The three columns below come from the missing-column upkeep; nullable and without a
    # default on purpose, or every INSERT of an older table shape would carry them.
    #: Set while the artist is frozen, as unmonitoring an artist in Lidarr: nothing of it is searched on its own, the
    #: switches of its albums stay as they are (fork 3). Null is watched.
    frozen_at: Mapped[datetime | None] = mapped_column(nullable=True)
    #: The groups of an artist page (``kinds.GROUPS``) whose new albums get watched, like Lidarr's metadata profile.
    #: Null is studio albums and EPs (decision 25).
    album_types: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    #: The program's own reference when a program added the artist through ``/api/v1`` (N19), and its key's name.
    origin: Mapped[str | None] = mapped_column(String(100), nullable=True)
    origin_key: Mapped[str | None] = mapped_column(String(100), nullable=True)


class AlbumArtist(Base):
    """A further artist of an album's credit (decision 3): the album shows on this artist's page too."""

    __tablename__ = "album_artists"

    title_id: Mapped[int] = mapped_column(ForeignKey("titles.id", ondelete="CASCADE"), primary_key=True)
    artist_id: Mapped[int] = mapped_column(
        ForeignKey("artists.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    #: The position in the credit, 0 first.
    position: Mapped[int] = mapped_column(Integer, default=0)


class Release(Base):
    """One official release of an album, as MusicBrainz cuts it (decision 4)."""

    __tablename__ = "releases"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title_id: Mapped[int] = mapped_column(ForeignKey("titles.id", ondelete="CASCADE"), index=True)
    mbid: Mapped[str] = mapped_column(String(36), unique=True)
    mbid_old: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    name: Mapped[str] = mapped_column(String(1024))
    #: MusicBrainz's status, lower case: ``official`` here; others are never stored (decision 5).
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    country: Mapped[str | None] = mapped_column(String(8), nullable=True)
    disambiguation: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    barcode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Labels with catalog numbers: ``[{"name": ..., "catalog_number": ...}]``.
    labels: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    packaging: Mapped[str | None] = mapped_column(String(64), nullable=True)
    media_count: Mapped[int] = mapped_column(Integer, default=0)
    track_count: Mapped[int] = mapped_column(Integer, default=0)
    #: The formats of the media in order, ``["CD", "CD"]``.
    formats: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    #: Whether every medium is audio (decision 27, step 1): no DVD, Blu-ray, video or data medium.
    audio_only: Mapped[bool] = mapped_column(Boolean, default=True)
    #: Whether the tracks of this release are stored (decision 6).
    tracks_loaded: Mapped[bool] = mapped_column(Boolean, default=False)
    mb_gone_at: Mapped[datetime | None] = mapped_column(nullable=True)


class ReleaseMedium(Base):
    """One medium of a release: a CD, a side of a record, a download."""

    __tablename__ = "release_media"
    __table_args__ = (
        UniqueConstraint("release_id", "position", name="uq_release_media_release_position"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    release_id: Mapped[int] = mapped_column(ForeignKey("releases.id", ondelete="CASCADE"), index=True)
    #: 1 for the first medium.
    position: Mapped[int] = mapped_column(Integer)
    format: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    track_count: Mapped[int] = mapped_column(Integer, default=0)


class ReleaseTrack(Base):
    """One track of one medium of one release. ``recording_mbid`` ties the same recording across releases."""

    __tablename__ = "release_tracks"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    release_id: Mapped[int] = mapped_column(ForeignKey("releases.id", ondelete="CASCADE"), index=True)
    medium_id: Mapped[int] = mapped_column(ForeignKey("release_media.id", ondelete="CASCADE"), index=True)
    #: MusicBrainz's track id, unique per release.
    mbid: Mapped[str] = mapped_column(String(36), unique=True)
    recording_mbid: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    #: 1 for the first track of the medium.
    position: Mapped[int] = mapped_column(Integer)
    #: The number as MusicBrainz prints it, ``A1`` on a record.
    number: Mapped[str | None] = mapped_column(String(16), nullable=True)
    name: Mapped[str] = mapped_column(String(1024))
    length_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: The credit of the track when it differs from the album's, ``[{"mbid": ..., "name": ..., "join": ...}]``.
    artist_credit: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)


class TrackFile(Base):
    """One audio file of an album version (decision 8), with the track of the actual release when known."""

    __tablename__ = "track_files"
    __table_args__ = (
        UniqueConstraint("version_id", "relative_path", name="uq_track_files_version_path"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("versions.id", ondelete="CASCADE"), index=True)
    #: Null while unknown, or when the file covers several tracks (``source_track_ids``).
    track_id: Mapped[int | None] = mapped_column(
        ForeignKey("release_tracks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: Relative to the album folder of the version, or to the source's root folder for an imported file.
    relative_path: Mapped[str] = mapped_column(String(2048))
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    #: The quality as the source or the file says it, ``FLAC``, ``MP3-320``.
    quality: Mapped[str | None] = mapped_column(String(100), nullable=True)
    #: Codec, bit depth, sample rate, channels, bitrate in nexcrate's own short shape.
    audio: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    #: Lidarr's ``trackfile`` id, to notice a new file without reading it.
    source_file_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Lidarr's track ids when one file covers several tracks (decision 49).
    source_track_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    #: The tracks of the actual release such a file covers (``release_tracks.id``), so each shows as present.
    track_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    added_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
    # ⚠️ The two columns below come from migration 16 (decision 30).
    #: Where the tags of the file come from: see ``TAG_STATES``; null for a file a source feeds.
    tags_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    tags_written_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # ⚠️ From migration 17 (decision 15).
    #: A file of the album folder without a track that the owner has not settled yet.
    unclear: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))


class SourceUnmappedFile(Base):
    """A file a Lidarr connection has but could not map to a track (decision 50)."""

    __tablename__ = "source_unmapped_files"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(String(4096))
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    quality: Mapped[str | None] = mapped_column(String(100), nullable=True)
    seen_at: Mapped[datetime] = mapped_column(default=utcnow)
