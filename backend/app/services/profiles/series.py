"""Profiles for series: the questions, the decision table, and the rules built from the answers.

The approved contract is the design notes, part S2.2 with decisions 5 to 11. The building blocks are the
ones movies use (``building.py``); only what TRaSH's Sonarr data does differently lives here.

What differs from movies
------------------------
* **No ``special_cuts``.** TRaSH's "Series Versions" group (Hybrid, Remaster) is in the base profile only, so the
  answer could add nothing (decision 6).
* **Resolution** starts at 1080p and **source** offers WEB and Remux only. TRaSH has no series profile of Blu-ray
  encodes and WEB for anything but German, where "HD Bluray + WEB" *is* the WEB profile: German with ``web``
  keeps Blu-ray encodes allowed, as TRaSH means it (decision 7, the owner's answer of 16.09.2026). Movies have
  their own ``encodes`` answer and therefore forbid them under ``web``; series have no such answer.
* **Streaming services** are not a question: General, UK, Miscellaneous and Dutch (+75 each) and the HD/UHD
  Streaming Boost are in every profile that TRaSH's ``include`` names, Asian stays the detailed question, because
  its scores run from 10 to 75. French carries no scores at all in TRaSH's Sonarr data (decision 8).
* **Season packs** are fixed: the group ``optional-season-packs`` (Season Pack +10) is in every series profile,
  and TRaSH lists all eight profiles of the table in its ``include``. "Multi-Episode" and "Single Episode" carry
  no scores and are left out (decision 9).
* **``audio``** is shown where TRaSH's ``audio-formats`` group acts: Remux + WEB, and not in the German profiles,
  which the group does not list. That is the ``excludes`` condition of decision 11.
* **Sizes** come from TRaSH's ``series`` set and are measured against the runtime of the episodes a release
  covers, not a movie's runtime; that is the engine's part (decision 23).

Anime (A4)
------------------------------
Every series profile carries a second set of rules under ``anime``, which judges the version's anime series (the
owner's answer of 22.09.2026: every version can do anime, nothing to reorder). It is built from TRaSH's anime profile
of the family: ``[Anime] Remux-1080p``, or ``[German] Anime HD Bluray + WEB`` when German is among the languages.
TRaSH has anime at 1080p only, so the anime rules aim at 1080p whatever the resolution answer says; ``take_now``,
``good_enough``, the size limit and the detailed answers where TRaSH's groups list the anime profile count as for the
series. The languages do not: TRaSH's anime profiles ask for no language and score them with their own formats
(Dual Audio, Dubs Only, German DL, German Subbed), and the minimum is the profile's own (100 for ``[Anime]``). Weekly
releases without a source (``[Group] Show - 05 (1080p)``) are HDTV-1080p, which TRaSH's anime profile groups with WEB.
The answer ``anime`` switches it: ``trash`` (the default) or ``same``, the series rules for anime too.
``rules_for`` picks the rules for a series by its type.

What is the same
----------------
Family (German when ``de`` is among the languages), the formats of the base profile, the unwanted group of the
family, the HDR groups at 2160p, the language formats and their minimum, ``take_now``, ``good_enough``, the
cutoff and upgrade-until, the size limit per hour, the summary and the one line. All of it in ``building.py``.
"""

from __future__ import annotations

from typing import Any

from ..trash_data import TrashSnapshot
from . import building
from .building import AnswerGroups
from .questions import Condition, ProfileBuildError, Question

KIND = "series"
SCHEMA = 1
LANGUAGE_CODES = building.LANGUAGE_CODES
LANGUAGE_ROLES = building.LANGUAGE_ROLES
RESOLUTIONS = building.RESOLUTIONS
MAX_GB_PER_HOUR = building.MAX_GB_PER_HOUR
#: The version of the rules this builder writes; 1 is the first one (16.09.2026), 2 carries ``anime`` (22.09.2026).
RULES_VERSION = 2
#: Sonarr's quality table, which every quality name in these rules comes from.
QUALITIES = building.SERIES_QUALITIES
#: No question has been retired yet.
RETIRED_QUESTIONS: frozenset[str] = frozenset()

_SHOWN_AT_2160P = (Condition("resolution", is_="2160p"),)
#: Where TRaSH's ``audio-formats`` group acts in the series profiles of ``PROFILE_TABLE``: Remux + WEB at both
#: resolutions. The German profiles are not in the group's ``include``, so German gets nothing from it.
_SHOWN_WHERE_AUDIO_ACTS = (Condition("source", is_="remux"), Condition("languages", excludes="de"))

QUESTIONS: tuple[Question, ...] = (
    Question("mode", "choice", "simple", options=("simple", "detailed")),
    Question("resolution", "choice", "1080p", options=("1080p", "2160p")),
    Question("take_now", "boolean", True),
    Question("source", "choice", "web", options=("web", "remux")),
    Question(
        "languages", "languages", [{"code": "de", "role": "required"}], codes=LANGUAGE_CODES, roles=LANGUAGE_ROLES
    ),
    Question(
        "required_languages",
        "choice",
        "all",
        options=("all", "any"),
        when=(Condition("languages", required_at_least=2),),
    ),
    Question("hdr", "choice", "safety", options=("safety", "free", "any"), when=_SHOWN_AT_2160P),
    Question("good_enough", "choice", "best", options=("best", "first")),
    Question("max_gb_per_hour", "number", None, min=MAX_GB_PER_HOUR[0], max=MAX_GB_PER_HOUR[1], nullable=True),
    Question("audio", "choice", "any", options=("any", "prefer"), detailed=True, when=_SHOWN_WHERE_AUDIO_ACTS),
    Question("accessibility", "choice", "any", options=("any", "avoid"), detailed=True),
    Question("x265_hd", "choice", "any", options=("any", "avoid"), detailed=True),
    Question("sdr", "choice", "any", options=("any", "avoid"), detailed=True, when=_SHOWN_AT_2160P),
    Question("asian_services", "choice", "any", options=("any", "include"), detailed=True),
    Question("anime", "choice", "trash", options=("trash", "same")),
)

#: (family, resolution, source) to TRaSH's Sonarr profile file (decision 7).
PROFILE_TABLE: dict[tuple[str, str, str], str] = {
    ("standard", "1080p", "web"): "web-1080p",
    ("standard", "1080p", "remux"): "remux-web-1080p",
    ("standard", "2160p", "web"): "web-2160p",
    ("standard", "2160p", "remux"): "remux-web-2160p",
    ("german", "1080p", "web"): "german-hd-bluray-web",
    ("german", "1080p", "remux"): "german-hd-remux-web",
    ("german", "2160p", "web"): "german-uhd-bluray-web",
    ("german", "2160p", "remux"): "german-uhd-remux-web",
}
UNWANTED_GROUPS = {"german": "unwanted-formats-german", "standard": "unwanted-formats"}
#: Groups every series profile gets where TRaSH's include names it (decisions 8 and 9). The boost adds only the
#: formats TRaSH marks as its default.
ALWAYS_GROUPS = (
    "streaming-services-general",
    "streaming-services-uk",
    "streaming-services-miscellaneous",
    "streaming-services-dutch",
    "optional-season-packs",
)
DEFAULTS_ONLY_GROUPS = ("streaming-services-hd-uhd-boost",)
HDR_GROUPS = ("hdr-formats-hdr", "hdr-formats-dv-boost", "hdr-formats-hdr10-boost")
#: Dolby Vision without an HDR fallback looks wrong on screens without DV; ``safety`` refuses it.
HDR_SAFETY_GROUP = "hdr-formats-dv-webdl"
SIZE_SET = "series"

#: TRaSH's anime profile per family (A4). TRaSH has anime at 1080p only.
ANIME_PROFILES = {"standard": "anime-remux-1080p", "german": "german-anime-hd-bluray-web"}
ANIME_RESOLUTION = "1080p"
ANIME_SIZE_SET = "anime"
#: As ``ALWAYS_GROUPS``, with the anime streaming services; each only where TRaSH's include names the anime profile.
ANIME_ALWAYS_GROUPS = (*ALWAYS_GROUPS, "streaming-services-anime")

#: Answers of the detailed questions to TRaSH groups.
ANSWER_GROUPS: tuple[AnswerGroups, ...] = (
    AnswerGroups("audio", "prefer", ("audio-formats",)),
    AnswerGroups("accessibility", "avoid", ("optional-accessibility",)),
    AnswerGroups("x265_hd", "avoid", ("optional-golden-rule-hd", "optional-golden-rule-uhd")),
    AnswerGroups("sdr", "avoid", ("hdr-formats-sdr",)),
    AnswerGroups("asian_services", "include", ("streaming-services-asian",)),
)


def family_of(answers: dict[str, Any]) -> str:
    return building.family_of(answers)


# --- Building --------------------------------------------------------------------------------- #


def build(answers: dict[str, Any], snapshot: TrashSnapshot) -> dict[str, Any]:
    """The rules for normalized answers, against TRaSH's Sonarr state. Raises ``ProfileBuildError``."""
    family = family_of(answers)
    resolution = answers["resolution"]
    key = (family, resolution, answers["source"])
    base = PROFILE_TABLE.get(key)
    if base is None:
        raise ProfileBuildError(f"no TRaSH profile for {key}")
    profile = snapshot.profile(base)
    if profile is None:
        raise ProfileBuildError(f"profile {base} is missing from the TRaSH state")

    found = building.Formats(snapshot, profile)
    format_items = profile.get("formatItems") if isinstance(profile.get("formatItems"), dict) else {}
    for trash_id in format_items.values():
        found.add(str(trash_id))
    unwanted = UNWANTED_GROUPS[family]
    if found.applies(unwanted):
        found.add_group(unwanted, defaults_only=True)
    for name in ALWAYS_GROUPS:
        if found.applies(name):
            found.add_group(name)
    for name in DEFAULTS_ONLY_GROUPS:
        if found.applies(name):
            found.add_group(name, defaults_only=True)

    without_effect: list[str] = []
    found.add_answer_groups(answers, ANSWER_GROUPS, without_effect)
    if resolution == "2160p" and answers["hdr"] != "any":
        hdr_groups = [*HDR_GROUPS, *((HDR_SAFETY_GROUP,) if answers["hdr"] == "safety" else ())]
        used = [name for name in hdr_groups if found.applies(name)]
        if not used:
            without_effect.append("hdr")
        for name in used:
            found.add_group(name)

    languages = building.language_formats(answers, family, snapshot, found, profile)
    min_score = languages.min_score

    target = RESOLUTIONS[resolution]
    items = building.qualities_of(
        profile,
        base,
        target=target,
        take_now=bool(answers["take_now"]),
        merge_lower=family == "german" and answers["good_enough"] == "best",
        table=QUALITIES,
    )
    cutoff, upgrade_until = building.cutoff_and_upgrade_until(
        answers, profile, base, items, target=target, family=family, min_score=min_score, table=QUALITIES
    )
    sizes, below = building.size_rules(snapshot, SIZE_SET, items, answers["max_gb_per_hour"])

    rules: dict[str, Any] = {
        "kind": KIND,
        "rules_version": RULES_VERSION,
        "trash_commit": snapshot.commit,
        "trash_profile": base,
        "target_resolution": target,
        "qualities": building.quality_rules(items),
        "cutoff": cutoff,
        "upgrades_allowed": bool(profile.get("upgradeAllowed", True)),
        "min_score": min_score,
        "upgrade_until": upgrade_until,
        "min_upgrade_step": int(profile.get("minUpgradeFormatScore") or 1),
        "formats": [*found.by_id.values(), *languages.generated],
        "sizes": sizes,
        "languages": [dict(entry) for entry in answers["languages"]],
        "required_languages": "all" if languages.all_together else "any",
        "warnings": building.warnings_of(below, without_effect),
    }
    if answers.get("anime", "trash") == "trash":
        rules["anime"] = build_anime(answers, snapshot)
    return rules


def build_anime(answers: dict[str, Any], snapshot: TrashSnapshot, family: str | None = None) -> dict[str, Any]:
    """The rules of the version's anime series from TRaSH's anime profile of the family (see the module).

    ``family`` picks the profile by hand (the expert mode's button, the design notes, B2b); left out, the
    languages of the answers decide, as for the assistant.
    """
    family = family or family_of(answers)
    if family not in ANIME_PROFILES:
        raise ProfileBuildError(f"no TRaSH anime profile for {family}")
    base = ANIME_PROFILES[family]
    profile = snapshot.profile(base)
    if profile is None:
        raise ProfileBuildError(f"profile {base} is missing from the TRaSH state")

    found = building.Formats(snapshot, profile)
    format_items = profile.get("formatItems") if isinstance(profile.get("formatItems"), dict) else {}
    for trash_id in format_items.values():
        found.add(str(trash_id))
    unwanted = UNWANTED_GROUPS[family]
    if found.applies(unwanted):
        found.add_group(unwanted, defaults_only=True)
    for name in ANIME_ALWAYS_GROUPS:
        if found.applies(name):
            found.add_group(name)
    for name in DEFAULTS_ONLY_GROUPS:
        if found.applies(name):
            found.add_group(name, defaults_only=True)
    without_effect: list[str] = []
    found.add_answer_groups(answers, ANSWER_GROUPS, without_effect)

    # TRaSH's own minimum: its anime profiles set no language, the formats score them.
    min_score = int(profile.get("minFormatScore") or 0)
    target = RESOLUTIONS[ANIME_RESOLUTION]
    items = building.qualities_of(
        profile,
        base,
        target=target,
        take_now=bool(answers["take_now"]),
        merge_lower=family == "german" and answers["good_enough"] == "best",
        table=QUALITIES,
    )
    cutoff, upgrade_until = building.cutoff_and_upgrade_until(
        answers, profile, base, items, target=target, family="anime", min_score=min_score, table=QUALITIES
    )
    sizes, below = building.size_rules(snapshot, ANIME_SIZE_SET, items, answers["max_gb_per_hour"])
    return {
        "kind": KIND,
        "rules_version": RULES_VERSION,
        "trash_commit": snapshot.commit,
        "trash_profile": base,
        "target_resolution": target,
        "qualities": building.quality_rules(items),
        "cutoff": cutoff,
        "upgrades_allowed": bool(profile.get("upgradeAllowed", True)),
        "min_score": min_score,
        "upgrade_until": upgrade_until,
        "min_upgrade_step": int(profile.get("minUpgradeFormatScore") or 1),
        "formats": list(found.by_id.values()),
        "sizes": sizes,
        "languages": [],
        "required_languages": "any",
        "warnings": building.warnings_of(below, without_effect),
    }


def rules_for(rules: dict[str, Any] | None, series_type: str | None) -> dict[str, Any] | None:
    """The rules a series is judged by: the anime rules for an anime series where the profile has them. Pure."""
    if rules is not None and series_type == "anime" and isinstance(rules.get("anime"), dict):
        return rules["anime"]
    return rules


# --- What the interface shows ------------------------------------------------------------------ #


def summary(answers: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
    """What the interface shows of a profile (``building.summary`` with Sonarr's quality table)."""
    return building.summary(answers, rules, QUALITIES)


#: What the one line shows. Stored answers without one of them have no line.
LINE_ANSWERS = building.LINE_ANSWERS


def line(answers: dict[str, Any], outdated: bool) -> dict[str, Any] | None:
    """The one line on the Fassungen tab. ``hdr`` is null where the question is not shown (1080p)."""
    return building.line(answers, outdated)
