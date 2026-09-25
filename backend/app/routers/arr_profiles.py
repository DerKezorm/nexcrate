"""Take the quality setup of a Radarr or Sonarr over (E5).

Two steps, as everywhere else in nexcrate: read and show what would happen, then do it. Reading writes nothing,
and nothing is ever written into the other app.

⚠️ Lidarr is refused here: music has its own, simpler profile, so a Lidarr profile has
nothing to become.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, NamedTuple

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from .. import crypto
from ..db import SessionLocal
from ..deps import DbSession
from ..meldungen import error, error_responses
from ..models import CustomFormat, Profile, utcnow
from ..services import trash
from ..services.profiles import arr_import, building
from ..services.radarr import RadarrClient, RadarrError, SourceUrlInvalid
from ..services.sonarr import SonarrClient
from .sources import _stored

logger = logging.getLogger("nexcrate.profiles")

router = APIRouter(tags=["profiles"])

TABLES = {"movie": building.MOVIE_QUALITIES, "series": building.SERIES_QUALITIES}


# --- Models ------------------------------------------------------------------------------------ #


class ArrFormatOut(BaseModel):
    name: str
    conditions: int = Field(description="How many conditions it brings along.")
    exists: bool = Field(description="A format of this name is already here; it stays as it is.")
    unknown_types: list[str] = Field(description="Condition types nexcrate has no place for.")
    from_release_profile: bool = Field(
        default=False, description="Built from one of Sonarr's release profiles, not read as a format there."
    )


class ArrReleaseProfileOut(BaseModel):
    name: str
    enabled: bool = Field(description="Switched on there. One that is off does nothing there and becomes nothing here.")
    formats: list[str] = Field(description="The custom formats it becomes, by name.")
    bound: list[str] = Field(
        description=(
            "What it is bound to there and cannot be bound to here: `indexer`, `tags`. Its formats then come "
            "without a score."
        )
    )
    unreadable_terms: list[str] = Field(description="Terms that are no pattern nexcrate can read; they are left out.")


class ArrRepackOfferOut(BaseModel):
    name: str
    score: int = Field(description="The score TRaSH gives it.")


class ArrProfileOut(BaseModel):
    name: str
    taken: bool = Field(description="A profile of this name is already here; this one would be left out.")
    qualities: int = Field(description="How many qualities it takes.")
    cutoff: str | None
    min_score: int
    upgrade_until: int
    upgrades_allowed: bool
    scored_formats: int = Field(description="How many formats it gives a score.")
    notes: list[str] = Field(
        description=(
            "What it loses on the way: qualities_unknown, language_unknown, nothing_allowed; and repack_unscored "
            "when none of its formats scores a repack or proper, so it would never take one as an upgrade."
        )
    )


class ArrSetupOut(BaseModel):
    source_id: int
    app: str
    kind: str
    profiles: list[ArrProfileOut]
    formats: list[ArrFormatOut]
    sizes: int = Field(description="How many qualities bring a size of their own.")
    unknown_qualities: list[str] = Field(description="Qualities of the other app this kind does not know.")
    release_profiles: list[ArrReleaseProfileOut] = Field(
        default_factory=list,
        description="Sonarr's release profiles and the custom formats they become; every profile scores them -10000.",
    )
    repack_offer: list[ArrRepackOfferOut] = Field(
        default_factory=list,
        description="TRaSH's repack formats, offered while a profile carries `repack_unscored`. Empty otherwise.",
    )
    repacks_upgrade_there: bool = Field(
        default=False,
        description=(
            "The other app takes a proper or a repack as an upgrade by itself (its default setting). Then taking "
            "the offer keeps what it does; otherwise nothing is lost without it."
        ),
    )


class ArrImportIn(BaseModel):
    profiles: list[str] = Field(
        default_factory=list, max_length=200, description="The profiles to take, by name. Empty takes none."
    )
    add_repack: bool = Field(
        default=False,
        description="Give every chosen profile that scores no repack TRaSH's repack formats with TRaSH's scores.",
    )


class ArrImportOut(BaseModel):
    formats: int
    sizes: int
    profiles: int


# --- Reading ------------------------------------------------------------------------------------ #


def _source(source_id: int) -> Any:
    stored = _stored(source_id)
    if stored is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    if stored.app not in arr_import.KIND_OF_APP:
        raise error(
            "source_app_unsupported",
            "Quality profiles can be taken from Radarr and Sonarr only; music has a profile of its own.",
            409,
        )
    return stored


class Setup(NamedTuple):
    """What is read of the other app, as it comes."""

    profiles: list[Any]
    formats: list[Any]
    definitions: list[Any]
    #: Sonarr only; Radarr 5 has none.
    release_profiles: list[Any]
    #: ``downloadPropersAndRepacks`` of the media management settings.
    propers_and_repacks: str | None


async def _read(source_id: int, stored: Any) -> Setup:
    key = await asyncio.to_thread(crypto.decrypt, stored.api_key)
    if not key:
        raise error(
            "source_key_missing", "The stored API key of this source cannot be read. Please enter it again.", 422
        )
    try:
        if stored.app == "sonarr":
            async with SonarrClient(stored.url, key) as sonarr:
                return Setup(
                    *await sonarr.quality_setup(), await sonarr.release_profiles(), await sonarr.propers_and_repacks()
                )
        async with RadarrClient(stored.url, key) as radarr:
            return Setup(*await radarr.quality_setup(), [], await radarr.propers_and_repacks())
    except RadarrError as exc:
        logger.info("Reading the quality setup of source %d failed: %s", source_id, exc.code)
        raise exc.http() from exc
    except SourceUrlInvalid as exc:
        raise error(
            "source_url_invalid",
            "This address does not work. It has to start with http:// or https:// and must not contain "
            "a user name or password.",
            422,
        ) from exc


def _repack_formats(kind: str) -> list[arr_import.RepackOffer]:
    """TRaSH's repack formats of the state in use, with the score TRaSH gives them by default."""
    by_name = {entry.get("name"): entry for entry in trash.current(kind).data["custom_formats"].values()}
    found: list[arr_import.RepackOffer] = []
    for name in arr_import.REPACK_NAMES:
        entry = by_name.get(name)
        score = (entry or {}).get("trash_scores", {}).get("default")
        if entry is None or not isinstance(score, int) or score <= 0:
            continue
        found.append(
            arr_import.RepackOffer(
                name=name,
                score=score,
                trash_id=entry.get("trash_id"),
                specifications=[
                    {key: spec.get(key) for key in ("name", "implementation", "negate", "required", "fields")}
                    for spec in entry.get("specifications") or []
                ],
            )
        )
    return found


def _plan(db: OrmSession, kind: str, raw: Setup) -> arr_import.Plan:
    return arr_import.plan(
        kind=kind,
        profiles=raw.profiles,
        formats=raw.formats,
        definitions=raw.definitions,
        known_qualities=set(TABLES[kind].weights),
        known_profile_names=set(db.scalars(select(Profile.name).where(Profile.kind == kind))),
        known_formats={
            row.name: list(row.specifications or [])
            for row in db.scalars(select(CustomFormat).where(CustomFormat.kind == kind))
        },
        release_profiles=raw.release_profiles,
        repack_formats=_repack_formats(kind),
        propers_and_repacks=raw.propers_and_repacks,
    )


def _out(source_id: int, stored: Any, kind: str, built: arr_import.Plan) -> ArrSetupOut:
    return ArrSetupOut(
        source_id=source_id,
        app=stored.app,
        kind=kind,
        profiles=[
            ArrProfileOut(
                name=planned.name,
                taken=planned.taken,
                qualities=sum(len(entry.get("items") or [entry.get("name")]) for entry in planned.qualities),
                cutoff=planned.cutoff,
                min_score=planned.min_score,
                upgrade_until=planned.upgrade_until,
                upgrades_allowed=planned.upgrades_allowed,
                scored_formats=len(planned.scores),
                notes=list(planned.notes),
            )
            for planned in built.profiles
        ],
        formats=[
            ArrFormatOut(
                name=planned.name,
                conditions=len(planned.specifications),
                exists=planned.exists,
                unknown_types=list(planned.unknown_types),
                from_release_profile=planned.from_release_profile,
            )
            for planned in built.formats
        ],
        sizes=len(built.sizes),
        unknown_qualities=list(built.unknown_qualities),
        release_profiles=[
            ArrReleaseProfileOut(
                name=planned.name,
                enabled=planned.enabled,
                formats=list(planned.formats),
                bound=list(planned.bound),
                unreadable_terms=list(planned.unreadable_terms),
            )
            for planned in built.release_profiles
        ],
        repack_offer=[ArrRepackOfferOut(name=offer.name, score=offer.score) for offer in built.repack_offer],
        repacks_upgrade_there=built.repacks_upgrade_there,
    )


@router.get(
    "/api/sources/{source_id}/quality-setup",
    response_model=ArrSetupOut,
    summary="Read the quality setup of a connection",
    description=(
        "The quality profiles, the custom formats and the quality definitions of a Radarr or Sonarr, and what "
        "taking them over would make here. Writes nothing, here or there."
    ),
    responses=error_responses(
        (404, "not_found"),
        (409, "source_app_unsupported"),
        (422, "source_key_missing"),
        (422, "source_url_invalid"),
        (502, "radarr_unreachable"),
        (502, "radarr_key_rejected"),
    ),
)
async def read_setup(source_id: int) -> ArrSetupOut:
    stored = await asyncio.to_thread(_source, source_id)
    kind = arr_import.KIND_OF_APP[stored.app]
    raw = await _read(source_id, stored)

    def build() -> ArrSetupOut:
        with SessionLocal() as db:
            return _out(source_id, stored, kind, _plan(db, kind, raw))

    return await asyncio.to_thread(build)


@router.post(
    "/api/sources/{source_id}/quality-setup",
    response_model=ArrImportOut,
    summary="Take the quality setup of a connection over",
    description=(
        "Creates the custom formats that are missing, writes the sizes of the qualities and makes a profile of "
        "its own for every chosen name. A profile whose name is already here is left out. A release profile of "
        "Sonarr becomes custom formats that every chosen profile scores minus 10000."
    ),
    responses=error_responses(
        (404, "not_found"),
        (409, "source_app_unsupported"),
        (422, "source_key_missing"),
        (422, "source_url_invalid"),
        (502, "radarr_unreachable"),
        (502, "radarr_key_rejected"),
    ),
)
async def take_setup(source_id: int, payload: ArrImportIn, db: DbSession) -> ArrImportOut:
    stored = await asyncio.to_thread(_source, source_id)
    kind = arr_import.KIND_OF_APP[stored.app]
    raw = await _read(source_id, stored)

    def write() -> ArrImportOut:
        with SessionLocal() as session:
            built = _plan(session, kind, raw)
            counts = arr_import.apply(session, built, set(payload.profiles), utcnow(), payload.add_repack)
            session.commit()
            return ArrImportOut(**counts)

    result = await asyncio.to_thread(write)
    logger.info(
        "Source %d handed over %d formats, %d sizes and %d profiles",
        source_id,
        result.formats,
        result.sizes,
        result.profiles,
    )
    return result
