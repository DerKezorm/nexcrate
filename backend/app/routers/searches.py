"""Searching on request: start a search for a movie or a series, follow it, read what nexcrate would take.

The search runs in the background (``services/search``); the page asks ``GET /api/searches/{search_id}`` until
``state`` is ``done``. Answers never carry a key, a download link or an info link, and no release title reaches a log
line outside the trace mode.

Since step 3 a read also says what can be loaded: per version the reason it cannot load at all, per release whether it
is on the title's blocklist, and per release and version whether it can load. These parts are read from the database
at every read, so they follow a download that started or a folder that was chosen after the search.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..deps import DbSession
from ..meldungen import error, error_responses
from ..models import Episode, Indexer, Title
from ..services.automatic import waiting
from ..services.downloads import loading
from ..services.search import jobs
from ..services.search import series as series_search
from ..services.series import anime
from .releases import ParsedOut, ParsedSeriesOut, ResultOut, SeriesResultOut

_LOAD_BLOCK_TEXT = (
    "The first reason this release cannot load for this version: version_fed_by_source, version_no_profile, "
    "version_no_folder, no_client_for_protocol, download_active, or for a series episodes_downloading (a running "
    "download of the version holds an episode of the release). Null with can_load true, and null with can_load "
    "false when the title no longer has this version."
)

logger = logging.getLogger("nexcrate.search")

router = APIRouter(tags=["searches"])

SEARCH_ID_MAX_LENGTH = 64


class SearchStarted(BaseModel):
    search_id: str = Field(description="Follow it with GET /api/searches/{search_id}.")


class SearchIn(BaseModel):
    scope: Literal["series", "season", "episode"] | None = Field(
        default=None, description="Series only: the whole series (default), one season or one episode."
    )
    season: int | None = Field(default=None, strict=True, ge=0, le=9999, description="With scope season.")
    episode_id: int | None = Field(default=None, strict=True, description="With scope episode.")


class SearchQuery(BaseModel):
    kind: str = Field(description="id for the search by TMDB and IMDb number, title for a search by title.")
    text: str = Field(
        description="The title query as sent, or ids. Never a key.", examples=["die schoene strasse 2021"]
    )
    releases: int = Field(description="Releases this query gave, over all its pages, before merging.")


class SearchIndexer(BaseModel):
    indexer_id: int
    name: str
    state: str = Field(
        description="waiting, searching, done, failed, timeout, or skipped when a series search found no series "
        "categories at this indexer."
    )
    error_code: str | None = Field(
        description="For failed and timeout: an indexer error code of the indexer routes, indexer_key_missing, "
        "indexer_timeout when the search's time ran out, internal_error when the log has more. For skipped: "
        "indexer_no_series_categories, or for an album indexer_no_music_categories."
    )
    queries: list[SearchQuery]
    releases: int = Field(description="Distinct releases of this indexer.")
    took_ms: int


class NothingFits(BaseModel):
    code: str = Field(
        description="A rejection code of the release checker, not_enough_seeders, or blocklisted for releases on the "
        "title's blocklist."
    )
    count: int = Field(description="How many releases had this code.")


class SearchVersion(BaseModel):
    version_id: int
    label: str
    has_profile: bool
    would_take: str | None = Field(
        description="The release_key of the release nexcrate would take, or null. Never a release on the title's "
        "blocklist."
    )
    keeps_current: bool = Field(
        description="Nothing is better than the version's file. False when every better release is on the title's "
        "blocklist."
    )
    nothing_fits: list[NothingFits] = Field(
        description="Only when nothing fits: the rejection codes, most common first. Nothing fits for a version "
        "without a file, and for one with a file when every better release is on the title's blocklist. Series: "
        "also when something fits, the codes of the releases of the episodes in no_fit."
    )
    load_block: str | None = Field(
        default=None,
        description="Why no release can load for this version, whatever the release: version_fed_by_source, "
        "version_no_profile, version_no_folder or download_active. Null otherwise.",
    )
    fed_by: str | None = Field(default=None, description="Series: the name of the source that feeds the version.")
    takes: list[SeriesTake] = Field(
        default_factory=list,
        description="Series: what nexcrate would take, in order; each release fills or replaces episodes no earlier "
        "one covers. would_take stays null for a series.",
    )
    takes_can_load: bool = Field(
        default=False,
        description="Series: whether every release of takes can load for this version, for POST /api/downloads/takes.",
    )
    not_found: list[str] = Field(
        default_factory=list,
        description="Series: watched, aired episodes of the search without a file that no release of the answer names.",
    )
    no_fit: list[str] = Field(
        default_factory=list,
        description="Series: watched, aired episodes of the search without a file that releases name, though none of "
        "them fits or may be taken; their reasons are in nothing_fits.",
    )
    packs_left_out: list[PackLeftOut] = Field(
        default_factory=list,
        description="Series: season packs ranked above releases of takes that would bring fewer than half of their "
        "episodes, left out because releases of fewer episodes, no worse without the pack's bonus for being one, "
        "bring all of them.",
    )


class PackLeftOut(BaseModel):
    release_key: str
    brings: int = Field(description="How many episodes of the search the pack would fill or replace.")
    episodes: int = Field(description="How many episodes the pack holds.")


class SeriesTake(BaseModel):
    release_key: str
    fills: list[str] = Field(description="Codes of episodes without a file it would fill.", examples=[["S02E08"]])
    replaces: list[str] = Field(description="Codes of episodes whose file it would replace.")
    covered_elsewhere: list[str] = Field(
        description="Codes of episodes it holds that an earlier release of the set covers; nothing would be filed "
        "from it for them."
    )


class NotThisSeries(BaseModel):
    parsed_title: str | None


class SearchMatch(BaseModel):
    via: str | None = Field(
        description="owner, scene, tvdb, group, tmdb, air_date or absolute (an anime series' number counted through); "
        "null when nothing matched."
    )
    ambiguous: bool
    other: dict[str, Any] | None
    missing: list[int]
    notes: list[str] = Field(
        description="unverified_scene, two_dates, not_daily, not_anime (counted through, but the series is no anime), "
        "group_counting (the release's group counts by the "
        "numbering of via, as its other releases in this answer show), scene_season (a scene name of one season said "
        "where an anime number counted through lies)."
    )
    episodes: list[str] = Field(description="The codes of the matched episodes as the series page shows them.")


class SearchScope(BaseModel):
    kind: Literal["series", "season", "episode"]
    season: int | None = None
    episode_id: int | None = None
    code: str | None = Field(default=None, description="S02, or the code of the episode.")


class NotThisMovie(BaseModel):
    parsed_title: str | None
    parsed_year: int | None


class SearchReleaseVersion(BaseModel):
    version_id: int
    result: ResultOut | None = Field(
        default=None,
        description="The release checker's result, with not_enough_seeders {seeders, minimum} among the rejections "
        "for a torrent below its indexer's minimum. Null without a profile, and for a series.",
    )
    series_result: SeriesResultOut | None = Field(
        default=None, description="Series: the series checker's result, with not_enough_seeders as for movies."
    )
    rank: int | None = Field(
        description="The place in this version's order of fitting releases, null when it does not fit or is on the "
        "title's blocklist."
    )
    can_load: bool = Field(
        default=False,
        description="Whether POST /api/downloads can load it for this version; a release that does not fit or is "
        "blocked needs a confirmation there.",
    )
    load_block: str | None = Field(default=None, description=_LOAD_BLOCK_TEXT)
    would_wait_until: datetime | None = Field(
        default=None,
        description="Until when the automatic would let this release wait for a better one, by the delay rule of the "
        "version; null when it would load at once. A load the owner starts never waits.",
    )


class SearchRelease(BaseModel):
    release_key: str = Field(description="Stable within the search; names neither guid nor link.")
    title: str
    indexer_id: int
    indexer: str
    protocol: str = Field(description="usenet or torrent.")
    size_bytes: int | None
    age_hours: float | None
    seeders: int | None
    peers: int | None
    grabs: int | None
    flags: list[str] = Field(
        description="freeleech, halfleech, double_upload, internal, scene, freeleech_75, freeleech_25, nuked."
    )
    belongs: bool = Field(description="Whether the release belongs to this movie, by number or by title and year.")
    blocklisted: bool = Field(
        default=False,
        description="On the title's blocklist: by its info hash, or its release title with the same protocol.",
    )
    not_this_movie: NotThisMovie | None = Field(
        default=None, description="What the name says, for a release of another movie."
    )
    parsed: ParsedOut | None = Field(default=None, description="The name as a movie release; null for a series.")
    not_this_series: NotThisSeries | None = Field(default=None, description="Series: a release of another series.")
    parsed_series: ParsedSeriesOut | None = Field(default=None, description="Series: the name as a series release.")
    match: SearchMatch | None = Field(default=None, description="Series: the episodes the release means.")
    in_scope: bool = Field(
        default=False, description="Series: whether it means an episode the search is for; only those can be taken."
    )
    versions: list[SearchReleaseVersion] = Field(description="Empty for a release of another movie.")


class Search(BaseModel):
    search_id: str
    title_id: int
    kind: str = Field(default="movie", description="movie or series.")
    scope: SearchScope | None = Field(default=None, description="Series: what the search is for.")
    targets: list[str] = Field(default_factory=list, description="Series: the seasons and episodes asked, in order.")
    state: str = Field(description="running or done.")
    started_at: datetime
    finished_at: datetime | None
    indexers: list[SearchIndexer]
    versions: list[SearchVersion] = Field(description="Empty while the search runs.")
    releases: list[SearchRelease] = Field(
        description="Empty while the search runs. Releases of the movie first, each group in the order found."
    )


@router.post(
    "/api/library/{title_id}/search",
    status_code=202,
    response_model=SearchStarted,
    summary="Search the indexers for a movie or a series",
    description=(
        "Starts a search in the background and answers at once. Every enabled indexer is asked in parallel: by "
        "number where its caps allow, else or when that finds nothing by title in several spellings. The releases "
        "of the title are evaluated with the profile of each version, and the answer says per version what nexcrate "
        "would take. Nothing is downloaded. A running search of the same title answers 409 `search_running` with "
        "its `search_id`. A series searches its watched seasons, or `scope` season or episode; a series of the type "
        "anime answers 409 `anime_later`."
    ),
    responses=error_responses(
        (404, "not_found"),
        (409, "search_running"),
        (409, "no_indexers"),
        (409, "search_busy"),
        (409, "anime_later"),
        (422, "invalid_input"),
    ),
)
def start_search(title_id: int, db: DbSession, payload: SearchIn | None = None) -> SearchStarted:
    title = db.get(Title, title_id)
    if title is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    body = payload or SearchIn()
    scope: dict[str, Any] | None = None
    if title.kind == "movie":
        if body.scope is not None or body.season is not None or body.episode_id is not None:
            raise error("invalid_input", "The input is not valid.", 422, fields=["scope"])
    elif title.kind == "series":
        if anime.not_searched(title.kind, title.series_type):
            raise error("anime_later", "nexcrate does not search anime yet.", 409)
        scope = _series_scope(db, title, body)
    else:
        raise error("invalid_input", "The input is not valid.", 422, fields=["title_id"])
    has_indexers = db.scalar(select(Indexer.id).where(Indexer.enabled.is_(True)).limit(1)) is not None
    try:
        search_id = jobs.start(title_id, has_indexers=has_indexers, scope=scope)
    except jobs.SearchRunning as exc:
        raise error(
            "search_running", "A search is already running for this title.", 409, search_id=exc.search_id
        ) from exc
    except jobs.NoIndexers as exc:
        raise error("no_indexers", "No indexer is enabled.", 409) from exc
    except jobs.SearchBusy as exc:
        raise error("search_busy", "Too many searches are running at the same time.", 409) from exc
    logger.info("Search %s started for title %d", search_id, title_id)
    return SearchStarted(search_id=search_id)


def _series_scope(db: Any, title: Title, body: SearchIn) -> dict[str, Any]:
    kind = body.scope or series_search.SERIES
    if kind == series_search.SEASON:
        if body.season is None or body.episode_id is not None:
            raise error("invalid_input", "The input is not valid.", 422, fields=["season"])
        known = db.scalar(
            select(Episode.id).where(Episode.title_id == title.id, Episode.season_number == body.season).limit(1)
        )
        if known is None:
            raise error("invalid_input", "The input is not valid.", 422, fields=["season"])
        return {"kind": kind, "season": body.season}
    if kind == series_search.EPISODE:
        episode = db.get(Episode, body.episode_id) if body.episode_id is not None else None
        if episode is None or episode.title_id != title.id or body.season is not None:
            raise error("invalid_input", "The input is not valid.", 422, fields=["episode_id"])
        return {"kind": kind, "episode_id": episode.id}
    if body.season is not None or body.episode_id is not None:
        raise error("invalid_input", "The input is not valid.", 422, fields=["scope"])
    return {"kind": kind}


@router.get(
    "/api/searches/{search_id}",
    response_model=Search,
    summary="Read a search",
    description=(
        "The search while it runs (per indexer its state and the queries so far) and when it is done (per version "
        "what nexcrate would take, and every release with its evaluation per version). A search is kept for 30 "
        "minutes after it finished, at most 20 are kept."
    ),
    responses=error_responses((404, "not_found")),
)
def read_search(search_id: str) -> Search:
    found = jobs.snapshot(search_id) if len(search_id) <= SEARCH_ID_MAX_LENGTH else None
    if found is None or found.get("kind") == "album":
        # An album search reads under /api/music/searches/{search_id}.
        raise error("not_found", "This does not exist, or not any more.", 404)
    if found.get("kind") == "series":
        loading.decorate_series(found, jobs.info_hashes(search_id))
    else:
        loading.decorate(found, jobs.info_hashes(search_id))
    waiting.annotate(search_id, found)
    return Search.model_validate(found)
