"""The TRaSH Guides state: which one is in use, the daily check, adopting a newer one.

TRaSH Guides data is MIT licensed; the answer carries the licence text and the copyright line for "Über
nexcrate". A newer state is never adopted on its own: the owner does it here, and a state that cannot build
every stored profile is refused.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..meldungen import error, error_responses
from ..services import trash

logger = logging.getLogger("nexcrate.trash")

router = APIRouter(prefix="/api/trash", tags=["trash"])


class TrashState(BaseModel):
    commit: str = Field(description="The full commit id of the state in use.")
    date: datetime = Field(description="The commit time, UTC.")
    source: str = Field(description="bundled or fetched.")
    update_available: bool = Field(description="The daily check found a newer data commit than the one in use.")
    latest_commit: str | None = Field(description="The newest data commit the last check found.")
    checked_at: datetime | None = Field(description="When GitHub was last asked, UTC.")
    updates_enabled: bool = Field(description="Whether the daily check runs.")
    license: str = Field(description="The licence text of the TRaSH Guides.")
    copyright: str = Field(examples=["Copyright (c) 2021 TRaSH"])
    url: str = Field(examples=["https://github.com/TRaSH-Guides/Guides"])


class TrashSettingsIn(BaseModel):
    updates_enabled: bool


@router.get(
    "",
    response_model=TrashState,
    summary="Read the TRaSH Guides state",
    description=(
        "The state in use (bundled with nexcrate or fetched later), whether the daily check found a newer one, "
        "the check switch, and the licence and copyright of the TRaSH Guides."
    ),
)
def read_state() -> TrashState:
    return TrashState.model_validate(trash.state())


@router.put(
    "/settings",
    response_model=TrashState,
    summary="Switch the daily TRaSH Guides check",
    description="Switches the daily check on or off. The check only looks for a newer state; it never adopts one.",
)
def change_settings(payload: TrashSettingsIn) -> TrashState:
    trash.set_updates_enabled(payload.updates_enabled)
    return TrashState.model_validate(trash.state())


@router.post(
    "/update",
    response_model=TrashState,
    summary="Adopt the newest TRaSH Guides state",
    description=(
        "Asks GitHub for the newest data commit, downloads it, builds every stored profile against it and only "
        "then uses it. Refused with 409 and the labels of the versions whose profile could not be built; "
        "nothing changes then. Profiles keep their rules and show as outdated until they are saved again."
    ),
    responses=error_responses(
        (409, "trash_update_breaks"),
        (502, "trash_unreachable"),
        (502, "trash_update_invalid"),
    ),
)
async def adopt_update() -> TrashState:
    try:
        await trash.adopt()
    except trash.TrashUpdateBreaks as exc:
        raise error(
            "trash_update_breaks",
            "The new TRaSH Guides state cannot build every profile; it was not adopted.",
            409,
            versions=exc.versions,
        ) from exc
    except trash.TrashUnreachable as exc:
        logger.info("Adopting a TRaSH Guides state failed: %s", exc)
        raise error("trash_unreachable", "GitHub cannot be reached right now.", 502) from exc
    except trash.TrashUpdateInvalid as exc:
        raise error("trash_update_invalid", "The download is not a usable TRaSH Guides state.", 502) from exc
    return TrashState.model_validate(await asyncio.to_thread(trash.state))
