"""Login, logout, the account and its password."""

from __future__ import annotations

import hmac
import logging
import unicodedata
from datetime import datetime

from fastapi import APIRouter, Request, Response
from fastapi.exceptions import HTTPException
from pydantic import BaseModel, Field

from ..deps import CurrentAccount, CurrentSession, DbSession
from ..meldungen import error, error_responses
from ..models import ACCOUNT_ID, Account
from ..security import PASSWORD_MIN_LENGTH, dummy_hash, hash_password, verify_password
from ..services import anmeldebremse, known_devices, sessions

logger = logging.getLogger("nexcrate.auth")

#: A short language code. Never a fixed list: the frontend registry decides which languages exist.
LANGUAGE_PATTERN = r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8}){0,2}$"
LANGUAGE_MAX_LENGTH = 35
USERNAME_MAX_LENGTH = 64
#: Upper bound for text fields, so nobody sends a megabyte as a password.
INPUT_MAX_LENGTH = 1024

#: Routes that work without a session.
public_router = APIRouter(prefix="/api/auth", tags=["auth"])
#: Routes that need a session; main.py adds the requirement.
router = APIRouter(prefix="/api/auth", tags=["auth"])


class Me(BaseModel):
    username: str = Field(examples=["owner"])
    language: str = Field(description="Language code of the interface.", examples=["en"])
    created_at: datetime = Field(description="When the account was created, UTC.")


class LoginIn(BaseModel):
    username: str = Field(max_length=INPUT_MAX_LENGTH)
    password: str = Field(max_length=INPUT_MAX_LENGTH)


class LanguageIn(BaseModel):
    language: str = Field(
        pattern=LANGUAGE_PATTERN,
        max_length=LANGUAGE_MAX_LENGTH,
        description="A language code such as de, en or pt-BR.",
    )


class PasswordChangeIn(BaseModel):
    current_password: str = Field(max_length=INPUT_MAX_LENGTH)
    new_password: str = Field(max_length=INPUT_MAX_LENGTH, description=f"At least {PASSWORD_MIN_LENGTH} characters.")


def me_out(account: Account) -> Me:
    return Me(username=account.username, language=account.language, created_at=account.created_at)


def validate_username(raw: str) -> str:
    """Checked in the route, not by the model, so the specific code comes back."""
    username = raw.strip()
    if (
        not username
        or len(username) > USERNAME_MAX_LENGTH
        or any(unicodedata.category(character).startswith("C") for character in username)
    ):
        raise error("username_invalid", "The username must not be empty and may have at most 64 characters.", 422)
    return username


def validate_new_password(password: str) -> None:
    """Checked in the route, not by the model, so the specific code comes back."""
    if len(password) < PASSWORD_MIN_LENGTH:
        raise error(
            "password_too_short",
            f"The password needs at least {PASSWORD_MIN_LENGTH} characters.",
            422,
            min=PASSWORD_MIN_LENGTH,
        )


def throttled(wait: int) -> HTTPException:
    return error(
        "login_throttled",
        f"Too many failed attempts. Please wait {wait} seconds.",
        429,
        headers={"Retry-After": str(wait)},
        retry_after=wait,
    )


def _same_username(given: str, stored: str) -> bool:
    return hmac.compare_digest(given.strip().casefold().encode("utf-8"), stored.casefold().encode("utf-8"))


@public_router.post(
    "/login",
    response_model=Me,
    summary="Log in with username and password",
    description=(
        "Checks username and password and starts a session: the answer sets the cookie `nexcrate_session`. "
        "A cookie of an earlier session sent along is ended. Three failed attempts are free, after that "
        "the wait grows up to five minutes; the answer then is 429 with `retry_after` and a Retry-After header. "
        "A successful login also sets `nexcrate_device`: a browser that logged in before is braked on its own counter, "
        "so failures from elsewhere never lock it out."
    ),
    responses=error_responses((401, "login_failed"), (429, "login_throttled")),
)
def login(payload: LoginIn, request: Request, response: Response, db: DbSession) -> Me:
    account = db.get(Account, ACCOUNT_ID)
    matches = account is not None and _same_username(payload.username, account.username)
    device = known_devices.known(request) if matches else None
    if device is not None:
        # A browser that logged in before is braked on its own counter (as in nexbeat).
        key = anmeldebremse.device_key(device)
    else:
        key = anmeldebremse.ACCOUNT_KEY if matches else anmeldebremse.login_key(payload.username)
    wait = anmeldebremse.wait_seconds(key)
    if wait:
        logger.warning("Login refused, the brake is on for %d more seconds", wait)
        raise throttled(wait)
    # A wrong username still costs a bcrypt check, so the answer time tells nothing.
    password_ok = verify_password(payload.password, account.password_hash if account and matches else dummy_hash())
    if account is None or not matches or not password_ok:
        anmeldebremse.failed(key)
        logger.info("Login failed")
        raise error("login_failed", "Username or password is wrong.", 401)
    anmeldebremse.succeeded(key)
    sessions.end_token(db, request.cookies.get(sessions.COOKIE_NAME))
    sessions.start(db, response, request)
    known_devices.remember(response, request)
    logger.info("Login succeeded, session started")
    return me_out(account)


@router.post(
    "/logout",
    status_code=204,
    response_model=None,
    summary="End the current session",
    description="Ends the session of this browser and clears its cookie. Other sessions stay.",
)
def logout(session: CurrentSession, request: Request, response: Response, db: DbSession) -> None:
    sessions.end(db, session)
    sessions.clear_cookie(response, request)
    logger.info("Logged out")


@router.post(
    "/logout-all",
    status_code=204,
    response_model=None,
    summary="End every session",
    description="Ends every session of the account, this one included, and clears the cookie of this browser.",
)
def logout_all(session: CurrentSession, request: Request, response: Response, db: DbSession) -> None:
    ended = sessions.end_all(db)
    sessions.clear_cookie(response, request)
    logger.info("Ended all %d sessions", ended)


@router.get(
    "/me",
    response_model=Me,
    summary="Read the logged-in account",
    description="Username, interface language and creation time of the account.",
)
def read_me(account: CurrentAccount) -> Me:
    return me_out(account)


@router.patch(
    "/me",
    response_model=Me,
    summary="Change the interface language",
    description=(
        "Stores the language of the interface. Any short language code is accepted; "
        "the interface decides which languages it offers."
    ),
)
def update_me(payload: LanguageIn, account: CurrentAccount, db: DbSession) -> Me:
    account.language = payload.language
    db.commit()
    return me_out(account)


@router.put(
    "/password",
    status_code=204,
    response_model=None,
    summary="Change the password",
    description=(
        "Needs the current password. Ends every other session; this one stays. "
        f"The new password needs at least {PASSWORD_MIN_LENGTH} characters. "
        "Wrong current passwords are throttled like logins."
    ),
    responses=error_responses((400, "password_wrong"), (422, "password_too_short"), (429, "login_throttled")),
)
def change_password(
    payload: PasswordChangeIn, session: CurrentSession, account: CurrentAccount, db: DbSession
) -> None:
    validate_new_password(payload.new_password)
    key = anmeldebremse.PASSWORD_CHANGE_KEY
    wait = anmeldebremse.wait_seconds(key)
    if wait:
        logger.warning("Password change refused, the brake is on for %d more seconds", wait)
        raise throttled(wait)
    if not verify_password(payload.current_password, account.password_hash):
        anmeldebremse.failed(key)
        logger.info("Password change refused, the current password is wrong")
        raise error("password_wrong", "The current password is wrong.", 400)
    anmeldebremse.succeeded(key)
    account.password_hash = hash_password(payload.new_password)
    db.commit()
    ended = sessions.end_others(db, session)
    logger.info("Password changed, ended %d other sessions", ended)
