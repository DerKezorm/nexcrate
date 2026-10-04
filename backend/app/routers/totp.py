"""The second factor: switching it on and off, recovery codes, and the code step of the sign-in.

The code step is public: it runs between the password step and the session, carried by the cookie ``nexcrate_pending``
that the password step set (``routers/auth.py``).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from ..deps import CurrentAccount, DbSession
from ..meldungen import error, error_responses
from ..models import ACCOUNT_ID, Account
from ..security import verify_password
from ..services import anmeldebremse, known_devices, sessions, totp
from .auth import INPUT_MAX_LENGTH, Me, me_out, throttled

logger = logging.getLogger("nexcrate.totp")

#: Routes that work without a session; main.py names the reason.
public_router = APIRouter(prefix="/api/auth/login", tags=["auth"])
#: Routes that need a session; main.py adds the requirement.
router = APIRouter(prefix="/api/auth/totp", tags=["auth"])

PENDING_COOKIE = "nexcrate_pending"
PENDING_PATH = "/api/auth/login"


class StateOut(BaseModel):
    enabled: bool = Field(description="A sign-in with the password asks for a code from the app as well.")
    recovery_codes_left: int = Field(description="Unused recovery codes.")


class EnrolmentOut(BaseModel):
    seed: str = Field(description="The secret for the app, base32. Shown once; typed in when the camera is no help.")
    uri: str = Field(description="The otpauth address the QR code carries.")
    qr_svg: str = Field(description="The QR code as SVG.")


class ConfirmIn(BaseModel):
    code: str = Field(max_length=32)
    password: str = Field(max_length=INPUT_MAX_LENGTH)


class PasswordIn(BaseModel):
    password: str = Field(max_length=INPUT_MAX_LENGTH)


class CodesOut(BaseModel):
    recovery_codes: list[str] = Field(description="Eight codes for one use each. Shown once, never again.")


class CodeIn(BaseModel):
    code: str = Field(max_length=32, description="Six digits from the app, or a recovery code.")


def _check_password(account: Account, password: str) -> None:
    """Braked like a password change: these routes hand out or take away what guards the account."""
    key = anmeldebremse.PASSWORD_CHANGE_KEY
    wait = anmeldebremse.wait_seconds(key)
    if wait:
        raise throttled(wait)
    if not verify_password(password, account.password_hash):
        anmeldebremse.failed(key)
        raise error("wrong_password", "The password is wrong.", 400)
    anmeldebremse.succeeded(key)


def set_pending_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        PENDING_COOKIE,
        token,
        max_age=totp.PENDING_SECONDS,
        path=sessions.cookie_path(PENDING_PATH),
        httponly=True,
        samesite="strict",
        secure=sessions.cookie_secure(request),
    )


def _clear_pending_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        PENDING_COOKIE,
        path=sessions.cookie_path(PENDING_PATH),
        httponly=True,
        samesite="strict",
        secure=sessions.cookie_secure(request),
    )


@router.get(
    "",
    response_model=StateOut,
    summary="Is the second factor on?",
    description="Whether a sign-in with the password asks for a code, and how many recovery codes are left.",
)
def read_state(db: DbSession) -> StateOut:
    return StateOut(enabled=totp.enabled(db), recovery_codes_left=totp.recovery_left(db))


@router.post(
    "/begin",
    response_model=EnrolmentOut,
    summary="Start switching the second factor on; the secret is shown once",
    description="Draws a secret and keeps it for ten minutes. Nothing is stored until /confirm.",
    responses=error_responses((409, "totp_already_on")),
)
def begin(account: CurrentAccount, db: DbSession) -> EnrolmentOut:
    if totp.enabled(db):
        raise error("totp_already_on", "The second factor is on already.", 409)
    seed = totp.begin_enrolment()
    uri = totp.provisioning_uri(seed, account.username)
    return EnrolmentOut(seed=seed, uri=uri, qr_svg=totp.qr_svg(uri))


@router.post(
    "/confirm",
    response_model=CodesOut,
    summary="Finish switching the second factor on: a code from the app and the password",
    description="Stores the secret and answers eight recovery codes, once.",
    responses=error_responses(
        (400, "wrong_password"), (400, "totp_code_wrong"), (409, "totp_enrolment_expired"), (429, "login_throttled")
    ),
)
def confirm(payload: ConfirmIn, account: CurrentAccount, db: DbSession) -> CodesOut:
    seed = totp.pending_seed()
    if seed is None:
        raise error("totp_enrolment_expired", "Start again: the secret was drawn more than ten minutes ago.", 409)
    _check_password(account, payload.password)
    step = totp.verify_code(seed, payload.code)
    if step is None:
        raise error("totp_code_wrong", "The code does not fit.", 400)
    codes = totp.generate_recovery_codes()
    totp.store(db, seed, codes, step)
    db.commit()
    totp.drop_enrolment()
    logger.info("Second factor switched on")
    return CodesOut(recovery_codes=codes)


@router.post(
    "/disable",
    status_code=204,
    response_model=None,
    summary="Switch the second factor off; needs the password",
    description="Forgets the secret and the recovery codes. A sign-in through OpenID Connect is not affected.",
    responses=error_responses((400, "wrong_password"), (429, "login_throttled")),
)
def disable(payload: PasswordIn, account: CurrentAccount, db: DbSession) -> None:
    _check_password(account, payload.password)
    totp.remove(db)
    db.commit()
    logger.info("Second factor switched off")


@router.post(
    "/recovery",
    response_model=CodesOut,
    summary="New recovery codes; the old ones stop working",
    description="Answers eight new codes, once. The unused old ones stop working at the same moment.",
    responses=error_responses((400, "wrong_password"), (409, "totp_off"), (429, "login_throttled")),
)
def new_recovery_codes(payload: PasswordIn, account: CurrentAccount, db: DbSession) -> CodesOut:
    if not totp.enabled(db):
        raise error("totp_off", "The second factor is off.", 409)
    _check_password(account, payload.password)
    codes = totp.generate_recovery_codes()
    totp.store_recovery(db, codes)
    db.commit()
    logger.info("New recovery codes drawn")
    return CodesOut(recovery_codes=codes)


@public_router.post(
    "/totp",
    response_model=Me,
    summary="Second step of the sign-in: the code from the app or a recovery code",
    description=(
        "Needs the cookie nexcrate_pending from the password step. Five wrong codes end the pending sign-in; "
        "every wrong code counts on the login brake like a wrong password."
    ),
    responses=error_responses(
        (400, "totp_code_wrong"), (401, "totp_pending_missing"), (429, "login_throttled"), (503, "totp_unreadable")
    ),
)
def login_code(payload: CodeIn, request: Request, response: Response, db: DbSession) -> Me:
    token = request.cookies.get(PENDING_COOKIE)
    pending = totp.get_pending(token)
    if pending is None or token is None:
        _clear_pending_cookie(response, request)
        raise error("totp_pending_missing", "Sign in with your password again.", 401)
    wait = anmeldebremse.wait_seconds(pending.brake_key)
    if wait:
        raise throttled(wait)
    try:
        passed = totp.check(db, payload.code)
    except totp.SeedUnreadable:
        logger.error(
            "The second factor cannot be read with the current secret key. Was NEXCRATE_SECRET_KEY changed, or "
            "data/secret.key lost? Switch it off with: python -m app.cli reset-second-factor"
        )
        raise error("totp_unreadable", "The second factor cannot be read; see the log.", 503) from None
    if not passed:
        anmeldebremse.failed(pending.brake_key)
        if not totp.fail_pending(token):
            _clear_pending_cookie(response, request)
            logger.info("Sign-in code wrong five times, the pending sign-in ended")
            raise error("totp_pending_missing", "Sign in with your password again.", 401)
        logger.info("Sign-in code wrong")
        raise error("totp_code_wrong", "The code does not fit.", 400)
    db.commit()
    totp.finish_pending(token)
    anmeldebremse.succeeded(pending.brake_key)
    _clear_pending_cookie(response, request)
    account = db.get(Account, ACCOUNT_ID)
    if account is None:
        raise error("totp_pending_missing", "Sign in with your password again.", 401)
    sessions.end_token(db, request.cookies.get(sessions.COOKIE_NAME))
    sessions.start(db, response, request)
    known_devices.remember(response, request)
    logger.info("Login succeeded with the second factor, session started")
    return me_out(account)


@public_router.post(
    "/totp/cancel",
    status_code=204,
    response_model=None,
    summary="Give up the second step and start over",
    description="Forgets the pending sign-in and clears its cookie; the password has to be given again.",
)
def cancel_code(request: Request, response: Response) -> None:
    token = request.cookies.get(PENDING_COOKIE)
    if token:
        totp.finish_pending(token)
    _clear_pending_cookie(response, request)
