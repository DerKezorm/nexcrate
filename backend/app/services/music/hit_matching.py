"""Which album a found release is: the one searched, another one of the artist, or none (M3.3).

A pure function: ``match`` gets the parsed name, the artist and every album of the artist, and reads no database.

**Exact, as the owner chose (decision 10).** The head of the name (the text in front of the features, see
``music_parser``) must be the artist followed by the album, word for word once both are spelling keys
(``schreibweisen.keys``: umlauts in three forms, punctuation as spaces, "&" as "and" and "und"). The parser's split
between artist and album is not trusted: an artist with a hyphen makes it guess. A leading "The" may be missing on
either side. A year the parser took off the end of the head counts as part of it when the album's title ends with it
("Summer Hits 2020"). Edition words (Deluxe, Remastered) are already out of the head; what the parser leaves at its
end is taken off too, when it says nothing but the edition: a bracket or a last part after a hyphen of edition words
only ("(Premium Edition)", "-Super Deluxe Edition", "(Explicit Version)"), and a bracket that starts with a year
("[2019 Pop]"). A bracket of other words ("(Instrumentals)", "- The Remixes") is other music and stays. Text is
compared in NFKC, so "²" is "2".

**Further artists.** When the head does not give the artist and album, the parser's split is tried, unless it is
unsure: the artist part may name further artists after the artist ("A & B", "A, B", "A feat. B", "A x B", "A vs. B"),
and the album part must be the album. An artist whose own name holds such a word ("A and the B") still matches, as
the parts are tried from the left.

**Against every album of the artist (decision 11),** not only the one searched: a release of another album says so
with that album. Two albums of the artist with the same name: the one whose year the name gives, else the one
searched (decision 12). The kind the name gives (single, EP, live, soundtrack, compilation) picks among albums of the
same name as well: a single of the same name is a single, not the album.

**Not this album (decision 13):** several albums in one release, a tribute or karaoke release when the album's own
title does not say so, a sampler for an album of one artist, another artist.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .. import schreibweisen
from ..releases.music_parser import ParsedAlbum

THIS = "this"
OTHER_ALBUM = "other_album"
UNKNOWN_ALBUM = "unknown_album"
OTHER_ARTIST = "other_artist"
NOT_AN_ALBUM = "not_an_album"
KINDS = (THIS, OTHER_ALBUM, UNKNOWN_ALBUM, OTHER_ARTIST, NOT_AN_ALBUM)

#: What a kind in the name means among MusicBrainz's types: the primary type, or a secondary one.
PRIMARY_OF_KIND = {"single": "Single", "ep": "EP"}
SECONDARY_OF_KIND = {
    "live": "Live",
    "soundtrack": "Soundtrack",
    "compilation": "Compilation",
    "demo": "Demo",
    "mixtape": "Mixtape/Street",
}
_ARTICLE = "the"
#: Words of an edition at the end of a head: taken off before comparing. Numbers count too ("10 Year Anniversary").
EDITION_WORDS = frozenset(
    {
        "edition",
        "version",
        "deluxe",
        "super",
        "premium",
        "explicit",
        "clean",
        "edited",
        "digital",
        "international",
        "intl",
        "collectors",
        "collector",
        "special",
        "limited",
        "expanded",
        "remastered",
        "remaster",
        "anniversary",
        "year",
        "years",
        "bonus",
        "track",
        "tracks",
        "platinum",
        "gold",
        "standard",
        "tour",
        "fan",
        "exclusive",
        "uncensored",
        "censored",
        "japan",
        "japanese",
        "import",
        "reissue",
        "mono",
        "stereo",
    }
)
_NOT_ONLY = frozenset({"edition", "version", "year", "years", "track", "tracks", "tour"})
_BRACKET_TAIL = re.compile(r"[\s._-]*[(\[]([^()\[\]]*)[)\]][\s._-]*$")
_DASH_TAIL = re.compile(r"(?:\s+-\s+|\.-\.|-)([^-]+)$")
_ORDINAL = re.compile(r"[0-9]+(?:st|nd|rd|th)?")
_CATALOGUE = re.compile(r"[A-Z]{0,6}[ ._]?[0-9][0-9 ._]{3,14}[A-Z0-9]?")
_YEAR_FIRST = re.compile(r"^\s*(19|20)\d{2}(?!\d)")
_JOINERS = re.compile(r"\s*(?:,|&|\+|\s(?:x|and|und|feat\.?|ft\.?|featuring|with|vs\.?|presents)\s)\s*", re.IGNORECASE)


@dataclass(frozen=True)
class AlbumRef:
    title_id: int
    title: str
    year: int | None = None
    primary_type: str | None = None
    secondary_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class ArtistRef:
    #: The artist's name and further names (aliases); the first is the one shown.
    names: tuple[str, ...]
    various: bool = False


@dataclass(frozen=True)
class Match:
    kind: str
    #: The album for ``this`` and ``other_album``.
    album: AlbumRef | None = None
    #: Why not: ``several_albums``, ``tribute``, ``karaoke``, ``sampler``, ``not_a_sampler``.
    reason: str | None = None
    #: What the name was read as, for the owner: artist and album as the parser proposes them.
    read_artist: str = ""
    read_album: str = ""
    #: The name gives a kind the album does not have (a single named like the album, and no single of that name).
    kind_differs: str | None = None


def _without_article(key: str) -> str | None:
    words = key.split(" ")
    return " ".join(words[1:]) if len(words) > 1 and words[0] == _ARTICLE else None


def _nfkc(text: str) -> str:
    """NFKC, with a digit such as "²" as a word of its own: "Blueprint²" is "Blueprint 2" in release names."""
    spaced = "".join(f" {character} " if unicodedata.category(character) == "No" else character for character in text)
    return unicodedata.normalize("NFKC", spaced)


def _edition_only(text: str) -> bool:
    words = [word for key in schreibweisen.keys(text)[:1] for word in key.split(" ")]
    if not words:
        return False
    if any(word not in EDITION_WORDS and not _ORDINAL.fullmatch(word) for word in words):
        return False
    return any(word in EDITION_WORDS and word not in _NOT_ONLY for word in words)


def _catalogue(text: str) -> bool:
    """A catalogue number as the last part: capitals and digits, at least four digits ("EPC85930", "588 708")."""
    return _CATALOGUE.fullmatch(text.strip()) is not None and sum(character.isdigit() for character in text) >= 4


def cleaned(head: str) -> list[str]:
    """The head, and the head without edition words and a dated bracket at its end, when those were there."""
    forms = [head]
    current = head
    while True:
        bracket = _BRACKET_TAIL.search(current)
        if bracket is not None and (_edition_only(bracket.group(1)) or _YEAR_FIRST.match(bracket.group(1))):
            current = current[: bracket.start()]
            forms.append(current)
            continue
        dash = _DASH_TAIL.search(current)
        if dash is not None and dash.start() > 0 and (_edition_only(dash.group(1)) or _catalogue(dash.group(1))):
            current = current[: dash.start()]
            forms.append(current)
            continue
        return forms


def _lead_artists(text: str) -> list[str]:
    """The artist part from the left, one further artist at a time: "A & B, C" gives "A", "A & B", "A & B, C"."""
    parts = _JOINERS.split(text)
    joins = _JOINERS.findall(text)
    found = []
    for index in range(len(parts)):
        found.append("".join(part + (joins[i] if i < index else "") for i, part in enumerate(parts[: index + 1])))
    return found


def _key_forms(texts: Iterable[str]) -> set[str]:
    found: set[str] = set()
    for text in texts:
        for key in schreibweisen.keys(_nfkc(text)):
            found.add(key)
            bare = _without_article(key)
            if bare:
                found.add(bare)
    return found


def _heads(parsed: ParsedAlbum, head: str) -> set[str]:
    texts: list[str] = []
    for form in cleaned(head):
        texts.append(form)
        for year in {parsed.year, parsed.edition_year}:
            if year:
                texts.append(f"{form} {year}")
    return _key_forms(texts)


def _split_fits(parsed: ParsedAlbum, artist_keys: set[str], album_keys: set[str]) -> bool:
    """The parser's split: an artist part that starts with the artist, and an album part that is the album."""
    if parsed.unsure or not parsed.artist or not parsed.album:
        return False
    if not album_keys & _key_forms(cleaned(parsed.album)):
        return False
    return any(artist_keys & _key_forms([lead]) for lead in _lead_artists(parsed.artist))


def _album_head(parsed: ParsedAlbum) -> str:
    """The head without the sampler's ``VA`` in front: the album of a sampler as the parser read it."""
    return parsed.album if parsed.various_artists else parsed.head


def _fits_kind(album: AlbumRef, kinds: Sequence[str]) -> bool:
    for kind in kinds:
        primary = PRIMARY_OF_KIND.get(kind)
        if primary is not None and album.primary_type != primary:
            return False
        secondary = SECONDARY_OF_KIND.get(kind)
        if secondary is not None and secondary not in album.secondary_types:
            return False
    return True


def _pick(found: list[AlbumRef], wanted_id: int, parsed: ParsedAlbum) -> AlbumRef:
    """Among the albums whose name the head gives: by kind, then by year, else the one searched, else the first."""
    kinds = [kind for kind in parsed.kinds if kind in PRIMARY_OF_KIND or kind in SECONDARY_OF_KIND]
    if kinds:
        fitting = [album for album in found if _fits_kind(album, kinds)]
        if fitting:
            found = fitting
    elif len(found) > 1:
        # A name without a kind is an album: a single or a live album of the same name comes after it.
        special = set(SECONDARY_OF_KIND.values())
        plain = [
            album for album in found if album.primary_type != "Single" and not set(album.secondary_types) & special
        ]
        if plain:
            found = plain
    wanted = next((album for album in found if album.title_id == wanted_id), None)
    if parsed.year is not None:
        dated = [album for album in found if album.year == parsed.year]
        if dated and (wanted is None or wanted.year != parsed.year):
            return dated[0]
    return wanted if wanted is not None else found[0]


def _words(text: str) -> set[str]:
    return {word for key in schreibweisen.keys(text) for word in key.split(" ")}


def _refused(parsed: ParsedAlbum, album: AlbumRef) -> str | None:
    """Several albums, or a tribute or karaoke release the album's own title does not name. The word counts in the
    parser's warnings and anywhere in the head ("Album (A Tribute)")."""
    if parsed.several_albums:
        return "several_albums"
    own = _words(album.title)
    head = _words(parsed.head)
    for warning in ("tribute", "karaoke"):
        if (warning in parsed.warnings or warning in head) and warning not in own:
            return warning
    return None


def match(parsed: ParsedAlbum, artist: ArtistRef, albums: Sequence[AlbumRef], wanted_id: int) -> Match:
    """The album this release is, among ``albums`` of ``artist``; ``wanted_id`` is the album searched."""
    read = {"read_artist": parsed.artist, "read_album": parsed.album}
    if parsed.several_albums:
        return Match(NOT_AN_ALBUM, reason="several_albums", **read)
    if artist.various and not parsed.various_artists:
        return Match(NOT_AN_ALBUM, reason="not_a_sampler", **read)
    if parsed.various_artists and not artist.various:
        return Match(NOT_AN_ALBUM, reason="sampler", **read)

    heads = _heads(parsed, _album_head(parsed))
    artist_keys = {""} if artist.various else _key_forms(artist.names)
    found: list[AlbumRef] = []
    for album in albums:
        album_keys = _key_forms([album.title])
        if (
            any(f"{artist_key} {album_key}".strip() in heads for artist_key in artist_keys for album_key in album_keys)
            or not artist.various
            and _split_fits(parsed, artist_keys, album_keys)
        ):
            found.append(album)
    if found:
        chosen = _pick(found, wanted_id, parsed)
        refused = _refused(parsed, chosen)
        if refused is not None:
            return Match(NOT_AN_ALBUM, album=chosen, reason=refused, **read)
        kinds = [kind for kind in parsed.kinds if kind in PRIMARY_OF_KIND or kind in SECONDARY_OF_KIND]
        differs = next((kind for kind in kinds if not _fits_kind(chosen, [kind])), None)
        kind = THIS if chosen.title_id == wanted_id else OTHER_ALBUM
        return Match(kind, album=chosen, kind_differs=differs, **read)
    if artist.various:
        return Match(UNKNOWN_ALBUM, **read)
    leads = _key_forms(_lead_artists(parsed.artist)) if parsed.artist and not parsed.unsure else set()
    for head in heads | leads:
        if any(head == key or head.startswith(f"{key} ") for key in artist_keys):
            refused = _refused(parsed, AlbumRef(title_id=0, title=""))
            if refused is not None:
                return Match(NOT_AN_ALBUM, reason=refused, **read)
            return Match(UNKNOWN_ALBUM, **read)
    return Match(OTHER_ARTIST, **read)
