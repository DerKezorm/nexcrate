"""Questions of a profile wizard and their answers, for every kind.

The wizard in the interface renders the list the server sends, so a new kind needs questions here and texts
in the frontend, not a new wizard. The shapes are the contract of the design notes, "Questions" and
"Changes after the owner's test":

* ``choice`` has ``options``, ``boolean`` has none, ``languages`` has ``codes`` and ``roles``, ``number`` has
  ``min``, ``max`` and ``nullable``.
* ``detailed: true``: shown only while the question ``mode`` is ``detailed``.
* ``when``: conditions that must all hold. Four simple shapes, ``{"question", "is"}``,
  ``{"question": "languages", "includes"}`` (any role), ``{"question": "languages", "excludes"}`` (in no role)
  and ``{"question": "languages", "required_at_least"}``,
  and ``{"any": [[<simple>, ...], ...]}``, which holds when every condition of at least one inner list holds.
  Inner lists hold simple shapes only, nothing nests. No other shapes.

**Answers** are normalized before anything else sees them: missing answers get their defaults, answers of
hidden questions go back to their defaults, and every stored or returned answer set is a normalized one.
Unknown ids, values outside the options or the range, and an empty or duplicated language list are refused
with the names of the fields. Ids of retired questions are dropped without an error and without a look at their
value, so answers stored or exported before a question was retired still load.

Conditions are evaluated in list order against the answers normalized so far; a condition refers to a
question earlier in the list. ``True`` is not ``1``: values compare with their type.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Any

#: The question that switches the detailed questions on.
MODE_QUESTION = "mode"
DETAILED_MODE = "detailed"
TYPES = ("choice", "boolean", "languages", "number")

_UNSET = object()


class AnswersInvalid(Exception):
    """Answers that do not fit the questions. ``fields`` names the offending ids."""

    def __init__(self, fields: list[str]) -> None:
        super().__init__(", ".join(fields))
        self.fields = fields


class ProfileBuildError(Exception):
    """The TRaSH state does not hold what the rules need. The message is safe for a log line."""


@dataclass(frozen=True)
class Condition:
    """One condition of ``when``: a simple shape (``question`` with exactly one of ``is_``, ``includes``,
    ``excludes`` and ``required_at_least``) or ``alternatives`` alone, the ``any`` shape. Build the latter with
    ``any_of``."""

    question: str = ""
    is_: Any = _UNSET
    includes: str | None = None
    #: The language code that must appear in no role (decision 11 of the design notes).
    excludes: str | None = None
    required_at_least: int | None = None
    #: The ``any`` shape: lists of simple conditions; it holds when every condition of one list holds.
    alternatives: tuple[tuple[Condition, ...], ...] | None = None

    def __post_init__(self) -> None:
        simple = (
            self.is_ is not _UNSET,
            self.includes is not None,
            self.excludes is not None,
            self.required_at_least is not None,
        )
        if self.alternatives is None:
            if not self.question or sum(simple) != 1:
                raise ValueError(
                    "a simple condition names a question and one of is, includes, excludes, required_at_least"
                )
            return
        if self.question or any(simple):
            raise ValueError("an any condition has no question and no value of its own")
        if not self.alternatives or not all(self.alternatives):
            raise ValueError("an any condition needs at least one list, and no list may be empty")
        if any(condition.alternatives is not None for alternative in self.alternatives for condition in alternative):
            raise ValueError("any conditions do not nest")

    def holds(self, answers: dict[str, Any]) -> bool:
        if self.alternatives is not None:
            return any(
                all(condition.holds(answers) for condition in alternative) for alternative in self.alternatives
            )
        value = answers.get(self.question)
        if self.is_ is not _UNSET:
            return type(value) is type(self.is_) and value == self.is_
        entries = value if isinstance(value, list) else []
        if self.includes is not None:
            return any(entry.get("code") == self.includes for entry in entries)
        if self.excludes is not None:
            return not any(entry.get("code") == self.excludes for entry in entries)
        required = sum(1 for entry in entries if entry.get("role") == "required")
        return self.required_at_least is not None and required >= self.required_at_least

    def questions(self) -> list[str]:
        """The question ids this condition looks at."""
        if self.alternatives is None:
            return [self.question]
        return [condition.question for alternative in self.alternatives for condition in alternative]

    def as_dict(self) -> dict[str, Any]:
        if self.alternatives is not None:
            return {"any": [[condition.as_dict() for condition in alternative] for alternative in self.alternatives]}
        if self.is_ is not _UNSET:
            return {"question": self.question, "is": self.is_}
        if self.includes is not None:
            return {"question": self.question, "includes": self.includes}
        if self.excludes is not None:
            return {"question": self.question, "excludes": self.excludes}
        return {"question": self.question, "required_at_least": self.required_at_least}


def any_of(*alternatives: tuple[Condition, ...]) -> Condition:
    """The ``any`` shape: holds when every condition of at least one of these tuples holds."""
    return Condition(alternatives=tuple(tuple(alternative) for alternative in alternatives))


@dataclass(frozen=True)
class Question:
    id: str
    type: str
    default: Any
    options: tuple[str, ...] = ()
    codes: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    min: float | None = None
    max: float | None = None
    nullable: bool = False
    detailed: bool = False
    when: tuple[Condition, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        shape: dict[str, Any] = {"id": self.id, "type": self.type}
        if self.type == "choice":
            shape["options"] = list(self.options)
        elif self.type == "languages":
            shape["codes"] = list(self.codes)
            shape["roles"] = list(self.roles)
        elif self.type == "number":
            shape.update({"min": self.min, "max": self.max, "nullable": self.nullable})
        shape["default"] = self.fresh_default()
        shape["detailed"] = self.detailed
        shape["when"] = [condition.as_dict() for condition in self.when]
        return shape

    def fresh_default(self) -> Any:
        return copy.deepcopy(self.default)

    def visible(self, answers: dict[str, Any]) -> bool:
        if self.detailed and answers.get(MODE_QUESTION) != DETAILED_MODE:
            return False
        return all(condition.holds(answers) for condition in self.when)


def _valid(question: Question, value: Any) -> tuple[bool, Any]:
    if question.type == "choice":
        return (isinstance(value, str) and value in question.options), value
    if question.type == "boolean":
        return isinstance(value, bool), value
    if question.type == "number":
        if value is None:
            return question.nullable, None
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
            return False, value
        if (question.min is not None and value < question.min) or (question.max is not None and value > question.max):
            return False, value
        return True, int(value) if float(value).is_integer() else float(value)
    if question.type == "languages":
        if not isinstance(value, list) or not value or len(value) > len(question.codes):
            return False, value
        entries: list[dict[str, str]] = []
        for entry in value:
            if not isinstance(entry, dict) or set(entry) != {"code", "role"}:
                return False, value
            if entry["code"] not in question.codes or entry["role"] not in question.roles:
                return False, value
            if any(existing["code"] == entry["code"] for existing in entries):
                return False, value
            entries.append({"code": entry["code"], "role": entry["role"]})
        return True, entries
    return False, value


def without_retired(answers: dict[str, Any], retired: frozenset[str]) -> dict[str, Any]:
    """The answers without the ids of retired questions, whatever their values."""
    return {key: value for key, value in answers.items() if key not in retired}


def normalize(
    questions: tuple[Question, ...],
    raw: Any,
    *,
    kind: str,
    schema: int,
    retired: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Validated answers with defaults filled in and hidden answers reset. Raises ``AnswersInvalid``.

    Ids in ``retired`` are dropped first, without a look at their values.
    """
    if not isinstance(raw, dict):
        raise AnswersInvalid(["answers"])
    raw = without_retired(raw, retired)
    known = {question.id: question for question in questions}
    fields: list[str] = [str(key) for key in raw if key not in known and key not in ("schema", "kind")]
    if "schema" in raw and (type(raw["schema"]) is not int or raw["schema"] != schema):
        fields.append("schema")
    if "kind" in raw and raw["kind"] != kind:
        fields.append("kind")
    values: dict[str, Any] = {}
    for question in questions:
        if question.id not in raw:
            values[question.id] = question.fresh_default()
            continue
        ok, value = _valid(question, raw[question.id])
        if ok:
            values[question.id] = value
        else:
            fields.append(question.id)
    if fields:
        raise AnswersInvalid(list(dict.fromkeys(fields)))
    normalized: dict[str, Any] = {}
    for question in questions:
        seen = {**values, **normalized}
        normalized[question.id] = values[question.id] if question.visible(seen) else question.fresh_default()
    return {"schema": schema, "kind": kind, **normalized}


def visible_ids(questions: tuple[Question, ...], answers: dict[str, Any]) -> list[str]:
    return [question.id for question in questions if question.visible(answers)]
