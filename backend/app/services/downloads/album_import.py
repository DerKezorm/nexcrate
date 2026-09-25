"""Filing a finished album download (M4.3 to M4.6).

``importing.run`` hands a download with the scope ``album`` here. The flow:

1. **Checks** as for movies: the version exists, no connection feeds it, the job folder is visible, nothing dangerous.
   Without loaded tracks the download waits (``waiting_tracks``) and its artist moves to the front of the loading job
   (decision 4); after 24 hours it is the problem ``album_tracks_missing``.
2. **The audio files** of the job, archives unpacked next to the album folder, each read with ``tags`` (length, codec,
   tags). The rules of movies for samples and extras do not hold: a track called "Sample" is music (decision 7).
3. **Which release, which track** (``music.file_matching``); one file per medium as long as the medium is a single
   file with a cue sheet: refused and blocked (decision 13). Every file is kept in ``download_audio_files`` with its
   decision before anything moves, so the dialog can show them.
4. **An album with files** is replaced only when neither the step nor the count of target tracks gets worse
   (decisions 20, E2, E11): then every old file goes into the recycle folder first. Otherwise nothing is filed and the
   problem ``album_not_better`` says why; the owner may confirm.
5. **Filing file by file**, each in its own transaction: the album folder (fixed once, decision 16), the name after the
   patterns, a copy instead of a hardlink for a torrent while tags are written, the tags written into the temporary
   file before its rename (decision 18), the ``track_files`` row right after the rename. Before a file moves its row
   says ``placing`` with its target; the next round takes over a file found there (decision 19).
6. **Afterwards:** ``folder.jpg``, the version's release, step and counts, and the state: a file nobody assigned makes
   the problem ``files_unassigned`` (decision 31), otherwise ``imported`` and SABnzbd's job folder goes.

⚠️ Every path written or deleted lies in the album folder or the job folder after resolving links; folder names come
only from the patterns. Logs carry ids and counts, never names or paths.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session as OrmSession

from ... import crypto
from ...db import SessionLocal, set_setting
from ...models import (
    Artist,
    Download,
    DownloadAudioFile,
    DownloadClient,
    Release,
    ReleaseMedium,
    ReleaseTrack,
    Title,
    TrackFile,
    Version,
    VersionDefinition,
)
from .. import companions_album, downloaders, folders, naming, naming_music, schreibweisen
from ..mediaservers import notify as mediaserver_notify
from ..music import album_quality, align, covers, fingerprint, same_song, tag_writing, tags
from ..music import file_matching as fm
from ..music import musicbrainz as mb
from ..music import store as music_store
from ..profiles import store as profile_store
from ..releases import music_qualities as mq
from . import files, store, unpacking

logger = logging.getLogger("nexcrate.import")

#: At most this many audio files per download; more is ``too_many_files``.
MAX_AUDIO = 500
#: How long a download waits for the tracks of its album (decision 4).
WAIT_FOR_TRACKS = timedelta(hours=24)
UNPACKED_PREFIX = "unpacked:"
#: Folders and files left out of an album download: hidden ones and those of NAS systems. Nothing else (decision 7).
_LEFT_OUT_FOLDERS = frozenset({"@eadir", ".@__thumb"})
_LEFT_OUT_FILES = frozenset({".ds_store", "thumbs.db", "desktop.ini"})
_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png"})
_COVER_NAMES = ("cover", "folder", "front")
FOLDER_IMAGE = "folder.jpg"
#: Problems that refuse the download: its release goes on the blocklist.
REFUSED = ("dangerous_file", "encrypted", "album_single_file")
#: Where the owner's choice for a file comes from in ``Manual``.
LOOSE = "loose"


class Problem(Exception):
    def __init__(self, code: str, **values: object) -> None:
        super().__init__(code)
        self.code = code
        self.values = dict(values)


class StillUnpacking(Exception):
    """The client still works on the files."""


class WaitingForTracks(Exception):
    """The album's tracks are not loaded yet."""


# --- What an import knows ----------------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CurrentFile:
    id: int
    relative_path: str
    track_ids: tuple[int, ...]
    quality: str | None


@dataclass(frozen=True)
class StoredRow:
    id: int
    path: str
    decision: str
    track_id: int | None
    target: str | None
    size: int
    #: The ``track_files`` row once the file lies in the album folder.
    track_file_id: int | None

    @property
    def placed(self) -> bool:
        return self.track_file_id is not None


@dataclass(frozen=True)
class Context:
    download_id: int
    title_id: int
    definition_id: int | None
    version_id: int | None
    version_fed: bool
    protocol: str
    client_download_id: str
    reported_path: str | None
    mappings: list[dict[str, str]]
    #: ⚠️ With the decrypted secret; None when the client is gone.
    target: downloaders.Target | None = field(repr=False)
    release_title: str
    completed_at: datetime | None
    version_folder: str | None
    root_folder: str | None
    #: The album folder below ``root_folder`` once a file lies in it: ``Artist/Album (Year)``.
    album_relative: str | None
    artist_id: int | None
    artist_folder: str | None
    album: naming_music.AlbumFacts
    naming: naming_music.MusicNaming
    editions: tuple[fm.Edition, ...]
    others: tuple[fm.OtherAlbum, ...]
    target_release_id: int | None
    #: The release this download's files were read as before (the dialog's default).
    download_release_id: int | None
    current: tuple[CurrentFile, ...]
    current_step: str | None
    rules: dict[str, Any] | None
    write_tags: bool
    covers_enabled: bool
    stored: tuple[StoredRow, ...]
    group_mbid: str | None
    #: ⚠️ The decrypted AcoustID key when fingerprinting can run (M4.9); None otherwise.
    fingerprint_key: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class Manual:
    """The owner's choice in the dialog (decisions 32 and 33): per row a track id, ``LOOSE``, or None (not filed)."""

    release_id: int | None
    chosen: dict[int, int | str | None]
    confirm: frozenset[str] = frozenset()


def _target_of(client: DownloadClient | None) -> downloaders.Target | None:
    if client is None:
        return None
    return downloaders.Target(
        kind=client.kind,
        url=client.url,
        username=client.username or "",
        secret=crypto.decrypt(client.secret) if client.secret else "",
        category=client.category,
    )


def _editions(db: OrmSession, title_id: int) -> tuple[fm.Edition, ...]:
    releases = list(
        db.scalars(
            select(Release).where(Release.title_id == title_id, Release.tracks_loaded.is_(True)).order_by(Release.id)
        )
    )
    if not releases:
        return ()
    ids = [release.id for release in releases]
    media = {row.id: row.position for row in db.scalars(select(ReleaseMedium).where(ReleaseMedium.release_id.in_(ids)))}
    tracks: dict[int, list[fm.Track]] = {release_id: [] for release_id in ids}
    for track in db.scalars(select(ReleaseTrack).where(ReleaseTrack.release_id.in_(ids))):
        tracks[track.release_id].append(
            fm.Track(
                id=track.id,
                mbid=track.mbid,
                recording_mbid=track.recording_mbid,
                medium=media.get(track.medium_id, 1),
                position=track.position,
                name=track.name,
                length_ms=track.length_ms,
            )
        )
    return tuple(
        fm.Edition(
            id=release.id,
            mbid=release.mbid,
            media=max(release.media_count, len({item.medium for item in tracks[release.id]}), 1),
            tracks=tuple(sorted(tracks[release.id], key=lambda item: (item.medium, item.position))),
        )
        for release in releases
    )


def load(db: OrmSession, download_id: int) -> Context | None:
    row = db.get(Download, download_id)
    if row is None or row.scope != store.ALBUM_SCOPE:
        return None
    title = db.get(Title, row.title_id)
    if title is None:
        return None
    client = db.get(DownloadClient, row.client_id) if row.client_id is not None else None
    version = store.version_of(db, row.title_id, row.version_definition_id)
    definition = db.get(VersionDefinition, row.version_definition_id) if row.version_definition_id else None
    artist = db.get(Artist, title.artist_id) if title.artist_id is not None else None
    others = (
        tuple(
            fm.OtherAlbum(title_id=other.id, mbid=other.mbid, name=other.title)
            for other in db.scalars(
                select(Title).where(Title.kind == "album", Title.artist_id == title.artist_id, Title.id != title.id)
            )
        )
        if title.artist_id is not None
        else ()
    )
    current: tuple[CurrentFile, ...] = ()
    if version is not None:
        current = tuple(
            CurrentFile(
                id=item.id,
                relative_path=item.relative_path,
                track_ids=tuple(item.track_ids or ([item.track_id] if item.track_id else [])),
                quality=item.quality,
            )
            for item in db.scalars(select(TrackFile).where(TrackFile.version_id == version.id).order_by(TrackFile.id))
        )
    profile = profile_store.of_version(db, row.version_definition_id)
    rules = profile.rules if profile is not None and isinstance(profile.rules, dict) else None
    year = None
    if title.first_release_date and title.first_release_date[:4].isdigit():
        year = int(title.first_release_date[:4])
    stored = tuple(
        StoredRow(item.id, item.path, item.decision, item.track_id, item.target, item.size, item.track_file_id)
        for item in db.scalars(
            select(DownloadAudioFile).where(DownloadAudioFile.download_id == row.id).order_by(DownloadAudioFile.id)
        )
    )
    return Context(
        download_id=row.id,
        title_id=row.title_id,
        definition_id=row.version_definition_id,
        version_id=version.id if version is not None else None,
        version_fed=version is not None and version.source_id is not None,
        protocol=row.protocol,
        client_download_id=row.client_download_id,
        reported_path=row.reported_path,
        mappings=[dict(mapping) for mapping in (client.path_mappings or [])] if client is not None else [],
        target=_target_of(client),
        release_title=row.release_title,
        completed_at=row.completed_at,
        version_folder=definition.folder if definition is not None else None,
        root_folder=version.root_folder if version is not None and version.relative_path else None,
        album_relative=version.relative_path if version is not None else None,
        artist_id=artist.id if artist is not None else None,
        artist_folder=artist.folder if artist is not None else None,
        album=naming_music.AlbumFacts(
            artist_name=artist.name if artist is not None else "",
            album_title=title.title or "",
            year=year or title.year,
            artist_mbid=artist.mbid if artist is not None else None,
            album_mbid=title.mbid,
            album_type=title.primary_type,
            disambiguation=title.release_group_disambiguation,
        ),
        naming=naming_music.load(db),
        editions=_editions(db, title.id),
        others=others,
        target_release_id=version.target_release_id if version is not None else None,
        download_release_id=row.release_id,
        current=current,
        current_step=version.quality if version is not None and version.has_file else None,
        rules=rules,
        write_tags=tag_writing.write_enabled(db),
        covers_enabled=mb.covers_enabled(db),
        stored=stored,
        group_mbid=title.mbid,
        fingerprint_key=fingerprint.key(db) if fingerprint.usable(db) else None,
    )


# --- Paths ---------------------------------------------------------------------------------------------------------- #


def _local_job(context: Context) -> Path:
    reported = context.reported_path
    if not reported:
        raise Problem("path_not_found", proposal=None)
    mapped = files.map_remote(reported, context.mappings)
    candidates: list[str | Path] = [mapped] if mapped is not None else []
    if files.remote_parts(reported) is not None:
        candidates.append(reported)
    for candidate in candidates:
        path = folders.visible_path(candidate)
        if path is not None:
            return path
    raise Problem("path_not_found", proposal=files.propose(reported, folders.mount_points()))


def _root(context: Context) -> Path:
    """The folder the artist folders lie in: the stored one of an album with files, else the music version's."""
    if context.root_folder and context.album_relative:
        try:
            root, _mount = folders.visible(context.root_folder)
        except folders.NotVisible as exc:
            raise Problem("import_failed", reason="folder_not_visible") from exc
        return root
    if not context.version_folder:
        raise Problem("import_failed", reason="no_folder")
    try:
        folder, _mount = folders.visible(context.version_folder)
    except folders.NotVisible as exc:
        raise Problem("import_failed", reason="folder_not_visible") from exc
    return folder


def _plain(name: str) -> bool:
    return bool(name) and name not in (".", "..") and not any(mark in name for mark in ("/", "\\", "\x00"))


def _ensure_folder(path: Path, inside: Path) -> Path:
    try:
        path.mkdir(exist_ok=True)
    except OSError as exc:
        raise Problem("import_failed", reason="folder_not_writable") from exc
    if files.is_link(path) or not files.strictly_inside(path, inside):
        raise Problem("import_failed", reason="destination_outside")
    return files.resolved(path) or path


def _key(path: str) -> str:
    return schreibweisen.nfc(path.replace("\\", "/").strip("/")).casefold()


def album_folder(db: OrmSession, context: Context, root: Path) -> tuple[str, str]:
    """The artist folder and the album folder names: the stored ones, else from the patterns, with an addition when
    another album of the library or a folder nexcrate did not make has the name already (decision 15)."""
    if context.album_relative:
        parts = [part for part in context.album_relative.replace("\\", "/").split("/") if part]
        if len(parts) == 2 and all(_plain(part) for part in parts):
            return parts[0], parts[1]
        raise Problem("import_failed", reason="destination_outside")
    artist = context.artist_folder or naming_music.artist_folder_name(context.naming, context.album)
    album = naming_music.album_folder_name(context.naming, context.album)
    if not (_plain(artist) and _plain(album)):
        raise Problem("import_failed", reason="destination_outside")
    taken = {
        _key(path)
        for path in db.scalars(
            select(Version.relative_path).where(
                Version.relative_path.is_not(None), Version.title_id != context.title_id, Version.has_file.is_(True)
            )
        )
        if path
    }
    folder = root / artist / album
    busy = _key(f"{artist}/{album}") in taken or (folder.is_dir() and any(folder.iterdir()))
    if busy:
        # The type first (``Single``, ``EP``), then the disambiguation, then the id: the design notes, answer G2.
        for addition in naming_music.additions(context.album, context.title_id):
            candidate = naming_music.album_folder_name(context.naming, context.album, addition)
            spot = root / artist / candidate
            if not _plain(candidate) or _key(f"{artist}/{candidate}") in taken:
                continue
            if spot.is_dir() and any(spot.iterdir()):
                continue
            album = candidate
            break
        else:
            raise Problem("import_failed", reason="destination_exists")
    return artist, album


# --- The files ---------------------------------------------------------------------------------------------------- #


@dataclass
class Found:
    audio: list[fm.AudioFile]
    reads: dict[int, tags.Read]
    #: Key to its real path and whether it came out of the archives.
    paths: dict[int, tuple[Path, bool]]
    sizes: dict[int, int]
    job: Path
    unpacked: Path | None
    cue_sheet: bool
    images: list[Path] = field(default_factory=list)


@dataclass
class _Walk:
    audio: list[tuple[Path, int]] = field(default_factory=list)
    archives: list[tuple[Path, int]] = field(default_factory=list)
    images: list[Path] = field(default_factory=list)
    cue_sheet: bool = False
    dangerous: bool = False
    entries: int = 0


def _left_out(name: str, folder: bool) -> bool:
    lowered = name.casefold()
    if name.startswith("."):
        return True
    return lowered in _LEFT_OUT_FOLDERS if folder else lowered in _LEFT_OUT_FILES or name.startswith("._")


def walk(root: Path) -> _Walk:
    """Audio files, archives, images and cue sheets of a download; dangerous files anywhere. Links are skipped."""
    found = _Walk()
    if files.is_link(root):
        return found
    entries: Iterable[tuple[Path, str]]
    if root.is_file():
        entries = [(root, root.name)]
    else:
        collected: list[tuple[Path, str]] = []
        for folder, dirnames, filenames in os.walk(root, followlinks=False):
            here = Path(folder)
            dirnames[:] = [name for name in dirnames if not files.is_link(here / name) and not _left_out(name, True)]
            collected.extend((here / name, name) for name in filenames)
            if len(collected) > files.MAX_ENTRIES:
                break
        entries = collected
    for path, name in entries:
        found.entries += 1
        if found.entries > files.MAX_ENTRIES or files.is_link(path) or _left_out(name, False):
            continue
        extension = files.extension_of(name)
        if extension in files.DANGEROUS_EXTENSIONS or extension in files.EXECUTABLE_EXTENSIONS:
            found.dangerous = True
            continue
        try:
            size = path.lstat().st_size
        except OSError:
            continue
        if files.is_archive(name):
            found.archives.append((path, size))
        elif tags.is_audio(name):
            found.audio.append((path, size))
        elif extension == ".cue":
            found.cue_sheet = True
        elif extension in _IMAGE_EXTENSIONS:
            found.images.append(path)
    return found


def _relative(path: Path, origin: Path, unpacked: bool) -> str:
    try:
        text = path.relative_to(origin).as_posix() if path != origin else path.name
    except ValueError:
        text = path.name
    return schreibweisen.nfc((UNPACKED_PREFIX if unpacked else "") + text)[:1024]


def gather(context: Context, root: Path) -> Found:
    """Walk the job, unpack when wanted, read every audio file. Raises ``Problem`` or ``StillUnpacking``.

    A job without audio is ``no_audio``, unless an earlier round moved its files already (a stop before the end).
    """
    job = _local_job(context)
    if files.below_working_folder(job):
        raise StillUnpacking
    walked = walk(job)
    if walked.dangerous:
        raise Problem("dangerous_file")
    if files.strictly_inside(root, job) or files.inside(job, root):
        raise Problem("import_failed", reason="source_in_version_folder")
    listed: list[tuple[Path, int, bool]] = [(path, size, False) for path, size in walked.audio]
    unpacked: Path | None = None
    audio_bytes = sum(size for _path, size in walked.audio)
    if walked.archives and (not walked.audio or sum(size for _path, size in walked.archives) > audio_bytes):
        try:
            unpacked = unpacking.unpack(unpacking.archive_sets(walked.archives), root, context.download_id)
        except files.FileProblem as exc:
            raise Problem(exc.code, **exc.values) from exc
        inner = walk(unpacked)
        if inner.dangerous:
            unpacking.remove(root, context.download_id)
            raise Problem("dangerous_file")
        listed += [(path, size, True) for path, size in inner.audio]
        walked.cue_sheet = walked.cue_sheet or inner.cue_sheet
        walked.images += inner.images
    if len(listed) > MAX_AUDIO:
        raise Problem("too_many_files")
    if not listed and not any(row.placed or row.decision == "placing" for row in context.stored):
        raise Problem("no_audio")
    audio: list[fm.AudioFile] = []
    reads: dict[int, tags.Read] = {}
    paths: dict[int, tuple[Path, bool]] = {}
    sizes: dict[int, int] = {}
    origin_job = job if job.is_dir() else job.parent
    for key, (path, size, from_archive) in enumerate(sorted(listed, key=lambda item: (item[2], str(item[0]))), 1):
        read = tags.read(path)
        reads[key] = read
        origin = unpacked if from_archive and unpacked is not None else origin_job
        audio.append(
            fm.AudioFile(
                key=key,
                path=_relative(path, origin, from_archive),
                duration_ms=read.audio.duration_ms if read.audio is not None else None,
                tags=read.tags,
            )
        )
        paths[key] = (path, from_archive)
        sizes[key] = size
    return Found(audio, reads, paths, sizes, job, unpacked, walked.cue_sheet, walked.images)


# --- Deciding ------------------------------------------------------------------------------------------------------- #


def _step_of_reads(reads: Iterable[tags.Read]) -> str:
    return mq.lowest([mq.step_of_lidarr(tags.quality_name(read.audio)) for read in reads])


def _song_keys(context: Context) -> dict[int, frozenset[str]]:
    return same_song.keys_by_track(edition.tracks for edition in context.editions)


def _recordings(context: Context, track_ids: Iterable[int]) -> set[str]:
    """The keys of the songs of these tracks: recording, and a name unique in its release (``same_song``)."""
    return same_song.union(_song_keys(context), track_ids)


def _covered(context: Context, track_ids: Iterable[int]) -> int:
    """How many tracks of the target these tracks cover as songs; without a target, how many tracks they are."""
    ids = list(track_ids)
    edition = next((item for item in context.editions if item.id == context.target_release_id), None)
    if edition is None:
        return len(set(ids))
    keys = _song_keys(context)
    have = same_song.union(keys, ids)
    return sum(1 for track in edition.tracks if keys.get(track.id, frozenset()) & have)


def split_completing(
    context: Context, current: list[CurrentFile], fresh: list[Planned]
) -> tuple[list[Planned], list[Planned], list[CurrentFile]]:
    """Which new files complete the album and which replace what it has (the owner's answer of 19.09.2026).

    Returns ``(adds, swaps, old)``: ``adds`` are files for a song the album has no file of (and files without a
    track), ``swaps`` files for a song it has (same recording, or same name, ``same_song``), and ``old`` the album's
    files those swaps would replace. A file of the album that no swap covers stays, so a download with fewer tracks
    never takes one away.
    """
    held = {recording for item in current for recording in _recordings(context, item.track_ids)}
    adds: list[Planned] = []
    swaps: list[Planned] = []
    for item in fresh:
        recordings = _recordings(context, [item.track_id]) if item.track_id is not None else set()
        if recordings and recordings & held:
            swaps.append(item)
        else:
            adds.append(item)
    swapped = {recording for item in swaps for recording in _recordings(context, [item.track_id])}
    old = [item for item in current if _recordings(context, item.track_ids) & swapped]
    return adds, swaps, old


def _better_step(old: list[CurrentFile], reads: list[tags.Read]) -> bool:
    before = album_quality.step_of_files(item.quality for item in old)
    return mq.rank(_step_of_reads(reads)) < mq.rank(before or mq.UNKNOWN)


def not_worse(
    context: Context, old: list[CurrentFile], reads: list[tags.Read], track_ids: list[int]
) -> dict[str, Any] | None:
    """None when the new files may replace the album's ``old`` ones (decision 20); else what would get worse."""
    if not old:
        return None
    before = album_quality.step_of_files(item.quality for item in old)
    after = _step_of_reads(reads)
    old_ids = [track_id for item in old for track_id in item.track_ids]
    covered_before = _covered(context, old_ids)
    covered_after = _covered(context, track_ids)
    worse_step = mq.rank(after) > mq.rank(before or mq.UNKNOWN)
    if not worse_step and covered_after >= covered_before:
        return None
    return {
        "step_before": before,
        "step_after": after,
        "tracks_before": covered_before,
        "tracks_after": covered_after,
    }


def with_fingerprints(context: Context, found: Found, result: fm.Result) -> tuple[fm.Result, dict[int, str]]:
    """The last fallback (M4.9, decision 38): the open files fingerprinted and looked up at AcoustID; a recording id
    of the answer is read like one of the tags, and the files are matched again against the same release."""
    edition = result.edition
    if edition is None or context.fingerprint_key is None:
        return result, {}
    open_keys = [item.key for item in edition.decisions if item.decision == fm.OPEN]
    if not open_keys:
        return result, {}
    store.set_step(context.download_id, "fingerprinting")
    outcome = fingerprint.identify({key: found.paths[key][0] for key in open_keys}, context.fingerprint_key)
    with SessionLocal() as db:
        if outcome.error_code is not None:
            set_setting(db, fingerprint.SETTING_LAST_ERROR, outcome.error_code)
        elif outcome.recordings:
            set_setting(db, fingerprint.SETTING_LAST_ERROR, "")
            set_setting(db, fingerprint.SETTING_LAST_OK, store.now().isoformat())
        db.commit()
    if not any(outcome.recordings.values()):
        return result, outcome.states
    printed: set[int] = set()
    audio: list[fm.AudioFile] = []
    for file in found.audio:
        recordings = outcome.recordings.get(file.key)
        if recordings and not file.tags.get("musicbrainz_trackid"):
            printed.add(file.key)
            file = fm.AudioFile(file.key, file.path, file.duration_ms, {**file.tags, "musicbrainz_trackid": recordings})
        audio.append(file)
    again = fm.match(
        audio,
        context.editions,
        target_id=context.target_release_id,
        chosen_id=edition.edition_id,
        others=context.others,
        album_name=context.album.album_title,
    )
    if again.edition is None:
        return result, outcome.states
    decisions = tuple(
        fm.Decision(item.key, item.decision, item.track_id, "fingerprint", item.proposal, item.reading)
        if item.key in printed and item.decision == fm.FILED and item.via == "id"
        else item
        for item in again.edition.decisions
    )
    edition_result = fm.EditionResult(again.edition.edition_id, decisions, again.edition.missing)
    return fm.Result(edition_result, result.reason, result.single_file), outcome.states


def record_rows(
    context: Context, found: Found, result: fm.Result, prints: dict[int, str] | None = None
) -> dict[int, int]:
    """Every audio file as a row with its decision before anything moves. Returns key to row id. Rows of an earlier
    round keep their id and a decision the owner or a filing made."""
    decisions = {item.key: item for item in (result.edition.decisions if result.edition else ())}
    by_path = {row.path: row for row in context.stored}
    moment = store.now()
    ids: dict[int, int] = {}
    with SessionLocal() as db:
        download = db.get(Download, context.download_id)
        if download is None:
            return ids
        if result.edition is not None:
            download.release_id = result.edition.edition_id
        for file in found.audio:
            read = found.reads[file.key]
            decision = decisions.get(file.key)
            existing = by_path.get(file.path)
            row = db.get(DownloadAudioFile, existing.id) if existing is not None else None
            if row is None:
                row = DownloadAudioFile(download_id=context.download_id, path=file.path)
                db.add(row)
            row.size = found.sizes[file.key]
            row.audio = read.audio.as_dict() if read.audio is not None else None
            row.tags = {name: values[:10] for name, values in read.tags.items()}
            if prints and file.key in prints:
                row.fingerprint = {"state": prints[file.key]}
            if decision is not None:
                row.reading = decision.reading.as_dict()
            if existing is not None and (existing.placed or existing.decision in ("placing", "not_filed")):
                db.flush()
                ids[file.key] = row.id
                continue
            if decision is None:
                row.decision, row.track_id, row.via, row.proposal = fm.OPEN, None, None, None
            else:
                row.decision = decision.decision
                row.track_id = decision.track_id
                row.via = decision.via
                row.proposal = list(decision.proposal) or None
                row.other_title_id = decision.other_title_id
            db.flush()
            ids[file.key] = row.id
        download.updated_at = moment
        db.commit()
    return ids


# --- Filing --------------------------------------------------------------------------------------------------------- #


@dataclass
class Spot:
    root: Path
    artist: str
    album: str
    folder: Path

    @property
    def relative(self) -> str:
        return f"{self.artist}/{self.album}"


def prepare_spot(context: Context, root: Path) -> Spot:
    """The album folder, made and stored on the version before the first file moves: a stop in the middle finds the
    same folder again instead of taking its own files for a stranger's (decision 16)."""
    with SessionLocal() as db:
        artist, album = album_folder(db, context, root)
    artist_path = _ensure_folder(root / artist, root)
    folder = _ensure_folder(artist_path / album, root)
    spot = Spot(root=root, artist=artist, album=album, folder=folder)
    if not context.album_relative:
        with SessionLocal() as db:
            version = db.get(Version, context.version_id)
            if version is not None and version.source_id is None and not version.relative_path:
                version.root_folder, version.relative_path = str(root), spot.relative
                db.commit()
    return spot


def _mark(row_id: int, decision: str, **values: Any) -> None:
    with SessionLocal() as db:
        db.execute(update(DownloadAudioFile).where(DownloadAudioFile.id == row_id).values(decision=decision, **values))
        db.commit()


@dataclass(frozen=True)
class Planned:
    key: int
    row_id: int
    #: None for a file filed without a track (``loose``).
    track_id: int | None
    via: str | None


def _track(context: Context, edition_id: int, track_id: int) -> fm.Track | None:
    edition = next((item for item in context.editions if item.id == edition_id), None)
    return next((item for item in edition.tracks if item.id == track_id), None) if edition else None


def target_tracks(context: Context, edition: fm.Edition) -> tuple[fm.Edition | None, dict[int, int]]:
    """The album's target edition and, per track of the filed edition, the track of the target that is the same song.

    Empty when the filed edition is the target, or the album has none. The owner's answer of 20.09.2026: a file that
    completes an album takes the name and the tags of the target, so an album is not half numbered one way and half
    the other.
    """
    target = next((item for item in context.editions if item.id == context.target_release_id), None)
    if target is None or target.id == edition.id:
        return None, {}
    keys = _song_keys(context)
    mapping: dict[int, int] = {}
    for track in edition.tracks:
        song = keys.get(track.id, frozenset())
        if not song:
            continue
        match = next((item for item in target.tracks if keys.get(item.id, frozenset()) & song), None)
        if match is not None:
            mapping[track.id] = match.id
    return target, mapping


def named_after(
    edition: fm.Edition, target: fm.Edition | None, mapping: dict[int, int], track_id: int | None
) -> tuple[fm.Edition, int | None]:
    """The edition and track a file is named and tagged after: the target when it has this song, else its own."""
    if target is not None and track_id is not None and track_id in mapping:
        return target, mapping[track_id]
    return edition, track_id


def _file_name(
    context: Context,
    edition: fm.Edition,
    planned: Planned,
    found: Found,
    formats: dict[int, str],
    credit: str,
    track_id: int | None = None,
) -> str:
    source, _unpacked = found.paths[planned.key]
    extension = source.suffix.lower()
    wanted_track = track_id if track_id is not None else planned.track_id
    if wanted_track is None:
        # A file that fits no track keeps its own name (decision 32).
        return naming._cut_bytes(naming.clean(source.stem), naming.MAX_PART_BYTES - 40) + extension
    track = next(item for item in edition.tracks if item.id == wanted_track)
    facts = naming_music.TrackFacts(
        medium=track.medium,
        position=track.position,
        title=track.name,
        artist_name=credit,
        medium_format=formats.get(track.medium),
        quality=tags.quality_name(found.reads[planned.key].audio),
        original_filename=source.stem,
    )
    return naming_music.file_name(context.naming, context.album, facts, extension, several_media=edition.media > 1)


def _medium_formats(db: OrmSession, release_id: int) -> dict[int, str]:
    return {
        row.position: row.format
        for row in db.scalars(select(ReleaseMedium).where(ReleaseMedium.release_id == release_id))
        if row.format
    }


def _cover(context: Context, release_mbid: str | None, size: str) -> tuple[bytes, str] | None:
    if not context.covers_enabled:
        return None
    try:
        return asyncio.run(covers.artwork(release_mbid, context.group_mbid, size))
    except Exception:  # noqa: BLE001 - a cover never stops filing
        logger.info("Download %d: the cover could not be fetched", context.download_id)
        return None


def recycle_current(old: list[CurrentFile], spot: Spot) -> int:
    """Every old file of the album into the recycle folder, each with its row gone (decision 20, E11). Returns how
    many went."""
    moment = store.now()
    moved = 0
    for item in old:
        parts = [part for part in item.relative_path.replace("\\", "/").split("/") if part]
        path = spot.folder.joinpath(*parts) if parts else None
        if path is not None and os.path.lexists(path) and files.strictly_inside(path, spot.folder):
            try:
                files.recycle(path, spot.root, moment)
            except (OSError, files.FileProblem) as exc:
                raise Problem("import_failed", reason="recycle_failed") from exc
        with SessionLocal() as db:
            db.execute(delete(TrackFile).where(TrackFile.id == item.id))
            db.commit()
        moved += 1
    return moved


def _medium_folder_for(target: Path, spot: Spot) -> bool:
    """Make the folder of the medium a name asks for (``CD 02/03 Title.flac``). False when that is not possible or
    the name would leave the album folder; the file then stays open instead of landing somewhere else."""
    if target.parent == spot.folder:
        return True
    if spot.folder not in target.parents or ".." in target.relative_to(spot.folder).parts:
        return False
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return not files.is_link(target.parent)


def drop_empty_medium_folders(relative_paths: list[str], spot: Spot) -> int:
    """The folders of media that held these files go when nothing is left in them; anything in them keeps them.

    Only a real folder directly below the album folder, never the album folder itself. Found on 20.09.2026: a
    newer version of an album left ``Digital Media 02`` behind, empty.
    """
    gone = 0
    for name in {path.replace("\\", "/").split("/")[0] for path in relative_paths if "/" in path.replace("\\", "/")}:
        folder = spot.folder / name
        if name in ("", ".", "..") or files.is_link(folder) or not folder.is_dir():
            continue
        try:
            if any(folder.iterdir()):
                continue
            folder.rmdir()
            gone += 1
        except OSError:
            logger.info("An emptied folder of a medium stays; it could not be removed")
    return gone


def place_one(
    context: Context,
    found: Found,
    spot: Spot,
    planned: Planned,
    name: str,
    wanted: dict[str, list[str]] | None,
    cover: tuple[bytes, str] | None,
) -> bool:
    """File one audio file. Returns whether it lies at its target and is recorded."""
    source, from_archive = found.paths[planned.key]
    target = spot.folder / name
    if not _medium_folder_for(target, spot):
        logger.info("Download %d: the folder of a medium could not be made; the file stays open", context.download_id)
        return False
    if os.path.lexists(target):
        logger.info(
            "Download %d: a file of the album folder has the name already; the file stays open", context.download_id
        )
        return False
    _mark(planned.row_id, "placing", target=name, track_id=planned.track_id)
    write = context.write_tags and wanted is not None
    try:
        if from_archive:
            placed = files.place_unpacked(source, spot.folder, name)
        else:
            placed = files.place(source, spot.folder, name, protocol=context.protocol, link=not write)
    except OSError, files.FileProblem:
        logger.info("Download %d: an audio file could not be placed", context.download_id)
        _mark(planned.row_id, fm.OPEN, target=None)
        return False
    tag_state = "download" if wanted is None else tag_writing.state_of(placed.partial, wanted, cover, enabled=write)
    try:
        os.replace(placed.partial, target)
    except OSError:
        files.undo(placed, source)
        _mark(planned.row_id, fm.OPEN, target=None)
        return False
    if placed.copied and context.protocol == "usenet" and not from_archive:
        try:
            source.unlink()
        except OSError:
            logger.info("Download %d: a copied Usenet file stays in the job", context.download_id)
    _record_file(context, found, planned, name, target, tag_state)
    return True


def _record_file(context: Context, found: Found, planned: Planned, name: str, target: Path, tag_state: str) -> None:
    moment = store.now()
    read = found.reads[planned.key]
    with SessionLocal() as db:
        row = TrackFile(
            version_id=context.version_id,
            track_id=planned.track_id,
            relative_path=name,
            size=target.stat().st_size,
            quality=tags.quality_name(read.audio),
            audio=read.audio.as_dict() if read.audio is not None else None,
            track_ids=[planned.track_id] if planned.track_id is not None else None,
            added_at=moment,
            updated_at=moment,
            tags_state=tag_state,
            tags_written_at=moment if tag_state == "written" else None,
        )
        db.add(row)
        db.flush()
        db.execute(
            update(DownloadAudioFile)
            .where(DownloadAudioFile.id == planned.row_id)
            .values(
                decision="filed" if planned.track_id is not None else LOOSE,
                track_file_id=row.id,
                target=None,
                via=planned.via,
                track_id=planned.track_id,
            )
        )
        db.commit()


STAGING_PREFIX = ".nexcrate-staging-"


def _staging(spot: Spot, download_id: int) -> Path:
    return spot.folder / f"{STAGING_PREFIX}{download_id}"


def clear_staging(spot: Spot, download_id: int) -> None:
    """The staging folder of an upgrade goes; only a real folder of this download inside the album folder."""
    stage = _staging(spot, download_id)
    if not os.path.lexists(stage) or files.is_link(stage) or not stage.is_dir():
        return
    if files.strictly_inside(stage, spot.folder):
        shutil.rmtree(stage)


def replace_album(
    context: Context,
    found: Found,
    spot: Spot,
    old: list[CurrentFile],
    named: list[tuple[Planned, str, dict[str, list[str]] | None]],
    cover: tuple[bytes, str] | None,
) -> int:
    """An upgrade (decision 20, E11) that never leaves the album with fewer files (finding of the review): every new
    file is first copied or linked into a staging folder in the album folder, never moved, its tags written there;
    only when all of them are there do the old files go into the recycle folder and the new ones take their names.
    A failure while staging leaves the album as it was. A stop at any point: the job still holds every source, the
    next round clears the staging folder and starts again; a file already renamed is taken over by its ``placing``
    row. Returns how many files were filed."""
    stage = _staging(spot, context.download_id)
    clear_staging(spot, context.download_id)
    staged: list[tuple[Planned, str, str]] = []
    try:
        stage.mkdir()
        for item, name, wanted in named:
            source, _from_archive = found.paths[item.key]
            write = context.write_tags and wanted is not None
            # A name may carry the folder of its medium; two media can hold a file of the same name.
            (stage / name).parent.mkdir(parents=True, exist_ok=True)
            placed = files.place(source, stage, name, protocol="torrent", link=not write)
            state = "download" if wanted is None else tag_writing.state_of(placed.partial, wanted, cover, enabled=write)
            os.replace(placed.partial, stage / name)
            staged.append((item, name, state))
    except (OSError, files.FileProblem) as exc:
        clear_staging(spot, context.download_id)
        raise Problem("import_failed", reason="transfer_failed") from exc
    recycle_current(old, spot)
    filed = 0
    for item, name, state in staged:
        target = spot.folder / name
        if not _medium_folder_for(target, spot):
            _mark(item.row_id, fm.OPEN, target=None)
            continue
        if os.path.lexists(target):
            logger.info("Download %d: a file of the album folder has the name; it stays open", context.download_id)
            _mark(item.row_id, fm.OPEN, target=None)
            continue
        _mark(item.row_id, "placing", target=name, track_id=item.track_id)
        try:
            os.replace(stage / name, target)
        except OSError:
            _mark(item.row_id, fm.OPEN, target=None)
            continue
        _record_file(context, found, item, name, target, state)
        filed += 1
    clear_staging(spot, context.download_id)
    drop_empty_medium_folders([item.relative_path for item in old], spot)
    return filed


def adopt_placing(
    context: Context,
    found: Found,
    spot: Spot,
    wanted_of: Callable[[int | None], dict[str, list[str]] | None] = lambda _track_id: None,
) -> set[str]:
    """A round after a stop between moving a file and recording it (decision 19): a file at its target is taken over,
    a finished temporary file whose source is gone gets its name. Returns the paths taken over.

    The tags of a taken-over file are written once more when writing is on (the stop may have come before or after
    they were written; writing is idempotent), so ``tags_state`` says what the file holds (finding of the review)."""
    adopted: set[str] = set()
    keys = {file.path: file.key for file in found.audio}
    for row in context.stored:
        if row.decision != "placing" or not row.target:
            continue
        target = spot.folder / row.target
        partial = target.with_name(target.name + files.PARTIAL_SUFFIX)
        key = keys.get(row.path)
        source_there = key is not None and found.paths[key][0].is_file()
        if not os.path.lexists(target) and os.path.lexists(partial) and not source_there:
            try:
                os.replace(partial, target)
            except OSError:
                continue
        if os.path.lexists(target) and not files.is_link(target):
            planned = Planned(key=key or 0, row_id=row.id, track_id=row.track_id, via="position")
            read = tags.read(target)
            reads = dict(found.reads)
            reads[planned.key] = read
            proxy = Found(found.audio, reads, found.paths, found.sizes, found.job, found.unpacked, found.cue_sheet)
            wanted = wanted_of(row.track_id)
            state = (
                "download" if wanted is None else tag_writing.state_of(target, wanted, None, enabled=context.write_tags)
            )
            _record_file(context, proxy, planned, row.target, target, state)
            adopted.add(row.path)
            logger.info("Download %d: a file placed before a stop is taken over", context.download_id)
            continue
        if os.path.lexists(partial) and source_there and not files.is_link(partial):
            try:
                partial.unlink()
            except OSError:
                logger.info("Download %d: a temporary file of a stopped round stays", context.download_id)
        # Not there: this round files it again as it decides now.
        _mark(row.id, fm.OPEN, target=None)
    return adopted


def _folder_image(context: Context, spot: Spot, found: Found, release_mbid: str | None) -> None:
    """``folder.jpg`` in the album folder (decision 28): the archive's cover, else a cover of the download. Never
    replaces one that is there. Never raises."""
    target = spot.folder / FOLDER_IMAGE
    if os.path.lexists(target):
        return
    image = _cover(context, release_mbid, covers.FOLDER_SIZE)
    try:
        if image is not None and image[1] == "image/jpeg":
            temporary = target.with_name(target.name + files.PARTIAL_SUFFIX)
            temporary.write_bytes(image[0])
            os.replace(temporary, target)
            return
        own = [
            path
            for path in found.images
            if path.stem.casefold() in _COVER_NAMES and path.suffix.lower() in (".jpg", ".jpeg") and path.is_file()
        ]
        if own:
            best = max(own, key=lambda path: path.stat().st_size)
            if best.stat().st_size <= covers.MAX_BYTES:
                temporary = target.with_name(target.name + files.PARTIAL_SUFFIX)
                temporary.write_bytes(best.read_bytes())
                os.replace(temporary, target)
    except OSError:
        logger.info("Download %d: folder.jpg could not be written", context.download_id)


# --- The whole run ------------------------------------------------------------------------------------------------ # #


def _claim(download_id: int, states: tuple[str, ...]) -> bool:
    with SessionLocal() as db:
        result = db.execute(
            update(Download)
            .where(Download.id == download_id, Download.state.in_(states))
            .values(state="importing", updated_at=store.now())
        )
        db.commit()
    return bool(getattr(result, "rowcount", 0))


def _back_to(download_id: int, state: str) -> None:
    with SessionLocal() as db:
        db.execute(
            update(Download).where(Download.id == download_id, Download.state == "importing").values(state=state)
        )
        db.commit()


def record_problem(download_id: int, problem: Problem) -> None:
    moment = store.now()
    with SessionLocal() as db:
        row = db.get(Download, download_id)
        if row is None or row.state != "importing":
            return
        row.state, row.problem_code, row.problem_values = "problem", problem.code, dict(problem.values)
        row.updated_at = moment
        if problem.code in REFUSED:
            store.block(db, row, problem.code, moment)
            store.add_history(db, row, "failed", problem.code, moment)
        store.follow(db, row, moment)
        db.commit()
    reason = problem.values.get("reason")
    logger.info(
        "Album download %d could not be filed: %s%s", download_id, problem.code, f" ({reason})" if reason else ""
    )


def _waiting(context: Context) -> None:
    """No tracks yet: wait, and after ``WAIT_FOR_TRACKS`` the problem (decision 4)."""
    from ..music import loading as music_loading

    completed = context.completed_at
    if completed is not None and store.now() - completed > WAIT_FOR_TRACKS:
        raise Problem("album_tracks_missing")
    music_loading.hurry_album(context.title_id)
    raise WaitingForTracks


def run(download_id: int, manual: Manual | None = None) -> None:
    """File an album download, in the calling thread. Without ``manual`` the download must be ``completed``; with it
    ``importing`` already."""
    if manual is None and not _claim(download_id, ("completed",)):
        return
    with SessionLocal() as db:
        context = load(db, download_id)
    if context is None:
        return
    if context.version_id is None or context.definition_id is None:
        record_problem(download_id, Problem("import_failed", reason="version_gone"))
        return
    if context.version_fed:
        record_problem(download_id, Problem("import_failed", reason="version_fed_by_source"))
        return
    root: Path | None = None
    try:
        if not context.editions:
            _waiting(context)
        root = _root(context)
        store.set_step(download_id, "matching")
        found = gather(context, root)
        _file_all(context, found, root, manual)
    except WaitingForTracks:
        store.set_step(download_id, "waiting_tracks")
        _back_to(download_id, "completed")
        return
    except StillUnpacking:
        _back_to(download_id, "completed")
    except Problem as problem:
        record_problem(download_id, problem)
    finally:
        if root is not None:
            unpacking.remove(root, download_id)
    store.set_step(download_id, None)


def _decide(context: Context, found: Found, manual: Manual | None) -> fm.Result:
    if manual is not None:
        return fm.match(
            found.audio,
            context.editions,
            target_id=context.target_release_id,
            chosen_id=manual.release_id or context.download_release_id,
            others=context.others,
            album_name=context.album.album_title,
        )
    chosen = context.download_release_id if any(row.placed for row in context.stored) else None
    return fm.match(
        found.audio,
        context.editions,
        target_id=context.target_release_id,
        chosen_id=chosen,
        others=context.others,
        album_name=context.album.album_title,
        cue_sheet=found.cue_sheet,
    )


def _planned(
    context: Context, found: Found, result: fm.Result | None, rows: dict[int, int], manual: Manual | None
) -> list[Planned]:
    """What this round files: clear files, or the owner's choice; never a file that lies in the album folder."""
    done = {row.path for row in context.stored if row.placed}
    by_key = {file.key: file for file in found.audio}
    planned: list[Planned] = []
    if manual is not None:
        for key, row_id in rows.items():
            if by_key[key].path in done or row_id not in manual.chosen:
                continue
            choice = manual.chosen[row_id]
            if choice == LOOSE:
                planned.append(Planned(key, row_id, None, "owner"))
            elif isinstance(choice, int):
                planned.append(Planned(key, row_id, choice, "owner"))
        return planned
    assert result.edition is not None
    for decision in result.edition.decisions:
        if decision.decision == fm.FILED and by_key[decision.key].path not in done:
            planned.append(Planned(decision.key, rows[decision.key], decision.track_id, decision.via))
    return planned


def _not_filed_by_owner(manual: Manual | None) -> list[int]:
    return [row_id for row_id, choice in (manual.chosen if manual else {}).items() if choice is None]


def _file_all(context: Context, found: Found, root: Path, manual: Manual | None) -> None:
    moved_before = not found.audio
    result = None if moved_before else _decide(context, found, manual)
    if result is not None and result.single_file:
        raise Problem("album_single_file")
    if result is not None and result.edition is None:
        raise Problem("album_tracks_missing") if result.reason.get("code") == "no_tracks" else Problem("no_audio")
    rows: dict[int, int] = {}
    prints: dict[int, str] = {}
    if result is not None and manual is None:
        result, prints = with_fingerprints(context, found, result)
    if result is not None:
        rows = record_rows(context, found, result, prints)
        with SessionLocal() as db:
            context = load(db, context.download_id) or context
    edition_id = result.edition.edition_id if result is not None and result.edition else context.download_release_id
    edition = next((item for item in context.editions if item.id == edition_id), None)
    if edition is None:
        raise Problem("no_audio")
    planned = _planned(context, found, result, rows, manual) if result is not None else []
    for row_id in _not_filed_by_owner(manual):
        _mark(row_id, "not_filed", target=None)
    spot = prepare_spot(context, root)
    # A file that completes an album is named and tagged after the album's target edition, not after the edition it
    # was downloaded in (the owner's answer of 20.09.2026). A song the target does not have keeps its own, and a
    # download that fills an empty album keeps the names of its own release (decision of M4).
    own = {row.track_file_id for row in context.stored if row.track_file_id is not None}
    current = [item for item in context.current if item.id not in own]
    target, same_songs = target_tracks(context, edition) if current else (None, {})
    with SessionLocal() as db:
        release = db.get(Release, edition.id)
        title = db.get(Title, context.title_id)
        album = tag_writing.album_tags(db, release, title) if release is not None and title is not None else None
        tracks = {row.id: row for row in db.scalars(select(ReleaseTrack).where(ReleaseTrack.release_id == edition.id))}
        formats = _medium_formats(db, edition.id)
        release_mbid = release.mbid if release is not None else None
        album_credit = title.artist_credit if title is not None else None
        target_album = album
        target_tracks_by_id = tracks
        target_formats = formats
        if target is not None:
            target_release = db.get(Release, target.id)
            target_album = (
                tag_writing.album_tags(db, target_release, title)
                if target_release is not None and title is not None
                else None
            )
            target_tracks_by_id = {
                row.id: row for row in db.scalars(select(ReleaseTrack).where(ReleaseTrack.release_id == target.id))
            }
            target_formats = _medium_formats(db, target.id)

    def _row_of(track_id: int | None) -> tuple[Any, dict[str, list[str]] | None]:
        """The track row and the album tags a file gets: the target's when it has this song."""
        if target is not None and track_id is not None and track_id in same_songs:
            return target_tracks_by_id.get(same_songs[track_id]), target_album
        return (tracks.get(track_id) if track_id is not None else None), album

    def wanted_of(track_id: int | None) -> dict[str, list[str]] | None:
        track, tags_of = _row_of(track_id)
        return tag_writing.wanted(tags_of, track) if tags_of is not None and track is not None else None

    adopted = adopt_placing(context, found, spot, wanted_of)
    paths = {file.key: file.path for file in found.audio}
    fresh = [item for item in planned if paths.get(item.key) not in adopted]
    adds, swaps, old = split_completing(context, current, fresh)
    replacing = bool(old) and bool(swaps)
    if replacing:
        swap_reads = [found.reads[item.key] for item in swaps]
        worse = not_worse(context, old, swap_reads, [item.track_id for item in swaps])
        confirmed = manual is not None and "not_better" in manual.confirm
        # A download that completes the album swaps the tracks it has only for a better step; at the same step the
        # files stay (no churn for nothing).
        keep = bool(adds) and not confirmed and not _better_step(old, swap_reads)
        if (worse is not None and not confirmed) or keep:
            if not adds:
                raise Problem("album_not_better", **(worse or {}))
            # Completing (the owner's answer of 19.09.2026): the missing tracks go in, the ones the album has stay.
            logger.info(
                "Album download %d: %d missing tracks completed, %d tracks the album has are kept",
                context.download_id,
                len(adds),
                len(swaps),
            )
            for item in swaps:
                _mark(item.row_id, "not_needed", target=None)
            fresh, swaps, old, replacing = adds, [], [], False
    store.set_step(context.download_id, "filing")
    cover = _cover(context, release_mbid, covers.EMBED_SIZE) if context.write_tags and fresh else None
    named = []
    for item in fresh:
        track, _tags_of = _row_of(item.track_id)
        credit = tag_writing.credit_text((track.artist_credit if track is not None else None) or album_credit)
        naming_edition, naming_track = named_after(edition, target, same_songs, item.track_id)
        name = _file_name(
            context,
            naming_edition,
            item,
            found,
            target_formats if naming_edition is target else formats,
            credit,
            naming_track,
        )
        named.append((item, name, wanted_of(item.track_id)))
    filed_now = 0
    if replacing:
        # Only the swaps go through the staging folder; the added tracks follow once the old files are gone.
        swapping = {item.key for item in swaps}
        swapped = [entry for entry in named if entry[0].key in swapping]
        filed_now = replace_album(context, found, spot, old, swapped, cover)
        for item, name, wanted in named:
            if item.key not in swapping and place_one(context, found, spot, item, name, wanted, cover):
                filed_now += 1
    else:
        for item, name, wanted in named:
            if place_one(context, found, spot, item, name, wanted, cover):
                filed_now += 1
    _folder_image(context, spot, found, release_mbid)
    finish(context, edition, spot, filed_now)
    if filed_now and context.version_id is not None:
        _align(context, spot)
    if context.version_id is not None and (filed_now or any(row.placed for row in context.stored)):
        # After the commit: a failing write never undoes the filing (decision 37).
        companions_album.write_version(context.version_id)
    if filed_now:
        # After the commit, in a thread of its own: a media server never holds up or fails an import.
        mediaserver_notify.request("music", [spot.folder])
    after_finish(context.download_id, found)


def _align(context: Context, spot: Spot) -> None:
    """Files of more than one release follow the target in names and tags (``music.align``); after the commit of the
    filing, so a failure here never undoes it."""
    try:
        with SessionLocal() as db:
            version = db.get(Version, context.version_id)
            if version is not None:
                align.align(db, version, spot.folder, context.naming, context.album, write_tags=context.write_tags)
                db.commit()
    except OSError:
        logger.warning("Download %d: the album's files could not all follow the target release", context.download_id)


def finish(context: Context, edition: fm.Edition, spot: Spot, filed_now: int) -> str:
    """The version and the download after filing (decisions 21, 23, 31). Returns the download's state."""
    moment = store.now()
    with SessionLocal() as db:
        download = db.get(Download, context.download_id)
        version = db.get(Version, context.version_id) if context.version_id is not None else None
        if download is None or download.state != "importing" or version is None:
            return "changed"
        rows = list(db.scalars(select(DownloadAudioFile).where(DownloadAudioFile.download_id == download.id)))
        filed = [row for row in rows if row.track_file_id is not None]
        # Every file not in the album folder that nobody left out waits for the owner (decisions 31 and 34).
        # A file for a track the album keeps (``not_needed``, completing) waits for nobody either.
        open_rows = [
            row for row in rows if row.track_file_id is None and row.decision not in ("not_filed", "not_needed")
        ]
        for row in open_rows:
            if row.decision not in (fm.OPEN, fm.OTHER_ALBUM):
                row.decision = fm.OPEN
        if filed:
            version.root_folder = str(spot.root)
            version.relative_path = spot.relative
            version.actual_release_id = edition.id
            version.file_ref = f"nexcrate:{download.id}"
            version.release_title = (download.release_title or "")[:1024] or None
            artist = db.get(Artist, context.artist_id) if context.artist_id is not None else None
            if artist is not None and not artist.folder:
                artist.folder = spot.artist
            music_store._count_tracks(db, version)
            qualities = list(db.scalars(select(TrackFile.quality).where(TrackFile.version_id == version.id)))
            album_quality.apply(version, album_quality.step_of_files(qualities), context.rules)
            version.updated_at = moment
        held = {
            track_id
            for item in db.scalars(select(TrackFile).where(TrackFile.version_id == version.id))
            for track_id in (item.track_ids or [])
        }
        missing = [track.id for track in edition.tracks if track.id not in held]
        download.filed_count = len(filed)
        download.open_count = len(open_rows)
        download.absent_count = len(missing)
        if open_rows:
            download.state, download.problem_code = "problem", "files_unassigned"
            download.problem_values = {"filed": len(filed), "open": len(open_rows), "missing": len(missing)}
        else:
            download.state, download.problem_code, download.problem_values = "imported", None, None
            download.imported_at = moment
            download.imported_path = spot.relative
            download.progress, download.remaining_seconds = 100.0, 0
            detail = f"filed={len(filed)} missing={len(missing)} release={edition.id} download={download.id}"
            store.add_history(db, download, "album_filed", detail, moment)
        download.updated_at = moment
        store.follow(db, download, moment)
        db.commit()
        state = download.state
    logger.info(
        "Album download %d: %d files filed now, %d open, %d tracks missing, state %s",
        context.download_id,
        filed_now,
        len(open_rows),
        len(missing),
        state,
    )
    return state


def after_finish(download_id: int, found: Found) -> None:
    """After an imported album: SABnzbd's job folder goes, then the job leaves its history. Never raises."""
    with SessionLocal() as db:
        context = load(db, download_id)
        row = db.get(Download, download_id)
        state = row.state if row is not None else None
    if context is None or state != "imported" or context.protocol != "usenet":
        return
    try:
        asyncio.run(_clean_usenet(context, found))
    except (downloaders.ClientError, OSError) as exc:
        logger.info("Download %d: cleaning up in the client failed: %s", download_id, type(exc).__name__)


async def _clean_usenet(context: Context, found: Found) -> None:
    target = context.target
    if target is None or not downloaders.is_usenet(target.kind):
        return
    folder = found.job if found.job.is_dir() else found.job.parent
    mount = folders.containing_mount(files.resolved(folder) or folder)
    keep = Path(context.root_folder) if context.root_folder else folder
    removed = mount is not None and files.remove_job_folder(folder, category=target.category, mount=mount, keep=keep)
    if not removed:
        logger.info("Download %d: the job folder is not one to delete; it stays in the client", context.download_id)
        return
    async with downloaders.open_client(target) as client:
        await client.remove_imported(context.client_download_id)
    logger.info("Download %d: the job folder is deleted and the job left the client", context.download_id)


def clean_after_owner(download_id: int) -> None:
    """After "Rest nicht ablegen" (decision 34): the unpack folder and SABnzbd's job folder go. Never raises."""
    with SessionLocal() as db:
        context = load(db, download_id)
    if context is None:
        return
    try:
        root = _root(context)
        unpacking.remove(root, download_id)
    except Problem:
        root = None
    if context.protocol != "usenet":
        return
    try:
        job = _local_job(context)
    except Problem:
        return
    found = Found(audio=[], reads={}, paths={}, sizes={}, job=job, unpacked=None, cue_sheet=False)
    try:
        asyncio.run(_clean_usenet(context, found))
    except (downloaders.ClientError, OSError) as exc:
        logger.info("Download %d: cleaning up in the client failed: %s", download_id, type(exc).__name__)
