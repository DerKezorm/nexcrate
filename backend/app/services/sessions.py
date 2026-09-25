"""Server-side sessions and the cookie that carries them.

The token leaves the backend once, in the cookie ``nexcrate_session`` (HttpOnly,
SameSite=Strict, path ``/api``). The database stores only its SHA-256 hash: a copy
of the database opens no session.

Sessions last 30 days and are extended on use. The extension is written at most once
per ``EXTEND_AFTER``, not on every request.

⚠️ ``Secure`` is never guessed. ``auto`` follows the scheme of the request: a Secure
cookie over plain http is dropped by the browser, and then nobody gets in. A reverse
proxy that ends TLS and forwards plain http needs ``on``.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import Request, Response
from sqlalchemy import delete
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session as OrmSession

from ..config import get_settings
from ..models import ACCOUNT_ID, Session, utcnow
from ..security import hash_token, new_session_token, session_ref
from . import logs

logger = logging.getLogger("nexcrate.sessions")

COOKIE_NAME = "nexcrate_session"
COOKIE_PATH = "/api"
LIFETIME = timedelta(days=30)
EXTEND_AFTER = timedelta(hours=1)
#: Longer cookie values are not ours and are not even hashed.
TOKEN_MAX_LENGTH = 256


def cookie_secure(request: Request) -> bool:
    setting = (get_settings().cookie_secure or "auto").strip().lower()
    if setting == "on":
        return True
    if setting == "off":
        return False
    if setting != "auto":
        logger.warning("NEXCRATE_COOKIE_SECURE %r is not understood, auto is used. Use auto, on or off.", setting)
    return request.url.scheme == "https"


def set_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=int(LIFETIME.total_seconds()),
        path=COOKIE_PATH,
        httponly=True,
        samesite="strict",
        secure=cookie_secure(request),
    )


def clear_cookie(response: Response, request: Request) -> None:
    # Path and flags as when setting it, or the browser keeps the real one.
    response.delete_cookie(
        COOKIE_NAME,
        path=COOKIE_PATH,
        httponly=True,
        samesite="strict",
        secure=cookie_secure(request),
    )


def start(db: OrmSession, response: Response, request: Request) -> Session:
    """A new session for the account, with its cookie on the response."""
    token = new_session_token()
    now = utcnow()
    row = Session(
        token_hash=hash_token(token),
        account_id=ACCOUNT_ID,
        created_at=now,
        last_used_at=now,
        expires_at=now + LIFETIME,
    )
    db.add(row)
    db.commit()
    set_cookie(response, request, token)
    logs.set_session_ref(session_ref(row.token_hash))
    return row


def find(db: OrmSession, token: str | None) -> Session | None:
    """The session of a cookie value, or None. An expired session is removed on the way."""
    if not token or len(token) > TOKEN_MAX_LENGTH:
        return None
    row = db.get(Session, hash_token(token))
    if row is None:
        return None
    if row.expires_at <= utcnow():
        db.delete(row)
        db.commit()
        return None
    return row


def extend(db: OrmSession, row: Session, response: Response, request: Request, token: str) -> bool:
    """Sliding expiry: 30 days from the last use, written at most once per ``EXTEND_AFTER``.

    ⚠️ The extension can wait; the request cannot. On 22.09.2026 two page loads right after an update failed after
    30 seconds with "database is locked", only because the session was due for its extension while the start judged
    every file again. So: not during that pass, and a locked database skips it; the next request extends.
    """
    from . import judging

    now = utcnow()
    if now - row.last_used_at < EXTEND_AFTER or judging.busy():
        return False
    row.last_used_at = now
    row.expires_at = now + LIFETIME
    try:
        db.commit()
    except OperationalError:
        db.rollback()
        logger.info("Session extension skipped: the database is busy; the next request extends it")
        return False
    set_cookie(response, request, token)
    return True


def end(db: OrmSession, row: Session) -> None:
    db.delete(row)
    db.commit()


def end_token(db: OrmSession, token: str | None) -> None:
    row = find(db, token)
    if row is not None:
        end(db, row)


def end_all(db: OrmSession) -> int:
    result = db.execute(delete(Session))
    db.commit()
    return int(getattr(result, "rowcount", 0) or 0)


def end_others(db: OrmSession, keep: Session) -> int:
    result = db.execute(delete(Session).where(Session.token_hash != keep.token_hash))
    db.commit()
    return int(getattr(result, "rowcount", 0) or 0)


def purge_expired() -> int:
    """Remove expired sessions. Runs as a background job."""
    from ..db import SessionLocal

    with SessionLocal() as db:
        result = db.execute(delete(Session).where(Session.expires_at <= utcnow()))
        db.commit()
    removed = int(getattr(result, "rowcount", 0) or 0)
    if removed:
        logger.info("Removed %d expired sessions", removed)
    return removed
