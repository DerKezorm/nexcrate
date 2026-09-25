"""What the library answers about a series (API): the series block of a title and one season.

Everything is read from the database; nothing asks TMDB or Sonarr. Versions are named by their definition id
(``version_id``), as everywhere in the library API.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ...models import (
    Episode,
    EpisodeFile,
    EpisodeNumber,
    EpisodeVersion,
    Season,
    SeasonVersion,
    Source,
    SourceEpisode,
    Title,
    Version,
)
from . import folder_read, names, unclear, watching


def version_watch(db: OrmSession, version: Version) -> dict[str, Any]:
    return {
        "rule": version.watch_rule if version.source_id is None else None,
        "from_season": version.watch_from_season if version.source_id is None else None,
        "custom": version.source_id is None and watching.custom(db, version),
        "fed": version.source_id is not None,
    }


def counts_out(version: Version) -> dict[str, Any] | None:
    return counts_from(version.episode_counts)


def counts_from(stored: object) -> dict[str, Any] | None:
    """The stored counts of a series version as the API answers them; None for a movie version."""
    if not isinstance(stored, dict):
        return None
    return {
        "files": int(stored.get("files") or 0),
        "have": int(stored.get("have") or 0),
        "upgrade": int(stored.get("upgrade") or 0),
        "aired_watched": int(stored.get("aired_watched") or 0),
        "wanted": int(stored.get("wanted") or 0),
        "watched": int(stored.get("watched") or 0),
        "total": int(stored.get("total") or 0),
        "next_air_date": stored.get("next_air_date") if isinstance(stored.get("next_air_date"), str) else None,
        "specials_missing": int(stored.get("specials_missing") or 0),
    }


def series_block(db: OrmSession, title: Title, versions: list[Version], on: str) -> dict[str, Any]:
    definition_of = {version.id: version.version_definition_id for version in versions}
    seasons = list(db.scalars(select(Season).where(Season.title_id == title.id).order_by(Season.number, Season.id)))
    switches = {
        (row.season_id, row.version_id): row.watched
        for row in db.scalars(
            select(SeasonVersion).where(SeasonVersion.version_id.in_([version.id for version in versions]))
        )
    }
    per_season: dict[int, dict[str, int]] = defaultdict(lambda: {"episodes": 0, "aired": 0})
    per_version: dict[tuple[int, int], dict[str, int]] = defaultdict(
        lambda: {"files": 0, "have": 0, "aired_watched": 0, "watched_episodes": 0, "loading": 0}
    )
    for season_id, air_date in db.execute(
        select(Episode.season_id, Episode.air_date).where(Episode.title_id == title.id, Episode.tmdb_gone_at.is_(None))
    ).tuples():
        per_season[season_id]["episodes"] += 1
        if watching.aired(air_date, on):
            per_season[season_id]["aired"] += 1
    rows = db.execute(
        select(
            Episode.season_id,
            Episode.air_date,
            EpisodeVersion.version_id,
            EpisodeVersion.watched,
            EpisodeVersion.episode_file_id,
            EpisodeVersion.queue_state,
        )
        .join(EpisodeVersion, EpisodeVersion.episode_id == Episode.id)
        .where(Episode.title_id == title.id, Episode.tmdb_gone_at.is_(None))
    ).tuples()
    late = set()
    for season_id, air_date, version_id, watched, file_id, queue_state in rows:
        counts = per_version[(season_id, version_id)]
        if queue_state in ("downloading", "problem"):
            counts["loading"] += 1
        if file_id is not None:
            counts["files"] += 1
        if watched:
            counts["watched_episodes"] += 1
            if watching.aired(air_date, on):
                counts["aired_watched"] += 1
                if file_id is not None:
                    counts["have"] += 1
    own = [version.id for version in versions if version.source_id is None]
    if own:
        late = set(
            db.scalars(
                select(EpisodeVersion.episode_id).where(
                    EpisodeVersion.version_id.in_(own),
                    EpisodeVersion.set_by == "late",
                    EpisodeVersion.watched.is_(False),
                )
            )
        )
    season_items = [
        {
            "id": season.id,
            "number": season.number,
            "name": season.name,
            "air_date": season.air_date,
            "episodes": per_season[season.id]["episodes"],
            "aired": per_season[season.id]["aired"],
            "tmdb_gone": season.tmdb_gone_at is not None,
            "versions": [
                {
                    "version_id": definition_of[version.id],
                    "watched": bool(switches.get((season.id, version.id), False)),
                    **per_version[(season.id, version.id)],
                }
                for version in versions
            ],
        }
        for season in seasons
    ]
    # Files without an episode, with proposals for a version of nexcrate's own (Ü3).
    unassigned = [
        {
            "id": item["file"].id,
            "version_id": definition_of[version.id],
            "relative_path": item["file"].relative_path,
            "size_bytes": item["file"].size,
            "quality": item["file"].quality,
            "source_numbers": item["file"].source_numbers,
            "read_as": item["file"].read_as,
            "left_out": item["left_out"],
            "source_episode": item["source_episode"],
            "proposal": item["proposal"],
            "occupied": item["occupied"],
            "nearby": item["nearby"],
        }
        for version in versions
        for item in unclear.describe(db, version)
    ]
    unassigned.sort(key=lambda entry: (entry["left_out"], entry["relative_path"]))
    reading: list[dict[str, Any]] = []
    for version in versions:
        state = folder_read.state_of(version.id)
        if state is None and version.source_id is None and version.files_read_at is None:
            # Not read yet and nothing queued in this process: it waits for the next start or a read.
            state = {"state": "queued", "done": 0, "total": None}
        if state is not None:
            reading.append({"version_id": definition_of[version.id], **state})
    return {
        "type": title.series_type or "standard",
        # ⚠️ A series a live Sonarr feeds takes its type from there on every run (A6), so the
        # owner cannot set it here; the interface says so instead of offering a change that would be undone.
        "type_fed": db.scalar(
            select(Source.app)
            .join(Version, Version.source_id == Source.id)
            .where(Version.title_id == title.id, Source.taken_over_at.is_(None), Source.app == "sonarr")
            .limit(1)
        ),
        "status": title.series_status,
        "networks": list(title.networks or []),
        "first_air_date": title.first_air_date,
        "last_air_date": title.last_air_date,
        "next_air_date": title.next_air_date,
        "tvdb_id": title.tvdb_id,
        "numbering": title.numbering_note,
        "late_episodes": len(late),
        "episode_groups": list(title.episode_groups or []),
        "seasons": season_items,
        "unassigned_files": unassigned,
        "reading": reading,
    }


def _episode_state(row: EpisodeVersion, air_date: str | None, on: str, upgradable: bool = False) -> str:
    """The state of one episode in one version. ``upgrade`` (decision 32) stands between ``wanted`` and
    ``available``: the file is there, and the profile would still take a better one."""
    if row.queue_state == "problem":
        return "problem"
    if row.queue_state == "downloading":
        return "downloading"
    if row.episode_file_id is not None:
        return "upgrade" if upgradable else "available"
    if not row.watched:
        return "unmonitored"
    return "wanted"


def source_only_episodes(db: OrmSession, version: Version) -> list[dict[str, Any]]:
    """Episodes the source feeding a version knows and TMDB does not (decision 47)."""
    if version.source_id is None:
        return []
    rows = db.scalars(
        select(SourceEpisode)
        .where(SourceEpisode.version_id == version.id)
        .order_by(SourceEpisode.season, SourceEpisode.episode, SourceEpisode.id)
    )
    return [
        {
            "season": row.season,
            "episode": row.episode,
            "name": row.name,
            "air_date": row.air_date,
            "watched": row.watched,
            "has_file": row.has_file,
        }
        for row in rows
    ]


def _second_part(row: EpisodeFile | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "relative_path": row.relative_path,
        "release_title": row.release_title,
        "size_bytes": row.size,
        "quality": row.quality,
    }


def season_episodes(
    db: OrmSession, title: Title, season_id: int, on: str, language: str | None = None
) -> dict[str, Any] | None:
    """The episodes of one season with their numbers and every version's switch, state and file. None without it.

    A placeholder name ("Folge 9") shows the English name where one is stored (decision 49). In the specials, each
    episode says whether a source feeding a version knows it (decision 48); null without such a version.
    """
    season = db.get(Season, season_id)
    if season is None or season.title_id != title.id:
        return None
    versions = list(
        db.scalars(select(Version).where(Version.title_id == title.id).order_by(Version.version_definition_id))
    )
    definition_of = {version.id: version.version_definition_id for version in versions}
    episodes = list(
        db.scalars(select(Episode).where(Episode.season_id == season_id).order_by(Episode.episode_number, Episode.id))
    )
    ids = [episode.id for episode in episodes]
    numbers: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in db.scalars(
        select(EpisodeNumber).where(EpisodeNumber.episode_id.in_(ids)).order_by(EpisodeNumber.scheme)
    ):
        numbers[row.episode_id].append(
            {
                "scheme": row.scheme,
                "season": row.season,
                "episode": row.episode,
                "episode_end": row.episode_end,
                "absolute": row.absolute,
                "verified": row.verified,
            }
        )
    links = list(db.scalars(select(EpisodeVersion).where(EpisodeVersion.episode_id.in_(ids))))
    file_ids = {row.episode_file_id for row in links if row.episode_file_id is not None}
    files = (
        {row.id: row for row in db.scalars(select(EpisodeFile).where(EpisodeFile.id.in_(file_ids)))} if file_ids else {}
    )
    # The second halves of double episodes name their episode themselves.
    seconds = {
        (row.version_id, int(row.part_of_episode_id or 0)): row
        for row in db.scalars(
            select(EpisodeFile).where(EpisodeFile.part == 2, EpisodeFile.part_of_episode_id.in_(ids))
        )
    }
    covered: dict[int, int] = defaultdict(int)
    if file_ids:
        for file_id in db.scalars(
            select(EpisodeVersion.episode_file_id).where(EpisodeVersion.episode_file_id.in_(file_ids))
        ):
            covered[file_id] += 1
    by_episode: dict[int, dict[int, EpisodeVersion]] = defaultdict(dict)
    for row in links:
        by_episode[row.episode_id][row.version_id] = row
    fed_ids = {version.id for version in versions if version.source_id is not None}
    items = []
    for episode in episodes:
        known: bool | None = None
        if season.number == 0 and fed_ids:
            known = any(
                bool(row.in_source) for version_id, row in by_episode[episode.id].items() if version_id in fed_ids
            )
        version_items = []
        for version in versions:
            row = by_episode[episode.id].get(version.id)
            if row is None:
                continue
            file = files.get(row.episode_file_id) if row.episode_file_id is not None else None
            version_items.append(
                {
                    "version_id": definition_of[version.id],
                    "state": _episode_state(row, episode.air_date, on, bool(file is not None and file.cutoff_not_met)),
                    "watched": row.watched,
                    "set_by": row.set_by,
                    "late": row.set_by == "late" and not row.watched,
                    "in_source": row.in_source,
                    "progress": row.progress,
                    "problem_code": row.problem_code,
                    "file": (
                        {
                            "id": file.id,
                            "relative_path": file.relative_path,
                            # The name the release had before nexcrate renamed it: the owner's own check whether the
                            # file really is this episode (a finding of 22.09.2026 on a wrongly numbered release).
                            "release_title": file.release_title,
                            "size_bytes": file.size,
                            "quality": file.quality,
                            "release_group": file.release_group,
                            "languages": list(file.languages or []),
                            "episodes": covered[file.id],
                            "second_part": _second_part(seconds.get((version.id, episode.id))),
                        }
                        if file is not None
                        else None
                    ),
                }
            )
        shown = names.display_name(episode.name, episode.name_en, language)
        items.append(
            {
                "id": episode.id,
                "season_number": episode.season_number,
                "number": episode.episode_number,
                "air_date": episode.air_date,
                "aired": watching.aired(episode.air_date, on),
                "name": shown,
                # Files and Sonarr carry the English title: shown beside another name, the owner can compare them.
                "name_en": episode.name_en if episode.name_en and episode.name_en.strip() != shown.strip() else None,
                "known_to_source": known,
                "overview": episode.overview,
                "runtime_min": episode.runtime,
                "episode_type": episode.episode_type,
                "tmdb_gone": episode.tmdb_gone_at is not None,
                "numbers": numbers.get(episode.id, []),
                "versions": version_items,
            }
        )
    return {"season_id": season.id, "number": season.number, "episodes": items}
