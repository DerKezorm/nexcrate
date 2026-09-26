"""Whether a release belongs to the title ("Matching releases to the title").

1. **By number:** the release's ``tmdbid`` equals the title's TMDB number, or its ``imdb`` number equals the title's
   IMDb number (compared as numbers, so leading zeros do not matter), and a year read from the name, if any, is at
   most one year away from the title's.
2. **By name,** only for a release without a number that can be compared: the title read from the name shares a
   spelling key (``schreibweisen.keys``, every form) with the title, the original title or an alternative title,
   and the year read from the name equals the title's year. A missing year does not match.

Decisions taken here:

* A number can be compared when both the release and the title have it. A release with only an IMDb number, for a
  title without one, goes by name.
* A release whose numbers name another movie is not this movie, whatever its name says; neither is one whose number
  fits but whose year is two or more years off. Radarr tries the name in that case; the plan does not.
* With numbers, a title without a year accepts any year.

**The name against the number** (``title_fits``, the owner's decision of 26.09.2026: never automatically). An indexer
may list a release under the wrong movie's number, and a movie of that number would be replaced by another movie, as in
Radarr. A release that belongs by its number still belongs, but when the title read from its name fits none of the
movie's names, the search marks it ``title_mismatch``: the automatic never takes it, the owner may. The import checks
the release name and the video's name the same way before anything moves (``downloads.importing``).

A name fits when, with the spelling keys (umlauts, accents, "&"), a leading article left aside, number words and Roman
numbers as digits, and without a trailing edition or release tag ("Extended", "Director's Cut", "REPACK", "Dubbed",
"HDR", "Criterion Collection"), it

* equals a title, the original title, an alternative title of any country, a translated title or an alias,
* or one part of such a title split at a colon, a dash, a slash or a bracket ("Example Hunters – Die Beispieljäger" is
  "Example Hunters" and "Die Beispieljäger"), but only with a year in the name that fits the movie's (at most one
  off, as for the number): a part alone is often the name of another movie of the series (the owner's decision of
  26.09.2026),
* or such a title with one number put in or left out inside it ("Examplezilla 2 King of the Examples" for
  "Examplezilla: King of the Examples"), never at its end: "Example 2" is not "Example",
* or such a title of at least two words followed by more words (a subtitle the title lacks), and never by fewer words:
  "The Example" is not "The Example Circle",
* or such a title followed by the part number 1 ("Example Harry 1", "Example Harry I", "Example Harry Part 1"),
* or the movie's names one after the other, each whole or as a part, each with or without a leading article: an
  original title followed by the German one, "Zehn - Ten - T3n", or "Examplia Preis der Beispiele" for "Examplia -
  Der Preis der Beispiele". A row of parts alone needs the year as one part alone does ("Example Impossible" for
  "Example: Impossible – Final Count"); one whole name in the row is enough.

A part number from 2 on after the title is a sequel, also behind "Part", "Teil", "Vol", "Chapter", "Episode" and with a
subtitle after it: "Example Harry Part 2", "Example Harry II Die Rückkehr", "Star Example Episode V". A collection
fits none of its movies: a range of parts ("Example 1-6", "Example I-III", "Example 1 bis 3", "Example 1+2") or a word
such as "Trilogy", "Collection", "Saga" or "Boxset", unless the movie's own name has it.

Words written together or apart are one name ("Examplepax" and "Example Pax"), numbers still count. A year inside the
name with title words after it ("Emil Beispiel 2011 und der Zauberstab") is read past: the parser stops at the year,
the title is the whole name.

Words in another order are another name ("So spielt das Beispiel" is not "Wie das Beispiel so spielt"). A name nothing
can be read from fits: there is nothing to compare. Only movies; series and albums match as before.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache
from itertools import pairwise

from .. import schreibweisen
from ..releases import parser
from .model import TitleInfo

YEAR_TOLERANCE = 1

#: The rejection of every version for a release whose number fits the movie and whose name does not.
TITLE_MISMATCH = "title_mismatch"


def belongs(
    title: TitleInfo,
    *,
    tmdb_id: int | None,
    imdb_id: int | None,
    parsed_title: str | None,
    parsed_year: int | None,
) -> bool:
    by_tmdb = tmdb_id is not None and title.tmdb_id is not None
    by_imdb = imdb_id is not None and title.imdb_id is not None
    if by_tmdb or by_imdb:
        same = (by_tmdb and tmdb_id == title.tmdb_id) or (by_imdb and imdb_id == title.imdb_id)
        if not same:
            return False
        return parsed_year is None or title.year is None or abs(parsed_year - title.year) <= YEAR_TOLERANCE
    if parsed_year is None or title.year is None or parsed_year != title.year:
        return False
    return not title.keys.isdisjoint(schreibweisen.keys(parsed_title))


# --- The name against the number -------------------------------------------------------------------------------- #

#: Leading articles left aside: ``schreibweisen.ARTICLES``, German declined forms, French, Spanish and Italian ones.
_ARTICLES = schreibweisen.ARTICLES | {"den", "dem", "des", "ein", "eine", "le", "la", "les", "el", "il"}
#: Number words and Roman numbers as digits, in every spelling key ("fünf" is "fuenf", "funf" and "fnf").
_NUMBERS = {
    **dict.fromkeys(("one", "eins"), "1"),
    **dict.fromkeys(("two", "zwei", "ii"), "2"),
    **dict.fromkeys(("three", "drei", "iii"), "3"),
    **dict.fromkeys(("four", "vier", "iv"), "4"),
    **dict.fromkeys(("five", "fuenf", "funf", "fnf", "v"), "5"),
    **dict.fromkeys(("six", "sechs", "vi"), "6"),
    **dict.fromkeys(("seven", "sieben", "vii"), "7"),
    **dict.fromkeys(("eight", "acht", "viii"), "8"),
    **dict.fromkeys(("nine", "neun", "ix"), "9"),
    **dict.fromkeys(("ten", "zehn", "x"), "10"),
}
#: Release tags that stay in a title read without a year. The ones of several words come first.
_TAGS = (
    r"Anniversary[ ._-]Edition|Special[ ._-]Extended[ ._-]Version|Criterion[ ._-]Collection|Black[ ._-]and[ ._-]White"
    r"|READ[ ._-]?NFO|PROPER|REPACK|RERiP|LiMiTED|INTERNAL|Dubbed|Rated|Hybrid|SDR|HDR|DV|Colorized|Kinoversion|FS|WS"
    # "Special Extended Version" read without a language: the parser stops at the edition and keeps "Special".
    r"|Special"
)
#: What may trail a title read without a year: a release tag, an edition, a German cut, a documentary tag, a resolution.
_TRAILING = re.compile(
    rf"(?:[ ._-]+(?:{_TAGS}|{parser.EDITION_PATTERN}|DC|Kinofassung|Langfassung|Doku|Dokumentation|\d{{3,4}}p))+$",
    re.IGNORECASE,
)
#: Where a title splits into parts: a colon, a vertical bar, a slash, a bracket, a dash between spaces or a long dash.
_PARTS = re.compile(r"\s*[:|/()\[\]]\s*|\s+-\s+|\s*[–—]\s*")
_AKA = re.compile(r"\s+a\.?k\.?a\.?\s+", re.IGNORECASE)
_YEAR = re.compile(r"(?:19|20)\d\d")
#: Words that name a part before its number: "Part 2", "Teil 2", "Vol 2", "Chapter 2", "Episode V".
_PART_WORDS = frozenset({"part", "pt", "teil", "vol", "volume", "chapter", "kapitel", "episode"})
#: The part number that names the first movie, not a sequel ("Example 1" is "Example").
_FIRST_PART = frozenset({"1", "i"})
#: Words of a collection of movies, and pairs of words ("Double Feature").
_COLLECTION_WORDS = frozenset(
    {
        "trilogy", "trilogie", "quadrilogy", "anthology", "collection", "sammlung", "filmreihe", "duology", "saga",
        "complete", "box", "boxset",
    }
)  # fmt: skip
_COLLECTION_PAIRS = frozenset({("double", "feature")})
#: What joins the two numbers of a range of parts; "-" and "+" are spaces in a spelling key.
_RANGE_WORDS = frozenset({"bis", "and", "und", "to"})

Words = tuple[str, ...]


def _words(key: str) -> Words:
    """A spelling key as words: numbers as digits, a part word before a number left out ("Part 2" is "2", in the name
    of a release as in the movie's own), a leading article left aside."""
    numbers = [_NUMBERS.get(word, word) for word in key.split()]
    words = [
        word
        for index, word in enumerate(numbers)
        if not (word in _PART_WORDS and index + 1 < len(numbers) and _is_number(numbers[index + 1]))
    ]
    if len(words) > 1 and words[0] in _ARTICLES:
        words = words[1:]
    return tuple(words)


def _forms(texts: Iterable[str | None], *, year: int | None = None) -> set[Words]:
    """The forms of texts. With ``year``, the movie's own year: a title ending in it is also the title without it."""
    found: set[Words] = set()
    for text in texts:
        for key in schreibweisen.keys(text):
            words = _words(key)
            if not words:
                continue
            found.add(words)
            # "Example (2019)" of 2019 is "Example"; "Example 2049" of 2017 stays, the number is part of its title.
            if year is not None and len(words) > 1 and words[-1] == str(year):
                found.add(words[:-1])
    return found


def _parts(text: str) -> list[str]:
    """The parts of a title, split at a colon, a dash, a slash or a bracket. None when a part is only a part number
    from 2 on ("Example Harry: Part 2"): the rest alone is the first movie, not this one."""
    pieces = [piece for piece in _PARTS.split(text) if piece.strip()]
    if len(pieces) < 2 or any(_a_part_number(piece) for piece in pieces):
        return []
    return pieces


def _a_part_number(text: str) -> bool:
    """Whether a text is only a part number from 2 on: "Part 2", "Teil II", "Vol. 3", "Zwei", "2"."""
    for key in schreibweisen.keys(text):
        words = _words(key)
        if len(words) == 1 and words[0].isdigit() and int(words[0]) >= 2:
            return True
    return False


@lru_cache(maxsize=256)
def whole_forms(title: TitleInfo) -> frozenset[Words]:
    """The forms of the movie's names as they are: each text whole, and every key (TMDB's are stored as keys)."""
    found = _forms(title.texts, year=title.year)
    found |= {words for key in title.keys if (words := _words(key))}
    return frozenset(found)


@lru_cache(maxsize=256)
def part_forms(title: TitleInfo) -> frozenset[Words]:
    """The forms of the parts of the movie's names that are not a whole name of it too."""
    found = _forms((part for text in title.texts for part in _parts(text)), year=title.year)
    return frozenset(found - whole_forms(title))


@lru_cache(maxsize=256)
def known_forms(title: TitleInfo) -> frozenset[Words]:
    """Every form of the movie's names: each text whole and in its parts, and every key."""
    return whole_forms(title) | part_forms(title)


@lru_cache(maxsize=256)
def _known_joined(title: TitleInfo) -> frozenset[str]:
    """The movie's names with their words written together."""
    return frozenset("".join(form) for form in known_forms(title))


@lru_cache(maxsize=256)
def _whole_joined(title: TitleInfo) -> frozenset[str]:
    """The movie's whole names with their words written together."""
    return frozenset("".join(form) for form in whole_forms(title))


@lru_cache(maxsize=256)
def _known_words(title: TitleInfo) -> frozenset[str]:
    return frozenset(word for form in known_forms(title) for word in form)


def _known_pair(title: TitleInfo, pair: tuple[str, str]) -> bool:
    return any(pair in pairwise(form) for form in known_forms(title))


def read_forms(parsed_title: str) -> set[Words]:
    """The forms of a title read from a name: whole and each side of an "aka", with and without a trailing edition."""
    texts = [parsed_title, *_AKA.split(parsed_title)]
    return _forms([*texts, *(_TRAILING.sub("", text) for text in texts)])


def _bare_forms(parsed_title: str) -> set[Words]:
    """The forms without a trailing edition or tag only: "Criterion Collection" is an edition, not a collection."""
    texts = [parsed_title, *_AKA.split(parsed_title)]
    return _forms(_TRAILING.sub("", text) for text in texts)


def _one_number_more(longer: Words, shorter: Words) -> bool:
    """``longer`` is ``shorter`` with one number put in, neither first nor last."""
    if len(longer) != len(shorter) + 1:
        return False
    return any(
        longer[index].isdigit() and longer[:index] + longer[index + 1 :] == shorter
        for index in range(1, len(longer) - 1)
    )


def _is_number(word: str) -> bool:
    return word.isdigit() or word in _FIRST_PART


def _part_number(more: Words) -> str | None:
    """The part number the words after a title start with ("Part" before it is left out already), "1" for the first
    part; None when they start otherwise."""
    word = more[0]
    if not _is_number(word):
        return None
    return "1" if word in _FIRST_PART else word


def _value(word: str) -> int:
    return 1 if word in _FIRST_PART else int(word)


def _has_range(words: Words) -> bool:
    """Whether the words hold a range of parts: a number and a higher one next to each other, or joined by "bis",
    "and", "to". Two equal numbers are no range ("Zehn - Ten" reads as 10 10)."""
    for index, word in enumerate(words[:-1]):
        if not _is_number(word):
            continue
        after = words[index + 1]
        if after in _RANGE_WORDS and index + 2 < len(words):
            after = words[index + 2]
        if _is_number(after) and _value(after) > _value(word):
            return True
    return False


def _fits(read: Words, known: Words) -> bool:
    if read == known or "".join(read) == "".join(known):
        return True
    if _one_number_more(read, known) or _one_number_more(known, read):
        return True
    more = read[len(known) :]
    if read[: len(known)] != known or not more:
        return False
    number = _part_number(more)
    if number is not None:
        # The first part fits, alone or before a subtitle of a longer title; a part from 2 on is a sequel.
        return number == "1" and (len(more) <= 2 or len(known) >= 2)
    return len(known) >= 2


def _known_run(words: Words, known: frozenset[Words], joined: frozenset[str]) -> bool:
    """Whether ``words`` is one of the movie's names, written together or apart, with or without a leading article."""
    if words in known or "".join(words) in joined:
        return True
    rest = words[1:]
    return words[0] in _ARTICLES and bool(rest) and (rest in known or "".join(rest) in joined)


#: What ``_names_in_a_row`` found: a row with one of the movie's whole names in it, or a row of parts only.
WHOLE_ROW, PARTS_ROW = "whole", "parts"


def _names_in_a_row(read: Words, title: TitleInfo) -> str | None:
    """Whether the name is the movie's names one after the other: ``WHOLE_ROW`` when one of them is a whole name,
    ``PARTS_ROW`` when all are parts, None otherwise. A row of one name is what ``_fits`` judged already."""
    known, joined = known_forms(title), _known_joined(title)
    whole, whole_joined = whole_forms(title), _whole_joined(title)
    # rows[i]: for the rows that make up read[:i], whether one of them has a whole name among its names.
    rows: list[set[bool]] = [{False}] + [set() for _ in read]
    for end in range(1, len(read) + 1):
        for start in range(end):
            words = read[start:end]
            if not rows[start] or not _known_run(words, known, joined):
                continue
            is_whole = _known_run(words, whole, whole_joined)
            rows[end] |= {has_whole or is_whole for has_whole in rows[start]}
    if True in rows[-1]:
        return WHOLE_ROW
    return PARTS_ROW if rows[-1] else None


def _year_fits(title: TitleInfo, name: str | None) -> bool:
    """Whether the name carries a year that fits the movie's, at most one off as ``belongs`` allows; any year for a
    movie without one. No name or no year there: False."""
    year = parser.parse_movie(name).year if name else None
    if year is None:
        return False
    return title.year is None or abs(year - title.year) <= YEAR_TOLERANCE


def _a_collection(title: TitleInfo, parsed_title: str) -> bool:
    """Whether the name is a collection of parts the movie's own names are not."""
    known = known_forms(title)
    for words in _bare_forms(parsed_title):
        if any(word in _COLLECTION_WORDS and word not in _known_words(title) for word in words):
            return True
        if any(pair in _COLLECTION_PAIRS and not _known_pair(title, pair) for pair in pairwise(words)):
            return True
        if _has_range(words) and not any(_has_range(form) for form in known):
            return True
    return False


def _read_past_a_year(name: str | None, parsed_title: str | None) -> list[str]:
    """Titles read from the name with one year left out, where that reads more title words: the parser stops at the
    first year, and "Emil Beispiel 2011 und der Zauberstab" is a title of five words, not two."""
    if not name:
        return []
    words = len((parsed_title or "").split())
    tokens = [token for token in re.split(r"[._ ]+", name) if token]
    found: list[str] = []
    for index, token in enumerate(tokens[:-1]):
        if not _YEAR.fullmatch(token) or index == 0:
            continue
        again = parser.parse_movie(".".join(tokens[:index] + tokens[index + 1 :])).title
        if again and len(again.split()) > words:
            found.append(again)
    return found


def title_fits(title: TitleInfo, parsed_title: str | None, name: str | None = None) -> bool:
    """Whether a title read from a name fits one of the movie's names. True when nothing could be read. With ``name``,
    the name the title was read from, a year inside it is read past."""
    read = read_forms(parsed_title) if parsed_title else set()
    if not parsed_title or not read:
        return True
    if _a_collection(title, parsed_title):
        return False
    for longer in _read_past_a_year(name, parsed_title):
        read |= read_forms(longer)
    if any(_fits(words, form) for words in read for form in whole_forms(title)):
        return True
    parts = part_forms(title)
    if parts and _year_fits(title, name) and any(_fits(words, form) for words in read for form in parts):
        return True
    rows = {_names_in_a_row(words, title) for words in read}
    return WHOLE_ROW in rows or (PARTS_ROW in rows and _year_fits(title, name))


def name_fits(title: TitleInfo, names: Iterable[str | None]) -> bool | None:
    """For the import: whether the title read from one of the names (release, video) fits the movie. None when no title
    could be read from any of them: nothing to compare."""
    read = [
        (name, parsed) for name in names if name and (parsed := parser.parse_movie(name).title) and read_forms(parsed)
    ]
    if not read:
        return None
    return any(title_fits(title, parsed, name) for name, parsed in read)
