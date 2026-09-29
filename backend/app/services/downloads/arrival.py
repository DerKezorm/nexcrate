"""Files that arrive after the client called the download done (a seedbox and a sync, issue #3 of 26.09.2026).

A client on a seedbox finishes the job there; a sync brings the files to the server minutes or hours later. The import
looked once, found nothing and left the problem ``path_not_found`` for the owner, and nothing ever looked again.

* Every ``INTERVAL_SECONDS`` the job looks at each download with ``path_not_found`` that finished less than ``WINDOW``
  ago: is its path, mapped like the import maps it, visible now?
* The same for ``no_video`` and ``no_audio`` (issue #3, 29.09.2026): a sync may create the folder first and bring the
  files later, or under a temporary name, so the import met a folder without media. Such a download is filed again
  only once the folder holds a video or music (or archives), and never twice for a folder that did not change since.
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
from ...models import Download, DownloadClient, Title
from .. import folders
from . import files, store

logger = logging.getLogger("nexcrate.downloads")

JOB_NAME = "late_files"
INTERVAL_SECONDS = 120
WINDOW = timedelta(days=2)
SETTLE_SECONDS = 120
#: More entries than this in a job folder: nexcrate does not count on, the folder counts as still moving.
MAX_ENTRIES = 20_000

#: Problems a folder without its media yet leaves: filed again only once the media are there.
WITHOUT_MEDIA = ("no_video", "no_audio")
WATCHED = ("path_not_found", *WITHOUT_MEDIA)

#: The wall clock for the files' change times; a test moves it.
_clock = time.time


@dataclass(frozen=True)
class Mark:
    count: int
    size: int
    newest: float


#: Per download: the path and what the last round saw there.
_seen: dict[int, tuple[Path, Mark]] = {}
#: Per download: the folder as it was filed again, and when. The same folder is not filed twice: an import that finds no
#: media where this job saw some would otherwise run every few minutes for two days.
_filed: dict[int, tuple[Path, Mark, float]] = {}


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


def holds_media(path: Path, kind: str | None) -> bool:
    """Whether the import would find something to file below a path: music for an album, else a video; archives too."""
    if kind == "album":
        from .album_import import walk

        walked = walk(path)
        return bool(walked.audio or walked.archives)
    found = files.scan(path, series=kind == "series")
    return bool(found.videos or found.archive_files)


@dataclass(frozen=True)
class _Waiting:
    download_id: int
    reported: str | None
    mappings: list[dict[str, str]]
    code: str
    kind: str | None


def _waiting() -> list[_Waiting]:
    since = store.now() - WINDOW
    with SessionLocal() as db:
        rows = db.execute(
            select(
                Download.id, Download.reported_path, DownloadClient.path_mappings, Download.problem_code, Title.kind
            )
            .outerjoin(DownloadClient, DownloadClient.id == Download.client_id)
            .outerjoin(Title, Title.id == Download.title_id)
            .where(
                Download.state == "problem",
                Download.problem_code.in_(WATCHED),
                Download.completed_at.is_not(None),
                Download.completed_at >= since,
            )
        ).all()
    return [_Waiting(row[0], row[1], [dict(mapping) for mapping in (row[2] or [])], row[3], row[4]) for row in rows]


def look() -> list[int]:
    """One round. Returns the downloads handed back to the import."""
    from . import actions

    waiting = _waiting()
    for gone in set(_seen) - {entry.download_id for entry in waiting}:
        _seen.pop(gone, None)
    moment = _clock()
    for old in [key for key, (_path, _mark, at) in _filed.items() if moment - at > WINDOW.total_seconds()]:
        _filed.pop(old, None)
    filed: list[int] = []
    for entry in waiting:
        download_id = entry.download_id
        path = local_path(entry.reported, entry.mappings)
        mark = mark_of(path) if path is not None else None
        if path is None or mark is None:
            _seen.pop(download_id, None)
            continue
        before = _seen.get(download_id)
        _seen[download_id] = (path, mark)
        if before != (path, mark) or moment - mark.newest < SETTLE_SECONDS:
            continue
        done = _filed.get(download_id)
        if done is not None and done[:2] == (path, mark):
            continue
        if entry.code in WITHOUT_MEDIA and not holds_media(path, entry.kind):
            continue
        try:
            actions.retry(download_id)
        except actions.ActionError:
            # The owner acted in between (removed it, retried it): nothing to do.
            continue
        _seen.pop(download_id, None)
        _filed[download_id] = (path, mark, moment)
        filed.append(download_id)
        logger.info("Download %d: its files arrived after the client finished, filing it now", download_id)
    return filed


def forget() -> None:
    _seen.clear()
    _filed.clear()


def run_job() -> None:
    look()
