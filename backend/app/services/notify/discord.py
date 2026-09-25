"""Discord: one webhook address per channel, no bot.

The address is the permission: whoever knows it writes into that one channel. It is stored encrypted like a password.
Sent as an embed whose colour carries the meaning: amber is loading, green is there, red went wrong.
"""

from __future__ import annotations

from dataclasses import dataclass

from .base import ChannelError, Notice, check_url, get_json, language_of, post

LABEL = "Discord"
PARENT_FIELDS = ("url", "username", "language")
PARENT_REQUIRED = ("url",)
CHILD_FIELDS: tuple[str, ...] = ()
CHILD_REQUIRED: tuple[str, ...] = ()
FIELDS = PARENT_FIELDS
SECRETS = ("url",)
REQUIRES_CODE = True

_BLURPLE = 0x5865F2
COLORS: dict[str, int] = {
    "download_started": 0xF59E0B,
    "download_imported": 0x2ECC71,
    "download_upgraded": 0x2ECC71,
    "season_complete": 0x2ECC71,
    "download_failed": 0xE74C3C,
    "problem_opened": 0xE74C3C,
    "health_changed": 0xE67E22,
    "title_removed": 0x95A5A6,
    "file_deleted": 0x95A5A6,
}


@dataclass(frozen=True)
class Config:
    url: str
    username: str
    language: str


def build(values: dict[str, str]) -> Config:
    return Config(
        url=values.get("url", "").strip(),
        username=values.get("username", "").strip(),
        language=language_of(values),
    )


async def check(config: Config) -> dict[str, str] | None:
    """A GET on the webhook's address describes it, without sign-in: the address is its own key."""
    check_url(config.url)
    data = await get_json(config.url)
    if not isinstance(data, dict) or "channel_id" not in data:
        raise ChannelError("notify_wrong_service", service=LABEL)
    return None


async def send(config: Config, notice: Notice) -> None:
    check_url(config.url)
    embed: dict[str, object] = {
        "title": notice.title,
        "description": notice.body,
        "color": COLORS.get(notice.event or "", _BLURPLE),
    }
    if notice.poster_url:
        embed["thumbnail"] = {"url": notice.poster_url}
    body: dict[str, object] = {"embeds": [embed]}
    if config.username:
        body["username"] = config.username
    await post(config.url, json=body)
