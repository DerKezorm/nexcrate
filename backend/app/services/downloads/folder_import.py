"""Importing from a folder by hand (issue #26), as the manual import of Radarr, Sonarr and Lidarr.

Files nexcrate did not order and no client lists, for example from another program, a USB disk or a rip. The owner
chooses a folder nexcrate sees and the title it belongs to; ``preview`` says what lies there.

**The folder becomes a download** of the version without a client, and the usual import files it: naming, media data,
an upgrade over the current file, unpacking next to the destination, and the problem cards for what it cannot place.
The origin is ``folder``. ``client_id`` stays empty, so tracking, seeding and removing have nothing to ask a client.

⚠️ **The folder stays as it is.** Its protocol is ``torrent``, the one that links or copies and never moves: a file may
be in use elsewhere, and nothing here knows who owns it. No path of a download without a client reaches a delete
(``discard.leftover``), and the import refuses a folder it would file into (``source_in_version_folder``); ``start``
says so before anything is recorded.

The owner names the title, so a name that fits none of its titles is not asked about again (``title_mismatch`` in
``confirmed``). Log lines carry ids and counts, never a name or a path.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ...db import SessionLocal
from ...meldungen import meldung
from ...models import Download, DownloadFile, Title, VersionDefinition
from .. import folder_rules, folders
from ..disk import proposals as proposing
from ..releases import music_parser
from ..releases import parser as release_parser
from . import album_import, files, foreign, store

logger = logging.getLogger("nexcrate.downloads")

#: Files the preview names; the rest is counted.
MAX_LISTED = 200
#: Names read for a proposal: the folder's and the first videos'.
MAX_READ = 10


@dataclass(frozen=True)
class _Found:
    path: Path
    videos: list[tuple[Path, int]]
    audio: list[tuple[Path, int]]
    archives: list[tuple[Path, int]]

    @property
    def size(self) -> int:
        return sum(size for _path, size in [*self.videos, *self.audio, *self.archives])

    @property
    def name(self) -> str:
        """The release's name: the only video's own, as a folder of one video may be called anything."""
        return self.videos[0][0].stem if len(self.videos) == 1 else self.path.name

    def relative(self, path: Path) -> str:
        return path.relative_to(self.path).as_posix()


def _refuse(code: str, message: str, status: int = 409, **values: Any) -> foreign.ForeignError:
    return foreign.ForeignError(meldung(code, message, **values), status)


def _look(raw: str) -> _Found:
    """What lies in the folder. Raises ``ForeignError``: 404 ``folder_not_visible``, 409 ``folder_import_dangerous``
    and ``folder_import_empty``."""
    try:
        path, _mount = folders.visible(raw)
    except folders.NotVisible as exc:
        raise _refuse(
            "folder_not_visible", "nexcrate does not see this folder. Only mounted folders can be chosen.", 404
        ) from exc
    scan = files.scan(path)
    walked = album_import.walk(path)
    if scan.dangerous or walked.dangerous:
        raise _refuse("folder_import_dangerous", "This folder holds a file nexcrate refuses to import.")
    found = _Found(path, scan.videos, walked.audio, scan.archive_files)
    if not found.videos and not found.audio and not found.archives:
        raise _refuse("folder_import_empty", "This folder holds no video, audio file or archive.")
    return found


def _listed(found: _Found) -> list[dict[str, Any]]:
    rows = [
        *((path, size, "video") for path, size in found.videos),
        *((path, size, "audio") for path, size in found.audio),
        *((path, size, "archive") for path, size in found.archives),
    ]
    return [{"path": found.relative(path), "size_bytes": size, "kind": kind} for path, size, kind in sorted(rows)]


def preview(raw: str) -> dict[str, Any]:
    """What the folder holds, a guess of its kind and proposals from its name: titles of the library only, as for a
    foreign job. Changes nothing."""
    found = _look(raw)
    listed = _listed(found)
    name = found.name
    answer: dict[str, Any] = {
        "path": str(found.path),
        "name": name,
        "size_bytes": found.size,
        "files": listed[:MAX_LISTED],
        "more_files": max(0, len(listed) - MAX_LISTED),
        "proposals": [],
        "series_proposals": [],
    }
    if found.audio and not found.videos:
        album = music_parser.parse_album(name)
        return {**answer, "kind": "album", "parsed": {"title": album.album or album.head or name, "year": album.year}}
    names = [name, *(path.stem for path, _size in found.videos)]
    reading = next(filter(None, map(foreign.series_reading, names)), None)
    with SessionLocal() as db:
        if reading is not None:
            return {
                **answer,
                "kind": "series",
                "parsed": {"title": reading.series_title, "year": reading.year},
                "series_proposals": foreign.series_matches(db, reading),
            }
        parsed, proposals = _movie_reading(proposing.Library.load(db), names)
        return {
            **answer,
            "kind": "movie",
            "parsed": {"title": parsed.title, "year": parsed.year},
            "proposals": proposals,
        }


def _movie_reading(library: Any, names: list[str]) -> tuple[Any, list[dict[str, Any]]]:
    """The first name the library knows, else the first with a year: a folder may be called anything, its videos say
    what they are."""
    readings = [release_parser.parse_movie(name) for name in names[:MAX_READ]]
    for parsed in readings:
        proposals = proposing.library_proposals(library, parsed.title, parsed.year)
        if proposals:
            return parsed, proposals
    return next((parsed for parsed in readings if parsed.year), readings[0]), []


def _check_chosen(found: _Found, title: Title, file: str | None) -> None:
    """A chosen video is one of the folder's, for a movie, and of a folder without archives: the import would file
    what it unpacks and not know the choice."""
    if file is None:
        return
    known = {found.relative(path) for path, _size in found.videos}
    if title.kind != "movie" or found.archives or file not in known:
        raise _refuse("invalid_input", "The input is not valid.", 422, fields=["file"])


def _check_not_in_library(db: Any, found: _Found, title: Title, definition: VersionDefinition, version: Any) -> None:
    """The folder is neither in nor around what the version files into."""
    for raw in (version.root_folder, folder_rules.folder_for(db, definition, title)):
        root = folders.resolved(raw) if raw else None
        if root is not None and folders.overlap(found.path, root):
            raise _refuse("folder_import_in_library", "This folder lies in the library folder of this version.")


def _check_not_running(db: Any, found: _Found) -> None:
    running = db.scalar(
        select(Download.id)
        .where(
            Download.client_id.is_(None),
            Download.reported_path == str(found.path),
            Download.state.in_(store.UNFINISHED_STATES),
        )
        .limit(1)
    )
    if running is not None:
        raise _refuse("folder_import_running", "This folder is being imported already, or waits under Problems.")


def start(raw: str, file: str | None, title_id: int, definition_id: int) -> int:
    """The folder becomes a download of the version and is filed away. ``file`` is the video to take of several, for a
    movie. Returns the download's id.

    Raises ``ForeignError``: 404 when the title or the version is gone, 409 ``folder_import_in_library`` for a folder
    the version files into and ``folder_import_running`` for one that is not through yet, 422 for a ``file`` that is
    no video to choose, what ``_look`` raises and what ``foreign.prepare_version`` raises."""
    found = _look(raw)
    names = [path.stem for path, _size in found.videos]
    moment = store.now()
    with SessionLocal() as db:
        title = db.get(Title, title_id)
        definition = db.get(VersionDefinition, definition_id)
        if title is None or definition is None:
            raise foreign.not_found()
        _check_chosen(found, title, file)
        _check_not_running(db, found)
        version, episodes = foreign.prepare_version(
            db,
            title,
            definition,
            moment,
            lambda stored: foreign.episodes_of_files(db, title, stored, names, found.path.name),
        )
        _check_not_in_library(db, found, title, definition, version)
        # The chosen video names the release: its quality and group, whatever the folder is called.
        release = Path(file).stem if file is not None else found.name
        download = foreign.record_download(
            db,
            title,
            definition,
            version,
            episodes,
            moment,
            client_id=None,
            protocol="torrent",
            client_download_id="",
            release_title=release[:1024],
            size_bytes=found.size,
            quality=foreign.name_quality(title.kind, release),
            confirmed=["title_mismatch"],
            origin="folder",
            state="completed",
            progress=100.0,
            reported_path=str(found.path),
            completed_at=moment,
        )
        if file is not None:
            size = next(size for path, size in found.videos if found.relative(path) == file)
            db.add(DownloadFile(download_id=download.id, path=file, size=size, decision="chosen"))
        store.add_history(db, download, "grabbed", download.quality, moment, {"origin": "folder"})
        store.follow(db, download, moment)
        db.commit()
        download_id = download.id
        kind = title.kind
    logger.info("A folder becomes download %d of %s %d (%d files)", download_id, kind, title_id, len(names))
    foreign.hand_over(download_id, kind, title_id, True)
    return download_id
