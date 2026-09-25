"""Profiles by hand: the sizes of the qualities, the custom formats, and the profile
of a version as the owner sets it.

Three places, as in Radarr: ``/api/quality`` holds what a quality may weigh, ``/api/custom-formats`` the formats,
and ``/api/versions/{id}/profile/expert`` the profile that orders the qualities and gives the formats their scores.
What every judgement reads stays the rules of the profile; the server builds them on every save.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ..deps import DbSession
from ..meldungen import error, error_responses
from ..models import CustomFormat, Profile, QualitySize, VersionDefinition, utcnow
from ..services import indexers as indexer_service
from ..services import judging
from ..services import trash as trash_service
from ..services.profiles import ProfileBuildError, building
from ..services.profiles import expert as profile_expert
from ..services.profiles import store as profile_store
from ..services.releases import decision as movie_decision
from ..services.releases import formats as format_engine
from ..services.releases import languages as lang
from ..services.releases import qualities as q
from ..services.releases import qualities_series as qs
from ..services.releases import series_decision

logger = logging.getLogger("nexcrate.profiles")

router = APIRouter(tags=["profiles"])

Kind = Literal["movie", "series"]
#: The patterns of a test are compiled under a name of their own and dropped again, whatever was typed.
TEST_COMMIT = "format-test"
TABLES = {"movie": building.MOVIE_QUALITIES, "series": building.SERIES_QUALITIES}


# --- Models ------------------------------------------------------------------------------------ #


class QualitySizeOut(BaseModel):
    quality: str = Field(examples=["Bluray-1080p"])
    min_mb_per_min: float = Field(description="What a file of this quality must weigh at least, per minute.")
    max_mb_per_min: float | None = Field(description="Its upper limit; null means no limit, as in Radarr.")
    preferred_mb_per_min: float | None = Field(
        description=(
            "Radarr's preferred size: between releases equal in everything else the one nearest to it is taken. "
            "Null: the larger one is taken."
        )
    )


class QualityListOut(BaseModel):
    kind: str
    items: list[QualitySizeOut] = Field(description="Every quality of this kind, from the weakest to the best.")


class QualitySizeIn(BaseModel):
    quality: str = Field(max_length=64)
    min_mb_per_min: float = Field(ge=0, le=10_000)
    max_mb_per_min: float | None = Field(default=None, ge=0, le=10_000)
    preferred_mb_per_min: float | None = Field(default=None, ge=0, le=10_000)


class QualityListIn(BaseModel):
    kind: Kind = "movie"
    items: list[QualitySizeIn] = Field(max_length=200)


class CustomFormatOut(BaseModel):
    id: int
    kind: str
    name: str
    origin: str = Field(description="trash came with the guides, own belongs to you.")
    trash_id: str | None
    specifications: list[dict[str, Any]] = Field(description="Radarr's shape, as the engine reads it.")
    used_by: list[str] = Field(description="The labels of the versions whose profile points at it.")
    updated_at: datetime


class CustomFormatIn(BaseModel):
    kind: Kind = "movie"
    name: str = Field(min_length=1, max_length=200)
    specifications: list[dict[str, Any]] = Field(default_factory=list, max_length=100)


class ConditionOption(BaseModel):
    value: int = Field(description="What goes into the field; the engine compares this number.")
    label: str = Field(description="The name the engine knows it by, in English, as Radarr writes it.")


class ConditionOut(BaseModel):
    implementation: str = Field(examples=["ReleaseTitleSpecification"])
    value: Literal["regex", "choice", "range"] = Field(
        description="What the field holds: a pattern, one of the options, or a from and a to."
    )
    options: list[ConditionOption] = Field(default_factory=list, description="For choice; empty otherwise.")
    unit: str | None = Field(default=None, description="For range: gb or year.")
    except_language: bool = Field(default=False, description="Only the language condition has the second switch.")


class ConditionListOut(BaseModel):
    kind: str
    items: list[ConditionOut]


class FormatTestIn(BaseModel):
    kind: Kind = "movie"
    name: str = Field(min_length=1, max_length=500, description="A release name, as an indexer would list it.")
    specifications: list[dict[str, Any]] = Field(max_length=100, description="As the editor holds them, saved or not.")
    original_language: str | None = Field(
        default=None, max_length=8, description="ISO 639-1 of the title's original language; unknown without it."
    )
    size_bytes: int | None = Field(default=None, ge=0, le=2**53, description="For a size condition; else unknown.")
    indexer_flags: int = Field(
        default=0, ge=0, le=2**31, description="For an indexer flag condition, in the numbers of the kind."
    )


class FormatTestCondition(BaseModel):
    index: int = Field(description="The place of the specification in the list that was sent.")
    implementation: str
    result: bool = Field(description="After negate: what the rule counts.")
    required: bool
    problem: str | None = Field(
        default=None, description="pattern_invalid when a pattern does not compile; it then counts as no match."
    )


class FormatTestGroup(BaseModel):
    implementation: str
    matches: bool = Field(description="One of them matched and no required one failed.")


class FormatTestOut(BaseModel):
    matches: bool = Field(description="Every group matches. A format without specifications matches everything.")
    conditions: list[FormatTestCondition]
    groups: list[FormatTestGroup]
    parsed: dict[str, Any] = Field(description="What the name was read as: quality, source, group, languages.")


class CustomFormatPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    specifications: list[dict[str, Any]] | None = Field(default=None, max_length=100)


class ExpertFormat(BaseModel):
    format_id: int
    score: int = Field(ge=-100_000, le=100_000)


class ExpertSetIn(BaseModel):
    """One set of rules by hand: the profile itself, or the anime branch of a series profile."""

    qualities: list[dict[str, Any]] = Field(max_length=200, description="The list in its order, as the rules hold it.")
    cutoff: Any = Field(default=None, description="Where upgrading stops, as the rules hold it: a name or a group.")
    upgrades_allowed: bool = True
    min_score: int = Field(default=0, ge=-100_000, le=100_000)
    upgrade_until: int = Field(default=0, ge=-100_000, le=100_000)
    min_upgrade_step: int = Field(default=1, ge=0, le=100_000)
    target_resolution: int | None = Field(default=None, ge=0, le=4320)
    languages: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    required_languages: str = Field(default="all", max_length=16)
    formats: list[ExpertFormat] = Field(default_factory=list, max_length=500)


class ExpertIn(ExpertSetIn):
    anime: ExpertSetIn | None = Field(
        default=None,
        description=(
            "Series only (B2): the rules an anime series of this version is judged by. Left "
            "out or null, an anime series is judged by the profile itself, as every other series."
        ),
    )


class ExpertOut(BaseModel):
    version_id: int | None = Field(default=None, description="The version it was read for, if it was one.")
    profile_id: int
    name: str
    kind: str
    mode: str = Field(description="wizard or expert. Saving here switches it to expert.")
    expert: dict[str, Any] = Field(description="What the owner set by hand, or what the assistant left behind.")
    qualities_available: list[str] = Field(description="Every quality of this kind, from the weakest to the best.")
    formats_available: list[CustomFormatOut]
    languages_available: list[str] = Field(
        description="What a release can be required to carry: `original` and every ISO 639-1 code the engine knows."
    )


class AnimeFromTrashIn(BaseModel):
    family: Literal["standard", "german"] | None = Field(
        default=None,
        description=(
            "Which of TRaSH's anime profiles: standard is [Anime] Remux-1080p, german is [German] Anime HD Bluray + "
            "WEB. Left out, German among the languages of the profile picks german."
        ),
    )


class AnimeFromTrashOut(BaseModel):
    anime: dict[str, Any] = Field(description="The anime branch as the expert mode holds it. Not saved yet.")
    trash_profile: str = Field(description="The TRaSH profile it was filled from.")
    formats_created: int = Field(description="Formats the table lacked and that were created for it.")
    formats_available: list[CustomFormatOut] = Field(description="Every format of the kind, the new ones included.")


# --- Helpers ----------------------------------------------------------------------------------- #


def _profile_of(db: OrmSession, version_id: int) -> Profile:
    """The profile a version judges by. Kept for the route below the version."""
    definition = db.get(VersionDefinition, version_id)
    if definition is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    if definition.kind not in TABLES:
        raise error("profile_kind_unsupported", "Only movies and series are kept by hand here.", 409)
    profile = profile_store.of_version(db, version_id)
    if profile is None:
        raise error("profile_missing", "This version has no profile yet. The assistant makes one.", 409)
    return profile


def _profile_by_id(db: OrmSession, profile_id: int) -> Profile:
    profile = db.get(Profile, profile_id)
    if profile is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    if profile.kind not in TABLES:
        raise error("profile_kind_unsupported", "Only movies and series are kept by hand here.", 409)
    return profile


def _start(db: OrmSession, profile: Profile) -> dict[str, Any]:
    """What the expert mode starts from: what is stored, or an empty profile's bare list of qualities.

    ⚠️ An empty profile has nothing stored. Guessing what it should take would be worse than showing every
    quality of its kind unticked, so that is what it gets; the owner ticks and the save refuses a list that
    takes nothing.
    """
    anime = profile_expert.anime_start(db, profile)
    if isinstance(profile.expert, dict) and profile.expert.get("qualities"):
        return {**profile.expert, **({"anime": anime} if anime is not None else {})}
    return {
        "qualities": [{"name": name, "allowed": False} for name in _quality_names(profile.kind)],
        "cutoff": None,
        "upgrades_allowed": True,
        "min_score": 0,
        "upgrade_until": 0,
        "min_upgrade_step": 1,
        "target_resolution": None,
        "languages": [],
        "required_languages": "all",
        "formats": [],
    }


def _expert_out(db: OrmSession, profile: Profile, version_id: int | None) -> ExpertOut:
    used = _used_by(db, profile.kind)
    rows = db.scalars(select(CustomFormat).where(CustomFormat.kind == profile.kind).order_by(CustomFormat.name))
    return ExpertOut(
        version_id=version_id,
        profile_id=profile.id,
        name=profile.name,
        kind=profile.kind,
        mode=profile.mode,
        expert=_start(db, profile),
        qualities_available=_quality_names(profile.kind),
        formats_available=[_format_out(row, used) for row in rows],
        languages_available=_language_codes(),
    )


def _language_codes() -> list[str]:
    """``original`` first, then every code once, in Radarr's order of its languages."""
    return ["original", *dict.fromkeys(language.iso for language in lang.LANGUAGES if language.iso)]


def _checked_languages(languages: list[dict[str, Any]], required: str) -> None:
    """A language the judgement cannot read would quietly demand nothing, or something no release ever has."""
    known = set(_language_codes())
    for entry in languages:
        if entry.get("code") not in known or entry.get("role") not in building.LANGUAGE_ROLES:
            raise error("invalid_input", "The input is not valid.", 422, fields=["languages"])
    if required not in ("all", "any"):
        raise error("invalid_input", "The input is not valid.", 422, fields=["required_languages"])


def _checked_set(db: OrmSession, kind: str, payload: ExpertSetIn, *, field: str) -> dict[str, Any]:
    """One set of rules, checked as far as the judgement needs it, as the expert mode stores it."""
    _checked_qualities(kind, payload.qualities, payload.cutoff)
    _checked_languages(payload.languages, payload.required_languages)
    known = profile_expert.formats_of(db, kind)
    for entry in payload.formats:
        if entry.format_id not in known:
            raise error("invalid_input", "The input is not valid.", 422, fields=[field])
    return {
        **{name: getattr(payload, name) for name in profile_expert.FIELDS},
        "formats": [{"format_id": entry.format_id, "score": entry.score} for entry in payload.formats],
    }


def _save_expert(db: OrmSession, profile: Profile, payload: ExpertIn) -> None:
    stored = _checked_set(db, profile.kind, payload, field="formats")
    if payload.anime is not None:
        if profile.kind != "series":
            raise error("invalid_input", "The input is not valid.", 422, fields=["anime"])
        stored["anime"] = _checked_set(db, profile.kind, payload.anime, field="anime.formats")
    moment = utcnow()
    profile.expert = stored
    profile.mode = "expert"
    profile.rules = profile_expert.compile_rules(db, profile)
    profile.updated_at = moment
    db.commit()
    logger.info("Profile %d is kept by hand now", profile.id)


def _quality_names(kind: str) -> list[str]:
    table = TABLES[kind]
    return [name for name, _weight in sorted(table.weights.items(), key=lambda entry: entry[1])]


def _used_by(db: OrmSession, kind: str) -> dict[int, list[str]]:
    used: dict[int, list[str]] = {}
    rows = db.execute(
        select(Profile, VersionDefinition.label)
        .join(VersionDefinition, VersionDefinition.profile_id == Profile.id)
        .where(Profile.kind == kind)
    ).tuples()
    for profile, label in rows:
        expert = profile.expert if isinstance(profile.expert, dict) else {}
        for entry in expert.get("formats") or []:
            used.setdefault(int(entry.get("format_id", 0)), []).append(label)
    return used


def _format_out(row: CustomFormat, used: dict[int, list[str]]) -> CustomFormatOut:
    return CustomFormatOut(
        id=row.id,
        kind=row.kind,
        name=row.name,
        origin=row.origin,
        trash_id=row.trash_id,
        specifications=list(row.specifications or []),
        used_by=sorted(set(used.get(row.id, []))),
        updated_at=row.updated_at,
    )


#: The release types Sonarr knows, by number, as ``formats.py`` compares them.
RELEASE_TYPES = ((1, "Single Episode"), (2, "Multi Episode"), (3, "Season Pack"))


def _sources(kind: str) -> list[ConditionOption]:
    names = q.SOURCES if kind == "movie" else qs.SOURCES
    return [ConditionOption(value=number, label=name) for number, name in enumerate(names)]


def _resolutions(kind: str) -> list[ConditionOption]:
    table = q.QUALITIES if kind == "movie" else qs.QUALITIES
    seen = sorted({quality.resolution for quality in table if quality.resolution})
    return [ConditionOption(value=number, label=f"{number}p") for number in seen]


def _conditions(kind: str) -> list[ConditionOut]:
    """The same order as the dialog shows them: the two patterns first, then what is fixed, then the ranges."""
    items = [
        ConditionOut(implementation="ReleaseTitleSpecification", value="regex"),
        ConditionOut(implementation="ReleaseGroupSpecification", value="regex"),
    ]
    if kind == "movie":
        items.append(ConditionOut(implementation="EditionSpecification", value="regex"))
    items.append(ConditionOut(implementation="SourceSpecification", value="choice", options=_sources(kind)))
    items.append(ConditionOut(implementation="ResolutionSpecification", value="choice", options=_resolutions(kind)))
    if kind == "movie":
        modifiers = [ConditionOption(value=number, label=name) for number, name in enumerate(q.MODIFIERS)]
        items.append(ConditionOut(implementation="QualityModifierSpecification", value="choice", options=modifiers))
    else:
        types = [ConditionOption(value=number, label=name) for number, name in RELEASE_TYPES]
        items.append(ConditionOut(implementation="ReleaseTypeSpecification", value="choice", options=types))
    # Any and Original stay out: a format that matches every language says nothing.
    languages = [
        ConditionOption(value=language.id, label=language.name)
        for language in sorted(lang.LANGUAGES, key=lambda entry: entry.name)
        if language.id > 0 or language.id == lang.ORIGINAL
    ]
    items.append(
        ConditionOut(implementation="LanguageSpecification", value="choice", options=languages, except_language=True)
    )
    # ⚠️ Sonarr numbers the flags differently, and a format of a series compares Sonarr's.
    flag_table = indexer_service.FLAGS if kind == "movie" else indexer_service.SONARR_FLAGS
    flags = [
        ConditionOption(value=number, label=name.replace("_", " "))
        for name, number in sorted(flag_table.items(), key=lambda entry: entry[1])
    ]
    items.append(ConditionOut(implementation="IndexerFlagSpecification", value="choice", options=flags))
    items.append(ConditionOut(implementation="SizeSpecification", value="range", unit="gb"))
    if kind == "movie":
        # ⚠️ Sonarr has no year condition (measured 20.09.2026: eight types, Radarr has ten). Offered for a series
        # it would only count when the release name happens to carry a year, and say nothing the rest of the time.
        # The engine still reads one, so a format that has it keeps working.
        items.append(ConditionOut(implementation="YearSpecification", value="range", unit="year"))
    return items


def _checked_qualities(kind: str, qualities: list[dict[str, Any]], cutoff: Any) -> None:
    """The list has to stay a list the judgement can read: known names, each one once, and a cutoff that is there.

    Without this a wrong list would be stored and every judgement of that version would quietly be built on it.
    """
    known = set(_quality_names(kind))
    seen: set[str] = set()
    labels: list[str] = []
    for entry in qualities:
        if not isinstance(entry, dict) or not isinstance(entry.get("allowed"), bool):
            raise error("invalid_input", "The input is not valid.", 422, fields=["qualities"])
        if entry.get("group"):
            items = entry.get("items")
            if not isinstance(items, list) or not items or any(name not in known for name in items):
                raise error("invalid_input", "The input is not valid.", 422, fields=["qualities"])
            names = [str(name) for name in items]
            label = str(entry["group"])
        else:
            if entry.get("name") not in known:
                raise error("invalid_input", "The input is not valid.", 422, fields=["qualities"])
            names = [str(entry["name"])]
            label = names[0]
        if seen & set(names):
            raise error("invalid_input", "The input is not valid.", 422, fields=["qualities"])
        seen.update(names)
        if entry["allowed"]:
            labels.append(label)
    if not labels:
        raise error("invalid_input", "The input is not valid.", 422, fields=["qualities"])
    if cutoff is not None and str(cutoff) not in labels:
        raise error("invalid_input", "The input is not valid.", 422, fields=["cutoff"])


def _after_change(db: OrmSession, kind: str, moment: datetime) -> None:
    """Every profile the owner keeps by hand gets its rules written again."""
    changed = profile_expert.recompile(db, kind, moment)
    if changed:
        logger.info("%d profiles built again after a change of formats or sizes", changed)


# --- Qualities ---------------------------------------------------------------------------------- #


@router.get(
    "/api/quality",
    response_model=QualityListOut,
    summary="Read what the qualities may weigh",
    description="Every quality of this kind with its size limits, as Radarr's quality definitions hold them.",
    responses=error_responses((422, "invalid_input")),
)
def read_quality(
    db: DbSession, kind: Annotated[Kind, Query(description="movie or series.")] = "movie"
) -> QualityListOut:
    stored = profile_expert.sizes_of(db, kind)
    items = [
        QualitySizeOut(
            quality=name,
            min_mb_per_min=float((stored.get(name) or {}).get("min_mb_per_min") or 0.0),
            max_mb_per_min=(stored.get(name) or {}).get("max_mb_per_min"),
            preferred_mb_per_min=(stored.get(name) or {}).get("preferred_mb_per_min"),
        )
        for name in _quality_names(kind)
    ]
    return QualityListOut(kind=kind, items=items)


@router.put(
    "/api/quality",
    response_model=QualityListOut,
    summary="Change what the qualities may weigh",
    description=(
        "Stores the limits of the qualities named in the body; the others stay. A maximum below the minimum is "
        "refused, and so is a preferred size outside of the two. Every profile kept by hand is built again afterwards."
    ),
    responses=error_responses((422, "invalid_input")),
)
def change_quality(db: DbSession, payload: QualityListIn) -> QualityListOut:
    known = set(_quality_names(payload.kind))
    moment = utcnow()
    rows = {row.quality: row for row in db.scalars(select(QualitySize).where(QualitySize.kind == payload.kind))}
    for item in payload.items:
        if item.quality not in known:
            raise error("invalid_input", "The input is not valid.", 422, fields=["quality"])
        if item.max_mb_per_min is not None and item.max_mb_per_min < item.min_mb_per_min:
            raise error("invalid_input", "The input is not valid.", 422, fields=["max_mb_per_min"])
        if item.preferred_mb_per_min is not None and (
            profile_expert.within(item.preferred_mb_per_min, item.min_mb_per_min, item.max_mb_per_min) is None
        ):
            raise error("invalid_input", "The input is not valid.", 422, fields=["preferred_mb_per_min"])
        row = rows.get(item.quality)
        if row is None:
            row = QualitySize(kind=payload.kind, quality=item.quality, updated_at=moment)
            db.add(row)
            rows[item.quality] = row
        row.min_mb_per_min = item.min_mb_per_min
        row.max_mb_per_min = item.max_mb_per_min
        row.preferred_mb_per_min = item.preferred_mb_per_min
        row.updated_at = moment
    db.flush()
    _after_change(db, payload.kind, moment)
    db.commit()
    logger.info("Quality sizes of %s changed: %d qualities", payload.kind, len(payload.items))
    return read_quality(db, payload.kind)


# --- Custom formats ------------------------------------------------------------------------------ #


@router.get(
    "/api/custom-formats",
    response_model=list[CustomFormatOut],
    summary="List the custom formats",
    description="Every format of this kind, the ones from the guides and your own, by name.",
    responses=error_responses((422, "invalid_input")),
)
def list_formats(
    db: DbSession, kind: Annotated[Kind, Query(description="movie or series.")] = "movie"
) -> list[CustomFormatOut]:
    used = _used_by(db, kind)
    rows = db.scalars(select(CustomFormat).where(CustomFormat.kind == kind).order_by(CustomFormat.name))
    return [_format_out(row, used) for row in rows]


@router.get(
    "/api/custom-formats/conditions",
    response_model=ConditionListOut,
    summary="List the conditions a format can be built from",
    description=(
        "What a specification can look at, with the numbers the engine compares. Movies and series differ: only "
        "movies know editions, quality modifiers and the year, only series know the release type. The lists are "
        "exactly Radarr's and Sonarr's own."
    ),
    responses=error_responses((422, "invalid_input")),
)
def list_conditions(
    db: DbSession, kind: Annotated[Kind, Query(description="movie or series.")] = "movie"
) -> ConditionListOut:
    return ConditionListOut(kind=kind, items=_conditions(kind))


@router.post(
    "/api/custom-formats/test",
    response_model=FormatTestOut,
    summary="Test a format against a release name",
    description=(
        "Reads the name the way a search would and says for every specification whether it counts, for every "
        "group of one type whether it matches, and whether the format matches. Nothing is stored, so a format "
        "can be tried before it is saved. The verdict is the one a judgement would reach."
    ),
    responses=error_responses((422, "invalid_input")),
)
def test_format(payload: FormatTestIn) -> FormatTestOut:
    name = payload.name.strip()
    if not name:
        raise error("invalid_input", "The input is not valid.", 422, fields=["name"])
    data: format_engine.FormatInput
    if payload.kind == "movie":
        movie = movie_decision.parse(name, payload.original_language)
        context = movie_decision.ReleaseContext(size_bytes=payload.size_bytes, indexer_flags=payload.indexer_flags)
        data, parsed = movie.format_input(context), movie.as_dict()
    else:
        series = series_decision.parse(name, payload.original_language)
        series_context = series_decision.SeriesContext(
            size_bytes=payload.size_bytes, indexer_flags=payload.indexer_flags
        )
        data, parsed = series.format_input(series_context), series.as_dict()
    try:
        told = format_engine.explain({"specifications": payload.specifications}, data, TEST_COMMIT)
    finally:
        format_engine.patterns.forget(TEST_COMMIT)
    return FormatTestOut(parsed=parsed, **told)


@router.post(
    "/api/custom-formats",
    response_model=CustomFormatOut,
    status_code=201,
    summary="Add a custom format of your own",
    description=(
        "The specifications are Radarr's shape: implementation, negate, required and fields. A profile gives it a "
        "score; without one it changes nothing."
    ),
    responses=error_responses((409, "custom_format_name_taken"), (422, "invalid_input")),
)
def add_format(db: DbSession, payload: CustomFormatIn) -> CustomFormatOut:
    taken = db.scalar(
        select(CustomFormat).where(CustomFormat.kind == payload.kind, CustomFormat.name == payload.name.strip())
    )
    if taken is not None:
        raise error("custom_format_name_taken", "A format of this name is already there.", 409)
    moment = utcnow()
    row = CustomFormat(
        kind=payload.kind,
        name=payload.name.strip(),
        origin="own",
        specifications=list(payload.specifications),
        created_at=moment,
        updated_at=moment,
    )
    db.add(row)
    db.commit()
    logger.info("Custom format %d added", row.id)
    return _format_out(row, {})


@router.put(
    "/api/custom-formats/{format_id}",
    response_model=CustomFormatOut,
    summary="Change a custom format",
    description=(
        "A format that came with the guides becomes your own as soon as its specifications change; the guides no "
        "longer touch it. Every profile that points at it is built again."
    ),
    responses=error_responses((404, "not_found"), (409, "custom_format_name_taken"), (422, "invalid_input")),
)
def change_format(db: DbSession, format_id: int, payload: CustomFormatPatch) -> CustomFormatOut:
    row = db.get(CustomFormat, format_id)
    if row is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    moment = utcnow()
    if payload.name is not None and payload.name.strip() != row.name:
        taken = db.scalar(
            select(CustomFormat).where(CustomFormat.kind == row.kind, CustomFormat.name == payload.name.strip())
        )
        if taken is not None:
            raise error("custom_format_name_taken", "A format of this name is already there.", 409)
        row.name = payload.name.strip()
    if payload.specifications is not None:
        row.specifications = list(payload.specifications)
        row.origin = "own"
    row.updated_at = moment
    db.flush()
    _after_change(db, row.kind, moment)
    db.commit()
    logger.info("Custom format %d changed", format_id)
    return _format_out(row, _used_by(db, row.kind))


@router.delete(
    "/api/custom-formats/{format_id}",
    status_code=204,
    summary="Remove a custom format",
    description="Only one no profile points at. Take it out of the profiles first.",
    responses=error_responses((404, "not_found"), (409, "custom_format_in_use")),
)
def remove_format(db: DbSession, format_id: int) -> None:
    row = db.get(CustomFormat, format_id)
    if row is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    used = _used_by(db, row.kind).get(format_id) or []
    if used:
        raise error("custom_format_in_use", "A profile still points at this format.", 409, versions=sorted(set(used)))
    db.delete(row)
    db.commit()
    logger.info("Custom format %d removed", format_id)


# --- The profile by hand -------------------------------------------------------------------------- #


@router.get(
    "/api/versions/{version_id}/profile/expert",
    response_model=ExpertOut,
    summary="Read the profile of a version as it is set by hand",
    description=(
        "What the owner set by hand, or, while the assistant still holds the profile, what it left behind. The "
        "qualities of the kind and every format come with it, so the interface needs nothing else."
    ),
    responses=error_responses((404, "not_found"), (409, "profile_kind_unsupported"), (409, "profile_missing")),
)
def read_expert(db: DbSession, version_id: int) -> ExpertOut:
    return _expert_out(db, _profile_of(db, version_id), version_id)


@router.put(
    "/api/versions/{version_id}/profile/expert",
    response_model=ExpertOut,
    summary="Set the profile of a version by hand",
    description=(
        "Stores what is sent, builds the rules from it and switches the profile to expert. The assistant can still "
        "be opened; saving there overwrites this again."
    ),
    responses=error_responses(
        (404, "not_found"),
        (409, "profile_kind_unsupported"),
        (409, "profile_missing"),
        (422, "invalid_input"),
    ),
)
def change_expert(db: DbSession, version_id: int, payload: ExpertIn) -> ExpertOut:
    profile = _profile_of(db, version_id)
    _save_expert(db, profile, payload)
    return _expert_out(db, profile, version_id)


@router.get(
    "/api/profiles/{profile_id}/expert",
    response_model=ExpertOut,
    summary="Read a profile as it is set by hand",
    description=(
        "The same as below a version, for the profile itself: every profile can be set, also one no version "
        "judges by yet. An empty profile answers the bare list of qualities of its kind, none of them taken."
    ),
    responses=error_responses((404, "not_found"), (409, "profile_kind_unsupported")),
)
def read_expert_profile(db: DbSession, profile_id: int) -> ExpertOut:
    return _expert_out(db, _profile_by_id(db, profile_id), None)


@router.put(
    "/api/profiles/{profile_id}/expert",
    response_model=ExpertOut,
    summary="Set a profile by hand",
    description=(
        "Stores what is sent, builds the rules from it and switches the profile to expert. Every version that "
        "judges by it follows at once."
    ),
    responses=error_responses((404, "not_found"), (409, "profile_kind_unsupported"), (422, "invalid_input")),
)
def change_expert_profile(db: DbSession, profile_id: int, payload: ExpertIn) -> ExpertOut:
    profile = _profile_by_id(db, profile_id)
    _save_expert(db, profile, payload)
    for definition in profile_store.versions_of(db, profile_id):
        judging.after_profile_change(definition.id)
    return _expert_out(db, profile, None)


@router.post(
    "/api/profiles/{profile_id}/expert/anime-from-trash",
    response_model=AnimeFromTrashOut,
    summary="Fill the anime branch of a series profile from TRaSH's anime profile",
    description=(
        "Answers the anime branch as TRaSH's anime profile has it, with the answers of the profile where it has "
        "any, and does not save it: the owner looks at it and saves the profile. Formats the table lacks are "
        "created; a format counts only where a profile scores it, so that changes no judgement."
    ),
    responses=error_responses(
        (404, "not_found"), (409, "profile_kind_unsupported"), (409, "profile_build_failed"), (422, "invalid_input")
    ),
)
def anime_from_trash(db: DbSession, profile_id: int, payload: AnimeFromTrashIn) -> AnimeFromTrashOut:
    profile = _profile_by_id(db, profile_id)
    if profile.kind != "series":
        raise error("profile_kind_unsupported", "Only a series profile has anime rules.", 409)
    try:
        branch, created, base = profile_expert.anime_from_trash(
            db, profile, trash_service.current("series"), payload.family, utcnow()
        )
    except ProfileBuildError as exc:
        db.rollback()
        logger.warning("The anime branch of profile %d cannot be filled: %s", profile_id, exc)
        raise error(
            "profile_build_failed", "The profile cannot be built with the TRaSH Guides state in use.", 409
        ) from exc
    db.commit()
    if created:
        logger.info("%d formats created for the anime branch of profile %d", created, profile_id)
    used = _used_by(db, profile.kind)
    rows = db.scalars(select(CustomFormat).where(CustomFormat.kind == profile.kind).order_by(CustomFormat.name))
    return AnimeFromTrashOut(
        anime=branch,
        trash_profile=base,
        formats_created=created,
        formats_available=[_format_out(row, used) for row in rows],
    )
