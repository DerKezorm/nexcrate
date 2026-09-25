"""What Plex, Jellyfin and Emby have in common: the target, a library as the server lists it, and the errors.

* Every call goes through the clients of ``http_log`` with a short timeout: a media server never holds anything up.
* ⚠️ The token travels in a header only, never in a URL: a URL ends up in logs, proxies and browser histories.
* The log lines carry the origin only (``origin_only=True``), no path, query or body: the folder of a title travels
  in Plex's query, and nexcrate's log names no paths. What was asked is logged by ``notify`` in its own words.
* What a server answers is never trusted: names and paths are read tolerantly, and an answer of another shape is
  ``mediaserver_wrong_kind``. A server's own sentences never reach an answer of nexcrate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import HTTPException

from ...meldungen import meldung

KINDS = ("plex", "jellyfin", "emby")
#: Short on purpose: a media server that does not answer must not hold up anything.
TIMEOUT = httpx.Timeout(10.0, connect=5.0)
MAX_LIBRARIES = 200
MAX_LOCATIONS = 50
NAME_LIMIT = 200
PATH_LIMIT = 4096


class ServerError(Exception):
    """An answer of a media server that nexcrate cannot use, with the error the API sends for it."""

    def __init__(self, detail: dict[str, Any], status: int = 502) -> None:
        super().__init__(detail["code"])
        self.detail = detail
        self.status = status

    @property
    def code(self) -> str:
        return str(self.detail["code"])

    def http(self) -> HTTPException:
        return HTTPException(status_code=self.status, detail=self.detail)


def unreachable(url: str) -> ServerError:
    return ServerError(
        meldung(
            "mediaserver_unreachable",
            f"The media server cannot be reached at {url}. Are address and port right?",
            url=url,
        )
    )


def auth_failed() -> ServerError:
    return ServerError(meldung("mediaserver_auth_failed", "The media server did not accept the token or API key."))


def wrong_kind() -> ServerError:
    return ServerError(
        meldung("mediaserver_wrong_kind", "Something answers at this address, but not the chosen media server.")
    )


def http_error(status: int) -> ServerError:
    return ServerError(
        meldung("mediaserver_http_error", f"The media server reports an error (HTTP {status}).", status=status)
    )


#: For the OpenAPI ``responses`` of every route that talks to a media server.
ERRORS = (
    (502, "mediaserver_unreachable"),
    (502, "mediaserver_auth_failed"),
    (502, "mediaserver_wrong_kind"),
    (502, "mediaserver_http_error"),
)


@dataclass(frozen=True)
class Target:
    kind: str
    url: str
    #: ⚠️ The plain token or API key; never logged, never part of a ``repr``.
    token: str = field(repr=False)


@dataclass(frozen=True)
class Listed:
    """A library as the server lists it. ``kind`` is movie, series, music or None (photos, mixed, anything else)."""

    id: str
    name: str
    kind: str | None
    locations: tuple[str, ...]


@dataclass(frozen=True)
class Checked:
    server_name: str
    version: str
    libraries: tuple[Listed, ...]


def usable_token(token: str) -> bool:
    """A header carries printable ASCII only; httpx would raise deep inside."""
    return bool(token) and token.isascii() and token.isprintable()


def text(value: Any, limit: int = NAME_LIMIT) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def json_of(response: httpx.Response) -> Any:
    """The JSON body of a 2xx answer, or ``mediaserver_wrong_kind``: a web page answers 200 as well."""
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise wrong_kind()
    try:
        return response.json()
    except ValueError as exc:
        raise wrong_kind() from exc


def raise_for(response: httpx.Response, *, allow: tuple[int, ...] = ()) -> None:
    """``allow``: statuses the caller handles itself, an address an older or newer server does not have."""
    status = response.status_code
    if status in allow:
        return
    if status in (401, 403):
        raise auth_failed()
    if 300 <= status < 400 or status == 404:
        raise wrong_kind()
    if not 200 <= status < 300:
        raise http_error(status)
