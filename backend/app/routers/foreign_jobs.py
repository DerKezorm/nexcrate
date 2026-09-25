"""Jobs in nexcrate's category that no download follows (the owner's finding 2 of 22.09.2026): list, assign and import,
remove. The service is ``services/downloads/foreign.py``."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field, model_validator

from ..meldungen import error_responses
from ..services import tmdb
from ..services.downloads import actions, foreign
from .downloads import Download, DownloadClientRef

logger = logging.getLogger("nexcrate.downloads")

router = APIRouter(prefix="/api/foreign-jobs", tags=["downloads"])


class ForeignParsed(BaseModel):
    title: str | None = Field(description="The movie title as the job's name spells it; null when it has none.")
    year: int | None


class ForeignProposal(BaseModel):
    tmdb_id: int
    imdb_id: str | None = None
    title: str
    year: int | None = None
    poster_file: str | None = None
    poster_url: str | None = None
    from_: str = Field(alias="from", description="library: a title of the library shares a spelling and the year.")
    title_id: int | None = None
    unambiguous: bool = False

    model_config = {"populate_by_name": True}


class ForeignLibraryMatch(BaseModel):
    title_id: int
    title: str
    year: int | None = None
    kind: str


class ForeignJobOut(BaseModel):
    id: int
    client: DownloadClientRef | None = Field(description="Null when the client was deleted since.")
    name: str = Field(description="The client's name of the job, as it gives it.")
    state: str = Field(description="queued, downloading, paused, completed, failed or problem, as the client says.")
    progress: float | None = Field(description="0 to 100.")
    size_bytes: int | None
    first_seen_at: datetime
    kind: str = Field(description="movie, or series when the name carries a season or episode numbering. A guess.")
    parsed: ForeignParsed
    proposals: list[ForeignProposal] = Field(
        description="Movies of the library the name points to, at most 5, the likeliest first."
    )
    series_proposals: list[ForeignLibraryMatch] = Field(
        default_factory=list, description="Series of the library the name points to, when it reads as a series."
    )


class ForeignJobsOut(BaseModel):
    items: list[ForeignJobOut]


class AdoptIn(BaseModel):
    title_id: int | None = Field(default=None, description="A movie of the library.")
    tmdb_id: int | None = Field(default=None, description="Or a movie of TMDB; added to the library when it is not.")
    version_id: int = Field(description="The version definition the job is filed into.")

    @model_validator(mode="after")
    def _one_movie(self) -> AdoptIn:
        if (self.title_id is None) == (self.tmdb_id is None):
            raise ValueError("title_id or tmdb_id, exactly one")
        return self


def _http(exc: foreign.ForeignError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)


@router.get(
    "",
    response_model=ForeignJobsOut,
    summary="Jobs in nexcrate's category that no download follows",
    description=(
        "Put there by hand, by another program, or handed over without an answer and never matched. nexcrate never "
        "imports them by itself; the owner assigns one to a movie or removes it. Read from the clients once a minute."
    ),
)
def list_foreign() -> ForeignJobsOut:
    return ForeignJobsOut.model_validate({"items": foreign.listing()})


async def _movie(tmdb_id: int) -> tmdb.MovieData:
    token = await asyncio.to_thread(tmdb.require_token)
    locale = await asyncio.to_thread(tmdb.account_locale)
    try:
        return await tmdb.fetch_movie(token, tmdb_id, locale)
    except tmdb.TmdbError as exc:
        logger.info("Assigning a foreign job stopped at TMDB: %s", exc.code)
        raise exc.http() from exc


@router.post(
    "/{job_id}/import",
    response_model=Download,
    status_code=201,
    summary="Assign a foreign job to a movie, a series or an album and import it",
    description=(
        "The job becomes a download of the version, as if nexcrate had loaded it: a finished one is filed away at "
        "once, one still loading is followed. A movie of TMDB not in the library is added first; a series and an "
        "album must be in the library. A series job is for the episodes its name names (a season pack for its "
        "seasons, no numbering for every season); what the import cannot place asks the owner as usual."
    ),
    responses=error_responses(
        (404, "not_found"),
        (409, "foreign_job_failed"),
        (409, "foreign_same_release"),
        (409, "foreign_no_version"),
        (409, "foreign_no_episodes"),
        (409, "version_owned_by_source"),
        (422, "invalid_input"),
        *tmdb.ERRORS,
    ),
)
async def adopt_foreign(job_id: int, payload: AdoptIn) -> Download:
    title_id = payload.title_id
    if title_id is None and payload.tmdb_id is not None:
        known = await asyncio.to_thread(foreign.title_of_tmdb, payload.tmdb_id, None)
        if known is None:
            data = await _movie(payload.tmdb_id)
            known = await asyncio.to_thread(foreign.title_of_tmdb, payload.tmdb_id, data)
        title_id = known
    try:
        download_id = await asyncio.to_thread(foreign.adopt, job_id, int(title_id or 0), payload.version_id)
    except foreign.ForeignError as exc:
        raise _http(exc) from exc
    answer: dict[str, Any] = await asyncio.to_thread(actions.read, download_id)
    return Download.model_validate(answer)


@router.delete(
    "/{job_id}",
    status_code=204,
    summary="Remove a foreign job with its files",
    description="The job leaves the download client, with its files; nothing else is touched.",
    responses=error_responses((404, "not_found"), (502, "client_unreachable")),
)
async def remove_foreign(job_id: int) -> Response:
    try:
        await foreign.remove(job_id)
    except foreign.ForeignError as exc:
        raise _http(exc) from exc
    return Response(status_code=204)
