"""What the fixed jumps into the interface (``/open/…``, the design notes, N21) point at: the page resolves a
reference with the session and goes on. Only numbers and kinds; the pages keep their own addresses.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..deps import DbSession
from ..meldungen import error, error_responses
from ..models import Download, VersionDefinition
from ..models.downloads import FINISHED_STATES
from ..services.api_v1 import KINDS, refs, titles
from ..services.downloads import store as download_store

router = APIRouter(prefix="/api/open", tags=["library"])


class TitleTarget(BaseModel):
    title_id: int | None = Field(description="The title's page; null for an artist.")
    artist_id: int | None = Field(default=None, description="The artist's page; null for a title.")


class VersionTarget(BaseModel):
    kind: str = Field(description="movie, series or album: the tab of the versions.")


class DownloadTarget(BaseModel):
    view: str = Field(description="active, problems or history: the tab the download stands in.")


def _gone() -> Exception:
    return error("not_found", "This does not exist, or not any more.", 404)


@router.get(
    "/title/{kind}/{ref}",
    response_model=TitleTarget,
    summary="The title a jump names",
    description="By kind and reference, as /api/v1 names titles.",
    responses=error_responses((404, "not_found")),
)
def open_title(kind: str, ref: str, db: DbSession) -> TitleTarget:
    if kind not in KINDS:
        raise _gone()
    try:
        found = titles.find(db, kind, refs.parse(kind, ref))
    except refs.RefError:
        raise _gone() from None
    if not found:
        raise _gone()
    if found[0] < 0:
        # An artist stands under the negative of its row number.
        return TitleTarget(title_id=None, artist_id=-found[0])
    return TitleTarget(title_id=found[0])


@router.get(
    "/version/{version_id}",
    response_model=VersionTarget,
    summary="The version a jump names",
    description="By its fixed id.",
    responses=error_responses((404, "not_found")),
)
def open_version(version_id: str, db: DbSession) -> VersionTarget:
    kind = db.scalar(select(VersionDefinition.kind).where(VersionDefinition.public_id == version_id[:16]))
    if kind is None:
        raise _gone()
    return VersionTarget(kind=kind)


@router.get(
    "/download/{download_id}",
    response_model=DownloadTarget,
    summary="The download a jump names",
    description="Which tab of the downloads shows it.",
    responses=error_responses((404, "not_found")),
)
def open_download(download_id: int, db: DbSession) -> DownloadTarget:
    row = db.get(Download, download_id)
    if row is None:
        raise _gone()
    client_code = None
    if download_store.problem_of(row, client_code) is not None:
        return DownloadTarget(view="problems")
    return DownloadTarget(view="history" if row.state in FINISHED_STATES else "active")
