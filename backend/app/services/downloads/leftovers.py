"""Folders SABnzbd leaves behind after nexcrate removed a failed job (the owner's finding 5 of 22.09.2026).

The sweep looks at NZBGet too (every Usenet client); NZBGet needs its user name for that.

nexcrate removes a failed job from SABnzbd with its files (``tracking``, ``remove`` with ``delete_files``). On the
owner's instance 7 of 42 such jobs left an empty ``_FAILED_<name>`` folder in the category folder anyway; SABnzbd's
history delete does not always take the folder of a job it moved there. Why exactly those 7 stays open: the sweep does
not depend on it.

**The sweep** runs with the tracking, a minute after a failed job was removed and otherwise every
``SWEEP_EVERY_SECONDS``: for each enabled SABnzbd, its category folder (``category_folder``, through the client's
path mappings) is looked at, and a folder ``_FAILED_<name>`` goes when

* its name belongs to a failed download of this client (SABnzbd shortens long names, so the release name starts with
  it; at least ``MIN_NAME`` characters),
* it is a direct child of the category folder and lies strictly inside a mount point,
* it is no link and holds no link, no video (samples too) and no archive: only what is useless without them.

**The rest of an import** (the owner's finding of 22.09.2026 afternoon): a folder named like an *imported* download
of this client (or the start of its name, as SABnzbd shortens) goes too when it holds nothing useful. An album's import
left cover, ``.sfv`` and ``.m3u`` until the ``.m3u`` stopped counting as a video, and the job was gone from SABnzbd by
then, so nothing else would take the folder. Audio counts as useful here as well as video and archives.

A folder with anything else stays, and so does everything outside the category folder. Every removal is a log line.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select

from ... import crypto
from ...db import SessionLocal
from ...models import Download, DownloadClient
from .. import downloaders, folders, naming
from ..schreibweisen import nfc
from . import files

logger = logging.getLogger("nexcrate.downloads")

FAILED_PREFIX = "_FAILED_"
#: A shortened name must still say which release it was.
MIN_NAME = 12
SWEEP_EVERY_SECONDS = 600.0
#: After a failed job left SABnzbd: SABnzbd may still be deleting.
AFTER_REMOVAL_SECONDS = 60.0

_lock = threading.Lock()
_next_sweep: float | None = None


def clock() -> float:
    return time.monotonic()


def after_removal() -> None:
    """A failed job left SABnzbd: look at its folder a minute from now."""
    global _next_sweep
    with _lock:
        soon = clock() + AFTER_REMOVAL_SECONDS
        _next_sweep = soon if _next_sweep is None else min(_next_sweep, soon)


def reset() -> None:
    global _next_sweep
    with _lock:
        _next_sweep = None


def due() -> bool:
    with _lock:
        return _next_sweep is None or clock() >= _next_sweep


@dataclass(frozen=True)
class _Client:
    id: int
    #: ⚠️ With the decrypted secret.
    target: downloaders.Target = field(repr=False)
    mappings: list[dict[str, str]]
    #: The release names of the client's failed downloads, NFC, case folded, also as the cleaned file name.
    names: tuple[tuple[int, str], ...]
    #: The same for its imported downloads.
    imported: tuple[tuple[int, str], ...] = ()


def _clients() -> list[_Client]:
    with SessionLocal() as db:
        rows = list(
            db.scalars(
                select(DownloadClient).where(
                    DownloadClient.kind.in_([kind for kind in downloaders.KINDS if downloaders.is_usenet(kind)]),
                    DownloadClient.enabled.is_(True),
                )
            )
        )
        found: list[_Client] = []
        for row in rows:
            names: list[tuple[int, str]] = []
            imported: list[tuple[int, str]] = []
            for download_id, title, state in db.execute(
                select(Download.id, Download.release_title, Download.state).where(
                    Download.client_id == row.id, Download.state.in_(("failed", "imported"))
                )
            ).tuples():
                for name in {title, naming.clean_file_name(title)}:
                    (names if state == "failed" else imported).append((int(download_id), nfc(name).casefold()))
            target = downloaders.Target(
                kind=row.kind,
                url=row.url,
                username=row.username or "",
                secret=crypto.decrypt(row.secret) if row.secret else "",
                category=row.category,
            )
            found.append(
                _Client(
                    id=row.id,
                    target=target,
                    mappings=list(row.path_mappings or []),
                    names=tuple(names),
                    imported=tuple(imported),
                )
            )
    return found


def owner_of(folder_name: str, names: tuple[tuple[int, str], ...]) -> int | None:
    """The failed download a ``_FAILED_`` folder belongs to, or None."""
    if not folder_name.upper().startswith(FAILED_PREFIX):
        return None
    return _named(folder_name[len(FAILED_PREFIX) :], names)


def _named(folder_name: str, names: tuple[tuple[int, str], ...]) -> int | None:
    name = nfc(folder_name).casefold()
    if len(name) < MIN_NAME:
        return None
    return next((download_id for download_id, release in names if release.startswith(name)), None)


def useless(folder: Path) -> bool:
    """No link, no video, no archive and no audio anywhere below: what is left of a job without its parts. A playlist
    is no video (``files.POINTER_EXTENSIONS``)."""
    from ..music import tags

    if files.is_link(folder) or not folder.is_dir():
        return False
    for root, dirs, names in os.walk(folder, followlinks=False):
        for name in [*dirs, *names]:
            path = Path(root) / name
            if files.is_link(path):
                return False
        for name in names:
            extension = files.extension_of(name)
            video = extension in files.VIDEO_EXTENSIONS and extension not in files.POINTER_EXTENSIONS
            if video or files.is_archive(name) or tags.is_audio(name):
                return False
    return True


def _category_folder(client: _Client, remote: str) -> Path | None:
    local = files.map_remote(remote, client.mappings)
    for candidate in [local, remote] if local is not None else [remote]:
        path = folders.visible_path(candidate)
        if path is not None and path.is_dir():
            return path
    return None


def clean(client: _Client, category: Path) -> int:
    """The ``_FAILED_`` folders of failed downloads in one category folder that hold nothing useful. Returns how many
    went."""
    removed = 0
    try:
        entries = sorted(category.iterdir())
    except OSError:
        return 0
    mount = folders.containing_mount(category)
    if mount is None:
        return 0
    for entry in entries:
        download_id = owner_of(entry.name, client.names)
        after_import = download_id is None
        if after_import:
            download_id = _named(entry.name, client.imported)
        if download_id is None or not files.strictly_inside(entry, mount) or not useless(entry):
            continue
        resolved = files.resolved(entry)
        if resolved is None or resolved.parent != category:
            continue
        try:
            shutil.rmtree(resolved)
        except OSError:
            logger.info("Download %d: the folder the client left could not be removed", download_id)
            continue
        removed += 1
        if after_import:
            logger.info("Download %d: what its import left in the category folder is removed", download_id)
            continue
        logger.info(
            "Download %d: the empty folder SABnzbd left after the failure is removed (%s)", download_id, entry.name
        )
    return removed


async def sweep() -> int:
    """Look at the category folder of every enabled SABnzbd once. Returns how many folders went."""
    global _next_sweep
    with _lock:
        _next_sweep = clock() + SWEEP_EVERY_SECONDS
    removed = 0
    for client in await asyncio.to_thread(_clients):
        if not client.names and not client.imported:
            continue
        try:
            async with downloaders.open_client(client.target) as sab:
                remote = await sab.category_folder()
        except downloaders.ClientError as exc:
            logger.info("Download client %d: its category folder is not known: %s", client.id, exc.code)
            continue
        if not remote:
            continue
        category = await asyncio.to_thread(_category_folder, client, remote)
        if category is None or category.name.casefold() != client.target.category.casefold():
            continue
        removed += await asyncio.to_thread(clean, client, category)
    return removed
