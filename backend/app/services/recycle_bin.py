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

⚠️ Files move before the commit. When a later step fails, what moved is moved back and nothing is written.
⚠️ Log lines carry ids and counts, never a title, a path or a name.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ..db import SessionLocal
from ..meldungen import error
from ..models import (
    Download,
    Episode,
    EpisodeFile,
    EpisodeVersion,
    ExtraFile,
    HistoryEntry,
    RecycleEntry,
    ReleaseTrack,
    Title,
    TrackFile,
    Version,
    VersionDefinition,
    utcnow,
)
from . import folders
from .downloads import files

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


@dataclass
class Result:
    versions: list[VersionResult] = field(default_factory=list)
    #: The folders that lost a file, for the media servers after the commit: ``(kind, folder)``.
    folders: list[tuple[str, str]] = field(default_factory=list)
    moves: list[Move] = field(default_factory=list)

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


def _entry(title: Title, version: Version, label: str, actor: Actor, moment: datetime, **values: Any) -> RecycleEntry:
    return RecycleEntry(
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
    track_refs = {
        track_id: mbid
        for track_id, mbid in db.execute(
            select(ReleaseTrack.id, ReleaseTrack.mbid).where(
                ReleaseTrack.id.in_({row.track_id for row in chosen if row.track_id is not None})
            )
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
                       bin_path=_relative(target, root), size=size, file_facts=_track_file_facts(row))
            )  # fmt: skip
            result.folders.append(("music", str(path.parent)))
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
    try:
        for version in versions:
            label = labels.get(version.version_definition_id, "")
            if title.kind == "movie":
                done = _delete_movie(db, title, version, label, actor, moment, result)
            elif title.kind == "album":
                done = _delete_album(db, title, version, label, scope, actor, moment, result)
            else:
                done = _delete_series(db, title, version, label, scope, actor, moment, result)
            result.versions.append(done)
            if done.files or done.missing:
                episodes = done.episodes if title.kind == "series" else None
                tracks = done.tracks if title.kind == "album" else None
                db.add(
                    history(version, label, "files_deleted", done.files, actor, moment, done.size, episodes, tracks)
                )
        db.flush()
    except BaseException:
        undo(result.moves)
        raise
    return result


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
    tell_media_servers(result.folders)
    logger.info("Title %d: %d files went into the recycle bin (%s)", title_id, result.files, actor.kind)
    return result


# --- Reading, restoring, purging ----------------------------------------------------------------------------------- #


def _bin_file(entry: RecycleEntry) -> tuple[Path, Path] | None:
    """The version root as seen and the file in the bin, or None when the root is not there or the path is not in it."""
    root = _root(entry.root_folder)
    if root is None:
        return None
    path = below(root, entry.bin_path)
    if path is None or not path.is_file() or not _in_bin(root, path):
        return None
    return root, path


def listed(db: OrmSession, kind: str | None = None) -> list[dict[str, Any]]:
    """The bin, newest first. ``present`` says whether the file is still there (a share not mounted says no)."""
    query = select(RecycleEntry).order_by(RecycleEntry.deleted_at.desc(), RecycleEntry.id.desc())
    if kind is not None:
        query = query.where(RecycleEntry.kind == kind)
    definitions = {row.id: row for row in db.scalars(select(VersionDefinition))}
    out = []
    for entry in db.scalars(query):
        definition = definitions.get(entry.version_definition_id) if entry.version_definition_id else None
        out.append(
            {
                "id": entry.id,
                "deleted_at": entry.deleted_at,
                "deleted_by": entry.deleted_by,
                "deleted_by_name": entry.deleted_by_name,
                "title_id": entry.title_id,
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
                "present": _bin_file(entry) is not None,
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


def _target_free(root: Path, relative: str) -> Path:
    target = below(root, relative)
    if target is None or os.path.lexists(target):
        raise error("recycle_target_taken", "Something lies where the file was.", 409)
    return target


def _restore_movie(db: OrmSession, entry: RecycleEntry, version: Version, root: Path, source: Path, moves: list[Move],
                   moment: datetime) -> Path:  # fmt: skip
    if version.has_file:
        raise error("recycle_slot_taken", "The version has another file by now.", 409)
    target = _target_free(root, entry.relative_path)
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


def _restore_episode(db: OrmSession, entry: RecycleEntry, version: Version, root: Path, source: Path,
                     moves: list[Move], moment: datetime) -> Path:  # fmt: skip
    from .series import watching

    facts = {name: _load(value) for name, value in (entry.file_facts or {}).items()}
    episode_ids = [int(value) for value in facts.pop("episode_ids", [])]
    links = [db.get(EpisodeVersion, (episode_id, version.id)) for episode_id in episode_ids]
    second = facts.get("part") == 2
    if second:
        # A second half needs its episode, and no second half there by now; the first half may well be there.
        taken = db.scalar(
            select(EpisodeFile.id).where(
                EpisodeFile.version_id == version.id,
                EpisodeFile.part == 2,
                EpisodeFile.part_of_episode_id.in_(episode_ids),
            )
        )
        if not links or any(link is None for link in links) or taken is not None:
            raise error("recycle_slot_taken", "An episode of the file has another file by now.", 409)
        links = []
    elif any(link is None or link.episode_file_id is not None for link in links):
        # Without episodes the file comes back as it went: unclear or left out.
        raise error("recycle_slot_taken", "An episode of the file has another file by now.", 409)
    columns = {column.key for column in EpisodeFile.__table__.columns} - _EPISODE_FILE_SKIP
    values = {name: value for name, value in facts.items() if name in columns}
    if db.scalar(
        select(EpisodeFile.id).where(
            EpisodeFile.version_id == version.id, EpisodeFile.relative_path == values.get("relative_path")
        )
    ):
        raise error("recycle_target_taken", "Something lies where the file was.", 409)
    target = _target_free(root, entry.relative_path)
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


def _restore_track(db: OrmSession, entry: RecycleEntry, version: Version, root: Path, source: Path,
                   moves: list[Move], moment: datetime) -> Path:  # fmt: skip
    facts = {name: _load(value) for name, value in (entry.file_facts or {}).items()}
    columns = {column.key for column in TrackFile.__table__.columns} - _TRACK_FILE_SKIP
    values = {name: value for name, value in facts.items() if name in columns}
    track_id = values.get("track_id")
    if track_id is not None and db.get(ReleaseTrack, track_id) is None:
        # The track left MusicBrainz's release since; the file comes back without one, as an unknown file does.
        values["track_id"] = None
    elif track_id is not None and db.scalar(
        select(TrackFile.id).where(TrackFile.version_id == version.id, TrackFile.track_id == track_id).limit(1)
    ):
        raise error("recycle_slot_taken", "The track has another file by now.", 409)
    if db.scalar(
        select(TrackFile.id).where(
            TrackFile.version_id == version.id, TrackFile.relative_path == values.get("relative_path")
        )
    ):
        raise error("recycle_target_taken", "Something lies where the file was.", 409)
    target = _target_free(root, entry.relative_path)
    _move(source, target, moves)
    row = TrackFile(version_id=version.id, **values)
    row.updated_at = moment
    db.add(row)
    db.flush()
    recount_album(db, version)
    version.updated_at = moment
    return target


def restore(entry_id: int, actor: Actor = OWNER) -> dict[str, Any]:
    """Put a file back where it was and record it again. Refuses when something lies there, the slot has another file,
    or the title or version is gone; the file then stays in the bin."""
    from .automatic import clock, planning

    moves: list[Move] = []
    with SessionLocal() as db:
        entry = db.get(RecycleEntry, entry_id)
        if entry is None:
            raise error("not_found", "This does not exist, or not any more.", 404)
        title = db.get(Title, entry.title_id) if entry.title_id is not None else None
        version = None
        if title is not None and entry.version_definition_id is not None:
            version = db.scalar(
                select(Version).where(
                    Version.title_id == title.id, Version.version_definition_id == entry.version_definition_id
                )
            )
        if title is None or version is None:
            raise error("recycle_title_gone", "The title or its version is no longer in the library.", 409)
        if version.source_id is not None:
            raise fed_by_source()
        located = _bin_file(entry)
        if located is None:
            raise error("recycle_file_gone", "The file is no longer in the recycle bin.", 409)
        root, source = located
        moment = utcnow()
        try:
            if entry.kind == "movie":
                target = _restore_movie(db, entry, version, root, source, moves, moment)
            elif entry.kind == "album":
                target = _restore_track(db, entry, version, root, source, moves, moment)
            else:
                target = _restore_episode(db, entry, version, root, source, moves, moment)
            restored = (
                [{"season": entry.season, "episode": number} for number in entry.episodes or []]
                if entry.kind == "series" and entry.season is not None
                else None
            )
            back = _track_refs(db, entry) if entry.kind == "album" else None
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
        answer = {"title_id": title.id, "kind": title.kind, "version_definition_id": version.version_definition_id}
    tell_media_servers([(answer["kind"], str(target.parent))])
    logger.info("Recycle entry %d restored for title %d (%s)", entry_id, answer["title_id"], actor.kind)
    return answer


def _track_refs(db: OrmSession, entry: RecycleEntry) -> list[str]:
    track_id = (entry.file_facts or {}).get("track_id")
    track = db.get(ReleaseTrack, track_id) if isinstance(track_id, int) else None
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
            if _bin_file(entry) is None:
                db.delete(entry)
                removed += 1
        db.commit()
    if removed:
        logger.info("%d recycle entries whose file is gone were forgotten", removed)
    return removed
