"""Files that arrive after the client called the download done (a seedbox and a sync, issue #3 of 26.09.2026).

A client on a seedbox finishes the job there; a sync brings the files to the server minutes or hours later. The import
looked once, found nothing and left the problem ``path_not_found`` for the owner, and nothing ever looked again.

* Every ``INTERVAL_SECONDS`` the job looks at each download with ``path_not_found`` that finished less than ``WINDOW``
  ago: is its path, mapped like the import maps it, visible now?
* A visible path is filed only once it stood still: the same number of files, the same sizes and the same newest
  change as the round before, and that change at least ``SETTLE_SECONDS`` old. A sync that is still copying would
  otherwise hand the import half a folder (``no_video``) or a cut-off video.
* Filing is the owner's retry (``actions.retry``): the problem goes, the import runs, the log names the download.
* After ``WINDOW`` the download waits for the owner as before; the card says what nexcrate did.

What a round saw lives in memory only. A restart looks twice before it files, which costs one round.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from ...db import SessionLocal
from ...models import Download, DownloadClient
from .. import folders
from . import files, store

logger = logging.getLogger("nexcrate.downloads")

JOB_NAME = "late_files"
INTERVAL_SECONDS = 120
WINDOW = timedelta(days=2)
SETTLE_SECONDS = 120
#: More entries than this in a job folder: nexcrate does not count on, the folder counts as still moving.
MAX_ENTRIES = 20_000

#: The wall clock for the files' change times; a test moves it.
_clock = time.time


@dataclass(frozen=True)
class Mark:
    count: int
    size: int
    newest: float


#: Per download: the path and what the last round saw there.
_seen: dict[int, tuple[Path, Mark]] = {}


def local_path(reported: str | None, mappings: list[dict[str, str]]) -> Path | None:
    """Where nexcrate sees a reported path, the way the import looks for it; None while it sees nothing."""
    if not reported:
        return None
    candidates: list[str | Path] = []
    mapped = files.map_remote(reported, mappings)
    if mapped is not None:
        candidates.append(mapped)
    if files.remote_parts(reported) is not None:
        candidates.append(reported)
    for candidate in candidates:
        path = folders.visible_path(candidate)
        if path is not None:
            return path
    return None


def mark_of(path: Path) -> Mark | None:
    """Files, bytes and the newest change below a path; None when it cannot be read whole."""
    try:
        if path.is_file():
            info = path.stat()
            return Mark(1, info.st_size, info.st_mtime)
        count, size, newest = 0, 0, path.stat().st_mtime
        for folder, subfolders, names in os.walk(path):
            newest = max(newest, os.stat(folder).st_mtime)
            count += len(subfolders) + len(names)
            if count > MAX_ENTRIES:
                return None
            for name in names:
                info = os.stat(os.path.join(folder, name))
                size += info.st_size
                newest = max(newest, info.st_mtime)
        return Mark(count, size, newest)
    except OSError:
        return None


def _waiting() -> list[tuple[int, str | None, list[dict[str, str]]]]:
    since = store.now() - WINDOW
    with SessionLocal() as db:
        rows = db.execute(
            select(Download.id, Download.reported_path, DownloadClient.path_mappings)
            .outerjoin(DownloadClient, DownloadClient.id == Download.client_id)
            .where(
                Download.state == "problem",
                Download.problem_code == "path_not_found",
                Download.completed_at.is_not(None),
                Download.completed_at >= since,
            )
        ).all()
    return [(row[0], row[1], [dict(mapping) for mapping in (row[2] or [])]) for row in rows]


def look() -> list[int]:
    """One round. Returns the downloads handed back to the import."""
    from . import actions

    waiting = _waiting()
    for gone in set(_seen) - {download_id for download_id, _reported, _mappings in waiting}:
        _seen.pop(gone, None)
    filed: list[int] = []
    for download_id, reported, mappings in waiting:
        path = local_path(reported, mappings)
        mark = mark_of(path) if path is not None else None
        if path is None or mark is None:
            _seen.pop(download_id, None)
            continue
        before = _seen.get(download_id)
        _seen[download_id] = (path, mark)
        if before != (path, mark) or _clock() - mark.newest < SETTLE_SECONDS:
            continue
        try:
            actions.retry(download_id)
        except actions.ActionError:
            # The owner acted in between (removed it, retried it): nothing to do.
            continue
        _seen.pop(download_id, None)
        filed.append(download_id)
        logger.info("Download %d: its files arrived after the client finished, filing it now", download_id)
    return filed


def forget() -> None:
    _seen.clear()


def run_job() -> None:
    look()
