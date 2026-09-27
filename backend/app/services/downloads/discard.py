"""A finished download nexcrate did not file goes with its files (the owner's finding of 26.09.2026).

SABnzbd takes a completed job out of its history but keeps its folder in ``complete/<category>``: ``del_files`` only
deletes the folder of a failed job. NZBGet keeps a finished job's folder as well. Radarr deletes that folder itself, and
so does nexcrate: when the owner removes such a download with its files (``actions.remove``, also ``remove_and_search``
of ``/api/v1`` and removing a title), and when a download fails for a broken archive (below). A torrent's files are
its client's to delete.

* **The job** (``job_folder``): the path the client reported, through the client's path mappings, and only when the
  client finished the job (``completed_at``). A reported video stands for its folder, as the import reads it. Only a
  direct child of a folder named like the client's category, after resolving too, strictly inside a mount point, and
  no link or junction itself; a path with a ``.`` or ``..`` part, or a name with a separator, is none. ``foreign`` uses
  the same for jobs no download follows.
* **Never** when a folder files are filed into lies in the job or around it (``protected``): the title's version
  folders, every library folder and every folder of a library rule. Never while an import runs: the caller deletes only
  after it moved the download out of the states an import claims. Never when another download still in the works, or
  a foreign job, points at the same folder (``shared``): an old download the client lost and a new one of the same
  release name share it.
* **Deleting** never follows a link: ``rmtree`` removes a link inside the job, not what it points to.
* **A broken archive** (``broken_reason``, the owner's decision of 26.09.2026): a finished download whose archive is
  certainly broken fails as a failure its client reported would, as in Radarr: volumes are missing by their names (the
  problem ``packed`` with ``incomplete`` and ``volumes_missing``, see ``unpacking``) or it wants a password
  (``encrypted``). The release goes on the blocklist, the history
  says ``failed``, the replacement searches another release, and the job leaves its client with its files, a torrent
  too. Everything else stays a problem for the owner: ``unsupported`` is an archive only nexcrate's tool cannot unpack;
  ``broken`` is what a damaged archive looks like, and just as much a tool that crashed, ran out of time or could not
  write; ``unsafe``, ``nested`` and ``too_large`` are archives nexcrate reads well and refuses by its own rules; an
  ``incomplete`` without ``volumes_missing`` may be a share that went away. A download that filed or placed a file
  already stays a problem too (``filed_something``).

Log lines carry ids, never a name or a path.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ... import crypto
from ...db import SessionLocal
from ...models import (
    Download,
    DownloadAudioFile,
    DownloadClient,
    DownloadFile,
    ForeignJob,
    Version,
    VersionDefinition,
)
from .. import downloaders, folder_rules, folders
from ..automatic import replacement
from . import files, store

logger = logging.getLogger("nexcrate.downloads")

#: The problems that mean a finished download is certainly broken, by code and reason, and the failure each becomes.
#: ``packed`` with ``incomplete`` only with ``volumes_missing``.
BROKEN = {("packed", "incomplete"): "archive_incomplete", ("encrypted", None): "encrypted"}
#: Names a job folder never has.
_ODD_NAMES = ("", ".", "..")


def broken_reason(code: str, values: Mapping[str, Any] | None) -> str | None:
    """The failure reason of a problem that means the download is certainly broken, else None."""
    given = values or {}
    reason = given.get("reason") if code == "packed" else None
    if reason == "incomplete" and not given.get("volumes_missing"):
        return None
    return BROKEN.get((code, reason))


def filed_something(db: OrmSession, row: Download) -> bool:
    """Whether the download filed a file or began to place one (a video, an episode, a track): then it stays a
    problem, as what it filed came from it. An album counts its files only once it is done, so its rows are asked."""
    if row.filed_count:
        return True
    placed = db.scalar(
        select(DownloadFile.id)
        .where(DownloadFile.download_id == row.id, DownloadFile.decision.in_(("filed", "placing")))
        .limit(1)
    )
    tracks = db.scalar(
        select(DownloadAudioFile.id)
        .where(
            DownloadAudioFile.download_id == row.id,
            (DownloadAudioFile.decision == "placing") | DownloadAudioFile.track_file_id.is_not(None),
        )
        .limit(1)
    )
    return placed is not None or tracks is not None


def _plain(path: str) -> bool:
    """No ``.`` or ``..`` part: the path says where it is without a way up or around."""
    return not any(part in (".", "..") for part in path.replace("\\", "/").split("/"))


def job_folder(reported: str | None, mappings: Iterable[Mapping[str, str]], category: str) -> Path | None:
    """The finished job's folder or file as nexcrate sees it, when it lies directly in a folder named like the
    category inside a mount point and is no link; else None, and then nothing on disk is touched."""
    if not reported or not category or not _plain(reported):
        return None
    mapped = files.map_remote(reported, [dict(mapping) for mapping in mappings])
    wanted = category.casefold()
    for candidate in ([mapped] if mapped is not None else []) + [Path(reported)]:
        raw = Path(candidate)
        local = folders.visible_path(raw) if _plain(str(raw)) else None
        if local is None:
            continue
        # A video SABnzbd names stands for its folder; a folder is the job itself.
        job_raw = raw.parent if local.is_file() and raw.parent.name.casefold() != wanted else raw
        name = job_raw.name
        if name in _ODD_NAMES or "/" in name or "\\" in name or job_raw.parent.name.casefold() != wanted:
            continue
        category_folder = folders.visible_path(job_raw.parent)
        if category_folder is None or not category_folder.is_dir() or category_folder.name.casefold() != wanted:
            continue
        job = category_folder / name
        mount = folders.containing_mount(category_folder)
        if mount is None or files.is_link(job) or not os.path.lexists(job):
            continue
        # After resolving as well: a direct child of the category folder, never the folder itself or one above.
        resolved = files.resolved(job)
        if resolved is None or resolved == category_folder or resolved.parent != category_folder:
            continue
        if not files.strictly_inside(resolved, mount):
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


def _followed(reported: str, mappings: Iterable[Mapping[str, str]], category: str) -> Path | None:
    """The direct child of the category folder a reported path lies in, however deep: only to compare with, never to
    delete. None when the path has a ``.`` or ``..`` part or no folder named like the category."""
    if not _plain(reported):
        return None
    mapped = files.map_remote(reported, [dict(mapping) for mapping in mappings])
    wanted = category.casefold()
    for candidate in ([mapped] if mapped is not None else []) + [Path(reported)]:
        raw = Path(candidate)
        child = next((part for part in (raw, *raw.parents) if part.parent.name.casefold() == wanted), None)
        if child is None or child.name in _ODD_NAMES:
            continue
        category_folder = folders.visible_path(child.parent)
        if category_folder is not None and category_folder.is_dir():
            return category_folder / child.name
    return None


def shared(download_id: int, folder: Path) -> bool:
    """Whether another download still in the works, or a foreign job, points at the same folder, or at a file anywhere
    in it. Rows whose reported path holds the folder's name, case ignored, are looked at closely; the resolved paths
    decide."""
    target = files.resolved(folder)
    if target is None:
        return True
    name = folder.name.casefold()
    with SessionLocal() as db:
        others = list(
            db.execute(
                select(Download.client_id, Download.reported_path).where(
                    Download.id != download_id,
                    Download.state.in_(store.UNFINISHED_STATES),
                    Download.reported_path.is_not(None),
                )
            ).tuples()
        )
        others += list(
            db.execute(
                select(ForeignJob.client_id, ForeignJob.reported_path).where(ForeignJob.reported_path.is_not(None))
            ).tuples()
        )
        clients = {
            client.id: (list(client.path_mappings or []), client.category)
            for client in db.scalars(select(DownloadClient))
        }
    for client_id, reported in others:
        if client_id not in clients or not reported or name not in reported.casefold():
            continue
        mappings, category = clients[client_id]
        other = _followed(reported, mappings, category)
        if other is not None and files.resolved(other) == target:
            return True
    return False


def delete_folder(left: Leftover) -> bool:
    """The finished Usenet job's folder, which its client keeps. Only after the download left the import's states, and
    only when nothing else follows the folder."""
    if left.folder is None:
        return False
    if shared(left.download_id, left.folder):
        logger.info("Download %d: another download or a foreign job follows its job folder; it stays", left.download_id)
        return False
    return delete(left.download_id, left.folder, left.keep)


def fail(db: OrmSession, row: Download, reason: str, moment: datetime) -> Leftover:
    """A finished download with a certainly broken archive fails as a failure its client reported (``tracking``):
    the blocklist, ``failed`` in the history, the replacement. Returns what to ``throw_away`` once the caller
    committed."""
    left = leftover(db, row)
    row.state, row.problem_code, row.problem_values = "failed", None, None
    row.failed_reason, row.failed_detail = reason, None
    row.updated_at = moment
    store.block(db, row, reason, moment)
    store.add_history(db, row, "failed", reason, moment, {"detail": None})
    # The next fitting release, at most three times a day, as after a failure the client reported.
    row.failure_handling = replacement.after_failure(db, row, moment)
    store.follow(db, row, moment)
    logger.info("Download %d failed: its archive is broken (%s)", row.id, reason)
    return left


def throw_away(left: Leftover) -> None:
    """After ``fail`` and its commit, in the import's thread: the job leaves its client with its files, and a finished
    Usenet job's folder goes, also when the client cannot be asked. Never raises."""
    try:
        asyncio.run(from_client(left))
    except (downloaders.ClientError, OSError, RuntimeError) as exc:
        logger.info("Download %d: its client did not remove the broken job: %s", left.download_id, type(exc).__name__)
    try:
        delete_folder(left)
    except OSError:
        logger.warning("Download %d: its job folder could not be deleted", left.download_id)
