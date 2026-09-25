"""The recycle bin: how many days a file stays in a recycle folder before the daily cleanup deletes it, and since
/api/v1 V2 its entries: list, put back, delete for good, empty."""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..deps import DbSession
from ..meldungen import error, error_responses
from ..models import Version
from ..services import recycle, recycle_bin

logger = logging.getLogger("nexcrate.recycle")

router = APIRouter(prefix="/api/recycle", tags=["recycle"])


class RecycleOut(BaseModel):
    days: int = Field(
        description=f"Days an old file stays in `.nexcrate-recycle` before it is deleted, {recycle.MIN_DAYS} to "
        f"{recycle.MAX_DAYS}.",
        examples=[recycle.DEFAULT_DAYS],
    )


class RecycleIn(BaseModel):
    days: int = Field(description=f"{recycle.MIN_DAYS} to {recycle.MAX_DAYS}.", examples=[14])


@router.get(
    "",
    response_model=RecycleOut,
    summary="Read the recycle time",
    description=(
        "How many days an old file stays in the recycle folder `.nexcrate-recycle` next to the movie folders before "
        f"the daily cleanup deletes it. {recycle.DEFAULT_DAYS} until changed."
    ),
)
def read_recycle(db: DbSession) -> RecycleOut:
    return RecycleOut(days=recycle.load_days(db))


@router.put(
    "",
    response_model=RecycleOut,
    summary="Change the recycle time",
    description=(
        f"Stores the days, {recycle.MIN_DAYS} to {recycle.MAX_DAYS}; the next daily cleanup uses them. Outside that "
        "range the answer is 422 `invalid_input` with `fields` naming `days`."
    ),
)
def update_recycle(payload: RecycleIn, db: DbSession) -> RecycleOut:
    if not recycle.valid(payload.days):
        raise error("invalid_input", "The input is not valid.", 422, fields=["days"])
    recycle.save_days(db, payload.days)
    db.commit()
    logger.info("Recycle time set to %d days", payload.days)
    return RecycleOut(days=recycle.load_days(db))


# --- The entries --------------------------------------------------------------------------- #


class BinEntry(BaseModel):
    id: int
    title_id: int | None = Field(description="Null when the title left the library.")
    kind: str
    title: str
    year: int | None
    version_label: str
    season: int | None
    episodes: list[int]
    file_name: str = Field(description="The file's own name, never its folder.")
    size_bytes: int
    deleted_at: datetime
    deleted_by: str = Field(description="owner, or key: a program; `deleted_by_name` is then its key's name.")
    deleted_by_name: str | None
    present: bool = Field(description="False when the file is gone or its disk cannot be seen right now.")
    in_library: bool = Field(description="False when the title or its version left the library: it cannot come back.")


class BinList(BaseModel):
    items: list[BinEntry] = Field(description="Newest first.")
    size_bytes: int = Field(description="Of every entry together.")


@router.get(
    "/bin",
    response_model=BinList,
    summary="List the recycle bin",
    description="Every file deleted into the bin, newest first, with who deleted it and whether it can come back.",
)
def list_bin(db: DbSession) -> BinList:
    listed = recycle_bin.listed(db)
    title_ids = [item["title_id"] for item in listed if item["title_id"] is not None]
    present = set(
        db.execute(
            select(Version.title_id, Version.version_definition_id).where(Version.title_id.in_(title_ids))
        ).tuples()
    )
    items = [
        BinEntry(
            id=item["id"],
            title_id=item["title_id"],
            kind=item["kind"],
            title=item["title"],
            year=item["year"],
            version_label=item["version_label"],
            season=item["season"],
            episodes=item["episodes"],
            file_name=item["file_name"],
            size_bytes=item["size"],
            deleted_at=item["deleted_at"],
            deleted_by=item["deleted_by"],
            deleted_by_name=item["deleted_by_name"],
            present=item["present"],
            in_library=(item["title_id"], item["version_definition_id"]) in present,
        )
        for item in listed
    ]
    return BinList(items=items, size_bytes=sum(item.size_bytes for item in items))


class RestoredEntry(BaseModel):
    title_id: int
    kind: str


_RESTORE_ERRORS = (
    (404, "not_found"),
    (409, "recycle_target_taken"),
    (409, "recycle_slot_taken"),
    (409, "recycle_title_gone"),
    (409, "recycle_file_gone"),
    (409, "version_fed_by_source"),
)


@router.post(
    "/bin/{entry_id}/restore",
    response_model=RestoredEntry,
    summary="Put a file back",
    description=(
        "The file goes back where it was and counts again. Refused when something lies there, the version or episode "
        "has another file by now, the title or version left the library, or the file is gone; it then stays in the bin."
    ),
    responses=error_responses(*_RESTORE_ERRORS),
)
def restore_entry(entry_id: int) -> RestoredEntry:
    answer = recycle_bin.restore(entry_id)
    return RestoredEntry(title_id=answer["title_id"], kind=answer["kind"])


@router.delete(
    "/bin/{entry_id}",
    status_code=204,
    response_model=None,
    summary="Delete a file for good",
    description=(
        "The file and its subtitles are deleted now, not after the recycle time. Only here, never by a program."
    ),
    responses=error_responses((404, "not_found")),
)
def purge_entry(entry_id: int) -> None:
    recycle_bin.purge(entry_id)


class Emptied(BaseModel):
    deleted: int


@router.delete(
    "/bin",
    response_model=Emptied,
    summary="Empty the recycle bin",
    description="Every file in the bin is deleted for good now.",
)
def empty_bin() -> Emptied:
    return Emptied(deleted=recycle_bin.empty())
