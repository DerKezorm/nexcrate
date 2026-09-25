"""Auto tags: rules that give tags by themselves, as Auto Tagging in Radarr, Sonarr and Lidarr.

A rule belongs to one kind: ``movie``, ``series`` or ``album`` (it works on artists, as Lidarr's works on artists).
Its conditions are grouped by type; a group fits unless a required condition in it fails or all of it fails, and the
rule fits when every group fits (measured in the apps' ``AutoTaggingService`` and ``SpecificationMatchesGroup``).
When a rule fits, its tags go on the title; when it does not and ``remove_automatically`` is on, its tags go off,
even one set by hand, as in the apps.

Quality profile, root folder and monitored belong to a version here: a title fits when any of its versions does.
Rules run when one is saved or removed (for the whole kind), when a title or artist is added, and every hour.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session as OrmSession

from ..models import Artist, ArtistTag, AutoTag, Tag, Title, TitleTag, Version, VersionDefinition
from .automatic import anchors
from .series.refresh import ENDED_STATUSES

logger = logging.getLogger("nexcrate.auto_tags")

KINDS = ("movie", "series", "album")

#: The condition types per kind, in the order the apps list them.
CONDITIONS: dict[str, tuple[str, ...]] = {
    "movie": ("genre", "year", "root_folder", "runtime", "keyword", "studio", "original_language", "profile", "status",
              "monitored", "tag"),
    "series": ("genre", "year", "root_folder", "original_language", "profile", "status", "monitored", "tag",
               "series_type"),
    "album": ("root_folder", "profile", "status", "monitored", "tag"),
}  # fmt: skip

#: The statuses per kind, as the apps name them (Radarr's ``inCinemas`` written here as ``in_cinemas``).
STATUSES: dict[str, tuple[str, ...]] = {
    "movie": ("tba", "announced", "in_cinemas", "released"),
    "series": ("continuing", "ended", "upcoming"),
    "album": ("continuing", "ended"),
}
SERIES_TYPES = ("standard", "daily", "anime")

LIST_TYPES = ("genre", "keyword", "studio")
RANGE_TYPES = ("year", "runtime")
TEXT_TYPES = ("root_folder", "original_language", "status", "series_type")

NAME_MAX = 100
CONDITIONS_MAX = 50
VALUES_MAX = 100
VALUE_MAX_LENGTH = 1024
RANGE_MAX = 100_000

#: TMDB's release types (docs: 1 premiere, 2 limited, 3 theatrical, 4 digital, 5 physical, 6 TV).
_CINEMA = (1, 2, 3)
_HOME = (4, 5)
_UPCOMING_SERIES = ("Planned", "Pilot")


class RuleInvalid(ValueError):
    def __init__(self, fields: list[str]) -> None:
        super().__init__(", ".join(fields))
        self.fields = fields


# --- Conditions as stored ---------------------------------------------------------------------------------------- #


def clean_conditions(
    kind: str, raw: Iterable[dict[str, Any]], tag_ids: dict[str, int] | None = None
) -> list[dict[str, Any]]:
    """The conditions as stored, checked against the kind. ``tag_ids``: tag names already turned into ids (a ``tag``
    condition carries its name in ``value``). Raises ``RuleInvalid`` with ``conditions.<n>.<field>``."""
    allowed = CONDITIONS[kind]
    items = list(raw)
    if not items:
        raise RuleInvalid(["conditions"])
    if len(items) > CONDITIONS_MAX:
        raise RuleInvalid(["conditions"])
    cleaned: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        where = f"conditions.{index}"
        kind_of = item.get("type")
        if kind_of not in allowed:
            raise RuleInvalid([f"{where}.type"])
        one: dict[str, Any] = {
            "type": kind_of, "negate": bool(item.get("negate")), "required": bool(item.get("required"))
        }
        if kind_of in LIST_TYPES:
            values = [str(value).strip()[:VALUE_MAX_LENGTH] for value in item.get("values") or [] if str(value).strip()]
            if not values or len(values) > VALUES_MAX:
                raise RuleInvalid([f"{where}.values"])
            one["values"] = list(dict.fromkeys(values))
        elif kind_of in RANGE_TYPES:
            low, high = item.get("min"), item.get("max")
            if not _whole(low) or not _whole(high) or low < 0 or high > RANGE_MAX or high < low:
                raise RuleInvalid([f"{where}.min", f"{where}.max"])
            one["min"], one["max"] = low, high
        elif kind_of == "profile":
            profile_id = item.get("profile_id")
            if not _whole(profile_id) or profile_id <= 0:
                raise RuleInvalid([f"{where}.profile_id"])
            one["profile_id"] = profile_id
        elif kind_of == "tag":
            name = str(item.get("value") or "").strip()
            tag_id = (tag_ids or {}).get(name)
            if tag_id is None:
                raise RuleInvalid([f"{where}.value"])
            one["tag_id"] = tag_id
        elif kind_of in TEXT_TYPES:
            value = str(item.get("value") or "").strip()
            if kind_of == "root_folder":
                value = _folder(value)
            elif kind_of == "original_language":
                value = value.lower()
            if not value or len(value) > VALUE_MAX_LENGTH:
                raise RuleInvalid([f"{where}.value"])
            if kind_of == "status" and value not in STATUSES[kind]:
                raise RuleInvalid([f"{where}.value"])
            if kind_of == "series_type" and value not in SERIES_TYPES:
                raise RuleInvalid([f"{where}.value"])
            one["value"] = value
        cleaned.append(one)
    return cleaned


def _whole(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _folder(path: str | None) -> str:
    """A root folder as compared: without a slash at the end (the apps compare the whole path)."""
    text = (path or "").strip()
    while len(text) > 1 and text[-1] in "/\\":
        text = text[:-1]
    return text


def folders_of(db: OrmSession, kind: str) -> list[str]:
    """The root folders versions of a kind use or their definition names, as compared, sorted."""
    folders: set[str] = set()
    versions = (
        select(Version.root_folder, VersionDefinition.folder)
        .join(VersionDefinition, VersionDefinition.id == Version.version_definition_id)
        .where(VersionDefinition.kind == kind)
        .distinct()
    )
    for root_folder, folder in db.execute(versions):
        folders.update(_folder(path) for path in (root_folder, folder) if path)
    for (folder,) in db.execute(select(VersionDefinition.folder).where(VersionDefinition.kind == kind)):
        if folder:
            folders.add(_folder(folder))
    return sorted(folders, key=str.casefold)


# --- What a title offers ----------------------------------------------------------------------------------------- #


@dataclass
class Facts:
    """What the conditions look at, for one title or artist."""

    genres: set[str] = field(default_factory=set)
    keywords: set[str] = field(default_factory=set)
    studios: set[str] = field(default_factory=set)
    year: int | None = None
    runtime: int | None = None
    original_language: str | None = None
    status: str | None = None
    #: Null for a series added without a type; that means "standard" (see ``satisfied``).
    series_type: str | None = None
    tags: set[int] = field(default_factory=set)
    profiles: set[int] = field(default_factory=set)
    root_folders: set[str] = field(default_factory=set)
    monitored: bool = False


def movie_status(release_dates: object, now: datetime) -> str:
    """Radarr's status from TMDB's dates: released once a digital or physical release is past, in cinemas once a
    cinema start is past, announced with a date ahead, else TBA."""
    found = anchors.entries(release_dates)
    if any(item.type in _HOME and item.at <= now for item in found):
        return "released"
    if any(item.type in _CINEMA and item.at <= now for item in found):
        return "in_cinemas"
    return "announced" if found else "tba"


def series_status(status: str | None) -> str:
    """Sonarr's status from TMDB's: ended and canceled are ended, planned and pilot upcoming, the rest continuing."""
    if status in ENDED_STATUSES:
        return "ended"
    if status in _UPCOMING_SERIES:
        return "upcoming"
    return "continuing"


def _lower(values: object) -> set[str]:
    if not isinstance(values, list):
        return set()
    return {str(value).strip().casefold() for value in values if str(value).strip()}


def facts_of(
    db: OrmSession, kind: str, ids: Iterable[int] | None = None, now: datetime | None = None
) -> dict[int, Facts]:
    """The facts of the titles (``movie``, ``series``) or artists (``album``) of a kind; all of them without ``ids``."""
    moment = now or datetime.now(UTC)
    wanted = None if ids is None else sorted(set(ids))
    if wanted is not None and not wanted:
        return {}
    if kind == "album":
        return _artist_facts(db, wanted)
    query = select(
        Title.id, Title.genres, Title.keywords, Title.studios, Title.year, Title.runtime, Title.original_language,
        Title.release_dates, Title.series_status, Title.series_type,
    ).where(Title.kind == kind)  # fmt: skip
    if wanted is not None:
        query = query.where(Title.id.in_(wanted))
    found: dict[int, Facts] = {}
    for row in db.execute(query):
        found[row.id] = Facts(
            genres=_lower(row.genres),
            keywords=_lower(row.keywords),
            studios=_lower(row.studios),
            year=row.year,
            runtime=row.runtime,
            original_language=(row.original_language or "").lower() or None,
            status=movie_status(row.release_dates, moment) if kind == "movie" else series_status(row.series_status),
            series_type=row.series_type,
        )
    if not found:
        return found
    keys = list(found)
    for title_id, tag_id in db.execute(select(TitleTag.title_id, TitleTag.tag_id).where(TitleTag.title_id.in_(keys))):
        found[title_id].tags.add(tag_id)
    versions = select(
        Version.title_id, Version.monitored, Version.root_folder, VersionDefinition.folder, VersionDefinition.profile_id
    ).join(VersionDefinition, VersionDefinition.id == Version.version_definition_id).where(Version.title_id.in_(keys))
    for row in db.execute(versions):
        _add_version(found[row.title_id], row.monitored, row.root_folder, row.folder, row.profile_id)
    return found


def _add_version(
    facts: Facts, monitored: bool, root_folder: str | None, folder: str | None, profile_id: int | None
) -> None:
    facts.monitored = facts.monitored or bool(monitored)
    for path in (root_folder, folder):
        if path:
            facts.root_folders.add(_folder(path))
    if profile_id is not None:
        facts.profiles.add(profile_id)


def _artist_facts(db: OrmSession, wanted: list[int] | None) -> dict[int, Facts]:
    query = select(Artist.id, Artist.ended, Artist.frozen_at).where(Artist.is_various.is_(False))
    if wanted is not None:
        query = query.where(Artist.id.in_(wanted))
    found = {
        row.id: Facts(status="ended" if row.ended else "continuing", monitored=row.frozen_at is None)
        for row in db.execute(query)
    }
    if not found:
        return found
    keys = list(found)
    links = select(ArtistTag.artist_id, ArtistTag.tag_id).where(ArtistTag.artist_id.in_(keys))
    for artist_id, tag_id in db.execute(links):
        found[artist_id].tags.add(tag_id)
    # Root folder and profile come from the versions of the artist's albums; monitored stays the artist's own.
    versions = (
        select(Title.artist_id, Version.root_folder, VersionDefinition.folder, VersionDefinition.profile_id)
        .join(Version, Version.title_id == Title.id)
        .join(VersionDefinition, VersionDefinition.id == Version.version_definition_id)
        .where(Title.artist_id.in_(keys))
    )
    for row in db.execute(versions):
        facts = found[row.artist_id]
        _add_version(facts, facts.monitored, row.root_folder, row.folder, row.profile_id)
    return found


# --- Matching ---------------------------------------------------------------------------------------------------- #


def satisfied(condition: dict[str, Any], facts: Facts) -> bool:
    """One condition, before ``negate``."""
    kind_of = condition.get("type")
    if kind_of in LIST_TYPES:
        own = {"genre": facts.genres, "keyword": facts.keywords, "studio": facts.studios}[kind_of]
        return any(str(value).casefold() in own for value in condition.get("values") or [])
    if kind_of in RANGE_TYPES:
        value = facts.year if kind_of == "year" else facts.runtime
        return value is not None and condition["min"] <= value <= condition["max"]
    if kind_of == "root_folder":
        return condition.get("value") in facts.root_folders
    if kind_of == "original_language":
        return facts.original_language == condition.get("value")
    if kind_of == "profile":
        return condition.get("profile_id") in facts.profiles
    if kind_of == "status":
        return facts.status == condition.get("value")
    if kind_of == "series_type":
        # ⚠️ A series added without a type stores null, one whose type was set stores the text; both mean standard.
        # Compared raw, a rule on "standard" quietly passed every series that had never been set.
        return (facts.series_type or "standard") == condition.get("value")
    if kind_of == "monitored":
        return facts.monitored
    if kind_of == "tag":
        return condition.get("tag_id") in facts.tags
    return False


def fits(conditions: list[dict[str, Any]], facts: Facts) -> bool:
    """The apps' rule: grouped by type, a group fails when a required one fails or all fail; every group must fit."""
    if not conditions:
        return False
    groups: dict[str, list[tuple[bool, bool]]] = defaultdict(list)
    for condition in conditions:
        result = satisfied(condition, facts)
        if condition.get("negate"):
            result = not result
        groups[str(condition.get("type"))].append((bool(condition.get("required")), result))
    return all(
        not any(required and not result for required, result in group) and any(result for _required, result in group)
        for group in groups.values()
    )


# --- Applying ---------------------------------------------------------------------------------------------------- #


def _rules(db: OrmSession, kind: str) -> list[AutoTag]:
    return list(db.scalars(select(AutoTag).where(AutoTag.kind == kind).order_by(AutoTag.id)))


def apply(db: OrmSession, kind: str, ids: Iterable[int] | None = None, now: datetime | None = None) -> int:
    """Run every rule of a kind over the titles (all without ``ids``); the number of titles whose tags changed.
    Every rule looks at the tags as they were; what one rule removes and another gives stays (giving wins). Does not
    commit."""
    rules = _rules(db, kind)
    if not rules:
        return 0
    existing = set(db.scalars(select(Tag.id)))
    facts = facts_of(db, kind, ids, now)
    model, column = (ArtistTag, ArtistTag.artist_id) if kind == "album" else (TitleTag, TitleTag.title_id)
    changed = 0
    for item_id, found in facts.items():
        give: set[int] = set()
        take: set[int] = set()
        for rule in rules:
            tags = {tag_id for tag_id in rule.tag_ids or [] if tag_id in existing}
            if not tags:
                continue
            if fits(rule.conditions or [], found):
                give |= tags
            elif rule.remove_automatically:
                take |= tags
        adding = give - found.tags
        removing = (take - give) & found.tags
        if not adding and not removing:
            continue
        changed += 1
        if removing:
            db.execute(delete(model).where(column == item_id, model.tag_id.in_(sorted(removing))))
        for tag_id in sorted(adding):
            db.execute(insert(model).values({column.key: item_id, "tag_id": tag_id, "source_id": None}))
    if changed:
        logger.info("Auto tags changed the tags of %d %s", changed, "artists" if kind == "album" else f"{kind} titles")
    return changed


def apply_everything(db: OrmSession) -> int:
    return sum(apply(db, kind) for kind in KINDS)


def apply_new(db: OrmSession, kind: str, ids: Iterable[int]) -> int:
    """For a title or artist just added; never raises, a failure only logs and takes back only its own writes."""
    try:
        with db.begin_nested():
            return apply(db, kind, ids)
    except Exception:
        logger.exception("Auto tags for new %s items failed", kind)
        return 0


JOB_NAME = "auto_tags"
#: Every hour, as a catch for what changed without a rule being saved: new versions, profiles, refreshed data.
INTERVAL_SECONDS = 3600


def run_job() -> int:
    from ..db import SessionLocal

    with SessionLocal() as db:
        if db.scalar(select(AutoTag.id).limit(1)) is None:
            return 0
        changed = apply_everything(db)
        db.commit()
        return changed
