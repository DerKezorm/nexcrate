"""Request id, CSRF check, unhandled errors and security headers, as pure ASGI.

Pure ASGI instead of ``BaseHTTPMiddleware``: that one runs the app in a separate task
and makes streaming answers needlessly tricky. Here only values are set, headers
added and, when needed, an answer sent.

**Request id.** Every request gets a short id. It is in every log line written during
the request and in the ``X-Request-Id`` header of the answer. A person reporting an
error quotes it, and one search in the log shows the whole request. It is 8
characters on purpose: the redaction masks long runs, and it must never eat the id.

**Unhandled errors** are logged here, while the request id is still bound, and answered
with 500 ``internal_error`` and the ``request_id``.

**CSRF.** A request that changes something must carry ``X-Requested-With: nexcrate``.
A foreign page cannot send that header without a CORS preflight, which nexcrate never
allows. ``/api/v1`` is left out: it never reads the cookie.

**The caller's request id.** A program that sends ``X-Request-Id`` to ``/api/v1`` finds it on every
log line of that request (``c:<id>``), next to nexcrate's own, which stays the one in the answer.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
import secrets
import time
from collections.abc import Awaitable, Callable, MutableMapping
from pathlib import Path
from typing import Any

from fastapi.responses import JSONResponse, RedirectResponse

from .meldungen import error_body, is_v1_path, meldung
from .services import logs
from .services.http_log import mask_query

logger = logging.getLogger("nexcrate.api")

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

REQUEST_ID_HEADER = b"x-request-id"
#: Paths whose requests explain nothing but fill the log.
QUIET_PATHS = ("/api/health", "/api/logs")
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CSRF_HEADER = b"x-requested-with"
CSRF_VALUE = "nexcrate"
#: What a program may send as its own request id (N6). Short on purpose: the redaction of the log masks long runs,
#: and the brackets of a log line must stay intact.
CALLER_ID = re.compile(r"[A-Za-z0-9-]{1,36}")


def new_request_id() -> str:
    return secrets.token_hex(4)


def is_api_path(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


def internal_error_response(request_id: str, path: str = "") -> JSONResponse:
    detail = meldung(
        "internal_error",
        f"Something went wrong on the server. Reference for troubleshooting: {request_id}",
        request_id=request_id,
    )
    return JSONResponse(status_code=500, content=error_body(path, detail))


def _caller_id(scope: Scope) -> str | None:
    """The request id a program sent along with a request to ``/api/v1``, when it is one nexcrate can log."""
    if not is_v1_path(scope.get("path", "")):
        return None
    for name, value in scope.get("headers", []):
        if name == REQUEST_ID_HEADER:
            text = value.decode("latin-1").strip()
            return text if CALLER_ID.fullmatch(text) else None
    return None


def _path_for_log(scope: Scope) -> str:
    query = mask_query(scope.get("query_string", b"").decode("latin-1"))
    path = scope.get("path", "?")
    return f"{path}?{query}" if query else path


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = new_request_id()
        # Also in the scope: the exception handler of last resort runs outside this middleware.
        scope.setdefault("state", {})["request_id"] = request_id
        token = logs.bind_request(request_id)
        caller = _caller_id(scope)
        if caller is not None:
            logs.set_caller_ref(caller)
        started = time.perf_counter()
        status = 0
        response_started = False

        async def send_with_id(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status = message["status"]
                headers = list(message.get("headers", []))
                headers.append((REQUEST_ID_HEADER, request_id.encode("ascii")))
                message["headers"] = headers
            await send(message)

        method = scope.get("method", "?")
        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            elapsed = (time.perf_counter() - started) * 1000
            logger.exception("Unhandled error on %s %s after %dms", method, _path_for_log(scope), elapsed)
            if response_started:
                raise
            await internal_error_response(request_id, scope.get("path", ""))(scope, receive, send_with_id)
        else:
            elapsed = (time.perf_counter() - started) * 1000
            if status >= 500:
                logger.error("%s %s -> %s in %dms", method, _path_for_log(scope), status, elapsed)
            elif not scope.get("path", "").startswith(QUIET_PATHS):
                logger.debug("%s %s -> %s in %dms", method, _path_for_log(scope), status, elapsed)
        finally:
            logs.unbind_request(token)


#: Answers at the root even under a sub path: the container's health check knows no sub path.
HEALTH_PATH = "/api/health"


class UrlBaseMiddleware:
    """nexcrate under a sub path (``NEXCRATE_URL_BASE``): a request below it goes on without it, the sub path itself
    is sent on with a slash, and everything else is not found. Only ``/api/health`` answers at the root as well.

    ⚠️ Outside the request id and the CSRF check: both look at the path, and they must see it without the sub path.
    """

    def __init__(self, app: ASGIApp, base: str) -> None:
        self.app = app
        self.base = base

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self.base or scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path.startswith(self.base + "/"):
            inner = dict(scope)
            inner["path"] = path[len(self.base) :]
            raw = scope.get("raw_path")
            if isinstance(raw, bytes) and raw.startswith(self.base.encode("latin-1") + b"/"):
                inner["raw_path"] = raw[len(self.base) :]
            await self.app(inner, receive, send)
            return
        if path == HEALTH_PATH:
            await self.app(scope, receive, send)
            return
        if path == self.base and scope["type"] == "http":
            query = scope.get("query_string", b"").decode("latin-1")
            target = self.base + "/" + (f"?{query}" if query else "")
            await RedirectResponse(target, status_code=308)(scope, receive, send)
            return
        if scope["type"] == "http":
            detail = meldung("not_found", "This does not exist, or not any more.")
            await JSONResponse(status_code=404, content=error_body(path, detail))(scope, receive, send)


class CsrfMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope.get("method") not in SAFE_METHODS
            and is_api_path(scope.get("path", ""))
            # /api/v1 opens with a key in a header and never with the cookie, so no foreign page can ride on a login.
            and not is_v1_path(scope.get("path", ""))
            and not _has_csrf_header(scope)
        ):
            logger.info("Refused %s %s: the header X-Requested-With is missing", scope.get("method"), scope.get("path"))
            response = JSONResponse(
                status_code=403,
                content={
                    "detail": meldung(
                        "csrf_header_missing",
                        "The request did not come from the nexcrate interface and was refused.",
                    )
                },
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _has_csrf_header(scope: Scope) -> bool:
    for name, value in scope.get("headers", []):
        if name == CSRF_HEADER and value.decode("latin-1").strip().lower() == CSRF_VALUE:
            return True
    return False


# --- Security headers -------------------------------------------------------- #

_INLINE_SCRIPT = re.compile(r"<script(?![^>]*\ssrc=)[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE)


def inline_script_hashes(html: str) -> list[str]:
    """CSP hashes of every inline script in a page.

    ⚠️ Computed at start from the page that is actually served, not written into the
    source. A written hash drifts apart at the first changed character, and the failure
    shows as a white page on somebody else's machine.
    """
    return [
        "'sha256-" + base64.b64encode(hashlib.sha256(content.encode("utf-8")).digest()).decode("ascii") + "'"
        for content in _INLINE_SCRIPT.findall(html)
    ]


def content_security_policy(index_html: Path | None = None) -> str:
    hashes = inline_script_hashes(index_html.read_text(encoding="utf-8")) if index_html is not None else []
    return "; ".join(
        [
            "default-src 'self'",
            "base-uri 'self'",
            "object-src 'none'",
            "form-action 'self'",
            "frame-ancestors 'none'",
            "script-src " + " ".join(["'self'", *hashes]),
            # Inline styles on purpose: without them every style attribute silently fails, and
            # an injected style cannot run code.
            "style-src 'self' 'unsafe-inline'",
            # Posters come through /api/images, never from a foreign host.
            "img-src 'self' data: blob:",
            "font-src 'self'",
            # The most important line: no script on this page can send data to a foreign address.
            "connect-src 'self'",
            "worker-src 'self'",
            "manifest-src 'self'",
        ]
    )


class SecurityHeadersMiddleware:
    """Adds the security headers to every answer, not only to the page.

    A rule that hangs on one of several delivery paths is sooner or later no rule.
    """

    def __init__(self, app: ASGIApp, csp: str) -> None:
        self.app = app
        self.headers = [
            (b"content-security-policy", csp.encode("ascii")),
            (b"x-content-type-options", b"nosniff"),
            (b"referrer-policy", b"no-referrer"),
            (b"x-frame-options", b"DENY"),
        ]
        # Only over https. Browsers ignore this header on plain http and log an error for every
        # page, and nexcrate usually runs on plain http inside the home network.
        self.https_headers = [(b"cross-origin-opener-policy", b"same-origin")]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        wanted = self.headers + (self.https_headers if scope.get("scheme") == "https" else [])

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _value in headers}
                headers.extend(header for header in wanted if header[0] not in present)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)
