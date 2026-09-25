"""Notifications to the owner's own services.

Every channel is a module with ``LABEL``, its fields per level (``PARENT_FIELDS``, ``CHILD_FIELDS``), which are
required and which are secrets, ``build(values)``, ``check(config)`` and ``send(config, notice)``. Another service
costs one module and a line below: targets, outbox and routes stay as they are.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

from . import apprise, discord, email, gotify, ntfy, telegram, webhook
from .base import DEFAULT_LEVEL, LEVELS, ChannelError, Notice

__all__ = ["CHANNELS", "DEFAULT_LEVEL", "LEVELS", "ChannelError", "Notice"]

#: In the order of the tabs.
CHANNELS: dict[str, ModuleType] = {
    "ntfy": ntfy,
    "gotify": gotify,
    "telegram": telegram,
    "discord": discord,
    "webhook": webhook,
    "apprise": apprise,
    "email": email,
}


def module(kind: str) -> ModuleType:
    return CHANNELS[kind]


def label(kind: str) -> str:
    return str(CHANNELS[kind].LABEL)


def fields(kind: str) -> tuple[str, ...]:
    return tuple(CHANNELS[kind].FIELDS)


def parent_fields(kind: str) -> tuple[str, ...]:
    return tuple(CHANNELS[kind].PARENT_FIELDS)


def child_fields(kind: str) -> tuple[str, ...]:
    return tuple(CHANNELS[kind].CHILD_FIELDS)


def has_children(kind: str) -> bool:
    return bool(CHANNELS[kind].CHILD_FIELDS)


def required(kind: str, child: bool) -> tuple[str, ...]:
    return tuple(CHANNELS[kind].CHILD_REQUIRED if child else CHANNELS[kind].PARENT_REQUIRED)


def secrets_of(kind: str) -> tuple[str, ...]:
    return tuple(CHANNELS[kind].SECRETS)


def requires_code(kind: str) -> bool:
    return bool(CHANNELS[kind].REQUIRES_CODE)


def can_list_chats(kind: str) -> bool:
    return hasattr(CHANNELS[kind], "chats")


def build(kind: str, values: dict[str, str]) -> Any:
    return CHANNELS[kind].build(values)


def fingerprint(kind: str, values: dict[str, str]) -> tuple[str, ...]:
    """A print of the connection: a test proves one address, and exactly that one may be saved afterwards."""
    return tuple(str(values.get(name, "")) for name in fields(kind))


async def check(kind: str, config: Any) -> dict[str, str] | None:
    return await CHANNELS[kind].check(config)


async def chats(kind: str, config: Any) -> list[dict[str, str]]:
    return await CHANNELS[kind].chats(config)


async def send(kind: str, config: Any, notice: Notice) -> None:
    await CHANNELS[kind].send(config, notice)
