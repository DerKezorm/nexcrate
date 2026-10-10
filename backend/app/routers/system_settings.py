"""Settings of the system that face outside: the address to the outside, the sub path as
started, and the check for a newer version.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from .. import db as database
from ..config import get_settings
from ..deps import DbSession
from ..meldungen import error, error_responses
from ..services import oidc_store, updates, web
from ..vendor.nexoidc import authentik

router = APIRouter(prefix="/api/system-settings", tags=["system"])


class UpdateOut(BaseModel):
    enabled: bool = Field(description="Whether nexcrate asks GitHub once a day.")
    current: str
    latest: str | None = Field(description="The newest release; null while unknown.")
    available: bool | None = Field(description="Null while the newest release is unknown.")
    checked_at: datetime | None
    problem: str | None = Field(description="not_found (no release, or a private repository) or unreachable.")
    url: str


class SystemSettingsOut(BaseModel):
    web_url: str | None = Field(description="The address to the outside; null while none is entered.")
    url_base: str = Field(description="The sub path nexcrate started with (NEXCRATE_URL_BASE); empty for the root.")
    update: UpdateOut


class SystemSettingsIn(BaseModel):
    web_url: str | None = Field(default=None, max_length=web.MAX_LENGTH, description="Empty removes it.")
    update_check: bool | None = None
    confirm_address: bool = Field(
        default=False,
        description="The owner confirmed the question that came with 409 ``public_url_change`` for this address.",
    )


def _out(db: DbSession) -> SystemSettingsOut:
    return SystemSettingsOut(
        web_url=web.web_url(db), url_base=get_settings().url_base, update=UpdateOut(**updates.status(db))
    )


@router.get(
    "",
    response_model=SystemSettingsOut,
    summary="The address to the outside and the version check",
    description="What programs are told about reaching nexcrate, and what is known about a newer version.",
)
def read_settings(db: DbSession) -> SystemSettingsOut:
    return _out(db)


@router.put(
    "",
    response_model=SystemSettingsOut,
    summary="Change the address to the outside or the version check",
    description=(
        "The address is http or https with a host and at most a path, no query: https://example.com/nexcrate (at "
        "most 199 characters). It is the address the sign-in providers send the browser back to: when a new one "
        "leaves a redirect address behind that authentik or a provider made by hand knows, nothing is saved until "
        "``confirm_address`` is true (409 ``public_url_change`` with ``count``, ``address`` and ``hand``). "
        "Switching the check off means nexcrate never asks GitHub."
    ),
    responses=error_responses((409, "public_url_change"), (422, "invalid_input")),
)
def change_settings(payload: SystemSettingsIn, request: Request, db: DbSession) -> SystemSettingsOut:
    if payload.web_url is not None:
        try:
            value = web.normalize(payload.web_url)
        except ValueError:
            raise error("invalid_input", "The address is not valid.", 422, fields=["web_url"]) from None
        # ⚠️ This is a way to save the public address: ask before it leaves a redirect address behind at a provider.
        question = authentik.public_url_guard(
            oidc_store.SqlStore(db),
            value,
            oidc_store.request_base(str(request.base_url)),
            confirm=payload.confirm_address,
        )
        if question is not None:
            raise error(
                "public_url_change", "Saving this address changes what the providers know.", 409, **question
            )
        database.set_setting(db, web.SETTING, value)
    if payload.update_check is not None:
        database.set_setting(db, updates.SETTING_ENABLED, "on" if payload.update_check else "off")
    db.commit()
    return _out(db)


@router.post(
    "/update-check",
    response_model=UpdateOut,
    summary="Look for a newer version now",
    description="Asks GitHub at once, when the check is switched on.",
)
async def check_now() -> UpdateOut:
    return UpdateOut(**await updates.check(force=True))
