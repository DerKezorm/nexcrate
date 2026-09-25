"""Restoring movies from their ``release.nex`` (L5, "Restoring").

A job of kind ``restore``, one transaction per 100 folders, progress per folder. Per folder:

* **Title:** the existing one with that TMDB number; else a new title with the file's name and year, which the TMDB fill
  takes (``tmdb._fill_due`` learns the history event ``restored``).
* **Version:** the definition by root, the label as the check (L1). The title's version there without a file gets the
  file; with a file, or fed by a source: ``conflict``; no version: a new one, ``added_by`` owner. A label that fits no
  definition of the root is a conflict; under a root that belongs to no definition the label decides, and a label
  without a definition is counted as ``no_definition`` ("create the version" in the interface).
* **Fields from the entry:** the folder as on disk and the named file, which must exist (when it is missing but exactly
  one video of the same size is there, that one), the size from disk, quality and where it came from, release title,
  group, languages, monitored, minimum availability, ``file_ref`` ``disk:<row id>``. A size different from the entry's:
  media data is read and quality and languages follow L7.
* **Subtitles:** a row in ``extra_files`` for each listed subtitle file that exists.
* **History:** ``imported`` at the entry's time (detail the quality), ``restored`` now (detail ``release.nex``). Then
  the judgement, and after the commit the entry rewritten with this installation's id.
* ⚠️ The file is input from outside: only the named files inside the folder the file lies in are looked for.
"""

from __future__ import annotations

import logging
import os
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ...db import SessionLocal
from ...models import DiskFolder, DiskRoot, ExtraFile, HistoryEntry, Title, Version, VersionDefinition, utcnow
from .. import companions, judging, media, schreibweisen
from ..downloads import files, store
from ..subtitles import records as subtitle_records
from . import roots as root_service
from .jobs import DiskJob

logger = logging.getLogger("nexcrate.disk")

CHUNK = 100
CONFLICT_EXAMPLES = 20

RESTORED = "restored"
CONFLICT = "conflict"
NO_DEFINITION = "no_definition"
SKIPPED = "skipped"
FAILED = "failed"


@dataclass
class Outcome:
    state: str
    #: The versions written, for the companion files after the commit.
    version_ids: list[int]
    reason: str | None = None


def _time(value: Any, fallback: datetime) -> datetime:
    """A time from the file as aware UTC; a time without a zone counts as UTC, an unreadable one as the fallback."""
    if not isinstance(value, str):
        return fallback
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return fallback
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def write_companion(version_id: int) -> str:
    """The version's ``release.nex`` after a commit, a file of another installation taken over first (decision 4)."""
    from .assign import write_companion as write

    return write(version_id)


def _regular_size(path: Path) -> int | None:
    try:
        info = os.lstat(path)
    except OSError:
        return None
    return int(info.st_size) if stat.S_ISREG(info.st_mode) and not files.is_link(path) else None


def _find_file(folder: Path, entry: dict[str, Any]) -> tuple[str, int] | None:
    """The named file in this folder, or exactly one video of the entry's size; None when neither is there."""
    name = str(entry["file"])
    size = _regular_size(folder / name)
    if size is not None:
        return name, size
    wanted = int(entry.get("size_bytes") or 0)
    if wanted <= 0:
        return None
    same_size = [
        (path.name, found) for path, found in files.scan(folder).videos if found == wanted and path.parent == folder
    ]
    return same_size[0] if len(same_size) == 1 else None


def _title_for(db: OrmSession, movie: dict[str, Any], row: DiskFolder, moment: datetime) -> Title:
    tmdb_id = int(movie["tmdb_id"])
    title = db.scalar(select(Title).where(Title.kind == "movie", Title.tmdb_id == tmdb_id))
    if title is not None:
        return title
    name = movie.get("title") or row.parsed_title or f"TMDB {tmdb_id}"
    title = Title(
        kind="movie",
        tmdb_id=tmdb_id,
        imdb_id=movie.get("imdb_id"),
        title=schreibweisen.nfc(str(name))[:1024],
        original_title=(movie.get("original_title") or None) and schreibweisen.nfc(str(movie["original_title"]))[:1024],
        year=movie.get("year") if movie.get("year") is not None else row.parsed_year,
        sort_key=schreibweisen.sort_key(str(name))[:1024],
        search_keys=schreibweisen.search_text([str(name), movie.get("original_title")]),
        added=moment,
        updated_at=moment,
    )
    db.add(title)
    db.flush()
    return title


def _definition_for(
    label: str, among: list[VersionDefinition], every: dict[str, VersionDefinition]
) -> tuple[VersionDefinition | None, str | None]:
    """The definition an entry's label names: among the root's definitions, else any; the outcome when none fits."""
    if among:
        found = next((definition for definition in among if definition.label == label), None)
        return found, None if found is not None else CONFLICT
    found = every.get(label)
    return found, None if found is not None else NO_DEFINITION


def _restore_entry(
    db: OrmSession,
    row: DiskFolder,
    root_path: Path,
    folder: Path,
    title: Title,
    definition: VersionDefinition,
    entry: dict[str, Any],
    moment: datetime,
) -> tuple[Version | None, str | None]:
    version = db.scalar(
        select(Version).where(Version.title_id == title.id, Version.version_definition_id == definition.id)
    )
    if version is not None and (version.has_file or version.source_id is not None):
        return None, "version_taken"
    found = _find_file(folder, entry)
    if found is None:
        return None, "file_missing"
    file_name, size = found
    if version is None:
        version = Version(
            title_id=title.id,
            version_definition_id=definition.id,
            source_id=None,
            added_by="owner",
            created_at=moment,
        )
        db.add(version)
        db.flush()
    quality, quality_from = str(entry.get("quality") or "Unknown"), str(entry.get("quality_from") or "name")
    languages = list(entry.get("languages") or [])
    media_info: dict[str, Any] | None = None
    if size != int(entry.get("size_bytes") or -1):
        # Another file than the one written: its media data decides quality and languages (L7).
        read = media.read(folder / file_name)
        decision = media.quality_of(file_name, folder.name, read.media)
        quality, quality_from = decision.quality, decision.quality_from
        languages = media.languages_of(read.media, Path(file_name).stem, title.original_language)
        media_info = read.media
    version.monitored = bool(entry.get("monitored", True))
    version.has_file = True
    version.file_ref = f"disk:{row.id}"[:64]
    version.root_folder = str(root_path)
    version.relative_path = f"{row.relative_path}/{file_name}"[:2048]
    version.size = size
    version.quality = quality
    version.quality_from = quality_from
    version.release_title = (entry.get("release_title") or None) and str(entry["release_title"])[:1024]
    version.release_group = (entry.get("release_group") or None) and str(entry["release_group"])[:200]
    version.languages = languages
    version.minimum_availability = entry.get("minimum_availability")
    version.media_info = media_info
    version.media_read_at = moment if media_info is not None else None
    version.source_movie_path = None
    version.upgrade_to = None
    version.progress = None
    version.problem_code = None
    version.updated_at = moment
    db.flush()
    for subtitle in entry.get("subtitles") or []:
        if not isinstance(subtitle, dict):
            continue
        name = str(subtitle.get("file") or "")
        if not name or "/" in name or "\\" in name or _regular_size(folder / name) is None:
            continue
        db.add(
            ExtraFile(
                version_id=version.id,
                download_id=None,
                relative_path=f"{row.relative_path}/{name}"[:4096],
                kind=subtitle_records.KIND,
                language=subtitle.get("language"),
                forced=bool(subtitle.get("forced")),
                sdh=bool(subtitle.get("sdh")),
                created_at=moment,
            )
        )
    came_from = entry.get("came_from") if isinstance(entry.get("came_from"), dict) else {}
    imported_at = _time(came_from.get("imported_at") or came_from.get("found_at"), moment)
    db.add(
        HistoryEntry(
            title_id=title.id,
            version_id=version.id,
            version_definition_id=definition.id,
            version_label=definition.label,
            event="imported",
            at=imported_at,
            detail=quality,
        )
    )
    db.add(
        HistoryEntry(
            title_id=title.id,
            version_id=version.id,
            version_definition_id=definition.id,
            version_label=definition.label,
            event="restored",
            at=moment,
            detail=companions.FILE_NAME,
        )
    )
    version.cutoff_not_met = judging.judge(db, version)
    store.follow_version(db, title.id, definition.id, moment)
    return version, None


def restore_row(db: OrmSession, row: DiskFolder, moment: datetime) -> Outcome:
    """Restore one folder inside the caller's transaction."""
    root = db.get(DiskRoot, row.root_id)
    root_path = root_service.visible_path(root) if root is not None else None
    if root is None or root_path is None or row.ignored or row.kind not in ("folder", "group"):
        return Outcome(SKIPPED, [], "not_restorable")
    parts = [part for part in row.relative_path.replace("\\", "/").split("/") if part]
    if not parts or any(part in (".", "..") for part in parts):
        return Outcome(SKIPPED, [], "not_restorable")
    candidate = root_path.joinpath(*parts)
    folder = files.resolved(candidate)
    inside = folder is not None and files.strictly_inside(folder, root_path)
    if folder is None or files.is_link(candidate) or not folder.is_dir() or not inside:
        return Outcome(CONFLICT, [], "folder_missing")
    # Read again from disk: the row's copy may be old, and the file decides.
    read = companions.read(folder, companions.installation_id(db))
    if not read.readable or read.movie is None or not read.entries:
        return Outcome(CONFLICT, [], "companion_unreadable")
    among = root_service.definitions_of(db, root)
    every = {
        definition.label: definition
        for definition in db.scalars(select(VersionDefinition).where(VersionDefinition.kind == "movie"))
    }
    title = _title_for(db, read.movie, row, moment)
    written: list[int] = []
    problems: list[str] = []
    for entry in read.entries:
        definition, problem = _definition_for(str(entry["version"]), among, every)
        if definition is None:
            problems.append(problem or CONFLICT)
            continue
        version, reason = _restore_entry(db, row, root_path, folder, title, definition, entry, moment)
        if version is None:
            problems.append(reason or CONFLICT)
            continue
        written.append(version.id)
    if written:
        row.state = "library"
        row.title_id = title.id
        row.version_id = written[0]
        row.tmdb_id = title.tmdb_id
        row.proposals = None
        return Outcome(RESTORED, written)
    if NO_DEFINITION in problems and all(problem == NO_DEFINITION for problem in problems):
        return Outcome(NO_DEFINITION, [], NO_DEFINITION)
    return Outcome(CONFLICT, [], problems[0] if problems else CONFLICT)


def restore_work(row_ids: list[int] | None) -> Any:
    def work(job: DiskJob) -> dict[str, Any]:
        return run_restore(job, row_ids)

    return work


def _chosen(row_ids: list[int] | None) -> list[int]:
    with SessionLocal() as db:
        # Series folders are restored by ``series_assign`` (P2), album folders by
        # ``album_assign``.
        statement = (
            select(DiskFolder.id)
            .join(DiskRoot, DiskRoot.id == DiskFolder.root_id)
            .where(DiskFolder.ignored.is_(False), DiskRoot.kind.not_in(("series", "album")))
            .order_by(DiskFolder.id)
        )
        if row_ids is None:
            statement = statement.where(DiskFolder.state == "restorable")
        else:
            statement = statement.where(DiskFolder.id.in_(row_ids))
        return list(db.scalars(statement))


class _RowFailed(Exception):
    def __init__(self, row_id: int) -> None:
        super().__init__(row_id)
        self.row_id = row_id


def _process(chunk: list[int], moment: datetime) -> tuple[list[tuple[int, Outcome]], list[int]]:
    """One transaction over a chunk. Raises ``_RowFailed`` with nothing committed when a row raises."""
    outcomes: list[tuple[int, Outcome]] = []
    written: list[int] = []
    with SessionLocal() as db:
        for row_id in chunk:
            row = db.get(DiskFolder, row_id)
            if row is None:
                outcomes.append((row_id, Outcome(SKIPPED, [], "gone")))
                continue
            try:
                outcome = restore_row(db, row, moment)
            except Exception as exc:
                db.rollback()
                logger.exception("Folder %d could not be restored", row_id)
                raise _RowFailed(row_id) from exc
            outcomes.append((row_id, outcome))
            written.extend(outcome.version_ids)
        db.commit()
    return outcomes, written


def run_restore(job: DiskJob, row_ids: list[int] | None) -> dict[str, Any]:
    chosen = _chosen(row_ids)
    counts = {RESTORED: 0, CONFLICT: 0, NO_DEFINITION: 0, SKIPPED: 0, FAILED: 0}
    conflicts: list[int] = []
    reasons: dict[str, int] = {}
    job.set(phase="restoring", done=0, total=len(chosen))
    done = 0
    for start in range(0, len(chosen), CHUNK):
        chunk = chosen[start : start + CHUNK]
        moment = utcnow()
        try:
            outcomes, written = _process(chunk, moment)
        except _RowFailed:
            # A row that raises undoes its chunk: the chunk again, one row per transaction, the failing one counted.
            outcomes, written = [], []
            for row_id in chunk:
                try:
                    found, ids = _process([row_id], moment)
                except _RowFailed:
                    found, ids = [(row_id, Outcome(FAILED, [], FAILED))], []
                outcomes.extend(found)
                written.extend(ids)
        for row_id, outcome in outcomes:
            counts[outcome.state] = counts.get(outcome.state, 0) + 1
            if outcome.reason and outcome.state != RESTORED:
                reasons[outcome.reason] = reasons.get(outcome.reason, 0) + 1
            if outcome.state in (CONFLICT, NO_DEFINITION) and len(conflicts) < CONFLICT_EXAMPLES:
                conflicts.append(row_id)
        done += len(chunk)
        job.progress(done)
        # After the commit: the entries rewritten with this installation's id.
        for version_id in written:
            write_companion(version_id)
    logger.info(
        "Restore: %d folders, %s",
        len(chosen),
        ", ".join(f"{key}={value}" for key, value in sorted(counts.items())),
    )
    return {**counts, "counts": dict(counts), "conflicts": conflicts, "reasons": reasons, "folders": len(chosen)}
