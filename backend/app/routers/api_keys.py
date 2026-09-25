"""The keys other programs sign in with at ``/api/v1`` (N1 and N2): make, name, revoke.

A key is shown once, in the answer that makes it. nexcrate keeps its hash and its last four characters; nobody can
read it again, not even the owner. What a key may do are its scopes; none of them reaches indexers, download clients,
the account, profiles or anything secret: those have no address under ``/api/v1`` at all.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from ..deps import DbSession
from ..meldungen import error, error_responses
from ..models import API_SCOPES, ApiKey, utcnow
from ..services import api_keys

logger = logging.getLogger("nexcrate.api_keys")

router = APIRouter(prefix="/api/api-keys", tags=["api-keys"])

Scope = Literal["read", "request", "operate"]


class ApiKeyOut(BaseModel):
    id: int
    name: str = Field(examples=["Nexview"])
    hint: str = Field(description="The last four characters of the key, to tell two keys apart.", examples=["x9Qd"])
    scopes: list[str] = Field(description="What the key may do; `read` is always among them.", examples=[["read"]])
    created_at: datetime
    last_used_at: datetime | None = Field(description="Null for a key no request has carried yet.")


class ApiKeyCreated(ApiKeyOut):
    key: str = Field(description="The key itself. This answer is the only place it ever appears.")


class ApiKeyList(BaseModel):
    items: list[ApiKeyOut]
    scopes: list[str] = Field(description="Every scope a key can carry, in their fixed order.")


class ApiKeyIn(BaseModel):
    name: str = Field(max_length=400, description=f"1 to {api_keys.NAME_MAX_LENGTH} characters, unique.")
    scopes: list[Scope] = Field(
        description="`read` reads. `request` adds, watches, searches and takes back (from stage V2 of the contract). "
        "`operate` resolves problems and assigns files by hand (from V3). `read` comes along with either.",
        max_length=len(API_SCOPES),
    )


class ApiKeyPatch(BaseModel):
    name: str = Field(max_length=400)


def _out(row: ApiKey) -> ApiKeyOut:
    return ApiKeyOut(
        id=row.id,
        name=row.name,
        hint=row.hint,
        scopes=list(row.scopes or []),
        created_at=row.created_at,
        last_used_at=api_keys.last_used(row),
    )


def _name_taken(db: OrmSession, name: str, own_id: int | None) -> bool:
    wanted = name.casefold()
    return any(
        row_name.casefold() == wanted and row_id != own_id
        for row_id, row_name in db.execute(select(ApiKey.id, ApiKey.name)).tuples()
    )


@router.get(
    "",
    response_model=ApiKeyList,
    summary="List the API keys",
    description="Every key with its name, scopes and when a request last carried it. Never the key itself.",
)
def list_keys(db: DbSession) -> ApiKeyList:
    rows = db.scalars(select(ApiKey).order_by(ApiKey.id))
    return ApiKeyList(items=[_out(row) for row in rows], scopes=list(API_SCOPES))


@router.post(
    "",
    response_model=ApiKeyCreated,
    status_code=201,
    summary="Make an API key",
    description=(
        "Makes a key and answers with it, once. Send it as `Authorization: Bearer <key>` to `/api/v1`, never in an "
        "address."
    ),
    responses=error_responses(
        (422, "api_key_name_invalid"),
        (422, "api_key_scopes_invalid"),
        (409, "api_key_name_taken"),
        (409, "api_key_limit"),
    ),
)
def create_key(payload: ApiKeyIn, db: DbSession) -> ApiKeyCreated:
    name = api_keys.clean_name(payload.name)
    if name is None:
        raise error(
            "api_key_name_invalid",
            f"A key needs a name of 1 to {api_keys.NAME_MAX_LENGTH} characters.",
            422,
            max=api_keys.NAME_MAX_LENGTH,
        )
    scopes = api_keys.clean_scopes(list(payload.scopes))
    if scopes is None:
        raise error("api_key_scopes_invalid", "A key needs at least one scope.", 422)
    if _name_taken(db, name, None):
        raise error("api_key_name_taken", "Another key already has this name.", 409)
    if int(db.scalar(select(func.count(ApiKey.id))) or 0) >= api_keys.KEYS_MAX:
        raise error("api_key_limit", f"There are {api_keys.KEYS_MAX} keys already.", 409, max=api_keys.KEYS_MAX)
    row, key = api_keys.create(db, name, scopes, utcnow())
    db.commit()
    logger.info("API key %d made, scopes %s", row.id, ",".join(scopes))
    return ApiKeyCreated(**_out(row).model_dump(), key=key)


@router.patch(
    "/{key_id}",
    response_model=ApiKeyOut,
    summary="Rename an API key",
    description="Gives the key another name. The key itself and its scopes stay; for other scopes make a new key.",
    responses=error_responses((404, "api_key_not_found"), (422, "api_key_name_invalid"), (409, "api_key_name_taken")),
)
def rename_key(key_id: int, payload: ApiKeyPatch, db: DbSession) -> ApiKeyOut:
    row = db.get(ApiKey, key_id)
    if row is None:
        raise error("api_key_not_found", "This key does not exist, or not any more.", 404)
    name = api_keys.clean_name(payload.name)
    if name is None:
        raise error(
            "api_key_name_invalid",
            f"A key needs a name of 1 to {api_keys.NAME_MAX_LENGTH} characters.",
            422,
            max=api_keys.NAME_MAX_LENGTH,
        )
    if _name_taken(db, name, row.id):
        raise error("api_key_name_taken", "Another key already has this name.", 409)
    row.name = name
    db.commit()
    return _out(row)


@router.delete(
    "/{key_id}",
    status_code=204,
    summary="Revoke an API key",
    description="The key stops working at once and for good. Whatever used it gets 401 `api_key_invalid` from now on.",
    responses=error_responses((404, "api_key_not_found")),
)
def revoke_key(key_id: int, db: DbSession) -> Response:
    row = db.get(ApiKey, key_id)
    if row is None:
        raise error("api_key_not_found", "This key does not exist, or not any more.", 404)
    db.delete(row)
    db.commit()
    api_keys.forget(key_id)
    logger.info("API key %d revoked", key_id)
    return Response(status_code=204)
