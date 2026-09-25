"""Apprise: a relay to more than a hundred services (Signal, Matrix, Slack, Pushover, SMS ...).

nexcrate talks to a self-hosted Apprise API: under a configuration key it holds the list of services with their
credentials. nexcrate knows only address and key; the secrets of the services stay in Apprise.
"""

from __future__ import annotations

from dataclasses import dataclass

from .base import DEFAULT_LEVEL, ChannelError, Notice, check_url, get_json, host_of, language_of, request

LABEL = "Apprise"
PARENT_FIELDS = ("url", "topic", "language")
PARENT_REQUIRED = ("url", "topic")
CHILD_FIELDS: tuple[str, ...] = ()
CHILD_REQUIRED: tuple[str, ...] = ()
FIELDS = PARENT_FIELDS
#: The configuration key is chosen freely and shown in Apprise's own page: no secret.
SECRETS: tuple[str, ...] = ()
REQUIRES_CODE = True

#: Apprise knows four kinds of message; the services colour them.
TYPES = {"low": "info", "normal": "info", "high": "warning", "urgent": "failure"}


@dataclass(frozen=True)
class Config:
    url: str
    topic: str
    language: str


def build(values: dict[str, str]) -> Config:
    return Config(
        url=values.get("url", "").strip().rstrip("/"),
        topic=values.get("topic", "").strip(),
        language=language_of(values),
    )


async def check(config: Config) -> dict[str, str] | None:
    check_url(config.url)
    data = await get_json(f"{config.url}/status", headers={"Accept": "application/json"})
    if not isinstance(data, dict) or "status" not in data:
        raise ChannelError("notify_wrong_service", service=LABEL)
    return None


async def send(config: Config, notice: Notice) -> None:
    check_url(config.url)
    if not config.topic:
        raise ChannelError("notify_field_missing", field="topic")
    body = {
        "title": notice.title,
        "body": notice.body,
        "type": TYPES.get(notice.level, TYPES[DEFAULT_LEVEL]),
        "format": "markdown",
    }
    address = f"{config.url}/notify/{config.topic}"
    answer = await request("POST", address, json=body, headers={"Accept": "application/json"})
    # A key without a configuration is a 204: a success for HTTP, the opposite for us.
    if answer.status_code == 204:
        raise ChannelError("apprise_no_config")
    if answer.status_code == 424:
        raise ChannelError("apprise_partial")
    if not answer.is_success:
        raise ChannelError("notify_http_error", host=host_of(address), status=answer.status_code)
