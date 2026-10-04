"""The switch for Bazarr. The setting ``bazarr_enabled``: ``1`` on, anything else off. Nothing stored means off.

Off, every address below ``/bazarr`` answers 404 ``bazarr_off`` once the key checks out, live connections are closed,
and nexcrate never moves a subtitle file it did not record.
"""

from __future__ import annotations

from sqlalchemy.orm import Session as OrmSession

from ...db import get_setting, set_setting

SETTING_ENABLED = "bazarr_enabled"


def load_enabled(db: OrmSession) -> bool:
    return get_setting(db, SETTING_ENABLED, "0") == "1"


def save_enabled(db: OrmSession, enabled: bool) -> None:
    """Stage the setting; the caller commits."""
    set_setting(db, SETTING_ENABLED, "1" if enabled else "0")
