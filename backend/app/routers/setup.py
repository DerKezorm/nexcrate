"""First start: create the one account.

⚠️ Once the account exists, this route answers 404 ``not_found`` for good. Closed, not
hidden. Whoever reaches a fresh installation first can claim it; the README says so
openly, because it cannot be built away.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from fastapi.exceptions import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session as OrmSession

from .. import __version__
from ..deps import DbSession
from ..meldungen import error, error_responses
from ..models import ACCOUNT_ID, Account
from ..security import hash_password
from ..services import backups, sessions
from . import backups as backups_router
from .auth import (
    INPUT_MAX_LENGTH,
    LANGUAGE_MAX_LENGTH,
    LANGUAGE_PATTERN,
    Me,
    me_out,
    validate_new_password,
    validate_username,
)
from .backups import ArchiveInfo
from .whats_new import mark_seen_at_setup

logger = logging.getLogger("nexcrate.setup")

router = APIRouter(prefix="/api/setup", tags=["setup"])

#: The codes of a broken or unreadable archive, as `POST /api/backups/check` names them.
RESTORE_CODES = (
    (413, "restore_too_large"),
    (422, "restore_not_an_archive"),
    (422, "restore_not_a_backup"),
    (422, "restore_wrong_password"),
    (422, "restore_not_a_database"),
    (422, "restore_no_manifest"),
)


class SetupStatus(BaseModel):
    setup_required: bool = Field(description="True until the account exists.")
    version: str = Field(examples=["0.1.0"])


class SetupIn(BaseModel):
    username: str = Field(max_length=INPUT_MAX_LENGTH, description="1 to 64 characters.")
    password: str = Field(max_length=INPUT_MAX_LENGTH, description="At least 8 characters.")
    language: str = Field(
        pattern=LANGUAGE_PATTERN,
        max_length=LANGUAGE_MAX_LENGTH,
        description="A language code such as de, en or pt-BR.",
    )


def account_exists(db: OrmSession) -> bool:
    return db.get(Account, ACCOUNT_ID) is not None


def closed() -> HTTPException:
    return error("not_found", "This does not exist, or not any more.", 404)


def setup_open(db: DbSession) -> None:
    """Runs before the body is validated: a closed setup stays 404 whatever is sent."""
    if account_exists(db):
        raise closed()


def insert_account(db: OrmSession, username: str, password_hash: str, language: str) -> Account | None:
    """Insert the account with the fixed id. None when another setup was faster.

    Two setups at the same moment both pass ``setup_open``. The database decides: only one
    insert can take the fixed id.
    """
    account = Account(id=ACCOUNT_ID, username=username, password_hash=password_hash, language=language)
    db.add(account)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    except OperationalError:
        db.rollback()
        if account_exists(db):
            return None
        raise
    return account


@router.get(
    "/status",
    response_model=SetupStatus,
    summary="Ask whether the account still has to be created",
    description="The interface asks this first and shows the setup page, the login page or the app.",
)
def setup_status(db: DbSession) -> SetupStatus:
    return SetupStatus(setup_required=not account_exists(db), version=__version__)


@router.post(
    "",
    status_code=201,
    response_model=Me,
    dependencies=[Depends(setup_open)],
    summary="Create the account on the first start",
    description=(
        "Creates the one account and logs it in: the answer sets the cookie `nexcrate_session`. "
        "Once the account exists this route answers 404 `not_found` for good. "
        "Whoever reaches a fresh installation first can claim it."
    ),
    responses=error_responses((404, "not_found"), (422, "username_invalid"), (422, "password_too_short")),
)
def create_account(payload: SetupIn, request: Request, response: Response, db: DbSession) -> Me:
    username = validate_username(payload.username)
    validate_new_password(payload.password)
    account = insert_account(db, username, hash_password(payload.password), payload.language)
    if account is None:
        logger.warning("A second setup arrived at the same time and was refused")
        raise closed()
    mark_seen_at_setup(db)
    sessions.start(db, response, request)
    logger.info("Account created, setup is closed from now on")
    return me_out(account)


# --- Restoring a backup instead of starting empty (as Nexview's setup can) --------------------------------------- #


@router.post(
    "/backup/check",
    response_model=ArchiveInfo,
    dependencies=[Depends(setup_open)],
    summary="Look into a backup before the account exists",
    description="As `POST /api/backups/check`, while the installation has no account yet: whoever reaches a fresh "
    "installation first may start it from a backup instead of empty. Closed for good once the account exists.",
    responses=error_responses((404, "not_found"), *RESTORE_CODES),
)
async def check_backup(file: Annotated[UploadFile, File()], password: Annotated[str, Form()]) -> ArchiveInfo:
    async with backups_router._saved(file) as path:
        try:
            opened, restorable, reason = await asyncio.to_thread(backups.check, path, password)
        except backups.BackupError as exc:
            raise backups_router._failed(exc) from exc
    return backups_router._info(opened, restorable, reason)


@router.post(
    "/backup/restore",
    response_model=ArchiveInfo,
    dependencies=[Depends(setup_open)],
    summary="Start a fresh installation from a backup",
    description="Lays the archive out and restarts nexcrate, which restores it at its start. Afterwards the account of "
    "the backup exists and the setup is closed: log in with it. Closed for good once an account exists.",
    responses=error_responses(
        (404, "not_found"), *RESTORE_CODES, (409, "restore_backup_newer"), (409, "restore_unknown_version")
    ),
)
async def restore_backup(file: Annotated[UploadFile, File()], password: Annotated[str, Form()]) -> ArchiveInfo:
    async with backups_router._saved(file) as path:
        try:
            opened = await asyncio.to_thread(backups.stage_restore, path, password)
        except backups.BackupError as exc:
            raise backups_router._failed(exc) from exc
    logger.info("A fresh installation restores a backup of version %s", opened.manifest.version)
    backups.restart_soon()
    return backups_router._info(opened, True, "ok", restarting=True)

