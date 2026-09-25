"""The building blocks movies and series share when rules are built from answers.

Decision 5 of the design notes: the two kinds ask nearly the same questions and turn TRaSH's data into
rules the same way. What differs stays in ``movie.py`` and ``series.py``: the question catalogue, the table from
family, resolution and source to TRaSH's profile, the groups a detailed answer adds, and the size set. What is
the same lives here: the formats of a profile, the quality list, the language formats, the size limits, the
summary and the one line.

⚠️ Nothing here may assume a kind. Every function that touches a quality name takes the ``QualityTable`` of its
kind: Radarr's names, sources and order for movies, Sonarr's for series. A test holds the rules of every movie
answer set equal to what ``movie.py`` built before the move.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..releases import formats as release_formats
from ..releases import languages as lang
from ..releases import qualities as q
from ..releases import qualities_series as qs
from ..trash_data import TrashSnapshot
from .questions import ProfileBuildError

#: ``original`` is the title's original language, whatever it is (18.09.2026): a series in Japanese or Korean otherwise
#: met no profile with a file whose audio track carries no language, which counts as the original one.
ORIGINAL = "original"
LANGUAGE_CODES = ("de", "en", "fr", "es", "it", "tr", ORIGINAL)
LANGUAGE_ROLES = ("required", "preferred")
RESOLUTIONS = {"2160p": 2160, "1080p": 1080}
MAX_GB_PER_HOUR = (1, 500)
#: The lower resolutions ``take_now`` adds, by target resolution: the next lower one only.
TAKE_NOW_BELOW: dict[int, tuple[int, ...]] = {2160: (1080,), 1080: (720,)}

REQUIRED_LANGUAGE_SCORE = 10_000
PREFERRED_LANGUAGE_SCORE = 500
ALL_LANGUAGES_SCORE = 50_000
#: TRaSH's own German language formats, by file name. Set to 0 for ``all``.
GERMAN_LANGUAGE_FILES = ("german", "german-dl", "german-dl-undefined")


def language_id(code: str) -> int:
    """Radarr's number of a profile's language code; ``original`` is Radarr's "Original" (-2)."""
    return lang.ORIGINAL if code == ORIGINAL else lang.radarr_id(code)


LANGUAGE_NAMES = {code: lang.name(language_id(code)) for code in LANGUAGE_CODES}
#: The minimum every acceptable release already carries in TRaSH's own German model ("German only").
GERMAN_OWN_MINIMUM = REQUIRED_LANGUAGE_SCORE
SUMMARY_FORMATS = 10


@dataclass(frozen=True)
class QualityTable:
    """The quality table of one kind: its names, its order, and the same source at another resolution."""

    by_name: dict[str, Any]
    weights: dict[str, int]
    same_kind_at: Callable[[Any, int], Any | None]


MOVIE_QUALITIES = QualityTable(by_name=q.BY_NAME, weights=q.WEIGHTS, same_kind_at=q.same_kind_at)
SERIES_QUALITIES = QualityTable(by_name=qs.BY_NAME, weights=qs.WEIGHTS, same_kind_at=qs.same_kind_at)


def family_of(answers: dict[str, Any]) -> str:
    """German when ``de`` is among the languages in any role, otherwise standard."""
    return "german" if any(entry["code"] == "de" for entry in answers["languages"]) else "standard"


# --- Formats ------------------------------------------------------------------------------ #


def format_score(custom_format: dict[str, Any], score_set: str) -> int:
    scores = custom_format.get("trash_scores") if isinstance(custom_format.get("trash_scores"), dict) else {}
    value = scores.get(score_set, scores.get("default", 0))
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0


def flagged(value: Any) -> bool:
    return value is True or value == "true"


def language_specification(code: str) -> dict[str, Any]:
    """One language specification. TRaSH's Sonarr and Radarr formats use the same language numbers."""
    return {
        "name": LANGUAGE_NAMES[code],
        "implementation": "LanguageSpecification",
        "negate": False,
        "required": True,
        "fields": {"value": language_id(code)},
    }


@dataclass(frozen=True)
class AnswerGroups:
    question: str
    answer: str
    #: Groups to add; a group counts where TRaSH's include lists the base profile.
    groups: tuple[str, ...]


class Formats:
    """The formats of one profile, collected by trash id, each with the score of the profile's score set."""

    def __init__(self, snapshot: TrashSnapshot, profile: dict[str, Any]) -> None:
        self.snapshot = snapshot
        self.profile = profile
        self.score_set = str(profile.get("trash_score_set") or "default")
        self.by_id: dict[str, dict[str, Any]] = {}

    def add(self, trash_id: str) -> None:
        if trash_id in self.by_id:
            return
        custom_format = self.snapshot.formats_by_id.get(trash_id)
        if custom_format is None:
            raise ProfileBuildError(f"custom format {trash_id} is missing from the TRaSH state")
        self.by_id[trash_id] = {
            "trash_id": trash_id,
            "name": custom_format["name"],
            "score": format_score(custom_format, self.score_set),
            "specifications": custom_format["specifications"],
        }

    def group(self, name: str) -> dict[str, Any]:
        entry = self.snapshot.group(name)
        if entry is None or not isinstance(entry.get("custom_formats"), list):
            raise ProfileBuildError(f"group {name} is missing from the TRaSH state")
        return entry

    def applies(self, name: str) -> bool:
        include = (self.group(name).get("quality_profiles") or {}).get("include") or {}
        return self.profile.get("name") in include or self.profile.get("trash_id") in include.values()

    def add_group(self, name: str, *, defaults_only: bool = False) -> None:
        for entry in self.group(name)["custom_formats"]:
            if defaults_only and not flagged(entry.get("default")):
                continue
            self.add(str(entry.get("trash_id")))

    def add_answer_groups(
        self, answers: dict[str, Any], rules: tuple[AnswerGroups, ...], without_effect: list[str]
    ) -> None:
        """Every group of an answer that is given, where TRaSH's include lists the profile."""
        for rule in rules:
            if answers.get(rule.question) != rule.answer:
                continue
            used = [name for name in rule.groups if self.applies(name)]
            if not used:
                without_effect.append(rule.question)
            for name in used:
                self.add_group(name)


# --- Qualities ---------------------------------------------------------------------------- #


@dataclass
class Entry:
    label: str
    members: list[str]
    allowed: bool
    group: bool


def quality_entries(profile: dict[str, Any], base: str, table: QualityTable) -> list[Entry]:
    """The profile's quality items turned round, lowest first. Every quality of the table must be listed once."""
    items = profile.get("items")
    if not isinstance(items, list) or not items:
        raise ProfileBuildError(f"profile {base} has no qualities")
    entries: list[Entry] = []
    seen: set[str] = set()
    for item in reversed(items):
        members = item.get("items")
        group = isinstance(members, list)
        names = [str(name) for name in members] if group else [str(item.get("name"))]
        for name in names:
            if name not in table.by_name or name in seen:
                raise ProfileBuildError(f"profile {base} names the quality {name} unknown or twice")
            seen.add(name)
        entries.append(Entry(str(item.get("name")), names, bool(item.get("allowed")), group))
    missing = sorted(set(table.by_name) - seen)
    if missing:
        raise ProfileBuildError(f"profile {base} does not list {', '.join(missing)}")
    return entries


def _singles(names: list[str], allowed: bool, table: QualityTable) -> list[Entry]:
    return [Entry(name, [name], allowed, False) for name in sorted(names, key=lambda name: table.weights[name])]


def _keep_only(entries: list[Entry], keep: Callable[[str], bool], table: QualityTable) -> None:
    """Allowed members that ``keep`` refuses leave their item; out of a group they stand directly below it."""
    index = 0
    while index < len(entries):
        entry = entries[index]
        dropped = [name for name in entry.members if not keep(name)] if entry.allowed else []
        if dropped and not entry.group:
            entry.allowed = False
        elif dropped:
            entry.members = [name for name in entry.members if keep(name)]
            singles = _singles(dropped, False, table)
            if entry.members:
                entries[index:index] = singles
                index += len(singles)
            else:
                entries[index : index + 1] = singles
                index += len(singles)
                continue
        index += 1


def _take_out(entries: list[Entry], name: str) -> None:
    for index, entry in enumerate(entries):
        if name in entry.members:
            entry.members.remove(name)
            if not entry.members:
                del entries[index]
            return


def _allow_where_it_stands(entries: list[Entry], name: str, wanted: set[str]) -> None:
    for index, entry in enumerate(entries):
        if name not in entry.members:
            continue
        if not entry.group or set(entry.members) <= wanted:
            entry.allowed = True
        else:
            entry.members.remove(name)
            entries.insert(index, Entry(name, [name], True, False))
        return


def resolutions_of(entry: Entry, table: QualityTable) -> set[int]:
    return {table.by_name[name].resolution for name in entry.members}


def _below_the_target(entries: list[Entry], target: int, table: QualityTable) -> None:
    """Allowed lower resolutions stand below every allowed item of the target resolution."""
    lowest_target = next(
        (index for index, entry in enumerate(entries) if entry.allowed and target in resolutions_of(entry, table)),
        None,
    )
    if lowest_target is None:
        return
    above = [
        entry
        for entry in entries[lowest_target + 1 :]
        if entry.allowed
        and target not in resolutions_of(entry, table)
        and max(resolutions_of(entry, table)) < target
    ]
    for entry in above:
        entries.remove(entry)
        entries.insert(lowest_target, entry)
        lowest_target += 1


def qualities_of(
    profile: dict[str, Any],
    base: str,
    *,
    target: int,
    take_now: bool,
    merge_lower: bool,
    table: QualityTable,
    keep: Callable[[Any], bool] | None = None,
) -> list[Entry]:
    """The quality list of the rules: the target resolution only, then what ``take_now`` adds below it.

    ``keep`` refuses allowed qualities of the target resolution as well (movies with ``web``); without it every
    quality of the target resolution the profile allows stays. ``merge_lower`` puts what ``take_now`` adds into the
    lowest allowed group instead of leaving it where it stands (German with ``best``).
    """
    entries = quality_entries(profile, base, table)

    def wanted(name: str) -> bool:
        quality = table.by_name[name]
        return quality.resolution == target and (keep is None or keep(quality))

    _keep_only(entries, wanted, table)
    if not any(entry.allowed for entry in entries):
        raise ProfileBuildError(f"profile {base} allows nothing at {target}p")

    if take_now:
        at_target = [name for entry in entries if entry.allowed for name in entry.members]
        lower: list[str] = []
        for resolution in TAKE_NOW_BELOW[target]:
            for name in at_target:
                below = table.same_kind_at(table.by_name[name], resolution)
                if below is not None and below.name not in lower:
                    lower.append(below.name)
        if merge_lower:
            merged = next((entry for entry in reversed(entries) if entry.allowed and entry.group), None)
            if merged is None:
                raise ProfileBuildError(f"profile {base} has no merged group")
            for name in lower:
                _take_out(entries, name)
                merged.members.append(name)
        else:
            for name in lower:
                _allow_where_it_stands(entries, name, set(lower))
            _below_the_target(entries, target, table)
    return entries


def quality_rules(entries: list[Entry]) -> list[dict[str, Any]]:
    """The quality list as the rules store it."""
    return [
        {"group": entry.label, "items": list(entry.members), "allowed": entry.allowed}
        if entry.group
        else {"name": entry.label, "allowed": entry.allowed}
        for entry in entries
    ]


# --- Languages ---------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Languages:
    generated: list[dict[str, Any]]
    all_together: bool
    min_score: int


def language_formats(
    answers: dict[str, Any], family: str, snapshot: TrashSnapshot, found: Formats, profile: dict[str, Any]
) -> Languages:
    """The generated language formats and the minimum score they set."""
    entries = answers["languages"]
    required = [entry["code"] for entry in entries if entry["role"] == "required"]
    all_together = len(required) >= 2 and answers["required_languages"] == "all"
    generated: list[dict[str, Any]] = []
    for entry in entries:
        if entry["code"] == "de" and family == "german":
            continue
        if all_together:
            score = 0
        elif entry["role"] == "required":
            score = REQUIRED_LANGUAGE_SCORE
        else:
            score = PREFERRED_LANGUAGE_SCORE
        generated.append(
            {
                "trash_id": None,
                "name": f"Language: {LANGUAGE_NAMES[entry['code']]}",
                "score": score,
                "specifications": [language_specification(entry["code"])],
            }
        )
    if all_together:
        if family == "german":
            for file_name in GERMAN_LANGUAGE_FILES:
                trash_id = snapshot.format_ids_by_file.get(file_name)
                if trash_id is None:
                    raise ProfileBuildError(f"custom format {file_name} is missing from the TRaSH state")
                if trash_id in found.by_id:
                    found.by_id[trash_id]["score"] = 0
        generated.append(
            {
                "trash_id": None,
                "name": "Languages: " + " + ".join(LANGUAGE_NAMES[code] for code in required),
                "score": ALL_LANGUAGES_SCORE,
                "specifications": [language_specification(code) for code in required],
            }
        )
        minimum = ALL_LANGUAGES_SCORE
    elif required:
        minimum = REQUIRED_LANGUAGE_SCORE
    else:
        minimum = int(profile.get("minFormatScore") or 0)
    return Languages(generated=generated, all_together=all_together, min_score=minimum)


def cutoff_and_upgrade_until(
    answers: dict[str, Any],
    profile: dict[str, Any],
    base: str,
    entries: list[Entry],
    *,
    target: int,
    family: str,
    min_score: int,
    table: QualityTable,
) -> tuple[str, int]:
    """The cutoff item and upgrade-until, as the design notes decided them for ``first`` and ``best``."""
    if answers["good_enough"] == "first":
        cutoff = next(entry.label for entry in entries if entry.allowed and target in resolutions_of(entry, table))
        return cutoff, min_score
    cutoff = str(profile.get("cutoff"))
    if not any(entry.allowed and entry.label == cutoff for entry in entries):
        raise ProfileBuildError(f"the cutoff {cutoff} of profile {base} is not an allowed item")
    own_minimum = GERMAN_OWN_MINIMUM if family == "german" else int(profile.get("minFormatScore") or 0)
    return cutoff, int(profile.get("cutoffFormatScore") or 0) + max(0, min_score - own_minimum)


# --- Sizes -------------------------------------------------------------------------------- #


def size_rules(
    snapshot: TrashSnapshot, size_set: str, entries: list[Entry], max_gb_per_hour: float | None
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """The size limits per allowed quality and the qualities whose minimum lies above the owner's limit."""
    sizes_data = snapshot.sizes(size_set)
    if sizes_data is None or not isinstance(sizes_data.get("qualities"), list):
        raise ProfileBuildError(f"size set {size_set} is missing from the TRaSH state")
    minimums = {
        str(entry.get("quality")): float(entry["min"])
        for entry in sizes_data["qualities"]
        if isinstance(entry, dict) and isinstance(entry.get("min"), int | float)
    }
    maximum = round(max_gb_per_hour * 1024 / 60, 1) if max_gb_per_hour is not None else None
    sizes: dict[str, dict[str, Any]] = {}
    below: list[str] = []
    for entry in entries:
        if not entry.allowed:
            continue
        for name in entry.members:
            minimum = minimums.get(name, 0.0)
            sizes[name] = {
                "min_mb_per_min": int(minimum) if minimum.is_integer() else minimum,
                "max_mb_per_min": maximum,
            }
            if maximum is not None and maximum < minimum:
                below.append(name)
    return sizes, below


def warnings_of(below: list[str], without_effect: list[str]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    if below:
        warnings.append({"code": "size_limit_below_minimum", "qualities": below})
    if without_effect:
        warnings.append({"code": "answer_without_effect", "questions": without_effect})
    return warnings


# --- What the interface shows ------------------------------------------------------------------ #


def _per_hour(mb_per_min: Any) -> float | None:
    """MB per minute as GB per hour, one decimal; None stays None (no limit)."""
    return round(float(mb_per_min) * 60 / 1024, 1) if isinstance(mb_per_min, int | float) else None


def summary(answers: dict[str, Any], rules: dict[str, Any], table: QualityTable) -> dict[str, Any]:
    """What the interface shows of a profile.

    ``preferred``, ``avoided`` and ``formats_total`` hold only formats that can match at least one allowed quality
    (``releases.formats.can_match_allowed``): a format bound to another resolution, source or modifier never
    applies. The rules keep every format, so scores stay equal to Radarr's and Sonarr's.
    """
    applicable = [
        entry
        for entry in rules["formats"]
        if release_formats.can_match_allowed(entry, rules["qualities"], table.by_name)
    ]
    scored = [entry for entry in applicable if entry.get("score")]
    preferred = sorted(
        (entry for entry in scored if entry["score"] > 0), key=lambda entry: (-entry["score"], entry["name"])
    )
    avoided = sorted(
        (entry for entry in scored if entry["score"] < 0), key=lambda entry: (entry["score"], entry["name"])
    )
    return {
        "qualities": [
            {"group": item["group"], "items": list(item["items"])} if "group" in item else item["name"]
            for item in rules["qualities"]
            if item["allowed"]
        ],
        "cutoff": rules["cutoff"],
        "min_score": rules["min_score"],
        "upgrade_until": rules["upgrade_until"],
        "preferred": [{"name": entry["name"], "score": entry["score"]} for entry in preferred[:SUMMARY_FORMATS]],
        "avoided": [{"name": entry["name"], "score": entry["score"]} for entry in avoided[:SUMMARY_FORMATS]],
        "formats_total": len(applicable),
        "sizes": [
            {
                "quality": name,
                "min_gb_per_hour": round(float(limits["min_mb_per_min"]) * 60 / 1024, 1),
                "max_gb_per_hour": _per_hour(limits.get("max_mb_per_min")),
            }
            for name, limits in rules["sizes"].items()
        ],
        # ⚠️ Out of the rules, not out of the answers: a profile set by hand has no answers, and every read of it
        # raised a KeyError before (the owner's finding of 20.09.2026). The assistant writes the same number into
        # every size it takes, so its summary stays as it was.
        "max_gb_per_hour": max(
            (
                _per_hour(limits.get("max_mb_per_min"))
                for limits in rules["sizes"].values()
                if limits.get("max_mb_per_min")
            ),
            default=None,
        ),
        "languages": [dict(entry) for entry in rules["languages"]],
        "required_languages": rules["required_languages"],
        "warnings": [dict(warning) for warning in rules.get("warnings") or []],
    }


#: What the one line shows. Stored answers without one of them have no line.
LINE_ANSWERS = ("resolution", "source", "languages", "hdr", "max_gb_per_hour")


def line(answers: dict[str, Any], outdated: bool) -> dict[str, Any] | None:
    """The one line on the Fassungen tab. ``hdr`` is null where the question is not shown (1080p).

    None for stored answers the line cannot read, for example a profile stored without answers: the tab then shows the
    profile without its line (``has_profile`` stays true).
    """
    if (
        not isinstance(answers, dict)
        or any(key not in answers for key in LINE_ANSWERS)
        or not all(isinstance(answers[key], str) for key in ("resolution", "source"))
        or not isinstance(answers["languages"], list)
        or not all(isinstance(entry, dict) for entry in answers["languages"])
    ):
        return None
    return {
        "resolution": answers["resolution"],
        "source": answers["source"],
        "languages": [dict(entry) for entry in answers["languages"]],
        "hdr": answers["hdr"] if answers["resolution"] == "2160p" else None,
        "max_gb_per_hour": answers["max_gb_per_hour"],
        "outdated": outdated,
    }
