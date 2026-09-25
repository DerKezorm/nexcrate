"""The release calendar: one span of days, over movies, episodes and albums, and its subscription.

``GET /api/calendar`` answers a span, never a day at a time: the month grid asks once for its 42 days.
``GET /api/calendar/feed.ics`` is the only public route here; it opens with a key of its own and only while
the owner has it turned on (``services/calendar_feed.py``).
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field

from ..deps import DbSession
from ..meldungen import error, error_responses
from ..services import calendar_feed, release_calendar
from ..services.automatic import clock

logger = logging.getLogger("nexcrate.calendar")

router = APIRouter(prefix="/api/calendar", tags=["calendar"])
public_router = APIRouter(prefix="/api/calendar", tags=["calendar"])


class CalendarEntry(BaseModel):
    """One title on one day. What lies on disk belongs to the library; the calendar says when, not which file."""

    day: str = Field(description="YYYY-MM-DD.", examples=["2026-09-22"])
    kind: Literal["movie", "episode", "album"] = Field(description="The kind of entry, not of the title.")
    occasion: Literal["theatrical", "digital", "physical", "air", "release"] = Field(
        description="Why the title is here: a movie's cinema, digital or disc date, an episode airing, an album "
        "coming out."
    )
    country: str | None = Field(
        default=None,
        description="ISO 3166-1 of the country the movie date comes from when it is not the chosen region; null "
        "for the region's own date and for every episode and album.",
        examples=["US"],
    )
    title_id: int = Field(description="The title; for an episode, its series.")
    title: str
    year: int | None = None
    season: int | None = Field(default=None, description="Episodes only.")
    episode: int | None = Field(default=None, description="Episodes only.")
    episode_title: str | None = Field(default=None, description="Episodes only; null while TMDB has no name.")
    artist: str | None = Field(default=None, description="Albums only.")
    monitored: bool = Field(description="Whether any version of the title watches it.")
    has_file: bool = Field(description="Whether any version of the title has a file.")


class CalendarPage(BaseModel):
    from_day: str = Field(alias="from", description="The first day of the span, YYYY-MM-DD.")
    to_day: str = Field(alias="to", description="The last day of the span, YYYY-MM-DD.")
    region: str = Field(description="The region the movie dates come from; empty means the earliest date anywhere.")
    items: list[CalendarEntry]
    counts: dict[str, int] = Field(description="How many entries per kind, after the filters.")
    truncated: bool = Field(description="Whether the span held more entries than one answer carries.")

    model_config = {"populate_by_name": True}


class RegionCount(BaseModel):
    country: str = Field(description="ISO 3166-1, upper case.", examples=["DE"])
    movies: int = Field(description="How many movies carry a date of that country.")


class Regions(BaseModel):
    items: list[RegionCount]


class CalendarSettings(BaseModel):
    region: str = Field(description="ISO 3166-1 of the chosen region; empty means the earliest date anywhere.")
    feed_enabled: bool = Field(description="Whether the subscription answers at all.")
    feed_path: str = Field(description="The path of the feed, without the key.", examples=["/api/calendar/feed.ics"])
    feed_key: str = Field(
        description="The key of the subscription, for the owner to copy; empty while none was made."
    )


class SettingsIn(BaseModel):
    region: str | None = Field(default=None, description="ISO 3166-1, or an empty string for no region.")
    feed_enabled: bool | None = Field(default=None, description="Turn the subscription on or off.")


def _span(from_day: str, to_day: str) -> release_calendar.Span:
    start, end = release_calendar.parse_day(from_day), release_calendar.parse_day(to_day)
    if start is None or end is None:
        raise error("invalid_input", "from and to are days as YYYY-MM-DD.", 422, fields=["from", "to"])
    if end < start:
        raise error("invalid_input", "to lies before from.", 422, fields=["to"])
    span = release_calendar.Span(start=start, end=end)
    if span.days > release_calendar.SPAN_DAYS_MAX:
        raise error(
            "invalid_input",
            f"The span covers at most {release_calendar.SPAN_DAYS_MAX} days.",
            422,
            fields=["to"],
        )
    return span


def _kinds(value: str) -> tuple[str, ...]:
    wanted = tuple(part.strip() for part in value.split(",") if part.strip())
    unknown = [part for part in wanted if part not in release_calendar.KINDS]
    if unknown:
        raise error("invalid_input", "kinds takes movie, episode and album.", 422, fields=["kinds"])
    return wanted or release_calendar.KINDS


def _region(given: str | None, db: DbSession) -> str:
    if given is None:
        return calendar_feed.region(db)
    cleaned = given.strip().upper()
    if cleaned and (len(cleaned) != 2 or not cleaned.isalpha()):
        raise error("invalid_input", "region is a country as two letters, or empty.", 422, fields=["region"])
    return cleaned


@router.get(
    "",
    response_model=CalendarPage,
    response_model_by_alias=True,
    summary="What comes out in a span of days",
    description="Movies (cinema, digital and disc dates), episodes (when they air) and albums (when they come "
    "out), in one bounded query per kind. A movie's date is the one of the chosen region; without a date there "
    "the earliest one anywhere is taken and its country named.",
    responses=error_responses((422, "invalid_input")),
)
def list_calendar(
    db: DbSession,
    from_day: Annotated[str, Query(alias="from", description="First day, YYYY-MM-DD.")],
    to_day: Annotated[str, Query(alias="to", description="Last day, YYYY-MM-DD.")],
    kinds: Annotated[str, Query(description="Comma separated: movie, episode, album. Empty means all three.")] = "",
    region: Annotated[
        str | None, Query(description="ISO 3166-1; left out, the stored region applies.", max_length=2)
    ] = None,
    monitored: Annotated[bool, Query(description="Only titles a version watches.")] = False,
    missing: Annotated[bool, Query(description="Only titles no version has a file for.")] = False,
) -> CalendarPage:
    span = _span(from_day, to_day)
    chosen = _region(region, db)
    items, truncated = release_calendar.entries(
        db,
        span,
        kinds=_kinds(kinds),
        region=chosen,
        only_monitored=monitored,
        only_missing=missing,
    )
    return CalendarPage(
        **{"from": span.first, "to": span.last},
        region=chosen,
        items=[CalendarEntry.model_validate(item) for item in items],
        counts=release_calendar.counts_of(items),
        truncated=truncated,
    )


@router.get(
    "/regions",
    response_model=Regions,
    summary="The countries the stored release dates name",
    description="Taken from the library itself, so the list holds the countries his own movies carry, most "
    "common first. nexcrate keeps no table of countries of its own.",
)
def list_regions(db: DbSession) -> Regions:
    return Regions(items=[RegionCount.model_validate(item) for item in release_calendar.regions(db)])


@router.get(
    "/settings",
    response_model=CalendarSettings,
    summary="Region and subscription of the calendar",
    description="The key is shown to the owner so it can be copied into a calendar app. It opens the feed and "
    "nothing else, and it never reaches the log.",
)
def read_settings(db: DbSession) -> CalendarSettings:
    return CalendarSettings(
        region=calendar_feed.region(db),
        feed_enabled=calendar_feed.is_enabled(db),
        feed_path=calendar_feed.FEED_PATH,
        feed_key=calendar_feed.key(db) if calendar_feed.is_enabled(db) else "",
    )


@router.put(
    "/settings",
    response_model=CalendarSettings,
    summary="Set the region, turn the subscription on or off",
    description="Turning the subscription on the first time makes its key. Turning it off leaves the key alone; "
    "nothing answers while it is off.",
    responses=error_responses((422, "invalid_input")),
)
def write_settings(payload: SettingsIn, db: DbSession) -> CalendarSettings:
    if payload.region is not None:
        cleaned = payload.region.strip().upper()
        if cleaned and (len(cleaned) != 2 or not cleaned.isalpha()):
            raise error("invalid_input", "region is a country as two letters, or empty.", 422, fields=["region"])
        calendar_feed.set_region(db, cleaned)
    if payload.feed_enabled is not None:
        calendar_feed.enable(db, payload.feed_enabled)
    db.commit()
    return read_settings(db)


@router.post(
    "/settings/key",
    response_model=CalendarSettings,
    summary="Give the subscription a new key",
    description="The key before stops working at once; every calendar app that holds it stops getting answers.",
)
def replace_key(db: DbSession) -> CalendarSettings:
    calendar_feed.new_key(db)
    db.commit()
    return read_settings(db)


@public_router.get(
    "/feed.ics",
    summary="The calendar as an iCalendar feed",
    description="Answers only while the subscription is on and only with its key. Every entry is an all day "
    "event, so no time zone moves it.",
    responses={
        200: {
            "description": "The calendar as iCalendar text.",
            "content": {"text/calendar": {"schema": {"type": "string"}}},
        },
        **error_responses((404, "not_found")),
    },
    response_class=Response,
)
def feed(
    db: DbSession,
    key: Annotated[str, Query(description="The key of the subscription.")] = "",
    past_days: Annotated[
        int, Query(ge=0, le=calendar_feed.PAST_DAYS_MAX, description="Days before today.")
    ] = calendar_feed.PAST_DAYS_DEFAULT,
    future_days: Annotated[
        int, Query(ge=1, le=calendar_feed.FUTURE_DAYS_MAX, description="Days after today.")
    ] = calendar_feed.FUTURE_DAYS_DEFAULT,
    unmonitored: Annotated[bool, Query(description="Take titles no version watches along.")] = False,
) -> Response:
    if not calendar_feed.opens(db, key):
        # The same answer for a wrong key and for a feed that is off: neither tells a stranger anything.
        raise error("not_found", "No calendar feed here.", 404)
    today = clock.now().date()
    span = release_calendar.span_around(today, before=past_days, after=future_days)
    items = _feed_entries(db, span, monitored_only=not unmonitored)
    text = calendar_feed.ics(items, now=clock.now())
    logger.info("The calendar feed answered with %d entries over %d days", len(items), span.days)
    return Response(
        content=text,
        media_type="text/calendar; charset=utf-8",
        headers={"Cache-Control": "no-store", "Content-Disposition": 'inline; filename="nexcrate.ics"'},
    )


def _feed_entries(db: DbSession, span: release_calendar.Span, *, monitored_only: bool) -> list[dict[str, Any]]:
    """The feed may cover more days than one query carries, so it walks the span in steps of its own."""
    found: list[dict[str, Any]] = []
    step = release_calendar.SPAN_DAYS_MAX
    start = span.start
    region = calendar_feed.region(db)
    while start <= span.end:
        end = min(span.end, date.fromordinal(start.toordinal() + step - 1))
        items, _cut = release_calendar.entries(
            db,
            release_calendar.Span(start=start, end=end),
            region=region,
            only_monitored=monitored_only,
        )
        found += items
        start = date.fromordinal(end.toordinal() + 1)
    return found
