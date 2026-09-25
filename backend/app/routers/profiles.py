"""Profiles: the wizard's questions, a preview, the profile of a version, the list of profiles, export and import.

A profile has a name and stands for itself; a version points at one of them, and several versions may point at the
same one. The server normalizes the answers, builds the rules with the TRaSH Guides
state in use and stores answers and rules together; the interface never builds rules itself. Nothing is ever
written into Radarr. Saving an import goes through ``PUT /api/versions/{id}/profile`` like the wizard, so both
ways store the same thing.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from ..deps import DbSession
from ..meldungen import error, error_responses
from ..models import Profile, VersionDefinition, utcnow
from ..services import judging, profiles, schreibweisen, trash
from ..services.profiles import expert as profile_expert
from ..services.profiles import files
from ..services.profiles import store as profile_store
from .versions import LanguageAnswer

logger = logging.getLogger("nexcrate.profiles")

router = APIRouter(tags=["profiles"])

YAML_MAX_CHARACTERS = 1_000_000


# --- Models ----------------------------------------------------------------------------------- #


class SimpleConditionOut(BaseModel):
    """One of the three simple shapes; only the key of its shape is present next to question."""

    model_config = ConfigDict(populate_by_name=True)

    question: str = Field(examples=["resolution"])
    is_: str | bool | int | float | None = Field(
        default=None, alias="is", description="The condition holds when the answer is this value."
    )
    includes: str | None = Field(default=None, description="For languages: this code is among them, in any role.")
    required_at_least: int | None = Field(default=None, description="For languages: at least this many required.")


class AnyConditionOut(BaseModel):
    """Holds when every condition of at least one inner list holds. Inner lists hold simple shapes only."""

    model_config = ConfigDict(populate_by_name=True)

    any_: list[list[SimpleConditionOut]] = Field(
        alias="any", description="Lists of simple conditions; the condition holds when one list holds completely."
    )


class QuestionOut(BaseModel):
    """Only the keys of its type are present: options for choice, codes and roles for languages, min, max and
    nullable for number."""

    id: str = Field(examples=["hdr"])
    type: str = Field(description="choice, boolean, languages or number.")
    options: list[str] | None = None
    codes: list[str] | None = None
    roles: list[str] | None = None
    min: int | float | None = None
    max: int | float | None = None
    nullable: bool | None = None
    default: Any = Field(default=None, description="The default answer; null for a number without a limit.")
    detailed: bool = Field(description="Shown only while mode is detailed.")
    when: list[SimpleConditionOut | AnyConditionOut] = Field(
        description=(
            "Conditions that must all hold; empty means always. Each is a simple shape or {any: [[simple, ...], ...]}."
        )
    )


class QuestionsOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    kind: str = Field(examples=["movie"])
    schema_: int = Field(alias="schema", examples=[1])
    questions: list[QuestionOut]


class QualityGroupOut(BaseModel):
    group: str = Field(examples=["Merged QPs"])
    items: list[str]


class NamedScore(BaseModel):
    name: str = Field(examples=["German DL"])
    score: int = Field(examples=[11000])


class SizeOut(BaseModel):
    quality: str = Field(examples=["Bluray-2160p"])
    min_gb_per_hour: float = Field(description="TRaSH's minimum, GB per hour of runtime.")
    max_gb_per_hour: int | float | None = Field(description="The owner's limit; null means none.")


class WarningOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    code: str = Field(
        description="size_limit_below_minimum with qualities, answer_without_effect with questions.",
        examples=["size_limit_below_minimum"],
    )


class SummaryOut(BaseModel):
    qualities: list[str | QualityGroupOut] = Field(
        description="Allowed qualities from lowest to highest; a merged group as {group, items}."
    )
    cutoff: str
    min_score: int
    upgrade_until: int
    preferred: list[NamedScore] = Field(
        description="At most ten formats with the highest scores, of those that can match an allowed quality."
    )
    avoided: list[NamedScore] = Field(
        description="At most ten formats with the lowest scores, of those that can match an allowed quality."
    )
    formats_total: int = Field(
        description="Formats that can match at least one allowed quality. The rules keep every format for the score."
    )
    sizes: list[SizeOut]
    max_gb_per_hour: int | float | None
    languages: list[LanguageAnswer]
    required_languages: str = Field(description="all or any: the mode that acts, all only with two required.")
    warnings: list[WarningOut]


class ProfileOut(BaseModel):
    version_id: int | None = Field(
        default=None, description="The version this was read for; null when the profile was read on its own."
    )
    profile_id: int = Field(description="The profile itself; several versions may point at the same one.")
    name: str = Field(description="What the owner calls it.")
    kind: str
    answers: dict[str, Any] = Field(description="The normalized answers, with schema and kind.")
    summary: SummaryOut | None = Field(
        default=None, description="What the rules do, in words. Null while the profile is empty."
    )
    empty: bool = Field(default=False, description="Nothing has filled it yet: no rules, so it judges nothing.")
    trash_commit: str = Field(description="The TRaSH Guides commit the rules were built with.")
    outdated: bool = Field(description="Built with another TRaSH Guides state than the one in use.")
    mode: str = Field(description="wizard, or expert while the owner keeps the profile by hand.")
    updated_at: datetime


class PreviewIn(BaseModel):
    version_id: int
    answers: dict[str, Any] = Field(
        default_factory=dict, description="Answers by question id; missing ones take defaults."
    )


class PreviewOut(BaseModel):
    answers: dict[str, Any]
    summary: SummaryOut


class ProfileIn(BaseModel):
    answers: dict[str, Any] = Field(default_factory=dict)


class ImportIn(BaseModel):
    yaml: str = Field(max_length=YAML_MAX_CHARACTERS, description="The file content, at most 64 KB.")
    version_id: int = Field(default=0, description="The version the file is meant for; 0 with a kind of its own.")
    kind: str | None = Field(
        default=None, description="The kind the file has to match, for a profile that belongs to no version."
    )


class ImportOut(BaseModel):
    kind: str
    name: str | None = Field(description="The version label the file was exported from.")
    answers: dict[str, Any]
    summary: SummaryOut = Field(description="Built with the TRaSH Guides state in use.")
    trash_commit: str | None = Field(description="The commit named in the file.")
    commit_differs: bool = Field(description="The file names another TRaSH Guides state than the one in use.")


# --- Helpers ---------------------------------------------------------------------------------- #


def _definition(db: OrmSession, version_id: int) -> VersionDefinition:
    row = db.get(VersionDefinition, version_id)
    if row is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    return row


def _kind_unsupported() -> HTTPException:
    return error("profile_kind_unsupported", "There are no profiles for this kind yet.", 422)


def _built_with_trash(definition: VersionDefinition) -> VersionDefinition:
    """Music has a profile, but one without the TRaSH Guides and with routes of its own (``/api/music/profile``)."""
    if definition.kind not in profiles.KINDS:
        raise _kind_unsupported()
    return definition


def _build_failed() -> HTTPException:
    return error("profile_build_failed", "The profile cannot be built with the TRaSH Guides state in use.", 409)


def _import_invalid(reason: str, fields: list[str] | None = None) -> HTTPException:
    values: dict[str, Any] = {"reason": reason}
    if fields is not None:
        values["fields"] = fields
    return error("profile_import_invalid", f"The file cannot be imported ({reason}).", 422, **values)


def _build(kind: str, raw: Any) -> profiles.Built:
    try:
        return profiles.build(kind, raw)
    except profiles.KindUnsupported as exc:
        raise _kind_unsupported() from exc
    except profiles.AnswersInvalid as exc:
        raise error(
            "profile_answers_invalid", "Some answers do not fit the questions.", 422, fields=exc.fields
        ) from exc
    except profiles.ProfileBuildError as exc:
        logger.warning("A %s profile cannot be built: %s", kind, exc)
        raise _build_failed() from exc


def _stored(db: OrmSession, version_id: int) -> Profile | None:
    return profile_store.of_version(db, version_id)


def profile_out(row: Profile, version_id: int | None) -> ProfileOut:
    return ProfileOut(
        version_id=version_id,
        profile_id=row.id,
        name=row.name,
        kind=row.kind,
        answers=profiles.stored_answers(row.kind, row.answers),
        # ⚠️ An empty profile has no rules; summarising them raised a KeyError and answered 500 (20.09.2026).
        summary=(
            None
            if not row.rules
            else SummaryOut.model_validate(profiles.summary_of(row.kind, row.answers, row.rules))
        ),
        empty=not row.rules,
        trash_commit=row.trash_commit,
        outdated=trash.is_outdated(row.trash_commit),
        mode=row.mode,
        updated_at=row.updated_at,
    )


def _file_name(label: str) -> tuple[str, str]:
    """An ASCII name for old clients and the label as it is for the others."""
    keys = schreibweisen.keys(label)
    plain = "-".join((keys[0] if keys else "").split()) or "profile"
    ascii_name = "".join(
        character for character in plain if character.isascii() and (character.isalnum() or character == "-")
    )
    return f"nexcrate-profile-{ascii_name or 'profile'}.yaml", f"nexcrate-profile-{schreibweisen.nfc(label)}.yaml"


# --- Routes ------------------------------------------------------------------------------------ #


@router.get(
    "/api/profiles/questions",
    response_model=QuestionsOut,
    response_model_exclude_unset=True,
    summary="Read the questions of the profile wizard",
    description=(
        "The questions of one kind, in order, with their options, defaults and conditions. The interface "
        "renders this list; texts live in the interface, keyed by question id and option value. Only movie "
        "has profiles so far."
    ),
    responses=error_responses((422, "profile_kind_unsupported")),
)
def read_questions(
    kind: Annotated[str, Query(max_length=16, description="movie or series. Music comes later.")] = "movie",
) -> dict[str, Any]:
    try:
        return profiles.questions_payload(kind)
    except profiles.KindUnsupported as exc:
        raise error("profile_kind_unsupported", "There are no profiles for this kind yet.", 422) from exc


@router.post(
    "/api/profiles/preview",
    response_model=PreviewOut,
    summary="Preview a profile",
    description=(
        "Normalizes the answers for the kind of the version and builds the rules with the TRaSH Guides state in "
        "use, then answers with the normalized answers and the summary. Nothing is saved."
    ),
    responses=error_responses(
        (404, "not_found"),
        (422, "profile_kind_unsupported"),
        (422, "profile_answers_invalid"),
        (409, "profile_build_failed"),
    ),
)
def preview(payload: PreviewIn, db: DbSession) -> PreviewOut:
    definition = _definition(db, payload.version_id)
    built = _build(definition.kind, payload.answers)
    return PreviewOut(answers=built.answers, summary=SummaryOut.model_validate(built.summary))


@router.get(
    "/api/versions/{version_id}/profile",
    response_model=ProfileOut,
    summary="Read the profile of a version",
    description=(
        "The stored answers, the summary of the stored rules, the TRaSH Guides commit they were built with and "
        "whether that is an older state than the one in use."
    ),
    responses=error_responses((404, "not_found"), (422, "profile_kind_unsupported")),
)
def read_profile(version_id: int, db: DbSession) -> ProfileOut:
    _built_with_trash(_definition(db, version_id))
    row = _stored(db, version_id)
    if row is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    return profile_out(row, version_id)


def _write_answers(db: OrmSession, row: Profile, built: Any, moment: datetime) -> None:
    """The assistant's answers and the rules built from them onto a profile that exists."""
    row.answers = built.answers
    row.rules = built.rules
    row.trash_commit = str(built.rules["trash_commit"])
    # The assistant takes the profile back: what the owner set by hand is gone, and the expert mode starts
    # from this again. The interface warns before it comes here.
    row.mode = "wizard"
    row.expert = None
    row.updated_at = moment


def _after_answers(db: OrmSession, row: Profile, moment: datetime) -> None:
    """Its formats and sizes belong in their own tables, and every version that judges by it follows."""
    with_split = profile_expert.split_out(db, moment)
    # The sizes the assistant picked count from here on, over whatever stood there before.
    written = profile_expert.write_sizes(db, row.kind, (row.rules or {}).get("sizes") or {}, moment)
    if any(with_split.values()) or written:
        db.commit()
    for definition in profile_store.versions_of(db, row.id):
        judging.after_profile_change(definition.id)


@router.put(
    "/api/versions/{version_id}/profile",
    response_model=ProfileOut,
    summary="Save the profile of a version",
    description=(
        "Normalizes the answers, builds the rules with the TRaSH Guides state in use and stores both, replacing "
        "an existing profile. Saving again is also how an outdated profile gets the current state. Afterwards the "
        "files of the version that no source feeds are judged by the new rules: whether they would still be "
        "upgraded, and their state with it."
    ),
    responses=error_responses(
        (404, "not_found"),
        (422, "profile_kind_unsupported"),
        (422, "profile_answers_invalid"),
        (409, "profile_build_failed"),
    ),
)
def save_profile(version_id: int, payload: ProfileIn, db: DbSession) -> ProfileOut:
    definition = _definition(db, version_id)
    built = _build(definition.kind, payload.answers)
    moment = utcnow()
    for attempt in (1, 2):
        row = _stored(db, version_id)
        fresh = row is None
        if row is None:
            # A version without one gets its own, named after it.
            row = Profile(name=profile_store.free_name(db, definition.kind, definition.label), created_at=moment)
            db.add(row)
        row.kind = definition.kind
        _write_answers(db, row, built, moment)
        if fresh:
            # The version points at it only once it has everything; the number comes from the flush.
            db.flush()
            profile_store.assign(db, version_id, row)
        try:
            db.commit()
            break
        except IntegrityError:
            # Another request created the profile in the meantime; the second attempt replaces it.
            db.rollback()
            if attempt == 2:
                raise
    logger.info("Profile of version %d saved", version_id)
    _after_answers(db, row, moment)
    return profile_out(row, version_id)


@router.delete(
    "/api/versions/{version_id}/profile",
    status_code=204,
    response_model=None,
    summary="Remove the profile of a version",
    description=(
        "Removes the profile. The version stays; without a profile the release checker skips it, and its files that "
        "no source feeds no longer count as upgradable."
    ),
    responses=error_responses((404, "not_found"), (422, "profile_kind_unsupported")),
)
def remove_profile(version_id: int, db: DbSession) -> None:
    _built_with_trash(_definition(db, version_id))
    row = _stored(db, version_id)
    if row is None:
        return
    profile_store.assign(db, version_id, None)
    db.flush()
    # ⚠️ Since migration 20 a profile may serve several versions; one that nobody else points at goes with it.
    if not profile_store.versions_of(db, row.id):
        db.delete(row)
    db.commit()
    logger.info("Profile of version %d removed", version_id)
    # Without rules nothing is upgraded: the version's own files follow (finding 15).
    judging.after_profile_change(version_id)


@router.get(
    "/api/versions/{version_id}/profile/export",
    response_class=Response,
    response_model=None,
    summary="Export the profile of a version as YAML",
    description=(
        "A YAML file with format nexcrate-profile/1, kind, name (the version label), the answers and the TRaSH "
        "Guides commit. No patterns and no resolved rules, so a shared file carries nothing but answers."
    ),
    responses={
        200: {
            "description": "The profile as a YAML attachment.",
            "content": {"application/yaml": {"schema": {"type": "string"}}},
        },
        **error_responses((404, "not_found"), (422, "profile_kind_unsupported")),
    },
)
def export_profile(version_id: int, db: DbSession) -> Response:
    definition = _built_with_trash(_definition(db, version_id))
    row = _stored(db, version_id)
    if row is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    text = files.export_text(
        row.kind, definition.label, profiles.stored_answers(row.kind, row.answers), row.trash_commit
    )
    plain, full = _file_name(definition.label)
    disposition = f"attachment; filename=\"{plain}\"; filename*=UTF-8''{quote(full, safe='')}"
    return Response(
        content=text.encode("utf-8"), media_type="application/yaml", headers={"Content-Disposition": disposition}
    )


@router.post(
    "/api/profiles/import",
    response_model=ImportOut,
    summary="Read a profile file before saving it",
    description=(
        "Reads a YAML profile file (at most 64 KB, safe loading only), checks format and kind against the version, "
        "normalizes the answers like the wizard and builds a summary with the TRaSH Guides state in use. "
        "Nothing is saved; saving goes through PUT /api/versions/{id}/profile. `reason` is too_large, not_yaml, "
        "not_a_profile, format_unknown, kind_unsupported, kind_mismatch or answers_invalid (with `fields`)."
    ),
    responses=error_responses((404, "not_found"), (422, "profile_import_invalid"), (409, "profile_build_failed")),
)
def import_profile(payload: ImportIn, db: DbSession) -> ImportOut:
    # The kind comes from the version, or is named outright: since the one place for profiles a file may be read
    # for a profile that belongs to no version (20.09.2026).
    wanted = payload.kind if payload.kind is not None else _definition(db, payload.version_id).kind
    try:
        found = files.read(payload.yaml)
    except files.ImportInvalid as exc:
        raise _import_invalid(exc.reason) from exc
    if found.kind != wanted:
        raise _import_invalid("kind_mismatch")
    try:
        built = profiles.build(found.kind, found.answers)
    except profiles.KindUnsupported as exc:
        raise _import_invalid("kind_unsupported") from exc
    except profiles.AnswersInvalid as exc:
        raise _import_invalid("answers_invalid", exc.fields) from exc
    except profiles.ProfileBuildError as exc:
        logger.warning("An imported %s profile cannot be built: %s", found.kind, exc)
        raise _build_failed() from exc
    logger.info("Profile file read for a %s profile", wanted)
    return ImportOut(
        kind=found.kind,
        name=found.name,
        answers=built.answers,
        summary=SummaryOut.model_validate(built.summary),
        trash_commit=found.trash_commit,
        commit_differs=files.commit_differs(found.trash_commit, str(built.rules["trash_commit"])),
    )


# --- The profiles as things of their own ------------------------------------------------------- #


class ProfileBrief(BaseModel):
    id: int
    name: str
    kind: str
    mode: str = Field(description="wizard or expert.")
    empty: bool = Field(default=False, description="Nothing has filled it yet: no rules, so it judges nothing.")
    outdated: bool = Field(description="Built with another TRaSH Guides state than the one in use.")
    used_by: list[str] = Field(description="The names of the versions that judge by it.")
    updated_at: datetime


class ProfileListIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(default="movie", max_length=16)
    copy_of: int | None = Field(default=None, description="Take everything from this profile; without it, empty.")


class ProfileRename(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ProfileChoice(BaseModel):
    profile_id: int | None = Field(description="The profile this version judges by; null takes its profile away.")


def _brief(row: Profile, used: dict[int, list[str]]) -> ProfileBrief:
    return ProfileBrief(
        id=row.id,
        name=row.name,
        kind=row.kind,
        mode=row.mode,
        empty=not row.rules,
        # ⚠️ Only what the guides built can be behind them. An empty profile carries no commit and is not outdated.
        outdated=bool(row.trash_commit) and row.kind in trash.FILE_NAMES and trash.is_outdated(row.trash_commit),
        used_by=used.get(row.id, []),
        updated_at=row.updated_at,
    )


def _used_by(db: OrmSession) -> dict[int, list[str]]:
    used: dict[int, list[str]] = {}
    rows = db.execute(
        select(VersionDefinition.profile_id, VersionDefinition.label)
        .where(VersionDefinition.profile_id.is_not(None))
        .order_by(VersionDefinition.kind, VersionDefinition.label)
    ).tuples()
    for profile_id, label in rows:
        used.setdefault(int(profile_id or 0), []).append(label)
    return used


@router.get(
    "/api/profiles",
    response_model=list[ProfileBrief],
    summary="List the profiles",
    description="Every profile of this kind, or of every kind, with the versions that judge by each of them.",
    responses=error_responses((422, "invalid_input")),
)
def list_profiles(
    db: DbSession,
    kind: Annotated[str | None, Query(description="movie, series or album; every kind if left out.")] = None,
) -> list[ProfileBrief]:
    query = select(Profile).order_by(Profile.kind, Profile.name)
    if kind is not None:
        query = query.where(Profile.kind == kind)
    used = _used_by(db)
    return [_brief(row, used) for row in db.scalars(query)]


@router.post(
    "/api/profiles",
    response_model=ProfileBrief,
    status_code=201,
    summary="Add a profile",
    description=(
        "A profile of its own, empty or as a copy of another one. Empty means: the assistant or the expert mode "
        "fills it; until then it judges nothing."
    ),
    responses=error_responses((404, "not_found"), (409, "profile_name_taken"), (422, "invalid_input")),
)
def add_profile(db: DbSession, payload: ProfileListIn) -> ProfileBrief:
    name = payload.name.strip()
    if not name:
        raise error("invalid_input", "The input is not valid.", 422, fields=["name"])
    taken = db.scalar(select(Profile).where(Profile.kind == payload.kind, Profile.name == name))
    if taken is not None:
        raise error("profile_name_taken", "A profile of this name is already there.", 409)
    source = db.get(Profile, payload.copy_of) if payload.copy_of is not None else None
    if payload.copy_of is not None and source is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    if source is not None and source.kind != payload.kind:
        raise error("invalid_input", "The input is not valid.", 422, fields=["copy_of"])
    moment = utcnow()
    row = Profile(
        name=name,
        kind=payload.kind,
        answers=dict(source.answers) if source is not None else {},
        rules=dict(source.rules) if source is not None else {},
        trash_commit=source.trash_commit if source is not None else "",
        mode=source.mode if source is not None else "wizard",
        expert=dict(source.expert) if source is not None and isinstance(source.expert, dict) else None,
        created_at=moment,
        updated_at=moment,
    )
    db.add(row)
    db.commit()
    logger.info("Profile %d added", row.id)
    return _brief(row, {})


@router.patch(
    "/api/profiles/{profile_id}",
    response_model=ProfileBrief,
    summary="Rename a profile",
    description="Only the name changes; what it judges by stays.",
    responses=error_responses((404, "not_found"), (409, "profile_name_taken"), (422, "invalid_input")),
)
def rename_profile(db: DbSession, profile_id: int, payload: ProfileRename) -> ProfileBrief:
    row = db.get(Profile, profile_id)
    if row is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    name = payload.name.strip()
    if not name:
        raise error("invalid_input", "The input is not valid.", 422, fields=["name"])
    if name != row.name:
        taken = db.scalar(select(Profile).where(Profile.kind == row.kind, Profile.name == name))
        if taken is not None:
            raise error("profile_name_taken", "A profile of this name is already there.", 409)
        row.name = name
        row.updated_at = utcnow()
        db.commit()
        logger.info("Profile %d renamed", profile_id)
    return _brief(row, _used_by(db))


@router.delete(
    "/api/profiles/{profile_id}",
    status_code=204,
    summary="Remove a profile",
    description="Only one no version judges by. Point those versions somewhere else first.",
    responses=error_responses((404, "not_found"), (409, "profile_in_use")),
)
def remove_profile_itself(db: DbSession, profile_id: int) -> None:
    row = db.get(Profile, profile_id)
    if row is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    used = [definition.label for definition in profile_store.versions_of(db, profile_id)]
    if used:
        raise error("profile_in_use", "A version still judges by this profile.", 409, versions=used)
    db.delete(row)
    db.commit()
    logger.info("Profile %d removed", profile_id)


@router.put(
    "/api/versions/{version_id}/profile-choice",
    response_model=ProfileBrief | None,
    summary="Choose the profile of a version",
    description=(
        "The version judges by this profile from now on; null takes its profile away without removing it. A "
        "profile of another kind is refused."
    ),
    responses=error_responses((404, "not_found"), (422, "invalid_input")),
)
def choose_profile(db: DbSession, version_id: int, payload: ProfileChoice) -> ProfileBrief | None:
    definition = _definition(db, version_id)
    chosen = db.get(Profile, payload.profile_id) if payload.profile_id is not None else None
    if payload.profile_id is not None and chosen is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    if chosen is not None and chosen.kind != definition.kind:
        raise error("invalid_input", "The input is not valid.", 422, fields=["profile_id"])
    profile_store.assign(db, version_id, chosen)
    db.commit()
    logger.info("Version %d judges by profile %s now", version_id, payload.profile_id)
    # What it judges by changed, so its own files are judged again (finding 15).
    judging.after_profile_change(version_id)
    return None if chosen is None else _brief(chosen, _used_by(db))


def _profile_row(db: OrmSession, profile_id: int) -> Profile:
    row = db.get(Profile, profile_id)
    if row is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    if row.kind not in profiles.KINDS:
        raise error("profile_kind_unsupported", "This kind has questions of its own.", 422)
    return row


@router.get(
    "/api/profiles/{profile_id}",
    response_model=ProfileOut,
    summary="Read a profile",
    description=(
        "The stored answers and the summary of the stored rules, for the profile itself. An empty profile "
        "answers without a summary."
    ),
    responses=error_responses((404, "not_found"), (422, "profile_kind_unsupported")),
)
def read_profile_itself(profile_id: int, db: DbSession) -> ProfileOut:
    return profile_out(_profile_row(db, profile_id), None)


@router.put(
    "/api/profiles/{profile_id}",
    response_model=ProfileOut,
    summary="Build a profile from the answers of the assistant",
    description=(
        "Normalizes the answers, builds the rules with the TRaSH Guides state in use and stores both. ⚠️ This "
        "takes the profile back from the expert mode; every version that judges by it follows at once."
    ),
    responses=error_responses(
        (404, "not_found"),
        (422, "profile_kind_unsupported"),
        (422, "profile_answers_invalid"),
        (409, "profile_build_failed"),
    ),
)
def save_profile_itself(profile_id: int, payload: ProfileIn, db: DbSession) -> ProfileOut:
    row = _profile_row(db, profile_id)
    built = _build(row.kind, payload.answers)
    moment = utcnow()
    _write_answers(db, row, built, moment)
    db.commit()
    logger.info("Profile %d saved from the answers of the assistant", profile_id)
    _after_answers(db, row, moment)
    return profile_out(row, None)


@router.get(
    "/api/profiles/{profile_id}/export",
    response_class=Response,
    response_model=None,
    summary="Export a profile as YAML",
    description="The same file as below a version, named after the profile.",
    responses={
        200: {
            "description": "The profile as a YAML attachment.",
            "content": {"application/yaml": {"schema": {"type": "string"}}},
        },
        **error_responses((404, "not_found"), (422, "profile_kind_unsupported")),
    },
)
def export_profile_itself(profile_id: int, db: DbSession) -> Response:
    row = _profile_row(db, profile_id)
    if not row.rules:
        raise error("not_found", "This does not exist, or not any more.", 404)
    text = files.export_text(row.kind, row.name, profiles.stored_answers(row.kind, row.answers), row.trash_commit)
    plain, full = _file_name(row.name)
    disposition = f"attachment; filename=\"{plain}\"; filename*=UTF-8\'\'{quote(full, safe='')}"
    return Response(
        content=text.encode("utf-8"), media_type="application/yaml", headers={"Content-Disposition": disposition}
    )
