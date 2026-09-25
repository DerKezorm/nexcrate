"""What the owner can set about how releases are ordered (finding 7b).

One setting so far: ``search_prefer_indexer_flags``, Radarr's "Prefer Indexer Flags" (Settings, Indexers, Options).
``1`` on, anything else off; nothing stored means off, as in Radarr. It concerns movies only: measured on 20.09.2026,
Radarr 5 answers ``preferIndexerFlags`` under ``/api/v3/config/indexer`` and Sonarr 4.0.19 has no such field, and
Sonarr's order of releases has no step for flags.
"""

from __future__ import annotations

from sqlalchemy.orm import Session as OrmSession

from ...db import SessionLocal, get_setting, set_setting

SETTING_PREFER_INDEXER_FLAGS = "search_prefer_indexer_flags"


def load_prefer_indexer_flags(db: OrmSession) -> bool:
    return get_setting(db, SETTING_PREFER_INDEXER_FLAGS, "0") == "1"


def save_prefer_indexer_flags(db: OrmSession, enabled: bool) -> None:
    """Stage the setting; the caller commits."""
    set_setting(db, SETTING_PREFER_INDEXER_FLAGS, "1" if enabled else "0")


def prefer_indexer_flags() -> bool:
    with SessionLocal() as db:
        return load_prefer_indexer_flags(db)
