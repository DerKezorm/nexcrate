"""Renaming a movie's versions (decisions 6, 7, 17, 18, 20 and 32).

Each version's file takes the name its version's naming builds now, with the facts filing used: the title from the
database, the release name kept with the file (never its file name), the stored quality, media data and release
group, and the formats TRaSH marks for renaming, judged again from the release name when the pattern names them.
Without a kept release name the group is the one stored with the file, and media data taken over from an Arr counts
as media data. The movie folder
is the folder the file lies in; it is renamed when only this title's versions lie in it and they all want the same
new name, and then everything else in it moves along. Otherwise the folder stays and the files are renamed in it.
Subtitles whose name starts with the video's follow its new name.
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ...models import ExtraFile, Title, Version, VersionDefinition
from .. import folders, judging, naming
from ..downloads import files
from ..downloads.loading import _rename_formats
from ..profiles import store as profile_store
from ..releases import decision
from .plan import Move, TitlePlan, files_below, finish, parts_of, posix, put


def _formats(
    db: OrmSession,
    version: Version,
    current: naming.Naming,
    release_title: str,
    language: str | None,
    file_name: str = "",
) -> tuple[str, ...]:
    """The matched formats TRaSH marks for renaming, only when the pattern names formats at all: against the kept
    release name, else against the file's own name, as Radarr does for a file without a scene name."""
    judged = release_title or file_name
    if not judged or "custom format" not in current.movie_file.casefold():
        return ()
    profile = profile_store.of_version(db, version.version_definition_id)
    rules = judging.usable_rules(profile)
    if rules is None:
        return ()
    try:
        matched = decision.evaluate(rules, judged, decision.ReleaseContext(original_language=language))[
            "matched"
        ]
    except ValueError:
        return ()
    return tuple(_rename_formats(rules, matched, "movie"))


def _wants_facts(pattern: str) -> bool:
    """Whether the file pattern names tokens only a release name or media data fills."""
    return naming.wants_facts(pattern, naming.KNOWN_KEYS, naming.FILE_KEYS)


def own_versions(db: OrmSession, title_id: int) -> list[Version]:
    return list(
        db.scalars(
            select(Version)
            .where(Version.title_id == title_id, Version.source_id.is_(None), Version.has_file.is_(True))
            .order_by(Version.id)
        )
    )


def plan_title(db: OrmSession, title: Title) -> TitlePlan:
    plan = TitlePlan(kind="movie", title_id=title.id, name=title.title or "", title_ids=[title.id])
    versions = [version for version in own_versions(db, title.id) if version.relative_path]
    if not versions:
        return plan
    movie = naming.MovieFacts(
        title=title.title or "",
        original_title=title.original_title,
        year=title.year,
        tmdb_id=title.tmdb_id,
        imdb_id=title.imdb_id,
    )
    # Per version: root, file, the folder it lies in (None: straight in the root), the wanted folder and file name.
    found: list[tuple[Version, Path, Path, Path | None, str, str]] = []
    # A file without a kept release name and without media data loses the parts of its name they fill (finding of
    # 20.09.2026); the preview says so instead of letting the name become shorter without a word.
    missing_facts = False
    for version in versions:
        try:
            root, _mount = folders.visible(version.root_folder)
        except folders.NotVisible:
            return plan.skipped("folder_missing")
        parts = parts_of(version.relative_path)
        file = root.joinpath(*parts)
        if not files.strictly_inside(file, root) or files.is_link(file):
            return plan.skipped("link")
        if not file.is_file():
            return plan.skipped("file_missing")
        definition = db.get(VersionDefinition, version.version_definition_id)
        current = naming.for_version(db, definition)
        # Without a kept release name the name stays empty, as for series: never the file's own name read again.
        release_title = version.release_title or ""
        release = naming.ReleaseFacts(
            release_title=release_title,
            formats=_formats(db, version, current, release_title, title.original_language, file.stem),
            original_filename=file.stem,
            quality=version.quality,
            media=version.media_info if isinstance(version.media_info, dict) else None,
            release_group=version.release_group,
        )
        if not release_title and release.media is None and _wants_facts(current.movie_file):
            missing_facts = True
        folder = file.parent if file.parent != root else None
        found.append(
            (
                version,
                root,
                file,
                folder,
                naming.folder_name(current, movie),
                naming.file_name(current, movie, release, file.suffix),
            )
        )
        if root not in plan.roots:
            plan.roots.append(root)

    if missing_facts:
        plan.note("facts_missing")

    # A folder is renamed only when nothing but this title's versions lies in it and they agree on the new name.
    wanted: dict[Path, set[str]] = {}
    for _version, _root, _file, folder, folder_name, _file_name in found:
        if folder is not None:
            wanted.setdefault(folder, set()).add(folder_name)
    mine = {version.id for version in versions}
    movable: dict[Path, Path] = {}
    for folder, names in wanted.items():
        root = next(item[1] for item in found if item[3] == folder)
        others = _others_in(db, folder, root, mine)
        if len(names) == 1 and not others:
            movable[folder] = folder.parent / next(iter(names))
        elif others:
            plan.note("folder_shared")

    tracked: set[Path] = set()
    for version, root, file, folder, folder_name, file_name in found:
        if folder is None:
            target_dir = root / folder_name
        else:
            target_dir = movable.get(folder, folder)
        new_file = target_dir / file_name
        plan.moves.append(Move(file, new_file, "file"))
        tracked.add(file)
        put(plan.before, "versions", version.id, relative_path=version.relative_path)
        put(plan.after, "versions", version.id, relative_path=posix(new_file, root))
        plan.companions.append(("movie", version.id))
        old_stem, new_stem = file.stem, Path(file_name).stem
        for extra in db.scalars(select(ExtraFile).where(ExtraFile.version_id == version.id)):
            extra_parts = parts_of(extra.relative_path)
            source = root.joinpath(*extra_parts)
            if not extra_parts or not files.strictly_inside(source, root):
                continue
            tracked.add(source)
            if source.name.startswith(old_stem + ".") and source.parent == file.parent:
                target = target_dir / (new_stem + source.name[len(old_stem) :])
            elif folder is not None and folder in movable and source.is_relative_to(folder):
                target = movable[folder] / source.relative_to(folder)
            else:
                continue
            if os.path.lexists(source):
                plan.moves.append(Move(source, target, "extra"))
            put(plan.before, "extra_files", extra.id, relative_path=extra.relative_path)
            put(plan.after, "extra_files", extra.id, relative_path=posix(target, root))

    for folder, target in movable.items():
        root = next(item[1] for item in found if item[3] == folder)
        if folder != target:
            plan.folders.append((posix(folder, root), posix(target, root)))
        for path in files_below(folder, tracked):
            plan.moves.append(Move(path, target / path.relative_to(folder), "other"))
    for _version, _root, _file, folder, folder_name, _name in found:
        if folder is None:
            plan.folders.append(("", folder_name))
    return plan


def _others_in(db: OrmSession, folder: Path, root: Path, mine: set[int]) -> bool:
    """Whether a version that is not this title's own lies in the folder or below it."""
    prefix = posix(folder, root) + "/"
    rows = db.execute(
        select(Version.id, Version.root_folder, Version.relative_path).where(Version.relative_path.like(prefix + "%"))
    )
    for version_id, root_folder, _relative in rows:
        if version_id in mine:
            continue
        try:
            other_root, _mount = folders.visible(root_folder)
        except folders.NotVisible:
            continue
        if other_root == root:
            return True
    return False


def plan(db: OrmSession, title_id: int, planned_elsewhere: set[Path] | None = None) -> TitlePlan | None:
    title = db.get(Title, title_id)
    if title is None or title.kind != "movie":
        return None
    return finish(plan_title(db, title), planned_elsewhere)


def title_ids(db: OrmSession) -> list[int]:
    """Movies with a file of nexcrate's own, by title."""
    return list(
        db.scalars(
            select(Title.id)
            .where(
                Title.kind == "movie",
                Title.id.in_(select(Version.title_id).where(Version.source_id.is_(None), Version.has_file.is_(True))),
            )
            .order_by(Title.sort_key, Title.id)
        )
    )
