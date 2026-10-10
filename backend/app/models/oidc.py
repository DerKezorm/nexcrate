"""The sign-in providers and the identities linked to the one account (the shared blueprint, part 01).

Two tables, column for column as every nex app has them: ``oidc_providers`` (one row per provider, a button on the
sign-in page each) and ``oidc_links`` (one identity at one provider, bound to the account). nexcrate keeps one account
(``ACCOUNT_ID``), so every link points at it, and a provider has at most one link (``provider_id, account_id`` is
unique).

⚠️ New tables: the first start with them copies the database before the schema change (``db.pending_changes``). That
is wanted; the move of the old single provider from ``settings`` runs once right after (``services/oidc_store``).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, utcnow


class OidcProvider(Base):
    """A provider that speaks OpenID Connect. The client secret is stored encrypted under its own context."""

    __tablename__ = "oidc_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: Part of the callback ``/api/oidc/<slug>/callback``; fixed once created. The entry ``oidc`` keeps the old
    #: callback ``/api/oidc/callback``.
    slug: Mapped[str] = mapped_column(String(40), unique=True)
    label: Mapped[str] = mapped_column(String(64))
    #: Without spaces and without a slash at the end.
    issuer: Mapped[str] = mapped_column(String(500))
    client_id: Mapped[str] = mapped_column(String(255))
    client_secret_enc: Mapped[str] = mapped_column(Text, default="")
    scopes: Mapped[str] = mapped_column(String(500), default="openid profile email")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    #: Always off here: there is one account and no other comes in through a provider.
    auto_create: Mapped[bool] = mapped_column(Boolean, default=False)
    trusts_second_factor: Mapped[bool] = mapped_column(Boolean, default=True)
    #: "" by hand, "authentik" set up by the authentik button.
    managed: Mapped[str] = mapped_column(String(20), default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    #: The callback the provider knows when it is not the standard one; set by a migration only, never by the form.
    redirect_path: Mapped[str] = mapped_column(String(300), default="")
    #: The redirect address the authentik button last registered at authentik for this entry; "" when it never did (an
    #: entry by hand, or one the button made before the address was remembered). Compared with the address of today it
    #: tells whether authentik still sends people to an old one.
    registered_redirect: Mapped[str] = mapped_column(String(500), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class OidcLink(Base):
    """The one account's identity at one provider. ``email`` is for display only, never for finding anybody."""

    __tablename__ = "oidc_links"
    __table_args__ = (
        UniqueConstraint("provider_id", "subject", name="uq_oidc_links_provider_subject"),
        UniqueConstraint("provider_id", "account_id", name="uq_oidc_links_provider_account"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("oidc_providers.id", ondelete="CASCADE"), index=True)
    subject: Mapped[str] = mapped_column(String(255))
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"), index=True)
    #: The ``iss`` of the token that made the link (with Entra ``common`` the real tenant).
    issuer: Mapped[str] = mapped_column(String(500))
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(nullable=True)
