"""Reading a movie version's file again (14.11.2026): the owner changed the video in the known movie folder by hand.

* **Only a version nexcrate owns with a file** (``companions.owned``); a version a Radarr connection feeds answers 409
  ``version_owned_by_source``, one without a file 409 ``version_without_file``.
* **The known folder** comes from ``relative_path``, resolved strictly inside a visible root folder and never through a
  link (``companions.locate``). A missing folder answers 409 ``folder_missing``: a moved folder is taken on the page
  "Folders".
* **Which video:** the known file when it is still there (read again, it may have been replaced under the same name);
  otherwise the one video the folder holds by the rules of the disk scan (video endings, no samples, no trailers, no
  links). No video answers 409 ``no_video``, several 409 ``several_videos`` with their names: nexcrate never guesses.
* **What is recorded** is what assigning a folder records (``disk/assign.py``): file name, size, media data, quality
  and languages, the release name from the file name, the judgement and the state. A new file adds a history entry
  ``found_on_disk``. Where the file came from (``file_ref``) stays as it was. After the commit the ``release.nex`` is
  written again.
* Log lines carry ids, never names or paths.
"""

from __future__ import annotations

import logging
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ...db import SessionLocal
from ...meldungen import error
from ...models import HistoryEntry, Title, Version, VersionDefinition, utcnow
from .. import companions, judging, media
from ..downloads import files, store
from . import assign, walk

logger = logging.getLogger("nexcrate.disk")

#: How many names a 409 ``several_videos`` lists at most.
MAX_NAMES = 20


@dataclass(frozen=True)
class FileRead:
    #: ``taken`` when another file became the version's, ``refreshed`` when the known file was read again.
    outcome: str
    title_id: int
    version_id: int


def not_found() -> HTTPException:
    return error("not_found", "This does not exist, or not any more.", 404)


def videos_in(folder: Path) -> list[tuple[str, int]]:
    """The videos directly in the folder by the rules of the disk scan, with their sizes, largest first."""
    found: list[tuple[str, int]] = []
    for entry in walk._entries(folder) or []:
        if not walk._video_file(entry):
            continue
        try:
            size = entry.stat(follow_symlinks=False).st_size
        except OSError:
            continue
        found.append((entry.name, int(size)))
    return sorted(found, key=lambda item: (-item[1], item[0]))


def _regular_size(path: Path) -> int | None:
    try:
        info = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode) or files.is_link(path):
        return None
    return int(info.st_size)


def _version(db: OrmSession, title_id: int, definition_id: int) -> tuple[Title, Version, VersionDefinition]:
    title = db.get(Title, title_id)
    if title is None or title.kind != "movie":
        raise not_found()
    version = db.scalar(
        select(Version).where(Version.title_id == title_id, Version.version_definition_id == definition_id)
    )
    definition = db.get(VersionDefinition, definition_id)
    if version is None or definition is None:
        raise not_found()
    if version.source_id is not None:
        raise error(
            "version_owned_by_source",
            "This version comes from a Radarr connection. It goes when you remove the movie there or delete the "
            "connection.",
            409,
        )
    if not companions.owned(version):
        raise error("version_without_file", "This version has no file yet.", 409)
    return title, version, definition


def _chosen(located: companions.Located) -> tuple[Path, int]:
    """The video to record: the known one, or the only one in the folder. Raises the 409 answers."""
    assert located.folder is not None
    if located.video is not None:
        size = _regular_size(located.video)
        if size is not None:
            return located.video, size
    videos = videos_in(located.folder)
    if not videos:
        raise error("no_video", "There is no video in the movie folder.", 409)
    if len(videos) > 1:
        names = [name for name, _size in videos[:MAX_NAMES]]
        raise error(
            "several_videos",
            "There are several videos in the movie folder. Leave only one there and read the file again.",
            409,
            files=names,
            videos=len(videos),
        )
    name, size = videos[0]
    return located.folder / name, size


def read_again(title_id: int, definition_id: int) -> FileRead:
    """``POST /api/library/{title_id}/versions/{version_id}/file/read``: record the file in the known folder."""
    moment = utcnow()
    with SessionLocal() as db:
        title, version, definition = _version(db, title_id, definition_id)
        located = companions.locate(version)
        if located.folder is None:
            raise error("folder_missing", "The movie folder is not there any more.", 409)
        video, size = _chosen(located)
        parts = [part for part in (version.relative_path or "").replace("\\", "/").split("/") if part]
        taken = video != located.video
        root = Path(version.root_folder or "")
        where = assign.Located(root=root, folder=located.folder, video=video, file_name=video.name, size=size)
        facts = assign.facts_of(where, media.read(video), title.original_language)
        version.relative_path = "/".join([*parts[:-1], video.name])[:2048]
        version.size = size
        version.quality = facts.quality
        version.quality_from = facts.quality_from
        version.release_title = Path(video.name).stem[:1024]
        version.release_group = (facts.release_group or None) and facts.release_group[:200]
        version.languages = list(facts.languages)
        version.media_info = facts.media
        version.media_read_at = moment if facts.media is not None else None
        version.updated_at = moment
        db.flush()
        version.cutoff_not_met = judging.judge(db, version)
        if taken:
            db.add(
                HistoryEntry(
                    title_id=title.id,
                    version_id=version.id,
                    version_definition_id=definition.id,
                    version_label=definition.label,
                    event="found_on_disk",
                    at=moment,
                    detail=facts.quality,
                )
            )
        store.follow_version(db, title.id, definition.id, moment)
        version_id = version.id
        db.commit()
    assign.write_companion(version_id)
    outcome = "taken" if taken else "refreshed"
    logger.info("Version %d: file read again (%s, %s)", version_id, outcome, facts.quality)
    return FileRead(outcome, title_id, version_id)
