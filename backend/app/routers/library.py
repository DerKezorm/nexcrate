"""The library: every title over every source, with its versions, and the titles the owner adds.

Owner and sources together: the owner adds a movie from TMDB with wanted
versions of his own. An import of the same movie takes such a version over. A version fed by a
source cannot be removed here; it goes when it is removed in Radarr or the connection is deleted.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from ..db import SessionLocal
from ..deps import DbSession
from ..meldungen import error, error_responses
from ..models import Artist, Download, Episode, Season, Source, Title, Version, VersionDefinition, utcnow
from ..models.media import KINDS, STATES
from ..models.series import SERIES_TYPES
from ..services import (
    auto_tags,
    companions,
    companions_series,
    downloaders,
    images,
    library,
    naming_series,
    ratings,
    recycle_bin,
    tmdb,
)
from ..services.automatic import clock as automatic_clock
from ..services.automatic import planning as automatic_planning
from ..services.downloads import actions as download_actions
from ..services.downloads import store as download_store
from ..services.music import loading as music_loading
from ..services.music import store as music_store
from ..services.series import absolute, folder_read, names, tmdb_series, type_proposals, unclear, watching
from ..services.series import detail as series_detail
from ..services.series import store as series_store

logger = logging.getLogger("nexcrate.library")

router = APIRouter(prefix="/api/library", tags=["library"])

#: At most this many version definitions in one request.
VERSION_IDS_MAX = 50


class EpisodeCounts(BaseModel):
    """Regular episodes only, as Sonarr's progress counts; specials have their own line."""

    files: int = Field(description="Regular episodes with a file in this version.")
    have: int = Field(description="Watched regular episodes that aired and have a file.")
    aired_watched: int = Field(description="Watched regular episodes that aired.")
    wanted: int = Field(description="Watched regular episodes that aired and have no file.")
    watched: int = Field(description="Regular episodes the version watches.")
    total: int = Field(description="Regular episodes TMDB lists, specials left out.")
    next_air_date: str | None = Field(
        description="YYYY-MM-DD of the next watched regular episode that has not aired; null without one."
    )
    specials_missing: int = Field(
        default=0, description="Watched specials that aired and have no file. They do not make the version wanted."
    )


class VersionBrief(BaseModel):
    id: int
    label: str = Field(examples=["4K"])
    state: str = Field(
        description="problem, downloading, incomplete (an album lacking tracks), upgrade, available, wanted or "
        "unmonitored."
    )
    progress: float | None = Field(description="Percent while downloading, null otherwise.", examples=[42.5])
    quality: str | None = Field(
        description=(
            "Radarr's decision about the file, shown as it comes. For an album the quality step of its worst file: "
            "lossless_24, lossless, lossy_high, lossy_mid, lossy_low or unknown. Null without a file."
        ),
        examples=["Bluray-2160p"],
    )
    size_bytes: int | None = Field(
        description="Size of the file, or of every file of a series version. Null without a file.",
        examples=[58_000_000_000],
    )
    counts: EpisodeCounts | None = Field(
        default=None, description="A series version's episode counts. Null for a movie version."
    )
    monitored: bool = Field(default=True, description="Whether nexcrate watches this version.")
    unclear_files: int = Field(
        default=0,
        description="Files of a series version that belong to no episode yet and are not left out; 0 for a movie.",
    )


class TitleSummary(BaseModel):
    artist: str | None = Field(default=None, description="Only for an album: the name of its artist.")
    id: int
    kind: str = Field(examples=["movie"])
    title: str
    year: int | None
    poster_url: str | None = Field(
        description="`/api/images/{id}/poster?v=<cache key>`; the key changes with the image.",
    )
    versions: list[VersionBrief]
    imdb_rating: float | None = Field(
        default=None, description="IMDb's rating from nexcrate's copy of its daily file; null without one."
    )
    tags: list[str] = Field(
        default_factory=list, description="Its tags by name; an album shows those of its artist."
    )


class LibraryPage(BaseModel):
    items: list[TitleSummary]
    total: int = Field(description="All titles that match, over all pages.")


class LibraryStats(BaseModel):
    movies: int
    series: int
    episodes: int = Field(default=0, description="Every episode of every series, whatever its state.")
    artists: int = Field(default=0, description="Every artist, also one whose albums nexcrate does not hold.")
    albums: int
    size_bytes: int = Field(description="Size of every file of every version.")
    problems: int = Field(description="Versions in the state problem.")
    problem_titles: int = Field(
        description="Titles with at least one version in the state problem. This is what the state filter shows."
    )
    unclear_titles: int = Field(
        default=0, description="Series with at least one unclear file. This is what the filter `state=unclear` shows."
    )
    unclear_albums: int = Field(
        default=0, description="Music M6: albums with a file of their folder without a track (`state=unclear`)."
    )
    state_titles: dict[str, dict[str, int]] = Field(
        default_factory=dict,
        description="Per kind (movie, series, album) and version state the titles with a version in that state, what "
        "the filter `state=` lists. A state without titles is left out.",
        examples=[{"movie": {"wanted": 3}, "series": {}, "album": {"incomplete": 2, "upgrade": 41}}],
    )


class VersionDownload(BaseModel):
    id: int
    state: str = Field(description="A download state, see /api/downloads.")
    progress: float | None = Field(description="0 to 100.")
    problem_code: str | None = Field(description="The download's problem or hint, null without one.")
    client_name: str | None = Field(description="The download client's name; null when the client was deleted.")


class VersionLocation(BaseModel):
    kind: Literal["file", "target", "radarr", "sonarr"] = Field(
        description="file: the folder holding the current file of a version of nexcrate's own. target: where the next "
        "file of a version of nexcrate's own without a file goes, its `root_folder` when set, else the version's "
        "default folder. radarr: the movie folder of a version a source feeds, as Radarr sees it. sonarr: the series "
        "folder of a series version a Sonarr connection feeds, as Sonarr sees it."
    )
    path: str = Field(examples=["/media/Movies/Example Movie (2003)"])


class SubtitleFile(BaseModel):
    language: str | None = Field(description="ISO 639-1; null when the file's name did not tell.", examples=["de"])
    forced: bool = Field(description="Only the foreign parts: the name said forced or foreign.")
    sdh: bool = Field(description="For the hard of hearing: the name said sdh, cc or hi.")
    file: str = Field(
        description="Relative to `root_folder`, as `relative_path`; never a full path.",
        examples=["Example Movie (2003)/Example Movie (2003).de.srt"],
    )


class SeriesWatch(BaseModel):
    rule: str | None = Field(
        description="all, future, missing, from_season or none. Null for a version a Sonarr connection feeds."
    )
    from_season: int | None = Field(description="The first season the rule from_season watches.")
    custom: bool = Field(description="The owner switched seasons or episodes by hand since the rule was applied.")
    fed: bool = Field(
        description="A Sonarr connection feeds the version: its switches come from Sonarr and cannot be changed here."
    )


class SeasonVersionBrief(BaseModel):
    version_id: int
    watched: bool = Field(description="The season's switch in this version; new episodes of the season take it.")
    files: int = Field(description="Episodes of the season with a file in this version.")
    have: int = Field(description="Watched episodes of the season that aired and have a file.")
    aired_watched: int = Field(description="Watched episodes of the season that aired.")
    watched_episodes: int = Field(description="Episodes of the season this version watches.")
    loading: int = Field(default=0, description="Episodes a download of this version holds: loading or a problem (S4).")


class SeasonBrief(BaseModel):
    id: int
    number: int = Field(description="TMDB's season number; 0 holds the specials.")
    name: str
    air_date: str | None = Field(description="YYYY-MM-DD.")
    episodes: int = Field(description="Episodes TMDB lists for the season.")
    aired: int = Field(description="Of those, the episodes that aired before today.")
    tmdb_gone: bool = Field(description="TMDB no longer lists the season; it stays while episodes hang on it.")
    versions: list[SeasonVersionBrief]


class NearbyEpisode(BaseModel):
    id: int
    season: int
    episode: int
    name: str | None
    air_date: str | None


class FileProposal(BaseModel):
    episode_ids: list[int]
    step: int = Field(
        description="1 the same air date and title, 2 the same title, 3 the same air date (one day either way), 4 the "
        "same numbers in TVDB's or the scene numbering. Only 1 and 2 are taken by "
        "`POST .../files/proposals`."
    )
    safe: bool = Field(
        default=False,
        description="The proposal rests on a source episode's own title; `POST .../files/proposals` takes only these "
        "unless `from_names` is true.",
    )


class SourceEpisodeOfFile(BaseModel):
    season: int
    episode: int
    name: str | None
    air_date: str | None


class OccupyingFile(BaseModel):
    id: int
    relative_path: str = Field(description="Relative to the series folder.")
    size_bytes: int | None = None
    quality: str | None = None


class OccupiedEpisode(NearbyEpisode):
    file: OccupyingFile = Field(description="The file the episode has now.")


class EpisodeChoice(NearbyEpisode):
    file: OccupyingFile | None = Field(description="The file the episode has in this version; null without one.")


class UnassignedFile(BaseModel):
    id: int
    version_id: int
    relative_path: str = Field(description="Relative to the series folder.")
    size_bytes: int
    quality: str | None
    source_numbers: dict[str, Any] | None = Field(
        description='The source\'s season and episodes of the file, `{"season": 2, "episodes": [13]}`.'
    )
    read_as: dict[str, Any] | None = Field(
        default=None,
        description="For a file read from disk: `season`, `episodes`, `numbering` and `reason` (no_numbers, "
        "unknown_numbers, ambiguous, counted_otherwise, episode_has_file, duplicate).",
    )
    left_out: bool = Field(default=False, description="The owner said it belongs to no episode.")
    source_episode: SourceEpisodeOfFile | None = Field(
        default=None, description="Sonarr's episode with the file's numbers, when Sonarr knew one."
    )
    proposal: FileProposal | None = Field(
        default=None, description="Version of nexcrate's own only: the episodes nexcrate would assign."
    )
    nearby: list[NearbyEpisode] = Field(
        default_factory=list,
        description="Version of nexcrate's own only: episodes without a file around the file's place, at most 12.",
    )
    occupied: list[OccupiedEpisode] = Field(
        default_factory=list,
        description="For a file read as episodes that have a file already (reason episode_has_file): those episodes "
        "with their file, so the owner can take this one instead (`replace`).",
    )


class FolderReading(BaseModel):
    version_id: int
    state: Literal["queued", "reading"]
    done: int
    total: int | None


class SeriesBlock(BaseModel):
    type: str = Field(description="standard, daily or anime.")
    type_fed: str | None = Field(
        default=None,
        description=(
            "The connection that decides the type, or null when the owner does. A series a Sonarr connection feeds "
            "takes its type from there on every run, and `PATCH /api/library/{title_id}` answers 409 for it."
        ),
    )
    status: str | None = Field(description="TMDB's status as it comes, for example Returning Series or Ended.")
    networks: list[str]
    first_air_date: str | None
    last_air_date: str | None
    next_air_date: str | None
    tvdb_id: int | None
    numbering: dict[str, Any] | None = Field(
        description="How a Sonarr connection counts against TMDB: `tmdb` and `source` with `seasons`, `episodes` and "
        "`sizes` (episodes per season), the source's `name`, and `unmatched` episodes. Null when both agree."
    )
    late_episodes: int = Field(
        description="Episodes TMDB added long after they aired that nexcrate does not watch by itself."
    )
    episode_groups: list[dict[str, Any]] = Field(description="TMDB's episode groups: id, name, type and counts.")
    seasons: list[SeasonBrief] = Field(description="Every season with counts per version; episodes per season below.")
    unassigned_files: list[UnassignedFile] = Field(
        description="Files without an episode: from Sonarr no TMDB episode matched, or read from disk without a clear "
        "episode (Ü3). Left-out files come last."
    )
    reading: list[FolderReading] = Field(
        default_factory=list,
        description="Series versions of nexcrate's own whose folder is not read yet; they want nothing until then.",
    )


class CompanionState(BaseModel):
    state: str = Field(
        description="written, current, missing, outdated, other_installation, newer_format, broken, foreign, changed, "
        "not_writable, no_space, folder_missing, file_missing or failed.",
        examples=["written"],
    )
    written_at: datetime | None = Field(description="UTC. When nexcrate last wrote the file; null when it never did.")


class SourceOnlyEpisode(BaseModel):
    season: int
    episode: int = Field(description="The source's number, for Sonarr TVDB's.")
    name: str
    air_date: str | None = Field(description="YYYY-MM-DD.")
    watched: bool
    has_file: bool


class TitleVersion(BaseModel):
    id: int
    origin: str | None = Field(
        default=None, description="The reference of the program that asked for this version through /api/v1."
    )
    origin_key: str | None = Field(default=None, description="The name of that program's key.")
    version_id: int = Field(
        description="The version definition: the same id as in `/api/versions`, in `version_ids` when adding a "
        "title and in `add` and `remove` when changing its versions."
    )
    label: str
    profile_name: str | None = Field(description="The quality profile at the source.")
    root_folder: str | None = Field(
        description="Radarr's root folder for a version a source feeds. For a version of nexcrate's own the folder its "
        "file lies below, or for one without a file the folder its next file goes into, when it has one."
    )
    state: str
    quality: str | None = Field(
        description=(
            "Radarr's decision about the file, shown as it comes. For an album the quality step of its worst file "
            "(lossless_24, lossless, lossy_high, lossy_mid, lossy_low, unknown); below the target of the music "
            "profile the state is upgrade."
        ),
        examples=["Bluray-1080p"],
    )
    upgrade_to: str | None = Field(
        description="What the profile still wants. For a version a source feeds, the cutoff of its profile "
        "there while the source says the cutoff is not met, as the source names it. For a version of "
        "nexcrate's own that can still be upgraded, the profile's cutoff item when upgrades are allowed and "
        "the file's quality ranks at or below it; a group by its name, its qualities in `upgrade_to_items`. "
        "Null otherwise.",
        examples=["Bluray-1080p"],
    )
    upgrade_to_items: list[str] = Field(
        default_factory=list,
        description="The qualities of `upgrade_to` when it is a group, for a version of nexcrate's own, in the "
        "profile's order: those that reach the profile's target resolution, every one when none does. Empty for a "
        "single quality, without `upgrade_to`, and for a version a source feeds (the source names only the group).",
        examples=[["Bluray-1080p", "WEBRip-1080p", "WEBDL-1080p"]],
    )
    upgrade_reason: Literal["quality", "score"] | None = Field(
        default=None,
        description="Why nexcrate would still upgrade the file of a version of its own (decision 20). quality: its "
        "quality ranks below the profile's cutoff, or its resolution lies below the target resolution. score: its "
        "quality ranks at the cutoff and `current_score` lies below `upgrade_until`. Null for a version a source "
        "feeds, without a file or movie rules, and while the stored judgement (`state`) says the file is done.",
    )
    current_score: int | None = Field(
        default=None,
        description="The score of the file of a version of nexcrate's own, scored as the upgrade check of a search "
        "scores the current file: by the release name it came with, else its file name, with its stored quality "
        "and the title's original language. Null for a version a source feeds, and without a file or movie rules.",
        examples=[11700],
    )
    upgrade_until: int | None = Field(
        default=None,
        description="The profile's upgrade-until score for a version of nexcrate's own with a file: a file at the "
        "cutoff quality with a lower score is still upgraded. Null while the profile's upgrades are switched off, "
        "for a version a source feeds, and without a file or movie rules.",
        examples=[35000],
    )
    size_bytes: int
    languages: list[str] = Field(description="Languages of the file as the source names them.")
    release_group: str | None
    relative_path: str | None = Field(description="File name inside the movie folder.")
    source_name: str | None = Field(description="The connection that feeds this version; null when none does.")
    monitored: bool = Field(
        default=True, description="Whether nexcrate watches this version. Off: it searches nothing for it any more."
    )
    last_failure: dict[str, Any] | None = Field(
        default=None,
        description="The newest failed download of this version the owner has not taken off: id, at, reason "
        "(client_failed or encrypted). Its release is on the blocklist. Null when there is none.",
    )
    waiting: dict[str, Any] | None = Field(
        default=None,
        description="Releases the automatic keeps for this version until its delay rule lets them load: count, "
        "due_at (the moment the first may load) and due (that moment has passed). Null when none waits. The list is "
        "`GET /api/waiting?title_id=`.",
    )
    added_by: str = Field(
        description="import or owner. A version the owner added stays owner when a source takes it over."
    )
    progress: float | None = Field(description="Percent while downloading, null otherwise.")
    problem_code: str | None = Field(
        description="While in problem: import_blocked, import_pending, download_error or download_warning from Radarr, "
        "or the problem code of nexcrate's own download.",
    )
    download: VersionDownload | None = Field(
        default=None, description="nexcrate's download of this version that is not finished, if any."
    )
    pending_downloads: int = Field(
        default=0,
        description="nexcrate's downloads of this version still in their client and not filed away: queued to "
        "importing, or a problem, hints such as dangerous_file included. Removing the version or the title answers 409 "
        "for them unless remove_downloads is set.",
    )
    location: VersionLocation | None = Field(
        description="Where the movie of this version lies or goes. Null when that is not known: a version of "
        "nexcrate's own with a file whose folder is not stored, one without a file that has neither `root_folder` nor "
        "a default folder, or a version a source feeds whose movie folder no import has kept yet."
    )
    subtitles: list[SubtitleFile] = Field(
        default_factory=list,
        description="The subtitle files nexcrate placed next to the file of a version of its own, by language. Empty "
        "for a version a source feeds, without a file, or when nexcrate placed none; files placed by others are not "
        "listed.",
    )
    companion: CompanionState | None = Field(
        default=None,
        description="The state of the version's release.nex. Null for a version a "
        "source feeds or one nexcrate never looked at.",
    )
    quality_from: str | None = Field(
        default=None,
        description="Where the file's quality came from: name (the release name), media (the file's media data) or "
        "radarr (the takeover kept Radarr's word). Null when nobody recorded it.",
    )
    media: dict[str, Any] | None = Field(
        default=None, description="The stored media data of the file in nexcrate's own shape, null when none was read."
    )
    watch: SeriesWatch | None = Field(default=None, description="What a series version watches. Null for a movie.")
    counts: EpisodeCounts | None = Field(default=None, description="A series version's counts. Null for a movie.")
    source_only_episodes: list[SourceOnlyEpisode] = Field(
        default_factory=list,
        description="Episodes the Sonarr connection feeding the version has and TMDB does not, mostly specials. "
        "nexcrate cannot search, load or assign them. Empty for a movie and for a version of nexcrate's own.",
    )


class HistoryItem(BaseModel):
    at: datetime = Field(description="UTC.")
    event: str = Field(
        description="added, imported, grabbed, failed, taken_over, takeover_undone, found_on_disk, restored, and for "
        "series renumbered (detail: old and new numbers, `S01E05 S01E06`) and episodes_late (detail: how many)."
    )
    version: str = Field(description="Label of the version.")
    detail: str | None = Field(
        description="The quality for grabbed; for imported the quality and, when subtitle files were placed, how many "
        "(`Bluray-1080p, 2 subtitles`); the reason for failed; null for added."
    )
    download_id: int | None = Field(
        default=None,
        description="For album_filed: the album download; `GET /api/downloads/{id}/album-files` says which file went "
        "to which track.",
    )


class SearchAnchor(BaseModel):
    date: str | None = Field(
        description="From when the title is searched, YYYY-MM-DD: the digital or physical release, or 90 days after "
        "the theatrical one. Null for year and none.",
        examples=["2026-08-20"],
    )
    kind: str = Field(description="digital, physical, theatrical, year (only the year is known) or none.")
    country: str | None = Field(
        description="ISO 3166-1 of a release in a country of the version's required languages; null for the general "
        "anchor.",
        examples=["DE"],
    )


class SearchSummaryIndexer(BaseModel):
    id: int
    name: str
    code: str | None = Field(description="The indexer's error code in that search, null when it answered.")


class SearchSummaryVersion(BaseModel):
    version_id: int
    label: str
    best_title: str | None = Field(description="The release it took or would take, else the closest one; never a link.")
    codes: list[str] = Field(description="At most 5 reasons why the best release does not fit or is no upgrade.")
    loaded: bool
    load_code: str | None = Field(description="Why nothing was loaded for this version, when a code says why.")


class SearchSummary(BaseModel):
    at: datetime = Field(description="UTC.")
    origin: str = Field(description="search, rss or replacement.")
    releases: int = Field(description="Releases of this title found.")
    indexers: list[SearchSummaryIndexer]
    versions: list[SearchSummaryVersion] = Field(description="Per movie version; empty for a series (see seasons).")


class SeasonPackOnly(BaseModel):
    size: int | None = Field(description="Bytes of the season pack left out.")
    episodes: int = Field(description="Episodes it would have replaced.")


class SeasonResultVersion(BaseModel):
    version_id: int
    label: str
    loaded: int = Field(description="Releases loaded for this season.")
    filled: int = Field(description="Episodes without a file they bring.")
    replaced: int = Field(description="Episodes they upgrade.")
    not_found: int = Field(description="Wanted episodes no release names.")
    no_fit: int = Field(description="Wanted episodes with releases, none of which fits or may be taken.")
    codes: list[str] = Field(description="At most 5 reasons why releases did not fit, most frequent first.")
    pack_only: SeasonPackOnly | None = Field(
        description="Only a season pack would upgrade these episodes, fewer than half of its own: the owner loads it."
    )
    load_code: str | None = Field(description="Why a release or the version was not loaded, when a code says why.")


class SeasonResult(BaseModel):
    season: int
    at: datetime = Field(description="UTC. The search or RSS sync the result comes from.")
    versions: list[SeasonResultVersion]


class SeasonPlanOut(BaseModel):
    season: int = Field(description="TMDB's season number; 0 holds the specials.")
    next_at: datetime | None = Field(description="UTC. The season's next automatic search.")
    reason: str = Field(
        description="schedule, limit, replacement, replacement_limit, air_date (waits for the day after an episode "
        "aired), no_date (episodes without an air date), or off."
    )
    missing: int = Field(description="Aired episodes a version has no file for.")
    upgrades: int = Field(description="Aired episodes whose file can still be upgraded.")
    waiting: int = Field(description="Wanted episodes that have not aired yet.")
    no_date: int = Field(description="Wanted episodes without an air date.")
    last_at: datetime | None = Field(description="UTC. The last automatic search of the season.")
    result: SeasonResult | None = Field(description="The outcome of the season's last automatic search or load.")


class SearchHold(BaseModel):
    version_id: int = Field(description="The series version definition.")
    reason: Literal["reading_files", "unclear_files"] = Field(
        description="reading_files: its series folder is not read yet. unclear_files: files without an episode hold "
        "back the episodes that aired before the version became nexcrate's (Ü3)."
    )
    count: int = Field(description="Unclear files; 0 while reading.")
    since: str | None = Field(description="The day the version became nexcrate's; null while reading.")


class SearchPlan(BaseModel):
    automatic: bool = Field(description="Whether automatic searching is switched on.")
    wanted: bool = Field(description="Whether a version of the title wants something: no file, or an upgrade.")
    last_at: datetime | None = Field(description="UTC. The last automatic search.")
    next_at: datetime | None = Field(description="UTC. The next automatic search; null when nothing is wanted.")
    reason: str = Field(
        description="anchor (waits for its anchor date), schedule, limit (the indexers' budget), replacement (after a "
        "failed download), replacement_limit (three replacements in 24 hours), no_date (waits for a release date), "
        "nothing_wanted, or off (it would search now, but automatic searching is off)."
    )
    anchor: SearchAnchor
    summary: SearchSummary | None = Field(description="The outcome of the last automatic search.")
    seasons: list[SeasonPlanOut] = Field(
        default_factory=list, description="A series only: one line per season that wants something."
    )
    held: list[SearchHold] = Field(
        default_factory=list, description="A series only: what holds a version of nexcrate's own back."
    )


class ReleaseHead(BaseModel):
    id: int
    mbid: str
    name: str
    date: str | None
    country: str | None
    formats: list[str]
    media_count: int
    track_count: int
    labels: list[dict[str, Any]]
    disambiguation: str | None
    audio_only: bool
    tracks_loaded: bool
    gone: bool


class AlbumTrack(BaseModel):
    id: int
    medium: int
    medium_format: str | None
    position: int
    number: str | None
    name: str
    length_ms: int | None
    artist_credit: list[dict[str, Any]] | None
    present: bool
    file: dict[str, Any] | None


class AlbumCredit(BaseModel):
    mbid: str
    name: str
    join: str
    artist_id: int | None


class AlbumBlock(BaseModel):
    mbid: str | None
    artist_id: int | None
    artist_name: str | None
    credit: list[AlbumCredit]
    primary_type: str | None
    secondary_types: list[str]
    group: str
    first_release_date: str | None
    disambiguation: str | None
    releases_state: str
    releases_refreshed_at: datetime | None
    mb_gone_at: datetime | None
    version_id: int | None
    monitored: bool
    target: ReleaseHead | None
    target_set_by: str | None
    target_reason: dict[str, Any] | None
    suggestion: ReleaseHead | None
    actual: ReleaseHead | None
    track_counts: dict[str, int] | None
    tracks: list[AlbumTrack]
    releases: list[ReleaseHead]
    unclear_files: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Music M6: files of the album folder without a track the owner has not settled: id, "
        "relative_path (inside the album folder), size, quality.",
    )
    can_read_folder: bool = Field(default=False, description="An own album with a folder: it can be read again.")
    files_read_at: datetime | None = Field(default=None, description="UTC. When the album folder was read last.")


class TitleDetail(BaseModel):
    id: int
    tags: list[str] = Field(default_factory=list, description="Its tags; an album shows those of its artist.")
    kind: str
    title: str
    year: int | None
    poster_url: str | None
    original_title: str | None
    runtime_min: int | None
    genres: list[str] = Field(description="English, as the source or TMDB names them.")
    overview: str | None
    tmdb_id: int | None = Field(description="Null for an album: it has a MusicBrainz release group instead.")
    imdb_id: str | None
    versions: list[TitleVersion]
    history: list[HistoryItem] = Field(description="Newest first.")
    search_plan: SearchPlan = Field(
        description="Automatic searching for this title: last and next search, why, and the outcome."
    )
    series: SeriesBlock | None = Field(default=None, description="Only for a series: seasons, counts, numbering.")
    album: AlbumBlock | None = Field(
        default=None,
        description="Only for an album: the credit, the target release with its reasons, its tracks, the releases.",
    )


class SeriesVersionIn(BaseModel):
    version_id: int = Field(description="A series version definition.")
    rule: str = Field(default="all", max_length=16, description="all, future, missing, from_season or none.")
    from_season: int | None = Field(
        default=None, ge=1, le=10_000, description="The first season from_season watches; needed for that rule."
    )


class TitleIn(BaseModel):
    kind: Literal["movie", "series"] = Field(default="movie", description="movie or series. Music comes later.")
    tmdb_id: int = Field(ge=1, le=2_147_483_647)
    version_ids: list[int] = Field(
        default_factory=list,
        max_length=VERSION_IDS_MAX,
        description="Movies: the movie version definitions the title gets, at least one.",
    )
    versions: list[SeriesVersionIn] = Field(
        default_factory=list,
        max_length=VERSION_IDS_MAX,
        description="Series: the series version definitions with what each watches, at least one.",
    )
    series_type: str | None = Field(
        default=None, max_length=16, description="Series: standard, daily or anime; null takes TMDB's proposal."
    )


class VersionsPatch(BaseModel):
    add: list[int] = Field(
        default_factory=list, max_length=VERSION_IDS_MAX, description="Version definitions to add as your own."
    )
    remove: list[int] = Field(
        default_factory=list,
        max_length=VERSION_IDS_MAX,
        description="Version definitions to remove. Only versions no source feeds; ids the title lacks are ignored.",
    )
    remove_downloads: bool = Field(
        default=False,
        description="Remove running downloads of the removed versions from their clients first. Without it such a "
        "download answers 409 version_download_active.",
    )


def _unique(values: list[int]) -> list[int]:
    result: list[int] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _definitions(db: OrmSession, ids: list[int], kind: str, field: str) -> list[VersionDefinition]:
    if not ids:
        return []
    rows = {row.id: row for row in db.scalars(select(VersionDefinition).where(VersionDefinition.id.in_(ids)))}
    if any(identifier not in rows for identifier in ids):
        raise error("invalid_input", "The input is not valid.", 422, fields=[field])
    if any(rows[identifier].kind != kind for identifier in ids):
        raise error(
            "version_kind_mismatch", "This version belongs to another type. A movie needs a movie version.", 422
        )
    return [rows[identifier] for identifier in ids]


def _title_exists(title_id: int) -> HTTPException:
    return error("title_exists", "This movie is already in the library.", 409, title_id=title_id)


def _existing_title(db: OrmSession, tmdb_id: int, kind: str = "movie") -> int | None:
    return db.scalar(select(Title.id).where(Title.kind == kind, Title.tmdb_id == tmdb_id))


def _check_new_title(tmdb_id: int, version_ids: list[int]) -> None:
    with SessionLocal() as db:
        _definitions(db, version_ids, "movie", "version_ids")
        existing = _existing_title(db, tmdb_id)
    if existing is not None:
        raise _title_exists(existing)


def _create_title(data: tmdb.MovieData, version_ids: list[int]) -> int:
    with SessionLocal() as db:
        definitions = _definitions(db, version_ids, "movie", "version_ids")
        existing = _existing_title(db, data.tmdb_id)
        if existing is not None:
            raise _title_exists(existing)
        try:
            title = library.add_title(db, data, definitions, utcnow())
            db.commit()
        except IntegrityError as exc:
            # Added by another request in the meantime.
            db.rollback()
            raise _title_exists(_existing_title(db, data.tmdb_id) or 0) from exc
        logger.info("Title %d added by the owner with %d versions", title.id, len(definitions))
        return title.id


def _detail(title_id: int) -> TitleDetail:
    with SessionLocal() as db:
        found = library.detail(db, title_id)
    if found is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    return TitleDetail.model_validate(found)


def _choice(value: str | None, allowed: tuple[str, ...], name: str, default: str | None) -> str | None:
    """An empty parameter means the default. The interface sends ``state=`` for no filter."""
    cleaned = (value or "").strip()
    if not cleaned:
        return default
    if cleaned not in allowed:
        raise error("invalid_input", "The input is not valid.", 422, fields=[name])
    return cleaned


@router.get(
    "",
    response_model=LibraryPage,
    summary="List the titles of the library",
    description=(
        "One page of titles with their versions: label, state, and quality and size of the file (null without "
        "one). `state` keeps titles with a version in that state. `q` searches title, original title and "
        "alternate titles, with umlauts and accents in every spelling. `sort` is title (without a leading "
        "article), year (newest first) or added (newest first). `state=unclear` is not a version state: it keeps "
        "series with files that belong to no episode yet."
    ),
)
def list_library(
    db: DbSession,
    kind: Annotated[str | None, Query(max_length=16, description="movie, series or album. Default movie.")] = None,
    state: Annotated[str | None, Query(max_length=16, description="One state, or unclear; empty for all.")] = None,
    q: Annotated[str | None, Query(max_length=200, description="Search text.")] = None,
    sort: Annotated[str | None, Query(max_length=16, description="title, year or added. Default title.")] = None,
    page: Annotated[int, Query(ge=1, le=100_000, description="Page, from 1.")] = 1,
    page_size: Annotated[
        int, Query(ge=1, le=library.PAGE_SIZE_MAX, description="1 to 200.")
    ] = library.PAGE_SIZE_DEFAULT,
    tag: Annotated[str | None, Query(max_length=64, description="Only titles carrying this tag.")] = None,
) -> LibraryPage:
    chosen_kind = _choice(kind, KINDS, "kind", "movie") or "movie"
    chosen_state = _choice(state, (*STATES, library.UNCLEAR), "state", None)
    chosen_sort = _choice(sort, library.SORTS, "sort", "title") or "title"
    items, total = library.list_titles(
        db, kind=chosen_kind, state=chosen_state, query=q, sort=chosen_sort, page=page, page_size=page_size, tag=tag
    )
    summaries = [TitleSummary.model_validate(item) for item in items]
    if chosen_kind in ("movie", "series") and summaries:
        # IMDb's rating for the page's titles, from the separate ratings file (decision 10).
        numbers = dict(
            db.execute(
                select(Title.id, Title.imdb_id).where(
                    Title.id.in_([item.id for item in summaries]), Title.imdb_id.is_not(None)
                )
            )
            .tuples()
            .all()
        )
        found = ratings.imdb_many(numbers.values())
        for item in summaries:
            value = found.get(numbers.get(item.id) or "")
            item.imdb_rating = value[0] if value else None
    return LibraryPage(items=summaries, total=total)


@router.get(
    "/stats",
    response_model=LibraryStats,
    summary="Count the library",
    description=(
        "Titles per kind, the size of all files, the versions with a problem and the titles with at least one. "
        "The interface shows `problem_titles`, because the state filter lists titles."
    ),
)
def library_stats(db: DbSession) -> LibraryStats:
    return LibraryStats.model_validate(library.stats(db))


@router.get(
    "/{title_id}",
    response_model=TitleDetail,
    summary="Read one title",
    description="The title with every version in full and its history.",
    responses=error_responses((404, "not_found")),
)
def read_title(title_id: int, db: DbSession) -> TitleDetail:
    found = library.detail(db, title_id)
    if found is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    return TitleDetail.model_validate(found)


@router.post(
    "",
    status_code=201,
    response_model=TitleDetail,
    summary="Add a movie or a series from TMDB",
    description=(
        "Adds the movie with its data from TMDB (account language, English fallback, English genre names) and "
        "one wanted version of your own per version definition. Nothing is searched or downloaded yet. At least "
        "one movie version definition is needed. An import of the same movie later takes these versions over. "
        "A series (`kind` series) comes with every season and episode and one version per entry of `versions`, each "
        "with its rule; specials are watched by none. nexcrate does not search or load series yet."
    ),
    responses=error_responses(
        (409, "title_exists"),
        (422, "version_kind_mismatch"),
        (404, "not_found"),
        *tmdb.ERRORS,
    ),
)
async def add_title(payload: TitleIn) -> TitleDetail:
    if payload.kind == "series":
        return await _add_series(payload)
    version_ids = _unique(payload.version_ids)
    if not version_ids:
        raise error("invalid_input", "The input is not valid.", 422, fields=["version_ids"])
    await asyncio.to_thread(_check_new_title, payload.tmdb_id, version_ids)
    token = await asyncio.to_thread(tmdb.require_token)
    locale = await asyncio.to_thread(tmdb.account_locale)
    try:
        data = await tmdb.fetch_movie(token, payload.tmdb_id, locale)
    except tmdb.TmdbError as exc:
        logger.info("Adding TMDB movie %d failed: %s", payload.tmdb_id, exc.code)
        raise exc.http() from exc
    title_id = await asyncio.to_thread(_create_title, data, version_ids)
    await asyncio.to_thread(_auto_tag, "movie", title_id)
    return await asyncio.to_thread(_detail, title_id)


# --- Series ------------------------------------------------------------------------------ #


def _series_choices(payload: TitleIn) -> list[SeriesVersionIn]:
    chosen: list[SeriesVersionIn] = []
    for choice in payload.versions:
        if any(existing.version_id == choice.version_id for existing in chosen):
            continue
        if not watching.valid_rule(choice.rule, choice.from_season):
            raise error("invalid_input", "The input is not valid.", 422, fields=["versions"])
        chosen.append(choice)
    if not chosen:
        raise error("invalid_input", "The input is not valid.", 422, fields=["versions"])
    if payload.series_type is not None and payload.series_type not in SERIES_TYPES:
        raise error("invalid_input", "The input is not valid.", 422, fields=["series_type"])
    return chosen


def _series_definitions(version_ids: list[int]) -> None:
    with SessionLocal() as db:
        _definitions(db, version_ids, "series", "versions")


def _check_new_series(tmdb_id: int, version_ids: list[int]) -> None:
    with SessionLocal() as db:
        _definitions(db, version_ids, "series", "versions")
        existing = _existing_title(db, tmdb_id, "series")
    if existing is not None:
        raise _title_exists(existing)


async def _series_data(tmdb_id: int) -> tmdb_series.SeriesData:
    token = await asyncio.to_thread(tmdb.require_token)
    locale = await asyncio.to_thread(tmdb.account_locale)
    try:
        return await tmdb_series.fetch_series(token, tmdb_id, locale)
    except tmdb.TmdbError as exc:
        logger.info("Reading TMDB series %d failed: %s", tmdb_id, exc.code)
        raise exc.http() from exc


def _create_series(data: tmdb_series.SeriesData, choices: list[SeriesVersionIn], series_type: str | None) -> int:
    with SessionLocal() as db:
        ids = [choice.version_id for choice in choices]
        definitions = {row.id: row for row in _definitions(db, ids, "series", "versions")}
        existing = _existing_title(db, data.tmdb_id, "series")
        if existing is not None:
            raise _title_exists(existing)
        moment, on = utcnow(), watching.today()
        try:
            title = series_store.new_title(data, moment, series_type)
            db.add(title)
            db.flush()
            reading: list[int] = []
            for choice in choices:
                version = library.add_owner_version(db, title, definitions[choice.version_id], moment)
                version.watch_rule = choice.rule
                version.watch_from_season = choice.from_season if choice.rule == "from_season" else None
                db.flush()
                # A series folder already on disk is read before the first search (31).
                if folder_read.claim(db, version, moment):
                    reading.append(version.id)
            db.flush()
            series_store.apply_series(db, title, data, moment, on)
            # A new series with aired episodes is due in the next minute (decision 23).
            _replan(db, title.id)
            db.commit()
            if reading:
                folder_read.enqueue(reading)
        except IntegrityError as exc:
            db.rollback()
            raise _title_exists(_existing_title(db, data.tmdb_id, "series") or 0) from exc
        logger.info(
            "Series %d added by the owner with %d versions and %d episodes",
            title.id,
            len(choices),
            data.episode_total(),
        )
        return title.id


def _auto_tag(kind: str, item_id: int) -> None:
    """The auto tags for something just added; never fails the adding."""
    with SessionLocal() as db:
        if auto_tags.apply_new(db, kind, [item_id]):
            db.commit()


async def _add_series(payload: TitleIn) -> TitleDetail:
    choices = _series_choices(payload)
    await asyncio.to_thread(_check_new_series, payload.tmdb_id, [choice.version_id for choice in choices])
    data = await _series_data(payload.tmdb_id)
    title_id = await asyncio.to_thread(_create_series, data, choices, payload.series_type)
    await asyncio.to_thread(_auto_tag, "series", title_id)
    return await asyncio.to_thread(_detail, title_id)


class OnDisk(BaseModel):
    folder: str = Field(description="The series folder that lies there already.")
    videos: int = Field(description="Videos reading it would look at.")


class PreviewVersion(BaseModel):
    version_id: int
    watched: int = Field(description="Episodes the rule would watch.")
    aired: int = Field(description="Of those, the episodes that aired before today.")
    on_disk: OnDisk | None = Field(
        default=None,
        description="The series folder this version would take is already on disk (decision "
        "31): adding the series reads it before the first search. Null when there is none.",
    )


class SeriesPreview(BaseModel):
    seasons: int = Field(description="Seasons TMDB lists, specials not counted.")
    episodes: int = Field(description="Episodes TMDB lists, specials not counted.")
    aired: int = Field(description="Of those, the episodes that aired before today.")
    proposed_type: str = Field(description="standard or daily, from TMDB's type of the series.")
    tmdb_type: str | None = Field(description="TMDB's type as it comes, for example Scripted or Talk Show.")
    versions: list[PreviewVersion]


def _folders_on_disk(version_ids: list[int], data: tmdb_series.SeriesData) -> dict[int, OnDisk]:
    """Per definition the series folder that already lies there (decision 31), with its videos."""
    found: dict[int, OnDisk] = {}
    facts = naming_series.SeriesFacts(
        title=data.title or data.original_title or "",
        year=data.year,
        tmdb_id=data.tmdb_id,
        tvdb_id=data.tvdb_id,
        imdb_id=data.imdb_id,
    )
    with SessionLocal() as db:
        for version_id in version_ids:
            definition = db.get(VersionDefinition, version_id)
            if definition is None or definition.kind != "series":
                continue
            place = folder_read.find_folder(db, definition, facts)
            if place is not None:
                found[version_id] = OnDisk(folder=place[1], videos=folder_read.videos_in(place[0] / place[1]))
    return found


@router.post(
    "/preview",
    response_model=SeriesPreview,
    summary="Count what adding a series would watch",
    description=(
        "The same body as adding a series. Reads the series from TMDB and counts per version the episodes its rule "
        "would watch and how many of them aired. Nothing is stored."
    ),
    responses=error_responses((422, "invalid_input"), (422, "version_kind_mismatch"), (404, "not_found"), *tmdb.ERRORS),
)
async def preview_series(payload: TitleIn) -> SeriesPreview:
    if payload.kind != "series":
        raise error("invalid_input", "The input is not valid.", 422, fields=["kind"])
    choices = _series_choices(payload)
    await asyncio.to_thread(_series_definitions, [choice.version_id for choice in choices])
    data = await _series_data(payload.tmdb_id)
    on = watching.today()
    on_disk = await asyncio.to_thread(_folders_on_disk, [choice.version_id for choice in choices], data)
    episodes = [episode for season in data.seasons for episode in season.episodes]
    regular = [episode for episode in episodes if episode.season_number > 0]
    versions = []
    for choice in choices:
        wanted = [
            episode
            for episode in episodes
            if watching.episode_wanted(
                choice.rule, choice.from_season, episode.season_number, episode.air_date, False, on
            )
        ]
        versions.append(
            PreviewVersion(
                version_id=choice.version_id,
                on_disk=on_disk.get(choice.version_id),
                watched=len(wanted),
                aired=sum(1 for episode in wanted if watching.aired(episode.air_date, on)),
            )
        )
    return SeriesPreview(
        seasons=len([season for season in data.seasons if season.number > 0]),
        episodes=len(regular),
        aired=sum(1 for episode in regular if watching.aired(episode.air_date, on)),
        proposed_type=data.proposed_type,
        tmdb_type=data.tmdb_type,
        versions=versions,
    )


class EpisodeSecondPartOut(BaseModel):
    id: int
    relative_path: str = Field(description="Relative to the series folder.")
    release_title: str | None = None
    size_bytes: int
    quality: str | None


class EpisodeFileOut(BaseModel):
    id: int
    relative_path: str = Field(description="Relative to the series folder.")
    release_title: str | None = Field(
        default=None,
        description=(
            "The name of the release the file came from, before nexcrate renamed it; null for a file read from disk "
            "without one. It says which episode the release itself claimed to be."
        ),
    )
    size_bytes: int
    quality: str | None
    release_group: str | None
    languages: list[str]
    episodes: int = Field(description="How many episodes this file covers in its version.")
    second_part: EpisodeSecondPartOut | None = Field(
        default=None,
        description=(
            "A double episode TMDB lists as one, held as two files: this file is part 1 "
            "and this is part 2. Null for an episode in one file."
        ),
    )


class EpisodeNumberOut(BaseModel):
    scheme: str = Field(
        description="tvdb, scene, owner, group, or absolute (the number an anime series counts through, only absolute "
        "set)."
    )
    season: int | None
    episode: int | None
    episode_end: int | None = Field(description="The last episode when one TMDB episode covers a range.")
    absolute: int | None
    verified: bool = Field(description="False for a scene number the source only extrapolated.")


class EpisodeVersionOut(BaseModel):
    version_id: int
    state: str = Field(description="problem, downloading, available, wanted or unmonitored.")
    watched: bool
    set_by: str = Field(description="rule, owner, source or late.")
    late: bool = Field(description="TMDB added the episode long after it aired; it stays off until switched on.")
    in_source: bool | None = Field(description="For a fed version: whether Sonarr has the episode. Null otherwise.")
    progress: float | None
    problem_code: str | None
    file: EpisodeFileOut | None


class EpisodeOut(BaseModel):
    id: int
    season_number: int
    number: int = Field(description="TMDB's aired episode number.")
    air_date: str | None = Field(description="YYYY-MM-DD.")
    aired: bool = Field(description="The air date lies before today in the server's time zone.")
    name: str = Field(description="In the account language; a placeholder such as `Episode 9` shows the English name.")
    name_en: str | None = Field(
        default=None, description="TMDB's English name when it differs from `name`, the title files usually carry."
    )
    known_to_source: bool | None = Field(
        default=None,
        description="Specials only: whether a Sonarr connection feeding a version has the special. Null otherwise.",
    )
    overview: str | None
    runtime_min: int | None
    episode_type: str | None
    tmdb_gone: bool = Field(description="TMDB no longer lists the episode; it stays because something hangs on it.")
    numbers: list[EpisodeNumberOut] = Field(description="Numberings other than TMDB's aired one.")
    versions: list[EpisodeVersionOut]


class SeasonEpisodes(BaseModel):
    season_id: int
    number: int
    episodes: list[EpisodeOut]


def _series_title(db: OrmSession, title_id: int) -> Title:
    title = db.get(Title, title_id)
    if title is None or title.kind != "series":
        raise error("not_found", "This does not exist, or not any more.", 404)
    return title


def _season_out(db: OrmSession, title: Title, season_id: int) -> SeasonEpisodes:
    found = series_detail.season_episodes(db, title, season_id, watching.today(), names.account_language(db))
    if found is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    return SeasonEpisodes.model_validate(found)


def _own_series_version(db: OrmSession, title_id: int, version_id: int) -> Version:
    _series_title(db, title_id)
    version = db.scalar(
        select(Version).where(Version.title_id == title_id, Version.version_definition_id == version_id)
    )
    if version is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    if version.source_id is not None:
        raise error(
            "version_fed_by_source",
            "A Sonarr connection feeds this version. What it watches is changed in Sonarr.",
            409,
        )
    return version


@router.get(
    "/{title_id}/seasons/{season_id}",
    response_model=SeasonEpisodes,
    summary="Read the episodes of a season",
    description="Every episode of one season of a series, with its other numberings and per version switch, state and "
    "file.",
    responses=error_responses((404, "not_found")),
)
def read_season(title_id: int, season_id: int, db: DbSession) -> SeasonEpisodes:
    return _season_out(db, _series_title(db, title_id), season_id)


class WatchIn(BaseModel):
    rule: str = Field(max_length=16, description="all, future, missing, from_season or none.")
    from_season: int | None = Field(default=None, ge=1, le=10_000, description="Needed for from_season.")


class WatchChange(BaseModel):
    added: int = Field(description="Episodes the rule switches on.")
    removed: int = Field(description="Episodes the rule switches off.")
    overridden: int = Field(description="Switches set by hand, or late marks, the rule overwrites.")
    watched: int = Field(description="Episodes watched afterwards.")
    aired: int = Field(description="Of those, the episodes that aired before today.")


_WATCH_ERRORS = ((404, "not_found"), (409, "version_fed_by_source"))
_RULE_ERRORS = (*_WATCH_ERRORS, (422, "invalid_input"))


class MonitoredAllIn(BaseModel):
    monitored: bool
    kind: Literal["movie", "series", "album"] = "movie"
    state: str | None = Field(default=None, description="The state filter of the view, as in `GET /api/library`.")
    q: str | None = Field(default=None, max_length=200, description="The search text of the view.")
    tag: str | None = Field(default=None, max_length=64, description="The tag filter of the view.")
    title_ids: list[int] | None = Field(
        default=None,
        max_length=5000,
        description="The titles the owner marked. Given, they count and the view is ignored.",
    )


class MonitoredAllOut(BaseModel):
    changed: int = Field(description="Versions that changed. Versions a source feeds are never touched.")


@router.put(
    "/monitored",
    response_model=MonitoredAllOut,
    summary="Watch every version of one view, or leave them all alone",
    description=(
        "Takes the same view as `GET /api/library`: kind, state and q. Every version of nexcrate's own in it is "
        "watched or left alone; a version a source feeds is watched there and stays as it is."
    ),
    responses=error_responses((422, "invalid_input")),
)
def change_monitored_all(payload: MonitoredAllIn, db: DbSession) -> MonitoredAllOut:
    if payload.state is not None and payload.state not in STATES and payload.state != library.UNCLEAR:
        raise error("invalid_input", "The input is not valid.", 422, fields=["state"])
    moment = utcnow()
    changed = library.set_monitored(
        db,
        kind=payload.kind,
        state=payload.state,
        query=payload.q,
        monitored=payload.monitored,
        moment=moment,
        title_ids=payload.title_ids,
        tag=payload.tag,
    )
    db.commit()
    logger.info("%d versions are now %s", changed, "watched" if payload.monitored else "left alone")
    return MonitoredAllOut(changed=changed)


class WatchRuleAllIn(BaseModel):
    rule: str = Field(max_length=16, description="all, future, missing, from_season or none.")
    from_season: int | None = Field(default=None, ge=1, le=10_000, description="Needed for from_season.")
    state: str | None = Field(default=None, description="The state filter of the view, as in `GET /api/library`.")
    q: str | None = Field(default=None, max_length=200, description="The search text of the view.")
    tag: str | None = Field(default=None, max_length=64, description="The tag filter of the view.")
    title_ids: list[int] | None = Field(
        default=None,
        max_length=5000,
        description="The series the owner marked. Given, they count and the view is ignored.",
    )


class WatchRuleAllOut(BaseModel):
    versions: int = Field(description="Series versions the rule was set for. Versions a Sonarr feeds are left out.")
    watched: int = Field(description="Episodes watched afterwards, over all of them.")


@router.put(
    "/watch-rule",
    response_model=WatchRuleAllOut,
    summary="Set what several series watch at once",
    description=(
        "The rule of `versions/{version_id}/watch` for every series version of nexcrate's own that the owner marked, "
        "or for the whole view. Switches set by hand are overwritten, as they are for one version."
    ),
    responses=error_responses((422, "invalid_input")),
)
def change_watch_rule_all(payload: WatchRuleAllIn, db: DbSession) -> WatchRuleAllOut:
    if not watching.valid_rule(payload.rule, payload.from_season):
        raise error("invalid_input", "The input is not valid.", 422, fields=["rule"])
    if payload.state is not None and payload.state not in STATES and payload.state != library.UNCLEAR:
        raise error("invalid_input", "The input is not valid.", 422, fields=["state"])
    moment = utcnow()
    on = watching.today()
    versions = library.series_versions_of(
        db, state=payload.state, query=payload.q, title_ids=payload.title_ids, tag=payload.tag
    )
    watched = 0
    for version in versions:
        change = watching.apply_rule(db, version, payload.rule, payload.from_season, on, write=True)
        version.updated_at = moment
        watched += change.watched
    _replan(db, sorted({version.title_id for version in versions}))
    db.commit()
    logger.info("Rule %s set for %d series versions, %d episodes watched", payload.rule, len(versions), watched)
    return WatchRuleAllOut(versions=len(versions), watched=watched)


class MonitoredIn(BaseModel):
    monitored: bool = Field(description="Off: nexcrate leaves this version alone, it searches and upgrades no more.")


@router.put(
    "/{title_id}/versions/{version_id}/monitored",
    response_model=TitleDetail,
    summary="Watch a version of a movie or stop watching it",
    description=(
        "`version_id` is the version definition. Off: nexcrate searches nothing for this version any more, and the "
        "automatic passes it by; the file it has stays where it is. A version a Radarr connection feeds is watched "
        "there and answers 409. For series the rule of `versions/{version_id}/watch` says what is watched."
    ),
    responses=error_responses((404, "not_found"), (409, "version_fed_by_source")),
)
def change_monitored(title_id: int, version_id: int, payload: MonitoredIn, db: DbSession) -> TitleDetail:
    title = db.get(Title, title_id)
    if title is None or title.kind != "movie":
        raise error("not_found", "This does not exist, or not any more.", 404)
    version = db.scalar(
        select(Version).where(Version.title_id == title_id, Version.version_definition_id == version_id)
    )
    if version is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    if version.source_id is not None:
        raise error(
            "version_fed_by_source",
            "A Radarr connection feeds this version. Whether it is watched is changed in Radarr.",
            409,
        )
    if version.monitored != payload.monitored:
        version.monitored = payload.monitored
        version.updated_at = utcnow()
        # Without a file the state follows at once; with one it keeps saying what the file is worth.
        download_store.follow_version(db, title_id, version_id, utcnow())
        _replan(db, title_id)
        db.commit()
        watched = "watched" if payload.monitored else "left alone"
        logger.info("Title %d: version %d is %s", title_id, version_id, watched)
    return TitleDetail.model_validate(library.detail(db, title_id))


@router.post(
    "/{title_id}/versions/{version_id}/watch/preview",
    response_model=WatchChange,
    summary="Count what a new rule would change",
    description="`version_id` is the series version definition. Nothing is stored.",
    responses=error_responses(*_RULE_ERRORS),
)
def preview_watch(title_id: int, version_id: int, payload: WatchIn, db: DbSession) -> WatchChange:
    if not watching.valid_rule(payload.rule, payload.from_season):
        raise error("invalid_input", "The input is not valid.", 422, fields=["rule"])
    version = _own_series_version(db, title_id, version_id)
    change = watching.apply_rule(db, version, payload.rule, payload.from_season, watching.today(), write=False)
    db.rollback()
    return WatchChange(**asdict(change))


@router.put(
    "/{title_id}/versions/{version_id}/watch",
    response_model=TitleDetail,
    summary="Change what a series version watches",
    description=(
        "Applies the rule to every season and episode of the version and keeps it for seasons and episodes that "
        "appear later. Switches set by hand are overwritten; `watch/preview` counts them first. A version a Sonarr "
        "connection feeds answers 409."
    ),
    responses=error_responses(*_RULE_ERRORS),
)
def change_watch(title_id: int, version_id: int, payload: WatchIn, db: DbSession) -> TitleDetail:
    if not watching.valid_rule(payload.rule, payload.from_season):
        raise error("invalid_input", "The input is not valid.", 422, fields=["rule"])
    version = _own_series_version(db, title_id, version_id)
    watching.apply_rule(db, version, payload.rule, payload.from_season, watching.today(), write=True)
    version.updated_at = utcnow()
    _replan(db, title_id)
    db.commit()
    logger.info("Title %d: version %d watches by a new rule", title_id, version_id)
    return TitleDetail.model_validate(library.detail(db, title_id))


def _replan(db: OrmSession, title_id: int | list[int]) -> None:
    """What a title wants changed: its automatic search is planned again at once (decision
    23). A new series version or rule with aired episodes is due in the next minute."""
    db.flush()
    automatic_planning.replan(db, [title_id] if isinstance(title_id, int) else title_id, automatic_clock.now())


class SwitchIn(BaseModel):
    version_id: int = Field(description="The series version definition.")
    watched: bool


@router.put(
    "/{title_id}/seasons/{season_id}/watch",
    response_model=SeasonEpisodes,
    summary="Switch a whole season on or off",
    description="Sets the season's switch and every episode's switch of the season in one version, by hand.",
    responses=error_responses(*_WATCH_ERRORS),
)
def switch_season(title_id: int, season_id: int, payload: SwitchIn, db: DbSession) -> SeasonEpisodes:
    version = _own_series_version(db, title_id, payload.version_id)
    season = db.get(Season, season_id)
    if season is None or season.title_id != title_id:
        raise error("not_found", "This does not exist, or not any more.", 404)
    if not watching.set_season(db, version, season_id, payload.watched, watching.today()):
        raise error("not_found", "This does not exist, or not any more.", 404)
    _replan(db, title_id)
    db.commit()
    return _season_out(db, _series_title(db, title_id), season_id)


@router.put(
    "/{title_id}/episodes/{episode_id}/watch",
    response_model=SeasonEpisodes,
    summary="Switch one episode on or off",
    description="Sets one episode's switch in one version, by hand. Answers the episodes of its season.",
    responses=error_responses(*_WATCH_ERRORS),
)
def switch_episode(title_id: int, episode_id: int, payload: SwitchIn, db: DbSession) -> SeasonEpisodes:
    version = _own_series_version(db, title_id, payload.version_id)
    episode = db.get(Episode, episode_id)
    if episode is None or episode.title_id != title_id:
        raise error("not_found", "This does not exist, or not any more.", 404)
    season_id = episode.season_id
    if not watching.set_episode(db, version, episode_id, payload.watched, watching.today()):
        raise error("not_found", "This does not exist, or not any more.", 404)
    _replan(db, title_id)
    db.commit()
    return _season_out(db, _series_title(db, title_id), season_id)


@router.post(
    "/{title_id}/late/watch",
    response_model=TitleDetail,
    summary="Watch the episodes TMDB added late",
    description="Switches on every episode TMDB added long after it aired, in every version of nexcrate's own.",
    responses=error_responses((404, "not_found")),
)
def watch_late(title_id: int, db: DbSession) -> TitleDetail:
    _series_title(db, title_id)
    on = watching.today()
    switched = 0
    for version in db.scalars(select(Version).where(Version.title_id == title_id, Version.source_id.is_(None))):
        switched += watching.watch_late(db, version, on)
    _replan(db, title_id)
    db.commit()
    logger.info("Title %d: %d late episode switches turned on", title_id, switched)
    return TitleDetail.model_validate(library.detail(db, title_id))


# --- Unclear files (Ü3) ---------------------------------------------------------------- #

_FILE_ERRORS = ((404, "not_found"), (409, "version_fed_by_source"))


class FileEpisodesIn(BaseModel):
    episode_ids: list[int] = Field(min_length=1, max_length=50, description="TMDB episodes of the title.")
    replace: bool = Field(
        default=False,
        description="An episode that has another file gives it up; that file becomes unclear. Nothing on disk changes.",
    )


class LeftOutIn(BaseModel):
    left_out: bool


def _after_files(db: OrmSession, version: Version, title_id: int, seasons: set[int]) -> TitleDetail:
    _replan(db, title_id)
    db.commit()
    if seasons:
        companions_series.write_version(version.id, seasons)
    return TitleDetail.model_validate(library.detail(db, title_id))


@router.post(
    "/{title_id}/versions/{version_id}/files/{file_id}/episodes",
    response_model=TitleDetail,
    summary="Assign a file of a series version to episodes",
    description=(
        "`version_id` is the series version definition. Links the file to the episodes, each of the title and "
        "without a file in this version; episodes the file held before lose it. The file is judged by the version's "
        "rules, its season folder is kept and release.nex is written. 409 `episode_has_file` when an episode has a "
        "file and `replace` is false; 422 `invalid_input` for an episode that is not the title's."
    ),
    responses=error_responses(*_FILE_ERRORS, (409, "episode_has_file"), (422, "invalid_input")),
)
def assign_file(title_id: int, version_id: int, file_id: int, payload: FileEpisodesIn, db: DbSession) -> TitleDetail:
    version = _own_series_version(db, title_id, version_id)
    try:
        seasons = unclear.assign(db, version, file_id, payload.episode_ids, replace=payload.replace)
    except LookupError as exc:
        raise error("not_found", "This does not exist, or not any more.", 404) from exc
    except unclear.NotAssignable as exc:
        if exc.code == "episode_has_file":
            raise error("episode_has_file", "An episode already has a file in this version.", 409) from exc
        raise error("invalid_input", "The input is not valid.", 422, fields=["episode_ids"]) from exc
    logger.info("Title %d: a file assigned to %d episodes by the owner", title_id, len(payload.episode_ids))
    return _after_files(db, version, title_id, seasons)


@router.delete(
    "/{title_id}/versions/{version_id}/files/{file_id}/episodes",
    response_model=TitleDetail,
    summary="Take a file of a series version off its episodes",
    description=(
        "The file stays on disk and becomes unclear: it waits under the unclear files for the owner to assign it "
        "again. Its episodes are without a file in this version. release.nex is written again."
    ),
    responses=error_responses(*_FILE_ERRORS),
)
def release_file(title_id: int, version_id: int, file_id: int, db: DbSession) -> TitleDetail:
    version = _own_series_version(db, title_id, version_id)
    try:
        seasons = unclear.release(db, version, file_id)
    except LookupError as exc:
        raise error("not_found", "This does not exist, or not any more.", 404) from exc
    logger.info("Title %d: a file taken off its episodes by the owner", title_id)
    return _after_files(db, version, title_id, seasons)


@router.get(
    "/{title_id}/versions/{version_id}/episode-choices",
    response_model=list[EpisodeChoice],
    summary="Every episode of a series version with its file",
    description=(
        'For "Andere Folge" when the episodes around a file are not enough: every episode of the version by season '
        "and number, with the file it has. Assigning to an episode with a file needs `replace`."
    ),
    responses=error_responses(*_FILE_ERRORS),
)
def episode_choices(title_id: int, version_id: int, db: DbSession) -> list[EpisodeChoice]:
    version = _own_series_version(db, title_id, version_id)
    return [EpisodeChoice.model_validate(item) for item in unclear.all_episodes(db, version)]


@router.put(
    "/{title_id}/versions/{version_id}/files/{file_id}/left-out",
    response_model=TitleDetail,
    summary="Mark a file as belonging to no episode",
    description=(
        "`left_out` true: the file stays on disk and no longer counts as unclear, so old missing episodes are searched "
        "again. False makes it unclear again. 409 `file_has_episodes` for a file that holds episodes."
    ),
    responses=error_responses(*_FILE_ERRORS, (409, "file_has_episodes")),
)
def leave_out_file(title_id: int, version_id: int, file_id: int, payload: LeftOutIn, db: DbSession) -> TitleDetail:
    version = _own_series_version(db, title_id, version_id)
    try:
        unclear.leave_out(db, version, file_id, payload.left_out)
    except LookupError as exc:
        raise error("not_found", "This does not exist, or not any more.", 404) from exc
    except unclear.NotAssignable as exc:
        raise error("file_has_episodes", "This file holds episodes; it cannot be left out.", 409) from exc
    return _after_files(db, version, title_id, set())


@router.post(
    "/{title_id}/versions/{version_id}/files/proposals",
    response_model=TitleDetail,
    summary="Take every safe proposal of a series version",
    description=(
        "Assigns every unclear file whose proposal has the same title, with or without the same air date (steps 1 "
        "and 2), one after another. Without `from_names` only proposals that rest on a source episode's own title "
        "(`safe`); with it also a title found inside a file name, which the interface confirms with the owner first."
    ),
    responses=error_responses(*_FILE_ERRORS),
)
def take_file_proposals(
    title_id: int,
    version_id: int,
    db: DbSession,
    from_names: Annotated[bool, Query(description="Take titles found inside file names too.")] = False,
) -> TitleDetail:
    version = _own_series_version(db, title_id, version_id)
    taken, seasons = unclear.take_proposals(db, version, from_names=from_names)
    logger.info("Title %d: %d proposals taken by the owner", title_id, taken)
    return _after_files(db, version, title_id, seasons)


@router.post(
    "/{title_id}/versions/{version_id}/files/read",
    status_code=202,
    response_model=TitleDetail,
    summary="Read the series folder again",
    description=(
        "Reads the version's series folder in the background for videos nexcrate does not know yet. Known files are "
        "never read twice. Until the read ends the version wants nothing; `series.reading` "
        "follows it."
    ),
    responses=error_responses(*_FILE_ERRORS),
)
def read_series_folder(title_id: int, version_id: int, db: DbSession) -> TitleDetail:
    version = _own_series_version(db, title_id, version_id)
    version.files_read_at = None
    db.commit()
    folder_read.enqueue([version.id])
    return TitleDetail.model_validate(library.detail(db, title_id))


class TitlePatch(BaseModel):
    series_type: str | None = Field(default=None, max_length=16, description="Series only: standard, daily or anime.")


def _set_series_type(db: OrmSession, title: Title, series_type: str, moment: datetime) -> None:
    """One series' type, with what hangs on it. The caller has checked the type and that nothing feeds the series."""
    title.series_type = series_type
    title.updated_at = moment
    # Anime counts through, another type does not (A1).
    absolute.store(db, title, moment)


@router.patch(
    "/{title_id}",
    response_model=TitleDetail,
    summary="Change a title",
    description=(
        "For a series its type: standard, daily (episodes by date) or anime. A movie answers 422. A series a Sonarr "
        "connection feeds answers 409: its type is decided there and would be taken over again at the next run."
    ),
    responses=error_responses((404, "not_found"), (409, "series_fed_by_source"), (422, "invalid_input")),
)
def change_title(title_id: int, payload: TitlePatch, db: DbSession) -> TitleDetail:
    title = db.get(Title, title_id)
    if title is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    if payload.series_type is not None:
        if title.kind != "series" or payload.series_type not in SERIES_TYPES:
            raise error("invalid_input", "The input is not valid.", 422, fields=["series_type"])
        if library.fed_series_ids(db, [title_id]):
            raise error(
                "series_fed_by_source",
                "A Sonarr connection feeds this series; its type is decided there.",
                409,
            )
        _set_series_type(db, title, payload.series_type, utcnow())
        _replan(db, title_id)
    db.commit()
    return TitleDetail.model_validate(library.detail(db, title_id))


class SeriesTypeAllIn(BaseModel):
    series_type: str = Field(max_length=16, description="standard, daily (episodes by date) or anime.")
    state: str | None = Field(default=None, description="The state filter of the view, as in `GET /api/library`.")
    q: str | None = Field(default=None, max_length=200, description="The search text of the view.")
    tag: str | None = Field(default=None, max_length=64, description="The tag filter of the view.")
    title_ids: list[int] | None = Field(
        default=None,
        max_length=5000,
        description="The series the owner marked. Given, they count and the view is ignored.",
    )


class SeriesTypeAllOut(BaseModel):
    changed: int = Field(description="Series whose type is now the chosen one.")
    fed: int = Field(description="Series left out because a Sonarr connection feeds them and decides their type.")


@router.put(
    "/series-type",
    response_model=SeriesTypeAllOut,
    summary="Set the type of several series at once",
    description=(
        "The type of `PATCH /api/library/{title_id}` for every series the owner marked, or for the whole view. "
        "Series a Sonarr connection feeds are left out and counted, they would take Sonarr's type again at the next "
        "run. A series that already has this type is not counted as changed."
    ),
    responses=error_responses((422, "invalid_input")),
)
def change_series_type_all(payload: SeriesTypeAllIn, db: DbSession) -> SeriesTypeAllOut:
    if payload.series_type not in SERIES_TYPES:
        raise error("invalid_input", "The input is not valid.", 422, fields=["series_type"])
    if payload.state is not None and payload.state not in STATES and payload.state != library.UNCLEAR:
        raise error("invalid_input", "The input is not valid.", 422, fields=["state"])
    titles = library.series_titles_of(
        db, state=payload.state, query=payload.q, title_ids=payload.title_ids, tag=payload.tag
    )
    fed = library.fed_series_ids(db, [title.id for title in titles])
    moment = utcnow()
    changed: list[int] = []
    for title in titles:
        # ⚠️ "standard" is stored as null for a series added without a type; both mean the same here.
        if title.id in fed or (title.series_type or "standard") == payload.series_type:
            continue
        _set_series_type(db, title, payload.series_type, moment)
        changed.append(title.id)
    _replan(db, changed)
    db.commit()
    logger.info(
        "Series type %s set for %d series, %d left out to their Sonarr", payload.series_type, len(changed), len(fed)
    )
    return SeriesTypeAllOut(changed=len(changed), fed=len(fed))


class SeriesTypeProposal(BaseModel):
    title_id: int
    title: str
    year: int | None
    series_type: str = Field(description="The type the series has: standard, daily or anime.")
    proposed: str = Field(description="What TMDB suggests: anime by keyword or Animation in Japanese, daily by type.")


class SeriesTypeRunOut(BaseModel):
    state: str = Field(description="idle, running or done.")
    done: int
    total: int
    failed: int = Field(description="Series TMDB no longer knows; they keep no suggestion.")
    problem: str | None = Field(description="Why a run stopped early: tmdb_token_missing or a TMDB error code.")


class SeriesTypeProposalsOut(BaseModel):
    proposals: list[SeriesTypeProposal] = Field(
        description="Series whose type differs from TMDB's suggestion; series a Sonarr connection feeds are left out."
    )
    unknown: int = Field(description="Series without a suggestion yet; the run asks TMDB for them.")
    run: SeriesTypeRunOut


class SeriesTypeRunIn(BaseModel):
    everything: bool = Field(
        default=False, description="Ask TMDB for every series again, not only for those without a suggestion."
    )


#: The running proposal run, kept so the event loop does not drop it.
_proposal_runs: set[asyncio.Task[None]] = set()


@router.get(
    "/series-type/proposals",
    response_model=SeriesTypeProposalsOut,
    summary="The series whose type differs from TMDB's suggestion",
    description=(
        "the design notes, B5: what TMDB suggests for every series (anime, daily, standard), kept by every TMDB "
        "refresh, against the type the series has. Nothing is changed; take a proposal with "
        "`PUT /api/library/series-type`."
    ),
)
def series_type_proposals(db: DbSession) -> SeriesTypeProposalsOut:
    return SeriesTypeProposalsOut(
        proposals=[SeriesTypeProposal(**item) for item in type_proposals.proposals(db)],
        unknown=type_proposals.unknown_count(db),
        run=SeriesTypeRunOut(**type_proposals.state()),
    )


@router.post(
    "/series-type/proposals/run",
    status_code=202,
    response_model=SeriesTypeRunOut,
    summary="Ask TMDB for the suggested type of the series",
    description=(
        "One TMDB request per series, for those without a suggestion or, with `everything`, for all; the same "
        "request a refresh asks first, so it costs nothing twice. A run that is running already answers 409."
    ),
    responses=error_responses((409, "series_type_run_running")),
)
async def run_series_type_proposals(payload: SeriesTypeRunIn) -> SeriesTypeRunOut:
    wanted = await asyncio.to_thread(type_proposals.claim, payload.everything)
    if wanted is None:
        raise error("series_type_run_running", "TMDB is being asked already. Please wait for it.", 409)
    task = asyncio.create_task(type_proposals.work(wanted))
    _proposal_runs.add(task)
    task.add_done_callback(_proposal_runs.discard)
    return SeriesTypeRunOut(**type_proposals.state())


# --- Running downloads ("Changes after the owner's live test", B) ---------------------- #

#: What removing a running download can answer: a download being filed away, or the client's error.
_DOWNLOAD_ERRORS = ((409, "download_finished"), *downloaders.ERRORS)


async def _remove_downloads(running: list[tuple[int, str]]) -> None:
    """Removes each download from its client, as ``DELETE /api/downloads/{id}?remove_from_client=true`` does.

    ⚠️ A download being filed away cannot be removed from its client. That is refused before any client is asked, so
    nothing is left half done. A client that fails answers with its error, and the caller then changes nothing.
    """
    if any(state == "importing" for _download_id, state in running):
        raise download_actions.download_finished().http()
    for download_id, _state in running:
        try:
            await download_actions.remove(download_id, remove_from_client=True, blocklist=False)
        except download_actions.ActionError as exc:
            if exc.detail["code"] == "download_not_found":
                continue
            raise exc.http() from exc


def _title_downloads(db: OrmSession, title_id: int) -> list[Download]:
    """The running downloads removing the title leaves behind: every one but those of versions a source feeds."""
    fed = set(
        db.scalars(
            select(Version.version_definition_id).where(Version.title_id == title_id, Version.source_id.is_not(None))
        )
    )
    pending = download_store.pending_downloads(db, title_id)
    return [download for download in pending if download.version_definition_id not in fed]


def _running_of_title(title_id: int) -> list[tuple[int, str]]:
    with SessionLocal() as db:
        if db.get(Title, title_id) is None:
            raise error("not_found", "This does not exist, or not any more.", 404)
        return [(download.id, download.state) for download in _title_downloads(db, title_id)]


@dataclass
class _Change:
    title: Title
    removing: list[Version]
    definitions: list[VersionDefinition]
    #: Running downloads of the versions that go.
    running: list[Download]


def _change(db: OrmSession, title_id: int, payload: VersionsPatch) -> _Change:
    """Checks a change of versions without making it."""
    title = db.get(Title, title_id)
    if title is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    add, remove = _unique(payload.add), _unique(payload.remove)
    if set(add) & set(remove):
        raise error("invalid_input", "The input is not valid.", 422, fields=["add", "remove"])
    current = {
        version.version_definition_id: version
        for version in db.scalars(select(Version).where(Version.title_id == title_id))
    }
    definitions = [
        definition for definition in _definitions(db, add, title.kind, "add") if definition.id not in current
    ]
    removing = [current[identifier] for identifier in remove if identifier in current]
    if any(version.source_id is not None for version in removing):
        raise error(
            "version_owned_by_source",
            "This version comes from a Radarr connection. It goes when it is removed in Radarr or the connection "
            "is deleted.",
            409,
        )
    if len(current) - len(removing) + len(definitions) == 0:
        raise error("invalid_input", "The input is not valid.", 422, fields=["remove"])
    going = {version.version_definition_id for version in removing}
    pending = download_store.pending_downloads(db, title_id)
    running = [download for download in pending if download.version_definition_id in going]
    return _Change(title=title, removing=removing, definitions=definitions, running=running)


def _running_of_change(title_id: int, payload: VersionsPatch) -> list[tuple[int, str]]:
    with SessionLocal() as db:
        return [(download.id, download.state) for download in _change(db, title_id, payload).running]


def _apply_change(title_id: int, payload: VersionsPatch) -> TitleDetail:
    with SessionLocal() as db:
        change = _change(db, title_id, payload)
        if change.running:
            order = [version.version_definition_id for version in change.removing]
            first = min(change.running, key=lambda download: (order.index(download.version_definition_id), download.id))
            named = db.get(VersionDefinition, first.version_definition_id)
            raise error(
                "version_download_active",
                "Downloads of this version are still running. Remove them together with the version, or wait until "
                "they are done.",
                409,
                label=named.label if named is not None else first.version_label,
                count=len(change.running),
            )
        moment = utcnow()
        # Before the versions go: their release.nex entries, removed after the commit; the movies stay on disk.
        removals = companions.plan_removal(db, change.removing) if companions.enabled(db) else []
        for version in change.removing:
            db.delete(version)
        db.flush()
        on = watching.today()
        reading: list[int] = []
        for definition in change.definitions:
            added = library.add_owner_version(db, change.title, definition, moment)
            if change.title.kind == "album":
                # The target rule runs for the new version, and the artist's releases load ahead of the background.
                language = music_store.account_language(db)
                music_store.refresh_target(db, change.title, added, moment=moment, language=language)
                album_artist = db.get(Artist, change.title.artist_id) if change.title.artist_id is not None else None
                if album_artist is not None:
                    music_loading.queue(db, album_artist, music_loading.PRIORITY_OWNER)
            if change.title.kind == "series":
                # A series version added later watches every episode until the owner changes its rule.
                added.watch_rule = "all"
                watching.sync_rows(db, added, on)
                watching.recount(db, added, on)
                if folder_read.claim(db, added, moment):
                    reading.append(added.id)
        if change.removing or change.definitions:
            change.title.updated_at = moment
            _replan(db, title_id)
        db.commit()
        if reading:
            folder_read.enqueue(reading)
        if removals:
            companions.remove(removals)
        if change.removing or change.definitions:
            logger.info(
                "Title %d: %d versions added, %d removed by the owner",
                title_id,
                len(change.definitions),
                len(change.removing),
            )
        found = library.detail(db, title_id)
    return TitleDetail.model_validate(found)


@router.patch(
    "/{title_id}/versions",
    response_model=TitleDetail,
    summary="Add or remove versions of a title",
    description=(
        "`add` gives the title wanted versions of your own. `remove` removes versions no source feeds; a version "
        "fed by a source answers 409 and nothing changes. The same id in both lists, or removing the last "
        "version, is invalid input: remove the title instead. A removed version with a download that still runs "
        "(queued to importing, or a problem) answers 409 `version_download_active` with `label` and `count`, unless "
        "`remove_downloads` is true: then each such download is removed from its client first, as "
        "`DELETE /api/downloads/{id}` does, and a client that fails answers with its error while nothing changes. "
        "Imported files are never deleted."
    ),
    responses=error_responses(
        (404, "not_found"),
        (409, "version_owned_by_source"),
        (409, "version_download_active"),
        (422, "version_kind_mismatch"),
        *_DOWNLOAD_ERRORS,
    ),
)
async def change_versions(title_id: int, payload: VersionsPatch) -> TitleDetail:
    if payload.remove_downloads:
        await _remove_downloads(await asyncio.to_thread(_running_of_change, title_id, payload))
    return await asyncio.to_thread(_apply_change, title_id, payload)


class DeleteFilesIn(BaseModel):
    version_id: int = Field(description="The version definition.")
    season: int | None = Field(default=None, ge=0, le=10_000, description="Series: one season's files, TMDB's number.")
    episode_file_id: int | None = Field(default=None, ge=1, description="Series: one episode file.")
    track_file_id: int | None = Field(default=None, ge=1, description="Album: one track's file.")


class DeletedFiles(BaseModel):
    files: int = Field(description="Files that went into the recycle bin.")
    missing: int = Field(description="Files nexcrate knew that were no longer on disk; their record is gone.")


@router.post(
    "/{title_id}/delete-files",
    response_model=DeletedFiles,
    summary="Move a version's files into the recycle bin",
    description=(
        "A movie version's file with its subtitles, a series version's files (all, one season's, or one episode "
        "file), or an album's files (all, or one track's). They stay in the recycle bin for the recycle time and come "
        "back from there. Watching stays as it is: a watched version is searched again. A version a source feeds "
        "answers 409."
    ),
    responses=error_responses(
        (404, "not_found"),
        (404, "version_not_found"),
        (409, "version_fed_by_source"),
        (409, "download_importing"),
        (422, "scope_not_for_kind"),
    ),
)
def delete_version_files(title_id: int, payload: DeleteFilesIn) -> DeletedFiles:
    scope = recycle_bin.Scope(
        definition_ids=(payload.version_id,),
        seasons=(payload.season,) if payload.season is not None else None,
        episode_file_ids=(payload.episode_file_id,) if payload.episode_file_id is not None else (),
        track_file_ids=(payload.track_file_id,) if payload.track_file_id is not None else (),
    )
    result = recycle_bin.delete(title_id, scope)
    return DeletedFiles(files=result.files, missing=sum(item.missing for item in result.versions))


class RemoveManyIn(BaseModel):
    kind: Literal["movie", "series", "album"] = "movie"
    title_ids: list[int] | None = Field(
        default=None,
        max_length=5000,
        description="The titles the owner marked. Given, they count and the view is ignored.",
    )
    state: str | None = Field(default=None, description="The state filter of the view, as in `GET /api/library`.")
    q: str | None = Field(default=None, max_length=200, description="The search text of the view.")
    tag: str | None = Field(default=None, max_length=64, description="The tag filter of the view.")
    delete_files: bool = Field(
        default=False,
        description="Also move the files of your own versions into the recycle bin first; they stay there for the "
        "recycle time. Without it the files stay where they are.",
    )


class RemoveManyOut(BaseModel):
    removed: int = Field(description="Titles removed.")
    kept_fed: int = Field(
        description="Titles that stay because a source feeds versions of them; your own versions went."
    )
    files: int = Field(description="Files moved into the recycle bin.")
    failed: int = Field(description="Titles left as they were: a download being filed away, or a client that failed.")


def _titles_to_remove(payload: RemoveManyIn) -> list[int]:
    with SessionLocal() as db:
        if payload.title_ids is not None:
            query = select(Title.id).where(Title.id.in_(payload.title_ids), Title.kind == payload.kind)
        else:
            query = select(Title.id).where(
                *library.title_conditions(payload.kind, payload.state, payload.q, payload.tag)
            )
        return sorted(db.scalars(query))


def _recycle_title(title_id: int) -> int:
    """Every file of the title's own versions into the recycle bin; returns how many went."""
    with SessionLocal() as db:
        definitions = tuple(
            sorted(
                {
                    version.version_definition_id
                    for version in db.scalars(
                        select(Version).where(Version.title_id == title_id, Version.source_id.is_(None))
                    )
                }
            )
        )
    if not definitions:
        return 0
    return recycle_bin.delete(title_id, recycle_bin.Scope(definition_ids=definitions)).files


@router.post(
    "/remove",
    response_model=RemoveManyOut,
    summary="Remove several titles at once",
    description=(
        "Removes the marked titles, or every title of the view, one after another as `DELETE /api/library/{id}` with "
        "`remove_downloads` does: their running downloads leave their clients, your own versions go, a title goes "
        "when no version is left. With `delete_files` the files of your own versions go into the recycle bin first. "
        "A title a source still feeds keeps those versions and is counted in `kept_fed`; a title with a download "
        "being filed away, or whose client fails, stays as it was and is counted in `failed`."
    ),
    responses=error_responses((422, "invalid_input")),
)
async def remove_many(payload: RemoveManyIn) -> RemoveManyOut:
    if payload.state is not None and payload.state not in STATES and payload.state != library.UNCLEAR:
        raise error("invalid_input", "The input is not valid.", 422, fields=["state"])
    removed = kept_fed = files = failed = 0
    for title_id in await asyncio.to_thread(_titles_to_remove, payload):
        try:
            await _remove_downloads(await asyncio.to_thread(_running_of_title, title_id))
            if payload.delete_files:
                files += await asyncio.to_thread(_recycle_title, title_id)
            await asyncio.to_thread(_remove_title, title_id)
            removed += 1
        except HTTPException as exc:
            code = exc.detail.get("code") if isinstance(exc.detail, dict) else None
            if code == "title_has_source_versions":
                kept_fed += 1
            elif code != "not_found":
                failed += 1
                logger.info("Title %d stays: %s", title_id, code)
    logger.info(
        "Several titles removed: %d removed, %d kept by a source, %d files into the recycle bin, %d failed",
        removed,
        kept_fed,
        files,
        failed,
    )
    return RemoveManyOut(removed=removed, kept_fed=kept_fed, files=files, failed=failed)


@router.delete(
    "/{title_id}",
    status_code=204,
    response_model=None,
    summary="Remove a title",
    description=(
        "Removes your own versions, the ones no source feeds, and the title when no version is left. When "
        "versions fed by a source remain, your own versions are removed all the same and the answer is 409 with "
        "`removed` (how many went) and `sources` (the names of the connections); the title stays with them. "
        "A download of the title that still runs (queued to importing, or a problem) answers 409 "
        "`title_download_active` with `count`, unless `remove_downloads` is true: then each such download is removed "
        "from its client first, as `DELETE /api/downloads/{id}` does, and a client that fails answers with its error "
        "while nothing is deleted. Imported files are never deleted."
    ),
    responses=error_responses(
        (404, "not_found"),
        (409, "title_has_source_versions"),
        (409, "title_download_active"),
        *_DOWNLOAD_ERRORS,
    ),
)
async def remove_title(
    title_id: int,
    remove_downloads: Annotated[
        bool, Query(description="Remove the title's running downloads from their clients first.")
    ] = False,
) -> None:
    if remove_downloads:
        await _remove_downloads(await asyncio.to_thread(_running_of_title, title_id))
    await asyncio.to_thread(_remove_title, title_id)


def _remove_title(title_id: int) -> None:
    with SessionLocal() as db:
        title = db.get(Title, title_id)
        if title is None:
            raise error("not_found", "This does not exist, or not any more.", 404)
        running = _title_downloads(db, title_id)
        if running:
            raise error(
                "title_download_active",
                "Downloads of this title are still running. Remove them together with the title, or wait until they "
                "are done.",
                409,
                count=len(running),
            )
        rows = (
            db.execute(
                select(Version, Source.name)
                .outerjoin(Source, Source.id == Version.source_id)
                .where(Version.title_id == title_id)
            )
            .tuples()
            .all()
        )
        own = [version for version, _name in rows if version.source_id is None]
        fed_sources = sorted({name for version, name in rows if version.source_id is not None and name})
        fed = [version for version, _name in rows if version.source_id is not None]
        # Before the versions go: their release.nex entries, removed after the commit; the movies stay on disk.
        removals = companions.plan_removal(db, own) if companions.enabled(db) else []
        for version in own:
            db.delete(version)
        # ⚠️ Before the title goes: its cascade would remove the versions first, and the flush then finds nothing.
        db.flush()
        if fed:
            if own:
                title.updated_at = utcnow()
            db.commit()
            if removals:
                companions.remove(removals)
            logger.info(
                "Title %d: %d own versions removed, %d versions of sources remain", title_id, len(own), len(fed)
            )
            raise error(
                "title_has_source_versions",
                "Your own versions are removed. The versions from Radarr stay until the movie is removed there or the "
                "connection is deleted.",
                409,
                removed=len(own),
                sources=fed_sources,
            )
        db.delete(title)
        db.commit()
    if removals:
        companions.remove(removals)
    images.forget([title_id])
    logger.info("Title %d removed with %d versions", title_id, len(own))
