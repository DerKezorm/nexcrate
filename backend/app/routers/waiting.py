"""Releases that wait out the delay of their version: the list over every title, loading
one at once, and taking one off.

The automatic puts a release here instead of loading it while the version's delay rule tells it to wait for a better
one; once the wait is over the best release known loads by itself. The rule itself is
``PUT /api/versions/{version_id}/delay``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..deps import DbSession
from ..meldungen import error, error_responses
from ..models import PendingRelease, Title, VersionDefinition
from ..services.automatic import settings as automatic_settings
from ..services.automatic import waiting
from ..services.downloads import loading

logger = logging.getLogger("nexcrate.downloads")

router = APIRouter(prefix="/api/waiting", tags=["waiting"])

PER_PAGE_MAX = 200


class WaitingTitle(BaseModel):
    id: int
    title: str
    year: int | None
    kind: str = Field(description="movie, series or album.")


class WaitingRelease(BaseModel):
    id: int
    release_title: str
    indexer: str = Field(description="The indexer's name when the release was kept.")
    protocol: str = Field(description="usenet or torrent.")
    size_bytes: int | None
    quality: str | None = Field(description="What it was read as when it was kept; for an album its step.")
    score: int | None = Field(description="Its custom format score then; null for an album.")
    episodes: list[str] = Field(description="The episodes of a series release it is wanted for, as codes.")
    origin: str = Field(description="search, rss or replacement: where the automatic found it.")
    published_at: datetime | None = Field(description="When the indexer published it; the wait counts from here.")
    first_seen_at: datetime
    due_at: datetime = Field(description="From when the automatic may load it, by the rule as it stands.")
    automatic_on: bool = Field(
        description="Whether the automatic of this media kind is switched on. Off, a release whose time is up stays "
        "until it is on again or the owner loads it himself."
    )
    version_id: int
    version: str = Field(description="The label of the version it waits for.")
    title: WaitingTitle


class WaitingPage(BaseModel):
    items: list[WaitingRelease]
    total: int
    page: int
    per_page: int


class LoadedOut(BaseModel):
    download_id: int


@router.get(
    "",
    response_model=WaitingPage,
    summary="List the releases that wait out a delay",
    description=(
        "Every release the automatic keeps instead of loading it, the one due first on top. `title_id` narrows the "
        "list to one title. Never a link: the release itself is kept encrypted and is not part of any answer."
    ),
)
def list_waiting(
    db: DbSession,
    title_id: Annotated[int | None, Query(ge=1, description="Only this title.")] = None,
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    per_page: Annotated[int, Query(ge=1, le=PER_PAGE_MAX)] = 50,
) -> WaitingPage:
    count = select(func.count()).select_from(PendingRelease)
    statement = (
        select(PendingRelease, Title, VersionDefinition.label)
        .join(Title, Title.id == PendingRelease.title_id)
        .join(VersionDefinition, VersionDefinition.id == PendingRelease.version_definition_id)
    )
    if title_id is not None:
        count = count.where(PendingRelease.title_id == title_id)
        statement = statement.where(PendingRelease.title_id == title_id)
    rows = db.execute(
        statement.order_by(PendingRelease.due_at, PendingRelease.id).offset((page - 1) * per_page).limit(per_page)
    ).all()
    switched_on = {kind: automatic_settings.load_enabled(db, kind) for kind in ("movie", "series", "album")}
    return WaitingPage(
        items=[
            WaitingRelease(
                id=row.id,
                release_title=row.release_title,
                indexer=row.indexer_name,
                protocol=row.protocol,
                size_bytes=row.size_bytes,
                quality=row.quality,
                score=row.score,
                episodes=list(row.episode_codes or []),
                origin=row.origin,
                published_at=row.published_at,
                first_seen_at=row.first_seen_at,
                due_at=row.due_at,
                automatic_on=switched_on.get(title.kind, False),
                version_id=row.version_definition_id,
                version=label,
                title=WaitingTitle(id=title.id, title=title.title or "", year=title.year, kind=title.kind),
            )
            for row, title, label in rows
        ],
        total=int(db.scalar(count) or 0),
        page=page,
        per_page=per_page,
    )


@router.post(
    "/{waiting_id}/load",
    status_code=201,
    response_model=LoadedOut,
    summary="Load a waiting release at once",
    description=(
        "Loads the release without waiting any longer, as a load the owner clicks: judged again against the files and "
        "the blocklist as they are now, with every check of `POST /api/downloads`. With the download everything else "
        "that waited for the same title and version goes (for a series: what shares an episode)."
    ),
    responses=error_responses((404, "waiting_release_not_found"), *loading.ERRORS),
)
async def load_waiting(waiting_id: int) -> LoadedOut:
    try:
        download_id = await waiting.load_now(waiting_id)
    except waiting.NotWaiting as exc:
        raise _missing() from exc
    except loading.LoadError as exc:
        raise exc.http() from exc
    logger.info("Waiting release %d loaded at once as download %d", waiting_id, download_id)
    return LoadedOut(download_id=download_id)


@router.delete(
    "/{waiting_id}",
    status_code=204,
    response_model=None,
    summary="Take a waiting release off",
    description=(
        "The release is not loaded when its wait is over. It is not blocked: a later search or RSS may find it again."
    ),
    responses=error_responses((404, "waiting_release_not_found")),
)
def discard_waiting(waiting_id: int) -> None:
    if not waiting.discard(waiting_id):
        raise _missing()
    logger.info("Waiting release %d taken off by the owner", waiting_id)


def _missing() -> Exception:
    return error("waiting_release_not_found", "This release does not wait any more.", 404)
