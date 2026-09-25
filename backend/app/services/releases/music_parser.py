"""Reading the name of a music release (part M2.1, decisions 1 to 9).

nexcrate's own reading, written from the forms measured on 3,647 names a real Lidarr grabbed (plan, "Gemessen") and
from the scene's naming rules as the research describes them. Nothing is taken from Lidarr's source (GPL-3.0): no
code, no pattern, no test.

Order of work
-------------
1. The ending of a file goes (``.nzb``, ``.rar``), then an appendix behind the group (``-xpost``), then the group:
   the last field after a hyphen that has no space next to it.
2. Bit depth and sample rate are made whole before anything is split: ``44-KHZ`` and ``44.1.KHZ`` are 44.1 kHz (the
   dot got lost on the way in 2,469 of 3,647 names), ``24-96`` and ``24.96`` are one feature.
3. Brackets are read on their own: a year, a catalogue number, features (``[FLAC 24-96]``), words of an edition.
4. **From the right**, word by word: whatever is a known feature or a year belongs to the features. The first word that
   is none ends it; everything left of it is the head. An album called "1999" or "Live" therefore loses nothing, and
   the reading never eats so far that the head has no album left.
5. The head is split into artist and album only as a **proposal**: at `` - ``, ``.-.``, ``--``, else at the first
   hyphen, and then it says ``unsure`` when the head has more than one. M3 maps it to the library.

A name of the scene's shape (year and group at the end) without a format is an MP3 release: the scene's rules put no
format into an MP3 name (55 of the measured names; Lidarr called 19 of them "Unknown"). The bitrate is not in the name.

A pure function: no network, no database, nothing raised. What is not understood comes back as far as it was read.
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from .parser import Revision

_I = re.IGNORECASE
_ENDINGS = re.compile(r"\.(nzb|rar|zip|7z|par2|torrent)$", _I)
#: What posters and indexers hang behind the group; never part of it.
_SUFFIX = re.compile(r"-(xpost|postbot|obfuscated|scrambled|repost|rp|nzbgeek|buymore|chamele0n)$", _I)
#: The group, with the scene's "internal" mark behind it in any of its spellings (``-GRP INT``, ``-GRP.iNT``).
_GROUP = re.compile(r"(?<! )-([A-Za-z0-9_]{2,24})(?:[ ._](int))?$", _I)
#: A hash in brackets at the very end is a poster's mark, no part of the name.
_HASH = re.compile(r"[ ._-]*\[[0-9a-f]{6,40}\]$", _I)
#: Dividers and symbols outside ASCII at the very end (a poster's star): nothing of the name, and they hide what stands
#: in front of them from the reading from the right.
_TRAILING = re.compile(r"(?:[ ._-]|[^\x00-\x7f\w])+$")
#: P2P posters put their name in brackets behind the features: ``... FLAC [POSTER]``.
_BRACKET_GROUP = re.compile(r"[ ._-]*\[([A-Za-z0-9_]{2,24})\]$")
_LAST_WORD = re.compile(r"([^-._ \[\]()]+)[\])]*$")
#: The scene's ending: the year as the last field in front of the group.
_TAIL_YEAR = re.compile(r"(?<! )-\d{4}$")
#: "1 file", "als 1 flac": the album as one file (decision 14), in the spellings posters use. Never "Vol 1 FLAC".
_ONE_FILE = re.compile(
    r"(?<![0-9A-Za-z])(?:(?:als|as|elk|each|in)[ ._-]1[ ._-]?(?:file|flac)|1[ ._-]?file)(?![A-Za-z])", _I
)
_KHZ = re.compile(r"(?<![0-9A-Za-z])(44|48|88|96|176|192|352|384)(?:[._-]([124]))?[-._ ]?khz(?![A-Za-z])", _I)
_BITS_KHZ = re.compile(r"(?<![0-9A-Za-z])(16|24|32)[-._ ](44|48|88|96|176|192)(?:[._]([124]))?(?![0-9A-Za-z])")
_BITS = re.compile(r"^(16|24|32)[-_ ]?bits?$|^(16|24|32)b$", _I)
_BRACKET = re.compile(r"\[([^\[\]]{0,80})\]|\(([^()]{0,80})\)")
_CATALOGUE = re.compile(r"^(?=[^a-z]*\d)[A-Z0-9][A-Z0-9 ._/-]{3,24}$")
_WORDS = re.compile(r"[^-._ ]+")
#: The scene's fields: divided by a hyphen that has no space next to it.
_FIELDS = re.compile(r"(?:[^-]|(?<= )-|-(?= ))+")
_DISCS = re.compile(r"^(\d{1,2})[x]?(cd|lp|dvd|sacd|disc|discs)s?$", _I)
_DISC_PART = re.compile(r"^(cd|disc|disk)(\d{1,2})$", _I)
_BITRATE = re.compile(r"^(64|96|112|128|160|192|224|256|320)(k|kbps|kbit)?$|^(v[0-9])$", _I)
_VA = re.compile(r"^(va|v\.a\.|various[ ._]artists?)(?=$|[-._ ])", _I)
#: Placeholders without a dot or a hyphen, so they stay one word: the rate in tenths of a kHz. Written as escapes:
#: a raw control character in the source is invisible in every diff.
_PLACEHOLDER_KHZ = "\x01KHZ{}\x02"
_PLACEHOLDER_PAIR = "\x01PAIR{}x{}\x02"
_PLACED = re.compile(r"^\x01(KHZ|PAIR)([0-9]+)(?:x([0-9]+))?\x02$")

FORMATS = {
    "flac": "FLAC",
    "webflac": "FLAC",
    "flac24": "FLAC",
    "mp3": "MP3",
    "aac": "AAC",
    "m4a": "AAC",
    "alac": "ALAC",
    "ogg": "Vorbis",
    "vorbis": "Vorbis",
    "opus": "Opus",
    "wav": "WAV",
    "ape": "APE",
    "wv": "WavPack",
    "wavpack": "WavPack",
    "wma": "WMA",
    "dsd": "DSD",
    "dsf": "DSD",
}
LOSSLESS = ("FLAC", "ALAC", "WAV", "APE", "WavPack", "DSD")
#: The scene's source words and what shops are called in p2p names. ``kind`` is what the source says about the album.
SOURCES = {
    "web": "WEB",
    "webflac": "WEB",
    "qobuz": "WEB",
    "tidal": "WEB",
    "deezer": "WEB",
    "bandcamp": "WEB",
    "hdtracks": "WEB",
    "cd": "CD",
    "cdr": "CD",
    "cda": "CD",
    "cds": "CD",
    "cdm": "CD",
    "cdep": "CD",
    "sacd": "SACD",
    "vinyl": "Vinyl",
    "vls": "Vinyl",
    "lp": "Vinyl",
    "dvd": "DVD",
    "dvda": "DVD",
    "bluray": "Blu-ray",
    "bd": "Blu-ray",
    "tape": "Tape",
    "cassette": "Tape",
    "mc": "Tape",
    "fm": "Radio",
    "dab": "Radio",
    "sat": "Radio",
    "radio": "Radio",
    "sbd": "Soundboard",
    "mag": "Magazine",
}
SOURCE_KINDS = {"cds": "single", "cdm": "single", "vls": "single", "cdep": "ep"}
KINDS = {
    "ep": "ep",
    "single": "single",
    "ost": "soundtrack",
    "soundtrack": "soundtrack",
    "live": "live",
    "bootleg": "bootleg",
    "promo": "promo",
    "advance": "promo",
    "demo": "demo",
    "mixtape": "mixtape",
    "sampler": "sampler",
    "compilation": "compilation",
}
EDITIONS = {
    "deluxe": "deluxe",
    "remastered": "remastered",
    "remaster": "remastered",
    "expanded": "expanded",
    "anniversary": "anniversary",
    "reissue": "reissue",
    "limited": "limited",
    "bonus": "bonus",
    "special": "special",
    "collectors": "collectors",
    "boxset": "box",
    "box": "box",
    "retail": "retail",
    "digipak": "digipak",
    "complete": "complete",
}
#: Words that belong to an edition and say nothing themselves.
FILLERS = {"edition", "version", "set", "the", "cbr", "vbr", "lossless", "hires", "hi-res", "stereo", "nfo"}
#: Several albums in one release. Not "anthology": that is what one album is called, often a double one (found on
#: real names, 18.09.2026: Lidarr had grabbed it, the profile refused it).
SEVERAL = {"discography", "discografia", "diskografie", "discographie"}
REPEATS = {"proper", "repack", "rerip", "real", "dirfix", "nfofix", "readnfo", "int", "internal"}
#: Only in capitals, as the scene writes them: "de" and "it" are words in many languages.
COUNTRIES = frozenset(
    ("DE", "GER", "AT", "CH", "FR", "IT", "ES", "NL", "PL", "SE", "NO", "DK", "FI", "JP", "KR", "RU", "UK", "US", "EU")
)
#: A feature of the release: one file with a cue sheet instead of tracks.
WARNINGS = {"cue": "cue", "image": "cue", "1file": "cue"}
#: Words of the title itself, wherever they stand: what nexcrate does not handle, or what is not the album asked for.
TITLE_WARNINGS = {
    "audiobook": "audiobook",
    "hoerbuch": "audiobook",
    "hörbuch": "audiobook",
    "hoerspiel": "audiobook",
    "hörspiel": "audiobook",
    "tribute": "tribute",
    "karaoke": "karaoke",
}
YEAR_FIRST = 1930


@dataclass(frozen=True)
class ParsedAlbum:
    release_title: str
    #: The text in front of the features, as it stands in the name.
    head: str = ""
    #: A proposal only (decision 3); ``unsure`` when the head has more than one hyphen and no clearer divider.
    artist: str = ""
    album: str = ""
    unsure: bool = False
    various_artists: bool = False
    #: The first year is the album's, the last before the group the edition's (decision 6); equal when there is one.
    year: int | None = None
    edition_year: int | None = None
    #: ``FLAC``, ``MP3``, ``AAC``, ``ALAC``, ``Vorbis``, ``Opus``, ``WAV``, ``APE``, ``WavPack``, ``WMA``, ``DSD``.
    format: str | None = None
    #: True when the format is not in the name and follows from the scene's shape (decision 4).
    format_assumed: bool = False
    #: ``320``, ``256``, ..., ``V0``, ``V2``: only what the name says.
    bitrate: str | None = None
    bit_depth: int | None = None
    sample_rate_khz: float | None = None
    source: str | None = None
    media_count: int | None = None
    disc_part: int | None = None
    editions: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ()
    several_albums: bool = False
    country: str | None = None
    catalogue_number: str | None = None
    group: str | None = None
    suffix: str | None = None
    revision: Revision = field(default_factory=Revision)
    #: ``cue``, ``audiobook``, ``tribute``, ``karaoke``.
    warnings: tuple[str, ...] = ()
    #: ``scene``, ``p2p`` or ``unknown``.
    shape: str = "unknown"


@dataclass
class _Found:
    years: list[int] = field(default_factory=list)
    format: str | None = None
    bitrate: str | None = None
    bit_depth: int | None = None
    khz: float | None = None
    source: str | None = None
    media_count: int | None = None
    disc_part: int | None = None
    editions: list[str] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)
    several: bool = False
    country: str | None = None
    catalogue: str | None = None
    repeats: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _khz(whole: str, fraction: str | None) -> float:
    if fraction:
        return float(f"{whole}.{fraction}")
    return {"44": 44.1, "88": 88.2, "176": 176.4}.get(whole, float(whole))


def _add(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _feature(word: str, found: _Found, last_year: int) -> bool:
    """Whether ``word`` is a feature; if so it is noted in ``found``. Words come in any case, as they stand."""
    placed = _PLACED.match(word)
    if placed:
        if placed.group(1) == "KHZ":
            found.khz = found.khz or int(placed.group(2)) / 10
        else:
            found.bit_depth = found.bit_depth or int(placed.group(2))
            found.khz = found.khz or int(placed.group(3)) / 10
        return True
    low = word.lower()
    if word.isdigit() and len(word) == 4 and YEAR_FIRST <= int(word) <= last_year:
        found.years.insert(0, int(word))
        return True
    bits = _BITS.match(word)
    if bits:
        found.bit_depth = found.bit_depth or int(bits.group(1) or bits.group(2))
        return True
    known = False
    if low in FORMATS:
        # A name that says two formats ("FLAC ... MP3") is read as the better one, as Lidarr does.
        if found.format is None or (found.format not in LOSSLESS and FORMATS[low] in LOSSLESS):
            found.format = FORMATS[low]
        if low == "flac24":
            found.bit_depth = found.bit_depth or 24
        known = True
    if low in SOURCES:
        found.source = found.source or SOURCES[low]
        if low in SOURCE_KINDS:
            _add(found.kinds, SOURCE_KINDS[low])
        known = True
    if known:
        return True
    if low in KINDS:
        _add(found.kinds, KINDS[low])
        return True
    if low in EDITIONS:
        _add(found.editions, EDITIONS[low])
        return True
    if low in SEVERAL:
        found.several = True
        return True
    if low in REPEATS:
        _add(found.repeats, low)
        return True
    if low in WARNINGS:
        _add(found.warnings, WARNINGS[low])
        return True
    if word in COUNTRIES:
        found.country = found.country or word
        return True
    discs = _DISCS.match(word)
    if discs:
        found.media_count = found.media_count or int(discs.group(1))
        return True
    part = _DISC_PART.match(word)
    if part:
        found.disc_part = found.disc_part or int(part.group(2))
        return True
    rate = _BITRATE.match(word)
    if rate:
        found.bitrate = found.bitrate or (rate.group(3) or rate.group(1)).upper()
        return True
    return low in FILLERS


def _hard(found: _Found) -> bool:
    """Whether what was read is a feature no album is called after: a format, a source, a rate, a year, discs."""
    return bool(
        found.format or found.source or found.years or found.bit_depth or found.khz or found.bitrate
        or found.media_count or found.disc_part or found.repeats or "cue" in found.warnings
    )  # fmt: skip


def _bracket(text: str, found: _Found, last_year: int) -> bool:
    """What stands in one pair of brackets; True when it was features, a year or a catalogue number, so it goes."""
    inner = text.strip()
    if not inner:
        return True
    words = _WORDS.findall(inner)
    probe = _Found()
    if words and all(_feature(word, probe, last_year) for word in words):
        for word in words:
            _feature(word, found, last_year)
        return True
    if _CATALOGUE.match(inner) and not (inner.isdigit() and len(inner) == 4):
        found.catalogue = found.catalogue or inner
        return True
    # "(Deluxe Edition)", "(20th Anniversary Edition)": an edition, though not every word is a feature.
    edition = [EDITIONS[word.lower()] for word in words if word.lower() in EDITIONS]
    if edition and len(words) <= 5:
        for name in edition:
            _add(found.editions, name)
        return True
    return False


_DIVIDER = re.compile(r" -- | - |\.-\.|_-_| \u2013 |--|-")


def _split_head(head: str) -> tuple[str, str, bool]:
    """At the first divider, whatever its spelling: in ``Artist-Album - Part Two`` the hyphen comes first. Unsure
    when a bare hyphen divides and another divider follows (an artist with a hyphen looks just like that)."""
    hit = _DIVIDER.search(head)
    if hit is None:
        return head, "", False
    artist, album = head[: hit.start()], head[hit.end() :]
    return artist, album, hit.group(0) == "-" and _DIVIDER.search(album) is not None


def _plain(text: str) -> str:
    """Dots and underscores between words are spaces when the text has no spaces of its own."""
    cleaned = text.strip(" -._")
    if " " not in cleaned:
        cleaned = re.sub(r"[._]+", " ", cleaned)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def _revision(repeats: list[str]) -> Revision:
    version = 2 if any(word in ("proper", "repack", "rerip") for word in repeats) else 1
    repack = "repack" in repeats or "rerip" in repeats
    return Revision(version=version, real=1 if "real" in repeats else 0, repack=repack)


def parse_album(name: str, *, today: date | None = None) -> ParsedAlbum:
    last_year = (today or datetime.now(UTC).date()).year + 1
    # "&39;" is an apostrophe an indexer broke; html.unescape knows the whole ones.
    title = unicodedata.normalize("NFC", html.unescape((name or "").replace("&39;", "'"))).strip()
    title = _ENDINGS.sub("", title).strip(" -_;,")
    text = title
    suffix = None
    hit = _SUFFIX.search(text)
    if hit:
        suffix = hit.group(1)
        text = text[: hit.start()]
    text = _TRAILING.sub("", _HASH.sub("", text))
    group = None
    internal = False
    hit = _GROUP.search(text)
    # A year or a feature at the very end is no group: "... - Album-1999", "...-FLAC".
    if hit and not _feature(hit.group(1), _Found(), last_year):
        group = hit.group(1)
        internal = hit.group(2) is not None
        text = text[: hit.start()]
    else:
        # "... FLAC [POSTER]": a word in brackets at the end is the group only right behind a hard feature, so an
        # album called "Something [Remixes]" keeps its name.
        hit = _BRACKET_GROUP.search(text)
        before = _LAST_WORD.search(text[: hit.start()]) if hit else None
        probe = _Found()
        if (
            hit
            and before
            and not _feature(hit.group(1), _Found(), last_year)
            and _feature(before.group(1), probe, last_year)
            and _hard(probe)
        ):
            group = hit.group(1)
            text = text[: hit.start()]
    # The scene's ending (year, then group) says "scene" also where a poster wrote " - " between artist and album.
    scene_tail = group is not None and _TAIL_YEAR.search(text) is not None

    found = _Found()
    if internal:
        found.repeats.append("int")
    text = _KHZ.sub(lambda m: _PLACEHOLDER_KHZ.format(round(_khz(m.group(1), m.group(2)) * 10)), text)
    text = _BITS_KHZ.sub(
        lambda m: _PLACEHOLDER_PAIR.format(m.group(1), round(_khz(m.group(2), m.group(3)) * 10)), text
    )

    def brackets(match: re.Match[str]) -> str:
        inner = match.group(1) if match.group(1) is not None else match.group(2)
        if not _bracket(inner, found, last_year):
            return match.group(0)
        # A bracket that was a field of its own leaves no field behind: "-(KAT 123)-" becomes "-", not "- -".
        before = match.string[match.start() - 1] if match.start() else "-"
        after = match.string[match.end()] if match.end() < len(match.string) else "-"
        return "" if before in "-._" and after in "-._" else " "

    text = re.sub(r"-{2,}(?=[^-])", "-", _BRACKET.sub(brackets, text)) if _BRACKET.search(text) else text

    # From the right, until something is no feature or the head would lose its album. The scene divides fields with
    # hyphens and the words of a field with spaces: there a field is a feature only when every word of it is one, so
    # "Sommer Sampler" and "Grosse Erfolge Live" stay albums. Where dots divide (or nothing does, as in p2p names)
    # single words are read, in a field with spaces only the hard ones: a format, a source, a year, never "Live".
    end = len(text)
    tail = _Found()

    def keeps_album(start: int) -> bool:
        rest = text[:start].rstrip(" -._")
        return bool(rest) and (bool(_split_head(rest)[1]) or not _split_head(text[:end].rstrip(" -._"))[1])

    for part in reversed(list(_FIELDS.finditer(text))):
        words = list(_WORDS.finditer(part.group(0)))
        if not words:
            continue
        probe = _Found()
        if all(_feature(word.group(0), probe, last_year) for word in words) and keeps_album(part.start()):
            for word in reversed(words):
                _feature(word.group(0), tail, last_year)
            end = part.start()
            continue
        spaced = " " in part.group(0).strip()
        for word in reversed(words):
            probe = _Found()
            if not _feature(word.group(0), probe, last_year) or (spaced and not _hard(probe)):
                break
            if not keeps_album(part.start() + word.start()):
                break
            _feature(word.group(0), tail, last_year)
            end = part.start() + word.start()
        break
    for values in (tail.editions, tail.kinds, tail.repeats, tail.warnings):
        values.reverse()
    head = text[:end].strip(" -._")

    # What the tail read counts after the brackets, the tail's years before them (they stand further left).
    years = tail.years + found.years if tail.years else found.years
    for key in ("format", "bitrate", "bit_depth", "khz", "source", "media_count", "disc_part", "country"):
        if getattr(found, key) is None:
            setattr(found, key, getattr(tail, key))
    for key in ("editions", "kinds", "repeats", "warnings"):
        for value in getattr(tail, key):
            _add(getattr(found, key), value)
    found.several = found.several or tail.several
    for word in _WORDS.findall(title):
        if word.lower() in TITLE_WARNINGS:
            _add(found.warnings, TITLE_WARNINGS[word.lower()])
        if word.lower() in SEVERAL:
            found.several = True
    if _ONE_FILE.search(title):
        _add(found.warnings, "cue")

    various = bool(_VA.match(head))
    artist, album, unsure = _split_head(head)
    first = _DIVIDER.search(head)
    p2p = first is not None and first.group(0) != "-" and "." not in first.group(0)
    shape = "scene" if group and years and (not p2p or scene_tail) else "p2p" if p2p else "unknown"
    fmt = found.format
    assumed = False
    if fmt is None and shape == "scene":
        fmt, assumed = "MP3", True
    return ParsedAlbum(
        release_title=title,
        head=head,
        artist="Various Artists" if various else _plain(artist),
        album=_plain(album),
        unsure=unsure and not various,
        various_artists=various,
        year=years[0] if years else None,
        edition_year=years[-1] if years else None,
        format=fmt,
        format_assumed=assumed,
        bitrate=found.bitrate if fmt not in LOSSLESS else None,
        bit_depth=found.bit_depth,
        sample_rate_khz=found.khz,
        source=found.source,
        media_count=found.media_count,
        disc_part=found.disc_part,
        editions=tuple(found.editions),
        kinds=tuple(found.kinds),
        several_albums=found.several,
        country=found.country,
        catalogue_number=found.catalogue,
        group=group,
        suffix=suffix,
        revision=_revision(found.repeats),
        warnings=tuple(found.warnings),
        shape=shape,
    )
