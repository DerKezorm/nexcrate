"""What happens when Bazarr says it wrote, uploaded or deleted a subtitle (``RescanMovie``, ``RescanSeries``).

The version's files are looked at, and the subtitle files next to each are brought into ``extra_files``:

* a subtitle file that lies in the folder of a video and is named after it (``<video name>.<anything>.<ext>``) and is
  not recorded yet gets a row, its language and tags read from its name (``subtitles.names``);
* a recorded subtitle of that video whose file is gone loses its row.

From then on the subtitle goes where the video goes: renamed with it, into the recycle folder with it on an upgrade or
a removal, back with it from the recycle folder. A file named after no video stays as it is and is never touched.

``everything`` does the same for every version Bazarr sees, once, when the owner switches the connection on: Bazarr
may have written subtitles before, next to files that came over from Radarr or Sonarr.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path, PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ... import db as database
from ...models import EpisodeFile, ExtraFile, Title, Version, utcnow
from .. import folders
from ..downloads import files
from ..schreibweisen import nfc
from ..subtitles import names
from ..subtitles.records import KIND, belongs_to_file

logger = logging.getLogger("nexcrate.bazarr")


def _root(raw: str | None) -> Path | None:
    try:
        return folders.visible(raw)[0]
    except folders.NotVisible:
        return None


def _parts(relative: str) -> list[str]:
    return [part for part in nfc(relative).replace("\\", "/").split("/") if part]


def _listing(folder: Path) -> list[str] | None:
    """The names of the files in a folder, links left out; None when it cannot be read."""
    if files.is_link(folder) or not folder.is_dir():
        return None
    found: list[str] = []
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                try:
                    if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                found.append(nfc(entry.name))
    except OSError:
        return None
    return found


def _sync_file(
    db: OrmSession,
    version: Version,
    base: Path,
    video_relative: str,
    recorded: list[ExtraFile],
    *,
    episode_file_id: int | None,
    groups: list[str | None],
) -> tuple[int, int]:
    """Bring the subtitles of one video into line. ``base`` is the folder the relative paths start from. Returns
    ``(added, dropped)``."""
    parts = _parts(video_relative)
    if not parts or any(part in (".", "..") for part in parts):
        return 0, 0
    folder = base.joinpath(*parts[:-1])
    if not files.inside(folder, base):
        return 0, 0
    listing = _listing(folder)
    if listing is None:
        return 0, 0
    video_name = parts[-1]
    stem = PurePosixPath(video_name).stem
    prefix = "/".join(parts[:-1])
    present = set(listing)
    added = dropped = 0
    known = {nfc(row.relative_path).replace("\\", "/") for row in recorded}
    for row in recorded:
        path = nfc(row.relative_path).replace("\\", "/")
        if belongs_to_file(path, video_relative) and path.rsplit("/", 1)[-1] not in present:
            db.delete(row)
            dropped += 1
    for name in sorted(listing):
        extension = os.path.splitext(name)[1].casefold()
        if extension not in names.EXTENSIONS or name == video_name:
            continue
        relative = f"{prefix}/{name}" if prefix else name
        if relative in known or not belongs_to_file(relative, video_relative):
            continue
        reading = names.read(os.path.splitext(name)[0], video_name=stem, groups=groups)
        db.add(
            ExtraFile(
                version_id=version.id,
                download_id=None,
                relative_path=relative,
                kind=KIND,
                language=reading.language,
                forced=reading.forced,
                sdh=reading.sdh,
                created_at=utcnow(),
                episode_file_id=episode_file_id,
            )
        )
        added += 1
    return added, dropped


def movie(db: OrmSession, version: Version) -> tuple[int, int]:
    """Stage the rows of a movie version's file; the caller commits."""
    if version.source_id is not None or not version.has_file or not version.relative_path:
        return 0, 0
    root = _root(version.root_folder)
    if root is None:
        return 0, 0
    recorded = list(
        db.scalars(
            select(ExtraFile).where(
                ExtraFile.version_id == version.id, ExtraFile.kind == KIND, ExtraFile.episode_file_id.is_(None)
            )
        )
    )
    return _sync_file(
        db, version, root, version.relative_path, recorded, episode_file_id=None, groups=[version.release_group]
    )


def series(db: OrmSession, version: Version) -> tuple[int, int]:
    """Stage the rows of every episode file of a series version; the caller commits."""
    from .library import series_folder

    if version.source_id is not None:
        return 0, 0
    folder_text = series_folder(version)
    root = _root(version.root_folder)
    if folder_text is None or root is None:
        return 0, 0
    base = root / (version.relative_path or "")
    if not files.strictly_inside(base, root) or files.is_link(base):
        return 0, 0
    rows = list(db.scalars(select(EpisodeFile).where(EpisodeFile.version_id == version.id).order_by(EpisodeFile.id)))
    recorded_by_file: dict[int, list[ExtraFile]] = {}
    for row in db.scalars(
        select(ExtraFile).where(
            ExtraFile.version_id == version.id, ExtraFile.kind == KIND, ExtraFile.episode_file_id.is_not(None)
        )
    ):
        recorded_by_file.setdefault(row.episode_file_id or 0, []).append(row)
    added = dropped = 0
    for row in rows:
        more, less = _sync_file(
            db,
            version,
            base,
            row.relative_path,
            recorded_by_file.get(row.id, []),
            episode_file_id=row.id,
            groups=[row.release_group],
        )
        added, dropped = added + more, dropped + less
    return added, dropped


def version(db: OrmSession, version_id: int, kind: str) -> tuple[int, int] | None:
    """Rescan one version Bazarr names and commit. None when it is not one Bazarr sees."""
    row = db.get(Version, version_id)
    if row is None or row.source_id is not None:
        return None
    title = db.get(Title, row.title_id)
    if title is None or title.kind != kind:
        return None
    counts = movie(db, row) if kind == "movie" else series(db, row)
    db.commit()
    if counts != (0, 0):
        logger.info("Bazarr rescan of version %d: %d subtitle(s) recorded, %d gone", version_id, *counts)
    return counts


def everything() -> tuple[int, int]:
    """Every version Bazarr sees, one commit each. Returns the totals."""
    with database.SessionLocal() as db:
        wanted = db.execute(
            select(Version.id, Title.kind)
            .join(Title, Title.id == Version.title_id)
            .where(Version.source_id.is_(None), Title.kind.in_(("movie", "series")))
            .order_by(Version.id)
        ).tuples().all()
    added = dropped = 0
    for version_id, kind in wanted:
        try:
            with database.SessionLocal() as db:
                counts = version(db, version_id, kind)
        except Exception:
            logger.exception("Bazarr rescan of version %d failed", version_id)
            continue
        if counts is not None:
            added, dropped = added + counts[0], dropped + counts[1]
    logger.info("Bazarr rescan of %d versions: %d subtitle(s) recorded, %d gone", len(wanted), added, dropped)
    return added, dropped
