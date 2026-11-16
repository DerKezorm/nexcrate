"""Music for Jellyfin and Emby: collected for a few minutes, then told once (16.11.2026).

Found on the owner's Jellyfin: ``Library/Media/Updated`` with an album folder whose artist folder Jellyfin does not
know yet makes it read the nearest folder it knows, the whole music library: one full pass, 30 to 40 minutes over NFS
for about 4,100 albums. While the owner loaded many albums, three such passes stood in Jellyfin's queue one after the
other. An album of a known artist told by its folder is read in seconds; that stays.

So, for music and only for Jellyfin and Emby (Plex and Kodi read the folder told and nothing more):

* **Collected.** ``notify`` hands the album folders here instead of telling them at once. They are told
  ``DEBOUNCE_SECONDS`` after the last one came, and never later than ``MAX_WAIT_SECONDS`` after the first.
* **Known artists by their album folder.** Before telling, the folders right below the library are read
  (``JellyfinClient.top_folders``). A folder whose parent or itself is among them is told as before.
* **New artists once.** All album folders of artists the server does not know make one call with the library's
  location: that is the pass the server makes anyway, now one for all of them instead of one per album.
* **Never on top of a running pass.** While the server reads that library (``RefreshStatus`` ``Active``), the new
  artists wait and are looked at again every ``SCAN_POLL_SECONDS``. When the pass ends, they are sorted again: what
  the pass found is told by its folder, what is still unknown gets the one call.
* A folder that lies in no library of the server reads the music libraries again as a whole, as ``notify`` does,
  also not on top of a running pass.
* A server that refuses the listing of folders (an Emby, not measured) is told every folder as before.

What waits lives in memory only: after a restart the server's own scheduled pass finds it. As everywhere in
``notify``: nothing here ever holds up or fails a download, every error is a log line and the row's code.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..radarr import SourceUrlInvalid
from . import base, paths
from .jellyfin import JellyfinClient

if TYPE_CHECKING:
    from .notify import Stored

logger = logging.getLogger("nexcrate.mediaservers")

#: The servers whose music is collected here.
KINDS = ("jellyfin", "emby")
DEBOUNCE_SECONDS = 180.0
MAX_WAIT_SECONDS = 900.0
SCAN_POLL_SECONDS = 60.0

_lock = threading.Lock()
_wake = threading.Event()
#: Per server id the album folders as nexcrate sees them.
_pending: dict[int, set[str]] = {}
_first = 0.0
_due = 0.0
_thread: threading.Thread | None = None
_generation = 0


@dataclass
class Outcome:
    """What one round told one server, and the folders that wait for the server's pass to end."""

    folders: list[str] = field(default_factory=list)
    roots: list[str] = field(default_factory=list)
    whole: bool = False
    unmatched: bool = False
    waiting: set[str] = field(default_factory=set)

    @property
    def told(self) -> bool:
        return bool(self.folders or self.roots or self.whole)


def add(server_id: int, folders: Iterable[str]) -> None:
    """Collect album folders for a server; they are told after the pause. Never raises."""
    global _first, _due, _thread
    try:
        wanted = {folder for folder in folders if folder}
        if not wanted:
            return
        now = time.monotonic()
        with _lock:
            if not _pending:
                _first = now
            _pending.setdefault(server_id, set()).update(wanted)
            _due = min(now + DEBOUNCE_SECONDS, _first + MAX_WAIT_SECONDS)
            if _thread is None or not _thread.is_alive():
                _thread = threading.Thread(
                    target=contextvars.copy_context().run,
                    args=(_work, _generation),
                    name="mediaserver-music",
                    daemon=True,
                )
                _thread.start()
        _wake.set()
    except Exception:
        logger.warning("Music for the media servers could not be collected", exc_info=True)


def waiting() -> dict[int, set[str]]:
    with _lock:
        return {server_id: set(found) for server_id, found in _pending.items()}


def reset() -> None:
    """Forget what waits and let the worker end (tests)."""
    global _generation, _thread
    with _lock:
        _generation += 1
        _pending.clear()
        thread, _thread = _thread, None
    _wake.set()
    if thread is not None and thread.ident is not None:
        thread.join(5)


def wait_idle(timeout: float = 30.0) -> bool:
    """Whether nothing waits any more and the worker ended within ``timeout``."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with _lock:
            thread = _thread
            if not _pending and (thread is None or not thread.is_alive()):
                return True
        time.sleep(0.02)
    return False


def _work(generation: int) -> None:
    global _first, _due, _thread
    while True:
        with _lock:
            if generation != _generation:
                return
            if not _pending:
                if _thread is threading.current_thread():
                    _thread = None
                return
            wait = _due - time.monotonic()
            if wait <= 0:
                batch = {server_id: sorted(found) for server_id, found in _pending.items()}
                _pending.clear()
        if wait > 0:
            # An album that comes meanwhile wakes the worker, which then waits for the new moment.
            _wake.wait(wait)
            _wake.clear()
            continue
        later: dict[int, set[str]] = {}
        for server_id, folders in batch.items():
            left = _flush(server_id, folders)
            if left:
                later[server_id] = left
        with _lock:
            if generation != _generation:
                return
            if later:
                now = time.monotonic()
                if not _pending:
                    _first = now
                    _due = now + SCAN_POLL_SECONDS
                else:
                    _due = min(_due, now + SCAN_POLL_SECONDS)
                for server_id, left in later.items():
                    _pending.setdefault(server_id, set()).update(left)


def _flush(server_id: int, folders: list[str]) -> set[str]:
    """Tell one server; returns the folders that wait for its pass to end. Never raises."""
    from . import notify

    try:
        server = next((item for item in notify._load() if item.id == server_id), None)
    except Exception:
        logger.warning("The media servers could not be read", exc_info=True)
        return set()
    if server is None or server.kind not in KINDS:
        return set()
    try:
        outcome = asyncio.run(_tell(server, folders))
    except base.ServerError as exc:
        notify._record(server.id, "failed", exc.code)
        logger.warning("Media server %d (%s) could not be told: %s", server.id, server.kind, exc.code)
        return set()
    except SourceUrlInvalid:
        notify._record(server.id, "failed", "mediaserver_unreachable")
        logger.warning("Media server %d (%s) has an address that does not work", server.id, server.kind)
        return set()
    except Exception as exc:  # noqa: BLE001 - the album is filed whatever goes wrong here
        notify._record(server.id, "failed", "mediaserver_http_error")
        logger.warning("Media server %d (%s) could not be told: %s", server.id, server.kind, type(exc).__name__)
        return set()
    if outcome.told:
        result = "library" if outcome.roots or outcome.whole else "folder"
        notify._record(server.id, result, notify.PATH_UNMATCHED if outcome.unmatched and outcome.whole else None)
    logger.info(
        "Media server %d (%s) was told music: %d album folders, %d library folders%s, %d wait for its pass",
        server.id,
        server.kind,
        len(outcome.folders),
        len(outcome.roots),
        ", every library" if outcome.whole else "",
        len(outcome.waiting),
    )
    return outcome.waiting


async def _tell(server: Stored, folders: list[str]) -> Outcome:
    known = paths.stored_libraries(server.libraries)
    by_id = {library.id: library for library in known}
    switched_on = [library for library in known if library.refresh and library.kind in ("music", None)]
    outcome = Outcome()
    grouped: dict[str, list[tuple[str, str]]] = {}
    unmatched: list[str] = []
    for folder in folders:
        remote = paths.to_remote(folder, server.path_mappings)
        library = paths.library_of(remote, known)
        if library is None:
            unmatched.append(folder)
        elif library.refresh:
            grouped.setdefault(library.id, []).append((folder, remote))
    if not grouped and not (unmatched and switched_on):
        return outcome
    async with JellyfinClient(server.kind, server.url, server.token) as client:
        reading = await client.reading()
        for library_id, entries in grouped.items():
            library = by_id[library_id]
            try:
                tops: set[tuple[str, ...]] | None = {paths.key(path) for path in await client.top_folders(library_id)}
            except base.ServerError:
                # The server does not list its folders: every folder is told as before.
                tops = None
            new: list[tuple[str, str]] = []
            for folder, remote in entries:
                key = paths.key(remote)
                if tops is None or key in tops or key[:-1] in tops:
                    outcome.folders.append(remote)
                else:
                    new.append((folder, remote))
            if not new:
                continue
            if library_id in reading:
                outcome.waiting.update(folder for folder, _remote in new)
                continue
            # The whole library is read: its known folders need no call of their own.
            roots = {paths.location_of(remote, library) for _folder, remote in new} - {None}
            outcome.roots.extend(sorted(root for root in roots if root and root not in outcome.roots))
            outcome.folders = [remote for remote in outcome.folders if paths.library_of(remote, known) is not library]
        if unmatched and switched_on:
            outcome.unmatched = True
            if reading & {library.id for library in switched_on}:
                outcome.waiting.update(unmatched)
            else:
                outcome.whole = True
        if outcome.whole:
            # ⚠️ One call for everything, as in ``notify``: every library is read, folders need nothing more.
            outcome.folders, outcome.roots = [], []
            await client.refresh_all()
            return outcome
        if outcome.folders:
            await client.updated(outcome.folders)
        if outcome.roots:
            await client.updated(outcome.roots)
    return outcome
