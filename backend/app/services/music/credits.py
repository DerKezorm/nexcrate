"""An artist credit as one line: ``Example Crew feat. Guest``.

MusicBrainz's join phrases carry their own spaces (`` feat. ``, `` & ``, ``, ``, `` x ``, `` (from …)``). Up to 1.7.7
nexcrate stripped them on read, so credits stored by then hold ``feat.``, ``&`` and ``,``. ``join_text`` puts the
spaces back for those, so they read right without fetching every album again; a join phrase that still has its spaces
stays as it is. Tags, file names and the album page all build the line here.
"""

from __future__ import annotations

from typing import Any

#: Signs that hug the name before them: ``A, B``, ``A (feat. B)``.
_NO_SPACE_BEFORE = (",", ";", ":", ")", "]", "}")
#: Signs that hug the name after them: ``A (feat. B)``.
_NO_SPACE_AFTER = ("(", "[", "{")
#: Japanese and Chinese separators go without spaces: ``A、B``, ``A・B``.
_NO_SPACES = ("、", "・", "，", "；", "／")


def join_text(join: Any) -> str:
    """The join phrase as it belongs between two names, also for one stored without its spaces."""
    if not isinstance(join, str) or not join:
        return ""
    core = join.strip()
    if core != join or core in _NO_SPACES:
        # Still with MusicBrainz's own spacing, or a sign that never had any.
        return join
    before = "" if core.startswith(_NO_SPACE_BEFORE) else " "
    after = "" if core.endswith(_NO_SPACE_AFTER) else " "
    return f"{before}{core}{after}"


def credit_text(credit: list[dict[str, Any]] | None) -> str:
    """``A feat. B``: the names with MusicBrainz's join phrases."""
    return "".join(f"{part.get('name') or ''}{join_text(part.get('join'))}" for part in credit or []).strip()
