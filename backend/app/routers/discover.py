"""Discover: lists of movies, series and albums the library lacks, for owners without nexview in front.

Every list holds at most twenty entries. Left out is what the library holds (movies and series by TMDB number, albums
by release group with a version) and what the owner marked "not interested". Movies are only what is out already on
digital or disc; see ``services/discover/tmdb_lists.py``. Albums come from ListenBrainz behind a switch of their own.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..db import SessionLocal
from ..meldungen import error, error_responses
from ..models import Title, Version
from ..services import tmdb
from ..services.discover import hidden, listenbrainz, tmdb_lists
from ..services.music import covers
from ..services.music import musicbrainz as mb

logger = logging.getLogger("nexcrate.discover")

router = APIRouter(prefix="/api/discover", tags=["discover"])


class HiddenCounts(BaseModel):
    movie: int
    series: int
    album: int


class DiscoverState(BaseModel):
    tmdb_configured: bool = Field(description="Whether a TMDB token is stored; movies and series need it.")
    listenbrainz_enabled: bool = Field(description="The switch for ListenBrainz; albums need it. Off from the factory.")
    region: str | None = Field(description="The country of the account language, as a default for the region filter.")
    hidden: HiddenCounts = Field(description="How many titles of each kind are marked not interested.")


class ListenBrainzIn(BaseModel):
    enabled: bool


class DiscoverTitle(BaseModel):
    tmdb_id: int
    title: str = Field(description="In the account language when TMDB has it.")
    original_title: str | None
    year: int | None = Field(description="Year of the release date, for a series of the first air date.")
    overview: str | None
    poster_url: str | None = Field(description="`/api/tmdb/poster/w185/{file}`, or null without a poster.")
    rating: float | None = Field(description="TMDB's average vote, 0 to 10, null without votes.")
    votes: int
    title_id: None = Field(default=None, description="Always null: a title in the library is never listed.")
    version_ids: list[int] = Field(default_factory=list, description="Always empty, like `title_id`.")
    on_disk: list[int] = Field(default_factory=list, description="Always empty: Discover does not read the disk scan.")


class DiscoverTitles(BaseModel):
    list: str
    items: list[DiscoverTitle]
    exhausted: bool = Field(description="TMDB had no further page; fewer than twenty then means there are no more.")


class DiscoverAlbum(BaseModel):
    mbid: str = Field(description="The release group.")
    title: str
    artist: str
    artist_mbids: list[str]
    first_release_date: str | None = Field(description="For new albums the release date, otherwise null.")
    primary_type: str | None = Field(description="Album or EP for new albums, otherwise null.")
    secondary_types: list[str] = Field(default_factory=list)
    listens: int | None = Field(description="Listens on ListenBrainz in the week, or ever for the classics.")
    cover_url: str | None = Field(description="`/api/discover/cover/{release}`, or null when no release is known.")
    score: None = None
    in_library: None = Field(default=None, description="Always null: an album with a version is never listed.")


class DiscoverAlbums(BaseModel):
    list: str
    items: list[DiscoverAlbum]


class Genre(BaseModel):
    id: int
    name: str


class HideIn(BaseModel):
    kind: Literal["movie", "series", "album"]
    key: str = Field(min_length=1, max_length=36, description="The TMDB number, for an album the release group id.")


class HiddenReset(BaseModel):
    removed: int


# --- Helpers ------------------------------------------------------------------------------------------------- #


def _region_of(locale: str) -> str | None:
    part = locale.split("-")[-1] if "-" in locale else ""
    return part.upper() if len(part) == 2 and part.isalpha() else None


def _state() -> DiscoverState:
    configured, _checked = tmdb.status()
    with SessionLocal() as db:
        on = listenbrainz.enabled(db)
        counts = hidden.counts(db)
    return DiscoverState(
        tmdb_configured=configured,
        listenbrainz_enabled=on,
        region=_region_of(tmdb.account_locale()),
        hidden=HiddenCounts(**counts),
    )


def _titles_left_out(kind: Literal["movie", "series"], tmdb_ids: list[int]) -> set[int]:
    """In the library (any title of the kind with the number) or hidden."""
    if not tmdb_ids:
        return set()
    with SessionLocal() as db:
        owned = set(db.scalars(select(Title.tmdb_id).where(Title.kind == kind, Title.tmdb_id.in_(tmdb_ids))).all())
        gone = {int(key) for key in hidden.keys(db, kind) if key.isdigit()}
    return {number for number in tmdb_ids if number in owned or number in gone}


def _albums_left_out(mbids: list[str]) -> set[str]:
    """Albums with a version in the library, or hidden. An album of an artist's catalogue without a version is still
    listed: nexcrate knows it, but does not have it."""
    found: set[str] = set()
    with SessionLocal() as db:
        for start in range(0, len(mbids), 500):
            part = mbids[start : start + 500]
            found.update(
                db.scalars(
                    select(Title.mbid)
                    .join(Version, Version.title_id == Title.id)
                    .where(Title.kind == "album", Title.mbid.in_(part))
                ).all()
            )
        found.update(hidden.keys(db, "album"))
    return found


def _clean_code(value: str | None, field: str) -> str | None:
    try:
        return tmdb_lists.clean_code(value)
    except ValueError:
        raise error("invalid_input", "The input is not valid.", 422, fields=[field]) from None


async def _titles(kind: Literal["movie", "series"], name: str, filters: tmdb_lists.Filters) -> DiscoverTitles:
    if name not in tmdb_lists.LISTS[kind]:
        raise error("invalid_input", "The input is not valid.", 422, fields=["list"])
    token = await asyncio.to_thread(tmdb.require_token)
    locale = await asyncio.to_thread(tmdb.account_locale)

    async def left_out(ids: list[int]) -> set[int]:
        return await asyncio.to_thread(_titles_left_out, kind, ids)

    try:
        items, exhausted = await tmdb_lists.fill(token, kind, name, filters, locale, left_out)
    except tmdb.TmdbError as exc:
        logger.info("TMDB discover %s %s failed: %s", kind, name, exc.code)
        raise exc.http() from exc
    return DiscoverTitles(
        list=name,
        exhausted=exhausted,
        items=[
            DiscoverTitle(
                tmdb_id=item.tmdb_id,
                title=item.title,
                original_title=item.original_title,
                year=item.year,
                overview=item.overview,
                poster_url=f"/api/tmdb/poster/w185/{item.poster_file}" if item.poster_file else None,
                rating=item.rating,
                votes=item.votes,
            )
            for item in items
        ],
    )


# --- Routes -------------------------------------------------------------------------------------------------- #


@router.get(
    "",
    response_model=DiscoverState,
    summary="Read what Discover needs",
    description="Whether TMDB and ListenBrainz can be asked, the region of the account language and the hidden counts.",
)
async def read_state() -> DiscoverState:
    return await asyncio.to_thread(_state)


@router.put(
    "/listenbrainz",
    response_model=DiscoverState,
    summary="Switch ListenBrainz on or off",
    description="ListenBrainz feeds the album lists. Off, no request goes out and the kept answers are forgotten.",
)
def switch_listenbrainz(payload: ListenBrainzIn) -> DiscoverState:
    with SessionLocal() as db:
        listenbrainz.save_enabled(db, payload.enabled)
        db.commit()
    logger.info("ListenBrainz switched %s", "on" if payload.enabled else "off")
    return _state()


@router.get(
    "/movies",
    response_model=DiscoverTitles,
    summary="Discover movies",
    description=(
        "Up to twenty movies the library lacks. `fresh`: out on digital or disc in the last 120 days, by popularity. "
        "`popular`: out on digital or disc, by popularity. `acclaimed`: out in the last year, by rating, at least 200 "
        "votes. `classics`: before 1996, by rating, at least 1,000 votes. `region` keeps releases in that country "
        "(not for classics), `genre` a TMDB genre, `language` the original language. Pages are cached six hours."
    ),
    responses=error_responses((422, "invalid_input"), *tmdb.ERRORS),
)
async def discover_movies(
    list_: Annotated[str, Query(alias="list", max_length=16, description="fresh, popular, acclaimed or classics.")],
    region: Annotated[str | None, Query(max_length=2, description="ISO 3166-1 country, such as DE.")] = None,
    genre: Annotated[int | None, Query(ge=1, le=1_000_000, description="A TMDB movie genre.")] = None,
    language: Annotated[str | None, Query(max_length=2, description="ISO 639-1 original language, such as en.")] = None,
) -> DiscoverTitles:
    filters = tmdb_lists.Filters(
        region=_clean_code(region, "region"), genre=genre, language=_clean_code(language, "language")
    )
    return await _titles("movie", list_, filters)


@router.get(
    "/series",
    response_model=DiscoverTitles,
    summary="Discover series",
    description=(
        "Up to twenty series the library lacks; news, reality, soap and talk shows left out. `new`: first aired in "
        "the last 120 days, by popularity. `popular`: by popularity, at least 100 votes. `ended`: ended, by rating, at "
        "least 300 votes. `classics`: first aired before 2010, by rating, at least 300 votes. `country` keeps series "
        "from that country of origin. Pages are cached six hours."
    ),
    responses=error_responses((422, "invalid_input"), *tmdb.ERRORS),
)
async def discover_series(
    list_: Annotated[str, Query(alias="list", max_length=16, description="new, popular, ended or classics.")],
    genre: Annotated[int | None, Query(ge=1, le=1_000_000, description="A TMDB series genre.")] = None,
    language: Annotated[str | None, Query(max_length=2, description="ISO 639-1 original language.")] = None,
    country: Annotated[str | None, Query(max_length=2, description="ISO 3166-1 country of origin.")] = None,
) -> DiscoverTitles:
    filters = tmdb_lists.Filters(
        genre=genre, language=_clean_code(language, "language"), country=_clean_code(country, "country")
    )
    return await _titles("series", list_, filters)


@router.get(
    "/genres",
    response_model=list[Genre],
    summary="List TMDB's genres",
    description="The genres of movies or series in the account language, sorted by name. Cached 30 days.",
    responses=error_responses(*tmdb.ERRORS),
)
async def list_genres(
    kind: Annotated[Literal["movie", "series"], Query(description="movie or series.")] = "movie",
) -> list[Genre]:
    token = await asyncio.to_thread(tmdb.require_token)
    locale = await asyncio.to_thread(tmdb.account_locale)
    try:
        found = await tmdb_lists.genres(token, kind, locale)
    except tmdb.TmdbError as exc:
        raise exc.http() from exc
    return [Genre(id=genre_id, name=name) for genre_id, name in found]


@router.get(
    "/albums",
    response_model=DiscoverAlbums,
    summary="Discover albums",
    description=(
        "Up to twenty albums the library lacks, from ListenBrainz. `fresh`: albums and EPs of the last 60 days among "
        "the week's most listened. `trending`: most listened this week. `classics`: most listened ever. Needs the "
        "ListenBrainz switch; lists are kept six hours."
    ),
    responses=error_responses((422, "invalid_input"), *listenbrainz.ERRORS),
)
async def discover_albums(
    list_: Annotated[str, Query(alias="list", max_length=16, description="fresh, trending or classics.")],
) -> DiscoverAlbums:
    if list_ not in listenbrainz.LISTS:
        raise error("invalid_input", "The input is not valid.", 422, fields=["list"])

    def switched_on() -> bool:
        with SessionLocal() as db:
            return listenbrainz.enabled(db)

    if not await asyncio.to_thread(switched_on):
        raise listenbrainz.disabled_error().http()
    try:
        albums = await listenbrainz.candidates(list_)
    except listenbrainz.ListenBrainzError as exc:
        logger.info("ListenBrainz list %s failed: %s", list_, exc.code)
        raise exc.http() from exc
    left_out = await asyncio.to_thread(_albums_left_out, [album.mbid for album in albums])
    return DiscoverAlbums(
        list=list_,
        items=[
            DiscoverAlbum(
                mbid=album.mbid,
                title=album.title,
                artist=album.artist,
                artist_mbids=list(album.artist_mbids),
                first_release_date=album.release_date,
                primary_type=album.primary_type,
                listens=album.listens,
                cover_url=f"/api/discover/cover/{album.release_mbid}" if album.release_mbid else None,
            )
            for album in listenbrainz.choose(albums, left_out)
        ],
    )


@router.post(
    "/hidden",
    status_code=204,
    response_model=None,
    summary="Mark a title not interested",
    description="Discover never lists it again. Each kind keeps the newest 5,000 entries.",
    responses=error_responses((422, "invalid_input")),
)
def hide(payload: HideIn) -> Response:
    key = payload.key.strip().lower()
    valid = mb.valid_mbid(key) if payload.kind == "album" else key.isdigit() and 0 < int(key) < 2**31
    if not valid:
        raise error("invalid_input", "The input is not valid.", 422, fields=["key"])
    with SessionLocal() as db:
        hidden.hide(db, payload.kind, str(int(key)) if payload.kind != "album" else key)
        db.commit()
    return Response(status_code=204)


@router.delete(
    "/hidden",
    response_model=HiddenReset,
    summary="Show every hidden title again",
    description="Empties the not interested list of every kind.",
)
def reset_hidden() -> HiddenReset:
    with SessionLocal() as db:
        removed = hidden.reset(db)
        db.commit()
    logger.info("Discover: %d hidden titles shown again", removed)
    return HiddenReset(removed=removed)


@router.get(
    "/cover/{release_mbid}",
    response_class=FileResponse,
    response_model=None,
    summary="Load an album cover for Discover",
    description=(
        "The front cover of a release from the Cover Art Archive, kept on disk. Only while the Cover Art Archive "
        "switch is on, otherwise only covers already on disk."
    ),
    responses={
        200: {
            "description": "The image.",
            "content": {"image/jpeg": {"schema": {"type": "string", "format": "binary"}}},
        },
        **error_responses((404, "poster_missing")),
    },
)
async def read_cover(release_mbid: str) -> FileResponse:
    found = await covers.release_cover(release_mbid)
    if found is None:
        raise error("poster_missing", "There is no image for this title.", 404)
    path, media_type = found
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": covers.CACHE_CONTROL})
