"""The tiles: read, fill, show (taken over from Nexview's ``channel_targets``).

Secrets lie encrypted in the table and leave the backend never, not even masked: an answer says ``<field>_set``. An
empty secret field when saving means "unchanged", so a form that never saw the token cannot wipe it.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ... import crypto
from ...models import NotificationMessage, NotificationTarget
from . import CHANNELS, child_fields, fields, has_children, parent_fields, secrets_of

#: Longest value of a field; the columns are longer, a form needs no more.
FIELD_MAX_LENGTH = 1024


def is_leaf(target: NotificationTarget) -> bool:
    """Does this tile receive messages? A topic, a chat, an address, or a one-level service."""
    return target.parent_id is not None or not has_children(target.channel)


def level_fields(target: NotificationTarget) -> tuple[str, ...]:
    if target.parent_id is not None:
        return child_fields(target.channel)
    return parent_fields(target.channel)


def _own(target: NotificationTarget) -> dict[str, str]:
    hidden = secrets_of(target.channel)
    found: dict[str, str] = {}
    for name in level_fields(target):
        raw = str(getattr(target, name, "") or "")
        found[name] = crypto.decrypt(raw) if name in hidden and raw else raw
    return found


def values(db: OrmSession, target: NotificationTarget) -> dict[str, str]:
    """The whole connection in plain text: the parent's fields and the target's own."""
    found = dict.fromkeys(fields(target.channel), "")
    if target.parent_id is not None:
        parent = db.get(NotificationTarget, target.parent_id)
        if parent is not None:
            found.update(_own(parent))
    found.update(_own(target))
    return found


def draft_values(
    db: OrmSession,
    kind: str,
    draft: dict[str, str],
    *,
    target: NotificationTarget | None = None,
    parent: NotificationTarget | None = None,
) -> dict[str, str]:
    """The values a check or test runs with: what is stored, overwritten by the form; an empty secret stays."""
    found = dict.fromkeys(fields(kind), "")
    if target is not None:
        found.update(values(db, target))
    elif parent is not None:
        found.update(values(db, parent))
    hidden = secrets_of(kind)
    for name in fields(kind):
        if name not in draft:
            continue
        text = str(draft[name]).strip() if name not in ("password",) else str(draft[name])
        if name in hidden and not text:
            continue
        found[name] = text
    return found


def apply(target: NotificationTarget, draft: dict[str, str]) -> None:
    """Take over the fields of the target's own level; an empty secret stays as it was."""
    hidden = secrets_of(target.channel)
    for name in level_fields(target):
        if name not in draft:
            continue
        text = str(draft[name]) if name == "password" else str(draft[name]).strip()
        if name in hidden:
            if not text:
                continue
            text = crypto.encrypt(text)
        setattr(target, name, text)


def missing(kind: str, values_: dict[str, str], *, child: bool) -> list[str]:
    from . import required

    return [name for name in required(kind, child) if not values_.get(name)]


def upper(db: OrmSession, kind: str) -> list[NotificationTarget]:
    return list(
        db.scalars(
            select(NotificationTarget)
            .where(NotificationTarget.channel == kind, NotificationTarget.parent_id.is_(None))
            .order_by(NotificationTarget.created_at, NotificationTarget.id)
        )
    )


def children(db: OrmSession, target: NotificationTarget) -> list[NotificationTarget]:
    return list(
        db.scalars(
            select(NotificationTarget)
            .where(NotificationTarget.parent_id == target.id)
            .order_by(NotificationTarget.created_at, NotificationTarget.id)
        )
    )


def _last(db: OrmSession, target: NotificationTarget, state: str) -> NotificationMessage | None:
    return db.scalars(
        select(NotificationMessage)
        .where(NotificationMessage.target_id == target.id, NotificationMessage.state == state)
        .order_by(NotificationMessage.done_at.desc(), NotificationMessage.id.desc())
        .limit(1)
    ).first()


def as_json(db: OrmSession, target: NotificationTarget) -> dict[str, Any]:
    """A tile for the interface: plain fields, ``<secret>_set`` for secrets, the children, and how the last message
    went, because a mailbox nothing reaches any more looks like one with nothing to report."""
    hidden = secrets_of(target.channel)
    found: dict[str, Any] = {
        "id": target.id,
        "channel": target.channel,
        "parent_id": target.parent_id,
        "name": target.name,
        "enabled": target.enabled,
        "verified": target.verified,
        "events": dict(target.events or {}),
        "created_at": target.created_at,
        "fields": {},
        "secrets_set": {},
    }
    for name in level_fields(target):
        raw = str(getattr(target, name, "") or "")
        if name in hidden:
            found["secrets_set"][name] = bool(raw)
        else:
            found["fields"][name] = raw
    failed = _last(db, target, "failed")
    delivered = _last(db, target, "delivered")
    newer_failure = failed is not None and (delivered is None or (failed.done_at or failed.created_at) > (
        delivered.done_at or delivered.created_at
    ))
    found["last_error"] = (
        {"code": failed.error_code, "params": dict(failed.error_params or {}), "at": failed.done_at}
        if newer_failure and failed is not None
        else None
    )
    found["last_delivered_at"] = delivered.done_at if delivered is not None else None
    found["children"] = (
        [as_json(db, child) for child in children(db, target)]
        if target.parent_id is None and has_children(target.channel)
        else []
    )
    return found


def kinds() -> list[dict[str, Any]]:
    """What the interface needs to know of each service: its fields per level, secrets, required ones, the code."""
    from . import required, requires_code

    return [
        {
            "channel": kind,
            "label": module.LABEL,
            "parent_fields": list(parent_fields(kind)),
            "child_fields": list(child_fields(kind)),
            "parent_required": list(required(kind, False)),
            "child_required": list(required(kind, True)),
            "secrets": list(secrets_of(kind)),
            "requires_code": requires_code(kind),
            "chats": hasattr(module, "chats"),
        }
        for kind, module in CHANNELS.items()
    ]
