"""Telegram: one bot, any number of chats.

Two levels: the bot carries the token, the chat below is the mailbox. HTML, not MarkdownV2: MarkdownV2 wants a dozen
characters escaped that release names are full of, and a forgotten one is an HTTP 400 instead of a message.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

from .base import ChannelError, Notice, get_json, language_of, post_json

LABEL = "Telegram"
PARENT_FIELDS = ("token", "username")
PARENT_REQUIRED = ("token",)
CHILD_FIELDS = ("chat_id", "thread_id", "silent", "language")
CHILD_REQUIRED = ("chat_id",)
FIELDS = PARENT_FIELDS + CHILD_FIELDS
SECRETS = ("token",)
REQUIRES_CODE = True

API = "https://api.telegram.org"


@dataclass(frozen=True)
class Config:
    token: str
    username: str
    chat_id: str
    thread_id: str
    silent: bool
    language: str

    def url(self, method: str) -> str:
        return f"{API}/bot{self.token}/{method}"


def build(values: dict[str, str]) -> Config:
    return Config(
        token=values.get("token", "").strip(),
        username=values.get("username", "").strip().lstrip("@"),
        chat_id=values.get("chat_id", "").strip(),
        thread_id=values.get("thread_id", "").strip(),
        silent=values.get("silent", "").strip().lower() in ("on", "true", "1"),
        language=language_of(values),
    )


async def check(config: Config) -> dict[str, str] | None:
    """Is the token right? ``getMe`` says so, and names the bot, so nobody types its name for the ``t.me`` link."""
    if not config.token:
        raise ChannelError("notify_field_missing", field="token")
    data = await get_json(config.url("getMe"))
    if not isinstance(data, dict) or not data.get("ok"):
        raise ChannelError("telegram_token_rejected")
    name = str((data.get("result") or {}).get("username") or "")
    return {"username": name} if name else None


async def chats(config: Config) -> list[dict[str, str]]:
    """Who wrote to this bot lately? ``getUpdates`` names each chat that sent /start or took the bot into a group.
    Telegram keeps them about a day."""
    if not config.token:
        raise ChannelError("notify_field_missing", field="token")
    data = await get_json(config.url("getUpdates"))
    if not isinstance(data, dict) or not data.get("ok"):
        raise ChannelError("telegram_token_rejected")
    found: dict[str, dict[str, str]] = {}
    for entry in data.get("result") or []:
        if not isinstance(entry, dict):
            continue
        for key in ("message", "channel_post", "edited_message", "my_chat_member"):
            chat = (entry.get(key) or {}).get("chat") if isinstance(entry.get(key), dict) else None
            if not isinstance(chat, dict) or "id" not in chat:
                continue
            number = str(chat["id"])
            person = " ".join(part for part in (chat.get("first_name"), chat.get("last_name")) if part)
            name = chat.get("title") or person or chat.get("username") or number
            found[number] = {"chat_id": number, "name": str(name)[:100], "type": str(chat.get("type") or "")}
    return list(found.values())


def _text(notice: Notice) -> str:
    """The message as Telegram HTML; everything escaped first, then ``**bold**`` turned into ``<b>``."""
    lines = [f"<b>{html.escape(notice.title)}</b>"]
    if notice.body:
        lines.append(re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(notice.body)))
    return "\n\n".join(lines)


async def send(config: Config, notice: Notice) -> None:
    if not config.token or not config.chat_id:
        raise ChannelError("notify_field_missing", field="chat_id" if config.token else "token")
    # Telegram knows no priority, only with or without sound: the lowest level arrives silently.
    body: dict[str, object] = {
        "chat_id": config.chat_id,
        "parse_mode": "HTML",
        "disable_notification": config.silent or notice.level == "low",
    }
    if config.thread_id:
        body["message_thread_id"] = config.thread_id
    if notice.poster_url:
        # With a picture Telegram fetches it itself; a caption holds up to 1,024 characters.
        method = "sendPhoto"
        body["photo"] = notice.poster_url
        body["caption"] = _text(notice)[:1024]
    else:
        method = "sendMessage"
        body["text"] = _text(notice)[:4096]
    answer = await post_json(config.url(method), json=body)
    if not isinstance(answer, dict) or not answer.get("ok"):
        reason = str(answer.get("description") or "") if isinstance(answer, dict) else ""
        raise ChannelError("telegram_rejected", reason=reason[:200])
