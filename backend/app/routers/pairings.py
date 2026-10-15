"""Programs that wait to be connected, for the owner to confirm or deny (N8), and the QR code with which the owner
connects the iPhone app without typing anything (S1)."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..deps import DbSession
from ..meldungen import error, error_responses
from ..services import pairing, totp, web

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


# --- The QR code for the app (S1) --------------------------------------------------------------------------------- #

#: The app opens links of this scheme; the iPhone's camera hands them to it.
APP_LINK = "nexcrate://connect"


class DeviceIn(BaseModel):
    address: str = Field(
        max_length=300,
        description="The address the browser reaches nexcrate at, sub path included. The address set under System "
        "goes first when there is one.",
    )
    app: str = Field(default="iPhone-App", max_length=pairing.APP_MAX_LENGTH, description="The key's name.")


class DeviceOut(BaseModel):
    pairing_id: str
    address: str = Field(description="The address the app will use.")
    link: str = Field(description="What the QR code carries: nexcrate://connect with address, pairing and secret.")
    qr_svg: str = Field(description="The QR code as SVG.")
    expires_at: datetime


class DeviceStateOut(BaseModel):
    state: str = Field(description="waiting, connected or expired.")
    key_name: str | None = Field(description="The name of the key the app got, once connected.")
    expires_at: datetime


@router.post(
    "/device",
    response_model=DeviceOut,
    status_code=201,
    summary="A QR code to connect the iPhone app",
    description=(
        "Prepares a pairing the owner approves by showing it: the app scans the code and fetches its own key with the "
        "scopes read, request and operate, once, within ten minutes. The key comes into being only then and stands "
        "under API keys like any other."
    ),
    responses=error_responses((422, "invalid_input"), (429, "pairing_limit")),
)
def prepare_device(payload: DeviceIn, db: DbSession) -> DeviceOut:
    try:
        address = web.web_url(db) or web.normalize(payload.address)
    except ValueError:
        address = ""
    if not address:
        raise error("invalid_input", "The input is not valid.", 422, fields=["address"])
    made = pairing.prepare(db, payload.app)
    link = (
        f"{APP_LINK}?address={quote(address, safe='')}&pairing={quote(made['pairing_id'], safe='')}"
        f"&secret={quote(made['secret'], safe='')}"
    )
    return DeviceOut(
        pairing_id=made["pairing_id"], address=address, link=link, qr_svg=totp.qr_svg(link),
        expires_at=made["expires_at"],
    )


@router.get(
    "/device/{pairing_id}",
    response_model=DeviceStateOut,
    summary="Whether the app took the QR code",
    description="waiting while nobody scanned it, connected with the name of the key the app got, or expired.",
    responses=error_responses((404, "pairing_not_found")),
)
def read_device(pairing_id: str, db: DbSession) -> DeviceStateOut:
    return DeviceStateOut.model_validate(pairing.prepared(db, pairing_id[:64]))


@router.delete(
    "/device/{pairing_id}",
    status_code=204,
    response_model=None,
    summary="Close the QR code",
    description="A code nobody scanned goes; one the app took already stays connected.",
)
def withdraw_device(pairing_id: str, db: DbSession) -> None:
    pairing.withdraw(db, pairing_id[:64])
