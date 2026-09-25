"""Whether a newer nexcrate exists (N36), as Nexview asks it.

At most once a day nexcrate asks GitHub's public API for the latest release. Nothing is sent but the request itself: no
title, no setting, no key. On by default, switched off under Settings, System; switched off, nexcrate reaches GitHub
never. ⚠️ While the repository is private, GitHub answers 404 without a login: the answer is then "unknown", never
"up to date". The check is an extra: when GitHub fails, nothing else does.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any

import httpx

from .. import __version__
from .. import db as database
from ..models import utcnow
from . import http_log

logger = logging.getLogger("nexcrate.updates")

REPO = "DerKezorm/nexcrate"
RELEASES_URL = f"https://github.com/{REPO}/releases"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
JOB_NAME = "update_check"
#: The job looks this often whether a check is due.
INTERVAL_SECONDS = 3600
CHECK_EVERY = timedelta(hours=24)
#: After a failed check, the next one this much later.
RETRY_AFTER = timedelta(hours=6)
TIMEOUT = httpx.Timeout(6.0, connect=4.0)

SETTING_ENABLED = "update_check"
SETTING_STATE = "update_state"

_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")


def parse_version(text: str) -> tuple[int, int, int] | None:
    found = _VERSION.match((text or "").strip())
    return (int(found[1]), int(found[2]), int(found[3])) if found else None


def is_newer(latest: str, current: str) -> bool:
    """Whether ``latest`` is above ``current``; unreadable is no: a wrong hint is worse than none."""
    a, b = parse_version(latest), parse_version(current)
    return a is not None and b is not None and a > b


def enabled(db: Any) -> bool:
    return database.get_setting(db, SETTING_ENABLED, "on") != "off"


def _state(db: Any) -> dict[str, Any]:
    try:
        value = json.loads(database.get_setting(db, SETTING_STATE, "") or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def status(db: Any) -> dict[str, Any]:
    """What is known, without asking anybody: ``available`` is null while the newest release is unknown."""
    state = _state(db)
    latest = state.get("latest") if isinstance(state.get("latest"), str) else None
    checked = state.get("checked_at")
    return {
        "enabled": enabled(db),
        "current": __version__,
        "latest": latest,
        "available": is_newer(latest, __version__) if latest else None,
        "checked_at": datetime.fromisoformat(checked) if isinstance(checked, str) else None,
        "problem": state.get("problem") if isinstance(state.get("problem"), str) else None,
        "url": RELEASES_URL,
    }


def _due(state: dict[str, Any], now: datetime) -> bool:
    for key, wait in (("checked_at", CHECK_EVERY), ("failed_at", RETRY_AFTER)):
        value = state.get(key)
        if isinstance(value, str) and now - datetime.fromisoformat(value) < wait:
            return False
    return True


async def _ask() -> tuple[str | None, str | None]:
    """The newest release's tag, and a problem code: ``not_found`` (no release, or a private repository)."""
    async with http_log.client("github", timeout=TIMEOUT) as http:
        answer = await http_log.send(
            http,
            "GET",
            API_URL,
            headers={"Accept": "application/vnd.github+json", "User-Agent": f"nexcrate/{__version__}"},
        )
    if answer.status_code == 404:
        return None, "not_found"
    answer.raise_for_status()
    body = answer.json()
    tag = body.get("tag_name") if isinstance(body, dict) else None
    return (tag.strip() or None) if isinstance(tag, str) else None, None


async def check(*, force: bool = False) -> dict[str, Any]:
    """Ask GitHub when it is on and due (or ``force``); returns the status. Never raises."""
    now = utcnow()
    with database.SessionLocal() as db:
        if not enabled(db):
            return status(db)
        state = _state(db)
        if not force and not _due(state, now):
            return status(db)
    try:
        latest, problem = await _ask()
    except (httpx.HTTPError, ValueError) as exc:
        logger.info("The check for a newer version failed: %s", type(exc).__name__)
        written = {**state, "failed_at": now.isoformat(), "problem": "unreachable"}
    else:
        written = {"latest": latest, "checked_at": now.isoformat(), "problem": problem}
        if latest and is_newer(latest, __version__):
            logger.info("A newer nexcrate is out: %s (running %s)", latest, __version__)
    with database.SessionLocal() as db:
        database.set_setting(db, SETTING_STATE, json.dumps(written))
        db.commit()
        return status(db)


async def run_job() -> None:
    await check()
