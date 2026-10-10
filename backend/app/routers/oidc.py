"""Sign-in through OpenID Connect providers, the shared way of every nex app (module ``vendor/nexoidc``).

The protocol, the provider list, linking and the authentik button live in the module; these routes put nexcrate's own
parts around it: the session, the password, the second factor and the brake.

Paths below ``/api/oidc``:

| Method | Path | Who |
|---|---|---|
| GET | ``/state`` | anybody: what the sign-in page shows (password form, one button per active provider) |
| GET | ``/providers`` | anybody: slug and label of the active entries |
| GET | ``/{slug}/start`` | anybody: off to the provider |
| GET | ``/{slug}/callback``, ``/callback`` | anybody: the return (the second for the entry ``oidc``, from 0.5.0) |
| GET, POST | ``/admin/providers`` | session: the list (never a secret), add one |
| PUT, DELETE | ``/admin/providers/{id}`` | session: save (``dropped`` links), remove (``count``, ``only``) |
| GET | ``/admin/providers/{id}/impact?issuer=`` | session: the numbers for the two confirmations |
| PUT | ``/admin/providers/order`` | session: ``{"ids": [...]}`` |
| GET | ``/me`` | session: every active entry with ``linked`` |
| POST, DELETE | ``/{slug}/link`` | session: link (password → ``{"url"}``), unlink |
| DELETE | ``/admin/accounts/{account}/links/{provider}`` | session, with the password |
| POST | ``/authentik/setup`` | session: ``{"url", "token", "confirm_issuer_change"}`` → always 200, the steps |
| GET | ``/authentik/blueprint`` | session: ``nexcrate-authentik.yaml`` |
| GET, PUT | ``/password-login`` | session: the switch for the sign-in with a password |

The return leg is a browser redirect, not an API answer: every outcome, every failure included, ends in a redirect
into the interface (``/login?error=<code>``, or ``/konto?error=<code>`` for a linking), never in bare JSON.

⚠️ The session cookie is ``SameSite=Strict`` and does not come back from the provider. A linking therefore carries the
hash of the session that started it in a signed cookie of its own next to the attempt; the session must still stand
when the provider sends the browser back.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from ..config import get_settings
from ..deps import CurrentAccount, CurrentSession, DbSession
from ..meldungen import error, error_responses
from ..models import ACCOUNT_ID, Session, utcnow
from ..security import verify_password
from ..services import anmeldebremse, known_devices, oidc_store, sessions, totp
from ..vendor.nexoidc import OidcError, ProviderInvalid, accounts, authentik, flow, providers
from ..vendor.nexoidc.config import current as oidc_config

logger = logging.getLogger("nexcrate.oidc")

#: Routes that work without a session; main.py names the reason.
public_router = APIRouter(prefix="/api/oidc", tags=["oidc"])
#: Routes that need a session; main.py adds the requirement.
router = APIRouter(prefix="/api/oidc", tags=["oidc"])

#: The brake of the way through a provider: start, return and the start of a linking. Like the password brake, not
#: per address (behind a proxy every visitor shares one).
BRAKE_KEY = "oidc-callback"
#: The cookie that names the session a linking started from (the session cookie itself stays behind, Strict).
LINK_COOKIE = "nexcrate_oidc_link"
#: Errors that cost nothing to produce, that anybody can make without an attempt of their own, or that a household
#: member at the provider would cause: they do not brake. ⚠️ Everything the module raises before it has read a valid
#: attempt cookie belongs here (``oidc_provider_error`` is raised first: ``/callback?error=x`` needs no cookie, and
#: braking it would let any visitor lock the owner out). What needs a started attempt (a code the provider refuses) does
#: brake, as it did in 0.8.1.
UNBRAKED = frozenset(
    {"oidc_state_mismatch", "too_many_attempts", "oidc_no_account", "oidc_not_configured", "oidc_provider_error"}
)


class ProviderIn(BaseModel):
    label: str = Field(max_length=200)
    slug: str = Field(default="", max_length=100, description="Only when adding; fixed afterwards.")
    issuer: str = Field(max_length=600)
    client_id: str = Field(max_length=300)
    #: Empty keeps the stored secret when editing; the page never shows it.
    client_secret: str = Field(default="", max_length=2000)
    scopes: str = Field(default="openid profile email", max_length=500)
    enabled: bool = True
    auto_create: bool = Field(default=False, description="Always off in nexcrate: there is one account.")
    trusts_second_factor: bool = True


class OrderIn(BaseModel):
    ids: list[int]


class PasswordIn(BaseModel):
    password: str = Field(max_length=1024)


class SetupIn(BaseModel):
    url: str = Field(min_length=1, max_length=500)
    token: str = Field(min_length=1, max_length=2000)
    #: The operator confirmed that the run may change the issuer of the button's entry (``reason: issuer_change``).
    confirm_issuer_change: bool = False


class PasswordLoginIn(BaseModel):
    enabled: bool


class PasswordLoginOut(BaseModel):
    password_login: bool = Field(description="The setting: the sign-in with a password stays open.")
    password_login_open: bool = Field(description="Whether a password opens nexcrate right now.")
    can_switch_off: bool = Field(description="An active provider is linked: only then can the password go.")
    emergency_switch: bool = Field(description="NEXCRATE_PASSWORD_LOGIN=1 holds the password sign-in open.")


class StateOut(BaseModel):
    password_login: bool = Field(description="The form for username and password is shown.")
    providers: list[dict[str, str]] = Field(description="One button per active provider: slug and label, in order.")


# --- Helpers ------------------------------------------------------------------------------------------------------ #


def _base(request: Request) -> str:
    return oidc_store.request_base(str(request.base_url))


def _store(db: Any) -> oidc_store.SqlStore:
    return oidc_store.SqlStore(db)


def _to_response(redirect: flow.Redirect) -> RedirectResponse:
    # 303: the browser loads the target with GET whatever way it came.
    response = RedirectResponse(redirect.location, status_code=303)
    _put_cookie(response, redirect.cookie)
    return response


def _put_cookie(response: Response, cookie: flow.Cookie) -> None:
    if cookie.max_age == 0:
        response.delete_cookie(
            cookie.name, path=cookie.path, secure=cookie.secure, httponly=True, samesite="lax"
        )
        response.delete_cookie(LINK_COOKIE, path=cookie.path, secure=cookie.secure, httponly=True, samesite="lax")
        return
    response.set_cookie(
        cookie.name,
        cookie.value,
        max_age=cookie.max_age,
        path=cookie.path,
        httponly=cookie.http_only,
        samesite="lax",
        secure=cookie.secure,
    )


def _link_key() -> bytes:
    secret = get_settings().resolved_secret_key().encode("utf-8")
    return hashlib.sha256(b"nexcrate-oidc-link:" + secret).digest()


def _link_value(session_hash: str) -> str:
    mac = hmac.new(_link_key(), session_hash.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{session_hash}.{mac}"


def _linking_account(request: Request, db: Any) -> int | None:
    """The account a returning linking belongs to: the session that started it must still stand. Its own cookie, or
    the session cookie when the browser sends it along."""
    candidates = []
    value = request.cookies.get(LINK_COOKIE) or ""
    session_hash, _, mac = value.partition(".")
    if session_hash and mac and hmac.compare_digest(_link_value(session_hash), value):
        candidates.append(db.get(Session, session_hash))
    candidates.append(sessions.find(db, request.cookies.get(sessions.COOKIE_NAME)))
    for row in candidates:
        if row is not None and row.expires_at > utcnow():
            return row.account_id
    return None


def _braked() -> bool:
    return anmeldebremse.wait_seconds(BRAKE_KEY) > 0


def _form_error(failure: ProviderInvalid) -> Any:
    return error(failure.code, failure.message, 422, field=failure.field)


def _check_password(account: Any, password: str) -> None:
    """The password before handing the account to an identity or taking a link away, braked like a password change."""
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


def _password_view(db: Any) -> PasswordLoginOut:
    return PasswordLoginOut(
        password_login=oidc_store.password_login_setting(db),
        password_login_open=oidc_store.password_login_open(db),
        can_switch_off=oidc_store.linked_and_active(db),
        emergency_switch=oidc_store.emergency_switch(),
    )


# --- Public: the sign-in page, the way out and the way back ------------------------------------------------------- #


@public_router.get(
    "/state",
    response_model=StateOut,
    summary="What the sign-in page shows (no session needed)",
    description="Whether the password form is shown, and a button per active provider. Reveals nothing else.",
)
def state(db: DbSession) -> StateOut:
    return StateOut(password_login=oidc_store.password_login_open(db), providers=providers.public_list(_store(db)))


@public_router.get(
    "/providers",
    summary="The active sign-in providers (no session needed)",
    description="Slug and label of every active provider, in the order of the list.",
)
def public_providers(db: DbSession) -> list[dict[str, str]]:
    return providers.public_list(_store(db))


# A hop for the browser, not an address for programs: left out of the API description.
@public_router.get("/{slug}/start", response_class=RedirectResponse, status_code=303, include_in_schema=False)
async def start(slug: str, request: Request, db: DbSession, invite: str | None = None) -> RedirectResponse:
    # ``invite``: nexcrate has no invitations; the module refuses one (``invite_invalid``), as in every one-account app.
    try:
        if _braked():
            raise OidcError("too_many_attempts", "sender is braked")
        redirect = await flow.begin(_store(db), slug, request_base=_base(request), invite=invite)
    except OidcError as failure:
        return _to_response(flow.refused(failure, None))
    return _to_response(redirect)


async def _returned(slug: str, request: Request, db: Any) -> RedirectResponse:
    cookie = request.cookies.get(oidc_config().cookie_name)
    store = _store(db)
    try:
        if _braked():
            raise OidcError("too_many_attempts", "sender is braked")
        arrival = await flow.finish(store, slug, cookie=cookie, params=dict(request.query_params))
        if arrival.started.purpose == "link":
            accounts.link(store, arrival, _linking_account(request, db))
            anmeldebremse.succeeded(BRAKE_KEY)
            return _to_response(flow.arrived(arrival, flow.linked_page(slug)))
        signed = accounts.sign_in(store, arrival)
    except OidcError as failure:
        if failure.code not in UNBRAKED:
            anmeldebremse.failed(BRAKE_KEY)
        return _to_response(flow.refused(failure, cookie))
    anmeldebremse.succeeded(BRAKE_KEY)
    if signed.second_factor_due:
        # The provider does not check the second factor here: the same code step as after the password.
        from .totp import set_pending_cookie

        response = _to_response(flow.arrived(arrival, oidc_config().login_page + "?step=code"))
        set_pending_cookie(response, request, totp.start_pending(anmeldebremse.ACCOUNT_KEY))
        logger.info("Signed in through %s, waiting for the code of the second factor", arrival.provider.slug)
        return response
    response = _to_response(flow.arrived(arrival))
    sessions.end_token(db, request.cookies.get(sessions.COOKIE_NAME))
    sessions.start(db, response, request)
    known_devices.remember(response, request)
    return response


# The provider's way back into nexcrate, for the browser alone: left out of the API description.
@public_router.get("/callback", response_class=RedirectResponse, status_code=303, include_in_schema=False)
async def legacy_callback(request: Request, db: DbSession) -> RedirectResponse:
    # The address of nexcrate 0.5.0 to 0.8.1, valid for ever: it belongs to the entry ``oidc``.
    return await _returned("oidc", request, db)


@public_router.get("/{slug}/callback", response_class=RedirectResponse, status_code=303, include_in_schema=False)
async def callback(slug: str, request: Request, db: DbSession) -> RedirectResponse:
    return await _returned(slug, request, db)


# --- The provider list ------------------------------------------------------------------------------------------- #


@router.get(
    "/admin/providers",
    summary="List the sign-in providers (never a secret)",
    description="Every entry with its callback, whether a secret is stored, and how many links it has.",
)
def admin_providers(request: Request, db: DbSession) -> list[dict[str, Any]]:
    store = _store(db)
    coupled = providers.coupled(store)
    return [
        providers.admin_view(entry, _base(request), len(store.links_of_provider(entry.id)), coupled=coupled)
        for entry in store.list_providers()
    ]


@router.post(
    "/admin/providers",
    status_code=201,
    summary="Add a sign-in provider",
    description="The issuer is checked with a fresh discovery first; nothing is stored when that fails.",
    responses=error_responses((422, "issuer_invalid"), (422, "oidc_issuer_mismatch"), (422, "slug_taken")),
)
async def add_provider(payload: ProviderIn, request: Request, db: DbSession) -> dict[str, Any]:
    store = _store(db)
    try:
        created = await providers.create(store, providers.Form(**payload.model_dump()))
    except ProviderInvalid as failure:
        raise _form_error(failure) from failure
    return providers.admin_view(created, _base(request), 0)


@router.put(
    "/admin/providers/order",
    summary="Put the sign-in providers in order",
    description="The order of the buttons on the sign-in page.",
)
def order_providers(payload: OrderIn, db: DbSession) -> list[dict[str, str]]:
    store = _store(db)
    try:
        providers.reorder(store, payload.ids)
    except ProviderInvalid as failure:
        raise _form_error(failure) from failure
    return providers.public_list(store)


@router.put(
    "/admin/providers/{provider_id}",
    summary="Save a sign-in provider",
    description=(
        "The short name stays. A really different issuer drops the provider's links (the same issuer written with or "
        "without a slash drops nothing); ``dropped`` says how many. An empty secret keeps the stored one."
    ),
    responses=error_responses((422, "issuer_invalid"), (422, "oidc_issuer_mismatch"), (422, "provider_unknown")),
)
async def save_provider(provider_id: int, payload: ProviderIn, request: Request, db: DbSession) -> dict[str, Any]:
    store = _store(db)
    try:
        saved, dropped = await providers.update(store, provider_id, providers.Form(**payload.model_dump()))
    except ProviderInvalid as failure:
        raise _form_error(failure) from failure
    view = providers.admin_view(saved, _base(request), len(store.links_of_provider(saved.id)))
    return {**view, "dropped": dropped}


@router.get(
    "/admin/providers/{provider_id}/impact",
    summary="What a change of issuer or a removal would cost",
    description="``issuer_change``: accounts that must link again with this issuer; ``count``, ``only``: removal.",
)
def provider_impact(provider_id: int, db: DbSession, issuer: str = "") -> dict[str, Any]:
    store = _store(db)
    removal = providers.removal_impact(store, provider_id)
    change = providers.issuer_change_impact(store, provider_id, issuer) if issuer else 0
    return {"issuer_change": change, **asdict(removal)}


@router.delete(
    "/admin/providers/{provider_id}",
    summary="Remove a sign-in provider with its links",
    description="The answer names how many links went. The sign-in with a password opens again when no link is left.",
    responses=error_responses((422, "provider_unknown")),
)
def remove_provider(provider_id: int, db: DbSession) -> dict[str, Any]:
    try:
        return asdict(providers.remove(_store(db), provider_id))
    except ProviderInvalid as failure:
        raise _form_error(failure) from failure


# --- The account's links ----------------------------------------------------------------------------------------- #


@router.get(
    "/me",
    summary="The sign-in providers of the account",
    description="Every active provider, whether the account is linked to it, and the address the provider named.",
)
def my_providers(db: DbSession) -> list[dict[str, Any]]:
    store = _store(db)
    linked = {link.provider_id: link for link in store.links_of_account(ACCOUNT_ID)}
    return [
        {
            "slug": entry.slug,
            "label": entry.label,
            "linked": entry.id in linked,
            "email": linked[entry.id].email if entry.id in linked else None,
        }
        for entry in store.list_providers()
        if entry.enabled
    ]


@router.post(
    "/{slug}/link",
    summary="Start linking the account to an identity of a provider; needs the password",
    description=(
        "Answers the address at the provider. Whoever signs in there becomes the account's identity at this provider; "
        "an identity linked before is replaced once the new one succeeds."
    ),
    responses=error_responses(
        (400, "wrong_password"), (409, "oidc_not_configured"), (429, "login_throttled"), (429, "too_many_attempts")
    ),
)
async def link_start(
    slug: str,
    payload: PasswordIn,
    request: Request,
    response: Response,
    session: CurrentSession,
    account: CurrentAccount,
    db: DbSession,
) -> dict[str, str]:
    if _braked():
        raise error("too_many_attempts", "Too many attempts. Wait a moment, then try again.", 429)
    _check_password(account, payload.password)
    try:
        redirect = await flow.begin(_store(db), slug, request_base=_base(request), link_account_id=account.id)
    except OidcError as failure:
        raise error(failure.code, failure.message, 409) from failure
    cookie = redirect.cookie
    _put_cookie(response, cookie)
    response.set_cookie(
        LINK_COOKIE,
        _link_value(session.token_hash),
        max_age=cookie.max_age,
        path=cookie.path,
        httponly=True,
        samesite="lax",
        secure=cookie.secure,
    )
    logger.info("Linking to provider %s started", slug)
    return {"url": redirect.location}


@router.delete(
    "/{slug}/link",
    status_code=204,
    response_model=None,
    summary="Unlink the account from a provider",
    description="Nobody signs in through this provider afterwards; with no link left the password opens again.",
    responses=error_responses((404, "oidc_not_configured")),
)
def unlink(slug: str, db: DbSession) -> None:
    store = _store(db)
    entry = store.provider_by_slug(slug)
    if entry is None:
        raise error("oidc_not_configured", "This provider is not set up.", 404)
    try:
        accounts.unlink(store, ACCOUNT_ID, entry.id)
    except OidcError as failure:
        raise error(failure.code, failure.message, 409) from failure


@router.delete(
    "/admin/accounts/{account_id}/links/{provider_id}",
    status_code=204,
    response_model=None,
    summary="Remove a link of an account, with the own password",
    description="nexcrate has one account: this is the same as unlinking, with the password asked once more.",
    responses=error_responses((400, "wrong_password"), (429, "login_throttled")),
)
def operator_unlink(
    account_id: int, provider_id: int, payload: PasswordIn, account: CurrentAccount, db: DbSession
) -> None:
    _check_password(account, payload.password)
    try:
        accounts.unlink(_store(db), account_id, provider_id)
    except OidcError as failure:
        raise error(failure.code, failure.message, 409) from failure


# --- authentik --------------------------------------------------------------------------------------------------- #


@router.post(
    "/authentik/setup",
    summary="Set up provider and application in authentik with a one-time token",
    description=(
        "Creates or updates a signing key, the provider, the application, fills in the entry here and binds the "
        "application to the token's user. The token is used for these calls only and never stored. Always 200 with "
        "the steps that ran; the first failure stops the run and names a reason. A run that would change the issuer "
        "of the entry with links stops first (``issuer_change``, ``count``) until ``confirm_issuer_change`` is true."
    ),
)
async def authentik_setup(payload: SetupIn, request: Request, db: DbSession) -> dict[str, Any]:
    result = await authentik.setup(
        _store(db), payload.url, payload.token, _base(request), confirm_issuer_change=payload.confirm_issuer_change
    )
    return result.as_dict()


@router.get(
    "/authentik/blueprint",
    summary="Download a blueprint that creates the same provider and application in authentik",
    description="For owners who would rather not hand over a token. Client id and secret go into the form afterwards.",
    response_class=Response,
    responses={200: {"content": {"application/yaml": {}}, "description": "The blueprint as YAML."}},
)
def authentik_blueprint(request: Request, db: DbSession) -> Response:
    text = authentik.blueprint(authentik.blueprint_redirect(_store(db), _base(request)))
    name = authentik.blueprint_filename()
    return Response(
        content=text, media_type="application/yaml", headers={"Content-Disposition": f'attachment; filename="{name}"'}
    )


# --- The sign-in with a password -------------------------------------------------------------------------------- #


@router.get(
    "/password-login",
    response_model=PasswordLoginOut,
    summary="Read the switch for the sign-in with a password",
    description="The setting, whether a password opens nexcrate now, and whether it may be switched off.",
)
def read_password_login(db: DbSession) -> PasswordLoginOut:
    return _password_view(db)


@router.put(
    "/password-login",
    response_model=PasswordLoginOut,
    summary="Switch the sign-in with a password on or off",
    description=(
        "Off needs an active provider the account is linked to. NEXCRATE_PASSWORD_LOGIN=1 in the environment opens "
        "the password sign-in again whatever this says, for the day the provider is gone."
    ),
    responses=error_responses((409, "password_login_needs_link")),
)
def set_password_login(payload: PasswordLoginIn, db: DbSession) -> PasswordLoginOut:
    if not payload.enabled and not oidc_store.linked_and_active(db):
        raise error("password_login_needs_link", "Link the account to an active provider first.", 409)
    from ..db import set_setting

    set_setting(db, oidc_store.PASSWORD_LOGIN, "1" if payload.enabled else "0")
    db.commit()
    logger.info("Sign-in with a password switched %s", "on" if payload.enabled else "off")
    return _password_view(db)
