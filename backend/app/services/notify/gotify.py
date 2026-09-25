"""Gotify: a self-hosted push service, one application token per mailbox. One level: the application is the mailbox."""

from __future__ import annotations

from dataclasses import dataclass

from .base import DEFAULT_LEVEL, ChannelError, Notice, check_url, get_json, language_of, post

LABEL = "Gotify"
PARENT_FIELDS = ("url", "token", "language")
PARENT_REQUIRED = ("url", "token")
CHILD_FIELDS: tuple[str, ...] = ()
CHILD_REQUIRED: tuple[str, ...] = ()
FIELDS = PARENT_FIELDS
SECRETS = ("token",)
REQUIRES_CODE = True

#: Gotify counts 0 to 10; 10 passes every quiet time on Android, so it belongs to the most urgent level only.
PRIORITIES = {"low": 2, "normal": 5, "high": 8, "urgent": 10}


@dataclass(frozen=True)
class Config:
    url: str
    token: str
    language: str


def build(values: dict[str, str]) -> Config:
    return Config(
        url=values.get("url", "").strip().rstrip("/"),
        token=values.get("token", "").strip(),
        language=language_of(values),
    )


async def check(config: Config) -> dict[str, str] | None:
    check_url(config.url)
    data = await get_json(f"{config.url}/version")
    if not isinstance(data, dict) or "version" not in data:
        raise ChannelError("notify_wrong_service", service=LABEL)
    return None


async def send(config: Config, notice: Notice) -> None:
    check_url(config.url)
    if not config.token:
        raise ChannelError("notify_field_missing", field="token")
    extras: dict[str, object] = {"client::display": {"contentType": "text/markdown"}}
    if notice.poster_url:
        extras["client::notification"] = {"bigImageUrl": notice.poster_url}
    payload = {
        "title": notice.title,
        "message": notice.body,
        "priority": PRIORITIES.get(notice.level, PRIORITIES[DEFAULT_LEVEL]),
        "extras": extras,
    }
    # The token in a header rather than the query: nothing of it lands in a proxy's log.
    await post(f"{config.url}/message", json=payload, headers={"X-Gotify-Key": config.token})
