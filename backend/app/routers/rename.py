"""The mass rename of files and folders: preview, run, the last run and its undo, one title.

A kind is ``movie``, ``series`` or ``music``; the unit of music is the artist. Paths in answers are relative to the
version's root folder. Nothing here writes to Radarr, Sonarr or Lidarr, and versions they feed are never touched.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from ..db import SessionLocal
from ..meldungen import error, error_responses
from ..models import Title
from ..services.rename import jobs
from ..services.rename.plan import TitlePlan

logger = logging.getLogger("nexcrate.rename")

router = APIRouter(prefix="/api/rename", tags=["rename"])

Kind = Literal["movie", "series", "music"]
#: Moves listed per unit in the preview list; the unit's own route lists up to ``plan.STEPS_SHOWN``.
LIST_STEPS = 12


class Step(BaseModel):
    what: Literal["file", "extra", "other"] = Field(
        description="file: a video or track; extra: a subtitle or lyrics; other: anything else in the folder."
    )
    old: str
    new: str


class FolderChange(BaseModel):
    old: str = Field(description="Empty when the file lies straight in the root folder.")
    new: str


class Album(BaseModel):
    title_id: int
    folder: str = Field(description="The album folder's new name.")
    files: int = Field(description="Tracks of the album that move.")


class Note(BaseModel):
    code: str = Field(
        description="folder_shared, artist_folder_shared, folder_split, file_missing; values as `name` or `title_id`."
    )
    values: dict[str, Any] = Field(default_factory=dict)


class Unit(BaseModel):
    id: int = Field(description="The title for movies and series, the artist for music.")
    kind: Kind
    title_id: int
    name: str
    files: int = Field(description="Videos or tracks that get another name or place.")
    moves: int = Field(description="Every file that moves, extras and other files included.")
    folders: list[FolderChange]
    steps: list[Step]
    more: int = Field(description="Moves not listed in `steps`.")
    notes: list[Note]
    skip: str | None = Field(
        description="Why the unit is left out: folder_missing, file_missing, busy, link, target_taken, target_twice, "
        "target_planned, several_roots, file_twice; null when it is renamed."
    )
    skip_values: dict[str, Any]
    left: int = Field(description="Files that stay in the old folder although their album moves out.")
    albums: list[Album]


class Counts(BaseModel):
    titles: int = Field(description="Units that would be renamed.")
    files: int
    moves: int
    folders: int
    skipped: int


class PreviewOut(BaseModel):
    kind: Kind
    computed_at: datetime | None = Field(description="Null before the first preview of this kind.")
    counts: Counts | None
    units: list[Unit]


class JobOut(BaseModel):
    id: str
    action: Literal["preview", "run", "undo"]
    kind: Kind
    state: Literal["running", "done", "failed"]
    done: int
    total: int
    started_at: datetime
    finished_at: datetime | None
    run_id: int | None
    result: dict[str, Any] = Field(
        description="preview: the counts; run: done, skipped, failed, files, folders, reasons; undo: undone, kept, "
        "reasons."
    )
    error: str | None


class LastRunOut(BaseModel):
    id: int
    kind: Kind
    state: Literal["done", "undone"]
    started_at: datetime
    finished_at: datetime | None
    undone_at: datetime | None
    counts: dict[str, Any]
    can_undo: bool


class PreviewIn(BaseModel):
    kind: Kind


class RunIn(BaseModel):
    kind: Kind
    ids: list[int] | None = Field(
        default=None,
        max_length=100_000,
        description="Titles (movies, series) or artists (music) to rename; null renames every unit of the last preview "
        "that is not left out.",
    )


def unit_out(found: TitlePlan, steps: int = LIST_STEPS) -> Unit:
    shown = jobs.shown_steps(found, steps)
    return Unit(
        id=found.artist_id if found.kind == "music" and found.artist_id is not None else found.title_id,
        kind=found.kind,  # type: ignore[arg-type]
        title_id=found.title_id,
        name=found.name,
        files=found.file_count(),
        moves=len(found.moves),
        folders=[FolderChange(old=old, new=new) for old, new in found.folders],
        steps=[Step.model_validate(step) for step in shown],
        more=max(0, len(found.moves) - len(shown)),
        notes=[
            Note(code=note["code"], values={key: value for key, value in note.items() if key != "code"})
            for note in found.notes
        ],
        skip=found.skip,
        skip_values=found.skip_values,
        left=found.left,
        albums=[Album(title_id=title_id, folder=name, files=count) for title_id, name, count in found.albums],
    )


def _job_out(job: jobs.Job) -> JobOut:
    return JobOut.model_validate(jobs.snapshot(job))


def _running() -> Exception:
    return error(
        "rename_running", "A rename, or the check of the release.nex files, is running. Please wait for it.", 409
    )


@router.get(
    "/preview",
    response_model=PreviewOut,
    summary="Read the rename preview of a kind",
    description=(
        "The units of the last preview of this kind that would change or are left out, with their folders and the "
        f"first {LIST_STEPS} moves each. Units without a change are not listed. The preview is kept until the next "
        "preview or run of the kind; `computed_at` is null before the first."
    ),
)
def read_preview(kind: Kind = Query(description="movie, series or music.")) -> PreviewOut:
    stored = jobs.preview(kind)
    if stored is None:
        return PreviewOut(kind=kind, computed_at=None, counts=None, units=[])
    return PreviewOut(
        kind=kind,
        computed_at=stored.computed_at,
        counts=Counts.model_validate(jobs.summary(stored.plans)),
        units=[unit_out(item) for item in stored.plans],
    )


@router.post(
    "/preview",
    status_code=202,
    response_model=JobOut,
    summary="Compute the rename preview of a kind",
    description=(
        "Starts computing, for every title (music: every artist) with files of nexcrate's own, where its files and "
        "folders would go under the naming in use. Reads the disk, changes nothing. Poll `GET /api/rename/job`."
    ),
    responses=error_responses((409, "rename_running")),
)
def start_preview(payload: PreviewIn) -> JobOut:
    try:
        job = jobs.start_preview(payload.kind)
    except jobs.JobRunning as exc:
        raise _running() from exc
    return _job_out(job)


@router.get(
    "/preview/{kind}/{unit_id}",
    response_model=Unit,
    summary="Read one unit of the rename preview",
    description="One title or artist of the stored preview with up to 400 of its moves.",
    responses=error_responses((404, "not_found")),
)
def read_unit(kind: Kind, unit_id: int) -> Unit:
    stored = jobs.preview(kind)
    found = next(
        (
            item
            for item in (stored.plans if stored else [])
            if (item.artist_id if kind == "music" else item.title_id) == unit_id
        ),
        None,
    )
    if found is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    return unit_out(found, steps=10_000)


@router.post(
    "/run",
    status_code=202,
    response_model=JobOut,
    summary="Rename files and folders",
    description=(
        "Renames the given units, or every ready unit of the last preview. Each unit is planned again right before it "
        "moves, moved with `rename` inside its root folder (never copied, never over another file), and taken back "
        "when anything fails. A title that is downloading or filing waits for another run (`busy`). Poll "
        "`GET /api/rename/job`."
    ),
    responses=error_responses((409, "rename_running")),
)
def start_run(payload: RunIn) -> JobOut:
    try:
        job = jobs.start_run(payload.kind, payload.ids)
    except jobs.JobRunning as exc:
        raise _running() from exc
    logger.info("Rename run of %s started for %d units", payload.kind, job.total)
    return _job_out(job)


@router.get(
    "/job",
    response_model=JobOut | None,
    summary="Read the rename job",
    description="The running rename job, or the last one for 30 minutes after it ended; null without one.",
)
def read_job() -> JobOut | None:
    job = jobs.latest()
    return _job_out(job) if job is not None else None


@router.get(
    "/last",
    response_model=LastRunOut | None,
    summary="Read the last rename run",
    description="The newest finished run with its counts and whether it can still be undone; null without one.",
)
def read_last() -> LastRunOut | None:
    last = jobs.last_run()
    return LastRunOut.model_validate(last) if last is not None else None


@router.post(
    "/last/undo",
    status_code=202,
    response_model=JobOut,
    summary="Undo the last rename run",
    description=(
        "Takes every title of the last run back to its old names and places, as long as its rows still hold what the "
        "run wrote, its files lie where the run put them and nothing happened to it since. The others stay and are "
        "counted with their reason (`changed`, `files_moved`, `busy`). Poll `GET /api/rename/job`."
    ),
    responses=error_responses((409, "rename_running"), (409, "rename_nothing_to_undo")),
)
def undo_last() -> JobOut:
    try:
        job = jobs.start_undo()
    except LookupError as exc:
        raise error("rename_nothing_to_undo", "There is no rename run to undo.", 409) from exc
    except jobs.JobRunning as exc:
        raise _running() from exc
    return _job_out(job)


def _kind_of(title_id: int) -> str:
    with SessionLocal() as db:
        title = db.get(Title, title_id)
        if title is None:
            raise error("not_found", "This does not exist, or not any more.", 404)
        return {"movie": "movie", "series": "series", "album": "music"}[title.kind]


@router.get(
    "/titles/{title_id}",
    response_model=Unit | None,
    summary="Preview the rename of one title",
    description=(
        "Where the files and folders of one movie, series or album would go. An album stays in its artist folder. "
        "Null when nothing would change."
    ),
    responses=error_responses((404, "not_found")),
)
def preview_title(title_id: int) -> Unit | None:
    kind = _kind_of(title_id)
    found = jobs.plan_unit(kind, title_id, album=kind == "music")
    if found is None or (found.skip is None and not found.changed):
        return None
    return unit_out(found, steps=10_000)


@router.post(
    "/titles/{title_id}",
    status_code=202,
    response_model=JobOut,
    summary="Rename one title",
    description="Renames one movie, series or album as its preview shows. Poll `GET /api/rename/job`.",
    responses=error_responses((404, "not_found"), (409, "rename_running")),
)
def rename_title(title_id: int) -> JobOut:
    kind = _kind_of(title_id)
    try:
        job = jobs.start_run(kind, [title_id], album=kind == "music")
    except jobs.JobRunning as exc:
        raise _running() from exc
    return _job_out(job)
