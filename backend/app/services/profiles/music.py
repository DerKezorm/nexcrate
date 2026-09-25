"""The profile for music: four questions, rules without TRaSH (part M2.3, decisions 13 to 16).

There is one music version, so there is one music profile. It lives in the table of all profiles (``kind`` is
``album``, ``trash_commit`` empty) but not among ``profiles.KINDS``: those kinds build against a TRaSH state, carry a
summary of qualities, formats and sizes, and export as YAML for Radarr and Sonarr. Music has none of it, so it shares
only the questions' machinery (``questions.normalize``) and has routes of its own (``/api/music/profile``).

The rules
---------
* ``steps``: what the profile does with each quality step (``releases/music_qualities.py``):
  ``target``, ``for_now`` (taken while nothing of the target is there, the file counts as one to be replaced),
  ``waits`` (not the target, and ``take_now`` is off) or ``never``. Mid and low lossy are never taken.
* ``target_floor``: the worst step that still is the target. A file below it can be improved.
* ``hires`` and ``source`` only order releases, they never shut one out (the owner's answer of 18.09.2026: 86 % of
  the measured releases are WEB, "CD only" would lock nearly everything out).
* Fixed and said, no question (decision 14): an album as one file with a cue sheet, several albums in one release
  and audiobooks are refused by ``releases/music_decision.py``.
"""

from __future__ import annotations

from typing import Any

from ..releases import music_decision as md
from ..releases import music_qualities as mq
from .questions import Question, normalize

KIND = md.KIND
SCHEMA = 1
#: The version of the rules ``build`` writes. Stored rules with a lower one are rebuilt on start.
RULES_VERSION = 1

QUALITIES = ("lossless", "either", "lossy")
HIRES = ("any", "prefer", "avoid")
SOURCES = ("avoid_vinyl", "any", "prefer_cd")

QUESTIONS: tuple[Question, ...] = (
    Question("quality", "choice", "lossless", options=QUALITIES),
    Question("take_now", "boolean", True),
    Question("hires", "choice", "any", options=HIRES),
    Question("source", "choice", "avoid_vinyl", options=SOURCES),
)
RETIRED_QUESTIONS: frozenset[str] = frozenset()

TARGET, FOR_NOW, WAITS, NEVER = md.TARGET, md.FOR_NOW, md.WAITS, md.NEVER


def questions_payload() -> dict[str, Any]:
    return {"kind": KIND, "schema": SCHEMA, "questions": [question.as_dict() for question in QUESTIONS]}


def normalized(raw: Any) -> dict[str, Any]:
    """Validated answers with defaults filled in. Raises ``questions.AnswersInvalid``."""
    return normalize(QUESTIONS, raw, kind=KIND, schema=SCHEMA, retired=RETIRED_QUESTIONS)


def _roles(quality: str, take_now: bool) -> dict[str, str]:
    other = FOR_NOW if take_now else WAITS
    lossless = TARGET if quality in ("lossless", "either") else other
    high = TARGET if quality in ("lossy", "either") else other
    return {
        mq.LOSSLESS_24: lossless,
        mq.LOSSLESS_16: lossless,
        mq.LOSSY_HIGH: high,
        mq.LOSSY_MID: NEVER,
        mq.LOSSY_LOW: NEVER,
    }


def build(answers: dict[str, Any]) -> dict[str, Any]:
    """The rules of normalized answers. Same answers, same rules."""
    quality = str(answers["quality"])
    return {
        "kind": KIND,
        "rules_version": RULES_VERSION,
        "trash_commit": "",
        "steps": _roles(quality, bool(answers["take_now"])),
        # With "lossy" the target is high lossy, and lossless is better than it: nothing below high is the target.
        "target_floor": mq.LOSSLESS_16 if quality == "lossless" else mq.LOSSY_HIGH,
        "hires": str(answers["hires"]),
        "source": str(answers["source"]),
    }


def summary(answers: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
    """What the interface says about the profile: the answers that make the sentence, and the ladder best first."""
    steps = rules.get("steps") if isinstance(rules.get("steps"), dict) else {}
    return {
        "quality": answers.get("quality"),
        "take_now": bool(answers.get("take_now")),
        "hires": answers.get("hires"),
        "source": answers.get("source"),
        "ladder": [{"step": step, "role": steps.get(step, NEVER)} for step in mq.STEPS],
    }


def rules_behind(rules: Any) -> bool:
    version = rules.get("rules_version") if isinstance(rules, dict) else None
    return not isinstance(version, int) or isinstance(version, bool) or version < RULES_VERSION
