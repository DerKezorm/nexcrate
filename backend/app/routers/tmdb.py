"""TMDB: the owner's token and the search for movies to add.

⚠️ The token is stored encrypted and never leaves the backend again, not even partly. Answers
carry ``configured`` instead.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..db import SessionLocal
from ..meldungen import error, error_responses
from ..models import DiskFolder, DiskRoot
from ..services import images, library, tmdb
from ..services.series import tmdb_series

logger = logging.getLogger("nexcrate.tmdb")

router = APIRouter(prefix="/api/tmdb", tags=["tmdb"])

TOKEN_MAX_LENGTH = 4096


class TmdbState(BaseModel):
    configured: bool = Field(description="Whether a token is stored. The token itself is never returned.")
    checked_at: datetime | None = Field(description="When the stored token was tested, UTC.")


class TokenIn(BaseModel):
    token: str = Field(max_length=TOKEN_MAX_LENGTH, description="TMDB's API Read Access Token, not the API key.")


class OnDisk(BaseModel):
    """A folder of the last disk scan that carries this TMDB number (L8)."""

    folder_id: int
    root: str = Field(description="The scanned folder the row lies below.")
    name: str = Field(description="The folder below the root as on disk.")
    state: str = Field(description="The row's state: restorable, proposal, library and so on.")


class TmdbResult(BaseModel):
    tmdb_id: int
    title: str = Field(description="In the account language; in English when TMDB has no translation.")
    original_title: str | None
    year: int | None = Field(description="Year of the primary release date, for a series of the first air date.")
    overview: str | None = Field(description="In the account language; in English when TMDB has none.")
    poster_url: str | None = Field(description="`/api/tmdb/poster/w185/{file}`, or null without a poster.")
    title_id: int | None = Field(description="The title in the library when the movie is there already.")
    version_ids: list[int] = Field(
        description="The version definitions that title already has. Empty when the movie is not in the library."
    )
    on_disk: list[OnDisk] = Field(
        default_factory=list,
        description="Folders of the stored disk scan with this TMDB number (from release.nex, a number in the name or "
        "a .nfo, or an unambiguous proposal). The add dialog offers to assign such a folder instead.",
    )


class TmdbSearchPage(BaseModel):
    page: int
    total_pages: int
    total: int = Field(description="Every match over all pages.")
    results: list[TmdbResult]


def _state() -> TmdbState:
    configured, checked_at = tmdb.status()
    return TmdbState(configured=configured, checked_at=checked_at)


def _known(tmdb_ids: list[int], kind: str = "movie") -> dict[int, tuple[int, list[int]]]:
    with SessionLocal() as db:
        return library.in_library(db, tmdb_ids, kind)


def _on_disk(tmdb_ids: list[int]) -> dict[int, list[OnDisk]]:
    """Stored scan rows by TMDB number, ignored rows left out."""
    if not tmdb_ids:
        return {}
    found: dict[int, list[OnDisk]] = {}
    with SessionLocal() as db:
        rows = db.execute(
            select(DiskFolder.id, DiskFolder.tmdb_id, DiskFolder.relative_path, DiskFolder.state, DiskRoot.path)
            .join(DiskRoot, DiskRoot.id == DiskFolder.root_id)
            .where(DiskFolder.tmdb_id.in_(tmdb_ids), DiskFolder.ignored.is_(False))
            .order_by(DiskFolder.id)
        ).all()
    for folder_id, tmdb_id, relative_path, state, root in rows:
        found.setdefault(tmdb_id, []).append(OnDisk(folder_id=folder_id, root=root, name=relative_path, state=state))
    return found


@router.get(
    "",
    response_model=TmdbState,
    summary="Read the TMDB state",
    description="Whether a TMDB token is stored and when it was tested. The token is never part of the answer.",
)
def read_state() -> TmdbState:
    return _state()


@router.put(
    "",
    response_model=TmdbState,
    summary="Test and store the TMDB token",
    description=(
        "Tests the token with TMDB's `GET /3/configuration` and stores it encrypted when TMDB accepts it. It has "
        "to be the API Read Access Token from themoviedb.org, Settings, API; the API key is refused. A new token "
        "starts with an empty cache."
    ),
    responses=error_responses(*(entry for entry in tmdb.ERRORS if entry[0] != 409)),
)
async def store_token(payload: TokenIn) -> TmdbState:
    token = payload.token.strip()
    if not tmdb.valid_token(token):
        raise error("invalid_input", "The input is not valid.", 422, fields=["token"])
    try:
        await tmdb.check_token(token)
    except tmdb.TmdbError as exc:
        logger.info("TMDB token test failed: %s", exc.code)
        raise exc.http() from exc
    await asyncio.to_thread(tmdb.save_token, token)
    return await asyncio.to_thread(_state)


@router.delete(
    "",
    status_code=204,
    response_model=None,
    summary="Remove the TMDB token",
    description=(
        "Forgets the token. Every TMDB call stops, cached answers and poster files are purged. Titles keep "
        "their data; the refresh waits for a new token."
    ),
)
def remove_token() -> None:
    tmdb.remove_token()


@router.get(
    "/search",
    response_model=TmdbSearchPage,
    summary="Search TMDB for movies or series",
    description=(
        "Searches TMDB's original, translated and alternative titles, in the account language with an English "
        "fallback for empty texts. `year` keeps movies with a release in that year, series first aired in that year. "
        "Titles already in the library carry `title_id` and their version definitions; movies the disk scan found in "
        "a folder carry `on_disk`. Answers are cached for 30 minutes."
    ),
    responses=error_responses(*tmdb.ERRORS),
)
async def search(
    q: Annotated[str, Query(min_length=1, max_length=200, description="Search text.")],
    year: Annotated[int | None, Query(ge=1870, le=2100, description="Only titles of this year.")] = None,
    page: Annotated[int, Query(ge=1, le=500, description="Page, from 1.")] = 1,
    kind: Annotated[Literal["movie", "series"], Query(description="movie or series. Default movie.")] = "movie",
) -> TmdbSearchPage:
    if not q.strip():
        raise error("invalid_input", "The input is not valid.", 422, fields=["q"])
    token = await asyncio.to_thread(tmdb.require_token)
    locale = await asyncio.to_thread(tmdb.account_locale)
    try:
        if kind == "series":
            found = await tmdb_series.search_series(token, q, year=year, page=page, locale=locale)
        else:
            found = await tmdb.search_movies(token, q, year=year, page=page, locale=locale)
    except tmdb.TmdbError as exc:
        logger.info("TMDB search failed: %s", exc.code)
        raise exc.http() from exc
    ids = [result.tmdb_id for result in found.results]
    known = await asyncio.to_thread(_known, ids, kind)
    on_disk = await asyncio.to_thread(_on_disk, ids) if kind == "movie" else {}
    results = []
    for result in found.results:
        title_id, version_ids = known.get(result.tmdb_id, (None, []))
        results.append(
            TmdbResult(
                tmdb_id=result.tmdb_id,
                title=result.title,
                original_title=result.original_title,
                year=result.year,
                overview=result.overview,
                poster_url=f"/api/tmdb/poster/w185/{result.poster_file}" if result.poster_file else None,
                title_id=title_id,
                version_ids=version_ids,
                on_disk=on_disk.get(result.tmdb_id, []),
            )
        )
    return TmdbSearchPage(page=found.page, total_pages=found.total_pages, total=found.total, results=results)


@router.get(
    "/poster/{size}/{file}",
    response_class=FileResponse,
    response_model=None,
    summary="Load a TMDB poster",
    description=(
        "A poster for search results, loaded from TMDB's image server and cached at most 180 days. `size` is "
        "w185 or w500, `file` a TMDB file name such as `abc.jpg`. Only while a TMDB token is stored."
    ),
    responses={
        200: {
            "description": "The image.",
            "content": {
                "image/jpeg": {"schema": {"type": "string", "format": "binary"}},
                "image/png": {"schema": {"type": "string", "format": "binary"}},
            },
        },
        **error_responses((404, "poster_missing")),
    },
)
async def read_poster(size: str, file: str) -> FileResponse:
    found = await tmdb.poster(size, file)
    if found is None:
        raise error("poster_missing", "There is no image for this title.", 404)
    path, media_type = found
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": images.TMDB_CACHE_CONTROL})
