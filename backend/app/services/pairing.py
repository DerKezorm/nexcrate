"""Pairing a program in one step (N8), as one confirms a device on a television.

The program sends its name and the scopes it wants and gets a pairing id, a secret and a code of six characters. The
owner sees "Nexview wants to connect, code 7Q4-K2P" in nexcrate and confirms, with the scopes the owner picks, or
denies.
Confirmed, a normal key comes into being under the program's name; the program fetches it **once** with its secret,
and afterwards it stands in the list of keys like any other and can be revoked. The key never shows in the interface.

A request lives ``LIFETIME``; at most ``PENDING_MAX`` wait at once, so nobody fills the owner's screen with them.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session as OrmSession

from .. import crypto
from ..meldungen import error
from ..models import ApiKey, ApiPairing, utcnow
from ..security import hash_token
from . import api_keys

logger = logging.getLogger("nexcrate.api_keys")

LIFETIME = timedelta(minutes=10)
#: A delivered or refused request is kept this long, so the program's last ask still gets its answer.
KEEP_ANSWERED = timedelta(minutes=10)
PENDING_MAX = 5
#: Letters and digits a person reads without confusing them (no 0, O, 1, I, L).
CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
APP_MAX_LENGTH = 60


def _code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))


def shown_code(code: str) -> str:
    return f"{code[:3]}-{code[3:]}"


def clear_expired(db: OrmSession, now: datetime) -> None:
    db.execute(delete(ApiPairing).where(ApiPairing.state == "pending", ApiPairing.expires_at < now))
    db.execute(
        delete(ApiPairing).where(ApiPairing.state != "pending", ApiPairing.created_at < now - LIFETIME - KEEP_ANSWERED)
    )


def ask(db: OrmSession, app: str, scopes: list[str]) -> dict[str, Any]:
    """A new request; commits. Raises ``invalid_input`` and ``pairing_limit``."""
    now = utcnow()
    name = api_keys.clean_name(app)
    chosen = api_keys.clean_scopes(scopes)
    if name is None or len(name) > APP_MAX_LENGTH or chosen is None:
        raise error("invalid_input", "The input is not valid.", 422, fields=["app", "scopes"])
    clear_expired(db, now)
    waiting = int(db.scalar(select(func.count()).select_from(ApiPairing).where(ApiPairing.state == "pending")) or 0)
    if waiting >= PENDING_MAX:
        raise error("pairing_limit", "Too many programs are waiting to be connected; try again later.", 429,
                    max=PENDING_MAX)  # fmt: skip
    secret = secrets.token_urlsafe(32)
    row = ApiPairing(
        pairing_id=secrets.token_urlsafe(16),
        secret_hash=hash_token(secret),
        app=name,
        scopes=chosen,
        code=_code(),
        state="pending",
        created_at=now,
        expires_at=now + LIFETIME,
    )
    db.add(row)
    db.commit()
    logger.info("A program asks to be connected; it waits for the owner")
    return {
        "pairing_id": row.pairing_id,
        "secret": secret,
        "code": shown_code(row.code),
        "expires_at": row.expires_at,
        "poll_seconds": 2,
    }


def _own(db: OrmSession, pairing_id: str, secret: str | None) -> ApiPairing:
    row = db.get(ApiPairing, pairing_id)
    # The same answer for an unknown id and a wrong secret: nobody learns which ids exist.
    if row is None or not secret or not secrets.compare_digest(row.secret_hash, hash_token(secret)):
        raise error("pairing_not_found", "This pairing does not exist, or not any more.", 404)
    return row


def poll(db: OrmSession, pairing_id: str, secret: str | None) -> dict[str, Any]:
    """How the request stands; a confirmed one hands out its key once, then counts as delivered. Commits."""
    now = utcnow()
    row = _own(db, pairing_id, secret)
    state, key, scopes = row.state, None, None
    if state == "pending" and row.expires_at < now:
        state = "expired"
    if state == "confirmed" and row.key_enc:
        key = crypto.decrypt(row.key_enc)
        made = db.get(ApiKey, row.key_id) if row.key_id is not None else None
        scopes = list(made.scopes or []) if made is not None else None
        row.state, row.key_enc = "delivered", None
        db.commit()
        logger.info("A connected program fetched its key %s", row.key_id)
    return {"state": state, "key": key, "scopes": scopes, "expires_at": row.expires_at}


def waiting(db: OrmSession) -> list[dict[str, Any]]:
    """The requests the owner has not answered yet, oldest first."""
    now = utcnow()
    rows = db.scalars(
        select(ApiPairing)
        .where(ApiPairing.state == "pending", ApiPairing.expires_at >= now)
        .order_by(ApiPairing.created_at)
    )
    return [
        {
            "pairing_id": row.pairing_id,
            "app": row.app,
            "code": shown_code(row.code),
            "scopes": list(row.scopes or []),
            "created_at": row.created_at,
            "expires_at": row.expires_at,
        }
        for row in rows
    ]


def _pending(db: OrmSession, pairing_id: str) -> ApiPairing:
    row = db.get(ApiPairing, pairing_id)
    if row is None or row.state != "pending" or row.expires_at < utcnow():
        raise error("pairing_not_found", "This pairing does not exist, or not any more.", 404)
    return row


def _free_name(db: OrmSession, name: str) -> str:
    taken = {value.casefold() for value in db.scalars(select(ApiKey.name))}
    if name.casefold() not in taken:
        return name
    number = 2
    while f"{name} {number}".casefold() in taken:
        number += 1
    return f"{name} {number}"[: api_keys.NAME_MAX_LENGTH]


def confirm(db: OrmSession, pairing_id: str, scopes: list[str]) -> dict[str, Any]:
    """The owner says yes: a key with the chosen scopes, kept for the program to fetch. Commits."""
    row = _pending(db, pairing_id)
    chosen = api_keys.clean_scopes(scopes)
    if chosen is None:
        raise error("invalid_input", "The input is not valid.", 422, fields=["scopes"])
    if int(db.scalar(select(func.count(ApiKey.id))) or 0) >= api_keys.KEYS_MAX:
        raise error("api_key_limit", f"There are {api_keys.KEYS_MAX} keys already.", 409, max=api_keys.KEYS_MAX)
    key_row, key = api_keys.create(db, _free_name(db, row.app), chosen, utcnow())
    row.state, row.key_id, row.key_enc = "confirmed", key_row.id, crypto.encrypt(key)
    db.commit()
    logger.info("The owner connected a program with key %d, scopes %s", key_row.id, ",".join(chosen))
    return {"key_id": key_row.id, "name": key_row.name, "scopes": chosen}


def deny(db: OrmSession, pairing_id: str) -> None:
    row = _pending(db, pairing_id)
    row.state = "denied"
    db.commit()
    logger.info("The owner refused a program that asked to be connected")
