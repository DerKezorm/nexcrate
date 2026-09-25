"""Downloads: loading a release of a search, the Downloads page, retry, clear, a path mapping, removing (step 3).

Loading needs a search that is kept (30 minutes) and a version that can load. The release file is fetched by nexcrate
and handed to the client; no answer carries a link or a key. Everything else happens in the background: tracking asks
the clients, a finished download is filed into its version's folder.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from ..meldungen import error, error_responses
from ..services import downloaders
from ..services.downloads import actions, album_assigning, assigning, loading

logger = logging.getLogger("nexcrate.downloads")

router = APIRouter(prefix="/api/downloads", tags=["downloads"])

PER_PAGE_MAX = 200


class DownloadTitle(BaseModel):
    id: int
    title: str
    year: int | None
    kind: str | None = Field(default=None, description="movie, series or album.")
    artist: str | None = Field(default=None, description="The artist of an album; null otherwise.")


class DownloadVersion(BaseModel):
    id: int | None = Field(description="The version definition; null when it was deleted since.")
    label: str


class DownloadClientRef(BaseModel):
    id: int
    name: str
    kind: str


class DownloadRelease(BaseModel):
    title: str
    indexer_id: int | None
    indexer: str
    size_bytes: int | None
    quality: str | None = Field(description="Parsed from the release name.")
    score: int | None
    below_target: bool


class DownloadProblem(BaseModel):
    code: str = Field(
        description="path_not_found, packed, no_video, no_space, gone_from_client, client_error, import_failed, "
        "dangerous_file, encrypted, client_unreachable or stalled. encrypted: an archive with a password; the release "
        "is on the blocklist, like dangerous_file. Since S4: files_unassigned (videos of a series download nexcrate "
        "could not file safely while an episode is missing), other_series_suspected (the files are named like "
        "another series), several_videos (a movie download with several similar videos), multi_part (a movie in "
        "parts, refused and blocked), import_stalled (30 minutes without progress), too_many_files. Since 24.09.2026: "
        "file_truncated (a video the container says is cut off; not filed, it replaced nothing; for a series "
        "download `episodes` names the episodes)."
    )
    needs_owner: bool
    values: dict[str, Any] = Field(
        description="path_not_found: `proposal` {remote, local} or null. packed: `reason`, one of unsupported, "
        "incomplete, broken, unsafe, nested or too_large. no_space: `needed_bytes` and `free_bytes`. import_failed: "
        "`reason`, for example destination_exists or database_busy. files_unassigned: `filed` episodes and `open` "
        "files. several_videos: `count`. download_failed: `reason` and `detail` (see failed_detail); only while "
        "nothing takes care of it by itself."
    )


class DownloadScope(BaseModel):
    kind: str = Field(description="episode, season, series, or album.")
    season: int | None = Field(default=None, description="The season when every episode lies in one.")
    episodes: list[str] = Field(
        default_factory=list, description="The codes of the episodes the download is for.", examples=[["S02E05"]]
    )
    release_id: int | None = Field(default=None, description="An album: the release its files turned out to be.")
    filed: int = Field(description="Episodes filed; files of an album filed.")
    open: int = Field(description="Videos waiting for the owner while an episode is missing; audio files of an album.")
    missing: int = Field(description="Episodes the download did not bring; tracks of the album's release.")
    skipped_codes: list[str] = Field(
        default_factory=list, description="Episodes left out: the file already there is not worse."
    )
    not_filed_codes: list[str] = Field(default_factory=list, description="Episodes the owner chose not to file.")
    missing_codes: list[str] = Field(default_factory=list, description="Episodes the download did not bring.")
    waiting_codes: list[str] = Field(
        default_factory=list, description="Episodes still without a file while the download is not done."
    )


class DownloadAftermath(BaseModel):
    kind: str = Field(
        description="What came of a failed download: replaced (a later download of the version), kept_file (the search "
        "found nothing better, the file there stays), waiting_limit (a better release waits for the indexer's grab "
        "limit), nothing_found (the search found nothing fitting yet), searching (the replacement search is due), "
        "schedule (three replacements in a day; the version waits for its plan), owner (nothing happens by itself)."
    )
    release: str | None = Field(
        default=None, description="replaced: the later release; waiting_limit: the one held back."
    )
    state: str | None = Field(default=None, description="replaced: the state of the later download.")
    at: datetime | None = Field(
        default=None,
        description="waiting_limit: when the indexer loads again; kept_file and nothing_found: the next search.",
    )


class Download(BaseModel):
    id: int
    title: DownloadTitle
    version: DownloadVersion
    client: DownloadClientRef | None = Field(description="Null when the client was deleted since.")
    protocol: str = Field(description="usenet or torrent.")
    release: DownloadRelease
    state: str = Field(
        description="queued, downloading, paused, completed, importing, imported, failed, problem or removed."
    )
    step: str | None = Field(
        description="What filing away does right now: unpacking while nexcrate unpacks the download's archives. Null "
        "otherwise; only while importing, and kept in memory only."
    )
    progress: float | None = Field(description="0 to 100.")
    remaining_seconds: int | None
    problem: DownloadProblem | None
    transfer: str | None = Field(
        description="hardlink, copy, move or unpacked (the video came out of the download's archives), once imported."
    )
    imported_file: str | None = Field(description="The file name only, never a path.")
    imported_path: str | None = Field(
        description="The imported file relative to the folder it was filed into, for example "
        "`Movie (2003)/Movie (2003) Bluray-1080p.mkv`; never a full path. Null until imported.",
        examples=["Movie (2003)/Movie (2003) Bluray-1080p.mkv"],
    )
    failed_reason: str | None = Field(
        description="For a failed download: client_failed (SABnzbd reported Failed) or encrypted. Null otherwise."
    )
    failed_detail: str | None = Field(
        default=None,
        description="For a failed download, what SABnzbd said as a code: repair_failed, incomplete, not_on_server, "
        "password, unpack_failed, encrypted, unwanted_extension, duplicate, aborted or other. Null when it said "
        "nothing.",
    )
    aftermath: DownloadAftermath | None = Field(default=None, description="For a failed download: what came of it.")
    confirmed: list[str] = Field(description="What the owner confirmed when loading: not_fitting, blocklisted.")
    origin: str = Field(
        description="Who started it: manual, search (a planned search or searching automatically now), rss, or "
        "replacement (after a failed download)."
    )
    grabbed_at: datetime
    completed_at: datetime | None
    imported_at: datetime | None
    updated_at: datetime
    scope: DownloadScope | None = Field(default=None, description="A series download's episodes; null for a movie.")


class DownloadCounts(BaseModel):
    active: int = Field(description="Queued to importing.")
    needs_owner: int = Field(description="Downloads with a problem that needs the owner.")
    hints: int = Field(description="Downloads with a problem that does not.")


class DownloadPage(BaseModel):
    items: list[Download]
    total: int
    page: int
    per_page: int
    counts: DownloadCounts


class LoadIn(BaseModel):
    search_id: str = Field(max_length=64, description="A search of the title, kept for 30 minutes after it finished.")
    release_key: str = Field(max_length=64, description="A release of that search that belongs to the title.")
    version_id: int = Field(description="The version definition, as everywhere in the API.")
    confirm: list[Literal["not_fitting", "blocklisted", "no_gain"]] = Field(
        default_factory=list,
        max_length=3,
        description="Load although the release does not fit, is blocked, or (series) fills and replaces nothing.",
    )


class TakesIn(BaseModel):
    search_id: str = Field(max_length=64, description="A series search, kept for 30 minutes after it finished.")
    version_id: int = Field(description="The series version definition.")
    confirm: list[Literal["not_fitting", "blocklisted", "no_gain"]] = Field(default_factory=list, max_length=3)


class TakeError(BaseModel):
    code: str
    values: dict[str, Any] = Field(default_factory=dict)


class TakeOut(BaseModel):
    release_key: str
    download: Download | None = Field(description="The download; null when the release did not load.")
    error: TakeError | None = Field(description="Why the release did not load; null when it did.")


class TakesOut(BaseModel):
    results: list[TakeOut]


class FileReading(BaseModel):
    form: str | None = Field(description="standard, multi_episode, daily, mini_series or download.")
    from_: str | None = Field(alias="from", description="file, folder or download: where the numbers were read.")
    season: int | None
    numbers: list[int]
    air_date: str | None
    part: int | None = Field(
        default=None, description="1 or 2 for a half of a double episode TMDB lists as one; null otherwise."
    )

    model_config = {"populate_by_name": True}


class FileEpisode(BaseModel):
    id: int
    code: str = Field(examples=["S02E05"])
    name: str


class DownloadVideo(BaseModel):
    key: int = Field(description="Names the file in assign and choose; never a path.")
    path: str = Field(description="Relative to the download; a video out of its archives starts with `unpacked:`.")
    size_bytes: int
    duration_seconds: int | None
    reading: FileReading | None = Field(description="What nexcrate read; null when nothing was readable.")
    decision: str = Field(
        description="filed, open, sample, extra, duplicate, not_needed, not_filed; candidate or chosen for a movie."
    )
    episodes: list[FileEpisode]


class CurrentEpisodeFile(BaseModel):
    quality: str | None
    size_bytes: int
    parts: list[int] = Field(
        default_factory=list,
        description="The halves the episode holds as two files, [1, 2] or [1]; empty for a whole file.",
    )


class SeriesEpisodeChoice(FileEpisode):
    season: int
    in_download: bool
    state: str | None = Field(description="expected, filed, skipped_not_better, missing or not_filed; null outside.")
    watched: bool
    current_file: CurrentEpisodeFile | None


class NamedRef(BaseModel):
    id: int | None
    title: str | None = None
    label: str | None = None


class DownloadFilesOut(BaseModel):
    download_id: int
    kind: str = Field(description="series or movie.")
    files: list[DownloadVideo]
    episodes: list[SeriesEpisodeChoice] = Field(
        description="Every episode of the series with a TMDB episode; specials only a Sonarr connection knows cannot "
        "be assigned. Empty for a movie."
    )
    series: NamedRef
    version: NamedRef


class AssignFile(BaseModel):
    key: int
    episode_ids: list[int] = Field(max_length=50, description="Empty: not filed.")
    part: Literal[1, 2] | None = Field(
        default=None,
        description=(
            "The half of a double episode this file is, with exactly one episode: two "
            "files of one episode as part 1 and part 2. Null for a file that holds its episodes whole."
        ),
    )


class AssignIn(BaseModel):
    files: list[AssignFile] = Field(min_length=1, max_length=2000)
    confirm: list[Literal["not_better"]] = Field(default_factory=list, max_length=1)


class ChooseIn(BaseModel):
    key: int


class AlbumTrackHeld(BaseModel):
    quality: str | None
    file: str = Field(description="The file name in the album folder.")


class AlbumTrackChoice(BaseModel):
    id: int
    medium: int
    position: int
    number: str | None
    name: str
    length_ms: int | None
    held: AlbumTrackHeld | None = Field(description="The file the album holds for this track; null when none.")


class AlbumReleaseChoice(BaseModel):
    id: int
    name: str
    date: str | None
    country: str | None
    formats: list[str]
    media_count: int
    track_count: int
    disambiguation: str | None


class AlbumOtherRef(BaseModel):
    id: int
    title: str


class AlbumAudioFile(BaseModel):
    key: int = Field(description="Names the file in album-assign; never a path.")
    path: str = Field(description="Relative to the download; a file out of its archives starts with `unpacked:`.")
    size_bytes: int
    duration_ms: int | None
    codec: str | None
    bit_depth: int | None
    bitrate: int | None
    tags: dict[str, str | None] = Field(description="title, artist, album, tracknumber, discnumber as the file says.")
    reading: dict[str, Any] | None = Field(description="Medium, position, where they came from, title.")
    decision: str = Field(description="filed, placing, open, loose, other_album, not_needed or not_filed.")
    placed: bool = Field(description="The file lies in the album folder.")
    track_id: int | None
    via: str | None = Field(description="id, position, name, fingerprint or owner.")
    proposal: list[int] = Field(description="Tracks that could fit an open file, best first.")
    other_album: AlbumOtherRef | None


class AlbumProblem(BaseModel):
    code: str
    values: dict[str, Any] = Field(default_factory=dict)


class AlbumFilesOut(BaseModel):
    download_id: int
    kind: str = Field(description="album.")
    album: NamedRef
    artist: str | None
    version: NamedRef
    problem: AlbumProblem | None
    release_id: int | None = Field(description="The release whose tracks are listed.")
    read_release_id: int | None = Field(description="The release nexcrate read the files as.")
    target_release_id: int | None
    release_fixed: bool = Field(description="Files lie in the album folder already: the release cannot change.")
    releases: list[AlbumReleaseChoice]
    tracks: list[AlbumTrackChoice]
    files: list[AlbumAudioFile]


class AlbumAssignFile(BaseModel):
    key: int
    track_id: int | None = Field(default=None, description="The track; null with `loose` false: not filed.")
    loose: bool = Field(default=False, description="File without a track: fits no track, file anyway.")


class AlbumAssignIn(BaseModel):
    release_id: int | None = Field(default=None, description="The release; null keeps the one nexcrate read.")
    files: list[AlbumAssignFile] = Field(min_length=1, max_length=1000)
    confirm: list[Literal["not_better"]] = Field(default_factory=list, max_length=1)


def _download(found: dict[str, Any]) -> Download:
    return Download.model_validate(found)


@router.get(
    "",
    response_model=DownloadPage,
    summary="List the downloads",
    description=(
        "`view` active (queued to importing), problems (a problem or a hint, such as stalled or an unreachable client) "
        "or history (imported, failed, removed), newest first. `counts` covers every view."
    ),
)
def list_downloads(
    view: Annotated[
        str | None, Query(max_length=16, description="active, problems or history. Default active.")
    ] = None,
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    per_page: Annotated[int, Query(ge=1, le=PER_PAGE_MAX)] = 50,
) -> DownloadPage:
    chosen = (view or "").strip() or "active"
    if chosen not in actions.VIEWS:
        raise error("invalid_input", "The input is not valid.", 422, fields=["view"])
    return DownloadPage.model_validate(actions.listing(chosen, page, per_page))


@router.post(
    "",
    status_code=201,
    response_model=Download,
    summary="Load a release",
    description=(
        "Loads a release of a kept search for a version: the version must be able to load; a release that does not "
        "fit or is on the blocklist needs `confirm`. nexcrate fetches the NZB or torrent itself and hands it to the "
        "first enabled client of the protocol by priority; the client never sees the indexer's link or key."
    ),
    responses=error_responses(*loading.ERRORS, (422, "invalid_input")),
)
async def load_release(payload: LoadIn) -> Download:
    try:
        download_id = await loading.grab(
            payload.search_id, payload.release_key, payload.version_id, list(payload.confirm)
        )
    except loading.LoadError as exc:
        raise exc.http() from exc
    return _download(actions.read(download_id))


@router.post(
    "/takes",
    response_model=TakesOut,
    summary="Load what a series search would take",
    description=(
        "Loads every release of `takes` of a kept series search for a version, one after the other, each as its own "
        "download. A release that fails leaves the others; each result names its download or its error."
    ),
    responses=error_responses((404, "search_expired"), (409, "nothing_to_take"), (422, "invalid_input")),
)
async def load_takes(payload: TakesIn) -> TakesOut:
    try:
        results = await loading.grab_takes(payload.search_id, payload.version_id, list(payload.confirm))
    except loading.LoadError as exc:
        raise exc.http() from exc
    out = []
    for item in results:
        download = _download(actions.read(item.download_id)) if item.download_id is not None else None
        error = None
        if item.error is not None:
            values = {key: value for key, value in item.error.items() if key not in ("code", "message")}
            error = TakeError(code=str(item.error.get("code")), values=values)
        out.append(TakeOut(release_key=item.release_key, download=download, error=error))
    return TakesOut(results=out)


@router.get(
    "/{download_id}/files",
    response_model=DownloadFilesOut,
    summary="Read the files of a download",
    description=(
        "The videos of a series download with what nexcrate read and decided, the episodes of the download and of the "
        "series; for a movie download with several videos the candidates. Paths are relative to the download."
    ),
    responses=error_responses((404, "download_not_found")),
)
def download_files(download_id: int) -> DownloadFilesOut:
    try:
        return DownloadFilesOut.model_validate(assigning.files_of(download_id))
    except actions.ActionError as exc:
        raise exc.http() from exc


@router.post(
    "/{download_id}/assign",
    response_model=Download,
    summary="Assign files of a series download by hand",
    description=(
        "Files the chosen videos with the chosen episodes, in the background; the answer is the download while it is "
        "filed. Each episode once, only episodes of the series; a video with no episodes is not filed, a video left "
        'out of the request keeps its decision. Replacing a file that is not worse needs `confirm: ["not_better"]`; '
        "a file of several episodes is only replaced with all of them (`assignment_covers_more` names the others)."
    ),
    responses=error_responses(
        (404, "download_not_found"),
        (409, "download_not_assignable"),
        (409, "download_busy"),
        (409, "assignment_not_better"),
        (409, "assignment_covers_more"),
        (409, "download_files_changed"),
        (422, "episode_twice"),
        (422, "episode_not_in_series"),
        (422, "invalid_input"),
    ),
)
def assign_files(download_id: int, payload: AssignIn) -> Download:
    chosen = [(item.key, list(item.episode_ids), item.part) for item in payload.files]
    try:
        return _download(assigning.assign(download_id, chosen, list(payload.confirm)))
    except actions.ActionError as exc:
        raise exc.http() from exc


@router.get(
    "/{download_id}/album-files",
    response_model=AlbumFilesOut,
    summary="Read the audio files of an album download",
    description=(
        "The audio files of an album download with what nexcrate read and proposes, the releases of the album with "
        "loaded tracks, and the tracks of one release (`release_id`, else the one nexcrate read) with the file the "
        "album holds for each. Paths are relative to the download."
    ),
    responses=error_responses((404, "download_not_found")),
)
def album_files(
    download_id: int, release_id: Annotated[int | None, Query(description="Another release of the album.")] = None
) -> AlbumFilesOut:
    try:
        return AlbumFilesOut.model_validate(album_assigning.files_of(download_id, release_id))
    except actions.ActionError as exc:
        raise exc.http() from exc


@router.post(
    "/{download_id}/album-assign",
    response_model=Download,
    summary="Assign files of an album download by hand",
    description=(
        "Files the audio files with the chosen tracks of one release, in the background; the answer is the download "
        "while it is filed. Each track once; `loose` files a file under its own name without a track (a hidden track, "
        "a medley); a file with neither is not filed. The release can change only while no file lies in the album "
        'folder. Replacing an album whose files are not worse needs `confirm: ["not_better"]`.'
    ),
    responses=error_responses(
        (404, "download_not_found"),
        (409, "download_not_assignable"),
        (409, "download_busy"),
        (422, "release_not_of_album"),
        (422, "release_fixed"),
        (422, "track_not_in_release"),
        (422, "track_twice"),
        (422, "invalid_input"),
    ),
)
def album_assign(download_id: int, payload: AlbumAssignIn) -> Download:
    chosen = [(item.key, item.track_id, item.loose) for item in payload.files]
    try:
        return _download(album_assigning.assign(download_id, payload.release_id, chosen, list(payload.confirm)))
    except actions.ActionError as exc:
        raise exc.http() from exc


@router.post(
    "/{download_id}/finish",
    response_model=Download,
    summary="File nothing more of a download",
    description=(
        "The download is imported with what is filed. Afterwards its unpack folder goes, and SABnzbd's job folder "
        "with the videos not filed, as after a filed download; a torrent keeps its files."
    ),
    responses=error_responses((404, "download_not_found"), (409, "download_not_assignable"), (409, "download_busy")),
)
def finish_download(download_id: int) -> Download:
    try:
        return _download(assigning.finish(download_id))
    except actions.ActionError as exc:
        raise exc.http() from exc


@router.post(
    "/{download_id}/choose",
    response_model=Download,
    summary="Choose the video of a movie download",
    description="For the problem several_videos: files the chosen video as usual.",
    responses=error_responses((404, "download_not_found"), (409, "download_not_choosable"), (422, "invalid_input")),
)
def choose_video(download_id: int, payload: ChooseIn) -> Download:
    try:
        return _download(assigning.choose(download_id, payload.key))
    except actions.ActionError as exc:
        raise exc.http() from exc


@router.delete(
    "/{download_id}",
    status_code=204,
    response_model=None,
    summary="Remove a download",
    description=(
        "With `remove_from_client` (default) the client drops the job: SABnzbd with its files, a torrent without its "
        "files. `blocklist` puts the release on the title's blocklist. A finished download or one being filed away "
        "cannot be removed from the client. Imported files are never touched."
    ),
    responses=error_responses((404, "download_not_found"), (409, "download_finished"), *downloaders.ERRORS),
)
async def remove_download(
    download_id: int,
    remove_from_client: Annotated[bool, Query(description="Also remove the job from the client.")] = True,
    blocklist: Annotated[bool, Query(description="Put the release on the title's blocklist.")] = False,
) -> None:
    try:
        await actions.remove(download_id, remove_from_client=remove_from_client, blocklist=blocklist)
    except actions.ActionError as exc:
        raise exc.http() from exc


@router.post(
    "/{download_id}/retry",
    response_model=Download,
    summary="Try a download again",
    description=(
        "For a download with a problem or one waiting to be filed away. A problem the client reported goes back to "
        "the client, any other to the import."
    ),
    responses=error_responses((404, "download_not_found"), (409, "download_not_retryable")),
)
def retry_download(download_id: int) -> Download:
    try:
        return _download(actions.retry(download_id))
    except actions.ActionError as exc:
        raise exc.http() from exc


@router.post(
    "/{download_id}/clear",
    response_model=Download,
    summary="Take a failed download off the problems",
    description=(
        "A failed download waits in `problems` until the owner takes it off; it stays in the history, and its "
        "release stays on the blocklist."
    ),
    responses=error_responses((404, "download_not_found"), (409, "download_not_failed")),
)
def clear_download(download_id: int) -> Download:
    try:
        return _download(actions.clear(download_id))
    except actions.ActionError as exc:
        raise exc.http() from exc


@router.post(
    "/{download_id}/mapping",
    response_model=Download,
    summary="Confirm a proposed path mapping",
    description=(
        "Stores the proposal of a `path_not_found` problem on the download's client and files the download away again."
    ),
    responses=error_responses((404, "download_not_found"), (409, "mapping_not_proposed")),
)
def confirm_mapping(download_id: int) -> Download:
    try:
        return _download(actions.confirm_mapping(download_id))
    except actions.ActionError as exc:
        raise exc.http() from exc
