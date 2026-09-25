"""How releases count a series, and the switch for TheXEM (S3.2, decisions 11, 12 and 15).

The owner reads and changes a series' search titles, its corrections per episode and the TMDB episode group releases
follow. TheXEM is a way out of the house and has its own switch.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..db import SessionLocal, set_setting
from ..meldungen import error, error_responses
from ..models import Title, utcnow
from ..services import tmdb
from ..services.series import numbering, tmdb_series, xem

logger = logging.getLogger("nexcrate.series")

router = APIRouter(prefix="/api", tags=["series"])


class EpisodeGroup(BaseModel):
    id: str
    name: str
    type: int | None = Field(
        description="TMDB's kind: 1 aired, 2 absolute, 3 DVD, 4 digital, 5 story, 6 production, 7 TV."
    )
    episode_count: int | None
    group_count: int | None


class CorrectionOut(BaseModel):
    episode_id: int
    code: str = Field(description="The episode as the series page shows it (TMDB's numbers).", examples=["S01E11"])
    season: int | None = Field(description="The season releases give it; null when only `absolute` is corrected.")
    episode: int | None = Field(description="The episode releases give it; null when only `absolute` is corrected.")
    episode_end: int | None = Field(description="The last episode when releases name a range for it.")
    absolute: int | None = Field(
        default=None,
        description="Anime series only: the number releases count the episode through by (`Show - 148`). Goes before "
        "TheXEM, Sonarr and TMDB's order. Sonarr has no such field.",
    )


class Numbering(BaseModel):
    title_id: int
    episode_groups: list[EpisodeGroup]
    episode_group_id: str | None = Field(description="The chosen group; null means TMDB's own numbering.")
    aliases: list[str] = Field(description="The owner's search titles, asked for first.")
    title_en: str | None = Field(description="TMDB's English name, which queries use as well.")
    corrections: list[CorrectionOut]
    use_scene_numbering: bool = Field(
        description="Whether releases are read through the scene numbering (TheXEM's or Sonarr's), as Sonarr's "
        "`useSceneNumbering`. On unless the owner switched it off."
    )
    scene_numbers: int = Field(
        description="How many episodes carry a scene number; without any the switch does nothing."
    )
    xem_state: str | None = Field(
        description="mapped, not_listed, bridge_failed, no_tvdb, or a failure code (xem_timeout, xem_certificate, "
        "xem_blocked, xem_unreachable); null before the first look."
    )
    xem_checked_at: datetime | None
    numbering_note: dict[str, Any] | None = Field(description="How the source counts against TMDB, when it differs.")


class CorrectionIn(BaseModel):
    episode_id: int
    season: int | None = Field(default=None, strict=True, description="With `episode`; left out with only `absolute`.")
    episode: int | None = Field(default=None, strict=True)
    episode_end: int | None = Field(default=None, strict=True)
    absolute: int | None = Field(
        default=None,
        strict=True,
        description="Anime series only, 1 to 9999, each number once: the number the episode is counted through by.",
    )


class NumberingIn(BaseModel):
    episode_group_id: str | None = Field(
        default="", max_length=64, description="Left out or empty text: unchanged. Null: TMDB's own numbering."
    )
    aliases: list[str] | None = Field(
        default=None, max_length=numbering.ALIASES_MAX * 2, description="Left out: unchanged. Replaces all."
    )
    corrections: list[CorrectionIn] | None = Field(
        default=None, max_length=10000, description="Left out: unchanged. Replaces all; empty removes them."
    )
    use_scene_numbering: bool | None = Field(
        default=None, description="Left out: unchanged. False reads releases without the scene numbering."
    )


class XemState(BaseModel):
    enabled: bool = Field(description="Whether nexcrate asks thexem.info at all.")
    last_ok_at: datetime | None
    last_error_code: str | None = Field(description="The code of the last failure, null after a success.")


class XemIn(BaseModel):
    enabled: bool


def _series(db: Any, title_id: int) -> Title:
    title = db.get(Title, title_id)
    if title is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    if title.kind != "series":
        raise error("invalid_input", "The input is not valid.", 422, fields=["title_id"])
    return title


def _read(title_id: int) -> Numbering:
    with SessionLocal() as db:
        return Numbering.model_validate(numbering.describe(db, _series(db, title_id)))


@router.get(
    "/series/{title_id}/numbering",
    response_model=Numbering,
    summary="Read how releases count a series",
    description=(
        "The TMDB episode groups of the series and the chosen one, the owner's search titles and corrections per "
        "episode, TheXEM's state for the series and the note how a source counts against TMDB."
    ),
    responses=error_responses((404, "not_found"), (422, "invalid_input")),
)
def read_numbering(title_id: int) -> Numbering:
    return _read(title_id)


def _check(
    title_id: int, payload: NumberingIn
) -> tuple[str | None, list[str] | None, list[numbering.Correction] | None]:
    """Checks the input; raises 422 naming the wrong fields."""
    with SessionLocal() as db:
        title = _series(db, title_id)
        group_id: str | None = title.episode_group_id
        if payload.episode_group_id is None:
            group_id = None
        elif payload.episode_group_id != "":
            if payload.episode_group_id not in {group.get("id") for group in title.episode_groups or []}:
                raise error("invalid_input", "The input is not valid.", 422, fields=["episode_group_id"])
            group_id = payload.episode_group_id
        texts = None
        if payload.aliases is not None:
            texts = numbering.clean_aliases(payload.aliases)
            if texts is None:
                raise error("invalid_input", "The input is not valid.", 422, fields=["aliases"])
        items = None
        if payload.corrections is not None:
            items = [
                numbering.Correction(item.episode_id, item.season, item.episode, item.episode_end, item.absolute)
                for item in payload.corrections
            ]
            wrong = numbering.check_corrections(db, title_id, items)
            if wrong:
                raise error(
                    "invalid_input", "The input is not valid.", 422, fields=[f"corrections.{name}" for name in wrong]
                )
        return group_id, texts, items


def _store(
    title_id: int,
    group_change: tuple[str | None, dict[int, tuple[int, int]]] | None,
    texts: list[str] | None,
    items: list[numbering.Correction] | None,
    use_scene: bool | None = None,
) -> None:
    moment = utcnow()
    with SessionLocal() as db:
        title = _series(db, title_id)
        if group_change is not None:
            stored = numbering.store_group(db, title, group_change[0], group_change[1], moment)
            logger.info(
                "Episode group of title %d: %s, %d episodes", title_id, "set" if group_change[0] else "none", stored
            )
        if texts is not None:
            numbering.set_aliases(db, title, texts, moment)
        if items is not None:
            numbering.set_corrections(db, title, items, moment)
        if use_scene is not None:
            # Null stands for on: only a switch the owner turned off is stored as such.
            title.use_scene_numbering = None if use_scene else False
        title.updated_at = moment
        db.commit()
    logger.info(
        "Numbering of title %d changed: aliases %s, corrections %s, scene numbering %s",
        title_id,
        "unchanged" if texts is None else len(texts),
        "unchanged" if items is None else len(items),
        "unchanged" if use_scene is None else ("on" if use_scene else "off"),
    )


@router.put(
    "/series/{title_id}/numbering",
    response_model=Numbering,
    summary="Change how releases count a series",
    description=(
        "Sets the TMDB episode group releases follow (null: TMDB's own numbering), replaces the search titles, "
        "replaces the corrections per episode and switches the scene numbering. What is left out stays. A chosen "
        "group is fetched from TMDB at once; when TMDB cannot deliver it, nothing changes. Search titles: at most 50, "
        "each 1 to 200 characters."
    ),
    responses=error_responses(
        (404, "not_found"),
        (422, "invalid_input"),
        (409, "tmdb_not_configured"),
        (502, "episode_group_unavailable"),
    ),
)
async def change_numbering(title_id: int, payload: NumberingIn) -> Numbering:
    group_id, texts, items = await asyncio.to_thread(_check, title_id, payload)
    current = await asyncio.to_thread(lambda: _read(title_id).episode_group_id)
    group_change: tuple[str | None, dict[int, tuple[int, int]]] | None = None
    if group_id != current:
        places: dict[int, tuple[int, int]] = {}
        if group_id is not None:
            token = await asyncio.to_thread(tmdb.require_token)
            try:
                places = numbering.group_places(await tmdb_series.fetch_episode_group(token, group_id))
            except tmdb.TmdbError as exc:
                logger.info("TMDB episode group of title %d not usable: %s", title_id, exc.code)
                raise error(
                    "episode_group_unavailable", "TMDB does not deliver this episode group right now.", 502
                ) from exc
        group_change = (group_id, places)
    await asyncio.to_thread(_store, title_id, group_change, texts, items, payload.use_scene_numbering)
    return await asyncio.to_thread(_read, title_id)


def _xem_state() -> XemState:
    with SessionLocal() as db:
        return XemState.model_validate(xem.state(db))


@router.get(
    "/settings/xem",
    response_model=XemState,
    summary="Read the TheXEM switch",
    description="Whether nexcrate asks thexem.info for scene numbers and names, and when it last worked or failed.",
)
def read_xem() -> XemState:
    return _xem_state()


@router.put(
    "/settings/xem",
    response_model=XemState,
    summary="Switch TheXEM",
    description=(
        "Off: nexcrate sends nothing to thexem.info. Numbers already stored stay and keep being used. On: the next "
        "refresh or search of a series asks again, also within the pause of six hours after a failure."
    ),
)
def change_xem(payload: XemIn) -> XemState:
    with SessionLocal() as db:
        set_setting(db, xem.SETTING_ENABLED, "1" if payload.enabled else "0")
        if payload.enabled:
            xem.end_pause(db)
        db.commit()
    logger.info("TheXEM switched %s", "on" if payload.enabled else "off")
    return _xem_state()
