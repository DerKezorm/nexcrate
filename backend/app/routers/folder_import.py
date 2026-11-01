"""Importing from a folder by hand: what lies there, and filing it for a title. The service is
``services/downloads/folder_import.py``."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..meldungen import error_responses
from ..services import tmdb
from ..services.downloads import actions, folder_import, foreign
from .downloads import Download
from .foreign_jobs import AdoptIn, ForeignLibraryMatch, ForeignParsed, ForeignProposal, title_of

router = APIRouter(prefix="/api/folder-import", tags=["downloads"])


class FolderIn(BaseModel):
    path: str = Field(min_length=1, max_length=4096, description="A folder nexcrate sees, from `GET /api/folders`.")


class FolderFile(BaseModel):
    path: str = Field(description="Relative to the folder.")
    size_bytes: int
    kind: str = Field(description="video, audio or archive. Samples and extras are left out.")


class FolderPreview(BaseModel):
    path: str
    name: str = Field(description="The release's name: the folder's, or the only video's.")
    kind: str = Field(description="movie, series or album, a guess from the files and the name.")
    size_bytes: int
    files: list[FolderFile] = Field(description="Sorted by path, at most 200.")
    more_files: int = Field(description="How many files `files` leaves out.")
    parsed: ForeignParsed
    proposals: list[ForeignProposal] = Field(description="Movies of the library the name points to.")
    series_proposals: list[ForeignLibraryMatch] = Field(description="Series of the library the name points to.")


class FolderImportIn(AdoptIn, FolderIn):
    file: str | None = Field(
        default=None,
        max_length=4096,
        description="The video to take of several, as `files` names it; a movie only, in a folder without archives.",
    )


def _http(exc: foreign.ForeignError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)


@router.post(
    "/preview",
    response_model=FolderPreview,
    summary="Look into a folder before importing it",
    description="What a folder holds and what it probably is. Nothing is recorded and nothing on disk changes.",
    responses=error_responses(
        (404, "folder_not_visible"),
        (409, "folder_import_dangerous"),
        (409, "folder_import_empty"),
    ),
)
async def preview_folder(payload: FolderIn) -> FolderPreview:
    try:
        answer = await asyncio.to_thread(folder_import.preview, payload.path)
    except foreign.ForeignError as exc:
        raise _http(exc) from exc
    return FolderPreview.model_validate(answer)


@router.post(
    "",
    response_model=Download,
    status_code=201,
    summary="Import a folder for a movie, a series or an album",
    description=(
        "The folder becomes a download of the version without a client, and the usual import files it: into the "
        "version's folder, with its naming, the old file into the recycle folder. Its files stay where they are, "
        "linked or copied, never moved. A movie of TMDB not in the library is added first; a series and an album must "
        "be in the library. A series is for the episodes the video names say; what the import cannot place asks the "
        "owner as usual."
    ),
    responses=error_responses(
        (404, "not_found"),
        (404, "folder_not_visible"),
        (409, "folder_import_dangerous"),
        (409, "folder_import_empty"),
        (409, "folder_import_in_library"),
        (409, "folder_import_running"),
        (409, "foreign_no_version"),
        (409, "foreign_no_episodes"),
        (409, "version_owned_by_source"),
        (422, "invalid_input"),
        *tmdb.ERRORS,
    ),
)
async def import_folder(payload: FolderImportIn) -> Download:
    title_id = await title_of(payload)
    try:
        download_id = await asyncio.to_thread(
            folder_import.start, payload.path, payload.file, title_id, payload.version_id
        )
    except foreign.ForeignError as exc:
        raise _http(exc) from exc
    answer: dict[str, Any] = await asyncio.to_thread(actions.read, download_id)
    return Download.model_validate(answer)
