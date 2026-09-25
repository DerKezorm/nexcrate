"""Webhooks to the outside, set up by the owner (N32): targets, their deliveries, a test."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .. import crypto
from ..db import SessionLocal
from ..deps import DbSession
from ..meldungen import error, error_responses
from ..models import Webhook, WebhookDelivery
from ..services import webhooks
from ..services.api_keys import clean_name
from ..services.api_v1 import events

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


class DeliveryOut(BaseModel):
    id: int
    event_seq: int | None = Field(description="The event's number; null for a test.")
    event_type: str
    state: str = Field(description="pending, delivered or failed.")
    attempts: int
    next_at: datetime | None
    status_code: int | None
    error_code: str | None = Field(description="timeout, unreachable or http_<status>.")
    created_at: datetime
    done_at: datetime | None


class WebhookOut(BaseModel):
    id: int
    name: str
    url: str
    types: list[str] = Field(description="The event types sent; empty for every type.")
    enabled: bool
    created_at: datetime
    last: DeliveryOut | None = Field(description="The newest delivery.")
    failed: int = Field(description="Deliveries given up among those kept.")


class WebhookListOut(BaseModel):
    items: list[WebhookOut]
    types: list[str] = Field(description="Every event type there is.")


class WebhookCreatedOut(BaseModel):
    webhook: WebhookOut
    secret: str = Field(description="Signs every delivery. Shown this once.")


class WebhookIn(BaseModel):
    name: str = Field(min_length=1, max_length=webhooks.NAME_MAX_LENGTH)
    url: str = Field(min_length=1, max_length=webhooks.URL_MAX_LENGTH)
    types: list[str] = Field(default_factory=list, max_length=len(events.TYPES))


class WebhookChangeIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=webhooks.NAME_MAX_LENGTH)
    url: str | None = Field(default=None, min_length=1, max_length=webhooks.URL_MAX_LENGTH)
    types: list[str] | None = Field(default=None, max_length=len(events.TYPES))
    enabled: bool | None = None


class DeliveryListOut(BaseModel):
    items: list[DeliveryOut] = Field(description="Newest first.")


def _out(db: DbSession, row: Webhook) -> WebhookOut:
    last = db.scalar(
        select(WebhookDelivery).where(WebhookDelivery.webhook_id == row.id).order_by(WebhookDelivery.id.desc())
    )
    failed = db.scalar(
        select(func.count())
        .select_from(WebhookDelivery)
        .where(WebhookDelivery.webhook_id == row.id, WebhookDelivery.state == "failed")
    )
    return WebhookOut(
        id=row.id,
        name=row.name,
        url=row.url,
        types=list(row.types or []),
        enabled=bool(row.enabled),
        created_at=row.created_at,
        last=DeliveryOut.model_validate(last, from_attributes=True) if last is not None else None,
        failed=int(failed or 0),
    )


def _checked(name: str | None, url: str | None, types: list[str] | None) -> tuple[str | None, str | None]:
    fields = []
    clean = clean_name(name) if name is not None else None
    if name is not None and clean is None:
        fields.append("name")
    address = None
    if url is not None:
        try:
            address = webhooks.check_url(url)
        except ValueError:
            fields.append("url")
    if types is not None and any(item not in events.TYPES for item in types):
        fields.append("types")
    if fields:
        raise error("invalid_input", "The input is not valid.", 422, fields=fields)
    return clean, address


def _target(db: DbSession, webhook_id: int) -> Webhook:
    row = db.get(Webhook, webhook_id)
    if row is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    return row


@router.get(
    "",
    response_model=WebhookListOut,
    summary="List the webhooks",
    description="Every target with its newest delivery. Never a secret.",
)
def list_webhooks(db: DbSession) -> WebhookListOut:
    rows = list(db.scalars(select(Webhook).order_by(Webhook.id)))
    return WebhookListOut(items=[_out(db, row) for row in rows], types=list(events.TYPES))


@router.post(
    "",
    status_code=201,
    response_model=WebhookCreatedOut,
    summary="Make a webhook",
    description=(
        "A target for the events of /api/v1, starting at the newest: no backlog. Its secret signs every delivery "
        "and is shown this once."
    ),
    responses=error_responses((422, "invalid_input"), (409, "webhook_limit")),
)
def make_webhook(payload: WebhookIn, db: DbSession) -> WebhookCreatedOut:
    name, url = _checked(payload.name, payload.url, payload.types)
    if int(db.scalar(select(func.count(Webhook.id))) or 0) >= webhooks.TARGETS_MAX:
        raise error(
            "webhook_limit", f"There are {webhooks.TARGETS_MAX} webhooks already.", 409, max=webhooks.TARGETS_MAX
        )
    row, secret = webhooks.make(db, name or "", url or "", list(payload.types))
    return WebhookCreatedOut(webhook=_out(db, row), secret=secret)


@router.patch(
    "/{webhook_id}",
    response_model=WebhookOut,
    summary="Change a webhook",
    description="Name, address, event types, or switch it off; off, nothing is sent and nothing piles up.",
    responses=error_responses((404, "not_found"), (422, "invalid_input")),
)
def change_webhook(webhook_id: int, payload: WebhookChangeIn, db: DbSession) -> WebhookOut:
    row = _target(db, webhook_id)
    name, url = _checked(payload.name, payload.url, payload.types)
    if name is not None:
        row.name = name
    if url is not None:
        row.url = url
    if payload.types is not None:
        row.types = sorted(set(payload.types))
    if payload.enabled is not None and payload.enabled != row.enabled:
        row.enabled = payload.enabled
        if payload.enabled:
            # Switched on again it starts at the newest event, as a new target.
            row.cursor = events.latest(db)
    db.commit()
    return _out(db, row)


@router.delete(
    "/{webhook_id}",
    status_code=204,
    response_model=None,
    summary="Remove a webhook",
    description="With its deliveries.",
    responses=error_responses((404, "not_found")),
)
def remove_webhook(webhook_id: int, db: DbSession) -> None:
    db.delete(_target(db, webhook_id))
    db.commit()


@router.post(
    "/{webhook_id}/secret",
    response_model=WebhookCreatedOut,
    summary="Make a new secret",
    description="The old one stops signing at once; the new one is shown this once.",
    responses=error_responses((404, "not_found")),
)
def new_secret(webhook_id: int, db: DbSession) -> WebhookCreatedOut:
    row = _target(db, webhook_id)
    secret = webhooks.new_secret()
    row.secret_enc = crypto.encrypt(secret)
    db.commit()
    return WebhookCreatedOut(webhook=_out(db, row), secret=secret)


@router.get(
    "/{webhook_id}/deliveries",
    response_model=DeliveryListOut,
    summary="The deliveries of a webhook",
    description=f"The last {webhooks.KEEP}, newest first.",
    responses=error_responses((404, "not_found")),
)
def list_deliveries(webhook_id: int, db: DbSession) -> DeliveryListOut:
    _target(db, webhook_id)
    rows = db.scalars(
        select(WebhookDelivery)
        .where(WebhookDelivery.webhook_id == webhook_id)
        .order_by(WebhookDelivery.id.desc())
        .limit(webhooks.KEEP)
    )
    return DeliveryListOut(items=[DeliveryOut.model_validate(row, from_attributes=True) for row in rows])


@router.post(
    "/{webhook_id}/test",
    response_model=DeliveryOut,
    summary="Send a test",
    description="A test event, sent at once and tried once; the answer says how it went.",
    responses=error_responses((404, "not_found")),
)
async def send_test(webhook_id: int) -> DeliveryOut:
    with SessionLocal() as db:
        delivery_id = webhooks.test_delivery(db, _target(db, webhook_id))
    sent = await webhooks.send_one(delivery_id)
    if sent is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    return DeliveryOut.model_validate(sent, from_attributes=True)
