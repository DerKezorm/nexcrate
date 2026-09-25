"""The calendar subscription: an iCalendar feed a calendar app reads without logging in.

⚠️ This is a way out of the house, so it has a bolt: it is **off** until the owner turns it on, it has a key
of its own that opens nothing else, and that key can be replaced at any time. Radarr, Sonarr and Lidarr hang
their full API key into the feed address instead, which hands out the whole instance to whoever sees the
link.

The key is stored encrypted, like every other credential, and never reaches the log.
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session as OrmSession

from .. import crypto
from ..db import get_setting, set_setting

logger = logging.getLogger("nexcrate.calendar")

SETTING_ENABLED = "calendar_feed_enabled"
SETTING_KEY = "calendar_feed_key"
SETTING_REGION = "calendar_region"
#: Long enough that guessing is hopeless, short enough to paste by hand.
KEY_BYTES = 24
#: The path the feed answers on. It carries the key, so it never appears in a log line.
FEED_PATH = "/api/calendar/feed.ics"
#: What the feed covers when nothing else is asked for.
PAST_DAYS_DEFAULT = 7
FUTURE_DAYS_DEFAULT = 90
PAST_DAYS_MAX = 90
FUTURE_DAYS_MAX = 365

OCCASION_TEXT = {
    "theatrical": "In cinemas",
    "digital": "Digital",
    "physical": "Disc",
    "air": "Airs",
    "release": "Released",
}


def is_enabled(db: OrmSession) -> bool:
    return get_setting(db, SETTING_ENABLED, "0") == "1"


def region(db: OrmSession) -> str:
    """The country whose dates the calendar shows, ISO 3166-1; empty means the earliest date anywhere."""
    value = get_setting(db, SETTING_REGION, "").strip().upper()
    return value if len(value) == 2 and value.isalpha() else ""


def set_region(db: OrmSession, value: str) -> str:
    cleaned = (value or "").strip().upper()
    cleaned = cleaned if len(cleaned) == 2 and cleaned.isalpha() else ""
    set_setting(db, SETTING_REGION, cleaned)
    return cleaned


def key(db: OrmSession) -> str:
    """The plain key, or an empty string while none was made."""
    stored = get_setting(db, SETTING_KEY, "")
    return crypto.decrypt(stored) if stored else ""


def new_key(db: OrmSession) -> str:
    """A fresh key; the one before stops working at once."""
    made = secrets.token_urlsafe(KEY_BYTES)
    set_setting(db, SETTING_KEY, crypto.encrypt(made))
    logger.info("The calendar subscription got a new key; the previous one no longer opens it")
    return made


def enable(db: OrmSession, enabled: bool) -> str:
    """Turn the feed on or off. Turning it on the first time makes a key."""
    set_setting(db, SETTING_ENABLED, "1" if enabled else "0")
    logger.info("The calendar subscription is now %s", "on" if enabled else "off")
    if not enabled:
        return key(db)
    return key(db) or new_key(db)


def opens(db: OrmSession, given: str) -> bool:
    """Whether that key opens the feed. Off means no key opens it."""
    if not is_enabled(db):
        return False
    stored = key(db)
    return bool(stored) and secrets.compare_digest(stored, given or "")


def _escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n").replace("\r", "")
    )


def _fold(line: str) -> str:
    """iCalendar wants no line longer than 75 octets; the rest continues after a space."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    pieces, rest = [], raw
    limit = 75
    while len(rest) > limit:
        cut = limit
        # Never cut a character in half.
        while cut > 0 and (rest[cut] & 0xC0) == 0x80:
            cut -= 1
        pieces.append(rest[:cut].decode("utf-8"))
        rest = rest[cut:]
        limit = 74
    pieces.append(rest.decode("utf-8"))
    return "\r\n ".join(pieces)


def _summary(entry: dict[str, Any]) -> str:
    occasion = OCCASION_TEXT.get(entry["occasion"], entry["occasion"])
    if entry["kind"] == "episode":
        number = f"S{entry.get('season') or 0:02d}E{entry.get('episode') or 0:02d}"
        name = entry.get("episode_title")
        return f"{entry['title']} - {number}" + (f" - {name}" if name else "")
    if entry["kind"] == "album":
        artist = entry.get("artist")
        return f"{artist} - {entry['title']}" if artist else entry["title"]
    year = f" ({entry['year']})" if entry.get("year") else ""
    return f"{entry['title']}{year} - {occasion}"


def _description(entry: dict[str, Any]) -> str:
    parts = [OCCASION_TEXT.get(entry["occasion"], entry["occasion"])]
    if entry.get("country"):
        parts.append(f"Date of {entry['country']}")
    parts.append("In the library" if entry["has_file"] else "Not in the library")
    if not entry["monitored"]:
        parts.append("Not watched")
    return ", ".join(parts)


def _uid(entry: dict[str, Any]) -> str:
    """Stable over refreshes: the same entry keeps its identity in the calendar app."""
    if entry["kind"] == "episode":
        return f"nexcrate-episode-{entry['title_id']}-{entry.get('season')}-{entry.get('episode')}@nexcrate"
    return f"nexcrate-{entry['kind']}-{entry['title_id']}-{entry['occasion']}@nexcrate"


def ics(entries: list[dict[str, Any]], *, now: datetime | None = None, name: str = "nexcrate") -> str:
    """The feed as text. Every entry is one all day event, so no time zone can move it."""
    stamp = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//nexcrate//release calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(name)}",
    ]
    for entry in entries:
        start = date.fromisoformat(entry["day"])
        day = start.strftime("%Y%m%d")
        lines += [
            "BEGIN:VEVENT",
            f"UID:{_uid(entry)}",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{day}",
            f"DTEND;VALUE=DATE:{(start + timedelta(days=1)).strftime('%Y%m%d')}",
            f"SUMMARY:{_escape(_summary(entry))}",
            f"DESCRIPTION:{_escape(_description(entry))}",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
