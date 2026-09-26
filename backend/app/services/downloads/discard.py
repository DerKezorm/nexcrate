"""A finished download nexcrate did not file goes with its files (the owner's finding of 26.09.2026).

SABnzbd takes a completed job out of its history but keeps its folder in ``complete/<category>``: ``del_files`` only
deletes the folder of a failed job. NZBGet keeps a finished job's folder as well. Radarr deletes that folder itself, and
so does nexcrate: when the owner removes such a download with its files (``actions.remove``, also ``remove_and_search``
of ``/api/v1`` and removing a title). A torrent's files are its client's to delete.

* **The job** (``job_folder``): the path the client reported, through the client's path mappings, and only when the
  client finished the job (``completed_at``). A reported video stands for its folder, as the import reads it. Only a
  direct child of a folder named like the client's category, strictly inside a mount point, and no link or junction
  itself. ``foreign`` uses the same for jobs no download follows.
* **Never** when a folder files are filed into lies in the job or around it (``protected``): the title's version
  folders, every library folder and every folder of a library rule. Never while an import runs: the caller deletes only
  after it moved the download out of the states an import claims.
* **Deleting** never follows a link: ``rmtree`` removes a link inside the job, not what it points to.

Log lines carry ids, never a name or a path.
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ... import crypto
from ...models import Download, DownloadClient, Version, VersionDefinition
from .. import downloaders, folder_rules, folders
from . import files, store

logger = logging.getLogger("nexcrate.downloads")


def job_folder(reported: str | None, mappings: Iterable[Mapping[str, str]], category: str) -> Path | None:
    """The finished job's folder or file as nexcrate sees it, when it lies directly in a folder named like the
    category inside a mount point and is no link; else None, and then nothing on disk is touched."""
    if not reported or not category:
        return None
    mapped = files.map_remote(reported, [dict(mapping) for mapping in mappings])
    wanted = category.casefold()
    for candidate in ([mapped] if mapped is not None else []) + [Path(reported)]:
        raw = Path(candidate)
        local = folders.visible_path(raw)
        if local is None:
            continue
        # A video SABnzbd names stands for its folder; a folder is the job itself.
        job_raw = raw.parent if local.is_file() and raw.parent.name.casefold() != wanted else raw
        if job_raw.parent.name.casefold() != wanted:
            continue
        category_folder = folders.visible_path(job_raw.parent)
        if category_folder is None or not category_folder.is_dir() or category_folder.name.casefold() != wanted:
            continue
        job = category_folder / job_raw.name
        mount = folders.containing_mount(category_folder)
        if mount is None or files.is_link(job) or not os.path.lexists(job) or not files.strictly_inside(job, mount):
            continue
        return job
    return None


def protected(db: OrmSession, title_id: int) -> tuple[Path, ...]:
    """The folders files are filed into that a job folder must neither hold nor lie in: the title's version folders,
    every library folder and every folder of a library rule."""
    found = {value or "" for value in db.scalars(select(Version.root_folder).where(Version.title_id == title_id))}
    for definition in db.scalars(select(VersionDefinition)):
        found.add(definition.folder or "")
        found.update(rule.folder for rule in folder_rules.load(db, definition.id))
    return tuple(Path(value) for value in sorted(found) if value and value.strip())


def delete(download_id: int, path: Path, keep: Iterable[Path]) -> bool:
    """The job folder or file, when no folder of ``keep`` lies in it or around it. Returns whether it went."""
    if files.is_link(path) or not os.path.lexists(path):
        return False
    for folder in keep:
        if files.inside(folder, path) or files.inside(path, folder):
            logger.info("Download %d: its job folder holds or lies in a library folder; it stays", download_id)
            return False
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    except OSError:
        logger.warning("Download %d: its job folder could not be deleted", download_id)
        return False
    logger.info("Download %d: its job folder in the category folder is deleted", download_id)
    return True


@dataclass(frozen=True)
class Leftover:
    """What of a download is still with its client: the job, and for a finished Usenet job its folder."""

    download_id: int
    client_download_id: str
    #: ⚠️ With the decrypted secret; None when the client is gone.
    target: downloaders.Target | None = field(repr=False)
    folder: Path | None
    keep: tuple[Path, ...] = ()


def leftover(db: OrmSession, row: Download) -> Leftover:
    """Read inside the caller's session; touches nothing."""
    client = db.get(DownloadClient, row.client_id) if row.client_id is not None else None
    target = None
    folder = None
    keep: tuple[Path, ...] = ()
    if client is not None:
        target = downloaders.Target(
            kind=client.kind,
            url=client.url,
            username=client.username or "",
            secret=crypto.decrypt(client.secret) if client.secret else "",
            category=client.category,
            login_blocked=store.login_blocked(client),
        )
        if downloaders.is_usenet(client.kind) and row.completed_at is not None:
            folder = job_folder(row.reported_path, client.path_mappings or [], client.category)
            keep = protected(db, row.title_id) if folder is not None else ()
    return Leftover(row.id, row.client_download_id, target, folder, keep)


async def from_client(left: Leftover) -> None:
    """The job leaves its client with its files. Raises ``downloaders.ClientError``."""
    if left.target is None or not left.client_download_id:
        return
    async with downloaders.open_client(left.target) as client:
        await client.remove(left.client_download_id, delete_files=True)


def delete_folder(left: Leftover) -> bool:
    """The finished Usenet job's folder, which its client keeps. Only after the download left the import's states."""
    return left.folder is not None and delete(left.download_id, left.folder, left.keep)
