"""References: a title's number together with where it comes from, ``tmdb:603`` (shape 2).

⚠️ A reference alone does not name a title: TMDB counts movies and series apart, ``tmdb:1399`` exists as both. What
names a title is ``kind`` plus ``ref``, as in the database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ...models import Artist, Title

#: The sources a title of a kind can be asked by. ``tmdb`` is the anchor and comes first in ``refs``; music has
#: MusicBrainz's id only, the release group for an album.
SOURCES = {"movie": ("tmdb", "imdb"), "series": ("tmdb", "tvdb", "imdb"), "album": ("mbid",), "artist": ("mbid",)}
_NUMBER = re.compile(r"[1-9][0-9]{0,11}")
_MBID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_IMDB = re.compile(r"tt[0-9]{5,12}")


@dataclass(frozen=True)
class Ref:
    source: str
    value: str

    def __str__(self) -> str:
        return f"{self.source}:{self.value}"


class RefError(ValueError):
    """``code`` is ``ref_invalid`` or ``ref_source_unknown``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def parse(kind: str, raw: str) -> Ref:
    """Split at the first colon and check the value against its source. Raises ``RefError``."""
    source, colon, value = raw.partition(":")
    if not colon or not source or not value:
        raise RefError("ref_invalid")
    if source not in SOURCES.get(kind, ()):
        raise RefError("ref_source_unknown")
    pattern = _IMDB if source == "imdb" else _MBID if source == "mbid" else _NUMBER
    if source == "mbid":
        value = value.lower()
    if not pattern.fullmatch(value):
        raise RefError("ref_invalid")
    return Ref(source, value)


def primary(title: Title) -> str | None:
    """``tmdb:<number>``, for an album ``mbid:<release group>``; None for a title without one."""
    if title.kind == "album":
        return f"mbid:{title.mbid}" if title.mbid else None
    return f"tmdb:{title.tmdb_id}" if title.tmdb_id is not None else None


def of_artist(artist: Artist) -> str:
    return f"mbid:{artist.mbid}"


def all_of(title: Title) -> list[str]:
    """Every reference nexcrate knows of the title, the anchor first."""
    found = [primary(title)]
    if title.kind == "series" and title.tvdb_id:
        found.append(f"tvdb:{title.tvdb_id}")
    if title.imdb_id:
        found.append(f"imdb:{title.imdb_id}")
    return [ref for ref in found if ref]
