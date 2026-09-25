"""Webhooks to the outside for other programs (N32). Nexview needs none: the event stream is
its way back. A receiver that cannot keep a stream open gets the same events pushed.

**The owner's latch:** only the owner makes a target, in the interface; no key reaches this. **Signed:** every
delivery carries ``X-Nexcrate-Signature: t=<unix time>,v1=<HMAC-SHA256 of "<t>.<body>" with the target's secret>``,
so the receiver knows it comes from this nexcrate and is fresh. **Tried again** after 1, 5, 30 and 120 minutes, then
given up and noted. The last ``KEEP`` deliveries of each target stay for the interface.

A job takes the events of the feed into deliveries (never one twice, none skipped: the target's ``cursor``) and sends
what is due. A new target starts at the newest event: no backlog lands on a receiver that was just set up.

⚠️ The target's address may carry a token of the receiver: the log names its origin only.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as OrmSession

from .. import __version__, crypto
from .. import db as database
from ..models import ApiEvent, Webhook, WebhookDelivery, utcnow
from . import http_log
from .api_v1 import events

logger = logging.getLogger("nexcrate.webhooks")

JOB_NAME = "webhooks"
INTERVAL_SECONDS = 10
#: The waits before the second to fifth try; after the fifth, a delivery is given up.
BACKOFF = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30), timedelta(minutes=120))
KEEP = 50
#: Events taken over per target and run, and deliveries sent per run.
TAKE_MAX = 200
SEND_MAX = 50
TIMEOUT = httpx.Timeout(10.0, connect=5.0)
TARGETS_MAX = 20
NAME_MAX_LENGTH = 100
URL_MAX_LENGTH = 1024


def new_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


def check_url(raw: str) -> str:
    """``http(s)://host…``; raises ``ValueError`` for anything else."""
    value = (raw or "").strip()
    parts = urlsplit(value)
    if len(value) > URL_MAX_LENGTH or parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("not an address")
    return value


def signature(secret: str, stamp: int, body: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), f"{stamp}.{body}".encode(), hashlib.sha256).hexdigest()
    return f"t={stamp},v1={digest}"


def _event_body(item: dict[str, Any]) -> str:
    from ..routers.v1_back import EventOut  # the answer's own shape, so a delivery equals /api/v1/events

    return json.dumps(EventOut.model_validate(item).model_dump(mode="json"), separators=(",", ":"))


def take_events(db: OrmSession) -> int:
    """New events into deliveries, per target from its cursor on. Returns how many deliveries were made; commits."""
    made = 0
    now = utcnow()
    targets = list(db.scalars(select(Webhook).where(Webhook.enabled.is_(True))))
    if not targets:
        return 0
    from .api_v1 import versions as v1_versions

    public = v1_versions.public_ids(db)
    for target in targets:
        rows = list(
            db.scalars(select(ApiEvent).where(ApiEvent.seq > target.cursor).order_by(ApiEvent.seq).limit(TAKE_MAX))
        )
        if not rows:
            continue
        wanted = set(target.types or [])
        for item in events.render(rows, public):
            if wanted and item["type"] not in wanted:
                continue
            db.add(
                WebhookDelivery(
                    webhook_id=target.id,
                    event_seq=item["seq"],
                    event_type=item["type"],
                    body=_event_body(item),
                    state="pending",
                    next_at=now,
                    created_at=now,
                )
            )
            made += 1
        target.cursor = rows[-1].seq
    db.commit()
    return made


async def send_one(delivery_id: int) -> WebhookDelivery | None:
    """Send one delivery now and note the outcome; returns it as it stands afterwards."""
    with database.SessionLocal() as db:
        delivery = db.get(WebhookDelivery, delivery_id)
        target = db.get(Webhook, delivery.webhook_id) if delivery is not None else None
        if delivery is None or target is None:
            return None
        url, secret, body, kind = target.url, crypto.decrypt(target.secret_enc), delivery.body, delivery.event_type
    stamp = int(time.time())
    headers = {
        "Content-Type": "application/json",
        "User-Agent": f"nexcrate/{__version__}",
        "X-Nexcrate-Event": kind,
        "X-Nexcrate-Delivery": str(delivery_id),
        "X-Nexcrate-Signature": signature(secret, stamp, body),
    }
    status: int | None = None
    problem: str | None = None
    async with http_log.client("webhook", timeout=TIMEOUT, origin_only=True, log_bodies=False) as http:
        try:
            answer = await http_log.send(http, "POST", url, content=body.encode("utf-8"), headers=headers)
            status = answer.status_code
            if not 200 <= status < 300:
                problem = f"http_{status}"
        except httpx.TimeoutException:
            problem = "timeout"
        except httpx.HTTPError:
            problem = "unreachable"
    now = utcnow()
    with database.SessionLocal() as db:
        delivery = db.get(WebhookDelivery, delivery_id)
        if delivery is None:
            return None
        delivery.attempts += 1
        delivery.status_code, delivery.error_code = status, problem
        if problem is None:
            delivery.state, delivery.next_at, delivery.done_at = "delivered", None, now
        elif delivery.attempts > len(BACKOFF) or delivery.event_seq is None:
            # A test is tried once; an event after its last wait.
            delivery.state, delivery.next_at, delivery.done_at = "failed", None, now
            logger.info("Webhook %d: delivery %d given up (%s)", delivery.webhook_id, delivery.id, problem)
        else:
            delivery.next_at = now + BACKOFF[delivery.attempts - 1]
        db.commit()
        db.refresh(delivery)
        db.expunge(delivery)
        return delivery


def _prune(db: OrmSession) -> None:
    for target_id in db.scalars(select(Webhook.id)):
        keep = list(
            db.scalars(
                select(WebhookDelivery.id)
                .where(WebhookDelivery.webhook_id == target_id)
                .order_by(WebhookDelivery.id.desc())
                .limit(KEEP)
            )
        )
        if keep:
            db.execute(
                delete(WebhookDelivery).where(
                    WebhookDelivery.webhook_id == target_id,
                    WebhookDelivery.id < min(keep),
                    WebhookDelivery.state != "pending",
                )
            )
    db.commit()


async def run_job() -> None:
    with database.SessionLocal() as db:
        take_events(db)
        due = list(
            db.scalars(
                select(WebhookDelivery.id)
                .join(Webhook, Webhook.id == WebhookDelivery.webhook_id)
                .where(
                    Webhook.enabled.is_(True),
                    WebhookDelivery.state == "pending",
                    WebhookDelivery.next_at <= utcnow(),
                )
                .order_by(WebhookDelivery.id)
                .limit(SEND_MAX)
            )
        )
    for delivery_id in due:
        await send_one(delivery_id)
    if due:
        with database.SessionLocal() as db:
            _prune(db)


def make(db: OrmSession, name: str, url: str, types: list[str]) -> tuple[Webhook, str]:
    """A new target starting at the newest event, and its secret, shown once. Commits."""
    secret = new_secret()
    row = Webhook(
        name=name,
        url=url,
        secret_enc=crypto.encrypt(secret),
        types=sorted(set(types)),
        enabled=True,
        cursor=events.latest(db),
        created_at=utcnow(),
    )
    db.add(row)
    db.commit()
    logger.info("Webhook %d made", row.id)
    return row, secret


def test_delivery(db: OrmSession, target: Webhook) -> int:
    """A test delivery of the target, not yet sent. Commits; returns its id."""
    body = json.dumps(
        {"seq": 0, "type": "test", "at": utcnow().isoformat(), "title": None, "version_id": None, "origin": None,
         "download_id": None, "params": {"message": "A test from nexcrate."}},
        separators=(",", ":"),
    )  # fmt: skip
    delivery = WebhookDelivery(
        webhook_id=target.id, event_seq=None, event_type="test", body=body, state="pending", next_at=None
    )
    db.add(delivery)
    db.commit()
    return delivery.id

