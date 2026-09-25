"""The plan per album (decisions 2 to 7): which album searches, when, and why.

The rules and intervals are the movies' (``planning``): a version wants when it is its own, monitored, has the music
profile and a default folder, no download blocks it, and it has no file or its step is below the profile's target
(``cutoff_not_met``, Music M2). An album whose tracks are incomplete but whose step reaches the target does not search
by itself (decision 3): before a download nexcrate cannot know which tracks a release holds.

**The anchor** is the album's release date (``first_release_date`` of the release group, else the target release's
date): a full date from that day, a month from its first day, a year alone as a movie with only a year, nothing as a
movie without a date. Never searched before the anchor (the basis, E9).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session as OrmSession

from ...models import Artist, Download, Release, Title, TrackFile, Version, VersionDefinition
from ..downloads import store
from ..music import album_quality
from . import anchors, planning, settings

#: The anchor kind of an album with a release date.
RELEASE = "release"


def anchor_of(first_release_date: str | None, target_date: str | None = None) -> anchors.Anchor:
    """The anchor of an album from its release date, as MusicBrainz writes it: ``2019-03-01``, ``2019-03`` or
    ``2019``; the target release's date when the album has none."""
    value = (first_release_date or target_date or "").strip()
    parts = value.split("-")
    try:
        year = int(parts[0]) if parts and parts[0] else None
        month = int(parts[1]) if len(parts) > 1 else None
        day = int(parts[2]) if len(parts) > 2 else None
    except ValueError:
        return anchors.Anchor(kind="none")
    if year is None:
        return anchors.Anchor(kind="none")
    if month is None:
        return anchors.Anchor(kind="year")
    try:
        return anchors.Anchor(kind=RELEASE, at=datetime(year, month, day or 1, tzinfo=UTC))
    except ValueError:
        return anchors.Anchor(kind="year")


def _year(value: str | None) -> int | None:
    head = (value or "")[:4]
    return int(head) if head.isdigit() else None


class AlbumFacts:
    """What the plan reads of one album: the movie's title facts, and the album's own anchor."""

    def __init__(self, facts: planning.TitleFacts, anchor: anchors.Anchor) -> None:
        self.facts = facts
        self.anchor = anchor

    @property
    def stored_next_at(self) -> datetime | None:
        return self.facts.stored_next_at

    @property
    def stored_reason(self) -> str | None:
        return self.facts.stored_reason

    @property
    def versions(self) -> tuple[planning.VersionFacts, ...]:
        return self.facts.versions


def load_facts(db: OrmSession, title_ids: Collection[int], now: datetime) -> dict[int, AlbumFacts]:
    """What the plan reads of these albums, in a few queries for all of them."""
    ids = sorted(set(title_ids))
    if not ids:
        return {}
    titles = list(db.scalars(select(Title).where(Title.id.in_(ids), Title.kind == "album")))
    # A frozen artist (fork 3): its albums keep their switches but want nothing on their own; a
    # program's search wish still goes, as a search by hand does.
    owners = {title.artist_id for title in titles if title.artist_id is not None}
    frozen_artists = (
        set(db.scalars(select(Artist.id).where(Artist.id.in_(owners), Artist.frozen_at.is_not(None))))
        if owners
        else set()
    )
    frozen = {
        title.id for title in titles if title.artist_id in frozen_artists and title.search_wish_at is None
    }
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
    # The music profile is one for every album (Music M2); the plan only asks whether it exists.
    rules = album_quality.rules_of(db)
    targets = {
        release_id: date
        for release_id, date in db.execute(
            select(Release.id, Release.date).where(
                Release.id.in_({version.target_release_id for version, _l, _f in rows if version.target_release_id})
            )
        ).tuples()
    }
    unfinished = db.scalars(
        select(Download).where(Download.title_id.in_(ids), Download.state.in_(store.UNFINISHED_STATES))
    )
    blocked = {(row.title_id, row.version_definition_id) for row in unfinished if store.blocks_loading(row)}
    # An album with unclear files waits for the owner (decision 15).
    unclear = set(
        db.scalars(
            select(Version.id)
            .join(TrackFile, TrackFile.version_id == Version.id)
            .where(Version.title_id.in_(ids), TrackFile.unclear.is_(True))
        )
    )
    failed = or_(
        Download.state == "failed",
        and_(Download.state == "problem", Download.problem_code.in_(planning.REFUSED_PROBLEMS)),
    )
    failures = planning._latest(
        db, Download.updated_at, ids, failed, Download.updated_at > now - planning.REPLACEMENT_WINDOW
    )
    imports = planning._latest(db, Download.imported_at, ids, Download.state == "imported")
    by_title: dict[int, list[planning.VersionFacts]] = defaultdict(list)
    target_dates: dict[int, str | None] = {}
    for version, label, folder in rows:
        key = (version.title_id, version.version_definition_id)
        target_dates.setdefault(version.title_id, targets.get(version.target_release_id))
        by_title[version.title_id].append(
            planning.VersionFacts(
                definition_id=version.version_definition_id,
                label=label,
                own=version.source_id is None,
                monitored=bool(version.monitored) and version.title_id not in frozen,
                rules=rules,
                has_folder=bool(folder),
                blocked=key in blocked or version.id in unclear,
                has_file=bool(version.has_file),
                cutoff_not_met=bool(version.cutoff_not_met),
                replacement_times=planning.parse_times(version.replacement_times),
                last_failure_at=failures.get(key),
                last_import_at=imports.get(key),
            )
        )
    found: dict[int, AlbumFacts] = {}
    for title in titles:
        anchor = anchor_of(title.first_release_date, target_dates.get(title.id))
        year = title.year or _year(title.first_release_date) or _year(target_dates.get(title.id))
        facts = planning.TitleFacts(
            title_id=title.id,
            kind="album",
            year=year,
            release_dates=None,
            added=title.added,
            last_search_at=title.last_search_at,
            stored_next_at=title.next_search_at,
            stored_reason=title.next_search_reason,
            versions=tuple(by_title.get(title.id, [])),
            grab_free_at=planning.grab_free_at(title.search_summary),
        )
        found[title.id] = AlbumFacts(facts, anchor)
    return found


def title_plan(fact: AlbumFacts, now: datetime) -> planning.Plan:
    """The album's plan: the movie's rules with the album's anchor."""
    title = fact.facts
    wanting = [version for version in title.versions if version.wants]
    if not wanting:
        return planning.Plan(wanted=False, next_at=None, reason="nothing_wanted", anchor=fact.anchor)
    planned = [planning.version_plan(title, version, now, anchor=fact.anchor) for version in wanting]
    next_at, reason, anchor = min(planned, key=lambda item: (item[0], planning._PRIORITY[item[1]]))
    next_at, reason = planning.grab_limit_plan(next_at, reason, title.grab_free_at)
    stored_limit = title.stored_reason == "limit" and title.stored_next_at is not None and title.stored_next_at > now
    limit_at = title.stored_next_at if stored_limit else None
    if limit_at is not None and reason in planning.SEARCHING_REASONS and next_at <= limit_at:
        next_at, reason = limit_at, "limit"
    return planning.Plan(
        wanted=True,
        next_at=next_at,
        reason=reason,
        anchor=anchor,
        wanting=tuple(version.definition_id for version in wanting),
    )


def search_plan(db: OrmSession, title: Title, now: datetime) -> dict[str, Any]:
    """``search_plan`` of an album's detail as the scheduler makes it; ``off`` when its time came, the switch off."""
    enabled = settings.load_enabled(db, "album")
    fact = load_facts(db, [title.id], now).get(title.id)
    if fact is None:
        plan = planning.Plan(wanted=False, next_at=None, reason="nothing_wanted", anchor=anchors.Anchor(kind="none"))
    else:
        plan = title_plan(fact, now)
    reason = plan.reason
    if not enabled and plan.wanted and plan.next_at is not None and plan.next_at <= now:
        reason = "off"
    return {
        "automatic": enabled,
        "wanted": plan.wanted,
        "last_at": title.last_search_at,
        "next_at": plan.next_at,
        "reason": reason,
        "anchor": planning.anchor_out(plan.anchor),
        "summary": planning.summary_out(title.search_summary),
        "seasons": [],
    }
