"""One log line per outgoing HTTP call.

Radarr and later services are where things go wrong in practice. Instead of logging
at every call site, the line hangs on the shared httpx clients: httpx calls the
hooks for every request. Create clients with ``client()`` and send with ``send()``.

The level follows the answer:

* ``DEBUG``   success (2xx, 3xx), visible in ``detailed`` and ``trace``
* ``INFO``    404 and similar client errors, often normal
* ``WARNING`` 401, 403, 429 and everything from 500: the other side refuses

A call that never gets an answer (timeout, refused connection) has no response for
the hooks to hang on. ``send`` writes that line through ``unreachable``.

⚠️ Query parameters with secret-looking names are masked (``apikey`` and ``passkey``
included), request headers and request bodies are never logged, and response bodies appear only
in ``trace``, truncated. A client created with ``log_bodies=False`` never writes a body: indexer
feeds carry download links with keys in shapes no pattern recognises.

⚠️ A client created with ``origin_only=True`` writes scheme, host and port only, never path or
query: a release's download link carries the key or passkey anywhere, ``r=`` or
``/download/<passkey>/`` included (step 3).
"""

from __future__ import annotations

import logging
import time
import weakref
from typing import Any
from urllib.parse import unquote_plus

import httpx

from . import logs

#: Query parameter names whose values never reach a log line (matched as a part of the name).
SENSITIVE_QUERY_NAMES = ("key", "token", "password", "passwd", "pwd", "secret", "auth", "signature", "session", "code")
LOUD_STATUSES = (401, 403, 429)
#: Characters of a response body written in trace mode.
BODY_LIMIT = 2000
_TEXT_TYPES = ("json", "text", "xml", "javascript")

_transport: httpx.AsyncBaseTransport | None = None
_services: weakref.WeakKeyDictionary[httpx.AsyncClient, str] = weakref.WeakKeyDictionary()
#: Clients whose lines carry the origin only.
_origin_only: weakref.WeakSet[httpx.AsyncClient] = weakref.WeakSet()


def use_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Put a transport under every client created afterwards. The tests use it to block the network."""
    global _transport
    _transport = transport


def current_transport() -> httpx.AsyncBaseTransport | None:
    """The transport new clients get. A shared client compares it to notice a change."""
    return _transport


def mask_query(query: str) -> str:
    """The query string with the values of secret-looking parameters replaced by ``***``."""
    if not query:
        return ""
    parts = []
    for pair in query.split("&"):
        name, _separator, _value = pair.partition("=")
        if any(word in unquote_plus(name).lower() for word in SENSITIVE_QUERY_NAMES):
            parts.append(f"{name}=***")
        else:
            parts.append(pair)
    return "&".join(parts)


def safe_url(url: httpx.URL | str) -> str:
    """Scheme, host, port, path and the masked query. Never user and password."""
    parsed = httpx.URL(url)
    port = f":{parsed.port}" if parsed.port else ""
    query = mask_query(parsed.query.decode("ascii", "replace"))
    return f"{parsed.scheme}://{parsed.host}{port}{parsed.path}" + (f"?{query}" if query else "")


def origin_url(url: httpx.URL | str) -> str:
    """Scheme, host and port, with ``/...`` for whatever follows. Never path, query, user or password."""
    try:
        parsed = httpx.URL(url)
    except (httpx.InvalidURL, TypeError, ValueError):
        return "(address hidden)"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.host}{port}/..."


def _level(status: int) -> int:
    if status >= 500 or status in LOUD_STATUSES:
        return logging.WARNING
    if status >= 400:
        return logging.INFO
    return logging.DEBUG


def _service_logger(service: str) -> logging.Logger:
    return logging.getLogger(f"nexcrate.http.{service}")


def event_hooks(
    service: str, log_bodies: bool = True, origin_only: bool = False, quiet_statuses: tuple[int, ...] = ()
) -> dict[str, list[Any]]:
    """``quiet_statuses`` are answers the caller expects and handles itself, logged on debug: MusicBrainz's 503 means
    "slow down" and is retried; the caller warns once when it gives up."""
    log = _service_logger(service)
    shown = origin_url if origin_only else safe_url

    async def on_request(request: httpx.Request) -> None:
        request.extensions["nexcrate_started"] = time.perf_counter()

    async def on_response(response: httpx.Response) -> None:
        started = response.request.extensions.get("nexcrate_started")
        elapsed = int((time.perf_counter() - started) * 1000) if started is not None else -1
        log.log(
            logging.DEBUG if response.status_code in quiet_statuses else _level(response.status_code),
            "%s %s -> %s in %dms",
            response.request.method,
            shown(response.request.url),
            response.status_code,
            elapsed,
        )
        content_type = response.headers.get("content-type", "")
        if (
            log_bodies
            and not origin_only
            and logs.current_mode() == "trace"
            and any(kind in content_type for kind in _TEXT_TYPES)
        ):
            await response.aread()
            body = response.text
            log.debug(
                "Response body, %d of %d characters: %s", min(len(body), BODY_LIMIT), len(body), body[:BODY_LIMIT]
            )

    return {"request": [on_request], "response": [on_response]}


def client(
    service: str,
    *,
    base_url: str = "",
    timeout: float | httpx.Timeout = 10.0,
    log_bodies: bool = True,
    origin_only: bool = False,
    quiet_statuses: tuple[int, ...] = (),
    **kwargs: Any,
) -> httpx.AsyncClient:
    """An httpx client with the log hooks. ``service`` names the logger: nexcrate.http.<service>.

    ``origin_only`` keeps path and query out of every line, the body as well.
    """
    created = httpx.AsyncClient(
        base_url=base_url,
        timeout=timeout,
        transport=_transport,
        event_hooks=event_hooks(service, log_bodies, origin_only, quiet_statuses),
        **kwargs,
    )
    _services[created] = service
    if origin_only:
        _origin_only.add(created)
    return created


def unreachable(
    service: str, method: str, url: httpx.URL | str, error: BaseException, *, origin_only: bool = False
) -> None:
    """A call that never got an answer: timeout or no connection.

    With ``origin_only`` neither path nor the error text is written: an error text can repeat the address.
    """
    if origin_only:
        _service_logger(service).warning("%s %s failed: %s", method, origin_url(url), type(error).__name__)
        return
    _service_logger(service).warning("%s %s failed: %s: %s", method, safe_url(url), type(error).__name__, error)


async def send(http: httpx.AsyncClient, method: str, url: str, **kwargs: Any) -> httpx.Response:
    """Send a request; a transport error is logged as unreachable and raised again."""
    try:
        return await http.request(method, url, **kwargs)
    except httpx.TransportError as exc:
        target = exc.request.url if _has_request(exc) else url
        unreachable(_services.get(http, "unknown"), method, target, exc, origin_only=http in _origin_only)
        raise


def _has_request(exc: httpx.TransportError) -> bool:
    try:
        exc.request  # noqa: B018 - the property raises when no request is attached
    except RuntimeError:
        return False
    return True
