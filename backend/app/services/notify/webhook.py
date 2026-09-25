"""Webhook: a POST with a fixed JSON body, for Home Assistant, n8n, Node-RED and own scripts.

Not the signed webhooks for programs under System (``services/webhooks.py``), which carry the raw event of
``/api/v1``: this one carries a readable message. The confirmation code stands in ``title`` for a person and in
``code`` for a machine, so an automation reads a value, not a translated sentence.
"""

from __future__ import annotations

from dataclasses import dataclass

from .base import Notice, check_url, language_of, post

LABEL = "Webhook"
PARENT_FIELDS = ("url", "token", "language")
PARENT_REQUIRED = ("url",)
CHILD_FIELDS: tuple[str, ...] = ()
CHILD_REQUIRED: tuple[str, ...] = ()
FIELDS = PARENT_FIELDS
#: The whole Authorization header, as "Bearer abc123"; optional, but a secret once given.
SECRETS = ("token",)
REQUIRES_CODE = True


@dataclass(frozen=True)
class Config:
    url: str
    token: str
    language: str


def build(values: dict[str, str]) -> Config:
    return Config(
        url=values.get("url", "").strip(), token=values.get("token", "").strip(), language=language_of(values)
    )


async def check(config: Config) -> dict[str, str] | None:
    """Only the address: a GET on somebody's endpoint might already trigger something there."""
    check_url(config.url)
    return None


async def send(config: Config, notice: Notice) -> None:
    check_url(config.url)
    body: dict[str, object] = {
        "source": "nexcrate",
        "event": notice.event or "test",
        "level": notice.level,
        "title": notice.title,
        # Machines read no emphasis: the asterisks go, the text stays.
        "body": notice.body.replace("**", ""),
        "image": notice.poster_url,
        "code": notice.code,
    }
    await post(config.url, json=body, headers={"Authorization": config.token} if config.token else None)
