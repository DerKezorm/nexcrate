"""nexcrate's side of the shared sign-in module ``vendor/nexoidc``: the ``Store`` over its tables, the start-up
configuration, the switch for the sign-in with a password, and the move of the single provider from ``settings``.

The module runs the protocol, decides who an identity is and keeps the provider list; it never sees a database. What
is nexcrate's own lives here: the one account (``ACCOUNT_ID``), its second factor, the encryption of the client
secrets with the secret key, and the way back to the version before the list (``mirror_legacy``).

⚠️ **One account.** Every link belongs to it, a provider has at most one link, and an identity that is not linked is
refused (``oidc_no_account``): an authentik application without a policy is open to every user of that authentik, the
ones made only for a media server included.

A ``SqlStore`` works inside one SQLAlchemy session, one request. The module commits when an outcome is settled and
rolls back when it refuses after a write.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session as OrmSession

from .. import crypto
from ..config import get_settings
from ..db import SessionLocal, get_setting, set_setting
from ..models import ACCOUNT_ID, Account, OidcLink, OidcProvider, utcnow
from ..vendor import nexoidc
from ..vendor.nexoidc import (
    AccountState,
    Invite,
    Link,
    Provider,
    ProviderValues,
    migrate,
    protocol,
    providers,
    secret_context,
)
from . import totp, web

logger = logging.getLogger("nexcrate.oidc")

APP_NAME = "nexcrate"
#: Where the module sends the browser, below the sub path. ``/konto`` is a jump to Settings, System, Account.
LOGIN_PAGE = "/login"
ACCOUNT_PAGE = "/konto"
HOME = "/"

#: The old single provider in ``settings`` (nexcrate 0.5.0 to 0.8.1). The version before the list reads them; they
#: always describe the entry ``oidc`` or are empty (``mirror_legacy``), and go one version later.
ISSUER = "oidc_issuer"
CLIENT_ID = "oidc_client_id"
CLIENT_SECRET = "oidc_client_secret"
PROVIDER_NAME = "oidc_provider_name"
SUBJECT = "oidc_subject"
SUBJECT_NAME = "oidc_subject_name"
LINKED_AT = "oidc_linked_at"
LEGACY_PROVIDER = (ISSUER, CLIENT_ID, CLIENT_SECRET, PROVIDER_NAME)
LEGACY_LINK = (SUBJECT, SUBJECT_NAME, LINKED_AT)
#: Set once the old settings were looked at: removing every entry later never brings the old provider back.
MIGRATED = "oidc_list_migrated"
#: The owner's choice for the sign-in with a password, "0" off; the same key the version before the list reads.
PASSWORD_LOGIN = "password_login"
#: The old version showed this much of a provider's name for the linked identity.
NAME_MAX = 100


# --- Start-up ------------------------------------------------------------------------------------------------------ #


def public_url() -> str:
    """The address people reach nexcrate at: the setting (``web_url``, sub path included), else
    ``NEXCRATE_PUBLIC_URL``; "" when neither is set (the module then takes the request's address)."""
    with SessionLocal() as db:
        configured = web.web_url(db) or ""
    if configured:
        return configured
    try:
        return web.normalize(get_settings().public_url)
    except ValueError:
        logger.warning("NEXCRATE_PUBLIC_URL is not an http or https address and is ignored")
        return ""


def _server_secret() -> str:
    return get_settings().resolved_secret_key()


def configure() -> None:
    """Tell the module who it runs in; called once at start-up (and by the tests)."""
    base = get_settings().url_base
    nexoidc.configure(
        nexoidc.AppConfig(
            app_name=APP_NAME,
            server_secret=_server_secret,
            public_url=public_url,
            single_account=True,
            login_page=base + LOGIN_PAGE,
            account_page=base + ACCOUNT_PAGE,
            home=base + HOME,
        )
    )


def request_base(request_url_base: str) -> str:
    """The address of a request as the browser sees it: the sub path is taken off by the middleware, never by the
    browser."""
    return request_url_base.rstrip("/") + get_settings().url_base


# --- The store ----------------------------------------------------------------------------------------------------- #


def _seal(value: str | None, provider_id: int) -> str:
    return crypto.encrypt(value, secret_context(provider_id)) if value else ""


class SqlStore:
    """``nexoidc.Store`` over ``oidc_providers`` and ``oidc_links``, inside one session."""

    def __init__(self, db: OrmSession) -> None:
        self.db = db

    # --- Providers -------------------------------------------------------------------------------------------------

    def _provider(self, row: OidcProvider | None) -> Provider | None:
        if row is None:
            return None
        return Provider(
            id=row.id,
            slug=row.slug,
            label=row.label,
            issuer=row.issuer,
            client_id=row.client_id,
            client_secret=crypto.decrypt(row.client_secret_enc, secret_context(row.id)),
            scopes=row.scopes,
            enabled=row.enabled,
            # One account: nobody else ever gets one through a provider.
            auto_create=False,
            trusts_second_factor=row.trusts_second_factor,
            managed=row.managed,
            position=row.position,
            redirect_path=row.redirect_path,
            created_at=row.created_at,
        )

    def list_providers(self) -> list[Provider]:
        rows = self.db.scalars(select(OidcProvider).order_by(OidcProvider.position, OidcProvider.id))
        return [provider for provider in (self._provider(row) for row in rows) if provider is not None]

    def get_provider(self, provider_id: int) -> Provider | None:
        return self._provider(self.db.get(OidcProvider, provider_id))

    def provider_by_slug(self, slug: str) -> Provider | None:
        return self._provider(self.db.scalar(select(OidcProvider).where(OidcProvider.slug == slug)))

    @staticmethod
    def _apply(row: OidcProvider, values: ProviderValues) -> None:
        row.slug, row.label, row.issuer, row.client_id = values.slug, values.label, values.issuer, values.client_id
        row.scopes, row.enabled, row.auto_create = values.scopes, values.enabled, False
        row.trusts_second_factor, row.managed, row.position = (
            values.trusts_second_factor,
            values.managed,
            values.position,
        )
        row.redirect_path = values.redirect_path

    def insert_provider(self, values: ProviderValues) -> Provider:
        row = OidcProvider()
        self._apply(row, values)
        self.db.add(row)
        self.db.flush()
        row.client_secret_enc = _seal(values.client_secret, row.id)
        self.db.flush()
        provider = self._provider(row)
        assert provider is not None
        return provider

    def update_provider(self, provider_id: int, values: ProviderValues) -> Provider:
        row = self.db.get(OidcProvider, provider_id)
        assert row is not None
        self._apply(row, values)
        if values.client_secret is not None:
            row.client_secret_enc = _seal(values.client_secret, row.id)
        self.db.flush()
        provider = self._provider(row)
        assert provider is not None
        return provider

    def delete_provider(self, provider_id: int) -> None:
        # Foreign keys are on (db.py), the links would go anyway; said here so that nothing depends on a pragma.
        self.drop_links(provider_id)
        self.db.execute(delete(OidcProvider).where(OidcProvider.id == provider_id))

    # --- Links -----------------------------------------------------------------------------------------------------

    @staticmethod
    def _link(row: OidcLink) -> Link:
        return Link(
            row.provider_id, row.subject, row.account_id, row.issuer, row.email, row.created_at, row.last_used_at
        )

    def find_link(self, provider_id: int, subject: str) -> Link | None:
        row = self.db.scalar(select(OidcLink).where(OidcLink.provider_id == provider_id, OidcLink.subject == subject))
        return self._link(row) if row else None

    def links_of_provider(self, provider_id: int) -> list[Link]:
        rows = self.db.scalars(select(OidcLink).where(OidcLink.provider_id == provider_id).order_by(OidcLink.id))
        return [self._link(row) for row in rows]

    def links_of_account(self, account_id: int) -> list[Link]:
        rows = self.db.scalars(select(OidcLink).where(OidcLink.account_id == account_id).order_by(OidcLink.id))
        return [self._link(row) for row in rows]

    def add_link(self, link: Link) -> None:
        self.db.add(
            OidcLink(
                provider_id=link.provider_id,
                subject=link.subject,
                account_id=link.account_id,
                issuer=link.issuer,
                email=link.email,
            )
        )
        self.db.flush()

    def remove_link(self, provider_id: int, account_id: int) -> bool:
        result = self.db.execute(
            delete(OidcLink).where(OidcLink.provider_id == provider_id, OidcLink.account_id == account_id)
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

    def drop_links(self, provider_id: int) -> int:
        result = self.db.execute(delete(OidcLink).where(OidcLink.provider_id == provider_id))
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    def touch_link(self, provider_id: int, subject: str, email: str | None) -> None:
        self.db.execute(
            update(OidcLink)
            .where(OidcLink.provider_id == provider_id, OidcLink.subject == subject)
            .values(last_used_at=utcnow(), email=email)
        )

    # --- The one account -------------------------------------------------------------------------------------------

    def account_state(self, account_id: int) -> AccountState | None:
        row = self.db.get(Account, account_id)
        if row is None:
            return None
        return AccountState(
            id=row.id,
            name=row.username,
            blocked=False,
            has_password=bool(row.password_hash),
            has_second_factor=totp.enabled(self.db),
        )

    def clean_name(self, raw: str) -> str:
        # One account: the module never names a new one here (``single_account``).
        return ""

    def name_taken(self, name: str) -> bool:
        return True

    def create_account(self, name: str, *, invite: Invite | None, email: str | None) -> int:
        raise NotImplementedError("nexcrate has one account; a provider never makes another")

    def find_invite(self, key: str) -> Invite | None:
        return None

    def consume_invite(self, invite: Invite) -> bool:
        return False

    def accept_invite(self, account_id: int, invite: Invite) -> None:
        raise NotImplementedError("nexcrate has one account and no invitations")

    # --- Transaction -----------------------------------------------------------------------------------------------

    def commit(self) -> None:
        # Whatever the module settled: the way back to the version before the list sees the same, and a sign-in
        # with a password that nothing could replace any more opens again.
        mirror_legacy(self.db)
        reopen_password_login(self.db)
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()


# --- The sign-in with a password ------------------------------------------------------------------------------------ #


def emergency_switch() -> bool:
    """``NEXCRATE_PASSWORD_LOGIN=1``: the way back for the day the provider is gone."""
    return (get_settings().password_login or "").strip().lower() in ("1", "on", "true", "yes")


def password_login_setting(db: OrmSession) -> bool:
    """What the owner chose; on until switched off."""
    return get_setting(db, PASSWORD_LOGIN, "1") != "0"


def linked_and_active(db: OrmSession) -> bool:
    """An active provider the account is linked to: the only thing that may stand in for the password."""
    found = db.scalar(
        select(OidcLink.id)
        .join(OidcProvider, OidcProvider.id == OidcLink.provider_id)
        .where(OidcLink.account_id == ACCOUNT_ID, OidcProvider.enabled.is_(True))
        .limit(1)
    )
    return found is not None


def password_login_open(db: OrmSession) -> bool:
    """Whether a password opens nexcrate now. Off needs an active provider with a link on top of the setting, so that
    a half-removed configuration can never lock the owner out; the emergency switch opens it in any case."""
    if emergency_switch() or password_login_setting(db):
        return True
    return not linked_and_active(db)


def reopen_password_login(db: OrmSession) -> None:
    """Switched off, but no active provider with a link is left (unlinked, removed, issuer changed, switched off): the
    setting goes back to on, so that a later link does not silently close the password again."""
    db.flush()
    if not password_login_setting(db) and not linked_and_active(db):
        set_setting(db, PASSWORD_LOGIN, "1")
        logger.warning("The sign-in with a password is open again: no active provider is linked any more")


# --- The way back (blueprint 01 "Umstieg" 4) ------------------------------------------------------------------------ #


def mirror_legacy(db: OrmSession) -> None:
    """The way back to nexcrate 0.8.1: that version reads ``oidc_issuer``, ``oidc_client_id``, ``oidc_client_secret``,
    ``oidc_provider_name`` and ``oidc_subject``, and lets in whoever has that subject at that issuer. So they must
    always describe the entry ``oidc`` exactly, or be empty; otherwise going back would bring a removed provider back,
    or let in an identity the account gave up.

    - The entry ``oidc`` gone or its issuer changed: the old settings and the old subject are emptied for good.
    - Else the old settings follow the entry: its client only while it is on (switched off, the old version finds no
      client and lets nobody in through it), its name; the old subject is the account's link there, or empty.

    Nothing is committed here; the store calls it before every commit."""
    db.flush()
    issuer = get_setting(db, ISSUER)
    entry = db.scalar(select(OidcProvider).where(OidcProvider.slug == nexoidc.LEGACY_SLUG)) if issuer else None
    if issuer and (entry is None or not protocol.same_issuer(entry.issuer, issuer)):
        for key in LEGACY_PROVIDER + LEGACY_LINK:
            set_setting(db, key, "")
        logger.info("The provider before the list is gone or another: its old settings and subject are emptied")
        entry = None
    if entry is None:
        if any(get_setting(db, key) for key in LEGACY_LINK):
            for key in LEGACY_LINK:
                set_setting(db, key, "")
        return
    link = db.scalar(select(OidcLink).where(OidcLink.provider_id == entry.id, OidcLink.account_id == ACCOUNT_ID))
    subject = link.subject if link is not None else ""
    if get_setting(db, SUBJECT) != subject:
        set_setting(db, SUBJECT, subject)
        set_setting(db, SUBJECT_NAME, ((link.email or "") if link is not None else "")[:NAME_MAX])
        when: datetime | None = link.created_at if link is not None else None
        set_setting(db, LINKED_AT, when.isoformat() if when is not None else "")
    client_id = entry.client_id if entry.enabled else ""
    secret = crypto.decrypt(entry.client_secret_enc, secret_context(entry.id)) if entry.enabled else ""
    if get_setting(db, CLIENT_ID) != client_id or crypto.decrypt(get_setting(db, CLIENT_SECRET)) != secret:
        set_setting(db, CLIENT_ID, client_id)
        set_setting(db, CLIENT_SECRET, crypto.encrypt(secret) if secret else "")
    if get_setting(db, PROVIDER_NAME) != entry.label:
        set_setting(db, PROVIDER_NAME, entry.label)


# --- The move from the settings (blueprint 01 "Umstieg", 06 "Wanderung") -------------------------------------------- #


def legacy_plan(db: OrmSession) -> migrate.Plan | None:
    """The entry ``oidc`` and its link the old settings become; None when no provider was set up."""
    label = get_setting(db, PROVIDER_NAME)
    legacy = migrate.Legacy(
        issuer=get_setting(db, ISSUER),
        client_id=get_setting(db, CLIENT_ID),
        client_secret=crypto.decrypt(get_setting(db, CLIENT_SECRET)),
        label=label,
        auto_create=False,
        # The authentik button of 0.5.0 to 0.8.1 named the provider "authentik"; the form never did by itself.
        set_up_by_button=label == "authentik",
    )
    subject = get_setting(db, SUBJECT)
    # nexcrate asked no code after the provider before the list: the moved entry keeps that (blueprint 06).
    return migrate.plan(legacy, [(ACCOUNT_ID, subject)] if subject else [], trusts_second_factor=True)


def migration_due(db: OrmSession) -> bool:
    return (
        get_setting(db, MIGRATED) != "1"
        and (db.scalar(select(func.count()).select_from(OidcProvider)) or 0) == 0
        and legacy_plan(db) is not None
    )


def migrate_settings(*, backed_up: bool) -> Provider | None:
    """Once, at the first start with the list: the provider of the settings becomes the entry ``oidc`` with the linked
    subject; ``/api/oidc/callback`` keeps signing in. ``backed_up``: this start copied the database already (the new
    tables do that before the schema change); else a copy is made first. Nothing happens when there was no provider
    or the list has entries already; a second start finds the flag and does nothing."""
    with SessionLocal() as db:
        if not migration_due(db):
            if get_setting(db, MIGRATED) != "1":
                set_setting(db, MIGRATED, "1")
                db.commit()
            return None
    if not backed_up:
        from . import backups

        backups.create(kind=backups.UPDATE, comment="before the sign-in provider list")
    with SessionLocal() as db:
        if not migration_due(db):
            return None
        store = SqlStore(db)
        entry = migrate.apply(store, legacy_plan(db))
        if entry is not None and not entry.client_secret:
            # The old version counted a provider without a readable secret as not set up; so does the list.
            store.update_provider(entry.id, providers.values_of(entry, enabled=False))
            logger.warning("OIDC: the moved entry has no readable client secret and stays off until it is entered")
        set_setting(db, MIGRATED, "1")
        store.commit()
        return entry
