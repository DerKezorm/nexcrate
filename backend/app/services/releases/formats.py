"""Custom formats: Radarr's match rule over a parsed release, and the score.

The rule ("Facts"), kept exactly, because TRaSH's scores were tested with it:

* Specifications are grouped by type (their ``implementation``).
* ``negate`` flips the result of a single specification first.
* A group matches when no ``required`` specification in it failed and at least one of them matched.
* The format matches when every group matches. A format without specifications matches everything.
* The score is the sum of the scores of the matched formats.

Regex specifications (release title, release group, edition) use the ``regex`` package, case-insensitive:
9 of TRaSH's patterns need a lookbehind of variable width, which Python's ``re`` refuses. A missing value (no
group, no edition) is no match, before ``negate``, as in Radarr.

Every pattern is compiled once per TRaSH commit and runs with a time limit. A pattern that does not compile
or runs out of time counts as no match (before ``negate``) and is logged once; it never stops an evaluation.

Types TRaSH's Radarr formats use: ReleaseTitle, ReleaseGroup, Source, Language, QualityModifier, Resolution,
IndexerFlag. TRaSH's Sonarr formats use the same ones without QualityModifier and Edition, plus ReleaseType
(1 single episode, 2 multi episode, 3 season pack). Size, Edition and Year work as Radarr describes them. An
unknown type never matches.

⚠️ ``source`` holds the number of the kind the rules were built for: Radarr's for movies, Sonarr's for series
(``qualities_series.py``). The same goes for the quality tables the summary check walks (``table``).

**Whether a format can apply at all** (``can_match_allowed``) is a separate, pure check for the profile summary
("Changes after the owner's test"): the same group rule over the quality-bound types only
(Resolution, Source, QualityModifier), against each allowed quality. Every other type counts as satisfiable,
because a release name can carry anything. It takes no part in a score.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import regex

from . import languages as lang
from . import qualities as q

logger = logging.getLogger("nexcrate.formats")

#: Seconds one pattern may search one text. TRaSH's patterns need microseconds.
PATTERN_TIMEOUT_SECONDS = 0.25
#: Commits whose compiled patterns stay in memory: the current state and a few older profiles.
COMMITS_KEPT = 4
GIB = 1024**3

REGEX_TYPES = frozenset({"ReleaseTitleSpecification", "ReleaseGroupSpecification", "EditionSpecification"})


@dataclass(frozen=True)
class FormatInput:
    """What a specification can look at."""

    release_title: str
    group: str | None
    edition: str | None
    source: int
    resolution: int
    modifier: int
    #: Radarr's numbers, already resolved against the movie's original language.
    languages: tuple[int, ...]
    #: Radarr's number of the movie's original language; 0 when unknown.
    original_language: int
    indexer_flags: int = 0
    size_bytes: int | None = None
    year: int | None = None
    #: Sonarr's release type for series: 1 single episode, 2 multi episode, 3 season pack; 0 when unknown.
    release_type: int = 0


class _Patterns:
    """Compiled patterns per commit, the newest ``COMMITS_KEPT`` commits kept."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_commit: OrderedDict[str, dict[str, regex.Pattern[str] | None]] = OrderedDict()
        self._reported: set[str] = set()

    def get(self, commit: str, pattern: str) -> regex.Pattern[str] | None:
        with self._lock:
            compiled = self._by_commit.get(commit)
            if compiled is None:
                compiled = {}
                self._by_commit[commit] = compiled
                while len(self._by_commit) > COMMITS_KEPT:
                    self._by_commit.popitem(last=False)
            else:
                self._by_commit.move_to_end(commit)
            if pattern in compiled:
                return compiled[pattern]
        try:
            result: regex.Pattern[str] | None = regex.compile(pattern, regex.IGNORECASE)
        except (regex.error, TypeError, ValueError, OverflowError) as exc:
            result = None
            self.report(pattern, f"does not compile: {type(exc).__name__}")
        with self._lock:
            compiled[pattern] = result
        return result

    def report(self, pattern: str, what: str) -> None:
        with self._lock:
            if pattern in self._reported:
                return
            self._reported.add(pattern)
        logger.warning(
            "A custom format pattern %s and counts as no match (pattern of %d characters)", what, len(pattern)
        )

    def forget(self, commit: str) -> None:
        """Drop what one commit compiled. The editor's test compiles whatever is typed, and would only ever grow."""
        with self._lock:
            self._by_commit.pop(commit, None)

    def clear(self) -> None:
        with self._lock:
            self._by_commit.clear()
            self._reported.clear()
        with _prepared_lock:
            _prepared.clear()

    def count(self, commit: str) -> int:
        with self._lock:
            return len(self._by_commit.get(commit, {}))


patterns = _Patterns()


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _search(pattern: Any, value: str | None, commit: str) -> bool:
    if value is None or not isinstance(pattern, str):
        return False
    compiled = patterns.get(commit, pattern)
    if compiled is None:
        return False
    try:
        return compiled.search(value, timeout=PATTERN_TIMEOUT_SECONDS) is not None
    except TimeoutError:
        patterns.report(pattern, f"ran longer than {PATTERN_TIMEOUT_SECONDS} seconds")
        return False


def specification_matches(specification: dict[str, Any], data: FormatInput, commit: str) -> bool:
    """The result of one specification without ``negate``."""
    implementation = specification.get("implementation")
    fields = specification.get("fields") if isinstance(specification.get("fields"), dict) else {}
    value = fields.get("value")
    if implementation == "ReleaseTitleSpecification":
        return _search(value, data.release_title, commit)
    if implementation == "ReleaseGroupSpecification":
        return _search(value, data.group, commit)
    if implementation == "EditionSpecification":
        return _search(value, data.edition, commit)
    if implementation == "SourceSpecification":
        return _int(value) == data.source
    if implementation == "ResolutionSpecification":
        return data.resolution != 0 and _int(value) == data.resolution
    if implementation == "QualityModifierSpecification":
        return _int(value) == data.modifier
    if implementation == "LanguageSpecification":
        wanted = _int(value)
        if wanted is None:
            return False
        if wanted == lang.ORIGINAL and data.original_language != lang.UNKNOWN:
            wanted = data.original_language
        if fields.get("exceptLanguage") is True:
            return any(language != wanted for language in data.languages)
        return wanted in data.languages
    if implementation == "ReleaseTypeSpecification":
        return data.release_type != 0 and _int(value) == data.release_type
    if implementation == "IndexerFlagSpecification":
        flag = _int(value)
        return bool(flag) and (data.indexer_flags & flag) == flag
    if implementation == "SizeSpecification":
        low, high = _number(fields.get("min")), _number(fields.get("max"))
        if not data.size_bytes or low is None or high is None:
            return False
        size = data.size_bytes / GIB
        return low < size <= high
    if implementation == "YearSpecification":
        low, high = _number(fields.get("min")), _number(fields.get("max"))
        if data.year is None or low is None or high is None:
            return False
        return low <= data.year <= high
    return False


def format_matches(custom_format: dict[str, Any], data: FormatInput, commit: str) -> bool:
    groups: dict[str, list[tuple[bool, bool]]] = {}
    for specification in custom_format.get("specifications") or []:
        if not isinstance(specification, dict):
            continue
        result = specification_matches(specification, data, commit) != bool(specification.get("negate"))
        groups.setdefault(str(specification.get("implementation")), []).append(
            (result, bool(specification.get("required")))
        )
    return all(
        any(result for result, _required in group) and not any(required and not result for result, required in group)
        for group in groups.values()
    )


def explain(custom_format: dict[str, Any], data: FormatInput, commit: str) -> dict[str, Any]:
    """Why a format matches a release or not: every specification with its result, grouped as the rule groups them.

    For the test in the editor. The verdict is ``format_matches``'s, computed from the same parts, so what the
    editor shows is what a judgement would do. ``result`` is the specification's result after ``negate``.
    """
    conditions: list[dict[str, Any]] = []
    groups: dict[str, list[tuple[bool, bool]]] = {}
    for index, specification in enumerate(custom_format.get("specifications") or []):
        if not isinstance(specification, dict):
            continue
        implementation = str(specification.get("implementation"))
        result = specification_matches(specification, data, commit) != bool(specification.get("negate"))
        required = bool(specification.get("required"))
        groups.setdefault(implementation, []).append((result, required))
        fields = specification.get("fields") if isinstance(specification.get("fields"), dict) else {}
        # A pattern that does not compile counts as no match and says nothing; here it has to say so.
        broken = implementation in REGEX_TYPES and (
            not isinstance(fields.get("value"), str) or patterns.get(commit, fields["value"]) is None
        )
        conditions.append(
            {
                "index": index,
                "implementation": implementation,
                "result": result,
                "required": required,
                "problem": "pattern_invalid" if broken else None,
            }
        )
    verdicts = {
        implementation: any(result for result, _required in group)
        and not any(required and not result for result, required in group)
        for implementation, group in groups.items()
    }
    return {
        "matches": all(verdicts.values()),
        "conditions": conditions,
        "groups": [{"implementation": name, "matches": verdict} for name, verdict in verdicts.items()],
    }


# --- Prepared formats ---------------------------------------------------------------------------------- #
#
# Measured on the owner's Synology on 16.09.2026: the series checker with 50 names and four versions scores 400 times
# over some 72 formats each, and only a fifth of the time went into the regex search itself. The rest was looking up
# each pattern again (a lock and two dictionaries per search) and reading the same fields out of the same dictionaries.
# ``score`` therefore prepares a rule set once: patterns compiled, numbers read, specifications grouped by type. The
# rule of ``format_matches`` stays exactly the same; a test holds both ways equal over TRaSH's formats.


@dataclass(frozen=True, slots=True)
class _Spec:
    implementation: str
    #: The compiled pattern for the regex types, else the number or the raw value.
    value: Any
    fields: dict[str, Any]
    negate: bool
    required: bool
    #: The pattern text, to name it in the log when it runs too long.
    pattern: str | None = None


@dataclass(frozen=True, slots=True)
class _Prepared:
    name: str
    score: int
    #: Specifications grouped by type, in the order the types first appear.
    groups: tuple[tuple[_Spec, ...], ...]


#: Rule sets prepared recently: (the list itself, its commit, the prepared formats). Holding the list keeps its id from
#: being reused while it is cached, so an identity check is enough.
_PREPARED_KEPT = 16
_prepared: list[tuple[list[dict[str, Any]], str, tuple[_Prepared, ...]]] = []
_prepared_lock = threading.Lock()


def _prepare_spec(specification: dict[str, Any], commit: str) -> _Spec:
    implementation = str(specification.get("implementation"))
    fields = specification.get("fields") if isinstance(specification.get("fields"), dict) else {}
    raw = fields.get("value")
    pattern: str | None = None
    if implementation in REGEX_TYPES:
        pattern = raw if isinstance(raw, str) else None
        value: Any = patterns.get(commit, pattern) if pattern is not None else None
    elif implementation in (
        "SourceSpecification",
        "ResolutionSpecification",
        "QualityModifierSpecification",
        "ReleaseTypeSpecification",
        "LanguageSpecification",
        "IndexerFlagSpecification",
    ):
        value = _int(raw)
    else:
        value = raw
    return _Spec(
        implementation=implementation,
        value=value,
        fields=fields,
        negate=bool(specification.get("negate")),
        required=bool(specification.get("required")),
        pattern=pattern,
    )


def _prepare(formats: list[dict[str, Any]], commit: str) -> tuple[_Prepared, ...]:
    with _prepared_lock:
        for kept, kept_commit, prepared in _prepared:
            if kept is formats and kept_commit == commit:
                return prepared
    result: list[_Prepared] = []
    for custom_format in formats:
        if not isinstance(custom_format, dict):
            continue
        grouped: dict[str, list[_Spec]] = {}
        for specification in custom_format.get("specifications") or []:
            if not isinstance(specification, dict):
                continue
            spec = _prepare_spec(specification, commit)
            grouped.setdefault(spec.implementation, []).append(spec)
        result.append(
            _Prepared(
                name=str(custom_format.get("name", "")),
                score=_int(custom_format.get("score")) or 0,
                groups=tuple(tuple(group) for group in grouped.values()),
            )
        )
    prepared = tuple(result)
    with _prepared_lock:
        _prepared.append((formats, commit, prepared))
        del _prepared[:-_PREPARED_KEPT]
    return prepared


def _prepared_matches(spec: _Spec, data: FormatInput) -> bool:
    """One prepared specification without ``negate``: exactly ``specification_matches``."""
    implementation = spec.implementation
    if spec.pattern is not None or implementation in REGEX_TYPES:
        if implementation == "ReleaseTitleSpecification":
            text = data.release_title
        elif implementation == "ReleaseGroupSpecification":
            text = data.group
        else:
            text = data.edition
        if text is None or spec.value is None:
            return False
        try:
            return spec.value.search(text, timeout=PATTERN_TIMEOUT_SECONDS) is not None
        except TimeoutError:
            patterns.report(spec.pattern or "", f"ran longer than {PATTERN_TIMEOUT_SECONDS} seconds")
            return False
    wanted = spec.value
    if implementation == "SourceSpecification":
        return wanted == data.source
    if implementation == "ResolutionSpecification":
        return data.resolution != 0 and wanted == data.resolution
    if implementation == "QualityModifierSpecification":
        return wanted == data.modifier
    if implementation == "ReleaseTypeSpecification":
        return data.release_type != 0 and wanted == data.release_type
    if implementation == "LanguageSpecification":
        if wanted is None:
            return False
        if wanted == lang.ORIGINAL and data.original_language != lang.UNKNOWN:
            wanted = data.original_language
        if spec.fields.get("exceptLanguage") is True:
            return any(language != wanted for language in data.languages)
        return wanted in data.languages
    if implementation == "IndexerFlagSpecification":
        return bool(wanted) and (data.indexer_flags & wanted) == wanted
    if implementation in ("SizeSpecification", "YearSpecification"):
        return specification_matches(
            {"implementation": implementation, "fields": spec.fields}, data, ""
        )
    return False


def _prepared_format_matches(prepared: _Prepared, data: FormatInput) -> bool:
    """``format_matches`` over a prepared format. A group that fails ends the check early; the result is the same."""
    for group in prepared.groups:
        passed = False
        for spec in group:
            result = _prepared_matches(spec, data) != spec.negate
            if spec.required and not result:
                return False
            passed = passed or result
        if not passed:
            return False
    return True


def score(formats: list[dict[str, Any]], data: FormatInput, commit: str) -> tuple[int, list[dict[str, Any]]]:
    """The sum of the matched formats and the matched formats (name and score), ordered by name as Radarr does."""
    matched = [
        {"name": prepared.name, "score": prepared.score}
        for prepared in _prepare(formats, commit)
        if _prepared_format_matches(prepared, data)
    ]
    matched.sort(key=lambda entry: entry["name"])
    return sum(entry["score"] for entry in matched), matched


def compile_all(formats: list[dict[str, Any]], commit: str) -> int:
    """Compile every regex pattern of a rule set now. Returns how many patterns there are."""
    count = 0
    for custom_format in formats:
        for specification in custom_format.get("specifications") or []:
            fields = specification.get("fields") if isinstance(specification.get("fields"), dict) else {}
            if specification.get("implementation") in REGEX_TYPES and isinstance(fields.get("value"), str):
                patterns.get(commit, fields["value"])
                count += 1
    return count


# --- Whether a format can apply at a quality -------------------------------------------------------- #

#: Specification types that look at the quality only, with the part of the quality each one compares.
QUALITY_BOUND_TYPES = {
    "ResolutionSpecification": "resolution",
    "SourceSpecification": "source",
    "QualityModifierSpecification": "modifier",
}


def _quality_part_matches(implementation: str, value: Any, quality: Any) -> bool:
    """One quality-bound specification without ``negate``, compared as ``specification_matches`` compares it.

    ``quality`` is an entry of either quality table; Sonarr's has no modifier, which counts as 0 (none).
    """
    wanted = _int(value)
    if implementation == "ResolutionSpecification":
        return quality.resolution != 0 and wanted == quality.resolution
    if implementation == "SourceSpecification":
        return wanted == quality.source
    return wanted == getattr(quality, "modifier", q.NONE)


def can_match_quality(custom_format: dict[str, Any], quality: Any) -> bool:
    """Whether a release of this quality can match the format at all.

    Radarr's group rule over the quality-bound types only: specifications grouped by type, ``negate`` flips a
    single one, every ``required`` one of a type must pass and at least one of the type must pass. Every other
    type counts as satisfiable, so a format without quality-bound specifications can match at every quality.
    """
    groups: dict[str, list[tuple[bool, bool]]] = {}
    for specification in custom_format.get("specifications") or []:
        if not isinstance(specification, dict):
            continue
        implementation = str(specification.get("implementation"))
        if implementation not in QUALITY_BOUND_TYPES:
            continue
        fields = specification.get("fields") if isinstance(specification.get("fields"), dict) else {}
        passed = _quality_part_matches(implementation, fields.get("value"), quality)
        result = passed != bool(specification.get("negate"))
        groups.setdefault(implementation, []).append((result, bool(specification.get("required"))))
    return all(
        any(result for result, _required in group) and not any(required and not result for result, required in group)
        for group in groups.values()
    )


def allowed_qualities(qualities: list[dict[str, Any]], table: dict[str, Any] | None = None) -> list[Any]:
    """The allowed qualities of a rules' quality list, lowest first; an allowed group gives every member.

    ``table`` names the quality table of the kind; movies keep Radarr's.
    """
    known = q.BY_NAME if table is None else table
    found: list[Any] = []
    for item in qualities:
        if not isinstance(item, dict) or not item.get("allowed"):
            continue
        names = item.get("items") if "group" in item else [item.get("name")]
        found.extend(known[name] for name in names or [] if isinstance(name, str) and name in known)
    return found


def can_match_allowed(
    custom_format: dict[str, Any], qualities: list[dict[str, Any]], table: dict[str, Any] | None = None
) -> bool:
    """Whether the format can match a release of at least one allowed quality of the rules' quality list."""
    return any(can_match_quality(custom_format, quality) for quality in allowed_qualities(qualities, table))
