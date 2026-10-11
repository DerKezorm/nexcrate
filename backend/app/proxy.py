"""What a trusted reverse proxy says about the request: the scheme, and the sender behind it.

uvicorn's own ``ProxyHeadersMiddleware`` takes ``X-Forwarded-Proto`` only when the whole value is exactly ``http``,
``https``, ``ws`` or ``wss``. A proxy that appends to a header the visitor brought ("http, https") or writes ``HTTPS``
does not count then, and the sign-in builds its redirect address with ``http``; it also takes ``wss`` for a plain
request. This layer sits in front of uvicorn's and, for a trusted sender only, takes the RIGHTMOST value of the last
``X-Forwarded-Proto`` line (the one the proxy wrote; everything left of it may come from the visitor), whatever its
case, and only ``http`` or ``https``; a WebSocket also takes ``ws`` or ``wss`` and becomes ``ws`` or ``wss``. Any
other value is no value: the scheme of the connection stays. The sender (``X-Forwarded-For``) is still set by
uvicorn's ``ProxyHeadersMiddleware`` (the rightmost address that is not a trusted proxy); its scheme is replaced by
this one afterwards.

Trusted are the addresses in ``FORWARDED_ALLOW_IPS`` (comma separated addresses or networks, ``*`` for every sender),
the variable uvicorn reads itself, so a setup that names its proxy there keeps working. Out of the box nobody is
trusted: the list is empty until the operator fills it, and the scheme is the one of the connection. The image starts
uvicorn with ``--no-proxy-headers`` and ``app.main:asgi`` for this.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from typing import Any

from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware, _TrustedHosts

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]

ENVIRONMENT_VARIABLE = "FORWARDED_ALLOW_IPS"

_PLAIN = {"http": "http", "https": "https"}
_SOCKET = {"http": "ws", "https": "wss", "ws": "ws", "wss": "wss"}


def trusted_proxies() -> str:
    """The proxies the operator named, "" for none (the default)."""
    return os.environ.get(ENVIRONMENT_VARIABLE, "").strip()


def forwarded_scheme(headers: list[tuple[bytes, bytes]], websocket: bool = False) -> str | None:
    """The scheme in the rightmost value of the last ``X-Forwarded-Proto`` line, None when there is no usable one."""
    value: bytes | None = None
    for name, raw in headers:
        if name.lower() == b"x-forwarded-proto":
            value = raw
    if value is None:
        return None
    last = value.decode("latin1").rsplit(",", 1)[-1].strip().lower()
    return (_SOCKET if websocket else _PLAIN).get(last)


_KEPT = "nexcrate.scheme"


class ForwardedScheme:
    """Outside: the scheme of the request, from a trusted proxy or from the connection."""

    def __init__(self, app: Any, trusted: str) -> None:
        self.app = app
        self.trusted = _TrustedHosts(trusted)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            scheme = scope.get("scheme", "http")
            client = scope.get("client")
            if client and client[0] in self.trusted:
                forwarded = forwarded_scheme(scope["headers"], websocket=scope["type"] == "websocket")
                if forwarded is not None:
                    scheme = forwarded
            scope[_KEPT] = scheme
        await self.app(scope, receive, send)


class _KeepScheme:
    """Inside, behind uvicorn's middleware: the scheme ``ForwardedScheme`` chose, back in place."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if _KEPT in scope:
            scope["scheme"] = scope.pop(_KEPT)
        await self.app(scope, receive, send)


def behind_proxy(app: Any, trusted: str) -> Any:
    """``app`` with the handling of trusted proxies: this scheme, uvicorn's sender. Nobody named: ``app`` itself,
    no forwarded header counts."""
    if not trusted.strip():
        return app
    return ForwardedScheme(ProxyHeadersMiddleware(_KeepScheme(app), trusted_hosts=trusted), trusted)
