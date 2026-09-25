"""Carrying out a rename plan, taking it back after a stop, and undoing a run (decisions 15 to 22
and answer G4).

One title at a time. Its moves are stored first (``rename_steps``), with the fields before and after; then the files
move with ``os.rename`` inside their root folder, never copied and never over another file; then the database takes
the new fields in one transaction together with the title's state ``done``. When a move fails or the database refuses,
every move of the title goes back. A title still ``moving`` after a restart never reached its database: its moves are
taken back at the start (``recover``).

Afterwards the folders the title left are removed when nothing but system rests lies in them (the rests go to the
recycle folder), and the ``release.nex`` of every version is written again.

Undo takes a title of the last run back while its rows still hold what the run wrote, its files lie where the run
put them, and nothing happened to it since (no history entry but the run's own).

Log lines carry ids, counts and codes, never names or paths.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from ...db import SessionLocal
from ...models import (
    Download,
    HistoryEntry,
    RenameRun,
    RenameStep,
    RenameTitle,
    Title,
    Version,
    VersionDefinition,
    utcnow,
)
from ..downloads import files, store
from . import guard, plan

logger = logging.getLogger("nexcrate.rename")

#: A temporary name for a move whose target is another move's source (a swap) or differs only in case.
SWAP_SUFFIX = ".nexcrate-rename"


class MoveFailed(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Outcome:
    #: ``done``, ``skipped``, ``failed``.
    state: str
    reason: str | None = None
    files: int = 0
    folders: int = 0


def _busy(title_ids: list[int]) -> bool:
    with SessionLocal() as db:
        return (
            db.scalar(
                select(Download.id)
                .where(Download.title_id.in_(title_ids), Download.state.in_(store.UNFINISHED_STATES))
                .limit(1)
            )
            is not None
        )


def _rename_one(old: Path, new: Path) -> None:
    """One move. A name that differs only in case on a disk that does not tell cases apart goes over a temporary name.
    Raises ``OSError``, ``FileExistsError`` when the target is taken."""
    if os.path.lexists(new):
        if not plan.same_entry(old, new):
            raise FileExistsError(new.name)
        temporary = old.with_name(old.name + SWAP_SUFFIX)
        os.rename(old, temporary)
        try:
            os.rename(temporary, new)
        except OSError:
            os.rename(temporary, old)
            raise
        return
    os.rename(old, new)


def _make_parents(path: Path, stop: list[Path], created: list[Path]) -> None:
    missing: list[Path] = []
    current = path
    while not current.exists() and current not in stop and current.parent != current:
        missing.append(current)
        current = current.parent
    for folder in reversed(missing):
        folder.mkdir()
        created.append(folder)


def move_all(moves: list[tuple[Path, Path]], roots: list[Path]) -> tuple[list[tuple[Path, Path]], list[Path]]:
    """Carry out moves in an order where no target is taken: a move waits while its target is another move's source,
    and a circle goes over a temporary name. Returns what was done, in order, and the folders made. Raises
    ``MoveFailed`` after taking everything back."""
    done: list[tuple[Path, Path]] = []
    created: list[Path] = []
    pending = list(moves)
    try:
        while pending:
            sources = {old for old, _new in pending}
            progress = False
            for item in list(pending):
                old, new = item
                if os.path.lexists(new) and new in sources and not plan.same_entry(old, new):
                    continue
                _make_parents(new.parent, roots, created)
                _rename_one(old, new)
                done.append((old, new))
                pending.remove(item)
                progress = True
            if not progress:
                old, new = pending.pop(0)
                temporary = old.with_name(old.name + SWAP_SUFFIX)
                os.rename(old, temporary)
                done.append((old, temporary))
                pending.append((temporary, new))
    except OSError as exc:
        back(done, created)
        raise MoveFailed("target_taken" if isinstance(exc, FileExistsError) else "move_failed") from exc
    return done, created


def back(done: list[tuple[Path, Path]], created: list[Path]) -> int:
    """Take moves back in reverse order, then remove the folders made for them when empty. Returns how many failed."""
    failed = 0
    for old, new in reversed(done):
        try:
            old.parent.mkdir(parents=True, exist_ok=True)
            _rename_one(new, old)
        except OSError:
            failed += 1
    for folder in sorted(created, key=lambda path: -len(path.parts)):
        try:
            folder.rmdir()
        except OSError:
            pass
    if failed:
        logger.warning("%d moves could not be taken back", failed)
    return failed


def tidy(folders: list[Path], root: Path, moment: datetime) -> int:
    """Remove folders a title left when only system rests lie in them; the rests go to the recycle folder."""
    removed = 0
    for folder in folders:
        if not folder.is_dir() or files.is_link(folder) or not files.strictly_inside(folder, root):
            continue
        if not plan.only_system_rests(folder):
            continue
        try:
            for rest in list(folder.iterdir()):
                files.recycle(rest, root, moment)
            folder.rmdir()
            removed += 1
        except OSError, files.FileProblem:
            logger.info("A folder left by a rename stays")
    return removed


def _history(db: object, title_ids: list[int], event: str, detail: str, moment: datetime) -> None:
    for version in db.scalars(  # type: ignore[attr-defined]
        select(Version).where(
            Version.title_id.in_(title_ids), Version.source_id.is_(None), Version.relative_path.is_not(None)
        )
    ):
        definition = db.get(VersionDefinition, version.version_definition_id)  # type: ignore[attr-defined]
        db.add(  # type: ignore[attr-defined]
            HistoryEntry(
                title_id=version.title_id,
                version_id=version.id,
                version_definition_id=version.version_definition_id,
                version_label=definition.label if definition is not None else "",
                event=event,
                at=moment,
                detail=detail,
            )
        )


def companions_after(targets: list[tuple[str, int]]) -> None:
    """Write the ``release.nex`` of renamed versions again. A failure is logged and changes nothing else."""
    from .. import companions, companions_album, companions_series

    for kind, version_id in targets:
        try:
            if kind == "movie":
                companions.write_version(version_id)
            elif kind == "series":
                companions_series.write_version(version_id)
            else:
                companions_album.write_version(version_id)
        except Exception:
            logger.exception("Version %d: release.nex after a rename could not be written", version_id)


def execute(current: plan.TitlePlan, run_id: int) -> Outcome:
    """Carry out one planned title. The caller holds it with ``guard.renaming``."""
    if current.skip is not None:
        return Outcome("skipped", current.skip)
    if not current.moves and current.before == current.after:
        return Outcome("skipped", "unchanged")
    if _busy(current.title_ids):
        return Outcome("skipped", "busy")
    folders_count = len(current.folders)
    files_count = current.file_count()
    with SessionLocal() as db:
        row = RenameTitle(
            run_id=run_id,
            title_id=current.title_id,
            state="moving",
            before=current.before,
            after=current.after,
            files=files_count,
            folders=folders_count,
        )
        db.add(row)
        db.flush()
        for position, move in enumerate(current.moves):
            db.add(RenameStep(run_title_id=row.id, position=position, old_path=str(move.old), new_path=str(move.new)))
        db.commit()
        row_id = row.id
    try:
        done, created = move_all([(move.old, move.new) for move in current.moves], current.roots)
    except MoveFailed as exc:
        _finish_title(row_id, "failed", exc.code)
        logger.info("Rename of title %d failed (%s); its files are back", current.title_id, exc.code)
        return Outcome("failed", exc.code)
    moment = store.now()
    try:
        with SessionLocal() as db:
            plan.write(db, current.after)
            _history(db, current.title_ids, "renamed", f"{files_count} {folders_count}", moment)
            title_row = db.get(RenameTitle, row_id)
            if title_row is not None:
                title_row.state = "done"
                title_row.finished_at = moment
            db.commit()
    except SQLAlchemyError:
        back(done, created)
        _finish_title(row_id, "failed", "database")
        logger.warning("Rename of title %d is taken back, the database refused it", current.title_id)
        return Outcome("failed", "database")
    for root in current.roots:
        tidy([folder for folder in current.old_folders if folder.is_relative_to(root)], root, moment)
    companions_after(current.companions)
    logger.info("Title %d renamed: %d files, %d folders", current.title_id, files_count, folders_count)
    return Outcome("done", None, files_count, folders_count)


def _finish_title(row_id: int, state: str, reason: str | None) -> None:
    with SessionLocal() as db:
        row = db.get(RenameTitle, row_id)
        if row is not None:
            row.state = state
            row.reason = reason
            row.finished_at = utcnow()
            db.commit()


def _steps(db: object, row_id: int) -> list[tuple[Path, Path]]:
    return [
        (Path(step.old_path), Path(step.new_path))
        for step in db.scalars(  # type: ignore[attr-defined]
            select(RenameStep).where(RenameStep.run_title_id == row_id).order_by(RenameStep.position)
        )
    ]


def recover() -> int:
    """At the start: titles still ``moving`` never reached the database, so their files go back. Returns how many."""
    with SessionLocal() as db:
        rows = list(db.scalars(select(RenameTitle).where(RenameTitle.state == "moving")))
        found = [(row.id, _steps(db, row.id)) for row in rows]
        runs = list(db.scalars(select(RenameRun).where(RenameRun.state.in_(("running", "undoing")))))
        for run in runs:
            run.state = "failed" if run.state == "running" else "done"
            run.finished_at = run.finished_at or utcnow()
        db.commit()
    for row_id, steps in found:
        moved = []
        for old, new in steps:
            if os.path.lexists(new) and not os.path.lexists(old):
                moved.append((old, new))
            elif os.path.lexists(old.with_name(old.name + SWAP_SUFFIX)) and not os.path.lexists(old):
                moved.append((old, old.with_name(old.name + SWAP_SUFFIX)))
        back(moved, [])
        _finish_title(row_id, "rolled_back", "stopped")
    if found:
        logger.info("%d titles stopped in the middle of a rename were taken back", len(found))
    return len(found)


# --- Undo ------------------------------------------------------------------------------------------------------- #


def undo_title(row_id: int, run_finished: datetime | None) -> str:
    """Take one title of the last run back. Returns ``undone`` or the reason it stays."""
    with SessionLocal() as db:
        row = db.get(RenameTitle, row_id)
        if row is None or row.state != "done" or row.before is None or row.after is None:
            return "not_done"
        title_ids = [row.title_id] if row.title_id is not None else []
        if row.title_id is not None:
            title = db.get(Title, row.title_id)
            if title is not None and title.kind == "album" and title.artist_id is not None:
                title_ids = list(
                    db.scalars(select(Title.id).where(Title.kind == "album", Title.artist_id == title.artist_id))
                )
        if plan.read(db, row.after) != row.after:
            return "changed"
        since = row.finished_at or run_finished
        if since is not None and title_ids:
            later = db.scalar(
                select(HistoryEntry.id)
                .where(
                    HistoryEntry.title_id.in_(title_ids),
                    HistoryEntry.at > since,
                    HistoryEntry.event.not_in(("renamed", "rename_undone")),
                )
                .limit(1)
            )
            if later is not None:
                return "changed"
        steps = _steps(db, row.id)
        before = row.before
    if any(
        not os.path.lexists(new) or (os.path.lexists(old) and not plan.same_entry(old, new))
        for old, new in steps
        if old != new
    ):
        return "files_moved"
    roots = _roots(title_ids)
    if not roots or not all(any(new.is_relative_to(root) for root in roots) for _old, new in steps):
        return "folder_missing"
    try:
        with guard.renaming(title_ids):
            try:
                done, created = move_all([(new, old) for old, new in reversed(steps)], roots)
            except MoveFailed as exc:
                return exc.code
            moment = store.now()
            try:
                with SessionLocal() as db:
                    plan.write(db, before)
                    _history(db, title_ids, "rename_undone", "", moment)
                    title_row = db.get(RenameTitle, row_id)
                    if title_row is not None:
                        title_row.state = "undone"
                    db.commit()
            except SQLAlchemyError:
                back(done, created)
                return "database"
            left = sorted({new.parent for _old, new in steps}, key=lambda path: -len(path.parts))
            for root in roots:
                tidy([folder for folder in left if folder.is_relative_to(root)], root, moment)
            with SessionLocal() as db:
                versions = [
                    (version.id, version.title_id)
                    for version in db.scalars(select(Version).where(Version.title_id.in_(title_ids)))
                    if version.source_id is None and version.relative_path
                ]
                kinds = {title.id: title.kind for title in db.scalars(select(Title).where(Title.id.in_(title_ids)))}
            companions_after(
                [
                    ({"movie": "movie", "series": "series"}.get(kinds.get(title_id, ""), "album"), version_id)
                    for version_id, title_id in versions
                ]
            )
    except guard.Busy:
        return "busy"
    return "undone"


def _roots(title_ids: list[int]) -> list[Path]:
    """The root folders of the titles' own versions, as far as they are visible."""
    from .. import folders

    found: list[Path] = []
    with SessionLocal() as db:
        for root_folder in db.scalars(
            select(Version.root_folder).where(Version.title_id.in_(title_ids), Version.source_id.is_(None)).distinct()
        ):
            try:
                root, _mount = folders.visible(root_folder)
            except folders.NotVisible:
                continue
            if root not in found:
                found.append(root)
    return found
