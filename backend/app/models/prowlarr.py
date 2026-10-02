"""The table of the Prowlarr connections nexcrate reads its indexers from.

⚠️ This table comes from ``create_all``. A change of a column here needs a new migration or the missing-column upkeep,
never an edit of an existing migration.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, utcnow

#: ``full``: what Prowlarr decides is written again at every sync, as Prowlarr's "Full Sync". ``add_remove``: the
#: fields are taken when an indexer is added and stay editable in nexcrate afterwards ("Add and Remove Only").
SYNC_LEVELS = ("full", "add_remove")


class ProwlarrConnection(Base):
    """One Prowlarr. Each of its indexers becomes an indexer of nexcrate's own, marked with this connection.

    ``url`` is the address with Prowlarr's URL base, for example ``http://prowlarr:9696`` or
    ``https://example.com/prowlarr``. ``api_key`` holds Prowlarr's key encrypted (``enc:...``), never the plain value.
    ``tags`` are Prowlarr's tags as ``{id, label}``: with any, only Prowlarr indexers sharing one are taken, as
    Prowlarr does for its apps. The id counts, the label follows Prowlarr at every sync. They never become nexcrate's
    tags, which mean something else.

    The last sync stays readable on the row: when, its counts and its error code.
    """

    __tablename__ = "prowlarr_connections"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    url: Mapped[str] = mapped_column(String(1024))
    api_key: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sync_level: Mapped[str] = mapped_column(String(16), default="full")
    tags: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    #: Prowlarr's version as read at the last sync, for example ``2.6.5.5623``.
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(nullable=True)
    #: The code of the last failed sync; null after a sync that went through.
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: What the last sync that went through did, by the names of ``services.prowlarr.sync.COUNTS``.
    last_counts: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
