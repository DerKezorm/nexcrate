"""Auto tag rules from Radarr, Sonarr and Lidarr: read there, turned into nexcrate's rules here.

Nothing is stored in this module. It answers each rule of the app in the fields of ``POST /api/auto-tags`` and says
whether it can be taken. The owner's answers (22.09.2026):

- A rule whose name the kind already has is not taken (``exists``); the one there stays as it is.
- A rule with a condition nexcrate cannot rebuild is not taken at all, with the reason. Without the condition it would
  fit more titles than in the app, and with "remove automatically" it would take tags away.
- The one exception is Lidarr's metadata profile: nexcrate has none, the condition is left out and named (``dropped``).

What the apps store was measured on 22.09.2026 (Radarr 6.3.0, Sonarr 4.0.19, Lidarr 3.1.0, a rule with every
condition written and read back): lists of text for genre, keyword and studio; ``min`` and ``max`` for year and
runtime; the path for a root folder; numbers for language, profile, tag, status (field ``status``) and series type.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ..models import AutoTag, Profile
from . import auto_tags, tags

#: The apps' status numbers, per kind (``MovieStatusType``, ``SeriesStatusType``, ``ArtistStatusType``).
_STATUSES: dict[str, dict[int, str]] = {
    "movie": {0: "tba", 1: "announced", 2: "in_cinemas", 3: "released"},
    "series": {0: "continuing", 1: "ended", 2: "upcoming"},
    "album": {0: "continuing", 1: "ended"},
}
_SERIES_TYPES = {0: "standard", 1: "daily", 2: "anime"}

#: The apps' language names as ISO 639-1, the way TMDB gives a title's original language. Left out on purpose:
#: "Portuguese (Brazil)", "Spanish (Latino)" and "Flemish" (TMDB has no original language for them, so such a
#: condition never fit in the app either), and "Unknown".
LANGUAGES: dict[str, str] = {
    "Afrikaans": "af", "Albanian": "sq", "Arabic": "ar", "Bengali": "bn", "Bosnian": "bs", "Bulgarian": "bg",
    "Catalan": "ca", "Chinese": "zh", "Croatian": "hr", "Czech": "cs", "Danish": "da", "Dutch": "nl",
    "English": "en", "Estonian": "et", "Finnish": "fi", "French": "fr", "Georgian": "ka", "German": "de",
    "Greek": "el", "Hebrew": "he", "Hindi": "hi", "Hungarian": "hu", "Icelandic": "is", "Indonesian": "id",
    "Italian": "it", "Japanese": "ja", "Kannada": "kn", "Korean": "ko", "Latvian": "lv", "Lithuanian": "lt",
    "Macedonian": "mk", "Malayalam": "ml", "Marathi": "mr", "Mongolian": "mn", "Norwegian": "no", "Persian": "fa",
    "Polish": "pl", "Portuguese": "pt", "Romanian": "ro", "Romansh": "rm", "Russian": "ru", "Serbian": "sr",
    "Slovak": "sk", "Slovenian": "sl", "Spanish": "es", "Swedish": "sv", "Tagalog": "tl", "Tamil": "ta",
    "Telugu": "te", "Thai": "th", "Turkish": "tr", "Ukrainian": "uk", "Urdu": "ur", "Vietnamese": "vi",
}  # fmt: skip

_LISTS = {"GenreSpecification": "genre", "KeywordSpecification": "keyword", "StudioSpecification": "studio"}
_RANGES = {"YearSpecification": "year", "RuntimeSpecification": "runtime"}


@dataclass
class Candidate:
    """One rule of the app, as nexcrate would store it, and whether it can be taken."""

    name: str
    tags: list[str]
    remove_automatically: bool
    conditions: list[dict[str, Any]]
    exists: bool = False
    #: Why the rule cannot be taken: ``{"code": …, "value": …}``, in the order of its conditions.
    problems: list[dict[str, str]] = field(default_factory=list)
    #: Conditions left out, the rule is taken without them (only Lidarr's metadata profile).
    dropped: list[dict[str, str]] = field(default_factory=list)

    @property
    def importable(self) -> bool:
        return not self.problems and not self.exists


def _fields(spec: dict[str, Any]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for item in spec.get("fields") or []:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            found[item["name"]] = item.get("value")
    return found


def _whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def known_profiles(db: OrmSession, kind: str) -> dict[str, int]:
    """The profiles of a kind by name, case ignored (names are unique per kind)."""
    return {name.casefold(): profile_id for profile_id, name in db.execute(
        select(Profile.id, Profile.name).where(Profile.kind == kind)
    ) if name}  # fmt: skip


def from_arr(
    db: OrmSession,
    kind: str,
    rules: list[Any],
    schema: list[Any],
    tag_names: dict[int, str],
    profile_names: dict[int, str],
) -> list[Candidate]:
    """Every rule of the app as a candidate, in the app's order."""
    languages = _language_names(schema)
    profiles = known_profiles(db, kind)
    folders = set(auto_tags.folders_of(db, kind))
    taken = {
        name.casefold() for (name,) in db.execute(select(AutoTag.name).where(AutoTag.kind == kind)) if name
    }
    found: list[Candidate] = []
    for rule in rules:
        if not isinstance(rule, dict) or not str(rule.get("name") or "").strip():
            continue
        name = str(rule["name"]).strip()[: auto_tags.NAME_MAX]
        labels = [
            label for tag_id in rule.get("tags") or [] if (label := tags.clean_or_none(tag_names.get(tag_id, "")))
        ]
        candidate = Candidate(
            name=name,
            tags=list(dict.fromkeys(labels)),
            remove_automatically=bool(rule.get("removeTagsAutomatically")),
            conditions=[],
            exists=name.casefold() in taken,
        )
        if not candidate.tags:
            candidate.problems.append({"code": "no_tags", "value": ""})
        for spec in rule.get("specifications") or []:
            if isinstance(spec, dict):
                _condition(candidate, kind, spec, languages, profiles, profile_names, folders, tag_names)
        if not candidate.conditions and not candidate.problems:
            candidate.problems.append({"code": "no_conditions", "value": ""})
        found.append(candidate)
    return found


def _language_names(schema: list[Any]) -> dict[int, str]:
    names: dict[int, str] = {}
    for spec in schema:
        if not isinstance(spec, dict) or spec.get("implementation") != "OriginalLanguageSpecification":
            continue
        for item in spec.get("fields") or []:
            options = item.get("selectOptions") if isinstance(item, dict) else None
            for option in options or []:
                if isinstance(option, dict) and _whole(option.get("value")) and isinstance(option.get("name"), str):
                    names[option["value"]] = option["name"]
    return names


def _condition(
    candidate: Candidate,
    kind: str,
    spec: dict[str, Any],
    languages: dict[int, str],
    profiles: dict[str, int],
    profile_names: dict[int, str],
    folders: set[str],
    tag_names: dict[int, str],
) -> None:
    implementation = str(spec.get("implementation") or "")
    values = _fields(spec)
    value = values.get("value")
    one: dict[str, Any] = {"negate": bool(spec.get("negate")), "required": bool(spec.get("required"))}

    def problem(code: str, shown: object = "") -> None:
        candidate.problems.append({"code": code, "value": str(shown)})

    if implementation == "MetadataProfileSpecification":
        candidate.dropped.append({"code": "metadata_profile", "value": str(value or "")})
        return
    if implementation in _LISTS:
        type_ = _LISTS[implementation]
        if type_ not in auto_tags.CONDITIONS[kind]:
            problem("condition_unsupported", type_)
            return
        items = [str(item).strip() for item in value or [] if str(item).strip()] if isinstance(value, list) else []
        if not items:
            problem("condition_empty", type_)
            return
        candidate.conditions.append({**one, "type": type_, "values": items[: auto_tags.VALUES_MAX]})
    elif implementation in _RANGES:
        type_ = _RANGES[implementation]
        low, high = values.get("min"), values.get("max")
        if type_ not in auto_tags.CONDITIONS[kind]:
            problem("condition_unsupported", type_)
        elif not _whole(low) or not _whole(high) or low < 0 or high < low or high > auto_tags.RANGE_MAX:
            problem("range_invalid", f"{low}-{high}")
        else:
            candidate.conditions.append({**one, "type": type_, "min": low, "max": high})
    elif implementation == "RootFolderSpecification":
        path = auto_tags._folder(str(value or ""))
        if not path or path not in folders:
            problem("root_folder_unknown", path)
        else:
            candidate.conditions.append({**one, "type": "root_folder", "value": path})
    elif implementation == "OriginalLanguageSpecification":
        name = languages.get(value) if _whole(value) else None
        code = LANGUAGES.get(name or "")
        if code is None:
            problem("language_unknown", name or value)
        else:
            candidate.conditions.append({**one, "type": "original_language", "value": code})
    elif implementation == "QualityProfileSpecification":
        name = profile_names.get(value) if _whole(value) else None
        profile_id = profiles.get((name or "").casefold()) if name else None
        if profile_id is None:
            problem("profile_unknown", name or value)
        else:
            candidate.conditions.append({**one, "type": "profile", "profile_id": profile_id})
    elif implementation == "StatusSpecification":
        status = _STATUSES[kind].get(values.get("status")) if _whole(values.get("status")) else None
        if status is None:
            problem("status_unknown", values.get("status"))
        else:
            candidate.conditions.append({**one, "type": "status", "value": status})
    elif implementation == "MonitoredSpecification":
        candidate.conditions.append({**one, "type": "monitored"})
    elif implementation == "TagSpecification":
        label = tags.clean_or_none(tag_names.get(value, "")) if _whole(value) else None
        if label is None:
            problem("tag_unknown", value)
        else:
            candidate.conditions.append({**one, "type": "tag", "value": label})
    elif implementation == "SeriesTypeSpecification" and kind == "series":
        series_type = _SERIES_TYPES.get(value) if _whole(value) else None
        if series_type is None:
            problem("series_type_unknown", value)
        else:
            candidate.conditions.append({**one, "type": "series_type", "value": series_type})
    else:
        problem("condition_unknown", implementation or "?")


def names_of_profiles(listed: list[Any]) -> dict[int, str]:
    """A quality profile's number to its name, from the client's parsed records."""
    return {profile.id: profile.name for profile in listed if getattr(profile, "name", None)}

