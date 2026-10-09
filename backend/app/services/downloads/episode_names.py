"""Episode names as evidence: the name a video carries behind its number, against TMDB's names of the series.

A pack that counts differently than TMDB (a double episode in one file, an episode TMDB splits or joins) still names
its episodes: ``The.Show.S07E13.Das.Ultimatum.GERMAN.1080p`` is TMDB's ``S07E12`` "Das Ultimatum", whatever the
number says. ``episodes.assign`` asks this module what a name says; it decides what follows from it.

* **What is read:** the words behind the numbering form of the file name, and of the folder name when that folder
  holds this one video. A name is looked for at the start of those words, the longest name first; another may follow
  it (``S01E01E02.Name.One.und.Name.Two``). The features after it (``GERMAN.1080p``) are simply not a name.
* **Compared** through ``schreibweisen`` (umlauts as ae, as the base letter, dropped; "&" as "and" and "und"), with
  number words and Roman numerals as digits, and without spaces: "Wieder-sehen" is "Wiedersehen". The account
  language's name and TMDB's English name of an episode both count.
* **A name two episodes share says nothing**, and neither does a common name (``PLAIN``: "Pilot", "Teil 1",
  "Folge 12", a name of fewer than ``PLAIN_LENGTH`` letters): it is marked ``plain`` and never decides alone.
* **File and folder disagree:** the video has no name.

Pure: no database, no disk.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .. import schreibweisen
from ..series.release_match import Numbering

#: Words a common name is made of; a name of these and digits only is plain.
PLAIN_WORDS = frozenset(
    {
        "pilot", "pilotfolge", "pilotfilm", "teil", "part", "folge", "episode", "episoden", "kapitel", "chapter",
        "finale", "final", "staffelfinale", "season", "staffel", "special", "specials", "tba", "tbd", "untitled",
        "unbenannt", "unknown", "prolog", "prologue", "epilog", "epilogue", "intro", "introduction", "einleitung",
        "the", "der", "die", "das", "a", "an", "of", "von", "und", "and", "end", "ende", "anfang", "beginning",
        # Features that follow a name in a release: an episode called so would be read off every name.
        "german", "english", "deutsch", "dl", "multi", "dual", "web", "webrip", "webdl", "bluray", "hdtv", "remux",
        "proper", "repack", "internal", "dubbed", "subbed", "uhd", "hdr", "x264", "x265", "hevc", "complete",
    }
)  # fmt: skip
#: A name of fewer letters is plain.
PLAIN_LENGTH = 5
#: Words that may stand between two names in one file.
CONNECTORS = frozenset({"and", "und", "plus"})
#: The most names one video is read for.
MOST_NAMES = 4
#: The most words a name is looked for in.
MOST_WORDS = 24

_ENGLISH_NUMBERS = (
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen",
    "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty",
)  # fmt: skip
_GERMAN_NUMBERS = (
    "eins", "zwei", "drei", "vier", "fuenf", "sechs", "sieben", "acht", "neun", "zehn", "elf", "zwoelf", "dreizehn",
    "vierzehn", "fuenfzehn", "sechzehn", "siebzehn", "achtzehn", "neunzehn", "zwanzig",
)  # fmt: skip
_NUMBER_WORDS = {
    **{word: str(number) for number, word in enumerate(_ENGLISH_NUMBERS, 1)},
    **{word: str(number) for number, word in enumerate(_GERMAN_NUMBERS, 1)},
    "fuenf": "5", "funf": "5", "fnf": "5", "zwoelf": "12", "zwolf": "12", "zwlf": "12",
    # Roman numerals from two on: "I", "V" and "X" are too often a word or a name.
    "ii": "2", "iii": "3", "iv": "4", "vi": "6", "vii": "7", "viii": "8", "ix": "9", "xi": "11", "xii": "12",
    "xiii": "13", "xiv": "14", "xv": "15",
    "pt": "part",
}  # fmt: skip


def _word(word: str) -> str:
    return _NUMBER_WORDS.get(word, word)


#: "Teil2", "Part2" written as one word.
_GLUED_PART = re.compile(r"(?:teil|part)(\d+)")


def _parts(words: list[str]) -> list[str]:
    """"Teil 1", "Part 1", "Teil1" and TMDB's "(1)" alike: the number alone."""
    found: list[str] = []
    for index, word in enumerate(words):
        glued = _GLUED_PART.fullmatch(word)
        if glued is not None:
            found.append(glued[1])
        elif word in ("teil", "part") and index + 1 < len(words) and words[index + 1].isdigit():
            continue
        else:
            found.append(word)
    return found


def forms(text: str | None) -> list[list[str]]:
    """The word lists of a text, one per spelling form of ``schreibweisen.keys``, words normalised."""
    return [_parts([_word(word) for word in key.split()]) for key in schreibweisen.keys(text)]


def is_plain(words: Sequence[str]) -> bool:
    joined = "".join(words)
    return len(joined) < PLAIN_LENGTH or all(word in PLAIN_WORDS or word.isdigit() for word in words)


@dataclass(frozen=True)
class Index:
    """The names of a series: each spelling, without spaces, to the episodes that carry it."""

    names: dict[str, frozenset[int]]
    #: The spellings that are plain.
    plain: frozenset[str]


def index(numbering: Numbering) -> Index:
    found: dict[str, set[int]] = defaultdict(set)
    plain: set[str] = set()
    for row in numbering.episodes.values():
        for name in dict.fromkeys((row.name, row.name_en)):
            for words in forms(name):
                if not words:
                    continue
                joined = "".join(words)
                found[joined].add(row.id)
                if is_plain(words):
                    plain.add(joined)
    return Index({key: frozenset(ids) for key, ids in found.items()}, frozenset(plain))


@dataclass(frozen=True)
class Named:
    #: The episodes the names name, in their order in the text.
    episode_ids: tuple[int, ...]
    #: Every name found is plain.
    plain: bool


def _longest(words: Sequence[str], names: Index) -> tuple[int, str] | None:
    """How many words the longest name at the start of ``words`` takes, and its spelling."""
    for count in range(min(len(words), MOST_WORDS), 0, -1):
        joined = "".join(words[:count])
        if joined in names.names:
            return count, joined
    return None


#: A word that ends a name in a release: languages, editions, sound, resolutions, sources, codecs, services, a year.
_ENDS_A_NAME = re.compile(
    r"german|deutsch|ger|english|eng|french|dl|dual|multi|ml|dubbed|subbed|synced|uncut|unrated|extended|remastered"
    r"|directors|doku|docu|complete|internal|proper|repack\d?|rerip|read|nfo|readnfo|real"
    r"|ac3d?|e?ac3|aac\d*|ddp?\d*|dts\w*|truehd|atmos|flac|mp3"
    r"|\d{3,4}[pi]|4k|uhd|hdr\d*|sdr|dv|hd|sd|fhd|ws|fs"
    r"|web|webrip|webdl|webhd|hdtv|pdtv|sdtv|dvd\w*|bd|bdrip|brrip|bluray|remux"
    r"|x26[45]|h26[45]|h|hevc|avc|xvid|divx|amazonhd|amzn|nf|dsnp|hmax|atvp|itunes"
    r"|(?:19|20)\d{2}"
)


def _ends_here(rest: Sequence[str], names: Index) -> bool:
    """Whether a name may end where ``rest`` begins: at the end, before a feature, or before another name.

    ⚠️ Measured on the owner's library (09.10.2026): "Der Magier-Kodex Teil 1" began with "Der Magier", another
    episode's name. A name followed by a word that is neither a feature nor another name is a shorter name inside a
    longer one; it says nothing.
    """
    if not rest or rest[0] in CONNECTORS or _ENDS_A_NAME.fullmatch(rest[0]):
        return True
    return _longest(rest, names) is not None


def _read(words: list[str], names: Index) -> Named | None:
    ids: list[int] = []
    plain = True
    position = 0
    for _ in range(MOST_NAMES):
        while ids and position < len(words) and words[position] in CONNECTORS:
            position += 1
        found = _longest(words[position:], names)
        if found is None:
            break
        count, joined = found
        if not _ends_here(words[position + count :], names):
            break
        episodes = names.names[joined]
        if len(episodes) != 1:
            # A name two episodes share: the text says nothing about which one.
            return None if not ids else Named(tuple(ids), plain)
        (episode_id,) = episodes
        if episode_id in ids:
            break
        ids.append(episode_id)
        plain = plain and joined in names.plain
        position += count
    return Named(tuple(ids), plain) if ids else None


def read(after: str, names: Index) -> Named | None:
    """What the words behind a number name; None without a name of the series."""
    best: Named | None = None
    for words in forms(after):
        found = _read(words, names)
        if found is not None and (best is None or len(found.episode_ids) > len(best.episode_ids)):
            best = found
    return best


def of_texts(afters: Iterable[str], names: Index) -> Named | None:
    """The name of a video from what stands behind the number in its file name and its folder name. None when neither
    names an episode, or when they name different ones."""
    found = [named for named in (read(after, names) for after in afters) if named is not None]
    if not found:
        return None
    if any(set(item.episode_ids) != set(found[0].episode_ids) for item in found[1:]):
        return None
    return Named(found[0].episode_ids, all(item.plain for item in found))
