"""Profiles for movies: the questions, the decision table, and the rules built from the answers.

The approved contract is the design notes ("Questions", "Building the rules" and "Changes after the owner's
test", which wins where they differ); Nexview's wizard is the groundwork for the tables. Same answers and same
TRaSH state give the same rules. A missing table row, profile, group or custom format is a loud
``ProfileBuildError``, never a fallback.

The building blocks
-------------------
* **Family:** German when ``de`` is among the languages (any role), otherwise standard.
* **Base profile:** ``PROFILE_TABLE`` by family, resolution and source. German has no pure WEB profile, so
  ``web`` takes Blu-ray and WEB and the non-WEB qualities are not allowed.
* **Formats:** the base profile's ``formatItems``; the unwanted group of the family (only formats flagged
  default); ``streaming-services-general``; the groups of the detailed answers; at 2160p with ``hdr`` not
  ``any`` the HDR, DV and HDR10+ groups, with ``safety`` also ``hdr-formats-dv-webdl``. A group counts only
  where TRaSH's ``quality_profiles.include`` lists the base profile. Scores come from the profile's score set,
  else ``default``, else 0. TRaSH's German release group and German miscellaneous groups are not used (see
  ``RETIRED_QUESTIONS``).
* **Languages** (Nexview's model): a required language 10,000 and the minimum 10,000, a preferred one 500.
  German keeps TRaSH's own formats and scores in either role; only ``required`` raises the minimum. French,
  Spanish, Italian, Turkish and English get a generated single language format. ``all`` with two or more
  required languages builds one format with every required language as a required specification, 50,000 and
  minimum 50,000, and sets the single language formats and TRaSH's German, German DL and German DL (undefined)
  to 0.
* **Qualities:** every quality in the order TRaSH's ``items`` give, turned round so the list runs from lowest
  to highest, merged groups kept.

Decisions the plan left open, taken here
----------------------------------------
* **TRaSH lists ``items`` highest first** (``Unknown`` last). The rules run lowest first, as the plan's shape
  says, so the list is turned round.
* **``take_now = false`` means wait:** an allowed quality below the target resolution is not allowed any
  more, also where TRaSH allows it (Bluray-720p in "HD Bluray + WEB", the 720p members of the German HD
  merged group). Otherwise a lower release would fit without ``take_now``.
* **``take_now = true``** adds the same source and modifier at the next lower resolution only (2160p: 1080p;
  1080p: 720p) where Radarr's table has them (``TAKE_NOW_BELOW``). German with ``best``: into the merged group.
  Otherwise those items are allowed where they stand, always below the target. A 2160p target never takes 720p,
  decided by the owner on 14.09.2026, although TRaSH's ``german-uhd-bluray-web-alternative`` goes down to 720p.
* **The rules carry ``target_resolution``** (2160 or 1080, from ``resolution``), so the engine can mark a release
  below it (``below_target``), **and ``rules_version``** (``RULES_VERSION``). Stored rules without it count as
  version 1; on start ``profile_upkeep`` rebuilds a profile of the TRaSH state in use whose version is lower.
* **A quality taken out of a group** (a lower resolution, a non-WEB quality for ``web``) becomes a single item
  directly below that group.
* **``best`` keeps TRaSH's cutoff and TRaSH's distance to upgrade-until.** TRaSH's upgrade-until assumes TRaSH's
  own minimum. When nexcrate's language formats lift every acceptable release by a fixed score (10,000 for a
  required language in the standard family, 50,000 for ``all``), upgrade-until is lifted by the same amount;
  the German family's own base is TRaSH's "German only" minimum of 10,000. Without the lift a standard profile
  with a required language would start at its own upgrade-until and never upgrade by score, and ``best``
  would behave like ``first``.
* **``first``:** the cutoff is the lowest allowed item holding the target resolution, upgrade-until the minimum.
* **Sizes:** TRaSH's ``movie`` size set gives the minimums; a quality without an entry has minimum 0 (TRaSH
  lists nothing below 720p). The maximum is ``max_gb_per_hour * 1024 / 60`` MB per minute, one decimal.
* **``audio`` is asked only where it acts:** its ``when`` names the rows of ``PROFILE_TABLE`` whose TRaSH profile
  the ``audio-formats`` group lists (remux, UHD encodes, German WEB at 2160p, which takes TRaSH's UHD Blu-ray and
  WEB profile). A test ties the condition to TRaSH's data. Hidden, the answer goes back to ``any`` and the group
  is not added.
* **Warnings:** ``size_limit_below_minimum {qualities}`` and ``answer_without_effect {questions}`` when TRaSH's
  data gives a shown answer nothing to add for this base profile. With the bundled state no shown answer does;
  the warning stays for a newer state that takes a profile out of a group.
* **The summary** lists and counts only formats that can match at least one allowed quality; the rules keep
  every format, so scores stay equal to Radarr's.
* **``required_languages`` in the rules** is the one that acts: ``all`` only with two or more required
  languages, otherwise ``any``.
* **Generated formats** have ``trash_id`` null and names "Language: French" and "Languages: German + English".
"""
from __future__ import annotations

from typing import Any

from ..releases import qualities as q
from ..trash_data import TrashSnapshot
from . import building
from .building import AnswerGroups
from .questions import Condition, ProfileBuildError, Question, any_of

KIND = "movie"
SCHEMA = 1
LANGUAGE_CODES = building.LANGUAGE_CODES
LANGUAGE_ROLES = building.LANGUAGE_ROLES
RESOLUTIONS = building.RESOLUTIONS
MAX_GB_PER_HOUR = building.MAX_GB_PER_HOUR
#: The version of the rules this builder writes. Stored rules without the field count as 1; a stored profile of the
#: TRaSH state in use with a lower version is rebuilt on start (``services/profile_upkeep.py``).
#: * 2 (14.09.2026, after the owner's test of step 2c): ``take_now`` at 2160p goes down to 1080p only, and the rules
#:   carry ``target_resolution``.
RULES_VERSION = 2
#: The lower resolutions ``take_now`` adds, by target resolution: the next lower one only.
TAKE_NOW_BELOW = building.TAKE_NOW_BELOW
#: Radarr's quality table, which every quality name in these rules comes from.
QUALITIES = building.MOVIE_QUALITIES

#: Ids of questions that were asked once. Stored answers and imported files may still carry them; normalization
#: drops them without an error, whatever their value, and the schema stays 1.
#: * ``regional_groups`` ("prefer German release groups"), retired 13.09.2026 after the owner's test of step 2b.
#:   It changed no score in any answer set: TRaSH's German profiles already carry the German tier formats, and
#:   the answer only added formats that can never apply at the allowed qualities.
RETIRED_QUESTIONS = frozenset({"regional_groups"})

_SHOWN_AT_2160P = (Condition("resolution", is_="2160p"),)
#: Where the TRaSH profile of ``PROFILE_TABLE`` lists the ``audio-formats`` group: remux, UHD encodes, and German
#: WEB at 2160p. English WEB and everything at 1080p except remux get nothing from the group.
_SHOWN_WHERE_AUDIO_ACTS = (
    any_of(
        (Condition("source", is_="remux"),),
        (Condition("resolution", is_="2160p"), Condition("source", is_="encodes")),
        (Condition("resolution", is_="2160p"), Condition("source", is_="web"), Condition("languages", includes="de")),
    ),
)

QUESTIONS: tuple[Question, ...] = (
    Question("mode", "choice", "simple", options=("simple", "detailed")),
    Question("resolution", "choice", "2160p", options=("2160p", "1080p")),
    Question("take_now", "boolean", True),
    Question("source", "choice", "encodes", options=("encodes", "remux", "web")),
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
    Question("special_cuts", "choice", "any", options=("any", "prefer"), detailed=True),
    Question("asian_services", "choice", "any", options=("any", "include"), detailed=True),
)

#: (family, resolution, source) to TRaSH's profile file. Nexview's decision table for movies.
PROFILE_TABLE: dict[tuple[str, str, str], str] = {
    ("standard", "1080p", "encodes"): "hd-bluray-web",
    ("standard", "1080p", "remux"): "remux-web-1080p",
    ("standard", "1080p", "web"): "web-1080p",
    ("standard", "2160p", "encodes"): "uhd-bluray-web",
    ("standard", "2160p", "remux"): "remux-web-2160p",
    ("standard", "2160p", "web"): "web-2160p",
    ("german", "1080p", "encodes"): "german-hd-bluray-web",
    ("german", "1080p", "remux"): "german-hd-remux-web",
    ("german", "1080p", "web"): "german-hd-bluray-web",
    ("german", "2160p", "encodes"): "german-uhd-bluray-web",
    ("german", "2160p", "remux"): "german-uhd-remux-web",
    ("german", "2160p", "web"): "german-uhd-bluray-web",
}
UNWANTED_GROUPS = {"german": "unwanted-formats-german", "standard": "unwanted-formats"}
ALWAYS_GROUPS = ("streaming-services-general",)
HDR_GROUPS = ("hdr-formats-hdr", "hdr-formats-dv-boost", "hdr-formats-hdr10-boost")
#: Dolby Vision without an HDR fallback looks wrong on screens without DV; ``safety`` refuses it.
HDR_SAFETY_GROUP = "hdr-formats-dv-webdl"
SIZE_SET = "movie"

#: Answers of the detailed questions to TRaSH groups, as in Nexview's table.
ANSWER_GROUPS: tuple[AnswerGroups, ...] = (
    AnswerGroups("audio", "prefer", ("audio-formats",)),
    AnswerGroups("accessibility", "avoid", ("optional-accessibility",)),
    AnswerGroups("x265_hd", "avoid", ("optional-golden-rule-hd", "optional-golden-rule-uhd")),
    AnswerGroups("sdr", "avoid", ("hdr-formats-sdr",)),
    AnswerGroups("special_cuts", "prefer", ("optional-movie-versions",)),
    AnswerGroups("asian_services", "include", ("streaming-services-asian",)),
)

REQUIRED_LANGUAGE_SCORE = building.REQUIRED_LANGUAGE_SCORE
PREFERRED_LANGUAGE_SCORE = building.PREFERRED_LANGUAGE_SCORE
ALL_LANGUAGES_SCORE = building.ALL_LANGUAGES_SCORE
GERMAN_LANGUAGE_FILES = building.GERMAN_LANGUAGE_FILES
LANGUAGE_NAMES = building.LANGUAGE_NAMES
GERMAN_OWN_MINIMUM = building.GERMAN_OWN_MINIMUM
SUMMARY_FORMATS = building.SUMMARY_FORMATS


def family_of(answers: dict[str, Any]) -> str:
    return building.family_of(answers)


# --- Building --------------------------------------------------------------------------------- #


def build(answers: dict[str, Any], snapshot: TrashSnapshot) -> dict[str, Any]:
    """The rules for normalized answers. Raises ``ProfileBuildError``."""
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
    web_only = answers["source"] == "web"
    items = building.qualities_of(
        profile,
        base,
        target=target,
        take_now=bool(answers["take_now"]),
        merge_lower=family == "german" and answers["good_enough"] == "best",
        table=QUALITIES,
        keep=(lambda quality: quality.source in (q.WEBDL, q.WEBRIP)) if web_only else None,
    )
    cutoff, upgrade_until = building.cutoff_and_upgrade_until(
        answers, profile, base, items, target=target, family=family, min_score=min_score, table=QUALITIES
    )
    sizes, below = building.size_rules(snapshot, SIZE_SET, items, answers["max_gb_per_hour"])

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
        "formats": [*found.by_id.values(), *languages.generated],
        "sizes": sizes,
        "languages": [dict(entry) for entry in answers["languages"]],
        "required_languages": "all" if languages.all_together else "any",
        "warnings": building.warnings_of(below, without_effect),
    }


# --- What the interface shows ------------------------------------------------------------------ #


def summary(answers: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
    """What the interface shows of a profile (``building.summary`` with Radarr's quality table)."""
    return building.summary(answers, rules, QUALITIES)


#: What the one line shows. Stored answers without one of them have no line.
LINE_ANSWERS = building.LINE_ANSWERS


def line(answers: dict[str, Any], outdated: bool) -> dict[str, Any] | None:
    """The one line on the Fassungen tab. ``hdr`` is null where the question is not shown (1080p)."""
    return building.line(answers, outdated)
