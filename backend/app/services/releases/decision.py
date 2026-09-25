"""One release for one version: does it fit, and would it be an upgrade over the current file.

Knows nothing about the database. ``evaluate(rules, name, context)`` takes the rules a profile was built into
("The rules shape") and answers with codes and values, never sentences.

**Fit** (``accepted``), every failing check listed:

1. quality allowed (``unknown_quality`` when the name has none, else ``quality_not_allowed``)
2. score at least the minimum (``score_below_minimum``)
3. size within the limits of that quality (``too_small``, ``too_large``): MB per minute times the runtime, an
   unknown runtime counts as 110 minutes, an unknown size is not checked. MB are binary, as in Radarr.
4. required languages present (``language_missing``): all of them, or one with ``required_languages = any``
5. no hardcoded subtitles (``hardcoded_subs``)

**Below the target** (``below_target``): the release's resolution is known and lower than the rules'
``target_resolution``. Computed whether the release fits or not; rules without a target (built before 14.09.2026)
give false. It changes nothing else: a fitting release below the target fits for now, as ``take_now`` allows.

**Upgrade**, only with a current file, in Radarr's order without revisions (TRaSH advises to let repack
formats score them instead):

1. a better quality item while the current file is below the cutoff: upgrade
2. a worse quality item: ``worse_quality``
3. upgrades not allowed: ``upgrades_disabled``
4. a better quality item, but the cutoff is met: ``cutoff_met``
5. a score not higher than the current file's: ``score_not_higher``
6. the current file at upgrade-until or above: ``upgrade_until_reached``
7. a gain below the minimum step: ``step_too_small``
8. otherwise: upgrade

Before all of that, a release with the very name the current file came from is no upgrade (``same_release``): the file
is that release, whatever its stored quality or languages say. Sonarr and Radarr refuse such a release as already
imported; without it a file whose media data reads lower than its name would be loaded again and again.

Qualities in one group share a place, so in a merged group only the score decides. With upgrades switched off
the cutoff is the lowest allowed item, as in Radarr. The current file is scored by its name, with the quality
Radarr stored for it and, when stored, its languages: Radarr scores a file by the languages it keeps with it, never by
the name again (measured against Radarr 6.3 on 17.09.2026). A MULTi name read again would lose the indexer's MULTi
languages, and every other MULTi release would look better.

⚠️ A quality the rules do not allow, or do not list, whose Radarr default weight lies above that of the best allowed
quality ranks above every allowed item (``_rank``), only where a current file and a release are compared. TRaSH's
profiles put their allowed group on top of better qualities they do not allow; by that order a 2160p file taken over
into a Full-HD version, or a Remux-2160p file in a 4K version for encodes, would lie below the cutoff and a lesser
release would replace it. The owner's rule: a file better than the profile's target stays, a worse one is upgraded.

**Cutoff not met** (``cutoff_not_met``), whether the rules would still upgrade a stored file. Decision 20 of 15.09.2026:
upgrades count the score too, as in Radarr. True when

1. its quality ranks below the cutoff, or its resolution lies below ``target_resolution``, at any score: ``quality``
2. upgrades are allowed, its quality ranks exactly at the cutoff (not below, not above) and its score lies below
   ``upgrade_until``: ``score``

The file is scored exactly as ``evaluate`` scores a current file: by its name, with its stored quality and the movie's
original language. A file whose quality ranks above the cutoff (``_rank``) is never upgraded for its score, the owner's
rule from finding 9. With upgrades switched off the cutoff is the lowest allowed item and the score never counts. Rules
of another kind give false. In TRaSH's German profiles every 1080p quality is one merged group that is also the cutoff,
so without the score any 1080p file would count as done.

``judge_file`` gives the same verdict with its reason, the file's score, ``upgrade_until`` (None while upgrades are
switched off) and, while the file can still be upgraded and ranks at or below the cutoff with upgrades allowed, the
cutoff item as its target: the label, and for a group the members that reach ``target_resolution`` (every member when
none does or the rules have no target). ``cutoff_not_met`` scores a file only where the score can decide.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath
from typing import Any

from . import formats, parser
from . import languages as lang
from . import qualities as q

#: Radarr assumes this runtime when a movie has none.
UNKNOWN_RUNTIME_MINUTES = 110
MIB = 1024 * 1024


@dataclass(frozen=True)
class CurrentFile:
    name: str | None = None
    #: Radarr's (or later nexcrate's) decision about the file, as a quality name.
    quality: str | None = None
    size_bytes: int | None = None
    #: The languages stored with the file, by name ("German"): the formats see them instead of the name's. Empty: the
    #: name's languages, as before.
    languages: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReleaseContext:
    #: ISO 639-1 of the movie's original language.
    original_language: str | None = None
    runtime_min: int | None = None
    size_bytes: int | None = None
    indexer_flags: int = 0
    current_file: CurrentFile | None = None
    #: ISO 639-1 codes the indexer's MULTi releases carry (step 2c). A name with the MULTi token gets them before the
    #: formats are checked; empty changes nothing.
    multi_languages: tuple[str, ...] = ()


@dataclass(frozen=True)
class Parsed:
    movie: parser.ParsedMovie
    #: Resolved against the movie's original language.
    languages: tuple[int, ...]
    original_language: int

    def as_dict(self) -> dict[str, Any]:
        quality = self.movie.quality
        return {
            "title": self.movie.title,
            "year": self.movie.year,
            "group": self.movie.group,
            "source": q.SOURCES[quality.source],
            "resolution": quality.resolution,
            "modifier": q.MODIFIERS[quality.modifier],
            "quality": quality.name,
            "revision": {
                "version": self.movie.revision.version,
                "real": self.movie.revision.real,
                "repack": self.movie.revision.repack,
            },
            "edition": self.movie.edition,
            "languages": [lang.name(language) for language in self.languages],
            "hardcoded_subs": self.movie.hardcoded_subs,
        }

    def format_input(self, context: ReleaseContext, quality: q.Quality | None = None) -> formats.FormatInput:
        used = quality or self.movie.quality
        return formats.FormatInput(
            release_title=self.movie.release_title,
            group=self.movie.group,
            edition=self.movie.edition,
            source=used.source,
            resolution=used.resolution,
            modifier=used.modifier,
            languages=self.languages,
            original_language=self.original_language,
            indexer_flags=context.indexer_flags,
            size_bytes=context.size_bytes,
            year=self.movie.year,
        )


def parse(name: str, original_language: str | None = None, multi_languages: tuple[str, ...] = ()) -> Parsed:
    """The name read for a movie, its languages resolved.

    ``multi_languages`` follows Radarr's order: a name with the MULTi token gets the indexer's MULTi languages added
    to the languages found in it (a name without a language of its own gets exactly them), and only then does the
    movie's original language fill in what is still unknown. Empty changes nothing.
    """
    movie = parser.parse_movie(name)
    found = movie.languages
    if multi_languages and parser.has_multi_token(movie.release_title):
        found = parser.with_multi_languages(found, tuple(lang.radarr_id(code) for code in multi_languages))
    return Parsed(
        movie=movie,
        languages=parser.aggregate_languages(found, original_language),
        original_language=lang.radarr_id(original_language),
    )


def quality_positions(rules: dict[str, Any]) -> dict[str, int]:
    """Quality name to its place in the rules' order, lowest first. Members of a group share one place."""
    return dict(_order(rules).position)


def highest_position(rules: dict[str, Any]) -> int:
    """The place of the best quality the rules allow; -1 when they allow none. For the delay rule: a release of
    that quality need not wait for a better one."""
    return _order(rules).highest_allowed


# --- The rules ------------------------------------------------------------------------------ #


def _item_names(item: dict[str, Any]) -> list[str]:
    return list(item.get("items") or []) if "group" in item else [str(item.get("name"))]


def _item_label(item: dict[str, Any]) -> str:
    return str(item.get("group") if "group" in item else item.get("name"))


@dataclass(frozen=True)
class _Order:
    position: dict[str, int]
    allowed: frozenset[str]
    cutoff: int
    lowest_allowed: int
    highest_allowed: int = -1
    #: The default weight of the best allowed quality in ``weights``; 0 without one.
    top_weight: int = 0
    #: The default order of the kind: Radarr's for movies, Sonarr's for series.
    weights: dict[str, int] = field(default_factory=lambda: q.WEIGHTS)


def _order(rules: dict[str, Any], weights: dict[str, int] | None = None) -> _Order:
    position: dict[str, int] = {}
    allowed: set[str] = set()
    cutoff = -1
    lowest_allowed = -1
    highest_allowed = -1
    for index, item in enumerate(rules.get("qualities") or []):
        for name in _item_names(item):
            position[name] = index
            if item.get("allowed"):
                allowed.add(name)
        if item.get("allowed"):
            highest_allowed = index
            if lowest_allowed < 0:
                lowest_allowed = index
        if _item_label(item) == rules.get("cutoff"):
            cutoff = index
    weights = q.WEIGHTS if weights is None else weights
    top_weight = max((weights.get(name, 0) for name in allowed), default=0)
    return _Order(
        position=position,
        allowed=frozenset(allowed),
        cutoff=cutoff,
        lowest_allowed=lowest_allowed,
        highest_allowed=highest_allowed,
        top_weight=top_weight,
        weights=weights,
    )


def _rank(order: _Order, name: str) -> int:
    """A quality's place when a current file and a release are compared.

    Its place in the rules, except for a quality the rules do not allow, or do not list, that Radarr's default order
    puts above the best allowed quality: that one ranks above every allowed item, and among its kind by Radarr's default
    order. So a file better than the profile's target never looks worse than an allowed quality: Remux-2160p stays above
    the Bluray-2160p of an encodes profile, 2160p above a Full-HD profile.
    """
    weight = order.weights.get(name, 0)
    if name not in order.allowed and 0 < order.top_weight < weight:
        return order.highest_allowed + 1 + weight
    return order.position.get(name, -1)


def _size_rejection(rules: dict[str, Any], quality: str, context: ReleaseContext) -> dict[str, Any] | None:
    size = context.size_bytes
    limits = (rules.get("sizes") or {}).get(quality)
    if not size or size <= 0 or not isinstance(limits, dict):
        return None
    runtime = context.runtime_min if context.runtime_min and context.runtime_min > 0 else UNKNOWN_RUNTIME_MINUTES
    low, high = limits.get("min_mb_per_min"), limits.get("max_mb_per_min")
    if isinstance(low, int | float) and low > 0:
        minimum = int(low * MIB) * runtime
        if size < minimum:
            return {"code": "too_small", "size_bytes": size, "minimum_bytes": minimum}
    if isinstance(high, int | float) and high > 0:
        maximum = int(high * MIB) * runtime
        if size > maximum:
            return {"code": "too_large", "size_bytes": size, "maximum_bytes": maximum}
    return None


def preferred_bytes(rules: dict[str, Any], quality: str, runtime_min: int | None) -> int | None:
    """What a release of this quality should weigh by the rules' preferred size, for the order of the releases.

    None without a preferred size, and without a known runtime: Radarr falls back to the size itself then, and the
    110 minutes the size limits assume would only invent a distance.
    """
    limits = (rules.get("sizes") or {}).get(quality)
    preferred = limits.get("preferred_mb_per_min") if isinstance(limits, dict) else None
    if not isinstance(preferred, int | float) or preferred <= 0 or not runtime_min or runtime_min <= 0:
        return None
    return int(preferred * MIB) * runtime_min


def _missing_languages(rules: dict[str, Any], present: tuple[int, ...], original: int = lang.UNKNOWN) -> list[str]:
    """The required codes the release lacks. ``original`` is the title's original language, which the code
    ``original`` stands for; while that is unknown, nothing can be missing there."""
    required = [entry["code"] for entry in rules.get("languages") or [] if entry.get("role") == "required"]
    if not required:
        return []

    def has(code: str) -> bool:
        if code == "original":
            return original == lang.UNKNOWN or original in present
        return lang.radarr_id(code) in present

    missing = [code for code in required if not has(code)]
    if rules.get("required_languages") == "all":
        return missing
    return required if len(missing) == len(required) else []


def _below_target(rules: dict[str, Any], quality: q.Quality) -> bool:
    """The resolution is known (above 0) and lower than the rules' target. Rules without a target give false."""
    target = rules.get("target_resolution")
    if not isinstance(target, int) or isinstance(target, bool):
        return False
    return 0 < quality.resolution < target


def _current_quality(current: CurrentFile, parsed_current: Parsed) -> q.Quality:
    if current.quality and current.quality in q.BY_NAME:
        return q.BY_NAME[current.quality]
    return parsed_current.movie.quality


def _read_current(current: CurrentFile, original_language: str | None) -> tuple[Parsed, q.Quality]:
    """A current file read as the upgrade check reads it: by the last part of its name, with its stored quality and
    its stored languages."""
    file_name = PurePosixPath((current.name or "").replace("\\", "/")).name
    parsed = parse(file_name, original_language)
    stored = lang.ids_of_names(current.languages, original_language)
    if stored:
        parsed = replace(parsed, languages=stored)
    return parsed, _current_quality(current, parsed)


def _current_score(
    rules: dict[str, Any], current: CurrentFile, parsed: Parsed, quality: q.Quality, context: ReleaseContext
) -> int:
    """A current file's score as the upgrade check scores it: its own size, no indexer flags, its quality."""
    current_context = ReleaseContext(
        original_language=context.original_language, runtime_min=context.runtime_min, size_bytes=current.size_bytes
    )
    total, _matched = formats.score(
        rules.get("formats") or [], parsed.format_input(current_context, quality), str(rules.get("trash_commit") or "")
    )
    return total


@dataclass(frozen=True)
class FileJudgement:
    """What the rules make of a stored file (``judge_file``)."""

    #: ``quality`` or ``score`` while the rules would still upgrade the file, else None.
    reason: str | None
    #: The file's score, as the upgrade check scores a current file.
    score: int
    #: The rules' upgrade-until score; None while upgrades are switched off.
    upgrade_until: int | None
    #: The label of the cutoff item while the file can still be upgraded and ranks at or below it with upgrades allowed.
    target: str | None = None
    #: The members of ``target`` when it is a group that reach the target resolution, every member when none does.
    target_items: tuple[str, ...] = ()


def _file_reason(rules: dict[str, Any], order: _Order, quality: q.Quality, score: Callable[[], int]) -> str | None:
    """``quality``, ``score`` or None, as "Cutoff not met" above says. ``score`` is called only where it can decide."""
    upgrades_allowed = bool(rules.get("upgrades_allowed"))
    cutoff = order.cutoff if upgrades_allowed else order.lowest_allowed
    rank = _rank(order, quality.name)
    if rank < cutoff or _below_target(rules, quality):
        return "quality"
    # Exactly at the cutoff: a quality above it stays whatever its score (finding 9).
    if upgrades_allowed and cutoff >= 0 and rank == cutoff and score() < int(rules.get("upgrade_until") or 0):
        return "score"
    return None


def _target(rules: dict[str, Any], order: _Order) -> tuple[str, tuple[str, ...]]:
    """The cutoff item's label and, for a group, its members reaching the target resolution (all when none does)."""
    item = (rules.get("qualities") or [])[order.cutoff]
    if "group" not in item:
        return _item_label(item), ()
    members = [str(name) for name in _item_names(item)]
    target = rules.get("target_resolution")
    reaching: list[str] = []
    if isinstance(target, int) and not isinstance(target, bool) and target > 0:
        reaching = [name for name in members if name in q.BY_NAME and q.BY_NAME[name].resolution >= target]
    return _item_label(item), tuple(reaching or members)


def cutoff_not_met(rules: dict[str, Any], current: CurrentFile, original_language: str | None = None) -> bool:
    """Whether these rules would still upgrade a file, judged as ``evaluate`` judges a current file.

    By its quality (the stored name when known, else read from its name) or, at the cutoff quality with upgrades
    allowed, by its score; see "Cutoff not met" above. The file is scored only where the score can decide. Rules of
    another kind give false.
    """
    if rules.get("kind") != "movie":
        return False
    parsed, quality = _read_current(current, original_language)
    context = ReleaseContext(original_language=original_language)
    return (
        _file_reason(rules, _order(rules), quality, lambda: _current_score(rules, current, parsed, quality, context))
        is not None
    )


def judge_file(
    rules: dict[str, Any], current: CurrentFile, original_language: str | None = None
) -> FileJudgement | None:
    """``cutoff_not_met`` with reason, score, upgrade-until and target. None for rules of another kind."""
    if rules.get("kind") != "movie":
        return None
    order = _order(rules)
    parsed, quality = _read_current(current, original_language)
    score = _current_score(rules, current, parsed, quality, ReleaseContext(original_language=original_language))
    reason = _file_reason(rules, order, quality, lambda: score)
    upgrades_allowed = bool(rules.get("upgrades_allowed"))
    target: str | None = None
    items: tuple[str, ...] = ()
    if reason is not None and upgrades_allowed and order.cutoff >= 0 and _rank(order, quality.name) <= order.cutoff:
        target, items = _target(rules, order)
    return FileJudgement(
        reason=reason,
        score=score,
        upgrade_until=int(rules.get("upgrade_until") or 0) if upgrades_allowed else None,
        target=target,
        target_items=items,
    )


#: The upgrade reason of a release the current file came from.
SAME_RELEASE = "same_release"
_NAME_SEPARATORS = re.compile(r"[\s._+\-]+")
_VIDEO_ENDINGS = frozenset({"mkv", "mp4", "m4v", "avi", "ts", "m2ts", "wmv", "mov", "webm"})


def same_release(name: str | None, other: str | None) -> bool:
    """Whether two names are the same release: case, separators and a video ending do not count. A path counts by its
    last part. Two empty names are not the same."""

    def key(text: str) -> str:
        last = PurePosixPath(text.replace("\\", "/")).name
        stem, dot, ending = last.rpartition(".")
        if dot and ending.casefold() in _VIDEO_ENDINGS:
            last = stem
        return _NAME_SEPARATORS.sub(".", last).strip(".").casefold()

    if not name or not other:
        return False
    first, second = key(name), key(other)
    return bool(first) and first == second


def _upgrade(
    rules: dict[str, Any], order: _Order, new_quality: str, new_score: int, current_quality: str, current_score: int
) -> dict[str, Any]:
    new, current = _rank(order, new_quality), _rank(order, current_quality)
    upgrades_allowed = bool(rules.get("upgrades_allowed"))
    cutoff = order.cutoff if upgrades_allowed else order.lowest_allowed
    upgrade_until = int(rules.get("upgrade_until") or 0)
    step = int(rules.get("min_upgrade_step") or 1)
    if new > current and current < cutoff:
        reason = None
    elif new < current:
        reason = "worse_quality"
    elif not upgrades_allowed:
        reason = "upgrades_disabled"
    elif new > current:
        reason = "cutoff_met"
    elif new_score <= current_score:
        reason = "score_not_higher"
    elif current_score >= upgrade_until:
        reason = "upgrade_until_reached"
    elif new_score < current_score + step:
        reason = "step_too_small"
    else:
        reason = None
    return {
        "current_quality": current_quality,
        "current_score": current_score,
        "better": reason is None,
        "reason": reason,
    }


def evaluate(
    rules: dict[str, Any], name: str, context: ReleaseContext | None = None, parsed: Parsed | None = None
) -> dict[str, Any]:
    """The engine result: parsed, accepted, score, matched, rejections, upgrade."""
    if rules.get("kind") != "movie":
        raise ValueError(f"no engine for the kind {rules.get('kind')!r}")
    context = context or ReleaseContext()
    parsed = parsed or parse(name, context.original_language, context.multi_languages)
    commit = str(rules.get("trash_commit") or "")
    order = _order(rules)
    quality = parsed.movie.quality
    total, matched = formats.score(rules.get("formats") or [], parsed.format_input(context), commit)

    rejections: list[dict[str, Any]] = []
    if quality.id == q.UNKNOWN_QUALITY.id:
        rejections.append({"code": "unknown_quality"})
    elif quality.name not in order.allowed:
        rejections.append({"code": "quality_not_allowed", "quality": quality.name})
    minimum = int(rules.get("min_score") or 0)
    if total < minimum:
        rejections.append({"code": "score_below_minimum", "score": total, "minimum": minimum})
    size = _size_rejection(rules, quality.name, context)
    if size is not None:
        rejections.append(size)
    missing = _missing_languages(rules, parsed.languages, parsed.original_language)
    if missing:
        rejections.append({"code": "language_missing", "languages": missing})
    if parsed.movie.hardcoded_subs:
        rejections.append({"code": "hardcoded_subs", "value": parsed.movie.hardcoded_subs})

    upgrade = None
    if context.current_file is not None:
        current = context.current_file
        # The same reading and scoring as ``cutoff_not_met``, so the search and the stored verdict never disagree.
        parsed_current, current_quality = _read_current(current, context.original_language)
        current_score = _current_score(rules, current, parsed_current, current_quality, context)
        upgrade = _upgrade(rules, order, quality.name, total, current_quality.name, current_score)
        if same_release(name, current.name):
            upgrade = {**upgrade, "better": False, "reason": SAME_RELEASE}

    return {
        "parsed": parsed.as_dict(),
        "accepted": not rejections,
        "score": total,
        "matched": matched,
        "rejections": rejections,
        "upgrade": upgrade,
        "below_target": _below_target(rules, quality),
    }


# --- Shared with the series engine ------------------------------------------------------------- #
#: ``series_decision.py`` builds on the same order, rank and upgrade rule; the names below are its handles.
#: The movie engine keeps using the private ones, so nothing about it changes.
order_of = _order
rank_of = _rank
upgrade_of = _upgrade
missing_languages_of = _missing_languages
below_target_of = _below_target
item_names_of = _item_names
item_label_of = _item_label
read_current = _read_current
