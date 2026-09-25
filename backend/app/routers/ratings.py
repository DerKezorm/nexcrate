"""Ratings in the interface (N39): switching IMDb's daily file on and off, loading it now, the
owner's OMDb key, and the ratings of one title for its page.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .. import crypto
from .. import db as database
from ..db import SessionLocal
from ..deps import DbSession
from ..meldungen import error, error_responses
from ..models import Title
from ..services import ratings

router = APIRouter(prefix="/api/ratings", tags=["ratings"])


class ImdbStateOut(BaseModel):
    loaded_at: datetime | None = Field(description="When the file was last loaded.")
    checked_at: datetime | None = Field(description="When IMDb was last asked.")
    rows: int | None = Field(description="Titles in the loaded file.")
    problem: str | None = Field(
        description="imdb_unreachable, imdb_file_broken, imdb_write_failed or imdb_http_<status>; null when fine."
    )


class OmdbStateOut(BaseModel):
    has_key: bool
    today: int = Field(description="Requests sent to OMDb today (UTC).")
    limit: int = Field(description="nexcrate stops at this many a day.")


class RatingsSettingsOut(BaseModel):
    imdb_enabled: bool
    imdb: ImdbStateOut
    omdb: OmdbStateOut
    attribution: str
    omdb_credit: str


class RatingsSettingsIn(BaseModel):
    imdb_enabled: bool


class OmdbKeyIn(BaseModel):
    key: str = Field(min_length=1, max_length=64)


class ImdbValueOut(BaseModel):
    rating: float
    votes: int


class TitleRatingsOut(BaseModel):
    imdb: ImdbValueOut | None
    rotten_tomatoes: int | None
    metacritic: int | None
    sources: dict[str, str] = Field(description="imdb: loaded, off, not_loaded. omdb: as in /api/v1/ratings.")


def _time(value: object) -> datetime | None:
    return datetime.fromisoformat(value) if isinstance(value, str) else None


def _out(db: DbSession) -> RatingsSettingsOut:
    state = ratings.imdb_state(db)
    return RatingsSettingsOut(
        imdb_enabled=ratings.imdb_enabled(db),
        imdb=ImdbStateOut(
            loaded_at=_time(state.get("loaded_at")) if ratings.imdb_path().is_file() else None,
            checked_at=_time(state.get("checked_at")),
            rows=state.get("rows") if isinstance(state.get("rows"), int) and ratings.imdb_path().is_file() else None,
            problem=state.get("problem") if isinstance(state.get("problem"), str) else None,
        ),
        omdb=OmdbStateOut(
            has_key=ratings.omdb_key(db) is not None, today=ratings.omdb_today(), limit=ratings.OMDB_DAY_LIMIT
        ),
        attribution=ratings.ATTRIBUTION,
        omdb_credit=ratings.OMDB_CREDIT,
    )


@router.get(
    "/settings",
    response_model=RatingsSettingsOut,
    summary="Where the ratings stand",
    description="Whether IMDb's file is loaded, when, and whether an OMDb key is stored. Never the key.",
)
def read_settings(db: DbSession) -> RatingsSettingsOut:
    return _out(db)


@router.put(
    "/settings",
    response_model=RatingsSettingsOut,
    summary="Switch IMDb's file on or off",
    description="Off, nexcrate never reaches IMDb; the values loaded stay until it is switched on again.",
)
def change_settings(payload: RatingsSettingsIn, db: DbSession) -> RatingsSettingsOut:
    database.set_setting(db, ratings.SETTING_IMDB, "on" if payload.imdb_enabled else "off")
    db.commit()
    return _out(db)


@router.post(
    "/imdb/refresh",
    response_model=RatingsSettingsOut,
    summary="Load IMDb's file now",
    description="Asks IMDb at once, when switched on; an unchanged file is not loaded again.",
)
async def refresh_now() -> RatingsSettingsOut:
    await ratings.refresh(force=True)
    with SessionLocal() as db:
        return _out(db)


@router.put(
    "/omdb-key",
    response_model=RatingsSettingsOut,
    summary="Store an OMDb key",
    description="The key is checked with one request to OMDb before it is stored, encrypted.",
    responses=error_responses((422, "omdb_key_invalid"), (502, "omdb_unreachable"), (429, "omdb_limit")),
)
async def store_key(payload: OmdbKeyIn) -> RatingsSettingsOut:
    key = payload.key.strip()
    checked = await ratings.check_key(key)
    if checked == "key_invalid":
        raise error("omdb_key_invalid", "OMDb does not know this key.", 422)
    if checked == "limit":
        raise error("omdb_limit", "OMDb says this key used up its requests for today.", 429)
    if checked != "ok":
        raise error("omdb_unreachable", "OMDb did not answer; the key is not stored.", 502)
    with SessionLocal() as db:
        database.set_setting(db, ratings.SETTING_OMDB_KEY, crypto.encrypt(key))
        db.commit()
        return _out(db)


@router.delete(
    "/omdb-key",
    response_model=RatingsSettingsOut,
    summary="Remove the OMDb key",
    description="Rotten Tomatoes and Metacritic are asked no more; what is kept stays for its 30 days.",
)
def remove_key(db: DbSession) -> RatingsSettingsOut:
    database.set_setting(db, ratings.SETTING_OMDB_KEY, "")
    db.commit()
    return _out(db)


@router.get(
    "/title/{title_id}",
    response_model=TitleRatingsOut,
    summary="The ratings of one title",
    description="IMDb from the local copy; with an OMDb key, Rotten Tomatoes and Metacritic of a movie.",
    responses=error_responses((404, "not_found")),
)
async def title_ratings(title_id: int) -> TitleRatingsOut:
    with SessionLocal() as db:
        title = db.get(Title, title_id)
        if title is None:
            raise error("not_found", "This does not exist, or not any more.", 404)
        number, kind, source, key = title.imdb_id, title.kind, ratings.imdb_source(db), ratings.omdb_key(db)
    if not number:
        sources = {"imdb": source, "omdb": "none"}
        return TitleRatingsOut(imdb=None, rotten_tomatoes=None, metacritic=None, sources=sources)
    found = ratings.imdb_many([number]).get(number)
    omdb = await ratings.omdb_one(key, number) if kind == "movie" else ratings.OmdbValues("not_for_kind")
    return TitleRatingsOut(
        imdb=ImdbValueOut(rating=found[0], votes=found[1]) if found else None,
        rotten_tomatoes=omdb.rotten_tomatoes,
        metacritic=omdb.metacritic,
        sources={"imdb": source, "omdb": omdb.state},
    )
