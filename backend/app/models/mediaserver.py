"""The table of the media servers nexcrate tells about new files.

⚠️ This table comes from ``create_all``. A change of a column here needs a new migration or the missing-column upkeep,
never an edit of an existing migration.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, utcnow

SERVER_KINDS = ("plex", "jellyfin", "emby")
#: What the last notification was: the folder alone, a whole library, or it failed.
NOTIFY_RESULTS = ("folder", "library", "failed")


class MediaServer(Base):
    """A Plex, a Jellyfin or an Emby.

    ``token`` holds Plex's token or the API key of Jellyfin or Emby, encrypted (``enc:...``), never the plain value.
    ``libraries`` is what the server listed at the last check, each with the owner's switch:
    ``{id, name, kind, locations, refresh}``; ``kind`` is movie, series, music or null. ``path_mappings`` is a list of
    ``{local, remote}`` pairs: the same folder as nexcrate sees it and as the server sees it.

    The last notification stays readable on the row (time, result, error code), for the interface and later for a
    reader outside.
    """

    __tablename__ = "media_servers"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(16))
    url: Mapped[str] = mapped_column(String(1024))
    token: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    libraries: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    path_mappings: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    #: What the server calls itself, and its version, as read at the last check.
    server_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The code of the last failed check or notification, or the state ``mediaserver_path_unmatched``; null when fine.
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_notify_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_notify_result: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
