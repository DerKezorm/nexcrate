"""The switch for subtitle files (C9, decision 16 of the owner).

The setting ``subtitles_enabled``: ``0`` off, anything else on. Nothing stored means on. Off, an import places no
subtitle file; what was placed before stays, and on an upgrade the old file's recorded subtitles still go into the
recycle folder with it, because they belong to that file and not to the new one.
"""

from __future__ import annotations

from sqlalchemy.orm import Session as OrmSession

from ...db import get_setting, set_setting

SETTING_ENABLED = "subtitles_enabled"


def load_enabled(db: OrmSession) -> bool:
    return get_setting(db, SETTING_ENABLED, "1") != "0"


def save_enabled(db: OrmSession, enabled: bool) -> None:
    """Stage the setting; the caller commits."""
    set_setting(db, SETTING_ENABLED, "1" if enabled else "0")
