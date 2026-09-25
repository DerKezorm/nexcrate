"""Which version's "What is new" the owner has seen (built after nexbeat, 24.09.2026).

The texts live in the interface (``frontend/src/lib/whatsnew``); the server only keeps the version seen last, in the
settings table, since there is one account. A fresh installation starts with the running version as seen: a text about
what changed means nothing to someone who never had the version before.
"""

from __future__ import annotations

import re

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .. import __version__
from ..db import get_setting, set_setting
from ..deps import DbSession

router = APIRouter(tags=["system"])

SETTING_SEEN = "whats_new_seen"
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")


class WhatsNewOut(BaseModel):
    version: str = Field(description="The running version.")
    seen: str | None = Field(description="The version whose \"What is new\" was closed last; null before the first.")


class WhatsNewIn(BaseModel):
    seen: str = Field(pattern=_VERSION.pattern, description="The version whose \"What is new\" was just closed.")


@router.get(
    "/api/whats-new",
    response_model=WhatsNewOut,
    summary="Read which \"What is new\" was seen",
    description="The interface shows the window once for every version newer than `seen` that has a text.",
)
def read_whats_new(db: DbSession) -> WhatsNewOut:
    seen = get_setting(db, SETTING_SEEN, "")
    return WhatsNewOut(version=__version__, seen=seen or None)


@router.put(
    "/api/whats-new",
    response_model=WhatsNewOut,
    summary="Note that \"What is new\" was seen",
    description="Stores the version. Never goes backwards: an older version than the stored one changes nothing.",
)
def update_whats_new(payload: WhatsNewIn, db: DbSession) -> WhatsNewOut:
    stored = get_setting(db, SETTING_SEEN, "")
    if not stored or _numbers(payload.seen) > _numbers(stored):
        set_setting(db, SETTING_SEEN, payload.seen)
        db.commit()
    return read_whats_new(db)


def _numbers(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version))


def mark_seen_at_setup(db: DbSession) -> None:
    """A fresh installation has nothing to catch up on."""
    set_setting(db, SETTING_SEEN, __version__)
    db.commit()
