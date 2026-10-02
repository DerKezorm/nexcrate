"""Prowlarr connections: enter Prowlarr's address and key once, and nexcrate keeps one indexer per Prowlarr indexer.

⚠️ Prowlarr's key is stored encrypted and never leaves the backend again, not even partly. Answers carry
``has_api_key``. The indexers of a connection carry the same key; their answers never show it either.

Saving tests the connection (``system/status``, version 1.8.6 or newer, the tags) and then syncs. A sync also runs
every 15 minutes and on ``POST /{id}/sync``; what it did stays on the connection (``last_sync_at``, ``last_counts``,
``last_error_code``). What it does is described in ``services/prowlarr/sync.py``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .. import crypto
from ..db import SessionLocal
from ..deps import DbSession
from ..meldungen import error, error_responses
from ..models import Indexer, ProwlarrConnection, utcnow
from ..services.prowlarr import sync as prowlarr_sync
from ..services.prowlarr.client import ERRORS as PROWLARR_ERRORS
from ..services.prowlarr.client import ProwlarrClient, ProwlarrError
from ..services.radarr import SourceUrlInvalid, normalize_base_url
from ..services.schreibweisen import nfc

logger = logging.getLogger("nexcrate.prowlarr")

router = APIRouter(prefix="/api/prowlarr", tags=["prowlarr"])

NAME_MAX_LENGTH = 100
KEY_MAX_LENGTH = 256
TAGS_MAX = 50
SyncLevel = Literal["full", "add_remove"]

_LEVEL_TEXT = (
    "`full`: what Prowlarr decides (name, enabled, automatic search, priority, minimum seeders, categories, seed "
    "goals, query limit) is written again at every sync and cannot be changed in nexcrate; an indexer that no longer "
    "qualifies goes. `add_remove`: those fields are taken when an indexer is added and stay editable afterwards. "
    "Both remove what Prowlarr no longer has; address and key always follow the connection."
)
_TAGS_TEXT = "Prowlarr's tag ids. With any, only Prowlarr indexers sharing one are taken; none: every indexer."


class ProwlarrTag(BaseModel):
    id: int
    label: str


class SyncCounts(BaseModel):
    total: int = Field(description="Indexers in Prowlarr.")
    added: int
    updated: int
    removed: int
    adopted: int = Field(description="Indexers nexcrate had already (same address) that joined the connection.")
    skipped_categories: int = Field(description="Left out: no category for movies, series, anime or music.")
    skipped_disabled: int = Field(description="Left out: switched off in Prowlarr and not in nexcrate yet.")
    skipped_tags: int = Field(description="Left out: sharing none of the connection's tags.")
    skipped_unsupported: int = Field(description="Left out: neither torrent nor Usenet.")
    kept_blocked: int = Field(description="Left as they are: Prowlarr holds them back after failures.")


class ProwlarrOut(BaseModel):
    id: int
    name: str
    url: str = Field(examples=["http://prowlarr:9696"], description="With Prowlarr's URL base, if it has one.")
    has_api_key: bool = Field(description="Whether a key is stored. It is never returned.")
    enabled: bool = Field(description="Off: no sync; the indexers stay as they are.")
    sync_level: str = Field(description=_LEVEL_TEXT)
    tags: list[ProwlarrTag] = Field(description=_TAGS_TEXT)
    version: str | None = Field(description="Prowlarr's version as read at the last sync.")
    indexer_count: int = Field(description="nexcrate's indexers of this connection now.")
    last_sync_at: datetime | None = Field(description="UTC. The last sync, also one that failed.")
    last_error_code: str | None = Field(
        description="The code of the last failed sync; null after one that went through."
    )
    last_counts: SyncCounts | None = Field(description="What the last sync that went through did.")


class ProwlarrIn(BaseModel):
    name: str = Field(max_length=400, description=f"1 to {NAME_MAX_LENGTH} characters.")
    url: str = Field(max_length=2048, description="http or https with Prowlarr's URL base, without user info or query.")
    api_key: str = Field(
        max_length=KEY_MAX_LENGTH, description="Prowlarr's API key (Settings, General). Never returned."
    )
    enabled: bool = True
    sync_level: SyncLevel = Field(default="full", description=_LEVEL_TEXT)
    tags: list[int] = Field(default_factory=list, max_length=TAGS_MAX, description=_TAGS_TEXT)


class ProwlarrPatch(BaseModel):
    name: str | None = Field(default=None, max_length=400)
    url: str | None = Field(default=None, max_length=2048)
    api_key: str | None = Field(default=None, max_length=KEY_MAX_LENGTH, description="Empty keeps the stored key.")
    enabled: bool | None = None
    sync_level: SyncLevel | None = Field(default=None, description=_LEVEL_TEXT)
    tags: list[int] | None = Field(default=None, max_length=TAGS_MAX, description=_TAGS_TEXT)


class ProwlarrTestIn(BaseModel):
    id: int | None = Field(default=None, description="Test a saved connection; the fields given along override it.")
    url: str | None = Field(default=None, max_length=2048, description="Needed without `id`.")
    api_key: str | None = Field(
        default=None, max_length=KEY_MAX_LENGTH, description="Needed without `id`; empty uses the stored key of `id`."
    )


class ProwlarrTestOut(BaseModel):
    version: str
    indexer_count: int = Field(description="Indexers in Prowlarr, switched off ones too.")
    tags: list[ProwlarrTag] = Field(description="Prowlarr's tags, to choose from.")


# --- Checks ------------------------------------------------------------------------------------------- #


def _not_found() -> HTTPException:
    return error("not_found", "This does not exist, or not any more.", 404)


def _clean_name(raw: str) -> str:
    name = nfc(raw).strip()
    if not name or len(name) > NAME_MAX_LENGTH:
        raise error("invalid_input", "The input is not valid.", 422, fields=["name"])
    return name


def _clean_url(raw: str) -> str:
    try:
        return normalize_base_url(raw)
    except SourceUrlInvalid as exc:
        raise error(
            "prowlarr_url_invalid",
            "This address does not work. It has to start with http:// or https:// and must contain neither a user "
            "name and password nor the key.",
            422,
        ) from exc


def _clean_key(raw: str | None) -> str:
    key = (raw or "").strip()
    if key and (not key.isascii() or any(character.isspace() or not character.isprintable() for character in key)):
        raise error("invalid_input", "The input is not valid.", 422, fields=["api_key"])
    return key


def _clean_tags(values: list[int]) -> list[int]:
    cleaned: list[int] = []
    for value in values:
        if value < 1:
            raise error("invalid_input", "The input is not valid.", 422, fields=["tags"])
        if value not in cleaned:
            cleaned.append(value)
    return cleaned


def _key_missing() -> HTTPException:
    return error(
        "prowlarr_key_missing", "The stored API key of this Prowlarr cannot be read. Please enter it again.", 422
    )


def _tags_unknown(missing: list[int]) -> HTTPException:
    return error(
        "prowlarr_tag_missing",
        "Prowlarr has no tag with this id (any more). Choose the tags again.",
        422,
        tags=missing,
    )


# --- Storage ------------------------------------------------------------------------------------------ #


@dataclass(frozen=True)
class _Stored:
    id: int
    url: str
    api_key: str = field(repr=False)
    enabled: bool = True
    tags: list[dict[str, Any]] = field(default_factory=list)


def _load(connection_id: int) -> _Stored | None:
    with SessionLocal() as db:
        row = db.get(ProwlarrConnection, connection_id)
        if row is None:
            return None
        return _Stored(
            id=row.id,
            url=row.url,
            api_key=row.api_key,
            enabled=row.enabled,
            tags=[dict(entry) for entry in row.tags or []],
        )


def _decrypted(stored: _Stored) -> str:
    if not stored.api_key:
        return ""
    key = crypto.decrypt(stored.api_key)
    if not key:
        raise _key_missing()
    return key


def connection_out(db: Any, row: ProwlarrConnection) -> ProwlarrOut:
    count = db.scalar(select(func.count(Indexer.id)).where(Indexer.prowlarr_id == row.id)) or 0
    counts = None
    if isinstance(row.last_counts, dict):
        counts = SyncCounts.model_validate({name: int(row.last_counts.get(name) or 0) for name in prowlarr_sync.COUNTS})
    return ProwlarrOut(
        id=row.id,
        name=row.name,
        url=row.url,
        has_api_key=bool(row.api_key),
        enabled=row.enabled,
        sync_level=row.sync_level,
        tags=[
            ProwlarrTag(id=int(entry["id"]), label=str(entry.get("label") or entry["id"])) for entry in row.tags or []
        ],
        version=row.version,
        indexer_count=int(count),
        last_sync_at=row.last_sync_at,
        last_error_code=row.last_error_code,
        last_counts=counts,
    )


def _read(connection_id: int) -> ProwlarrOut:
    with SessionLocal() as db:
        row = db.get(ProwlarrConnection, connection_id)
        if row is None:
            raise _not_found()
        return connection_out(db, row)


@dataclass(frozen=True)
class _Tested:
    version: str
    indexer_count: int
    tags: dict[int, str]


async def _test(url: str, key: str) -> _Tested:
    try:
        async with ProwlarrClient(url, key) as prowlarr:
            status = await prowlarr.system_status()
            listed = await prowlarr.indexers()
            tags = await prowlarr.tags()
    except ProwlarrError as exc:
        logger.info("Prowlarr test failed: %s", exc.code)
        raise exc.http() from exc
    return _Tested(version=status.version, indexer_count=len(listed), tags=tags)


def _chosen(tags: list[int], known: dict[int, str]) -> list[dict[str, Any]]:
    missing = [tag for tag in tags if tag not in known]
    if missing:
        raise _tags_unknown(missing)
    return [{"id": tag, "label": known[tag]} for tag in tags]


def _insert(values: dict[str, Any], key: str) -> int:
    moment = utcnow()
    with SessionLocal() as db:
        row = ProwlarrConnection(**values, api_key=crypto.encrypt(key), created_at=moment, updated_at=moment)
        db.add(row)
        db.commit()
        logger.info("Prowlarr connection %d created", row.id)
        return row.id


def _apply(connection_id: int, changes: dict[str, Any], key: str | None) -> None:
    with SessionLocal() as db:
        row = db.get(ProwlarrConnection, connection_id)
        if row is None:
            raise _not_found()
        for name, value in changes.items():
            setattr(row, name, value)
        if key:
            row.api_key = crypto.encrypt(key)
        row.updated_at = utcnow()
        db.commit()
        logger.info("Prowlarr connection %d changed", connection_id)


async def _sync_quietly(connection_id: int) -> None:
    """The sync after saving: a failure stays on the connection, the save stands."""
    try:
        await prowlarr_sync.sync(connection_id)
    except (ProwlarrError, prowlarr_sync.SyncBusy, LookupError):
        return


# --- Routes ------------------------------------------------------------------------------------------- #


@router.get(
    "",
    response_model=list[ProwlarrOut],
    summary="List the Prowlarr connections",
    description="Every connection with its last sync. The API key is never part of the answer.",
)
def list_connections(db: DbSession) -> list[ProwlarrOut]:
    return [
        connection_out(db, row) for row in db.scalars(select(ProwlarrConnection).order_by(ProwlarrConnection.id))
    ]


@router.post(
    "/test",
    response_model=ProwlarrTestOut,
    summary="Test a Prowlarr connection",
    description=(
        "Send `url` and `api_key`, or `id` of a saved connection, whose stored values the given ones override. Reads "
        "Prowlarr's status (it has to be Prowlarr, version 1.8.6 or newer), its indexers and its tags. Nothing is "
        "stored."
    ),
    responses=error_responses(
        (404, "not_found"), (422, "prowlarr_url_invalid"), (422, "prowlarr_key_missing"), *PROWLARR_ERRORS
    ),
)
async def test_connection(payload: ProwlarrTestIn) -> ProwlarrTestOut:
    if payload.id is not None:
        stored = await asyncio.to_thread(_load, payload.id)
        if stored is None:
            raise _not_found()
        url = _clean_url(payload.url) if (payload.url or "").strip() else stored.url
        key = _clean_key(payload.api_key) or await asyncio.to_thread(_decrypted, stored)
    else:
        missing = [name for name, value in (("url", payload.url), ("api_key", payload.api_key)) if not (value or "")]
        if missing:
            raise error("invalid_input", "The input is not valid.", 422, fields=missing)
        url = _clean_url(payload.url or "")
        key = _clean_key(payload.api_key)
    tested = await _test(url, key)
    return ProwlarrTestOut(
        version=tested.version,
        indexer_count=tested.indexer_count,
        tags=[ProwlarrTag(id=tag_id, label=label) for tag_id, label in sorted(tested.tags.items())],
    )


@router.post(
    "",
    status_code=201,
    response_model=ProwlarrOut,
    summary="Add a Prowlarr connection",
    description=(
        "Tests the connection, stores it with the key encrypted and syncs its indexers at once. A sync that fails "
        "after the test keeps its code on the connection; the connection is saved all the same."
    ),
    responses=error_responses(
        (422, "prowlarr_url_invalid"), (422, "prowlarr_key_missing"), (422, "prowlarr_tag_missing"), *PROWLARR_ERRORS
    ),
)
async def create_connection(payload: ProwlarrIn) -> ProwlarrOut:
    name = _clean_name(payload.name)
    url = _clean_url(payload.url)
    key = _clean_key(payload.api_key)
    if not key:
        raise error("invalid_input", "The input is not valid.", 422, fields=["api_key"])
    tags = _clean_tags(payload.tags)
    tested = await _test(url, key)
    values = {
        "name": name,
        "url": url,
        "enabled": payload.enabled,
        "sync_level": payload.sync_level,
        "tags": _chosen(tags, tested.tags),
        "version": tested.version,
    }
    connection_id = await asyncio.to_thread(_insert, values, key)
    if payload.enabled:
        await _sync_quietly(connection_id)
    return await asyncio.to_thread(_read, connection_id)


@router.patch(
    "/{connection_id}",
    response_model=ProwlarrOut,
    summary="Change a Prowlarr connection",
    description=(
        "Changes any of the given fields. An empty or missing `api_key` keeps the stored key. A new address or key, "
        "or new tags, are tested before anything is stored. An enabled connection syncs right after saving."
    ),
    responses=error_responses(
        (404, "not_found"),
        (422, "prowlarr_url_invalid"),
        (422, "prowlarr_key_missing"),
        (422, "prowlarr_tag_missing"),
        *PROWLARR_ERRORS,
    ),
)
async def update_connection(connection_id: int, payload: ProwlarrPatch) -> ProwlarrOut:
    stored = await asyncio.to_thread(_load, connection_id)
    if stored is None:
        raise _not_found()
    changes: dict[str, Any] = {}
    if payload.name is not None:
        changes["name"] = _clean_name(payload.name)
    if payload.enabled is not None:
        changes["enabled"] = payload.enabled
    if payload.sync_level is not None:
        changes["sync_level"] = payload.sync_level
    url = _clean_url(payload.url) if payload.url is not None else stored.url
    new_key = _clean_key(payload.api_key)
    tags = _clean_tags(payload.tags) if payload.tags is not None else None
    if url != stored.url or new_key or tags is not None:
        key = new_key or await asyncio.to_thread(_decrypted, stored)
        tested = await _test(url, key)
        changes["url"] = url
        changes["version"] = tested.version
        if tags is not None:
            changes["tags"] = _chosen(tags, tested.tags)
    await asyncio.to_thread(_apply, connection_id, changes, new_key or None)
    if changes.get("enabled", stored.enabled):
        await _sync_quietly(connection_id)
    return await asyncio.to_thread(_read, connection_id)


def _delete(connection_id: int, keep_indexers: bool) -> int:
    with SessionLocal() as db:
        row = db.get(ProwlarrConnection, connection_id)
        if row is None:
            raise _not_found()
        indexers = list(db.scalars(select(Indexer).where(Indexer.prowlarr_id == connection_id)))
        for indexer in indexers:
            if keep_indexers:
                indexer.prowlarr_id = None
                indexer.prowlarr_indexer_id = None
                indexer.prowlarr_limit = None
                indexer.updated_at = utcnow()
            else:
                db.delete(indexer)
        db.delete(row)
        db.commit()
        logger.info(
            "Prowlarr connection %d deleted, its %d indexers %s",
            connection_id,
            len(indexers),
            "kept as indexers of their own" if keep_indexers else "with it",
        )
        return len(indexers)


@router.delete(
    "/{connection_id}",
    status_code=204,
    response_model=None,
    summary="Delete a Prowlarr connection",
    description=(
        "Removes the connection and its stored key, and its indexers with it; waiting releases of those indexers go "
        "too, downloads keep the indexer's name. With `keep_indexers` the indexers stay as indexers of their own, "
        "editable and no longer synced. Prowlarr itself is not contacted."
    ),
    responses=error_responses((404, "not_found")),
)
async def delete_connection(
    connection_id: int,
    keep_indexers: bool = Query(default=False, description="Keep the indexers as indexers of their own."),
) -> None:
    await asyncio.to_thread(_delete, connection_id, keep_indexers)


@router.post(
    "/{connection_id}/sync",
    response_model=ProwlarrOut,
    summary="Sync a Prowlarr connection now",
    description=(
        "Reads Prowlarr and brings the connection's indexers up to date, also for a switched off connection. A sync "
        "of the same connection that runs already answers 409 `prowlarr_sync_running`. A failure is kept on the "
        "connection as well."
    ),
    responses=error_responses(
        (404, "not_found"),
        (409, "prowlarr_sync_running"),
        (422, "prowlarr_key_missing"),
        (422, "prowlarr_tag_missing"),
        *PROWLARR_ERRORS,
    ),
)
async def sync_connection(connection_id: int) -> ProwlarrOut:
    try:
        await prowlarr_sync.sync(connection_id)
    except LookupError as exc:
        raise _not_found() from exc
    except prowlarr_sync.SyncBusy as exc:
        raise error("prowlarr_sync_running", "A sync of this connection runs right now.", 409) from exc
    except ProwlarrError as exc:
        raise exc.http() from exc
    return await asyncio.to_thread(_read, connection_id)
