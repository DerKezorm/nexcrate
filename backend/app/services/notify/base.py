"""What every channel shares: the message, the error with a code, the HTTP calls.

A channel is deliberately little: it takes a ``Notice`` and sends it. When, how often and to whom is the outbox's
business, so another service costs one module. Taken over from Nexview, with nexcrate's rules: errors carry a code the
interface translates, every request goes through ``http_log`` with the host only (a Telegram token sits in the path, a
Discord address is its own key), and no redirect is followed, because a redirect would
send the message to a place the owner never entered.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from .. import http_log

#: How long a service may take. Generous for an instance at home, short enough that a dead one blocks nothing long.
TIMEOUT = httpx.Timeout(8.0, connect=5.0)

#: The levels the owner picks per event. Named, not numbered: Gotify counts 0 to 10, ntfy 1 to 5, Discord has none;
#: each channel turns a name into what its service understands.
LEVELS = ("low", "normal", "high", "urgent")
DEFAULT_LEVEL = "normal"


class ChannelError(Exception):
    """Sending or checking failed, with a code the interface translates and its values."""

    def __init__(self, code: str, **params: Any) -> None:
        super().__init__(code)
        self.code = code
        self.params = params


@dataclass(frozen=True)
class Notice:
    """What is sent, before any channel shaped it.

    ``body`` is Markdown with ``**bold**`` only; the push services show it as it is, Telegram gets HTML, the webhook and
    Apprise plain text. ``event`` is the event key (Discord picks its colour by it); None for the test message.
    """

    title: str
    body: str
    level: str = DEFAULT_LEVEL
    poster_url: str | None = None
    event: str | None = None
    #: The four digits of the test message. They stand in the title for a person; a webhook gets them as a value too.
    code: str | None = None


def host_of(url: str) -> str:
    return urlsplit(url).hostname or url.split("//", 1)[-1].split("/", 1)[0] or "?"


def check_url(url: str) -> None:
    """``http`` or ``https`` with a host; anything else is never tried."""
    parts = urlsplit(url or "")
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ChannelError("notify_url_invalid")


def readable(error: httpx.HTTPError, host: str) -> ChannelError:
    """An HTTP failure as a code one can act on."""
    if isinstance(error, httpx.TimeoutException):
        return ChannelError("notify_timeout", host=host)
    if isinstance(error, httpx.ConnectError):
        return ChannelError("notify_unreachable", host=host)
    return ChannelError("notify_unreachable", host=host)


def status_error(status: int, host: str) -> ChannelError:
    if status in (401, 403):
        return ChannelError("notify_auth_rejected", host=host, status=status)
    if status == 404:
        return ChannelError("notify_not_found", host=host)
    if status == 413:
        return ChannelError("notify_too_large", host=host)
    return ChannelError("notify_http_error", host=host, status=status)


def client() -> httpx.AsyncClient:
    """A short-lived client per call: a message goes out every few hours, and the address may change in between."""
    return http_log.client("notify", timeout=TIMEOUT, follow_redirects=False, origin_only=True, log_bodies=False)


async def request(
    method: str, url: str, *, json: Any = None, headers: dict[str, str] | None = None, content: bytes | None = None
) -> httpx.Response:
    """One request; a transport failure becomes a ``ChannelError``. The status is the caller's to judge."""
    host = host_of(url)
    try:
        async with client() as http:
            return await http_log.send(http, method, url, json=json, headers=headers or {}, content=content)
    except httpx.HTTPError as exc:
        raise readable(exc, host) from exc


async def post(url: str, *, json: Any, headers: dict[str, str] | None = None) -> None:
    """Send, or fail with a ``ChannelError``."""
    answer = await request("POST", url, json=json, headers=headers)
    # A redirect is no delivery either: nobody follows it.
    if not answer.is_success:
        raise status_error(answer.status_code, host_of(url))


async def get_json(url: str, *, headers: dict[str, str] | None = None) -> Any:
    """Ask instead of send, for the check of an instance."""
    answer = await request("GET", url, headers=headers)
    if not answer.is_success:
        raise status_error(answer.status_code, host_of(url))
    try:
        return answer.json()
    except ValueError as exc:
        raise ChannelError("notify_bad_answer", host=host_of(url)) from exc


async def post_json(url: str, *, json: Any) -> Any:
    """Send and read the answer, even an error: Telegram explains a refusal in the body of its 400."""
    answer = await request("POST", url, json=json)
    try:
        return answer.json()
    except ValueError as exc:
        raise ChannelError("notify_bad_answer", host=host_of(url)) from exc


def language_of(values: dict[str, str]) -> str:
    return values.get("language") if values.get("language") in ("de", "en") else "de"
