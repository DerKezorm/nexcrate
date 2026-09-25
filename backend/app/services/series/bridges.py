"""Repairing the bridges between TMDB and TVDB after a takeover from Sonarr ("Befund: falsche
Brücken").

The import paired Sonarr's episodes with TMDB's by stored TVDB number, numbers and date, without a look at the title,
and the takeover linked every file by those pairs. Where TVDB and TMDB date a season differently, a file landed one
episode off and the stored TVDB number kept the pair for every later run. ``matching.match`` now lets the title veto
such a pair; this module brings a taken-over connection up to it:

1. every series of the connection is fetched from TMDB again, so each episode has its English name;
2. Sonarr's episodes are read (only read) and matched anew;
3. Sonarr's numbers on the TMDB episodes (``episode_numbers``, ``episodes.tvdb_id``), Sonarr's episodes without a TMDB
   partner and the numbering line are written as an import writes them;
4. each file taken from Sonarr moves to the episodes the new pairs name, but only while it still holds what the takeover
   gave it (the owner's own assignments, and files left out, stay as they are); a file whose new place holds another
   file stays unclear for the owner;
5. ``release.nex`` of every season folder of the version is written anew where it differs: it carries the TVDB numbers
   too, which change without a file moving.

Nothing on disk changes but ``release.nex``. Log lines carry ids and counts, never titles.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ... import crypto
from ...db import SessionLocal
from ...models import (
    Episode,
    EpisodeFile,
    EpisodeNumber,
    EpisodeVersion,
    HistoryEntry,
    Source,
    Title,
    Version,
    VersionDefinition,
    utcnow,
)
from .. import companions_series, tmdb
from ..sonarr import EpisodeItem, SonarrClient
from . import matching, refresh, sonarr_import, store, tmdb_series, unclear, watching

logger = logging.getLogger("nexcrate.series.bridges")


@dataclass
class Report:
    #: Counts over every series: ``series``, ``moved``, ``linked`` (unclear before), ``released`` (no place now),
    #: ``occupied`` (the new place holds another file), ``owner`` (changed by the owner, kept), ``vetoed`` pairs.
    counts: Counter[str] = field(default_factory=Counter)
    #: Per series with a change: title id, and the moves as ``(file id, old codes, new codes)``.
    series: list[dict[str, Any]] = field(default_factory=list)


def _code(season: int, number: int) -> str:
    return store.code(season, number)


def _taken_versions(db: OrmSession, source: Source) -> list[Version]:
    """The versions the takeover of this connection made nexcrate's own."""
    if source.taken_over_at is None:
        return []
    return list(
        db.scalars(
            select(Version)
            .where(Version.version_definition_id == source.version_id, Version.own_since == source.taken_over_at)
            .order_by(Version.id)
        )
    )


def _old_pairs(db: OrmSession, title_id: int, episodes: list[EpisodeItem]) -> dict[int, int]:
    """Sonarr's episode id to the TMDB episode the last import paired it with, as far as the stored numbers say:
    the episode's TVDB number, else Sonarr's numbers the import wrote on the TMDB episode."""
    by_tvdb: dict[int, list[int]] = defaultdict(list)
    for episode_id, tvdb_id in db.execute(
        select(Episode.id, Episode.tvdb_id).where(Episode.title_id == title_id, Episode.tvdb_id.is_not(None))
    ).tuples():
        by_tvdb[int(tvdb_id)].append(episode_id)
    by_numbers: dict[tuple[int, int], list[int]] = defaultdict(list)
    for row in db.scalars(
        select(EpisodeNumber).where(
            EpisodeNumber.title_id == title_id, EpisodeNumber.scheme == "tvdb", EpisodeNumber.origin == "sonarr"
        )
    ):
        if row.season is None or row.episode is None:
            continue
        for number in range(row.episode, (row.episode_end or row.episode) + 1):
            by_numbers[(row.season, number)].append(row.episode_id)
    pairs: dict[int, int] = {}
    for episode in episodes:
        found = by_tvdb.get(episode.tvdb_id or -1, [])
        if len(found) != 1:
            found = by_numbers.get((episode.season, episode.episode), [])
        if len(found) == 1:
            pairs[episode.id] = found[0]
    return pairs


def repair_version(
    db: OrmSession, version: Version, source_name: str, episodes: list[EpisodeItem], report: Report
) -> set[int]:
    """Match one taken-over version anew and move its files. Returns the seasons whose ``release.nex`` wants writing.
    The caller commits."""
    title = db.get(Title, version.title_id)
    if title is None:
        return set()
    tmdb_rows = db.execute(
        select(
            Episode.id,
            Episode.season_number,
            Episode.episode_number,
            Episode.air_date,
            Episode.name,
            Episode.tvdb_id,
            Episode.name_en,
        ).where(Episode.title_id == title.id, Episode.tmdb_gone_at.is_(None))
    ).tuples()
    tmdb_episodes = [
        matching.TmdbEpisode(
            id=row[0], season=row[1], episode=row[2], air_date=row[3], name=row[4], tvdb_id=row[5], name_en=row[6]
        )
        for row in tmdb_rows
    ]
    source_episodes = [
        matching.SourceEpisode(
            id=item.id,
            season=item.season,
            episode=item.episode,
            air_date=item.air_date,
            title=item.title,
            tvdb_id=item.tvdb_id,
            file_id=item.episode_file_id,
        )
        for item in episodes
    ]
    old = _old_pairs(db, title.id, episodes)
    matched = matching.match(source_episodes, tmdb_episodes)
    report.counts["series"] += 1
    report.counts["vetoed"] += matched.counts()["title_veto"]
    by_source = {item.id: item for item in episodes}
    targets: dict[int, list[EpisodeItem]] = defaultdict(list)
    for source_id, episode_id in matched.pairs.items():
        targets[episode_id].append(by_source[source_id])

    # Sonarr's numbers and its episodes without a partner, as an import writes them.
    sonarr_import._merge_numbers(db, title, version, targets)
    sonarr_import._merge_source_only(db, title, version, [by_source[source_id] for source_id in matched.unmatched])
    title.numbering_note = matching.numbering_note(source_name, source_episodes, tmdb_episodes, matched)

    # The files: where they are, where the takeover put them, where the new pairs put them.
    files = {
        row.source_file_id: row
        for row in db.scalars(
            select(EpisodeFile).where(EpisodeFile.version_id == version.id, EpisodeFile.source_file_id.is_not(None))
        )
    }
    current: dict[int, set[int]] = defaultdict(set)
    for episode_id, file_id in db.execute(
        select(EpisodeVersion.episode_id, EpisodeVersion.episode_file_id).where(
            EpisodeVersion.version_id == version.id, EpisodeVersion.episode_file_id.is_not(None)
        )
    ).tuples():
        current[int(file_id)].add(episode_id)
    sonarr_of_file: dict[int, list[EpisodeItem]] = defaultdict(list)
    for item in episodes:
        if item.episode_file_id is not None:
            sonarr_of_file[item.episode_file_id].append(item)
    codes = {item.id: _code(item.season, item.episode) for item in tmdb_episodes}

    moves: list[tuple[EpisodeFile, set[int], set[int]]] = []
    for source_file_id, row in files.items():
        held = sonarr_of_file.get(source_file_id, [])
        if not held:
            continue
        wanted = {matched.pairs[item.id] for item in held if item.id in matched.pairs}
        now = current.get(row.id, set())
        if now == wanted:
            continue
        before = {old[item.id] for item in held if item.id in old}
        if now != before or (row.left_out and not now):
            report.counts["owner"] += 1
            continue
        moves.append((row, now, wanted))

    seasons: set[int] = set()
    for row, _now, _wanted in moves:
        seasons |= unclear.release(db, version, row.id)
    moved: list[tuple[int, str, str]] = []
    moment = utcnow()
    definition = db.get(VersionDefinition, version.version_definition_id)
    for row, now, wanted in moves:
        placed = False
        if wanted:
            try:
                seasons |= unclear.assign(db, version, row.id, sorted(wanted))
                placed = True
            except unclear.NotAssignable:
                report.counts["occupied"] += 1
        if placed:
            report.counts["moved" if now else "linked"] += 1
        elif now:
            report.counts["released"] += 1
        else:
            continue
        before_codes = ",".join(sorted(codes[episode_id] for episode_id in now)) or "-"
        after_codes = ",".join(sorted(codes[episode_id] for episode_id in wanted)) if placed else "-"
        moved.append((row.id, before_codes, after_codes))
        db.add(
            HistoryEntry(
                title_id=title.id,
                version_id=version.id,
                version_definition_id=version.version_definition_id,
                version_label=definition.label if definition is not None else "",
                event="file_relinked",
                at=moment,
                detail=f"{before_codes} {after_codes}",
            )
        )
    db.flush()
    watching.recount(db, version, watching.today())
    if moved or matched.counts()["title_veto"]:
        report.series.append(
            {"title_id": title.id, "vetoed": matched.counts()["title_veto"], "moves": moved, "seasons": sorted(seasons)}
        )
    return seasons


async def run(source_id: int, *, sonarr_url: str | None = None, refresh_tmdb: bool = True) -> Report:
    """Repair every taken-over version of one Sonarr connection. Sonarr is only read. ``sonarr_url`` reaches Sonarr
    under another address (a copy of the database elsewhere)."""
    report = Report()
    has_token, token = await asyncio.to_thread(tmdb.token_state)
    if not has_token or not token:
        raise RuntimeError("no TMDB token")
    locale = await asyncio.to_thread(tmdb.account_locale)
    with SessionLocal() as db:
        source = db.get(Source, source_id)
        if source is None or source.app != "sonarr" or source.taken_over_at is None:
            raise RuntimeError("not a taken-over Sonarr connection")
        url, key, source_name = sonarr_url or source.url, crypto.decrypt(source.api_key), source.name
        wanted = [
            (version.id, version.title_id, title.tmdb_id, title.tvdb_id)
            for version in _taken_versions(db, source)
            if (title := db.get(Title, version.title_id)) is not None
        ]
    async with SonarrClient(url, key) as sonarr:
        listed, _ = await sonarr.series()
        by_tvdb = {item.tvdb_id: item for item in listed if item.tvdb_id}
        for version_id, title_id, tmdb_id, tvdb_id in wanted:
            item = by_tvdb.get(tvdb_id) if tvdb_id else None
            if item is None:
                report.counts["not_in_sonarr"] += 1
                continue
            if refresh_tmdb:
                data = await tmdb_series.fetch_series(token, tmdb_id, locale, refresh=True)
                await asyncio.to_thread(refresh._apply, title_id, data)
            episodes = await sonarr.episodes(item.id)
            if await asyncio.to_thread(_repair_one, version_id, source_name, episodes, report):
                states = await asyncio.to_thread(companions_series.write_version, version_id)
                for state, count in states.items():
                    report.counts[f"release_nex_{state}"] += count
    logger.info("Bridges of Sonarr source %d repaired: %s", source_id, dict(sorted(report.counts.items())))
    return report


def _repair_one(version_id: int, source_name: str, episodes: list[EpisodeItem], report: Report) -> bool:
    with SessionLocal() as db:
        version = db.get(Version, version_id)
        if version is None or version.source_id is not None:
            return False
        repair_version(db, version, source_name, episodes, report)
        db.commit()
    return True
