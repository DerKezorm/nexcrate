"""What an import brought is not upgraded (the owner's wish of 24.09.2026).

The takeover of a Radarr, Sonarr or Lidarr connection and the page "Ordner" offer "keep what is there as it is"
(``keep_as_is``). It does what the owner did by hand for his whole library that morning, only for what the import
brought:

* a movie version with a file is left alone (``monitored`` off), as the button on the version does
  (``library.set_monitored``); a version without a file stays watched;
* an album version with a file is left alone the same way, unless it lacks tracks (state ``incomplete``): those it
  still wants;
* every watched episode with a file is switched off, as the switch on the episode does (``watching.set_episode``,
  ``set_by='owner'``); a missing episode stays on, and the series' rule stays as it is.

Everything else stays as the import made it, and whatever arrives later is upgraded as usual.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ..models import EpisodeVersion, Title, Version
from .downloads import store as download_store


@dataclass(frozen=True)
class Kept:
    movies: int = 0
    albums: int = 0
    episodes: int = 0


def apply(db: OrmSession, version_ids: Collection[int], moment: datetime) -> Kept:
    """Keep these versions' files as they are; the caller commits."""
    from .automatic import clock as automatic_clock
    from .automatic import planning as automatic_planning
    from .series import watching

    ids = sorted(set(version_ids))
    if not ids:
        return Kept()
    movies = albums = episodes = 0
    touched: set[int] = set()
    on = watching.today()
    for version, kind in db.execute(
        select(Version, Title.kind)
        .join(Title, Title.id == Version.title_id)
        .where(Version.id.in_(ids), Version.source_id.is_(None))
    ).tuples():
        if kind in ("movie", "album"):
            if not version.has_file or not version.monitored or (kind == "album" and version.state == "incomplete"):
                continue
            version.monitored = False
            version.updated_at = moment
            db.flush()
            download_store.follow_version(db, version.title_id, version.version_definition_id, moment)
            movies += kind == "movie"
            albums += kind == "album"
            touched.add(version.title_id)
        elif kind == "series":
            rows = list(
                db.scalars(
                    select(EpisodeVersion).where(
                        EpisodeVersion.version_id == version.id,
                        EpisodeVersion.watched.is_(True),
                        EpisodeVersion.episode_file_id.is_not(None),
                    )
                )
            )
            if not rows:
                continue
            for row in rows:
                row.watched = False
                row.set_by = "owner"
            episodes += len(rows)
            db.flush()
            watching.recount(db, version, on)
            version.updated_at = moment
            touched.add(version.title_id)
    if touched:
        db.flush()
        automatic_planning.replan(db, sorted(touched), automatic_clock.now())
    return Kept(movies=movies, albums=albums, episodes=episodes)


def arrived_since(db: OrmSession, moment: datetime, kinds: Collection[str]) -> list[int]:
    """Versions of these kinds that got a file from the disk since then (``found_on_disk``, ``restored``)."""
    from ..models import HistoryEntry

    return sorted(
        {
            version_id
            for version_id in db.scalars(
                select(HistoryEntry.version_id)
                .join(Title, Title.id == HistoryEntry.title_id)
                .where(
                    HistoryEntry.event.in_(("found_on_disk", "restored")),
                    HistoryEntry.at >= moment,
                    HistoryEntry.version_id.is_not(None),
                    Title.kind.in_(tuple(kinds)),
                )
            )
            if version_id is not None
        }
    )
