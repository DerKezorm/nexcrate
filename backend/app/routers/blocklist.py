"""The blocklist of a title: releases that failed or were refused, and removing an entry (step 3).

A release counts as blocked for its title when an entry has its info hash, or its release title with the same
protocol. A blocked release can still be loaded after a confirmation.

Since the owner's finding of 20.09.2026 there is also one list over every title (``GET /api/blocklist/all``), the
newest first: after a failed download the entry is written without being asked, and it has to be findable.
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
from ..models import BlocklistEntry, Title

logger = logging.getLogger("nexcrate.downloads")

router = APIRouter(prefix="/api/blocklist", tags=["blocklist"])


class BlocklistItem(BaseModel):
    id: int
    release_title: str
    indexer: str = Field(description="The indexer's name at the time.")
    reason: str = Field(description="client_failed, encrypted, dangerous_file or removed_by_owner.")
    created_at: datetime


class BlocklistTitle(BaseModel):
    id: int
    title: str
    year: int | None
    kind: str = Field(description="movie, series or album.")


class BlocklistRow(BlocklistItem):
    title: BlocklistTitle | None = Field(description="The title the release was blocked for; null when it is gone.")


class BlocklistPage(BaseModel):
    items: list[BlocklistRow]
    total: int
    page: int
    per_page: int


PER_PAGE_MAX = 200


@router.get(
    "/all",
    response_model=BlocklistPage,
    summary="List the blocklist over every title",
    description=(
        "Every blocked release with its title, newest first. A failed download blocks its release without being "
        "asked; this is where that is seen. Removing an entry is `DELETE /api/blocklist/{entry_id}`."
    ),
)
def list_all_blocked(
    db: DbSession,
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    per_page: Annotated[int, Query(ge=1, le=PER_PAGE_MAX)] = 50,
) -> BlocklistPage:
    total = db.scalar(select(func.count()).select_from(BlocklistEntry)) or 0
    rows = db.execute(
        select(BlocklistEntry, Title)
        .join(Title, Title.id == BlocklistEntry.title_id, isouter=True)
        .order_by(BlocklistEntry.created_at.desc(), BlocklistEntry.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    ).all()
    return BlocklistPage(
        items=[
            BlocklistRow(
                id=row.id,
                release_title=row.release_title,
                indexer=row.indexer_name,
                reason=row.reason,
                created_at=row.created_at,
                title=(
                    BlocklistTitle(id=title.id, title=title.title or "", year=title.year, kind=title.kind)
                    if title is not None
                    else None
                ),
            )
            for row, title in rows
        ],
        total=int(total),
        page=page,
        per_page=per_page,
    )


@router.get(
    "",
    response_model=list[BlocklistItem],
    summary="List the blocklist of a title",
    description="Every blocked release of the title, newest first.",
)
def list_blocklist(
    db: DbSession, title_id: Annotated[int, Query(ge=1, description="The title.")]
) -> list[BlocklistItem]:
    rows = db.scalars(
        select(BlocklistEntry)
        .where(BlocklistEntry.title_id == title_id)
        .order_by(BlocklistEntry.created_at.desc(), BlocklistEntry.id.desc())
    )
    return [
        BlocklistItem(
            id=row.id,
            release_title=row.release_title,
            indexer=row.indexer_name,
            reason=row.reason,
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.delete(
    "/{entry_id}",
    status_code=204,
    response_model=None,
    summary="Remove a blocklist entry",
    description="The release is no longer blocked for its title.",
    responses=error_responses((404, "blocklist_entry_not_found")),
)
def delete_blocklist_entry(entry_id: int, db: DbSession) -> None:
    row = db.get(BlocklistEntry, entry_id)
    if row is None:
        raise error("blocklist_entry_not_found", "This blocklist entry does not exist, or not any more.", 404)
    db.delete(row)
    db.commit()
    logger.info("Blocklist entry %d removed", entry_id)
