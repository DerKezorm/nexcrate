"""``/api/v1/discover``: the lists of Discover for a program, with the scope read; "not interested" with request.

The same lists as the interface (``routers/discover.py``, ``services/discover/``): movies and series from TMDB with the
owner's token, albums from ListenBrainz behind the owner's switch. Every entry carries ``kind`` and ``ref`` as
``POST /api/v1/preview`` and ``POST /api/v1/requests`` take them. What the library holds (movies and series by TMDB
number, albums with a version) and what the owner marked "not interested" is left out, as in the interface. The six
rules of ``v1.py`` hold; errors are flat.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field

from ..db import SessionLocal
from ..deps import require_scope
from ..meldungen import error, v1_error_responses
from ..services import tmdb
from ..services.api_v1 import refs
from ..services.api_v1.titles import COVER_URL
from ..services.discover import hidden, listenbrainz, tmdb_lists
from .discover import _albums_left_out, _region_of, _titles_left_out
from .v1 import CreditOut, _KindBlocks

logger = logging.getLogger("nexcrate.api_v1")

router = APIRouter(prefix="/api/v1", tags=["v1"], dependencies=[Depends(require_scope("read"))])

#: The kinds Discover lists, in the order of the interface.
KINDS = ("movie", "series", "album")

#: The lists of each kind: key and a name for a program to show. The keys are those of the interface.
LIST_NAMES: dict[str, dict[str, tuple[str, str]]] = {
    "movie": {
        "fresh": ("Fresh", "Out on digital or disc in the last 120 days, by popularity."),
        "popular": ("Popular", "Out on digital or disc, by popularity."),
        "acclaimed": ("Acclaimed", "Out in the last year, by rating, at least 200 votes."),
        "classics": ("Classics", "Before 1996, by rating, at least 1,000 votes."),
    },
    "series": {
        "new": ("New", "First aired in the last 120 days, by popularity."),
        "popular": ("Popular", "By popularity, at least 100 votes."),
        "ended": ("Ended", "Ended, by rating, at least 300 votes."),
        "classics": ("Classics", "First aired before 2010, by rating, at least 300 votes."),
    },
    "album": {
        "fresh": ("New", "Albums and EPs of the last 60 days among the week's most listened."),
        "trending": ("Trending", "Most listened this week."),
        "classics": ("Classics", "Most listened ever."),
    },
}

#: The filters of each kind, as query parameters of the list. Albums have none.
FILTERS: dict[str, tuple[str, ...]] = {
    "movie": ("region", "genre", "language"),
    "series": ("genre", "language", "country"),
    "album": (),
}

_LIST_ERRORS = (
    (422, "kind_unsupported"),
    (422, "list_unknown"),
    (422, "filter_not_for_kind"),
    (422, "invalid_input"),
    *tmdb.ERRORS,
    *listenbrainz.ERRORS,
)


class DiscoverListOut(BaseModel):
    id: str = Field(description="The key of the list in `GET /api/v1/discover/{kind}/{list}`.")
    name: str = Field(description="English, for a program to show or to translate by `id`.")
    description: str


class DiscoverKindOut(BaseModel):
    kind: str
    available: bool = Field(description="Whether the lists of this kind can be read now.")
    reason: str | None = Field(
        description="Why not: `tmdb_not_configured` (movies and series need the owner's TMDB token) or "
        "`listenbrainz_disabled` (albums need the owner's ListenBrainz switch). Null when available."
    )
    lists: list[DiscoverListOut]
    filters: list[str] = Field(description="The query parameters this kind takes: region, genre, language, country.")


class DiscoverListsOut(BaseModel):
    region: str | None = Field(
        description="The country of the owner's language, as a default for the `region` filter; null when unknown."
    )
    kinds: list[DiscoverKindOut]


class DiscoverAlbumBlock(BaseModel):
    artists: list[CreditOut] = Field(
        description="One credit; its `ref` is null when ListenBrainz names more than one artist or none."
    )
    artist_refs: list[str] = Field(description="`mbid:<artist>` of every artist ListenBrainz names.")
    type: str | None = Field(description="Album or EP of the new albums, otherwise null.")
    first_release_date: str | None = Field(description="Of the new albums the release date, otherwise null.")
    cover_url: str | None = Field(description="The Cover Art Archive's picture; nexcrate hands out none itself.")
    listens: int | None = Field(description="Listens on ListenBrainz in the week, or ever for the classics.")


class DiscoverItemOut(_KindBlocks):
    kind: str
    ref: str = Field(description="`tmdb:<number>`, for an album `mbid:<release group>`: as a request takes it.")
    name: str = Field(description="In the account language when TMDB has it.")
    original_name: str | None = Field(description="The original title; null for albums.")
    year: int | None = Field(description="Of the release date, a series' first air date, an album's release date.")
    overview: str | None = Field(description="Null for albums.")
    poster_path: str | None = Field(description="TMDB's path, as on a title. Null for albums: see `album.cover_url`.")
    rating: float | None = Field(description="TMDB's average vote, 0 to 10; null without votes and for albums.")
    votes: int | None = Field(description="TMDB's number of votes; null for albums.")
    album: DiscoverAlbumBlock | None = None


class DiscoverOut(BaseModel):
    kind: str
    list: str
    items: list[DiscoverItemOut] = Field(description="At most twenty, best first.")
    exhausted: bool | None = Field(
        description="Movies and series: TMDB had no further page, so fewer than twenty means there are no more. "
        "Null for albums."
    )


class GenreOut(BaseModel):
    id: int = Field(description="TMDB's number, for the `genre` filter.")
    name: str = Field(description="In the account language.")


class GenresOut(BaseModel):
    items: list[GenreOut] = Field(description="Sorted by name.")


class HideIn(BaseModel):
    ref: str = Field(max_length=64, description="`tmdb:<number>` for a movie or series, `mbid:<release group>`.")


class HiddenResetOut(BaseModel):
    removed: int = Field(description="How many entries there were, of every kind.")


def _kind(kind: str) -> None:
    if kind not in KINDS:
        raise error("kind_unsupported", f"This nexcrate does not discover the kind {kind}.", 422, kind=kind)


def _year(date: str | None) -> int | None:
    return int(date[:4]) if date and date[:4].isdigit() else None


def _album_switch() -> bool:
    with SessionLocal() as db:
        return listenbrainz.enabled(db)


def _lists() -> DiscoverListsOut:
    configured, _checked = tmdb.status()
    on = _album_switch()
    kinds = []
    for kind in KINDS:
        available = on if kind == "album" else configured
        reason = None if available else ("listenbrainz_disabled" if kind == "album" else "tmdb_not_configured")
        kinds.append(
            DiscoverKindOut(
                kind=kind,
                available=available,
                reason=reason,
                lists=[
                    DiscoverListOut(id=key, name=name, description=text)
                    for key, (name, text) in LIST_NAMES[kind].items()
                ],
                filters=list(FILTERS[kind]),
            )
        )
    return DiscoverListsOut(region=_region_of(tmdb.account_locale()), kinds=kinds)


def _code(value: str | None, field: str) -> str | None:
    try:
        return tmdb_lists.clean_code(value)
    except ValueError:
        raise error("invalid_input", "The input is not valid.", 422, fields=[field]) from None


async def _titles(kind: str, name: str, filters: tmdb_lists.Filters) -> DiscoverOut:
    token = await asyncio.to_thread(tmdb.require_token)
    locale = await asyncio.to_thread(tmdb.account_locale)
    which: tmdb_lists.Kind = "series" if kind == "series" else "movie"

    async def left_out(ids: list[int]) -> set[int]:
        return await asyncio.to_thread(_titles_left_out, which, ids)

    try:
        found, exhausted = await tmdb_lists.fill(token, which, name, filters, locale, left_out)
    except tmdb.TmdbError as exc:
        logger.info("TMDB discover %s %s failed: %s", kind, name, exc.code)
        raise exc.http() from exc
    items = [
        DiscoverItemOut(
            kind=kind,
            ref=f"tmdb:{item.tmdb_id}",
            name=item.title,
            original_name=item.original_title,
            year=item.year,
            overview=item.overview,
            poster_path=f"/{item.poster_file}" if item.poster_file else None,
            rating=item.rating,
            votes=item.votes,
        )
        for item in found
    ]
    return DiscoverOut(kind=kind, list=name, items=items, exhausted=exhausted)


def _credit(album: listenbrainz.Album) -> list[dict[str, Any]]:
    ref = f"mbid:{album.artist_mbids[0]}" if len(album.artist_mbids) == 1 else None
    return [{"ref": ref, "name": album.artist or None}]


async def _albums(name: str) -> DiscoverOut:
    if not await asyncio.to_thread(_album_switch):
        raise listenbrainz.disabled_error().http()
    try:
        found = await listenbrainz.candidates(name)
    except listenbrainz.ListenBrainzError as exc:
        logger.info("ListenBrainz list %s failed: %s", name, exc.code)
        raise exc.http() from exc
    left_out = await asyncio.to_thread(_albums_left_out, [album.mbid for album in found])
    items = [
        DiscoverItemOut(
            kind="album",
            ref=f"mbid:{album.mbid}",
            name=album.title,
            original_name=None,
            year=_year(album.release_date),
            overview=None,
            poster_path=None,
            rating=None,
            votes=None,
            album=DiscoverAlbumBlock.model_validate(
                {
                    "artists": _credit(album),
                    "artist_refs": [f"mbid:{mbid}" for mbid in album.artist_mbids],
                    "type": album.primary_type,
                    "first_release_date": album.release_date,
                    "cover_url": COVER_URL.format(mbid=album.mbid),
                    "listens": album.listens,
                }
            ),
        )
        for album in listenbrainz.choose(found, left_out)
    ]
    return DiscoverOut(kind="album", list=name, items=items, exhausted=None)


@router.get(
    "/discover/lists",
    response_model=DiscoverListsOut,
    summary="Which Discover lists there are",
    description=(
        "The lists of each kind, the filters each takes, and whether the kind can be read now: movies and series "
        "need the owner's TMDB token, albums the owner's ListenBrainz switch. A kind that cannot be read says why "
        "instead of failing. Since 15.10.2026."
    ),
    responses=v1_error_responses((422, "kind_unsupported")),
)
async def read_lists(
    kind: Annotated[str | None, Query(max_length=16, description="movie, series or album; all without it.")] = None,
) -> DiscoverListsOut:
    if kind is not None:
        _kind(kind)
    found = await asyncio.to_thread(_lists)
    if kind is not None:
        found.kinds = [entry for entry in found.kinds if entry.kind == kind]
    return found


@router.get(
    "/discover/genres",
    response_model=GenresOut,
    summary="List TMDB's genres",
    description=(
        "The genres of movies or series in the account language, sorted by name, for the `genre` filter. Cached 30 "
        "days. Since 15.10.2026."
    ),
    responses=v1_error_responses((422, "kind_unsupported"), *tmdb.ERRORS),
)
async def read_genres(
    kind: Annotated[str, Query(max_length=16, description="movie or series.")],
) -> GenresOut:
    if kind not in ("movie", "series"):
        raise error("kind_unsupported", f"Only movies and series have genres, not {kind}.", 422, kind=kind)
    token = await asyncio.to_thread(tmdb.require_token)
    locale = await asyncio.to_thread(tmdb.account_locale)
    try:
        found = await tmdb_lists.genres(token, "series" if kind == "series" else "movie", locale)
    except tmdb.TmdbError as exc:
        raise exc.http() from exc
    return GenresOut(items=[GenreOut(id=genre_id, name=name) for genre_id, name in found])


@router.get(
    "/discover/{kind}/{list_name}",
    response_model=DiscoverOut,
    summary="Read a Discover list",
    description=(
        "Up to twenty movies, series or albums the library lacks. Left out is what the library holds and what the "
        "owner marked not interested. Each entry names itself as POST /api/v1/preview and POST /api/v1/requests take "
        "it. The lists and their filters are in GET /api/v1/discover/lists. Movies take `region`, `genre` and "
        "`language`, series `genre`, `language` and `country`, albums no filter. Pages are kept six hours. Since "
        "15.10.2026."
    ),
    responses=v1_error_responses(*_LIST_ERRORS),
)
async def read_list(
    kind: str,
    list_name: str,
    region: Annotated[str | None, Query(max_length=2, description="ISO 3166-1 country, such as DE.")] = None,
    genre: Annotated[int | None, Query(ge=1, le=1_000_000, description="A TMDB genre, see /discover/genres.")] = None,
    language: Annotated[str | None, Query(max_length=2, description="ISO 639-1 original language, such as en.")] = None,
    country: Annotated[str | None, Query(max_length=2, description="ISO 3166-1 country of origin.")] = None,
) -> DiscoverOut:
    _kind(kind)
    if list_name not in LIST_NAMES[kind]:
        raise error("list_unknown", f"The kind {kind} has no list {list_name}.", 422, kind=kind, list=list_name)
    asked = {"region": region, "genre": genre, "language": language, "country": country}
    for filter_, value in asked.items():
        if value is not None and filter_ not in FILTERS[kind]:
            raise error("filter_not_for_kind", f"The kind {kind} takes no filter {filter_}.", 422, kind=kind,
                        filter=filter_)  # fmt: skip
    if kind == "album":
        return await _albums(list_name)
    filters = tmdb_lists.Filters(
        region=_code(region, "region"),
        genre=genre,
        language=_code(language, "language"),
        country=_code(country, "country"),
    )
    return await _titles(kind, list_name, filters)


@router.post(
    "/discover/{kind}/hidden",
    status_code=204,
    response_model=None,
    summary="Mark a title not interested",
    description=(
        "Discover never lists it again, for the owner's interface too. Each kind keeps the newest 5,000 entries; "
        "hiding twice keeps one. Needs the scope request. Since 15.10.2026."
    ),
    dependencies=[Depends(require_scope("request"))],
    responses=v1_error_responses(
        (422, "kind_unsupported"), (422, "ref_invalid"), (422, "ref_source_unknown"), (422, "ref_not_addable")
    ),
)
def hide(kind: str, payload: HideIn) -> Response:
    _kind(kind)
    try:
        ref = refs.parse(kind, payload.ref.strip())
    except refs.RefError as problem:
        raise error(problem.code, "The reference is not valid for this kind.", 422, kind=kind) from None
    if ref.source != ("mbid" if kind == "album" else "tmdb"):
        raise error("ref_not_addable", "Discover names a movie or series by its TMDB number.", 422, kind=kind)
    if kind != "album" and int(ref.value) >= 2**31:
        raise error("ref_invalid", "The reference is not valid for this kind.", 422, kind=kind)
    with SessionLocal() as db:
        hidden.hide(db, "album" if kind == "album" else ("series" if kind == "series" else "movie"), ref.value)
        db.commit()
    return Response(status_code=204)


@router.delete(
    "/discover/hidden",
    response_model=HiddenResetOut,
    summary="Show every hidden title again",
    description="Empties the not interested list of every kind, as in the interface. Needs the scope request. Since "
    "15.10.2026.",
    dependencies=[Depends(require_scope("request"))],
)
def reset_hidden() -> HiddenResetOut:
    with SessionLocal() as db:
        removed = hidden.reset(db)
        db.commit()
    logger.info("Discover: %d hidden titles shown again", removed)
    return HiddenResetOut(removed=removed)
