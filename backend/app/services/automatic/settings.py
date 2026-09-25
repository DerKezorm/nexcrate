"""The switches for everything automatic (C1, decision 1; the design notes, decision 1).

Three settings, one per kind: ``automatic_enabled`` for movies, ``automatic_series_enabled`` for series and, since
Music M5, ``automatic_music_enabled`` for albums (decision 1). ``1`` on,
anything else off; nothing stored means off. Off for a kind, nothing automatic of that kind reaches an indexer or a
download client: no planned search, no replacement after a failure, no automatic grab. The plan is still computed.
RSS runs while any is on and loads only for the kinds switched on. Budget, pauses and escalation per indexer are
shared.
"""

from __future__ import annotations

from sqlalchemy.orm import Session as OrmSession

from ...db import SessionLocal, get_setting, set_setting

SETTING_ENABLED = "automatic_enabled"
SETTING_SERIES_ENABLED = "automatic_series_enabled"
#: Music M5 (decision 1): the third switch, for albums.
SETTING_MUSIC_ENABLED = "automatic_music_enabled"
KINDS = ("movie", "series", "album")
_KEYS = {"movie": SETTING_ENABLED, "series": SETTING_SERIES_ENABLED, "album": SETTING_MUSIC_ENABLED}


def _key(kind: str) -> str:
    if kind not in KINDS:
        raise ValueError(kind)
    return _KEYS[kind]


def load_enabled(db: OrmSession, kind: str = "movie") -> bool:
    """The switch of one kind; without a kind the movie switch, as step 3c reads it."""
    return get_setting(db, _key(kind), "0") == "1"


def load_kinds(db: OrmSession) -> frozenset[str]:
    """The kinds switched on."""
    return frozenset(kind for kind in KINDS if load_enabled(db, kind))


def save_enabled(db: OrmSession, enabled: bool, kind: str = "movie") -> None:
    """Stage the setting; the caller commits."""
    set_setting(db, _key(kind), "1" if enabled else "0")


def is_enabled(kind: str = "movie") -> bool:
    with SessionLocal() as db:
        return load_enabled(db, kind)
