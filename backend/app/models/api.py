"""The tables of the machine interface ``/api/v1`` (plan-api-v3.md): the keys programs sign
in with, the change marker of the library, and the event feed.

⚠️ All tables come from ``create_all``. A change of a column here needs a new migration or the missing-column upkeep,
never an edit of an existing migration.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, utcnow

#: What a key may do. ``read`` reads, ``request`` adds and takes back (from V2), ``operate`` resolves problems and
#: assigns by hand (from V3). A key with ``request`` or ``operate`` always carries ``read`` as well.
API_SCOPES = ("read", "request", "operate")


class ApiKey(Base):
    """A key a program signs in with. Only the SHA-256 hash of the key is stored, never the key.

    ``hint`` holds its last four characters, so the owner can tell two keys apart. Revoking a key removes the row.
    """

    __tablename__ = "api_keys"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    hint: Mapped[str] = mapped_column(String(8), default="")
    #: A list out of ``API_SCOPES``.
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    #: Written at most once a minute by a background job, never by the request itself: reading stays reading.
    last_used_at: Mapped[datetime | None] = mapped_column(nullable=True)


class TitleChange(Base):
    """The change marker of one title: a running number that moves whenever what ``/api/v1`` says about it changes.

    ``fingerprint`` is the SHA-256 of the title as ``/api/v1/titles`` lists it. A title that went away keeps its row
    with ``removed_at``, so a program that asks for the changes since its last number learns that it is gone.
    ``title_id`` is no foreign key for that reason.
    """

    __tablename__ = "title_changes"
    __table_args__ = (Index("ix_title_changes_seq", "seq", unique=True),)

    title_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    kind: Mapped[str] = mapped_column(String(16))
    #: ``tmdb:603``: the title's reference when it was last seen.
    ref: Mapped[str] = mapped_column(String(64))
    seq: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(String(64), default="")
    removed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    #: The fixed ids of the title's versions when it was last seen, so the event feed can tell a version that came or
    #: went. Null until the first look after V3 fills it. ⚠️ From the missing-column upkeep:
    #: nullable and without a default on purpose.
    versions: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)


class ApiEvent(Base):
    """One entry of the event feed ``/api/v1/events`` (N30).

    ``seq`` is the number a program asks after; ``AUTOINCREMENT``, so a number is never given twice, not even after the
    newest row was cleared away. The title is kept by kind, reference and name as it was: an event about a title that
    left the library still says which. ``block`` holds what only the kind has (``{"series": {...}}``, rule 3).

    ⚠️ Comes from ``create_all``, without a migration.
    """

    __tablename__ = "api_events"
    __table_args__ = ({"sqlite_autoincrement": True},)

    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(48))
    at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    #: The row number of the title, for joining only; never handed out.
    title_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    #: The version definition; its fixed id is looked up when the event is read, and kept here once known.
    version_definition_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: The program's own reference of the version (or of the title) the event is about.
    origin: Mapped[str | None] = mapped_column(String(100), nullable=True)
    download_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    block: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ApiDownloadMark(Base):
    """What the event feed last saw of a download that is not finished, to tell what changed.

    Downloads are also changed past the ORM (``update(Download)``), so a job compares instead of a hook listening.
    ``download_id`` is no foreign key: a mark whose download went with its title is simply dropped.

    ⚠️ Comes from ``create_all``, without a migration.
    """

    __tablename__ = "api_download_marks"

    download_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    state: Mapped[str] = mapped_column(String(16))
    #: The problem code as ``/api/v1/problems`` shows it; null without one.
    problem: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ApiPairing(Base):
    """A program asking for a key in one step (N8): it waits until the owner confirms or denies.

    ``pairing_id`` names it in the address, ``secret_hash`` is the SHA-256 of the secret only the program got: without
    it nobody learns how the request stands. Once confirmed, ``key_enc`` holds the new key encrypted until the program
    fetches it, once; then it is emptied. A request lives ``pairing.LIFETIME``.

    ⚠️ Comes from ``create_all``, without a migration.
    """

    __tablename__ = "api_pairings"

    pairing_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    secret_hash: Mapped[str] = mapped_column(String(64))
    #: The name the program gave, and the scopes it asked for.
    app: Mapped[str] = mapped_column(String(100))
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    #: Six characters both sides show, so the owner confirms the right program.
    code: Mapped[str] = mapped_column(String(8))
    #: pending, confirmed, denied or delivered.
    state: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime] = mapped_column()
    key_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: ⚠️ ``enc:…`` until the program fetched it; never in an answer to the interface, never in a log line.
    key_enc: Mapped[str | None] = mapped_column(String(512), nullable=True)


class Webhook(Base):
    """A target the owner gave for the events of ``/api/v1`` (N32).

    ``secret_enc`` signs every delivery (HMAC-SHA256); it is shown once when made. ``cursor`` is the number of the last
    event taken over into deliveries, so none is sent twice and none is skipped. ``types`` empty means every type.

    ⚠️ Comes from ``create_all``, without a migration.
    """

    __tablename__ = "webhooks"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    #: ⚠️ May carry a token of the receiver; the log names its origin only.
    url: Mapped[str] = mapped_column(String(1024))
    secret_enc: Mapped[str] = mapped_column(String(512))
    types: Mapped[list[str]] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    cursor: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class WebhookDelivery(Base):
    """One event for one target, and how sending it went. The last ``webhooks.KEEP`` per target stay.

    ⚠️ Comes from ``create_all``, without a migration.
    """

    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        Index("ix_webhook_deliveries_due", "state", "next_at"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    webhook_id: Mapped[int] = mapped_column(ForeignKey("webhooks.id", ondelete="CASCADE"), index=True)
    #: The event's number; null for a test.
    event_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_type: Mapped[str] = mapped_column(String(48))
    #: The event as ``/api/v1/events`` shows it, as sent.
    body: Mapped[str] = mapped_column(Text)
    #: pending, delivered or failed (given up after the last try).
    state: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_at: Mapped[datetime | None] = mapped_column(nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: timeout, unreachable or http_<status>; null when delivered.
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    done_at: Mapped[datetime | None] = mapped_column(nullable=True)
