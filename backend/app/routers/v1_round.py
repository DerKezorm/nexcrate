"""``/api/v1``, stage V4: the round things. The calendar, ratings of IMDb (and with the owner's
OMDb key of Rotten Tomatoes and Metacritic), and asking before a request whether it would bring anything.

The six rules of ``v1.py`` hold. Errors are flat.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..db import SessionLocal
from ..deps import DbSession, require_scope
from ..meldungen import error, v1_error_responses
from ..models import ApiKey, Title
from ..services import api_v1, calendar_feed, ratings, release_calendar, tmdb
from ..services.api_v1 import calendar as v1_calendar
from ..services.api_v1 import preview, titles
from .v1 import ReasonOut, TitleOut, _KindBlocks
from .v1_back import WhyOut

router = APIRouter(prefix="/api/v1", tags=["v1"], dependencies=[Depends(require_scope("read"))])

RequestKey = Annotated[ApiKey, Depends(require_scope("request"))]


def _kind(kind: str | None) -> None:
    if kind is not None and kind not in api_v1.KINDS:
        raise error("kind_unsupported", f"This nexcrate does not answer for the kind {kind}.", 422, kind=kind)


# --- The calendar ------------------------------------------------------------------------------------------------ #


class CalendarVersionOut(BaseModel):
    version_id: str | None
    state: str = Field(description="Of the movie, or of that one episode.")
    monitored: bool


class CalendarSeriesBlock(BaseModel):
    season: int = Field(description="TMDB's number.")
    episode: int = Field(description="TMDB's number.")
    name: str | None


class CalendarCreditOut(BaseModel):
    ref: str | None
    name: str | None


class CalendarAlbumBlock(BaseModel):
    artists: list[CalendarCreditOut]
    type: str | None


class CalendarEntryOut(_KindBlocks):
    kind: str
    ref: str
    name: str | None
    year: int | None
    date: str = Field(description="YYYY-MM-DD. TMDB gives no time of day for an episode.", examples=["2026-10-02"])
    date_kind: str = Field(
        description="theatrical, digital or physical for a movie; air for an episode; release for an album."
    )
    country: str | None = Field(
        description="The country of a movie's date when it is not the region's own; null otherwise."
    )
    monitored: bool
    versions: list[CalendarVersionOut]
    series: CalendarSeriesBlock | None = None
    album: CalendarAlbumBlock | None = None


class CalendarOut(BaseModel):
    from_day: str = Field(alias="from")
    to_day: str = Field(alias="to")
    region: str = Field(description="The region the movie dates come from; empty is the earliest date anywhere.")
    items: list[CalendarEntryOut] = Field(description="By day.")
    truncated: bool = Field(description="The span held more entries than one answer carries: ask a shorter one.")

    model_config = {"populate_by_name": True}


@router.get(
    "/calendar",
    response_model=CalendarOut,
    response_model_by_alias=True,
    summary="What comes out in a span of days",
    description=(
        f"Movies with their cinema, digital and disc dates, episodes on the day they air, at most "
        f"{release_calendar.SPAN_DAYS_MAX} days at once. A movie's date is the one of the region (the one set in "
        "nexcrate, or `region`); without a date there, the earliest anywhere, and `country` names it."
    ),
    responses=v1_error_responses((422, "kind_unsupported"), (422, "invalid_input")),
)
def read_calendar(
    db: DbSession,
    from_day: Annotated[str, Query(alias="from", description="YYYY-MM-DD.")],
    to_day: Annotated[str, Query(alias="to", description="YYYY-MM-DD.")],
    kind: Annotated[str | None, Query(max_length=16)] = None,
    region: Annotated[str | None, Query(max_length=2, description="ISO 3166-1; left out, the one set.")] = None,
    monitored: Annotated[bool, Query(description="Only what a version watches.")] = False,
    missing: Annotated[bool, Query(description="Only what has no file yet.")] = False,
) -> CalendarOut:
    _kind(kind)
    start, end = release_calendar.parse_day(from_day), release_calendar.parse_day(to_day)
    if start is None or end is None or end < start:
        raise error("invalid_input", "from and to are days as YYYY-MM-DD, to not before from.", 422,
                    fields=["from", "to"])  # fmt: skip
    span = release_calendar.Span(start=start, end=end)
    if span.days > release_calendar.SPAN_DAYS_MAX:
        raise error("invalid_input", f"The span covers at most {release_calendar.SPAN_DAYS_MAX} days.", 422,
                    fields=["to"])  # fmt: skip
    chosen = calendar_feed.region(db) if region is None else region.strip().upper()
    if chosen and (len(chosen) != 2 or not chosen.isalpha()):
        raise error("invalid_input", "region is a country as two letters.", 422, fields=["region"])
    items, cut = v1_calendar.entries(
        db, span, kind=kind, region=chosen, only_monitored=monitored, only_missing=missing
    )
    return CalendarOut.model_validate(
        {"from": span.first, "to": span.last, "region": chosen, "items": items, "truncated": cut}
    )


# --- Ratings ------------------------------------------------------------------------------------------------------ #


class ImdbRatingOut(BaseModel):
    rating: float = Field(description="IMDb's weighted average, 1 to 10.", examples=[8.7])
    votes: int


class RatingItemIn(BaseModel):
    kind: str = Field(max_length=16)
    ref: str = Field(max_length=64, description="imdb:tt…, or another reference of a title in the library.")


class RatingsIn(BaseModel):
    items: list[RatingItemIn] = Field(max_length=ratings.LOOKUP_MAX)


class RatingItemOut(BaseModel):
    kind: str
    ref: str = Field(description="As asked.")
    imdb_ref: str | None = Field(description="The IMDb number the value belongs to.")
    imdb: ImdbRatingOut | None = Field(description="Null when IMDb has none, or see `imdb` of the answer.")
    rotten_tomatoes: int | None = Field(
        description="The Tomatometer in percent, from nexcrate's 30-day store of OMDb answers; a batch never asks OMDb."
    )
    metacritic: int | None = Field(description="The Metascore, 0 to 100, from the same store.")
    sources: dict[str, str] = Field(
        description="imdb: loaded, off or not_loaded. omdb: ok, not_found, no_key, not_for_kind (series), or "
        "not_cached: not in the store yet; GET /api/v1/ratings/{kind}/{ref} asks OMDb for one title."
    )
    error: str | None = Field(
        description="kind_unsupported, ref_invalid, ref_source_unknown, ref_ambiguous or imdb_unknown (nexcrate "
        "knows no IMDb number for it: ask by imdb:)."
    )


class RatingsOut(BaseModel):
    items: list[RatingItemOut] = Field(description="In the order asked.")
    imdb: str = Field(description="loaded, off (the owner switched it off) or not_loaded (not loaded yet).")
    attribution: str = Field(description="Show it where the ratings show.")
    omdb_attribution: str | None = Field(
        description="Show it too when any Rotten Tomatoes or Metacritic value shows; null when none does."
    )


class RatingOut(BaseModel):
    kind: str
    ref: str
    imdb_ref: str | None
    imdb: ImdbRatingOut | None
    rotten_tomatoes: int | None = Field(description="The Tomatometer in percent.")
    metacritic: int | None = Field(description="The Metascore, 0 to 100.")
    sources: dict[str, str] = Field(
        description="imdb: loaded, off or not_loaded. omdb: ok, not_found, no_key, key_invalid, limit (the day's "
        "requests are used up), failed, or not_for_kind (OMDb has no such values for series)."
    )
    attribution: list[str]


#: A title nexcrate does not have, asked by ``tmdb:``: its IMDb number comes from TMDB (``_resolve_outside``).
OUTSIDE = "outside_library"
#: TMDB is asked for this many titles at a time.
OUTSIDE_AT_ONCE = 5


def _imdb_of(db: DbSession, asked: list[tuple[str, str]]) -> list[tuple[str | None, str | None]]:
    """Per entry its IMDb number, or the code why there is none; ``OUTSIDE`` for a ``tmdb:`` title nexcrate does not
    have."""
    by_library = titles.lookup(db, [(kind, raw) for kind, raw in asked])
    title_ids = [entry.get("title_id") for entry in by_library]
    known = {
        row.id: row.imdb_id
        for row in db.scalars(select(Title).where(Title.id.in_([title_id for title_id in title_ids if title_id])))
    }
    result: list[tuple[str | None, str | None]] = []
    for (kind_asked, raw), entry in zip(asked, by_library, strict=True):
        if kind_asked in api_v1.MUSIC_KINDS:
            # IMDb rates no music.
            result.append((None, "kind_unsupported"))
            continue
        # An IMDb number is its own answer; the library is not asked, so two titles with it are no problem.
        if raw.startswith("imdb:") and entry["error"] in (None, "ref_ambiguous"):
            result.append((raw.partition(":")[2], None))
            continue
        if entry["error"] is not None:
            result.append((None, entry["error"]))
            continue
        if not entry["known"] and raw.startswith("tmdb:"):
            result.append((None, OUTSIDE))
            continue
        imdb_id = known.get(entry.get("title_id") or 0)
        result.append((imdb_id, None) if imdb_id else (None, "imdb_unknown"))
    return result


@router.post(
    "/ratings",
    response_model=RatingsOut,
    summary="IMDb ratings of many titles at once",
    description=(
        f"Up to {ratings.LOOKUP_MAX} titles, also titles that are in no library, from nexcrate's own copy of IMDb's "
        "daily file; Rotten Tomatoes and Metacritic from the 30-day store of OMDb answers. A `tmdb:` title nexcrate "
        "does not have is looked up at TMDB once for its IMDb number (kept 30 days); nothing else leaves. "
        + ratings.ATTRIBUTION
    ),
)
async def ratings_many(payload: RatingsIn) -> RatingsOut:
    asked = [(item.kind, item.ref) for item in payload.items]

    def read() -> tuple[list[tuple[str | None, str | None]], str, bool]:
        with SessionLocal() as db:
            return _imdb_of(db, asked), ratings.imdb_source(db), bool(ratings.omdb_key(db))

    numbers, source, has_key = await asyncio.to_thread(read)
    numbers = await _resolve_outside(asked, numbers)
    values = await asyncio.to_thread(ratings.imdb_many, [number for number, _error in numbers if number])
    stored = await asyncio.to_thread(
        ratings.omdb_cached_many,
        [number for (kind, _raw), (number, _error) in zip(asked, numbers, strict=True) if number and kind == "movie"],
    )
    items = []
    for (kind, raw), (number, problem) in zip(asked, numbers, strict=True):
        found = values.get(number) if number else None
        if kind != "movie":
            omdb = ratings.OmdbValues("not_for_kind")
        elif not has_key:
            omdb = ratings.OmdbValues("no_key")
        else:
            omdb = stored.get(number or "", ratings.OmdbValues("not_cached"))
        items.append(
            RatingItemOut(
                kind=kind,
                ref=raw,
                imdb_ref=f"imdb:{number}" if number else None,
                imdb=ImdbRatingOut(rating=found[0], votes=found[1]) if found else None,
                rotten_tomatoes=omdb.rotten_tomatoes,
                metacritic=omdb.metacritic,
                sources={"imdb": source, "omdb": omdb.state},
                error=problem,
            )
        )
    shown = any(item.rotten_tomatoes is not None or item.metacritic is not None for item in items)
    return RatingsOut(
        items=items,
        imdb=source,
        attribution=ratings.ATTRIBUTION,
        omdb_attribution=ratings.OMDB_CREDIT if shown else None,
    )


async def _resolve_outside(
    asked: list[tuple[str, str]], numbers: list[tuple[str | None, str | None]]
) -> list[tuple[str | None, str | None]]:
    """The IMDb number of each ``tmdb:`` title outside the library from TMDB, a few at a time; ``imdb_unknown`` when
    TMDB has none, no token is stored, or TMDB fails."""
    wanted = {
        (kind, int(raw.partition(":")[2]))
        for (kind, raw), (_number, problem) in zip(asked, numbers, strict=True)
        if problem == OUTSIDE
    }
    if not wanted:
        return numbers
    try:
        token = tmdb.require_token()
    except HTTPException:
        token = None
    found: dict[tuple[str, int], str | None] = {}
    if token is not None:
        gate = asyncio.Semaphore(OUTSIDE_AT_ONCE)

        async def one(entry: tuple[str, int]) -> None:
            async with gate:
                try:
                    found[entry] = await tmdb.imdb_of(token, entry[0], entry[1])
                except tmdb.TmdbError:
                    found[entry] = None

        await asyncio.gather(*(one(entry) for entry in sorted(wanted)))
    result = []
    for (kind, raw), (number, problem) in zip(asked, numbers, strict=True):
        if problem == OUTSIDE:
            resolved = found.get((kind, int(raw.partition(":")[2])))
            result.append((resolved, None) if resolved else (None, "imdb_unknown"))
        else:
            result.append((number, problem))
    return result


@router.get(
    "/ratings/{kind}/{ref}",
    response_model=RatingOut,
    summary="The ratings of one title",
    description=(
        "IMDb from nexcrate's copy; with an OMDb key of the owner also Rotten Tomatoes and Metacritic (movies only), "
        "kept 30 days and within OMDb's daily limit. " + ratings.ATTRIBUTION
    ),
    responses=v1_error_responses(
        (422, "kind_unsupported"),
        (422, "ref_invalid"),
        (422, "ref_source_unknown"),
        (409, "ref_ambiguous"),
        (404, "imdb_unknown"),
    ),
)
async def rating_one(kind: str, ref: str) -> RatingOut:
    _kind(kind)
    if kind in api_v1.MUSIC_KINDS:
        raise error("kind_unsupported", "IMDb rates no music.", 422, kind=kind)

    def read() -> tuple[str | None, str | None, str, str | None]:
        with SessionLocal() as db:
            [(number, problem)] = _imdb_of(db, [(kind, ref)])
            return number, problem, ratings.imdb_source(db), ratings.omdb_key(db)

    number, problem, source, key = await asyncio.to_thread(read)
    [(number, problem)] = await _resolve_outside([(kind, ref)], [(number, problem)])
    if problem is not None and problem != "imdb_unknown":
        codes = {"ref_invalid": 422, "ref_source_unknown": 422, "ref_ambiguous": 409, "kind_unsupported": 422}
        raise error(problem, "This reference cannot be read.", codes.get(problem, 422), kind=kind, ref=ref)
    if number is None:
        raise error("imdb_unknown", "nexcrate knows no IMDb number for this title; ask by imdb:.", 404, ref=ref)
    found = (await asyncio.to_thread(ratings.imdb_many, [number])).get(number)
    if kind == "movie":
        omdb = await ratings.omdb_one(key, number)
    else:
        omdb = ratings.OmdbValues("not_for_kind")
    attribution = [ratings.ATTRIBUTION] + ([ratings.OMDB_CREDIT] if omdb.state == "ok" else [])
    return RatingOut(
        kind=kind,
        ref=ref,
        imdb_ref=f"imdb:{number}",
        imdb=ImdbRatingOut(rating=found[0], votes=found[1]) if found else None,
        rotten_tomatoes=omdb.rotten_tomatoes,
        metacritic=omdb.metacritic,
        sources={"imdb": source, "omdb": omdb.state},
        attribution=attribution,
    )



# --- Asking before a request ------------------------------------------------------------------------------------- #


class PreviewSeriesIn(BaseModel):
    season: int | None = Field(default=None, ge=0, le=10_000, description="Search one season; left out, the series.")


class PreviewIn(BaseModel):
    kind: str = Field(max_length=16)
    ref: str = Field(max_length=64, description="A title nexcrate does not have only by tmdb:.")
    versions: list[Annotated[str, Field(max_length=16)]] | None = Field(
        default=None, max_length=20, description="Fixed version ids; left out, every version of the kind."
    )
    search: bool = Field(
        default=False, description="Also search the indexers, without loading, within their budget."
    )
    series: PreviewSeriesIn | None = None


class DatedOut(BaseModel):
    date: str
    country: str | None = Field(description="Named when the date is not the region's own.")


class MovieDatesOut(BaseModel):
    theatrical: DatedOut | None
    digital: DatedOut | None
    physical: DatedOut | None


class PreviewMovieBlock(BaseModel):
    dates: MovieDatesOut


class PreviewSeriesBlock(BaseModel):
    status: str | None
    first_air_date: str | None
    next_air_date: str | None
    seasons: int = Field(description="Regular seasons TMDB lists.")


class PreviewVersionOut(BaseModel):
    version_id: str | None
    ready: bool
    reasons: list[ReasonOut]


class PreviewSearchOut(BaseModel):
    preview_id: str | None = Field(description="Read it with GET /api/v1/preview/{preview_id}; null when no search.")
    state: str = Field(description="running or not_possible.")
    problem: str | None = Field(
        description="no_indexers, search_not_possible (a series nexcrate does not have yet), or "
        "anime_not_supported (an anime series while `capabilities.anime` is false)."
    )


class PreviewAlbumBlock(BaseModel):
    artists: list[CalendarCreditOut]
    type: str | None
    first_release_date: str | None


class PreviewArtistBlock(BaseModel):
    type: str | None
    country: str | None
    disambiguation: str | None


class PreviewOut(_KindBlocks):
    kind: str
    ref: str = Field(description="As asked.")
    known: bool = Field(description="Whether the title is in the library.")
    name: str | None
    year: int | None
    released: bool | None = Field(description="Whether any date lies in the past; null without a date.")
    title: TitleOut | None = Field(description="The title as the library has it; null when unknown.")
    why: WhyOut | None = Field(description="Why it is not there yet, when known.")
    versions: list[PreviewVersionOut] = Field(description="Whether each version would search and load by itself.")
    search: PreviewSearchOut | None = Field(description="Null when no search was asked for.")
    movie: PreviewMovieBlock | None = None
    series: PreviewSeriesBlock | None = None
    album: PreviewAlbumBlock | None = None
    artist: PreviewArtistBlock | None = None


class PreviewReleaseOut(BaseModel):
    name: str = Field(description="The release name. Never a link.")
    quality: str | None
    size_bytes: int | None
    indexer: str | None
    protocol: str | None
    age_hours: float | None


class EpisodeRefOut(BaseModel):
    season: int
    episode: int


class PreviewTakeOut(BaseModel):
    release: PreviewReleaseOut | None
    fills: list[EpisodeRefOut]
    replaces: list[EpisodeRefOut]


class PreviewSeriesResult(BaseModel):
    takes: list[PreviewTakeOut] = Field(description="The releases nexcrate would take, together.")
    not_found: list[EpisodeRefOut] = Field(description="Episodes no release names.")
    no_fit: list[EpisodeRefOut] = Field(description="Episodes whose releases the profile refuses.")


class PreviewResultVersionOut(_KindBlocks):
    version_id: str | None
    has_profile: bool
    keeps_current: bool = Field(description="Nothing better than the file there.")
    codes: list[str] = Field(description="Why nothing fits, as the release checker's codes.")
    would_take: PreviewReleaseOut | None = Field(description="A movie: the release nexcrate would take.")
    series: PreviewSeriesResult | None = None


class PreviewIndexerOut(BaseModel):
    name: str
    state: str
    error_code: str | None


class PreviewResultOut(BaseModel):
    preview_id: str
    state: str = Field(description="running or done.")
    releases: int = Field(description="Releases of the title found.")
    indexers: list[PreviewIndexerOut]
    versions: list[PreviewResultVersionOut]


@router.post(
    "/preview",
    response_model=PreviewOut,
    summary="Ask before a request",
    description=(
        "Whether the title is there, when it comes out, whether each version would search and load by itself, and "
        "with `search` a search of the indexers without loading (follow it with GET /api/v1/preview/{preview_id}). "
        "A title nexcrate does not have comes from TMDB; a series nexcrate does not have cannot be searched yet."
    ),
    responses=v1_error_responses(
        (422, "kind_unsupported"),
        (422, "ref_invalid"),
        (422, "ref_source_unknown"),
        (409, "ref_ambiguous"),
        (422, "ref_not_addable"),
        (422, "version_unknown"),
        (422, "scope_not_for_kind"),
        (409, "search_busy"),
        *tmdb.ERRORS,
    ),
)
async def ask_preview(payload: PreviewIn, key: RequestKey) -> PreviewOut:
    season = payload.series.season if payload.series is not None else None
    found = await preview.ask(payload.kind, payload.ref, payload.versions, payload.search, season)
    return PreviewOut.model_validate(found)


@router.get(
    "/preview/{preview_id}",
    response_model=PreviewResultOut,
    summary="What a preview's search found",
    description="Per version the release nexcrate would take, or why nothing fits. Kept 30 minutes.",
    responses=v1_error_responses((404, "preview_not_found")),
)
def read_preview(preview_id: str, key: RequestKey) -> PreviewResultOut:
    return PreviewResultOut.model_validate(preview.result(preview_id))
