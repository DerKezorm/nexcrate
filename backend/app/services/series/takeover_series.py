"""Taking over a Sonarr connection (Ü1, decisions 1 to 12).

The jobs, the mapping of root folders by files, the blockers and the undo are the Radarr takeover's
(``services/takeover.py``); this module holds what differs for series:

* **Reading** is a Sonarr import (``sonarr_import.run``), then Sonarr's naming and media management. When Sonarr does
  not answer, the stored state serves, provided a run finished once and every version with files kept its series
  folder; otherwise the job ends with ``takeover_needs_import`` and Sonarr's code as ``reason``.
* **Samples** for the mapping are one file per series, spread over the series (decision 2): all samples in one series
  folder would prove nothing about the others. Then every episode file is looked at.
* **The takeover** writes in one transaction per connection: every version becomes nexcrate's own with its series
  folder as nexcrate sees it, its switches as in Sonarr (set by the owner), a rule for new episodes from Sonarr's
  ``monitorNewItems`` (decision 5), its found files with ``taken:<Sonarr's file id>`` and nexcrate's judgement, its
  season folders from the paths; missing files go with their links. ``own_since`` is now, ``files_read_at`` empty
  while the series folder exists.
* **Afterwards**, phase ``reading_folders``: every taken-over version's series folder is read for files Sonarr did not
  know (``folder_read``); phase ``companions``: ``release.nex`` per season folder. Neither fails the takeover.
* **Undo** removes the files nexcrate knows of itself (they are not Sonarr's) and nexcrate's ``release.nex`` entries of
  those season folders, while unchanged, before the import feeds the versions again.

Log lines carry ids, counts and codes, never titles, names or paths.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session as OrmSession

from ... import __version__, crypto
from ...db import SessionLocal
from ...models import (
    Episode,
    EpisodeFile,
    EpisodeVersion,
    HistoryEntry,
    ImportRun,
    SeasonFolder,
    SeasonVersion,
    Source,
    Title,
    Version,
    VersionDefinition,
    utcnow,
)
from .. import companions, companions_series, folders, naming_series, releases, tags, takeover
from ..downloads import files
from ..profiles import series as series_profile
from ..profiles import store as profile_store
from ..radarr import MediaManagement, RadarrError, SourceUrlInvalid
from ..releases import series_decision
from ..schreibweisen import nfc
from ..sonarr import SeriesNamingConfig, SonarrClient
from . import folder_read, sonarr_import, watching
from .parts import as_whole

logger = logging.getLogger("nexcrate.takeover")

QUEUE_STATES = ("downloading", "problem")
MISSING_EXAMPLES = 20


# --- Naming (Ü2, decision 13) --------------------------------------------------------------------------------------- #


def source_naming(config: SeriesNamingConfig, current_style: str) -> dict[str, Any]:
    """Sonarr's naming as nexcrate would take it for a series version, the first code of nexcrate's check per pattern,
    and notes. Without renaming Sonarr keeps the release name, which is what ``{Original Title}`` renders."""
    patterns = {
        "series_folder": nfc(config.series_folder),
        "season_folder": nfc(config.season_folder),
        "specials_folder": nfc(config.specials_folder),
        "episode_file": nfc(config.episode_file) if config.rename_episodes else "{Original Title}",
        "daily_file": nfc(config.daily_file) if config.rename_episodes else "{Original Title}",
        # Sonarr's anime episode format (A5).
        "anime_file": nfc(config.anime_file) if config.rename_episodes else "{Original Title}",
    }
    problems = {which: naming_series.problem_of(pattern, which) for which, pattern in patterns.items()}
    notes: list[dict[str, Any]] = []
    if not config.rename_episodes:
        notes.append({"code": "sonarr_no_rename", "values": {}})
    for which, pattern in patterns.items():
        if "/" in pattern or "\\" in pattern:
            notes.append({"code": "slash_in_pattern", "values": {"pattern": which}})
    if config.colon_replacement and config.colon_replacement != "smart":
        notes.append({"code": "colon_format", "values": {"format": config.colon_replacement}})
    if not config.replace_illegal_characters:
        notes.append({"code": "illegal_characters_kept", "values": {}})
    if config.multi_episode_style and config.multi_episode_style != current_style:
        notes.append({"code": "style_differs", "values": {"style": config.multi_episode_style}})
    return {
        **patterns,
        "rename_episodes": config.rename_episodes,
        "colon_replacement": config.colon_replacement,
        "multi_episode_style": config.multi_episode_style,
        "problems": problems,
        "can_take": all(problem is None for problem in problems.values()),
        "notes": notes,
    }


def media_notes(media: MediaManagement | None) -> list[dict[str, Any]]:
    """Radarr's notes with Sonarr's codes (decision 12)."""
    return [
        {"code": "sonarr_" + note["code"].removeprefix("radarr_"), "values": note["values"]}
        for note in takeover.media_notes(media)
    ]


async def _extras(
    url: str, key: str
) -> tuple[SeriesNamingConfig | None, str | None, MediaManagement | None, list[int] | None]:
    """Sonarr's naming and media management, and the series its queue still holds an item for."""
    naming: SeriesNamingConfig | None = None
    naming_error: str | None = None
    media: MediaManagement | None = None
    queued: list[int] | None = None
    async with SonarrClient(url, key) as sonarr:
        try:
            naming = await sonarr.naming()
        except RadarrError as exc:
            naming_error = exc.code
        try:
            media = await sonarr.media_management()
        except RadarrError:
            media = None
        try:
            queued = [item.series_id for item in await sonarr.queue() if item.series_id is not None]
        except RadarrError:
            queued = None
    return naming, naming_error, media, queued


# --- What the job looks at ------------------------------------------------------------------------------------------ #


def item_of(version: Version, key: int, relative_path: str | None, size: int, has_file: bool) -> takeover.Item:
    """A ``takeover.Item`` for one file of a series version (or the version without a file): ``version_id`` holds
    ``key``, the series folder stands where the movie folder stands."""
    series = files.remote_parts(version.source_series_path) if version.source_series_path else None
    relative = files.remote_parts(relative_path) if relative_path else None
    root: str | None = None
    kind = ""
    root_names: tuple[str, ...] = ()
    series_names: tuple[str, ...] = ()
    if series is not None and series[1]:
        kind, names = series
        stored = files.remote_parts(version.root_folder) if version.root_folder else None
        below = len(names) - 1
        if (
            stored is not None
            and stored[0] == kind
            and len(stored[1]) < len(names)
            and [nfc(name) for name in names[: len(stored[1])]] == [nfc(name) for name in stored[1]]
        ):
            below = len(stored[1])
        # A series folder outside every root folder counts under its parent folder.
        root_names, series_names = tuple(names[:below]), tuple(names[below:])
        root = files.join_remote(kind, list(root_names))
    return takeover.Item(
        version_id=key,
        title_id=version.title_id,
        has_file=has_file,
        size=size,
        state=version.state,
        root=root,
        root_kind=kind,
        root_names=root_names,
        movie_names=series_names,
        file_names=tuple(relative[1]) if relative is not None else (),
    )


@dataclass
class Looked:
    series: list[takeover.Item]
    episode_files: list[takeover.Item]
    #: Episode file id to its version id.
    version_of: dict[int, int]
    #: Version id to its series item.
    series_of: dict[int, takeover.Item]


def items(db: OrmSession, source_id: int) -> Looked:
    versions = list(db.scalars(select(Version).where(Version.source_id == source_id).order_by(Version.id)))
    rows: dict[int, list[EpisodeFile]] = defaultdict(list)
    ids = [version.id for version in versions]
    if ids:
        for row in db.scalars(
            select(EpisodeFile)
            .where(EpisodeFile.version_id.in_(ids), EpisodeFile.source_file_id.is_not(None))
            .order_by(EpisodeFile.id)
        ):
            rows[row.version_id].append(row)
    looked = Looked([], [], {}, {})
    for version in versions:
        own = rows.get(version.id, [])
        first = own[0] if own else None
        series_item = item_of(
            version,
            version.id,
            first.relative_path if first is not None else None,
            int(first.size or 0) if first is not None else 0,
            first is not None,
        )
        looked.series.append(series_item)
        looked.series_of[version.id] = series_item
        for row in own:
            looked.episode_files.append(item_of(version, row.id, row.relative_path, int(row.size or 0), True))
            looked.version_of[row.id] = version.id
    return looked


def _read(job: takeover.Job, run_id: int) -> dict[str, Any]:
    """Read Sonarr as an import does, then its naming and media management; else the stored state."""
    sonarr_import.run(job.source_id, run_id)
    with SessionLocal() as db:
        source = db.get(Source, job.source_id)
        run = db.get(ImportRun, run_id)
        if source is None:
            raise takeover.JobFailed("not_found")
        if source.taken_over_at is not None:
            raise takeover.JobFailed("source_taken_over")
        url, stored_key = source.url, source.api_key
        if run is not None and run.status == "done":
            fresh = {"data_from": "fresh", "read_at": run.finished_at}
        else:
            code = run.error_code if run is not None else None
            last = db.scalar(
                select(ImportRun)
                .where(ImportRun.source_id == source.id, ImportRun.status == "done", ImportRun.id != run_id)
                .order_by(ImportRun.id.desc())
                .limit(1)
            )
            if last is None:
                raise takeover.JobFailed(code or "takeover_needs_import")
            with_files = select(EpisodeFile.version_id).where(EpisodeFile.source_file_id.is_not(None))
            lacking = db.scalar(
                select(func.count(Version.id)).where(
                    Version.source_id == source.id, Version.source_series_path.is_(None), Version.id.in_(with_files)
                )
            )
            if lacking:
                raise takeover.JobFailed("takeover_needs_import", reason=code)
            return {
                "data_from": "stored",
                "read_at": last.finished_at,
                "naming": None,
                "naming_error": code,
                "media": None,
                "queued": None,
            }
    key = crypto.decrypt(stored_key)
    naming: SeriesNamingConfig | None = None
    naming_error: str | None = "source_key_missing" if not key else None
    media: MediaManagement | None = None
    queued: list[int] | None = None
    if key:
        try:
            naming, naming_error, media, queued = asyncio.run(_extras(url, key))
        except RadarrError, SourceUrlInvalid:
            naming_error = naming_error or "sonarr_http_error"
    return {**fresh, "naming": naming, "naming_error": naming_error, "media": media, "queued": queued}


def _queue(db: OrmSession, source_id: int, queued: list[int] | None) -> int:
    """Items Sonarr's queue holds for series of this connection; without its answer, the stored states.

    ⚠️ Counted from Sonarr's own answer: an item Sonarr cannot finish right now (``downloadClientUnavailable``,
    measured on the bench on 17.09.2026) is no state of nexcrate's, and Sonarr files it once its client is back.
    """
    if queued is not None:
        series_ids = set(
            db.scalars(
                select(Version.sonarr_series_id).where(
                    Version.source_id == source_id, Version.sonarr_series_id.is_not(None)
                )
            )
        )
        return sum(1 for series_id in queued if series_id in series_ids)
    return int(
        db.scalar(
            select(func.count())
            .select_from(EpisodeVersion)
            .join(Version, Version.id == EpisodeVersion.version_id)
            .where(Version.source_id == source_id, EpisodeVersion.queue_state.in_(QUEUE_STATES))
        )
        or 0
    )


def _current(row: EpisodeFile, episodes: int, size: int | None = None) -> releases.CurrentEpisodeFile:
    return releases.CurrentEpisodeFile(
        release_title=row.release_title,
        name=row.relative_path,
        quality=row.quality,
        release_type=row.release_type,
        size_bytes=as_whole(size if size is not None else row.size, row.part),
        episode_count=max(1, episodes),
        file_id=row.id,
        languages=releases.stored_languages(row.languages),
    )


def _rules(db: OrmSession, definition_id: int) -> dict[str, Any] | None:
    """The version's series rules; each judgement picks the anime rules for an anime series (``rules_for``)."""
    profile = profile_store.of_version(db, definition_id)
    rules = profile_store.series_rules(profile, None)
    return rules if rules is not None and rules.get("kind") == "series" else None


def _counts(db: OrmSession, source_id: int, found: dict[int, takeover.Found]) -> dict[str, Any]:
    """Unclear files and the files nexcrate's rules would upgrade (null without series rules)."""
    source = db.get(Source, source_id)
    rules = _rules(db, source.version_id) if source is not None else None
    versions = select(Version.id).where(Version.source_id == source_id)
    linked: dict[int, int] = defaultdict(int)
    for (file_id,) in db.execute(
        select(EpisodeVersion.episode_file_id).where(
            EpisodeVersion.version_id.in_(versions), EpisodeVersion.episode_file_id.is_not(None)
        )
    ).tuples():
        linked[int(file_id)] += 1
    unclear = 0
    would_upgrade = 0
    for row, original, series_type in db.execute(
        select(EpisodeFile, Title.original_language, Title.series_type)
        .join(Version, Version.id == EpisodeFile.version_id)
        .join(Title, Title.id == Version.title_id)
        .where(Version.source_id == source_id, EpisodeFile.source_file_id.is_not(None))
    ).tuples():
        if row.id not in linked:
            unclear += 1 if not row.left_out else 0
            continue
        located = found.get(row.id)
        if rules is None or located is None:
            continue
        judged = series_decision.judge_episode_file(
            series_profile.rules_for(rules, series_type), _current(row, linked[row.id], located.size), original
        )
        would_upgrade += 1 if judged is not None and judged.reason is not None else 0
    return {"unclear": unclear, "would_upgrade": would_upgrade if rules is not None else None}


# --- The work ------------------------------------------------------------------------------------------------------- #


def work(job: takeover.Job, request: takeover.Request, run_id: int) -> dict[str, Any]:
    read = _read(job, run_id)
    with SessionLocal() as db:
        source = db.get(Source, job.source_id)
        if source is None:
            raise takeover.JobFailed("not_found")
        looked = items(db, source.id)
        queue = _queue(db, source.id, read["queued"])
        current_style = naming_series.load(db).style

    job.set(phase="mapping")
    sight = takeover.Sight()
    roots = takeover.map_roots(sight, looked.series, request.mappings)
    # The files of a series count under the root folder of its series.
    for state in roots.values():
        state.files = sum(1 for item in looked.episode_files if item.root == state.remote and item.locatable)
    checked = takeover.look_at_files(job, sight, looked.episode_files, roots)
    with SessionLocal() as db:
        source = db.get(Source, job.source_id)
        if source is None:
            raise takeover.JobFailed("not_found")
        version = takeover._version_brief(db, source, roots)
        # A series profile: the movie check of the brief does not know series rules.
        version["has_profile"] = _rules(db, source.version_id) is not None
        counts = _counts(db, source.id, checked.found)
    naming_out = source_naming(read["naming"], current_style) if read["naming"] is not None else None
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
        "app": "sonarr",
        "data_from": read["data_from"],
        "read_at": read["read_at"],
        "movies": 0,
        "series": len(looked.series),
        "with_file": sum(1 for item in looked.series if item.has_file),
        "episode_files": len(looked.episode_files),
        "roots": [state.out() for state in sorted(roots.values(), key=lambda state: state.remote)],
        "files_found": len(checked.found),
        "files_missing": len(checked.missing),
        "files_other_size": checked.other_size,
        "missing_examples": ["/".join(item.below_root) for item in checked.missing if item.locatable][
            :MISSING_EXAMPLES
        ],
        "queue": queue,
        "unclear": counts["unclear"],
        "would_upgrade": counts["would_upgrade"],
        "version": version,
        "naming": naming_out,
        "notes": media_notes(read["media"]),
        "blockers": blockers,
        "taken": None,
        "companions": None,
    }
    logger.info(
        "Takeover %s %d of Sonarr source %d: %s data, %d series, %d files found, %d missing, %d of other size, %d root "
        "folders, %d unmapped, %d queue items, %d unclear",
        job.kind,
        job.id,
        job.source_id,
        read["data_from"],
        len(looked.series),
        len(checked.found),
        len(checked.missing),
        checked.other_size,
        len(roots),
        len(unmapped),
        queue,
        counts["unclear"],
    )
    if job.kind == "check":
        return result

    if unmapped:
        raise takeover.JobFailed("takeover_root_unmapped", remote=unmapped[0].remote)
    if checked.missing and not request.accept_missing:
        raise takeover.JobFailed("takeover_files_missing", count=len(checked.missing))
    if queue and not request.accept_queue:
        raise takeover.JobFailed("takeover_queue_active", count=queue)
    patterns: dict[str, str] | None = None
    if request.take_naming:
        if naming_out is None:
            raise takeover.JobFailed(read["naming_error"] or "sonarr_http_error")
        for problem in naming_out["problems"].values():
            if problem is not None:
                raise takeover.JobFailed(problem["code"], **problem["values"])
        patterns = {which: naming_out[which] for which in naming_series.PATTERNS}
    job.set(phase="saving")
    places = series_places(sight, looked, roots)
    result["taken"] = save(job.source_id, request, checked.found, looked, places, patterns)
    result["companions"] = after_save(job, result["taken"]["version_ids"])
    result["taken"].pop("version_ids")
    return result


def series_places(
    sight: takeover.Sight, looked: Looked, roots: dict[str, takeover.RootState]
) -> dict[int, tuple[str, str, bool]]:
    """Per version its folder as nexcrate sees it: the folder that holds the series folder, the series folder's name as
    on disk (or as Sonarr names it when it is not there), and whether it exists."""
    places: dict[int, tuple[str, str, bool]] = {}
    for version_id, item in looked.series_of.items():
        state = roots.get(item.root) if item.root is not None else None
        if state is None or state.local is None or not item.movie_names:
            continue
        local = sight.folder(state.local)
        if local is None:
            continue
        parent = sight.lookup(local, item.movie_names[:-1]) if len(item.movie_names) > 1 else local
        parent_folder = sight.folder(parent) if parent is not None else None
        if parent_folder is None:
            continue
        found = sight.lookup(parent_folder, item.movie_names[-1:])
        exists = found is not None and sight.folder(found) is not None
        places[version_id] = (
            str(parent_folder),
            found.name if exists and found is not None else item.movie_names[-1],
            exists,
        )
    return places


def _rule_of(details: dict[str, Any] | None) -> str:
    """Decision 5: new seasons and episodes as Sonarr would watch them."""
    details = details or {}
    return "all" if details.get("monitored") and details.get("monitor_new_items") == "all" else "none"


def save(
    source_id: int,
    request: takeover.Request,
    found: dict[int, takeover.Found],
    looked: Looked,
    places: dict[int, tuple[str, str, bool]],
    patterns: dict[str, str] | None,
) -> dict[str, Any]:
    """The takeover's one transaction. Returns the counts of ``taken`` and the version ids for the phases after."""
    moment = takeover.now()
    on = watching.today()
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
        rules = _rules(db, definition.id)
        versions = list(db.scalars(select(Version).where(Version.source_id == source.id).order_by(Version.id)))
        version_ids = [version.id for version in versions]
        with_files = missing = 0
        for version in versions:
            place = places.get(version.id)
            version.source_id = None
            version.sonarr_series_id = None
            version.profile_name = None
            version.source_series_path = None
            if place is not None:
                version.root_folder, version.relative_path = place[0], place[1] if place[2] else None
            else:
                version.root_folder, version.relative_path = None, None
            version.watch_rule = _rule_of(version.source_details)
            version.watch_from_season = None
            version.own_since = moment
            version.files_read_at = None if version.relative_path else moment
            version.updated_at = moment
            db.execute(
                update(EpisodeVersion)
                .where(EpisodeVersion.version_id == version.id)
                .values(in_source=None, queue_state=None, progress=None, problem_code=None),
                execution_options={"synchronize_session": False},
            )
            db.execute(
                update(EpisodeVersion)
                .where(EpisodeVersion.version_id == version.id, EpisodeVersion.set_by == "source")
                .values(set_by="owner"),
                execution_options={"synchronize_session": False},
            )
            db.execute(
                update(SeasonVersion).where(SeasonVersion.version_id == version.id).values(set_by="owner"),
                execution_options={"synchronize_session": False},
            )
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

        titles = {version.id: version.title_id for version in versions}
        languages = (
            dict(
                db.execute(select(Title.id, Title.original_language).where(Title.id.in_(set(titles.values()))))
                .tuples()
                .all()
            )
            if titles
            else {}
        )
        series_types = (
            dict(
                db.execute(select(Title.id, Title.series_type).where(Title.id.in_(set(titles.values()))))
                .tuples()
                .all()
            )
            if titles
            else {}
        )
        links: dict[int, list[int]] = defaultdict(list)
        if version_ids:
            for episode_id, file_id in db.execute(
                select(EpisodeVersion.episode_id, EpisodeVersion.episode_file_id).where(
                    EpisodeVersion.version_id.in_(version_ids), EpisodeVersion.episode_file_id.is_not(None)
                )
            ).tuples():
                links[int(file_id)].append(episode_id)
        seasons_of: dict[int, int] = {}
        if links:
            episode_ids = [episode_id for ids in links.values() for episode_id in ids]
            for index in range(0, len(episode_ids), 500):
                chunk = episode_ids[index : index + 500]
                seasons_of.update(
                    db.execute(select(Episode.id, Episode.season_number).where(Episode.id.in_(chunk))).tuples().all()
                )
        gone: list[int] = []
        folders_of: dict[int, dict[int, str]] = defaultdict(dict)
        rows = (
            list(
                db.scalars(
                    select(EpisodeFile).where(
                        EpisodeFile.version_id.in_(version_ids), EpisodeFile.source_file_id.is_not(None)
                    )
                )
            )
            if version_ids
            else []
        )
        for row in rows:
            located = found.get(row.id)
            place = places.get(row.version_id)
            if located is None or place is None or not place[2]:
                gone.append(row.id)
                missing += 1
                continue
            series_prefix = Path(place[0], place[1])
            try:
                relative = Path(located.root_folder, located.relative_path).relative_to(series_prefix).as_posix()
            except ValueError:
                gone.append(row.id)
                missing += 1
                continue
            row.relative_path = relative[:2048]
            row.size = located.size
            row.file_ref = f"taken:{row.source_file_id or 0}"[:64]
            row.updated_at = moment
            episodes = links.get(row.id, [])
            if rules is not None and episodes:
                judged = series_decision.judge_episode_file(
                    series_profile.rules_for(rules, series_types.get(titles.get(row.version_id))),
                    _current(row, len(episodes)),
                    languages.get(titles.get(row.version_id)),
                )
                row.cutoff_not_met = judged is not None and judged.reason is not None
            else:
                row.cutoff_not_met = False
            with_files += 1
            parts = relative.split("/")
            numbers = {seasons_of[episode_id] for episode_id in episodes if episode_id in seasons_of}
            if len(parts) == 2 and len(numbers) == 1:
                (number,) = numbers
                folders_of[row.version_id].setdefault(number, parts[0])
        for index in range(0, len(gone), 500):
            chunk = gone[index : index + 500]
            db.execute(
                update(EpisodeVersion).where(EpisodeVersion.episode_file_id.in_(chunk)).values(episode_file_id=None),
                execution_options={"synchronize_session": False},
            )
            db.execute(
                delete(EpisodeFile).where(EpisodeFile.id.in_(chunk)), execution_options={"synchronize_session": False}
            )
        for version_id, by_season in folders_of.items():
            known = set(db.scalars(select(SeasonFolder.season_number).where(SeasonFolder.version_id == version_id)))
            for number, name in by_season.items():
                if number not in known:
                    db.add(SeasonFolder(version_id=version_id, season_number=number, name=name))
        db.flush()
        for version in versions:
            watching.recount(db, version, on)
        source.taken_over_at = moment
        # The connection's tags become the owner's.
        tags.release(db, source.id)
        source.updated_at = moment
        if patterns is not None:
            naming_series.set_version(definition, patterns, definition.episode_numbering)
        from ..automatic import clock as automatic_clock
        from ..automatic import planning as automatic_planning

        automatic_planning.replan(db, set(titles.values()), automatic_clock.now())
        db.commit()
    logger.info(
        "Sonarr source %d taken over: %d versions, %d files kept, %d files missing",
        source_id,
        len(version_ids),
        with_files,
        missing,
    )
    return {
        "versions": len(version_ids),
        "with_file": with_files,
        "missing": missing,
        "titles": 0,
        "version_ids": version_ids,
    }


def after_save(job: takeover.Job, version_ids: list[int]) -> dict[str, int]:
    """The phases after the commit: read the series folders, then ``release.nex``. Never fails the takeover."""
    counts: dict[str, int] = {}
    try:
        job.set(phase="reading_folders", done=0, total=len(version_ids))
        for index, version_id in enumerate(version_ids, start=1):
            with SessionLocal() as db:
                row = db.get(Version, version_id)
                pending = row is not None and row.source_id is None and row.files_read_at is None
            if pending:
                folder_read.read_tracked(version_id)
            job.set(done=index)
        # The second halves of double episodes Sonarr counts apart (D4).
        from . import halves

        for version_id in version_ids:
            with SessionLocal() as db:
                row = db.get(Version, version_id)
                if row is not None and halves.link(db, row):
                    db.commit()
        job.set(phase="companions", done=0, total=len(version_ids))
        for index, version_id in enumerate(version_ids, start=1):
            for state, count in companions_series.write_version(version_id).items():
                counts[state] = counts.get(state, 0) + count
            job.set(done=index)
    except Exception:  # the takeover is committed
        logger.exception("Takeover %d of Sonarr source %d: a phase after saving stopped", job.id, job.source_id)
    return counts


# --- Undo ----------------------------------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SeasonRemoval:
    folder: Path
    label: str
    sha256: str
    version_id: int
    season: int


def prepare_undo(db: OrmSession, versions: list[Version]) -> list[SeasonRemoval]:
    """In undo's transaction: the files nexcrate knows of itself go (Sonarr's import brings its own), the version is no
    longer nexcrate's. Returns the ``release.nex`` entries to remove after the commit."""
    removals: list[SeasonRemoval] = []
    enabled = companions.enabled(db)
    for version in versions:
        definition = db.get(VersionDefinition, version.version_definition_id)
        if enabled and definition is not None:
            removed: set[str] = set()
            for season in db.scalars(select(SeasonFolder).where(SeasonFolder.version_id == version.id)):
                folder = companions_series._folder(version, season.name)
                # Season folders sharing one folder on disk share its file: it is removed once.
                if folder is not None and season.companion_hash and season.name not in removed:
                    removed.add(season.name)
                    removals.append(
                        SeasonRemoval(folder, definition.label, season.companion_hash, version.id, season.season_number)
                    )
        own = select(EpisodeFile.id).where(EpisodeFile.version_id == version.id, EpisodeFile.source_file_id.is_(None))
        db.execute(
            update(EpisodeVersion)
            .where(EpisodeVersion.version_id == version.id, EpisodeVersion.episode_file_id.in_(own))
            .values(episode_file_id=None),
            execution_options={"synchronize_session": False},
        )
        db.execute(
            delete(EpisodeFile).where(EpisodeFile.version_id == version.id, EpisodeFile.source_file_id.is_(None)),
            execution_options={"synchronize_session": False},
        )
        db.execute(
            update(SeasonFolder)
            .where(SeasonFolder.version_id == version.id)
            .values(companion_state=None, companion_hash=None, companion_written_at=None),
            execution_options={"synchronize_session": False},
        )
        version.own_since = None
        version.files_read_at = None
    return removals


def remove_entries(removals: list[SeasonRemoval]) -> dict[str, int]:
    """After undo's commit: nexcrate's entries of those season folders, only while the file is unchanged since written.
    The file goes when no entry is left. Never raises."""
    counts = {"removed": 0, "kept": 0, "missing": 0, "failed": 0}
    try:
        installation = companions.installation_id()
        for removal in removals:
            with companions._lock_for(removal.folder):
                existing = companions_series.read(removal.folder, installation)
                if existing["outcome"] == companions.MISSING:
                    counts["missing"] += 1
                    continue
                if existing["outcome"] != companions.OURS or existing.get("sha256") != removal.sha256:
                    counts["kept"] += 1
                    continue
                left = [entry for entry in existing["entries"] if entry.get("version") != removal.label]
                try:
                    if left:
                        doc = {
                            "format": companions.FORMAT,
                            "format_version": companions.FORMAT_VERSION,
                            "written_at": companions._iso(utcnow()),
                            "written_by": {"app": "nexcrate", "version": __version__, "installation": installation},
                            "series": existing["series"],
                            "season": removal.season,
                            "entries": left,
                        }
                        data = companions.encode(doc)
                        companions.write_bytes(removal.folder, data)
                        _rehash(removal, hashlib.sha256(data).hexdigest())
                    elif not companions.remove_file(removal.folder):
                        counts["failed"] += 1
                        continue
                except companions._WriteFailed:
                    counts["failed"] += 1
                    continue
                counts["removed"] += 1
    except Exception:
        logger.exception("Removing release.nex entries after undoing a Sonarr takeover stopped")
    logger.info("release.nex entries of season folders removed after an undo: %s", companions._codes(counts))
    return counts


def _rehash(removal: SeasonRemoval, digest: str) -> None:
    """Season folders of other versions whose entries stay learn the new hash."""
    with SessionLocal() as db:
        version = db.get(Version, removal.version_id)
        if version is None:
            return
        for season in db.scalars(
            select(SeasonFolder)
            .join(Version, Version.id == SeasonFolder.version_id)
            .where(
                Version.title_id == version.title_id,
                Version.id != version.id,
                SeasonFolder.season_number == removal.season,
                SeasonFolder.companion_hash.is_not(None),
            )
        ):
            season.companion_hash = digest
        db.commit()
