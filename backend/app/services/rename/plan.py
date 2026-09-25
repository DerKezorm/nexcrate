"""What renaming one title means, before anything moves (decisions 6 to 11 and 15 to 21).

A ``TitlePlan`` is a list of moves (old path, new path, both absolute and inside one root folder) and the database
fields of the title's rows before and after. The planners in ``movies``, ``series`` and ``music`` build it; ``finish``
checks it: identical moves go, a target that exists and does not move away itself, or two moves to one target, make a
conflict; a file still in transfer or a link makes the title wait.

The fields are kept as a snapshot: ``{"versions": {"12": {"relative_path": "…"}}, "episode_files": {…}, …}``. The
same shape writes the new state after the moves and the old one back on undo.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session as OrmSession

from ...models import Artist, EpisodeFile, ExtraFile, SeasonFolder, TrackFile, Version
from .. import naming
from ..downloads import files

#: System rests (decision 21): a folder holding only these counts as empty. Files of these names move along with
#: their folder, so the undo brings them back; the folders (``@eaDir``, Synology's thumbnails) stay and go to the
#: recycle folder with the old folder.
SYSTEM_NAMES = frozenset({".ds_store", "thumbs.db", "desktop.ini", "@eadir", ".appledouble", "._.ds_store"})
#: Images and lyrics follow an album out of a shared folder (the owner's answer G3).
IMAGE_ENDINGS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"})
AUDIO_ENDINGS = frozenset(
    {".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".aiff", ".aif", ".wma", ".ape", ".wv", ".alac", ".dsf"}
)
#: How many moves of a title the preview lists; the rest is a number.
STEPS_SHOWN = 400


@dataclass(frozen=True)
class Move:
    old: Path
    new: Path
    #: ``file`` (a tracked media file), ``extra`` (a subtitle or lyrics), ``other`` (anything else in the folder).
    what: str = "file"


@dataclass
class TitlePlan:
    kind: str
    #: The title for movies and series; for music the artist's first album title, ``artist_id`` names the artist.
    title_id: int
    name: str
    title_ids: list[int] = field(default_factory=list)
    artist_id: int | None = None
    roots: list[Path] = field(default_factory=list)
    moves: list[Move] = field(default_factory=list)
    #: Folders as the preview names them, relative to their root: (old, new).
    folders: list[tuple[str, str]] = field(default_factory=list)
    before: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    after: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    #: Folders that may be empty after the moves, deepest first.
    old_folders: list[Path] = field(default_factory=list)
    #: A code the whole title is skipped with, and its values.
    skip: str | None = None
    skip_values: dict[str, Any] = field(default_factory=dict)
    notes: list[dict[str, Any]] = field(default_factory=list)
    #: Files that stay where they are although their folder is renamed.
    left: int = 0
    #: What to write again afterwards: ("movie" | "series" | "album", version id).
    companions: list[tuple[str, int]] = field(default_factory=list)
    #: Albums of a music plan: (title id, album name, number of files).
    albums: list[tuple[int, str, int]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.moves) or self.before != self.after

    def note(self, code: str, **values: Any) -> None:
        self.notes.append({"code": code, **values})

    def skipped(self, code: str, **values: Any) -> TitlePlan:
        self.skip, self.skip_values = code, values
        return self

    def file_count(self) -> int:
        return sum(1 for move in self.moves if move.what == "file")


def parts_of(relative: str | None) -> list[str]:
    return [part for part in (relative or "").replace("\\", "/").split("/") if part and part not in (".", "..")]


def posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def is_system(path: Path) -> bool:
    return path.name.casefold() in SYSTEM_NAMES


def same_entry(one: Path, other: Path) -> bool:
    """Whether two paths name one entry on disk: the same path, or one name in another case on a disk that does not
    tell cases apart."""
    if one == other:
        return True
    try:
        return os.path.samefile(one, other)
    except OSError:
        return False


def files_below(folder: Path, skip: set[Path]) -> list[Path]:
    """Every file below a folder except those in ``skip``, links as they are, never followed. System rest files
    (``.DS_Store``, ``desktop.ini``) are files like any other; system rest folders (``@eaDir``) are not entered."""
    found: list[Path] = []
    if not folder.is_dir() or files.is_link(folder):
        return found
    for current, folder_names, file_names in os.walk(folder, followlinks=False):
        base = Path(current)
        folder_names[:] = [name for name in folder_names if name.casefold() not in SYSTEM_NAMES]
        for name in folder_names:
            if files.is_link(base / name):
                found.append(base / name)
        for name in file_names:
            path = base / name
            if path in skip:
                continue
            found.append(path)
    return found


#: nexcrate's own companion files: a folder left with nothing but these and system rests counts as empty after a move.
COMPANION_NAMES = frozenset({"release.nex", ".release.nex"})


#: A folder holding none of these holds no media: only rests of an older rip (``.sfv``, ``.m3u``, covers).
MEDIA_ENDINGS = AUDIO_ENDINGS | {".mkv", ".mp4", ".m4v", ".avi", ".ts", ".m2ts", ".mov", ".wmv", ".webm"}


def holds_media(folder: Path) -> bool:
    """Whether any media file lies in the folder or below it. A folder without one is a rest an album may move into
    (the owner's answer of 19.09.2026)."""
    if not folder.is_dir() or files.is_link(folder):
        return False
    for _current, folder_names, file_names in os.walk(folder, followlinks=False):
        folder_names[:] = [name for name in folder_names if name.casefold() not in SYSTEM_NAMES]
        if any(Path(name).suffix.casefold() in MEDIA_ENDINGS for name in file_names):
            return True
    return False


def only_system_rests(folder: Path) -> bool:
    try:
        return all(entry.name.casefold() in SYSTEM_NAMES | COMPANION_NAMES for entry in folder.iterdir())
    except OSError:
        return False


def finish(plan: TitlePlan, planned_elsewhere: set[Path] | None = None) -> TitlePlan:
    """Drop moves that change nothing, then look for conflicts. Leaves the plan skipped with a reason when one is found.

    ``planned_elsewhere`` holds targets other titles of the same preview claim; one of them here is a conflict too.
    """
    moves = [move for move in plan.moves if move.old != move.new]
    plan.moves = moves
    if plan.skip is not None:
        return plan
    sources = {move.old for move in moves}
    targets: dict[Path, Move] = {}
    for move in moves:
        if files.is_link(move.old.parent) or (move.what == "file" and files.is_link(move.old)):
            return plan.skipped("link")
        if move.old.name.endswith(naming.PARTIAL_SUFFIX):
            return plan.skipped("busy")
        key = Path(os.path.normcase(str(move.new)))
        if key in targets:
            return plan.skipped("target_twice", name=move.new.name)
        targets[key] = move
        if os.path.lexists(move.new) and move.new not in sources and not same_entry(move.new, move.old):
            return plan.skipped("target_taken", name=move.new.name)
        if planned_elsewhere is not None and move.new in planned_elsewhere:
            return plan.skipped("target_planned", name=move.new.name)
        ancestor = move.new.parent
        while ancestor not in plan.roots and ancestor.parent != ancestor:
            if os.path.lexists(ancestor) and not ancestor.is_dir():
                return plan.skipped("target_taken", name=ancestor.name)
            if files.is_link(ancestor):
                return plan.skipped("link")
            ancestor = ancestor.parent
    plan.old_folders = _old_folders(plan)
    return plan


def _old_folders(plan: TitlePlan) -> list[Path]:
    """The folders the moves leave, and their parents up to the root, deepest first."""
    new_ancestors: set[Path] = set()
    for move in plan.moves:
        new_ancestors.update(move.new.parents)
    found: set[Path] = set()
    for move in plan.moves:
        for ancestor in move.old.parents:
            if ancestor in plan.roots or not any(ancestor.is_relative_to(root) for root in plan.roots):
                break
            if ancestor not in new_ancestors:
                found.add(ancestor)
    return sorted(found, key=lambda path: (-len(path.parts), str(path)))


# --- Snapshots ---------------------------------------------------------------------------------------------------- #

_TABLES: dict[str, tuple[type, tuple[str, ...]]] = {
    "versions": (Version, ("relative_path",)),
    "episode_files": (EpisodeFile, ("relative_path", "named_tba", "name_numbering")),
    "track_files": (TrackFile, ("relative_path",)),
    "extra_files": (ExtraFile, ("relative_path",)),
    "artists": (Artist, ("folder",)),
}


def put(snapshot: dict[str, dict[str, dict[str, Any]]], table: str, key: object, **values: Any) -> None:
    snapshot.setdefault(table, {}).setdefault(str(key), {}).update(values)


def read(db: OrmSession, spec: dict[str, dict[str, dict[str, Any]]]) -> dict[str, dict[str, dict[str, Any]]]:
    """The current values of the fields ``spec`` names, in its shape. A row that is gone reads as None."""
    found: dict[str, dict[str, dict[str, Any]]] = {}
    for table, rows in spec.items():
        for key, fields in rows.items():
            if table == "season_folders":
                version_id, season = (int(part) for part in key.split(":"))
                row = db.get(SeasonFolder, (version_id, season))
                found.setdefault(table, {})[key] = {"name": row.name if row is not None else None}
                continue
            model, _names = _TABLES[table]
            item = db.get(model, int(key))
            found.setdefault(table, {})[key] = {
                name: getattr(item, name) if item is not None else None for name in fields
            }
    return found


def write(db: OrmSession, snapshot: dict[str, dict[str, dict[str, Any]]]) -> None:
    """Set the fields of a snapshot; a season folder that is not there yet is added, one with None removed."""
    for table, rows in snapshot.items():
        for key, fields in rows.items():
            if table == "season_folders":
                version_id, season = (int(part) for part in key.split(":"))
                row = db.get(SeasonFolder, (version_id, season))
                name = fields.get("name")
                if name is None:
                    if row is not None:
                        db.delete(row)
                elif row is None:
                    db.add(SeasonFolder(version_id=version_id, season_number=season, name=name))
                else:
                    row.name = name
                continue
            model, allowed = _TABLES[table]
            item = db.get(model, int(key))
            if item is None:
                continue
            for name, value in fields.items():
                if name in allowed:
                    setattr(item, name, value)


def unique(paths: Iterable[Path]) -> list[Path]:
    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            result.append(path)
    return result
