"""release.nex next to every movie nexcrate owns: the switch, the check and the backfill, and "replace".

the design notes, L3. The check and the backfill run as jobs in the background;
``GET /api/companions/job`` follows the newest one. Nothing here writes into a folder Radarr still controls.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..db import SessionLocal
from ..meldungen import error, error_responses
from ..services import companion_jobs, companions

router = APIRouter(prefix="/api/companions", tags=["companions"])


class CompanionProgress(BaseModel):
    done: int = Field(description="Versions looked at.")
    total: int = Field(description="Versions nexcrate owns with a file.")


class CompanionExample(BaseModel):
    version_id: int
    title_id: int
    folder: str | None = Field(description="The movie folder's name, never a path.", examples=["Example Movie (2003)"])


class CompanionResult(BaseModel):
    versions: int = Field(description="Versions nexcrate owns with a file, the ones the job looked at.")
    counts: dict[str, int] = Field(
        description="Versions per state: written, current, missing, outdated, other_installation, newer_format, "
        "broken, foreign, changed, not_writable, no_space, folder_missing, file_missing, failed. Every state is named, "
        "zero when none. A check counts missing and outdated where a run writes."
    )
    examples: dict[str, list[CompanionExample]] = Field(
        description="Up to 20 example folders per state other than current and written."
    )


class CompanionJob(BaseModel):
    id: int
    kind: Literal["check", "backfill"]
    state: Literal["running", "done", "failed"]
    progress: CompanionProgress
    started_at: datetime = Field(description="UTC.")
    finished_at: datetime | None = Field(description="UTC. Null while running.")
    error_code: str | None = Field(
        description="Why a failed job failed: companions_off when the switch went off during a run, else "
        "internal_error. Null unless failed."
    )
    result: CompanionResult | None = Field(description="Null unless done.")


class CompanionsOut(BaseModel):
    enabled: bool = Field(description="The switch: whether nexcrate writes and removes release.nex at all.")
    job: CompanionJob | None = Field(description="The newest check or backfill of the last 30 minutes, if any.")


class CompanionsIn(BaseModel):
    enabled: bool = Field(description="Off: nothing is written or removed anywhere; the stored states stay.")


class ReplaceIn(BaseModel):
    version_id: int = Field(ge=1, description="The version whose release.nex is to be replaced.")


class ReplaceOut(BaseModel):
    state: str = Field(
        description="written, or the state that stopped it: folder_missing, file_missing, not_writable, no_space, "
        "failed, or a read outcome of a file that appeared meanwhile.",
        examples=["written"],
    )


def _status() -> CompanionsOut:
    with SessionLocal() as db:
        on = companions.enabled(db)
    job = companion_jobs.latest()
    return CompanionsOut(enabled=on, job=CompanionJob.model_validate(job) if job is not None else None)


@router.get(
    "",
    response_model=CompanionsOut,
    summary="Read the companion file settings and the newest job",
    description=(
        "Whether nexcrate writes release.nex next to the movies it owns, and the newest check or backfill that runs "
        "or ended less than 30 minutes ago. Jobs live in memory; after a restart there is none."
    ),
)
def read_companions() -> CompanionsOut:
    return _status()


@router.put(
    "",
    response_model=CompanionsOut,
    summary="Switch writing companion files on or off",
    description=(
        "Off: nexcrate writes no release.nex when it files a download away or takes a connection over, and removes "
        "none when a version goes back to Radarr or is removed; the stored states stay. After a loss of the database "
        "the movies then have to be assigned by hand. On by default."
    ),
    responses=error_responses((422, "invalid_input")),
)
def update_companions(payload: CompanionsIn) -> CompanionsOut:
    with SessionLocal() as db:
        companions.set_enabled(db, payload.enabled)
        db.commit()
    return _status()


_JOB_ERRORS = ((409, "companion_job_running"),)


def _start(kind: str) -> CompanionJob:
    try:
        job = companion_jobs.start(kind)
    except companion_jobs.CompanionJobRunning as exc:
        raise error(
            "companion_job_running", "A check or backfill of the companion files is already running.", 409
        ) from exc
    except companion_jobs.CompanionsOff as exc:
        raise error("companions_off", "Writing companion files is switched off.", 409) from exc
    return CompanionJob.model_validate(job)


@router.post(
    "/check",
    status_code=202,
    response_model=CompanionJob,
    summary="Check the companion files of every movie nexcrate owns",
    description=(
        "Starts a check in the background and answers at once with the running job; follow it with "
        "`GET /api/companions/job`. Per version nexcrate owns with a file it reads the folder's release.nex, compares "
        "it with what nexcrate would write and stores the state on the version. Nothing on disk is written. One job at "
        "a time, and none while a takeover writes its companion files."
    ),
    responses=error_responses(*_JOB_ERRORS),
)
def check_companions() -> CompanionJob:
    return _start("check")


@router.post(
    "/backfill",
    status_code=202,
    response_model=CompanionJob,
    summary="Write the missing and outdated companion files",
    description=(
        "Starts the backfill in the background and answers at once with the running job. It does the check's work "
        "and writes release.nex where it is missing or outdated, one folder after another with a short pause after "
        "each write. Files nexcrate does not own (foreign, broken, newer, of another installation, changed by hand) "
        "are counted and left alone; `POST /api/companions/replace` handles them one by one. Needs the switch on."
    ),
    responses=error_responses(*_JOB_ERRORS, (409, "companions_off")),
)
def backfill_companions() -> CompanionJob:
    return _start("backfill")


@router.get(
    "/job",
    response_model=CompanionJob,
    summary="Follow the newest check or backfill",
    description=(
        "The newest job that runs or ended less than 30 minutes ago. Jobs live in memory; after a restart there is "
        "none."
    ),
    responses=error_responses((404, "not_found")),
)
def read_job() -> CompanionJob:
    job = companion_jobs.latest()
    if job is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    return CompanionJob.model_validate(job)


def _replace(version_id: int) -> ReplaceOut:
    try:
        state = companion_jobs.replace(version_id)
    except companion_jobs.VersionMissing as exc:
        raise error("not_found", "This does not exist, or not any more.", 404) from exc
    except companion_jobs.CompanionsOff as exc:
        raise error("companions_off", "Writing companion files is switched off.", 409) from exc
    except companion_jobs.NotReplaceable as exc:
        raise error(
            "companion_not_replaceable",
            "This release.nex cannot be replaced: only a foreign, broken, newer or hand-changed file, or one of "
            "another installation, is replaced.",
            409,
            state=exc.state,
        ) from exc
    return ReplaceOut(state=state)


@router.post(
    "/replace",
    response_model=ReplaceOut,
    summary="Replace a release.nex nexcrate refuses to overwrite",
    description=(
        "For a version whose stored state is foreign, broken, newer_format, other_installation or changed: the "
        "existing file moves into `.nexcrate-recycle/<date>/<movie folder>/` of the version's root folder, then "
        "nexcrate's file is written. Never for a version a source feeds. The answer names the state written, or the "
        "one that stopped it."
    ),
    responses=error_responses(
        (404, "not_found"), (409, "companions_off"), (409, "companion_not_replaceable"), (422, "invalid_input")
    ),
)
async def replace_companion(payload: ReplaceIn) -> ReplaceOut:
    return await asyncio.to_thread(_replace, payload.version_id)

