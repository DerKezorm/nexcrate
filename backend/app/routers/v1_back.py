"""``/api/v1``, stage V3: the way back. What loads and what is stuck, why a title is not there
yet, its history, and an event feed a program reads in seconds, also as a stream. With the scope ``operate`` a program
resolves problems and assigns the files of a stuck download by hand.

Reading needs ``read``; everything that changes something needs ``operate``. The six rules of ``v1.py`` hold: titles
by ``kind`` and ``ref``, versions by their fixed ``version_id``, what only a kind has under its name (``series``),
episodes in TMDB's numbers, missing values null. Errors are flat.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..db import SessionLocal
from ..deps import DbSession, require_scope
from ..meldungen import error, v1_error_responses
from ..models import ApiKey
from ..services import api_v1
from ..services.api_v1 import downloads as v1_downloads
from ..services.api_v1 import events as v1_events
from ..services.api_v1 import history as v1_history
from ..services.api_v1 import why as v1_why
from .v1 import _KindBlocks, _title_id

router = APIRouter(prefix="/api/v1", tags=["v1"], dependencies=[Depends(require_scope("read"))])

OperateKey = Annotated[ApiKey, Depends(require_scope("operate"))]
ReadKey = Annotated[ApiKey, Depends(require_scope("read"))]

_TITLE_ERRORS = (
    (422, "kind_unsupported"),
    (422, "ref_invalid"),
    (422, "ref_source_unknown"),
    (404, "title_not_found"),
    (409, "ref_ambiguous"),
)
_ACT_ERRORS = ((404, "download_not_found"), (409, "action_not_allowed"))


def _kind(kind: str | None) -> None:
    if kind is not None and kind not in api_v1.KINDS:
        raise error("kind_unsupported", f"This nexcrate does not answer for the kind {kind}.", 422, kind=kind)


def _title_kind(kind: str) -> None:
    """Why and history belong to a title: an artist has neither; its albums have."""
    _kind(kind)
    if kind == "artist":
        raise error("kind_unsupported", "An artist has no versions of its own; ask its albums.", 422, kind=kind)


# --- Shapes ------------------------------------------------------------------------------------------------------- #


class EpisodeRefOut(BaseModel):
    season: int = Field(description="TMDB's season number; 0 holds the specials.")
    episode: int = Field(description="TMDB's episode number.")


class QueueSeriesBlock(BaseModel):
    season: int | None = Field(description="The season when every episode lies in one; null for several.")
    episodes: list[EpisodeRefOut] = Field(description="The episodes the download is for, in TMDB's numbers.")


class QueueTitleOut(BaseModel):
    kind: str
    ref: str | None
    name: str | None
    origin: str | None = Field(description="The `origin` of the program that added the title.")


class ProblemOut(BaseModel):
    code: str = Field(
        description="download_failed, path_not_found, packed, no_video, no_space, gone_from_client, client_error, "
        "import_failed, dangerous_file, encrypted, client_unreachable, stalled, files_unassigned, "
        "other_series_suspected, several_videos, import_stalled, too_many_files, multi_part. Translate by code; show "
        "one you do not know as a problem."
    )
    needs_owner: bool = Field(description="True: it waits for somebody. False: a hint, nexcrate goes on by itself.")
    message: str = Field(description="English fallback text.")
    params: dict[str, Any] = Field(
        description="The values of the problem, such as `needed_bytes` and `free_bytes` of no_space, or `proposal` of "
        "path_not_found. Never a key."
    )
    actions: list[str] = Field(
        description="What may be done, the likeliest first: retry, remove, remove_and_search, clear, search, "
        "confirm_mapping, finish, assign. Anything else answers 409 `action_not_allowed`."
    )
    automatic: list[str] = Field(
        description="Of those, what an automatic may do without a human. Empty: only a human decides."
    )


class QueueItemOut(_KindBlocks):
    download_id: int = Field(description="Fixed for good; names the download in every address.")
    title: QueueTitleOut
    version_id: str | None = Field(description="The version it loads for; null when the version was deleted since.")
    origin: str | None = Field(description="The `origin` of the program that added this version.")
    started_by: str = Field(description="Who started it: manual, search, rss or replacement.")
    state: str = Field(
        description="queued, downloading, paused, completed, importing, problem, or failed (waits for the owner); "
        "imported and removed only after acting."
    )
    progress: float | None = Field(description="0 to 100. Not an event: read it here.")
    size_bytes: int | None
    remaining_bytes: int | None = Field(description="From size and progress; null when one is missing.")
    remaining_seconds: int | None = Field(description="As the download client estimates it.")
    quality: str | None = Field(description="Read from the release name.")
    release: str | None = Field(description="The release name. Never a link.")
    protocol: str = Field(description="usenet or torrent.")
    grabbed_at: datetime
    problem: ProblemOut | None
    series: QueueSeriesBlock | None = None


class QueueOut(BaseModel):
    items: list[QueueItemOut] = Field(description="Newest first.")


class ActOut(BaseModel):
    download: QueueItemOut = Field(description="The download as it stands after the action.")


class ReadingOut(BaseModel):
    form: str | None = Field(description="standard, multi_episode, daily, mini_series or download.")
    from_: str | None = Field(alias="from", description="file, folder or download: where the numbers were read.")
    season: int | None = Field(description="As the name says, which may differ from TMDB's.")
    numbers: list[int] = Field(description="As the name says, which may differ from TMDB's.")
    air_date: str | None

    model_config = {"populate_by_name": True}


class FileSeriesBlock(BaseModel):
    episodes: list[EpisodeRefOut] = Field(description="What nexcrate decided for the file, in TMDB's numbers.")


class FileAlbumBlock(BaseModel):
    track: str | None = Field(description="`mbid:<track>` the file stands for; null while open or without one.")
    placed: bool = Field(description="Filed into the album folder already.")
    other_album: bool = Field(description="The file belongs to another album of the artist.")


class DownloadFileOut(_KindBlocks):
    key: int = Field(description="Names the file in assign; never a path.")
    name: str = Field(
        description="Relative to the download, never a path of the disk; out of its archives with `unpacked:`."
    )
    size_bytes: int
    duration_seconds: int | None
    reading: ReadingOut | None = Field(description="What nexcrate read from the name; null when nothing.")
    decision: str = Field(
        description="filed, open, sample, extra, duplicate, not_needed, not_filed; candidate or chosen for a movie."
    )
    series: FileSeriesBlock | None = None
    album: FileAlbumBlock | None = None


class CurrentFileOut(BaseModel):
    quality: str | None
    size_bytes: int


class SeriesEpisodeOut(BaseModel):
    season: int
    episode: int
    name: str | None
    in_download: bool
    state: str | None = Field(description="expected, filed, skipped_not_better, missing or not_filed; null outside.")
    monitored: bool
    current_file: CurrentFileOut | None = Field(description="The file the version holds for it; null when none.")


class FilesSeriesBlock(BaseModel):
    episodes: list[SeriesEpisodeOut] = Field(description="Every episode of the series TMDB lists.")


class AlbumReleaseOut(BaseModel):
    ref: str | None = Field(description="`mbid:<release>`.")
    name: str | None
    date: str | None
    country: str | None
    formats: list[str]
    tracks: int


class AlbumTrackOut(BaseModel):
    ref: str | None = Field(description="`mbid:<track>`, what assign names a file's track by.")
    medium: int
    position: int
    number: str | None
    name: str | None
    length_ms: int | None
    held: bool = Field(description="The version holds a file for it already.")


class FilesAlbumBlock(BaseModel):
    release: str | None = Field(description="The release whose tracks are listed; assign may name another.")
    target_release: str | None
    release_fixed: bool = Field(description="Files lie in the album folder already: the release cannot change.")
    releases: list[AlbumReleaseOut]
    tracks: list[AlbumTrackOut]


class DownloadFilesOut(_KindBlocks):
    download: QueueItemOut
    files: list[DownloadFileOut]
    series: FilesSeriesBlock | None = None
    album: FilesAlbumBlock | None = None


class EpisodeRefIn(BaseModel):
    season: int = Field(ge=0, le=10_000)
    episode: int = Field(ge=0, le=100_000)


class AssignFileSeriesIn(BaseModel):
    episodes: list[EpisodeRefIn] = Field(max_length=50, description="TMDB's numbers; empty: the file is not filed.")


class AssignFileAlbumIn(BaseModel):
    track: str | None = Field(
        default=None, max_length=64, description="`mbid:<track>` of the release; null: the file is not filed."
    )
    without_track: bool = Field(default=False, description="File it without a track, a hidden track for one.")


class AssignFileIn(BaseModel):
    key: int
    series: AssignFileSeriesIn | None = Field(
        default=None, description="A series file's episodes; leave it out for the video of a movie."
    )
    album: AssignFileAlbumIn | None = Field(default=None, description="An album file's track.")


class AssignAlbumIn(BaseModel):
    release: str | None = Field(
        default=None, max_length=64, description="`mbid:<release>` the tracks belong to; without it the one shown."
    )


class AssignIn(BaseModel):
    files: list[AssignFileIn] = Field(min_length=1, max_length=2000)
    album: AssignAlbumIn | None = Field(default=None, description="Only for an album download.")
    confirm: list[Literal["not_better"]] = Field(
        default_factory=list,
        max_length=1,
        description="Replace a file that is not worse all the same, as the owner may.",
    )



class RemoveIn(BaseModel):
    search_again: bool = Field(
        default=False,
        description="Put the release on the blocklist and ask for a new search (the action remove_and_search).",
    )


# --- Why ------------------------------------------------------------------------------------------------------- #


class BecauseOut(BaseModel):
    code: str = Field(
        description="available, upgrade_possible, downloading, problem, unmonitored, artist_frozen, version_not_ready, "
        "held, waiting_delay, not_released, no_date, not_found, no_fitting_release, search_limit, wish_searching or "
        "searching_soon. wish_searching (since 22.09.2026): a program's search wish searches at once; params since, "
        "running. anime_not_supported: an anime series while `capabilities.anime` is false; it comes after "
        "problem, downloading and available and before everything else, no params. "
        "A later stage may add one: show it as not there yet."
    )
    params: dict[str, Any] = Field(
        description="downloading: download_id, state, progress, remaining_seconds. problem: code, download_id. "
        "version_not_ready: reasons. held: reason, count. waiting_delay: until, release. not_released: date, kind "
        "(digital, physical, theatrical, year). no_fitting_release: releases, codes, best, at. search_limit and "
        "searching_soon: next_at."
    )


class LastSearchOut(BaseModel):
    version_id: str | None
    at: datetime | None
    releases: int | None = Field(description="Releases of the title the search found, fitting or not.")
    best: str | None = Field(description="The name of the best release, fitting or not.")
    codes: list[str] = Field(description="Why the profile refused, as the release checker's codes.")
    loaded: bool
    load_code: str | None = Field(description="Why nothing loaded although something fitted.")


class WhyVersionOut(BaseModel):
    version_id: str | None
    state: str
    monitored: bool
    because: BecauseOut
    last_search: LastSearchOut | None


class ReleaseOut(BaseModel):
    date: str | None = Field(description="The day the search waits for; null without one.")
    kind: str | None = Field(description="digital, physical, theatrical, year or none.")
    country: str | None


class SeasonSearchOut(BaseModel):
    version_id: str | None
    loaded: int
    not_found: int
    no_fit: int
    codes: list[str]
    pack_only: dict[str, Any] | None = Field(description="Only a pack of the season would do: its size and episodes.")
    load_code: str | None


class SeasonLastSearchOut(BaseModel):
    at: datetime | None
    versions: list[SeasonSearchOut]


class WhySeasonOut(BaseModel):
    season: int
    next_search_at: datetime | None
    next_search_reason: str | None
    missing: int
    upgrades: int
    waiting: int
    no_date: int
    last_search_at: datetime | None
    last_search: SeasonLastSearchOut | None


class WhySeriesBlock(BaseModel):
    seasons: list[WhySeasonOut] = Field(description="Every season that wants something.")


class WhyAlbumBlock(BaseModel):
    first_release_date: str | None
    release_date: str | None = Field(description="The day the album's search waits for: its target release's.")
    artist_frozen: bool = Field(description="The album's artist is frozen; nothing of it is searched on its own.")


class WhyTitleOut(BaseModel):
    kind: str
    ref: str | None
    name: str | None


class WhyOut(_KindBlocks):
    title: WhyTitleOut
    automatic: bool = Field(description="Whether the automatic of this kind is switched on.")
    search_wish: bool = Field(description="A program asked for a search that has not run yet.")
    last_search_at: datetime | None
    next_search_at: datetime | None
    next_search_reason: str | None = Field(
        description="anchor, schedule, limit, replacement, replacement_limit, no_date, nothing_wanted, off, or wish "
        "(a program's search wish searches at once, since 22.09.2026)."
    )
    release: ReleaseOut | None = Field(description="A movie: the day its search waits for. Null for a series.")
    versions: list[WhyVersionOut]
    series: WhySeriesBlock | None = None
    album: WhyAlbumBlock | None = None


class WhyItemIn(BaseModel):
    kind: str = Field(max_length=16)
    ref: str = Field(max_length=64)


class WhyManyIn(BaseModel):
    items: list[WhyItemIn] = Field(max_length=v1_why.BATCH_MAX)


class WhyManyItemOut(BaseModel):
    kind: str
    ref: str
    known: bool
    why: WhyOut | None
    error: str | None = Field(description="kind_unsupported, ref_invalid, ref_source_unknown or ref_ambiguous.")


class WhyManyOut(BaseModel):
    items: list[WhyManyItemOut] = Field(description="In the order asked.")


# --- History ---------------------------------------------------------------------------------------------------- #


class ReplacedOut(BaseModel):
    quality: str | None = Field(description="Null when the replaced files differed or it is unknown.")
    size_bytes: int | None


class HistoryFileOut(BaseModel):
    episodes: list[EpisodeRefOut]
    quality: str | None
    size_bytes: int | None
    replaced: list[ReplacedOut]


class HistorySeriesBlock(BaseModel):
    episodes: list[EpisodeRefOut] | None = Field(description="The episodes the entry is about; null when unknown.")
    files: list[HistoryFileOut] | None = Field(
        default=None, description="An import: per file its episodes, quality, size and the files it replaced."
    )


class HistoryItemOut(_KindBlocks):
    entry_id: int
    type: str = Field(
        description="grabbed, imported, failed, deleted, restored, requested, withdrawn, renamed, added, taken_over, "
        "operated or other."
    )
    event: str = Field(description="nexcrate's own name of the entry.")
    at: datetime
    version_id: str | None
    by: str | None = Field(description="owner, or the name of the key a program acted with; null for nexcrate.")
    origin: str | None
    download_id: int | None
    quality: str | None
    size_bytes: int | None
    replaced: ReplacedOut | None = Field(description="An import that replaced a file: the old one.")
    count: int | None = Field(description="Files deleted or restored.")
    code: str | None = Field(description="A failure's code.")
    detail: str | None = Field(
        description="A failure: what the download client said, as a code (repair_failed, incomplete, not_on_server, "
        "password, unpack_failed, encrypted, unwanted_extension, duplicate, aborted, other). Since 22.09.2026.",
    )
    action: str | None = Field(description="What a program did (operated).")
    series: HistorySeriesBlock | None = None
    album: dict[str, Any] | None = Field(
        default=None, description="What only an album has: `tracks` the entry was about, as `mbid:<track>`."
    )


class HistoryOut(BaseModel):
    items: list[HistoryItemOut] = Field(description="Newest first. Entries from before V3 carry only kind and time.")
    next_before: int | None = Field(description="Ask with `before` set to this for older entries; null at the end.")


# --- Events ----------------------------------------------------------------------------------------------------- #


class EventTitleOut(BaseModel):
    kind: str
    ref: str | None
    name: str | None


class EventOut(_KindBlocks):
    seq: int = Field(description="Ask after it; given once and never again.")
    type: str = Field(description="See GET /api/v1/system for the list; skip a type you do not know.")
    at: datetime
    title: EventTitleOut | None = Field(description="Null for events of no title: versions, health, takeovers.")
    version_id: str | None
    origin: str | None
    download_id: int | None
    params: dict[str, Any]
    series: dict[str, Any] | None = Field(
        default=None, description="What only a series has: `season`, `episodes` in TMDB's numbers."
    )
    album: dict[str, Any] | None = Field(
        default=None, description="What only an album has: `tracks` as `mbid:<track>`."
    )


class EventsOut(BaseModel):
    items: list[EventOut] = Field(description="Oldest first.")
    next_after: int = Field(description="Ask with this next.")
    more: bool = Field(description="True: ask again at once.")
    latest: int = Field(description="The highest number given so far.")


# --- The queue and problems ------------------------------------------------------------------------------------ #


@router.get(
    "/queue",
    response_model=QueueOut,
    summary="What is loading, stuck or failed",
    description=(
        "Every download that is not finished, one entry per download with its episodes and one state: running, "
        "stuck, and failed ones that wait for the owner. Progress and remaining time are only here, never events."
    ),
    responses=v1_error_responses((422, "kind_unsupported")),
)
def read_queue(db: DbSession, kind: Annotated[str | None, Query(max_length=16)] = None) -> QueueOut:
    _kind(kind)
    return QueueOut.model_validate({"items": v1_downloads.queue(db, kind)})


@router.get(
    "/queue/{download_id}",
    response_model=QueueItemOut,
    summary="One download",
    description="A download as the queue lists it, also after it finished.",
    responses=v1_error_responses((404, "download_not_found")),
)
def read_download(download_id: int, db: DbSession) -> QueueItemOut:
    return QueueItemOut.model_validate(v1_downloads.one(db, download_id))


@router.get(
    "/problems",
    response_model=QueueOut,
    summary="Downloads with a problem, and what may be done",
    description=(
        "The downloads of the queue with a problem or a hint, those that need somebody first. Each problem names its "
        "allowed actions and which of them an automatic may take without a human."
    ),
    responses=v1_error_responses((422, "kind_unsupported")),
)
def read_problems(db: DbSession, kind: Annotated[str | None, Query(max_length=16)] = None) -> QueueOut:
    _kind(kind)
    return QueueOut.model_validate({"items": v1_downloads.problems(db, kind)})


def _after(download_id: int) -> ActOut:
    with SessionLocal() as db:
        return ActOut(download=QueueItemOut.model_validate(v1_downloads.one(db, download_id)))


@router.post(
    "/downloads/{download_id}/retry",
    response_model=ActOut,
    summary="Try a download again",
    description="A problem the client reported goes back to the client, any other to filing away.",
    responses=v1_error_responses(*_ACT_ERRORS, (409, "download_not_retryable")),
)
async def retry(download_id: int, key: OperateKey) -> ActOut:
    await v1_downloads.act(download_id, "retry", key.name)
    return await asyncio.to_thread(_after, download_id)


@router.post(
    "/downloads/{download_id}/remove",
    response_model=ActOut,
    summary="Remove a download",
    description=(
        "The download client drops the job (SABnzbd with its files, a torrent without). With `search_again` the "
        "release goes on the title's blocklist and the title into the order of the automatic, even with its switch "
        "off. Filed files are never touched."
    ),
    responses=v1_error_responses(*_ACT_ERRORS, (409, "download_finished")),
)
async def remove(download_id: int, key: OperateKey, payload: RemoveIn | None = None) -> ActOut:
    action = "remove_and_search" if payload is not None and payload.search_again else "remove"
    await v1_downloads.act(download_id, action, key.name)
    return await asyncio.to_thread(_after, download_id)


@router.post(
    "/downloads/{download_id}/clear",
    response_model=ActOut,
    summary="Take a failed download off the problems",
    description="It stays in the history, and its release stays on the blocklist.",
    responses=v1_error_responses(*_ACT_ERRORS, (409, "download_not_failed")),
)
async def clear(download_id: int, key: OperateKey) -> ActOut:
    await v1_downloads.act(download_id, "clear", key.name)
    return await asyncio.to_thread(_after, download_id)


@router.post(
    "/downloads/{download_id}/search",
    response_model=ActOut,
    summary="Search the title of a download again",
    description=(
        "The title goes into the order of the automatic, even with its switch off, within the indexers' budget. A "
        "failed download is taken off the problems with it."
    ),
    responses=v1_error_responses(*_ACT_ERRORS),
)
async def search(download_id: int, key: OperateKey) -> ActOut:
    await v1_downloads.act(download_id, "search", key.name)
    return await asyncio.to_thread(_after, download_id)


@router.post(
    "/downloads/{download_id}/confirm-mapping",
    response_model=ActOut,
    summary="Confirm the proposed path of a download",
    description="Stores the proposal of `path_not_found` with the download client and files the download away again.",
    responses=v1_error_responses(*_ACT_ERRORS, (409, "mapping_not_proposed")),
)
async def confirm_mapping(download_id: int, key: OperateKey) -> ActOut:
    await v1_downloads.act(download_id, "confirm_mapping", key.name)
    return await asyncio.to_thread(_after, download_id)


@router.post(
    "/downloads/{download_id}/finish",
    response_model=ActOut,
    summary="File nothing more of a download",
    description="The download counts as filed with what is filed; the rest is not filed.",
    responses=v1_error_responses(*_ACT_ERRORS, (409, "download_not_assignable"), (409, "download_busy")),
)
async def finish(download_id: int, key: OperateKey) -> ActOut:
    await v1_downloads.act(download_id, "finish", key.name)
    return await asyncio.to_thread(_after, download_id)


@router.get(
    "/downloads/{download_id}/files",
    response_model=DownloadFilesOut,
    summary="The files of a stuck download",
    description=(
        "Every video with what nexcrate read from its name and decided; for a series every episode in TMDB's numbers "
        "with the file the version holds for it. `name` is relative to the download, never a path of the disk."
    ),
    responses=v1_error_responses((404, "download_not_found")),
)
def read_files(download_id: int) -> DownloadFilesOut:
    return DownloadFilesOut.model_validate(v1_downloads.files(download_id))


@router.post(
    "/downloads/{download_id}/assign",
    response_model=ActOut,
    summary="Assign the files of a download by hand",
    description=(
        "A series: each file with its episodes in TMDB's numbers, filed in the background; a file with no episodes is "
        "not filed, a file left out keeps its decision. A movie with several videos: exactly one file, the one to "
        'file. Replacing a file that is not worse needs `confirm: ["not_better"]`.'
    ),
    responses=v1_error_responses(
        *_ACT_ERRORS,
        (409, "download_not_assignable"),
        (409, "download_busy"),
        (409, "assignment_not_better"),
        (409, "assignment_covers_more"),
        (409, "download_files_changed"),
        (409, "download_not_choosable"),
        (422, "episode_not_found"),
        (422, "episode_twice"),
        (422, "scope_not_for_kind"),
        (422, "invalid_input"),
        (422, "track_not_found"),
        (422, "release_not_of_album"),
        (422, "track_not_in_release"),
        (422, "track_twice"),
        (422, "release_fixed"),
    ),
)
async def assign(download_id: int, payload: AssignIn, key: OperateKey) -> ActOut:
    chosen: list[tuple[int, list[tuple[int, int]] | None]] = []
    for item in payload.files:
        pairs = None
        if item.series is not None:
            pairs = [(episode.season, episode.episode) for episode in item.series.episodes]
        chosen.append((item.key, pairs))
    album = None
    if payload.album is not None or any(item.album is not None for item in payload.files):
        album = v1_downloads.AlbumAssign(
            release=payload.album.release if payload.album is not None else None,
            files=[
                (item.key, item.album.track if item.album else None, bool(item.album and item.album.without_track))
                for item in payload.files
            ],
        )
    await v1_downloads.assign(download_id, chosen, list(payload.confirm), key.name, album)
    return await asyncio.to_thread(_after, download_id)


# --- Why, history ----------------------------------------------------------------------------------------------- #


@router.get(
    "/titles/{kind}/{ref}/why",
    response_model=WhyOut,
    summary="Why a title is not there yet",
    description=(
        "Per version one reason as a code with params, the weightiest first: loading, stuck, not released yet, the "
        "last search found only releases the profile refuses, the next search, waiting out a delay. Build the sentence "
        "from the code. Asks no indexer."
    ),
    responses=v1_error_responses(*_TITLE_ERRORS),
)
def read_why(kind: str, ref: str, db: DbSession) -> WhyOut:
    _title_kind(kind)
    found = v1_why.explain(db, _title_id(db, kind, ref))
    if found is None:
        raise error("title_not_found", "nexcrate does not have this title.", 404, kind=kind, ref=ref)
    return WhyOut.model_validate(found)


@router.post(
    "/titles/why",
    response_model=WhyManyOut,
    summary="Why many titles are not there yet",
    description=(
        f"The same for up to {v1_why.BATCH_MAX} titles, in the order asked; an entry nexcrate cannot answer carries "
        "`error` and breaks nothing else."
    ),
)
def read_why_many(payload: WhyManyIn, db: DbSession) -> WhyManyOut:
    from ..services.api_v1 import titles as v1_titles

    items = []
    for entry in v1_titles.lookup(db, [(item.kind, item.ref) for item in payload.items]):
        title_id = entry.get("title_id")
        # An artist has no why of its own.
        artist = entry["kind"] == "artist"
        why = v1_why.explain(db, title_id) if title_id is not None and not artist else None
        problem = entry["error"] or ("kind_unsupported" if artist else None)
        items.append(
            {"kind": entry["kind"], "ref": entry["ref"], "known": why is not None, "why": why, "error": problem}
        )
    return WhyManyOut.model_validate({"items": items})


@router.get(
    "/titles/{kind}/{ref}/history",
    response_model=HistoryOut,
    summary="The history of a title",
    description=(
        "Loaded, filed (with the replaced file's quality and size), failed, deleted, restored, requested, taken back, "
        "and what programs did, newest first in pages."
    ),
    responses=v1_error_responses(*_TITLE_ERRORS),
)
def read_history(
    kind: str,
    ref: str,
    db: DbSession,
    before: Annotated[int | None, Query(ge=1, description="The `next_before` of the last page.")] = None,
    limit: Annotated[int, Query(ge=1, le=v1_history.PAGE_MAX)] = v1_history.PAGE_DEFAULT,
) -> HistoryOut:
    _title_kind(kind)
    found = v1_history.of_title(db, _title_id(db, kind, ref), before, limit)
    if found is None:
        raise error("title_not_found", "nexcrate does not have this title.", 404, kind=kind, ref=ref)
    return HistoryOut.model_validate(found)


# --- Events ----------------------------------------------------------------------------------------------------- #


@router.get(
    "/events",
    response_model=EventsOut,
    summary="What happened since a number",
    description=(
        "Events after `after`, oldest first; ask again with `next_after`. Start with the `latest` of a first call. "
        "Events stay 90 days; a number older than that answers 410 `marker_too_old`: read the library again. Types: "
        + ", ".join(v1_events.TYPES)
        + "."
    ),
    responses=v1_error_responses((410, "marker_too_old")),
)
def read_events(
    db: DbSession,
    after: Annotated[int, Query(ge=0, description="The `next_after` of the last answer.")] = 0,
    limit: Annotated[int, Query(ge=1, le=v1_events.PAGE_MAX)] = v1_events.PAGE_DEFAULT,
) -> EventsOut:
    try:
        return EventsOut.model_validate(v1_events.since(db, after, limit))
    except v1_events.MarkerTooOld:
        raise error("marker_too_old", "This number is too old; read the library again.", 410) from None


#: Open streams per key, and how many a key may hold.
STREAMS_PER_KEY = 5
#: A stream ends after this long; the program reconnects with ``Last-Event-ID`` (proxies like it that way too).
STREAM_SECONDS = 3600
POLL_SECONDS = 1.0
KEEPALIVE_SECONDS = 15.0
_streams: dict[int, int] = {}
_streams_lock = threading.Lock()


def _take_stream(key_id: int) -> bool:
    with _streams_lock:
        if _streams.get(key_id, 0) >= STREAMS_PER_KEY:
            return False
        _streams[key_id] = _streams.get(key_id, 0) + 1
        return True


def _give_stream(key_id: int) -> None:
    with _streams_lock:
        left = _streams.get(key_id, 0) - 1
        if left > 0:
            _streams[key_id] = left
        else:
            _streams.pop(key_id, None)


def _page(after: int) -> dict[str, Any]:
    with SessionLocal() as db:
        return v1_events.since(db, after, v1_events.PAGE_MAX)


async def _stream(request: Request, after: int, key_id: int) -> AsyncIterator[str]:
    last = after
    started = time.monotonic()
    quiet = 0.0
    try:
        yield "retry: 3000\n\n"
        while time.monotonic() - started < STREAM_SECONDS:
            if await request.is_disconnected():
                break
            try:
                page = await asyncio.to_thread(_page, last)
            except v1_events.MarkerTooOld:
                break
            for item in page["items"]:
                body = EventOut.model_validate(item).model_dump(mode="json")
                yield f"id: {item['seq']}\nevent: {item['type']}\ndata: {json.dumps(body, separators=(',', ':'))}\n\n"
                last = item["seq"]
                quiet = 0.0
            if page["more"]:
                continue
            await asyncio.sleep(POLL_SECONDS)
            quiet += POLL_SECONDS
            if quiet >= KEEPALIVE_SECONDS:
                quiet = 0.0
                yield ": keep-alive\n\n"
    finally:
        _give_stream(key_id)


@router.get(
    "/events/stream",
    response_model=None,
    summary="The events as a stream",
    description=(
        "The same events as Server-Sent Events: `id` is the number, `event` the type, `data` the event as JSON; a "
        "comment every 15 seconds keeps proxies from closing. One way only: nexcrate never needs to reach the program. "
        "After a break reconnect with the header `Last-Event-ID` or `after`. A stream ends after an hour. At most "
        f"{STREAMS_PER_KEY} open streams per key (`too_many_streams`)."
    ),
    responses={
        200: {"content": {"text/event-stream": {}}, "description": "The stream."},
        **v1_error_responses((410, "marker_too_old"), (429, "too_many_streams")),
    },
)
async def stream_events(
    request: Request,
    key: ReadKey,
    after: Annotated[int | None, Query(ge=0, description="The number to follow; the latest when left out.")] = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID", max_length=20)] = None,
) -> StreamingResponse:
    start = after
    if start is None and last_event_id is not None and last_event_id.isdigit():
        start = int(last_event_id)

    def begin() -> int:
        with SessionLocal() as db:
            if start is None:
                return v1_events.latest(db)
            v1_events.since(db, start, 1)
            return start

    try:
        first = await asyncio.to_thread(begin)
    except v1_events.MarkerTooOld:
        raise error("marker_too_old", "This number is too old; read the library again.", 410) from None
    if not _take_stream(key.id):
        raise error("too_many_streams", "This key holds as many open streams as it may.", 429, limit=STREAMS_PER_KEY)
    return StreamingResponse(
        _stream(request, first, key.id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- Foreign jobs (the owner's finding 2 of 22.09.2026) ------------------------------------------------------------- #


class ForeignProposalOut(BaseModel):
    kind: Literal["movie"] = "movie"
    ref: str = Field(description="The movie as `tmdb:<id>`.", examples=["tmdb:603"])
    name: str
    year: int | None = None
    in_library: bool


class ForeignJobV1Out(BaseModel):
    job_id: int = Field(description="nexcrate's number of the foreign job, for `remove`.")
    client_id: int | None = Field(description="The download client; null when it was deleted since.")
    name: str = Field(description="The client's name of the job.")
    state: str = Field(description="queued, downloading, paused, completed, failed or problem, as the client says.")
    progress: float | None = Field(description="0 to 100.")
    size_bytes: int | None
    first_seen_at: datetime
    proposals: list[ForeignProposalOut] = Field(description="Movies of the library the name points to.")


class ForeignJobsV1Out(BaseModel):
    items: list[ForeignJobV1Out]


def _foreign_items() -> dict[str, Any]:
    from ..services.downloads import foreign

    items = []
    for row in foreign.listing():
        items.append(
            {
                "job_id": row["id"],
                "client_id": row["client"]["id"] if row["client"] else None,
                "name": row["name"],
                "state": row["state"],
                "progress": row["progress"],
                "size_bytes": row["size_bytes"],
                "first_seen_at": row["first_seen_at"],
                "proposals": [
                    {
                        "ref": f"tmdb:{item['tmdb_id']}",
                        "name": item["title"],
                        "year": item["year"],
                        "in_library": item["title_id"] is not None,
                    }
                    for item in row["proposals"]
                ],
            }
        )
    return {"items": items}


@router.get(
    "/foreign-jobs",
    response_model=ForeignJobsV1Out,
    summary="Jobs in nexcrate's category that no download follows",
    description=(
        "Put there by hand, by another program, or handed over without an answer and never matched; read from the "
        "clients once a minute. nexcrate never imports them by itself. The owner assigns one to a movie in the "
        "interface; a program can remove one. Since 22.09.2026."
    ),
)
def foreign_jobs() -> ForeignJobsV1Out:
    return ForeignJobsV1Out.model_validate(_foreign_items())


class ForeignAdoptV1In(BaseModel):
    kind: Literal["movie", "series", "album"]
    ref: str = Field(max_length=100, description="The title as everywhere in /api/v1, for example `tmdb:603`.")
    version_id: str = Field(max_length=64, description="The public id of the version definition.")


class ForeignAdoptV1Out(BaseModel):
    download_id: int = Field(description="The download the job became; follow it in the queue.")


@router.post(
    "/foreign-jobs/{job_id}/import",
    response_model=ForeignAdoptV1Out,
    status_code=201,
    summary="Assign a foreign job to a title of the library and import it",
    description=(
        "As in the interface: the job becomes a download of the version and is filed away when finished. Only titles "
        "of the library; a series job is for the episodes its name names. Needs the scope operate. Since 22.09.2026."
    ),
    responses=v1_error_responses(
        (404, "not_found"),
        (404, "title_not_found"),
        (409, "foreign_job_failed"),
        (409, "foreign_same_release"),
        (409, "foreign_no_version"),
        (409, "foreign_no_episodes"),
        (409, "version_owned_by_source"),
        (422, "invalid_input"),
        (422, "version_unknown"),
        (422, "version_kind_mismatch"),
    ),
)
def import_foreign_job(job_id: int, payload: ForeignAdoptV1In, db: DbSession, key: OperateKey) -> ForeignAdoptV1Out:
    from ..services.api_v1 import writing as v1_writing
    from ..services.downloads import foreign

    title_id = _title_id(db, payload.kind, payload.ref)
    [definition] = v1_writing.definitions_of(db, payload.kind, [payload.version_id])
    try:
        download_id = foreign.adopt(job_id, title_id, definition.id)
    except foreign.ForeignError as exc:
        params = {name: value for name, value in exc.detail.items() if name not in ("code", "message")}
        raise error(exc.detail["code"], exc.detail.get("message", ""), exc.status, **params) from exc
    return ForeignAdoptV1Out(download_id=download_id)


@router.post(
    "/foreign-jobs/{job_id}/remove",
    status_code=204,
    response_model=None,
    summary="Remove a foreign job with its files",
    description="The job leaves the download client with its files. Needs the scope operate.",
    responses=v1_error_responses((404, "not_found"), (502, "client_unreachable")),
)
async def remove_foreign_job(job_id: int, key: OperateKey) -> None:
    from ..services.downloads import foreign

    try:
        await foreign.remove(job_id)
    except foreign.ForeignError as exc:
        raise error(exc.detail["code"], exc.detail.get("message", ""), exc.status) from exc
