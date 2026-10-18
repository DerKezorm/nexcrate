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
#: What the old settings and the old subject said when the mirror wrote them last (``migrate.fingerprint``); at the next
#: start ``migrate.resync`` takes over what the version before the list changed since.
FINGERPRINT = "oidc_legacy_fingerprint"
#: The issuer the version before the list knows, as it wrote it (with or without the slash), or ``migrate.CLOSED``:
#: ``migrate.way_back`` decides by it what the old settings show. Stored before the move and kept by ``resync``.
ANCHOR = "oidc_legacy_anchor"
#: The owner's choice for the sign-in with a password, "0" off; the same key the version before the list reads.
PASSWORD_LOGIN = "password_login"
#: The old version showed this much of a provider's name for the linked identity.
NAME_MAX = 100
#: How the version before the list behaved after a provider: no code asked.
TRUSTED_BEFORE = True


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


def save_public_url(url: str) -> None:
    """The address the operator confirmed in the authentik card, stored as the address to the outside (the setting
    ``public_url`` reads first), in a transaction of its own and committed at once: it outlives a restart, and a run of
    the button that fails later keeps it. ``ValueError`` for an address the module would refuse."""
    value = web.normalize(url)
    with SessionLocal() as db:
        set_setting(db, web.SETTING, value)
        db.commit()


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
            save_public_url=save_public_url,
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
            registered_redirect=row.registered_redirect or "",
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
        if values.registered_redirect is not None:
            # None keeps what the button registered last; only the button writes it.
            row.registered_redirect = values.registered_redirect

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


# --- The way back --------------------------------------------------------------------------------------------------- #


def read_legacy(db: OrmSession) -> tuple[migrate.Legacy, list[tuple[int, str]]]:
    """The old settings and the old subject, read the one way the move, the fingerprint and ``resync`` read them. The
    secret is the decrypted one: it is sealed with a fresh nonce at every write, and a fingerprint of the sealed text
    would change with each."""
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
    return legacy, [(ACCOUNT_ID, subject)] if subject else []


def mirror_legacy(db: OrmSession) -> None:
    """The way back to nexcrate 0.8.1 (see ``_mirror``), then the fingerprint of what stands there now, in the same
    transaction: at the next start ``migrate.resync`` takes over only what 0.8.1 changed after it.

    Nothing is committed here; the store calls it before every commit."""
    _mirror(db)
    db.flush()
    legacy, subjects = read_legacy(db)
    set_setting(db, FINGERPRINT, migrate.fingerprint(legacy, subjects))


def _mirror(db: OrmSession) -> None:
    """That version reads ``oidc_issuer``, ``oidc_client_id``, ``oidc_client_secret``, ``oidc_provider_name`` and
    ``oidc_subject``, and lets in whoever has that subject at that issuer. What it finds there is the module's one rule
    for the way back (``migrate.way_back``, decided by the entry ``oidc`` and the stored anchor); this writes it in the
    form 0.8.1 reads:

    - The entry on and trusting the provider with the second factor, at the issuer the anchor names: all of it.
    - Off, or asking the code here: issuer and name stay, no client (0.8.1 knows neither switch and would skip the code,
      so with no client it lets nobody in). On again brings everything back.
    - Another issuer than the anchor, or no entry: everything empty, the subject too, and the anchor closed for good.

    The issuer is written as the anchor holds it, letter by letter (0.8.1 compares it so when a provider is saved, and
    a slash more or less would throw the linked subject away). The anchor is stored here, in the same transaction."""
    db.flush()
    store = SqlStore(db)
    entry = store.provider_by_slug(nexoidc.LEGACY_SLUG)
    link = (
        db.scalar(select(OidcLink).where(OidcLink.provider_id == entry.id, OidcLink.account_id == ACCOUNT_ID))
        if entry is not None
        else None
    )
    mirror = migrate.way_back(entry, [(ACCOUNT_ID, link.subject)] if link is not None else [], get_setting(db, ANCHOR))
    legacy = mirror.legacy
    if get_setting(db, ANCHOR) != mirror.anchor:
        set_setting(db, ANCHOR, mirror.anchor)
    wanted = mirror.subjects.get(ACCOUNT_ID, "")
    if get_setting(db, SUBJECT) != wanted:
        set_setting(db, SUBJECT, wanted)
        set_setting(db, SUBJECT_NAME, ((link.email or "") if link is not None and wanted else "")[:NAME_MAX])
        when: datetime | None = link.created_at if link is not None and wanted else None
        set_setting(db, LINKED_AT, when.isoformat() if when is not None else "")
    if get_setting(db, ISSUER) != legacy.issuer:
        set_setting(db, ISSUER, legacy.issuer)
    secret_now = crypto.decrypt(get_setting(db, CLIENT_SECRET))
    if get_setting(db, CLIENT_ID) != legacy.client_id or secret_now != legacy.client_secret:
        set_setting(db, CLIENT_ID, legacy.client_id)
        set_setting(db, CLIENT_SECRET, crypto.encrypt(legacy.client_secret) if legacy.client_secret else "")
    if get_setting(db, PROVIDER_NAME) != legacy.label:
        set_setting(db, PROVIDER_NAME, legacy.label)


# --- The move from the settings ------------------------------------------------------------------------------------- #


def legacy_plan(db: OrmSession) -> migrate.Plan | None:
    """The entry ``oidc`` and its link the old settings become; None when no provider was set up."""
    legacy, subjects = read_legacy(db)
    # nexcrate asked no code after the provider before the list: the moved entry keeps that.
    return migrate.plan(legacy, subjects, trusts_second_factor=TRUSTED_BEFORE)


def migration_due(db: OrmSession) -> bool:
    return (
        get_setting(db, MIGRATED) != "1"
        and (db.scalar(select(func.count()).select_from(OidcProvider)) or 0) == 0
        and legacy_plan(db) is not None
    )


def migrate_settings(*, backed_up: bool) -> Provider | None:
    """What every start runs for the old single provider. The first start with the list: the provider of the settings
    becomes the entry ``oidc`` with the linked subject; ``/api/oidc/callback`` keeps signing in. ``backed_up``: this
    start copied the database already (the new tables do that before the schema change); else a copy is made first.
    Nothing is moved when there was no provider or the list has entries already.

    Every later start: whatever the version before the list changed in the old settings since the last mirror (the
    owner went back, changed something there and came up again) is taken over (``migrate.resync``), with a copy of the
    database first when there is something to take over. The mirror, the anchor and the fingerprint are written again
    each time, also on a start without any provider (a provider set up in 0.8.1 later would be thrown away
    otherwise), so that the next start compares against what stands there now."""
    with SessionLocal() as db:
        legacy, subjects = read_legacy(db)
        moving = migration_due(db)
        changed = (
            not moving
            and get_setting(db, MIGRATED) == "1"
            and migrate.changes_since(get_setting(db, FINGERPRINT) or None, legacy, subjects).any
        )
    if (moving or changed) and not backed_up:
        from . import backups

        comment = "before the sign-in provider list" if moving else "before taking over the sign-in settings of 0.8.1"
        backups.create(kind=backups.UPDATE, comment=comment)
    with SessionLocal() as db:
        store = SqlStore(db)
        if moving and migration_due(db):
            # The anchor first: the commit of the move mirrors the way back and has to see it.
            legacy, _subjects = read_legacy(db)
            set_setting(db, ANCHOR, migrate.anchor_of(legacy))
            entry = migrate.apply(store, legacy_plan(db))
            if entry is not None and not entry.client_secret:
                # The old version counted a provider without a readable secret as not set up; so does the list.
                store.update_provider(entry.id, providers.values_of(entry, enabled=False))
                logger.warning("OIDC: the moved entry has no readable client secret and stays off until it is entered")
            set_setting(db, MIGRATED, "1")
            store.commit()
            return entry
        if get_setting(db, MIGRATED) == "1":
            legacy, subjects = read_legacy(db)

            def keep_anchor(value: str) -> None:
                set_setting(db, ANCHOR, value)

            migrate.resync(
                store,
                legacy,
                subjects,
                previous=get_setting(db, FINGERPRINT) or None,
                trusts_second_factor=TRUSTED_BEFORE,
                anchor=get_setting(db, ANCHOR) or None,
                keep_anchor=keep_anchor,
            )
        else:
            legacy, _subjects = read_legacy(db)
            set_setting(db, ANCHOR, migrate.anchor_of(legacy))
            set_setting(db, MIGRATED, "1")
        store.commit()
        return None
