"""Auto tags: rules that give tags by themselves, as Auto Tagging in Radarr, Sonarr and Lidarr.

Saving or removing a rule runs every rule of its kind over the whole library at once (the apps wait for the next
refresh); the answer says how many titles changed.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..deps import DbSession
from ..meldungen import error, error_responses
from ..models import AutoTag, Profile, Tag, Title, utcnow
from ..services import auto_tags, tags

logger = logging.getLogger("nexcrate.auto_tags")

router = APIRouter(tags=["tags"])

Kind = Literal["movie", "series", "album"]
ConditionType = Literal[
    "genre", "year", "root_folder", "runtime", "keyword", "studio", "original_language", "profile", "status",
    "monitored", "tag", "series_type",
]  # fmt: skip


class ConditionIn(BaseModel):
    type: ConditionType
    negate: bool = Field(default=False, description="Fits when the condition does not.")
    required: bool = Field(
        default=False,
        description="Must fit. Conditions of one type fit when one of them does, unless a required one fails; the "
        "rule fits when every type fits, as in the apps.",
    )
    values: list[str] | None = Field(
        default=None, max_length=auto_tags.VALUES_MAX, description="genre, keyword, studio: any of them, case ignored."
    )
    min: int | None = Field(default=None, description="year, runtime (minutes): from, included.")
    max: int | None = Field(default=None, description="year, runtime: to, included.")
    value: str | None = Field(
        default=None,
        max_length=auto_tags.VALUE_MAX_LENGTH,
        description="root_folder (the whole path), original_language (ISO 639-1), status, series_type, tag (by name).",
    )
    profile_id: int | None = Field(default=None, description="profile: any version of the title uses it.")


class ConditionOut(ConditionIn):
    pass


class AutoTagIn(BaseModel):
    kind: Kind = "movie"
    name: str = Field(min_length=1, max_length=auto_tags.NAME_MAX)
    tags: list[str] = Field(min_length=1, max_length=tags.PER_ITEM_MAX, description="The tags it gives, by name.")
    remove_automatically: bool = Field(
        default=False,
        description="When the rule no longer fits, its tags go from the title, even one set by hand, as in the apps.",
    )
    conditions: list[ConditionIn] = Field(min_length=1, max_length=auto_tags.CONDITIONS_MAX)


class AutoTagOut(BaseModel):
    id: int
    kind: Kind
    name: str
    tags: list[str] = Field(description="By name; a deleted tag has dropped out.")
    remove_automatically: bool
    conditions: list[ConditionOut]


class AutoTagSaved(BaseModel):
    rule: AutoTagOut
    changed: int = Field(description="Titles or artists whose tags changed when the rules of the kind ran just now.")


class AutoTagRemoved(BaseModel):
    changed: int = Field(description="Titles or artists whose tags changed when the remaining rules ran.")


class ProfileOption(BaseModel):
    id: int
    name: str


class AutoTagOptions(BaseModel):
    conditions: list[str] = Field(description="The condition types of the kind, in the apps' order.")
    statuses: list[str]
    series_types: list[str]
    genres: list[str] = Field(description="The genres titles of the kind carry, by name.")
    languages: list[str] = Field(description="The original languages titles of the kind have (ISO 639-1).")
    root_folders: list[str] = Field(description="The root folders versions of the kind use.")
    profiles: list[ProfileOption]


def _out(db: DbSession, row: AutoTag) -> AutoTagOut:
    names = dict(db.execute(select(Tag.id, Tag.label)).tuples().all()) if (row.tag_ids or row.conditions) else {}
    conditions = []
    for item in row.conditions or []:
        shown = {key: value for key, value in item.items() if key != "tag_id"}
        if item.get("type") == "tag":
            shown["value"] = names.get(item.get("tag_id"), "")
        conditions.append(ConditionOut.model_validate(shown))
    return AutoTagOut(
        id=row.id,
        kind=row.kind,  # type: ignore[arg-type]
        name=row.name,
        tags=sorted(names[tag_id] for tag_id in row.tag_ids or [] if tag_id in names),
        remove_automatically=row.remove_automatically,
        conditions=conditions,
    )



@router.get(
    "/api/auto-tags",
    response_model=list[AutoTagOut],
    summary="List the auto tags",
    description="The rules that give tags by themselves, of one kind or all, by name.",
)
def list_auto_tags(db: DbSession, kind: Kind | None = Query(default=None)) -> list[AutoTagOut]:
    query = select(AutoTag).order_by(AutoTag.kind, func.lower(AutoTag.name), AutoTag.id)
    if kind is not None:
        query = query.where(AutoTag.kind == kind)
    return [_out(db, row) for row in db.scalars(query)]


@router.get(
    "/api/auto-tags/options",
    response_model=AutoTagOptions,
    summary="What a condition can name",
    description="The condition types of a kind and the values the library offers for them.",
)
def auto_tag_options(db: DbSession, kind: Kind = Query(default="movie")) -> AutoTagOptions:
    genres: set[str] = set()
    languages: set[str] = set()
    if kind != "album":
        for row_genres, language in db.execute(select(Title.genres, Title.original_language).where(Title.kind == kind)):
            genres.update(str(genre) for genre in row_genres or [] if str(genre).strip())
            if language:
                languages.add(language.lower())
    profiles = db.execute(
        select(Profile.id, Profile.name).where(Profile.kind == kind).order_by(func.lower(Profile.name))
    )
    return AutoTagOptions(
        conditions=list(auto_tags.CONDITIONS[kind]),
        statuses=list(auto_tags.STATUSES[kind]),
        series_types=list(auto_tags.SERIES_TYPES) if kind == "series" else [],
        genres=sorted(genres, key=str.casefold),
        languages=sorted(languages),
        root_folders=auto_tags.folders_of(db, kind),
        profiles=[ProfileOption(id=profile_id, name=name or "") for profile_id, name in profiles],
    )


def _tag_name(item: ConditionIn) -> str | None:
    """A tag condition names its tag as the tag field stores it; the other conditions keep their value."""
    if item.type == "tag" and item.value and item.value.strip():
        return tags.clean(item.value)
    return item.value


def _store(db: DbSession, row: AutoTag, payload: AutoTagIn) -> None:
    """Checks and writes a rule; raises the API error. New tags named in it are created, as in the apps' tag field."""
    name = payload.name.strip()
    if not name:
        raise error("invalid_input", "The input is not valid.", 422, fields=["name"])
    taken = db.scalar(
        select(AutoTag.id).where(
            AutoTag.kind == payload.kind, func.lower(AutoTag.name) == name.lower(), AutoTag.id != (row.id or 0)
        )
    )
    if taken is not None:
        raise error("auto_tag_name_taken", "An auto tag of this kind has this name already.", 409, name=name)
    labels = tags.cleaned(payload.tags)
    if not labels:
        raise error("invalid_input", "The input is not valid.", 422, fields=["tags"])
    named = [item.value for item in payload.conditions if item.type == "tag" and item.value and item.value.strip()]
    wanted = [tags.clean(value) for value in named]
    ids = tags.ensure(db, [*labels, *wanted])
    try:
        conditions = auto_tags.clean_conditions(
            payload.kind,
            [
                {**item.model_dump(), "value": _tag_name(item)}
                for item in payload.conditions
            ],
            ids,
        )
    except auto_tags.RuleInvalid as exc:
        raise error("invalid_input", "The input is not valid.", 422, fields=exc.fields) from exc
    for index, item in enumerate(conditions):
        if item["type"] == "profile":
            kind_of = db.scalar(select(Profile.kind).where(Profile.id == item["profile_id"]))
            if kind_of != payload.kind:
                raise error("invalid_input", "The input is not valid.", 422, fields=[f"conditions.{index}.profile_id"])
    row.kind = payload.kind
    row.name = name
    row.tag_ids = sorted({ids[label] for label in labels})
    row.remove_automatically = payload.remove_automatically
    row.conditions = conditions
    row.updated_at = utcnow()


@router.post(
    "/api/auto-tags",
    status_code=201,
    response_model=AutoTagSaved,
    summary="Add an auto tag",
    description="Stores the rule and runs every rule of its kind over the library at once.",
    responses=error_responses((409, "auto_tag_name_taken"), (422, "invalid_input")),
)
def create_auto_tag(db: DbSession, payload: AutoTagIn) -> AutoTagSaved:
    row = AutoTag()
    _store(db, row, payload)
    db.add(row)
    db.flush()
    changed = auto_tags.apply(db, row.kind)
    db.commit()
    logger.info("Auto tag %d added for %s, %d changed", row.id, row.kind, changed)
    return AutoTagSaved(rule=_out(db, row), changed=changed)


@router.put(
    "/api/auto-tags/{auto_tag_id}",
    response_model=AutoTagSaved,
    summary="Change an auto tag",
    description="Replaces the rule and runs every rule of its kind over the library at once. The kind stays.",
    responses=error_responses((404, "not_found"), (409, "auto_tag_name_taken"), (422, "invalid_input")),
)
def update_auto_tag(db: DbSession, auto_tag_id: int, payload: AutoTagIn) -> AutoTagSaved:
    row = db.get(AutoTag, auto_tag_id)
    if row is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    if payload.kind != row.kind:
        raise error("invalid_input", "The input is not valid.", 422, fields=["kind"])
    _store(db, row, payload)
    db.flush()
    changed = auto_tags.apply(db, row.kind)
    db.commit()
    logger.info("Auto tag %d changed, %d changed", row.id, changed)
    return AutoTagSaved(rule=_out(db, row), changed=changed)


@router.delete(
    "/api/auto-tags/{auto_tag_id}",
    response_model=AutoTagRemoved,
    summary="Remove an auto tag",
    description="The tags it gave stay on the titles, as in the apps; the other rules of the kind run again.",
    responses=error_responses((404, "not_found")),
)
def delete_auto_tag(db: DbSession, auto_tag_id: int) -> AutoTagRemoved:
    row = db.get(AutoTag, auto_tag_id)
    if row is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    kind = row.kind
    db.delete(row)
    db.flush()
    changed = auto_tags.apply(db, kind)
    db.commit()
    logger.info("Auto tag %d removed, %d changed", auto_tag_id, changed)
    return AutoTagRemoved(changed=changed)
