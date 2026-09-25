"""Folders on disk: scanning, proposals, assigning by hand and restoring from release.nex.

the design notes, "API", "Folders on disk". The scan, restoring and assigning many at once run as jobs in
memory; ``GET /api/disk/scan`` follows the newest one. Nothing here moves, renames or deletes a movie; the only file
written on disk is ``release.nex``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..db import SessionLocal
from ..meldungen import error, error_responses
from ..models import DiskFolder, DiskRoot, Title, VersionDefinition, utcnow
from ..services import keep_as_is, schreibweisen, tmdb
from ..services.disk import album_assign, series_assign
from ..services.disk import assign as assigning
from ..services.disk import jobs as disk_jobs
from ..services.disk import restore as restoring
from ..services.disk import roots as root_service
from ..services.disk import scan as scanning
from ..services.series import watching
from .library import TitleDetail, _detail

logger = logging.getLogger("nexcrate.disk")

router = APIRouter(prefix="/api/disk", tags=["disk"])

PATH_MAX_LENGTH = 4096
FILE_MAX_LENGTH = 1024
IDS_MAX = 10_000
LIMIT_MAX = 500

DiskState = Literal[
    "library", "moved", "radarr", "sonarr", "lidarr", "restorable", "proposal", "unknown", "conflict", "file", "disc",
    "no_video", "unreadable", "ignored",
]  # fmt: skip
RootKind = Literal["movie", "series", "album"]


class VersionRef(BaseModel):
    id: int = Field(description="The version definition.")
    label: str


class DiskRootOut(BaseModel):
    id: int
    path: str = Field(description="The folder, resolved, as nexcrate sees it.")
    kind: RootKind = Field(
        default="movie",
        description="movie: its subfolders are movie folders; series: series folders (S6); album: artist and album "
        "folders (Music M6).",
    )
    versions: list[VersionRef] = Field(
        description="The version definitions this folder belongs to: the one whose default folder it is, and those "
        "whose own versions have it as root folder. Empty for a folder the owner added that belongs to none."
    )
    added_by_owner: bool
    last_scan_at: datetime | None = Field(description="UTC. Null before the first scan.")
    counts: dict[str, int] = Field(
        description="Rows per state after the last scan; `ignored` counts the rows the owner marked, whatever their "
        "state. Empty before the first scan."
    )
    missing_files: int = Field(description="Own files below this folder the last scan did not find (decision 25).")
    missing_examples: list[str] = Field(description="Up to 20 movie folders of such files, relative to the root.")
    radarr_unmapped: list[str] = Field(
        description="Names of Radarr connections whose root folders the scan could not map by files; their movie "
        "folders stay unknown until mapped."
    )
    error_code: str | None = Field(description="`folder_not_visible` when the last scan could not see the folder.")


class Progress(BaseModel):
    done: int
    total: int | None


class DiskJobOut(BaseModel):
    id: int
    kind: Literal["scan", "restore", "assign"]
    state: Literal["running", "done", "failed"]
    phase: str | None = Field(
        description="scan: listing, folders, radarr, matching, tmdb; restore: restoring; assign: assigning. Null "
        "unless running."
    )
    progress: Progress | None
    started_at: datetime = Field(description="UTC.")
    finished_at: datetime | None = Field(description="UTC. Null while running.")
    error_code: str | None = Field(description="Null unless failed; what was committed before stays.")
    result: dict[str, Any] | None = Field(
        description="Null unless done. Every kind carries `counts` per outcome. scan: `roots`, `skipped`, `truncated`, "
        "`counts` per state, `missing_files`, `tmdb_ready`, `tmdb_pending`, `tmdb_asked`. restore: `counts` with "
        "restored, conflict, no_definition, skipped, failed (also at the top level), `conflicts` (up to 20 folder "
        "ids), `reasons`, `folders`. assign: `counts` with assigned, skipped, conflict, failed (also at the top "
        "level), `conflicts`, `folders`."
    )


class DiskOverview(BaseModel):
    roots: list[DiskRootOut]
    job: DiskJobOut | None = Field(description="The newest job of the last 30 minutes.")
    tmdb_ready: bool = Field(description="Whether a TMDB token is stored: without one proposals wait.")


class RootIn(BaseModel):
    path: str = Field(max_length=PATH_MAX_LENGTH, description="A folder nexcrate sees, chosen with GET /api/folders.")
    kind: RootKind = Field(default="movie", description="Whether the folder holds movie, series or album folders.")


class ScanIn(BaseModel):
    root_ids: list[int] | None = Field(default=None, max_length=1000, description="Roots to scan; omitted: all.")


class Video(BaseModel):
    name: str
    size_bytes: int
    modified_at: str | None = Field(default=None, description="UTC, ISO 8601.")
    name_quality: str = Field(description="What the file name alone says, Unknown without a source or resolution.")


class Parsed(BaseModel):
    title: str | None
    year: int | None


class Proposal(BaseModel):
    tmdb_id: int
    imdb_id: str | None
    title: str
    year: int | None
    poster_file: str | None = Field(description="A TMDB poster file for `/api/tmdb/poster/w185/{file}`, or null.")
    poster_url: str | None = Field(
        default=None,
        description="The poster address the interface loads: the library's poster for a title in the library, else "
        "`/api/tmdb/poster/w185/{poster_file}`, or null.",
    )
    from_: Literal["companion", "name_number", "nfo_number", "title_year", "library"] = Field(alias="from")
    title_id: int | None = Field(description="The title in the library when the movie is there already.")
    unambiguous: bool = Field(description="Decision 18: a number naming one movie, or exactly one title/year match.")

    model_config = {"populate_by_name": True}


class AlbumProposal(BaseModel):
    title_id: int | None = Field(description="The album in the library; null: add it over MusicBrainz first (`mbid`).")
    mbid: str | None = Field(description="The MusicBrainz release group.")
    title: str
    artist: str | None
    year: int | None
    from_: Literal["companion", "tags", "library"] = Field(alias="from")
    unambiguous: bool = Field(description="An id of the folder, or exactly one album of the artist by its name.")

    model_config = {"populate_by_name": True}


class AlbumFolder(BaseModel):
    """What an album folder says (Music M6)."""

    audio: int = Field(description="Audio files in the folder and its medium folders.")
    artist: str | None = Field(description="From the tags of the first file, else the artist folder's name.")
    album: str = Field(description="From the tags of the first file, else the folder's name.")
    release_group: str | None = Field(description="The MusicBrainz release group of release.nex or the tags.")
    release: str | None = Field(description="The MusicBrainz release of release.nex or the tags.")
    from_: Literal["companion", "tags"] | None = Field(alias="from", description="Where the ids came from.")
    proposals: list[AlbumProposal]

    model_config = {"populate_by_name": True}


class DiskFolderOut(BaseModel):
    id: int
    root_id: int
    relative_path: str = Field(description="Below the root as on disk, `Group/Movie` inside a group folder.")
    kind: Literal["folder", "group", "file", "disc"]
    state: Literal[
        "library", "moved", "radarr", "sonarr", "lidarr", "restorable", "proposal", "unknown", "conflict", "file",
        "disc", "no_video", "unreadable",
    ]  # fmt: skip
    ignored: bool
    videos: list[Video] = Field(description="Largest first, at most 20.")
    parsed: Parsed
    companion: dict[str, Any] | None = Field(
        description="The folder's release.nex: `outcome` (ours, other_installation, newer_format, broken, foreign), "
        "`installation`, `tmdb_id`, `imdb_id`, `title`, `year`, and `entries` (version, file, size_bytes, quality, "
        "quality_from, release_title, release_group, languages; never subtitles). A series folder carries `outcome`, "
        "`tmdb_id` and `entries` with only `version`, the labels of every season folder's file. Null without a file."
    )
    proposals: list[Proposal]
    tmdb_id: int | None = Field(description="The best known TMDB number.")
    title_id: int | None
    version_id: int | None
    source_name: str | None = Field(description="The Radarr connection for a `radarr` row, Sonarr's for `sonarr`.")
    seen_at: datetime = Field(description="UTC.")
    series: dict[str, Any] | None = Field(
        default=None,
        description="A series folder: `videos` (how many reading it would look at), `seasons` (season folder names), "
        "`companion_tmdb_id`. Null for a movie folder.",
    )
    album: AlbumFolder | None = Field(
        default=None,
        description="An album folder (below a music root, Music M6); its proposals are here, `proposals` is empty.",
    )


class FolderPage(BaseModel):
    total: int
    items: list[DiskFolderOut]


class FileIn(BaseModel):
    file: str | None = Field(
        default=None, max_length=FILE_MAX_LENGTH, description="A video of the folder; omitted: the largest."
    )


class MediaOut(BaseModel):
    file: str
    media: dict[str, Any] | None = Field(description="nexcrate's media shape (schema 1), or null when unreadable.")
    summary: str | None = Field(description="One line for the dialog.")
    quality: str = Field(description="What would be recorded (decision 22).")
    quality_from: Literal["media", "name"]
    name_quality: str = Field(description="What the names alone say.")
    languages: list[str] = Field(description="Radarr's language names (decision 24).")
    error_code: Literal["media_unreadable", "media_timeout", "media_truncated"] | None = Field(
        description="Set when the tool failed or found the file cut off (media_truncated); the name decides then."
    )


_KEEP_AS_IS = (
    "Keep what the folders bring as it is: a movie or album version with a file is left alone, every episode with a "
    "file switched off once its folder is read, so nothing of it is upgraded; what arrives later is upgraded as usual."
)


class AssignIn(BaseModel):
    tmdb_id: int = Field(ge=1, le=2_147_483_647)
    version_id: int = Field(description="The version definition the file goes into.")
    file: str | None = Field(default=None, max_length=FILE_MAX_LENGTH, description="A video of the folder.")
    rule: str = Field(
        default="all",
        max_length=16,
        description="A series folder: what the version watches, as when adding a series (all, future, missing, "
        "from_season, none).",
    )
    from_season: int | None = Field(default=None, ge=1, le=10_000)
    keep_as_is: bool = Field(default=False, description=_KEEP_AS_IS)


class RestoreIn(BaseModel):
    folder_ids: list[int] | None = Field(default=None, max_length=IDS_MAX)
    all: bool = Field(default=False, description="Every restorable folder.")
    keep_as_is: bool = Field(default=False, description=_KEEP_AS_IS)


class AssignManyIn(BaseModel):
    folder_ids: list[int] | None = Field(default=None, max_length=IDS_MAX)
    all: bool = Field(default=False, description="Every row in `proposal` with exactly one unambiguous proposal.")
    keep_as_is: bool = Field(default=False, description=_KEEP_AS_IS)


# --- Helpers -------------------------------------------------------------------------------------------------- #


def _job_out(snapshot: dict[str, Any] | None) -> DiskJobOut | None:
    return DiskJobOut.model_validate(snapshot) if snapshot is not None else None


def _root_out(root: DiskRoot, definitions: list[VersionDefinition]) -> DiskRootOut:
    counts = dict(root.last_counts or {})
    return DiskRootOut(
        id=root.id,
        path=root.path,
        kind=root.kind if root.kind in ("series", "album") else "movie",
        versions=[VersionRef(id=definition.id, label=definition.label) for definition in definitions],
        added_by_owner=bool(root.added_by_owner),
        last_scan_at=root.last_scan_at,
        counts={key: int(value) for key, value in counts.items() if isinstance(value, int) and key != "missing_files"},
        missing_files=int(counts.get("missing_files") or 0),
        missing_examples=[str(item) for item in counts.get("missing_examples") or []],
        radarr_unmapped=[str(item) for item in counts.get("radarr_unmapped") or []],
        error_code=root.last_error_code,
    )


def _overview(kind: str | None = None) -> DiskOverview:
    with SessionLocal() as db:
        roots = root_service.sync(db)
        db.commit()
        wanted = root_service.derived(db)
        outs = [
            _root_out(root, root_service.definitions_of(db, root, wanted))
            for root in roots
            if kind is None or (root.kind or "movie") == kind
        ]
    return DiskOverview(roots=outs, job=_job_out(disk_jobs.latest()), tmdb_ready=scanning.tmdb_ready())


def _album_roots(db: Any) -> set[int]:
    return set(db.scalars(select(DiskRoot.id).where(DiskRoot.kind == "album")))


def _album_out(row: DiskFolder) -> AlbumFolder:
    numbers = row.numbers if isinstance(row.numbers, dict) else {}
    return AlbumFolder.model_validate(
        {
            "audio": int(numbers.get("audio") or 0),
            "artist": numbers.get("artist"),
            "album": str(numbers.get("album") or row.parsed_title or row.relative_path),
            "release_group": numbers.get("release_group"),
            "release": numbers.get("release"),
            "from": numbers.get("from"),
            "proposals": [item for item in (row.proposals or []) if isinstance(item, dict)],
        }
    )


def _folder_out(row: DiskFolder, source_name: str | None, album: bool = False) -> DiskFolderOut:
    return DiskFolderOut(
        id=row.id,
        root_id=row.root_id,
        relative_path=row.relative_path,
        kind=row.kind,
        state=row.state,
        ignored=bool(row.ignored),
        videos=[Video.model_validate(video) for video in (row.videos or []) if isinstance(video, dict)],
        parsed=Parsed(title=row.parsed_title, year=row.parsed_year),
        companion=row.companion,
        proposals=[]
        if album
        else [Proposal.model_validate(item) for item in (row.proposals or []) if isinstance(item, dict)],
        tmdb_id=row.tmdb_id,
        title_id=row.title_id,
        version_id=row.version_id,
        source_name=source_name,
        seen_at=row.seen_at,
        series=row.series,
        album=_album_out(row) if album else None,
    )


def _one(row_id: int) -> DiskFolderOut:
    with SessionLocal() as db:
        row = db.get(DiskFolder, row_id)
        if row is None:
            raise assigning.not_found()
        names = assigning.source_names(db, [row])
        return _folder_out(row, names.get(row.source_id or 0), row.root_id in _album_roots(db))


def _running() -> Any:
    return error(
        "disk_job_running", "nexcrate is scanning folders or restoring movies right now. Wait until it finished.", 409
    )


def _start(kind: str, work: Any) -> DiskJobOut:
    try:
        return DiskJobOut.model_validate(disk_jobs.start(kind, work))
    except disk_jobs.DiskJobRunning as exc:
        raise _running() from exc


# --- Routes ---------------------------------------------------------------------------------------------------- #


@router.get(
    "",
    response_model=DiskOverview,
    summary="Read the scanned folders",
    description=(
        "The roots the scan walks (the default folder of every movie version, every root folder of a version "
        "nexcrate owns, and the folders the owner added), each with its version definitions, the counts of its last "
        "scan and its own files missing on disk; the newest job of the last 30 minutes; and whether TMDB can be asked."
    ),
)
def read_disk(
    kind: Annotated[RootKind | None, Query(description="Only roots of this kind; omitted: every root.")] = None,
) -> DiskOverview:
    return _overview(kind)


@router.post(
    "/roots",
    status_code=201,
    response_model=DiskRootOut,
    summary="Add a folder to scan",
    description=(
        "Adds a folder the owner chose with GET /api/folders to the scanned folders. A folder that is a root already "
        "answers 409 `root_exists`; one nexcrate does not see, 404 `folder_not_visible`."
    ),
    responses=error_responses((404, "folder_not_visible"), (409, "root_exists"), (422, "invalid_input")),
)
def add_root(payload: RootIn) -> DiskRootOut:
    if not payload.path.strip():
        raise error("invalid_input", "The input is not valid.", 422, fields=["path"])
    with SessionLocal() as db:
        try:
            root = root_service.add(db, payload.path, payload.kind)
        except root_service.NotVisible as exc:
            raise error(
                "folder_not_visible", "nexcrate does not see this folder. Only mounted folders can be chosen.", 404
            ) from exc
        except root_service.RootExists as exc:
            raise error("root_exists", "nexcrate already scans this folder.", 409) from exc
        db.commit()
        return _root_out(root, root_service.definitions_of(db, root))


@router.delete(
    "/roots/{root_id}",
    status_code=204,
    response_model=None,
    summary="Remove a folder the owner added",
    description=(
        "Removes a root the owner added, with its scan rows. A root that belongs to a version definition or to "
        "versions nexcrate owns cannot be removed: 409 `root_not_removable`."
    ),
    responses=error_responses((404, "not_found"), (409, "root_not_removable")),
)
def remove_root(root_id: int) -> None:
    with SessionLocal() as db:
        root = db.get(DiskRoot, root_id)
        if root is None:
            raise error("not_found", "This does not exist, or not any more.", 404)
        try:
            root_service.remove(db, root)
        except root_service.NotRemovable as exc:
            raise error(
                "root_not_removable",
                "This folder cannot be removed: it belongs to a version. Only folders you added can be removed.",
                409,
            ) from exc
        db.commit()


@router.post(
    "/scan",
    status_code=202,
    response_model=DiskJobOut,
    summary="Scan folders",
    description=(
        "Starts a scan of the given roots, or of every root, in the background and answers at once with the running "
        "job; follow it with GET /api/disk/scan. Phases: listing, folders, radarr, matching, tmdb (50 folders, then a "
        "minute's pause; without a TMDB token the phase ends at once and the rows wait for the next scan). Rows are "
        "updated in place by relative path; rows not seen again go. Nothing on disk changes. One job at a time: 409 "
        "`disk_job_running`. An unknown root id answers 404 `not_found`."
    ),
    responses=error_responses((404, "not_found"), (409, "disk_job_running"), (422, "invalid_input")),
)
def start_scan(payload: ScanIn | None = None) -> DiskJobOut:
    root_ids = payload.root_ids if payload is not None else None
    if root_ids is not None:
        with SessionLocal() as db:
            root_service.sync(db)
            db.commit()
            known = set(db.scalars(select(DiskRoot.id)))
        if not root_ids or any(root_id not in known for root_id in root_ids):
            raise error("not_found", "This does not exist, or not any more.", 404)
    return _start("scan", scanning.scan_work(root_ids))


@router.get(
    "/scan",
    response_model=DiskJobOut,
    summary="Follow the newest disk job",
    description="The newest scan, restore or assign job that runs or ended less than 30 minutes ago; 404 otherwise.",
    responses=error_responses((404, "not_found")),
)
def read_scan() -> DiskJobOut:
    found = disk_jobs.latest()
    if found is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    return DiskJobOut.model_validate(found)


def _matches_query(row: DiskFolder, forms: list[str]) -> bool:
    haystack = schreibweisen.search_text([row.relative_path, row.parsed_title])
    return any(form in haystack for form in forms)


def _page(
    state: str | None, root_id: int | None, query: str | None, offset: int, limit: int, kind: str | None = None
) -> FolderPage:
    with SessionLocal() as db:
        statement = select(DiskFolder)
        if kind is not None:
            kinds = select(DiskRoot.id).where(DiskRoot.kind == kind)
            statement = statement.where(DiskFolder.root_id.in_(kinds))
        if root_id is not None:
            statement = statement.where(DiskFolder.root_id == root_id)
        if state == "ignored":
            statement = statement.where(DiskFolder.ignored.is_(True))
        elif state:
            statement = statement.where(DiskFolder.state == state, DiskFolder.ignored.is_(False))
        forms = schreibweisen.query_keys(query)
        if forms:
            ordered = db.scalars(statement.order_by(DiskFolder.relative_path))
            rows = [row for row in ordered if _matches_query(row, forms)]
            total = len(rows)
            rows = rows[offset : offset + limit]
        else:
            total = int(db.scalar(select(func.count()).select_from(statement.subquery())) or 0)
            rows = list(db.scalars(statement.order_by(DiskFolder.relative_path).offset(offset).limit(limit)))
        names = assigning.source_names(db, rows)
        albums = _album_roots(db)
        return FolderPage(
            total=total, items=[_folder_out(row, names.get(row.source_id or 0), row.root_id in albums) for row in rows]
        )


@router.get(
    "/folders",
    response_model=FolderPage,
    summary="List scanned folders",
    description=(
        "The rows of the last scans, filtered by state (`ignored` lists the rows the owner marked, every other state "
        "leaves them out), by root and by a search through the spelling keys of folder name and parsed title; sorted "
        "by folder name."
    ),
)
def list_folders(
    state: Annotated[DiskState | None, Query(description="One state, or `ignored`.")] = None,
    root_id: Annotated[int | None, Query(ge=1)] = None,
    q: Annotated[str | None, Query(max_length=200, description="Search text.")] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=LIMIT_MAX)] = 60,
    kind: Annotated[RootKind | None, Query(description="Only rows below roots of this kind.")] = None,
) -> FolderPage:
    return _page(state, root_id, q, offset, limit, kind)


@router.get(
    "/folders/{folder_id}",
    response_model=DiskFolderOut,
    summary="Read one scanned folder",
    description="One row of the last scans, as the list gives it; 404 `not_found` when it is gone.",
    responses=error_responses((404, "not_found")),
)
def read_folder(folder_id: int) -> DiskFolderOut:
    return _one(folder_id)


@router.post(
    "/folders/{folder_id}/ignore",
    response_model=DiskFolderOut,
    summary="Ignore a folder",
    description="Marks the row; the mark survives new scans and the row is counted under `ignored`.",
    responses=error_responses((404, "not_found")),
)
def ignore_folder(folder_id: int) -> DiskFolderOut:
    assigning.set_ignored(folder_id, True)
    return _one(folder_id)


@router.delete(
    "/folders/{folder_id}/ignore",
    response_model=DiskFolderOut,
    summary="Stop ignoring a folder",
    description="Removes the owner's mark from the row.",
    responses=error_responses((404, "not_found")),
)
def unignore_folder(folder_id: int) -> DiskFolderOut:
    assigning.set_ignored(folder_id, False)
    return _one(folder_id)


@router.post(
    "/folders/{folder_id}/media",
    response_model=MediaOut,
    summary="Read the media data of a folder's video",
    description=(
        "Runs mediainfo on the chosen video (the largest when `file` is omitted) and answers with nexcrate's media "
        "shape, the quality and languages that would be recorded (decisions 22 and 24) and where the quality came "
        "from. When the tool fails, `error_code` says so and the name's quality stands. The reading is kept 30 minutes "
        "for the assignment. 409 `folder_changed` when the folder or the video is not as scanned."
    ),
    responses=error_responses((404, "not_found"), (409, "folder_changed"), (422, "invalid_input")),
)
async def read_media(folder_id: int, payload: FileIn | None = None) -> MediaOut:
    file_name = payload.file if payload is not None else None
    answer = await asyncio.to_thread(assigning.media_answer, folder_id, file_name)
    return MediaOut.model_validate(answer)


async def _fetch_movie(tmdb_id: int) -> tmdb.MovieData:
    token = await asyncio.to_thread(tmdb.require_token)
    locale = await asyncio.to_thread(tmdb.account_locale)
    try:
        return await tmdb.fetch_movie(token, tmdb_id, locale)
    except tmdb.TmdbError as exc:
        logger.info("Assigning stopped at TMDB: %s", exc.code)
        raise exc.http() from exc


def _is_series_row(folder_id: int) -> bool:
    with SessionLocal() as db:
        row = db.get(DiskFolder, folder_id)
        if row is None:
            raise assigning.not_found()
        return series_assign.is_series_row(db, row)


def _series_precheck(folder_id: int, payload: AssignIn) -> None:
    if not watching.valid_rule(payload.rule, payload.from_season):
        raise error("invalid_input", "The input is not valid.", 422, fields=["rule"])
    with SessionLocal() as db:
        row = db.get(DiskFolder, folder_id)
        if row is None:
            raise assigning.not_found()
        series_assign.check(db, row, payload.tmdb_id, payload.version_id)


async def _assign_series(folder_id: int, payload: AssignIn) -> TitleDetail:
    """A series folder (P2): title, version and folder now, the files by reading afterwards."""
    await asyncio.to_thread(_series_precheck, folder_id, payload)
    data = None
    if not await asyncio.to_thread(series_assign.title_known, payload.tmdb_id):
        data = await series_assign.fetch(payload.tmdb_id)
    token = series_assign.KEEP_AS_IS.set(payload.keep_as_is)
    try:
        title_id = await asyncio.to_thread(
            series_assign.assign,
            folder_id,
            payload.tmdb_id,
            payload.version_id,
            payload.rule,
            payload.from_season,
            data,
        )
    finally:
        series_assign.KEEP_AS_IS.reset(token)
    return await asyncio.to_thread(_detail, title_id)


def _known_title(tmdb_id: int) -> bool:
    with SessionLocal() as db:
        return db.scalar(select(Title.id).where(Title.kind == "movie", Title.tmdb_id == tmdb_id)) is not None


def _precheck(folder_id: int, tmdb_id: int, version_id: int, file_name: str | None) -> None:
    with SessionLocal() as db:
        row = db.get(DiskFolder, folder_id)
        if row is None:
            raise assigning.not_found()
        assigning.check_assignable(db, row, tmdb_id, version_id, file_name)


@router.post(
    "/folders/{folder_id}/assign",
    status_code=201,
    response_model=TitleDetail,
    summary="Assign a folder to a movie",
    description=(
        "Records, in one transaction, the title (existing, or created with TMDB's data), the title's version of the "
        "chosen definition without a file or a new one (added by the owner, monitored), the video as its file with "
        "quality and languages from its media data and name (decisions 22 and 24), a history entry `found_on_disk`, "
        "the judgement by the profile and the state. Afterwards release.nex is written (switch on) and the row is "
        "`library`. 409 `folder_changed` when folder or video are not as scanned; 409 `folder_known` when the row "
        "became `library` or `radarr`; 409 `version_has_file` with `location` when the version has a file already; "
        "409 `version_owned_by_source` when a Radarr connection feeds it; 422 `version_kind_mismatch`; TMDB's codes. "
        "A series folder (a row below a series root) records the title, the version with "
        "`rule` and this folder as its series folder, then reads the folder in the background; `file` is not used, and "
        "409 `version_has_files` when the version has a series folder already."
    ),
    responses=error_responses(
        (404, "not_found"),
        (409, "folder_changed"),
        (409, "folder_known"),
        (409, "version_has_file"),
        (409, "version_owned_by_source"),
        (409, "version_has_files"),
        (422, "version_kind_mismatch"),
        (422, "invalid_input"),
        *tmdb.ERRORS,
    ),
)
async def assign_folder(folder_id: int, payload: AssignIn) -> TitleDetail:
    if await asyncio.to_thread(_is_series_row, folder_id):
        return await _assign_series(folder_id, payload)
    await asyncio.to_thread(_precheck, folder_id, payload.tmdb_id, payload.version_id, payload.file)
    data: tmdb.MovieData | None = None
    if not await asyncio.to_thread(_known_title, payload.tmdb_id):
        data = await _fetch_movie(payload.tmdb_id)
    title_id, version_id = await asyncio.to_thread(
        assigning.assign, folder_id, payload.tmdb_id, payload.version_id, payload.file, data
    )
    if payload.keep_as_is:
        await asyncio.to_thread(_keep, [version_id])
    return await asyncio.to_thread(_detail, title_id)


def _keep(version_ids: list[int]) -> None:
    with SessionLocal() as db:
        keep_as_is.apply(db, version_ids, utcnow())
        db.commit()


@router.post(
    "/folders/{folder_id}/path",
    response_model=TitleDetail,
    summary="Take the new path of a moved movie",
    description=(
        "For a `moved` row: the version its release.nex names takes this folder and file as its path; the size is "
        "read from disk, nothing on disk is moved. Afterwards release.nex is written and the row is `library`. 409 "
        "`folder_changed` when the row is not `moved` any more or the named file is not there as scanned."
    ),
    responses=error_responses((404, "not_found"), (409, "folder_changed")),
)
async def take_path(folder_id: int) -> TitleDetail:
    title_id = await asyncio.to_thread(assigning.take_path, folder_id)
    return await asyncio.to_thread(_detail, title_id)


@router.post(
    "/restore",
    status_code=202,
    response_model=DiskJobOut,
    summary="Restore movies from their release.nex",
    description=(
        "Starts a job of kind `restore` over the given folders, or every `restorable` folder with `all`, and answers "
        "at once. One transaction per 100 folders: the title (existing or new), the version by the root's definition "
        "and the entry's label, the file's fields from the entry (media data read when the size differs), the listed "
        "subtitle files that exist, history `imported` at the entry's time and `restored` now, the judgement; after "
        "each commit the release.nex is rewritten with this installation's id. A version with a file, one a source "
        "feeds, or a label that fits no definition of the root is a conflict. 409 `disk_job_running`."
    ),
    responses=error_responses((409, "disk_job_running"), (422, "invalid_input")),
)
def start_restore(payload: RestoreIn | None = None) -> DiskJobOut:
    ids = payload.folder_ids if payload is not None else None
    if payload is not None and payload.all:
        ids = None
    elif not ids:
        raise error("invalid_input", "The input is not valid.", 422, fields=["folder_ids"])
    keep = payload is not None and payload.keep_as_is
    return _start("restore", _with_series("restore", ids, restoring.run_restore, "restorable", keep))


@router.post(
    "/assign",
    status_code=202,
    response_model=DiskJobOut,
    summary="Assign many folders at once",
    description=(
        "Starts a job of kind `assign` over the given folders, or with `all` over every row in `proposal` with exactly "
        "one unambiguous proposal: such rows whose root belongs to exactly one version definition are assigned as "
        "POST /api/disk/folders/{id}/assign does; the others are counted as `skipped`, conflicts as `conflict`. 409 "
        "`disk_job_running`."
    ),
    responses=error_responses((409, "disk_job_running"), (422, "invalid_input")),
)
def start_assign_many(payload: AssignManyIn | None = None) -> DiskJobOut:
    ids = payload.folder_ids if payload is not None else None
    if payload is not None and payload.all:
        ids = None
    elif not ids:
        raise error("invalid_input", "The input is not valid.", 422, fields=["folder_ids"])
    ids = list(dict.fromkeys(ids)) if ids is not None else None
    keep = payload is not None and payload.keep_as_is
    return _start("assign", _with_series("assign", ids, assigning.assign_many, "proposal", keep))


def _with_series(kind: str, ids: list[int] | None, movie_work: Any, state: str, keep: bool = False) -> Any:
    """A job over movie folders as before, then series folders (P2), then album folders
    (decision 19). With ``keep`` what the job brings stays as it is (``keep_as_is``)."""

    def work(job: disk_jobs.DiskJob) -> dict[str, Any]:
        started = utcnow()
        token = series_assign.KEEP_AS_IS.set(keep)
        try:
            result = _work_of(job)
        finally:
            series_assign.KEEP_AS_IS.reset(token)
        if keep:
            with SessionLocal() as db:
                kept = keep_as_is.apply(db, keep_as_is.arrived_since(db, started, ("movie", "album")), utcnow())
                db.commit()
            logger.info("Disk job %s keeps what it brought: %d movies, %d albums", kind, kept.movies, kept.albums)
        return result

    def _work_of(job: disk_jobs.DiskJob) -> dict[str, Any]:
        series_ids = series_assign.series_rows(ids, state)
        album_ids = album_assign.album_rows(ids, state)
        chosen = set(series_ids) | set(album_ids)
        movie_ids = None if ids is None else [row_id for row_id in ids if row_id not in chosen]
        if movie_ids is None or movie_ids:
            result = movie_work(job, movie_ids)
        else:
            result = {"counts": {}, "conflicts": [], "folders": 0}
        if series_ids:
            result["folders"] = int(result.get("folders") or 0) + len(series_ids)
            counts, reasons = series_assign.run_many(job, kind, series_ids)
            result = series_assign.merged(result, counts, reasons)
        if album_ids:
            result["folders"] = int(result.get("folders") or 0) + len(album_ids)
            counts, reasons = album_assign.run_many(job, kind, album_ids)
            result = series_assign.merged(result, counts, reasons)
        return result

    return work


_ALBUM_REFUSALS = {
    "not_found": ("This does not exist, or not any more.", 404),
    "album_folder_known": ("This folder belongs to an album of the library or of a Lidarr connection by now.", 409),
    "album_fed_by_source": ("This album is watched in Lidarr; change it there.", 409),
    "album_has_folder": ("This album has a folder with files already.", 409),
    "album_not_in_library": ("Add the album the release.nex names first.", 409),
    "folder_changed": ("The folder is not as scanned any more. Scan again.", 409),
}


def _album_refused(exc: album_assign.Refused) -> Exception:
    message, status = _ALBUM_REFUSALS[exc.code]
    return error(exc.code, message, status)


class AlbumAssignIn(BaseModel):
    title_id: int = Field(
        ge=1, description="The album in the library; an unknown one is added with POST /api/music/albums first."
    )


@router.post(
    "/folders/{folder_id}/album",
    response_model=TitleDetail,
    summary="Assign an album folder",
    description=(
        "A row below a music root (decision 19): the album's version, or a new one, gets this "
        "folder as its album folder, the album's releases are loaded when they are not yet, and the folder is read: "
        "files linked by their tags and names, the rest unclear on the album page. Nothing is moved or renamed. 409 "
        "`album_fed_by_source`, `album_has_folder` (the version has files in another folder), `album_folder_known`."
    ),
    responses=error_responses(*((status, code) for code, (_, status) in _ALBUM_REFUSALS.items())),
)
async def assign_album_folder(folder_id: int, payload: AlbumAssignIn) -> TitleDetail:
    try:
        await asyncio.to_thread(album_assign.check, folder_id, payload.title_id)
        await asyncio.to_thread(album_assign.load_releases, payload.title_id)
        title_id = await asyncio.to_thread(album_assign.assign, folder_id, payload.title_id)
    except album_assign.Refused as exc:
        raise _album_refused(exc) from exc
    return await asyncio.to_thread(_detail, title_id)
