"""The mass rename: runs, the titles of a run and every planned move.

A title's moves are stored before the first one happens; a title still ``moving`` after a restart had its database
unchanged, so its moves are taken back. ``before`` and ``after`` hold the paths the title's rows had and have, for
the undo of the last run. Paths in ``rename_steps`` are absolute, as nexcrate sees them in the container.

From ``create_all``; a change of a column needs a numbered migration.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, utcnow


class RenameRun(Base):
    __tablename__ = "rename_runs"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: ``movie``, ``series`` or ``music``.
    kind: Mapped[str] = mapped_column(String(16))
    #: ``running``, ``done``, ``failed``, ``undoing``, ``undone``.
    state: Mapped[str] = mapped_column(String(16), default="running")
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    undone_at: Mapped[datetime | None] = mapped_column(nullable=True)
    #: Counts of the run: titles, files, folders, skipped, failed, and the reasons with their counts.
    counts: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class RenameTitle(Base):
    __tablename__ = "rename_titles"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("rename_runs.id", ondelete="CASCADE"), index=True)
    title_id: Mapped[int | None] = mapped_column(ForeignKey("titles.id", ondelete="SET NULL"), nullable=True)
    #: ``moving``, ``done``, ``rolled_back``, ``failed``, ``undone``.
    state: Mapped[str] = mapped_column(String(16), default="moving")
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    files: Mapped[int] = mapped_column(Integer, default=0)
    folders: Mapped[int] = mapped_column(Integer, default=0)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)


class RenameStep(Base):
    __tablename__ = "rename_steps"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_title_id: Mapped[int] = mapped_column(ForeignKey("rename_titles.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    old_path: Mapped[str] = mapped_column(String(4096))
    new_path: Mapped[str] = mapped_column(String(4096))
