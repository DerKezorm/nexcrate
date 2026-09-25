"""Profiles by hand: custom formats and quality sizes of their own.

Radarr keeps three things apart, and so does nexcrate from now on: the sizes a quality may weigh, the custom
formats, and the profile that orders the qualities and gives the formats their scores. What every judgement reads
stays ``profiles.rules``; it is built either from the answers of the assistant or, in expert mode, from
``profiles.expert`` plus the two tables. Building it on every save keeps the hot paths untouched.

* ``split_out`` runs once after the migration: it takes the formats and the sizes out of the stored profiles into
  their own tables and writes what the expert mode starts from. It changes no rules, so nothing is judged
  differently because of it.
* ``compile_rules`` builds the rules of a profile in expert mode.
* ``recompile`` writes them again for every profile of a kind, after a format or a size changed.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ...models import CustomFormat, Profile, QualitySize
from . import quality_defaults

logger = logging.getLogger("nexcrate.profiles")

#: The kinds that judge with TRaSH's custom formats. Music has its own, simpler rules (M2).
KINDS = ("movie", "series")
#: What ``expert`` holds, beside the formats: the parts of the rules the owner sets by hand.
FIELDS = (
    "qualities",
    "cutoff",
    "upgrades_allowed",
    "min_score",
    "upgrade_until",
    "min_upgrade_step",
    "target_resolution",
    "languages",
    "required_languages",
)


def formats_of(db: OrmSession, kind: str) -> dict[int, CustomFormat]:
    return {row.id: row for row in db.scalars(select(CustomFormat).where(CustomFormat.kind == kind))}


def sizes_of(db: OrmSession, kind: str) -> dict[str, dict[str, Any]]:
    """The sizes of a kind as the rules hold them. A quality without a preferred size has no such entry, so rules
    built from the table stay exactly the rules the assistant writes."""
    found: dict[str, dict[str, Any]] = {}
    for row in db.scalars(select(QualitySize).where(QualitySize.kind == kind)):
        found[row.quality] = {"min_mb_per_min": row.min_mb_per_min, "max_mb_per_min": row.max_mb_per_min}
        if row.preferred_mb_per_min is not None:
            found[row.quality]["preferred_mb_per_min"] = row.preferred_mb_per_min
    return found


def _allowed_names(qualities: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for entry in qualities:
        if not entry.get("allowed"):
            continue
        names.extend(entry.get("items") or ([entry["name"]] if entry.get("name") else []))
    return names


def _one_set(
    expert: dict[str, Any],
    *,
    kind: str,
    known: dict[int, CustomFormat],
    sizes: dict[str, dict[str, Any]],
    rules_version: int,
    trash_commit: str,
    trash_profile: Any,
) -> dict[str, Any]:
    """One set of rules from one set of hand-made settings. Used for the profile itself and for its anime branch."""
    qualities = list(expert.get("qualities") or [])
    formats = []
    for entry in expert.get("formats") or []:
        row = known.get(int(entry.get("format_id", 0)))
        if row is None:
            continue
        formats.append(
            {
                "id": row.id,
                "name": row.name,
                "score": int(entry.get("score") or 0),
                "trash_id": row.trash_id,
                "specifications": list(row.specifications or []),
            }
        )
    rules: dict[str, Any] = {
        "kind": kind,
        "rules_version": rules_version,
        "trash_commit": trash_commit,
        "trash_profile": trash_profile,
        "qualities": qualities,
        "formats": formats,
        "sizes": {name: sizes[name] for name in _allowed_names(qualities) if name in sizes},
        "warnings": [],
    }
    for field in FIELDS:
        if field in ("qualities",):
            continue
        rules[field] = expert.get(field)
    rules["upgrades_allowed"] = bool(expert.get("upgrades_allowed", True))
    rules["min_score"] = int(expert.get("min_score") or 0)
    rules["upgrade_until"] = int(expert.get("upgrade_until") or 0)
    rules["min_upgrade_step"] = int(expert.get("min_upgrade_step") or 1)
    return rules


def compile_rules(db: OrmSession, profile: Profile) -> dict[str, Any]:
    """The rules of a profile in expert mode: its own settings, the formats it points at, the sizes of its kind.

    A series profile may carry an anime branch of its own (``expert["anime"]``, the design notes, B2). It is
    built exactly like the profile itself and lands under ``rules["anime"]``, where ``profiles.series.rules_for``
    reads it for a series of the type anime. Without it an anime series is judged by the profile itself, as before.
    """
    expert = profile.expert if isinstance(profile.expert, dict) else {}
    known = formats_of(db, profile.kind)
    sizes = sizes_of(db, profile.kind)
    common: dict[str, Any] = {
        "kind": profile.kind,
        "known": known,
        "sizes": sizes,
        "rules_version": int((profile.rules or {}).get("rules_version") or 1),
        "trash_commit": profile.trash_commit or "",
        "trash_profile": (profile.rules or {}).get("trash_profile"),
    }
    rules = _one_set(expert, **common)
    anime = expert.get("anime")
    if profile.kind == "series" and isinstance(anime, dict):
        rules["anime"] = _one_set(anime, **common)
    return rules


def _hand_made(rules: dict[str, Any], format_ids: dict[str, int]) -> dict[str, Any]:
    """One set of rules as hand-made settings: the fields, and the formats as references."""
    expert = {field: rules.get(field) for field in FIELDS}
    expert["formats"] = [
        {"format_id": format_ids[entry["name"]], "score": int(entry.get("score") or 0)}
        for entry in rules.get("formats") or []
        if isinstance(entry, dict) and entry.get("name") in format_ids
    ]
    return expert


def start_from_rules(rules: dict[str, Any], format_ids: dict[str, int]) -> dict[str, Any]:
    """What the expert mode starts from: the profile as the assistant built it, with the formats as references.

    An anime branch the assistant built (A4) comes along, so switching to the expert mode keeps
    judging an anime series the way it did before (B2).
    """
    expert = _hand_made(rules, format_ids)
    anime = rules.get("anime")
    if isinstance(anime, dict):
        expert["anime"] = _hand_made(anime, format_ids)
    return expert


def anime_start(db: OrmSession, profile: Profile) -> dict[str, Any] | None:
    """The anime branch the expert mode starts from, or None when the profile has none.

    ⚠️ ``split_out`` ran before the anime rules existed (A4), so a profile of the assistant carries them in
    ``rules["anime"]`` but not yet in ``expert``. They are derived here, so switching to the expert mode keeps
    judging an anime series the way the assistant did instead of quietly falling back to the profile itself.
    """
    expert = profile.expert if isinstance(profile.expert, dict) else {}
    stored = expert.get("anime")
    if isinstance(stored, dict):
        return stored
    rules = profile.rules if isinstance(profile.rules, dict) else {}
    anime = rules.get("anime")
    if profile.kind != "series" or not isinstance(anime, dict):
        return None
    ids = {row.name: row.id for row in db.scalars(select(CustomFormat).where(CustomFormat.kind == profile.kind))}
    return _hand_made(anime, ids)


def trash_formats_into_table(
    db: OrmSession, kind: str, formats: list[dict[str, Any]], moment: datetime
) -> tuple[dict[str, int], int]:
    """The rows for the formats of rules built from TRaSH, created where the table has none, and how many were new.

    A row is found by its trash id first, then by its name. ⚠️ A row the owner changed keeps what he made of it:
    the name is taken, and taking his format beats a second one beside it that no list could tell apart.
    """
    rows = list(db.scalars(select(CustomFormat).where(CustomFormat.kind == kind)))
    by_trash = {row.trash_id: row for row in rows if row.trash_id}
    by_name = {row.name: row for row in rows}
    ids: dict[str, int] = {}
    created = 0
    for entry in formats:
        name = str(entry.get("name") or "")[:200]
        trash_id = str(entry["trash_id"])[:64] if entry.get("trash_id") else None
        if not name:
            continue
        row = (by_trash.get(trash_id) if trash_id else None) or by_name.get(name)
        if row is None:
            row = CustomFormat(
                kind=kind,
                name=name,
                origin="trash" if trash_id else "own",
                trash_id=trash_id,
                specifications=list(entry.get("specifications") or []),
                created_at=moment,
                updated_at=moment,
            )
            db.add(row)
            db.flush()
            by_name[name] = row
            if trash_id:
                by_trash[trash_id] = row
            created += 1
        ids[name] = row.id
    return ids, created


def anime_from_trash(
    db: OrmSession, profile: Profile, snapshot: Any, family: str | None, moment: datetime
) -> tuple[dict[str, Any], int, str]:
    """An anime branch filled from TRaSH's anime profile (B2b), not saved.

    The answers of the profile count where it has any (take now, good enough, size limit, the detailed ones), as
    for the assistant; a profile made by hand from nothing gets the defaults. Formats the table lacks are created,
    which changes no judgement: a format counts only where a profile gives it a score. Returns the branch, the
    number of formats created and the TRaSH profile it came from.
    """
    from . import AnswersInvalid, normalized
    from . import series as series_profiles

    stored = profile.answers if isinstance(profile.answers, dict) and profile.answers else None
    try:
        answers = normalized("series", stored or {})
    except AnswersInvalid:
        stored, answers = None, normalized("series", {})
    if family is None:
        # The languages set by hand decide, as the expert mode is what the owner looks at. ⚠️ Not the defaults of
        # the assistant: they ask for German, and a profile without answers would always get the German profile.
        if isinstance(profile.expert, dict):
            languages = profile.expert.get("languages") or []
            german = any(isinstance(entry, dict) and entry.get("code") == "de" for entry in languages)
        else:
            german = stored is not None and series_profiles.family_of(answers) == "german"
        family = "german" if german else "standard"
    rules = series_profiles.build_anime(answers, snapshot, family)
    ids, created = trash_formats_into_table(db, profile.kind, list(rules.get("formats") or []), moment)
    return _hand_made(rules, ids), created, str(rules.get("trash_profile") or "")


def split_out(db: OrmSession, moment: datetime) -> dict[str, int]:
    """Take the formats and the sizes of every stored profile into their own tables, once.

    Idempotent: a format or a size that is already there stays as it is, and a profile that already knows what the
    expert mode starts from is left alone. Nothing about the rules changes.
    """
    counts = {"formats": 0, "sizes": 0, "profiles": 0}
    rows = list(db.scalars(select(Profile).where(Profile.kind.in_(KINDS))))
    for profile in rows:
        rules = profile.rules if isinstance(profile.rules, dict) else {}
        known = {row.name: row for row in db.scalars(select(CustomFormat).where(CustomFormat.kind == profile.kind))}
        for entry in rules.get("formats") or []:
            if not isinstance(entry, dict) or not entry.get("name") or entry["name"] in known:
                continue
            row = CustomFormat(
                kind=profile.kind,
                name=str(entry["name"])[:200],
                origin="trash" if entry.get("trash_id") else "own",
                trash_id=(str(entry["trash_id"])[:64] if entry.get("trash_id") else None),
                specifications=list(entry.get("specifications") or []),
                created_at=moment,
                updated_at=moment,
            )
            db.add(row)
            db.flush()
            known[row.name] = row
            counts["formats"] += 1
        stored = {row.quality for row in db.scalars(select(QualitySize).where(QualitySize.kind == profile.kind))}
        for quality, size in (rules.get("sizes") or {}).items():
            if quality in stored or not isinstance(size, dict):
                continue
            db.add(
                QualitySize(
                    kind=profile.kind,
                    quality=str(quality)[:64],
                    min_mb_per_min=float(size.get("min_mb_per_min") or 0.0),
                    max_mb_per_min=(
                        float(size["max_mb_per_min"]) if isinstance(size.get("max_mb_per_min"), int | float) else None
                    ),
                    updated_at=moment,
                )
            )
            stored.add(quality)
            counts["sizes"] += 1
        if not isinstance(profile.expert, dict):
            profile.expert = start_from_rules(rules, {name: row.id for name, row in known.items()})
            profile.updated_at = moment
            counts["profiles"] += 1
    return counts


def write_sizes(db: OrmSession, kind: str, sizes: dict[str, Any], moment: datetime) -> int:
    """Put the sizes of a profile of the assistant into the table of its kind, overwriting what is there.

    ⚠️ The assistant is the one place that picks a size set and an upper limit. Since the split the table is what
    everything is built from, so its numbers have to be the assistant's, not the defaults seeded before it. Only
    the qualities the profile names are touched; the owner's other numbers stay.

    ⚠️ A set without ``preferred_mb_per_min`` (the assistant's) takes the preferred size away. Found by a test: the
    default of 95 stayed next to the guides' limits, where it means nothing, and the smaller of two equal releases
    would have won. The guides want the larger one, which is what no preferred size gives.
    """
    rows = {row.quality: row for row in db.scalars(select(QualitySize).where(QualitySize.kind == kind))}
    changed = 0
    for quality, size in sizes.items():
        if not isinstance(size, dict):
            continue
        minimum = float(size.get("min_mb_per_min") or 0.0)
        maximum = float(size["max_mb_per_min"]) if isinstance(size.get("max_mb_per_min"), int | float) else None
        row = rows.get(quality)
        preferred = size.get("preferred_mb_per_min")
        preferred = within(float(preferred), minimum, maximum) if isinstance(preferred, int | float) else None
        if row is None:
            db.add(
                QualitySize(
                    kind=kind,
                    quality=str(quality)[:64],
                    min_mb_per_min=minimum,
                    max_mb_per_min=maximum,
                    preferred_mb_per_min=preferred,
                    updated_at=moment,
                )
            )
            changed += 1
        elif (row.min_mb_per_min, row.max_mb_per_min, row.preferred_mb_per_min) != (minimum, maximum, preferred):
            row.min_mb_per_min = minimum
            row.max_mb_per_min = maximum
            row.preferred_mb_per_min = preferred
            row.updated_at = moment
            changed += 1
    return changed


def within(preferred: float, minimum: float, maximum: float | None) -> float | None:
    """The preferred size when it lies within the limits, else nothing."""
    if preferred < minimum or (maximum is not None and preferred > maximum):
        return None
    return preferred


def seed_sizes(db: OrmSession, moment: datetime) -> int:
    """Give every quality that has no size yet the default of Radarr or Sonarr, once.

    A quality nobody takes had no entry at all before, so its page said zero and no limit. What a stored profile
    already brought stays untouched, and a quality no profile allows takes no part in any judgement until the
    owner allows it, so nothing is judged differently by this.
    """
    added = 0
    for kind, defaults in quality_defaults.BY_KIND.items():
        stored = {row.quality for row in db.scalars(select(QualitySize).where(QualitySize.kind == kind))}
        for quality, (minimum, maximum, preferred) in defaults.items():
            if quality in stored:
                continue
            db.add(
                QualitySize(
                    kind=kind,
                    quality=quality,
                    min_mb_per_min=float(minimum),
                    max_mb_per_min=None if maximum is None else float(maximum),
                    preferred_mb_per_min=None if preferred is None else float(preferred),
                    updated_at=moment,
                )
            )
            added += 1
    return added


def seed_preferred(db: OrmSession, moment: datetime) -> int:
    """Give the rows that still hold the untouched defaults the preferred size that belongs to them, once.

    The column came later than the rows. A row whose limits are exactly Radarr's or Sonarr's own is one nobody has
    set, so the app's own preferred size fits it. A row with other limits (the assistant's, the owner's) stays
    without one: the default would lie anywhere relative to those, and without one the larger release wins, which
    is what those rows did before.
    """
    changed = 0
    for kind, defaults in quality_defaults.BY_KIND.items():
        for row in db.scalars(select(QualitySize).where(QualitySize.kind == kind)):
            default = defaults.get(row.quality)
            if default is None or row.preferred_mb_per_min is not None or default[2] is None:
                continue
            if (row.min_mb_per_min, row.max_mb_per_min) == (float(default[0]), default[1] and float(default[1])):
                row.preferred_mb_per_min = float(default[2])
                row.updated_at = moment
                changed += 1
    return changed


def recompile(db: OrmSession, kind: str, moment: datetime) -> int:
    """Write the rules again for every profile of this kind, after a format or a size changed.

    ⚠️ A profile of the assistant is built again too, not only one kept by hand. Measured on a fresh profile,
    building it from its references gives exactly the rules the assistant wrote (only each format now carries the
    number of its row); so a changed format reaches every profile that points at it, as it does in Radarr. Saving
    in the assistant afterwards still builds from the guides and overwrites the change, and it says so.

    A profile without references is left alone: ``split_out`` gives every stored profile its references, and until
    then there is nothing to build from.
    """
    changed = 0
    for profile in db.scalars(select(Profile).where(Profile.kind == kind)):
        if not isinstance(profile.expert, dict) or not profile.expert.get("qualities"):
            continue
        built = compile_rules(db, profile)
        if built != profile.rules:
            profile.rules = built
            profile.updated_at = moment
            changed += 1
    return changed
