"""Searching and loading by itself (the design notes): the switches for movies and series,
and searching one title automatically now.

Both switches are off until the owner switches them on. Off, nothing automatic of that kind reaches an indexer or a
download client; the plan per title is still kept current, so the title page shows what would happen. Switching on while
both were off starts no burst: the buckets of the indexers start empty and due titles go through the budget.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator

from ..deps import DbSession
from ..meldungen import error, error_responses
from ..services.automatic import budget, clock, scheduler, settings, upgrade_guard
from ..services.search import jobs as search_jobs

logger = logging.getLogger("nexcrate.automatic")

router = APIRouter(tags=["automatic"])


class AutomaticOut(BaseModel):
    enabled: bool = Field(description="Whether nexcrate searches and loads movies by itself. Off until switched on.")
    series_enabled: bool = Field(
        description="Whether nexcrate searches and loads series by itself. Off until switched on."
    )
    music_enabled: bool = Field(
        default=False, description="Whether nexcrate searches and loads albums by itself. Off until switched on."
    )


class AutomaticIn(BaseModel):
    enabled: bool | None = Field(
        default=None, strict=True, description="true switches automatic searching and loading of movies on, false off."
    )
    series_enabled: bool | None = Field(
        default=None, strict=True, description="true switches automatic searching and loading of series on, false off."
    )
    music_enabled: bool | None = Field(
        default=None, strict=True, description="true switches automatic searching and loading of albums on, false off."
    )

    @model_validator(mode="after")
    def one_switch(self) -> AutomaticIn:
        if self.enabled is None and self.series_enabled is None and self.music_enabled is None:
            raise ValueError("enabled, series_enabled or music_enabled")
        return self


class AutomaticStarted(BaseModel):
    started: bool = Field(description="Always true: the search runs in the background.")


@router.get(
    "/api/automatic",
    response_model=AutomaticOut,
    summary="Read the switches for automatic searching",
    description=(
        "Whether nexcrate searches missing and upgradable movies, series and albums by itself, replaces failed "
        "downloads and loads what fits, one switch per kind. All are off until switched on; off, nothing automatic of "
        "that kind reaches an indexer or a download client. RSS runs while any is on and loads only for the kinds "
        "switched on."
    ),
)
def read_automatic(db: DbSession) -> AutomaticOut:
    return AutomaticOut(
        enabled=settings.load_enabled(db),
        series_enabled=settings.load_enabled(db, "series"),
        music_enabled=settings.load_enabled(db, "album"),
    )


@router.put(
    "/api/automatic",
    response_model=AutomaticOut,
    summary="Switch automatic searching on or off",
    description=(
        "Stores one switch or more; a field left out keeps its value, at least one is needed. Switching on while all "
        "were off starts no burst: every indexer's budget starts empty, and due titles are searched as the budget "
        "allows. Switching a kind off stops its planned searches, replacements and automatic loads; a search that "
        "already runs finishes without loading."
    ),
)
def update_automatic(payload: AutomaticIn, db: DbSession) -> AutomaticOut:
    before = settings.load_kinds(db)
    wanted = {"movie": payload.enabled, "series": payload.series_enabled, "album": payload.music_enabled}
    for kind, value in wanted.items():
        if value is not None:
            settings.save_enabled(db, value, kind)
    db.commit()
    after = settings.load_kinds(db)
    if after and not before:
        budget.reset()
    for kind in settings.KINDS:
        if (kind in after) != (kind in before):
            logger.info("Automatic searching and loading of %s switched %s", kind, "on" if kind in after else "off")
    return AutomaticOut(enabled="movie" in after, series_enabled="series" in after, music_enabled="album" in after)


class UpgradesOut(BaseModel):
    paused: bool = Field(description="Whether automatic upgrades of what was there at `paused_since` are paused.")
    paused_since: datetime | None = Field(
        description="When the pause began. A file that arrived later is upgraded as usual; null without a pause."
    )
    per_day: int = Field(description="Automatic upgrade downloads allowed in 24 hours; 0 for no limit.")
    used: int = Field(description="Automatic upgrade downloads of the last 24 hours. Missing titles never count.")


class UpgradesIn(BaseModel):
    paused: bool | None = Field(
        default=None,
        strict=True,
        description="true pauses the upgrades of everything there now (a running pause keeps its moment), false ends "
        "the pause.",
    )
    per_day: int | None = Field(
        default=None, strict=True, ge=0, le=upgrade_guard.PER_DAY_MAX, description="0 for no limit."
    )

    @model_validator(mode="after")
    def one_field(self) -> UpgradesIn:
        if self.paused is None and self.per_day is None:
            raise ValueError("paused or per_day")
        return self


def _upgrades(db: DbSession) -> UpgradesOut:
    guard = upgrade_guard.load(db, clock.now())
    return UpgradesOut(
        paused=guard.paused_since is not None,
        paused_since=guard.paused_since,
        per_day=guard.per_day,
        used=upgrade_guard.used(db, clock.now()),
    )


@router.get(
    "/api/automatic/upgrades",
    response_model=UpgradesOut,
    summary="Read how automatic upgrades are held back",
    description=(
        "The pause of the upgrades of what was there when it began, and the daily limit of automatic upgrade "
        "downloads. Neither holds back a missing title or a load by hand."
    ),
)
def read_upgrades(db: DbSession) -> UpgradesOut:
    return _upgrades(db)


@router.put(
    "/api/automatic/upgrades",
    response_model=UpgradesOut,
    summary="Pause automatic upgrades or limit them per day",
    description=(
        "`paused` true: from now on nothing that is there now is upgraded automatically; what arrives later is "
        "upgraded as usual. false ends the pause. `per_day`: at most so many automatic upgrade downloads in 24 hours, "
        "0 for no limit. A field left out keeps its value, at least one is needed. Plans follow within the next "
        "planning rounds."
    ),
)
def update_upgrades(payload: UpgradesIn, db: DbSession) -> UpgradesOut:
    now = clock.now()
    if payload.paused is True:
        upgrade_guard.pause(db, now)
    elif payload.paused is False:
        upgrade_guard.resume(db)
    if payload.per_day is not None:
        upgrade_guard.set_per_day(db, payload.per_day)
    db.commit()
    answer = _upgrades(db)
    logger.info(
        "Automatic upgrades: %s, at most %s a day",
        f"paused since {answer.paused_since.isoformat()}" if answer.paused_since else "not paused",
        answer.per_day or "no limit",
    )
    return answer


@router.post(
    "/api/library/{title_id}/search/automatic",
    status_code=202,
    response_model=AutomaticStarted,
    summary="Search a title automatically now",
    description=(
        "Runs the title's planned search at once and loads the best fitting release per version that wants something, "
        "as a planned search does, but without waiting for its time or for the indexers' budget. Only with the switch "
        "on and a version that wants something. Its outcome appears in the title's `search_plan`. A running search of "
        "the title answers 409 `search_running` with its `search_id`."
    ),
    responses=error_responses(
        (404, "not_found"),
        (409, "automatic_off"),
        (409, "nothing_wanted"),
        (409, "search_running"),
        (409, "no_indexers"),
        (409, "search_busy"),
    ),
)
def search_automatically_now(title_id: int) -> AutomaticStarted:
    try:
        scheduler.search_now(title_id)
    except scheduler.TitleMissing as exc:
        raise error("not_found", "This does not exist, or not any more.", 404) from exc
    except scheduler.AutomaticOff as exc:
        raise error("automatic_off", "Automatic searching is switched off.", 409) from exc
    except scheduler.NothingWanted as exc:
        raise error("nothing_wanted", "There is nothing to search for this title right now.", 409) from exc
    except search_jobs.SearchRunning as exc:
        raise error(
            "search_running", "A search is already running for this title.", 409, search_id=exc.search_id
        ) from exc
    except search_jobs.NoIndexers as exc:
        raise error("no_indexers", "No indexer is enabled.", 409) from exc
    except search_jobs.SearchBusy as exc:
        raise error("search_busy", "Too many searches are running at the same time.", 409) from exc
    return AutomaticStarted(started=True)
