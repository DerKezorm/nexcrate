"""The tables of step 3: download clients, downloads and the blocklist.

A download is one release nexcrate handed to a client for one version of a title. It keeps what the release was
(title, indexer, quality, score, the languages of the evaluation and the matched formats for naming), because the
search it came from lives in memory only. The client's download id (SABnzbd's ``nzo_id``, a torrent's info hash in
lower case) is how tracking finds it again; the category only keeps other programs away.

⚠️ These tables come from ``create_all``, ``version_definitions.folder`` from migration 4, ``downloads.origin`` and the
table ``extra_files`` with migration 6. A change of a column here needs a new migration or the missing-column upkeep,
never an edit of an existing migration.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, utcnow

CLIENT_KINDS = ("sabnzbd", "qbittorrent", "nzbget", "transmission", "deluge")
#: The protocol a client kind loads.
PROTOCOL_OF_KIND = {
    "sabnzbd": "usenet",
    "qbittorrent": "torrent",
    "nzbget": "usenet",
    "transmission": "torrent",
    "deluge": "torrent",
}
STATES = ("queued", "downloading", "paused", "completed", "importing", "imported", "failed", "problem", "removed")
#: Queued to importing: what the Downloads page lists as active.
ACTIVE_STATES = ("queued", "downloading", "paused", "completed", "importing")
#: What the client still works on: tracking asks the client about these.
CLIENT_STATES = ("queued", "downloading", "paused")
#: Imported, failed and removed: the history.
FINISHED_STATES = ("imported", "failed", "removed")
#: ``unpacked``: the video came out of the download's archives (T3).
TRANSFERS = ("hardlink", "copy", "move", "unpacked")
#: Why a failed download failed: SABnzbd reported Failed, the job was encrypted, or the client did not answer in time
#: when it was handed the release and never showed it afterwards (``not_taken``, the owner's finding 1 of 22.09.2026).
FAILED_REASONS = ("client_failed", "encrypted", "not_taken")
#: What SABnzbd said about a failed job, as a code (``downloaders.sabnzbd.fail_code``); its own sentence is never kept.
FAILED_DETAILS = (
    "repair_failed",
    "incomplete",
    "not_on_server",
    "password",
    "unpack_failed",
    "encrypted",
    "unwanted_extension",
    "duplicate",
    "aborted",
    "other",
)
#: Who takes care of a failed download (the owner's findings of 22.09.2026): nexcrate searches a replacement at once,
#: the version waits for its schedule after three replacements in a day, or nothing happens by itself and the owner
#: gets a card.
FAILURE_HANDLINGS = ("replacement", "schedule", "owner")
#: Why a release is on a title's blocklist.
BLOCK_REASONS = (*FAILED_REASONS, "dangerous_file", "multi_part", "removed_by_owner")
#: Series downloads: what the download covers.
#: ``album`` since M4 (decision 3).
SCOPES = ("episode", "season", "series", "album")
#: Why an episode belongs to a download: it fills a gap, replaces a file, or the owner loaded a release without gain.
EPISODE_ACTIONS = ("fills", "replaces", "confirmed")
#: Where an episode of a download stands.
EPISODE_STATES = ("expected", "filed", "skipped_not_better", "missing", "not_filed")
#: What became of a video file of a download; ``candidate`` is a video of a movie download with several.
FILE_DECISIONS = ("filed", "open", "sample", "extra", "duplicate", "not_needed", "not_filed", "candidate")
#: Who started a download: the owner, a planned search or "search automatically now", RSS, or a replacement after a
#: failure.
ORIGINS = ("manual", "search", "rss", "replacement")
#: What an extra file next to a movie is.
EXTRA_KINDS = ("subtitle",)


class DownloadClient(Base):
    """A SABnzbd or a qBittorrent.

    ``secret`` holds SABnzbd's API key or qBittorrent's password, encrypted (``enc:...``), never the plain value.
    ``path_mappings`` is a list of ``{remote, local}`` pairs the owner confirmed.
    """

    __tablename__ = "download_clients"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(16))
    url: Mapped[str] = mapped_column(String(1024))
    #: qBittorrent's user name; null for SABnzbd.
    username: Mapped[str | None] = mapped_column(String(256), nullable=True)
    secret: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(64), default="nexcrate")
    #: 1 to 50: of several enabled clients for one protocol, the lowest number is asked first.
    priority: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    path_mappings: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The Radarr connection it was fetched from, if any. Only information.
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("sources.id", ondelete="SET NULL"), nullable=True, index=True
    )
    radarr_client_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class Download(Base):
    """One release handed to a client for one version of a title."""

    __tablename__ = "downloads"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title_id: Mapped[int] = mapped_column(ForeignKey("titles.id", ondelete="CASCADE"), index=True)
    #: The title's version row at the time; the version is found again by title and definition.
    version_id: Mapped[int | None] = mapped_column(ForeignKey("versions.id", ondelete="SET NULL"), nullable=True)
    version_definition_id: Mapped[int | None] = mapped_column(
        ForeignKey("version_definitions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: The label at the time, kept when the definition is gone.
    version_label: Mapped[str] = mapped_column(String(64), default="")
    client_id: Mapped[int | None] = mapped_column(
        ForeignKey("download_clients.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: usenet or torrent.
    protocol: Mapped[str] = mapped_column(String(16))
    #: SABnzbd's nzo_id, or the torrent's info hash in lower case.
    client_download_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    release_title: Mapped[str] = mapped_column(String(1024))
    indexer_id: Mapped[int | None] = mapped_column(ForeignKey("indexers.id", ondelete="SET NULL"), nullable=True)
    indexer_name: Mapped[str] = mapped_column(String(100), default="")
    release_key: Mapped[str] = mapped_column(String(64), default="")
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: The quality parsed from the release name.
    quality: Mapped[str | None] = mapped_column(String(100), nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    below_target: Mapped[bool] = mapped_column(Boolean, default=False)
    #: The languages of the evaluation, by name, as the release checker gives them.
    languages: Mapped[list[str]] = mapped_column(JSON, default=list)
    #: The matched formats TRaSH marks for renaming, for ``{Custom Formats}``.
    formats: Mapped[list[str]] = mapped_column(JSON, default=list)
    #: What the owner confirmed when loading: not_fitting, blocklisted.
    confirmed: Mapped[list[str]] = mapped_column(JSON, default=list)
    state: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    #: 0 to 100.
    progress: Mapped[float | None] = mapped_column(Float, nullable=True)
    remaining_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    problem_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    problem_values: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    #: How many rounds in a row the client did not know the download.
    missing_count: Mapped[int] = mapped_column(Integer, default=0)
    #: The path the client reports, as it reports it. Never trusted.
    reported_path: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    #: The client's own state, for the log; never shown as a sentence.
    client_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The imported file, relative to the version folder.
    imported_path: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    transfer: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: For a failed download, see ``FAILED_REASONS``. Never the client's sentence.
    failed_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: When the owner took a failed download off the problems; null while it waits there. ⚠️ From migration 18.
    cleared_at: Mapped[datetime | None] = mapped_column(nullable=True)
    #: For a failed download: what the client said, see ``FAILED_DETAILS``; null when it said nothing nexcrate knows.
    #: ⚠️ From the missing-column upkeep (22.09.2026).
    failed_detail: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: For a failed download: who takes care of it, see ``FAILURE_HANDLINGS``. Only ``owner`` makes it a problem.
    #: ⚠️ From the missing-column upkeep (22.09.2026).
    failure_handling: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: The client did not answer in time when it was handed the release (finding 1 of 22.09.2026): nexcrate looks for
    #: the job by its name until the client shows it. Null once confirmed. ⚠️ From the missing-column upkeep.
    handed_unsure_at: Mapped[datetime | None] = mapped_column(nullable=True)
    grabbed_at: Mapped[datetime] = mapped_column(default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    imported_at: Mapped[datetime | None] = mapped_column(nullable=True)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
    #: Who started it, see ``ORIGINS``. ⚠️ From migration 6.
    origin: Mapped[str] = mapped_column(String(16), default="manual", server_default="manual")
    # ⚠️ The columns below come from migration 11 (S4.0); a change needs a new migration.
    #: For a series download: ``episode``, ``season`` or ``series``; null for a movie.
    scope: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: The season of a series download when all its episodes lie in one; null otherwise.
    season: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: The numbering the release matched through when it was loaded (``scene``, ``tvdb``, ``tmdb`` ...).
    match_via: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: Episodes filed, video files still open, and episodes the download did not bring.
    filed_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    open_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    absent_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # ⚠️ From migration 16; a change needs a new migration.
    #: For an album download: the release the files turned out to be (decision 9); null until they were read.
    release_id: Mapped[int | None] = mapped_column(ForeignKey("releases.id", ondelete="SET NULL"), nullable=True)


class ExtraFile(Base):
    """A file nexcrate placed next to a version's movie file, so far a subtitle (step 3c, C9).

    ⚠️ The table comes from ``create_all`` with migration 6; a change of a column needs a new migration.
    """

    __tablename__ = "extra_files"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("versions.id", ondelete="CASCADE"), index=True)
    download_id: Mapped[int | None] = mapped_column(
        ForeignKey("downloads.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: Relative to the version's root folder, as ``versions.relative_path``; a subtitle of an episode file relative to
    #: the series folder, as ``episode_files.relative_path``.
    relative_path: Mapped[str] = mapped_column(String(4096))
    #: See ``EXTRA_KINDS``.
    kind: Mapped[str] = mapped_column(String(16), default="subtitle")
    #: ISO 639-1; null when unknown.
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    forced: Mapped[bool] = mapped_column(Boolean, default=False)
    sdh: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    #: The episode file a subtitle belongs to; null for a movie's. ⚠️ From migration 11.
    episode_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("episode_files.id", ondelete="CASCADE"), nullable=True, index=True
    )


class DownloadEpisode(Base):
    """An episode a series download is for, fixed when it was loaded (decision 2).

    ⚠️ From ``create_all`` with migration 11; a change of a column needs a new migration.
    """

    __tablename__ = "download_episodes"

    download_id: Mapped[int] = mapped_column(ForeignKey("downloads.id", ondelete="CASCADE"), primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"), primary_key=True, index=True)
    #: See ``EPISODE_ACTIONS``.
    action: Mapped[str] = mapped_column(String(16), default="fills")
    #: See ``EPISODE_STATES``.
    state: Mapped[str] = mapped_column(String(24), default="expected")
    episode_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("episode_files.id", ondelete="SET NULL"), nullable=True
    )


class DownloadFile(Base):
    """A video file of a finished series download, or a candidate of a movie download with several videos.

    ``path`` is relative to the download, NFC; ``id`` is the key the owner's dialog names a file by, never the path.
    ⚠️ From ``create_all`` with migration 11; a change of a column needs a new migration.
    """

    __tablename__ = "download_files"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    download_id: Mapped[int] = mapped_column(ForeignKey("downloads.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(String(1024))
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: What nexcrate read: ``{form, numbers, from, title}``; null when nothing was readable.
    reading: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    #: See ``FILE_DECISIONS``.
    decision: Mapped[str] = mapped_column(String(16), default="open")
    episode_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    episode_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("episode_files.id", ondelete="SET NULL"), nullable=True
    )


#: What became of an audio file of an album download (M4.3 to M4.6): filed with its track,
#: ``placing`` while it moves, ``open`` for the owner, filed without a track (``loose``, decision 32), another album of
#: the artist (``other_album``), not needed once the album is complete, or not filed by the owner.
AUDIO_DECISIONS = ("filed", "placing", "open", "loose", "other_album", "not_needed", "not_filed")


class DownloadAudioFile(Base):
    """An audio file of a finished album download (M4.1).

    ``path`` is relative to the download, NFC; ``id`` is the key the owner's dialog names a file by, never the path.
    ⚠️ From ``create_all`` with migration 16; a change of a column needs a new migration.
    """

    __tablename__ = "download_audio_files"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    download_id: Mapped[int] = mapped_column(ForeignKey("downloads.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(String(1024))
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    #: Codec, length, bit depth, sample rate, channels, bitrate as ``tags.Audio`` reads them; null when unreadable.
    audio: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    #: The tags nexcrate read, by Picard's names.
    tags: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    #: Medium, position, where they came from, title (``file_matching.Reading``).
    reading: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    #: See ``AUDIO_DECISIONS``.
    decision: Mapped[str] = mapped_column(String(16), default="open")
    #: The track of the release it was filed as, or proposed for.
    track_id: Mapped[int | None] = mapped_column(
        ForeignKey("release_tracks.id", ondelete="SET NULL"), nullable=True
    )
    #: How it was matched: ``id``, ``position``, ``name``, ``fingerprint``, ``owner``.
    via: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: Tracks proposed for an open file, best first.
    proposal: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    #: The album another album's file belongs to (decision 12).
    other_title_id: Mapped[int | None] = mapped_column(ForeignKey("titles.id", ondelete="SET NULL"), nullable=True)
    track_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("track_files.id", ondelete="SET NULL"), nullable=True
    )
    #: While ``placing``: the name it gets, relative to the album folder (decision 19).
    target: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    #: What AcoustID said (M4.9): ``{"state": "matched"|"none"|"failed", "recordings": [...]}``.
    fingerprint: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class BlocklistEntry(Base):
    """A release that failed or was refused for a title.

    A release counts as blocked for the title when an entry has its info hash, or its release title (case ignored)
    with the same protocol.
    """

    __tablename__ = "blocklist"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title_id: Mapped[int] = mapped_column(ForeignKey("titles.id", ondelete="CASCADE"), index=True)
    release_title: Mapped[str] = mapped_column(String(1024))
    protocol: Mapped[str] = mapped_column(String(16))
    indexer_id: Mapped[int | None] = mapped_column(ForeignKey("indexers.id", ondelete="SET NULL"), nullable=True)
    #: The indexer's name at the time, kept when the indexer is gone.
    indexer_name: Mapped[str] = mapped_column(String(100), default="")
    release_key: Mapped[str] = mapped_column(String(64), default="")
    #: A torrent's info hash in lower case; null for Usenet.
    info_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: See ``BLOCK_REASONS``.
    reason: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class PendingRelease(Base):
    """A release the automatic would have loaded, waiting out the delay of its version.

    One row per release, title and version. The release itself lies in ``payload``, encrypted, because its link
    carries the indexer's key or a passkey: it is judged again from there once the wait is over, without asking the
    indexer a second time (a release from RSS is no longer in the feed by then).

    ⚠️ Comes from ``create_all``, without a migration.
    """

    __tablename__ = "pending_releases"
    __table_args__ = (
        UniqueConstraint(
            "title_id", "version_definition_id", "indexer_id", "release_key", name="uq_pending_releases_release"
        ),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title_id: Mapped[int] = mapped_column(ForeignKey("titles.id", ondelete="CASCADE"), index=True)
    version_definition_id: Mapped[int] = mapped_column(
        ForeignKey("version_definitions.id", ondelete="CASCADE"), index=True
    )
    #: Without its indexer a release can neither be judged (priority, seeders rule) nor fetched: it goes with it.
    indexer_id: Mapped[int] = mapped_column(ForeignKey("indexers.id", ondelete="CASCADE"), index=True)
    indexer_name: Mapped[str] = mapped_column(String(100), default="")
    release_key: Mapped[str] = mapped_column(String(64))
    release_title: Mapped[str] = mapped_column(String(1024))
    protocol: Mapped[str] = mapped_column(String(16))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: What it was read as when it was put here, for the list only.
    quality: Mapped[str | None] = mapped_column(String(64), nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: The episode ids of a series release it was wanted for; null for a movie or an album.
    episode_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    #: Their codes in words (``S02E04``), for the list only.
    episode_codes: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    #: search or rss: where the automatic found it.
    origin: Mapped[str] = mapped_column(String(16), default="search")
    published_at: Mapped[datetime | None] = mapped_column(nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    #: From when the automatic may load it, by the rule as it stood when this was written; worked out again whenever
    #: the rule of the version changes.
    due_at: Mapped[datetime] = mapped_column(index=True)
    #: ⚠️ ``enc:…``: the release as the feed gave it, its link included. Never in an answer or a log line.
    payload: Mapped[str] = mapped_column(Text)


#: What a job in nexcrate's category that no download follows can be, as ``downloaders.Job`` says.
FOREIGN_STATES = ("queued", "downloading", "paused", "completed", "failed", "problem")


class ForeignJob(Base):
    """A job in nexcrate's category of a client that no download of nexcrate follows (the owner's finding 2 of
    22.09.2026): put there by hand, by another program, or by a hand-over whose answer never came. It shows under
    Downloads, Problems, until the owner imports it into a title, removes it, or it leaves the client.

    ``name`` is the client's name of the job, ``reported_path`` its finished path as the client reports it (never
    trusted, never answered). ⚠️ From ``create_all``, without a migration.
    """

    __tablename__ = "foreign_jobs"
    __table_args__ = (
        UniqueConstraint("client_id", "client_download_id", name="uq_foreign_jobs_job"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("download_clients.id", ondelete="CASCADE"), index=True)
    client_download_id: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(1024))
    #: See ``FOREIGN_STATES``.
    state: Mapped[str] = mapped_column(String(16))
    progress: Mapped[float | None] = mapped_column(Float, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reported_path: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
