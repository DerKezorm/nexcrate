"""Notifications to the owner's own services: ntfy, Gotify, Telegram, Discord, a webhook, Apprise and e-mail
.

A target is a tile under Settings, Notifications. Some services have two levels: an ntfy **instance** carries address
and sign-in and holds any number of **topics**; a Telegram **bot** holds chats; a **mail server** holds addresses. Only
the lower level receives anything, so only it has events and a confirmation. Single-level services (Gotify, Discord,
webhook, Apprise) are both at once.

Every field of every service is a column here, flat, as in Nexview: ``password`` and ``token`` hold ciphertext
(``crypto.encrypt``), and Discord's ``url`` too, because the address of a Discord webhook is its key.

⚠️ Both tables come from ``create_all``, without a migration.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, utcnow


class NotificationTarget(Base):
    """One tile: an instance or mailbox of a service, with its events and its place in the event feed."""

    __tablename__ = "notification_targets"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: ntfy, gotify, telegram, discord, webhook, apprise or email.
    channel: Mapped[str] = mapped_column(String(16), index=True)
    #: The instance above a topic, a bot above a chat, a mail server above an address; null on the upper level.
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("notification_targets.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    #: The test message reached somebody (the code was typed, or the mail was accepted).
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Event key to level (low, normal, high, urgent); a key that is missing is off.
    events: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # --- The fields of the services; which a service uses, its module says. ---
    url: Mapped[str] = mapped_column(String(1024), default="")
    topic: Mapped[str] = mapped_column(String(256), default="")
    auth: Mapped[str] = mapped_column(String(16), default="")
    username: Mapped[str] = mapped_column(String(256), default="")
    password: Mapped[str] = mapped_column(String(1024), default="")
    token: Mapped[str] = mapped_column(String(1024), default="")
    chat_id: Mapped[str] = mapped_column(String(64), default="")
    thread_id: Mapped[str] = mapped_column(String(32), default="")
    silent: Mapped[str] = mapped_column(String(8), default="")
    address: Mapped[str] = mapped_column(String(320), default="")
    subject: Mapped[str] = mapped_column(String(256), default="")
    smtp_host: Mapped[str] = mapped_column(String(256), default="")
    smtp_port: Mapped[str] = mapped_column(String(8), default="")
    smtp_security: Mapped[str] = mapped_column(String(16), default="")
    smtp_from_address: Mapped[str] = mapped_column(String(320), default="")
    smtp_from_name: Mapped[str] = mapped_column(String(100), default="")
    #: de or en: the language of the messages, the receiver's, not the one of the interface.
    language: Mapped[str] = mapped_column(String(8), default="de")
    #: The number of the last event of ``/api/v1``'s feed taken over; a new mailbox starts at the newest.
    cursor: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class NotificationMessage(Base):
    """One message for one mailbox, as rendered when the event was taken over, and how sending it went. The last
    ``outbox.KEEP`` per mailbox stay."""

    __tablename__ = "notification_messages"
    __table_args__ = (
        Index("ix_notification_messages_due", "state", "next_at"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("notification_targets.id", ondelete="CASCADE"), index=True)
    #: The event's number; null for a message that comes from no single event (a summary, a new version).
    event_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_key: Mapped[str] = mapped_column(String(32))
    level: Mapped[str] = mapped_column(String(16), default="normal")
    title: Mapped[str] = mapped_column(String(512))
    body: Mapped[str] = mapped_column(Text, default="")
    poster_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    #: pending, delivered or failed (given up after the last try).
    state: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_params: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    done_at: Mapped[datetime | None] = mapped_column(nullable=True)
