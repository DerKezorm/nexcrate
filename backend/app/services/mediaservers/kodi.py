"""A Kodi: who it is, its sources, and "scan this folder again".

Only one address: ``POST {base}/jsonrpc`` with JSON-RPC 2.0 bodies. That it is a Kodi is proven by
``JSONRPC.Ping`` (``"pong"``); the version and the name come from ``Application.GetProperties`` (the JSON-RPC
version as the fallback when Kodi names none). The libraries are the file sources: ``Files.GetSources`` with
``video`` and with ``music``. A video source holds movies and series mixed, so its kind is None; a music source
is music. A changed folder is scanned again with ``VideoLibrary.Scan`` or ``AudioLibrary.Scan`` and its path as
Kodi sees it, ``showdialogs`` off so nothing pops up on the screen; everything is read again with the same calls
without a directory. The facts and their sources stand in the design notes; none of them is measured on a real
server.

The access is HTTP Basic with the username and password of Kodi's webserver settings. Without either no
``Authorization`` header is sent, for a Kodi that allows control without them.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any, Self

import httpx

from .. import http_log, logs
from ..radarr import normalize_base_url
from . import base

RPC_PATH = "/jsonrpc"
#: A video source holds movies and series mixed; only music sources have a kind here.
SOURCE_KINDS = {"video": None, "music": "music"}


def auth_headers(username: str, password: str) -> dict[str, str]:
    """The ``Authorization`` header, or none: without either Kodi may allow control as it is."""
    if not username and not password:
        return {}
    token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def rpc_path(base_url: str) -> str:
    """The path to ask: ``/jsonrpc``, unless the base URL already names it."""
    return "" if base_url.rstrip("/").lower().endswith(RPC_PATH) else RPC_PATH


class KodiClient:
    """Use as ``async with KodiClient(url, username, password) as kodi: ...``."""

    def __init__(
        self, base_url: str, username: str = "", password: str = "", *, timeout: httpx.Timeout | float = base.TIMEOUT
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self._username = username
        self._password = password
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None
        self._request_id = 0

    async def __aenter__(self) -> Self:
        self._http = http_log.client(
            "kodi",
            base_url=self.base_url,
            timeout=self._timeout,
            # The bodies carry the folders of titles: no path, query or body in any line.
            origin_only=True,
            headers={**auth_headers(self._username, self._password), "Accept": "application/json"},
            follow_redirects=False,
        )
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """The ``result`` of one JSON-RPC call, or ``mediaserver_wrong_kind``: another program answers its own JSON."""
        if self._http is None:
            raise RuntimeError("KodiClient is used outside of 'async with'")
        self._request_id += 1
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "id": self._request_id}
        if params is not None:
            body["params"] = params
        try:
            # As for the other servers: the lines of a media server carry the origin only.
            with logs.quiet_libraries():
                response = await http_log.send(self._http, "POST", rpc_path(self.base_url), json=body)
        except httpx.RequestError as exc:
            raise base.unreachable(self.base_url) from exc
        # Kodi names no token of its own: the pair of its web server settings is what it refuses or accepts.
        if response.status_code in (401, 403):
            raise base.kodi_auth_failed()
        base.raise_for(response)
        data = base.json_of(response)
        if not isinstance(data, dict) or data.get("jsonrpc") != "2.0" or "result" not in data:
            raise base.wrong_kind()
        if data.get("error") is not None:
            raise base.wrong_kind()
        return data["result"]

    async def ping(self) -> None:
        """Proves it is a Kodi: only one answers ``"pong"`` here."""
        if await self._call("JSONRPC.Ping") != "pong":
            raise base.wrong_kind()

    async def info(self) -> tuple[str, str]:
        """Kodi's name and version, or the JSON-RPC version when Kodi names none."""
        result = await self._call("Application.GetProperties", {"properties": ["version", "name"]})
        version = base.text(_app_version(result.get("version") if isinstance(result, dict) else None), 64)
        name = base.text(result.get("name") if isinstance(result, dict) else None) or "Kodi"
        if not version:
            version = base.text(_rpc_version(await self._call("JSONRPC.Version")), 64)
        if not version:
            raise base.wrong_kind()
        return name, version

    async def libraries(self) -> tuple[base.Listed, ...]:
        found: dict[str, base.Listed] = {}
        for media, kind in SOURCE_KINDS.items():
            result = await self._call("Files.GetSources", {"media": media})
            sources = result.get("sources") if isinstance(result, dict) else None
            if not isinstance(sources, list):
                raise base.wrong_kind()
            for entry in sources:
                if len(found) >= base.MAX_LIBRARIES:
                    break
                if not isinstance(entry, dict):
                    continue
                path = base.text(entry.get("file"), base.PATH_LIMIT)
                if not path:
                    continue
                digest = hashlib.sha1(path.encode("utf-8")).hexdigest()[:16]
                identifier = f"{media}-{digest}"
                if identifier in found:
                    continue
                found[identifier] = base.Listed(
                    id=identifier,
                    name=base.text(entry.get("label")) or path,
                    kind=kind,
                    locations=(path,),
                )
        return tuple(found.values())

    async def check(self) -> base.Checked:
        await self.ping()
        name, version = await self.info()
        return base.Checked(server_name=name, version=version, libraries=await self.libraries())

    async def scan(self, kind: str, directory: str | None = None) -> None:
        """Scan one folder again, or without ``directory`` the whole video or audio library.

        ``kind`` is nexcrate's: movie and series scan the video library, music the audio one. Nothing pops up on
        Kodi's screen; the scan runs in the background and this call only hands it over.

        Kodi keeps the paths of its own sources with a trailing slash, so the folder goes out with one as well;
        a scan of the whole library names no directory and is left as it is.
        """
        method = "AudioLibrary.Scan" if kind == "music" else "VideoLibrary.Scan"
        params: dict[str, Any] = {"showdialogs": False}
        if directory:
            params["directory"] = directory if directory.endswith("/") else f"{directory}/"
        await self._call(method, params)


def _app_version(value: Any) -> str:
    """``{"major": 21, "minor": 0, "patch": 0}`` as ``21.0.0``; empty when it names none."""
    if not isinstance(value, dict):
        return ""
    parts = [str(value[part]) for part in ("major", "minor", "patch") if isinstance(value.get(part), int)]
    return ".".join(parts)


def _rpc_version(value: Any) -> str:
    """The JSON-RPC version as ``JSON-RPC 13.0``; empty when it names none."""
    version = value.get("version") if isinstance(value, dict) else None
    if not isinstance(version, dict):
        return ""
    parts = [str(version[part]) for part in ("major", "minor") if isinstance(version.get(part), int)]
    return f"JSON-RPC {'.'.join(parts)}" if parts else ""
