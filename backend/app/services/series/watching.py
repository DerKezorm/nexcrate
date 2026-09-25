"""What a series version watches (S1.4, decisions 9 to 15).

Three layers, as in Sonarr: the rule on the version, a switch per season, a switch per episode. The rule sets the
switches when a version is added, when the rule changes, and for every season and episode that appears later; after
that the switches count. A new episode takes its season's switch, except one TMDB added more than 14 days after it
aired in a series that already had episodes: it stays off, marked ``late``.

A version a source feeds takes its switches from the source and has no rule; every change here is refused.

Watching changes nothing in S1 but the counts and the state: nexcrate does not search series yet.

"Aired" means an air date before today in the container's time zone (decision 15). ``today`` is a function so tests can
fix the date.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from ...models import Episode, EpisodeFile, EpisodeVersion, Season, SeasonVersion, Version
from ...models.series import WATCH_RULES

#: Stuck queue items, most severe first, as the Radarr import names them.
PROBLEM_ORDER = ("import_blocked", "download_error", "import_pending", "download_warning")


def today() -> str:
    """Today's date in the container's time zone (``TZ``), ``YYYY-MM-DD``."""
    return time.strftime("%Y-%m-%d", time.localtime())


def aired(air_date: str | None, on: str) -> bool:
    return air_date is not None and air_date < on


def out_for_pack(air_date: str | None, on: str) -> bool:
    """Whether an episode counts as out for a season pack: Sonarr takes a pack whose last episode airs within 24 hours;
    with TMDB's dates to the day, an episode of today counts (after the review, 17.09.2026)."""
    return air_date is not None and air_date <= on


def season_wanted(rule: str | None, from_season: int | None, number: int) -> bool:
    """The switch a rule gives a season. Specials are off under every rule."""
    if number == 0 or rule in (None, "none"):
        return False
    if rule == "from_season":
        return number >= (from_season or 1)
    return True


def episode_wanted(
    rule: str | None, from_season: int | None, season_number: int, air_date: str | None, has_file: bool, on: str
) -> bool:
    """The switch a rule gives an episode when the rule is applied."""
    if season_number == 0 or rule in (None, "none"):
        return False
    if rule == "all":
        return True
    if rule == "future":
        return not aired(air_date, on)
    if rule == "missing":
        return not has_file
    if rule == "from_season":
        return season_number >= (from_season or 1)
    return False


def valid_rule(rule: str | None, from_season: int | None) -> bool:
    if rule not in WATCH_RULES:
        return False
    return rule != "from_season" or (from_season is not None and from_season >= 1)


def fed(version: Version) -> bool:
    return version.source_id is not None


# --- Rows ----------------------------------------------------------------------------------------------------- #


def sync_rows(db: OrmSession, version: Version, on: str, late_episode_ids: set[int] | None = None) -> int:
    """Create the missing switch rows of a version for every season and episode of its title. Returns how many
    episode rows were created.

    A version without any rows yet takes its rule episode by episode; afterwards a new episode takes its season's
    switch, and a late one stays off. A version a source feeds gets every new switch off: the import sets them.
    """
    late = late_episode_ids or set()
    is_fed = fed(version)
    season_switch: dict[int, bool] = {
        row.season_id: row.watched
        for row in db.scalars(select(SeasonVersion).where(SeasonVersion.version_id == version.id))
    }
    have_episodes = set(db.scalars(select(EpisodeVersion.episode_id).where(EpisodeVersion.version_id == version.id)))
    initial = not season_switch and not have_episodes
    for season_id, number in db.execute(
        select(Season.id, Season.number).where(Season.title_id == version.title_id)
    ).tuples():
        if season_id in season_switch:
            continue
        watched = False if is_fed else season_wanted(version.watch_rule, version.watch_from_season, number)
        db.add(
            SeasonVersion(
                season_id=season_id, version_id=version.id, watched=watched, set_by="source" if is_fed else "rule"
            )
        )
        season_switch[season_id] = watched
    created = 0
    rows = db.execute(
        select(Episode.id, Episode.season_id, Episode.season_number, Episode.air_date).where(
            Episode.title_id == version.title_id
        )
    ).tuples()
    for episode_id, season_id, season_number, air_date in rows:
        if episode_id in have_episodes:
            continue
        if is_fed:
            row = EpisodeVersion(
                episode_id=episode_id, version_id=version.id, watched=False, set_by="source", in_source=False
            )
        elif initial:
            row = EpisodeVersion(
                episode_id=episode_id,
                version_id=version.id,
                watched=episode_wanted(
                    version.watch_rule, version.watch_from_season, season_number, air_date, False, on
                ),
                set_by="rule",
            )
        elif episode_id in late:
            row = EpisodeVersion(episode_id=episode_id, version_id=version.id, watched=False, set_by="late")
        else:
            row = EpisodeVersion(
                episode_id=episode_id, version_id=version.id, watched=season_switch.get(season_id, False), set_by="rule"
            )
        db.add(row)
        created += 1
    db.flush()
    return created


# --- Changes -------------------------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RuleChange:
    added: int
    removed: int
    #: Switches the owner set (or ``late`` marks) the rule overwrites.
    overridden: int
    #: What the version watches afterwards, and how many of those episodes have aired.
    watched: int
    aired: int


def apply_rule(
    db: OrmSession, version: Version, rule: str, from_season: int | None, on: str, *, write: bool
) -> RuleChange:
    """The rule's effect on every switch of the version; with ``write`` also the switches, the rule and the counts.

    Preview and saving run this one function, so the count before saving is what saving does.
    """
    added = removed = overridden = watched = aired_count = 0
    rows = db.execute(
        select(EpisodeVersion, Episode.season_number, Episode.air_date, Episode.tmdb_gone_at)
        .join(Episode, Episode.id == EpisodeVersion.episode_id)
        .where(EpisodeVersion.version_id == version.id)
    ).tuples()
    for row, season_number, air_date, gone in rows:
        want = episode_wanted(rule, from_season, season_number, air_date, row.episode_file_id is not None, on)
        if gone is None:
            if want and not row.watched:
                added += 1
            if not want and row.watched:
                removed += 1
            if row.set_by in ("owner", "late") and want != row.watched:
                overridden += 1
            if want:
                watched += 1
                if aired(air_date, on):
                    aired_count += 1
        if write:
            row.watched = want
            row.set_by = "rule"
    if write:
        for season_row, number in db.execute(
            select(SeasonVersion, Season.number)
            .join(Season, Season.id == SeasonVersion.season_id)
            .where(SeasonVersion.version_id == version.id)
        ).tuples():
            season_row.watched = season_wanted(rule, from_season, number)
            season_row.set_by = "rule"
        version.watch_rule = rule
        version.watch_from_season = from_season if rule == "from_season" else None
        recount(db, version, on)
    return RuleChange(added=added, removed=removed, overridden=overridden, watched=watched, aired=aired_count)


def set_episode(db: OrmSession, version: Version, episode_id: int, watched: bool, on: str) -> bool:
    """One episode's switch, set by the owner. False when the version has no such episode."""
    row = db.get(EpisodeVersion, (episode_id, version.id))
    if row is None:
        return False
    row.watched = watched
    row.set_by = "owner"
    recount(db, version, on)
    return True


def set_season(db: OrmSession, version: Version, season_id: int, watched: bool, on: str) -> bool:
    """A season's switch and every switch of its episodes, set by the owner. False without such a season."""
    row = db.get(SeasonVersion, (season_id, version.id))
    if row is None:
        return False
    row.watched = watched
    row.set_by = "owner"
    episode_ids = select(Episode.id).where(Episode.season_id == season_id)
    for episode_row in db.scalars(
        select(EpisodeVersion).where(
            EpisodeVersion.version_id == version.id, EpisodeVersion.episode_id.in_(episode_ids)
        )
    ):
        episode_row.watched = watched
        episode_row.set_by = "owner"
    recount(db, version, on)
    return True


def watch_late(db: OrmSession, version: Version, on: str) -> int:
    """Switch on every ``late`` episode of a version. Returns how many."""
    rows = list(
        db.scalars(
            select(EpisodeVersion).where(EpisodeVersion.version_id == version.id, EpisodeVersion.set_by == "late")
        )
    )
    for row in rows:
        row.watched = True
        row.set_by = "owner"
    if rows:
        recount(db, version, on)
    return len(rows)


def custom(db: OrmSession, version: Version) -> bool:
    """Whether the owner set a switch by hand since the rule was last applied."""
    return (
        db.scalar(
            select(EpisodeVersion.episode_id)
            .where(EpisodeVersion.version_id == version.id, EpisodeVersion.set_by == "owner")
            .limit(1)
        )
        is not None
        or db.scalar(
            select(SeasonVersion.season_id)
            .where(SeasonVersion.version_id == version.id, SeasonVersion.set_by == "owner")
            .limit(1)
        )
        is not None
    )


# --- Counts and state ----------------------------------------------------------------------------------------- #


def counts_of(rows: list[tuple[Any, ...]], on: str) -> dict[str, Any]:
    """Counts from rows of (watched, has_file, air_date, upgradable, special), episodes TMDB still lists only.

    ``upgrade`` counts watched episodes whose file the profile would still improve on (decision 32);
    ``upgradable_files`` every regular episode with such a file, watched or not, which the state goes by. Only regular
    episodes count, as in Sonarr's progress (decision 46); a watched special that aired without
    a file is ``specials_missing``, a line of its own.
    """
    files = watched = aired_watched = have = total = upgrade = upgradable_files = specials_missing = 0
    next_air: str | None = None
    for is_watched, has_file, air_date, *rest in rows:
        upgradable = bool(rest[0]) if rest else False
        special = bool(rest[1]) if len(rest) > 1 else False
        if special:
            if is_watched and not has_file and aired(air_date, on):
                specials_missing += 1
            continue
        total += 1
        if has_file:
            files += 1
            if upgradable:
                upgradable_files += 1
        if not is_watched:
            continue
        watched += 1
        if aired(air_date, on):
            aired_watched += 1
            if has_file:
                have += 1
                if upgradable:
                    upgrade += 1
        elif air_date is not None and (next_air is None or air_date < next_air):
            next_air = air_date
    return {
        "files": files,
        "have": have,
        "upgrade": upgrade,
        "upgradable_files": upgradable_files,
        "aired_watched": aired_watched,
        "wanted": aired_watched - have,
        "watched": watched,
        "total": total,
        "next_air_date": next_air,
        "specials_missing": specials_missing,
    }


def recount(db: OrmSession, version: Version, on: str) -> None:
    """Counts, size and state of a series version (decision 14), written onto the version."""
    db.flush()
    rows = (
        db.execute(
            select(
                EpisodeVersion.watched,
                EpisodeVersion.episode_file_id,
                Episode.air_date,
                EpisodeVersion.queue_state,
                EpisodeVersion.progress,
                EpisodeVersion.problem_code,
                EpisodeFile.cutoff_not_met,
                Episode.season_number,
            )
            .join(Episode, Episode.id == EpisodeVersion.episode_id)
            .join(EpisodeFile, EpisodeFile.id == EpisodeVersion.episode_file_id, isouter=True)
            .where(EpisodeVersion.version_id == version.id, Episode.tmdb_gone_at.is_(None))
        )
        .tuples()
        .all()
    )
    counts = counts_of(
        [
            (watched, file_id is not None, air_date, upgradable, season_number == 0)
            for watched, file_id, air_date, *_rest, upgradable, season_number in rows
        ],
        on,
    )
    problems = [
        code or "download_warning" for *_head, state, _progress, code, _cut, _season in rows if state == "problem"
    ]
    progresses = [
        progress
        for *_head, state, progress, _code, _cut, _season in rows
        if state == "downloading" and progress is not None
    ]
    downloading = any(state == "downloading" for *_head, state, _progress, _code, _cut, _season in rows)
    version.episode_counts = counts
    version.size = int(
        db.scalar(select(func.coalesce(func.sum(EpisodeFile.size), 0)).where(EpisodeFile.version_id == version.id)) or 0
    )
    version.monitored = counts["watched"] > 0
    version.has_file = False
    if problems:
        version.state = "problem"
        version.problem_code = min(
            problems, key=lambda code: PROBLEM_ORDER.index(code) if code in PROBLEM_ORDER else 99
        )
        version.progress = None
    elif downloading:
        version.state = "downloading"
        version.problem_code = None
        version.progress = max(progresses) if progresses else None
    else:
        version.problem_code = None
        version.progress = None
        version.state = state_from(counts)


def state_from(counts: dict[str, Any]) -> str:
    """The state of a series version without a problem or a download, as a movie version's: what lies there decides,
    whether it is watched or not (the owner's finding of 24.09.2026: 129 series left alone showed as without files
    although their files lay there and could be improved). ``wanted`` while a watched episode that aired has no file,
    or while only episodes to come are watched and nothing lies there; ``unmonitored`` only without any file and
    without a watched episode, as ``/api/v1/states`` says."""
    if counts["watched"] > 0 and counts["have"] < counts["aired_watched"]:
        return "wanted"
    if counts["files"] == 0:
        return "wanted" if counts["watched"] > 0 else "unmonitored"
    return "upgrade" if counts.get("upgradable_files", counts["upgrade"]) > 0 else "available"


#: Raised when the counting rules change; every series version is counted again once at the next start.
COUNTS_VERSION = "3"
SETTING_COUNTS_VERSION = "series_counts_version"


def recount_all_once() -> int:
    """Count every series version again when the counting rules changed since the last start (the design notes,
    decision 46: specials left the count). Returns how many versions were counted."""
    from ...db import SessionLocal, get_setting, set_setting
    from ...models import Title

    with SessionLocal() as db:
        if get_setting(db, SETTING_COUNTS_VERSION, "") == COUNTS_VERSION:
            return 0
        on = today()
        versions = list(
            db.scalars(select(Version).join(Title, Title.id == Version.title_id).where(Title.kind == "series"))
        )
        for version in versions:
            recount(db, version, on)
        set_setting(db, SETTING_COUNTS_VERSION, COUNTS_VERSION)
        db.commit()
    return len(versions)
