"""Signing in at plex.tv with a PIN: create the PIN, the owner opens a link, ask until the token is there, then list
the servers of the account so the owner picks his instead of typing an address (as Nexview does it).

⚠️ This is the only module that talks to plex.tv, and it does so only from the three routes behind the button
"Sign in at plex.tv": create the PIN, ask for its state, list the servers. Nothing else in nexcrate contacts plex.tv:
an owner who types the token by hand causes no contact at all. ``tests/test_mediaserver_plextv.py`` holds both.

The servers come from ``GET /api/v2/resources?includeHttps=1`` with the account's token in ``X-Plex-Token``. The shape
is the one Nexview reads and has run against plex.tv for months (``nexview/backend/app/services/mediaserver/
plextv.py``): entries with ``provides`` containing ``server``, ``clientIdentifier``, ``name``, ``owned``,
``accessToken`` and ``connections[]`` of ``{local, uri}``. Only servers the account owns are offered: a server that was
merely shared with it is somebody else's. The local addresses come first; which one answers is tried by the caller.

The token never reaches the browser. It stays here in memory under the PIN's id until a server is saved with that
id, or until the PIN expires; the browser only learns that the PIN was claimed.

The facts and their sources stand in the design notes; ⚠️ none of them is measured against plex.tv.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx

from ...db import SessionLocal, get_setting, set_setting
from ...meldungen import meldung
from .. import http_log, logs
from . import base

logger = logging.getLogger("nexcrate.mediaservers")

HOST = "https://plex.tv"
PINS_PATH = "/api/v2/pins"
RESOURCES_PATH = "/api/v2/resources"
AUTH_PAGE = "https://app.plex.tv/auth"
PRODUCT = "nexcrate"
#: The settings key of this installation's ``X-Plex-Client-Identifier``: made up once, at the first sign-in.
CLIENT_ID_SETTING = "plex_client_identifier"
#: How long a PIN is kept at most, whatever plex.tv says.
MAX_LIFETIME_SECONDS = 1800
MAX_PENDING = 20
MAX_SERVERS = 50
MAX_ADDRESSES = 20

ERRORS = ((502, "plextv_unreachable"), (502, "plextv_error"))


def _unreachable() -> base.ServerError:
    return base.ServerError(meldung("plextv_unreachable", "plex.tv cannot be reached."))


def _failed(status: int | None = None) -> base.ServerError:
    values = {"status": status} if status is not None else {}
    return base.ServerError(meldung("plextv_error", "plex.tv gave an answer nexcrate cannot use.", **values))


@dataclass(frozen=True)
class Server:
    """A Plex server of the signed-in account, as plex.tv lists it."""

    machine_id: str
    name: str
    #: Its addresses, the local ones first.
    addresses: tuple[str, ...]
    #: ⚠️ The token this account uses at this server. For the owner it equals the account's token.
    token: str = field(repr=False)


@dataclass
class _Pending:
    code: str = field(repr=False)
    expires_at: float
    token: str | None = field(default=None, repr=False)
    #: The account's own servers as listed last, by machine id.
    servers: dict[str, Server] = field(default_factory=dict, repr=False)
    #: The token of the server the owner chose; saving takes this one.
    server_token: str | None = field(default=None, repr=False)


_lock = threading.Lock()
_pending: dict[int, _Pending] = {}
clock = time.monotonic


def reset() -> None:
    with _lock:
        _pending.clear()


def _drop_expired() -> None:
    moment = clock()
    for pin_id in [pin_id for pin_id, pending in _pending.items() if pending.expires_at <= moment]:
        del _pending[pin_id]


def client_identifier() -> str:
    """This installation's identifier for plex.tv: a random UUID, stored at the first use."""
    with SessionLocal() as db:
        known = get_setting(db, CLIENT_ID_SETTING)
        if known:
            return known
        made = uuid.uuid4().hex
        set_setting(db, CLIENT_ID_SETTING, made)
        db.commit()
        return get_setting(db, CLIENT_ID_SETTING, made)


def _client() -> httpx.AsyncClient:
    # ⚠️ Origin only: the PIN's code travels in the query, and the answer carries the token.
    return http_log.client(
        "plextv",
        base_url=HOST,
        timeout=base.TIMEOUT,
        origin_only=True,
        headers={"Accept": "application/json", "X-Plex-Product": PRODUCT},
        follow_redirects=False,
    )


async def _ask(
    method: str, path: str, identifier: str, params: dict[str, str], *, token: str | None = None, listed: bool = False
) -> tuple[int, Any]:
    headers = {"X-Plex-Client-Identifier": identifier}
    if token:
        # ⚠️ In a header only, never in the address.
        headers["X-Plex-Token"] = token
    async with _client() as http:
        try:
            # ⚠️ Without quiet_libraries httpx would write the PIN's code into the log in the trace mode.
            with logs.quiet_libraries():
                response = await http_log.send(http, method, path, params=params, headers=headers)
        except httpx.RequestError as exc:
            raise _unreachable() from exc
    if response.status_code == 404:
        return 404, None
    if not 200 <= response.status_code < 300:
        raise _failed(response.status_code)
    try:
        data = response.json()
    except ValueError as exc:
        raise _failed() from exc
    if not isinstance(data, list if listed else dict):
        raise _failed()
    return response.status_code, data


@dataclass(frozen=True)
class Pin:
    id: int
    auth_url: str
    expires_in: int


def auth_url(identifier: str, code: str) -> str:
    """The page the owner opens. Everything sits behind the ``#``, so none of it reaches plex.tv's server log."""
    context = quote("context[device][product]", safe="")
    return f"{AUTH_PAGE}#?clientID={quote(identifier, safe='')}&code={quote(code, safe='')}&{context}={quote(PRODUCT)}"


async def create_pin() -> Pin:
    identifier = await asyncio.to_thread(client_identifier)
    _status, data = await _ask("POST", PINS_PATH, identifier, {"strong": "true"})
    pin_id, code = (data or {}).get("id"), (data or {}).get("code")
    if isinstance(pin_id, bool) or not isinstance(pin_id, int) or not isinstance(code, str) or not code:
        raise _failed()
    lifetime = data.get("expiresIn")
    seconds = lifetime if isinstance(lifetime, int) and not isinstance(lifetime, bool) else MAX_LIFETIME_SECONDS
    seconds = max(60, min(seconds, MAX_LIFETIME_SECONDS))
    with _lock:
        _drop_expired()
        while len(_pending) >= MAX_PENDING:
            del _pending[min(_pending, key=lambda key: _pending[key].expires_at)]
        _pending[pin_id] = _Pending(code=code, expires_at=clock() + seconds)
    logger.info("A PIN for signing in at plex.tv was created")
    return Pin(id=pin_id, auth_url=auth_url(identifier, code), expires_in=seconds)


async def check_pin(pin_id: int) -> str:
    """``waiting``, ``claimed`` or ``expired``. plex.tv is asked only for a PIN this installation created and still
    waits for."""
    with _lock:
        _drop_expired()
        pending = _pending.get(pin_id)
        if pending is None:
            return "expired"
        if pending.token:
            return "claimed"
        code = pending.code
    identifier = await asyncio.to_thread(client_identifier)
    status, data = await _ask("GET", f"{PINS_PATH}/{pin_id}", identifier, {"code": code})
    if status == 404:
        with _lock:
            _pending.pop(pin_id, None)
        return "expired"
    token = (data or {}).get("authToken")
    if not isinstance(token, str) or not token:
        return "waiting"
    with _lock:
        kept = _pending.get(pin_id)
        if kept is None:
            return "expired"
        kept.token = token
    logger.info("The sign-in at plex.tv is done; the token waits for a server to be saved")
    return "claimed"


def token_of(pin_id: int) -> str | None:
    """The token a claimed PIN brought, without asking anyone: the one of the server the owner chose, else the
    account's. None: unknown, expired or not claimed."""
    with _lock:
        _drop_expired()
        pending = _pending.get(pin_id)
        return (pending.server_token or pending.token) if pending is not None else None


def _addresses(connections: Any) -> tuple[str, ...]:
    """Every address of a server, the local ones first: nexcrate mostly stands in the same network."""
    usable = [
        entry
        for entry in (connections if isinstance(connections, list) else [])
        if isinstance(entry, dict) and isinstance(entry.get("uri"), str) and entry["uri"].startswith(("http://", "https://"))
    ]
    local = [entry["uri"] for entry in usable if entry.get("local")]
    far = [entry["uri"] for entry in usable if not entry.get("local")]
    return tuple(dict.fromkeys([*local, *far]))[:MAX_ADDRESSES]


async def list_servers(pin_id: int) -> tuple[list[Server], int] | None:
    """The servers the signed-in account owns, and how many more were only shared with it. None: the PIN is unknown,
    expired or not claimed. ⚠️ Asks plex.tv."""
    with _lock:
        _drop_expired()
        pending = _pending.get(pin_id)
        token = pending.token if pending is not None else None
    if not token:
        return None
    identifier = await asyncio.to_thread(client_identifier)
    status, data = await _ask("GET", RESOURCES_PATH, identifier, {"includeHttps": "1"}, token=token, listed=True)
    if status == 404:
        raise _failed(404)
    owned: list[Server] = []
    shared = 0
    for entry in data[: MAX_SERVERS * 4]:
        if not isinstance(entry, dict) or "server" not in str(entry.get("provides") or ""):
            continue
        machine_id = base.text(entry.get("clientIdentifier"), 128)
        if not machine_id:
            continue
        if not entry.get("owned"):
            shared += 1
            continue
        own_token = entry.get("accessToken")
        owned.append(
            Server(
                machine_id=machine_id,
                name=base.text(entry.get("name")) or "Plex",
                addresses=_addresses(entry.get("connections")),
                # Missing, the account's token holds, as Plex does it for the owner.
                token=own_token if isinstance(own_token, str) and base.usable_token(own_token) else token,
            )
        )
    owned = owned[:MAX_SERVERS]
    with _lock:
        kept = _pending.get(pin_id)
        if kept is None:
            return None
        kept.servers = {server.machine_id: server for server in owned}
    logger.info("plex.tv lists %d own servers and %d shared ones for the signed-in account", len(owned), shared)
    return owned, shared


def server_of(pin_id: int, machine_id: str) -> Server | None:
    """A server of the last listing, without asking anyone."""
    with _lock:
        _drop_expired()
        pending = _pending.get(pin_id)
        return pending.servers.get(machine_id) if pending is not None else None


def choose(pin_id: int, machine_id: str) -> bool:
    """Saving with this PIN takes the token of this server from now on."""
    with _lock:
        pending = _pending.get(pin_id)
        server = pending.servers.get(machine_id) if pending is not None else None
        if pending is None or server is None:
            return False
        pending.server_token = server.token
        return True


def forget(pin_id: int) -> None:
    with _lock:
        _pending.pop(pin_id, None)
