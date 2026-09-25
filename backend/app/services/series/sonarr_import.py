"""Reading Sonarr into the library (S1.7, decisions 21 to 28).

One run per connection, started like a Radarr import (``importer.start`` and the 15-minute job hand a Sonarr connection
to ``run``):

1. Read ``system/status`` (Sonarr 4 or later), root folders, profiles, every series, per series its episodes and files,
   then the queue. Any failure ends the run with its code before the library changes.
2. Series by series: the TMDB number from Sonarr, else ``/find`` by TVDB, else by IMDb. A series nexcrate does not have
   yet gets TMDB's data now; one it has keeps it (the refresh job renews it). Then the version of this connection, the
   matching of Sonarr's episodes to TMDB's, Sonarr's TVDB and scene numbers, files, switches and queue states, all in
   one transaction per series.
3. Versions of series gone from Sonarr go, as for Radarr; titles left without versions go.

Without a TMDB token the run fails with ``tmdb_not_configured``: series need TMDB (decision 23). A TMDB error other
than an unknown series ends the run; series written so far stay.

Log lines carry ids and counts, never titles, names or paths.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session as OrmSession

from ... import crypto
from ...db import SessionLocal
from ...models import (
    AlternateTitle,
    Episode,
    EpisodeFile,
    EpisodeNumber,
    EpisodeVersion,
    HistoryEntry,
    ImportRun,
    Season,
    SeasonVersion,
    Source,
    SourceEpisode,
    Title,
    Version,
    VersionDefinition,
    utcnow,
)
from .. import images, importer, tags, tmdb
from ..radarr import RadarrError, RootFolder, SourceUrlInvalid
from ..schreibweisen import search_text
from ..sonarr import EpisodeFileItem, EpisodeItem, QualityProfile, QueueItem, SeriesItem, SonarrClient, SystemStatus
from . import absolute, matching, store, tmdb_series, watching

logger = logging.getLogger("nexcrate.import")

#: Series named in a run's result when TMDB does not know them.
NAMED_MAX = 20
#: The run's progress is written after this many series.
PROGRESS_EVERY = 10


@dataclass
class Fetched:
    status: SystemStatus
    root_folders: list[RootFolder]
    profiles: list[QualityProfile]
    series: list[SeriesItem]
    skipped: int
    episodes: dict[int, list[EpisodeItem]]
    files: dict[int, list[EpisodeFileItem]]
    queue: list[QueueItem]
    #: Tag id to name; None when Sonarr did not answer for them: the tags stay as they are then.
    tags: dict[int, str] | None = None


@dataclass
class Tally:
    titles_new: int = 0
    titles_updated: int = 0
    versions_total: int = 0
    versions_removed: int = 0
    not_on_tmdb: list[dict[str, Any]] = field(default_factory=list)
    not_on_tmdb_count: int = 0
    episodes_matched: int = 0
    episodes_unmatched: int = 0
    files_unmatched: int = 0
    steps: dict[str, int] = field(default_factory=lambda: dict.fromkeys(matching.STEPS, 0))

    def details(self, series: int, skipped: int) -> dict[str, Any]:
        return {
            "series": series,
            "skipped": skipped,
            "not_on_tmdb": self.not_on_tmdb,
            "not_on_tmdb_count": self.not_on_tmdb_count,
            "episodes_matched": self.episodes_matched,
            "episodes_unmatched": self.episodes_unmatched,
            "files_unmatched": self.files_unmatched,
            "steps": self.steps,
        }


def _progress(run_id: int, phase: str, done: int, total: int) -> None:
    with SessionLocal() as db:
        db.execute(
            update(ImportRun)
            .where(ImportRun.id == run_id, ImportRun.status == "running")
            .values(details={"phase": phase, "done": done, "total": total})
        )
        db.commit()


async def fetch(url: str, api_key: str, run_id: int | None = None) -> Fetched:
    async with SonarrClient(url, api_key) as sonarr:
        status = await sonarr.system_status()
        roots = await sonarr.root_folders()
        profiles = await sonarr.quality_profiles()
        series, skipped = await sonarr.series()
        episodes: dict[int, list[EpisodeItem]] = {}
        files: dict[int, list[EpisodeFileItem]] = {}
        for index, item in enumerate(series, start=1):
            episodes[item.id] = await sonarr.episodes(item.id)
            files[item.id] = await sonarr.episode_files(item.id)
            if run_id is not None and (index % PROGRESS_EVERY == 0 or index == len(series)):
                await asyncio.to_thread(_progress, run_id, "reading", index, len(series))
        queue = await sonarr.queue()
        try:
            names: dict[int, str] | None = await sonarr.tags()
        except RadarrError:
            names = None
    return Fetched(status, roots, profiles, series, skipped, episodes, files, queue, names)


def run(source_id: int, run_id: int) -> None:
    """Run a begun import of a Sonarr connection to its end. The caller releases the source."""
    started = time.perf_counter()
    with SessionLocal() as db:
        source = db.get(Source, source_id)
        if source is None:
            logger.info("Import of source %d stopped, the source was deleted", source_id)
            return
        url, stored_key = source.url, source.api_key
    logger.info("Import of Sonarr source %d started", source_id)
    api_key = crypto.decrypt(stored_key)
    if not api_key:
        logger.warning("Import of source %d failed, the stored API key cannot be read", source_id)
        importer._finish_failed(run_id, "source_key_missing")
        return
    configured, token = tmdb.token_state()
    if not configured or not token:
        code = "tmdb_not_configured" if not configured else "tmdb_token_unreadable"
        logger.warning("Import of source %d failed: %s", source_id, code)
        importer._finish_failed(run_id, code)
        return
    try:
        fetched = asyncio.run(fetch(url, api_key, run_id))
    except RadarrError as exc:
        logger.warning("Import of source %d failed: %s", source_id, exc.code)
        importer._finish_failed(run_id, exc.code, {k: v for k, v in exc.detail.items() if k not in ("code", "message")})
        return
    except SourceUrlInvalid:
        logger.warning("Import of source %d failed, the stored address is not valid", source_id)
        importer._finish_failed(run_id, "source_url_invalid")
        return
    if fetched.skipped:
        logger.warning("Source %d: %d series without an id were skipped", source_id, fetched.skipped)
    asyncio.run(_write_all(source_id, run_id, fetched, token, started))


async def _write_all(source_id: int, run_id: int, fetched: Fetched, token: str, started: float) -> None:
    locale = await asyncio.to_thread(tmdb.account_locale)
    tally = Tally()
    on = watching.today()
    seen_series: set[int] = set()
    seen_tmdb: set[int] = set()
    queue_by_episode: dict[int, list[QueueItem]] = defaultdict(list)
    for item in fetched.queue:
        if item.episode_id is not None:
            queue_by_episode[item.episode_id].append(item)
    total = len(fetched.series)
    for index, item in enumerate(fetched.series, start=1):
        seen_series.add(item.id)
        try:
            tmdb_id = item.tmdb_id or await tmdb_series.find_series(
                token, tvdb_id=item.tvdb_id, imdb_id=item.imdb_id, locale=locale
            )
            data = None
            if tmdb_id is not None and tmdb_id not in seen_tmdb and await asyncio.to_thread(_needs_tmdb, tmdb_id):
                data = await tmdb_series.fetch_series(token, tmdb_id, locale)
        except tmdb.TmdbError as exc:
            if exc.code != "not_found":
                logger.warning("Import of source %d stopped at series %d of %d: %s", source_id, index, total, exc.code)
                _fail_with(run_id, exc.code, tally, total, fetched.skipped)
                return
            tmdb_id = None
        if tmdb_id is None:
            tally.not_on_tmdb_count += 1
            if len(tally.not_on_tmdb) < NAMED_MAX:
                tally.not_on_tmdb.append({"title": item.title[:200], "tvdb_id": item.tvdb_id})
            continue
        if tmdb_id in seen_tmdb:
            # Two Sonarr series on one TMDB series: the first one feeds the version.
            logger.info("Source %d: Sonarr series %d shares its TMDB series with another one", source_id, item.id)
            continue
        seen_tmdb.add(tmdb_id)
        written = await asyncio.to_thread(
            _write_series,
            source_id,
            tmdb_id,
            item,
            fetched.episodes.get(item.id, []),
            fetched.files.get(item.id, []),
            queue_by_episode,
            fetched.root_folders,
            {profile.id: profile.name for profile in fetched.profiles},
            data,
            on,
            tally,
        )
        if not written:
            logger.info("Import of source %d stopped, the source was deleted or taken over", source_id)
            return
        if index % PROGRESS_EVERY == 0:
            await asyncio.to_thread(_progress, run_id, "series", index, total)
    tag_names = (
        {item.id: [fetched.tags[tag_id] for tag_id in item.tags if tag_id in fetched.tags] for item in fetched.series}
        if fetched.tags is not None
        else None
    )
    removed_titles = await asyncio.to_thread(
        _finish, source_id, run_id, seen_series, tally, total, fetched.skipped, tag_names
    )
    images.forget(removed_titles)
    importer.prune_runs(source_id)
    logger.info(
        "Import of Sonarr source %d done in %dms: %d series, %d titles new, %d updated, %d versions removed, "
        "%d without TMDB, %d episodes matched, %d unmatched, %d files without an episode, %d queue items",
        source_id,
        (time.perf_counter() - started) * 1000,
        total,
        tally.titles_new,
        tally.titles_updated,
        tally.versions_removed,
        tally.not_on_tmdb_count,
        tally.episodes_matched,
        tally.episodes_unmatched,
        tally.files_unmatched,
        len(fetched.queue),
    )


def _needs_tmdb(tmdb_id: int) -> bool:
    """TMDB is asked only for series nexcrate has no TMDB data of yet."""
    with SessionLocal() as db:
        refreshed = db.execute(
            select(Title.id, Title.tmdb_refreshed_at).where(Title.kind == "series", Title.tmdb_id == tmdb_id)
        ).first()
    return refreshed is None or refreshed.tmdb_refreshed_at is None


def _fail_with(run_id: int, code: str, tally: Tally, total: int, skipped: int) -> None:
    with SessionLocal() as db:
        db.execute(
            update(ImportRun)
            .where(ImportRun.id == run_id)
            .values(
                status="failed",
                error_code=code,
                finished_at=utcnow(),
                titles_new=tally.titles_new,
                titles_updated=tally.titles_updated,
                details=tally.details(total, skipped),
            )
        )
        db.commit()


def _root_of(path: str | None, roots: list[RootFolder]) -> str | None:
    if not path:
        return None
    for root in sorted((folder.path for folder in roots), key=len, reverse=True):
        trimmed = root.rstrip("/\\")
        if path == trimmed or path.startswith((trimmed + "/", trimmed + "\\")):
            return root
    return None


def _write_series(
    source_id: int,
    tmdb_id: int,
    item: SeriesItem,
    episodes: list[EpisodeItem],
    files: list[EpisodeFileItem],
    queue_by_episode: dict[int, list[QueueItem]],
    roots: list[RootFolder],
    profiles: dict[int, str | None],
    data: tmdb_series.SeriesData | None,
    on: str,
    tally: Tally,
) -> bool:
    moment = utcnow()
    with SessionLocal() as db:
        db.execute(update(Source).where(Source.id == source_id).values(updated_at=Source.updated_at))
        source = db.get(Source, source_id)
        if source is None or source.taken_over_at is not None:
            db.rollback()
            return False
        definition = db.get(VersionDefinition, source.version_id)
        if definition is None:
            db.rollback()
            return False
        title = db.scalar(select(Title).where(Title.kind == "series", Title.tmdb_id == tmdb_id))
        is_new = title is None
        if title is not None and item.series_type != title.series_type:
            # Sonarr keeps the type of a series it feeds (A6, N44 point 7).
            title.series_type = item.series_type
            title.updated_at = moment
        if title is None:
            if data is None:
                return True
            title = store.new_title(data, moment, series_type=item.series_type)
            title.added = moment
            db.add(title)
            db.flush()
            tally.titles_new += 1
        version = db.scalar(
            select(Version).where(Version.title_id == title.id, Version.version_definition_id == definition.id)
        )
        if version is None:
            version = Version(
                title_id=title.id,
                version_definition_id=definition.id,
                source_id=source.id,
                added_by="import",
                monitored=item.monitored,
                has_file=False,
                state="wanted",
                created_at=moment,
                updated_at=moment,
            )
            db.add(version)
            db.flush()
            db.add(
                HistoryEntry(
                    title_id=title.id,
                    version_id=version.id,
                    version_definition_id=definition.id,
                    version_label=definition.label,
                    event="added",
                    at=moment,
                    detail=None,
                )
            )
        elif version.source_id != source.id:
            # An owner's version in this definition that no source feeds: the connection takes it over.
            version.source_id = source.id
        before_files = set(db.scalars(select(EpisodeFile.source_file_id).where(EpisodeFile.version_id == version.id)))
        version.sonarr_series_id = item.id
        version.source_series_path = (item.path or "")[:4096] or None
        version.root_folder = (item.root_folder_path or _root_of(item.path, roots) or "")[:1024] or None
        profile = profiles.get(item.quality_profile_id) if item.quality_profile_id is not None else None
        version.profile_name = profile[:200] if profile else None
        version.source_details = {
            "monitored": item.monitored,
            "monitor_new_items": item.monitor_new_items,
            "season_folder": item.season_folder,
            "use_scene_numbering": item.use_scene_numbering,
        }
        version.updated_at = moment
        if data is not None:
            store.apply_series(db, title, data, moment, on)
        else:
            watching.sync_rows(db, version, on)
        _merge_alternates(db, title, item, source.id)
        outcome = _merge_episodes(db, title, version, source.name, item, episodes, files, queue_by_episode, on)
        tally.episodes_matched += outcome["matched"]
        tally.episodes_unmatched += outcome["unmatched"]
        tally.files_unmatched += outcome["files_unmatched"]
        for step, count in outcome["steps"].items():
            tally.steps[step] = tally.steps.get(step, 0) + count
        after_files = {file.id for file in files}
        new_files = len(after_files - {file_id for file_id in before_files if file_id is not None})
        if new_files:
            db.add(
                HistoryEntry(
                    title_id=title.id,
                    version_id=version.id,
                    version_definition_id=definition.id,
                    version_label=definition.label,
                    event="imported",
                    at=moment,
                    detail=f"{new_files} files",
                )
            )
        tally.versions_total += 1
        if not is_new and (new_files or data is not None):
            tally.titles_updated += 1
        db.commit()
    return True


def _merge_alternates(db: OrmSession, title: Title, item: SeriesItem, source_id: int) -> None:
    wanted: list[str] = []
    for name in [*item.alternate_titles, item.title]:
        if name and name[:1024] not in wanted:
            wanted.append(name[:1024])
    existing = {
        row.text: row
        for row in db.scalars(
            select(AlternateTitle).where(AlternateTitle.title_id == title.id, AlternateTitle.source_id == source_id)
        )
    }
    for text, row in existing.items():
        if text not in wanted:
            db.delete(row)
    for text in wanted:
        if text not in existing:
            db.add(AlternateTitle(title_id=title.id, source_id=source_id, text=text, search_keys=search_text([text])))


def _merge_episodes(
    db: OrmSession,
    title: Title,
    version: Version,
    source_name: str,
    item: SeriesItem,
    episodes: list[EpisodeItem],
    files: list[EpisodeFileItem],
    queue_by_episode: dict[int, list[QueueItem]],
    on: str,
) -> dict[str, Any]:
    """Match, then numbers, files, switches and queue states of one Sonarr series in one version."""
    db.flush()
    tmdb_rows = (
        db.execute(
            select(
                Episode.id,
                Episode.season_number,
                Episode.episode_number,
                Episode.air_date,
                Episode.name,
                Episode.tvdb_id,
                Episode.name_en,
            ).where(Episode.title_id == title.id, Episode.tmdb_gone_at.is_(None))
        )
        .tuples()
        .all()
    )
    tmdb_episodes = [
        matching.TmdbEpisode(
            id=row[0], season=row[1], episode=row[2], air_date=row[3], name=row[4], tvdb_id=row[5], name_en=row[6]
        )
        for row in tmdb_rows
    ]
    source_episodes = [
        matching.SourceEpisode(
            id=episode.id,
            season=episode.season,
            episode=episode.episode,
            air_date=episode.air_date,
            title=episode.title,
            tvdb_id=episode.tvdb_id,
            file_id=episode.episode_file_id,
        )
        for episode in episodes
    ]
    matched = matching.match(source_episodes, tmdb_episodes)
    by_source = {episode.id: episode for episode in episodes}
    targets: dict[int, list[EpisodeItem]] = defaultdict(list)
    for source_id, episode_id in matched.pairs.items():
        targets[episode_id].append(by_source[source_id])

    _merge_numbers(db, title, version, targets)
    # Sonarr's absolute numbers go first for an anime series (A1).
    absolute.store(db, title, utcnow())
    _merge_source_only(db, title, version, [by_source[source_id] for source_id in matched.unmatched])

    # Files.
    stored = {
        row.source_file_id: row
        for row in db.scalars(select(EpisodeFile).where(EpisodeFile.version_id == version.id))
        if row.source_file_id is not None
    }
    by_file_episodes: dict[int, list[EpisodeItem]] = defaultdict(list)
    for episode in episodes:
        if episode.episode_file_id is not None:
            by_file_episodes[episode.episode_file_id].append(episode)
    # Files gone from Sonarr leave first. An upgrade gives the new file a new id and often the same path, and the path
    # is unique per version: the new row must not go in while the old one still stands.
    current_ids = {file.id for file in files}
    gone_files = [row.id for file_id, row in stored.items() if file_id not in current_ids]
    if gone_files:
        db.execute(
            update(EpisodeVersion).where(EpisodeVersion.episode_file_id.in_(gone_files)).values(episode_file_id=None),
            execution_options={"synchronize_session": False},
        )
        db.execute(
            delete(EpisodeFile).where(EpisodeFile.id.in_(gone_files)), execution_options={"synchronize_session": False}
        )
        for file_id in [file_id for file_id in stored if file_id not in current_ids]:
            db.expunge(stored.pop(file_id))
    moment = utcnow()
    file_rows: dict[int, EpisodeFile] = {}
    for file in files:
        numbers = sorted(by_file_episodes.get(file.id, []), key=lambda episode: (episode.season, episode.episode))
        values = {
            "relative_path": (file.relative_path or f"sonarr-file-{file.id}")[:2048],
            "size": file.size,
            "quality": file.quality[:100] if file.quality else None,
            "languages": list(file.languages),
            "release_group": file.release_group[:200] if file.release_group else None,
            "release_title": file.scene_name[:1024] if file.scene_name else None,
            "release_type": file.release_type,
            "cutoff_not_met": file.cutoff_not_met,
            "media_info": file.media_info,
            "source_numbers": {
                "season": numbers[0].season if numbers else file.season,
                "episodes": [episode.episode for episode in numbers],
            },
        }
        row = stored.pop(file.id, None)
        if row is None:
            row = EpisodeFile(
                version_id=version.id, source_file_id=file.id, added_at=file.date_added, updated_at=moment, **values
            )
            db.add(row)
        else:
            for name, value in values.items():
                if getattr(row, name) != value:
                    setattr(row, name, value)
                    row.updated_at = moment
        file_rows[file.id] = row
    db.flush()

    # Switches, files and queue states per TMDB episode.
    rows = {
        row.episode_id: row for row in db.scalars(select(EpisodeVersion).where(EpisodeVersion.version_id == version.id))
    }
    linked_files: set[int] = set()
    for episode_id, row in rows.items():
        sources = targets.get(episode_id, [])
        row.set_by = "source"
        row.in_source = bool(sources)
        row.watched = any(source.monitored for source in sources)
        file_id = next((source.episode_file_id for source in sources if source.episode_file_id is not None), None)
        file_row = file_rows.get(file_id) if file_id is not None else None
        row.episode_file_id = file_row.id if file_row is not None else None
        if file_row is not None:
            linked_files.add(file_row.id)
        queued = [record for source in sources for record in queue_by_episode.get(source.id, [])]
        state = importer.compute_state(
            monitored=row.watched, has_file=file_row is not None, cutoff_not_met=False, queue=queued
        )
        if state.state in ("problem", "downloading"):
            row.queue_state = state.state
            row.progress = state.progress
            row.problem_code = state.problem_code
        else:
            row.queue_state = None
            row.progress = None
            row.problem_code = None
    for season_row, season_id in db.execute(
        select(SeasonVersion, SeasonVersion.season_id).where(SeasonVersion.version_id == version.id)
    ).tuples():
        episode_ids = [episode_id for episode_id, row in rows.items() if row.watched]
        season_row.watched = bool(
            episode_ids
            and db.scalar(
                select(Episode.id).where(Episode.season_id == season_id, Episode.id.in_(episode_ids)).limit(1)
            )
        )
        season_row.set_by = "source"
    title.numbering_note = matching.numbering_note(source_name, source_episodes, tmdb_episodes, matched)
    watching.recount(db, version, on)
    return {
        "matched": len(matched.pairs),
        "unmatched": len(matched.unmatched),
        "files_unmatched": len([row for row in file_rows.values() if row.id not in linked_files]),
        "steps": matched.counts(),
    }


def _merge_source_only(db: OrmSession, title: Title, version: Version, unmatched: list[EpisodeItem]) -> None:
    """Sonarr's episodes no TMDB episode matched, visible with the version (decision 47).
    Replaced on every run; they get no episode of their own."""
    db.execute(
        delete(SourceEpisode).where(SourceEpisode.version_id == version.id),
        execution_options={"synchronize_session": False},
    )
    for episode in sorted(unmatched, key=lambda item: (item.season, item.episode, item.id)):
        db.add(
            SourceEpisode(
                title_id=title.id,
                version_id=version.id,
                source_id=episode.id,
                season=episode.season,
                episode=episode.episode,
                name=(episode.title or "")[:1024],
                air_date=episode.air_date,
                watched=episode.monitored,
                has_file=episode.episode_file_id is not None,
            )
        )


def _merge_numbers(db: OrmSession, title: Title, version: Version, targets: dict[int, list[EpisodeItem]]) -> None:
    """Sonarr's TVDB and scene numbers on the matched TMDB episodes; numbers of episodes no longer matched go, unless
    another connection still feeds the title."""
    moment = utcnow()
    # One row per episode and scheme. TheXEM's numbers replace Sonarr's (plan S3): a row another origin holds stays as
    # it is, and Sonarr adds no second row next to it.
    existing = {
        (row.episode_id, row.scheme): row
        for row in db.scalars(
            select(EpisodeNumber).where(EpisodeNumber.title_id == title.id, EpisodeNumber.scheme.in_(("tvdb", "scene")))
        )
    }
    kept: set[tuple[int, str]] = set()
    for episode_id, sources in targets.items():
        ordered = sorted(sources, key=lambda episode: (episode.season, episode.episode))
        first, last = ordered[0], ordered[-1]
        wanted: dict[str, dict[str, Any]] = {
            "tvdb": {
                "season": first.season,
                "episode": first.episode,
                "episode_end": last.episode if len(ordered) > 1 and last.season == first.season else None,
                "absolute": first.absolute,
                "verified": True,
            }
        }
        if first.scene_season is not None and first.scene_episode is not None:
            wanted["scene"] = {
                "season": first.scene_season,
                "episode": first.scene_episode,
                "episode_end": last.scene_episode
                if len(ordered) > 1 and last.scene_episode != first.scene_episode
                else None,
                "absolute": first.scene_absolute,
                "verified": not first.unverified_scene,
            }
        for scheme, values in wanted.items():
            kept.add((episode_id, scheme))
            row = existing.get((episode_id, scheme))
            if row is not None and row.origin != "sonarr":
                continue
            if row is None:
                db.add(
                    EpisodeNumber(
                        episode_id=episode_id,
                        title_id=title.id,
                        scheme=scheme,
                        origin="sonarr",
                        updated_at=moment,
                        **values,
                    )
                )
                continue
            for name, value in values.items():
                if getattr(row, name) != value:
                    setattr(row, name, value)
                    row.updated_at = moment
        if len(ordered) == 1 and first.tvdb_id is not None:
            episode = db.get(Episode, episode_id)
            if episode is not None and episode.tvdb_id != first.tvdb_id:
                # A number the title veto moved leaves the episode that held it: two holders pair neither again.
                for holder in db.scalars(
                    select(Episode).where(
                        Episode.title_id == title.id, Episode.tvdb_id == first.tvdb_id, Episode.id != episode_id
                    )
                ):
                    # The session does not flush on its own: a holder given its new number in this run keeps it.
                    if holder.tvdb_id == first.tvdb_id:
                        holder.tvdb_id = None
                episode.tvdb_id = first.tvdb_id
    other_feed = db.scalar(
        select(Version.id)
        .where(Version.title_id == title.id, Version.source_id.is_not(None), Version.id != version.id)
        .limit(1)
    )
    if other_feed is None:
        for key, row in existing.items():
            if key not in kept and row.origin == "sonarr":
                db.delete(row)


def _finish(source_id: int, run_id: int, seen_series: set[int], tally: Tally, total: int, skipped: int,
            tag_names: dict[int, list[str]] | None = None) -> list[int]:  # fmt: skip
    """Versions of series gone from Sonarr go, Sonarr's tags are mirrored; the run is done. Returns the removed
    title ids."""
    moment = utcnow()
    detached: list[int] = []
    with SessionLocal() as db:
        db.execute(update(ImportRun).where(ImportRun.id == run_id).values(status="running"))
        source = db.get(Source, source_id)
        run = db.get(ImportRun, run_id)
        if source is None or run is None:
            db.rollback()
            return []
        on = watching.today()
        for version in list(db.scalars(select(Version).where(Version.source_id == source_id))):
            if version.sonarr_series_id in seen_series:
                continue
            if version.added_by == "owner":
                detach(db, version, moment, on)
                detached.append(version.id)
            else:
                db.delete(version)
                tally.versions_removed += 1
        db.flush()
        if tag_names is not None:
            # By Sonarr's series id to the title its version feeds.
            wanted: dict[int, list[str]] = {}
            for title_id, series_id in db.execute(
                select(Version.title_id, Version.sonarr_series_id).where(Version.source_id == source_id)
            ).tuples():
                if series_id in tag_names:
                    wanted[title_id] = tag_names[series_id]
            tags.sync_titles(db, source_id, wanted)
        removed = importer.remove_orphans(db)
        run.status = "done"
        run.finished_at = utcnow()
        run.titles_new = tally.titles_new
        run.titles_updated = tally.titles_updated
        run.versions_total = tally.versions_total
        run.versions_removed = tally.versions_removed
        run.error_code = None
        run.details = tally.details(total, skipped)
        db.commit()
    if detached:
        from . import folder_read

        folder_read.enqueue(detached)
    return removed


def detach(db: OrmSession, version: Version, moment: datetime, on: str) -> None:
    """An owner's series version no connection feeds any more: its files go with Sonarr, its rule decides again.

    It is nexcrate's own from now on (``own_since``) and wants nothing until its folder is read (``files_read_at``
    empty, the design notes, decision 23); the caller enqueues it after the commit.
    """
    version.own_since = moment
    version.files_read_at = None
    version.source_id = None
    version.sonarr_series_id = None
    version.source_series_path = None
    version.source_details = None
    version.root_folder = None
    version.profile_name = None
    db.execute(
        update(EpisodeVersion)
        .where(EpisodeVersion.version_id == version.id)
        .values(episode_file_id=None, in_source=None, queue_state=None, progress=None, problem_code=None),
        execution_options={"synchronize_session": False},
    )
    db.execute(
        delete(EpisodeFile).where(EpisodeFile.version_id == version.id),
        execution_options={"synchronize_session": False},
    )
    db.execute(
        delete(SourceEpisode).where(SourceEpisode.version_id == version.id),
        execution_options={"synchronize_session": False},
    )
    if version.watch_rule is None:
        version.watch_rule = "all"
    watching.apply_rule(db, version, version.watch_rule, version.watch_from_season, on, write=True)
    version.updated_at = moment


def release_series_source(db: OrmSession, source_id: int, moment: datetime) -> list[int]:
    """A Sonarr connection is deleted: the owner's versions it fed are detached; the importer removes the rest.

    Returns the versions whose series folder wants reading after the commit (decision 23).
    """
    on = watching.today()
    detached: list[int] = []
    for version in list(db.scalars(select(Version).where(Version.source_id == source_id, Version.added_by == "owner"))):
        detach(db, version, moment, on)
        detached.append(version.id)
    return detached


def count_seasons(db: OrmSession, title_id: int) -> int:
    return len(list(db.scalars(select(Season.id).where(Season.title_id == title_id, Season.number > 0))))
