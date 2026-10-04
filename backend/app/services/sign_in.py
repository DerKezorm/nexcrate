"""How the one account signs in: the OpenID Connect provider, the identity linked to the account, and the switch for
the sign-in with a password.

Everything lives in ``settings``, not in columns of ``account``: there is one account, and a new column would cost
the copy of the whole database before the schema change.

⚠️ **Exactly one identity of the provider opens nexcrate**, the one linked from a session with the password. Whoever
else the provider lets through is refused here, however the provider is set up: an authentik application without a
policy is open to every user of that authentik, the ones made only for a media server included.

The password sign-in can be switched off only while an identity is linked. ``NEXCRATE_PASSWORD_LOGIN=1`` opens it
again whatever the setting says, for the day the provider is gone.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import Request
from sqlalchemy.orm import Session as OrmSession

from .. import crypto
from ..config import get_settings
from ..db import get_setting, set_setting
from ..models import utcnow
from . import oidc, web

logger = logging.getLogger("nexcrate.sign_in")

ISSUER = "oidc_issuer"
CLIENT_ID = "oidc_client_id"
CLIENT_SECRET = "oidc_client_secret"
PROVIDER_NAME = "oidc_provider_name"
SUBJECT = "oidc_subject"
SUBJECT_NAME = "oidc_subject_name"
LINKED_AT = "oidc_linked_at"
PASSWORD_LOGIN = "password_login"

DEFAULT_PROVIDER_NAME = "OpenID Connect"
CALLBACK_PATH = "/api/oidc/callback"
#: Longer names from the provider are cut: it is a label for the settings page, nothing more.
NAME_MAX = 100


@dataclass(frozen=True)
class Provider:
    issuer: str
    client_id: str
    client_secret: str
    name: str


def provider(db: OrmSession) -> Provider | None:
    """The configured provider with its secret opened, or None when something is missing.

    A secret that no longer opens (another secret key after a restore) counts as missing: signing in fails closed,
    and the settings page says that the provider is not set up.
    """
    issuer = get_setting(db, ISSUER)
    client_id = get_setting(db, CLIENT_ID)
    secret = crypto.decrypt(get_setting(db, CLIENT_SECRET))
    if not issuer or not client_id or not secret:
        return None
    return Provider(issuer=issuer, client_id=client_id, client_secret=secret, name=provider_name(db))


def configured(db: OrmSession) -> bool:
    return provider(db) is not None


def provider_name(db: OrmSession) -> str:
    return get_setting(db, PROVIDER_NAME) or DEFAULT_PROVIDER_NAME


def save_provider(db: OrmSession, issuer: str, client_id: str, secret: str | None, name: str) -> None:
    """Stage the provider; ``secret`` None keeps the stored one. A new issuer drops the link: a subject means
    something only at the provider that issued it, and user "3" there is not user "3" here. The caller commits."""
    previous = get_setting(db, ISSUER)
    if previous and previous != issuer:
        forget_link(db)
        logger.warning("OIDC issuer changed from %r to %r, the linked identity was dropped", previous, issuer)
    set_setting(db, ISSUER, issuer)
    set_setting(db, CLIENT_ID, client_id)
    set_setting(db, PROVIDER_NAME, name)
    if secret is not None:
        set_setting(db, CLIENT_SECRET, crypto.encrypt(secret))
    oidc.clear_cache()


def remove_provider(db: OrmSession) -> None:
    """Stage removing the provider, the link with it. The password sign-in opens again: without a provider it would
    lock the owner out. The caller commits."""
    for key in (ISSUER, CLIENT_ID, CLIENT_SECRET, PROVIDER_NAME):
        set_setting(db, key, "")
    forget_link(db)
    set_setting(db, PASSWORD_LOGIN, "1")
    oidc.clear_cache()


# --- The linked identity ----------------------------------------------------------------------------------------- #


def linked_subject(db: OrmSession) -> str:
    return get_setting(db, SUBJECT)


def linked_name(db: OrmSession) -> str:
    return get_setting(db, SUBJECT_NAME)


def linked_at(db: OrmSession) -> str:
    return get_setting(db, LINKED_AT)


def link(db: OrmSession, identity: oidc.Identity) -> None:
    """Stage the identity as the one that opens the account. The caller commits."""
    set_setting(db, SUBJECT, identity.subject)
    set_setting(db, SUBJECT_NAME, (identity.username or identity.email or "")[:NAME_MAX])
    set_setting(db, LINKED_AT, utcnow().isoformat())


def forget_link(db: OrmSession) -> None:
    """Stage forgetting the linked identity; the password sign-in opens again with it. The caller commits."""
    for key in (SUBJECT, SUBJECT_NAME, LINKED_AT):
        set_setting(db, key, "")
    set_setting(db, PASSWORD_LOGIN, "1")


# --- The password sign-in ---------------------------------------------------------------------------------------- #


def emergency_switch() -> bool:
    return (get_settings().password_login or "").strip().lower() in ("1", "on", "true", "yes")


def password_login_setting(db: OrmSession) -> bool:
    """What the owner chose; on until switched off."""
    return get_setting(db, PASSWORD_LOGIN, "1") != "0"


def password_login_open(db: OrmSession) -> bool:
    """Whether a password opens nexcrate now. Off needs a provider and a linked identity on top of the setting, so
    that a half-removed configuration can never lock the owner out; the emergency switch opens it in any case."""
    if emergency_switch() or password_login_setting(db):
        return True
    return not (configured(db) and linked_subject(db))


# --- The address the provider sends the browser back to ---------------------------------------------------------- #


def redirect_uri(db: OrmSession, request: Request) -> str:
    """The callback as the provider has it on file: from nexcrate's address to the outside when it is set (behind a
    proxy that is the only one the provider knows), otherwise from the request and the sub path."""
    base = web.web_url(db)
    if not base:
        base = str(request.base_url).rstrip("/") + get_settings().url_base
    return base + CALLBACK_PATH
