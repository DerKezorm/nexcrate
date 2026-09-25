"""Usenet retention, read from the Usenet clients' own news servers instead of asked of the owner (25.09.2026).

Radarr and Sonarr have a setting "Retention" (days, 0 for unlimited) and reject a Usenet release older than that
(``RetentionSpecification``): the news server would not have its articles any more, the download would fail. nexcrate
reads the number where it lives already:

* **SABnzbd**: ``mode=get_config&section=servers``, every server with ``enable`` and ``retention`` in days, 0 for
  unlimited (measured on SABnzbd 5.1.3: set to 1200, read back as the number 1200).
* **NZBGet**: its configuration, ``ServerN.Active`` and ``ServerN.Retention`` in days, 0 for unlimited (26.3).

A release is on the servers when any active server of any enabled Usenet client still has it, so the longest retention
counts (``downloaders.longest_retention``), and one server without a limit, one client that could not be read or one
without an active server means no limit at all: nexcrate rather loads a release that fails (and then looks for a
replacement) than leaves out one that would have worked.

The result lives in the setting ``usenet_retention``, with the days per client for the page of download clients. The
job looks every ``INTERVAL_SECONDS`` and reads again when the Usenet clients changed or ``REFRESH_SECONDS`` passed; the
rule itself is ``search.ranking.retention_rejection``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import crypto
from ...db import SessionLocal, get_setting, set_setting
from ...models import DownloadClient, utcnow
from .. import downloaders

logger = logging.getLogger("nexcrate.downloads")

JOB_NAME = "usenet_retention"
SETTING = "usenet_retention"
INTERVAL_SECONDS = 300
REFRESH_SECONDS = 3600
#: A client whose servers could not be read.
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Retention:
    #: The days nexcrate keeps to; None for no limit.
    days: int | None = None
    #: Per client id: its days, None for unlimited, ``UNKNOWN`` when it could not be read.
    clients: dict[int, int | str | None] = field(default_factory=dict)
    read_at: datetime | None = None
    #: Which Usenet clients the reading was of: a fingerprint of their rows (``_clients``).
    of: str = ""


def read(db: Session) -> Retention:
    try:
        data = json.loads(get_setting(db, SETTING, "") or "{}")
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        return Retention()
    days = data.get("days")
    clients = data.get("clients")
    read_at = data.get("read_at")
    try:
        moment = datetime.fromisoformat(read_at) if isinstance(read_at, str) else None
    except ValueError:
        moment = None
    per_client = clients.items() if isinstance(clients, dict) else ()
    return Retention(
        days=days if isinstance(days, int) and not isinstance(days, bool) and days > 0 else None,
        clients={int(key): value for key, value in per_client if str(key).isdigit()},
        read_at=moment,
        of=str(data.get("of") or ""),
    )


def days(db: Session | None) -> int | None:
    """The retention nexcrate keeps to, or None for no limit."""
    if db is None:
        with SessionLocal() as own:
            return read(own).days
    return read(db).days


def _clients() -> tuple[str, list[tuple[int, downloaders.Target]]]:
    with SessionLocal() as db:
        rows = list(
            db.scalars(
                select(DownloadClient)
                .where(DownloadClient.enabled.is_(True))
                .where(DownloadClient.kind.in_([kind for kind in downloaders.KINDS if downloaders.is_usenet(kind)]))
                .order_by(DownloadClient.id)
            )
        )
        seen = [(row.id, row.kind, row.url, row.username or "", row.secret or "") for row in rows]
        targets = [
            (
                row.id,
                downloaders.Target(
                    kind=row.kind,
                    url=row.url,
                    username=row.username or "",
                    secret=crypto.decrypt(row.secret) if row.secret else "",
                    category=row.category,
                ),
            )
            for row in rows
        ]
    return hashlib.sha256(json.dumps(seen).encode()).hexdigest()[:16], targets


async def _read_client(target: downloaders.Target) -> int | str | None:
    try:
        async with downloaders.open_client(target) as client:
            return await client.usenet_retention()
    except downloaders.ClientError as exc:
        logger.info("The retention of a %s could not be read: %s", target.kind, exc.code)
        return UNKNOWN


async def refresh(*, only_when_due: bool = False) -> Retention:
    """Read every enabled Usenet client again and store the result. With ``only_when_due`` nothing is asked unless the
    clients changed or the last reading is older than ``REFRESH_SECONDS``."""
    fingerprint, targets = await asyncio.to_thread(_clients)
    with SessionLocal() as db:
        known = read(db)
    now = utcnow()
    if (
        only_when_due
        and known.of == fingerprint
        and known.read_at is not None
        and now - known.read_at < timedelta(seconds=REFRESH_SECONDS)
    ):
        return known
    clients: dict[int, int | str | None] = {}
    for client_id, target in targets:
        clients[client_id] = await _read_client(target)
    found = downloaders.longest_retention([value if isinstance(value, int) else None for value in clients.values()])
    result = Retention(days=found, clients=clients, read_at=now, of=fingerprint)

    def store() -> None:
        with SessionLocal() as db:
            payload = {
                "days": result.days,
                "clients": {str(key): value for key, value in result.clients.items()},
                "read_at": now.isoformat(),
                "of": fingerprint,
            }
            set_setting(db, SETTING, json.dumps(payload))
            db.commit()

    await asyncio.to_thread(store)
    if known.days != result.days or known.of != fingerprint:
        logger.info(
            "Usenet retention read from %d clients: %s", len(clients), f"{found} days" if found else "no limit"
        )
    return result


def run_job() -> None:
    asyncio.run(refresh(only_when_due=True))
