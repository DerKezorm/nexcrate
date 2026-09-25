"""Taking over a Lidarr connection (decisions 1 to 13).

The jobs, the mapping of root folders by files, the blockers and the undo are the Radarr takeover's
(``services/takeover.py``); this module holds what differs for music:

* **Reading** is a full Lidarr import (``lidarr_import.run``, everything, not only what changed), then Lidarr's queue.
  When Lidarr does not answer, the stored state serves, provided a run finished once; otherwise the job ends with
  ``takeover_needs_import``.
* **Paths:** Lidarr names every file relative to its root folder (``Artist/Album [Year]/CD 01/…``). The album folder
  of a version is the common folder of its files without a medium folder at its end (decision 6); samples for the
  mapping are one file per album, spread over the albums. Then every file is looked at.
* **The takeover** writes in one transaction per connection: every version becomes nexcrate's own with its album
  folder as nexcrate sees it, its files relative to that folder (a medium folder kept), the target release it has
  (set by the owner from now on, decision 8), tags untouched (decision 9); missing files go. ``own_since`` is now,
  ``files_read_at`` empty while the album folder exists.
* **Afterwards**, phase ``reading_folders``: every album folder is read for files Lidarr did not know
  (``album_read``); phase ``companions``: ``release.nex`` per album folder. Neither fails the takeover.
* **Undo** removes every file row of the versions (Lidarr's import brings its own back with Lidarr's paths) and
  nexcrate's ``release.nex`` of those album folders, while unchanged.

Log lines carry ids, counts and codes, never names or paths.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session as OrmSession

from ... import crypto
from ...db import SessionLocal
from ...models import (
    Artist,
    HistoryEntry,
    ImportRun,
    Source,
    SourceUnmappedFile,
    Title,
    TrackFile,
    Version,
    VersionDefinition,
)
from .. import companions, companions_album, folders, naming_music, tags, takeover
from ..downloads import files
from ..lidarr import LidarrClient, LidarrError, MusicNamingConfig
from ..radarr import SourceUrlInvalid
from ..schreibweisen import nfc
from . import album_quality, lidarr_import, paths, store

logger = logging.getLogger("nexcrate.takeover")

MISSING_EXAMPLES = 20
#: A folder that holds one medium of an album: ``CD 01``, ``Disc 2``, ``CD2``, ``Enhanced CD 02``, ``Disk 1``.
MEDIUM_FOLDER = paths.MEDIUM_FOLDER


# --- Paths ---------------------------------------------------------------------------------------------------------- #


def source_naming(config: MusicNamingConfig) -> dict[str, Any]:
    """Lidarr's naming as nexcrate would take it, the first code of nexcrate's check per pattern, and notes.

    Without renaming Lidarr keeps the name a file came with, which is what ``{Original Filename}`` renders. Lidarr
    knows more tokens than nexcrate; a pattern with one of them cannot be taken, and the check says which.
    """
    original = "{Original Filename}"
    patterns = {
        "artist_folder": nfc(config.artist_folder),
        "album_folder": nfc(config.album_folder),
        "track_file": nfc(config.track_file) if config.rename_tracks else original,
        "multi_disc_file": nfc(config.multi_disc_file) if config.rename_tracks else original,
    }
    problems = {which: naming_music.problem_of(pattern, which) for which, pattern in patterns.items()}
    notes: list[dict[str, Any]] = []
    if not config.rename_tracks:
        notes.append({"code": "lidarr_no_rename", "values": {}})
    for which, pattern in patterns.items():
        # The folder of a medium is the one folder a pattern may hold (``naming_music.split_medium_folder``); it comes
        # over as it is. Anywhere else a slash still becomes a "+".
        if which == "multi_disc_file" and problems[which] is None:
            continue
        if "/" in pattern or "\\" in pattern:
            notes.append({"code": "slash_in_pattern", "values": {"pattern": which}})
    if config.colon_replacement and config.colon_replacement != "smart":
        notes.append({"code": "colon_format", "values": {"format": config.colon_replacement}})
    if not config.replace_illegal_characters:
        notes.append({"code": "illegal_characters_kept", "values": {}})
    return {
        **patterns,
        "rename_tracks": config.rename_tracks,
        "colon_replacement": config.colon_replacement,
        "problems": problems,
        "can_take": all(problem is None for problem in problems.values()),
        "notes": notes,
    }


def album_names(relatives: list[tuple[str, ...]]) -> tuple[str, ...]:
    """The album folder below the root folder: the folder most of the files lie in, a medium folder at its end left
    out, as Lidarr keeps one path per album. A file somewhere else (a stray second folder) stays outside it."""
    counted: Counter[tuple[str, ...]] = Counter()
    spelled: dict[tuple[str, ...], tuple[str, ...]] = {}
    for parts in relatives:
        if not parts:
            continue
        folder = list(parts[:-1])
        while folder and MEDIUM_FOLDER.match(folder[-1]):
            folder.pop()
        key = tuple(nfc(name) for name in folder)
        counted[key] += 1
        spelled.setdefault(key, tuple(folder))
    if not counted:
        return ()
    # Most files first; on a tie the folder named first keeps it.
    best = max(counted, key=lambda key: counted[key])
    return spelled[best]


def inside_album(relative: tuple[str, ...], album: tuple[str, ...]) -> bool:
    """Whether a file lies in the album folder or one of its medium folders."""
    return len(relative) > len(album) and tuple(nfc(name) for name in relative[: len(album)]) == tuple(
        nfc(name) for name in album
    )


def _parts(relative: str | None) -> tuple[str, ...]:
    return tuple(part for part in (relative or "").replace("\\", "/").split("/") if part)


def item_of(version: Version, key: int, relative: tuple[str, ...], album: tuple[str, ...], size: int) -> takeover.Item:
    """A ``takeover.Item`` for one file of an album version: the album folder stands where the movie folder stands."""
    root = files.remote_parts(version.root_folder) if version.root_folder else None
    kind, root_names = (root[0], tuple(root[1])) if root is not None else ("", ())
    inside = relative[len(album) :] if album and relative[: len(album)] == album else relative
    return takeover.Item(
        version_id=key,
        title_id=version.title_id,
        has_file=bool(relative),
        size=size,
        state=version.state,
        root=files.join_remote(kind, list(root_names)) if root is not None else None,
        root_kind=kind,
        root_names=root_names,
        movie_names=album,
        file_names=tuple(inside),
    )


@dataclass
class Looked:
    albums: list[takeover.Item]
    album_files: list[takeover.Item]
    #: Track file id to its version id.
    version_of: dict[int, int]
    #: Version id to its album item and its album folder below the root folder.
    album_of: dict[int, takeover.Item]
    #: Files Lidarr lists outside their album's folder: they stay where they are, their tracks count as missing.
    outside: int = 0


def items(db: OrmSession, source_id: int) -> Looked:
    versions = list(db.scalars(select(Version).where(Version.source_id == source_id).order_by(Version.id)))
    rows: dict[int, list[TrackFile]] = defaultdict(list)
    ids = [version.id for version in versions]
    for start in range(0, len(ids), 500):
        for row in db.scalars(
            select(TrackFile)
            .where(TrackFile.version_id.in_(ids[start : start + 500]), TrackFile.source_file_id.is_not(None))
            .order_by(TrackFile.id)
        ):
            rows[row.version_id].append(row)
    looked = Looked([], [], {}, {})
    for version in versions:
        own = rows.get(version.id, [])
        relatives = [_parts(row.relative_path) for row in own]
        album = album_names(relatives)
        first = own[0] if own else None
        album_item = item_of(
            version, version.id, relatives[0] if relatives else (), album, int(first.size or 0) if first else 0
        )
        looked.albums.append(album_item)
        looked.album_of[version.id] = album_item
        for row, relative in zip(own, relatives, strict=True):
            if album and not inside_album(relative, album):
                looked.outside += 1
                continue
            looked.album_files.append(item_of(version, row.id, relative, album, int(row.size or 0)))
            looked.version_of[row.id] = version.id
    return looked


# --- Reading -------------------------------------------------------------------------------------------------------- #


async def _queue_albums(url: str, key: str) -> list[int]:
    async with LidarrClient(url, key) as lidarr:
        return [item.album_id for item in await lidarr.queue() if item.album_id is not None]


def _read(job: takeover.Job, run_id: int) -> dict[str, Any]:
    """Read Lidarr as the owner's import does (everything), then its queue; else the stored state."""
    lidarr_import.run(job.source_id, run_id, scheduled=False)
    with SessionLocal() as db:
        source = db.get(Source, job.source_id)
        run = db.get(ImportRun, run_id)
        if source is None:
            raise takeover.JobFailed("not_found")
        if source.taken_over_at is not None:
            raise takeover.JobFailed("source_taken_over")
        url, stored_key = source.url, source.api_key
        if run is None or run.status != "done":
            code = run.error_code if run is not None else None
            last = db.scalar(
                select(ImportRun)
                .where(ImportRun.source_id == source.id, ImportRun.status == "done", ImportRun.id != run_id)
                .order_by(ImportRun.id.desc())
                .limit(1)
            )
            if last is None:
                raise takeover.JobFailed(code or "takeover_needs_import")
            return {"data_from": "stored", "read_at": last.finished_at, "queued": None}
        fresh = {"data_from": "fresh", "read_at": run.finished_at}
    key = crypto.decrypt(stored_key)
    queued: list[int] | None = None
    if key:
        try:
            queued = asyncio.run(_queue_albums(url, key))
        except LidarrError, SourceUrlInvalid:
            queued = None
    return {**fresh, "queued": queued}


def _queue(db: OrmSession, source_id: int, queued: list[int] | None) -> int:
    """Items Lidarr's queue holds for albums of this connection; without its answer, the stored states."""
    if queued is not None:
        albums = set(
            db.scalars(
                select(Version.lidarr_album_id).where(
                    Version.source_id == source_id, Version.lidarr_album_id.is_not(None)
                )
            )
        )
        return sum(1 for album_id in queued if album_id in albums)
    return int(
        db.scalar(
            select(func.count(Version.id)).where(Version.source_id == source_id, Version.state == "downloading")
        )
        or 0
    )


def _counts(db: OrmSession, source_id: int) -> dict[str, Any]:
    """What the owner learns before taking over (decision 7): wanted albums, albums below the profile's target,
    albums lacking tracks, and the files Lidarr could not map."""
    versions = list(db.scalars(select(Version).where(Version.source_id == source_id)))
    rules = album_quality.rules_of(db)
    return {
        "wanted": sum(1 for version in versions if version.monitored and not version.has_file),
        "would_upgrade": sum(1 for version in versions if version.has_file and version.cutoff_not_met)
        if rules is not None
        else None,
        "incomplete": sum(1 for version in versions if store.incomplete(version)),
        "unmapped": int(
            db.scalar(select(func.count(SourceUnmappedFile.id)).where(SourceUnmappedFile.source_id == source_id)) or 0
        ),
    }


# --- The work ------------------------------------------------------------------------------------------------------- #


def work(job: takeover.Job, request: takeover.Request, run_id: int) -> dict[str, Any]:
    read = _read(job, run_id)
    with SessionLocal() as db:
        source = db.get(Source, job.source_id)
        if source is None:
            raise takeover.JobFailed("not_found")
        looked = items(db, source.id)
        queue = _queue(db, source.id, read["queued"])

    job.set(phase="mapping")
    sight = takeover.Sight()
    with_files = [item for item in looked.albums if item.has_file and item.movie_names]
    roots = takeover.map_roots(sight, with_files, request.mappings)
    for state in roots.values():
        state.files = sum(1 for item in looked.album_files if item.root == state.remote and item.locatable)
    checked = takeover.look_at_files(job, sight, looked.album_files, roots)
    with SessionLocal() as db:
        source = db.get(Source, job.source_id)
        if source is None:
            raise takeover.JobFailed("not_found")
        version = takeover._version_brief(db, source, roots)
        # The music profile: the movie check of the brief does not know it.
        version["has_profile"] = album_quality.rules_of(db) is not None
        counts = _counts(db, source.id)
    unmapped = sorted(
        (state for state in roots.values() if state.files > 0 and state.local is None), key=lambda state: state.remote
    )
    blockers = [
        name
        for name, applies in (
            ("root_unmapped", bool(unmapped)),
            ("files_missing", bool(checked.missing)),
            ("queue_active", queue > 0),
        )
        if applies
    ]
    result: dict[str, Any] = {
        "app": "lidarr",
        "data_from": read["data_from"],
        "read_at": read["read_at"],
        "movies": 0,
        "series": 0,
        "albums": len(looked.albums),
        "with_file": sum(1 for item in looked.albums if item.has_file),
        "album_files": len(looked.album_files),
        "episode_files": 0,
        "roots": [state.out() for state in sorted(roots.values(), key=lambda state: state.remote)],
        "files_found": len(checked.found),
        "files_missing": len(checked.missing),
        "files_other_size": checked.other_size,
        "files_outside": looked.outside,
        "missing_examples": ["/".join(item.below_root) for item in checked.missing if item.locatable][
            :MISSING_EXAMPLES
        ],
        "queue": queue,
        "unclear": counts["unmapped"],
        "would_upgrade": counts["would_upgrade"],
        "wanted": counts["wanted"],
        "incomplete": counts["incomplete"],
        "version": version,
        "naming": None,
        "notes": [],
        "blockers": blockers,
        "taken": None,
        "companions": None,
    }
    logger.info(
        "Takeover %s %d of Lidarr source %d: %s data, %d albums, %d files found, %d missing, %d of other size, "
        "%d root folders, %d unmapped, %d queue items",
        job.kind,
        job.id,
        job.source_id,
        read["data_from"],
        len(looked.albums),
        len(checked.found),
        len(checked.missing),
        checked.other_size,
        len(roots),
        len(unmapped),
        queue,
    )
    if job.kind == "check":
        return result

    if unmapped:
        raise takeover.JobFailed("takeover_root_unmapped", remote=unmapped[0].remote)
    if checked.missing and not request.accept_missing:
        raise takeover.JobFailed("takeover_files_missing", count=len(checked.missing))
    if queue and not request.accept_queue:
        raise takeover.JobFailed("takeover_queue_active", count=queue)
    job.set(phase="saving")
    places = album_places(sight, looked, roots)
    result["taken"] = save(job.source_id, request, checked.found, looked, places)
    result["companions"] = after_save(job, result["taken"]["version_ids"])
    result["taken"].pop("version_ids")
    return result


def album_places(
    sight: takeover.Sight, looked: Looked, roots: dict[str, takeover.RootState]
) -> dict[int, tuple[str, str | None]]:
    """Per version nexcrate's folder of its root folder, and its album folder below it as spelled on disk (None when it
    is not there, or the album has no folder of its own)."""
    places: dict[int, tuple[str, str | None]] = {}
    seen: dict[str, Path | None] = {}
    for version_id, item in looked.album_of.items():
        state = roots.get(item.root) if item.root is not None else None
        if state is None or state.local is None:
            continue
        if state.remote not in seen:
            seen[state.remote] = sight.folder(state.local)
        local = seen[state.remote]
        if local is None:
            continue
        relative: str | None = None
        if item.movie_names:
            found = sight.lookup(local, item.movie_names)
            if found is not None and sight.folder(found) is not None:
                relative = found.relative_to(local).as_posix()
        places[version_id] = (str(local), relative)
    return places


def save(
    source_id: int,
    request: takeover.Request,
    found: dict[int, takeover.Found],
    looked: Looked,
    places: dict[int, tuple[str, str | None]],
) -> dict[str, Any]:
    """The takeover's one transaction. Returns the counts of ``taken`` and the version ids for the phases after."""
    moment = takeover.now()
    with SessionLocal() as db:
        db.execute(update(Source).where(Source.id == source_id).values(updated_at=moment))
        source = db.get(Source, source_id)
        if source is None:
            db.rollback()
            raise takeover.JobFailed("not_found")
        if source.taken_over_at is not None:
            db.rollback()
            raise takeover.JobFailed("source_taken_over")
        definition = db.get(VersionDefinition, source.version_id)
        if definition is None:
            db.rollback()
            raise takeover.JobFailed("not_found")
        if request.folder and definition.folder is None:
            try:
                definition.folder = folders.check_version_folder(db, definition, request.folder)
            except HTTPException as exc:
                db.rollback()
                raise takeover._failed_from(exc) from exc
        versions = list(db.scalars(select(Version).where(Version.source_id == source.id).order_by(Version.id)))
        version_ids = [version.id for version in versions]
        for version in versions:
            root, relative = places.get(version.id, (None, None))
            version.source_id = None
            version.lidarr_album_id = None
            version.source_album_path = None
            version.profile_name = None
            version.root_folder = root
            version.relative_path = relative
            if version.target_set_by == "source":
                # Decision 8: the release the album has is the owner's choice from now on.
                version.target_set_by = "owner"
            version.own_since = moment
            version.files_read_at = None if relative else moment
            version.updated_at = moment
            db.add(
                HistoryEntry(
                    title_id=version.title_id,
                    version_id=version.id,
                    version_definition_id=definition.id,
                    version_label=definition.label,
                    event="taken_over",
                    at=moment,
                    detail=source.name[:1024],
                )
            )
        db.flush()

        kept = missing = 0
        gone: list[int] = []
        for start in range(0, len(version_ids), 500):
            rows = list(
                db.scalars(
                    select(TrackFile).where(
                        TrackFile.version_id.in_(version_ids[start : start + 500]),
                        TrackFile.source_file_id.is_not(None),
                    )
                )
            )
            for row in rows:
                located = found.get(row.id)
                place = places.get(row.version_id)
                if located is None or place is None or place[1] is None:
                    gone.append(row.id)
                    missing += 1
                    continue
                try:
                    inside = Path(located.root_folder, located.relative_path).relative_to(Path(place[0], place[1]))
                except ValueError:
                    gone.append(row.id)
                    missing += 1
                    continue
                row.relative_path = inside.as_posix()[:2048]
                row.size = located.size
                row.updated_at = moment
                kept += 1
        for start in range(0, len(gone), 500):
            db.execute(
                delete(TrackFile).where(TrackFile.id.in_(gone[start : start + 500])),
                execution_options={"synchronize_session": False},
            )
        db.flush()
        artists: dict[int, str] = {}
        for version in versions:
            store._count_tracks(db, version)
            title = db.get(Title, version.title_id)
            if title is not None and title.artist_id is not None and version.relative_path:
                parts = version.relative_path.split("/")
                if len(parts) > 1:
                    artists.setdefault(title.artist_id, parts[0])
        for artist_id, folder in artists.items():
            artist = db.get(Artist, artist_id)
            if artist is not None and not artist.folder:
                artist.folder = folder
        source.taken_over_at = moment
        # The connection's tags become the owner's.
        tags.release(db, source.id)
        source.updated_at = moment
        from ..automatic import clock as automatic_clock
        from ..automatic import planning as automatic_planning

        automatic_planning.replan(db, {version.title_id for version in versions}, automatic_clock.now())
        db.commit()
    logger.info(
        "Lidarr source %d taken over: %d versions, %d files kept, %d files missing",
        source_id,
        len(version_ids),
        kept,
        missing,
    )
    return {
        "versions": len(version_ids),
        "with_file": kept,
        "missing": missing,
        "titles": 0,
        "version_ids": version_ids,
    }


def after_save(job: takeover.Job, version_ids: list[int]) -> dict[str, int]:
    """The phases after the commit: read the album folders, then ``release.nex``. Never fails the takeover."""
    from . import album_read

    counts: dict[str, int] = {}
    try:
        job.set(phase="reading_folders", done=0, total=len(version_ids))
        for index, version_id in enumerate(version_ids, start=1):
            with SessionLocal() as db:
                row = db.get(Version, version_id)
                pending = row is not None and row.source_id is None and row.files_read_at is None
            if pending:
                album_read.read_version(version_id)
            job.set(done=index)
        job.set(phase="companions", done=0, total=len(version_ids))
        for index, version_id in enumerate(version_ids, start=1):
            with SessionLocal() as db:
                row = db.get(Version, version_id)
                has_file = row is not None and row.has_file
            # An album without files has no folder: nothing to write, and nothing to report.
            if has_file:
                state = companions_album.write_version(version_id)
                counts[state] = counts.get(state, 0) + 1
            job.set(done=index)
    except Exception:  # the takeover is committed
        logger.exception("Takeover %d of Lidarr source %d: a phase after saving stopped", job.id, job.source_id)
    return counts


# --- Undo ----------------------------------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AlbumRemoval:
    folder: Path
    sha256: str


def prepare_undo(db: OrmSession, versions: list[Version], source_id: int) -> list[AlbumRemoval]:
    """In undo's transaction: every file row of the versions goes (Lidarr's import brings its own back, with Lidarr's
    paths), the versions belong to the connection again (the import adopts only the owner's own albums by itself).
    Returns the ``release.nex`` files to remove after the commit."""
    removals: list[AlbumRemoval] = []
    enabled = companions.enabled(db)
    for version in versions:
        if enabled and version.companion_sha256:
            folder = companions_album._folder(version)
            if folder is not None:
                removals.append(AlbumRemoval(folder, version.companion_sha256))
        db.execute(
            delete(TrackFile).where(TrackFile.version_id == version.id),
            execution_options={"synchronize_session": False},
        )
        version.companion_state = None
        version.companion_sha256 = None
        version.companion_written_at = None
        version.own_since = None
        version.files_read_at = None
        version.source_id = source_id
    return removals


def remove_entries(removals: list[AlbumRemoval]) -> dict[str, int]:
    """After undo's commit: nexcrate's ``release.nex`` of those album folders, only while unchanged. Never raises."""
    counts = {"removed": 0, "kept": 0, "missing": 0, "failed": 0}
    try:
        for removal in removals:
            path = removal.folder / companions.FILE_NAME
            with companions._lock_for(removal.folder):
                try:
                    data = path.read_bytes()
                except FileNotFoundError:
                    counts["missing"] += 1
                    continue
                except OSError:
                    counts["failed"] += 1
                    continue
                if hashlib.sha256(data).hexdigest() != removal.sha256:
                    counts["kept"] += 1
                    continue
                if companions.remove_file(removal.folder):
                    counts["removed"] += 1
                else:
                    counts["failed"] += 1
    except Exception:
        logger.exception("Removing release.nex files after undoing a Lidarr takeover stopped")
    logger.info("release.nex files of album folders removed after an undo: %s", companions._codes(counts))
    return counts
