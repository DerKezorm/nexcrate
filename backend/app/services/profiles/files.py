"""Profiles as YAML files: export and import.

**Export** holds ``format: nexcrate-profile/1``, ``kind``, ``name`` (the version label), ``answers`` and
``trash_commit``. No patterns and no resolved rules: a shared file carries no untrusted regex and no TRaSH
content, and the receiving installation builds the rules with its own TRaSH state.

**Import** reads at most 64 KB with ``yaml.safe_load``, which builds plain data only (a ``!!python`` tag is an
error), then checks the shape before the answers go through the wizard's normalization. Every refusal is
``profile_import_invalid`` with a ``reason``:

* ``too_large``: more than 64 KB
* ``not_yaml``: not readable as one YAML document, or a tag safe loading refuses
* ``not_a_profile``: not a mapping, unknown top-level keys, or a field of the wrong type
* ``format_unknown``: a ``format`` other than ``nexcrate-profile/1``
* ``kind_unsupported``: a kind without profiles
* ``kind_mismatch``: another kind than the version it is imported into (checked by the route)
* ``answers_invalid``: answers the wizard refuses, with ``fields``
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import yaml

from . import KINDS

FORMAT = "nexcrate-profile/1"
MAX_BYTES = 64 * 1024
NAME_MAX_LENGTH = 200
_COMMIT = re.compile(r"^[0-9a-f]{7,40}$")
_KEYS = frozenset({"format", "kind", "name", "answers", "trash_commit"})


class ImportInvalid(Exception):
    def __init__(self, reason: str, fields: list[str] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.fields = fields


@dataclass(frozen=True)
class ProfileFile:
    kind: str
    name: str | None
    answers: dict[str, Any]
    trash_commit: str | None


def export_text(kind: str, name: str, answers: dict[str, Any], trash_commit: str) -> str:
    document = {"format": FORMAT, "kind": kind, "name": name, "answers": answers, "trash_commit": trash_commit}
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True, default_flow_style=False)


def read(text: str) -> ProfileFile:
    """The shape of an imported file. The answers are not normalized yet. Raises ``ImportInvalid``."""
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ImportInvalid("too_large")
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ImportInvalid("not_yaml") from exc
    if not isinstance(document, dict) or not set(document) <= _KEYS or "format" not in document:
        raise ImportInvalid("not_a_profile")
    if document["format"] != FORMAT:
        raise ImportInvalid("format_unknown")
    kind = document.get("kind")
    if not isinstance(kind, str):
        raise ImportInvalid("not_a_profile")
    if kind not in KINDS:
        raise ImportInvalid("kind_unsupported")
    name = document.get("name")
    if name is not None and (not isinstance(name, str) or len(name) > NAME_MAX_LENGTH):
        raise ImportInvalid("not_a_profile")
    answers = document.get("answers")
    if not isinstance(answers, dict):
        raise ImportInvalid("not_a_profile")
    commit = document.get("trash_commit")
    if commit is not None and (not isinstance(commit, str) or not _COMMIT.match(commit)):
        raise ImportInvalid("not_a_profile")
    return ProfileFile(kind=kind, name=name, answers=answers, trash_commit=commit)


def commit_differs(file_commit: str | None, current_commit: str) -> bool:
    """A file without a commit, or with another one than the current state (a short id counts as the same)."""
    return file_commit is None or not current_commit.startswith(file_commit)
