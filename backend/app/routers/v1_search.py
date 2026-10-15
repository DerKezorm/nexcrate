"""``/api/v1/search``: finding a title to request by its name.

TMDB for movies and series with the owner's token, MusicBrainz for artists and albums. Every hit carries ``kind`` and
``ref`` as ``POST /api/v1/preview`` and ``POST /api/v1/requests`` take them, and whether the library has it. Searching
needs the scope read; requesting stays with request. The six rules of ``v1.py`` hold; errors are flat.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..db import SessionLocal
from ..deps import require_scope
from ..meldungen import error, v1_error_responses
from ..services import tmdb
from ..services.api_v1 import titles
from ..services.music import musicbrainz as mb
from ..services.series import tmdb_series
from .v1 import _KindBlocks

logger = logging.getLogger("nexcrate.api_v1")

router = APIRouter(prefix="/api/v1", tags=["v1"], dependencies=[Depends(require_scope("read"))])

#: The kinds that can be searched by name.
SEARCH_KINDS = ("movie", "series", "artist", "album")
QUERY_MAX = 200


class SearchCreditOut(BaseModel):
    ref: str | None = Field(description="`mbid:<artist>`; null for a credit without an id.")
    name: str | None


class SearchArtistBlock(BaseModel):
    type: str | None = Field(description="Person, Group, Orchestra and the like, as MusicBrainz says.")
    country: str | None
    disambiguation: str | None


class SearchAlbumBlock(BaseModel):
    artists: list[SearchCreditOut]
    type: str | None = Field(description="MusicBrainz's primary type: Album, EP, Single, Broadcast, Other.")
    first_release_date: str | None


class SearchHitOut(_KindBlocks):
    kind: str
    ref: str = Field(description="`tmdb:<number>`, for an artist or album `mbid:<id>`: as a request takes it.")
    name: str
    original_name: str | None = Field(description="The original title; null for music.")
    year: int | None
    overview: str | None = Field(description="In the account language, in English when TMDB has none; null for music.")
    poster_path: str | None = Field(description="TMDB's path, as on a title. nexcrate hands out no pictures here.")
    known: bool = Field(description="Whether the title is in the library.")
    artist: SearchArtistBlock | None = None
    album: SearchAlbumBlock | None = None


class SearchOut(BaseModel):
    items: list[SearchHitOut] = Field(description="In the order of the source: TMDB's first page, MusicBrainz's ten.")


def _known(kind: str, refs: list[str]) -> set[str]:
    with SessionLocal() as db:
        found = titles.lookup(db, [(kind, ref) for ref in refs])
    return {entry["ref"] for entry in found if entry.get("known")}


async def _tmdb_hits(kind: str, query: str, year: int | None) -> list[dict[str, Any]]:
    token = await asyncio.to_thread(tmdb.require_token)
    locale = await asyncio.to_thread(tmdb.account_locale)
    try:
        if kind == "series":
            found = await tmdb_series.search_series(token, query, year=year, page=1, locale=locale)
        else:
            found = await tmdb.search_movies(token, query, year=year, page=1, locale=locale)
    except tmdb.TmdbError as exc:
        logger.info("TMDB search for a program failed: %s", exc.code)
        raise exc.http() from exc
    return [
        {
            "kind": kind,
            "ref": f"tmdb:{result.tmdb_id}",
            "name": result.title,
            "original_name": result.original_title,
            "year": result.year,
            "overview": result.overview,
            "poster_path": f"/{result.poster_file}" if result.poster_file else None,
        }
        for result in found.results
    ]


def _year(date: str | None) -> int | None:
    return int(date[:4]) if date and date[:4].isdigit() else None


async def _music_hits(kind: str, query: str) -> list[dict[str, Any]]:
    try:
        if kind == "artist":
            artists = await mb.search_artists(query, mb.OWNER)
            return [
                {
                    "kind": "artist",
                    "ref": f"mbid:{artist.mbid}",
                    "name": artist.name,
                    "original_name": None,
                    "year": artist.begin_year,
                    "overview": None,
                    "poster_path": None,
                    "artist": {"type": artist.artist_type, "country": artist.country,
                               "disambiguation": artist.disambiguation},
                }
                for artist in artists
            ]
        groups = await mb.search_release_groups(query, None, "album", mb.OWNER)
    except mb.MusicBrainzError as exc:
        raise exc.http() from exc
    hits = []
    for group in groups:
        credits = [{"ref": f"mbid:{c['mbid']}" if c.get("mbid") else None, "name": c.get("name")} for c in group.credit]
        hits.append(
            {
                "kind": "album",
                "ref": f"mbid:{group.mbid}",
                "name": group.title,
                "original_name": None,
                "year": _year(group.first_release_date),
                "overview": None,
                "poster_path": None,
                "album": {
                    "artists": credits,
                    "type": group.primary_type,
                    "first_release_date": group.first_release_date,
                },
            }
        )
    return hits


@router.get(
    "/search",
    response_model=SearchOut,
    summary="Search a title by its name",
    description=(
        "Movies and series at TMDB with nexcrate's token (the first page, in the account language), artists and "
        "albums at MusicBrainz (up to ten). Each hit names itself as POST /api/v1/preview and POST /api/v1/requests "
        "take it, and says whether the library has it. Since 15.10.2026."
    ),
    responses=v1_error_responses(
        (422, "kind_unsupported"), (422, "invalid_input"), *tmdb.ERRORS, *mb.ERRORS
    ),
)
async def search(
    kind: Annotated[str, Query(max_length=16, description="movie, series, artist or album.")],
    q: Annotated[str, Query(max_length=QUERY_MAX, description="The name, or part of it.")],
    year: Annotated[int | None, Query(ge=1870, le=2100, description="Movies and series only: of this year.")] = None,
) -> SearchOut:
    if kind not in SEARCH_KINDS:
        raise error("kind_unsupported", f"This nexcrate does not search for the kind {kind}.", 422, kind=kind)
    query = q.strip()
    if not query:
        raise error("invalid_input", "The input is not valid.", 422, fields=["q"])
    hits = await (_tmdb_hits(kind, query, year) if kind in ("movie", "series") else _music_hits(kind, query))
    known = await asyncio.to_thread(_known, kind, [hit["ref"] for hit in hits])
    return SearchOut(items=[SearchHitOut.model_validate({**hit, "known": hit["ref"] in known}) for hit in hits])
