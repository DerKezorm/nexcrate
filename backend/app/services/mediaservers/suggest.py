"""Path pairs the dialog can offer, from the folders the server lists and the folders of nexcrate's versions.

⚠️ This is an offer, shown to the owner in the dialog before anything is saved, and never more. ``notify`` still
translates with the pairs the owner saved and guesses nothing (``paths``): a pair nobody confirmed is never used.

How a pair is found, per media kind (a movie version against the movie libraries, and so on):

* The version's folder lies inside a library folder as it is: nothing to translate, no pair.
* Both end in the same folder names (``/volume1/media/movies`` here, ``/data/movies`` there): the pair is what stands
  before that ending (``/volume1/media`` and ``/data``). The longest common ending wins; two library folders with the
  same ending but another beginning are ambiguous and give no pair.
* No common ending, but exactly one version folder and exactly one library folder of the kind: those two as a pair.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from . import paths

#: The kind of a version definition to the kind of a library.
LIBRARY_KIND = {"movie": "movie", "series": "series", "album": "music"}
MAX_PAIRS = 20


@dataclass(frozen=True)
class Suggested:
    local: str
    remote: str
    #: same_ending or only_one.
    reason: str


@dataclass(frozen=True)
class Suggestion:
    pairs: tuple[Suggested, ...]
    #: Every version folder already lies inside a library folder as it is: no pair is needed.
    nothing_needed: bool


def _head(path: str, keep: int) -> str:
    """``path`` without its last folder names, in its own shape; empty when nothing is left."""
    parts = paths._parts(path)[:keep]
    if not parts:
        return ""
    if paths._windows(path):
        return ("\\\\" if path.startswith("\\\\") else "") + "\\".join(parts)
    return "/" + "/".join(parts)


def _common_ending(local: str, remote: str) -> int:
    first, second = paths._parts(local), paths._parts(remote)
    if paths._windows(remote) or paths._windows(local):
        first, second = [part.casefold() for part in first], [part.casefold() for part in second]
    count = 0
    while count < min(len(first), len(second)) and first[-1 - count] == second[-1 - count]:
        count += 1
    return count


def _pair_for(local: str, remotes: list[str]) -> Suggested | None:
    found: dict[tuple[str, str], int] = {}
    for remote in remotes:
        count = _common_ending(local, remote)
        if not count:
            continue
        local_head = _head(local, len(paths._parts(local)) - count)
        remote_head = _head(remote, len(paths._parts(remote)) - count)
        # One side is the root: the whole folders are the clearer pair.
        pair = (local_head, remote_head) if local_head and remote_head else (local, remote)
        found[pair] = max(found.get(pair, 0), count)
    if not found:
        return None
    longest = max(found.values())
    best = [pair for pair, count in found.items() if count == longest]
    if len(best) != 1:
        return None
    return Suggested(local=best[0][0], remote=best[0][1], reason="same_ending")


def suggest(local_by_kind: Mapping[str, Iterable[str]], libraries: Iterable[Any]) -> Suggestion:
    """``local_by_kind``: the folders of the versions per kind of version (movie, series, album). ``libraries``: what
    the server lists, anything with ``kind`` and ``locations``."""
    listed = [library for library in libraries if getattr(library, "locations", None)]
    pairs: list[Suggested] = []
    any_folder, all_covered = False, True
    for kind, folders in local_by_kind.items():
        wanted = LIBRARY_KIND.get(kind)
        of_kind = [library for library in listed if library.kind == wanted]
        remotes = [location for library in of_kind for location in library.locations]
        locals_ = [folder for folder in folders if folder]
        if not remotes:
            continue
        for local in locals_:
            any_folder = True
            whole = paths.Library(id="all", name="", kind=wanted, locations=tuple(remotes), refresh=True)
            seen_as_is = paths.library_of(local, [whole]) is not None
            if seen_as_is:
                continue
            all_covered = False
            pair = _pair_for(local, remotes)
            if pair is None and len(locals_) == 1 and len(remotes) == 1:
                pair = Suggested(local=local, remote=remotes[0], reason="only_one")
            if pair is not None and (pair.local, pair.remote) not in {(item.local, item.remote) for item in pairs}:
                pairs.append(pair)
    return Suggestion(pairs=tuple(pairs[:MAX_PAIRS]), nothing_needed=any_folder and all_covered)

