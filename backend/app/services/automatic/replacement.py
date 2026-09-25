"""What a failed or a filed away download changes in its title's plan (C4 and C7, decision 11).

``downloads/tracking`` and ``downloads/importing`` call these inside their transaction; the caller commits.

* **After a failure** (the client failed the download, it was encrypted, or it was refused for a dangerous file or an
  archive with a password): the version's replacement times of the last 24 hours are counted. Below 3 the time is
  added and the title is due at once with the reason ``replacement``, **also with the switch off** (the owner's answer
  of 22.09.2026, as Radarr's "Redownload failed": a download the owner started by hand is replaced as well). Otherwise
  the title is planned again by the next round, which shows ``replacement_limit`` and the version's schedule.
  ``after_failure`` says who takes care: ``replacement``, ``schedule`` (the switch is on), or ``owner`` (nothing
  happens by itself: the failure is a problem for the owner).
* **After filing away** a download whose version still wants an upgrade: the title's next search is brought forward to
  then, reason ``schedule``, so the judgement of the new file decides whether an upgrade is still wanted.

**Series** (decisions 22 and 25), with the switch on or off: a failure counts per version **and
season** (``series_replacements``), at most three in 24 hours, so a broken pack of season 2 never holds season 5 back;
every season of the download's episodes is due at once. A filed episode file still below the target **by its quality**
makes its episodes due at once (their search time is cleared); below it only by score, the plan brings the upgrade.

Log lines carry ids and counts only.
"""

from __future__ import annotations

import logging
from collections.abc import Collection
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session as OrmSession

from ...models import Download, DownloadEpisode, Episode, Title, Version
from ..downloads import store
from . import planning, series_planning, settings

logger = logging.getLogger("nexcrate.automatic")


def after_failure(db: OrmSession, row: Download, moment: datetime) -> str:
    """The replacement of a failed download. Returns who takes care: ``replacement``, ``schedule`` or ``owner``."""
    title = db.get(Title, row.title_id)
    if title is not None and title.kind == "series":
        return _after_series_failure(db, row, title, moment)
    # Albums since Music M5 (decision 10), under their own switch.
    kind = title.kind if title is not None else None
    if kind not in ("movie", "album"):
        return "owner"
    version = store.version_of(db, row.title_id, row.version_definition_id)
    if version is None or version.source_id is not None or title is None:
        return "owner"
    window = moment - planning.REPLACEMENT_WINDOW
    recent = [time for time in planning.parse_times(version.replacement_times) if time > window]
    if len(recent) < planning.REPLACEMENTS_PER_DAY:
        recent.append(moment)
        # A new list: a JSON column changed in place is not written.
        version.replacement_times = [planning.format_time(time) for time in recent]
        title.next_search_at, title.next_search_reason = moment, "replacement"
        logger.info(
            "Title %d: download %d of version %d failed; replacement %d of %d in 24 hours is due",
            row.title_id,
            row.id,
            version.version_definition_id,
            len(recent),
            planning.REPLACEMENTS_PER_DAY,
        )
        return "replacement"
    version.replacement_times = [planning.format_time(time) for time in recent]
    # The next round plans the title again: its schedule, with the reason replacement_limit.
    title.next_search_at, title.next_search_reason = None, None
    logger.info(
        "Title %d: download %d of version %d failed after %d replacements in 24 hours; "
        "the version waits for its schedule",
        row.title_id,
        row.id,
        version.version_definition_id,
        len(recent),
    )
    return "schedule" if settings.load_enabled(db, kind) else "owner"


def after_import(db: OrmSession, row: Download, version: Version | None, moment: datetime) -> None:
    if version is None or version.source_id is not None or not version.cutoff_not_met:
        return
    title = db.get(Title, row.title_id)
    if title is None or title.kind != "movie":
        return
    title.next_search_at, title.next_search_reason = moment, "schedule"
    logger.info(
        "Title %d: download %d is filed away and can still be upgraded; the next search is due", row.title_id, row.id
    )


def _after_series_failure(db: OrmSession, row: Download, title: Title, moment: datetime) -> str:
    version = store.version_of(db, row.title_id, row.version_definition_id)
    if version is None or version.source_id is not None:
        return "owner"
    seasons = sorted(
        set(
            db.scalars(
                select(Episode.season_number)
                .join(DownloadEpisode, DownloadEpisode.episode_id == Episode.id)
                .where(DownloadEpisode.download_id == row.id)
            )
        )
    )
    if not seasons:
        return "owner"
    window = moment - planning.REPLACEMENT_WINDOW
    recent = [item for item in series_planning.parse_replacements(version.series_replacements) if item[0] > window]
    granted: list[int] = []
    for season in seasons:
        if sum(1 for _at, replaced in recent if replaced == season) < planning.REPLACEMENTS_PER_DAY:
            recent.append((moment, season))
            granted.append(season)
    # A new list: a JSON column changed in place is not written.
    version.series_replacements = series_planning.format_replacements(recent)
    if granted:
        title.next_search_at, title.next_search_reason = moment, "replacement"
    else:
        # The next round plans the title again: the seasons' schedules, with the reason replacement_limit.
        title.next_search_at, title.next_search_reason = None, None
    logger.info(
        "Series %d: download %d of version %d failed; replacements due for %d of %d seasons",
        row.title_id,
        row.id,
        version.version_definition_id,
        len(granted),
        len(seasons),
    )
    if granted:
        return "replacement"
    return "schedule" if settings.load_enabled(db, "series") else "owner"


def after_series_file(
    db: OrmSession, title_id: int, episode_ids: Collection[int], reason: str | None, moment: datetime
) -> None:
    """A filed episode file (decision 25): below the target by its quality, its episodes are due at once; by score only,
    nothing changes here."""
    if reason != "quality" or not episode_ids:
        return
    title = db.get(Title, title_id)
    if title is None or title.kind != "series":
        return
    db.execute(
        update(Episode)
        .where(Episode.title_id == title_id, Episode.id.in_(sorted(episode_ids)))
        .values(last_search_at=None),
        execution_options={"synchronize_session": False},
    )
    # A replacement already due keeps its reason: the title is due now either way.
    replacing = title.next_search_reason == "replacement" and title.next_search_at is not None
    if not (replacing and title.next_search_at <= moment):
        title.next_search_at, title.next_search_reason = moment, "schedule"
    logger.info(
        "Series %d: a filed file of %d episodes is below the target by quality; the next search is due",
        title_id,
        len(episode_ids),
    )
