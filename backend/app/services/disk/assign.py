"""Assigning a folder by hand (L6), taking a moved folder's path, ignoring, media data.

* **Assigning** records in one transaction: the title (existing, or created with TMDB's data fetched before the
  transaction), the title's version of the chosen definition without a file or a new one (``added_by`` owner,
  monitored), the file's fields with the quality and languages of L7, the media data, a history entry
  ``found_on_disk``, the judgement and the state. After the commit the ``release.nex`` is written and the scan row is
  ``library``.
* **The folder must be as scanned:** the folder exists strictly inside the root, is no link, and the chosen video is
  there with the scanned size; otherwise 409 ``folder_changed``. A row that became ``library`` or ``radarr`` answers
  409 ``folder_known``.
* **Media data** is read once per file and size, kept in memory for 30 minutes: the dialog reads it first
  (``POST /api/disk/folders/{id}/media``), assigning uses the same reading.
* Log lines carry ids, never names or paths.
"""

from __future__ import annotations

import asyncio
import logging
import os
import stat
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ... import __version__
from ...db import SessionLocal
from ...meldungen import error
from ...models import DiskFolder, DiskRoot, HistoryEntry, Source, Title, Version, VersionDefinition, utcnow
from .. import companions, judging, library, media, schreibweisen, tmdb
from ..downloads import files, store
from ..releases import parser as release_parser
from . import roots as root_service
from .jobs import DiskJob

logger = logging.getLogger("nexcrate.disk")

MEDIA_CACHE_SECONDS = 30 * 60
_media_lock = threading.Lock()
_media_cache: dict[tuple[int, str, int], tuple[float, media.Read]] = {}


def not_found() -> HTTPException:
    return error("not_found", "This does not exist, or not any more.", 404)


def folder_changed() -> HTTPException:
    return error("folder_changed", "The folder is no longer as nexcrate scanned it. Scan the folder again.", 409)


def folder_known() -> HTTPException:
    return error("folder_known", "This folder now belongs to a movie in the library or to Radarr.", 409)


# --- The folder as scanned ------------------------------------------------------------------------------ #


@dataclass(frozen=True)
class Located:
    """A scan row's folder and chosen video on disk, checked against the scan."""

    root: Path
    folder: Path
    video: Path
    file_name: str
    size: int


def _video_of(row: DiskFolder, file_name: str | None) -> dict[str, Any] | None:
    videos = [video for video in (row.videos or []) if isinstance(video, dict)]
    if not videos:
        return None
    if file_name is None:
        return videos[0]
    wanted = schreibweisen.nfc(file_name)
    return next((video for video in videos if schreibweisen.nfc(str(video.get("name"))) == wanted), None)


def locate(root: DiskRoot, row: DiskFolder, file_name: str | None) -> Located:
    """The row's folder and the chosen video, as scanned. Raises 409 ``folder_changed``."""
    valid_file_name(file_name)
    video = _video_of(row, file_name)
    root_path = root_service.visible_path(root)
    if video is None or root_path is None or row.kind not in ("folder", "group"):
        raise folder_changed()
    parts = [part for part in row.relative_path.replace("\\", "/").split("/") if part]
    if not parts or any(part in (".", "..") for part in parts):
        raise folder_changed()
    folder_candidate = root_path.joinpath(*parts)
    folder = files.resolved(folder_candidate)
    if folder is None or files.is_link(folder_candidate) or not folder.is_dir():
        raise folder_changed()
    if not files.strictly_inside(folder, root_path):
        raise folder_changed()
    name = str(video["name"])
    video_path = folder / name
    try:
        info = os.lstat(video_path)
    except OSError as exc:
        raise folder_changed() from exc
    if not stat.S_ISREG(info.st_mode) or files.is_link(video_path) or info.st_size != int(video["size_bytes"]):
        raise folder_changed()
    return Located(root=root_path, folder=folder, video=video_path, file_name=name, size=int(info.st_size))


# --- Media data ------------------------------------------------------------------------------------------- #


def read_media(row_id: int, located: Located) -> media.Read:
    """The media data of the chosen video, read once and kept for 30 minutes."""
    key = (row_id, located.file_name, located.size)
    now = time.monotonic()
    with _media_lock:
        for cached_key, (stamp, _read) in list(_media_cache.items()):
            if now - stamp > MEDIA_CACHE_SECONDS:
                del _media_cache[cached_key]
        hit = _media_cache.get(key)
    if hit is not None:
        return hit[1]
    read = media.read(located.video)
    with _media_lock:
        _media_cache[key] = (now, read)
    return read


def forget_media() -> None:
    with _media_lock:
        _media_cache.clear()


@dataclass(frozen=True)
class FileFacts:
    quality: str
    quality_from: str
    name_quality: str
    languages: list[str]
    media: dict[str, Any] | None
    error_code: str | None
    release_group: str | None
    edition: str | None


def facts_of(located: Located, read: media.Read, original_language: str | None) -> FileFacts:
    """Quality and languages of the chosen file by L7, plus what its name says."""
    decision = media.quality_of(located.file_name, located.folder.name, read.media)
    stem = Path(located.file_name).stem
    parsed = release_parser.parse_movie(stem)
    return FileFacts(
        quality=decision.quality,
        quality_from=decision.quality_from,
        name_quality=decision.name_quality,
        languages=media.languages_of(read.media, stem, original_language),
        media=read.media,
        error_code=read.error_code,
        release_group=parsed.group,
        edition=parsed.edition,
    )


# --- Assigning ----------------------------------------------------------------------------------------- #


def _definition(db: OrmSession, version_id: int) -> VersionDefinition:
    definition = db.get(VersionDefinition, version_id)
    if definition is None:
        raise not_found()
    if definition.kind != "movie":
        raise error(
            "version_kind_mismatch", "This version belongs to another type. A movie needs a movie version.", 422
        )
    return definition


def _version_conflicts(db: OrmSession, title: Title, definition: VersionDefinition) -> Version | None:
    """The title's version of the definition, checked: 409 when a source feeds it or it has a file."""
    version = db.scalar(
        select(Version).where(Version.title_id == title.id, Version.version_definition_id == definition.id)
    )
    if version is None:
        return None
    if version.source_id is not None:
        raise error(
            "version_owned_by_source",
            "This version comes from a Radarr connection. It goes when you remove the movie there or delete the "
            "connection.",
            409,
        )
    if version.has_file:
        where = library.location(version, definition.folder)
        raise error(
            "version_has_file",
            "This version already has a file. nexcrate replaces nothing.",
            409,
            location=where["path"] if where is not None else None,
        )
    return version


def valid_file_name(file_name: str | None) -> None:
    """A file name is a single name, never a path. Raises 422 ``invalid_input``."""
    if file_name is None:
        return
    bad = not file_name.strip() or "/" in file_name or "\\" in file_name or "\x00" in file_name
    if bad or file_name in (".", ".."):
        raise error("invalid_input", "The input is not valid.", 422, fields=["file"])


def check_assignable(db: OrmSession, row: DiskFolder, tmdb_id: int, version_id: int, file_name: str | None) -> None:
    """The checks that need no TMDB and no disk, before TMDB is asked: the file name, the row's state, the definition,
    the version."""
    valid_file_name(file_name)
    if row.state in ("library", "radarr") or row.kind not in ("folder", "group"):
        raise folder_known()
    definition = _definition(db, version_id)
    title = db.scalar(select(Title).where(Title.kind == "movie", Title.tmdb_id == tmdb_id))
    if title is not None:
        _version_conflicts(db, title, definition)


def _title_of(db: OrmSession, tmdb_id: int, data: tmdb.MovieData | None, moment: datetime) -> Title:
    title = db.scalar(select(Title).where(Title.kind == "movie", Title.tmdb_id == tmdb_id))
    if title is not None:
        return title
    if data is None:
        raise not_found()
    title = Title(kind="movie", tmdb_id=tmdb_id, added=moment, updated_at=moment)
    tmdb.apply_movie_data(title, data, moment)
    db.add(title)
    db.flush()
    return title


def _fill_version(
    db: OrmSession,
    version: Version,
    row: DiskFolder,
    located: Located,
    facts: FileFacts,
    moment: datetime,
) -> None:
    version.has_file = True
    version.file_ref = f"disk:{row.id}"[:64]
    version.root_folder = str(located.root)
    version.relative_path = f"{row.relative_path}/{located.file_name}"[:2048]
    version.size = located.size
    version.quality = facts.quality
    version.quality_from = facts.quality_from
    version.release_title = Path(located.file_name).stem[:1024]
    version.release_group = (facts.release_group or None) and facts.release_group[:200]
    version.languages = list(facts.languages)
    version.media_info = facts.media
    version.media_read_at = moment if facts.media is not None else None
    version.source_movie_path = None
    version.upgrade_to = None
    version.progress = None
    version.problem_code = None
    version.updated_at = moment
    db.flush()
    version.cutoff_not_met = judging.judge(db, version)


def assign(
    row_id: int,
    tmdb_id: int,
    version_id: int,
    file_name: str | None,
    data: tmdb.MovieData | None,
    read: media.Read | None = None,
) -> tuple[int, int]:
    """The transaction of assigning. Returns the title id and the version id. Raises the codes of L6.

    ``data`` is TMDB's answer for a movie not in the library yet, fetched before; ``read`` the media data when it was
    read already.
    """
    moment = utcnow()
    with SessionLocal() as db:
        row = db.get(DiskFolder, row_id)
        if row is None:
            raise not_found()
        root = db.get(DiskRoot, row.root_id)
        if root is None:
            raise not_found()
        check_assignable(db, row, tmdb_id, version_id, file_name)
        definition = _definition(db, version_id)
        located = locate(root, row, file_name)
        title = _title_of(db, tmdb_id, data, moment)
        version = _version_conflicts(db, title, definition)
        if version is None:
            version = library.add_owner_version(db, title, definition, moment)
        media_read = read if read is not None else read_media(row.id, located)
        facts = facts_of(located, media_read, title.original_language)
        _fill_version(db, version, row, located, facts, moment)
        db.add(
            HistoryEntry(
                title_id=title.id,
                version_id=version.id,
                version_definition_id=definition.id,
                version_label=definition.label,
                event="found_on_disk",
                at=moment,
                detail=facts.quality,
            )
        )
        store.follow_version(db, title.id, definition.id, moment)
        row.state = "library"
        row.title_id = title.id
        row.version_id = version.id
        row.tmdb_id = title.tmdb_id
        row.source_id = None
        row.proposals = None
        title_id, new_version_id = title.id, version.id
        db.commit()
    write_companion(new_version_id)
    logger.info("Folder %d assigned to title %d as version %d (%s)", row_id, title_id, new_version_id, facts.quality)
    return title_id, new_version_id


def adopt_companion(folder: Path) -> bool:
    """A ``release.nex`` of another installation in a folder the owner took over becomes this installation's
    (decision 4): the same document, written with this installation's id. Returns whether it was rewritten."""
    installation = companions.installation_id()
    existing = companions.read(folder, installation)
    if existing.outcome != companions.OTHER_INSTALLATION or existing.movie is None:
        return False
    document = {
        "format": companions.FORMAT,
        "format_version": companions.FORMAT_VERSION,
        "written_at": existing.written_at,
        "written_by": {"app": "nexcrate", "version": __version__, "installation": installation},
        "movie": existing.movie,
        "entries": sorted(existing.entries, key=lambda item: item["version"]),
    }
    try:
        companions.write_bytes(folder, companions.encode(document))
    except Exception:
        logger.exception("A release.nex of another installation could not be taken over; it stays as it is")
        return False
    return True


def write_companion(version_id: int) -> str:
    """The version's ``release.nex`` after a commit, a file of another installation taken over first; a failure is
    logged and never undoes what was committed."""
    try:
        with SessionLocal() as db:
            version = db.get(Version, version_id)
            located = companions.locate(version) if version is not None else None
        if located is not None and located.folder is not None:
            adopt_companion(located.folder)
        return companions.write_version(version_id)
    except Exception:
        logger.exception("Version %d: writing release.nex after assigning failed", version_id)
        return "failed"


def media_answer(row_id: int, file_name: str | None) -> dict[str, Any]:
    """``POST /api/disk/folders/{id}/media``: the media data and what would be recorded."""
    with SessionLocal() as db:
        row = db.get(DiskFolder, row_id)
        if row is None:
            raise not_found()
        root = db.get(DiskRoot, row.root_id)
        if root is None:
            raise not_found()
        located = locate(root, row, file_name)
        original_language = None
        if row.tmdb_id is not None:
            original_language = db.scalar(
                select(Title.original_language).where(Title.kind == "movie", Title.tmdb_id == row.tmdb_id)
            )
    read = read_media(row.id, located)
    facts = facts_of(located, read, original_language)
    return {
        "file": located.file_name,
        "media": facts.media,
        "summary": media.summary(facts.media),
        "quality": facts.quality,
        "quality_from": facts.quality_from,
        "name_quality": facts.name_quality,
        "languages": facts.languages,
        "error_code": facts.error_code,
    }


# --- Many at once -------------------------------------------------------------------------------------------- #


def _unambiguous(row: DiskFolder) -> dict[str, Any] | None:
    found = [item for item in (row.proposals or []) if isinstance(item, dict) and item.get("unambiguous")]
    return found[0] if len(found) == 1 else None


def assign_many_work(row_ids: list[int] | None) -> Any:
    def work(job: DiskJob) -> dict[str, Any]:
        return assign_many(job, row_ids)

    return work


def unambiguous_rows() -> list[int]:
    """Every row in ``proposal`` that is not ignored and has exactly one unambiguous proposal."""
    with SessionLocal() as db:
        rows = db.scalars(
            select(DiskFolder)
            .join(DiskRoot, DiskRoot.id == DiskFolder.root_id)
            .where(
                DiskFolder.state == "proposal",
                DiskFolder.ignored.is_(False),
                DiskRoot.kind.not_in(("series", "album")),
            )
            .order_by(DiskFolder.id)
        )
        return [row.id for row in rows if _unambiguous(row) is not None]


def assign_many(job: DiskJob, row_ids: list[int] | None) -> dict[str, Any]:
    """Assign every row whose proposal is unambiguous and whose root belongs to exactly one definition; the rest is
    ``skipped``. TMDB is asked for movies not in the library."""
    if row_ids is None:
        row_ids = unambiguous_rows()
    counts = {"assigned": 0, "skipped": 0, "conflict": 0, "failed": 0}
    conflicts: list[int] = []
    job.set(phase="assigning", done=0, total=len(row_ids))
    for index, row_id in enumerate(row_ids, start=1):
        try:
            outcome = _assign_one_unambiguous(row_id)
        except HTTPException as exc:
            code = exc.detail.get("code") if isinstance(exc.detail, dict) else None
            outcome = "conflict" if code in ("folder_changed", "folder_known", "version_has_file") else "skipped"
            if code == "version_owned_by_source":
                outcome = "conflict"
            if outcome == "conflict" and len(conflicts) < 20:
                conflicts.append(row_id)
        except Exception:
            logger.exception("Folder %d could not be assigned", row_id)
            outcome = "failed"
        counts[outcome] = counts.get(outcome, 0) + 1
        job.progress(index)
    logger.info("Assigning many: %s", ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))
    return {**counts, "counts": dict(counts), "conflicts": conflicts, "folders": len(row_ids)}


def _assign_one_unambiguous(row_id: int) -> str:
    with SessionLocal() as db:
        row = db.get(DiskFolder, row_id)
        if row is None or row.ignored or row.state != "proposal":
            return "skipped"
        proposal = _unambiguous(row)
        root = db.get(DiskRoot, row.root_id)
        if proposal is None or root is None:
            return "skipped"
        definitions = root_service.definitions_of(db, root)
        if len(definitions) != 1:
            return "skipped"
        definition_id = definitions[0].id
        tmdb_id = int(proposal["tmdb_id"])
        in_library = db.scalar(select(Title.id).where(Title.kind == "movie", Title.tmdb_id == tmdb_id)) is not None
    data: tmdb.MovieData | None = None
    if not in_library:
        data = asyncio.run(_fetch(tmdb_id))
        if data is None:
            return "skipped"
    assign(row_id, tmdb_id, definition_id, None, data)
    return "assigned"


async def _fetch(tmdb_id: int) -> tmdb.MovieData | None:
    stored, token = tmdb.token_state()
    if not stored or not token:
        return None
    try:
        return await tmdb.fetch_movie(token, tmdb_id, tmdb.account_locale())
    except tmdb.TmdbError as exc:
        logger.info("TMDB did not answer for a proposal: %s", exc.code)
        return None
    finally:
        await tmdb.close()


# --- A moved folder -------------------------------------------------------------------------------------------- #


def take_path(row_id: int) -> int:
    """``POST /api/disk/folders/{id}/path``: the version named by a ``moved`` row takes this folder. Returns the title
    id."""
    moment = utcnow()
    with SessionLocal() as db:
        row = db.get(DiskFolder, row_id)
        if row is None:
            raise not_found()
        root = db.get(DiskRoot, row.root_id)
        version = db.get(Version, row.version_id) if row.version_id is not None else None
        if root is None or row.state != "moved" or version is None or not companions.owned(version):
            raise folder_changed()
        definition = db.get(VersionDefinition, version.version_definition_id)
        entry = None
        for item in (row.companion or {}).get("entries", []) if isinstance(row.companion, dict) else []:
            if isinstance(item, dict) and definition is not None and item.get("version") == definition.label:
                entry = item
        if entry is None:
            raise folder_changed()
        located = locate(root, row, str(entry.get("file")))
        version.root_folder = str(located.root)
        version.relative_path = f"{row.relative_path}/{located.file_name}"[:2048]
        version.size = located.size
        version.updated_at = moment
        row.state = "library"
        title_id, version_id = version.title_id, version.id
        db.commit()
    write_companion(version_id)
    logger.info("Version %d takes the folder of row %d", version_id, row_id)
    return title_id


def set_ignored(row_id: int, value: bool) -> DiskFolder:
    with SessionLocal() as db:
        row = db.get(DiskFolder, row_id)
        if row is None:
            raise not_found()
        row.ignored = value
        db.commit()
        db.refresh(row)
        db.expunge(row)
        return row


def source_names(db: OrmSession, rows: list[DiskFolder]) -> dict[int, str]:
    ids = {row.source_id for row in rows if row.source_id is not None}
    if not ids:
        return {}
    return dict(db.execute(select(Source.id, Source.name).where(Source.id.in_(ids))).all())
