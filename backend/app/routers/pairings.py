"""Programs that wait to be connected, for the owner to confirm or deny (N8)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..deps import DbSession
from ..meldungen import error_responses
from ..services import pairing

router = APIRouter(prefix="/api/pairings", tags=["api-keys"])


class WaitingOut(BaseModel):
    pairing_id: str
    app: str = Field(description="The name the program gave.")
    code: str = Field(description="The same code the program shows.")
    scopes: list[str] = Field(description="What the program asked for.")
    created_at: datetime
    expires_at: datetime


class WaitingListOut(BaseModel):
    items: list[WaitingOut]


class ConfirmIn(BaseModel):
    scopes: list[str] = Field(min_length=1, max_length=3, description="What the program may do; read always.")


class ConfirmedOut(BaseModel):
    key_id: int
    name: str
    scopes: list[str]


@router.get(
    "",
    response_model=WaitingListOut,
    summary="Programs waiting to be connected",
    description="The requests of the last ten minutes nobody answered yet, oldest first.",
)
def list_waiting(db: DbSession) -> WaitingListOut:
    return WaitingListOut.model_validate({"items": pairing.waiting(db)})


@router.post(
    "/{pairing_id}/confirm",
    response_model=ConfirmedOut,
    summary="Connect a program",
    description=(
        "Makes a key under the program's name with the chosen scopes. The program fetches it once; it then stands in "
        "the list of keys like any other and can be revoked."
    ),
    responses=error_responses((404, "pairing_not_found"), (422, "invalid_input"), (409, "api_key_limit")),
)
def confirm(pairing_id: str, payload: ConfirmIn, db: DbSession) -> ConfirmedOut:
    return ConfirmedOut.model_validate(pairing.confirm(db, pairing_id[:64], list(payload.scopes)))


@router.post(
    "/{pairing_id}/deny",
    status_code=204,
    response_model=None,
    summary="Refuse a program",
    description="The program learns that it was refused; no key comes into being.",
    responses=error_responses((404, "pairing_not_found")),
)
def deny(pairing_id: str, db: DbSession) -> None:
    pairing.deny(db, pairing_id[:64])
