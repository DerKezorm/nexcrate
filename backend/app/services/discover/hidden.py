"""Titles of Discover the owner never wants to see again ("Not interested").

Kept in the setting ``discover_hidden`` as JSON, ``{"movie": [603, …], "series": […], "album": ["<mbid>", …]}``, no
table of its own. Each kind keeps the newest ``LIMIT`` entries; the oldest fall out first. Resetting empties all.
"""

from __future__ import annotations

import json
from typing import Literal

from sqlalchemy.orm import Session as OrmSession

from ...db import get_setting, set_setting

SETTING = "discover_hidden"
KINDS = ("movie", "series", "album")
#: Far more than anyone clicks; keeps the setting small.
LIMIT = 5000

Kind = Literal["movie", "series", "album"]


def _read(db: OrmSession) -> dict[str, list[str]]:
    try:
        raw = json.loads(get_setting(db, SETTING, "") or "{}")
    except ValueError:
        raw = {}
    stored = raw if isinstance(raw, dict) else {}
    found: dict[str, list[str]] = {}
    for kind in KINDS:
        entries = stored.get(kind)
        found[kind] = (
            [str(entry) for entry in entries if isinstance(entry, (str, int))] if isinstance(entries, list) else []
        )
    return found


def keys(db: OrmSession, kind: Kind) -> set[str]:
    """The hidden keys of a kind: TMDB numbers as text for movies and series, release group ids for albums."""
    return set(_read(db)[kind])


def counts(db: OrmSession) -> dict[str, int]:
    return {kind: len(entries) for kind, entries in _read(db).items()}


def hide(db: OrmSession, kind: Kind, key: str) -> None:
    """Stage one more hidden key; hiding twice keeps one entry. The caller commits."""
    stored = _read(db)
    entries = [entry for entry in stored[kind] if entry != key]
    entries.append(key)
    stored[kind] = entries[-LIMIT:]
    set_setting(db, SETTING, json.dumps(stored, separators=(",", ":")))


def reset(db: OrmSession) -> int:
    """Stage an empty list; returns how many entries there were. The caller commits."""
    removed = sum(counts(db).values())
    set_setting(db, SETTING, "{}")
    return removed
