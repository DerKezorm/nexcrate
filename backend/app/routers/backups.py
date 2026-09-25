"""Backups: list, make, download as an encrypted archive, delete, check and restore an archive
(built after Nexview's backup page).

A restore happens at the next start: the archive is checked and laid out, the answer goes out, and nexcrate ends
itself; the container starts it again and the start swaps the files before anything opens the database.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from starlette.background import BackgroundTask

from .. import __version__
from ..config import get_settings
from ..db import set_setting
from ..deps import DbSession
from ..meldungen import error, error_responses
from ..services import backups

logger = logging.getLogger("nexcrate.backups")

router = APIRouter(tags=["backups"])


class BackupOut(BaseModel):
    name: str = Field(description="File name of the copy in the backup folder; the key for every other route.")
    size: int = Field(description="Bytes of the copy with its extras.")
    created: str = Field(description="When the copy was made, ISO 8601.")
    kind: str = Field(description="`manual`, `scheduled` or `update` (before a schema change or a restore).")
    comment: str = Field(description="What the owner wrote when making it; empty for automatic copies.")
    version: str = Field(description="The nexcrate version that made it; empty when unknown.")
    restorable: bool = Field(description="Whether this version could restore it.")
    reason: str = Field(description="`ok`, `backup_newer` or `unknown_version`.")


class BackupsOut(BaseModel):
    entries: list[BackupOut]
    version: str = Field(description="The running version.")
    folder: str = Field(description="Where the copies lie, as the server sees it.")
    schedule: str = Field(description="`off`, `daily`, `weekly` or `monthly`. Scheduled copies start at night.")
    keep: int = Field(description="Automatic copies kept; copies made by hand are never pruned.")


class BackupIn(BaseModel):
    comment: str = Field(default="", max_length=200, description="A note that also becomes part of the file name.")


class BackupSettingsIn(BaseModel):
    schedule: str | None = Field(default=None, description="`off`, `daily`, `weekly` or `monthly`.")
    keep: int | None = Field(default=None, strict=True, ge=backups.KEEP_MIN, le=backups.KEEP_MAX)

    @model_validator(mode="after")
    def one_field(self) -> BackupSettingsIn:
        if self.schedule is None and self.keep is None:
            raise ValueError("schedule or keep")
        if self.schedule is not None and self.schedule not in backups.SCHEDULES:
            raise ValueError("schedule")
        return self


class ArchiveIn(BaseModel):
    # The archive holds secret.key, and with it every stored credential: no shorter password.
    password: str = Field(min_length=backups.PASSWORD_MIN, max_length=200)


class ArchiveInfo(BaseModel):
    version: str = Field(description="The version that made the backup.")
    created: str
    kind: str
    comment: str
    restorable: bool
    reason: str = Field(description="`ok`, `backup_newer` or `unknown_version`.")
    key_in_archive: bool = Field(description="Whether the archive carries secret.key.")
    key_from_environment: bool = Field(
        description="Whether this installation takes its key from NEXCRATE_SECRET_KEY; then that wins over the "
        "archive's key, and stored credentials stay unreadable unless it holds the same value."
    )
    restarting: bool = Field(default=False, description="True after a restore was laid out: nexcrate restarts now.")


def _out(entry: backups.Entry) -> BackupOut:
    return BackupOut(
        name=entry.name,
        size=entry.size,
        created=entry.created,
        kind=entry.kind,
        comment=entry.comment,
        version=entry.version,
        restorable=entry.restorable,
        reason=entry.reason,
    )


def _list(db: DbSession) -> BackupsOut:
    return BackupsOut(
        entries=[_out(entry) for entry in backups.entries()],
        version=__version__,
        folder=str(backups.folder()),
        schedule=backups.schedule(db),
        keep=backups.keep(db),
    )


def _info(opened: backups.Opened, restorable: bool, reason: str, *, restarting: bool = False) -> ArchiveInfo:
    return ArchiveInfo(
        version=opened.manifest.version,
        created=opened.manifest.created,
        kind=opened.manifest.kind,
        comment=opened.manifest.comment,
        restorable=restorable,
        reason=reason,
        key_in_archive=opened.has_key,
        key_from_environment=bool(get_settings().secret_key),
        restarting=restarting,
    )


def _failed(exc: backups.BackupError) -> Exception:
    return error(exc.code, exc.message, exc.status)


@asynccontextmanager
async def _saved(upload: UploadFile) -> AsyncIterator[Path]:
    """The upload in a temporary file next to the backups, in pieces: an archive is never held whole in memory."""
    target = backups.temporary(".upload-")
    written = 0
    try:
        with target.open("wb") as sink:
            while chunk := await upload.read(1024 * 1024):
                written += len(chunk)
                if written > backups.MAX_UPLOAD:
                    raise error("restore_too_large", "The archive is larger than 4 GB.", 413)
                sink.write(chunk)
        yield target
    finally:
        target.unlink(missing_ok=True)


@router.get(
    "/api/backups",
    response_model=BackupsOut,
    summary="List the backups",
    description="Every copy in the backup folder, newest first, with whether this version could restore it.",
)
def list_backups(db: DbSession) -> BackupsOut:
    return _list(db)


@router.put(
    "/api/backups/settings",
    response_model=BackupsOut,
    summary="Set the schedule and how many automatic copies stay",
    description="A field left out keeps its value, at least one is needed. Scheduled copies start between 3 and 6 at "
    "night; one overdue by a day starts at any hour.",
)
def update_backup_settings(payload: BackupSettingsIn, db: DbSession) -> BackupsOut:
    if payload.schedule is not None:
        set_setting(db, backups.SETTING_SCHEDULE, payload.schedule)
    if payload.keep is not None:
        set_setting(db, backups.SETTING_KEEP, str(payload.keep))
    db.commit()
    logger.info("Backup settings changed: schedule %s, keep %s", backups.schedule(db), backups.keep(db))
    return _list(db)


@router.post(
    "/api/backups",
    response_model=BackupOut,
    status_code=201,
    summary="Make a backup now",
    description="A consistent copy of the database with the TRaSH state; caches are emptied in the copy. Copies made "
    "by hand are never pruned.",
    responses=error_responses((500, "backup_failed")),
)
async def create_backup(payload: BackupIn) -> BackupOut:
    try:
        # In a worker thread: on a NAS the copy of a large database takes minutes.
        path = await asyncio.to_thread(backups.create, kind=backups.MANUAL, comment=payload.comment)
    except Exception as exc:
        logger.exception("Manual backup failed")
        raise error("backup_failed", "The backup could not be made.", 500) from exc
    for entry in backups.entries():
        if entry.name == path.name:
            return _out(entry)
    raise error("backup_failed", "The backup could not be made.", 500)


@router.post(
    "/api/backups/{name}/archive",
    summary="Download a backup as an encrypted archive",
    description="An AES-encrypted ZIP with the database, secret.key and the TRaSH state; 7-Zip and WinRAR open "
    "it. POST, because the password belongs in the body, not in an address.",
    responses={
        200: {"content": {"application/zip": {}}, "description": "The archive."},
        **error_responses((404, "backup_not_found"), (422, "backup_password_short")),
    },
)
async def download_backup(name: str, payload: ArchiveIn) -> FileResponse:
    try:
        path = await asyncio.to_thread(backups.archive, name, payload.password)
    except backups.BackupError as exc:
        raise _failed(exc) from exc
    # The temporary archive goes once it is sent.
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"{name.removesuffix('.db')}.zip",
        background=BackgroundTask(path.unlink, missing_ok=True),
    )


@router.delete(
    "/api/backups/{name}",
    status_code=204,
    summary="Delete a backup",
    description="Removes the copy with its manifest and extras. The question before it sits in the interface.",
    responses=error_responses((404, "backup_not_found")),
)
def delete_backup(name: str) -> Response:
    try:
        path = backups.path_of(name)
    except backups.BackupError as exc:
        raise _failed(exc) from exc
    backups.remove(path)
    logger.info("Backup deleted: %s", name)
    return Response(status_code=204)


class LocalRestoreOut(BaseModel):
    name: str
    version: str
    created: str
    restarting: bool = Field(description="Always true: nexcrate restarts now and restores the copy at its start.")


@router.post(
    "/api/backups/{name}/restore",
    response_model=LocalRestoreOut,
    summary="Restore a copy of the list",
    description="Lays the copy out and restarts nexcrate, which restores it before anything opens the database, as "
    "with an archive: a copy of the current state first, everybody logged out afterwards. The key stays this "
    "installation's own. Only a copy of this version or an older one.",
    responses=error_responses(
        (404, "backup_not_found"), (409, "restore_backup_newer"), (409, "restore_unknown_version")
    ),
)
async def restore_copy(name: str) -> LocalRestoreOut:
    try:
        manifest = await asyncio.to_thread(backups.stage_local, name)
    except backups.BackupError as exc:
        raise _failed(exc) from exc
    backups.restart_soon()
    return LocalRestoreOut(name=name, version=manifest.version, created=manifest.created, restarting=True)


RESTORE_ERRORS = error_responses(
    (413, "restore_too_large"),
    (422, "restore_not_an_archive"),
    (422, "restore_not_a_backup"),
    (422, "restore_wrong_password"),
    (422, "restore_not_a_database"),
    (422, "restore_no_manifest"),
)


@router.post(
    "/api/backups/check",
    response_model=ArchiveInfo,
    summary="Look into an archive",
    description="Opens the archive with the password and says what it holds and whether it could be restored. "
    "Replaces nothing.",
    responses=RESTORE_ERRORS,
)
async def check_archive(file: Annotated[UploadFile, File()], password: Annotated[str, Form()]) -> ArchiveInfo:
    async with _saved(file) as path:
        try:
            opened, restorable, reason = await asyncio.to_thread(backups.check, path, password)
        except backups.BackupError as exc:
            raise _failed(exc) from exc
    return _info(opened, restorable, reason)


@router.post(
    "/api/backups/restore",
    response_model=ArchiveInfo,
    summary="Restore an archive",
    description="Checks the archive and lays it out, then nexcrate restarts and restores it before anything opens "
    "the database: a copy of the current state first, then database, key and TRaSH state. Everyone is logged out "
    "afterwards. Only a backup of this version or an older one can be restored.",
    responses={
        **RESTORE_ERRORS,
        409: {
            "model": RESTORE_ERRORS[422]["model"],
            "description": "Error codes: `restore_backup_newer`, `restore_unknown_version`",
        },
    },
)
async def restore_archive(file: Annotated[UploadFile, File()], password: Annotated[str, Form()]) -> ArchiveInfo:
    async with _saved(file) as path:
        try:
            opened = await asyncio.to_thread(backups.stage_restore, path, password)
        except backups.BackupError as exc:
            raise _failed(exc) from exc
    backups.restart_soon()
    return _info(opened, True, "ok", restarting=True)
