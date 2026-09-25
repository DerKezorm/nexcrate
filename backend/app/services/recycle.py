"""How long an old file stays in a recycle folder (the takeover plan, T4).

The setting ``recycle_days``, 1 to 365, 7 when nothing is stored or the stored value is not a number in that range. The
daily cleanup of ``downloads/importing.py`` reads it.
"""

from __future__ import annotations

from sqlalchemy.orm import Session as OrmSession

from ..db import get_setting, set_setting

SETTING_DAYS = "recycle_days"
MIN_DAYS = 1
MAX_DAYS = 365
DEFAULT_DAYS = 7


def valid(days: int) -> bool:
    return isinstance(days, int) and not isinstance(days, bool) and MIN_DAYS <= days <= MAX_DAYS


def load_days(db: OrmSession) -> int:
    raw = get_setting(db, SETTING_DAYS, "")
    try:
        days = int(raw)
    except ValueError:
        return DEFAULT_DAYS
    return days if valid(days) else DEFAULT_DAYS


def save_days(db: OrmSession, days: int) -> None:
    """Stage the setting; the caller checks the range first and commits."""
    set_setting(db, SETTING_DAYS, str(days))
