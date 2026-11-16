"""Which version of a song or an album a name says: live, instrumental, a cappella, remix, karaoke, demo, acoustic.

Found on the owner's instance (16.11.2026): the live release of a studio album was taken as the album, and one of its
live tracks was filed on the studio track of the same position, as its length was 10 s off at most.

**Only a qualifier counts, never a word of the title.** A song called "Live Forever" or an album called "Live and
Dangerous" says nothing about its version; "Live Forever (Live at Knebworth)" and "Song - Instrumental" do. So the
words are looked for in brackets and in the part after the last " - ", never in the rest of the name.

**Compared, not filtered.** A mark counts only when one side has it and the other has not, and when the album does not
expect it: an album whose own title or MusicBrainz type says live (``kinds``) expects live tracks, whatever their
names say. MusicBrainz writes "(live)" only where the cover does; a file of a live album may say "(Live)" anyway.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

LIVE = "live"
INSTRUMENTAL = "instrumental"
ACAPPELLA = "acappella"
REMIX = "remix"
KARAOKE = "karaoke"
DEMO = "demo"
ACOUSTIC = "acoustic"
MARKS = (LIVE, INSTRUMENTAL, ACAPPELLA, REMIX, KARAOKE, DEMO, ACOUSTIC)

#: Single words of a qualifier and the mark they give.
_WORDS = {
    "live": LIVE,
    "instrumental": INSTRUMENTAL,
    "instrumentals": INSTRUMENTAL,
    "instr": INSTRUMENTAL,
    "inst": INSTRUMENTAL,
    "acapella": ACAPPELLA,
    "acappella": ACAPPELLA,
    "accapella": ACAPPELLA,
    "acapellas": ACAPPELLA,
    "acappellas": ACAPPELLA,
    "remix": REMIX,
    "remixes": REMIX,
    "remixed": REMIX,
    "rmx": REMIX,
    "karaoke": KARAOKE,
    "demo": DEMO,
    "demos": DEMO,
    "acoustic": ACOUSTIC,
    "unplugged": ACOUSTIC,
}
#: Two words that make one mark ("a cappella").
_PAIRS = {("a", "capella"): ACAPPELLA, ("a", "cappella"): ACAPPELLA}
#: The secondary types of MusicBrainz (``kinds``) and what an album of that type expects.
_SECONDARY = {"Live": LIVE, "Remix": REMIX, "DJ-mix": REMIX, "Demo": DEMO}

_BRACKETS = re.compile(r"[(\[{]([^()\[\]{}]*)[)\]}]")
_DASH = re.compile(r"\s[-–—]\s")
_WORD = re.compile(r"[a-z0-9]+")


def _folded(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text.replace("_", " "))
    return "".join(char for char in plain.casefold() if not unicodedata.combining(char))


def _of_part(part: str) -> set[str]:
    words = _WORD.findall(_folded(part))
    found = {_WORDS[word] for word in words if word in _WORDS}
    found |= {mark for pair, mark in _PAIRS.items() if any(tuple(words[i : i + 2]) == pair for i in range(len(words)))}
    return found


def qualifiers(name: str | None) -> list[str]:
    """The parts of a name that qualify it: every bracket, and the part after the last " - " that is no bracket."""
    if not name:
        return []
    parts = _BRACKETS.findall(name)
    rest = _BRACKETS.sub(" ", name)
    pieces = _DASH.split(rest)
    if len(pieces) > 1 and pieces[-1].strip():
        parts.append(pieces[-1])
    return parts


def without_qualifiers(name: str | None) -> str:
    """The name without the qualifiers that say a version: "Song (instrumental)" is "Song"."""
    text = name or ""
    text = _BRACKETS.sub(lambda found: " " if _of_part(found.group(1)) else found.group(0), text)
    pieces = _DASH.split(text)
    if len(pieces) > 1 and _of_part(pieces[-1]):
        text = text[: text.rfind(pieces[-1])].rstrip(" -–—")
    return " ".join(text.split())


def marks(name: str | None) -> frozenset[str]:
    """The versions a name says in its qualifiers."""
    return frozenset(mark for part in qualifiers(name) for mark in _of_part(part))


def expected(album_title: str | None, secondary_types: Iterable[str] | None = ()) -> frozenset[str]:
    """What an album's tracks may say without being another version: its own qualifiers, the words of its title
    ("MTV Unplugged", "Live at Leeds"), and its MusicBrainz types."""
    own = {_WORDS[word] for word in _WORD.findall(_folded(album_title or "")) if word in _WORDS}
    own |= marks(album_title)
    own |= {_SECONDARY[kind] for kind in secondary_types or () if kind in _SECONDARY}
    return frozenset(own)


def of_release(release_name: str | None, album_title: str | None) -> frozenset[str]:
    """The versions a release's name says beyond the album's title: "Album Instrumentals",
    "Album + Instrumentals", "Album: Remix Plus". A release names its version in words of its own, often without
    brackets; a word the album's title has as well ("MTV Unplugged") says nothing."""
    own = set(_WORD.findall(_folded(album_title or "")))
    words = [word for word in _WORD.findall(_folded(release_name or "")) if word not in own]
    found = {_WORDS[word] for word in words if word in _WORDS}
    found |= {mark for pair, mark in _PAIRS.items() if any(tuple(words[i : i + 2]) == pair for i in range(len(words)))}
    return frozenset(found)


def differs(one: Iterable[str], other: Iterable[str], allowed: Iterable[str] = ()) -> frozenset[str]:
    """The marks only one side has, without those the album expects: empty when both are the same version."""
    return frozenset((set(one) ^ set(other)) - set(allowed))
