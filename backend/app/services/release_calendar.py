"""The release calendar: what comes out in a span of days, over movies, episodes and albums.

Reads only what is stored already. A movie's dates come from ``titles.release_dates`` (TMDB's
``release_dates.results``, raw per country), an episode's from ``episodes.air_date``, an album's from
``titles.first_release_date``.

**Regions.** TMDB has a date per country, and they differ: a movie opens in the US weeks before it opens in
Germany. The calendar picks the date of one region and says so; without a date there it falls back to the
earliest date anywhere and names that country. Radarr, Sonarr and Lidarr know one date and no region at all.

**An entry is a title and a day**, not a version: the calendar answers "when does the next one come", and
what lies on disk is the library's job. It carries ``monitored`` and ``has_file`` for the filters only.

**Bounds, not a loop per day.** Every kind is one bounded query over the span. ``release_dates`` is a JSON
blob with no index, so the span is first narrowed by the months it touches (a text match on the blob, which
can only add candidates, never drop one), and only those rows are unpacked. Measured in
the test bench.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import Text, cast, func, or_, select, text
from sqlalchemy.orm import Session as OrmSession

from ..models import Artist, Episode, EpisodeVersion, Title, Version
from .automatic import anchors

logger = logging.getLogger("nexcrate.calendar")

#: The kinds of entry the calendar knows. An entry's kind, not a title's: an episode belongs to a series.
KINDS = ("movie", "episode", "album")
#: Why a title is in the calendar on that day.
OCCASIONS = ("theatrical", "digital", "physical", "air", "release")
#: Which TMDB release types make which occasion (see ``anchors``: 2 limited, 3 theatrical, 4 digital, 5 physical).
OCCASION_TYPES: dict[str, tuple[int, ...]] = {
    "theatrical": anchors.THEATRICAL_TYPES,
    "digital": (anchors.DIGITAL,),
    "physical": (anchors.PHYSICAL,),
}
#: At most this many days in one request: the month grid needs 42, the list view 60.
SPAN_DAYS_MAX = 100
#: At most this many entries in one answer. More would not be read; the answer says that it was cut.
ENTRIES_MAX = 2_000
#: A full day, as MusicBrainz writes a partial date: ``2019``, ``2019-03`` and ``2019-03-01`` are all stored.
FULL_DAY_LENGTH = 10


@dataclass(frozen=True)
class Span:
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def first(self) -> str:
        return self.start.isoformat()

    @property
    def last(self) -> str:
        return self.end.isoformat()


def parse_day(value: str) -> date | None:
    """``YYYY-MM-DD`` as a date; None for anything else, including a partial date such as ``2019-03``."""
    text = (value or "").strip()
    if len(text) != FULL_DAY_LENGTH:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def months_of(span: Span) -> list[str]:
    """``YYYY-MM`` of every month the span touches, for the pre-filter over the JSON blob."""
    months: list[str] = []
    year, month = span.start.year, span.start.month
    while (year, month) <= (span.end.year, span.end.month):
        months.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def _flags_by_title(db: OrmSession, title_ids: list[int]) -> dict[int, tuple[bool, bool]]:
    """Per title: is any version watched, and does any version have a file. One query for all of them."""
    if not title_ids:
        return {}
    rows = db.execute(
        select(
            Version.title_id,
            func.max(func.iif(Version.monitored, 1, 0)),
            func.max(func.iif(Version.has_file, 1, 0)),
        )
        .where(Version.title_id.in_(title_ids))
        .group_by(Version.title_id)
    ).all()
    return {title_id: (bool(monitored), bool(has_file)) for title_id, monitored, has_file in rows}


def _keep(entry: dict[str, Any], *, only_monitored: bool, only_missing: bool) -> bool:
    if only_monitored and not entry["monitored"]:
        return False
    return not (only_missing and entry["has_file"])


def _pick(dated: list[anchors.Dated], types: tuple[int, ...], region: str) -> anchors.Dated | None:
    """The earliest date of those types in the region; without one there, the earliest anywhere."""
    of_type = [item for item in dated if item.type in types]
    if not of_type:
        return None
    if region:
        local = [item for item in of_type if item.country == region]
        if local:
            return min(local, key=lambda item: (item.at, item.type))
    return min(of_type, key=lambda item: (item.at, item.type, item.country))


def _movie_entries(
    db: OrmSession, span: Span, *, region: str, only_monitored: bool, only_missing: bool
) -> list[dict[str, Any]]:
    """One entry per movie and occasion whose date of the region falls into the span."""
    patterns = [f'%"{month}-%' for month in months_of(span)]
    rows = db.execute(
        select(Title.id, Title.title, Title.year, Title.release_dates).where(
            Title.kind == "movie",
            Title.release_dates.is_not(None),
            or_(*[cast(Title.release_dates, Text).like(pattern) for pattern in patterns]),
        )
    ).all()
    picked: list[tuple[int, str, int | None, str, str, str | None]] = []
    for title_id, title, year, raw in rows:
        dated = anchors.entries(raw)
        if not dated:
            continue
        for occasion, types in OCCASION_TYPES.items():
            chosen = _pick(dated, types, region)
            if chosen is None:
                continue
            day = chosen.at.strftime("%Y-%m-%d")
            if span.first <= day <= span.last:
                # The country is worth a word only when the date is not the region's own.
                foreign = None if region and chosen.country == region else chosen.country
                picked.append((title_id, title, year, occasion, day, foreign))
    flags = _flags_by_title(db, [item[0] for item in picked])
    entries = []
    for title_id, title, year, occasion, day, country in picked:
        monitored, has_file = flags.get(title_id, (False, False))
        entry = {
            "day": day,
            "kind": "movie",
            "occasion": occasion,
            "country": country,
            "title_id": title_id,
            "title": title,
            "year": year,
            "monitored": monitored,
            "has_file": has_file,
        }
        if _keep(entry, only_monitored=only_monitored, only_missing=only_missing):
            entries.append(entry)
    return entries


def _episode_entries(db: OrmSession, span: Span, *, only_monitored: bool, only_missing: bool) -> list[dict[str, Any]]:
    """One entry per episode that airs in the span, with its series and its number."""
    rows = db.execute(
        select(
            Episode.id,
            Episode.title_id,
            Episode.season_number,
            Episode.episode_number,
            Episode.name,
            Episode.air_date,
            Title.title,
            Title.year,
        )
        .join(Title, Title.id == Episode.title_id)
        .where(
            Episode.air_date >= span.first,
            Episode.air_date <= span.last,
            Episode.tmdb_gone_at.is_(None),
        )
        .order_by(Episode.air_date, Episode.title_id, Episode.season_number, Episode.episode_number)
    ).all()
    if not rows:
        return []
    watching = db.execute(
        select(
            EpisodeVersion.episode_id,
            func.max(func.iif(EpisodeVersion.watched, 1, 0)),
            func.max(func.iif(EpisodeVersion.episode_file_id.is_not(None), 1, 0)),
        )
        .where(EpisodeVersion.episode_id.in_([row[0] for row in rows]))
        .group_by(EpisodeVersion.episode_id)
    ).all()
    flags = {episode_id: (bool(watched), bool(has_file)) for episode_id, watched, has_file in watching}
    entries = []
    for episode_id, title_id, season, number, name, air_date, series_title, year in rows:
        monitored, has_file = flags.get(episode_id, (False, False))
        entry = {
            "day": air_date,
            "kind": "episode",
            "occasion": "air",
            "country": None,
            "title_id": title_id,
            "title": series_title,
            "year": year,
            "season": season,
            "episode": number,
            "episode_title": name or None,
            "monitored": monitored,
            "has_file": has_file,
        }
        if _keep(entry, only_monitored=only_monitored, only_missing=only_missing):
            entries.append(entry)
    return entries


def _album_entries(db: OrmSession, span: Span, *, only_monitored: bool, only_missing: bool) -> list[dict[str, Any]]:
    """One entry per album whose release date falls into the span. An album with only a year has no day."""
    rows = db.execute(
        select(Title.id, Title.title, Title.first_release_date, Artist.name)
        .outerjoin(Artist, Artist.id == Title.artist_id)
        .where(
            Title.kind == "album",
            Title.first_release_date >= span.first,
            Title.first_release_date <= span.last,
            func.length(Title.first_release_date) == FULL_DAY_LENGTH,
        )
        .order_by(Title.first_release_date, Title.id)
    ).all()
    flags = _flags_by_title(db, [row[0] for row in rows])
    entries = []
    for title_id, title, day, artist in rows:
        monitored, has_file = flags.get(title_id, (False, False))
        entry = {
            "day": day,
            "kind": "album",
            "occasion": "release",
            "country": None,
            "title_id": title_id,
            "title": title,
            "year": None,
            "artist": artist,
            "monitored": monitored,
            "has_file": has_file,
        }
        if _keep(entry, only_monitored=only_monitored, only_missing=only_missing):
            entries.append(entry)
    return entries


def entries(
    db: OrmSession,
    span: Span,
    *,
    kinds: tuple[str, ...] = KINDS,
    region: str = "",
    only_monitored: bool = False,
    only_missing: bool = False,
) -> tuple[list[dict[str, Any]], bool]:
    """Every entry of the span, sorted by day, and whether the answer was cut at ``ENTRIES_MAX``."""
    found: list[dict[str, Any]] = []
    if "movie" in kinds:
        found += _movie_entries(db, span, region=region, only_monitored=only_monitored, only_missing=only_missing)
    if "episode" in kinds:
        found += _episode_entries(db, span, only_monitored=only_monitored, only_missing=only_missing)
    if "album" in kinds:
        found += _album_entries(db, span, only_monitored=only_monitored, only_missing=only_missing)
    found.sort(key=lambda entry: (entry["day"], entry["kind"], entry.get("title") or "", entry.get("episode") or 0))
    cut = len(found) > ENTRIES_MAX
    return found[:ENTRIES_MAX], cut


def counts_of(found: list[dict[str, Any]]) -> dict[str, int]:
    counts = dict.fromkeys(KINDS, 0)
    for entry in found:
        counts[entry["kind"]] += 1
    return counts


#: The countries of the stored dates, counted in SQLite itself: only the outer level of the JSON is walked,
#: never the dates inside it. Unpacking every movie in Python was measurably slower (see the plan), and it is
#: still the most expensive query here, so the interface asks for it only when the chooser is touched.
REGIONS_SQL = text(
    """
    SELECT upper(json_extract(country.value, '$.iso_3166_1')) AS code, count(DISTINCT titles.id) AS movies
    FROM titles, json_each(titles.release_dates) country
    WHERE titles.kind = 'movie' AND titles.release_dates IS NOT NULL
      AND length(json_extract(country.value, '$.iso_3166_1')) = 2
    GROUP BY code
    ORDER BY movies DESC, code
    """
)


def regions(db: OrmSession) -> list[dict[str, Any]]:
    """The countries that appear in the stored release dates, with how many movies name them.

    The list comes from the library itself, so it holds the countries his movies actually have, and nexcrate
    carries no table of countries of its own.
    """
    return [{"country": code, "movies": movies} for code, movies in db.execute(REGIONS_SQL).all() if code]


def span_around(today: date, *, before: int, after: int) -> Span:
    return Span(start=today - timedelta(days=before), end=today + timedelta(days=after))
