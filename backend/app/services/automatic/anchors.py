"""From when a version is searched: its anchor date (C2 and C3, decisions 4 and 5).

TMDB's release date types: 1 premiere, 2 theatrical limited, 3 theatrical, 4 digital, 5 physical, 6 TV, per country,
with no fallback between countries (facts B7). Knows nothing about the database.

* **General anchor:** the earliest digital (4) or physical (5) date in any country, its country left out. Without one,
  the earliest theatrical (2 or 3) date plus 90 days, as Radarr's availability.
* **Language anchor:** for a version whose rules require languages, the earliest digital or physical date in a country
  where one of them is spoken (``data/language_countries.json``, ISO 639-1 to ISO 3166-1, for the languages the
  profile builder offers). The version uses it when it exists, else the general anchor.
* **2160p:** a version whose target resolution is 2160 starts its frequent window again at the first physical date of
  the same countries when that lies after its anchor: UHD discs often come later.
* **Without a date:** kind ``year`` when the title has a year, ``none`` without one; there is no anchor time then.
  Premieres and TV dates never count, and neither does anything that is not a usable date.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

DIGITAL = 4
PHYSICAL = 5
THEATRICAL_TYPES = (2, 3)
HOME_VIDEO_TYPES = (DIGITAL, PHYSICAL)
KNOWN_TYPES = frozenset(range(1, 7))
#: Radarr's availability without a digital or physical date.
THEATRICAL_DELAY = timedelta(days=90)
UHD_RESOLUTION = 2160
KINDS = ("digital", "physical", "theatrical", "year", "none")

LANGUAGE_TABLE = Path(__file__).resolve().parents[2] / "data" / "language_countries.json"


@dataclass(frozen=True)
class Dated:
    at: datetime
    type: int
    #: ISO 3166-1, upper case.
    country: str


@dataclass(frozen=True)
class Anchor:
    #: digital, physical, theatrical, year or none.
    kind: str
    #: From when the version may be searched; None for ``year`` and ``none``.
    at: datetime | None = None
    #: The country of a language anchor; None for the general anchor.
    country: str | None = None
    #: 2160p only: a later physical date from which the frequent window starts again.
    window_at: datetime | None = None


@functools.cache
def language_countries() -> dict[str, frozenset[str]]:
    """The table: ISO 639-1 code to the countries where the language is spoken."""
    raw = json.loads(LANGUAGE_TABLE.read_text(encoding="utf-8"))
    table: dict[str, frozenset[str]] = {}
    for code, countries in raw.items():
        if isinstance(code, str) and isinstance(countries, list):
            table[code.lower()] = frozenset(country.upper() for country in countries if isinstance(country, str))
    return table


def countries_of(languages: Iterable[str]) -> frozenset[str]:
    table = language_countries()
    found: set[str] = set()
    for code in languages:
        found |= table.get(code.lower(), frozenset())
    return frozenset(found)


def required_languages(rules: dict[str, Any] | None, original: str | None = None) -> tuple[str, ...]:
    """The ISO 639-1 codes a profile's rules require; ``original`` stands for the title's original language and is
    left out while that is unknown."""
    codes: list[str] = []
    for entry in (rules or {}).get("languages") or []:
        if isinstance(entry, dict) and entry.get("role") == "required" and isinstance(entry.get("code"), str):
            code = original if entry["code"] == "original" else entry["code"]
            if code and code not in codes:
                codes.append(code)
    return tuple(codes)


def target_resolution(rules: dict[str, Any] | None) -> int | None:
    value = (rules or {}).get("target_resolution")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _moment(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text[:10])
        except ValueError:
            return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def entries(release_dates: object) -> list[Dated]:
    """Every usable date of TMDB's ``release_dates.results``, as nexcrate stores it on a title."""
    found: list[Dated] = []
    for country_entry in release_dates if isinstance(release_dates, list) else []:
        if not isinstance(country_entry, dict):
            continue
        country = country_entry.get("iso_3166_1")
        dates = country_entry.get("release_dates")
        if not isinstance(country, str) or len(country.strip()) != 2 or not isinstance(dates, list):
            continue
        for item in dates:
            if not isinstance(item, dict):
                continue
            kind = item.get("type")
            if not isinstance(kind, int) or isinstance(kind, bool) or kind not in KNOWN_TYPES:
                continue
            at = _moment(item.get("release_date"))
            if at is not None:
                found.append(Dated(at=at, type=kind, country=country.strip().upper()))
    return found


def _earliest(found: list[Dated], types: tuple[int, ...], countries: frozenset[str] | None) -> Dated | None:
    chosen = [item for item in found if item.type in types and (countries is None or item.country in countries)]
    return min(chosen, key=lambda item: (item.at, item.type, item.country)) if chosen else None


def _home_video(found: list[Dated], countries: frozenset[str] | None, uhd: bool) -> Anchor | None:
    first = _earliest(found, HOME_VIDEO_TYPES, countries)
    if first is None:
        return None
    window = None
    if uhd:
        disc = _earliest(found, (PHYSICAL,), countries)
        if disc is not None and disc.at > first.at:
            window = disc.at
    return Anchor(
        kind="digital" if first.type == DIGITAL else "physical",
        at=first.at,
        country=first.country if countries is not None else None,
        window_at=window,
    )


def anchor_for(
    release_dates: object,
    *,
    year: int | None,
    languages: Iterable[str] = (),
    resolution: int | None = None,
) -> Anchor:
    """A version's anchor from its required languages and target resolution; without them, the general anchor."""
    found = entries(release_dates)
    uhd = resolution == UHD_RESOLUTION
    countries = countries_of(languages)
    if countries:
        local = _home_video(found, countries, uhd)
        if local is not None:
            return local
    general = _home_video(found, None, uhd)
    if general is not None:
        return general
    cinema = _earliest(found, THEATRICAL_TYPES, None)
    if cinema is not None:
        return Anchor(kind="theatrical", at=cinema.at + THEATRICAL_DELAY)
    return Anchor(kind="year") if year is not None else Anchor(kind="none")
