"""Renaming a series' versions (decisions 29 to 31).

The series folder takes the name of the version's naming; every episode file goes into its season folder (the
specials folder for season 0) with the name filing builds: the numbering of the version (TVDB's only when every
episode of the file has one, as filing does), the titles in the account language, the release name, quality, media
data and release group kept with the file (media data of Sonarr's own shape included), and the formats TRaSH marks
for renaming (those of nexcrate's download, else judged again
from the release name when the pattern names formats). Subtitles follow their video.

Files without an episode (unclear or left out) keep their place inside the series folder. When the series folder is
renamed, everything else in it moves along; a season folder whose episode files all go into one new season folder
takes its other files with it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ...models import (
    Download,
    EpisodeFile,
    EpisodeVersion,
    ExtraFile,
    SeasonFolder,
    Title,
    Version,
    VersionDefinition,
)
from .. import folders, naming, naming_series
from ..downloads import files
from ..downloads.loading import _rename_formats
from ..downloads.tba import _facts
from ..profiles import store as profile_store
from ..releases import series_decision
from ..series import names
from .plan import Move, TitlePlan, files_below, finish, parts_of, posix, put


def _formats(
    db: OrmSession,
    row: EpisodeFile,
    current: naming_series.SeriesNaming,
    release_title: str,
    rules: dict | None,
    file_name: str = "",
    original_language: str | None = None,
) -> tuple[str, ...]:
    """The formats TRaSH marks for renaming: those of nexcrate's own download, else matched against the kept release
    name, else against the file's own name, as Sonarr does for a file without a scene name (24.09.2026: the files
    Sonarr handed over carry no release name, and a rename dropped their ``[German DL]``). The series' original language
    goes along, as in a search: TRaSH's "German DL" wants German and the original language, and without knowing it
    ``DL`` only reaches "German DL (undefined)", which names no file."""
    if "custom format" not in (current.episode_file + current.daily_file + current.anime_file).casefold():
        return ()
    ref = row.file_ref or ""
    if ref.startswith("nexcrate:") and ref[9:].isdigit():
        download = db.get(Download, int(ref[9:]))
        if download is not None:
            return tuple(download.formats or [])
    judged = release_title or file_name
    if rules is None or not judged:
        return ()
    try:
        context = series_decision.SeriesContext(original_language=original_language)
        matched = series_decision.evaluate(rules, judged, context)["matched"]
    except ValueError:
        return ()
    return tuple(_rename_formats(rules, matched, "series"))


def _wants_facts(current: naming_series.SeriesNaming, daily: bool, anime: bool = False) -> bool:
    """Whether the file pattern in use names tokens only a release name or media data fills."""
    pattern = current.anime_file if anime else current.daily_file if daily else current.episode_file
    return naming.wants_facts(pattern, naming_series.KNOWN_KEYS, naming_series.FILE_KEYS)


def _rules(db: OrmSession, version: Version) -> dict | None:
    profile = profile_store.of_version(db, version.version_definition_id)
    series_type = db.scalar(select(Title.series_type).where(Title.id == version.title_id))
    rules = profile_store.series_rules(profile, series_type)
    return rules if rules is not None and rules.get("kind") == "series" else None


def plan_title(db: OrmSession, title: Title) -> TitlePlan:
    plan = TitlePlan(kind="series", title_id=title.id, name=title.title or title.title_en or "", title_ids=[title.id])
    versions = list(
        db.scalars(
            select(Version)
            .where(Version.title_id == title.id, Version.source_id.is_(None), Version.id.in_(_with_files()))
            .order_by(Version.id)
        )
    )
    language = names.account_language(db)
    series = naming_series.SeriesFacts(
        title=title.title or title.title_en or "",
        year=title.year,
        tmdb_id=title.tmdb_id,
        tvdb_id=title.tvdb_id,
        imdb_id=title.imdb_id,
    )
    daily = title.series_type == "daily"
    anime = title.series_type == "anime"
    for version in versions:
        if not version.relative_path:
            continue
        try:
            root, _mount = folders.visible(version.root_folder)
        except folders.NotVisible:
            return plan.skipped("folder_missing")
        series_dir = root.joinpath(*parts_of(version.relative_path))
        if not files.strictly_inside(series_dir, root) or files.is_link(series_dir):
            return plan.skipped("link")
        if not series_dir.is_dir():
            return plan.skipped("folder_missing")
        if root not in plan.roots:
            plan.roots.append(root)
        definition = db.get(VersionDefinition, version.version_definition_id)
        current = naming_series.for_version(db, definition)
        rules = _rules(db, version)
        shared = _shared(db, version, root)
        new_series_dir = series_dir if shared else series_dir.parent / naming_series.series_folder_name(current, series)
        if shared:
            plan.note("folder_shared")
        put(plan.before, "versions", version.id, relative_path=version.relative_path)
        put(plan.after, "versions", version.id, relative_path=posix(new_series_dir, root))
        if new_series_dir != series_dir:
            plan.folders.append((posix(series_dir, root), posix(new_series_dir, root)))
        plan.companions.append(("series", version.id))

        rows = list(
            db.scalars(select(EpisodeFile).where(EpisodeFile.version_id == version.id).order_by(EpisodeFile.id))
        )
        episodes_of: dict[int, list[int]] = {}
        for episode_id, file_id in db.execute(
            select(EpisodeVersion.episode_id, EpisodeVersion.episode_file_id).where(
                EpisodeVersion.version_id == version.id, EpisodeVersion.episode_file_id.is_not(None)
            )
        ):
            episodes_of.setdefault(int(file_id), []).append(int(episode_id))
        # The second half of a double episode names its episode itself.
        for row in rows:
            if row.part == 2 and row.part_of_episode_id is not None:
                episodes_of.setdefault(row.id, []).append(int(row.part_of_episode_id))
        tracked: set[Path] = set()
        missing_facts = False
        # Which new season folder each old folder's episode files go to; a folder with one answer moves as a whole.
        goes_to: dict[Path, set[Path]] = {}
        seasons_after: dict[int, str] = {}
        extras = list(
            db.scalars(
                select(ExtraFile).where(ExtraFile.version_id == version.id, ExtraFile.episode_file_id.is_not(None))
            )
        )
        for row in rows:
            parts = parts_of(row.relative_path)
            old = series_dir.joinpath(*parts)
            if not parts or not files.strictly_inside(old, series_dir):
                continue
            tracked.add(old)
            episode_ids = episodes_of.get(row.id, [])
            if row.left_out or not episode_ids:
                # Unclear and left out: the place in the series folder stays.
                target = new_series_dir / old.relative_to(series_dir)
                if os.path.lexists(old):
                    plan.moves.append(Move(old, target, "other"))
                continue
            if files.is_link(old):
                return plan.skipped("link")
            if not old.is_file():
                return plan.skipped("file_missing")
            facts, _numbering = _facts(db, episode_ids, current.numbering, language)
            if not facts:
                continue
            season = facts[0].season
            season_name = naming_series.season_folder_name(current, series, season)
            # Without a kept release name the name stays empty: the file name is nexcrate's own or the source's, and
            # reading a group out of it again would change the name on every rename (bench, 19.09.2026).
            release_title = row.release_title or ""
            release = naming.ReleaseFacts(
                release_title=release_title,
                formats=_formats(db, row, current, release_title, rules, old.stem, title.original_language),
                original_filename=old.stem,
                quality=row.quality,
                media=row.media_info if isinstance(row.media_info, dict) else None,
                release_group=row.release_group,
            )
            # Neither a release name nor media data: the name loses what they fill, and the preview says so
            # (finding of 20.09.2026).
            if not release_title and release.media is None and _wants_facts(current, daily, anime):
                missing_facts = True
            built = naming_series.file_name(
                current,
                series,
                facts,
                release,
                old.suffix,
                daily=daily,
                numbering=current.numbering,
                anime=anime,
                part=row.part,
            )
            target_dir = new_series_dir / season_name
            target = target_dir / built.name
            plan.moves.append(Move(old, target, "file"))
            goes_to.setdefault(old.parent, set()).add(target_dir)
            seasons_after.setdefault(season, season_name)
            put(
                plan.before,
                "episode_files",
                row.id,
                relative_path=row.relative_path,
                named_tba=row.named_tba,
                name_numbering=row.name_numbering,
            )
            put(
                plan.after,
                "episode_files",
                row.id,
                relative_path=posix(target, new_series_dir),
                named_tba=built.named_tba,
                name_numbering=built.numbering,
            )
            old_stem, new_stem = old.stem, Path(built.name).stem
            for extra in extras:
                if extra.episode_file_id != row.id:
                    continue
                extra_parts = parts_of(extra.relative_path)
                source = series_dir.joinpath(*extra_parts)
                if not extra_parts or not files.strictly_inside(source, series_dir):
                    continue
                tracked.add(source)
                if source.name.startswith(old_stem + "."):
                    moved = target_dir / (new_stem + source.name[len(old_stem) :])
                else:
                    moved = new_series_dir / source.relative_to(series_dir)
                if os.path.lexists(source):
                    plan.moves.append(Move(source, moved, "extra"))
                put(plan.before, "extra_files", extra.id, relative_path=extra.relative_path)
                put(plan.after, "extra_files", extra.id, relative_path=posix(moved, new_series_dir))

        if missing_facts:
            plan.note("facts_missing")

        for season_row in db.scalars(select(SeasonFolder).where(SeasonFolder.version_id == version.id)):
            key = f"{version.id}:{season_row.season_number}"
            put(plan.before, "season_folders", key, name=season_row.name)
            put(plan.after, "season_folders", key, name=seasons_after.get(season_row.season_number, season_row.name))
        for season, name in seasons_after.items():
            key = f"{version.id}:{season}"
            if key not in plan.before.get("season_folders", {}):
                put(plan.before, "season_folders", key, name=None)
                put(plan.after, "season_folders", key, name=name)

        # Everything else: a season folder with one new home takes its files along, the rest keeps its place.
        whole = {old: next(iter(new)) for old, new in goes_to.items() if len(new) == 1 and old != series_dir}
        for old_dir, new_dir in whole.items():
            if old_dir != new_dir and posix(old_dir, series_dir) != posix(new_dir, new_series_dir):
                plan.folders.append((posix(old_dir, root), posix(new_dir, root)))
        if shared and not whole:
            continue
        for path in files_below(series_dir, tracked):
            home = next((old_dir for old_dir in whole if path.is_relative_to(old_dir)), None)
            if home is not None:
                target = whole[home] / path.relative_to(home)
            elif shared:
                continue
            else:
                target = new_series_dir / path.relative_to(series_dir)
            plan.moves.append(Move(path, target, "other"))
    return plan


def _with_files() -> Any:
    """Series versions hold their files per episode; ``has_file`` is a movie's."""
    return select(EpisodeFile.version_id).distinct()


def _shared(db: OrmSession, version: Version, root: Path) -> bool:
    """Whether another version's folder is the same series folder or lies inside it."""
    prefix = version.relative_path or ""
    rows = db.execute(
        select(Version.id, Version.root_folder, Version.relative_path).where(
            Version.id != version.id,
            (Version.relative_path == prefix) | Version.relative_path.like(prefix.rstrip("/") + "/%"),
        )
    )
    for _version_id, root_folder, _relative in rows:
        try:
            other_root, _mount = folders.visible(root_folder)
        except folders.NotVisible:
            continue
        if other_root == root:
            return True
    return False


def plan(db: OrmSession, title_id: int, planned_elsewhere: set[Path] | None = None) -> TitlePlan | None:
    title = db.get(Title, title_id)
    if title is None or title.kind != "series":
        return None
    return finish(plan_title(db, title), planned_elsewhere)


def title_ids(db: OrmSession) -> list[int]:
    return list(
        db.scalars(
            select(Title.id)
            .where(
                Title.kind == "series",
                Title.id.in_(
                    select(Version.title_id).where(Version.source_id.is_(None), Version.id.in_(_with_files()))
                ),
            )
            .order_by(Title.sort_key, Title.id)
        )
    )
