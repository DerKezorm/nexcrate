"""ntfy: push to a phone through a topic, without an account.

Sent as JSON to the base address with the topic in the body: titles with umlauts need no header encoding that way.
Two levels: the instance carries address and sign-in, the topic is the mailbox, and only there can arrival be proven.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

from .base import DEFAULT_LEVEL, ChannelError, Notice, check_url, get_json, language_of, post

LABEL = "ntfy"
PARENT_FIELDS = ("url", "auth", "username", "password", "token")
PARENT_REQUIRED = ("url",)
CHILD_FIELDS = ("topic", "language")
CHILD_REQUIRED = ("topic",)
FIELDS = PARENT_FIELDS + CHILD_FIELDS
SECRETS = ("password", "token")
REQUIRES_CODE = True

#: ntfy knows 1 (quiet) to 5 (urgent). 5 rings through the night's silence, so only for the most urgent level.
PRIORITIES = {"low": 2, "normal": 3, "high": 4, "urgent": 5}


@dataclass(frozen=True)
class Config:
    url: str
    topic: str
    auth: str
    username: str
    password: str
    token: str
    language: str


def build(values: dict[str, str]) -> Config:
    auth = values.get("auth", "")
    return Config(
        url=values.get("url", "").strip().rstrip("/"),
        topic=values.get("topic", "").strip(),
        auth=auth if auth in ("none", "basic", "token") else "none",
        username=values.get("username", "").strip(),
        password=values.get("password", ""),
        token=values.get("token", "").strip(),
        language=language_of(values),
    )


def _headers(config: Config) -> dict[str, str]:
    if config.auth == "basic" and config.username:
        raw = f"{config.username}:{config.password}".encode()
        return {"Authorization": "Basic " + base64.b64encode(raw).decode()}
    if config.auth == "token" and config.token:
        return {"Authorization": f"Bearer {config.token}"}
    return {}


async def check(config: Config) -> dict[str, str] | None:
    """Does an ntfy answer at this address? For the instance: a typo shows at once, not at the first topic."""
    check_url(config.url)
    data = await get_json(f"{config.url}/v1/health", headers=_headers(config))
    if not isinstance(data, dict) or not data.get("healthy"):
        raise ChannelError("notify_wrong_service", service=LABEL)
    return None


async def send(config: Config, notice: Notice) -> None:
    check_url(config.url)
    if not config.topic:
        raise ChannelError("notify_field_missing", field="topic")
    payload: dict[str, object] = {
        "topic": config.topic,
        "title": notice.title,
        "message": notice.body,
        "priority": PRIORITIES.get(notice.level, PRIORITIES[DEFAULT_LEVEL]),
        "markdown": True,
    }
    # TMDB's poster is public, nexcrate itself usually is not: the image goes along, a link to nexcrate does not.
    if notice.poster_url:
        payload["attach"] = notice.poster_url
    await post(config.url, json=payload, headers=_headers(config))
