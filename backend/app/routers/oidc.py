"""Sign-in through an OpenID Connect provider: its configuration, the authentik button, linking the one account, and
the switch for the sign-in with a password.

The return leg is a browser redirect, not an API answer: the provider sends the browser back with GET, and a human sees
whatever comes out. So every outcome of the callback, every failure included, ends in a redirect into the interface with
a code in the address (``?oidc_error=<code>``), never in bare JSON. A sign-in ends on the start page with the ordinary
session cookie; a link ends on the account settings.

Only the identity linked to the account signs in (``services/sign_in.py``). Linking starts from a session and asks for
the password, so that neither a page elsewhere nor somebody at an unattended browser can hand the account to their own
identity at the provider.

``/state``, ``/start`` and ``/callback`` are public: they run before there is a session.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from ..config import get_settings
from ..deps import CurrentAccount, CurrentSession, DbSession
from ..meldungen import error, error_responses
from ..models import Session, utcnow
from ..security import verify_password
from ..services import anmeldebremse, authentik, known_devices, oidc, sessions, sign_in

logger = logging.getLogger("nexcrate.oidc")

#: Routes that work without a session; main.py names the reason.
public_router = APIRouter(prefix="/api/oidc", tags=["oidc"])
#: Routes that need a session; main.py adds the requirement.
router = APIRouter(prefix="/api/oidc", tags=["oidc"])

#: Where a link ends, in the interface below the sub path.
ACCOUNT_PAGE = "/einstellungen?reiter=konto"
#: How much of a provider's error text goes into the log; it comes from outside and has no length limit.
FOREIGN_TEXT_MAX = 200
#: The brake for callbacks that reached the provider and failed there.
BRAKE_KEY = "oidc-callback"


class ProviderOut(BaseModel):
    configured: bool = Field(description="Issuer, client id and a readable secret are stored.")
    issuer: str
    client_id: str
    provider_name: str = Field(description="The name on the sign-in button; empty means OpenID Connect.")
    redirect_uri: str = Field(description="The callback to register at the provider.")
    linked: bool = Field(description="An identity of the provider is linked to the account.")
    linked_name: str = Field(description="The name the provider gave for the linked identity, as a label.")
    linked_at: str = Field(description="When it was linked, UTC, ISO 8601; empty when nothing is linked.")
    password_login: bool = Field(description="The setting: the sign-in with a password stays open.")
    password_login_open: bool = Field(description="Whether a password opens nexcrate right now.")
    emergency_switch: bool = Field(description="NEXCRATE_PASSWORD_LOGIN=1 holds the password sign-in open.")


class ProviderIn(BaseModel):
    issuer: str = Field(min_length=1, max_length=500)
    client_id: str = Field(min_length=1, max_length=255)
    #: Empty keeps the stored secret; the page never shows it, so an untouched field must not delete it.
    client_secret: str = Field(default="", max_length=500)
    provider_name: str = Field(default="", max_length=64)


class AuthentikIn(BaseModel):
    url: str = Field(min_length=1, max_length=500)
    token: str = Field(min_length=1, max_length=2000)


class StepOut(BaseModel):
    key: str = Field(description="reached, owner, signing_key, mappings, provider, application, binding or filled.")
    ok: bool
    detail: str = Field(description="English: what happened, or what went wrong.")


class AuthentikOut(BaseModel):
    ok: bool
    steps: list[StepOut]
    owner: str = Field(description="The authentik user the application lets in: the owner of the token.")
    provider: ProviderOut


class PasswordIn(BaseModel):
    password: str = Field(max_length=1024)


class LinkOut(BaseModel):
    url: str = Field(description="The address at the provider to send the browser to.")


class PasswordLoginIn(BaseModel):
    enabled: bool


class StateOut(BaseModel):
    enabled: bool = Field(description="The sign-in button is shown: a provider is set up and an identity linked.")
    provider_name: str
    password_login: bool = Field(description="The form for username and password is shown.")


def _view(db: DbSession, request: Request) -> ProviderOut:
    from ..db import get_setting

    return ProviderOut(
        configured=sign_in.configured(db),
        issuer=get_setting(db, sign_in.ISSUER),
        client_id=get_setting(db, sign_in.CLIENT_ID),
        provider_name=get_setting(db, sign_in.PROVIDER_NAME),
        redirect_uri=sign_in.redirect_uri(db, request),
        linked=bool(sign_in.linked_subject(db)),
        linked_name=sign_in.linked_name(db),
        linked_at=sign_in.linked_at(db),
        password_login=sign_in.password_login_setting(db),
        password_login_open=sign_in.password_login_open(db),
        emergency_switch=sign_in.emergency_switch(),
    )


def _check_address(text: str, code: str) -> str:
    address = text.strip().rstrip("/")
    if not address.lower().startswith(("http://", "https://")):
        raise error(code, "The address must start with http:// or https://.", 422)
    return address


def _check_password(account: Any, password: str) -> None:
    """The password before handing the account to an identity, braked like a password change."""
    key = anmeldebremse.PASSWORD_CHANGE_KEY
    wait = anmeldebremse.wait_seconds(key)
    if wait:
        raise error(
            "login_throttled",
            f"Too many failed attempts. Please wait {wait} seconds.",
            429,
            headers={"Retry-After": str(wait)},
            retry_after=wait,
        )
    if not verify_password(password, account.password_hash):
        anmeldebremse.failed(key)
        raise error("wrong_password", "The password is wrong.", 400)
    anmeldebremse.succeeded(key)


# --- Configuration ---------------------------------------------------------------------------------------------- #


@router.get(
    "/config",
    response_model=ProviderOut,
    summary="Read the sign-in through OpenID Connect (never the secret)",
    description="The provider, the callback to register there, the linked identity and the password switch.",
)
def read_config(request: Request, db: DbSession) -> ProviderOut:
    return _view(db, request)


@router.put(
    "/config",
    response_model=ProviderOut,
    summary="Set the OpenID Connect provider by hand",
    description=(
        "Issuer, client id and secret of a provider such as authentik, Authelia, Keycloak or Pocket ID. The issuer is "
        "checked once against its discovery document. A new issuer drops the linked identity. An empty secret keeps "
        "the stored one."
    ),
    responses=error_responses((422, "issuer_invalid"), (422, "issuer_unreachable"), (422, "secret_required")),
)
async def write_config(payload: ProviderIn, request: Request, db: DbSession) -> ProviderOut:
    issuer = _check_address(payload.issuer, "issuer_invalid")
    secret = payload.client_secret.strip()
    if not secret and sign_in.provider(db) is None:
        raise error("secret_required", "Enter the client secret.", 422)
    try:
        await oidc.discovery(issuer, fresh=True)
    except oidc.OidcError as exc:
        code = "issuer_unreachable" if exc.code == "oidc_provider_unreachable" else "issuer_invalid"
        raise error(code, exc.message, 422, reason=exc.code) from exc
    sign_in.save_provider(db, issuer, payload.client_id.strip(), secret or None, payload.provider_name.strip())
    db.commit()
    logger.info("OIDC provider set by hand, issuer %s", issuer)
    return _view(db, request)


@router.delete(
    "/config",
    status_code=204,
    response_model=None,
    summary="Remove the OpenID Connect provider",
    description="Forgets provider and linked identity; the sign-in with a password opens again.",
)
def delete_config(db: DbSession) -> None:
    sign_in.remove_provider(db)
    db.commit()
    logger.info("OIDC provider removed, the password sign-in is open")


@router.post(
    "/authentik/setup",
    response_model=AuthentikOut,
    summary="Set up provider and application in authentik with a one-time token",
    description=(
        "Creates or updates a signing key, the provider, the application and a binding that lets only the token's "
        "user in, then stores the settings. The token is used for these calls only and never stored. Every step is "
        "reported; the first failure stops the run."
    ),
    responses=error_responses((422, "url_invalid")),
)
async def authentik_setup(payload: AuthentikIn, request: Request, db: DbSession) -> AuthentikOut:
    url = _check_address(payload.url, "url_invalid")
    logger.info("authentik setup started for %s", url)
    result = await authentik.setup(db, url, payload.token.strip(), sign_in.redirect_uri(db, request))
    steps = [StepOut(key=step.key, ok=step.ok, detail=step.detail) for step in result.steps]
    return AuthentikOut(ok=result.ok, steps=steps, owner=result.owner, provider=_view(db, request))


@router.get(
    "/authentik/blueprint",
    summary="Download a blueprint that creates the same provider and application in authentik",
    description="For owners who would rather not hand over a token. The binding to a user is left to the owner.",
    response_class=Response,
    responses={200: {"content": {"application/yaml": {}}, "description": "The blueprint as YAML."}},
)
def authentik_blueprint(request: Request, db: DbSession) -> Response:
    return Response(
        content=authentik.blueprint(sign_in.redirect_uri(db, request)),
        media_type="application/yaml",
        headers={"Content-Disposition": 'attachment; filename="nexcrate-authentik.yaml"'},
    )


# --- Linking ---------------------------------------------------------------------------------------------------- #


def _attempt_cookie(response: Response, request: Request, attempt: oidc.Attempt, *, link: str | None) -> None:
    response.set_cookie(
        oidc.COOKIE_NAME,
        oidc.pack_attempt(attempt, link=link),
        max_age=oidc.ATTEMPT_MINUTES * 60,
        path=sessions.cookie_path(oidc.COOKIE_PATH),
        httponly=True,
        # ``lax`` lets the cookie travel on the return from the provider, a top-level navigation from another site;
        # ``strict`` would break exactly that step.
        samesite="lax",
        secure=sessions.cookie_secure(request),
    )


def _drop_attempt_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        oidc.COOKIE_NAME,
        path=sessions.cookie_path(oidc.COOKIE_PATH),
        httponly=True,
        samesite="lax",
        secure=sessions.cookie_secure(request),
    )


@router.post(
    "/link/start",
    response_model=LinkOut,
    summary="Start linking the account to an identity of the provider; needs the password",
    description=(
        "Answers the address at the provider. Whoever signs in there becomes the one identity that opens nexcrate; "
        "a link already there is replaced once the new one succeeds."
    ),
    responses=error_responses(
        (400, "wrong_password"),
        (409, "oidc_not_configured"),
        (429, "login_throttled"),
        (502, "oidc_provider_unreachable"),
    ),
)
async def start_link(
    payload: PasswordIn,
    request: Request,
    response: Response,
    session: CurrentSession,
    account: CurrentAccount,
    db: DbSession,
) -> LinkOut:
    _check_password(account, payload.password)
    provider = sign_in.provider(db)
    if provider is None:
        raise error("oidc_not_configured", "OpenID Connect is not set up.", 409)
    try:
        description = await oidc.discovery(provider.issuer)
    except oidc.OidcError as exc:
        raise error(exc.code, exc.message, 502) from exc
    attempt = oidc.new_attempt()
    url = oidc.authorization_url(description, provider.client_id, sign_in.redirect_uri(db, request), attempt)
    _attempt_cookie(response, request, attempt, link=session.token_hash)
    logger.info("Linking to the provider started")
    return LinkOut(url=url)


@router.delete(
    "/link",
    status_code=204,
    response_model=None,
    summary="Forget the linked identity",
    description="Nobody signs in through the provider afterwards, and the sign-in with a password opens again.",
)
def unlink(db: DbSession) -> None:
    sign_in.forget_link(db)
    db.commit()
    logger.info("The linked identity was forgotten, the password sign-in is open")


@router.put(
    "/password-login",
    response_model=ProviderOut,
    summary="Switch the sign-in with a password on or off",
    description=(
        "Off needs a provider and a linked identity. NEXCRATE_PASSWORD_LOGIN=1 in the environment opens the password "
        "sign-in again whatever this says, for the day the provider is gone."
    ),
    responses=error_responses((409, "oidc_not_linked")),
)
def set_password_login(payload: PasswordLoginIn, request: Request, db: DbSession) -> ProviderOut:
    if not payload.enabled and not (sign_in.configured(db) and sign_in.linked_subject(db)):
        raise error("oidc_not_linked", "Link an identity of the provider first.", 409)
    from ..db import set_setting

    set_setting(db, sign_in.PASSWORD_LOGIN, "1" if payload.enabled else "0")
    db.commit()
    logger.info("Sign-in with a password switched %s", "on" if payload.enabled else "off")
    return _view(db, request)


# --- Public: the button, the way out and the way back ----------------------------------------------------------- #


@public_router.get(
    "/state",
    response_model=StateOut,
    summary="What the sign-in page shows (no session needed)",
    description="Whether the button for the provider and the password form are shown. Reveals nothing else.",
)
def state(db: DbSession) -> StateOut:
    linked = sign_in.configured(db) and bool(sign_in.linked_subject(db))
    return StateOut(
        enabled=linked,
        provider_name=sign_in.provider_name(db) if linked else "",
        password_login=sign_in.password_login_open(db),
    )


def _to_page(path: str, request: Request, **query: str) -> RedirectResponse:
    # 303: the browser loads the target with GET whatever way it came. Below the sub path, as the browser sees it.
    target = get_settings().url_base + path
    if query:
        target += ("&" if "?" in target else "?") + urlencode(query)
    response = RedirectResponse(target, status_code=303)
    _drop_attempt_cookie(response, request)
    return response


# A hop for the browser, not an address for programs: left out of the API description.
@public_router.get("/start", response_class=RedirectResponse, status_code=302, include_in_schema=False)
async def start(request: Request, db: DbSession) -> RedirectResponse:
    # The sign-in button. Failures land on the sign-in page with a code.
    provider = sign_in.provider(db)
    if provider is None or not sign_in.linked_subject(db):
        return _to_page("/", request, oidc_error="oidc_not_configured")
    try:
        description = await oidc.discovery(provider.issuer)
    except oidc.OidcError as exc:
        logger.warning("OIDC sign-in could not be started: %s", exc.code)
        return _to_page("/", request, oidc_error=exc.code)
    attempt = oidc.new_attempt()
    url = oidc.authorization_url(description, provider.client_id, sign_in.redirect_uri(db, request), attempt)
    response = RedirectResponse(url, status_code=302)
    _attempt_cookie(response, request, attempt, link=None)
    return response


# The provider's way back into nexcrate, for the browser alone: left out of the API description.
@public_router.get("/callback", response_class=RedirectResponse, status_code=303, include_in_schema=False)
async def callback(
    request: Request,
    db: DbSession,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> RedirectResponse:
    # The order of the checks is deliberate: is OIDC set up, is the sender braked, then our own state (cookie, state),
    # then the provider, then the identity. Nothing is written before everything in front of it has passed. What fails
    # before the provider was asked can be produced for free by anyone who knows the address, so those exits leave only
    # a DEBUG line; foreign text goes into the log at that level only, truncated and repr'd.
    attempt = oidc.read_attempt(request.cookies.get(oidc.COOKIE_NAME))
    linking = attempt is not None and bool(attempt.get("link"))

    def refuse(code_out: str, reason: str, *, real: bool = True) -> RedirectResponse:
        line = "OIDC callback refused (%s): code=%s"
        if real:
            logger.warning(line, reason, code_out)
        else:
            logger.debug(line, reason, code_out)
        return _to_page(ACCOUNT_PAGE if linking else "/", request, oidc_error=code_out)

    provider = sign_in.provider(db)
    if provider is None:
        return refuse("oidc_not_configured", "OIDC is not set up", real=False)
    wait = anmeldebremse.wait_seconds(BRAKE_KEY)
    if wait:
        return refuse("oidc_throttled", "sender is braked", real=False)
    if error:
        # Checked before the state: a return with ``error`` carries no code and not necessarily a usable state.
        reason = f"provider returned error={error[:FOREIGN_TEXT_MAX]!r}"
        if error_description:
            reason += f" description={error_description[:FOREIGN_TEXT_MAX]!r}"
        return refuse("oidc_denied", reason, real=False)
    if attempt is None:
        return refuse("oidc_state_mismatch", "attempt cookie missing or expired", real=False)
    if not code or not state:
        return refuse("oidc_state_mismatch", "callback without code or state", real=False)
    if attempt.get("state") != state:
        return refuse("oidc_state_mismatch", "state does not match the running attempt", real=False)
    if not oidc.consume_state(state):
        return refuse("oidc_state_mismatch", "state was already used", real=False)

    try:
        description = await oidc.discovery(provider.issuer)
        id_token, access_token = await oidc.exchange_code(
            description,
            provider.client_id,
            provider.client_secret,
            code,
            sign_in.redirect_uri(db, request),
            str(attempt.get("verifier", "")),
        )
        identity = await oidc.verify_id_token(
            description, provider.client_id, id_token, str(attempt.get("nonce", "")), access_token
        )
    except oidc.OidcError as failure:
        anmeldebremse.failed(BRAKE_KEY)
        return refuse(failure.code, f"the run at the provider failed: {failure.code}")

    # From here on the provider vouches for the identity; what is left is whether it is the linked one.
    if linking:
        # The session that started the link must still stand: logged out meanwhile means nobody asked for it any more.
        started_by = db.get(Session, str(attempt.get("link")))
        if started_by is None or started_by.expires_at <= utcnow():
            return refuse("not_logged_in", "the session that started the link has ended")
        sign_in.link(db, identity)
        db.commit()
        anmeldebremse.succeeded(BRAKE_KEY)
        logger.info("Linked to the identity %r of the provider", sign_in.linked_name(db) or "without a name")
        return _to_page(ACCOUNT_PAGE, request, oidc="linked")

    if not sign_in.linked_subject(db) or identity.subject != sign_in.linked_subject(db):
        # Not braked: getting this far takes a real sign-in at the provider, and a household member trying would
        # otherwise brake the owner's own way in.
        name = identity.username or "without a name"
        return refuse("oidc_not_linked", f"the identity {name!r} is not the linked one")
    anmeldebremse.succeeded(BRAKE_KEY)
    response = _to_page("/", request)
    sessions.end_token(db, request.cookies.get(sessions.COOKIE_NAME))
    sessions.start(db, response, request)
    known_devices.remember(response, request)
    logger.info("Signed in through OpenID Connect")
    return response
