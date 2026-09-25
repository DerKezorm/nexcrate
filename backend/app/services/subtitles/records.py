"""The subtitle files nexcrate placed, in the table ``extra_files`` (C9).

* Each placed file is one row, its path relative to the version's ``root_folder``; an ``.idx`` and its ``.sub`` are two.
* A recorded subtitle **belongs to the version's file** when it lies in the folder of that file and its name starts with
  the file's name without extension and a dot. Only those are listed with the version and go into the recycle folder
  with the file on an upgrade. A row left from an earlier file (a source fed the version for a time, the file is gone)
  stays as it is, and so does its file.
* Only a version no source feeds, and only while it has a file, has subtitles to list or to recycle.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session as OrmSession

from ...models import ExtraFile, Version
from ..schreibweisen import nfc
from .placing import Placed

KIND = "subtitle"


def _parts(path: str) -> list[str]:
    return [part for part in nfc(path).replace("\\", "/").split("/") if part]


def belongs_to_file(relative_path: str, file_path: str) -> bool:
    """Whether a subtitle at ``relative_path`` lies next to the file at ``file_path`` and is named after it."""
    subtitle, movie = _parts(relative_path), _parts(file_path)
    if not subtitle or not movie or subtitle[-1] == movie[-1]:
        return False
    return subtitle[:-1] == movie[:-1] and subtitle[-1].startswith(PurePosixPath(movie[-1]).stem + ".")


def _file_of(version: Version) -> str | None:
    if version.source_id is not None or not version.has_file or not version.relative_path:
        return None
    return version.relative_path


def current_rows(db: OrmSession, version: Version) -> list[tuple[int, str]]:
    """The recorded subtitles of the version's file as ``(id, relative_path)``."""
    file_path = _file_of(version)
    if file_path is None:
        return []
    rows = db.execute(
        select(ExtraFile.id, ExtraFile.relative_path)
        .where(ExtraFile.version_id == version.id, ExtraFile.kind == KIND)
        .order_by(ExtraFile.id)
    ).tuples()
    return [(row_id, path) for row_id, path in rows if belongs_to_file(path, file_path)]


def listed(db: OrmSession, version: Version) -> list[dict[str, Any]]:
    """The title page's subtitles of a version: by language (unknown last), forced, SDH, then path."""
    file_path = _file_of(version)
    if file_path is None:
        return []
    rows = [
        row
        for row in db.scalars(select(ExtraFile).where(ExtraFile.version_id == version.id, ExtraFile.kind == KIND))
        if belongs_to_file(row.relative_path, file_path)
    ]
    rows.sort(
        key=lambda row: (
            row.language is None,
            row.language or "",
            bool(row.forced),
            bool(row.sdh),
            row.relative_path.casefold(),
            row.id,
        )
    )
    return [
        {"language": row.language, "forced": bool(row.forced), "sdh": bool(row.sdh), "file": row.relative_path}
        for row in rows
    ]


def record(
    db: OrmSession,
    version: Version,
    download_id: int,
    placed: Iterable[Placed],
    gone: Iterable[int],
    moment: datetime,
) -> None:
    """Stage the rows: the old file's subtitles that left go, the placed ones come. The caller commits."""
    gone_ids = list(gone)
    if gone_ids:
        db.execute(delete(ExtraFile).where(ExtraFile.version_id == version.id, ExtraFile.id.in_(gone_ids)))
    for item in placed:
        db.add(
            ExtraFile(
                version_id=version.id,
                download_id=download_id,
                relative_path=item.relative_path,
                kind=KIND,
                language=item.language,
                forced=item.forced,
                sdh=item.sdh,
                created_at=moment,
            )
        )


def imported_detail(quality: str | None, subtitles: int) -> str | None:
    """The detail of the history entry ``imported``: the quality, and how many subtitle files were placed."""
    if subtitles <= 0:
        return quality
    count = f"{subtitles} subtitle" + ("" if subtitles == 1 else "s")
    return f"{quality}, {count}" if quality else count
