"""Profiles per version, for every kind: questions, answers, rules, summary, the one line.

⚠️ Nothing here may assume movies. Each kind registers its questions, the ids of its retired questions, its
builder, its summary and its line in ``KINDS``: ``movie`` and ``series``. Music brings its own module later and
no new wizard, because the interface renders the question list the server sends. Every kind builds against the
TRaSH state of its own app (``trash.current(kind)``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..trash_data import TrashSnapshot
from . import movie, series
from .questions import AnswersInvalid, ProfileBuildError, Question, normalize, without_retired


class KindUnsupported(Exception):
    """No profiles for this kind (yet)."""


@dataclass(frozen=True)
class KindSpec:
    kind: str
    schema: int
    #: The version of the rules ``build`` writes. A stored profile with a lower one is rebuilt on start.
    rules_version: int
    questions: tuple[Question, ...]
    #: Ids of questions that were asked once; normalization drops them from stored and imported answers.
    retired: frozenset[str]
    build: Callable[[dict[str, Any], TrashSnapshot], dict[str, Any]]
    summary: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
    #: None when the stored answers cannot fill the line.
    line: Callable[[dict[str, Any], bool], dict[str, Any] | None]


KINDS: dict[str, KindSpec] = {
    movie.KIND: KindSpec(
        kind=movie.KIND,
        schema=movie.SCHEMA,
        rules_version=movie.RULES_VERSION,
        questions=movie.QUESTIONS,
        retired=movie.RETIRED_QUESTIONS,
        build=movie.build,
        summary=movie.summary,
        line=movie.line,
    ),
    series.KIND: KindSpec(
        kind=series.KIND,
        schema=series.SCHEMA,
        rules_version=series.RULES_VERSION,
        questions=series.QUESTIONS,
        retired=series.RETIRED_QUESTIONS,
        build=series.build,
        summary=series.summary,
        line=series.line,
    ),
}


def spec(kind: str) -> KindSpec:
    found = KINDS.get(kind)
    if found is None:
        raise KindUnsupported(kind)
    return found


def rules_version_of(rules: Any) -> int:
    """The version stored rules were built with. Without the field, or with anything but a whole number, they count
    as 1, the version before the field existed."""
    value = rules.get("rules_version") if isinstance(rules, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else 1


def rules_behind(kind: str, rules: Any) -> bool:
    """Whether stored rules are of a lower version than the builder of their kind writes. Raises ``KindUnsupported``."""
    return rules_version_of(rules) < spec(kind).rules_version


def questions_payload(kind: str) -> dict[str, Any]:
    kind_spec = spec(kind)
    return {
        "kind": kind_spec.kind,
        "schema": kind_spec.schema,
        "questions": [question.as_dict() for question in kind_spec.questions],
    }


def _normalize(kind_spec: KindSpec, raw: Any) -> dict[str, Any]:
    return normalize(
        kind_spec.questions, raw, kind=kind_spec.kind, schema=kind_spec.schema, retired=kind_spec.retired
    )


def normalized(kind: str, raw: Any) -> dict[str, Any]:
    """Validated and normalized answers. Raises ``KindUnsupported`` or ``AnswersInvalid``."""
    return _normalize(spec(kind), raw)


def stored_answers(kind: str, answers: dict[str, Any]) -> dict[str, Any]:
    """Stored answers as the API shows and exports them: without the ids of retired questions.

    A profile saved before a question was retired still holds the id in the database; it goes when the profile
    is saved again. The stored rules stay as built meanwhile, like those of every stored profile.
    """
    found = KINDS.get(kind)
    return without_retired(answers, found.retired) if found is not None else dict(answers)


@dataclass(frozen=True)
class Built:
    answers: dict[str, Any]
    rules: dict[str, Any]
    summary: dict[str, Any]


def build(kind: str, raw: Any, snapshot: TrashSnapshot | None = None) -> Built:
    """Normalize the answers and build the rules against a TRaSH state, by default the current one.

    Raises ``KindUnsupported``, ``AnswersInvalid`` or ``ProfileBuildError``.
    """
    kind_spec = spec(kind)
    answers = _normalize(kind_spec, raw)
    if snapshot is None:
        from .. import trash

        snapshot = trash.current(kind_spec.kind)
    rules = kind_spec.build(answers, snapshot)
    return Built(answers=answers, rules=rules, summary=kind_spec.summary(answers, rules))


def summary_of(kind: str, answers: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
    return spec(kind).summary(answers, rules)


def line_of(kind: str, answers: dict[str, Any], outdated: bool) -> dict[str, Any] | None:
    found = KINDS.get(kind)
    return found.line(answers, outdated) if found is not None else None


__all__ = [
    "KINDS",
    "AnswersInvalid",
    "Built",
    "KindSpec",
    "KindUnsupported",
    "ProfileBuildError",
    "build",
    "line_of",
    "normalized",
    "questions_payload",
    "rules_behind",
    "rules_version_of",
    "spec",
    "stored_answers",
    "summary_of",
]
