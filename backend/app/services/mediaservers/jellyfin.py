"""A Jellyfin or an Emby: who it is, its libraries, and "these folders changed".

Both grew from the same code and answer nearly the same addresses: ``GET /System/Info``,
``POST /Library/Media/Updated`` with the folders, and ``POST /Library/Refresh`` for everything. They differ in the
header of the API key, in how they name themselves, and in where the libraries are listed: Jellyfin at
``GET /Library/VirtualFolders``, today's Emby at ``GET /Library/VirtualFolders/Query`` (an Emby that does not know
that address is asked the old one). ⚠️ None of this
is measured on a real server.

For music (``music_batch``) two more readings, measured on the owner's Jellyfin (16.11.2026): the library listing
carries ``RefreshStatus`` (``Active`` while the library is read, with ``RefreshProgress``), and
``GET /Items?ParentId=<library>&Fields=Path`` lists the folders right below a library with their paths, here the
artist folders (372 in 0.2 s). Emby: not measured; an answer without these fields reads as "not reading", and a
refused listing makes ``music_batch`` tell the folders as before.

⚠️ The key travels in a header only. Both also accept ``?api_key=``; a URL ends up in logs and proxies.
"""

from __future__ import annotations

from typing import Any, Self

import httpx

from .. import http_log, logs
from ..radarr import normalize_base_url
from . import base

INFO_PATH = "/System/Info"
FOLDERS_PATH = "/Library/VirtualFolders"
#: Emby's reference lists only this one for reading; it answers ``{"Items": [...], "TotalRecordCount": n}``.
FOLDERS_QUERY_PATH = "/Library/VirtualFolders/Query"
UPDATED_PATH = "/Library/Media/Updated"
ITEMS_PATH = "/Items"
#: Folders right below a library read per page, and at most.
ITEMS_PAGE = 2_000
MAX_TOP_FOLDERS = 40_000
REFRESH_PATH = "/Library/Refresh"
#: ``CollectionType`` and what it is here. A library of mixed content has none.
COLLECTION_KINDS = {"movies": "movie", "tvshows": "series", "music": "music"}
#: Jellyfin names itself in ``ProductName``; Emby's ``System/Info`` has no such field.
JELLYFIN_PRODUCT = "jellyfin"
#: How many folders one ``Media/Updated`` call carries at most.
MAX_UPDATES = 500


def auth_headers(kind: str, key: str) -> dict[str, str]:
    if kind == "jellyfin":
        return {"Authorization": f'MediaBrowser Token="{key}"'}
    return {"X-Emby-Token": key}


class JellyfinClient:
    """Use as ``async with JellyfinClient(kind, url, key) as server: ...``; ``kind`` is jellyfin or emby."""

    def __init__(self, kind: str, base_url: str, key: str, *, timeout: httpx.Timeout | float = base.TIMEOUT) -> None:
        self.kind = kind
        self.base_url = normalize_base_url(base_url)
        self._key = key
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> Self:
        self._http = http_log.client(
            self.kind,
            base_url=self.base_url,
            timeout=self._timeout,
            # No path, query or body in any line: the bodies carry the folders of titles.
            origin_only=True,
            headers={**auth_headers(self.kind, self._key), "Accept": "application/json"},
            follow_redirects=False,
        )
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _send(self, method: str, path: str, *, allow: tuple[int, ...] = (), **kwargs: Any) -> httpx.Response:
        if self._http is None:
            raise RuntimeError("JellyfinClient is used outside of 'async with'")
        # A quotation mark would end the value of Jellyfin's header early.
        if not base.usable_token(self._key) or '"' in self._key:
            raise base.auth_failed()
        try:
            # As for Plex: the lines of a media server carry the origin only, httpx's own lines included.
            with logs.quiet_libraries():
                response = await http_log.send(self._http, method, path, **kwargs)
        except httpx.RequestError as exc:
            raise base.unreachable(self.base_url) from exc
        base.raise_for(response, allow=allow)
        return response

    async def info(self) -> tuple[str, str]:
        """The server's name and version. A Jellyfin under "Emby", or the other way round, is the wrong kind."""
        data = base.json_of(await self._send("GET", INFO_PATH))
        if not isinstance(data, dict) or not base.text(data.get("Version"), 64):
            raise base.wrong_kind()
        is_jellyfin = JELLYFIN_PRODUCT in base.text(data.get("ProductName")).lower()
        if is_jellyfin != (self.kind == "jellyfin"):
            raise base.wrong_kind()
        return base.text(data.get("ServerName")), base.text(data.get("Version"), 64)

    async def _folder_entries(self) -> list[Any]:
        data: Any = None
        if self.kind == "emby":
            response = await self._send("GET", FOLDERS_QUERY_PATH, allow=(404, 405))
            if response.status_code == 200:
                answer = base.json_of(response)
                data = answer.get("Items") if isinstance(answer, dict) else None
        if data is None:
            data = base.json_of(await self._send("GET", FOLDERS_PATH))
        if not isinstance(data, list):
            raise base.wrong_kind()
        return data

    async def libraries(self) -> tuple[base.Listed, ...]:
        data = await self._folder_entries()
        listed = []
        for entry in data[: base.MAX_LIBRARIES]:
            if not isinstance(entry, dict):
                continue
            name = base.text(entry.get("Name"))
            identifier = base.text(entry.get("ItemId"), 64) or name
            if not identifier:
                continue
            raw_locations = entry.get("Locations")
            locations = tuple(
                base.text(location, base.PATH_LIMIT)
                for location in (raw_locations if isinstance(raw_locations, list) else [])[: base.MAX_LOCATIONS]
                if base.text(location, base.PATH_LIMIT)
            )
            kind = COLLECTION_KINDS.get(base.text(entry.get("CollectionType"), 32).lower())
            listed.append(base.Listed(id=identifier, name=name or identifier, kind=kind, locations=locations))
        return tuple(listed)

    async def check(self) -> base.Checked:
        name, version = await self.info()
        return base.Checked(server_name=name, version=version, libraries=await self.libraries())

    async def updated(self, paths: list[str]) -> None:
        """Tell the server these folders changed; it reads them again."""
        updates = [{"Path": path, "UpdateType": "Modified"} for path in paths[:MAX_UPDATES]]
        if updates:
            await self._send("POST", UPDATED_PATH, json={"Updates": updates})

    async def reading(self) -> set[str]:
        """The ids of the libraries the server reads right now (``RefreshStatus`` ``Active``)."""
        found = set()
        for entry in (await self._folder_entries())[: base.MAX_LIBRARIES]:
            if not isinstance(entry, dict):
                continue
            identifier = base.text(entry.get("ItemId"), 64) or base.text(entry.get("Name"))
            if identifier and base.text(entry.get("RefreshStatus"), 32).lower() == "active":
                found.add(identifier)
        return found

    async def top_folders(self, library_id: str) -> list[str]:
        """The paths of the folders right below a library, as the server knows them: for music the artist folders."""
        found: list[str] = []
        while len(found) < MAX_TOP_FOLDERS:
            params = {
                "ParentId": library_id,
                "Fields": "Path",
                "EnableImages": "false",
                "EnableUserData": "false",
                "StartIndex": str(len(found)),
                "Limit": str(ITEMS_PAGE),
            }
            answer = base.json_of(await self._send("GET", ITEMS_PATH, params=params))
            items = answer.get("Items") if isinstance(answer, dict) else None
            if not isinstance(items, list):
                raise base.wrong_kind()
            found.extend(
                base.text(item.get("Path"), base.PATH_LIMIT)
                for item in items
                if isinstance(item, dict) and base.text(item.get("Path"), base.PATH_LIMIT)
            )
            if len(items) < ITEMS_PAGE:
                break
        return found

    async def refresh_all(self) -> None:
        """Read every library again."""
        await self._send("POST", REFRESH_PATH)
