"""Tell the media servers that folders changed: after filing a download, after a rename run and its undo.

⚠️ A media server never holds up or fails anything. ``request`` is called after the commit, never raises, and does
its work in a thread of its own with short timeouts; what goes wrong is a log line and the row's
``last_error_code``. The file is filed whatever the server says.

What is told ("So gebaut"):

* Only enabled servers, and only libraries whose switch is on.
* The folder is translated with the owner's path pairs and looked up in the locations the server listed for its
  libraries. It lies in a library with the switch on: that folder alone is read again (Plex: ``refresh?path=``;
  Jellyfin and Emby: ``Library/Media/Updated``). It lies in a library with the switch off: nothing, the owner said no.
* ⚠️ It lies in no library of the server: nothing is guessed. The libraries of that media kind with the switch on are
  read again as a whole, and the row says ``mediaserver_path_unmatched``, so the owner sees that a path pair is
  missing. A server without such a library is left alone.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Any

from sqlalchemy import func, select

from ... import crypto
from ...db import SessionLocal
from ...models import MediaServer, utcnow
from ..radarr import SourceUrlInvalid
from . import base, paths
from .jellyfin import JellyfinClient
from .plex import PlexClient

logger = logging.getLogger("nexcrate.mediaservers")

KINDS = ("movie", "series", "music")
#: More folders than this in one notification: Plex reads the sections again as a whole instead of folder by folder.
MAX_TARGETED = 20
PATH_UNMATCHED = "mediaserver_path_unmatched"

_lock = threading.Lock()
_threads: list[threading.Thread] = []


@dataclass(frozen=True)
class Stored:
    id: int
    kind: str
    url: str
    #: ⚠️ Decrypted.
    token: str = field(repr=False)
    libraries: list[dict[str, Any]]
    path_mappings: list[dict[str, str]]


@dataclass(frozen=True)
class Plan:
    """What one server is told. ``targeted`` maps a library id to the folders as the server sees them."""

    targeted: dict[str, list[str]]
    whole: list[str]
    unmatched: bool

    @property
    def empty(self) -> bool:
        return not self.targeted and not self.whole


def plan(libraries: list[dict[str, Any]], mappings: list[dict[str, str]], kind: str, folders: Iterable[str]) -> Plan:
    known = paths.stored_libraries(libraries)
    switched_on = [library for library in known if library.refresh]
    targeted: dict[str, list[str]] = {}
    unmatched = False
    if not switched_on:
        return Plan({}, [], False)
    for folder in folders:
        remote = paths.to_remote(folder, mappings)
        library = paths.library_of(remote, known)
        if library is None:
            unmatched = True
        elif library.refresh and remote not in targeted.setdefault(library.id, []):
            targeted[library.id].append(remote)
    whole = [library.id for library in switched_on if library.kind in (kind, None)] if unmatched else []
    # A library read again as a whole needs no folder of its own any more.
    for library_id in whole:
        targeted.pop(library_id, None)
    return Plan(targeted, whole, unmatched and bool(whole))


# --- The threads ------------------------------------------------------------------------------------------ #


def _any_enabled() -> bool:
    with SessionLocal() as db:
        return bool(db.scalar(select(func.count(MediaServer.id)).where(MediaServer.enabled.is_(True))))


def request(kind: str, folders: Iterable[Path | PurePath | str]) -> bool:
    """Tell every enabled server about ``folders`` of a media kind. Returns whether a thread started. Never raises."""
    try:
        wanted = sorted({PurePath(folder).as_posix() for folder in folders if str(folder)})
        if kind not in KINDS or not wanted or not _any_enabled():
            return False
        thread = threading.Thread(
            target=contextvars.copy_context().run,
            args=(_execute, kind, wanted),
            name="mediaserver-notify",
            daemon=True,
        )
        with _lock:
            _threads[:] = [existing for existing in _threads if existing.is_alive()]
            _threads.append(thread)
        thread.start()
    except Exception:
        # The file is filed; a media server must never undo or fail that.
        logger.warning("Telling the media servers could not be started", exc_info=True)
        return False
    return True


def wait_idle(timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    with _lock:
        threads = list(_threads)
    for thread in threads:
        if thread.ident is not None:
            thread.join(max(0.0, deadline - time.monotonic()))
    with _lock:
        _threads[:] = [thread for thread in _threads if thread.is_alive()]
        return not _threads


def _load() -> list[Stored]:
    with SessionLocal() as db:
        rows = db.scalars(select(MediaServer).where(MediaServer.enabled.is_(True)).order_by(MediaServer.id))
        return [
            Stored(
                id=row.id,
                kind=row.kind,
                url=row.url,
                token=crypto.decrypt(row.token) if row.token else "",
                libraries=[dict(entry) for entry in (row.libraries or [])],
                path_mappings=[dict(entry) for entry in (row.path_mappings or [])],
            )
            for row in rows
        ]


def _record(server_id: int, result: str, code: str | None) -> None:
    moment = utcnow()
    with SessionLocal() as db:
        row = db.get(MediaServer, server_id)
        if row is None:
            return
        row.last_notify_at = moment
        row.last_notify_result = result
        row.last_error_code = code
        db.commit()


def _execute(kind: str, folders: list[str]) -> None:
    try:
        servers = _load()
    except Exception:
        logger.warning("The media servers could not be read", exc_info=True)
        return
    for server in servers:
        try:
            wanted = plan(server.libraries, server.path_mappings, kind, folders)
            if wanted.empty:
                continue
            whole = asyncio.run(_tell(server, wanted))
            result, code = ("library" if whole else "folder"), (PATH_UNMATCHED if wanted.unmatched else None)
            logger.info(
                "Media server %d (%s) was told: %d folders, %d whole libraries",
                server.id,
                server.kind,
                sum(len(found) for found in wanted.targeted.values()),
                len(wanted.whole),
            )
        except base.ServerError as exc:
            result, code = "failed", exc.code
            logger.warning("Media server %d (%s) could not be told: %s", server.id, server.kind, exc.code)
        except SourceUrlInvalid:
            result, code = "failed", "mediaserver_unreachable"
            logger.warning("Media server %d (%s) has an address that does not work", server.id, server.kind)
        except Exception as exc:  # noqa: BLE001 - the file is filed whatever goes wrong here
            # Never more than a log line and the row's code; the type only, an error text can repeat an address.
            result, code = "failed", "mediaserver_http_error"
            logger.warning("Media server %d (%s) could not be told: %s", server.id, server.kind, type(exc).__name__)
        try:
            _record(server.id, result, code)
        except Exception:
            logger.warning("The result for media server %d could not be stored", server.id, exc_info=True)


async def _tell(server: Stored, wanted: Plan) -> bool:
    """Returns whether a library was read again as a whole."""
    if server.kind == "plex":
        async with PlexClient(server.url, server.token) as plex:
            many = sum(len(found) for found in wanted.targeted.values()) > MAX_TARGETED
            for library_id, found in wanted.targeted.items():
                if many:
                    await plex.refresh(library_id)
                    continue
                for folder in found:
                    await plex.refresh(library_id, folder)
            for library_id in wanted.whole:
                await plex.refresh(library_id)
        return many or bool(wanted.whole)
    async with JellyfinClient(server.kind, server.url, server.token) as other:
        folders = [folder for found in wanted.targeted.values() for folder in found]
        if folders:
            await other.updated(folders)
        if wanted.whole:
            # ⚠️ Jellyfin and Emby have one call for everything; there is none for a single library among the
            # addresses nexcrate uses.
            await other.refresh_all()
    return bool(wanted.whole)
