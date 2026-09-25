"""Import runs: what the imports of the sources did."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..deps import DbSession
from ..meldungen import error, error_responses
from ..models import media

router = APIRouter(prefix="/api/imports", tags=["imports"])


class ImportRun(BaseModel):
    id: int
    source_id: int
    status: str = Field(description="running, done or failed.", examples=["done"])
    started_at: datetime = Field(description="UTC.")
    finished_at: datetime | None = Field(description="UTC. Null while running.")
    titles_new: int = Field(description="Titles that did not exist before this run.")
    titles_updated: int = Field(description="Existing titles whose data or version changed.")
    versions_total: int = Field(description="Versions this source has after the run.")
    versions_removed: int = Field(description="Versions removed because the movie is gone from the source.")
    error_code: str | None = Field(
        description="Why the run failed, a code from errors.json. Null unless failed.",
        examples=["radarr_key_rejected"],
    )
    error_values: dict[str, str | int] = Field(
        default_factory=dict,
        description="The values that belong to the error text, for example `status` for radarr_http_error. "
        "Empty unless failed.",
        examples=[{"status": 500}],
    )
    details: dict[str, Any] | None = Field(
        default=None,
        description="Only for a Sonarr connection. While running `phase` (reading or series), `done` and `total`; "
        "afterwards `series`, `skipped`, `not_on_tmdb` (up to 20, each with `title` and `tvdb_id`), "
        "`not_on_tmdb_count`, `episodes_matched`, `episodes_unmatched`, `files_unmatched` and `steps` (matched "
        "episodes per matching step). Null for a Radarr connection.",
    )


def run_out(row: media.ImportRun) -> ImportRun:
    return ImportRun(
        id=row.id,
        source_id=row.source_id,
        status=row.status,
        started_at=row.started_at,
        finished_at=row.finished_at,
        titles_new=row.titles_new,
        titles_updated=row.titles_updated,
        versions_total=row.versions_total,
        versions_removed=row.versions_removed,
        error_code=row.error_code,
        error_values=dict(row.error_values or {}),
        details=row.details if isinstance(row.details, dict) else None,
    )


@router.get(
    "",
    response_model=list[ImportRun],
    summary="List the latest import runs",
    description="The newest runs first, of every source or of one. The last 100 runs per source are kept.",
)
def list_runs(
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100, description="How many runs, 1 to 100.")] = 20,
    source_id: Annotated[int | None, Query(description="Only the runs of this source.")] = None,
) -> list[ImportRun]:
    statement = select(media.ImportRun).order_by(media.ImportRun.id.desc()).limit(limit)
    if source_id is not None:
        statement = statement.where(media.ImportRun.source_id == source_id)
    return [run_out(row) for row in db.scalars(statement)]


@router.get(
    "/{run_id}",
    response_model=ImportRun,
    summary="Read one import run",
    description="Poll this after starting an import until `status` is no longer running.",
    responses=error_responses((404, "not_found")),
)
def read_run(run_id: int, db: DbSession) -> ImportRun:
    row = db.get(media.ImportRun, run_id)
    if row is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    return run_out(row)
