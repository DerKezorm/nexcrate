"""Known browsers for the login brake (as in nexbeat; built 25.09.2026).

A browser that logged in successfully gets a second cookie, ``nexcrate_device``: a random id, its expiry and an HMAC
over both with a key derived from nexcrate's secret key. It opens nothing. It only gives the brake a counter of its own
for that browser, so whoever knows only the username and guesses from elsewhere cannot lock the owner out, and the
owner's own typos brake only the owner's browser.

A cookie that is garbled, altered, expired or signed with another key counts as no device at all. A new secret key
(a restore with another ``secret.key``, or ``NEXCRATE_SECRET_KEY`` changed) makes every known browser unknown again.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from fastapi import Request, Response

from ..config import get_settings
from . import sessions

COOKIE_NAME = "nexcrate_device"
#: Only the login reads it.
COOKIE_PATH = "/api/auth"
DAYS = 365


def _key() -> bytes:
    return hashlib.sha256(b"nexcrate-device:" + get_settings().resolved_secret_key().encode("utf-8")).digest()


def _signature(device_id: str, expires: int) -> str:
    return hmac.new(_key(), f"{device_id}.{expires}".encode(), hashlib.sha256).hexdigest()


def token_for(device_id: str, now: float | None = None) -> str:
    expires = int((time.time() if now is None else now) + DAYS * 24 * 60 * 60)
    return f"{device_id}.{expires}.{_signature(device_id, expires)}"


def known(request: Request, now: float | None = None) -> str | None:
    """The id of this browser when it logged in here before and its cookie is sound; None otherwise."""
    raw = request.cookies.get(COOKIE_NAME) or ""
    parts = raw.split(".")
    if len(parts) != 3 or not parts[0] or not parts[1].isdigit():
        return None
    device_id, expires, signature = parts[0], int(parts[1]), parts[2]
    if expires < (time.time() if now is None else now):
        return None
    if not hmac.compare_digest(signature, _signature(device_id, expires)):
        return None
    return device_id


def remember(response: Response, request: Request) -> None:
    """After a successful login: this browser counts as known, with its id kept and the expiry renewed."""
    device_id = known(request) or secrets.token_urlsafe(16)
    response.set_cookie(
        COOKIE_NAME,
        token_for(device_id),
        max_age=DAYS * 24 * 60 * 60,
        path=COOKIE_PATH,
        httponly=True,
        samesite="lax",
        secure=sessions.cookie_secure(request),
    )
