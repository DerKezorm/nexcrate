"""Filing a finished series download (S4.4 and S4.5).

``importing.run`` hands a download with a scope here. The flow:

1. **Checks** as for movies: the version exists, no source feeds it, the job folder is visible, nothing dangerous.
2. **The series folder:** the stored one of the version (``root_folder`` and ``relative_path``), else a new one below
   the version's ``root_folder`` when it is writable, or the version folder, named by the series folder pattern. Its
   name is fixed once the first file lies in it (decision 15). Archives are unpacked next to it, every first volume.
3. **Assigning** (``episodes.assign``), or the owner's assignment for ``POST /api/downloads/{id}/assign``. Every video
   is kept in ``download_files`` with its decision before anything moves, so the dialog can show them.
4. **Filing file by file**, each recorded in its own transaction: the season folder (named once, decision 15), the
   name after the version's numbering, a replaced file into the recycle folder of the root folder. A file is not
   filed when the episode has a file that is not worse by now (decision 25), when the file it would replace holds an
   episode this download does not bring or does not file (decision 26), or when a video nexcrate does not know lies
   in the season folder for the same episode. A multi-episode file goes to the recycle folder only with its last
   episode replaced.
   The media data is read before the video moves: the name and the stored file carry its quality, codec and audio
   (the owner's answer of 17.09.2026).
   **Safe against a stop at any moment:** before a video moves, its row says ``placing`` and the download's
   ``imported_path`` names the target; the file is recorded right after its final rename, the subtitles follow. The
   next round finds a ``placing`` row and takes over the file at the target (or finishes its temporary name) instead
   of losing it. A file that fails does not stop the others; a failure of the folder does.
5. **Afterwards:** episodes without a file are ``missing`` (a line in the history); open videos while an episode is
   missing make the problem ``files_unassigned``; otherwise the download is ``imported``, SABnzbd's job folder goes.

⚠️ Every path written or deleted lies in the series folder or the job folder after resolving links; the names of the
series and season folders come only from the patterns. Logs carry ids and counts, never names or paths.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session as OrmSession

from ... import crypto
from ...db import SessionLocal
from ...models import (
    AlternateTitle,
    Download,
    DownloadClient,
    DownloadEpisode,
    DownloadFile,
    Episode,
    EpisodeFile,
    EpisodeNumber,
    EpisodeVersion,
    ExtraFile,
    SeasonFolder,
    Title,
    TitleAlias,
    Version,
    VersionDefinition,
    XemName,
)
from .. import downloaders, folder_rules, folders, media, naming, naming_series, releases, schreibweisen
from ..automatic import replacement
from ..mediaservers import notify as mediaserver_notify
from ..profiles import store as profile_store
from ..releases import parser as release_parser
from ..releases import qualities as movie_qualities
from ..releases import qualities_series, series_decision
from ..series import names, release_match
from ..series.parts import as_whole
from ..subtitles import placing as subtitle_placing
from ..subtitles import settings as subtitle_settings
from . import episodes, files, store, unpacking

logger = logging.getLogger("nexcrate.import")

#: At most this many videos and files of a download are looked at ("Sicherheit").
MAX_VIDEOS = 2_000
MAX_FILES = 10_000
#: Durations are read for downloads up to this many videos; a larger one files without the sample check by runtime.
MAX_DURATION_READS = 100
#: The path prefix of a video that came out of the download's archives.
UNPACKED_PREFIX = "unpacked:"
#: Subtitle limits per video and per download (decision 29).
SUBTITLES_PER_VIDEO = 50
SUBTITLES_PER_DOWNLOAD = 1_000
#: The decision of a video while it moves, and of one not filed because the episode's file is not worse.
PLACING = "placing"
SKIPPED = "skipped"
#: Failures of a single file; any other reason (no space, a folder nexcrate cannot write) stops the whole round.
FILE_REASONS = frozenset(
    {
        "destination_exists", "foreign_file", "transfer_failed", "source_outside", "episode_gone", "recycle_outside",
        "file_truncated",
    }
)


class Problem(Exception):
    def __init__(self, code: str, **values: object) -> None:
        super().__init__(code)
        self.code = code
        self.values = dict(values)


class StillUnpacking(Exception):
    """The client still works on the files."""


# --- What an import knows ----------------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CurrentFile:
    id: int
    relative_path: str
    size: int
    quality: str | None
    release_title: str | None
    release_type: str | None
    #: Every episode the file holds in the version.
    episode_ids: tuple[int, ...]
    #: The download that filed it, when nexcrate did.
    download_id: int | None
    #: The languages stored with it, by name.
    languages: tuple[str, ...] = ()
    #: 1 or 2 for a half of a double episode; None for a whole file.
    part: int | None = None


@dataclass(frozen=True)
class Stored:
    """A video recorded for the download before."""

    id: int
    path: str
    size: int
    decision: str
    episode_ids: tuple[int, ...]
    #: The half of a double episode it was read as.
    part: int | None = None


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
    formats: tuple[str, ...]
    languages: tuple[str, ...]
    version_folder: str | None
    root_folder: str | None
    series_relative: str | None
    series: naming_series.SeriesFacts
    naming: naming_series.SeriesNaming
    daily: bool
    numbering: release_match.Numbering
    titles: tuple[str, ...]
    keys: frozenset[str]
    #: Episode id to its action (fills, replaces, confirmed) and its state.
    expected: dict[int, str]
    states: dict[int, str]
    via: str | None
    season_folders: dict[int, str]
    current: dict[int, CurrentFile]
    tvdb: dict[int, tuple[int, int]]
    names: dict[int, tuple[str, str | None]]
    language: str
    rules: dict[str, Any] | None = field(repr=False)
    original_language: str | None
    subtitles_enabled: bool
    stored: tuple[Stored, ...]
    #: The download's ``imported_path``: while a row is ``placing``, the target of that video below the series folder.
    marker: str | None = None
    #: Every episode file of the version by its path below the series folder, casefolded: the videos nexcrate knows.
    known_paths: frozenset[str] = frozenset()
    #: Episode file id to its recorded subtitles, ``(row id, path below the series folder)``.
    subtitles: dict[int, tuple[tuple[int, str], ...]] = field(default_factory=dict)
    #: What this round filed, for the history's ``data``: per file its episodes, quality, size and what it replaced.
    filed_log: tuple[dict[str, Any], ...] = ()
    #: The second halves of double episodes by their episode. The first half, like any
    #: file, is in ``current``.
    halves: dict[int, CurrentFile] = field(default_factory=dict)


def _titles(db: OrmSession, title: Title) -> tuple[tuple[str, ...], frozenset[str]]:
    texts = [title.title, title.title_en, title.original_title]
    texts += list(db.scalars(select(TitleAlias.text).where(TitleAlias.title_id == title.id)))
    texts += list(db.scalars(select(AlternateTitle.text).where(AlternateTitle.title_id == title.id)))
    if title.tvdb_id:
        texts += list(db.scalars(select(XemName.text).where(XemName.tvdb_id == title.tvdb_id)))
    unique: list[str] = []
    for text in texts:
        if text and text not in unique:
            unique.append(text)
    keys: set[str] = set()
    for text in unique:
        keys.update(schreibweisen.keys(text))
    for stored in (title.search_keys, title.tmdb_search_keys):
        keys.update(part for part in (stored or "").split("|") if part)
    return tuple(unique), frozenset(keys)


def load(db: OrmSession, download_id: int) -> Context | None:
    row = db.get(Download, download_id)
    if row is None or row.scope is None:
        return None
    title = db.get(Title, row.title_id)
    if title is None:
        return None
    client = db.get(DownloadClient, row.client_id) if row.client_id is not None else None
    version = store.version_of(db, row.title_id, row.version_definition_id)
    definition = db.get(VersionDefinition, row.version_definition_id) if row.version_definition_id else None
    target = None
    if client is not None:
        target = downloaders.Target(
            kind=client.kind,
            url=client.url,
            username=client.username or "",
            secret=crypto.decrypt(client.secret) if client.secret else "",
            category=client.category,
        )
    language = names.account_language(db)
    expected: dict[int, str] = {}
    states: dict[int, str] = {}
    for item in db.scalars(select(DownloadEpisode).where(DownloadEpisode.download_id == row.id)):
        expected[item.episode_id] = item.action
        states[item.episode_id] = item.state
    current: dict[int, CurrentFile] = {}
    halves: dict[int, CurrentFile] = {}
    season_folders: dict[int, str] = {}
    subtitle_rows: dict[int, list[tuple[int, str]]] = defaultdict(list)
    known_paths: frozenset[str] = frozenset()
    if version is not None:
        known_paths = frozenset(
            _path_key(path)
            for path in db.scalars(select(EpisodeFile.relative_path).where(EpisodeFile.version_id == version.id))
        )
        links = list(
            db.execute(
                select(EpisodeVersion.episode_id, EpisodeVersion.episode_file_id).where(
                    EpisodeVersion.version_id == version.id, EpisodeVersion.episode_file_id.is_not(None)
                )
            ).tuples()
        )
        by_file: dict[int, list[int]] = defaultdict(list)
        for episode_id, file_id in links:
            by_file[int(file_id)].append(episode_id)
        rows = {item.id: item for item in db.scalars(select(EpisodeFile).where(EpisodeFile.id.in_(list(by_file))))}
        for file_id, episode_ids in by_file.items():
            stored_file = rows.get(file_id)
            if stored_file is None:
                continue
            made_by = stored_file.file_ref or ""
            entry = CurrentFile(
                id=stored_file.id,
                relative_path=stored_file.relative_path,
                size=stored_file.size,
                quality=stored_file.quality,
                release_title=stored_file.release_title,
                release_type=stored_file.release_type,
                episode_ids=tuple(sorted(episode_ids)),
                download_id=int(made_by[9:]) if made_by.startswith("nexcrate:") and made_by[9:].isdigit() else None,
                languages=releases.stored_languages(stored_file.languages),
                part=stored_file.part,
            )
            for episode_id in episode_ids:
                current[episode_id] = entry
        for second in db.scalars(
            select(EpisodeFile).where(
                EpisodeFile.version_id == version.id,
                EpisodeFile.part == 2,
                EpisodeFile.part_of_episode_id.is_not(None),
            )
        ):
            made_by = second.file_ref or ""
            halves[int(second.part_of_episode_id or 0)] = CurrentFile(
                id=second.id,
                relative_path=second.relative_path,
                size=second.size,
                quality=second.quality,
                release_title=second.release_title,
                release_type=second.release_type,
                episode_ids=(int(second.part_of_episode_id or 0),),
                download_id=int(made_by[9:]) if made_by.startswith("nexcrate:") and made_by[9:].isdigit() else None,
                languages=releases.stored_languages(second.languages),
                part=2,
            )
        season_folders = {
            item.season_number: item.name
            for item in db.scalars(select(SeasonFolder).where(SeasonFolder.version_id == version.id))
        }
        for extra in db.scalars(
            select(ExtraFile).where(ExtraFile.version_id == version.id, ExtraFile.episode_file_id.is_not(None))
        ):
            subtitle_rows[int(extra.episode_file_id)].append((extra.id, extra.relative_path))
    profile = profile_store.of_version(db, row.version_definition_id)
    rules = profile_store.series_rules(profile, title.series_type)
    tvdb = {
        number.episode_id: (int(number.season), int(number.episode))
        for number in db.scalars(
            select(EpisodeNumber).where(EpisodeNumber.title_id == title.id, EpisodeNumber.scheme == "tvdb")
        )
        if number.season is not None and number.episode is not None
    }
    episode_names = {
        episode_id: (name or "", name_en)
        for episode_id, name, name_en in db.execute(
            select(Episode.id, Episode.name, Episode.name_en).where(Episode.title_id == title.id)
        ).tuples()
    }
    titles, keys = _titles(db, title)
    stored = tuple(
        Stored(
            item.id,
            item.path,
            item.size,
            item.decision,
            tuple(item.episode_ids or []),
            item.reading.get("part") if isinstance(item.reading, dict) else None,
        )
        for item in db.scalars(select(DownloadFile).where(DownloadFile.download_id == row.id).order_by(DownloadFile.id))
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
        target=target,
        release_title=row.release_title,
        formats=tuple(row.formats or []),
        languages=tuple(row.languages or []),
        # The library rules of the version choose where a new series goes.
        version_folder=folder_rules.folder_for(db, definition, title),
        root_folder=version.root_folder if version is not None else None,
        series_relative=version.relative_path if version is not None else None,
        series=naming_series.SeriesFacts(
            title=title.title or title.title_en or title.original_title or "",
            year=title.year,
            tmdb_id=title.tmdb_id,
            tvdb_id=title.tvdb_id,
            imdb_id=title.imdb_id,
        ),
        naming=naming_series.for_version(db, definition),
        daily=title.series_type == "daily",
        numbering=release_match.load(db, title),
        titles=titles,
        keys=keys,
        expected=expected,
        states=states,
        via=row.match_via,
        season_folders=season_folders,
        current=current,
        halves=halves,
        tvdb=tvdb,
        names=episode_names,
        language=language,
        rules=rules,
        original_language=title.original_language,
        subtitles_enabled=subtitle_settings.load_enabled(db),
        stored=stored,
        marker=row.imported_path,
        known_paths=known_paths,
        subtitles={file_id: tuple(rows) for file_id, rows in subtitle_rows.items()},
    )


def _path_key(path: str) -> str:
    """A path below the series folder as nexcrate compares it: slashes, NFC, casefolded (SMB and Windows ignore case).
    """
    return schreibweisen.nfc(path.replace("\\", "/").strip("/")).casefold()


# --- Paths -------------------------------------------------------------------------------------------------------- #


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
    """The folder the series folder lies in: the stored one, a writable ``root_folder``, else the version folder."""
    if context.root_folder:
        try:
            root, _mount = folders.visible(context.root_folder)
        except folders.NotVisible:
            root = None
        if root is not None and (context.series_relative or folders.is_writable(root)):
            return root
        if context.series_relative:
            raise Problem("import_failed", reason="folder_not_visible")
    if not context.version_folder:
        raise Problem("import_failed", reason="no_folder")
    try:
        folder, _mount = folders.visible(context.version_folder)
    except folders.NotVisible as exc:
        raise Problem("import_failed", reason="folder_not_visible") from exc
    return folder


def _plain_part(name: str) -> bool:
    return bool(name) and name not in (".", "..") and not any(mark in name for mark in ("/", "\\", "\x00"))


def series_folder(context: Context, root: Path) -> tuple[Path, str]:
    """The series folder below ``root`` and its name; the stored name, else one from the pattern."""
    name = context.series_relative or naming_series.series_folder_name(context.naming, context.series)
    parts = [part for part in name.replace("\\", "/").split("/") if part]
    if len(parts) != 1 or not _plain_part(parts[0]):
        raise Problem("import_failed", reason="destination_outside")
    return root / parts[0], parts[0]


def _ensure_folder(path: Path, inside: Path) -> Path:
    try:
        path.mkdir(exist_ok=True)
    except OSError as exc:
        raise Problem("import_failed", reason="folder_not_writable") from exc
    if files.is_link(path) or not files.strictly_inside(path, inside):
        raise Problem("import_failed", reason="destination_outside")
    return files.resolved(path) or path


# --- The videos --------------------------------------------------------------------------------------------------- #


@dataclass
class Found:
    #: Every video, samples and extras marked, so the owner can still assign them.
    videos: list[episodes.Video]
    #: Video key to its real path, and whether it came out of the archives.
    paths: dict[int, tuple[Path, bool]]
    job: Path
    unpacked: Path | None
    #: How many videos the download holds, for the subtitles of a single video.
    count: int
    #: Video key to its media data as ``gather`` read it for the runtime; the name and the quality use it again.
    reads: dict[int, media.Read] = field(default_factory=dict)


def _relative(path: Path, origin: Path, unpacked: bool) -> str:
    try:
        text = path.relative_to(origin).as_posix() if path != origin else path.name
    except ValueError:
        text = path.name
    return schreibweisen.nfc((UNPACKED_PREFIX if unpacked else "") + text)[:1024]


def gather(context: Context, root: Path) -> Found:
    """Scan the job, unpack when wanted, and number the videos. Raises ``Problem`` or ``StillUnpacking``."""
    job = _local_job(context)
    if files.below_working_folder(job):
        raise StillUnpacking
    found = files.scan(job, series=True)
    if found.dangerous:
        raise Problem("dangerous_file")
    if files.strictly_inside(root, job) or files.inside(job, root):
        raise Problem("import_failed", reason="source_in_version_folder")
    listed: list[tuple[Path, int, bool, str | None]] = [(path, size, False, None) for path, size in found.videos]
    listed += [(path, size, False, reason) for path, size, reason in found.skipped_videos]
    unpacked: Path | None = None
    if unpacking.wanted(found):
        try:
            unpacked = unpacking.unpack(unpacking.archive_sets(found.archive_files), root, context.download_id)
        except files.FileProblem as exc:
            raise Problem(exc.code, **exc.values) from exc
        inner = files.scan(unpacked, series=True)
        if inner.dangerous:
            unpacking.remove(root, context.download_id)
            raise Problem("dangerous_file")
        listed += [(path, size, True, None) for path, size in inner.videos]
        listed += [(path, size, True, reason) for path, size, reason in inner.skipped_videos]
    if len(listed) > MAX_VIDEOS or found.entries > MAX_FILES:
        raise Problem("too_many_files")
    if not listed:
        raise Problem("no_video")
    counting = [item for item in listed if item[3] in (None, episodes.SKIP_EXTRA_NAME)]
    per_folder: dict[Path, int] = defaultdict(int)
    for path, _size, _unpacked, _skip in counting:
        per_folder[path.parent] += 1
    videos: list[episodes.Video] = []
    paths: dict[int, tuple[Path, bool]] = {}
    reads: dict[int, media.Read] = {}
    read_durations = len(counting) <= MAX_DURATION_READS
    ordered = sorted(listed, key=lambda item: (item[2], str(item[0])))
    for key, (path, size, from_archive, skip) in enumerate(ordered, 1):
        origin = unpacked if from_archive and unpacked is not None else job
        duration = None
        if read_durations and skip in (None, episodes.SKIP_EXTRA_NAME) and media.readable(path):
            answer = media.read(path)
            reads[key] = answer
            value = (answer.media or {}).get("duration_seconds") if answer.media else None
            duration = int(value) if isinstance(value, int | float) and value > 0 else None
        folder_name = path.parent.name if path.parent != origin or origin == job else None
        if path.parent == origin and origin == job and job.is_file():
            folder_name = None
        videos.append(
            episodes.Video(
                key=key,
                path=_relative(path, origin if origin.is_dir() else origin.parent, from_archive),
                size=size,
                duration_seconds=duration,
                folder_name=folder_name,
                folder_videos=per_folder[path.parent],
                skip=skip,
            )
        )
        paths[key] = (path, from_archive)
    return Found(videos, paths, job, unpacked, len(counting), reads)


# --- Deciding ----------------------------------------------------------------------------------------------------- #


def _current_of(entry: CurrentFile) -> releases.CurrentEpisodeFile:
    return releases.CurrentEpisodeFile(
        release_title=entry.release_title,
        name=entry.relative_path,
        quality=entry.quality,
        release_type=entry.release_type,
        size_bytes=as_whole(entry.size, entry.part),
        episode_count=len(entry.episode_ids),
        file_id=entry.id,
        languages=entry.languages,
    )


def file_quality(path: str, release_title: str, original_language: str | None) -> str | None:
    """The quality of a video in Sonarr's names, as Sonarr sums it up: the video's own name when it states source and
    resolution (two qualities in one pack), else the release name, else what the video's name gives at all.

    A bare resolution is no source: ``tvr-friends-s01e01-720p.mkv`` takes Bluray from its release name, not the HDTV the
    parser guesses (23.09.2026: 48 packs skipped as worse, their folders deleted)."""
    stem = PurePosixPath(path.removeprefix(UNPACKED_PREFIX)).stem
    own = releases.parse_series(stem, original_language)
    if (
        own.series.quality.source != movie_qualities.UNKNOWN
        and own.series.quality.resolution
        and release_parser.names_source(stem)
    ):
        return own.quality.name
    release = releases.parse_series(release_title, original_language).quality.name
    if release != "Unknown":
        return release
    return own.quality.name if own.quality.name != "Unknown" else None


def _file_quality(context: Context, path: str) -> str | None:
    return file_quality(path, context.release_title, context.original_language)


def _candidate(
    context: Context, video: episodes.Video, count: int, part: int | None = None
) -> releases.CurrentEpisodeFile:
    """A new file as the rules read it, like the search read its release: formats from the release name, never from a
    file name that lacks them (``example.show.s02e01.1080p.web`` in a German DL pack); the languages the download
    keeps from the search, the indexer's MULTi languages included; the quality as ``_file_quality`` finds it."""
    quality = _file_quality(context, video.path)
    kind = {1: "singleEpisode", 2: "multiEpisode"}.get(count, "multiEpisode")
    if releases.parse_series(context.release_title).series.release_type == "season_pack":
        kind = "seasonPack"
    return releases.CurrentEpisodeFile(
        release_title=context.release_title,
        name=video.path,
        quality=quality,
        size_bytes=as_whole(video.size, part),
        release_type=kind,
        episode_count=count,
        languages=context.languages,
    )


def ranker(context: Context) -> episodes.Ranker | None:
    rules = context.rules
    if rules is None or rules.get("kind") != "series":
        return None

    def rank(video: episodes.Video, reading: episodes.Reading) -> tuple[int, ...]:
        candidate = _candidate(context, video, len(reading.episode_ids) or 1, reading.part)
        _quality, place, score = series_decision.file_standing(rules, candidate, context.original_language)
        return (place, score)

    return rank


def assignment_context(context: Context) -> episodes.Context:
    return episodes.Context(
        numbering=context.numbering,
        titles=context.titles,
        keys=context.keys,
        expected=frozenset(context.expected),
        via=context.via,
        original_language=context.original_language,
        filed=frozenset(item for item, state in context.states.items() if state == "filed"),
        anime=context.numbering.series_type == "anime",
    )


# --- Names -------------------------------------------------------------------------------------------------------- #


def _episode_facts(context: Context, episode_ids: Iterable[int]) -> tuple[list[naming_series.EpisodeFacts], str]:
    """The episodes in the numbering of the name: TVDB's when the version asks for it and every episode has one."""
    rows = [context.numbering.episodes[item] for item in episode_ids if item in context.numbering.episodes]
    use_tvdb = context.naming.numbering == "tvdb" and rows and all(row.id in context.tvdb for row in rows)
    # The numbers an anime series counts through, for ``{absolute}`` (A5).
    counted = {episode_id: number for number, episode_id in context.numbering.by_absolute.items()}
    facts = []
    for row in sorted(rows, key=lambda item: (item.season, item.episode)):
        season, number = context.tvdb[row.id] if use_tvdb else (row.season, row.episode)
        name, name_en = context.names.get(row.id, (row.name, None))
        facts.append(
            naming_series.EpisodeFacts(
                season=season,
                episode=number,
                air_date=row.air_date,
                title=names.file_title(name, name_en, context.language),
                absolute=counted.get(row.id),
            )
        )
    return facts, ("tvdb" if use_tvdb else "tmdb")


# --- Filing one file ---------------------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Target:
    """Where one video goes, decided before anything moves."""

    source: Path
    from_archive: bool
    origin: Path
    #: The folder the series folder lies in (its recycle folder takes replaced files), and the series folder.
    root: Path
    folder: Path
    target_dir: Path
    target: Path
    season: int
    season_folder: str | None
    numbering: str
    named_tba: bool
    #: The files of the version this video takes an episode from, and those it replaces whole (the recycle folder).
    replaced: tuple[CurrentFile, ...]
    whole: tuple[CurrentFile, ...]
    #: The video's media data and quality, read before it moves; the name carries them.
    measured: FileMedia

    @property
    def relative(self) -> str:
        return self.target.relative_to(self.folder).as_posix()


@dataclass(frozen=True)
class Filed:
    key: int
    episode_ids: tuple[int, ...]
    relative_path: str
    size: int
    #: hardlink, copy or move; None for a file taken over after a stop.
    transfer: str | None
    named_tba: bool
    numbering: str
    season: int
    season_folder: str | None
    replaced: tuple[CurrentFile, ...]
    whole: tuple[CurrentFile, ...]
    #: The quality and media data its name carries; stored as they are.
    measured: FileMedia
    #: The half of a double episode it is; None for a whole file.
    part: int | None = None


@dataclass
class Skip:
    key: int
    episode_ids: tuple[int, ...]
    #: ``not_better`` or ``covers_more``.
    reason: str


def _replaced(context: Context, episode_ids: Iterable[int], part: int | None = None) -> list[CurrentFile]:
    """The files of the version that hold one of these episodes now, except those this download filed.

    A whole file takes the place of both halves of a double episode; a first half takes the place of the first half
    or of a whole file, a second half only that of the second.
    """
    seen: dict[int, CurrentFile] = {}
    for episode_id in episode_ids:
        found = [
            context.current.get(episode_id) if part in (None, 1) else None,
            context.halves.get(episode_id) if part in (None, 2) else None,
        ]
        for entry in found:
            if entry is not None and entry.download_id != context.download_id:
                seen[entry.id] = entry
    return list(seen.values())


def _remaining(context: Context, entry: CurrentFile) -> set[int]:
    """The episodes a file still holds in the version: a multi-episode file loses them part by part."""
    return {episode_id for episode_id, current in context.current.items() if current.id == entry.id}


def plan_replacements(context: Context, filed: list[episodes.Decision], *, manual: bool) -> dict[int, str]:
    """Video key to the reason it is not filed.

    * ``not_better``: a file it would replace is not worse (decision 25); not when the owner confirmed no gain, and not
      for the owner's own assignment, whose dialog asked already.
    * ``covers_more``: a file it would replace holds an episode that no video of this download files (decision 26). A
      multi-episode file is replaced by all its parts or by none: when one part is not better, the others stay too.
    """
    reasons: dict[int, str] = {}
    rules = context.rules
    judged = rules is not None and rules.get("kind") == "series"
    for decision in filed:
        replaced = _replaced(context, decision.episode_ids, decision.part)
        confirmed = all(context.expected.get(item) == "confirmed" for item in decision.episode_ids)
        if manual or not replaced or not judged or confirmed or rules is None:
            continue
        candidate = _candidate(context, decision.video, len(decision.episode_ids), decision.part)
        for entry in replaced:
            if not series_decision.better_file(rules, candidate, _current_of(entry), context.original_language):
                reasons[decision.video.key] = "not_better"
                break
    before = {item for item, state in context.states.items() if state == "filed"}
    changed = True
    while changed:
        changed = False
        active = [decision for decision in filed if decision.video.key not in reasons]
        bringing = before | {episode_id for decision in active for episode_id in decision.episode_ids}
        halves = {(item, decision.part) for decision in active if decision.part for item in decision.episode_ids}
        for decision in active:
            if any(
                not _remaining(context, entry) <= bringing
                for entry in _replaced(context, decision.episode_ids, decision.part)
            ) or _half_of_a_whole(context, decision, halves):
                reasons[decision.video.key] = "covers_more"
                changed = True
    return reasons


def _half_of_a_whole(context: Context, decision: episodes.Decision, halves: set[tuple[int, int]]) -> bool:
    """A half that would stand beside a whole file of its episode, or take its place, without its other half coming
    too: the episode would lose its second half or hold three pieces. Both halves come, or neither."""
    if decision.part not in (1, 2):
        return False
    other = 3 - decision.part
    for episode_id in decision.episode_ids:
        entry = context.current.get(episode_id)
        if (
            entry is not None
            and entry.part is None
            and entry.download_id != context.download_id
            and (episode_id, other) not in halves
        ):
            return True
    return False


def _parts(relative: str) -> list[str]:
    return [part for part in relative.replace("\\", "/").split("/") if part]


def _codes_of(context: Context, episode_ids: Iterable[int]) -> str:
    return ", ".join(_codes(context, episode_ids))


def prepare(
    context: Context,
    found: Found,
    video: episodes.Video,
    episode_ids: tuple[int, ...],
    root: Path,
    listings: dict[Path, list[str]],
    part: int | None = None,
) -> Target:
    """Where a video goes: folders made, name built, conflicts checked. Raises ``Problem``; nothing moves here."""
    source, from_archive = found.paths[video.key]
    origin = found.unpacked if from_archive and found.unpacked is not None else found.job
    if not from_archive and files.below_working_folder(source):
        raise StillUnpacking
    if not files.inside(source, origin):
        raise Problem("import_failed", reason="source_outside")
    facts, numbering = _episode_facts(context, episode_ids)
    if not facts:
        raise Problem("import_failed", reason="episode_gone")
    season = facts[0].season
    folder, _name = series_folder(context, root)
    folder = _ensure_folder(folder, root)
    replaced = _replaced(context, episode_ids, part)
    whole = tuple(entry for entry in replaced if _remaining(context, entry) <= set(episode_ids))
    season_name: str | None = None
    target_dir: Path | None = None
    if len(replaced) == 1:
        # An upgrade goes into the folder of the file it replaces, while that folder is still there.
        parts = _parts(replaced[0].relative_path)
        existing = folder.joinpath(*parts[:-1]) if len(parts) > 1 else folder
        if existing.is_dir() and files.inside(existing, folder):
            target_dir = existing
    if target_dir is None:
        season_name = context.season_folders.get(season) or naming_series.season_folder_name(
            context.naming, context.series, season
        )
        if not _plain_part(season_name):
            raise Problem("import_failed", reason="destination_outside")
        target_dir = _ensure_folder(folder / season_name, folder)
    # The media data before anything moves: the name carries the file's quality, codec and audio (the owner's answer
    # of 17.09.2026), and the window between moving and recording stays short.
    answer = found.reads[video.key] if video.key in found.reads else read_media(source)
    if answer.error_code == media.MEDIA_TRUNCATED:
        # This episode stays where it is and replaces nothing; the other videos of the download are filed.
        raise Problem("file_truncated", reason="file_truncated", episodes=_codes_of(context, episode_ids))
    measured = file_media(context, answer, target_dir.name, video.path)
    release = naming.ReleaseFacts(
        release_title=context.release_title,
        formats=context.formats,
        original_filename=Path(source.name).stem,
        quality=measured.quality,
        media=measured.media,
    )
    built = naming_series.file_name(
        context.naming,
        context.series,
        facts,
        release,
        source.suffix,
        daily=context.daily,
        numbering=numbering,
        anime=context.numbering.series_type == "anime",
        part=part,
    )
    target = target_dir / built.name
    replaced_paths = [folder.joinpath(*_parts(entry.relative_path)) for entry in replaced]
    if os.path.lexists(target) and all(files.resolved(target) != files.resolved(path) for path in replaced_paths):
        raise Problem("import_failed", reason="destination_exists", episodes=_codes_of(context, episode_ids))
    _refuse_foreign(context, folder, target_dir, target, episode_ids, listings)
    return Target(
        source=source,
        from_archive=from_archive,
        origin=origin,
        root=root,
        folder=folder,
        target_dir=target_dir,
        target=target,
        season=season,
        season_folder=season_name,
        numbering=numbering,
        named_tba=built.named_tba,
        replaced=tuple(replaced),
        whole=whole,
        measured=measured,
    )


def _refuse_foreign(
    context: Context,
    folder: Path,
    target_dir: Path,
    target: Path,
    episode_ids: tuple[int, ...],
    listings: dict[Path, list[str]],
) -> None:
    """A video in the target folder that nexcrate does not know and that reads one of these episodes: nexcrate puts no
    second file next to it (Sonarr's files in the same folder, a file placed by hand). ``foreign_file``."""
    if target_dir not in listings:
        names: list[str] = []
        try:
            for entry in os.scandir(target_dir):
                is_video = files.extension_of(entry.name) in files.VIDEO_EXTENSIONS
                if not entry.is_file(follow_symlinks=False) or not is_video:
                    continue
                if _path_key((target_dir / entry.name).relative_to(folder).as_posix()) not in context.known_paths:
                    names.append(entry.name)
        except OSError:
            names = []
        listings[target_dir] = names
    wanted = set(episode_ids)
    reading_context = assignment_context(context)
    for name in listings[target_dir]:
        if name == target.name:
            continue
        reading = episodes.read(episodes.Video(key=0, path=name, size=0), reading_context, single=False)
        if reading is not None and wanted & set(reading.episode_ids):
            codes = _codes_of(context, wanted & set(reading.episode_ids))
            raise Problem("import_failed", reason="foreign_file", episodes=codes)


def place(context: Context, found: Found, spot: Target) -> files.Placed:
    """Move or link the video to its target: the temporary name, the whole replaced files into the recycle folder, the
    final name. On a failure everything is put back and ``Problem`` is raised."""
    try:
        if spot.from_archive:
            placed = files.place_unpacked(spot.source, spot.target_dir, spot.target.name)
        else:
            placed = files.place(spot.source, spot.target_dir, spot.target.name, protocol=context.protocol)
    except files.FileProblem as exc:
        raise Problem(exc.code, **exc.values) from exc
    except OSError as exc:
        raise Problem("import_failed", reason="transfer_failed") from exc
    recycled: list[tuple[Path, Path]] = []
    try:
        moment = store.now()
        for entry in spot.whole:
            path = spot.folder.joinpath(*_parts(entry.relative_path))
            if os.path.lexists(path) and files.inside(path, spot.folder):
                recycled.append((path, files.recycle(path, spot.root, moment)))
        os.replace(placed.partial, spot.target)
    except (OSError, files.FileProblem) as exc:
        for original, moved in reversed(recycled):
            try:
                os.rename(moved, original)
            except OSError:
                logger.warning("Download %d: a replaced file stays in the recycle folder", context.download_id)
        files.undo(placed, spot.source)
        reason = "transfer_failed"
        if isinstance(exc, files.FileProblem):
            reason = str(exc.values.get("reason", reason))
        raise Problem("import_failed", reason=reason) from exc
    if placed.copied and context.protocol == "usenet" and files.inside(spot.source, found.job):
        try:
            spot.source.unlink()
        except OSError:
            logger.warning("Download %d: a copied source file could not be deleted", context.download_id)
    if spot.whole:
        _recycle_subtitles(context, spot)
    return placed


def _recycle_subtitles(context: Context, spot: Target) -> None:
    """The recorded subtitles of the files replaced whole follow them into the recycle folder, as with movies. A
    subtitle that cannot move stays; recording the new file removes the rows either way."""
    rows = [
        (row_id, "/".join([spot.folder.name, *_parts(relative)]))
        for entry in spot.whole
        for row_id, relative in context.subtitles.get(entry.id, ())
    ]
    if not rows:
        return
    try:
        recycled = subtitle_placing.recycle(rows, spot.root, store.now())
    except Exception:  # noqa: BLE001 - the episode is in place; its old subtitles must not undo that
        logger.warning("Download %d: the subtitle files of a replaced file could not be handled", context.download_id)
        return
    if recycled.failed:
        logger.warning("Download %d: %d old subtitle files stay in place", context.download_id, recycled.failed)


@dataclass(frozen=True)
class FileMedia:
    """A video's media data and its quality in Sonarr's names: from the media data, else from the names."""

    media: dict[str, Any] | None
    quality: str | None
    #: ``media`` or ``name``.
    quality_from: str


def read_media(path: Path) -> media.Read:
    """The media tool's answer for one video. Never raises."""
    try:
        return media.read(path) if media.readable(path) else media.Read(None, media.MEDIA_UNREADABLE)
    except Exception:  # noqa: BLE001 - a video without media data is named and judged by its names
        return media.Read(None, media.MEDIA_UNREADABLE)


def file_media(context: Context, answer: media.Read, folder_name: str, path: str) -> FileMedia:
    """The quality of a video from its media data by Radarr's rule (plan-library-from-disk, decision 22) in Sonarr's
    names, else as ``_file_quality`` reads its names. The name and the stored file carry the same (the owner's answer of
    17.09.2026)."""
    return media_quality(context.release_title, context.original_language, answer, folder_name, path)


def media_quality(
    release_title: str, original_language: str | None, answer: media.Read, folder_name: str | None, path: str
) -> FileMedia:
    """``file_media`` without a download: a file found on disk passes its own name as ``release_title``."""
    named = file_quality(path, release_title, original_language)
    if answer.media is None:
        return FileMedia(None, named, "name")
    decision = media.quality_of(release_title, folder_name, answer.media)
    movie_quality = movie_qualities.BY_NAME.get(decision.quality)
    if movie_quality is None:
        return FileMedia(answer.media, named, "name")
    # Radarr's names (Remux-1080p, Bluray-576p) are not Sonarr's: the engine reads a series file by Sonarr's table.
    series_quality = qualities_series.from_movie(movie_quality, movie_quality.resolution or None)
    return FileMedia(answer.media, series_quality.name, decision.quality_from)


def _place_subtitles(
    context: Context, found: Found, source: Path, origin: Path, target: Path, folder: Path
) -> tuple[subtitle_placing.Placed, ...]:
    """The subtitles that belong to this video (C9 per episode file, decision 29). Never fails the file."""
    if not context.subtitles_enabled:
        return ()
    origins = [(found.job, False)] if found.unpacked is None else [(found.job, False), (found.unpacked, True)]
    try:
        groups = {group for name in (context.release_title, source.stem) if (group := releases.parse(name).movie.group)}
        result = subtitle_placing.place(
            origins=origins,
            video_name=source.stem,
            videos=found.count,
            groups=groups,
            target=target,
            folder=folder,
            protocol=context.protocol,
            max_files=SUBTITLES_PER_DOWNLOAD,
            per_video=SUBTITLES_PER_VIDEO,
        )
    except Exception:  # noqa: BLE001 - the episode is in place; a subtitle must never undo that
        logger.warning("Download %d: the subtitle files of an episode could not be placed", context.download_id)
        return ()
    if result.failed:
        logger.warning("Download %d: %d subtitle files could not be placed", context.download_id, result.failed)
    return result.placed


# --- Recording ---------------------------------------------------------------------------------------------------- #


def _claim(download_id: int, states: tuple[str, ...]) -> bool:
    with SessionLocal() as db:
        result = db.execute(
            update(Download)
            .where(Download.id == download_id, Download.state.in_(states))
            .values(state="importing", updated_at=store.now())
        )
        db.commit()
    return bool(getattr(result, "rowcount", 0))


def record_files(context: Context, result: episodes.Result) -> dict[int, int]:
    """Keep every video with its decision; returns video key to row id. Rows of files filed before stay; a video about
    to be filed is ``open`` until it moves."""
    ids: dict[int, int] = {}
    with SessionLocal() as db:
        kept = {
            item.path: item
            for item in db.scalars(select(DownloadFile).where(DownloadFile.download_id == context.download_id))
        }
        for decision in result.files:
            video = decision.video
            row = kept.pop(video.path, None)
            if row is not None and row.decision == episodes.FILED and row.episode_file_id is not None:
                ids[video.key] = row.id
                continue
            if row is None:
                row = DownloadFile(download_id=context.download_id, path=video.path)
                db.add(row)
            row.size = video.size
            row.duration_seconds = video.duration_seconds
            row.reading = _reading(decision.reading)
            row.decision = decision.decision if decision.decision != episodes.FILED else episodes.OPEN
            row.episode_ids = list(decision.episode_ids)
            db.flush()
            ids[video.key] = row.id
        for row in kept.values():
            if row.decision != episodes.FILED:
                db.delete(row)
        db.commit()
    return ids


def _mark(download_id: int, row_id: int, decision: str, *, target: str | None = None) -> bool:
    """One row's decision; with ``target`` the video is about to move there (the marker a stop leaves)."""
    with SessionLocal() as db:
        download = db.get(Download, download_id)
        row = db.get(DownloadFile, row_id)
        if download is None or row is None or download.state != "importing":
            return False
        row.decision = decision
        if target is not None:
            download.imported_path = target[:4096]
            download.updated_at = store.now()
        db.commit()
    return True


def _reading(reading: episodes.Reading | None) -> dict[str, Any] | None:
    return reading.as_dict() if reading is not None else None


def record_one(context: Context, filed: Filed, row_id: int, moment: datetime) -> int | None:
    """One filed video in one transaction: its episode file, the links of its episodes, the download's rows. Returns
    the new episode file's id; None when the download changed meanwhile (the file stays in place)."""
    with SessionLocal() as db:
        download = db.get(Download, context.download_id)
        version = db.get(Version, context.version_id) if context.version_id is not None else None
        if download is None or download.state != "importing" or version is None or version.source_id is not None:
            logger.warning("Download %d changed while it was filed; the file stays in place", context.download_id)
            return None
        if not version.relative_path:
            root = _root(context)
            _folder, name = series_folder(context, root)
            version.root_folder = str(root)
            version.relative_path = name
        release_type = "multiEpisode" if len(filed.episode_ids) > 1 else "singleEpisode"
        if releases.parse_series(context.release_title).series.release_type == "season_pack":
            release_type = "seasonPack"
        parsed = releases.parse_series(context.release_title, context.original_language)
        measured = filed.measured
        # Known audio stream languages win over the release name's (plan-library-from-disk, decision 24).
        kept = media.file_languages(measured.media, list(context.languages))
        episode_file = EpisodeFile(
            version_id=version.id,
            relative_path=filed.relative_path[:2048],
            size=filed.size,
            quality=measured.quality,
            languages=kept,
            release_group=parsed.series.group,
            release_title=context.release_title[:1024],
            release_type=release_type,
            media_info=measured.media,
            file_ref=f"nexcrate:{context.download_id}",
            quality_from=measured.quality_from,
            named_tba=filed.named_tba,
            name_numbering=filed.numbering,
            added_at=moment,
            updated_at=moment,
            part=filed.part,
            # The second half hangs on its episode here; the first is linked through ``episode_versions`` below.
            part_of_episode_id=filed.episode_ids[0] if filed.part == 2 and filed.episode_ids else None,
        )
        # A file replaced whole goes with its rows; one that keeps an episode keeps them and loses only the link below.
        gone_ids = [entry.id for entry in filed.whole]
        if gone_ids:
            db.execute(
                update(EpisodeVersion)
                .where(EpisodeVersion.episode_file_id.in_(gone_ids))
                .values(episode_file_id=None),
                execution_options={"synchronize_session": False},
            )
            db.execute(delete(ExtraFile).where(ExtraFile.episode_file_id.in_(gone_ids)))
            db.execute(delete(EpisodeFile).where(EpisodeFile.id.in_(gone_ids)))
        db.add(episode_file)
        db.flush()
        judged = None
        if context.rules is not None and context.rules.get("kind") == "series":
            judged = series_decision.judge_episode_file(
                context.rules,
                releases.CurrentEpisodeFile(
                    release_title=episode_file.release_title,
                    name=episode_file.relative_path,
                    quality=episode_file.quality,
                    release_type=episode_file.release_type,
                    size_bytes=as_whole(episode_file.size, filed.part),
                    episode_count=len(filed.episode_ids),
                    file_id=episode_file.id,
                    languages=releases.stored_languages(episode_file.languages),
                ),
                context.original_language,
            )
            episode_file.cutoff_not_met = judged is not None and judged.reason is not None
            # Below the target by quality: the season is searched again at once (decision 25).
            replacement.after_series_file(
                db, version.title_id, filed.episode_ids, judged.reason if judged is not None else None, moment
            )
        if filed.part != 2:
            db.execute(
                update(EpisodeVersion)
                .where(EpisodeVersion.version_id == version.id, EpisodeVersion.episode_id.in_(list(filed.episode_ids)))
                .values(episode_file_id=episode_file.id),
                execution_options={"synchronize_session": False},
            )
        for episode_id in filed.episode_ids:
            link = db.get(DownloadEpisode, (context.download_id, episode_id))
            if link is None:
                link = DownloadEpisode(download_id=context.download_id, episode_id=episode_id, action="confirmed")
                db.add(link)
            link.state = "filed"
            if filed.part != 2 or link.episode_file_id is None:
                link.episode_file_id = episode_file.id
        file_row = db.get(DownloadFile, row_id)
        if file_row is not None:
            file_row.decision = episodes.FILED
            file_row.episode_ids = list(filed.episode_ids)
            file_row.episode_file_id = episode_file.id
        if filed.season_folder and db.get(SeasonFolder, (version.id, filed.season)) is None:
            db.add(SeasonFolder(version_id=version.id, season_number=filed.season, name=filed.season_folder))
        download.filed_count = (download.filed_count or 0) + len(filed.episode_ids)
        if filed.transfer is not None:
            download.transfer = filed.transfer
        download.imported_path = filed.relative_path[:4096]
        download.updated_at = moment
        db.commit()
        return episode_file.id


def complete_one(context: Context, found: Found, spot_source: Path, target: Path, folder: Path, file_id: int) -> None:
    """After recording: the subtitles of the filed video, in their own small transaction. They cannot undo the filing.
    The media data came before the video moved (``prepare``)."""
    origin = found.job
    if not origin.is_dir():
        # A video taken over after its job folder went: no subtitles are left to bring.
        return
    subtitles = _place_subtitles(context, found, spot_source, origin, target, folder)
    if not subtitles:
        return
    moment = store.now()
    with SessionLocal() as db:
        episode_file = db.get(EpisodeFile, file_id)
        if episode_file is None:
            return
        for item in subtitles:
            db.add(
                ExtraFile(
                    version_id=episode_file.version_id,
                    download_id=context.download_id,
                    relative_path=item.relative_path,
                    kind="subtitle",
                    language=item.language,
                    forced=item.forced,
                    sdh=item.sdh,
                    created_at=moment,
                    episode_file_id=episode_file.id,
                )
            )
        db.commit()


def _set_states(db: OrmSession, download_id: int, episode_ids: Iterable[int], state: str) -> None:
    for episode_id in episode_ids:
        link = db.get(DownloadEpisode, (download_id, episode_id))
        if link is not None and link.state != "filed":
            link.state = state


def _codes(context: Context, episode_ids: Iterable[int]) -> list[str]:
    rows = [context.numbering.episodes[item] for item in episode_ids if item in context.numbering.episodes]
    return [row.code for row in sorted(rows, key=lambda item: (item.season, item.episode))]


def finish(
    context: Context,
    result: episodes.Result,
    skips: list[Skip],
    filed_now: int,
    failure: Problem | None,
    row_ids: dict[int, int] | None = None,
) -> str:
    """The download's state after filing. Returns it."""
    moment = store.now()
    with SessionLocal() as db:
        download = db.get(Download, context.download_id)
        if download is None or download.state != "importing":
            return "changed"
        filed_ids = {
            item.episode_id
            for item in db.scalars(
                select(DownloadEpisode).where(
                    DownloadEpisode.download_id == context.download_id, DownloadEpisode.state == "filed"
                )
            )
        }
        skipped_ids = {episode_id for skip in skips for episode_id in skip.episode_ids} - filed_ids
        _set_states(db, context.download_id, skipped_ids, "skipped_not_better")
        for skip in skips:
            # A video not filed because the episode's file is not worse: no open file, the owner may still choose it.
            row = db.get(DownloadFile, (row_ids or {}).get(skip.key, -1))
            if row is not None and row.decision in (episodes.OPEN, PLACING):
                row.decision = SKIPPED
        db.flush()
        open_rows = list(
            db.scalars(
                select(DownloadFile).where(
                    DownloadFile.download_id == context.download_id, DownloadFile.decision == episodes.OPEN
                )
            )
        )
        uncovered = [item for item in context.expected if item not in filed_ids and item not in skipped_ids]
        download.open_count = len(open_rows) if uncovered else 0
        if failure is not None:
            download.state, download.problem_code = "problem", failure.code
            download.problem_values = {**failure.values, "filed": len(filed_ids)}
        elif result.problem == "other_series_suspected":
            download.state, download.problem_code, download.problem_values = "problem", "other_series_suspected", {}
        elif open_rows and uncovered:
            download.state, download.problem_code = "problem", "files_unassigned"
            values: dict[str, Any] = {"filed": len(filed_ids), "open": len(open_rows)}
            if result.unknown:
                values["unknown"] = ", ".join(result.unknown[:20])
            if result.counted:
                values["counted"] = result.counted
            download.problem_values = values
        else:
            for row in open_rows:
                row.decision = episodes.NOT_NEEDED
            _set_states(db, context.download_id, uncovered, "missing")
            download.absent_count = len(uncovered)
            download.state, download.problem_code, download.problem_values = "imported", None, None
            download.imported_at = moment
            download.progress, download.remaining_seconds = 100.0, 0
            detail = history_detail(len(filed_ids), len(skipped_ids), _codes(context, uncovered))
            written = history_data(db, context.download_id, context.filed_log)
            store.add_history(db, download, "episodes_filed", detail, moment, written)
        download.updated_at = moment
        store.follow_series(db, download, moment)
        db.commit()
        state = download.state
    logger.info(
        "Download %d: %d episodes filed now, %d skipped, state %s",
        context.download_id,
        filed_now,
        len(skips),
        state if failure is None else f"{state} ({failure.code})",
    )
    return state


def history_detail(filed: int, skipped: int, missing: list[str]) -> str:
    """``filed=3 skipped=1 missing=S02E07,S02E08``: counts and codes, never names."""
    parts = [f"filed={filed}", f"skipped={skipped}"]
    if missing:
        parts.append("missing=" + ",".join(missing[:200]))
    return " ".join(parts)


def record_problem(download_id: int, problem: Problem) -> None:
    moment = store.now()
    with SessionLocal() as db:
        row = db.get(Download, download_id)
        if row is None or row.state != "importing":
            return
        row.state, row.problem_code, row.problem_values = "problem", problem.code, dict(problem.values)
        row.updated_at = moment
        if problem.code in ("dangerous_file", "encrypted"):
            store.block(db, row, problem.code, moment)
            store.add_history(db, row, "failed", problem.code, moment)
        store.follow_series(db, row, moment)
        db.commit()
    reason = problem.values.get("reason")
    logger.info("Download %d could not be filed: %s%s", download_id, problem.code, f" ({reason})" if reason else "")


def _back_to(download_id: int, state: str) -> None:
    with SessionLocal() as db:
        db.execute(
            update(Download)
            .where(Download.id == download_id, Download.state == "importing")
            .values(state=state, updated_at=store.now())
        )
        db.commit()


# --- The whole run ------------------------------------------------------------------------------------------------ #


def run(download_id: int, manual: dict[int, tuple[int, ...]] | None = None) -> None:
    """File a series download, in the calling thread. ``manual`` maps the owner's row ids to episodes; a row with no
    episodes is not filed. Without ``manual`` the download must be ``completed``; with it ``importing`` already."""
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
    try:
        root = _root(context)
    except Problem as problem:
        record_problem(download_id, problem)
        return
    try:
        found = _gathered(context, root)
        _file_all(context, found, root, manual)
    except StillUnpacking:
        _back_to(download_id, "completed")
    except Problem as problem:
        record_problem(download_id, problem)
    finally:
        # Every round unpacks again, the owner's later assignment too: no unpacked video stays hidden in the library.
        unpacking.remove(root, download_id)


def _gathered(context: Context, root: Path) -> Found:
    """``gather``, or nothing to gather when a round before moved its video already.

    ⚠️ 23.09.2026: a video moved into the series folder, then recording it met a locked database. The next round found
    the job folder empty or gone and ended as ``no_video`` or ``path_not_found``, with the video lying in the library
    unrecorded; ``adopt_placing`` never ran. A download with such a row goes on without videos of the job, so the
    taking over can record what arrived.
    """
    try:
        return gather(context, root)
    except Problem as problem:
        if problem.code not in ("no_video", "path_not_found") or not any(
            item.decision == PLACING for item in context.stored
        ):
            raise
        logger.info("Download %d: the job is empty, a video placed before is taken over", context.download_id)
        return Found([], {}, _gone_job(context, root), None, 0)


def _gone_job(context: Context, root: Path) -> Path:
    """The job folder as far as it can be told, else a path below the library root that holds nothing."""
    try:
        return _local_job(context)
    except Problem:
        return root / ".nexcrate-no-job"


def _file_all(context: Context, found: Found, root: Path, manual: dict[int, tuple[int, ...]] | None) -> bool:
    context, adopted = adopt_placing(context, found, root)
    # A video taken over from a stopped round is filed in this round too: the log and the media servers count it.
    filed_now = len(adopted)
    already = {item.path for item in context.stored if item.decision == episodes.FILED} | adopted
    videos = [video for video in found.videos if video.path not in already]
    if manual is None:
        result = episodes.assign(videos, assignment_context(context), ranker(context))
    else:
        result = _manual_result(context, videos, manual)
    row_ids = record_files(context, result)
    if result.problem == "other_series_suspected":
        finish(context, result, [], filed_now, None)
        return False
    reasons = plan_replacements(context, result.filed, manual=manual is not None)
    skips: list[Skip] = []
    failure: Problem | None = None
    listings: dict[Path, list[str]] = {}
    for decision in result.filed:
        video = decision.video
        reason = reasons.get(video.key)
        if reason is not None:
            skips.append(Skip(video.key, decision.episode_ids, reason))
            continue
        try:
            spot = prepare(context, found, video, decision.episode_ids, root, listings, decision.part)
        except StillUnpacking:
            _back_to(context.download_id, "completed")
            return False
        except Problem as problem:
            failure = failure or problem
            if problem.values.get("reason") in FILE_REASONS:
                continue
            break
        if not _mark(context.download_id, row_ids[video.key], PLACING, target=spot.relative):
            return False
        try:
            placed = place(context, found, spot)
        except Problem as problem:
            _mark(context.download_id, row_ids[video.key], episodes.OPEN)
            failure = failure or problem
            if problem.values.get("reason") in FILE_REASONS:
                continue
            break
        filed = Filed(
            key=video.key,
            episode_ids=decision.episode_ids,
            relative_path=spot.relative,
            size=placed.size_bytes,
            transfer=placed.transfer,
            named_tba=spot.named_tba,
            numbering=spot.numbering,
            season=spot.season,
            season_folder=spot.season_folder,
            replaced=spot.replaced,
            whole=spot.whole,
            measured=spot.measured,
            part=decision.part,
        )
        file_id = record_one(context, filed, row_ids[video.key], store.now())
        if file_id is None:
            return False
        filed_now += 1
        context = _after_filed(context, filed)
        complete_one(context, found, spot.source, spot.target, spot.folder, file_id)
    state = finish(context, result, skips, filed_now, failure, row_ids)
    _write_companions(context)
    if filed_now:
        _tell_media_servers(context, root)
    if state == "imported" and context.protocol == "usenet":
        try:
            asyncio.run(_clean_usenet(context, found, root))
        except (downloaders.ClientError, OSError) as exc:
            logger.info("Download %d: cleaning up in the client failed: %s", context.download_id, type(exc).__name__)
    return state == "imported"


def adopt_placing(context: Context, found: Found, root: Path) -> tuple[Context, set[str]]:
    """A round after a stop: the video that was moving when nexcrate stopped (its row ``placing``, the target in the
    download's ``imported_path``) is taken over where it arrived, instead of being lost or refused as in the way.

    * The target has the video's size: recorded as filed.
    * Only the temporary name is there with the video's size and the video is gone from the job: it was renamed, so
      it is finished and recorded.
    * Otherwise the row is open again, a temporary copy goes, and the round files the video as usual.
    Returns the new context and the paths of the videos taken over.
    """
    placing = [item for item in context.stored if item.decision == PLACING]
    adopted: set[str] = set()
    if not placing:
        return context, adopted
    try:
        folder, _name = series_folder(context, root)
    except Problem:
        folder = None
    in_job = {video.path for video in found.videos}
    for item in placing:
        parts = _parts(context.marker or "")
        target = folder.joinpath(*parts) if folder is not None and parts else None
        partial = target.with_name(target.name + files.PARTIAL_SUFFIX) if target is not None else None
        usable = bool(
            target is not None and folder is not None and item.episode_ids and files.inside(target.parent, folder)
        )
        if usable and not _same_size(target, item.size) and _same_size(partial, item.size) and item.path not in in_job:
            try:
                os.replace(partial, target)
            except OSError:
                usable = False
        if not usable or not _same_size(target, item.size):
            if partial is not None and item.path in in_job and os.path.lexists(partial) and not files.is_link(partial):
                try:
                    partial.unlink()
                except OSError:
                    logger.info("Download %d: a temporary file of a stopped round stays", context.download_id)
            _mark(context.download_id, item.id, episodes.OPEN)
            continue
        facts, numbering = _episode_facts(context, item.episode_ids)
        if not facts:
            _mark(context.download_id, item.id, episodes.OPEN)
            continue
        relative = target.relative_to(folder).as_posix()
        replaced = _replaced(context, item.episode_ids, item.part)
        whole = tuple(entry for entry in replaced if _remaining(context, entry) <= set(item.episode_ids))
        for entry in whole:
            old = folder.joinpath(*_parts(entry.relative_path))
            if os.path.lexists(old) and files.inside(old, folder) and files.resolved(old) != files.resolved(target):
                try:
                    files.recycle(old, root, store.now())
                except (OSError, files.FileProblem):
                    logger.warning("Download %d: a replaced file stays in place", context.download_id)
        parent = target.parent
        measured = file_media(context, read_media(target), parent.name, item.path)
        filed = Filed(
            key=0,
            episode_ids=item.episode_ids,
            relative_path=relative,
            size=target.stat().st_size,
            transfer=None,
            named_tba=_named_tba(context, facts, numbering, item.path, target.name, measured),
            numbering=numbering,
            season=facts[0].season,
            season_folder=parent.name if parent != folder else None,
            replaced=tuple(replaced),
            whole=whole,
            measured=measured,
            part=item.part,
        )
        file_id = record_one(context, filed, item.id, store.now())
        if file_id is None:
            return context, adopted
        logger.info("Download %d: a file placed before a stop is taken over", context.download_id)
        context = _after_filed(context, filed)
        complete_one(context, found, found.job / PurePosixPath(item.path).name, target, folder, file_id)
        adopted.add(item.path)
    return context, adopted


def _same_size(path: Path | None, size: int) -> bool:
    try:
        return path is not None and path.is_file() and not files.is_link(path) and path.stat().st_size == size
    except OSError:
        return False


def _named_tba(
    context: Context,
    facts: list[naming_series.EpisodeFacts],
    numbering: str,
    path: str,
    name: str,
    measured: FileMedia,
) -> bool:
    """Whether a file taken over carries the name built with TBA: only then may a later title rename it."""
    release = naming.ReleaseFacts(
        release_title=context.release_title,
        formats=context.formats,
        original_filename=PurePosixPath(path.removeprefix(UNPACKED_PREFIX)).stem,
        quality=measured.quality,
        media=measured.media,
    )
    suffix = PurePosixPath(name).suffix
    built = naming_series.file_name(
        context.naming,
        context.series,
        facts,
        release,
        suffix,
        daily=context.daily,
        numbering=numbering,
        anime=context.numbering.series_type == "anime",
    )
    return built.named_tba and built.name == name


def _after_filed(context: Context, filed: Filed) -> Context:
    """The context with the new file as the current one of its episodes, its season folder named and its path known."""
    from dataclasses import replace

    current = dict(context.current)
    halves = dict(context.halves)
    entry = CurrentFile(
        id=-1,
        relative_path=filed.relative_path,
        size=filed.size,
        quality=filed.measured.quality,
        release_title=context.release_title,
        release_type=None,
        episode_ids=filed.episode_ids,
        download_id=context.download_id,
        languages=tuple(media.file_languages(filed.measured.media, list(context.languages))),
        part=filed.part,
    )
    for episode_id in filed.episode_ids:
        if filed.part == 2:
            halves[episode_id] = entry
        else:
            current[episode_id] = entry
            if filed.part is None:
                # A whole file took the place of both halves.
                halves.pop(episode_id, None)
    season_folders = dict(context.season_folders)
    if filed.season_folder:
        season_folders.setdefault(filed.season, filed.season_folder)
    relative = context.series_relative
    if not relative:
        relative = naming_series.series_folder_name(context.naming, context.series)
    states = {**context.states, **dict.fromkeys(filed.episode_ids, "filed")}
    known = context.known_paths | {_path_key(filed.relative_path)}
    logged = {
        "episode_ids": list(filed.episode_ids),
        "quality": filed.measured.quality,
        "size_bytes": filed.size,
        "replaced": [{"quality": item.quality, "size_bytes": item.size} for item in filed.replaced],
    }
    return replace(
        context,
        current=current,
        halves=halves,
        season_folders=season_folders,
        series_relative=relative,
        states=states,
        known_paths=known,
        filed_log=(*context.filed_log, logged),
    )


def history_data(db: OrmSession, download_id: int, log: tuple[dict[str, Any], ...] = ()) -> dict[str, Any]:
    """``data`` of an ``episodes_filed`` line: the filed episodes in TMDB's numbers, and per file of this round its
    episodes, quality, size and the files it replaced. What an earlier round filed is in the episodes only."""
    pairs = {
        episode_id: (season, number)
        for episode_id, season, number in db.execute(
            select(DownloadEpisode.episode_id, Episode.season_number, Episode.episode_number)
            .join(Episode, Episode.id == DownloadEpisode.episode_id)
            .where(DownloadEpisode.download_id == download_id, DownloadEpisode.state == "filed")
        ).tuples()
    }
    wanted = set(pairs)
    for item in log:
        wanted.update(item["episode_ids"])
    missing = wanted - set(pairs)
    if missing:
        for episode_id, season, number in db.execute(
            select(Episode.id, Episode.season_number, Episode.episode_number).where(Episode.id.in_(missing))
        ).tuples():
            pairs[episode_id] = (season, number)

    def named(ids: list[int]) -> list[dict[str, int]]:
        return [
            {"season": pairs[episode_id][0], "episode": pairs[episode_id][1]}
            for episode_id in sorted(ids, key=lambda value: pairs.get(value, (0, 0)))
            if episode_id in pairs
        ]

    return {
        "episodes": named([episode_id for episode_id in pairs if episode_id not in missing]),
        "files": [
            {
                "episodes": named(item["episode_ids"]),
                "quality": item["quality"],
                "size_bytes": item["size_bytes"],
                "replaced": item["replaced"],
            }
            for item in log
        ],
    }


def _manual_result(
    context: Context, videos: list[episodes.Video], manual: dict[int, tuple[int, ...]]
) -> episodes.Result:
    """The owner's assignment by row id: a mapped video is filed with its episodes, one mapped to nothing is not filed,
    every other video keeps its decision (an open one stays open for a later assignment)."""
    stored = {item.path: item for item in context.stored}
    result = episodes.Result()
    for video in videos:
        row = stored.get(video.path)
        chosen = manual.get(row.id) if row is not None else None
        if chosen and row is not None:
            result.files.append(episodes.Decision(video, None, episodes.FILED, tuple(chosen), row.part))
        elif row is not None and chosen is not None:
            result.files.append(episodes.Decision(video, None, "not_filed", ()))
        elif row is not None:
            result.files.append(episodes.Decision(video, None, row.decision, row.episode_ids))
    covered = {item for decision in result.filed for item in decision.episode_ids}
    covered |= {item for item, state in context.states.items() if state == "filed"}
    result.uncovered = tuple(sorted(set(context.expected) - covered))
    return result


def _tell_media_servers(context: Context, root: Path) -> None:
    """After the commit, in a thread of its own: a media server never holds up or fails an import. The series folder is
    told, so a replaced episode and a new season folder are both seen."""
    try:
        folder, _name = series_folder(context, root)
    except Problem:
        return
    mediaserver_notify.request("series", [folder])


def _write_companions(context: Context) -> None:
    """The ``release.nex`` of every season folder the download touched (S4.7). Never fails the filing."""
    if context.version_id is None:
        return
    try:
        from .. import companions_series

        companions_series.write_version(context.version_id)
    except Exception:  # noqa: BLE001 - the episodes are filed; release.nex must never undo that
        logger.warning("Download %d: writing release.nex failed unexpectedly", context.download_id)


async def _clean_usenet(context: Context, found: Found, root: Path) -> None:
    """SABnzbd: the job folder goes when it holds no video but this download's own left-over ones."""
    target = context.target
    if target is None or not downloaders.is_usenet(target.kind):
        return
    folder = found.job if found.job.is_dir() else found.job.parent
    mount = folders.containing_mount(files.resolved(folder) or folder)
    own = {path for path, from_archive in found.paths.values() if not from_archive}
    removed = mount is not None and files.remove_job_folder(
        folder, category=target.category, mount=mount, keep=root, known=own
    )
    if not removed:
        logger.info("Download %d: the job folder is not one to delete; it stays in the client", context.download_id)
        return
    async with downloaders.open_client(target) as client:
        await client.remove_imported(context.client_download_id)
    logger.info("Download %d: the job folder is deleted and the job left the client", context.download_id)


def after_finish(download_id: int) -> None:
    """After "Rest nicht ablegen" (decision 30): the unpack folder goes, and SABnzbd's job folder as after a filed
    download, holding no video but this download's own. A torrent keeps its files. Never raises."""
    try:
        with SessionLocal() as db:
            context = load(db, download_id)
            paths = [
                item.path for item in db.scalars(select(DownloadFile).where(DownloadFile.download_id == download_id))
            ]
        if context is None:
            return
        root = _root(context)
        unpacking.remove(root, download_id)
        if context.protocol != "usenet":
            return
        job = _local_job(context)
        base = job if job.is_dir() else job.parent
        own = [base.joinpath(*_parts(path)) for path in paths if not path.startswith(UNPACKED_PREFIX)]
        found = Found(
            videos=[], paths={index: (path, False) for index, path in enumerate(own)}, job=job, unpacked=None, count=0
        )
        asyncio.run(_clean_usenet(context, found, root))
    except (Problem, downloaders.ClientError, OSError) as exc:
        logger.info("Download %d: cleaning up after the rest was not filed failed: %s", download_id, type(exc).__name__)


