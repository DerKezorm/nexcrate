"""``/api/v1/pairing``: a program asks for a key in one step (N8).

The only addresses of ``/api/v1`` without a key: a program that has none yet asks here, and learns with the secret of
its request whether the owner confirmed. It never learns anything else: no key, no title, no setting, and an unknown
request and a wrong secret answer the same.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..deps import DbSession
from ..meldungen import v1_error_responses
from ..services import pairing

router = APIRouter(prefix="/api/v1", tags=["v1"])


class PairingIn(BaseModel):
    app: str = Field(
        min_length=1, max_length=pairing.APP_MAX_LENGTH, description="The program's name, shown to the owner."
    )
    scopes: list[str] = Field(
        min_length=1, max_length=3, description="read, request, operate; the owner may choose fewer."
    )


class PairingOut(BaseModel):
    pairing_id: str
    secret: str = Field(description="Send it as X-Pairing-Secret when asking how the request stands. Shown once.")
    code: str = Field(description="Show it to the owner: nexcrate shows the same.", examples=["7Q4-K2P"])
    expires_at: datetime
    poll_seconds: int = Field(description="Ask again after this many seconds.")


class PairingStateOut(BaseModel):
    state: str = Field(description="pending, confirmed (this answer carries the key), delivered, denied or expired.")
    key: str | None = Field(description="The key, exactly once: in the first answer after the owner confirmed.")
    scopes: list[str] | None = Field(description="What the owner granted, with the key.")
    expires_at: datetime


@router.post(
    "/pairing",
    response_model=PairingOut,
    status_code=201,
    summary="Ask to be connected",
    description=(
        "Needs no key. nexcrate shows the request with its code to the owner, who confirms it with the scopes of "
        "the owner's choice or denies it. Ask GET /api/v1/pairing/{pairing_id} with the secret until it is decided; "
        f"a request lives {int(pairing.LIFETIME.total_seconds() // 60)} minutes, "
        f"at most {pairing.PENDING_MAX} wait at once."
    ),
    responses=v1_error_responses((422, "invalid_input"), (429, "pairing_limit")),
)
def ask(payload: PairingIn, db: DbSession) -> JSONResponse:
    answer = PairingOut.model_validate(pairing.ask(db, payload.app, list(payload.scopes)))
    return JSONResponse(status_code=201, content=answer.model_dump(mode="json"))


@router.get(
    "/pairing/{pairing_id}",
    response_model=PairingStateOut,
    summary="How a request to be connected stands",
    description=(
        "With the header X-Pairing-Secret. Confirmed, the answer carries the key once; from then on the request is "
        "delivered. An unknown request and a wrong secret both answer 404."
    ),
    responses=v1_error_responses((404, "pairing_not_found")),
)
def poll(
    pairing_id: str,
    db: DbSession,
    secret: Annotated[str | None, Header(alias="X-Pairing-Secret", max_length=100)] = None,
) -> PairingStateOut:
    return PairingStateOut.model_validate(pairing.poll(db, pairing_id[:64], secret))
