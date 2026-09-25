"""The core tables: runtime settings, the one account, server-side sessions, the TMDB cache."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, utcnow

#: The one account always has this id.
ACCOUNT_ID = 1


class Setting(Base):
    """A key and a value, for runtime settings such as the log mode."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class Account(Base):
    """The one account.

    ⚠️ The fixed primary key is what makes a second account impossible, not a check
    in the code. Two setups arriving at the same moment both pass any "is there an
    account yet?" check; only one of their inserts can take id 1. The CHECK
    constraint closes the other door: code that forgets the id gets 2, and that
    is refused as well.
    """

    __tablename__ = "account"
    __table_args__ = (CheckConstraint(f"id = {ACCOUNT_ID}", name="ck_account_single"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    username: Mapped[str] = mapped_column(String(64))
    password_hash: Mapped[str] = mapped_column(String(128))
    language: Mapped[str] = mapped_column(String(35))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Session(Base):
    """A login. Only the SHA-256 hash of the token is stored, never the token."""

    __tablename__ = "sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_used_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(index=True)


class TmdbCacheEntry(Base):
    """One cached TMDB answer: ``{"data": ...}`` until ``expires_at``.

    ⚠️ TMDB's terms allow no cache older than six months; the longest time to live here is 30 days,
    and removing the token empties the table.
    """

    __tablename__ = "tmdb_cache"

    #: ``<kind>:<sha256 of the request>``, never the plain search text.
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
