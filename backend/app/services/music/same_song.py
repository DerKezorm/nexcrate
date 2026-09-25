"""When two tracks of an album are the same song (finding of 19.09.2026 on the owner's instance).

MusicBrainz gives a remaster a recording of its own for some tracks and keeps the old one for others: of the 28 tracks
of an album's first edition, 8 had another recording in its 30th anniversary edition. Compared by recording alone, a
download of the new edition added those 8 a second time. A track therefore has keys: its recording, and its folded name
when that name is unique in its release (two tracks called "Intro" on one release are never merged by name).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Protocol

from .. import schreibweisen


class TrackLike(Protocol):
    @property
    def id(self) -> int: ...

    @property
    def recording_mbid(self) -> str | None: ...

    @property
    def name(self) -> str: ...


def name_key(name: str | None) -> str:
    forms = schreibweisen.keys(name)
    return forms[0] if forms else ""


def keys_by_track(releases: Iterable[Iterable[TrackLike]]) -> dict[int, frozenset[str]]:
    """Per track id of the given releases (each an iterable of its tracks): the keys of its song."""
    found: dict[int, frozenset[str]] = {}
    for tracks in releases:
        listed = list(tracks)
        names = Counter(name_key(track.name) for track in listed)
        for track in listed:
            keys = {track.recording_mbid or f"track:{track.id}"}
            name = name_key(track.name)
            if name and names[name] == 1:
                keys.add(f"name:{name}")
            found[track.id] = frozenset(keys)
    return found


def union(keys: dict[int, frozenset[str]], track_ids: Iterable[int]) -> set[str]:
    return {key for track_id in track_ids for key in keys.get(track_id, frozenset())}
