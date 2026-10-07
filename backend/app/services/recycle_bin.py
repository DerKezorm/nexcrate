"""The recycle bin ("Der Papierkorb"): deleting a version's files, and putting them back.

Until V2 nexcrate never deleted a media file. Now the owner can, and a program with the scope ``request`` too, and both
only ever into the bin: a file moves into ``.nexcrate-recycle/<day>/`` of its version's root folder, where replaced
files have always gone, and a row in ``recycle_entries`` remembers where it came from and what nexcrate knew about it.
The daily cleanup deletes day folders after ``recycle_days``; a row whose file went with them goes too
(``forget_missing``). Only the owner deletes for good before that (``purge``, ``empty``).

A movie version's file goes with its subtitles; the version keeps its watching and wants a file again when it is
watched, as in Radarr. A series version's files go per episode file: a file that holds two episodes goes whole when one
of them is asked for. An album's files go one by one, the whole album or the tracks asked for, each as an entry of its
own; ``release.nex`` and files nexcrate does not know stay, the folder may be another album's too
. The album stays watched and is ``incomplete`` afterwards, as in Lidarr.

A movie file the import replaced is an entry too (``replaced_movie``, ``deleted_by`` replaced, since 26.09.2026), so a
replacement can be undone: before, the old file lay in the recycle folder unseen. It comes back once the version's new
file went into the bin; until then the version has another file (``recycle_slot_taken``). Episode and track files an
import replaced still go into the recycle folder without an entry.

⚠️ Files move before the commit. When a later step fails, what moved is moved back and nothing is written.
⚠️ Log lines carry ids and counts, never a title, a path or a name.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from ..db import SessionLocal
from ..meldungen import error
from ..models import (
    DiskRoot,
    Download,
    Episode,
    EpisodeFile,
    EpisodeVersion,
    ExtraFile,
    HistoryEntry,
    RecycleEntry,
    ReleaseTrack,
    SeasonFolder,
    Title,
    TrackFile,
    Version,
    VersionDefinition,
    utcnow,
)
from . import companions, folders
from .downloads import files

if TYPE_CHECKING:
    from .recycle_again import Fetched

logger = logging.getLogger("nexcrate.recycle_bin")

#: Columns of an episode file that are not copied into the bin: its own row number and its version.
_EPISODE_FILE_SKIP = frozenset({"id", "version_id"})
#: The same for a track file.
_TRACK_FILE_SKIP = frozenset({"id", "version_id"})
#: The media servers' word for a kind.
_SERVER_KIND = {"movie": "movie", "series": "series", "album": "music"}
#: Columns of a movie version that describe its file, cleared on delete and written back on restore.
MOVIE_FILE_COLUMNS = (
    "file_ref",
    "quality",
    "cutoff_not_met",
    "size",
    "languages",
    "release_group",
    "relative_path",
    "release_title",
    "quality_from",
    "media_info",
    "media_read_at",
)
_DATETIME = "__datetime__"


@dataclass(frozen=True)
class Actor:
    """Who deletes or restores: the owner, or a program by the name of its key."""

    kind: str = "owner"
    name: str | None = None

    @classmethod
    def key(cls, name: str) -> Actor:
        return cls("key", name)


OWNER = Actor()
#: The import replaced the file with a new one (``deleted_by`` replaced). Before 26.09.2026 such a file went into the
#: recycle folder without an entry: invisible here, and back only by hand.
REPLACED = Actor("replaced")


@dataclass(frozen=True)
class Scope:
    """What of a title to delete. ``definition_ids`` None means every version of nexcrate's own.

    For a series: ``seasons`` None and no ``episodes`` means every file; otherwise the files of these seasons and of
    these ``(season, episode)`` pairs, both in TMDB's numbers. ``episode_file_ids`` names single files (the interface).
    For an album: ``track_ids`` names tracks (``release_tracks.id``), ``track_file_ids`` single files; neither is the
    whole album.
    """

    definition_ids: tuple[int, ...] | None = None
    seasons: tuple[int, ...] | None = None
    episodes: tuple[tuple[int, int], ...] = ()
    episode_file_ids: tuple[int, ...] = ()
    track_ids: tuple[int, ...] = ()
    track_file_ids: tuple[int, ...] = ()
    #: With a whole scope: what is left in each version's own folder goes into the bin too, as Radarr and Sonarr remove
    #: a title's folder (Issue #10). Removing a title and taking it back set it; deleting files alone does not.
    with_folders: bool = False

    @property
    def whole(self) -> bool:
        return (
            self.seasons is None
            and not self.episodes
            and not self.episode_file_ids
            and not self.track_ids
            and not self.track_file_ids
        )


@dataclass
class VersionResult:
    definition_id: int
    files: int = 0
    size: int = 0
    #: Files the version knew that were no longer on disk: their record is cleared, nothing moved.
    missing: int = 0
    #: A series: the episodes of the files that went, in TMDB's numbers, for the history.
    episodes: list[dict[str, int]] = field(default_factory=list)
    #: An album: the tracks of the files that went, as ``mbid:<track>``, for the history.
    tracks: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Drop:
    """A season or album folder whose version kept no file in it: its ``release.nex`` goes after the commit, when it is
    still the one nexcrate wrote and names nothing else (Issue #10)."""

    kind: str
    folder: Path
    label: str
    sha256: str


@dataclass
class Result:
    versions: list[VersionResult] = field(default_factory=list)
    #: The folders that lost a file, for the media servers after the commit: ``(kind, folder)``.
    folders: list[tuple[str, str]] = field(default_factory=list)
    #: The root folder of each folder in ``folders``: tidying climbs no higher (Issue #10).
    roots: dict[str, str] = field(default_factory=dict)
    #: The ``release.nex`` of season and album folders left without a file of their version (Issue #10).
    drops: list[Drop] = field(default_factory=list)
    moves: list[Move] = field(default_factory=list)
    #: The ``release.nex`` entries of movie files that went, for ``companions.remove`` after the commit: the entry
    #: named a file in the bin.
    companions: list[companions.Removal] = field(default_factory=list)

    @property
    def files(self) -> int:
        return sum(item.files for item in self.versions)

    @property
    def changed(self) -> bool:
        return any(item.files or item.missing for item in self.versions)


@dataclass
class Move:
    """One file moved: from ``before`` to ``after``. Undoing moves it back."""

    before: Path
    after: Path


# --- Facts, copied into the bin and back ----------------------------------------------------------------------- #


def _dump(value: Any) -> Any:
    if isinstance(value, datetime):
        return {_DATETIME: value.isoformat()}
    return value


def _load(value: Any) -> Any:
    if isinstance(value, dict) and set(value) == {_DATETIME}:
        return datetime.fromisoformat(value[_DATETIME])
    return value


def _track_file_facts(row: TrackFile) -> dict[str, Any]:
    return {
        column.key: _dump(getattr(row, column.key))
        for column in TrackFile.__table__.columns
        if column.key not in _TRACK_FILE_SKIP
    }


def _episode_file_facts(row: EpisodeFile) -> dict[str, Any]:
    return {
        column.key: _dump(getattr(row, column.key))
        for column in EpisodeFile.__table__.columns
        if column.key not in _EPISODE_FILE_SKIP
    }


# --- Paths ------------------------------------------------------------------------------------------------------ #


def _root(raw: str | None) -> Path | None:
    try:
        return folders.visible(raw)[0]
    except folders.NotVisible:
        return None


def below(root: Path, relative: str) -> Path | None:
    """``relative`` below ``root``, or None when it is empty, climbs out, or leads out of ``root`` through a link.

    The path need not exist: its nearest existing folder is what has to lie in ``root``.
    """
    parts = [part for part in relative.replace("\\", "/").split("/") if part]
    if not parts or any(part in (".", "..") for part in parts):
        return None
    path = root.joinpath(*parts)
    if os.path.lexists(path) and files.is_link(path):
        return None
    existing = path.parent
    while not existing.exists() and existing != root and root in existing.parents:
        existing = existing.parent
    return path if files.inside(existing, root) else None


def _in_bin(root: Path, path: Path) -> bool:
    return files.strictly_inside(path, root / files.RECYCLE_FOLDER)


def _move_into_bin(path: Path, root: Path, moment: datetime, moves: list[Move]) -> Path:
    target = files.recycle(path, root, moment)
    moves.append(Move(path, target))
    return target


def _move(source: Path, target: Path, moves: list[Move]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    os.rename(source, target)
    moves.append(Move(source, target))


def undo(moves: list[Move]) -> None:
    """Move every file back, the last first. A file that cannot go back stays where it is, with a warning."""
    for move in reversed(moves):
        try:
            move.before.parent.mkdir(parents=True, exist_ok=True)
            os.rename(move.after, move.before)
        except OSError:
            logger.warning("A file could not be moved back after a failed step; it stays where it went")


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def file_name(relative: str) -> str:
    """The file's own name, never its folders: the bin shows which file, not where the library lies."""
    return relative.replace("\\", "/").rsplit("/", 1)[-1]


# --- Checks --------------------------------------------------------------------------------------------------- #


def _no_import_running(db: OrmSession, title_id: int, definition_ids: Iterable[int]) -> None:
    importing = db.scalar(
        select(Download.id)
        .where(
            Download.title_id == title_id,
            Download.version_definition_id.in_(list(definition_ids)),
            Download.state == "importing",
        )
        .limit(1)
    )
    if importing is not None:
        raise error(
            "download_importing",
            "A download of this version is being filed away right now; delete its files once it is done.",
            409,
        )


def fed_by_source() -> Exception:
    return error(
        "version_fed_by_source",
        "A connection to Radarr, Sonarr or Lidarr feeds this version; change it there.",
        409,
    )


def own_versions(db: OrmSession, title: Title, definition_ids: tuple[int, ...] | None) -> list[Version]:
    """The versions a scope names, in their order. A version a source feeds is refused, one the title lacks is 404.
    Without ``definition_ids`` every version no source feeds."""
    versions = list(
        db.scalars(select(Version).where(Version.title_id == title.id).order_by(Version.version_definition_id))
    )
    if definition_ids is None:
        return [version for version in versions if version.source_id is None]
    by_definition = {version.version_definition_id: version for version in versions}
    chosen = []
    for definition_id in dict.fromkeys(definition_ids):
        version = by_definition.get(definition_id)
        if version is None:
            raise error("version_not_found", "The title has no such version.", 404, version_id=definition_id)
        if version.source_id is not None:
            raise fed_by_source()
        chosen.append(version)
    return chosen


# --- Deleting ------------------------------------------------------------------------------------------------- #


def history(
    version: Version,
    label: str,
    event: str,
    count: int,
    actor: Actor,
    moment: datetime,
    size_bytes: int | None = None,
    episodes: list[dict[str, int]] | None = None,
    tracks: list[str] | None = None,
) -> HistoryEntry:
    """A history line: ``detail`` is the count, and after a space the key's name when a program did it. ``data`` holds
    the same for ``/api/v1``, with the size and a series' episodes when known."""
    detail = f"{count} {actor.name}" if actor.name else str(count)
    by = actor.name if actor.kind == "key" else "owner"
    data: dict[str, Any] = {"count": count, "by": by, "size_bytes": size_bytes}
    if episodes is not None:
        data["episodes"] = episodes
    if tracks is not None:
        data["tracks"] = tracks
    return HistoryEntry(
        title_id=version.title_id,
        version_id=version.id,
        version_definition_id=version.version_definition_id,
        version_label=label[:64],
        event=event,
        at=moment,
        detail=detail[:1024],
        data=data,
    )


def _entry(title: Title, version: Version, label: str, actor: Actor, moment: datetime, *,
           file_facts: dict[str, Any], kept: dict[str, Any] | None = None, **values: Any) -> RecycleEntry:  # fmt: skip
    """The row of one file. ``file_facts`` carries, besides the file's columns, what a restore needs to add title and
    version again when they left the library (``recycle_again``)."""
    from . import recycle_again

    return RecycleEntry(
        file_facts={**file_facts, recycle_again.LIBRARY: recycle_again.facts(title, version, **(kept or {}))},
        deleted_at=moment,
        deleted_by=actor.kind,
        deleted_by_name=actor.name,
        title_id=title.id,
        kind=title.kind,
        tmdb_id=title.tmdb_id,
        title_name=title.title or "",
        year=title.year,
        version_definition_id=version.version_definition_id,
        version_label=label[:200],
        root_folder=version.root_folder or "",
        **values,
    )


def _subtitle(row: ExtraFile, base: Path, root: Path, moment: datetime, moves: list[Move]) -> dict[str, Any] | None:
    """Move one subtitle into the bin; ``base`` is the folder its ``relative_path`` counts from."""
    path = below(base, row.relative_path)
    if path is None or not path.is_file():
        return None
    moved = _move_into_bin(path, root, moment, moves)
    return {
        "row_path": row.relative_path,
        "path": _relative(path, root),
        "bin": _relative(moved, root),
        "kind": row.kind,
        "language": row.language,
        "forced": bool(row.forced),
        "sdh": bool(row.sdh),
    }


def replaced_movie(
    db: OrmSession,
    title: Title,
    version: Version,
    label: str,
    moment: datetime,
    *,
    relative_path: str,
    bin_path: str,
    size: int,
    subtitles: Iterable[tuple[int, str, str]] = (),
) -> RecycleEntry:
    """The entry of a movie file the import replaced and already moved into the bin, with the version's facts about it
    as they stand before the new file is recorded. ``subtitles`` are the old file's subtitles that went with it, as
    ``(row id, path before, path in the bin)`` below the version's root folder."""
    facts = {name: _dump(getattr(version, name)) for name in MOVIE_FILE_COLUMNS}
    rows = {row.id: row for row in db.scalars(select(ExtraFile).where(ExtraFile.version_id == version.id))}
    extras = [
        {
            "row_path": row.relative_path,
            "path": before,
            "bin": binned,
            "kind": row.kind,
            "language": row.language,
            "forced": bool(row.forced),
            "sdh": bool(row.sdh),
        }
        for row_id, before, binned in subtitles
        if (row := rows.get(row_id)) is not None
    ]
    entry = _entry(title, version, label, REPLACED, moment, relative_path=relative_path, bin_path=bin_path,
                   size=size, extras=extras or None, file_facts=facts)  # fmt: skip
    db.add(entry)
    return entry


def clear_movie_file(version: Version, moment: datetime) -> None:
    version.has_file = False
    for name in MOVIE_FILE_COLUMNS:
        setattr(version, name, None)
    version.size = 0
    version.languages = []
    version.cutoff_not_met = False
    version.state = "wanted" if version.monitored else "unmonitored"
    version.progress = None
    version.problem_code = None
    version.updated_at = moment


def _delete_movie(db: OrmSession, title: Title, version: Version, label: str, actor: Actor, moment: datetime,
                  result: Result) -> VersionResult:  # fmt: skip
    done = VersionResult(version.version_definition_id)
    if not version.has_file or not version.relative_path:
        return done
    root = _root(version.root_folder)
    path = below(root, version.relative_path) if root is not None else None
    facts = {name: _dump(getattr(version, name)) for name in MOVIE_FILE_COLUMNS}
    subtitles = list(
        db.scalars(select(ExtraFile).where(ExtraFile.version_id == version.id, ExtraFile.episode_file_id.is_(None)))
    )
    if companions.enabled(db):
        result.companions.extend(companions.plan_removal(db, [version]))
        companions.clear_state(version)
    if root is None or path is None or not path.is_file():
        # The file is gone already: the record goes, nothing moves.
        done.missing = 1
    else:
        size = path.stat().st_size
        target = _move_into_bin(path, root, moment, result.moves)
        extras = [item for row in subtitles if (item := _subtitle(row, root, root, moment, result.moves)) is not None]
        db.add(
            _entry(title, version, label, actor, moment, relative_path=_relative(path, root),
                   bin_path=_relative(target, root), size=size, extras=extras or None, file_facts=facts)
        )  # fmt: skip
        result.folders.append(("movie", str(path.parent)))
        result.roots[str(path.parent)] = str(root)
        done.files, done.size = 1, size
    for row in subtitles:
        db.delete(row)
    clear_movie_file(version, moment)
    return done


def _series_folder(db: OrmSession, version: Version) -> tuple[Path, Path] | None:
    """The version's root folder as seen and its series folder, or None when either is not there."""
    from .series import folder_read

    root = _root(version.root_folder)
    # The one place a series folder is found, private to its module.
    folder, _settled = folder_read._series_folder(db, version)
    if root is None or folder is None or not files.strictly_inside(folder, root):
        return None
    return root, files.resolved(folder) or folder


def episode_files(db: OrmSession, version: Version, scope: Scope) -> list[tuple[EpisodeFile, list[Episode]]]:
    """The version's episode files a scope names, each with the episodes it is linked to.

    The second half of a double episode is not linked through ``episode_versions`` but names
    its episode itself; it goes and comes with that episode like the first half.
    """
    rows = (
        db.execute(
            select(EpisodeFile, Episode)
            .join(EpisodeVersion, EpisodeVersion.episode_file_id == EpisodeFile.id)
            .join(Episode, Episode.id == EpisodeVersion.episode_id)
            .where(EpisodeFile.version_id == version.id, EpisodeVersion.version_id == version.id)
            .order_by(EpisodeFile.id, Episode.season_number, Episode.episode_number)
        )
        .tuples()
        .all()
    )
    rows += (
        db.execute(
            select(EpisodeFile, Episode)
            .join(Episode, Episode.id == EpisodeFile.part_of_episode_id)
            .where(EpisodeFile.version_id == version.id, EpisodeFile.part == 2)
            .order_by(EpisodeFile.id)
        )
        .tuples()
        .all()
    )
    chosen: dict[int, tuple[EpisodeFile, list[Episode]]] = {}
    for row, episode in rows:
        chosen.setdefault(row.id, (row, []))[1].append(episode)
    if scope.episode_file_ids:
        # A file without an episode, unclear or left out, goes by its id alone (the owner's answer of 25.09.2026).
        for row in db.scalars(
            select(EpisodeFile).where(
                EpisodeFile.version_id == version.id, EpisodeFile.id.in_(scope.episode_file_ids)
            )
        ):
            chosen.setdefault(row.id, (row, []))
    if scope.whole:
        # Every file means every file: those without an episode too, unclear or left out (25.09.2026).
        for row in db.scalars(
            select(EpisodeFile).where(EpisodeFile.version_id == version.id, EpisodeFile.id.not_in(list(chosen)))
        ):
            chosen[row.id] = (row, [])
        return list(chosen.values())
    seasons, pairs, ids = set(scope.seasons or ()), set(scope.episodes), set(scope.episode_file_ids)
    return [
        (row, episodes)
        for row, episodes in chosen.values()
        if row.id in ids
        or any(
            episode.season_number in seasons or (episode.season_number, episode.episode_number) in pairs
            for episode in episodes
        )
    ]


def _delete_series(db: OrmSession, title: Title, version: Version, label: str, scope: Scope, actor: Actor,
                   moment: datetime, result: Result) -> VersionResult:  # fmt: skip
    from .series import watching

    done = VersionResult(version.version_definition_id)
    chosen = episode_files(db, version, scope)
    if not chosen:
        return done
    located = _series_folder(db, version)
    for row, episodes in chosen:
        facts = _episode_file_facts(row)
        facts["episode_ids"] = [episode.id for episode in episodes]
        subtitles = list(db.scalars(select(ExtraFile).where(ExtraFile.episode_file_id == row.id)))
        path = below(located[1], row.relative_path) if located is not None else None
        for link in db.scalars(
            select(EpisodeVersion).where(
                EpisodeVersion.version_id == version.id, EpisodeVersion.episode_file_id == row.id
            )
        ):
            link.episode_file_id = None
        if located is None or path is None or not path.is_file():
            done.missing += 1
        else:
            root, folder = located
            size = path.stat().st_size
            target = _move_into_bin(path, root, moment, result.moves)
            extras = [
                item for subtitle in subtitles if (item := _subtitle(subtitle, folder, root, moment, result.moves))
            ]
            db.add(
                _entry(title, version, label, actor, moment, relative_path=_relative(path, root),
                       bin_path=_relative(target, root), size=size, extras=extras or None, file_facts=facts,
                       season=episodes[0].season_number if episodes else None,
                       episodes=[episode.episode_number for episode in episodes])
            )  # fmt: skip
            result.folders.append(("series", str(path.parent)))
            result.roots[str(path.parent)] = str(root)
            done.files += 1
            done.size += size
            done.episodes.extend(
                {"season": episode.season_number, "episode": episode.episode_number} for episode in episodes
            )
        for subtitle in subtitles:
            db.delete(subtitle)
        db.delete(row)
    db.flush()
    watching.recount(db, version, watching.today())
    version.updated_at = moment
    return done


def track_files(db: OrmSession, version: Version, scope: Scope) -> list[TrackFile]:
    """The album version's files a scope names: every file, or those of the tracks and files it names. A file that
    covers several tracks goes when one of them is named."""
    rows = list(db.scalars(select(TrackFile).where(TrackFile.version_id == version.id).order_by(TrackFile.id)))
    if scope.whole:
        return rows
    tracks, ids = set(scope.track_ids), set(scope.track_file_ids)
    return [
        row
        for row in rows
        if row.id in ids or row.track_id in tracks or bool(set(row.track_ids or []) & tracks)
    ]


def recount_album(db: OrmSession, version: Version) -> None:
    """Tracks, size, step and state of an album version after its files changed."""
    from .music import album_quality
    from .music import store as music_store

    rows = list(db.scalars(select(TrackFile).where(TrackFile.version_id == version.id)))
    music_store.count_tracks(db, version)
    version.size = sum(row.size or 0 for row in rows)
    album_quality.apply(
        version, album_quality.step_of_files([row.quality for row in rows]), album_quality.rules_of(db)
    )
    if not rows:
        version.quality, version.cutoff_not_met = None, False
        version.state = music_store.state_of(version)


def _delete_album(db: OrmSession, title: Title, version: Version, label: str, scope: Scope, actor: Actor,
                  moment: datetime, result: Result) -> VersionResult:  # fmt: skip
    from .music import album_read

    done = VersionResult(version.version_definition_id)
    chosen = track_files(db, version, scope)
    if not chosen:
        return done
    root = _root(version.root_folder)
    folder = album_read.album_folder(version)
    named = {row.track_id for row in chosen if row.track_id is not None}
    named |= {value for row in chosen for value in row.track_ids or [] if isinstance(value, int)}
    track_refs = {
        track_id: mbid
        for track_id, mbid in db.execute(
            select(ReleaseTrack.id, ReleaseTrack.mbid).where(ReleaseTrack.id.in_(named))
        ).tuples()
    }
    for row in chosen:
        path = below(folder, row.relative_path) if folder is not None else None
        if root is None or path is None or not path.is_file() or not files.strictly_inside(path, root):
            done.missing += 1
        else:
            size = path.stat().st_size
            target = _move_into_bin(path, root, moment, result.moves)
            db.add(
                _entry(title, version, label, actor, moment, relative_path=_relative(path, root),
                       bin_path=_relative(target, root), size=size, file_facts=_track_file_facts(row),
                       kept={"track": track_refs.get(row.track_id or 0),
                             "tracks": [track_refs[value] for value in row.track_ids or [] if value in track_refs]})
            )  # fmt: skip
            result.folders.append(("music", str(path.parent)))
            result.roots[str(path.parent)] = str(root)
            done.files += 1
            done.size += size
            if mbid := track_refs.get(row.track_id or 0):
                done.tracks.append(f"mbid:{mbid}")
        db.delete(row)
    db.flush()
    recount_album(db, version)
    version.updated_at = moment
    return done


def delete_in(db: OrmSession, title: Title, scope: Scope, actor: Actor, moment: datetime) -> Result:
    """Delete the files a scope names into the bin and stage the rows; the caller commits, or calls ``undo`` with
    ``result.moves`` when anything after fails. A series scope on a movie and a version a source feeds are refused."""
    if title.kind not in ("movie", "series", "album"):
        raise error(
            "kind_unsupported", f"This nexcrate does not answer for the kind {title.kind}.", 422, kind=title.kind
        )
    if title.kind == "movie" and not scope.whole:
        raise error("scope_not_for_kind", "A movie has no seasons or episodes.", 422, kind="movie")
    if title.kind == "album" and (scope.seasons is not None or scope.episodes or scope.episode_file_ids):
        raise error("scope_not_for_kind", "An album has no seasons or episodes.", 422, kind="album")
    if title.kind == "series" and (scope.track_ids or scope.track_file_ids):
        raise error("scope_not_for_kind", "A series has no tracks.", 422, kind="series")
    versions = own_versions(db, title, scope.definition_ids)
    _no_import_running(db, title.id, [version.version_definition_id for version in versions])
    labels = {
        row.id: row.label
        for row in db.scalars(
            select(VersionDefinition).where(VersionDefinition.id.in_([v.version_definition_id for v in versions]))
        )
    }
    result = Result()
    # Before the files go: a movie version forgets its path with its file.
    own_folders = _own_folders(db, title, versions) if scope.with_folders and scope.whole else []
    try:
        for version in versions:
            label = labels.get(version.version_definition_id, "")
            before = len(result.folders)
            if title.kind == "movie":
                done = _delete_movie(db, title, version, label, actor, moment, result)
            elif title.kind == "album":
                done = _delete_album(db, title, version, label, scope, actor, moment, result)
            else:
                done = _delete_series(db, title, version, label, scope, actor, moment, result)
            result.versions.append(done)
            if done.files:
                _plan_drops(db, title, version, label, result, result.folders[before:])
            if done.files or done.missing:
                episodes = done.episodes if title.kind == "series" else None
                tracks = done.tracks if title.kind == "album" else None
                db.add(
                    history(version, label, "files_deleted", done.files, actor, moment, done.size, episodes, tracks)
                )
        for kind, root, folder in own_folders:
            _recycle_rest(kind, root, folder, moment, result)
        db.flush()
    except BaseException:
        undo(result.moves)
        raise
    return result


def _own_folders(db: OrmSession, title: Title, versions: list[Version]) -> list[tuple[str, Path, Path]]:
    """The folder of each version that is the version's alone: ``(kind, root, folder)``.

    A movie's folder is the one its file lies in, a series' the series folder, an album's the album folder. Never a
    root folder or a link, and never a folder that holds, or is, what nexcrate counts as another version's or a library
    folder: that version would lose its file without knowing it.
    """
    from .music import album_read
    from .music import paths as music_paths

    removing = {version.id for version in versions}
    found: list[tuple[str, Path, Path]] = []
    for version in versions:
        root = _root(version.root_folder)
        folder: Path | None = None
        if root is None:
            continue
        if title.kind == "movie" and version.relative_path and "/" in version.relative_path.replace("\\", "/"):
            path = below(root, version.relative_path)
            folder = path.parent if path is not None else None
        elif title.kind == "series":
            located = _series_folder(db, version)
            folder = located[1] if located is not None else None
        elif title.kind == "album" and not music_paths.sharing(db, version):
            folder = album_read.album_folder(version)
        if folder is None or files.is_link(folder) or not folder.is_dir() or not files.strictly_inside(folder, root):
            continue
        if _holds_another(db, folder, removing):
            logger.info("Version %d: its folder holds another version's files or folder; only its own files went",
                        version.id)  # fmt: skip
            continue
        if _holds_a_link(folder):
            logger.info("Version %d: its folder holds a link; only its own files went", version.id)
            continue
        kind = {"movie": "movie", "series": "series"}.get(title.kind, "music")
        if (kind, root, folder) not in found:
            found.append((kind, root, folder))
    return found


def _holds_another(db: OrmSession, folder: Path, removing: set[int]) -> bool:
    """Whether a library folder, or a file or folder of a version not being removed, lies in ``folder``."""
    raw: list[str | None] = []
    raw.extend(db.scalars(select(VersionDefinition.folder).where(VersionDefinition.folder.is_not(None))))
    raw.extend(db.scalars(select(DiskRoot.path)))
    for root_folder, relative in db.execute(
        select(Version.root_folder, Version.relative_path).where(
            Version.id.not_in(removing), Version.root_folder.is_not(None), Version.relative_path.is_not(None)
        )
    ).tuples():
        raw.append(str(Path(root_folder, relative)))
    for value in raw:
        if not value:
            continue
        seen = folders.visible_path(value)
        if seen is not None and files.inside(seen, folder):
            return True
    return False


def _holds_a_link(folder: Path) -> bool:
    """A link anywhere in the folder: moving it would follow it, removing it would take what it leads to."""
    for current, names, file_names in os.walk(folder):
        here = Path(current)
        if any(files.is_link(here / name) for name in [*names, *file_names]):
            return True
    return False


def _recycle_rest(kind: str, root: Path, folder: Path, moment: datetime, result: Result) -> None:
    """Everything left in a version's own folder into the bin beside its files, one file at a time. A file that will
    not move stays, and so does its folder; the title's own files went all the same. The empty folders go
    afterwards with ``tidy``."""
    moved = kept = 0
    for current, _names, file_names in os.walk(folder):
        here = Path(current)
        for name in file_names:
            try:
                _move_into_bin(here / name, root, moment, result.moves)
                moved += 1
            except (OSError, files.FileProblem):
                kept += 1
    for current, _names, _files in os.walk(folder, topdown=False):
        if Path(current) != folder:
            try:
                os.rmdir(current)
            except OSError:
                pass
    if moved or kept:
        logger.info("%d further files of a version's own folder went into the recycle bin, %d stayed", moved, kept)
    if (kind, str(folder)) not in result.folders:
        result.folders.append((kind, str(folder)))
    result.roots[str(folder)] = str(root)


def _plan_drops(db: OrmSession, title: Title, version: Version, label: str, result: Result,
                lost_in: list[tuple[str, str]]) -> None:  # fmt: skip
    """Season and album folders this version lost files in and keeps none in: their ``release.nex`` may go. Only with
    the hash nexcrate stored when it wrote the file, so a file changed since stays."""
    db.flush()
    lost = {Path(folder) for kind, folder in lost_in if kind in ("series", "music")}
    if title.kind == "album":
        left = db.scalar(select(TrackFile.id).where(TrackFile.version_id == version.id).limit(1))
        if left is None and version.companion_sha256:
            for folder in lost:
                result.drops.append(Drop("album", folder, label, version.companion_sha256))
        return
    if title.kind != "series":
        return
    # The season folders the version still has a file in.
    names = set()
    for relative in db.scalars(select(EpisodeFile.relative_path).where(EpisodeFile.version_id == version.id)):
        parts = relative.replace("\\", "/").split("/")
        if len(parts) > 1:
            names.add(parts[0])
    for season in db.scalars(select(SeasonFolder).where(SeasonFolder.version_id == version.id)):
        if not season.companion_hash or season.name in names:
            continue
        for folder in lost:
            drop = Drop("series", folder, label, season.companion_hash)
            if folder.name == season.name and drop not in result.drops:
                result.drops.append(drop)


def drop_companions(drops: Iterable[Drop]) -> int:
    """After the commit: each planned ``release.nex`` goes when it is nexcrate's, unchanged, and names only this
    version. Returns how many went; never raises."""
    from . import companions_album, companions_series

    gone = 0
    planned = list(drops)
    if not planned:
        return 0
    try:
        installation = companions.installation_id()
        for drop in planned:
            key = files.resolved(drop.folder) or drop.folder
            with companions._lock_for(key):
                reader = companions_series.read if drop.kind == "series" else companions_album.read
                found = reader(drop.folder, installation)
                if found["outcome"] != companions.OURS or found.get("sha256") != drop.sha256:
                    continue
                if drop.kind == "series" and any(entry.get("version") != drop.label for entry in found["entries"]):
                    continue
                if companions.remove_file(drop.folder):
                    gone += 1
    except Exception:  # the files went already; a release.nex left behind is no reason to fail
        logger.exception("A release.nex of a folder left without files could not be removed")
    if gone:
        logger.info("%d release.nex files of folders left without files went", gone)
    return gone


def settle(result: Result, removals: Iterable[companions.Removal] = ()) -> None:
    """After the commit, in this order: the ``release.nex`` entries of what went, folders left empty, the media
    servers."""
    planned = [*result.companions, *removals]
    if planned:
        companions.remove(planned)
    drop_companions(result.drops)
    tell_media_servers(tidy(result.folders, result.roots))


def tidy(changed: Iterable[tuple[str, str]], roots: dict[str, str]) -> list[tuple[str, str]]:
    """After the commit, and after ``release.nex`` went: the folders the bin left empty go, upward to just below their
    root folder, as Radarr removes a movie's folder with its file (Issue #10, 06.10.2026).

    Only ``os.rmdir``: whatever is left in a folder keeps it (artwork, a ``.nfo``, a file nexcrate does not know), a
    link is never followed, a root folder and anything outside it never go. A file brought back from the bin makes its
    folders again. Returns ``changed`` for the media servers, a removed folder replaced by the nearest one left.
    """
    told: list[tuple[str, str]] = []
    removed = 0
    for kind, raw in changed:
        folder, root = Path(raw), Path(roots.get(raw, raw))
        # A folder that went already: the nearest one left counts.
        while not os.path.lexists(folder) and folder != root and root in folder.parents:
            folder = folder.parent
        while files.strictly_inside(folder, root) and not files.is_link(folder):
            try:
                os.rmdir(folder)
            except OSError:
                break
            removed += 1
            folder = folder.parent
        if (kind, str(folder)) not in told:
            told.append((kind, str(folder)))
    if removed:
        logger.info("%d folders left empty by the recycle bin went", removed)
    return told


def tell_media_servers(changed: Iterable[tuple[str, str]]) -> None:
    """After the commit: the media servers look at the folders that lost or got back a file."""
    from .mediaservers import notify

    by_kind: dict[str, list[str]] = {}
    for kind, folder in changed:
        by_kind.setdefault(kind, []).append(folder)
    for kind, folders_of_kind in by_kind.items():
        notify.request(_SERVER_KIND.get(kind, kind), folders_of_kind)


def delete(title_id: int, scope: Scope, actor: Actor = OWNER) -> Result:
    """Delete into the bin in one transaction, and plan the title's search again."""
    from .automatic import clock, planning

    with SessionLocal() as db:
        title = db.get(Title, title_id)
        if title is None:
            raise error("not_found", "This does not exist, or not any more.", 404)
        moment = utcnow()
        result = delete_in(db, title, scope, actor, moment)
        try:
            if result.changed:
                title.updated_at = moment
                planning.replan(db, [title.id], clock.now())
            db.commit()
        except BaseException:
            db.rollback()
            undo(result.moves)
            raise
    settle(result)
    logger.info("Title %d: %d files went into the recycle bin (%s)", title_id, result.files, actor.kind)
    return result


# --- Reading, restoring, purging ----------------------------------------------------------------------------------- #


def bin_file(entry: RecycleEntry) -> tuple[Path, Path] | None:
    """The version root as seen and the file in the bin, or None when the root is not there or the path is not in it."""
    root = _root(entry.root_folder)
    if root is None:
        return None
    path = below(root, entry.bin_path)
    if path is None or not path.is_file() or not _in_bin(root, path):
        return None
    return root, path


def _episode_slot_taken(db: OrmSession, version: Version, episode_ids: list[int], second: bool) -> bool:
    """Whether an episode of the file has another file by now. A second half needs its episode, and no second half
    there by now; the first half may well be there."""
    links = [db.get(EpisodeVersion, (episode_id, version.id)) for episode_id in episode_ids]
    if second:
        taken = db.scalar(
            select(EpisodeFile.id).where(
                EpisodeFile.version_id == version.id,
                EpisodeFile.part == 2,
                EpisodeFile.part_of_episode_id.in_(episode_ids),
            )
        )
        return not links or any(link is None for link in links) or taken is not None
    # Without episodes the file comes back as it went: unclear or left out.
    return any(link is None or link.episode_file_id is not None for link in links)


def _track_slot_taken(db: OrmSession, version: Version, track_id: int | None) -> bool:
    return track_id is not None and (
        db.scalar(
            select(TrackFile.id).where(TrackFile.version_id == version.id, TrackFile.track_id == track_id).limit(1)
        )
        is not None
    )


#: Why a restore would refuse a file for its place: something lies where it was, the version has another file there
#: by now, or a Radarr, Sonarr or Lidarr connection feeds the version.
PLACES_TAKEN = ("path", "version", "source")


def place_taken(db: OrmSession, entry: RecycleEntry, title: Title | None, version: Version | None) -> str | None:
    """Why a restore would refuse the file for its place (one of ``PLACES_TAKEN``), or None when it is free. Reads
    only; ``restore`` checks the same inside its transaction."""
    from . import recycle_again

    root = _root(entry.root_folder)
    if root is not None:
        target = below(root, entry.relative_path)
        if target is None or os.path.lexists(target):
            return "path"
    if title is None or version is None:
        # The restore adds them again: nothing can be in the way.
        return None
    if version.source_id is not None:
        return "source"
    if entry.kind == "movie":
        return "version" if version.has_file else None
    facts = {name: _load(value) for name, value in (entry.file_facts or {}).items()}
    if entry.kind == "album":
        track_id, _tracks = recycle_again.track_ids(db, entry, title, facts.get("track_id"), facts.get("track_ids"))
        same_path = select(TrackFile.id).where(
            TrackFile.version_id == version.id, TrackFile.relative_path == facts.get("relative_path")
        )
        taken = _track_slot_taken(db, version, track_id) or db.scalar(same_path.limit(1)) is not None
        return "version" if taken else None
    stored = [int(value) for value in facts.get("episode_ids", [])]
    episode_ids, found_again = recycle_again.episode_ids(db, entry, title, stored)
    second = facts.get("part") == 2 and (bool(episode_ids) or not found_again)
    same_path = select(EpisodeFile.id).where(
        EpisodeFile.version_id == version.id, EpisodeFile.relative_path == facts.get("relative_path")
    )
    taken = _episode_slot_taken(db, version, episode_ids, second) or db.scalar(same_path.limit(1)) is not None
    return "version" if taken else None


def listed(db: OrmSession, kind: str | None = None) -> list[dict[str, Any]]:
    """The bin, newest first. ``present`` says whether the file is still there (a share not mounted says no);
    ``can_add`` whether a restore could add title and version again when they left the library; ``place_taken``
    why a restore would refuse it for where it goes, or None."""
    from . import recycle_again

    query = select(RecycleEntry).order_by(RecycleEntry.deleted_at.desc(), RecycleEntry.id.desc())
    if kind is not None:
        query = query.where(RecycleEntry.kind == kind)
    definitions = {row.id: row for row in db.scalars(select(VersionDefinition))}
    out = []
    for entry in db.scalars(query):
        definition = definitions.get(entry.version_definition_id) if entry.version_definition_id else None
        # The title the file goes back into: its own, or one of the same reference added again since.
        title = recycle_again.find_title(db, entry)
        version = recycle_again.find_version(db, entry, title) if title is not None else None
        out.append(
            {
                "id": entry.id,
                "deleted_at": entry.deleted_at,
                "deleted_by": entry.deleted_by,
                "deleted_by_name": entry.deleted_by_name,
                "title_id": title.id if title is not None else None,
                "kind": entry.kind,
                "tmdb_id": entry.tmdb_id,
                "title": entry.title_name,
                "year": entry.year,
                "version_definition_id": entry.version_definition_id,
                "version_public_id": definition.public_id if definition is not None else None,
                "version_label": definition.label if definition is not None else entry.version_label,
                "season": entry.season,
                "episodes": list(entry.episodes or []),
                "track_id": (entry.file_facts or {}).get("track_id") if entry.kind == "album" else None,
                "file_name": file_name(entry.relative_path),
                "size": entry.size,
                "present": bin_file(entry) is not None,
                "can_add": recycle_again.can_add(db, entry, title is not None),
                "place_taken": place_taken(db, entry, title, version),
                "album_mbid": recycle_again.ref_of(entry) if entry.kind == "album" else None,
                "track_mbid": recycle_again.kept(entry).get("track") if entry.kind == "album" else None,
            }
        )
    return out


def _restore_subtitles(db: OrmSession, entry: RecycleEntry, root: Path, version: Version, episode_file_id: int | None,
                       moves: list[Move]) -> None:  # fmt: skip
    """The subtitles that went with the file come back where they were. One that cannot stays in the bin."""
    for item in entry.extras or []:
        if not isinstance(item, dict):
            continue
        source = below(root, str(item.get("bin") or ""))
        target = below(root, str(item.get("path") or ""))
        if source is None or target is None or not source.is_file() or not _in_bin(root, source):
            continue
        if os.path.lexists(target):
            continue
        _move(source, target, moves)
        db.add(
            ExtraFile(
                version_id=version.id,
                relative_path=str(item.get("row_path") or ""),
                kind=str(item.get("kind") or "subtitle"),
                language=item.get("language") or None,
                forced=bool(item.get("forced")),
                sdh=bool(item.get("sdh")),
                episode_file_id=episode_file_id,
            )
        )


def target_free(root: Path, relative: str) -> Path:
    target = below(root, relative)
    if target is None or os.path.lexists(target):
        raise error("recycle_target_taken", "Something lies where the file was.", 409)
    return target


def file_gone() -> Exception:
    return error("recycle_file_gone", "The file is no longer in the recycle bin.", 409)


def title_gone() -> Exception:
    return error("recycle_title_gone", "The title left the library and cannot be added again.", 409)


def _restore_movie(db: OrmSession, entry: RecycleEntry, version: Version, root: Path, source: Path, moves: list[Move],
                   moment: datetime) -> Path:  # fmt: skip
    if version.has_file:
        raise error("recycle_slot_taken", "The version has another file by now.", 409)
    target = target_free(root, entry.relative_path)
    _move(source, target, moves)
    for name, value in (entry.file_facts or {}).items():
        if name in MOVIE_FILE_COLUMNS:
            setattr(version, name, _load(value))
    version.relative_path = entry.relative_path
    version.has_file = True
    version.state = "upgrade" if version.cutoff_not_met else "available"
    version.updated_at = moment
    _restore_subtitles(db, entry, root, version, None, moves)
    return target


def _restore_episode(db: OrmSession, entry: RecycleEntry, title: Title, version: Version, root: Path, source: Path,
                     moves: list[Move], moment: datetime) -> Path:  # fmt: skip
    from . import recycle_again
    from .series import watching

    facts = {name: _load(value) for name, value in (entry.file_facts or {}).items()}
    stored = [int(value) for value in facts.pop("episode_ids", [])]
    episode_ids, found_again = recycle_again.episode_ids(db, entry, title, stored)
    if found_again and facts.get("part") == 2:
        # The title was added again: the second half names its episode's new row, or is a whole file without one.
        facts["part_of_episode_id"] = episode_ids[0] if episode_ids else None
        facts["part"] = 2 if episode_ids else None
    second = facts.get("part") == 2
    if _episode_slot_taken(db, version, episode_ids, second):
        raise error("recycle_slot_taken", "An episode of the file has another file by now.", 409)
    # A second half links to no episode of its own.
    links = [] if second else [db.get(EpisodeVersion, (episode_id, version.id)) for episode_id in episode_ids]
    columns = {column.key for column in EpisodeFile.__table__.columns} - _EPISODE_FILE_SKIP
    values = {name: value for name, value in facts.items() if name in columns}
    if db.scalar(
        select(EpisodeFile.id).where(
            EpisodeFile.version_id == version.id, EpisodeFile.relative_path == values.get("relative_path")
        )
    ):
        raise error("recycle_target_taken", "Something lies where the file was.", 409)
    target = target_free(root, entry.relative_path)
    _move(source, target, moves)
    row = EpisodeFile(version_id=version.id, **values)
    row.updated_at = moment
    db.add(row)
    db.flush()
    for link in links:
        if link is not None:
            link.episode_file_id = row.id
    _restore_subtitles(db, entry, root, version, row.id, moves)
    db.flush()
    watching.recount(db, version, watching.today())
    version.updated_at = moment
    return target


def _restore_track(db: OrmSession, entry: RecycleEntry, title: Title, version: Version, root: Path, source: Path,
                   moves: list[Move], moment: datetime) -> tuple[Path, int | None]:  # fmt: skip
    """Returns where the file went and the track it is linked to."""
    from . import recycle_again

    facts = {name: _load(value) for name, value in (entry.file_facts or {}).items()}
    columns = {column.key for column in TrackFile.__table__.columns} - _TRACK_FILE_SKIP
    values = {name: value for name, value in facts.items() if name in columns}
    # A track that left MusicBrainz's release since, or whose album was added again and has other rows: found by its
    # MusicBrainz id. Else the file comes back without one and unclear, as reading the folder leaves a file it cannot
    # place: the album page lists it under the unclear files, where the owner assigns it. Reading the folder again
    # would not, it keeps a path it knows as it is.
    values["track_id"], values["track_ids"] = recycle_again.track_ids(
        db, entry, title, values.get("track_id"), values.get("track_ids")
    )
    track_id = values["track_id"]
    if track_id is None:
        values["unclear"] = True
    if _track_slot_taken(db, version, track_id):
        raise error("recycle_slot_taken", "The track has another file by now.", 409)
    if db.scalar(
        select(TrackFile.id).where(
            TrackFile.version_id == version.id, TrackFile.relative_path == values.get("relative_path")
        )
    ):
        raise error("recycle_target_taken", "Something lies where the file was.", 409)
    target = target_free(root, entry.relative_path)
    _move(source, target, moves)
    row = TrackFile(version_id=version.id, **values)
    row.updated_at = moment
    db.add(row)
    db.flush()
    recount_album(db, version)
    version.updated_at = moment
    return target, track_id


def restore(entry_id: int, actor: Actor = OWNER, fetched: Fetched | None = None) -> dict[str, Any]:
    """Put a file back where it was and record it again. Refuses when something lies there, the slot has another file,
    the version no longer exists, or the file is gone; the file then stays in the bin.

    When the title or the version left the library they are added again first (``recycle_again``), in the same
    transaction: the title from ``fetched``, what ``bring_back`` fetched from TMDB or MusicBrainz. Without it a missing
    title is ``recycle_title_gone``. ``created`` in the answer says whether the title was added again.
    """
    from . import recycle_again
    from .automatic import clock, planning
    from .series import folder_read

    moves: list[Move] = []
    reading: list[int] = []
    with SessionLocal() as db:
        entry = db.get(RecycleEntry, entry_id)
        if entry is None:
            raise error("not_found", "This does not exist, or not any more.", 404)
        title = recycle_again.find_title(db, entry)
        version = recycle_again.find_version(db, entry, title) if title is not None else None
        if version is not None and version.source_id is not None:
            raise fed_by_source()
        definition = recycle_again.definition_of(db, entry) if version is None else None
        located = bin_file(entry)
        if located is None:
            raise file_gone()
        root, source = located
        moment = utcnow()
        created = False
        try:
            if title is None:
                if fetched is None or fetched.kind != entry.kind or fetched.ref != recycle_again.ref_of(entry):
                    raise title_gone()
                assert definition is not None
                title = recycle_again.add_title(db, entry, fetched, definition, moment)
                created = True
            if version is None:
                assert definition is not None
                version, read = recycle_again.add_version(db, entry, title, definition, moment)
                if read:
                    reading.append(version.id)
            track_id = None
            if entry.kind == "movie":
                target = _restore_movie(db, entry, version, root, source, moves, moment)
            elif entry.kind == "album":
                target, track_id = _restore_track(db, entry, title, version, root, source, moves, moment)
            else:
                target = _restore_episode(db, entry, title, version, root, source, moves, moment)
            restored = (
                [{"season": entry.season, "episode": number} for number in entry.episodes or []]
                if entry.kind == "series" and entry.season is not None
                else None
            )
            back = _track_refs(db, track_id) if entry.kind == "album" else None
            db.add(
                history(version, entry.version_label, "file_restored", 1, actor, moment, entry.size, restored, back)
            )
            db.delete(entry)
            title.updated_at = moment
            planning.replan(db, [title.id], clock.now())
            db.commit()
        except BaseException:
            db.rollback()
            undo(moves)
            raise
        answer = {
            "title_id": title.id,
            "kind": title.kind,
            "version_definition_id": version.version_definition_id,
            "created": created,
        }
        restored_version = version.id
    if reading:
        folder_read.enqueue(reading)
    if answer["kind"] == "movie":
        # release.nex names the file that came back (it named the file that went, or nothing).
        companions.write_version(restored_version)
    tell_media_servers([(answer["kind"], str(target.parent))])
    logger.info("Recycle entry %d restored for title %d (%s)", entry_id, answer["title_id"], actor.kind)
    return answer


async def bring_back(entry_id: int, actor: Actor = OWNER) -> dict[str, Any]:
    """``restore``, and when the title left the library, TMDB or MusicBrainz asked first. Everything that needs no
    network is refused before; when they cannot answer, their error stands and nothing is added."""
    from . import recycle_again

    wanted = await asyncio.to_thread(recycle_again.missing, entry_id)
    fetched = await recycle_again.fetch(wanted) if wanted is not None else None
    try:
        return await asyncio.to_thread(restore, entry_id, actor, fetched)
    except IntegrityError:
        # The same title was added at the same moment by another way: once more, the restore then finds it.
        return await asyncio.to_thread(restore, entry_id, actor, fetched)


def _track_refs(db: OrmSession, track_id: int | None) -> list[str]:
    track = db.get(ReleaseTrack, track_id) if track_id is not None else None
    return [f"mbid:{track.mbid}"] if track is not None and track.mbid else []


def _purge_files(entry: RecycleEntry) -> None:
    root = _root(entry.root_folder)
    if root is None:
        return
    bins = [entry.bin_path, *[str(item.get("bin") or "") for item in entry.extras or [] if isinstance(item, dict)]]
    for relative in bins:
        path = below(root, relative)
        if path is not None and path.is_file() and _in_bin(root, path):
            path.unlink(missing_ok=True)


def purge(entry_id: int) -> None:
    """Delete one entry for good, file and subtitles. Only the owner, only in the interface."""
    with SessionLocal() as db:
        entry = db.get(RecycleEntry, entry_id)
        if entry is None:
            raise error("not_found", "This does not exist, or not any more.", 404)
        _purge_files(entry)
        db.delete(entry)
        db.commit()
    logger.info("Recycle entry %d deleted for good", entry_id)


def empty() -> int:
    """Delete every entry for good. Returns how many."""
    with SessionLocal() as db:
        entries = list(db.scalars(select(RecycleEntry)))
        for entry in entries:
            _purge_files(entry)
            db.delete(entry)
        db.commit()
    if entries:
        logger.info("Recycle bin emptied: %d entries deleted for good", len(entries))
    return len(entries)


def forget_missing() -> int:
    """Rows whose file is gone from a root nexcrate sees, after the daily cleanup. A root not mounted keeps its rows."""
    removed = 0
    with SessionLocal() as db:
        for entry in list(db.scalars(select(RecycleEntry))):
            root = _root(entry.root_folder)
            if root is None:
                continue
            if bin_file(entry) is None:
                db.delete(entry)
                removed += 1
        db.commit()
    if removed:
        logger.info("%d recycle entries whose file is gone were forgotten", removed)
    return removed
