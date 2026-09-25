"""The tables below a series (S1.1): seasons, episodes, numberings, files, watching.

A series is a ``Title`` with ``kind = "series"``, a series version a ``Version`` of it, as for movies. Below them:

* ``seasons``: TMDB's aired seasons, number 0 for specials, found again by TMDB's season id.
* ``episodes``: TMDB's episodes. ⚠️ An episode keeps its id for good; TMDB's numbers are values on it, not its identity.
* ``episode_numbers``: every other numbering of an episode (``tvdb`` and ``scene`` from Sonarr now, ``absolute`` for
  anime), one row per scheme.
* ``episode_files``: one video of one version; one file can cover several episodes.
* ``season_versions`` and ``episode_versions``: what a version watches, and which file an episode has in it. A row
  exists for every season and episode of the title in every version of it.

Air dates are plain ``YYYY-MM-DD`` texts, as TMDB gives them: no time, no zone.

⚠️ Ids are never reused (``sqlite_autoincrement``): history and later downloads point at an episode for good.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, utcnow

#: How a series names its releases. Anime is stored and shown; its own handling comes with a later block.
SERIES_TYPES = ("standard", "daily", "anime")
#: The rule of a series version (decision 9). A version a source feeds has none.
WATCH_RULES = ("all", "future", "missing", "from_season", "none")
#: Who set a switch: the rule, the owner, the source that feeds the version, or ``late`` for an episode TMDB added
#: long after it aired, which stays off (decision 11).
SET_BY = ("rule", "owner", "source", "late")
#: The numbering schemes: ``tvdb`` and ``scene`` (from Sonarr or TheXEM), ``owner`` (a local correction) and ``group``
#: (the chosen TMDB episode group), see the design notes, decisions 9 to 13; ``absolute`` (the number an anime
#: series counts through, only ``absolute`` set, the design notes, A1).
NUMBER_SCHEMES = ("tvdb", "scene", "owner", "group", "absolute")
#: Where a numbering came from. ``tmdb_order`` and ``counted_on`` only for the scheme ``absolute``.
NUMBER_ORIGINS = ("sonarr", "xem", "owner", "tmdb", "tmdb_order", "counted_on")


class Season(Base):
    """One of TMDB's aired seasons of a series."""

    __tablename__ = "seasons"
    __table_args__ = (
        UniqueConstraint("title_id", "tmdb_season_id", name="uq_seasons_title_tmdb_season"),
        Index("ix_seasons_title_number", "title_id", "number"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title_id: Mapped[int] = mapped_column(ForeignKey("titles.id", ondelete="CASCADE"))
    tmdb_season_id: Mapped[int] = mapped_column(Integer)
    #: TMDB's season number; 0 holds the specials.
    number: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(1024), default="")
    overview: Mapped[str | None] = mapped_column(Text, nullable=True)
    air_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    #: The number of episodes TMDB counts for the season.
    episode_count: Mapped[int] = mapped_column(Integer, default=0)
    #: When TMDB stopped listing the season; null while it does.
    tmdb_gone_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Episode(Base):
    """One of TMDB's episodes. Its numbers are TMDB's aired numbers and may change; its id does not."""

    __tablename__ = "episodes"
    __table_args__ = (
        Index("ix_episodes_title_numbers", "title_id", "season_number", "episode_number"),
        Index("ix_episodes_title_air_date", "title_id", "air_date"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title_id: Mapped[int] = mapped_column(ForeignKey("titles.id", ondelete="CASCADE"))
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id", ondelete="CASCADE"), index=True)
    #: Null only for an episode no TMDB entry backs, which S1 never creates.
    tmdb_episode_id: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True)
    season_number: Mapped[int] = mapped_column(Integer)
    episode_number: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(1024), default="")
    #: TMDB's English name, for specials searched by name (decisions 14 and 21). Migration 10.
    name_en: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    overview: Mapped[str | None] = mapped_column(Text, nullable=True)
    air_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    runtime: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: TMDB's ``episode_type``: ``standard``, ``finale`` or its mid-season value.
    episode_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: From Sonarr; TMDB gives an episode's external numbers only in one call per episode.
    tvdb_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    imdb_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    #: When TMDB stopped listing the episode; it stays while something hangs on it (decision 5).
    tmdb_gone_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # ⚠️ From migration 12 (S5.0); a change needs a new migration.
    #: When an automatic search last asked for the episode, as Sonarr's ``LastSearchTime`` (decision 7).
    last_search_at: Mapped[datetime | None] = mapped_column(nullable=True)


class EpisodeNumber(Base):
    """One numbering of an episode other than TMDB's aired one. No uniqueness on the values (decision 6)."""

    __tablename__ = "episode_numbers"
    __table_args__ = (
        UniqueConstraint("episode_id", "scheme", name="uq_episode_numbers_episode_scheme"),
        Index("ix_episode_numbers_lookup", "title_id", "scheme", "season", "episode"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"))
    title_id: Mapped[int] = mapped_column(ForeignKey("titles.id", ondelete="CASCADE"))
    scheme: Mapped[str] = mapped_column(String(48))
    season: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episode: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: The last episode when one TMDB episode stands for a range in this scheme; null otherwise.
    episode_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    absolute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: False for a numbering the source only extrapolated (Sonarr's ``unverifiedSceneNumbering``).
    verified: Mapped[bool] = mapped_column(Boolean, default=True)
    origin: Mapped[str] = mapped_column(String(16))
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class EpisodeFile(Base):
    """One video of one series version. ``episode_versions`` links it to its episodes; none when nothing matched."""

    __tablename__ = "episode_files"
    __table_args__ = (
        UniqueConstraint("version_id", "relative_path", name="uq_episode_files_version_path"),
        Index("ix_episode_files_version_source_file", "version_id", "source_file_id"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("versions.id", ondelete="CASCADE"))
    #: Relative to the series folder, with forward slashes.
    relative_path: Mapped[str] = mapped_column(String(2048))
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    quality: Mapped[str | None] = mapped_column(String(100), nullable=True)
    languages: Mapped[list[str]] = mapped_column(JSON, default=list)
    release_group: Mapped[str | None] = mapped_column(String(200), nullable=True)
    release_title: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    #: Sonarr's ``releaseType``: ``singleEpisode``, ``multiEpisode``, ``seasonPack`` or ``unknown``.
    release_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: Sonarr's short media info as it comes.
    media_info: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # ⚠️ The column below comes from migration 9; a change needs a new migration.
    #: Whether the profile of the version would still upgrade this file. Null means not judged and counts as no
    #: upgrade: for a version a source feeds it is Sonarr's ``qualityCutoffNotMet``, otherwise nexcrate's own verdict.
    cutoff_not_met: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    #: The file's id at the source; null for a file of nexcrate's own.
    source_file_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: The source's season and episodes of the file (``{"season": 2, "episodes": [5, 6]}``), kept for files no TMDB
    #: episode matched.
    source_numbers: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    added_at: Mapped[datetime | None] = mapped_column(nullable=True)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
    # ⚠️ The columns below come from migration 11 (S4.0); a change needs a new migration.
    #: ``nexcrate:<download id>`` for a file nexcrate filed away; null for a file a source knows.
    file_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: ``name`` or ``media``: what decided the quality.
    quality_from: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: The name was built with ``TBA`` for a missing episode title; renamed once TMDB has one (decision 27).
    named_tba: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    #: The numbering the name carries: ``tmdb`` or ``tvdb`` (decision 14).
    name_numbering: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # ⚠️ From migration 13; a change needs a new migration.
    #: The owner said the file belongs to no episode; it stays on disk and is no longer unclear (decision 17).
    left_out: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    #: What reading the folder found in the name: ``season``, ``episodes``, ``numbering``, ``reason``; null for a file
    #: of a download or a source.
    read_as: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    #: 1 or 2 for a half of a double episode TMDB lists as one; None for a whole file.
    #: Part 1 is linked through ``episode_versions`` like any file; part 2 names its episode in ``part_of_episode_id``.
    #: ⚠️ Both added by the missing-column upkeep, nullable, no migration.
    part: Mapped[int | None] = mapped_column(Integer, nullable=True)
    part_of_episode_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SeasonVersion(Base):
    """Whether a version watches a season: the switch new episodes of the season take (decision 9)."""

    __tablename__ = "season_versions"

    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id", ondelete="CASCADE"), primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("versions.id", ondelete="CASCADE"), primary_key=True, index=True)
    watched: Mapped[bool] = mapped_column(Boolean, default=False)
    set_by: Mapped[str] = mapped_column(String(16), default="rule")


class EpisodeVersion(Base):
    """An episode in one version: watched or not, its file, and the source's queue state."""

    __tablename__ = "episode_versions"
    __table_args__ = (Index("ix_episode_versions_version_watched", "version_id", "watched"),)

    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"), primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("versions.id", ondelete="CASCADE"), primary_key=True)
    watched: Mapped[bool] = mapped_column(Boolean, default=False)
    set_by: Mapped[str] = mapped_column(String(16), default="rule")
    episode_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("episode_files.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: For a version a source feeds: whether the source has this episode at all. Null for a version of nexcrate's own.
    in_source: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    #: ``downloading`` or ``problem`` while the source's queue holds the episode; null otherwise.
    queue_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    problem_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    progress: Mapped[float | None] = mapped_column(nullable=True)


class TitleAlias(Base):
    """A search title the owner added to a series: asked for first and matched like its titles (decision 12)."""

    __tablename__ = "title_aliases"
    __table_args__ = (
        UniqueConstraint("title_id", "text", name="uq_title_aliases_title_text"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title_id: Mapped[int] = mapped_column(ForeignKey("titles.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(String(200))
    #: Comparison keys of the text, ``|key|key|``.
    search_keys: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class XemName(Base):
    """A scene name TheXEM lists for a TVDB series, for all seasons (``season`` null) or one; replaced per fetch."""

    __tablename__ = "xem_names"
    __table_args__ = (Index("ix_xem_names_tvdb", "tvdb_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tvdb_id: Mapped[int] = mapped_column(Integer)
    season: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str] = mapped_column(String(1024))
    search_keys: Mapped[str] = mapped_column(Text, default="")


class SeasonFolder(Base):
    """The name of a season folder of a series version, fixed the first time a file is filed into it (decision 15),
    and the state of its ``release.nex`` (decision 40).

    ⚠️ From ``create_all`` with migration 11; a change of a column needs a new migration.
    """

    __tablename__ = "season_folders"

    version_id: Mapped[int] = mapped_column(ForeignKey("versions.id", ondelete="CASCADE"), primary_key=True)
    season_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(1024))
    companion_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    companion_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    companion_written_at: Mapped[datetime | None] = mapped_column(nullable=True)


class SourceEpisode(Base):
    """An episode a source knows and no TMDB episode matched, mostly specials (decision 47). Replaced per import.

    ⚠️ From ``create_all`` with migration 11; a change of a column needs a new migration.
    """

    __tablename__ = "source_episodes"
    __table_args__ = (
        Index("ix_source_episodes_version", "version_id", "season", "episode"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title_id: Mapped[int] = mapped_column(ForeignKey("titles.id", ondelete="CASCADE"), index=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("versions.id", ondelete="CASCADE"))
    #: The episode's id at the source.
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    season: Mapped[int] = mapped_column(Integer)
    episode: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(1024), default="")
    air_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    watched: Mapped[bool] = mapped_column(Boolean, default=False)
    has_file: Mapped[bool] = mapped_column(Boolean, default=False)
