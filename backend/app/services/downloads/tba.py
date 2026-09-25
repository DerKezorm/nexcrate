"""Renaming episode files named with ``TBA`` once TMDB has a title (decision 27).

After a series is refreshed, each file nexcrate filed with ``TBA`` in its name gets the name its version's naming
builds now, with its subtitles, and the season folder's ``release.nex`` follows. Only a file that still carries the
name nexcrate gave it: one renamed by hand, moved or gone stays as it is. No waiting for a title as Sonarr's 48 hours.

The new name is linked first, then the old one goes (as subtitles are moved); where a link is impossible the file is
renamed. A name taken by another file leaves the file as it is. A file whose recorded name is not on disk any more was
renamed or moved by hand and stays. Each file is recorded in its own transaction right after its rename; when the
database refuses it, the names go back, so disk and database never part. Logs carry ids and counts, never names or
paths.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from ...db import SessionLocal
from ...models import (
    Download,
    Episode,
    EpisodeFile,
    EpisodeNumber,
    EpisodeVersion,
    ExtraFile,
    HistoryEntry,
    SeasonFolder,
    Title,
    Version,
    VersionDefinition,
)
from .. import folders, naming, naming_series
from ..rename import guard as rename_guard
from ..series import names
from . import files, store

logger = logging.getLogger("nexcrate.import")


@dataclass(frozen=True)
class Renamed:
    version_id: int
    season: int | None


def _rename(old: Path, new: Path) -> None:
    """The new name first, then the old one goes. Raises ``OSError``; nothing is replaced."""
    if os.path.lexists(new):
        raise FileExistsError(new.name)
    try:
        os.link(old, new)
    except OSError:
        os.rename(old, new)
        return
    old.unlink()


def rename_title(title_id: int) -> int:
    """Rename the TBA files of every own series version of a title whose episodes have a title now. Returns how many."""
    try:
        with rename_guard.filing(title_id):
            return _rename_title(title_id)
    except rename_guard.Busy:
        return 0


def _rename_title(title_id: int) -> int:
    renamed: list[Renamed] = []
    with SessionLocal() as db:
        title = db.get(Title, title_id)
        if title is None or title.kind != "series":
            return 0
        language = names.account_language(db)
        versions = list(db.scalars(select(Version).where(Version.title_id == title.id, Version.source_id.is_(None))))
        for version in versions:
            renamed.extend(_rename_version(db, title, version, language))
        db.commit()
    if renamed:
        from .. import companions_series

        for version_id in {item.version_id for item in renamed}:
            seasons = {item.season for item in renamed if item.version_id == version_id and item.season is not None}
            companions_series.write_version(version_id, seasons)
        logger.info("Title %d: %d files named with TBA were renamed", title_id, len(renamed))
    return len(renamed)


def _rename_version(db: object, title: Title, version: Version, language: str) -> list[Renamed]:
    rows = list(
        db.scalars(  # type: ignore[attr-defined]
            select(EpisodeFile).where(EpisodeFile.version_id == version.id, EpisodeFile.named_tba.is_(True))
        )
    )
    if not rows or not version.root_folder or not version.relative_path:
        return []
    try:
        root, _mount = folders.visible(version.root_folder)
    except folders.NotVisible:
        return []
    series_folder = root / version.relative_path
    if files.is_link(series_folder) or not files.strictly_inside(series_folder, root):
        return []
    definition = db.get(VersionDefinition, version.version_definition_id)  # type: ignore[attr-defined]
    current = naming_series.for_version(db, definition)  # type: ignore[arg-type]
    series = naming_series.SeriesFacts(
        title=title.title or title.title_en or "", year=title.year, tmdb_id=title.tmdb_id, tvdb_id=title.tvdb_id,
        imdb_id=title.imdb_id,
    )  # fmt: skip
    done: list[Renamed] = []
    moment = store.now()
    for row in rows:
        if not (row.file_ref or "").startswith("nexcrate:"):
            continue
        episodes = list(
            db.execute(  # type: ignore[attr-defined]
                select(EpisodeVersion.episode_id).where(
                    EpisodeVersion.version_id == version.id, EpisodeVersion.episode_file_id == row.id
                )
            ).scalars()
        )
        facts, _numbering = _facts(db, episodes, row.name_numbering or "tmdb", language)
        if not facts or any(item.title is None for item in facts):
            continue
        parts = [part for part in row.relative_path.split("/") if part]
        old = series_folder.joinpath(*parts)
        if files.is_link(old) or not old.is_file() or not files.strictly_inside(old, series_folder):
            continue
        download = db.get(Download, int(row.file_ref[9:])) if row.file_ref[9:].isdigit() else None  # type: ignore[attr-defined]
        # The quality and media data stored with the file, as filing named it.
        release = naming.ReleaseFacts(
            release_title=row.release_title or "",
            formats=tuple(download.formats or []) if download is not None else (),
            original_filename=Path(old.name).stem,
            quality=row.quality,
            media=row.media_info if isinstance(row.media_info, dict) else None,
            release_group=row.release_group,
        )
        daily = title.series_type == "daily"
        numbering = row.name_numbering or "tmdb"
        built = naming_series.file_name(
            current,
            series,
            facts,
            release,
            old.suffix,
            daily=daily,
            numbering=numbering,
            anime=title.series_type == "anime",
        )
        if built.name == old.name:
            row.named_tba = built.named_tba
            continue
        new = old.with_name(built.name)
        try:
            _rename(old, new)
        except OSError:
            logger.info("Episode file %d could not be renamed after TBA; it keeps its name", row.id)
            continue
        moved: list[tuple[Path, Path]] = [(old, new)]
        old_stem, new_stem = Path(old.name).stem, Path(new.name).stem
        for extra in db.scalars(select(ExtraFile).where(ExtraFile.episode_file_id == row.id)):  # type: ignore[attr-defined]
            extra_parts = [part for part in extra.relative_path.split("/") if part]
            if not extra_parts or not extra_parts[-1].startswith(old_stem + "."):
                continue
            # A series subtitle's path is relative to the series folder, as its episode file's.
            source = series_folder.joinpath(*extra_parts)
            target = source.with_name(new_stem + extra_parts[-1][len(old_stem) :])
            try:
                if files.strictly_inside(source, series_folder) and not files.is_link(source):
                    _rename(source, target)
                    moved.append((source, target))
                    extra.relative_path = target.relative_to(series_folder).as_posix()
            except OSError:
                logger.info("A subtitle of episode file %d could not be renamed", row.id)
        row.relative_path = "/".join([*parts[:-1], new.name])
        row.named_tba = built.named_tba
        row.updated_at = moment
        db.add(  # type: ignore[attr-defined]
            HistoryEntry(
                title_id=title.id, version_id=version.id, version_definition_id=version.version_definition_id,
                version_label=definition.label if definition is not None else "", event="episode_renamed", at=moment,
                detail=",".join(f"S{item.season:02d}E{item.episode:02d}" for item in facts),
            )
        )  # fmt: skip
        season = db.scalar(  # type: ignore[attr-defined]
            select(SeasonFolder.season_number).where(
                SeasonFolder.version_id == version.id, SeasonFolder.name == (parts[-2] if len(parts) > 1 else "")
            )
        )
        try:
            db.commit()  # type: ignore[attr-defined]
        except SQLAlchemyError:
            db.rollback()  # type: ignore[attr-defined]
            for source, target in reversed(moved):
                try:
                    _rename(target, source)
                except OSError:
                    logger.warning("Episode file %d: a renamed file could not get its old name back", row.id)
            logger.warning("Episode file %d: renaming after TBA is undone, the database refused it", row.id)
            continue
        done.append(Renamed(version.id, season))
    return done


def _facts(
    db: object, episode_ids: list[int], numbering: str, language: str
) -> tuple[list[naming_series.EpisodeFacts], str]:
    rows = list(db.scalars(select(Episode).where(Episode.id.in_(episode_ids))))  # type: ignore[attr-defined]
    tvdb = {
        number.episode_id: (number.season, number.episode)
        for number in db.scalars(  # type: ignore[attr-defined]
            select(EpisodeNumber).where(EpisodeNumber.episode_id.in_(episode_ids), EpisodeNumber.scheme == "tvdb")
        )
        if number.season is not None and number.episode is not None
    }
    use_tvdb = numbering == "tvdb" and rows and all(row.id in tvdb for row in rows)
    # The numbers an anime series counts through (A5); none for another series.
    counted = dict(
        db.execute(  # type: ignore[attr-defined]
            select(EpisodeNumber.episode_id, EpisodeNumber.absolute).where(
                EpisodeNumber.episode_id.in_(episode_ids), EpisodeNumber.scheme == "absolute"
            )
        )
        .tuples()
        .all()
    )
    facts = []
    for row in sorted(rows, key=lambda item: (item.season_number, item.episode_number)):
        season, number = tvdb[row.id] if use_tvdb else (row.season_number, row.episode_number)
        facts.append(
            naming_series.EpisodeFacts(
                season=int(season),
                episode=int(number),
                air_date=row.air_date,
                title=names.file_title(row.name, row.name_en, language),
                absolute=counted.get(row.id),
            )
        )
    return facts, ("tvdb" if use_tvdb else "tmdb")
