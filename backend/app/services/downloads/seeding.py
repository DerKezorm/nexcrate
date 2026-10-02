"""After the import: a torrent's seed goal, and removing the finished torrent from its client (02.10.2026).

Once a minute, from the tracking round, every enabled torrent client is asked about nexcrate's imported torrents that
were not dealt with yet (``seed_done_at`` empty), when it may remove them (``remove_completed``) or holds a seed time
nexcrate has to watch.

* **The seed time of Transmission and Deluge is nexcrate's.** Neither has one of its own (Transmission's idle limit
  means something else). When the seeding seconds the client reports reach the goal kept on the download, nexcrate stops
  the torrent and marks the download (``seed_stopped_at``). Never twice: an owner who starts it again keeps it seeding.
  qBittorrent keeps its own seed time; its ratio and time came with the torrent.
* **At its goal** is a torrent the client stopped at one of its limits (qBittorrent ``stoppedUP`` with a resolved limit
  reached, Transmission ``status 0`` with ``isFinished``, Deluge ``Paused`` at its stop ratio), or one nexcrate stopped
  for its seed time that is still stopped. A forced one never is. A client's own limits count as in Radarr, also
  without a goal from the indexer.
* **Removing** only with ``remove_completed``, only for a download in state ``imported`` and a torrent in nexcrate's
  category, with its files in the download folder, as Radarr removes. ⚠️ Never when the torrent's files lie in a
  library folder (a version's folder, a root folder, a folder on disk), hold one, or are the filed movie: then it stays,
  a log line says why, and ``seed_done`` is ``kept``. A torrent whose files nexcrate does not see stays as well and is
  looked at again later. The download's state stays ``imported``; ``seed_done_at`` keeps it from happening twice.
* The library file survives: an import hardlinks or copies a torrent, it never moves it.
* Once an hour each torrent client is asked whether it removes torrents itself (``self_removal``), for the warning on
  its card.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ... import crypto
from ...db import SessionLocal
from ...models import DiskRoot, Download, DownloadClient, Version, VersionDefinition
from ...models.downloads import PROTOCOL_OF_KIND
from .. import downloaders, folders
from . import arrival, files, store

logger = logging.getLogger("nexcrate.downloads")

#: How often the torrents are looked at, and the clients asked whether they remove torrents themselves.
LOOK_EVERY_SECONDS = 60.0
SELF_REMOVAL_EVERY_SECONDS = 3600.0
#: The kinds whose seed time nexcrate watches itself.
OWN_SEED_TIME = frozenset({"transmission", "deluge"})
TORRENT_KINDS = tuple(kind for kind, protocol in PROTOCOL_OF_KIND.items() if protocol == "torrent")

_lock = threading.Lock()
_next_look: float | None = None
_next_check: float | None = None
#: Downloads whose files nexcrate did not see, logged once until the next start.
_unseen: set[int] = set()


def clock() -> float:
    return time.monotonic()


def reset() -> None:
    global _next_look, _next_check
    with _lock:
        _next_look = None
        _next_check = None
        _unseen.clear()


def due() -> bool:
    with _lock:
        return _next_look is None or clock() >= _next_look


@dataclass(frozen=True)
class _Torrent:
    download_id: int
    client_download_id: str
    #: The seed time kept on the download, in minutes.
    seed_time: int | None
    stopped_by_nexcrate: bool


@dataclass(frozen=True)
class _Client:
    id: int
    kind: str
    remove_completed: bool
    #: ⚠️ With the decrypted secret.
    target: downloaders.Target = field(repr=False)
    torrents: tuple[_Torrent, ...] = ()


def _target(row: DownloadClient) -> downloaders.Target:
    return downloaders.Target(
        kind=row.kind,
        url=row.url,
        username=row.username or "",
        secret=crypto.decrypt(row.secret) if row.secret else "",
        category=row.category,
    )


def _clients(with_torrents: bool) -> list[_Client]:
    """The enabled torrent clients; with ``with_torrents`` only those with imported torrents to look at, and those."""
    found: list[_Client] = []
    with SessionLocal() as db:
        rows = db.scalars(
            select(DownloadClient)
            .where(DownloadClient.enabled.is_(True), DownloadClient.kind.in_(TORRENT_KINDS))
            .order_by(DownloadClient.id)
        )
        for row in rows:
            if store.login_blocked(row):
                continue
            torrents: tuple[_Torrent, ...] = ()
            if with_torrents:
                downloads = db.scalars(
                    select(Download).where(
                        Download.client_id == row.id,
                        Download.protocol == "torrent",
                        Download.state == "imported",
                        Download.seed_done_at.is_(None),
                        Download.client_download_id != "",
                    )
                )
                torrents = tuple(
                    _Torrent(
                        download_id=download.id,
                        client_download_id=download.client_download_id.lower(),
                        seed_time=download.seed_time,
                        stopped_by_nexcrate=download.seed_stopped_at is not None,
                    )
                    for download in downloads
                    if row.remove_completed or (row.kind in OWN_SEED_TIME and download.seed_time is not None)
                )
                if not torrents:
                    continue
            found.append(
                _Client(
                    id=row.id,
                    kind=row.kind,
                    remove_completed=bool(row.remove_completed),
                    target=_target(row),
                    torrents=torrents,
                )
            )
    return found


def _stopped(download_id: int, moment: datetime) -> None:
    with SessionLocal() as db:
        row = db.get(Download, download_id)
        if row is not None and row.seed_stopped_at is None:
            row.seed_stopped_at = moment
            db.commit()


def _done(download_id: int, outcome: str, moment: datetime) -> None:
    with SessionLocal() as db:
        row = db.get(Download, download_id)
        if row is not None:
            row.seed_done_at = moment
            row.seed_done = outcome
            db.commit()


def _library_folders(db: OrmSession) -> list[str]:
    """Every folder the library lives in: the versions' folders, the root folders of their titles, the folders on
    disk."""
    paths: list[str | None] = []
    paths.extend(db.scalars(select(VersionDefinition.folder).where(VersionDefinition.folder.is_not(None))))
    paths.extend(db.scalars(select(Version.root_folder).where(Version.root_folder.is_not(None)).distinct()))
    paths.extend(db.scalars(select(DiskRoot.path)))
    return [path for path in dict.fromkeys(paths) if path]


def _filed_movie(db: OrmSession, download: Download) -> Path | None:
    version = db.get(Version, download.version_id) if download.version_id is not None else None
    if version is None or not version.root_folder or not version.relative_path:
        return None
    return Path(version.root_folder, version.relative_path)


def safety(download_id: int, client_id: int, reported: str | None) -> str:
    """``ok`` to remove, ``library`` when the torrent's files lie in, hold or are the library, ``unseen`` when nexcrate
    does not see them."""
    with SessionLocal() as db:
        download = db.get(Download, download_id)
        client = db.get(DownloadClient, client_id)
        if download is None or client is None:
            return "unseen"
        local = arrival.local_path(reported, list(client.path_mappings or []))
        if local is None:
            return "unseen"
        library = _library_folders(db)
        movie = _filed_movie(db, download)
    for raw in library:
        folder = folders.visible_path(raw)
        if folder is None:
            continue
        if files.inside(local, folder) or files.inside(folder, local):
            return "library"
    if movie is not None:
        filed = folders.visible_path(movie)
        if filed is not None and (files.resolved(filed) == files.resolved(local) or files.inside(filed, local)):
            return "library"
    return "ok"


async def _look_at(client: _Client) -> None:
    moment = store.now()
    async with downloaders.open_client(client.target) as opened:
        found = await opened.seeding([torrent.client_download_id for torrent in client.torrents])
        for torrent in client.torrents:
            seeding = found.get(torrent.client_download_id)
            if seeding is None or not seeding.done:
                continue
            stopped_by_nexcrate = torrent.stopped_by_nexcrate
            if (
                client.kind in OWN_SEED_TIME
                and torrent.seed_time is not None
                and not stopped_by_nexcrate
                and not seeding.stopped
                and seeding.seeding_seconds is not None
                and seeding.seeding_seconds >= torrent.seed_time * 60
            ):
                await opened.stop(torrent.client_download_id)
                await asyncio.to_thread(_stopped, torrent.download_id, moment)
                logger.info(
                    "Download %d: its torrent seeded %d minutes and reached its seed time; nexcrate stopped it",
                    torrent.download_id,
                    seeding.seeding_seconds // 60,
                )
                stopped_by_nexcrate = True
                seeding = dataclasses.replace(seeding, stopped=True)
            at_goal = seeding.goal_reached or (stopped_by_nexcrate and seeding.stopped)
            if not client.remove_completed or not at_goal or seeding.forced:
                continue
            verdict = await asyncio.to_thread(safety, torrent.download_id, client.id, seeding.path)
            if verdict == "unseen":
                if torrent.download_id not in _unseen:
                    _unseen.add(torrent.download_id)
                    logger.info(
                        "Download %d: its torrent reached its seed goal, but nexcrate does not see its files; it stays "
                        "in the client for now",
                        torrent.download_id,
                    )
                continue
            if verdict == "library":
                await asyncio.to_thread(_done, torrent.download_id, "kept", moment)
                logger.warning(
                    "Download %d: its torrent reached its seed goal, but its files lie in a library folder; it stays "
                    "in the client and nothing is deleted",
                    torrent.download_id,
                )
                continue
            await opened.remove(torrent.client_download_id, delete_files=True)
            await asyncio.to_thread(_done, torrent.download_id, "removed", moment)
            logger.info(
                "Download %d: its torrent reached its seed goal and left the client with its files in the download "
                "folder; the library file stays",
                torrent.download_id,
            )


def record_self_removal(client_id: int, code: str | None) -> None:
    with SessionLocal() as db:
        row = db.get(DownloadClient, client_id)
        if row is not None:
            row.self_removal = code
            row.self_removal_checked_at = store.now()
            db.commit()


async def check_self_removal(client_id: int, target: downloaders.Target) -> str | None:
    """Ask a torrent client whether it removes torrents itself and keep the answer; a failure keeps the last one."""
    try:
        async with downloaders.open_client(target) as opened:
            code = await opened.self_removal()
    except downloaders.ClientError as exc:
        logger.info("Download client %d: whether it removes torrents itself could not be read: %s", client_id, exc.code)
        return None
    await asyncio.to_thread(record_self_removal, client_id, code)
    if code is not None:
        logger.info("Download client %d removes torrents itself at its share limit (%s)", client_id, code)
    return code


async def look() -> None:
    """Seed goals and removing, once a minute; the clients' own removal rules once an hour."""
    global _next_look, _next_check
    with _lock:
        _next_look = clock() + LOOK_EVERY_SECONDS
        checking = _next_check is None or clock() >= _next_check
        if checking:
            _next_check = clock() + SELF_REMOVAL_EVERY_SECONDS
    for client in await asyncio.to_thread(_clients, True):
        try:
            await _look_at(client)
        except downloaders.ClientError as exc:
            logger.info("Download client %d: its finished torrents could not be looked at: %s", client.id, exc.code)
    if checking:
        for client in await asyncio.to_thread(_clients, False):
            await check_self_removal(client.id, client.target)
