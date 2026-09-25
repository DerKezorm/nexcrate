"""The halves of double episodes that already lie in a series folder (D4).

A takeover from Sonarr, or reading a folder, links each file by its numbers. Where TMDB lists a double episode as one
and the files count its halves (``S08E23 … 1``, ``S08E24 … 2``), the first half got the episode and the second stayed
without one: an unclear file, often left out by the owner, who had no way to assign it.

Here the numbers of all files of a season are the evidence, as the videos of a pack are when filing: when they name a
number TMDB's season lacks and the split of ``parts.py`` explains every one of them, each file without an episode that
the split names as a half is linked to that half. The file its episode holds becomes the other half, but only when its
own numbers say it is that half; otherwise nothing changes. Nothing on disk moves; a rename gives the halves their
`` - pt1`` and `` - pt2`` later, when the owner runs it.

Log lines carry ids and counts, never titles.
"""

from __future__ import annotations

import logging
from pathlib import PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ...db import SessionLocal
from ...models import EpisodeFile, EpisodeVersion, Title, Version, utcnow
from .. import releases
from . import parts, release_match

logger = logging.getLogger("nexcrate.series")


def numbers_of(row: EpisodeFile) -> tuple[int, tuple[int, ...]] | None:
    """The season and episode numbers a file names: Sonarr's, then what reading the folder kept, then its name."""
    for stored in (row.source_numbers, row.read_as):
        if isinstance(stored, dict) and isinstance(stored.get("season"), int):
            episodes = tuple(value for value in stored.get("episodes") or [] if isinstance(value, int))
            if episodes:
                return int(stored["season"]), episodes
    series = releases.parse_series(PurePosixPath(row.relative_path).stem).series
    if series.form in ("standard", "multi_episode") and series.season is not None and series.episodes:
        return int(series.season), tuple(series.episodes)
    return None


def link(db: OrmSession, version: Version) -> int:
    """Link the halves of one version; returns how many files became a half. The caller commits."""
    title = db.get(Title, version.title_id)
    if title is None or title.kind != "series" or version.source_id is not None:
        return 0
    rows = list(db.scalars(select(EpisodeFile).where(EpisodeFile.version_id == version.id)))
    links = {
        link.episode_id: link
        for link in db.scalars(select(EpisodeVersion).where(EpisodeVersion.version_id == version.id))
    }
    held = {link.episode_file_id for link in links.values() if link.episode_file_id is not None}
    loose = [row for row in rows if row.id not in held and row.part is None]
    if not loose:
        return 0
    named: dict[int, tuple[int, tuple[int, ...]]] = {}
    for row in rows:
        found = numbers_of(row)
        if found is not None:
            named[row.id] = found
    numbering = release_match.load(db, title)
    seasons = parts.explained(
        numbering, [(season, number) for season, numbers in named.values() for number in numbers]
    )
    if not seasons:
        return 0
    by_id = {row.id: row for row in rows}
    seconds = {row.part_of_episode_id for row in rows if row.part == 2}

    def half_of(row: EpisodeFile) -> parts.Part | None:
        found = named.get(row.id)
        if found is None or found[0] not in seasons or len(found[1]) != 1:
            return None
        return seasons[found[0]].get(found[1][0])

    changed = 0
    moment = utcnow()
    for row in loose:
        half = half_of(row)
        if half is None or half.part is None:
            continue
        episode_id = half.episode_id
        entry = links.get(episode_id)
        if entry is None:
            continue
        current = by_id.get(entry.episode_file_id) if entry.episode_file_id is not None else None
        other = half_of(current) if current is not None else None
        if current is not None and (other is None or other != parts.Part(episode_id, 3 - half.part)):
            # The episode holds a file that is not the other half by its own numbers: nothing is sure.
            continue
        if episode_id in seconds:
            continue
        if half.part == 1:
            row.part, row.part_of_episode_id = 1, None
            entry.episode_file_id = row.id
            if current is not None:
                current.part, current.part_of_episode_id = 2, episode_id
                current.updated_at = moment
        else:
            row.part, row.part_of_episode_id = 2, episode_id
            if current is not None:
                current.part = 1
                current.updated_at = moment
        seconds.add(episode_id)
        row.left_out = False
        row.updated_at = moment
        changed += 1
    if changed:
        from . import watching

        db.flush()
        watching.recount(db, version, watching.today())
        version.updated_at = moment
        logger.info("Version %d: %d files linked as the half of a double episode", version.id, changed)
    return changed


def link_all() -> int:
    """Every series version of nexcrate's own that has files without an episode; returns how many files became a
    half. Each version in a transaction of its own, so one that fails keeps the others."""
    with SessionLocal() as db:
        candidates = list(
            db.scalars(
                select(Version.id)
                .join(Title, Title.id == Version.title_id)
                .where(Title.kind == "series", Version.source_id.is_(None))
                .where(
                    select(EpisodeFile.id)
                    .where(EpisodeFile.version_id == Version.id, EpisodeFile.part.is_(None))
                    .where(
                        ~select(EpisodeVersion.episode_id)
                        .where(EpisodeVersion.episode_file_id == EpisodeFile.id)
                        .exists()
                    )
                    .exists()
                )
            )
        )
    total = 0
    touched: list[int] = []
    for version_id in candidates:
        try:
            with SessionLocal() as db:
                version = db.get(Version, version_id)
                if version is None:
                    continue
                count = link(db, version)
                if count:
                    db.commit()
                    total += count
                    touched.append(version_id)
        except Exception:
            logger.exception("Linking the halves of version %d failed; its files stay as they are", version_id)
    if touched:
        from .. import companions_series

        for version_id in touched:
            try:
                companions_series.write_version(version_id)
            except Exception:
                logger.exception("release.nex of version %d could not be written after linking halves", version_id)
    return total
