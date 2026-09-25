"""The plan per title: which versions want something, when the title is searched next, and why (the design notes,
C2, C3, C4, C7, C8, decisions 2 to 6, 11 and 12).

**A version wants something** (decision 3) when no source feeds it, it is monitored, its definition has a profile with
movie rules and a default folder, no download blocks it (queued to importing, or a problem that needs the owner), and it
has no file or its file can still be upgraded (``cutoff_not_met``: by its quality, or since decision 20 by its score at
the cutoff quality; both want the same way, at the same rates). Only movies take part.

**Its next search,** by the first rule that applies:

1. A replacement after a failure that no search followed yet (a time in ``replacement_times`` after the title's last
   search): at that time, reason ``replacement``.
2. The current or a coming year without any usable date: no search, TMDB may still bring a date; a day later, reason
   ``no_date``.
3. Its anchor lies ahead: at the anchor plus the spread, reason ``anchor``. Never before the anchor.
4. Otherwise reason ``schedule``. Never searched since the anchor: at the anchor plus the spread (without an anchor
   time, when the title was added). Else the last search plus the interval of the tier the last search fell into, plus
   the spread. A 2160p version's later physical date starts the frequent window again. A download of the version filed
   away after the last search, when the version still wants an upgrade, makes it due at that time. After three
   replacements in 24 hours, a failure that no search followed shows the reason ``replacement_limit``.

**Intervals** for a missing version, by whole days since its anchor: under 15 days every 6 hours, under 61 daily, under
366 every 3 days, under 1096 weekly, then every 14 days, never stopping. Without an anchor time (a past year, or no year
at all): every 14 days. A version with a file that can be upgraded, by quality or by score, at half those rates.
**The spread:** up to a fifth of the interval more, the fraction taken from a hash of the title and the base time, so
the same inputs always give the same time (planning again changes nothing) and titles with the same anchor spread over
the day.

**The title** takes the earliest next time of its wanting versions, with that version's reason and anchor. A stored
``limit`` stays while its time lies after the plan's: the budget moved it. Nothing wanted: no next time, reason
``nothing_wanted``, and the general anchor.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm.attributes import InstrumentedAttribute

from ...models import Download, Title, Version, VersionDefinition
from .. import judging
from ..downloads import store
from ..profiles import store as profile_store
from . import anchors, settings, upgrade_guard

REASONS = (
    "anchor",
    "schedule",
    "limit",
    "replacement",
    "replacement_limit",
    "no_date",
    "nothing_wanted",
    "off",
    # A program's search wish searches at once (22.09.2026); only in what the title page and /api/v1 show.
    "wish",
)
#: Reasons whose time is a search: the scheduler starts a title with one of them once its time came.
SEARCHING_REASONS = ("schedule", "replacement", "replacement_limit", "limit")
#: Of two versions due at the same time, the reason shown.
_PRIORITY = {"replacement": 0, "limit": 1, "schedule": 2, "replacement_limit": 3, "no_date": 4, "anchor": 5}
#: Problems that refuse a download and put its release on the blocklist (``downloads/importing.REFUSED``).
REFUSED_PROBLEMS = ("dangerous_file", "encrypted")

REPLACEMENTS_PER_DAY = 3
REPLACEMENT_WINDOW = timedelta(hours=24)
#: Whole days since the anchor below which a tier applies, and its interval for a missing version.
TIERS = ((15, timedelta(hours=6)), (61, timedelta(days=1)), (366, timedelta(days=3)), (1096, timedelta(days=7)))
LAST_TIER = timedelta(days=14)
UPGRADE_FACTOR = 2
SPREAD_SHARE = 0.2
NO_DATE_RECHECK = timedelta(days=1)


@dataclass(frozen=True)
class VersionFacts:
    definition_id: int
    label: str
    #: No source feeds it.
    own: bool
    monitored: bool
    #: The profile's movie rules; None without a usable profile.
    rules: dict[str, Any] | None
    has_folder: bool
    #: A download blocks a new load: queued to importing, or a problem that needs the owner.
    blocked: bool
    has_file: bool
    cutoff_not_met: bool
    replacement_times: tuple[datetime, ...] = ()
    #: The newest failed or refused download of the last 24 hours.
    last_failure_at: datetime | None = None
    #: The newest download filed away.
    last_import_at: datetime | None = None

    @property
    def wants(self) -> bool:
        if not (self.own and self.monitored and self.rules is not None and self.has_folder and not self.blocked):
            return False
        return not self.has_file or self.cutoff_not_met


@dataclass(frozen=True)
class TitleFacts:
    title_id: int
    kind: str
    year: int | None
    release_dates: Any
    added: datetime
    last_search_at: datetime | None
    stored_next_at: datetime | None
    stored_reason: str | None
    versions: tuple[VersionFacts, ...] = ()
    #: ISO 639-1, for a profile that requires the original language.
    original_language: str | None = None
    #: The last search found a release that only the indexer's grab limit held back: when the indexer loads again.
    grab_free_at: datetime | None = None


def grab_free_at(summary: object) -> datetime | None:
    """``grab_free_at`` of a stored search summary (``scheduler.hold_for_grab_limit``)."""
    return _aware(summary.get("grab_free_at")) if isinstance(summary, dict) else None


def grab_limit_plan(next_at: datetime, reason: str, free_at: datetime | None) -> tuple[datetime, str]:
    """A release that only the grab limit held back is searched again when the indexer loads again, not with the
    schedule (finding 6 of 22.09.2026: 287 upgrades found on 21.09.2026 waited a month, Full Metal Jacket too). Until
    the next search the time stays, also once it passed: the title is due then."""
    if free_at is not None and reason in SEARCHING_REASONS and next_at > free_at:
        return free_at, "limit"
    return next_at, reason


@dataclass(frozen=True)
class Plan:
    wanted: bool
    next_at: datetime | None
    reason: str
    anchor: anchors.Anchor
    #: The definition ids of the versions that want something.
    wanting: tuple[int, ...] = ()


# --- Times ---------------------------------------------------------------------------------------------------------- #


def format_time(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_times(values: object) -> tuple[datetime, ...]:
    """The times of a JSON list as ``replacement_times`` stores them; anything unreadable is left out."""
    found: list[datetime] = []
    for value in values if isinstance(values, list) else []:
        if not isinstance(value, str):
            continue
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            continue
        found.append(parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC))
    return tuple(sorted(found))


def interval(days: int, upgrade: bool) -> timedelta:
    """The interval of the tier ``days`` whole days after the anchor fall into."""
    step = LAST_TIER
    for limit, tier in TIERS:
        if days < limit:
            step = tier
            break
    return step * (UPGRADE_FACTOR if upgrade else 1)


def spread_fraction(title_id: int, base: datetime) -> float:
    """A fraction from 0 to below 1, the same for the same title and base time."""
    digest = hashlib.sha256(f"{title_id}|{format_time(base)}".encode()).digest()
    return int.from_bytes(digest[:4], "big") / 2**32


def _spread(title_id: int, base: datetime, step: timedelta) -> datetime:
    return base + step * SPREAD_SHARE * spread_fraction(title_id, base)


# --- The plan ------------------------------------------------------------------------------------------ #


def version_plan(
    title: TitleFacts, version: VersionFacts, now: datetime, anchor: anchors.Anchor | None = None
) -> tuple[datetime, str, anchors.Anchor]:
    """The next search of one version that wants something: its time, its reason and its anchor. An album brings its
    own anchor (``album_planning``); a movie's comes from its TMDB dates."""
    if anchor is None:
        anchor = anchors.anchor_for(
            title.release_dates,
            year=title.year,
            languages=anchors.required_languages(version.rules, title.original_language),
            resolution=anchors.target_resolution(version.rules),
        )
    last = title.last_search_at
    upgrade = version.has_file
    first_step = interval(0, upgrade)
    granted = version.replacement_times[-1] if version.replacement_times else None
    if granted is not None and (last is None or granted > last):
        return granted, "replacement", anchor
    if anchor.kind == "year" and title.year is not None and title.year >= now.year:
        if title.stored_reason == "no_date" and title.stored_next_at is not None and title.stored_next_at > now:
            return title.stored_next_at, "no_date", anchor
        return now + NO_DATE_RECHECK, "no_date", anchor
    if anchor.at is not None and now < anchor.at:
        return _spread(title.title_id, anchor.at, first_step), "anchor", anchor

    if anchor.at is None:
        if last is None:
            next_at = title.added
        else:
            step = LAST_TIER * (UPGRADE_FACTOR if upgrade else 1)
            next_at = _spread(title.title_id, last, step) + step
    elif last is None or last < anchor.at:
        next_at = _spread(title.title_id, anchor.at, first_step)
    else:
        window = anchor.window_at
        start = window if window is not None and last >= window else anchor.at
        step = interval((last - start).days, upgrade)
        next_at = _spread(title.title_id, last, step) + step
        if window is not None and last < window:
            next_at = min(next_at, _spread(title.title_id, window, first_step))
    if (
        version.last_import_at is not None
        and version.has_file
        and version.cutoff_not_met
        and (last is None or version.last_import_at > last)
    ):
        next_at = min(next_at, version.last_import_at)

    reason = "schedule"
    recent = [moment for moment in version.replacement_times if moment > now - REPLACEMENT_WINDOW]
    if (
        len(recent) >= REPLACEMENTS_PER_DAY
        and version.last_failure_at is not None
        and (last is None or version.last_failure_at > last)
    ):
        reason = "replacement_limit"
    return next_at, reason, anchor


def title_plan(title: TitleFacts, now: datetime) -> Plan:
    general = anchors.anchor_for(title.release_dates, year=title.year)
    wanting = [version for version in title.versions if version.wants] if title.kind == "movie" else []
    if not wanting:
        return Plan(wanted=False, next_at=None, reason="nothing_wanted", anchor=general)
    planned = [version_plan(title, version, now) for version in wanting]
    next_at, reason, anchor = min(planned, key=lambda item: (item[0], _PRIORITY[item[1]]))
    next_at, reason = grab_limit_plan(next_at, reason, title.grab_free_at)
    stored_limit = title.stored_reason == "limit" and title.stored_next_at is not None and title.stored_next_at > now
    limit_at = title.stored_next_at if stored_limit else None
    if limit_at is not None and reason in SEARCHING_REASONS and next_at <= limit_at:
        next_at, reason = limit_at, "limit"
    return Plan(
        wanted=True,
        next_at=next_at,
        reason=reason,
        anchor=anchor,
        wanting=tuple(version.definition_id for version in wanting),
    )


# --- The database -------------------------------------------------------------------------------------- #


def _aware(value: object) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _latest(
    db: OrmSession, column: InstrumentedAttribute[Any], title_ids: list[int], *conditions: Any
) -> dict[tuple[int, int], datetime]:
    statement = (
        select(Download.title_id, Download.version_definition_id, func.max(column))
        .where(Download.title_id.in_(title_ids), Download.version_definition_id.is_not(None), *conditions)
        .group_by(Download.title_id, Download.version_definition_id)
    )
    found: dict[tuple[int, int], datetime] = {}
    for title_id, definition_id, value in db.execute(statement).tuples():
        moment = _aware(value)
        if moment is not None and definition_id is not None:
            found[(int(title_id), int(definition_id))] = moment
    return found


def load_facts(db: OrmSession, title_ids: Collection[int], now: datetime) -> dict[int, TitleFacts]:
    """What the plan reads of these titles, in a few queries for all of them."""
    ids = sorted(set(title_ids))
    if not ids:
        return {}
    titles = list(db.scalars(select(Title).where(Title.id.in_(ids))))
    rows = (
        db.execute(
            select(Version, VersionDefinition.label, VersionDefinition.folder)
            .join(VersionDefinition, VersionDefinition.id == Version.version_definition_id)
            .where(Version.title_id.in_(ids))
            .order_by(Version.title_id, VersionDefinition.id)
        )
        .tuples()
        .all()
    )
    definitions = sorted({version.version_definition_id for version, _label, _folder in rows})
    rules: dict[int, dict[str, Any] | None] = {
        version_id: judging.usable_rules(profile)
        for version_id, profile in profile_store.by_versions(db, definitions).items()
    }
    unfinished = db.scalars(
        select(Download).where(Download.title_id.in_(ids), Download.state.in_(store.UNFINISHED_STATES))
    )
    blocked = {(row.title_id, row.version_definition_id) for row in unfinished if store.blocks_loading(row)}
    failed = or_(
        Download.state == "failed", and_(Download.state == "problem", Download.problem_code.in_(REFUSED_PROBLEMS))
    )
    failures = _latest(db, Download.updated_at, ids, failed, Download.updated_at > now - REPLACEMENT_WINDOW)
    imports = _latest(db, Download.imported_at, ids, Download.state == "imported")
    # Paused upgrades (upgrade_guard): a file that was there when they were paused is not wanted. The daily limit is
    # not planned: a title planned as wanting nothing would not be planned again when the window frees.
    paused = upgrade_guard.Guard(paused_since=upgrade_guard.paused_since(db))
    arrived = (
        upgrade_guard.arrivals(db, [version.id for version, _label, _folder in rows if version.has_file])
        if paused.paused_since is not None
        else {}
    )

    by_title: dict[int, list[VersionFacts]] = defaultdict(list)
    for version, label, folder in rows:
        key = (version.title_id, version.version_definition_id)
        by_title[version.title_id].append(
            VersionFacts(
                definition_id=version.version_definition_id,
                label=label,
                own=version.source_id is None,
                monitored=bool(version.monitored),
                rules=rules.get(version.version_definition_id),
                has_folder=bool(folder),
                blocked=key in blocked,
                has_file=bool(version.has_file),
                cutoff_not_met=bool(version.cutoff_not_met)
                and not (version.has_file and paused.blocks(arrived.get(version.id))),
                replacement_times=parse_times(version.replacement_times),
                last_failure_at=failures.get(key),
                last_import_at=imports.get(key),
            )
        )
    return {
        title.id: TitleFacts(
            title_id=title.id,
            kind=title.kind,
            year=title.year,
            release_dates=title.release_dates,
            added=title.added,
            last_search_at=title.last_search_at,
            stored_next_at=title.next_search_at,
            stored_reason=title.next_search_reason,
            versions=tuple(by_title.get(title.id, [])),
            original_language=title.original_language,
            grab_free_at=grab_free_at(title.search_summary),
        )
        for title in titles
    }


#: A title's new plan next to the stored one: next time, reason, stored time, stored reason.
Planned = tuple[datetime | None, str, datetime | None, str | None]


def plans(db: OrmSession, title_ids: Collection[int], now: datetime) -> dict[int, Planned]:
    """The plans of these titles next to the stored ones, only read. The round compares them without the write lock
    and plans again under it only the titles whose plan changed (``replan``).

    ⚠️ Until 22.09.2026 the round read the facts of 500 titles under the write lock: 2.6 to 13.7 s on the owner's quiet
    Synology (measured on a copy of its database), and on 21.09.2026, with 14 downloads and their imports on the same
    disks, long enough for the tracking and an import to give up with "database is locked".
    """
    # Series have a plan of their own; imported here, it imports this module.
    from . import series_planning

    ids = sorted(set(title_ids))
    kinds = dict(db.execute(select(Title.id, Title.kind).where(Title.id.in_(ids))).tuples().all()) if ids else {}
    planned: dict[int, tuple[datetime | None, str, datetime | None, str | None]] = {}
    for title_id, fact in load_facts(db, [item for item, kind in kinds.items() if kind == "movie"], now).items():
        plan = title_plan(fact, now)
        planned[title_id] = (plan.next_at, plan.reason, fact.stored_next_at, fact.stored_reason)
    series = [item for item, kind in kinds.items() if kind == "series"]
    for title_id, series_fact in series_planning.load_facts(db, series, now).items():
        series_plan = series_planning.title_plan(series_fact, now)
        planned[title_id] = (
            series_plan.next_at,
            series_plan.reason,
            series_fact.stored_next_at,
            series_fact.stored_reason,
        )
    # Albums likewise.
    from . import album_planning

    albums = [item for item, kind in kinds.items() if kind == "album"]
    for title_id, album_fact in album_planning.load_facts(db, albums, now).items():
        album_plan = album_planning.title_plan(album_fact, now)
        planned[title_id] = (album_plan.next_at, album_plan.reason, album_fact.stored_next_at, album_fact.stored_reason)
    return planned


def differing(found: dict[int, Planned]) -> list[int]:
    """The titles whose new plan is not the stored one."""
    return sorted(
        title_id
        for title_id, (next_at, reason, stored_at, stored_reason) in found.items()
        if (stored_at, stored_reason) != (next_at, reason)
    )


def replan(db: OrmSession, title_ids: Collection[int], now: datetime) -> tuple[int, int]:
    """Plan these titles again and stage what changed; the caller commits. Returns how many were planned and changed."""
    # A write first, though it matches no row: SQLite's write lock is taken before anything is read, so a failure or a
    # filing away committed meanwhile is never overwritten by a plan made without it.
    db.execute(
        update(Title).where(Title.id < 1).values(next_search_reason=Title.next_search_reason),
        execution_options={"synchronize_session": False},
    )
    planned = plans(db, title_ids, now)
    changed = 0
    for title_id, (next_at, reason, stored_at, stored_reason) in planned.items():
        if (stored_at, stored_reason) == (next_at, reason):
            continue
        title = db.get(Title, title_id)
        if title is None:
            continue
        title.next_search_at, title.next_search_reason = next_at, reason
        changed += 1
    return len(planned), changed


# --- What the title page shows ------------------------------------------------------------------------ #


def anchor_out(anchor: anchors.Anchor) -> dict[str, Any]:
    return {
        "date": anchor.at.date().isoformat() if anchor.at is not None else None,
        "kind": anchor.kind,
        "country": anchor.country,
    }


_SUMMARY_KEYS = frozenset({"at", "origin", "releases", "indexers", "versions"})


def summary_out(value: object) -> dict[str, Any] | None:
    """The stored summary when it has the shape nexcrate writes; None otherwise."""
    if not isinstance(value, dict) or not _SUMMARY_KEYS <= set(value):
        return None
    if not isinstance(value["indexers"], list) or not isinstance(value["versions"], list):
        return None
    return value


def search_plan(db: OrmSession, title: Title, now: datetime) -> dict[str, Any]:
    """``search_plan`` of the title detail: the plan as the scheduler makes it, computed on reading.

    With the switch off, a title whose time came shows the reason ``off``: it would be searched now.
    """
    if title.kind == "series":
        from . import series_planning

        return _with_wish(title, series_planning.search_plan(db, title, now))
    if title.kind == "album":
        from . import album_planning

        return _with_wish(title, album_planning.search_plan(db, title, now))
    return _with_wish(title, _movie_search_plan(db, title, now))


def _with_wish(title: Title, found: dict[str, Any]) -> dict[str, Any]:
    """A program's wish searches at once, whatever the plan and the switch say (22.09.2026): the reason ``wish``, and
    the time of the wish as the next time."""
    if title.search_wish_at is not None and found.get("wanted"):
        return {**found, "reason": "wish", "next_at": title.search_wish_at}
    return found


def _movie_search_plan(db: OrmSession, title: Title, now: datetime) -> dict[str, Any]:
    enabled = settings.load_enabled(db)
    fact = load_facts(db, [title.id], now).get(title.id)
    if fact is None:
        plan = Plan(wanted=False, next_at=None, reason="nothing_wanted", anchor=anchors.Anchor(kind="none"))
    else:
        plan = title_plan(fact, now)
    reason = plan.reason
    # A replacement runs with the switch off as well (the owner's answer of 22.09.2026).
    if not enabled and plan.wanted and plan.next_at is not None and plan.next_at <= now and reason != "replacement":
        reason = "off"
    return {
        "automatic": enabled,
        "wanted": plan.wanted,
        "last_at": title.last_search_at,
        "next_at": plan.next_at,
        "reason": reason,
        "anchor": anchor_out(plan.anchor),
        "summary": summary_out(title.search_summary),
        "seasons": [],
    }
