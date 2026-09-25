"""The recycle bin ("Der Papierkorb"): one row per file the owner or a program deleted.

The file itself lies in ``.nexcrate-recycle/<day>/`` of its version's folder, where replaced files have always gone; the
daily cleanup deletes day folders after ``recycle_days``, and a row whose file is gone goes with it.

⚠️ The table comes from ``create_all``. A change of a column needs a new migration or the missing-column upkeep.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, utcnow

#: Who deleted: the owner in the interface, or a program through ``/api/v1``.
DELETED_BY = ("owner", "key")


class RecycleEntry(Base):
    """One deleted file with what is needed to put it back.

    The title's facts are copied, so the bin can still name a title that left the library. ``root_folder`` is the
    version's folder as stored on the version; ``relative_path`` the file's place below it before, ``bin_path``
    its place in the bin. ``file_facts`` holds the columns the file had on the version or episode file, written
    back on restore.
    """

    __tablename__ = "recycle_entries"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deleted_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    #: ``owner`` or ``key``; with ``key`` the key's name stands in ``deleted_by_name``.
    deleted_by: Mapped[str] = mapped_column(String(16))
    deleted_by_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    title_id: Mapped[int | None] = mapped_column(
        ForeignKey("titles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    tmdb_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title_name: Mapped[str] = mapped_column(String(1024), default="")
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version_definition_id: Mapped[int | None] = mapped_column(
        ForeignKey("version_definitions.id", ondelete="SET NULL"), nullable=True
    )
    version_label: Mapped[str] = mapped_column(String(200), default="")
    #: For an episode file: its season and the TMDB episode numbers it held.
    season: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episodes: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    root_folder: Mapped[str] = mapped_column(String(4096))
    relative_path: Mapped[str] = mapped_column(String(4096))
    bin_path: Mapped[str] = mapped_column(String(4096))
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    #: The subtitles that went with the file: ``row_path`` (as their row had it), ``path`` and ``bin`` (below
    #: ``root_folder``), ``kind``, ``language``, ``forced``, ``sdh``.
    extras: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    file_facts: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
