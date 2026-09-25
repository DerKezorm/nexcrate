"""The music profile (M2.3): four questions, a preview, and the one stored profile.

There is one music version, so there is one music profile, and the routes name no version. It is stored with the
profiles of movies and series but built without the TRaSH Guides; the routes under ``/api/profiles`` and
``/api/versions/{id}/profile`` refuse the music version with ``profile_kind_unsupported``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from ..deps import DbSession
from ..meldungen import error, error_responses
from ..models import Profile, utcnow
from ..services.music import album_quality, store
from ..services.profiles import music as music_profile
from ..services.profiles import store as profile_store
from ..services.profiles.questions import AnswersInvalid
from .profiles import QuestionsOut

logger = logging.getLogger("nexcrate.profiles")

router = APIRouter(prefix="/api/music/profile", tags=["music"])


class LadderStep(BaseModel):
    step: str = Field(description="lossless_24, lossless, lossy_high, lossy_mid or lossy_low.", examples=["lossless"])
    role: str = Field(
        description=(
            "target; for_now: taken while nothing of the target is there, and replaced later; waits: not the target, "
            "and take_now is off; never."
        ),
        examples=["target"],
    )


class MusicSummary(BaseModel):
    quality: str = Field(description="lossless, either or lossy.", examples=["lossless"])
    take_now: bool
    hires: str = Field(description="any, prefer or avoid. Orders releases, shuts none out.", examples=["any"])
    source: str = Field(description="avoid_vinyl, any or prefer_cd. Orders releases, shuts none out.")
    ladder: list[LadderStep] = Field(description="The five quality steps, best first, with what the profile does.")


class MusicProfileOut(BaseModel):
    version_id: int = Field(description="The one music version.")
    answers: dict[str, Any] = Field(description="The normalized answers, with schema and kind.")
    summary: MusicSummary
    updated_at: datetime


class MusicProfileIn(BaseModel):
    answers: dict[str, Any] = Field(
        default_factory=dict, description="Answers by question id; missing ones take defaults."
    )


class MusicPreviewOut(BaseModel):
    answers: dict[str, Any]
    summary: MusicSummary


def _normalized(raw: Any) -> dict[str, Any]:
    try:
        return music_profile.normalized(raw)
    except AnswersInvalid as exc:
        raise error(
            "profile_answers_invalid", "Some answers do not fit the questions.", 422, fields=exc.fields
        ) from exc


def _stored(db: OrmSession) -> Profile | None:
    definition = store.definition(db)
    if definition is None:
        return None
    return profile_store.of_version(db, definition.id)


def _out(row: Profile, version_id: int) -> MusicProfileOut:
    return MusicProfileOut(
        version_id=version_id,
        answers=dict(row.answers),
        summary=MusicSummary.model_validate(music_profile.summary(row.answers, row.rules)),
        updated_at=row.updated_at,
    )


@router.get(
    "/questions",
    response_model=QuestionsOut,
    response_model_exclude_unset=True,
    summary="Read the questions of the music profile",
    description=(
        "Four questions in order, with their options and defaults, in the shape of the other profile wizards. Texts "
        "live in the interface, keyed by question id and option value."
    ),
)
def read_questions() -> dict[str, Any]:
    return music_profile.questions_payload()


@router.post(
    "/preview",
    response_model=MusicPreviewOut,
    summary="Preview the music profile",
    description="Normalizes the answers and says what the profile would do with each quality step. Nothing is saved.",
    responses=error_responses((422, "profile_answers_invalid")),
)
def preview(payload: MusicProfileIn) -> MusicPreviewOut:
    answers = _normalized(payload.answers)
    summary = music_profile.summary(answers, music_profile.build(answers))
    return MusicPreviewOut(answers=answers, summary=MusicSummary.model_validate(summary))


@router.get(
    "",
    response_model=MusicProfileOut,
    summary="Read the music profile",
    description="The stored answers and what the stored rules do with each quality step.",
    responses=error_responses((404, "not_found")),
)
def read_profile(db: DbSession) -> MusicProfileOut:
    definition = store.definition(db)
    row = _stored(db)
    if row is None or definition is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    return _out(row, definition.id)


@router.put(
    "",
    response_model=MusicProfileOut,
    summary="Save the music profile",
    description=(
        "Normalizes the answers, builds the rules and stores both, replacing an existing profile. Afterwards every "
        "album with files is judged: below the target of the profile it shows as upgrade. Nothing is searched for."
    ),
    responses=error_responses((422, "profile_answers_invalid")),
)
def save_profile(payload: MusicProfileIn, db: DbSession) -> MusicProfileOut:
    answers = _normalized(payload.answers)
    rules = music_profile.build(answers)
    definition = store.ensure_definition(db, store.account_language(db))
    moment = utcnow()
    for attempt in (1, 2):
        row = profile_store.of_version(db, definition.id)
        fresh = row is None
        if row is None:
            # Music has exactly one version; its profile is named after it, like the others.
            row = Profile(name=profile_store.free_name(db, music_profile.KIND, definition.label), created_at=moment)
            db.add(row)
        row.kind = music_profile.KIND
        row.answers = answers
        row.rules = rules
        row.trash_commit = ""
        row.updated_at = moment
        if fresh:
            db.flush()
            profile_store.assign(db, definition.id, row)
        try:
            db.commit()
            break
        except IntegrityError:
            # Another request created the profile in the meantime; the second attempt replaces it.
            db.rollback()
            if attempt == 2:
                raise
    logger.info("Music profile saved")
    album_quality.after_profile_change()
    return _out(row, definition.id)


@router.delete(
    "",
    status_code=204,
    response_model=None,
    summary="Remove the music profile",
    description="Without a profile no release name is judged, and no album counts as one that could be improved.",
)
def remove_profile(db: DbSession) -> None:
    definition = store.definition(db)
    if definition is None:
        return
    row = profile_store.of_version(db, definition.id)
    if row is None:
        return
    profile_store.assign(db, definition.id, None)
    db.flush()
    if not profile_store.versions_of(db, row.id):
        db.delete(row)
    db.commit()
    logger.info("Music profile removed")
    album_quality.after_profile_change()
