"""Reading a movie release name: title and year, group, quality, revision, edition, subtitles, languages.

nexcrate's own reading, written from the rules in the design notes and the owner's notes on how Radarr
reads a name. Nothing is taken from Radarr's source (GPL-3.0): no code, no pattern, no test. Where TRaSH's
scores depend on Radarr's reading, nexcrate reads the same way, because TRaSH tested its scores there.

Order of work
-------------
1. A file extension goes, then trailing dashes and underscores, then the characters ``<>?*|``. What is left
   is the release title that title formats see.
2. Title and year: the first year after at least one word that is not directly followed by another year. In
   tracker style (``Title.German.DL.2021``) a German tag before the year ends the title, unless the word
   before it is "The" or "Good".
3. The title in the release title becomes "A Movie" ("A.Movie" when the title has dots). Group, languages and
   edition are read from that masked title, so "German" in a movie's own title is no language.
4. Group: the last ``-Group`` that is no part of WEB-DL, Blu-Ray, DTS-HD and the like and has no resolution
   after it; a group in brackets at the very end wins over it.
5. Languages from the masked title with the group taken out, then the German DL and ML rule.
6. Quality from the whole name, word by word: the last source tag wins, resolution, remux and BR-DISK decide
   the rest.
7. Revision, edition and hardcoded subtitles.

Radarr's behaviour kept, because TRaSH's scores assume it
----------------------------------------------------------
* BDRip and BRRip count as Bluray encodes of their resolution.
* A WEB-DL other than 2160p, 1080p and 720p is WEBDL-480p; a resolution without a source is HDTV.
* BR-DISK has the resolution 1080; a disc name with x264, x265, MKV, Remux, 720p or "German ... DL" is none.
* ``DL`` (not the one of WEB-DL) adds the original language and ``ML`` adds original and English, only when
  German is the only language found.
* Hardcoded subtitles: a word ending in SUB or SUBS (not SOFTSUB, MULTISUB, HORRIBLESUB), HC or SUBBED.
* A proper or repack raises the version (to 2, or one above a ``v2`` tag); ``REAL`` in capitals counts.

Deviations from Radarr
----------------------
* No quality from a file extension: a name without source and resolution is Unknown. A current file's
  quality comes from its stored value instead.
* A name without a year still parses: the title ends at the first tag. Radarr cannot read such a name.
* A bare ``WEB`` is WEB-DL next to a codec, a resolution or a streaming service tag, or at the end.
* No hand kept list of release groups without a dash, no anime naming (subgroup in front, hash at the end).
* Languages: the tags of the languages TRaSH's formats and nexcrate's questions use, every language name
  written out, and a few common short tags. Radarr knows some more short tags.
* "Rogue Cut" counts as an edition.
* The title keeps its spelling; dots and underscores become spaces.
* The MULTi token (step 2c) is looked for with the movie's title masked; Radarr looks at the whole name, so a movie
  named Multi-something would count there.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import languages as lang
from . import qualities as q

_I = re.IGNORECASE

# .ts and .mk3d are video files too, as in Radarr's list of extensions.
_EXTENSION = re.compile(
    r"\.(?:mkv|mk3d|mp4|m4v|avi|wmv|mpe?g|(?:m2)?ts|iso|img|vob|webm|mov|flv|nzb|torrent|rar|zip)$", _I
)
_UNSAFE = re.compile(r"[<>?*|]")
_TOKEN = re.compile(r"[^\s._\-\[\]()]+")
_WORD = re.compile(r"[A-Za-z0-9]+")
_YEAR = re.compile(r"(?:18|19|20)\d{2}")


# --- Result ------------------------------------------------------------------------------ #


@dataclass(frozen=True)
class Revision:
    version: int = 1
    real: int = 0
    repack: bool = False


@dataclass(frozen=True)
class ParsedMovie:
    #: The movie title as the name spells it; None when the name has none.
    title: str | None
    year: int | None
    group: str | None
    quality: q.Quality
    revision: Revision
    edition: str | None
    hardcoded_subs: str | None
    #: As found in the name: Radarr's numbers, with ``ORIGINAL`` and ``UNKNOWN`` still unresolved.
    languages: tuple[int, ...]
    #: The release title with the movie title masked: what title formats see.
    release_title: str


# --- Title and year ---------------------------------------------------------------------- #

#: A tag that ends a title in tracker style, and the words that keep it inside the title.
_TRACKER_TAGS = frozenset({"german", "truefrench"})
_TRACKER_KEEP = frozenset({"the", "good"})
#: Words that end a title in a name without a year.
_TITLE_END_WORDS = frozenset(
    {
        "german",
        "ger",
        "dl",
        "ml",
        "french",
        "truefrench",
        "multi",
        "english",
        "eng",
        "dubbed",
        "subbed",
        "bluray",
        "blu",
        "bdrip",
        "brrip",
        "web",
        "webdl",
        "webrip",
        "hdtv",
        "dvd",
        "dvdrip",
        "remux",
        "uhd",
        "hdr",
        "x264",
        "x265",
        "h264",
        "h265",
        "hevc",
        "avc",
        "xvid",
        "proper",
        "repack",
        "extended",
        "unrated",
        "uncut",
        "remastered",
        "imax",
        "complete",
    }
)
_TITLE_END_SHAPE = re.compile(r"(?:\d{3,4}[pi]|[48]k)", _I)


def _ends_title(word: str) -> bool:
    return word.casefold() in _TITLE_END_WORDS or bool(_TITLE_END_SHAPE.fullmatch(word))


def _title_span(text: str) -> tuple[str | None, int | None, int, int]:
    """The title, the year and where the title sits in the text (start, end)."""
    tokens = list(_TOKEN.finditer(text))
    if not tokens:
        return None, None, 0, 0
    year_at: int | None = None
    for index in range(1, len(tokens)):
        if not _YEAR.fullmatch(tokens[index].group()):
            continue
        following = tokens[index + 1] if index + 1 < len(tokens) else None
        directly_followed = (
            following is not None
            and _YEAR.fullmatch(following.group()) is not None
            and following.start() - tokens[index].end() <= 1
        )
        if not directly_followed:
            year_at = index
            break
    limit = year_at if year_at is not None else len(tokens)
    end_at: int | None = None
    for index in range(1, limit):
        if (
            tokens[index].group().casefold() in _TRACKER_TAGS
            and tokens[index - 1].group().casefold() not in _TRACKER_KEEP
        ):
            end_at = index
            break
    if end_at is None:
        if year_at is not None:
            end_at = year_at
        else:
            end_at = next((index for index in range(1, len(tokens)) if _ends_title(tokens[index].group())), len(tokens))
    start, end = tokens[0].start(), tokens[end_at - 1].end()
    title = " ".join(text[start:end].replace(".", " ").replace("_", " ").split()) or None
    year = int(tokens[year_at].group()) if year_at is not None else None
    return title, year, start, end


def _masked(text: str, start: int, end: int) -> str:
    if end <= start:
        return text
    replacement = "A.Movie" if "." in text[start:end] else "A Movie"
    return text[:start] + replacement + text[end:]


# --- Release group ------------------------------------------------------------------------ #

#: Suffixes some posting tools add after the group.
_GROUP_JUNK = re.compile(
    r"(?:-(?:RP|NZBGeek|Obfuscated|Obfuscation|Scrambled|sample|postbot|xpost|WhiteRev|BUYMORE|AsRequested|4P|4Planet))+$",
    _I,
)
_DASHED = re.compile(r"-([A-Za-z0-9]+(?:-[A-Za-z0-9]+)?)(?=$|[._ \[\]()])")
_BRACKETED = re.compile(r"[._ -]\[([A-Za-z0-9]+)\]$")
_RESOLUTION_TAG = re.compile(r"(?:480|576|720|1080|2160)p", _I)
_LAST_WORD = re.compile(r"([A-Za-z0-9]+)$")
#: Words joined by a dash that are no group: WEB-DL, WEB-Rip, Blu-Ray, DTS-HD, DTS-X, DTS-MA, DTS-ES.
_COMPOUNDS = {"web": frozenset({"dl", "rip"}), "blu": frozenset({"ray"}), "dts": frozenset({"hd", "x", "ma", "es"})}
#: Language tags after a dash.
_NOT_A_GROUP = frozenset({"es", "en", "cat", "eng", "jap", "ger", "fra", "fre", "ita", "hdrip"})
_NO_GROUP_SHAPE = re.compile(r"(?:[se]\d+|[0-9a-f]{8}|\d+|tt\d{7,8})", _I)


def parse_group(masked: str) -> str | None:
    text = _GROUP_JUNK.sub("", masked.strip())
    bracketed = _BRACKETED.search(text)
    if bracketed:
        return bracketed.group(1)
    found: str | None = None
    for match in _DASHED.finditer(text):
        parts = match.group(1).split("-")
        previous = _LAST_WORD.search(text[: match.start()])
        previous_word = previous.group(1).casefold() if previous else ""
        if parts[0].casefold() in _COMPOUNDS.get(previous_word, frozenset()):
            parts = parts[1:]
        if not parts:
            continue
        value = "-".join(parts)
        if _RESOLUTION_TAG.search(text[match.end() :]):
            continue
        if value.casefold() in _NOT_A_GROUP or _NO_GROUP_SHAPE.fullmatch(value):
            continue
        if value.casefold() == "bit" and previous_word.isdigit():
            continue
        found = value
    return found


# --- Languages ---------------------------------------------------------------------------- #

#: Language names written out, found anywhere in the name.
_LANGUAGE_NAMES: tuple[tuple[str, int], ...] = (
    ("english", 1),
    ("spanish", 3),
    ("danish", 6),
    ("dutch", 7),
    ("japanese", 8),
    ("icelandic", 9),
    ("mandarin", 10),
    ("cantonese", 10),
    ("chinese", 10),
    ("korean", 21),
    ("russian", 11),
    ("romanian", 27),
    ("hindi", 26),
    ("arabic", 31),
    ("thai", 28),
    ("bulgarian", 29),
    ("polish", 12),
    ("vietnamese", 13),
    ("swedish", 14),
    ("norwegian", 15),
    ("finnish", 16),
    ("turkish", 17),
    ("portuguese", 18),
    ("brazilian", 30),
    ("hungarian", 22),
    ("hebrew", 23),
    ("ukrainian", 32),
    ("persian", 33),
    ("bengali", 34),
    ("slovak", 35),
    ("latvian", 36),
    ("latino", 37),
    ("tamil", 43),
    ("telugu", 45),
    ("malayalam", 48),
    ("kannada", 49),
    ("albanian", 50),
    ("afrikaans", 51),
    ("marathi", 52),
    ("tagalog", 53),
)
#: Two letter tags that count only in capitals, and not next to "SUB".
_CAPITAL_TAGS = {"EN": 1, "DE": 4, "ES": 3, "PL": 12, "CZ": 25, "LT": 24, "BG": 29, "SK": 35}
_CAPITAL_TAG = re.compile(r"\b(EN|DE|ES|PL|CZ|LT|BG|SK)\b")
_SUB_BEFORE = re.compile(r"SUB[^A-Za-z0-9]$", _I)
_SUB_AFTER = re.compile(r"^[^A-Za-z0-9]SUB", _I)
_DTS_BEFORE = re.compile(r"DTS[._ -]$", _I)
#: Tags in any case.
_LANGUAGE_TAGS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"\beng\b", _I), 1),
    (re.compile(r"\b(?:ita|italian)\b", _I), 5),
    (re.compile(r"\b(?:swiss)?german\b|videomann|\bger[. ]dub|\bger\b", _I), 4),
    (re.compile(r"flemish", _I), 19),
    (re.compile(r"greek", _I), 20),
    (re.compile(r"\b(?:fr|vo|vf|vff|vfq|vfi|vf2|truefrench|french|fre|fra)\b", _I), 2),
    (re.compile(r"\b(?:rus|ru)\b", _I), 11),
    (re.compile(r"\b(?:hun|hundub)\b", _I), 22),
    (re.compile(r"\b(?:hebdub|hebdubbed)\b", _I), 23),
    (re.compile(r"\b(?:español|castellano)\b", _I), 3),
    (re.compile(r"\b(?:catalan|catala|catalán|català)\b", _I), 38),
    (re.compile(r"\bvie\b", _I), 13),
    (re.compile(r"\bjap\b", _I), 8),
    (re.compile(r"\bkor\b", _I), 21),
    (re.compile(r"\[(?:chs|cht|big5|gb)\]|简|繁|字幕", _I), 10),
    (re.compile(r"\b(?:dublado|pt-br)\b", _I), 30),
    (re.compile(r"\b(?:orig|original)\b", _I), lang.ORIGINAL),
)
_DL = re.compile(r"\bDL\b", _I)
_WEB_BEFORE = re.compile(r"WEB[-_. ]?$", _I)
_ML = re.compile(r"\bML\b", _I)


def parse_languages(text: str) -> tuple[int, ...]:
    """Languages in a masked release title, with ``ORIGINAL`` for DL and ML and ``UNKNOWN`` when there is none."""
    found: list[int] = []
    lower = text.lower()
    for word, language in _LANGUAGE_NAMES:
        if word in lower:
            found.append(language)
    for match in _CAPITAL_TAG.finditer(text):
        before, after = text[: match.start()], text[match.end() :]
        if _SUB_BEFORE.search(before) or _SUB_AFTER.search(after):
            continue
        if match.group(1) == "ES" and _DTS_BEFORE.search(before):
            continue
        found.append(_CAPITAL_TAGS[match.group(1)])
    for pattern, language in _LANGUAGE_TAGS:
        if pattern.search(text):
            found.append(language)
    unique = list(dict.fromkeys(found))
    if not unique:
        return (lang.UNKNOWN,)
    if unique == [lang.BY_ID[4].id]:
        dual = any(not _WEB_BEFORE.search(text[: match.start()]) for match in _DL.finditer(text))
        if dual:
            unique.append(lang.ORIGINAL)
        elif _ML.search(text):
            unique.extend((lang.ORIGINAL, 1))
    return tuple(dict.fromkeys(unique))


def aggregate_languages(
    found: tuple[int, ...], original_iso: str | None, *, unknown_for_double: bool = True
) -> tuple[int, ...]:
    """Languages of a release: its original language fills in what the name does not say.

    Nothing or only Unknown found: the original language. The ``ORIGINAL`` of DL and ML becomes the original
    language; is that one already there, Radarr adds Unknown in its place, which ``unknown_for_double`` keeps.
    ⚠️ Sonarr does not: a German DL release of a German series is German to it, nothing else (measured
    16.09.2026 over 36 real names). Series therefore pass false.
    """
    original = lang.radarr_id(original_iso)
    languages = list(found)
    if not languages or languages == [lang.UNKNOWN]:
        languages = [original]
    if lang.ORIGINAL in languages:
        languages = [language for language in languages if language != lang.ORIGINAL]
        if original not in languages:
            languages.append(original)
        elif unknown_for_double:
            languages.append(lang.UNKNOWN)
    return tuple(dict.fromkeys(languages))


_MULTI = re.compile(r"(?<![A-Za-z0-9])multi(?![A-Za-z0-9])", _I)


def has_multi_token(release_title: str) -> bool:
    """Whether a masked release title carries the MULTi token: the word multi on its own, in any case.

    MULTISUBS and the like are other words. Looked for with the movie's title masked, so a movie named Multi-something
    is no MULTi release.
    """
    return _MULTI.search(release_title) is not None


def with_multi_languages(found: tuple[int, ...], multi: tuple[int, ...]) -> tuple[int, ...]:
    """The languages found in a name together with an indexer's MULTi languages, in Radarr's order.

    A name without a language of its own (nothing or Unknown) gets exactly the MULTi languages; otherwise they are
    added after the ones found. Unknown, Any and Original in ``multi`` are left out. The original language fills in
    afterwards, in ``aggregate_languages``.
    """
    wanted = [language for language in multi if language not in (lang.UNKNOWN, lang.ANY, lang.ORIGINAL)]
    if not wanted:
        return found
    if not found or found == (lang.UNKNOWN,):
        return tuple(dict.fromkeys(wanted))
    return tuple(dict.fromkeys([*found, *wanted]))


# --- Quality ------------------------------------------------------------------------------ #


@dataclass(frozen=True)
class _Word:
    text: str
    lower: str
    start: int
    end: int


def _words(text: str) -> list[_Word]:
    return [_Word(match.group(), match.group().lower(), match.start(), match.end()) for match in _WORD.finditer(text)]


def _joined(words: list[_Word], index: int, text: str) -> str | None:
    """A word and the next one when a single separator stands between them: ``Blu-Ray`` gives ``bluray``."""
    if index + 1 >= len(words):
        return None
    gap = text[words[index].end : words[index + 1].start]
    return words[index].lower + words[index + 1].lower if len(gap) == 1 and gap in "-_. " else None


#: Source tags as whole words, and as two words joined by one separator.
_SOURCE_WORDS: dict[str, str] = {
    **dict.fromkeys(
        ("bluray", "mbluray", "hddvd", "uhdbd", "uhd2bd", "bdiso", "bdmux", "bd25", "bd50", "brdisk"), "bluray"
    ),
    **dict.fromkeys(
        (
            "webdl",
            "webdlmux",
            "amazonhd",
            "amazonsd",
            "ituneshd",
            "maxdomehd",
            "netflixhd",
            "netflixuhd",
            "webhd",
            "hbomaxhd",
            "disneyhd",
        ),
        "webdl",
    ),
    **dict.fromkeys(("webrip", "webmux"), "webrip"),
    "hdtv": "hdtv",
    **dict.fromkeys(("bdrip", "bdlight", "hddvdrip", "uhdbdrip", "brrip"), "bdrip"),
    **dict.fromkeys(("dvd", "dvdrip", "xvidvd"), "dvd"),
    **dict.fromkeys(("wsdsr", "dsr", "pdtv", "sdtv", "tvrip"), "tv"),
    "regional": "regional",
    **dict.fromkeys(("scr", "screener", "dvdscr", "dvdscreener"), "screener"),
    **dict.fromkeys(("ts", "telesync", "telesynch", "hdts", "pdvd", "tsrip", "hdtsrip"), "telesync"),
    **dict.fromkeys(("tc", "telecine", "hdtc"), "telecine"),
    **dict.fromkeys(("cam", "camrip", "newcam", "hdcam", "hdcamrip", "hqcam"), "cam"),
    **dict.fromkeys(("workprint", "wp"), "workprint"),
}
#: Source tags written as two words ("Blu-Ray", "WEB.DL"). Only these may be joined, so "HD.TV" stays two words.
_SOURCE_JOINS = frozenset(
    {
        "bluray",
        "mbluray",
        "hddvd",
        "brdisk",
        "webdl",
        "webrip",
        "hddvdrip",
        "hdts",
        "hdtc",
        "hdcam",
        "hdcamrip",
        "wsdsr",
    }
)
_DVD_R = re.compile(r"\d?x?m?dvd[r59]")
_REGIONAL_CODE = re.compile(r"r\d")
_CODEC_WORDS = frozenset({"x264", "h264", "x265", "h265", "avc", "hevc"})
_WEB_RESOLUTION = re.compile(r"\d{3,4}0p")
_SERVICE_TAGS = frozenset({"amzn", "nf", "dp", "atvp", "dsnp", "hmax", "hulu", "pcok", "pmtp"})


def _bare_web_is_webdl(words: list[_Word], index: int, text: str) -> bool:
    following = words[index + 1] if index + 1 < len(words) else None
    previous = words[index - 1] if index > 0 else None
    if following is None:
        return True
    if following.lower in _CODEC_WORDS or following.lower in ("ddp5", "dd5"):
        return True
    if following.lower in ("x", "h") and index + 2 < len(words) and words[index + 2].lower in ("264", "265"):
        return True
    if _WEB_RESOLUTION.fullmatch(following.lower):
        return True
    if previous is not None and _WEB_RESOLUTION.fullmatch(previous.lower):
        return True
    if (
        previous is not None
        and previous.lower == "hybrid"
        and index > 1
        and _WEB_RESOLUTION.fullmatch(words[index - 2].lower)
    ):
        return True
    return previous is not None and previous.lower in _SERVICE_TAGS and following.lower != "rip"


#: What only Radarr reads as a source: the cinema copies and the sizes of a disc. Sonarr has no such quality and reads
#: a name as if these words were not in it (measured on 20.09.2026, see ``parse_quality``).
_RADARR_ONLY_SOURCES = frozenset({"regional", "screener", "telesync", "telecine", "cam", "workprint"})
_RADARR_ONLY_WORDS = frozenset({"bd25", "bd50", "brdisk"})
_DVD_STANDARDS = frozenset({"ntsc", "pal"})


def _last_source(words: list[_Word], text: str, as_sonarr: bool = False) -> str | None:
    found: str | None = None
    index = 0
    while index < len(words):
        joined = _joined(words, index, text)
        if joined is not None and (joined in _SOURCE_JOINS or _DVD_R.fullmatch(joined)):
            if not as_sonarr:
                found = "dvdr" if _DVD_R.fullmatch(joined) else _SOURCE_WORDS[joined]
            elif _DVD_R.fullmatch(joined):
                # "DVD-R" holds the word DVD, and that is all Sonarr sees of it.
                found = "dvd"
            elif joined not in _RADARR_ONLY_WORDS and _SOURCE_WORDS[joined] not in _RADARR_ONLY_SOURCES:
                found = _SOURCE_WORDS[joined]
            index += 2
            continue
        word = words[index].lower
        if word == "web" and _bare_web_is_webdl(words, index, text):
            found = "webdl"
        elif word == "bd" and index < len(words) - 1:
            found = "bluray"
        elif _DVD_R.fullmatch(word):
            # Written as one word (DVDR, DVD9) Sonarr finds no source in it; a TV standard beside it makes it a DVD.
            if not as_sonarr:
                found = "dvdr"
            elif _has_word(words, *_DVD_STANDARDS):
                found = "dvd"
        elif _REGIONAL_CODE.fullmatch(word):
            if not as_sonarr:
                found = "regional"
        elif word == "ts" and text[words[index].end : words[index].end + 1] not in ("-", "_", ".", " "):
            # Radarr's TS needs a separator after it (TS[-_. ]): a file's ".ts" or a name ending in ".TS" is no
            # TELESYNC (measured against Radarr 6.3 on 24.09.2026).
            pass
        elif word in _SOURCE_WORDS and not (
            as_sonarr and (word in _RADARR_ONLY_WORDS or _SOURCE_WORDS[word] in _RADARR_ONLY_SOURCES)
        ):
            found = _SOURCE_WORDS[word]
        index += 1
    return found


_RESOLUTION_WORDS = {
    "360p": 360,
    "480p": 480,
    "480i": 480,
    "640x480": 480,
    "848x480": 480,
    "540p": 540,
    "576p": 576,
    "720p": 720,
    "1280x720": 720,
    "960p": 720,
    "1080p": 1080,
    "1080i": 1080,
    "1920x1080": 1080,
    "1440p": 1080,
    "fhd": 1080,
    "2160p": 2160,
    "3840x2160": 2160,
}
_FOUR_K_NEIGHBOURS = frozenset({"uhd", "hevc", "bd", "h265"})


def _resolution(words: list[_Word], text: str) -> int:
    """The first resolution in the name; a lone UHD or [4K] means 2160 when there is none."""
    for index, word in enumerate(words):
        if word.lower in _RESOLUTION_WORDS:
            return _RESOLUTION_WORDS[word.lower]
        if word.lower == "4k":
            neighbours = {
                words[index + 1].lower if index + 1 < len(words) else "",
                words[index - 1].lower if index else "",
            }
            if index + 2 < len(words) and words[index + 1].lower == "h" and words[index + 2].lower == "265":
                neighbours.add("h265")
            if neighbours & _FOUR_K_NEIGHBOURS:
                return 2160
    if any(word.lower == "uhd" for word in words) or "[4k]" in text.lower():
        return 2160
    return 0


def _has_word(words: list[_Word], *names: str) -> bool:
    wanted = set(names)
    return any(word.lower in wanted for word in words)


def _has_joined(words: list[_Word], text: str, *names: str) -> bool:
    wanted = set(names)
    return any(word.lower in wanted or _joined(words, index, text) in wanted for index, word in enumerate(words))


def _first_codec(words: list[_Word], text: str) -> str | None:
    for index, word in enumerate(words):
        joined = _joined(words, index, text)
        if joined == "xvid":
            return "xvid"
        if word.lower in ("x264", "h264", "xvidhd", "xvid", "divx"):
            return word.lower
    return None


_GLUED_BD = re.compile(r"bd(720|1080|2160)")
_BLURAY_WORDS = ("bluray", "bd", "hddvd")
_DISC_CODECS = ("avc", "hevc", "vc1", "mvc", "mpeg2", "bdmv", "iso")
_DISC_SIZE = re.compile(r"(?:bd|uhd)[-_. ]?(?:25|50|66|100|iso)", _I)
_GERMAN_DUAL_AFTER = re.compile(r"\bGerman\b.*\b[DM]L\b", _I)


def _german_remux(words: list[_Word], text: str) -> bool:
    """German remux naming: a year, then German, then later a disc codec and Blu-ray."""
    seen_year = False
    for index, word in enumerate(words):
        if re.fullmatch(r"\d{4}", word.lower):
            seen_year = True
        elif seen_year and word.lower == "german":
            rest = words[index + 1 :]
            rest_text = text[word.end :]
            codec = _has_joined(rest, rest_text, *_DISC_CODECS)
            bluray = _has_word(rest, "bluray") or re.search(r"blu-ray", rest_text, _I) is not None
            return codec and bluray
    return False


def _is_br_disk(words: list[_Word], text: str) -> bool:
    lowered = [word.lower for word in words]
    dvd_alone = any(word == "dvd" and not (index and lowered[index - 1] == "hd") for index, word in enumerate(lowered))
    if (
        dvd_alone
        or _has_word(
            words, "bdrip", "mkv", "xvid", "wmv", "d3g", "remux", "bdremux", "720p", "x264", "x265", "h264", "h265"
        )
        or _has_joined(words, text, "x264", "x265", "h264", "h265")
        or (_has_word(words, "1080p") and _has_word(words, "hevc"))
        or _GERMAN_DUAL_AFTER.search(text)
        or _german_remux(words, text)
    ):
        return False
    bluray = _has_joined(words, text, *_BLURAY_WORDS)
    if bluray and _has_joined(words, text, *_DISC_CODECS):
        return True
    complete = any(word.startswith("complete") for word in lowered) or _has_word(words, "disc", "disk")
    if complete and _has_joined(words, text, "bluray", "hddvd"):
        return True
    return _has_joined(words, text, "3dbd", "brdisk", "fullbluray") or bool(_DISC_SIZE.search(text))


def _remux(words: list[_Word], text: str) -> bool:
    return _has_joined(words, text, "remux", "bdremux", "uhdremux") or _german_remux(words, text)


def names_source(name: str) -> bool:
    """Whether the name says where it came from, not only its resolution.

    ``parse_quality`` makes a bare ``720p`` HDTV-720p, as Radarr's and Sonarr's parsers do. Their import trusts such a
    guess least and takes the source from the folder or the release name instead: ``tvr-friends-s01e01-720p.mkv`` in
    ``Friends.S01.German.DL.720p.BDRiP.x264-TvR`` is Bluray-720p there, not HDTV-720p.
    """
    text = name.replace("_", " ").strip()
    if not text:
        return False
    words = _words(text)
    return _last_source(words, text) is not None or _has_joined(words, text, "rawhd")


def parse_quality(name: str, as_sonarr: bool = False) -> q.Quality:
    """The quality a name carries, as Radarr reads it.

    ``as_sonarr`` reads it as Sonarr does where the two part ways. Sonarr has no BR-DISK, no cinema copies (CAM,
    TELESYNC, TELECINE, WORKPRINT, DVDSCR, REGIONAL) and no DVD-R, and it does not refuse such a name: it reads it as
    if those words were not there. Measured on 20.09.2026 against Sonarr 4.0.19 and Radarr 5 with 58 names each:
    ``…COMPLETE.BLURAY-GRP`` is BR-DISK in Radarr and Bluray-720p in Sonarr, ``…1080p.BluRay.AVC.DTS-HD.MA.5.1`` is
    Bluray-1080p, ``…CAM.x264`` is SDTV (from the codec), ``…DVDR`` is unknown, ``…DVD-R`` and ``…NTSC.DVDR`` are DVD.
    The series reader asks this way only for a name whose quality Sonarr does not know, so every other name is read
    exactly as before.
    """
    text = name.replace("_", " ").strip()
    if not text:
        return q.UNKNOWN_QUALITY
    words = _words(text)
    source = _last_source(words, text, as_sonarr)
    resolution = _resolution(words, text)
    remux = _remux(words, text)
    br_disk = not as_sonarr and _is_br_disk(words, text)
    codec = _first_codec(words, text)
    by_resolution = {2160: 2160, 1080: 1080, 720: 720, 576: 576}

    if _has_joined(words, text, "rawhd") and not br_disk:
        return q.named("Raw-HD")
    if source == "bluray":
        if br_disk:
            return q.named("BR-DISK")
        if codec in ("xvid", "divx"):
            return q.named("Bluray-480p")
        if resolution in (2160, 1080):
            return q.named(f"{'Remux' if remux else 'Bluray'}-{resolution}p")
        if resolution in (720, 576):
            return q.named(f"Bluray-{resolution}p")
        if resolution in (360, 480, 540):
            return q.named("Bluray-480p")
        return q.named("Remux-1080p" if remux else "Bluray-720p")
    if source in ("webdl", "webrip"):
        prefix = "WEBDL" if source == "webdl" else "WEBRip"
        if resolution in (2160, 1080, 720):
            return q.named(f"{prefix}-{resolution}p")
        if source == "webdl" and "[WEBDL]" in name:
            return q.named("WEBDL-720p")
        return q.named(f"{prefix}-480p")
    simple = {
        "screener": "DVDSCR",
        "cam": "CAM",
        "telesync": "TELESYNC",
        "telecine": "TELECINE",
        "workprint": "WORKPRINT",
        "regional": "REGIONAL",
    }
    if source in simple:
        return q.named(simple[source])
    if source == "hdtv":
        if _has_joined(words, text, "mpeg2"):
            return q.named("Raw-HD")
        if resolution in (2160, 1080, 720):
            return q.named(f"HDTV-{resolution}p")
        return q.named("HDTV-720p" if "[HDTV]" in name else "SDTV")
    if source == "bdrip":
        return (
            q.named(f"Bluray-{by_resolution[resolution]}p") if resolution in by_resolution else q.named("Bluray-480p")
        )
    if source == "dvdr":
        return q.named("DVD-R")
    if source == "dvd":
        return q.named("DVD")
    if source == "tv":
        if resolution == 1080 or "1080p" in text.lower():
            return q.named("HDTV-1080p")
        if resolution == 720 or "720p" in text.lower():
            return q.named("HDTV-720p")
        return q.named("HDTV-720p" if _has_joined(words, text, "hrws") else "SDTV")

    if remux and resolution in (480, 720, 1080, 2160):
        return q.named({480: "Bluray-480p", 720: "Bluray-720p", 1080: "Remux-1080p", 2160: "Remux-2160p"}[resolution])
    lowered_words = [word.lower for word in words]
    for word in lowered_words:
        glued_disc = _GLUED_BD.fullmatch(word)
        if glued_disc:
            value = int(glued_disc.group(1))
            if value in (1080, 2160):
                return q.named(f"{'Remux' if remux else 'Bluray'}-{value}p")
            return q.named("Bluray-720p")
    if resolution in (2160, 1080, 720):
        return q.named(f"HDTV-{resolution}p")
    if resolution in (360, 480, 540, 576):
        return q.named("SDTV")
    if codec == "x264":
        return q.named("SDTV")
    joined_text = "".join(lowered_words)
    for glued, quality in (
        ("bluray720p", "Bluray-720p"),
        ("bluray1080p", "Bluray-1080p"),
        ("bluray2160p", "Bluray-2160p"),
    ):
        if glued in joined_text:
            return q.named(quality)
    if _has_joined(words, text, "hdtv"):
        return q.named("HDTV-720p")
    if _has_joined(words, text, "sdtv"):
        return q.named("SDTV")
    return q.UNKNOWN_QUALITY


# --- Revision, edition, subtitles --------------------------------------------------------- #

_VERSION_TAG = re.compile(r"\d[-._ ]?v(\d)[-._ ]|\[v(\d)\]|(?:repack|rerip)(\d)", _I)
_PROPER = re.compile(r"\bproper\b", _I)
_REPACK = re.compile(r"\b(?:repack|rerip)\d?\b", _I)
_REAL = re.compile(r"\bREAL\b")


def parse_revision(name: str) -> Revision:
    text = name.replace("_", " ")
    tag = _VERSION_TAG.search(text)
    tagged = int(next(group for group in tag.groups() if group)) if tag else None
    version = tagged if tagged is not None else 1
    repack = False
    if _PROPER.search(text):
        version = tagged + 1 if tagged is not None else 2
    if _REPACK.search(text):
        version = tagged + 1 if tagged is not None else 2
        repack = True
    return Revision(version=version, real=len(_REAL.findall(name)), repack=repack)


_EDITION_NAMES = (
    r"(?:(?:Recut|Extended|Ultimate)[._ -])?"
    r"(?:Director'?s|Collector'?s|Theatrical|Ultimate|Extended|Despecialized"
    r"|(?:Special|Rogue|Final|Assembly|Imperial|Diamond|Signature|Hunter|Rekall)(?=[._ -](?:Cut|Edition|Version))"
    r"|\d{2,3}(?:th)?[._ -]Anniversary)"
    r"(?:[._ -](?:Cut|Edition|Version))?"
    r"(?:[._ -](?:Extended|Uncensored|Remastered|Unrated|Uncut|Open[._ -]?Matte|IMAX|Fan[._ -]?Edit))?"
)
_EDITION_SINGLE = r"Uncensored|Remastered|Unrated|Uncut|Open[._ -]?Matte|IMAX|Fan[._ -]?Edit|Restored|[234]in1"
_EDITION = re.compile(rf"\b(?:{_EDITION_NAMES}|{_EDITION_SINGLE})\b", _I)


def parse_edition(masked: str) -> str | None:
    """The first edition after the title, dots as spaces."""
    start = masked.find("A Movie") if "A Movie" in masked else masked.find("A.Movie")
    offset = start + len("A Movie") if start >= 0 else 1
    found = _EDITION.search(masked, offset)
    return found.group().replace(".", " ") if found else None


_SUB_EXCEPTIONS = ("soft", "multi", "horrible")
_WORD_WITH_UNDERSCORE = re.compile(r"\w+")


def parse_hardcoded_subs(name: str) -> str | None:
    """The last hardcoded subtitle tag: a word ending in SUB or SUBS, or HC and SUBBED as a generic one."""
    found: str | None = None
    for match in _WORD_WITH_UNDERSCORE.finditer(name):
        word = match.group()
        lower = word.lower()
        if lower in ("hc", "subbed"):
            found = "Generic Hardcoded Subs"
            continue
        for suffix in ("subs", "sub"):
            if lower.endswith(suffix) and len(lower) > len(suffix):
                if not lower[: -len(suffix)].endswith(_SUB_EXCEPTIONS):
                    found = word
                break
    return found


# --- All together -------------------------------------------------------------------------- #


def release_title(name: str) -> str:
    text = _EXTENSION.sub("", name.strip())
    return _UNSAFE.sub("", text.strip("-_"))


def parse_movie(name: str) -> ParsedMovie:
    text = release_title(name or "")
    title, year, start, end = _title_span(text)
    masked = _masked(text, start, end) if title else text
    group = parse_group(masked)
    language_text = masked.replace(group, "RlsGrp") if group else masked
    return ParsedMovie(
        title=title,
        year=year,
        group=group,
        quality=parse_quality(name or ""),
        revision=parse_revision(name or ""),
        edition=parse_edition(masked) if title else None,
        hardcoded_subs=parse_hardcoded_subs(name or ""),
        languages=parse_languages(language_text),
        release_title=masked,
    )
