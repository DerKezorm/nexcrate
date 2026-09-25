"""The keys programs sign in with at ``/api/v1`` (N1 and N2).

A key is made once and shown once: the database keeps its SHA-256 hash and its last four characters. It travels only
in the header ``Authorization: Bearer``, never in an address, and no log line carries it (the redaction masks anything
behind ``Bearer``).

⚠️ "Last used" is kept in memory and written by a background job once a minute. A request that reads must never wait
for the one writer an import or a download holds (N37), so the request itself writes nothing.
"""

from __future__ import annotations

import logging
import secrets
import threading
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from .. import db as database
from ..models import API_SCOPES, ApiKey, utcnow
from ..security import hash_token
from .schreibweisen import nfc

logger = logging.getLogger("nexcrate.api_keys")

#: Every key starts with it, so a key found somewhere says where it belongs.
PREFIX = "nxc_"
NAME_MAX_LENGTH = 100
#: More keys than this nobody needs; the limit keeps a broken script from filling the table.
KEYS_MAX = 50
HINT_LENGTH = 4

JOB_NAME = "api_key_usage"
INTERVAL_SECONDS = 60

_lock = threading.Lock()
_used: dict[int, datetime] = {}


def new_key() -> str:
    """``nxc_`` and 43 characters of base64url. Leaves the backend once, in the answer that created it."""
    return PREFIX + secrets.token_urlsafe(32)


def clean_name(raw: str) -> str | None:
    name = nfc(raw).strip()
    return name if name and len(name) <= NAME_MAX_LENGTH else None


def clean_scopes(raw: list[str]) -> list[str] | None:
    """The scopes in their fixed order, with ``read`` added: whoever may request or operate may read. None when a scope
    is unknown or none is given."""
    if not raw or any(scope not in API_SCOPES for scope in raw):
        return None
    wanted = set(raw) | {"read"}
    return [scope for scope in API_SCOPES if scope in wanted]


def create(db: OrmSession, name: str, scopes: list[str], moment: datetime) -> tuple[ApiKey, str]:
    key = new_key()
    row = ApiKey(name=name, token_hash=hash_token(key), hint=key[-HINT_LENGTH:], scopes=scopes, created_at=moment)
    db.add(row)
    db.flush()
    return row, key


def find(db: OrmSession, key: str | None) -> ApiKey | None:
    if not key or not key.startswith(PREFIX):
        return None
    return db.scalar(select(ApiKey).where(ApiKey.token_hash == hash_token(key)))


def mark_used(key_id: int, moment: datetime | None = None) -> None:
    with _lock:
        _used[key_id] = moment or utcnow()


def last_used(row: ApiKey) -> datetime | None:
    """What the database knows, or what this minute saw and the job has not written yet."""
    with _lock:
        seen = _used.get(row.id)
    if seen is None:
        return row.last_used_at
    return seen if row.last_used_at is None or seen > row.last_used_at else row.last_used_at


def forget(key_id: int) -> None:
    with _lock:
        _used.pop(key_id, None)


def reset() -> None:
    with _lock:
        _used.clear()


def flush_used() -> int:
    """Write what the last minute saw. Returns how many keys got a newer time."""
    with _lock:
        seen = dict(_used)
        _used.clear()
    if not seen:
        return 0
    written = 0
    with database.SessionLocal() as db:
        for row in db.scalars(select(ApiKey).where(ApiKey.id.in_(seen))):
            moment = seen[row.id]
            if row.last_used_at is None or moment > row.last_used_at:
                row.last_used_at = moment
                written += 1
        db.commit()
    return written
