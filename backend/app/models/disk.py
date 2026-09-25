"""The disk scan: scanned folders ("roots") and what lies below them.

A root is the default folder of a movie version definition, a root folder of a version nexcrate owns, or a folder the
owner added for scanning. A row in ``disk_folders`` is one candidate below a root: a movie folder, a group folder, a
video without a folder or a disc folder, with the state the matching gave it. Names are stored as on disk and compared
in NFC. ⚠️ Ids of folders are never reused: the interface links assign dialogs to them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, utcnow

#: Kinds of a row.
DISK_KINDS = ("folder", "group", "file", "disc")
#: States of a row, decided by the scan's matching order; ``ignored`` is a mark of the owner kept on the row.
DISK_STATES = (
    "library",
    "moved",
    "radarr",
    "restorable",
    "proposal",
    "unknown",
    "conflict",
    "file",
    "disc",
    "no_video",
    "unreadable",
)


class DiskRoot(Base):
    """A folder the scan walks."""

    __tablename__ = "disk_roots"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: The resolved path as the owner sees it in the container.
    path: Mapped[str] = mapped_column(String(1024), unique=True)
    #: True for a root the owner added on the page; roots from definitions and versions come and go with those.
    added_by_owner: Mapped[bool] = mapped_column(Boolean, default=False)
    last_scan_at: Mapped[datetime | None] = mapped_column(nullable=True)
    #: Counts per state of the last scan, and ``missing_files``.
    last_counts: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    #: ``movie`` or ``series`` (migration 13): below a series root the direct subfolders are series folders.
    kind: Mapped[str] = mapped_column(String(16), default="movie", server_default="movie")


class DiskFolder(Base):
    """One candidate below a root."""

    __tablename__ = "disk_folders"
    __table_args__ = (
        UniqueConstraint("root_id", "relative_path", name="uq_disk_folders_root_path"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    root_id: Mapped[int] = mapped_column(ForeignKey("disk_roots.id", ondelete="CASCADE"), index=True)
    #: The folder below the root as on disk, with ``/`` between a group folder and its movie folder.
    relative_path: Mapped[str] = mapped_column(String(2048))
    kind: Mapped[str] = mapped_column(String(16), default="folder")
    state: Mapped[str] = mapped_column(String(16), default="unknown", index=True)
    ignored: Mapped[bool] = mapped_column(Boolean, default=False)
    #: The videos of the folder, largest first, at most 20: ``name``, ``size_bytes``, ``modified_at``, ``name_quality``.
    videos: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    parsed_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    parsed_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: The read outcome of the folder's ``release.nex`` with numbers, name, year and entries without subtitles; null
    #: when there is none.
    companion: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    #: TMDB and IMDb numbers found in the name or a ``.nfo``, each with where it came from.
    numbers: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    proposals: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    #: The best known TMDB number: from ``release.nex``, a number in the name or a ``.nfo``, or an unambiguous proposal.
    tmdb_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    title_id: Mapped[int | None] = mapped_column(ForeignKey("titles.id", ondelete="SET NULL"), nullable=True)
    version_id: Mapped[int | None] = mapped_column(ForeignKey("versions.id", ondelete="SET NULL"), nullable=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"), nullable=True)
    seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    #: A series folder (migration 13): ``videos`` counted, ``seasons`` folder names, ``companion_tmdb_id``.
    series: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
