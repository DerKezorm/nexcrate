"""TMDB's series data in the library (S1.2): one function for adding, refreshing and importing.

``apply_series`` writes the title's fields, finds seasons by TMDB season id and episodes by TMDB episode id, records
renumbered episodes in the history, marks or removes vanished ones (decision 5), gives new ones their switches in every
version (decisions 9 to 11), and computes counts and state of every version again.

A season TMDB sent no details for keeps its stored episodes: an answer with a gap removes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session as OrmSession

from ...models import (
    Download,
    DownloadEpisode,
    Episode,
    EpisodeNumber,
    EpisodeVersion,
    HistoryEntry,
    Season,
    Title,
    Version,
)
from ..schreibweisen import search_text, sort_key
from . import absolute, watching
from .tmdb_series import SeriesData

#: An episode that appears with an air date further back than this, in a series that had episodes, is late.
LATE_AFTER_DAYS = 14


@dataclass
class Applied:
    new_episodes: int = 0
    late: int = 0
    renumbered: int = 0
    #: Vanished episodes kept because something hangs on them.
    gone: int = 0
    #: Vanished episodes removed.
    removed: int = 0
    late_episode_ids: set[int] = field(default_factory=set)


def code(season: int, episode: int) -> str:
    return f"S{season:02d}E{episode:02d}"


def apply_title_fields(title: Title, data: SeriesData, moment: datetime) -> None:
    """The title's own fields from TMDB. The series type stays once set; a new series gets TMDB's proposal."""
    title.title = data.title[:1024]
    title.original_title = data.original_title
    title.imdb_id = data.imdb_id
    title.tvdb_id = data.tvdb_id
    title.year = data.year
    title.runtime = data.runtime
    title.genres = list(data.genres)
    title.overview = data.overview
    title.sort_key = sort_key(title.title)[:1024]
    title.search_keys = search_text([title.title, title.original_title])
    title.tmdb_search_keys = search_text(data.other_titles)
    title.original_language = data.original_language
    title.tmdb_poster_path = data.poster_file
    if title.poster_source_id is None:
        title.poster_origin = "tmdb" if data.poster_file else None
    title.series_status = data.status
    title.networks = list(data.networks)
    title.first_air_date = data.first_air_date
    title.last_air_date = data.last_air_date
    title.next_air_date = data.next_air_date
    title.episode_groups = list(data.episode_groups)
    title.title_en = data.title_en
    if title.series_type is None:
        title.series_type = data.proposed_type
    # What TMDB suggests, kept apart from the type the owner or Sonarr set (B5).
    title.type_proposed = data.proposed_type
    title.tmdb_refreshed_at = moment
    title.updated_at = moment


def new_title(data: SeriesData, moment: datetime, series_type: str | None = None) -> Title:
    title = Title(kind="series", tmdb_id=data.tmdb_id, added=moment, updated_at=moment, series_type=series_type)
    apply_title_fields(title, data, moment)
    return title


#: Download states that still file: an episode such a download holds is never removed under it.
_UNFINISHED = ("queued", "downloading", "paused", "completed", "importing", "problem")


def _hanging(db: OrmSession, episode_ids: list[int]) -> set[int]:
    """Episodes something hangs on: a file in a version, a switch the owner set, a number from a source, or a download
    that still files (removing the episode would drop it from the download, whose video then counts as not needed)."""
    if not episode_ids:
        return set()
    found = set(
        db.scalars(
            select(EpisodeVersion.episode_id).where(
                EpisodeVersion.episode_id.in_(episode_ids),
                or_(EpisodeVersion.episode_file_id.is_not(None), EpisodeVersion.set_by == "owner"),
            )
        )
    )
    # Absolute numbers are worked out again from their sources (A1); nothing hangs on them.
    found.update(
        db.scalars(
            select(EpisodeNumber.episode_id).where(
                EpisodeNumber.episode_id.in_(episode_ids), EpisodeNumber.scheme != absolute.SCHEME
            )
        )
    )
    found.update(
        db.scalars(
            select(DownloadEpisode.episode_id)
            .join(Download, Download.id == DownloadEpisode.download_id)
            .where(DownloadEpisode.episode_id.in_(episode_ids), Download.state.in_(_UNFINISHED))
        )
    )
    return found


def apply_series(db: OrmSession, title: Title, data: SeriesData, moment: datetime, on: str) -> Applied:
    """Write ``data`` into ``title`` and everything below it. The title must have an id. The caller commits."""
    applied = Applied()
    apply_title_fields(title, data, moment)

    seasons = {row.tmdb_season_id: row for row in db.scalars(select(Season).where(Season.title_id == title.id))}
    episodes = {
        row.tmdb_episode_id: row
        for row in db.scalars(select(Episode).where(Episode.title_id == title.id))
        if row.tmdb_episode_id is not None
    }
    had_episodes = bool(episodes)
    late_before = (date.fromisoformat(on) - timedelta(days=LATE_AFTER_DAYS)).isoformat()

    seen_seasons: set[int] = set()
    for season_data in data.seasons:
        season = seasons.get(season_data.tmdb_id)
        if season is None:
            season = Season(title_id=title.id, tmdb_season_id=season_data.tmdb_id, number=season_data.number)
            db.add(season)
            seasons[season_data.tmdb_id] = season
        season.number = season_data.number
        season.name = season_data.name[:1024]
        season.overview = season_data.overview
        season.air_date = season_data.air_date
        season.episode_count = season_data.episode_count
        season.tmdb_gone_at = None
        seen_seasons.add(season_data.tmdb_id)
    db.flush()

    seen_episodes: set[int] = set()
    new_rows: list[Episode] = []
    for season_data in data.seasons:
        season = seasons[season_data.tmdb_id]
        if not season_data.episodes_known:
            seen_episodes.update(tmdb_id for tmdb_id, row in episodes.items() if row.season_id == season.id)
            continue
        for episode_data in season_data.episodes:
            if episode_data.tmdb_id in seen_episodes:
                continue
            seen_episodes.add(episode_data.tmdb_id)
            row = episodes.get(episode_data.tmdb_id)
            if row is None:
                row = Episode(
                    title_id=title.id,
                    season_id=season.id,
                    tmdb_episode_id=episode_data.tmdb_id,
                    season_number=episode_data.season_number,
                    episode_number=episode_data.episode_number,
                    first_seen_at=moment,
                )
                db.add(row)
                episodes[episode_data.tmdb_id] = row
                new_rows.append(row)
                applied.new_episodes += 1
            elif (row.season_number, row.episode_number) != (episode_data.season_number, episode_data.episode_number):
                db.add(
                    HistoryEntry(
                        title_id=title.id,
                        version_id=None,
                        version_definition_id=None,
                        version_label="",
                        event="renumbered",
                        at=moment,
                        detail=f"{code(row.season_number, row.episode_number)} "
                        f"{code(episode_data.season_number, episode_data.episode_number)}",
                    )
                )
                applied.renumbered += 1
            row.season_id = season.id
            row.season_number = episode_data.season_number
            row.episode_number = episode_data.episode_number
            row.name = episode_data.name
            row.name_en = episode_data.name_en
            row.overview = episode_data.overview
            row.air_date = episode_data.air_date
            row.runtime = episode_data.runtime
            row.episode_type = episode_data.episode_type
            row.tmdb_gone_at = None
    db.flush()

    if data.seasons:
        vanished = [row for tmdb_id, row in episodes.items() if tmdb_id not in seen_episodes]
        hanging = _hanging(db, [row.id for row in vanished])
        for row in vanished:
            if row.id in hanging:
                if row.tmdb_gone_at is None:
                    row.tmdb_gone_at = moment
                applied.gone += 1
            else:
                db.delete(row)
                applied.removed += 1
        db.flush()
        for tmdb_id, season in seasons.items():
            if tmdb_id in seen_seasons:
                continue
            left = db.scalar(select(Episode.id).where(Episode.season_id == season.id).limit(1))
            if left is None:
                db.delete(season)
            elif season.tmdb_gone_at is None:
                season.tmdb_gone_at = moment
        db.flush()

    if had_episodes:
        applied.late_episode_ids = {
            row.id
            for row in new_rows
            if row.air_date is not None and row.air_date < late_before and row.season_number > 0
        }
        applied.late = len(applied.late_episode_ids)
    versions = list(db.scalars(select(Version).where(Version.title_id == title.id).order_by(Version.id)))
    for version in versions:
        watching.sync_rows(db, version, on, applied.late_episode_ids)
    if applied.late and any(version.source_id is None for version in versions):
        db.add(
            HistoryEntry(
                title_id=title.id,
                version_id=None,
                version_definition_id=None,
                version_label="",
                event="episodes_late",
                at=moment,
                detail=str(applied.late),
            )
        )
    for version in versions:
        watching.recount(db, version, on)
    # An anime series counts its episodes through as well (A1).
    absolute.store(db, title, moment)
    return applied
