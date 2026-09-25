"""Read the quality setup of a Radarr or Sonarr and build the same thing here (E5).

Three things come over, exactly the three places both apps keep apart:

* the **quality definitions** (what a quality may weigh, and the size it prefers) into ``quality_sizes`` of the kind,
* the **custom formats** with their conditions into ``custom_formats``,
* the **quality profiles** into profiles of their own, in expert mode, pointing at those formats.

⚠️ Measured on 20.09.2026 against Sonarr 4.0.19 and Radarr 5: a custom format's ``specifications[].fields`` comes
back from the API as a **list** of ``{order, name, label, value, …}``, while the engine (and TRaSH's files) hold a
dictionary ``{"value": …}``. The import turns the one into the other; without that every condition would read as
empty and the format would match everything.

What cannot be rebuilt is named, never guessed: a quality this kind does not know, a condition the engine has no
type for, and Radarr's single profile language when it is not a plain language.

**Sonarr's release profiles** (finding 6 of 20.09.2026) have no place of their own here and become custom formats,
the way TRaSH itself writes "unwanted" ones: *must not contain* is a format that matches when one of the terms is in
the release title, *must contain* is a format that matches when none of them is, and every profile taken over gives
both ``REJECT_SCORE``. Measured against Sonarr 4.0.19: ``GET /api/v3/releaseprofile`` answers ``name``, ``enabled``,
``required``, ``ignored``, ``indexerId`` and ``tags``; a term is plain text, or ``/pattern/`` with an optional ``i``.
A profile bound to an indexer or to tags there cannot be bound here (no tags, and a format does not see the indexer):
its formats come over **without a score**, and the plan says so. One that is switched off does nothing there and
becomes nothing here.

**Repacks** (finding 4): there is no step for proper and repack here (``search/ranking.py``), so a profile gets a
repack as an upgrade only through a format that scores it. The plan reads, with the engine itself, whether a profile
scores such a format; one that does not says ``repack_unscored``, and TRaSH's own formats are offered with it.

Nothing is ever written into the other app; every call is a GET.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import regex
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ...models import CustomFormat, Profile
from ..releases import formats as format_engine
from ..releases import languages as lang
from . import expert as profile_expert
from . import store as profile_store

logger = logging.getLogger("nexcrate.profiles")

#: The app of a connection and the kind of its profiles. Lidarr is left out: music has its own, simpler profile.
KIND_OF_APP = {"radarr": "movie", "sonarr": "series"}
#: The fields of a specification the engine reads; everything else the API sends is label and layout.
FIELD_NAMES = ("value", "min", "max", "exceptLanguage")
#: The specification types the engine knows (``releases/formats.py``).
KNOWN_TYPES = frozenset(
    {
        "ReleaseTitleSpecification",
        "ReleaseGroupSpecification",
        "EditionSpecification",
        "SourceSpecification",
        "ResolutionSpecification",
        "QualityModifierSpecification",
        "LanguageSpecification",
        "ReleaseTypeSpecification",
        "IndexerFlagSpecification",
        "SizeSpecification",
        "YearSpecification",
    }
)


#: What a format gets that stands for "never take this", as TRaSH's unwanted formats do.
REJECT_SCORE = -10000
#: TRaSH's formats for a repack, a second and a third one; offered where a profile scores none.
REPACK_NAMES = ("Repack/Proper", "Repack2", "Repack3")
#: A term of a release profile written as a pattern: ``/pattern/`` and what follows the last slash.
_PATTERN_TERM = regex.compile(r"^/(?P<pattern>.*)/(?P<modifiers>[a-z]*)$")
#: The commit under which the plan compiles what it only probes; forgotten again afterwards.
_PROBE_COMMIT = "arr-import-probe"


@dataclass(frozen=True)
class PlannedFormat:
    name: str
    specifications: list[dict[str, Any]]
    #: A format of this name is already here; it stays as it is and the profile points at it.
    exists: bool
    #: Condition types the engine has no place for; they are left out of the format.
    unknown_types: tuple[str, ...] = ()
    #: Built from a release profile of Sonarr, not read as a format there.
    from_release_profile: bool = False


@dataclass(frozen=True)
class PlannedReleaseProfile:
    name: str
    #: The formats it became, by name. Empty for one that is switched off, or that held nothing readable.
    formats: tuple[str, ...]
    enabled: bool
    #: What it is bound to there and cannot be bound to here: ``indexer``, ``tags``. Its formats get no score then.
    bound: tuple[str, ...] = ()
    #: Terms that are no pattern the engine can compile; they are left out.
    unreadable_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class RepackOffer:
    name: str
    score: int
    trash_id: str | None
    specifications: list[dict[str, Any]]


@dataclass(frozen=True)
class PlannedProfile:
    name: str
    #: A profile of this name is already here; taking it over would need another name.
    taken: bool
    qualities: list[dict[str, Any]]
    cutoff: str | None
    min_score: int
    upgrade_until: int
    min_upgrade_step: int
    upgrades_allowed: bool
    #: Format name to score, only the ones with a score.
    scores: dict[str, int]
    languages: list[dict[str, str]] = field(default_factory=list)
    #: What this profile loses on the way, in plain words for the interface.
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Plan:
    kind: str
    profiles: list[PlannedProfile]
    formats: list[PlannedFormat]
    #: Quality to (minimum, maximum) in MB per minute.
    sizes: dict[str, tuple[float, float | None, float | None]]
    #: Qualities of the other app this kind does not know, by name.
    unknown_qualities: tuple[str, ...] = ()
    release_profiles: tuple[PlannedReleaseProfile, ...] = ()
    #: TRaSH's repack formats, only while a profile of this plan scores no repack.
    repack_offer: tuple[RepackOffer, ...] = ()
    #: The other app takes a proper or a repack as an upgrade by itself (its default). Then a profile without a
    #: repack format behaves differently here, and the offer is worth taking; otherwise nothing is lost.
    repacks_upgrade_there: bool = False


def _fields_of(raw: Any) -> dict[str, Any]:
    """The fields of a specification as the engine holds them: a dictionary, not the API's list."""
    if isinstance(raw, dict):
        return {name: raw[name] for name in FIELD_NAMES if name in raw}
    found: dict[str, Any] = {}
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict) and entry.get("name") in FIELD_NAMES and "value" in entry:
                found[str(entry["name"])] = entry["value"]
    return found


def _specification_of(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    implementation = str(raw.get("implementation") or "")
    if implementation not in KNOWN_TYPES:
        return None
    return {
        "name": str(raw.get("name") or "")[:200],
        "implementation": implementation,
        "negate": bool(raw.get("negate")),
        "required": bool(raw.get("required")),
        "fields": _fields_of(raw.get("fields")),
    }


def _unknown_types(raw: Any) -> list[str]:
    found: list[str] = []
    for entry in raw if isinstance(raw, list) else []:
        if isinstance(entry, dict):
            implementation = str(entry.get("implementation") or "")
            if implementation and implementation not in KNOWN_TYPES:
                found.append(implementation)
    return found


def _format_of(raw: Any, known_names: set[str]) -> PlannedFormat | None:
    if not isinstance(raw, dict) or not raw.get("name"):
        return None
    name = str(raw["name"])[:200]
    specifications = [
        built for built in (_specification_of(entry) for entry in raw.get("specifications") or []) if built is not None
    ]
    return PlannedFormat(
        name=name,
        specifications=specifications,
        exists=name in known_names,
        unknown_types=tuple(dict.fromkeys(_unknown_types(raw.get("specifications")))),
    )


def _quality_name(entry: Any) -> str | None:
    quality = entry.get("quality") if isinstance(entry, dict) else None
    name = quality.get("name") if isinstance(quality, dict) else None
    return str(name) if name else None


def _entries_of(items: Any, known_qualities: set[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """The quality list in the shape the rules hold, and the names this kind does not know."""
    entries: list[dict[str, Any]] = []
    unknown: list[str] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        allowed = bool(item.get("allowed"))
        members = item.get("items")
        if isinstance(members, list) and members:
            names = []
            for member in members:
                name = _quality_name(member)
                if name is None:
                    continue
                (names if name in known_qualities else unknown).append(name)
            if names:
                entries.append({"group": str(item.get("name") or names[0])[:64], "items": names, "allowed": allowed})
            continue
        name = _quality_name(item)
        if name is None:
            continue
        if name in known_qualities:
            entries.append({"name": name, "allowed": allowed})
        else:
            unknown.append(name)
    return entries, unknown


def _cutoff_label(items: Any, cutoff: Any, entries: list[dict[str, Any]]) -> str | None:
    """The name of the entry the cutoff points at: a quality by its id, a group by its own id."""
    wanted = cutoff if isinstance(cutoff, int) else None
    if wanted is None:
        return None
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        members = item.get("items")
        if isinstance(members, list) and members:
            if item.get("id") == wanted:
                return str(item.get("name") or "")[:64] or None
            continue
        quality = item.get("quality") if isinstance(item.get("quality"), dict) else {}
        if quality.get("id") == wanted:
            return _quality_name(item)
    # A cutoff nothing matches leaves the best allowed entry, so upgrading still stops somewhere.
    allowed = [entry.get("group") or entry.get("name") for entry in entries if entry.get("allowed")]
    return str(allowed[-1]) if allowed else None


def _languages_of(raw: Any) -> tuple[list[dict[str, str]], str | None]:
    """Radarr's one language as nexcrate's list; Sonarr has none. Returns the list and what could not be taken.

    ⚠️ Radarr's **Original** (-2) is not a loss: a profile here knows the same thing under the code ``original``
    (``profiles/building.py``), and most of the owner's profiles are set to it. Only **Any** (-1) becomes nothing,
    because a profile that takes every language demands none.
    """
    if not isinstance(raw, dict):
        return [], None
    number = raw.get("id")
    name = str(raw.get("name") or "")
    if not isinstance(number, int):
        return [], name or None
    if number == lang.ORIGINAL:
        return [{"code": "original", "role": "required"}], None
    if number <= 0:
        # Any, Unknown: nothing to demand. Not a loss either, so no note.
        return [], None
    language = lang.BY_ID.get(number)
    if language is None or not language.iso:
        return [], name or None
    return [{"code": language.iso, "role": "required"}], None


def term_pattern(term: str) -> str | None:
    """A term of a release profile as the pattern the engine searches the release title with; None when unreadable.

    Sonarr looks for plain text anywhere in the title, whatever the case; ``/pattern/i`` is a pattern of any case and
    ``/pattern/`` one that minds the case. The engine ignores case throughout, so the last one switches that off for
    itself.
    """
    written = _PATTERN_TERM.match(term)
    if written is None:
        return regex.escape(term, special_only=True, literal_spaces=True)
    pattern = written["pattern"] if "i" in written["modifiers"] else f"(?-i:{written['pattern']})"
    try:
        regex.compile(pattern, regex.IGNORECASE)
    except (regex.error, TypeError, ValueError, OverflowError):
        # ⚠️ The engine counts a pattern that does not compile as "not found". Negated, that is "always": a term
        # that must be contained would refuse every release there is.
        return None
    return pattern


def _terms_of(raw: Any) -> list[str]:
    # An older Sonarr sent one comma-separated string; 4.0.19 sends a list.
    parts = raw.split(",") if isinstance(raw, str) else raw if isinstance(raw, list) else []
    return list(dict.fromkeys(str(part).strip() for part in parts if str(part).strip()))


def _release_profile_of(
    raw: Any, taken_names: set[str], known_format_names: set[str]
) -> tuple[PlannedReleaseProfile, list[PlannedFormat]] | None:
    if not isinstance(raw, dict):
        return None
    number = raw.get("id")
    name = str(raw.get("name") or "").strip()[:150] or f"Release profile {number if number is not None else '?'}"
    enabled = raw.get("enabled") is not False
    bound = tuple(
        label for label, held in (("indexer", bool(raw.get("indexerId"))), ("tags", bool(raw.get("tags")))) if held
    )
    if not enabled:
        return PlannedReleaseProfile(name=name, formats=(), enabled=False, bound=bound), []
    built: list[PlannedFormat] = []
    unreadable: list[str] = []
    # Must not contain: one term found is enough. Must contain: every term has to be missing.
    for label, terms, negate in (
        ("must not contain", _terms_of(raw.get("ignored")), False),
        ("must contain", _terms_of(raw.get("required")), True),
    ):
        specifications = []
        for term in terms:
            pattern = term_pattern(term)
            if pattern is None:
                unreadable.append(term)
                continue
            specifications.append(
                {
                    "name": term[:200],
                    "implementation": "ReleaseTitleSpecification",
                    "negate": negate,
                    "required": negate,
                    "fields": {"value": pattern},
                }
            )
        if not specifications:
            continue
        format_name = f"{name} ({label})"
        if format_name in taken_names:
            format_name = f"{name} {number} ({label})"
        taken_names.add(format_name)
        built.append(
            PlannedFormat(
                name=format_name,
                specifications=specifications,
                exists=format_name in known_format_names,
                from_release_profile=True,
            )
        )
    profile = PlannedReleaseProfile(
        name=name,
        formats=tuple(entry.name for entry in built),
        enabled=True,
        bound=bound,
        unreadable_terms=tuple(unreadable),
    )
    return profile, built


#: The same release without and with the word; a repack format tells them apart.
_REPACK_PROBES = (
    ("Example.Title.2019.1080p.BluRay.x264-GROUP", "Example.Title.2019.REPACK.1080p.BluRay.x264-GROUP"),
    ("Example.Title.2019.1080p.BluRay.x264-GROUP", "Example.Title.2019.PROPER.1080p.BluRay.x264-GROUP"),
)


def _probe_input(title: str) -> format_engine.FormatInput:
    return format_engine.FormatInput(
        release_title=title,
        group="GROUP",
        edition=None,
        source=0,
        resolution=1080,
        modifier=0,
        languages=(),
        original_language=0,
    )


def scores_a_repack(specifications: list[dict[str, Any]]) -> bool:
    """Whether a format tells a repack or a proper from the same release without the word.

    Read by the engine itself, so a format counts by what it does and not by what it is called.
    """
    if not specifications:
        return False
    custom_format = {"specifications": specifications}
    return any(
        format_engine.format_matches(custom_format, _probe_input(marked), _PROBE_COMMIT)
        and not format_engine.format_matches(custom_format, _probe_input(plain), _PROBE_COMMIT)
        for plain, marked in _REPACK_PROBES
    )


def plan(
    *,
    kind: str,
    profiles: list[Any],
    formats: list[Any],
    definitions: list[Any],
    known_qualities: set[str],
    known_profile_names: set[str],
    known_formats: dict[str, list[dict[str, Any]]],
    release_profiles: list[Any] | None = None,
    repack_formats: list[RepackOffer] | None = None,
    propers_and_repacks: str | None = None,
) -> Plan:
    """What taking this connection over would make here. Reads nothing and writes nothing.

    ``known_formats`` are the formats of this kind that are already here, by name with their conditions: a format
    of a name that is taken stays as it is, so what it does is what the one here does.
    """
    known_format_names = set(known_formats)
    planned_formats = [built for built in (_format_of(entry, known_format_names) for entry in formats) if built]
    taken_names = {entry.name for entry in planned_formats}
    planned_release_profiles: list[PlannedReleaseProfile] = []
    rejecting: list[str] = []
    for raw_release_profile in release_profiles or []:
        read = _release_profile_of(raw_release_profile, taken_names, known_format_names)
        if read is None:
            continue
        release_profile, its_formats = read
        planned_release_profiles.append(release_profile)
        planned_formats.extend(its_formats)
        if not release_profile.bound:
            rejecting.extend(release_profile.formats)
    # What a format of a name does here: the one that is here already, else the one that comes over.
    doing = {entry.name: entry.specifications for entry in planned_formats} | known_formats
    try:
        repack_names = {name for name, specifications in doing.items() if scores_a_repack(specifications)}
    finally:
        format_engine.patterns.forget(_PROBE_COMMIT)
    sizes: dict[str, tuple[float, float | None, float | None]] = {}
    for entry in definitions:
        quality = entry.get("quality") if isinstance(entry, dict) else None
        name = quality.get("name") if isinstance(quality, dict) else None
        if not name or str(name) not in known_qualities:
            continue
        minimum = entry.get("minSize")
        maximum = entry.get("maxSize")
        preferred = entry.get("preferredSize")
        sizes[str(name)] = (
            float(minimum) if isinstance(minimum, int | float) else 0.0,
            float(maximum) if isinstance(maximum, int | float) else None,
            float(preferred) if isinstance(preferred, int | float) else None,
        )

    planned_profiles: list[PlannedProfile] = []
    unknown_qualities: list[str] = []
    for raw in profiles:
        if not isinstance(raw, dict) or not raw.get("name"):
            continue
        entries, unknown = _entries_of(raw.get("items"), known_qualities)
        unknown_qualities.extend(unknown)
        scores = {
            str(item.get("name")): int(item.get("score") or 0)
            for item in raw.get("formatItems") or []
            if isinstance(item, dict) and item.get("name") and int(item.get("score") or 0) != 0
        }
        scores.update(dict.fromkeys(rejecting, REJECT_SCORE))
        languages, missed_language = _languages_of(raw.get("language"))
        notes: list[str] = []
        if unknown:
            notes.append("qualities_unknown")
        if missed_language:
            notes.append("language_unknown")
        if not entries or not any(entry["allowed"] for entry in entries):
            notes.append("nothing_allowed")
        if not any(score > 0 and name in repack_names for name, score in scores.items()):
            notes.append("repack_unscored")
        planned_profiles.append(
            PlannedProfile(
                name=str(raw["name"])[:200],
                taken=str(raw["name"])[:200] in known_profile_names,
                qualities=entries,
                cutoff=_cutoff_label(raw.get("items"), raw.get("cutoff"), entries),
                min_score=int(raw.get("minFormatScore") or 0),
                upgrade_until=int(raw.get("cutoffFormatScore") or 0),
                min_upgrade_step=int(raw.get("minUpgradeFormatScore") or 1) or 1,
                upgrades_allowed=bool(raw.get("upgradeAllowed")),
                scores=scores,
                languages=languages,
                notes=tuple(notes),
            )
        )
    return Plan(
        kind=kind,
        profiles=planned_profiles,
        formats=planned_formats,
        sizes=sizes,
        unknown_qualities=tuple(dict.fromkeys(unknown_qualities)),
        release_profiles=tuple(planned_release_profiles),
        repack_offer=(
            tuple(repack_formats or ())
            if any("repack_unscored" in planned.notes for planned in planned_profiles)
            else ()
        ),
        repacks_upgrade_there=propers_and_repacks == "preferAndUpgrade",
    )


def apply(db: OrmSession, built: Plan, wanted: set[str], moment: datetime, add_repack: bool = False) -> dict[str, int]:
    """Create the formats, the sizes and the chosen profiles. A profile whose name is taken is left alone.

    ``add_repack`` gives every chosen profile that scores no repack TRaSH's repack formats with TRaSH's scores; a
    format of that name that is already here is pointed at, not made a second time.
    """
    counts = {"formats": 0, "sizes": 0, "profiles": 0}
    rows = {row.name: row for row in db.scalars(select(CustomFormat).where(CustomFormat.kind == built.kind))}
    taking = [planned for planned in built.profiles if planned.name in wanted and not planned.taken]
    if add_repack and any("repack_unscored" in planned.notes for planned in taking):
        for offer in built.repack_offer:
            if offer.name in rows:
                continue
            row = CustomFormat(
                kind=built.kind,
                name=offer.name,
                origin="trash" if offer.trash_id else "own",
                trash_id=offer.trash_id,
                specifications=list(offer.specifications),
                created_at=moment,
                updated_at=moment,
            )
            db.add(row)
            db.flush()
            rows[row.name] = row
            counts["formats"] += 1
    for planned in built.formats:
        if planned.name in rows:
            continue
        row = CustomFormat(
            kind=built.kind,
            name=planned.name,
            origin="own",
            specifications=list(planned.specifications),
            created_at=moment,
            updated_at=moment,
        )
        db.add(row)
        db.flush()
        rows[row.name] = row
        counts["formats"] += 1

    if built.sizes:
        counts["sizes"] = profile_expert.write_sizes(
            db,
            built.kind,
            {
                name: {"min_mb_per_min": low, "max_mb_per_min": high, "preferred_mb_per_min": preferred}
                for name, (low, high, preferred) in built.sizes.items()
            },
            moment,
        )

    for planned in taking:
        scores = dict(planned.scores)
        if add_repack and "repack_unscored" in planned.notes:
            scores.update({offer.name: offer.score for offer in built.repack_offer})
        profile = Profile(
            name=profile_store.free_name(db, built.kind, planned.name),
            kind=built.kind,
            answers={},
            rules={},
            trash_commit="",
            mode="expert",
            expert={
                "qualities": planned.qualities,
                "cutoff": planned.cutoff,
                "upgrades_allowed": planned.upgrades_allowed,
                "min_score": planned.min_score,
                "upgrade_until": planned.upgrade_until,
                "min_upgrade_step": planned.min_upgrade_step,
                "target_resolution": None,
                "languages": planned.languages,
                "required_languages": "all",
                "formats": [
                    {"format_id": rows[name].id, "score": score}
                    for name, score in scores.items()
                    if name in rows
                ],
            },
            created_at=moment,
            updated_at=moment,
        )
        db.add(profile)
        db.flush()
        profile.rules = profile_expert.compile_rules(db, profile)
        counts["profiles"] += 1
    logger.info(
        "Taken over from the other app: %d formats, %d sizes, %d profiles",
        counts["formats"],
        counts["sizes"],
        counts["profiles"],
    )
    return counts
