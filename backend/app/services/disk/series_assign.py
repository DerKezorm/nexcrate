"""Assigning and restoring series folders (P2, decisions 27 to 29).

**Assigning** a series folder records, in one transaction, the title (existing, or created with TMDB's data), the
title's version of the chosen series definition (existing without a series folder, or new, added by the owner) with the
rule the owner chose, this folder as its series folder, ``own_since`` now and ``files_read_at`` empty; the row becomes
``library``. After the commit the folder is read (``folder_read``): release.nex first, then the names, media data,
unclear files. Until then the version wants nothing.

**Restoring** is assigning without the owner: the TMDB number of the folder's ``release.nex``, the definition its
entries name (else the root's only series definition), the rule "all". The read links the files by their TMDB episode
ids.

Refused: a row that is ``library`` or ``sonarr`` (``folder_known``), a definition of another kind
(``version_kind_mismatch``), a version a source feeds (``version_owned_by_source``) and a version that has a series
folder already (``version_has_files``, with its ``location``).
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ...db import SessionLocal
from ...meldungen import error
from ...models import DiskFolder, DiskRoot, Title, Version, VersionDefinition, utcnow
from .. import companions, companions_series, folders, library, tmdb
from ..downloads import files
from ..series import folder_read, tmdb_series, watching
from ..series import store as series_store
from . import roots as root_service
from .jobs import DiskJob

logger = logging.getLogger("nexcrate.disk")


def is_series_row(db: OrmSession, row: DiskFolder) -> bool:
    root = db.get(DiskRoot, row.root_id)
    return root is not None and root.kind == "series"


def check(db: OrmSession, row: DiskFolder, tmdb_id: int, definition_id: int) -> None:
    """Raises the refusals of the module's docstring as HTTP errors."""
    if row.state in ("library", "sonarr"):
        raise error("folder_known", "This folder belongs to a version already.", 409)
    definition = db.get(VersionDefinition, definition_id)
    if definition is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    if definition.kind != "series":
        raise error("version_kind_mismatch", "This version is for another kind of title.", 422)
    title = db.scalar(select(Title).where(Title.kind == "series", Title.tmdb_id == tmdb_id))
    if title is None:
        return
    version = db.scalar(
        select(Version).where(Version.title_id == title.id, Version.version_definition_id == definition_id)
    )
    if version is None:
        return
    if version.source_id is not None:
        raise error(
            "version_owned_by_source",
            "A Sonarr connection feeds this version. Take the connection over first.",
            409,
        )
    if version.relative_path:
        raise error(
            "version_has_files",
            "This version has a series folder already.",
            409,
            location=str(Path(version.root_folder or "", version.relative_path)),
        )


def _used_by_another(db: OrmSession, row: DiskFolder, root: DiskRoot, version_id: int | None) -> bool:
    """Whether another version of nexcrate's own already has this folder as its series folder."""
    wanted = folder_read._key(row.relative_path)
    root_key = folder_read._key(root.path.rstrip("/\\"))
    for other in db.scalars(select(Version).where(Version.source_id.is_(None), Version.relative_path.is_not(None))):
        if other.id == version_id or not other.root_folder:
            continue
        if folder_read._key(other.relative_path or "") == wanted and (
            folder_read._key(other.root_folder.rstrip("/\\")) == root_key
        ):
            return True
    return False


def record(
    db: OrmSession,
    row: DiskFolder,
    tmdb_id: int,
    definition_id: int,
    rule: str | None,
    from_season: int | None,
    data: tmdb_series.SeriesData | None,
) -> tuple[int, int]:
    """Stage the assignment. Returns the title id and the version id; the caller commits and enqueues the read."""
    root = db.get(DiskRoot, row.root_id)
    if root is None:
        raise error("folder_changed", "The folder is not as it was scanned. Scan it again.", 409)
    check(db, row, tmdb_id, definition_id)
    existing = db.scalar(
        select(Version)
        .join(Title, Title.id == Version.title_id)
        .where(Title.kind == "series", Title.tmdb_id == tmdb_id, Version.version_definition_id == definition_id)
    )
    if _used_by_another(db, row, root, existing.id if existing is not None else None):
        raise error("folder_known", "Another version has this folder as its series folder already.", 409)
    definition = db.get(VersionDefinition, definition_id)
    assert definition is not None
    moment, on = utcnow(), watching.today()
    title = db.scalar(select(Title).where(Title.kind == "series", Title.tmdb_id == tmdb_id))
    version: Version | None = None
    if title is None:
        if data is None:
            raise error("folder_changed", "The folder is not as it was scanned. Scan it again.", 409)
        title = series_store.new_title(data, moment)
        db.add(title)
        db.flush()
        version = library.add_owner_version(db, title, definition, moment)
        version.watch_rule = rule or "all"
        version.watch_from_season = from_season if rule == "from_season" else None
        db.flush()
        series_store.apply_series(db, title, data, moment, on)
    else:
        version = db.scalar(
            select(Version).where(Version.title_id == title.id, Version.version_definition_id == definition_id)
        )
        if version is None:
            version = library.add_owner_version(db, title, definition, moment)
            version.watch_rule = rule or "all"
            version.watch_from_season = from_season if rule == "from_season" else None
            db.flush()
            watching.sync_rows(db, version, on)
        elif rule is not None and version.watch_rule != rule:
            # ⚠️ Only when the owner chose a rule in the dialog: applying one overwrites every switch he set.
            watching.apply_rule(db, version, rule, from_season, on, write=True)
    version.root_folder = root.path
    version.relative_path = row.relative_path
    version.own_since = moment
    version.files_read_at = None
    version.updated_at = moment
    row.state, row.title_id, row.version_id, row.tmdb_id = "library", title.id, version.id, tmdb_id
    watching.recount(db, version, on)
    from ..automatic import clock as automatic_clock
    from ..automatic import planning as automatic_planning

    db.flush()
    automatic_planning.replan(db, [title.id], automatic_clock.now())
    return title.id, version.id


def title_known(tmdb_id: int) -> bool:
    with SessionLocal() as db:
        return db.scalar(select(Title.id).where(Title.kind == "series", Title.tmdb_id == tmdb_id)) is not None


async def fetch(tmdb_id: int) -> tmdb_series.SeriesData:
    token = await asyncio.to_thread(tmdb.require_token)
    locale = await asyncio.to_thread(tmdb.account_locale)
    try:
        return await tmdb_series.fetch_series(token, tmdb_id, locale)
    except tmdb.TmdbError as exc:
        logger.info("Assigning a series folder stopped at TMDB: %s", exc.code)
        raise exc.http() from exc
    finally:
        await tmdb.close()


#: Set for an assignment whose folder's episodes stay as they are once read (``keep_as_is``, 24.09.2026).
KEEP_AS_IS: contextvars.ContextVar[bool] = contextvars.ContextVar("keep_as_is", default=False)


def assign(
    folder_id: int,
    tmdb_id: int,
    definition_id: int,
    rule: str | None,
    from_season: int | None,
    data: tmdb_series.SeriesData | None,
) -> int:
    with SessionLocal() as db:
        row = db.get(DiskFolder, folder_id)
        if row is None:
            raise error("not_found", "This does not exist, or not any more.", 404)
        title_id, version_id = record(db, row, tmdb_id, definition_id, rule, from_season, data)
        db.commit()
    folder_read.enqueue([version_id])
    if KEEP_AS_IS.get():
        folder_read.keep_as_is([version_id])
    logger.info("Series folder %d assigned to title %d", folder_id, title_id)
    return title_id


# --- Restoring and assigning many ----------------------------------------------------------------------------------- #


def read_companion(db: OrmSession, row: DiskFolder, root: DiskRoot) -> tuple[int | None, list[str]]:
    """The series and the labels the folder's ``release.nex`` names, read from disk again.

    ⚠️ Never from the scanned row: it may be old, and the file decides (as the movie restore does).
    """
    installation = companions.installation_id(db)
    folder = _folder_of(root, row)
    if folder is None:
        return None, []
    seasons = [name for name in (row.series or {}).get("seasons") or [] if isinstance(name, str)]
    tmdb_id: int | None = None
    labels: list[str] = []
    for name in seasons:
        if "/" in name or "\\" in name or name in (".", ".."):
            continue
        read = companions_series.read(folder / name, installation)
        if read["outcome"] not in (companions.OURS, companions.OTHER_INSTALLATION):
            continue
        series = read.get("series") if isinstance(read.get("series"), dict) else {}
        found = series.get("tmdb_id")
        if isinstance(found, int) and not isinstance(found, bool) and found > 0 and tmdb_id is None:
            tmdb_id = found
        for entry in read.get("entries") or []:
            label = entry.get("version")
            if isinstance(label, str) and label not in labels:
                labels.append(label)
    return tmdb_id, labels


def _folder_of(root: DiskRoot, row: DiskFolder) -> Path | None:
    """The row's folder, resolved, strictly inside a root nexcrate sees; never a link."""
    if "/" in row.relative_path or "\\" in row.relative_path or row.relative_path in (".", ".."):
        return None
    try:
        base, _mount = folders.visible(root.path)
    except folders.NotVisible:
        return None
    candidate = base / row.relative_path
    found = files.resolved(candidate)
    if found is None or files.is_link(candidate) or not found.is_dir() or not files.strictly_inside(found, base):
        return None
    return found


def _definitions_for(db: OrmSession, root: DiskRoot, labels: list[str]) -> tuple[list[int], list[str]]:
    """The definitions the labels name, among the root's series definitions; the labels that fit none.

    Without a label at all: the root's only series definition (the folder came from this installation and the entry
    labels were unreadable).
    """
    among = [item for item in root_service.definitions_of(db, root) if item.kind == "series"]
    if not labels:
        return ([among[0].id] if len(among) == 1 else []), []
    found: list[int] = []
    unknown: list[str] = []
    for label in labels:
        named = next((item for item in among if item.label == label), None)
        if named is None:
            named = db.scalar(
                select(VersionDefinition).where(VersionDefinition.kind == "series", VersionDefinition.label == label)
            )
        if named is not None and named.id not in found:
            found.append(named.id)
        elif named is None:
            unknown.append(label)
    return found, unknown


def _one(job_kind: str, row_id: int) -> tuple[str, str | None]:
    """One row: its outcome and, when it is not done, why."""
    with SessionLocal() as db:
        row = db.get(DiskFolder, row_id)
        root = db.get(DiskRoot, row.root_id) if row is not None else None
        if row is None or root is None or row.ignored:
            return "skipped", "gone"
        definition_ids: list[int] = []
        if job_kind == "restore":
            if row.state != "restorable":
                return "skipped", "not_restorable"
            tmdb_id, labels = read_companion(db, row, root)
            if tmdb_id is None:
                return "conflict", "companion_unreadable"
            definition_ids, unknown = _definitions_for(db, root, labels)
            if not definition_ids:
                return "no_definition", unknown[0] if unknown else "no_definition"
        else:
            unambiguous = [item for item in row.proposals or [] if isinstance(item, dict) and item.get("unambiguous")]
            if row.state != "proposal" or len(unambiguous) != 1:
                return "skipped", "not_unambiguous"
            tmdb_id = unambiguous[0].get("tmdb_id")
            among = [item for item in root_service.definitions_of(db, root) if item.kind == "series"]
            if len(among) != 1:
                return "skipped", "no_single_definition"
            definition_ids = [among[0].id]
        if not isinstance(tmdb_id, int):
            return "skipped", "no_number"
    if not title_known(tmdb_id):
        try:
            data = asyncio.run(fetch(tmdb_id))
        except HTTPException as exc:
            code = exc.detail.get("code") if isinstance(exc.detail, dict) else None
            return "skipped", str(code or "tmdb_failed")
    else:
        data = None
    # Every label of the file becomes a version of its definition; the rule of a version that exists stays (S6, 27).
    for definition_id in definition_ids:
        assign(row_id, tmdb_id, definition_id, None, None, data)
        data = None
    return ("restored" if job_kind == "restore" else "assigned"), None


def run_many(job: DiskJob, kind: str, row_ids: list[int]) -> tuple[dict[str, int], dict[str, int]]:
    """The counts per outcome and the reasons of what was not done."""
    counts: dict[str, int] = {}
    reasons: dict[str, int] = {}
    for index, row_id in enumerate(row_ids, start=1):
        reason: str | None = None
        try:
            outcome, reason = _one(kind, row_id)
        except HTTPException as exc:
            code = exc.detail.get("code") if isinstance(exc.detail, dict) else None
            outcome = (
                "conflict" if code in ("folder_known", "version_has_files", "version_owned_by_source") else "skipped"
            )
            reason = str(code or "refused")
        except Exception:
            logger.exception("Series folder %d could not be handled", row_id)
            outcome, reason = "failed", "internal_error"
        counts[outcome] = counts.get(outcome, 0) + 1
        if reason and outcome not in ("restored", "assigned"):
            reasons[reason] = reasons.get(reason, 0) + 1
        job.progress(index)
    logger.info("Series folders (%s): %s", kind, ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))
    return counts, reasons


def series_rows(row_ids: list[int] | None, state: str) -> list[int]:
    """The rows below series roots: the given ones, or every row in ``state`` that is not ignored."""
    with SessionLocal() as db:
        statement = (
            select(DiskFolder.id)
            .join(DiskRoot, DiskRoot.id == DiskFolder.root_id)
            .where(DiskRoot.kind == "series", DiskFolder.ignored.is_(False))
            .order_by(DiskFolder.id)
        )
        statement = (
            statement.where(DiskFolder.state == state)
            if row_ids is None
            else statement.where(DiskFolder.id.in_(row_ids))
        )
        return list(db.scalars(statement))


def merged(movie: dict[str, Any], series: dict[str, int], reasons: dict[str, int] | None = None) -> dict[str, Any]:
    """A movie job's result with the series counts and reasons added."""
    counts = dict(movie.get("counts") or {})
    for key, value in series.items():
        counts[key] = counts.get(key, 0) + value
        movie[key] = int(movie.get(key) or 0) + value
    movie["counts"] = counts
    if reasons:
        merged_reasons = dict(movie.get("reasons") or {})
        for key, value in reasons.items():
            merged_reasons[key] = merged_reasons.get(key, 0) + value
        movie["reasons"] = merged_reasons
    return movie
