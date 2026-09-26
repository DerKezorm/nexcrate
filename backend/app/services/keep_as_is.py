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

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session as OrmSession

from ..models import Download, EpisodeVersion, Title, Version
from .downloads import store as download_store

#: Versions one query reads, and SQLite's limit for an IN list with room to spare.
_CHUNK = 500


@dataclass(frozen=True)
class Kept:
    movies: int = 0
    albums: int = 0
    episodes: int = 0
    #: The titles whose plan may have changed; ``plan_again`` plans them after the caller's commit.
    titles: tuple[int, ...] = ()


def plan_again(kept: Kept) -> None:
    """After the caller's commit: the titles ``apply`` touched are planned again in parts (``replan_apart``), not under
    the write lock of the change, which after a takeover covers a whole library."""
    from .automatic import clock as automatic_clock
    from .automatic import planning as automatic_planning

    automatic_planning.replan_apart(kept.titles, automatic_clock.now())


def apply(db: OrmSession, version_ids: Collection[int], moment: datetime) -> Kept:
    """Keep these versions' files as they are; the caller commits and then calls ``plan_again``.

    With few statements for many versions: one flush and two queries per version held the write lock of a kept
    Lidarr library of 3,000 albums for 6.5 s, of 4,000 movies for 4.3 s (measured 26.09.2026). A version with a
    download that is not finished follows it as before (``follow_version``), the others take the state of their file.
    """
    from .music import store as music_store
    from .series import watching

    ids = sorted(set(version_ids))
    if not ids:
        return Kept()
    movies = albums = episodes = 0
    touched: set[int] = set()
    on = watching.today()
    loaded: list[tuple[Version, str]] = []
    for start in range(0, len(ids), _CHUNK):
        loaded.extend(
            db.execute(
                select(Version, Title.kind)
                .join(Title, Title.id == Version.title_id)
                .where(Version.id.in_(ids[start : start + _CHUNK]), Version.source_id.is_(None))
            ).tuples()
        )
    kept = [
        version
        for version, kind in loaded
        if kind in ("movie", "album")
        and version.has_file
        and version.monitored
        and not (kind == "album" and version.state == "incomplete")
    ]
    kinds = {version.id: kind for version, kind in loaded}
    title_ids = sorted({version.title_id for version in kept})
    loading: set[int] = set()
    for start in range(0, len(title_ids), _CHUNK):
        loading.update(
            db.scalars(
                select(Download.title_id).where(
                    Download.title_id.in_(title_ids[start : start + _CHUNK]),
                    Download.state.in_(download_store.UNFINISHED_STATES),
                )
            )
        )
    following: list[Version] = []
    for version in kept:
        version.monitored = False
        version.updated_at = moment
        if version.title_id in loading:
            following.append(version)
        else:
            # What ``follow_version`` gives a version with a file and no download.
            state = "upgrade" if version.cutoff_not_met else "available"
            if version.track_counts is not None and music_store.incomplete(version):
                state = "incomplete"
            version.state, version.progress, version.problem_code = state, None, None
        movies += kinds[version.id] == "movie"
        albums += kinds[version.id] == "album"
        touched.add(version.title_id)
    db.flush()
    for version in following:
        download_store.follow_version(db, version.title_id, version.version_definition_id, moment)

    series = [version for version, kind in loaded if kind == "series"]
    series_ids = [version.id for version in series]
    watched_with_file = (
        EpisodeVersion.watched.is_(True),
        EpisodeVersion.episode_file_id.is_not(None),
    )
    counts: dict[int, int] = {}
    for start in range(0, len(series_ids), _CHUNK):
        part = series_ids[start : start + _CHUNK]
        counts.update(
            (version_id, int(count))
            for version_id, count in db.execute(
                select(EpisodeVersion.version_id, func.count())
                .where(EpisodeVersion.version_id.in_(part), *watched_with_file)
                .group_by(EpisodeVersion.version_id)
            ).tuples()
        )
        db.execute(
            update(EpisodeVersion)
            .where(EpisodeVersion.version_id.in_(part), *watched_with_file)
            .values(watched=False, set_by="owner"),
            execution_options={"synchronize_session": False},
        )
    for version in series:
        if not counts.get(version.id):
            continue
        episodes += counts[version.id]
        watching.recount(db, version, on)
        version.updated_at = moment
        touched.add(version.title_id)
    if touched:
        db.flush()
    return Kept(movies=movies, albums=albums, episodes=episodes, titles=tuple(sorted(touched)))


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
