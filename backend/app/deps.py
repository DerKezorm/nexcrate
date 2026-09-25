"""Reusable dependencies: the database session, the logged-in session, and the key of a program at ``/api/v1``."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Request, Response
from fastapi.security import APIKeyCookie, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session as OrmSession

from .db import get_db
from .meldungen import error
from .models import Account, ApiKey, Session
from .security import session_ref
from .services import api_keys, api_limits, logs, sessions

DbSession = Annotated[OrmSession, Depends(get_db)]

session_cookie = APIKeyCookie(
    name=sessions.COOKIE_NAME,
    scheme_name="session",
    description="Set by POST /api/setup and POST /api/auth/login. HttpOnly, SameSite=Strict, path /api.",
    auto_error=False,
)


def require_session(
    request: Request,
    response: Response,
    db: DbSession,
    token: Annotated[str | None, Depends(session_cookie)],
) -> Session:
    """The session of the request, or 401 ``not_logged_in``.

    Binds a short session reference (the first 8 characters of the hash, never the token)
    to the log lines of the request, and extends the session on use.
    """
    row = sessions.find(db, token)
    if row is None or token is None:
        raise error("not_logged_in", "Please log in.", 401)
    logs.set_session_ref(session_ref(row.token_hash))
    sessions.extend(db, row, response, request, token)
    return row


CurrentSession = Annotated[Session, Depends(require_session)]


def current_account(session: CurrentSession, db: DbSession) -> Account:
    account = db.get(Account, session.account_id)
    if account is None:
        raise error("not_logged_in", "Please log in.", 401)
    return account


CurrentAccount = Annotated[Account, Depends(current_account)]


# --- /api/v1: a key instead of a session ------------------------------------ #

api_key_header = HTTPBearer(
    scheme_name="key",
    description=(
        "A key from Settings, API keys: `Authorization: Bearer nxc_...`. Only `/api/v1` takes it, and `/api/v1` takes "
        "nothing else: the session cookie of the interface does not open it."
    ),
    auto_error=False,
)


def require_api_key(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(api_key_header)],
) -> ApiKey:
    """The key of the request, or 401 ``api_key_missing`` or ``api_key_invalid``.

    Binds the number of the key (never the key) to the log lines of the request and notes that it was used.
    """
    challenge = {"WWW-Authenticate": "Bearer"}
    if credentials is None:
        raise error(
            "api_key_missing", "This address needs a key in the header Authorization: Bearer.", 401, headers=challenge
        )
    row = api_keys.find(db, credentials.credentials)
    if row is None:
        raise error("api_key_invalid", "This key is unknown or was revoked.", 401, headers=challenge)
    logs.set_key_ref(row.id)
    wait = api_limits.take(row.id)
    if wait is not None:
        raise error(
            "rate_limited",
            "This key sent too many requests; ask again later.",
            429,
            headers={"Retry-After": str(wait)},
            retry_after=wait,
        )
    api_keys.mark_used(row.id)
    return row


def require_scope(scope: str) -> Callable[[ApiKey], ApiKey]:
    """A dependency that lets a key through only when it carries ``scope``; otherwise 403 ``scope_missing``."""

    def dependency(key: Annotated[ApiKey, Depends(require_api_key)]) -> ApiKey:
        if scope not in (key.scopes or []):
            raise error("scope_missing", f"This key lacks the scope {scope}.", 403, scope=scope)
        return key

    return dependency
