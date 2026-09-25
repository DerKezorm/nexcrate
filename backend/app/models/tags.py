"""Tags: labels on movies, series and artists, as in Radarr, Sonarr and Lidarr.

A tag is a name, stored in lower case as the apps store it. A link remembers the Radarr, Sonarr or Lidarr connection it
came from (``source_id``): an import sets that connection's tags anew and never touches the owner's (``source_id``
null). A takeover makes them the owner's.

Since T2 indexers and download clients carry tags too; since T3 rules give tags by themselves (``AutoTag``).

⚠️ The tables come from ``create_all``, without a migration.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, utcnow

#: The longest name a tag may have.
LABEL_MAX_LENGTH = 64


class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(LABEL_MAX_LENGTH), unique=True)


class TitleTag(Base):
    """A tag on a movie or series."""

    __tablename__ = "title_tags"
    __table_args__ = (Index("ix_title_tags_tag", "tag_id"),)

    title_id: Mapped[int] = mapped_column(ForeignKey("titles.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)
    #: The connection the tag came from; null for the owner's.
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), nullable=True, index=True
    )


class ArtistTag(Base):
    """A tag on an artist; its albums show it (Lidarr binds at the artist)."""

    __tablename__ = "artist_tags"
    __table_args__ = (Index("ix_artist_tags_tag", "tag_id"),)

    artist_id: Mapped[int] = mapped_column(ForeignKey("artists.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), nullable=True, index=True
    )


class IndexerTag(Base):
    """A tag on an indexer: the indexer is asked only for titles sharing one of its tags."""

    __tablename__ = "indexer_tags"

    indexer_id: Mapped[int] = mapped_column(ForeignKey("indexers.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)


class DownloadClientTag(Base):
    """A tag on a download client: with one that fits, only those clients load a title."""

    __tablename__ = "download_client_tags"

    client_id: Mapped[int] = mapped_column(ForeignKey("download_clients.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)


class AutoTag(Base):
    """A rule that gives tags by itself, as Auto Tagging in Radarr, Sonarr and Lidarr: for one
    kind (``movie``, ``series``, ``album`` for artists), with its tags by id and its conditions as stored by
    ``services.auto_tags``. A deleted tag drops out of a rule when it is read."""

    __tablename__ = "auto_tags"
    __table_args__ = (UniqueConstraint("kind", "name", name="uq_auto_tags_kind_name"), {"sqlite_autoincrement": True})

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(100))
    tag_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    #: When the rule no longer fits, its tags go from the title, even one set by hand, as in the apps.
    remove_automatically: Mapped[bool] = mapped_column(Boolean, default=False)
    conditions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
