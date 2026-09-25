"""Searching an album on request (M3.2 to M3.6).

Start a search, follow it, read which release is which album and what nexcrate would take. Nothing is loaded before
M4: the answer carries no link, and ``POST /api/downloads`` refuses a release of an album search. Answers never carry
a key, and no release title reaches a log line outside the trace mode.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from ..deps import DbSession
from ..meldungen import error, error_responses
from ..models import Indexer, Title
from ..services.automatic import waiting
from ..services.downloads import loading
from ..services.search import jobs
from .searches import SEARCH_ID_MAX_LENGTH, SearchIndexer, SearchStarted

logger = logging.getLogger("nexcrate.search")

router = APIRouter(tags=["music"])


class AlbumSearchIn(BaseModel):
    aliases: bool = Field(
        default=False,
        description="Ask with up to two further names of the artist from MusicBrainz, for when nothing fitted.",
    )


class Code(BaseModel):
    model_config = ConfigDict(extra="allow")

    code: str


class AlbumMatchOut(BaseModel):
    kind: str = Field(
        description=(
            "this: the album searched. other_album: another album of the artist (title_id, album, year). "
            "unknown_album: the artist, but no album of it has this name. other_artist. not_an_album with reason: "
            "several_albums, tribute, karaoke, sampler (a sampler for an album of one artist), not_a_sampler."
        )
    )
    title_id: int | None
    album: str | None
    year: int | None
    reason: str | None
    read_artist: str = Field(description="The artist as the name was read, a proposal.")
    read_album: str
    kind_differs: str | None = Field(
        description="The name says single, ep, live, soundtrack, compilation, demo or mixtape, the album is none."
    )


class AlbumVerdictOut(BaseModel):
    judged: bool = Field(description="False without a music profile: only what nexcrate never takes is refused.")
    accepted: bool
    for_now: bool
    rejections: list[Code] = Field(
        description=(
            "The codes of the music checker, not_enough_seeders {seeders, minimum}, and too_small_for_album "
            "{minutes, minimum_bytes}: smaller than the whole target release at 96 kbit/s."
        )
    )
    notes: list[Code] = Field(
        description=(
            "The notes of the music checker, and format_from_category (the name gives no format, the indexer's "
            "category 3040 or 3010 did), category_differs, small_for_step {step, kbit}, large_for_album {minutes}, "
            "other_edition {editions, media}: the name says an edition or a number of discs the target release "
            "does not have."
        )
    )
    rank: list[int] = Field(description="What orders the accepted releases, compared as a list.")


class AlbumReleaseOut(BaseModel):
    release_key: str
    indexer_id: int
    indexer_name: str
    protocol: str = Field(description="usenet or torrent.")
    title: str
    size_bytes: int | None
    published_at: datetime | None
    age_hours: float | None
    seeders: int | None
    peers: int | None
    grabs: int | None
    categories: list[int]
    priority: int
    match: AlbumMatchOut
    step: str | None = Field(description="lossless_24, lossless, lossy_high, lossy_mid, lossy_low, or null.")
    format: str | None
    bit_depth: int | None
    source: str | None
    verdict: AlbumVerdictOut | None = Field(description="Only for releases of the album searched.")
    place: int | None = Field(description="The place among the accepted releases, 1 for the best.")
    can_load: bool | None = Field(
        default=None, description="Whether the music version can load it; only for releases of this album (M4)."
    )
    load_block: str | None = Field(default=None, description="The first reason it cannot load.")
    would_wait_until: datetime | None = Field(
        default=None,
        description="Until when the automatic would let this release wait for a better one, by the delay rule of the "
        "music version; null when it would load at once. A load the owner starts never waits.",
    )
    blocklisted: bool | None = Field(default=None, description="The release is on the album's blocklist.")


class AlbumDecisionOut(BaseModel):
    version_id: int | None = Field(description="The music version; null while there is none.")
    label: str
    has_profile: bool
    would_take: str | None = Field(description="The release key nexcrate would take.")
    keeps_current: bool = Field(description="The album's files are as good as anything found.")
    nothing_fits: list[Code] = Field(description="Rejection codes with count, most common first.")
    load_block: str | None = Field(
        default=None, description="Why the music version cannot load at all; null when it can."
    )


class AlbumScopeOut(BaseModel):
    kind: str
    aliases: bool


class AlbumSearchOut(BaseModel):
    search_id: str
    title_id: int
    state: str = Field(description="running or done.")
    scope: AlbumScopeOut
    started_at: datetime
    finished_at: datetime | None
    indexers: list[SearchIndexer] = Field(
        description="As for movies; skipped with indexer_no_music_categories when an indexer has no music categories."
    )
    versions: list[AlbumDecisionOut] = Field(description="Empty while the search runs.")
    releases: list[AlbumReleaseOut] = Field(
        description=(
            "Empty while the search runs. The album's releases first, the accepted ones in the order of the decision, "
            "then other albums of the artist, then the rest."
        )
    )


@router.post(
    "/api/music/albums/{title_id}/search",
    status_code=202,
    response_model=SearchStarted,
    summary="Search the indexers for an album",
    description=(
        "Starts a search in the background and answers at once; follow it with GET /api/music/searches/{search_id}. "
        "Every enabled indexer with music categories is asked: by artist and album where its caps offer music "
        "search, else or when that finds nothing as text in several spellings, at most four queries. Every release "
        "is matched to an album of the artist; the album's releases are judged with the music profile. Loading is "
        "POST /api/downloads with the search's id."
    ),
    responses=error_responses(
        (404, "not_found"),
        (409, "search_running"),
        (409, "no_indexers"),
        (409, "search_busy"),
        (409, "album_no_artist"),
    ),
)
def start_album_search(title_id: int, db: DbSession, payload: AlbumSearchIn | None = None) -> SearchStarted:
    title = db.get(Title, title_id)
    if title is None or title.kind != "album":
        raise error("not_found", "This does not exist, or not any more.", 404)
    if title.artist_id is None:
        raise error("album_no_artist", "This album has no artist in the library, so it cannot be searched.", 409)
    body = payload or AlbumSearchIn()
    has_indexers = db.scalar(select(Indexer.id).where(Indexer.enabled.is_(True)).limit(1)) is not None
    try:
        search_id = jobs.start(
            title_id, has_indexers=has_indexers, album_scope={"kind": "album", "aliases": body.aliases}
        )
    except jobs.SearchRunning as exc:
        raise error(
            "search_running", "A search is already running for this title.", 409, search_id=exc.search_id
        ) from exc
    except jobs.NoIndexers as exc:
        raise error("no_indexers", "No indexer is enabled.", 409) from exc
    except jobs.SearchBusy as exc:
        raise error("search_busy", "Too many searches are running at the same time.", 409) from exc
    logger.info("Search %s started for album %d", search_id, title_id)
    return SearchStarted(search_id=search_id)


@router.get(
    "/api/music/searches/{search_id}",
    response_model=AlbumSearchOut,
    summary="Read an album search",
    description="The album search while it runs and when it is done. Kept for 30 minutes after it finished.",
    responses=error_responses((404, "not_found")),
)
def read_album_search(search_id: str) -> AlbumSearchOut:
    found = jobs.snapshot(search_id) if len(search_id) <= SEARCH_ID_MAX_LENGTH else None
    if found is None or found.get("kind") != "album":
        raise error("not_found", "This does not exist, or not any more.", 404)
    if found.get("state") == "done":
        loading.decorate_album(found, jobs.info_hashes(search_id))
        waiting.annotate(search_id, found)
    return AlbumSearchOut.model_validate(found)
