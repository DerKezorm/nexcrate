"""The media tables: version definitions, sources, import runs, titles, their versions, indexers.

A title is one movie, identified by its TMDB id. A version is that movie in one version definition:
the file, the quality, the state. A movie in two Radarr instances is one title with two versions.
Since step 2a the owner can add a title and its versions himself; such a version has no source
until an import brings the same movie and takes it over.

⚠️ Ids of titles, sources, versions and runs are never reused (``sqlite_autoincrement``). The
interface links to ``/titel/<id>`` and caches posters by title id; a reused id would show a
deleted movie's picture on a new one.

Every text that comes from Radarr is stored in Unicode NFC (see ``services/schreibweisen.py``).
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, utcnow

#: Media kinds. Movies since step 1, series since S1.
KINDS = ("movie", "series", "album")
#: Apps a source can be: Radarr feeds a movie version, Sonarr (since S1) a series version, Lidarr (since M1) the one
#: album version.
APPS = ("radarr", "sonarr", "lidarr")
#: The media kind of the version an app feeds.
APP_KINDS = {"radarr": "movie", "sonarr": "series", "lidarr": "album"}
#: States of a version, in the order of precedence: the first that applies wins.
#: ``incomplete`` since 19.09.2026: an album with files that lacks tracks of its target release.
STATES = ("problem", "downloading", "incomplete", "upgrade", "available", "wanted", "unmonitored")
RUN_STATUSES = ("running", "done", "failed")
#: ``grabbed`` and ``failed`` since step 3: a release handed to a download client, a download that failed.
#: ``taken_over`` since the takeover: a version of a Radarr connection became nexcrate's own.
#: ``found_on_disk`` and ``restored`` since the library from disk: a folder assigned by hand, a version restored from
#: its ``release.nex``. ``renumbered`` and ``episodes_late`` since S1: TMDB gave an episode other numbers, or added
#: episodes long after they aired (decisions 5 and 11). ``episodes_filed`` and
#: ``episode_renamed`` since S4: a series download filed, a file named with TBA renamed.
#: ``file_relinked`` since S6: the repair of a wrong bridge to TVDB moved a taken-over file, detail ``old new`` codes.
#: ``album_appeared``, ``album_type_changed``, ``album_gone`` and ``target_changed`` since M1 (the design notes,
#: decisions 22 to 24, 29): MusicBrainz showed a new album of a watched artist, changed an album's type, stopped
#: listing one, or the target release of an album version changed. ``album_filed`` and ``tags_written`` since M4
#:: an album download filed, the tags of an album's files written from the preview.
#: ``renamed`` and ``rename_undone`` since the mass rename: files and folders of a version
#: renamed, and taken back; detail ``<files> <folders>``.
#: ``requested`` and ``withdrawn`` since /api/v1 V2: a program asked for a version or took it
#: back; detail the key's name, after a line break the program's origin. ``files_deleted`` and ``file_restored``:
#: files into the recycle bin and one back; detail the count, after a space the key's name when a program did it.
#: ``operated`` since V3: a program acted on a download (retry, remove, assign ...); detail the
#: action, after a space the key's name.
HISTORY_EVENTS = (
    "added", "imported", "grabbed", "failed", "taken_over", "takeover_undone", "found_on_disk", "restored",
    "renumbered", "episodes_late", "episodes_filed", "episode_renamed", "file_relinked",
    "album_appeared", "album_type_changed", "album_gone", "target_changed", "album_filed", "tags_written",
    "renamed", "rename_undone", "requested", "withdrawn", "files_deleted", "file_restored", "operated", "relocated",
)  # fmt: skip
#: Who brought a version: an import, or the owner. A version the owner added keeps ``owner`` when a
#: source takes it over.
ADDED_BY = ("import", "owner")
#: Where a title's poster comes from.
POSTER_ORIGINS = ("source", "tmdb")
INDEXER_KINDS = ("newznab", "torznab")


def new_public_id() -> str:
    """What ``/api/v1`` calls a version: ``v_`` and eight hex characters, never the row number."""
    return "v_" + secrets.token_hex(4)


class VersionDefinition(Base):
    """A named slot per media kind, for example "HD" or "4K" for movies."""

    __tablename__ = "version_definitions"
    __table_args__ = (
        UniqueConstraint("kind", "label", name="uq_version_definitions_kind_label"),
        # Music has exactly one version (decision 10): two requests at once cannot make two.
        Index("uq_version_definitions_one_album", "kind", unique=True, sqlite_where=text("kind = 'album'")),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), default="movie")
    label: Mapped[str] = mapped_column(String(64))
    #: The profile this version judges by, or null while it has none. ⚠️ From migration 20: several versions may
    #: point at the same profile, and removing a version leaves the profile alone.
    profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # ⚠️ Unused since a version became only a name; the API neither shows nor takes them. Existing
    # databases have these three columns as NOT NULL without a database default, so an insert that
    # leaves them out fails there. The defaults fill them. Do not drop them: nothing drops columns.
    profile_name: Mapped[str] = mapped_column(String(200), default="")
    root_folder: Mapped[str] = mapped_column(String(1024), default="")
    auto_add: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    #: The folder imported files of this version go into (step 3), chosen from the folders nexcrate sees. Null until
    #: the owner picks one. ⚠️ Added by migration 4; a change needs a new migration.
    folder: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    # ⚠️ The naming of a version comes from migration 5; a change needs a new migration.
    #: The movie folder pattern of this version; null with ``naming_movie_file`` null: the default naming.
    naming_movie_folder: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    #: The movie file pattern of this version; set and removed together with ``naming_movie_folder``.
    naming_movie_file: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # ⚠️ The series naming of a version comes from migration 11 (S4.2); a change needs a new
    # migration. Null means the default pattern of the settings.
    naming_series_folder: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    naming_season_folder: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    naming_specials_folder: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    naming_episode_file: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    naming_daily_file: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # Added by the column upkeep (A5), no migration.
    naming_anime_file: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    #: ``tmdb`` or ``tvdb``: the numbers in the names of this series version (decision 14). Null means ``tmdb``.
    episode_numbering: Mapped[str | None] = mapped_column(String(8), nullable=True)
    #: The countries that break a tie for the target release of an album version, in order (the design notes,
    #: decision 28). Null means the list of the interface language. ⚠️ From migration 14.
    music_countries: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    #: The delay rule of this version (``services/delay.py``): preferred protocol, a protocol switched off, minutes to
    #: wait. Null means the apps' defaults: Usenet preferred, no waiting. Nullable, so it comes without a migration.
    delay: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    #: What ``/api/v1`` calls this version (``v_7c1e90ab``): fixed for good, not guessable, and no row number, so that
    #: a program's rights can hang on it (N9). Nullable, so it comes without a migration; rows
    #: from before get theirs when ``/api/v1`` first names them (``services/api_v1/versions.ensure_public_ids``).
    public_id: Mapped[str | None] = mapped_column(String(16), nullable=True, default=new_public_id)


class Source(Base):
    """A Radarr instance that feeds exactly one version definition. Read only.

    ``api_key`` holds the encrypted key (``enc:...``), never the plain one. A taken-over source stays as a record with
    ``taken_over_at`` and an empty key: nexcrate never reads it again.
    """

    __tablename__ = "sources"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    app: Mapped[str] = mapped_column(String(16), default="radarr")
    url: Mapped[str] = mapped_column(String(1024))
    api_key: Mapped[str] = mapped_column(Text, default="")
    version_id: Mapped[int] = mapped_column(
        ForeignKey("version_definitions.id", ondelete="RESTRICT"), unique=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
    #: When its versions became nexcrate's own. Null for a source nexcrate still reads. ⚠️ From migration 5.
    taken_over_at: Mapped[datetime | None] = mapped_column(nullable=True)


class ImportRun(Base):
    """One import of one source, with its counts. ``error_code`` names what went wrong."""

    __tablename__ = "import_runs"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="running")
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    titles_new: Mapped[int] = mapped_column(Integer, default=0)
    titles_updated: Mapped[int] = mapped_column(Integer, default=0)
    versions_total: Mapped[int] = mapped_column(Integer, default=0)
    versions_removed: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The values that belong to the error text, for example ``{"status": 500}``.
    error_values: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    #: Only for a Sonarr run: progress while running, then series read, series TMDB does not know (up to 20), matched
    #: and unmatched episodes, files without an episode. ⚠️ From migration 8.
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class Title(Base):
    """One movie (later: show, album), merged over every source.

    The descriptive fields come from one source, ``meta_source_id``: two Radarr instances in
    different languages would otherwise overwrite each other's title every 15 minutes. The
    other sources add their titles as alternate titles for the search. The poster works the
    same way through ``poster_source_id``. When the owning source goes away, the next import
    of another source takes over.
    """

    __tablename__ = "titles"
    __table_args__ = (
        UniqueConstraint("kind", "tmdb_id", name="uq_titles_kind_tmdb_id"),
        # An album has no TMDB number; its MusicBrainz release group is unique per kind instead (M1.0).
        Index("uq_titles_kind_mbid", "kind", "mbid", unique=True, sqlite_where=text("mbid IS NOT NULL")),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), default="movie")
    #: TMDB's number of a movie or series. ⚠️ Null for an album since migration 14 (decision 1);
    #: a title of kind ``album`` never has one.
    tmdb_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    imdb_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    title: Mapped[str] = mapped_column(String(1024), default="")
    original_title: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    runtime: Mapped[int | None] = mapped_column(Integer, nullable=True)
    genres: Mapped[list[str]] = mapped_column(JSON, default=list)
    #: TMDB's keywords and the studios (production companies) of a movie, for auto tags.
    #: Null until TMDB or Radarr told them. ⚠️ From the column upkeep, without a migration: no default.
    keywords: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    #: The type TMDB suggests for a series: anime, daily or standard (B5). Written by every TMDB
    #: refresh; null until one ran since. ⚠️ From the column upkeep, without a migration.
    type_proposed: Mapped[str | None] = mapped_column(String(16), nullable=True)
    studios: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    overview: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Form (a) of the title without a leading article, see ``schreibweisen.sort_key``.
    sort_key: Mapped[str] = mapped_column(String(1024), default="", index=True)
    #: Comparison keys of title and original title, ``|key|key|``.
    search_keys: Mapped[str] = mapped_column(Text, default="")
    meta_source_id: Mapped[int | None] = mapped_column(
        ForeignKey("sources.id", ondelete="SET NULL"), nullable=True, index=True
    )
    poster_source_id: Mapped[int | None] = mapped_column(
        ForeignKey("sources.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: The relative ``/MediaCover/...`` address at the poster source. Never a TMDB address.
    poster_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    #: ``lastWrite`` of that address: a new value means a new image.
    poster_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    added: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
    #: ``source`` or ``tmdb``; null without a poster. Titles from step 1 have null and a source poster.
    poster_origin: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: The TMDB poster file (``abc.jpg``), without size and leading slash.
    tmdb_poster_path: Mapped[str | None] = mapped_column(String(256), nullable=True)
    #: When the TMDB data was last fetched. Null for a title that never had TMDB data.
    tmdb_refreshed_at: Mapped[datetime | None] = mapped_column(nullable=True, index=True)
    #: TMDB release dates, raw per country, for the calendar and the scheduled search later.
    release_dates: Mapped[list[dict[str, object]] | None] = mapped_column(JSON, nullable=True)
    #: Comparison keys of the TMDB alternative and translated titles, ``|key|key|``. Kept apart from
    #: ``search_keys``, which an import rewrites.
    tmdb_search_keys: Mapped[str] = mapped_column(Text, default="")
    #: ISO 639-1 of the movie's original language, from Radarr's ``originalLanguage`` or TMDB's
    #: ``original_language``. The release checker needs it: TRaSH's German DL means German plus this language.
    original_language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # ⚠️ The search plan of step 3c comes from migration 6; a change needs a new migration.
    #: When the planned automatic search is due; null until the scheduler planned the title, or nothing is wanted.
    next_search_at: Mapped[datetime | None] = mapped_column(nullable=True, index=True)
    #: When an automatic search of the title last ran.
    last_search_at: Mapped[datetime | None] = mapped_column(nullable=True)
    #: Why the next search is when it is: anchor, schedule, limit, replacement, replacement_limit, no_date,
    #: nothing_wanted.
    next_search_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: The outcome of the last automatic search: counts, codes and release titles, never a link.
    search_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # ⚠️ The nine columns below come from migration 8 (series, S1); a change needs a new migration. Null for movies.
    #: ``standard``, ``daily`` or ``anime``, see ``series.SERIES_TYPES``.
    series_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: TVDB's number of the series, as TMDB or Sonarr gives it. Not unique: one TVDB series can stand for two TMDB ones.
    tvdb_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    #: TMDB's status as it comes: ``Returning Series``, ``Planned``, ``In Production``, ``Ended``, ``Canceled``,
    #: ``Pilot``.
    series_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: The networks' names.
    networks: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    first_air_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    last_air_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    next_air_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    #: TMDB's episode groups of the series: id, name, type, episode and group count. Their contents come with S3.
    episode_groups: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    #: How the source counts against TMDB (decision 25), computed at import and refresh; null when both agree.
    numbering_note: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # ⚠️ From migration 10; a change needs a new migration.
    #: TMDB's English name of the series, for queries: scene releases mostly carry it (decision 14).
    title_en: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    #: When TheXEM's mapping of the series was last fetched, and what came of it: ``mapped``, ``not_listed``,
    #: ``bridge_failed``, or the code of a failure.
    xem_checked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    xem_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: The TMDB episode group the owner chose as the release numbering; null means TMDB's own (decision 11).
    episode_group_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Whether releases are read through the scene numbering (Sonarr's ``useSceneNumbering``, the design notes,
    #: B6). Null means on, as Sonarr has it for every series with a mapping; only the owner sets false. ⚠️ From the
    #: column upkeep, without a migration.
    use_scene_numbering: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # ⚠️ From migration 12 (S5.0); a change needs a new migration.
    #: Per indexer id the last time its search by number found releases of the series: automatic searches leave the
    #: title queries out for 30 days after it (decision 11). ``{"<indexer id>": "<ISO time>"}``.
    id_search_seen: Mapped[dict[str, str] | None] = mapped_column(JSON, nullable=True)
    # ⚠️ The thirteen columns below come from migration 14 (M1.0); a change needs a new
    # migration. Null for movies and series. An album is one MusicBrainz release group.
    mbid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    #: Earlier ids MusicBrainz merged into this one (decision 22).
    mbid_old: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    #: The artist the album belongs to: the first of its credit that is in the library (decision 3).
    artist_id: Mapped[int | None] = mapped_column(
        ForeignKey("artists.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: MusicBrainz's credit as it comes: ``[{"mbid": ..., "name": ..., "join": ...}]``.
    artist_credit: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    #: MusicBrainz's primary and secondary types as text (decision 25): ``Album``, ``EP``, ``Single``, ``Broadcast``,
    #: ``Other``, or null; ``["Live", "Compilation"]``.
    primary_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    secondary_types: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    first_release_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    release_group_disambiguation: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    #: When the releases of the album were last loaded, and when they are due again (decision 21).
    releases_refreshed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    releases_due_at: Mapped[datetime | None] = mapped_column(nullable=True, index=True)
    #: See ``music.RELEASES_STATES``: ``none``, ``heads``, ``tracks``, ``failed``.
    releases_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: When MusicBrainz stopped listing the release group; the album stays (decision 22).
    mb_gone_at: Mapped[datetime | None] = mapped_column(nullable=True)
    #: When nexcrate saw the release group for the first time: "new" means seen at a later load (decision 24).
    first_seen_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # ⚠️ The three columns below come from the missing-column upkeep; nullable and without a
    # default on purpose, or every INSERT of an older table shape would carry them.
    #: The program's own reference when a program added the title through ``/api/v1`` (N19), and the name of its
    #: key then. Null for a title of the owner or of an import.
    origin: Mapped[str | None] = mapped_column(String(100), nullable=True)
    origin_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    #: A program asked for a search (``search_now`` or ``/search``). The automatic starts the title even with its
    #: switch off, within the same limits, and clears this after the search.
    search_wish_at: Mapped[datetime | None] = mapped_column(nullable=True)


class AlternateTitle(Base):
    """A further name of a title, for the search only. Belongs to the source that named it."""

    __tablename__ = "alternate_titles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title_id: Mapped[int] = mapped_column(ForeignKey("titles.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(String(1024))
    search_keys: Mapped[str] = mapped_column(Text, default="")


class Version(Base):
    """A title in one version definition, fed by a source or added by the owner.

    ⚠️ ``migrations.py`` rebuilds this table for databases of step 1. A change here needs a new
    migration or the missing-column upkeep, never an edit of migration 1.
    """

    __tablename__ = "versions"
    __table_args__ = (
        UniqueConstraint("title_id", "version_definition_id", name="uq_versions_title_definition"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title_id: Mapped[int] = mapped_column(ForeignKey("titles.id", ondelete="CASCADE"), index=True)
    version_definition_id: Mapped[int] = mapped_column(
        ForeignKey("version_definitions.id", ondelete="RESTRICT"), index=True
    )
    #: The source that feeds this version; null for a version of the owner that no source feeds.
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), nullable=True, index=True
    )
    #: ``import`` or ``owner``, see ``ADDED_BY``.
    added_by: Mapped[str] = mapped_column(String(16), default="import", server_default="import")
    radarr_movie_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monitored: Mapped[bool] = mapped_column(Boolean, default=False)
    has_file: Mapped[bool] = mapped_column(Boolean, default=False)
    #: What identifies the file at the source, to notice a new one.
    file_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Radarr's decision (``movieFile.quality.quality.name``), shown as it comes.
    quality: Mapped[str | None] = mapped_column(String(100), nullable=True)
    cutoff_not_met: Mapped[bool] = mapped_column(Boolean, default=False)
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    languages: Mapped[list[str]] = mapped_column(JSON, default=list)
    release_group: Mapped[str | None] = mapped_column(String(200), nullable=True)
    relative_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    #: The quality profile and root folder the movie has at the source.
    profile_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    root_folder: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    #: The cutoff of the profile while the cutoff is not met.
    upgrade_to: Mapped[str | None] = mapped_column(String(200), nullable=True)
    state: Mapped[str] = mapped_column(String(16), default="wanted", index=True)
    #: Percent, only while downloading.
    progress: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Our name for a stuck queue item, never Radarr's translated sentence.
    problem_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
    # ⚠️ The two columns below come from migration 5; a change needs a new migration.
    #: The movie folder as the source sees it, kept by every import; null for a version no source feeds.
    source_movie_path: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    #: The release name of the current file: Radarr's scene name or original file name, or the release title of the
    #: download nexcrate filed away. Null when nobody kept one.
    release_title: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    #: Radarr's ``minimumAvailability`` in its camel case (``released``), kept by every import and after the takeover.
    #: ⚠️ From migration 5.
    minimum_availability: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: When automatic replacements after a failed download were started, ISO 8601 in UTC, the last 24 hours only.
    #: ⚠️ From migration 6.
    replacement_times: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    # ⚠️ The six columns below come from migration 7 (the library from disk); a change needs a new migration.
    #: The state of the version's ``release.nex``: ``written``, ``current``, ``missing``, ``outdated``, ``foreign``,
    #: ``broken``, ``newer_format``, ``other_installation``, ``changed``, ``not_writable``, ``no_space``,
    #: ``folder_missing``, ``file_missing``, ``failed``; null for a version a source feeds or one never looked at.
    companion_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    companion_written_at: Mapped[datetime | None] = mapped_column(nullable=True)
    #: SHA-256 of the bytes nexcrate wrote last; a file whose hash differs was changed by hand.
    companion_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Where the quality came from: ``name``, ``media`` or ``radarr``.
    quality_from: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: Media data of the current file in nexcrate's own shape (``services/media.py``), read by ``mediainfo``.
    media_info: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    media_read_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # ⚠️ The six columns below come from migration 8 (series, S1); a change needs a new migration. Null for movies.
    #: The rule of a series version of nexcrate's own (``series.WATCH_RULES``); null for a version a source feeds.
    watch_rule: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: The first season ``from_season`` watches.
    watch_from_season: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: The counts of a series version: files, wanted, aired_watched, watched, total, next_air_date.
    episode_counts: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    sonarr_series_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: The series folder as Sonarr sees it, kept by every import.
    source_series_path: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    #: Sonarr's settings of the series, for the takeover later: monitored, monitor_new_items, season_folder,
    #: use_scene_numbering.
    source_details: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # ⚠️ From migration 12 (S5.0); a change needs a new migration.
    #: Replacements after a failed series download per season, the last 24 hours only: ``[{"at": ISO, "season": 2}]``.
    #: ``replacement_times`` stays for movies.
    series_replacements: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    # ⚠️ From migration 13; a change needs a new migration.
    #: When a series version became nexcrate's own: taken over, assigned, restored or added. Old missing episodes of a
    #: version with unclear files wait; episodes aired after this time do not (decision 18).
    own_since: Mapped[datetime | None] = mapped_column(nullable=True)
    #: When the series folder of a version of nexcrate's own was read; until then it wants nothing (decision 23).
    files_read_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # ⚠️ The eight columns below come from migration 14 (M1.0); a change needs a new migration.
    # Null for movies and series.
    #: The release the album version aims at (decision 7), who chose it (``music.TARGET_SET_BY``) and why, as codes
    #: with values (decision 27, step 5).
    target_release_id: Mapped[int | None] = mapped_column(
        ForeignKey("releases.id", ondelete="SET NULL"), nullable=True
    )
    target_set_by: Mapped[str | None] = mapped_column(String(16), nullable=True)
    target_reason: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    #: The release the files on disk belong to; null until a file lies there.
    actual_release_id: Mapped[int | None] = mapped_column(
        ForeignKey("releases.id", ondelete="SET NULL"), nullable=True
    )
    #: A release the rule would choose now while the target is frozen by a file (decision 29).
    target_suggestion_id: Mapped[int | None] = mapped_column(
        ForeignKey("releases.id", ondelete="SET NULL"), nullable=True
    )
    #: The counts of an album version: ``{"wanted": 12, "present": 9}``.
    track_counts: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    lidarr_album_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: The album folder as Lidarr sees it, kept by every import.
    source_album_path: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    # ⚠️ The two columns below come from the missing-column upkeep; nullable, no default.
    #: The program's own reference when a program added this version through ``/api/v1``, and its key's name.
    origin: Mapped[str | None] = mapped_column(String(100), nullable=True)
    origin_key: Mapped[str | None] = mapped_column(String(100), nullable=True)


class Indexer(Base):
    """A Newznab or Torznab endpoint: a Usenet indexer, one indexer in Prowlarr, or Jackett.

    ``url`` is the endpoint with its path, for example ``https://indexer.example.com/api``.
    ``api_key`` holds the encrypted key (``enc:...``) or nothing. ``caps`` is null when the indexer's
    caps were missing or broken; the defaults of ``services/indexers.py`` apply then.
    """

    __tablename__ = "indexers"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(16), default="newznab")
    url: Mapped[str] = mapped_column(String(1024))
    api_key: Mapped[str] = mapped_column(Text, default="")
    categories: Mapped[list[int]] = mapped_column(JSON, default=list)
    # ⚠️ From migration 10 (series stage S3); a change needs a new migration.
    #: The categories a series search asks in; empty means the indexer is skipped for series, visibly.
    series_categories: Mapped[list[int]] = mapped_column(JSON, default=list, server_default=text("'[]'"))
    # ⚠️ From migration 15 (M3.1); a change needs a new migration.
    #: The categories an album search asks in; null means the default from the caps, empty skips the indexer.
    music_categories: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    # Added by the column upkeep (A3), no migration.
    #: The categories an anime series is also asked in; null means the default from the caps (5070 and its
    #: subcategories), empty asks an anime series only in the series categories.
    anime_categories: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    # Added by the column upkeep (B3), no migration.
    #: Whether an anime series is also asked for in the standard form (``S01E01``) and not only by the number it
    #: counts through. Sonarr calls this ``animeStandardFormatSearch`` and has it off; nexcrate has always asked
    #: both, so it stays on here and the switch is there to turn it off.
    anime_standard_format_search: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    caps: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    caps_checked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    #: Set after a request limit: no request goes out before this time.
    paused_until: Mapped[datetime | None] = mapped_column(nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The Radarr connection it was fetched from, if any. Only information; nothing follows Radarr.
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("sources.id", ondelete="SET NULL"), nullable=True, index=True
    )
    radarr_indexer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
    # ⚠️ The search settings of step 2c come from migration 3 in databases of step 2b; a change here needs a new
    # migration, never an edit of migration 3.
    #: Radarr's priority, 1 to 50: of two releases that rank the same otherwise, the lower number wins. Default 25.
    priority: Mapped[int] = mapped_column(Integer, default=25, server_default=text("25"))
    #: Torznab only: a release with fewer known seeders does not fit. Null for Newznab, Radarr's 1 by default.
    minimum_seeders: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: ISO 639-1 codes a release of this indexer with the MULTi token carries.
    multi_languages: Mapped[list[str]] = mapped_column(JSON, default=list, server_default=text("'[]'"))
    #: Ask by title without the year.
    remove_year: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    #: Whether the automatic search of step 3 may use it (Radarr's RSS or automatic search). Not shown in step 2c.
    automatic_search: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"))
    # ⚠️ The budget, the escalation and the RSS state of step 3c come from migration 6; a change needs a new migration.
    #: Requests a day the owner allows, 1 to 100000; null when unknown. ``newznab:apilimits`` win when the indexer
    #: sends them.
    daily_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: The latest ``newznab:apilimits`` values the indexer sent, and when.
    api_current: Mapped[int | None] = mapped_column(Integer, nullable=True)
    api_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    grab_current: Mapped[int | None] = mapped_column(Integer, nullable=True)
    grab_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    api_next_at: Mapped[datetime | None] = mapped_column(nullable=True)
    grab_next_at: Mapped[datetime | None] = mapped_column(nullable=True)
    limits_seen_at: Mapped[datetime | None] = mapped_column(nullable=True)
    #: Radarr's escalation after failures, 0 to 9. Pauses automatic work only.
    escalation_level: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    initial_failure_at: Mapped[datetime | None] = mapped_column(nullable=True)
    automatic_paused_until: Mapped[datetime | None] = mapped_column(nullable=True)
    #: RSS: the newest release of the previous sync as publish date and release key (never the link), the last sync,
    #: and a gap the last sync could not cover.
    rss_newest_at: Mapped[datetime | None] = mapped_column(nullable=True)
    rss_newest_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rss_last_at: Mapped[datetime | None] = mapped_column(nullable=True)
    rss_gap_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # ⚠️ From migration 12 (S5.0); a change needs a new migration.
    #: What the last RSS sync asked for: ``movie``, ``series`` or ``both``. A sync of another form reads one page.
    rss_form: Mapped[str | None] = mapped_column(String(8), nullable=True)


class Profile(Base):
    """A profile with a name: the owner's answers and the rules built from them.

    ``rules`` are what the release checker evaluates; they stay as built until the profile is saved again, so
    a newer TRaSH state changes nothing silently. ``trash_commit`` names the state they were built with; a
    profile of another commit than the one in use is outdated.

    ⚠️ Since migration 20 a profile stands for itself, as in Radarr: several per kind, and a version points at
    one of them (``VersionDefinition.profile_id``). Before that it belonged to exactly one version. Nothing
    reads a profile by a version any more; ``services/profiles/store.py`` is the one way from a version to its
    profile.

    ⚠️ ``migrations.py`` creates this table for older databases (migration 2) and rebuilds it in migration 20.
    A change here needs a new migration or the missing-column upkeep, never an edit of an old one.
    """

    __tablename__ = "profiles"
    __table_args__ = (
        UniqueConstraint("kind", "name", name="uq_profiles_kind_name"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: What the owner calls it. Unique within a kind, so a list can be read.
    name: Mapped[str] = mapped_column(String(200), default="")
    kind: Mapped[str] = mapped_column(String(16), default="movie")
    answers: Mapped[dict[str, Any]] = mapped_column(JSON)
    rules: Mapped[dict[str, Any]] = mapped_column(JSON)
    trash_commit: Mapped[str] = mapped_column(String(64), default="")
    #: ⚠️ From migration 19: what the owner set by hand, when ``mode`` is expert.
    #: ``rules`` stays the one thing every judgement reads; it is built from the answers or from this.
    mode: Mapped[str] = mapped_column(String(16), default="wizard", server_default="wizard")
    expert: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class CustomFormat(Base):
    """A custom format of its own, as Radarr keeps them: a name, what it looks at, and where it came from.

    A profile points at it and gives it a score. The specifications are Radarr's
    shape, so the engine of ``releases/formats.py`` reads them unchanged.

    ⚠️ From migration 19.
    """

    __tablename__ = "custom_formats"
    __table_args__ = (
        UniqueConstraint("kind", "name", name="uq_custom_formats_kind_name"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), default="movie")
    name: Mapped[str] = mapped_column(String(200))
    #: ``trash`` came with TRaSH's data and is kept up to date there, ``own`` belongs to the owner.
    origin: Mapped[str] = mapped_column(String(16), default="own")
    #: TRaSH's id of the format, for the update; null for one of the owner's own.
    trash_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    specifications: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class QualitySize(Base):
    """What a quality may weigh, as Radarr's quality definitions do: megabytes per minute, per kind.

    One row per quality and kind, for every profile of that kind.

    ⚠️ From migration 19.
    """

    __tablename__ = "quality_sizes"
    __table_args__ = (
        UniqueConstraint("kind", "quality", name="uq_quality_sizes_kind_quality"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), default="movie")
    quality: Mapped[str] = mapped_column(String(64))
    min_mb_per_min: Mapped[float] = mapped_column(Float, default=0.0)
    #: Null: no upper limit, as Radarr's unlimited.
    max_mb_per_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Radarr's preferred size: between releases equal in everything else the one nearest to it wins. Null: the
    #: larger one wins, which is also what Radarr does without one. Added by the column upkeep, no migration.
    preferred_mb_per_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class HistoryEntry(Base):
    """What happened to a title: a version appeared (``added``), got a file (``imported``), a release was handed to a
    download client (``grabbed``) or its download failed (``failed``)."""

    __tablename__ = "history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title_id: Mapped[int] = mapped_column(ForeignKey("titles.id", ondelete="CASCADE"), index=True)
    version_id: Mapped[int | None] = mapped_column(ForeignKey("versions.id", ondelete="SET NULL"), nullable=True)
    version_definition_id: Mapped[int | None] = mapped_column(
        ForeignKey("version_definitions.id", ondelete="SET NULL"), nullable=True
    )
    #: The label at the time, kept when the definition is gone.
    version_label: Mapped[str] = mapped_column(String(64), default="")
    event: Mapped[str] = mapped_column(String(16))
    at: Mapped[datetime] = mapped_column(default=utcnow)
    detail: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    #: What ``/api/v1`` tells about the entry (N29): the download, quality and size, the file it
    #: replaced, the episodes, who did it. Null for entries from before V3. ⚠️ From the missing-column upkeep: nullable
    #: and without a default on purpose.
    data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
