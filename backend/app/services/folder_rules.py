"""Rules for target folders: the library a title goes to (built 25.09.2026).

A movie or series version has its default folder and, below it, a list of rules "when … then folder …"; the first rule
that fits a title decides. A rule looks at one thing of the title:

* ``genre``: one of TMDB's genres of the title, by TMDB's English name ("Documentary", "Animation").
* ``certification``: the rating of the country set for the calendar, one of the listed values ("0", "6"); movies only,
  as TMDB's ratings of series are not stored. Without a country or a rating the rule does not fit.
* ``tag``: one of the title's tags, by id.
* ``series_type``: ``standard``, ``daily`` or ``anime``; series only.

The owner's answer: rules hold for new titles only. They are read where the folder is chosen today, when the first file
of a version is filed (``downloads/importing.py``, ``series_import``); a version with a file stays where it lies.

**Moving by the rules** (M2, the owner's button "Nach Regel umziehen"): ``relocations`` lists every title of the version
whose folder lies elsewhere than the rules say, and why one cannot move; ``relocate`` moves the title's folder as a
whole into the rule's folder (``rename.apply.move_all``, taken back when a step fails) and writes the new
``root_folder``. The file names inside stay, so nothing else of the title changes. Only on the same disk: a rename
across disks would be a copy, and a title whose folder another version shares stays.

Stored in ``settings`` as ``folder_rules:<definition id>`` (JSON), not as a column: a new column would cost the copy of
the database before the schema change when updating.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ..db import get_setting, set_setting
from ..meldungen import error
from ..models import HistoryEntry, Title, Version, VersionDefinition, utcnow
from . import calendar_feed, folders
from . import tags as tag_store

logger = logging.getLogger("nexcrate.folder_rules")

WHEN = ("genre", "certification", "tag", "series_type")
#: What a kind of version may ask about.
WHEN_FOR = {"movie": ("genre", "certification", "tag"), "series": ("genre", "tag", "series_type")}
SERIES_TYPES = ("standard", "daily", "anime")
RULES_MAX = 20
VALUES_MAX = 50
VALUE_MAX_LENGTH = 64


@dataclass(frozen=True)
class Rule:
    when: str
    values: tuple[str, ...]
    folder: str

    def as_dict(self) -> dict[str, Any]:
        return {"when": self.when, "values": list(self.values), "folder": self.folder}


def _key(definition_id: int) -> str:
    return f"folder_rules:{definition_id}"


def load(db: OrmSession, definition_id: int) -> list[Rule]:
    """The stored rules in their order; an unreadable entry is left out. Never raises."""
    try:
        raw = json.loads(get_setting(db, _key(definition_id), "") or "[]")
    except ValueError:
        return []
    found: list[Rule] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict) or entry.get("when") not in WHEN or not isinstance(entry.get("folder"), str):
            continue
        values = entry.get("values")
        if not isinstance(values, list) or not values or not all(isinstance(value, str) for value in values):
            continue
        found.append(Rule(entry["when"], tuple(values), entry["folder"]))
    return found


def save(db: OrmSession, definition: VersionDefinition, given: list[dict[str, Any]]) -> list[Rule]:
    """Check and store the rules of a version; 422 ``invalid_input`` naming every bad field, or the folder's own error
    (``folder_not_visible``, ``folder_not_writable``, ``folder_in_use``)."""
    allowed = WHEN_FOR.get(definition.kind)
    if allowed is None:
        raise error(
            "folder_rules_not_for_kind", f"A {definition.kind} version has no folder rules.", 422, kind=definition.kind
        )
    if len(given) > RULES_MAX:
        raise error("invalid_input", "The input is not valid.", 422, fields=["rules"])
    bad: list[str] = []
    rules: list[Rule] = []
    for index, entry in enumerate(given):
        when = entry.get("when")
        values = entry.get("values")
        if when not in allowed:
            bad.append(f"rules.{index}.when")
        cleaned = [str(value).strip() for value in values if str(value).strip()] if isinstance(values, list) else []
        cleaned = cleaned[:VALUES_MAX]
        if not cleaned or any(len(value) > VALUE_MAX_LENGTH for value in cleaned):
            bad.append(f"rules.{index}.values")
        if when == "series_type" and any(value not in SERIES_TYPES for value in cleaned):
            bad.append(f"rules.{index}.values")
        if when == "tag" and not all(value.isdigit() for value in cleaned):
            bad.append(f"rules.{index}.values")
        folder = entry.get("folder")
        if not isinstance(folder, str) or not folder.strip():
            bad.append(f"rules.{index}.folder")
        if bad:
            continue
        rules.append(Rule(str(when), tuple(dict.fromkeys(cleaned)), str(folder)))
    if bad:
        raise error("invalid_input", "The input is not valid.", 422, fields=sorted(set(bad)))
    # Every folder as a version's own folder is checked: visible, writable, no other version's.
    rules = [Rule(rule.when, rule.values, folders.check_version_folder(db, definition, rule.folder)) for rule in rules]
    set_setting(db, _key(definition.id), json.dumps([rule.as_dict() for rule in rules], ensure_ascii=False))
    logger.info("Version definition %d: %d folder rules", definition.id, len(rules))
    return rules


def certification(title: Title, country: str) -> str | None:
    """The title's rating in this country from TMDB's release dates: the first one given."""
    if not country:
        return None
    for entry in title.release_dates or []:
        if not isinstance(entry, dict) or str(entry.get("iso_3166_1", "")).upper() != country:
            continue
        for dated in entry.get("release_dates") or []:
            value = str((dated or {}).get("certification") or "").strip() if isinstance(dated, dict) else ""
            if value:
                return value
    return None


def fits(rule: Rule, title: Title, tag_ids: set[int], country: str) -> bool:
    if rule.when == "genre":
        wanted = {value.casefold() for value in rule.values}
        return bool(wanted & {str(genre).casefold() for genre in title.genres or []})
    if rule.when == "certification":
        found = certification(title, country) if title.kind == "movie" else None
        return found is not None and found in rule.values
    if rule.when == "tag":
        return bool({int(value) for value in rule.values if value.isdigit()} & tag_ids)
    if rule.when == "series_type":
        return title.kind == "series" and (title.series_type or "standard") in rule.values
    return False


def folder_for(db: OrmSession, definition: VersionDefinition | None, title: Title | None) -> str | None:
    """The folder a new file of this title goes to in this version: the first fitting rule's, else the default."""
    if definition is None:
        return None
    if title is None:
        return definition.folder
    rules = load(db, definition.id)
    if not rules:
        return definition.folder
    tag_ids = tag_store.title_tag_ids(db, title.id)
    country = calendar_feed.region(db)
    for rule in rules:
        if fits(rule, title, tag_ids, country):
            return rule.folder
    return definition.folder


# --- Moving by the rules (M2) --------------------------------------------------------------------------------------- #

#: What a title is left with in the list, and why.
SKIPS = ("folder_missing", "other_disk", "shared_folder", "target_exists", "busy", "move_failed")


def _device(path: Path) -> int | None:
    try:
        return path.stat().st_dev
    except OSError:
        return None


def _plan(db: OrmSession, definition: VersionDefinition, version: Version, title: Title) -> dict[str, Any] | None:
    """One title's move, or None when it lies where the rules say. ``skip`` names why it cannot move."""
    if version.root_folder is None or not version.relative_path:
        return None
    target = folder_for(db, definition, title)
    if target is None or Path(target) == Path(version.root_folder):
        return None
    top = PurePosixPath(version.relative_path).parts[0]
    found: dict[str, Any] = {
        "title_id": title.id, "name": title.title, "year": title.year, "from": version.root_folder, "to": target,
        "folder": top, "skip": None,
    }  # fmt: skip
    try:
        old_root, _mount = folders.visible(version.root_folder)
        new_root, _mount = folders.visible(target)
    except folders.NotVisible:
        found["skip"] = "folder_missing"
        return found
    if not (old_root / top).exists():
        found["skip"] = "folder_missing"
    elif _device(old_root) != _device(new_root):
        found["skip"] = "other_disk"
    elif (new_root / top).exists():
        found["skip"] = "target_exists"
    else:
        shared = db.scalar(
            select(Version.id).where(
                Version.id != version.id,
                Version.root_folder == version.root_folder,
                (Version.relative_path == top) | Version.relative_path.like(top + "/%"),
            )
        )
        if shared is not None:
            found["skip"] = "shared_folder"
    return found


def _own(db: OrmSession, definition: VersionDefinition) -> list[tuple[Version, Title]]:
    return list(
        db.execute(
            select(Version, Title)
            .join(Title, Title.id == Version.title_id)
            .where(Version.version_definition_id == definition.id, Version.source_id.is_(None))
            .order_by(Title.title, Title.id)
        ).tuples()
    )


def relocations(db: OrmSession, definition: VersionDefinition) -> list[dict[str, Any]]:
    """Every title of the version whose folder lies elsewhere than its rules say, with where it would go."""
    if definition.kind not in WHEN_FOR:
        return []
    found = []
    for version, title in _own(db, definition):
        item = _plan(db, definition, version, title)
        if item is not None:
            found.append(item)
    return found


def relocate(db: OrmSession, definition: VersionDefinition, title_ids: list[int]) -> dict[str, Any]:
    """Move these titles into their rule's folder, one by one; each either moves whole or stays. Commits per title."""
    from .rename import apply as rename_apply
    from .rename import guard

    wanted = set(title_ids)
    moved: list[int] = []
    left: list[dict[str, Any]] = []
    for version, title in _own(db, definition):
        if title.id not in wanted:
            continue
        item = _plan(db, definition, version, title)
        if item is None:
            continue
        if item["skip"] is not None:
            left.append({"title_id": title.id, "skip": item["skip"]})
            continue
        old_root, _mount = folders.visible(version.root_folder or "")
        new_root, _mount = folders.visible(item["to"])
        try:
            with guard.renaming([title.id]):
                rename_apply.move_all([(old_root / item["folder"], new_root / item["folder"])], [new_root])
        except guard.Busy:
            left.append({"title_id": title.id, "skip": "busy"})
            continue
        except rename_apply.MoveFailed:
            left.append({"title_id": title.id, "skip": "move_failed"})
            continue
        moment = utcnow()
        version.root_folder = str(new_root)
        version.updated_at = moment
        db.add(
            HistoryEntry(
                title_id=title.id, version_id=version.id, version_definition_id=definition.id,
                version_label=definition.label, event="relocated", at=moment, detail=str(new_root)[:1024],
            )
        )  # fmt: skip
        db.commit()
        moved.append(title.id)
        logger.info("Title %d moved by the library rules of version definition %d", title.id, definition.id)
    return {"moved": moved, "left": left}
