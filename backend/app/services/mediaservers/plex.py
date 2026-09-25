"""A Plex Media Server: who it is, its libraries, and "read this folder again".

Only these addresses: ``GET /identity``, ``GET /library/sections`` and ``GET /library/sections/{key}/refresh``, with
``path`` for one folder. ⚠️ Plex's own description names the refresh as POST, Radarr and Sonarr send GET; GET is tried
first, and a 405 is asked again as POST. The facts and their sources stand in the design notes; ⚠️ none of
them is measured on a real server.

⚠️ This module never talks to plex.tv. The token goes to the owner's server only, in the ``X-Plex-Token`` header.
"""

from __future__ import annotations

from typing import Any, Self

import httpx

from .. import http_log, logs
from ..radarr import normalize_base_url
from . import base

IDENTITY_PATH = "/identity"
SECTIONS_PATH = "/library/sections"
#: Plex's section types and what they are here.
SECTION_KINDS = {"movie": "movie", "show": "series", "artist": "music"}


class PlexClient:
    """Use as ``async with PlexClient(url, token) as plex: ...``."""

    def __init__(self, base_url: str, token: str, *, timeout: httpx.Timeout | float = base.TIMEOUT) -> None:
        self.base_url = normalize_base_url(base_url)
        self._token = token
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> Self:
        self._http = http_log.client(
            "plex",
            base_url=self.base_url,
            timeout=self._timeout,
            # The folder of a title travels in the query: no path, query or body in any line.
            origin_only=True,
            headers={"X-Plex-Token": self._token, "Accept": "application/json"},
            follow_redirects=False,
        )
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _send(
        self, method: str, path: str, params: dict[str, str] | None = None, *, allow: tuple[int, ...] = ()
    ) -> httpx.Response:
        if self._http is None:
            raise RuntimeError("PlexClient is used outside of 'async with'")
        if not base.usable_token(self._token):
            raise base.auth_failed()
        try:
            # ⚠️ Without quiet_libraries httpx would write the whole address, the folder included, in the trace mode.
            with logs.quiet_libraries():
                response = await http_log.send(self._http, method, path, params=params)
        except httpx.RequestError as exc:
            raise base.unreachable(self.base_url) from exc
        base.raise_for(response, allow=allow)
        return response

    async def _get(self, path: str) -> httpx.Response:
        return await self._send("GET", path)

    async def _container(self, path: str) -> dict[str, Any]:
        data = base.json_of(await self._get(path))
        container = data.get("MediaContainer") if isinstance(data, dict) else None
        if not isinstance(container, dict):
            raise base.wrong_kind()
        return container

    async def identity(self) -> tuple[str, str]:
        """The machine identifier and the version. Only a Plex answers this shape."""
        container = await self._container(IDENTITY_PATH)
        machine = base.text(container.get("machineIdentifier"))
        if not machine:
            raise base.wrong_kind()
        return machine, base.text(container.get("version"), 64)

    async def libraries(self) -> tuple[base.Listed, ...]:
        """The sections with their folders. ⚠️ ``/identity`` answers without a token; this call is the one that proves
        the token."""
        container = await self._container(SECTIONS_PATH)
        listed = []
        directories = container.get("Directory")
        for entry in (directories if isinstance(directories, list) else [])[: base.MAX_LIBRARIES]:
            if not isinstance(entry, dict):
                continue
            key = base.text(str(entry.get("key") or ""), 64)
            if not key:
                continue
            raw_locations = entry.get("Location")
            locations = tuple(
                base.text(location.get("path"), base.PATH_LIMIT)
                for location in (raw_locations if isinstance(raw_locations, list) else [])[: base.MAX_LOCATIONS]
                if isinstance(location, dict) and base.text(location.get("path"), base.PATH_LIMIT)
            )
            listed.append(
                base.Listed(
                    id=key,
                    name=base.text(entry.get("title")) or key,
                    kind=SECTION_KINDS.get(base.text(entry.get("type"), 32)),
                    locations=locations,
                )
            )
        return tuple(listed)

    async def check(self) -> base.Checked:
        _machine, version = await self.identity()
        # Plex's name for itself stands at ``GET /``; nexcrate does not need it and does not ask.
        return base.Checked(server_name="", version=version, libraries=await self.libraries())

    async def refresh(self, section_id: str, path: str | None = None) -> None:
        """Read a section again, or with ``path`` only that folder of it."""
        if not section_id.isalnum():
            raise base.wrong_kind()
        address, params = f"{SECTIONS_PATH}/{section_id}/refresh", ({"path": path} if path else None)
        response = await self._send("GET", address, params, allow=(405,))
        if response.status_code == 405:
            await self._send("POST", address, params)
