"""Subtitle files next to the movie (C9): the switch.

On until switched off. On, an import places the download's subtitle files that belong to the movie next to it, named
after the movie file with their language and tags. Off, it places none. Switching touches no file: subtitles placed
before stay, and an upgrade still sends the old file's subtitles into the recycle folder with it.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..deps import DbSession
from ..services.subtitles import settings

logger = logging.getLogger("nexcrate.subtitles")

router = APIRouter(prefix="/api/subtitles", tags=["subtitles"])


class SubtitlesOut(BaseModel):
    enabled: bool = Field(
        description="Whether an import places subtitle files next to the movie. On until switched off."
    )


class SubtitlesIn(BaseModel):
    enabled: bool = Field(
        strict=True, description="true places subtitle files next to the movie from the next import on, false stops it."
    )


@router.get(
    "",
    response_model=SubtitlesOut,
    summary="Read the switch for subtitle files",
    description=(
        "Whether an import places the download's subtitle files that belong to the movie next to it (srt, ass, ssa, "
        "vtt, sub, idx, sup, smi), named after the movie file with language, forced and SDH. On until switched off."
    ),
)
def read_subtitles(db: DbSession) -> SubtitlesOut:
    return SubtitlesOut(enabled=settings.load_enabled(db))


@router.put(
    "",
    response_model=SubtitlesOut,
    summary="Switch subtitle files on or off",
    description=(
        "Stores the switch for the next import. Off, no subtitle file is placed. Switching touches no file: subtitles "
        "placed before stay, and an upgrade still sends the old file's subtitles into the recycle folder with it. "
        "Anything but true or false answers 422 `invalid_input` with `fields` naming `enabled`."
    ),
)
def update_subtitles(payload: SubtitlesIn, db: DbSession) -> SubtitlesOut:
    before = settings.load_enabled(db)
    settings.save_enabled(db, payload.enabled)
    db.commit()
    if payload.enabled != before:
        logger.info("Subtitle files switched %s", "on" if payload.enabled else "off")
    return SubtitlesOut(enabled=payload.enabled)
