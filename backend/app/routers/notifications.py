"""Notifications to the owner's own services: the tiles per service, check, test with
a code, confirm, save, switch, remove.

⚠️ Secrets (a password, a token, a Discord address) are stored encrypted and never returned, not even masked: a tile says
``secrets_set``. An empty secret when saving means "unchanged". A mailbox (a topic, a chat, an address, a one-level
service) is saved only after its test message was confirmed with exactly the connection being saved: the code for push
services, the accepted test mail for e-mail. It starts at the newest event, so nothing old lands on it.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete

from ..db import SessionLocal
from ..meldungen import error, error_responses, meldung
from ..models import NotificationMessage, NotificationTarget, utcnow
from ..services import notify
from ..services.notify import outbox, targets, texts, verify
from ..services.notify.base import LEVELS, ChannelError
from ..services.schreibweisen import nfc

logger = logging.getLogger("nexcrate.notify")

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

Channel = Literal["ntfy", "gotify", "telegram", "discord", "webhook", "apprise", "email"]
NAME_MAX_LENGTH = 100
TARGETS_MAX = 50

#: English fallbacks of the codes a service can answer with; the interface translates by code.
MESSAGES: dict[str, str] = {
    "notify_url_invalid": "The address has to start with http:// or https://.",
    "notify_auth_rejected": "{host} refused the sign-in (HTTP {status}). Check the credentials.",
    "notify_not_found": "{host} does not know this address (HTTP 404). Check address and path.",
    "notify_too_large": "{host} finds the message too large (HTTP 413).",
    "notify_http_error": "{host} answered with HTTP {status}.",
    "notify_timeout": "{host} did not answer in time.",
    "notify_unreachable": "{host} cannot be reached. Is the service running, is the port right?",
    "notify_bad_answer": "{host} gave no answer nexcrate can read.",
    "notify_wrong_service": "No {service} answers at this address.",
    "notify_field_missing": "The field {field} is empty.",
    "telegram_token_rejected": "Telegram refused the bot token.",
    "telegram_rejected": "Telegram refused the message. {reason}",
    "apprise_no_config": "Apprise holds no configuration under this key.",
    "apprise_partial": "Apprise could not deliver to at least one of its services.",
    "mail_tls": "The encrypted connection to the mail server failed. Does the encryption fit the port?",
    "mail_timeout": "{host}:{port} did not answer in time.",
    "mail_refused": "{host}:{port} takes no connection. Is the port right?",
    "mail_dns": "The server name {host} cannot be resolved.",
    "mail_unreachable": "The mail server {host}:{port} cannot be reached.",
    "mail_auth": "The mail server refused the sign-in. Are user name and password right?",
    "mail_recipient_refused": "The mail server refused the recipient.",
    "mail_sender_refused": "The mail server refused the sender address.",
    "mail_rejected": "The mail server refused the mail.",
    "mail_address_invalid": "This is no valid e-mail address.",
}
#: For the OpenAPI ``responses`` of every route that talks to a service.
CHANNEL_ERRORS = tuple((502, code) for code in MESSAGES)


class Draft(BaseModel):
    fields: dict[str, str] = Field(default_factory=dict, description="The service's fields; see GET for which exist.")
    target_id: int | None = Field(
        default=None, description="The tile being edited: what the form leaves out comes from there."
    )
    parent_id: int | None = Field(default=None, description="The instance a new topic, chat or address belongs to.")


class SaveIn(BaseModel):
    name: str = Field(max_length=NAME_MAX_LENGTH)
    fields: dict[str, str] = Field(default_factory=dict)
    events: dict[str, str] = Field(default_factory=dict, description="Event key to level; a key left out is off.")
    parent_id: int | None = Field(default=None, description="Makes a topic, chat or address below this instance.")


class CodeIn(BaseModel):
    code: str = Field(max_length=8)


class EnabledIn(BaseModel):
    enabled: bool


class LastError(BaseModel):
    code: str | None
    params: dict[str, Any]
    at: datetime | None


class TargetOut(BaseModel):
    id: int
    channel: str
    parent_id: int | None
    name: str
    enabled: bool
    verified: bool
    events: dict[str, str]
    fields: dict[str, str] = Field(description="The plain fields of this level. Secrets are never returned.")
    secrets_set: dict[str, bool] = Field(description="Per secret field of this level: whether one is stored.")
    last_error: LastError | None = Field(description="The last message given up, when no delivery came after it.")
    last_delivered_at: datetime | None
    created_at: datetime
    children: list[TargetOut] = Field(description="Topics, chats or addresses below an instance.")


class ServiceOut(BaseModel):
    channel: str
    label: str
    parent_fields: list[str]
    child_fields: list[str]
    parent_required: list[str]
    child_required: list[str]
    secrets: list[str]
    requires_code: bool
    chats: bool


class GroupOut(BaseModel):
    group: str
    events: list[str]


class OverviewOut(BaseModel):
    services: list[ServiceOut]
    groups: list[GroupOut]
    levels: list[str]


class CheckOut(BaseModel):
    found: dict[str, str] = Field(description="What the service told about itself, such as a Telegram bot's name.")


class TestOut(BaseModel):
    requires_code: bool = Field(description="Whether the code in the test message has to be typed back.")
    confirmed: bool = Field(description="True for e-mail: the accepted test mail is the proof.")


class ChatsOut(BaseModel):
    chats: list[dict[str, str]]


# --- Helpers ------------------------------------------------------------------------------------------------------- #


def _not_found() -> Exception:
    return error("notify_target_not_found", "This notification target does not exist.", 404)


def _invalid(*names: str) -> Exception:
    return error("invalid_input", "The input is not valid.", 422, fields=list(names))


def _channel_error(exc: ChannelError) -> Exception:
    template = MESSAGES.get(exc.code, exc.code)
    try:
        message = template.format(**exc.params)
    except (KeyError, IndexError):
        message = template
    return HTTPException(status_code=502, detail=meldung(exc.code, message.strip(), **exc.params))


def _clean_fields(kind: str, raw: dict[str, str]) -> dict[str, str]:
    allowed = set(notify.fields(kind))
    unknown = [name for name in raw if name not in allowed]
    if unknown:
        raise _invalid(*unknown)
    cleaned = {}
    for name, value in raw.items():
        text = nfc(str(value))
        if len(text) > targets.FIELD_MAX_LENGTH or any(ord(char) < 32 for char in text):
            raise _invalid(name)
        cleaned[name] = text
    return cleaned


def _clean_events(raw: dict[str, str]) -> dict[str, str]:
    bad = [key for key, level in raw.items() if key not in texts.EVENT_KEYS or level not in LEVELS]
    if bad:
        raise _invalid("events")
    return {key: raw[key] for key in texts.EVENT_KEYS if key in raw}


def _clean_name(raw: str) -> str:
    name = nfc(raw).strip()
    if not name or len(name) > NAME_MAX_LENGTH or any(ord(char) < 32 for char in name):
        raise _invalid("name")
    return name


def _load(kind: str, target_id: int | None, parent_id: int | None) -> tuple[dict[str, Any], bool]:
    """The stored values a draft builds on, and whether it is a mailbox (the lower level, or a one-level service)."""
    with SessionLocal() as db:
        target = db.get(NotificationTarget, target_id) if target_id is not None else None
        parent = db.get(NotificationTarget, parent_id) if parent_id is not None else None
        if (target_id is not None and (target is None or target.channel != kind)) or (
            parent_id is not None and (parent is None or parent.channel != kind or parent.parent_id is not None)
        ):
            raise _not_found()
        if parent is not None and not notify.has_children(kind):
            raise _invalid("parent_id")
        stored = targets.values(db, target) if target is not None else (targets.values(db, parent) if parent else {})
        leaf = (
            targets.is_leaf(target)
            if target is not None
            else (parent is not None or not notify.has_children(kind))
        )
        return stored, leaf


def _merged(kind: str, stored: dict[str, Any], draft: dict[str, str]) -> dict[str, str]:
    found = dict.fromkeys(notify.fields(kind), "")
    found.update(stored)
    hidden = notify.secrets_of(kind)
    for name, value in draft.items():
        if name in hidden and not value:
            continue
        found[name] = value if name == "password" else value.strip()
    return found


def _read(target_id: int) -> TargetOut:
    with SessionLocal() as db:
        target = db.get(NotificationTarget, target_id)
        if target is None:
            raise _not_found()
        return TargetOut.model_validate(targets.as_json(db, target))


# --- Routes -------------------------------------------------------------------------------------------------------- #


@router.get(
    "",
    response_model=OverviewOut,
    summary="The services and the events",
    description="Every service with its fields per level, which are secrets and required; the event keys in groups.",
)
def overview() -> OverviewOut:
    return OverviewOut(
        services=[ServiceOut.model_validate(item) for item in targets.kinds()],
        groups=[GroupOut(group=group, events=list(keys)) for group, keys in texts.GROUPS],
        levels=list(LEVELS),
    )


@router.get(
    "/{channel}/targets",
    response_model=list[TargetOut],
    summary="The tiles of a service",
    description="The upper level with its children. No secret is returned.",
)
def list_targets(channel: Channel) -> list[TargetOut]:
    with SessionLocal() as db:
        return [TargetOut.model_validate(targets.as_json(db, target)) for target in targets.upper(db, channel)]


@router.post(
    "/{channel}/check",
    response_model=CheckOut,
    summary="Check an instance",
    description=(
        "Whether the service answers at this address with these credentials. Sends nothing; for a Telegram bot it "
        "names the bot. Talks to the service."
    ),
    responses=error_responses((404, "notify_target_not_found"), (422, "invalid_input"), *CHANNEL_ERRORS),
)
async def check(channel: Channel, payload: Draft) -> CheckOut:
    draft = _clean_fields(channel, payload.fields)
    stored, _leaf = await asyncio.to_thread(_load, channel, payload.target_id, payload.parent_id)
    values = _merged(channel, stored, draft)
    missing = targets.missing(channel, values, child=False)
    if missing:
        raise _invalid(*missing)
    try:
        found = await notify.check(channel, notify.build(channel, values))
    except ChannelError as exc:
        raise _channel_error(exc) from exc
    return CheckOut(found=found or {})


@router.post(
    "/{channel}/test",
    response_model=TestOut,
    summary="Send a test message",
    description=(
        "Sends a test message with a four-digit code to the mailbox of this draft. The code has to be typed back "
        "before the mailbox can be saved; for e-mail the accepted test mail is enough. Talks to the service."
    ),
    responses=error_responses((404, "notify_target_not_found"), (422, "invalid_input"), *CHANNEL_ERRORS),
)
async def test(channel: Channel, payload: Draft) -> TestOut:
    draft = _clean_fields(channel, payload.fields)
    stored, leaf = await asyncio.to_thread(_load, channel, payload.target_id, payload.parent_id)
    if not leaf:
        raise _invalid("parent_id")
    values = _merged(channel, stored, draft)
    missing = targets.missing(channel, values, child=False) + (
        targets.missing(channel, values, child=True) if notify.has_children(channel) else []
    )
    if missing:
        raise _invalid(*missing)
    needs_code = notify.requires_code(channel)
    print_ = notify.fingerprint(channel, values)
    code = verify.start(channel, print_)
    try:
        notice = outbox.test_notice(values.get("language", "de"), code)
        await notify.send(channel, notify.build(channel, values), notice)
    except ChannelError as exc:
        verify.forget(channel)
        raise _channel_error(exc) from exc
    if not needs_code:
        verify.start(channel, print_, confirmed=True)
    return TestOut(requires_code=needs_code, confirmed=not needs_code)


@router.post(
    "/{channel}/confirm",
    response_model=TestOut,
    summary="Type back the code",
    description="The code of the last test message of this service. Five tries, ten minutes.",
    responses=error_responses(
        (422, "notify_code_wrong"), (422, "notify_code_missing"), (422, "notify_code_too_many")
    ),
)
def confirm(channel: Channel, payload: CodeIn) -> TestOut:
    problem = verify.confirm(channel, payload.code)
    if problem is not None:
        texts_of = {
            "notify_code_wrong": "The code is not right.",
            "notify_code_missing": "There is no code. Send a test message first.",
            "notify_code_too_many": "Too many wrong tries. Send a new test message.",
        }
        raise error(problem, texts_of[problem], 422)
    return TestOut(requires_code=notify.requires_code(channel), confirmed=True)


@router.post(
    "/{channel}/chats",
    response_model=ChatsOut,
    summary="The chats a Telegram bot knows",
    description="Who wrote to the bot lately (Telegram keeps it about a day). Telegram only. Talks to Telegram.",
    responses=error_responses((404, "notify_target_not_found"), (422, "invalid_input"), *CHANNEL_ERRORS),
)
async def list_chats(channel: Channel, payload: Draft) -> ChatsOut:
    if not notify.can_list_chats(channel):
        raise _invalid("channel")
    draft = _clean_fields(channel, payload.fields)
    stored, _leaf = await asyncio.to_thread(_load, channel, payload.target_id, payload.parent_id)
    values = _merged(channel, stored, draft)
    try:
        found = await notify.chats(channel, notify.build(channel, values))
    except ChannelError as exc:
        raise _channel_error(exc) from exc
    return ChatsOut(chats=found)


def _save(
    kind: str, target_id: int | None, payload: SaveIn, name: str, draft: dict[str, str], events: dict[str, str]
) -> int:
    with SessionLocal() as db:
        if target_id is None:
            count = db.query(NotificationTarget).count()
            if count >= TARGETS_MAX:
                raise error("notify_too_many", f"At most {TARGETS_MAX} notification targets.", 409, limit=TARGETS_MAX)
            target = NotificationTarget(channel=kind, parent_id=payload.parent_id, created_at=utcnow())
            db.add(target)
        else:
            target = db.get(NotificationTarget, target_id)
            if target is None or target.channel != kind:
                raise _not_found()
        stored = targets.values(db, target) if target_id is not None else {}
        if target_id is None and payload.parent_id is not None:
            parent = db.get(NotificationTarget, payload.parent_id)
            stored = targets.values(db, parent) if parent is not None else {}
        values = _merged(kind, stored, draft)
        leaf = targets.is_leaf(target)
        missing = targets.missing(kind, values, child=False)
        if leaf and notify.has_children(kind):
            missing += targets.missing(kind, values, child=True)
        if missing:
            raise _invalid(*missing)
        if leaf:
            print_ = notify.fingerprint(kind, values)
            unchanged = target_id is not None and target.verified and notify.fingerprint(kind, stored) == print_
            if not unchanged and not verify.confirmed_for(kind, print_):
                raise error(
                    "notify_not_confirmed",
                    "Send a test message and type back its code before saving this connection.",
                    409,
                )
            if not unchanged or not target.verified:
                target.verified = True
                outbox.start_at_newest(db, target)
            target.events = events
        target.name = name
        targets.apply(target, draft)
        db.commit()
        if leaf:
            verify.forget(kind)
        return target.id


@router.post(
    "/{channel}/targets",
    status_code=201,
    response_model=TargetOut,
    summary="Add a tile",
    description=(
        "An instance (ntfy, Telegram bot, mail server) is checked first; a mailbox needs its test message confirmed "
        "with exactly this connection. A new mailbox starts at the newest event. Talks to the service for an instance."
    ),
    responses=error_responses(
        (404, "notify_target_not_found"),
        (409, "notify_not_confirmed"),
        (409, "notify_too_many"),
        (422, "invalid_input"),
        *CHANNEL_ERRORS,
    ),
)
async def create_target(channel: Channel, payload: SaveIn) -> TargetOut:
    name = _clean_name(payload.name)
    draft = _clean_fields(channel, payload.fields)
    events = _clean_events(payload.events)
    stored, leaf = await asyncio.to_thread(_load, channel, None, payload.parent_id)
    if not leaf:
        values = _merged(channel, stored, draft)
        missing = targets.missing(channel, values, child=False)
        if missing:
            raise _invalid(*missing)
        try:
            await notify.check(channel, notify.build(channel, values))
        except ChannelError as exc:
            raise _channel_error(exc) from exc
    target_id = await asyncio.to_thread(_save, channel, None, payload, name, draft, events)
    return await asyncio.to_thread(_read, target_id)


@router.put(
    "/{channel}/targets/{target_id}",
    response_model=TargetOut,
    summary="Change a tile",
    description=(
        "Name, fields and events. A changed connection of a mailbox needs a confirmed test message again; an instance "
        "is checked again. Talks to the service for an instance."
    ),
    responses=error_responses(
        (404, "notify_target_not_found"), (409, "notify_not_confirmed"), (422, "invalid_input"), *CHANNEL_ERRORS
    ),
)
async def update_target(channel: Channel, target_id: int, payload: SaveIn) -> TargetOut:
    name = _clean_name(payload.name)
    draft = _clean_fields(channel, payload.fields)
    events = _clean_events(payload.events)
    stored, leaf = await asyncio.to_thread(_load, channel, target_id, None)
    if not leaf:
        values = _merged(channel, stored, draft)
        try:
            await notify.check(channel, notify.build(channel, values))
        except ChannelError as exc:
            raise _channel_error(exc) from exc
    payload = payload.model_copy(update={"parent_id": None})
    await asyncio.to_thread(_save, channel, target_id, payload, name, draft, events)
    return await asyncio.to_thread(_read, target_id)


def _set_enabled(kind: str, target_id: int, enabled: bool) -> None:
    with SessionLocal() as db:
        target = db.get(NotificationTarget, target_id)
        if target is None or target.channel != kind:
            raise _not_found()
        target.enabled = enabled
        if enabled and targets.is_leaf(target):
            # Switched on again: what happened meanwhile is no news any more.
            outbox.start_at_newest(db, target)
        db.commit()


@router.put(
    "/{channel}/targets/{target_id}/enabled",
    response_model=TargetOut,
    summary="Switch a tile on or off",
    description="Off, a mailbox gets nothing; an instance switched off silences its mailboxes. On again, it starts at "
    "the newest event.",
    responses=error_responses((404, "notify_target_not_found")),
)
async def set_enabled(channel: Channel, target_id: int, payload: EnabledIn) -> TargetOut:
    await asyncio.to_thread(_set_enabled, channel, target_id, payload.enabled)
    return await asyncio.to_thread(_read, target_id)


@router.delete(
    "/{channel}/targets/{target_id}",
    status_code=204,
    response_class=Response,
    summary="Remove a tile",
    description="With its mailboxes and their messages.",
    responses=error_responses((404, "notify_target_not_found")),
)
def delete_target(channel: Channel, target_id: int) -> Response:
    with SessionLocal() as db:
        target = db.get(NotificationTarget, target_id)
        if target is None or target.channel != channel:
            raise _not_found()
        ids = [target.id, *(child.id for child in targets.children(db, target))]
        db.execute(delete(NotificationMessage).where(NotificationMessage.target_id.in_(ids)))
        db.execute(delete(NotificationTarget).where(NotificationTarget.parent_id == target.id))
        db.delete(target)
        db.commit()
    return Response(status_code=204)

