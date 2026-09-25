"""Names of imported movies: Radarr's tokens, TRaSH's default patterns and clean characters (step 3).

What the plan takes from Radarr's source and TRaSH's recommendation ("Naming"); no code of
Radarr is used, only its behaviour, as in ``releases/parser.py``:

* **Tokens** are looked up regardless of case and separators. Inside the braces, the prefix characters ``-{ ._[(``
  and suffix characters ``-} ._)]`` appear only with a value; the separator between a token's words replaces the spaces
  in the value; an all lower case or all upper case token forces the case; ``:N`` shortens to N characters with
  ``...``, ``:-N`` keeps the end. ``{imdb-{ImdbId}}``, ``{tmdb-{TmdbId}}`` and ``{edition-{Edition Tags}}`` keep their
  braces around the value and vanish without one.
* **Filling:** title, original title and year from the title; edition, proper and group from the release name. Quality,
  codec, HDR type and audio of a filed file from its media data as Radarr names them (the owner's answer of
  17.09.2026): the codec spelled x264 or x265 when the file's encoder says so, else as the release name spells it; 3D
  from the release name. Without media data all of it from the release name, and media info tokens stay empty where
  the name says nothing. Media data an Arr handed over (its own flat shape) counts as media data, and its spelling of
  the codec is kept. ``{Release Group}`` is the one stored with the file when the release name gives none, and empty
  when both do (Radarr writes "Radarr" there).
* **Characters:** ": " becomes " - " and any other colon "-"; backslash and slash "+", "?" "!", "*" "-", and ``< > |``
  and quotes go. Repeated separators collapse, leading spaces and dots and trailing separators go, reserved Windows
  names get "_" instead of their dot.
* **Umlauts:** ``keep`` writes NFC, the clean tokens included (Radarr's clean title strips them to the base letter);
  ``replace`` writes form (a) of ``schreibweisen`` with the case kept.
* ⚠️ **Length:** a folder name stays within 255 bytes, a file name within 255 bytes including the temporary suffix
  ``.nexcrate-partial``, by shortening the title first.
* **Per version** (the takeover plan, T2): a movie version may have both patterns of its own; without them it uses the
  default patterns of the settings. The umlaut rule is one for all.
* **Tokens added for the takeover:** ``{Original Filename}``, the downloaded video file's name without its extension,
  empty when the pattern holds more than one token (as in Radarr); ``{Custom Format:Name}``, that format's name when it
  matched and TRaSH marks it for renaming. A colon modifier that is not a number (``{Movie Title:DE}``,
  ``{Custom Formats:x264}``) is refused with ``naming_modifier_unsupported``. Media info tokens nexcrate cannot fill
  without video analysis stay unknown.
* **Shortening** writes Radarr's ellipsis only after a path part is trimmed, so ``{Movie Title:3}`` gives ``...``.
  ``{Movie CleanTitleThe}`` cleans the title first and adds ``, The`` afterwards, as Radarr does.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ..db import get_setting, set_setting
from ..meldungen import meldung
from ..models import Source, VersionDefinition
from . import releases, schreibweisen
from .media import ARR_TOOLS, from_arr, is_arr_shape

SETTING_FOLDER = "naming_movie_folder"
SETTING_FILE = "naming_movie_file"
SETTING_UMLAUTS = "naming_umlauts"
#: TRaSH's standard recommendation for Radarr (TRaSH-Guides, MIT), quoted exactly.
DEFAULT_MOVIE_FOLDER = "{Movie CleanTitle} ({Release Year})"
DEFAULT_MOVIE_FILE = (
    "{Movie CleanTitle} {(Release Year)} - {{Edition Tags}} {[MediaInfo 3D]}{[Custom Formats]}{[Quality Full]}"
    "{[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}{[MediaInfo VideoDynamicRangeType]}{[Mediainfo VideoCodec]}"
    "{-Release Group}"
)
UMLAUT_MODES = ("keep", "replace")
DEFAULT_UMLAUTS = "keep"
PATTERN_MAX_LENGTH = 1000
MAX_PART_BYTES = 255
#: The name a transfer writes first; a file name leaves room for it.
PARTIAL_SUFFIX = ".nexcrate-partial"

#: Radarr's names of the tokens nexcrate fills, in the order the interface shows them.
TOKENS: tuple[str, ...] = (
    "{Movie Title}",
    "{Movie CleanTitle}",
    "{Movie TitleThe}",
    "{Movie CleanTitleThe}",
    "{Movie TitleFirstCharacter}",
    "{Movie OriginalTitle}",
    "{Movie CleanOriginalTitle}",
    "{Release Year}",
    "{ImdbId}",
    "{TmdbId}",
    "{Edition Tags}",
    "{Quality Full}",
    "{Quality Title}",
    "{Quality Proper}",
    "{Quality Real}",
    "{MediaInfo 3D}",
    "{MediaInfo VideoCodec}",
    "{MediaInfo VideoDynamicRangeType}",
    "{MediaInfo AudioCodec}",
    "{MediaInfo AudioChannels}",
    "{Custom Formats}",
    "{Release Group}",
    "{Original Title}",
    "{Original Filename}",
    "{Custom Format:Name}",
)


def token_key(name: str) -> str:
    """How a token is looked up: without case and separators."""
    return re.sub(r"[- ._]", "", name).casefold()


KNOWN_KEYS = frozenset(token_key(token[1:-1].split(":", 1)[0]) for token in TOKENS)
TITLE_KEYS = frozenset(
    token_key(name)
    for name in (
        "Movie Title",
        "Movie CleanTitle",
        "Movie TitleThe",
        "Movie CleanTitleThe",
        "Movie OriginalTitle",
        "Movie CleanOriginalTitle",
    )
)
#: Tokens that come from the file: never in a folder pattern.
FILE_KEYS = frozenset(
    token_key(name)
    for name in (
        "Edition Tags",
        "Quality Full",
        "Quality Title",
        "Quality Proper",
        "Quality Real",
        "MediaInfo 3D",
        "MediaInfo VideoCodec",
        "MediaInfo VideoDynamicRangeType",
        "MediaInfo AudioCodec",
        "MediaInfo AudioChannels",
        "Custom Formats",
        "Release Group",
        "Original Title",
        "Original Filename",
        "Custom Format",
    )
)
YEAR_KEY = token_key("Release Year")
ORIGINAL_KEY = token_key("Original Title")
ORIGINAL_FILENAME_KEY = token_key("Original Filename")
CUSTOM_FORMAT_KEY = token_key("Custom Format")
#: Where ``token_values`` keeps the names of the matched formats for ``{Custom Format:Name}``; no token has this key.
FORMAT_NAMES_KEY = "custom-format-names"
FORMAT_NAMES_SEPARATOR = "\n"
#: Stands for Radarr's ellipsis until a path part is trimmed: a private use character nothing else writes.
ELLIPSIS = ""
_LENGTH = re.compile(r"-?\d{1,4}")
#: The values shortened first when a name is too long.
SHORTENED_KEYS = TITLE_KEYS | {ORIGINAL_KEY, ORIGINAL_FILENAME_KEY}
#: Tokens whose modifier is a number format of zeros (``{season:00}``), not a length: Sonarr's numbers, and Lidarr's
#: ``{medium:0}`` and ``{track:00}`` (decision 14).
PAD_KEYS = frozenset({"season", "episode", "absolute", "medium", "track"})
_PAD = re.compile(r"0{1,4}")

_PATTERN = re.compile(
    r"(?P<wrapper>\{(?P<wrap>imdb|imdbid|tmdb|tmdbid|tvdb|tvdbid|edition)-"
    r"\{(?P<inner>[A-Za-z0-9]+(?:[- ._]+[A-Za-z0-9]+)?)\}\})"
    # A server's id addition in square brackets, such as Jellyfin's [tmdbid-{TmdbId}] or Emby's [tvdbid={TvdbId}]: it
    # vanishes as a whole without the id (decision 9).
    r"|(?P<bracket>\[(?P<bwrap>imdbid|tmdbid|tvdbid|imdb|tmdb|tvdb)(?P<bsep>[-=])\{(?P<binner>[A-Za-z]+Id)\}\])"
    r"|(?P<token>\{(?P<prefix>[-{ ._\[(]*)(?P<name>[A-Za-z0-9]+(?:(?P<separator>[- ._]+)[A-Za-z0-9]+)?)"
    r"(?::(?P<modifier>[ ,A-Za-z0-9|+-]*[,A-Za-z0-9|+]))?(?P<suffix>[-} ._)\]]*)\})"
    r"|(?P<other>\{[^{}]*\})",
    re.IGNORECASE,
)


# --- Errors ------------------------------------------------------------------------------------ #


class NamingError(Exception):
    def __init__(self, detail: dict[str, Any]) -> None:
        super().__init__(detail["code"])
        self.detail = detail

    @property
    def code(self) -> str:
        return str(self.detail["code"])

    def http(self) -> HTTPException:
        return HTTPException(status_code=422, detail=self.detail)


def _unknown(token: str) -> NamingError:
    return NamingError(meldung("naming_token_unknown", f"The token {token} is not known.", token=token))


def _empty(pattern: str) -> NamingError:
    return NamingError(meldung("naming_pattern_empty", "A naming pattern must not be empty.", pattern=pattern))


def _incomplete(pattern: str) -> NamingError:
    return NamingError(
        meldung(
            "naming_pattern_incomplete",
            "The pattern needs a title token; a file name also needs {Release Year}, or {Original Title} instead.",
            pattern=pattern,
        )
    )


def _not_in_folder(token: str) -> NamingError:
    return NamingError(
        meldung(
            "naming_token_not_in_folder",
            f"The token {token} comes from the file and cannot name a folder.",
            token=token,
        )
    )


def _modifier_unsupported(token: str) -> NamingError:
    return NamingError(
        meldung(
            "naming_modifier_unsupported",
            f"The token {token} has a modifier nexcrate does not support; only a number that shortens works.",
            token=token,
        )
    )


ERRORS = (
    (422, "naming_token_unknown"),
    (422, "naming_pattern_empty"),
    (422, "naming_pattern_incomplete"),
    (422, "naming_token_not_in_folder"),
    (422, "naming_modifier_unsupported"),
)


def scan(pattern: str, known: frozenset[str], file_keys: frozenset[str], *, folder: bool) -> list[str]:
    """The lookup keys of a pattern's tokens. Raises ``NamingError`` for an empty pattern is not checked here; for an
    unknown token, a modifier nexcrate does not support, or a file token in a folder pattern."""
    keys: list[str] = []
    for match in _PATTERN.finditer(pattern):
        if match.group("other"):
            raise _unknown(match.group("other"))
        if match.group("bracket"):
            name = match.group("binner")
        elif match.group("wrapper"):
            name = match.group("inner")
        else:
            name = match.group("name")
        key = token_key(name)
        if key not in known:
            raise _unknown("{" + name + "}")
        modifier = match.group("modifier")
        if modifier is not None:
            if key in PAD_KEYS:
                if not _PAD.fullmatch(modifier):
                    raise _modifier_unsupported("{" + name + ":" + modifier + "}")
            elif key != CUSTOM_FORMAT_KEY and not _LENGTH.fullmatch(modifier):
                raise _modifier_unsupported("{" + name + ":" + modifier + "}")
        if folder and key in file_keys:
            raise _not_in_folder("{" + name + (":" + modifier if modifier is not None else "") + "}")
        keys.append(key)
    return keys


#: Tokens only a kept release name or media data can fill. A file that has neither loses these parts of its name.
FACT_KEYS = frozenset(
    token_key(name)
    for name in (
        "Edition Tags",
        "MediaInfo 3D",
        "MediaInfo VideoCodec",
        "MediaInfo VideoDynamicRangeType",
        "MediaInfo AudioCodec",
        "MediaInfo AudioChannels",
        "Custom Formats",
        "Release Group",
    )
)


def wants_facts(pattern: str, known: frozenset[str], file_keys: frozenset[str]) -> bool:
    """Whether a pattern names a token that only a release name or media data fills. An unusable pattern says no."""
    try:
        keys = scan(pattern, known, file_keys, folder=False)
    except NamingError:
        return False
    return any(key in FACT_KEYS for key in keys)


def check(pattern: str, which: str) -> None:
    """Raises ``NamingError`` for an empty pattern, an unknown token, a file token in a folder, or a missing title.

    ``which`` is ``movie_folder`` or ``movie_file``.
    """
    if not pattern.strip():
        raise _empty(which)
    keys = scan(pattern, KNOWN_KEYS, FILE_KEYS, folder=which == "movie_folder")
    has_title = bool(TITLE_KEYS & set(keys))
    if which == "movie_folder" and not has_title:
        raise _incomplete(which)
    original = ORIGINAL_KEY in keys or ORIGINAL_FILENAME_KEY in keys
    if which == "movie_file" and not ((has_title and YEAR_KEY in keys) or original):
        raise _incomplete(which)


# --- Settings ------------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Naming:
    movie_folder: str = DEFAULT_MOVIE_FOLDER
    movie_file: str = DEFAULT_MOVIE_FILE
    umlauts: str = DEFAULT_UMLAUTS


def load(db: OrmSession) -> Naming:
    umlauts = get_setting(db, SETTING_UMLAUTS, DEFAULT_UMLAUTS)
    return Naming(
        movie_folder=get_setting(db, SETTING_FOLDER, "") or DEFAULT_MOVIE_FOLDER,
        movie_file=get_setting(db, SETTING_FILE, "") or DEFAULT_MOVIE_FILE,
        umlauts=umlauts if umlauts in UMLAUT_MODES else DEFAULT_UMLAUTS,
    )


def save(db: OrmSession, naming: Naming) -> None:
    """Stage the settings; the caller commits. Check the patterns first."""
    set_setting(db, SETTING_FOLDER, naming.movie_folder)
    set_setting(db, SETTING_FILE, naming.movie_file)
    set_setting(db, SETTING_UMLAUTS, naming.umlauts)


def has_own(definition: VersionDefinition) -> bool:
    return bool(definition.naming_movie_folder) and bool(definition.naming_movie_file)


def for_version(db: OrmSession, definition: VersionDefinition | None) -> Naming:
    """The naming a version files away with: its own patterns, else the default ones. The umlaut rule is one for all."""
    base = load(db)
    if definition is None or not has_own(definition):
        return base
    return Naming(
        movie_folder=definition.naming_movie_folder or base.movie_folder,
        movie_file=definition.naming_movie_file or base.movie_file,
        umlauts=base.umlauts,
    )


def set_version(definition: VersionDefinition, movie_folder: str | None, movie_file: str | None) -> None:
    """Stage a version's own patterns, or none of them with ``None``; the caller checks first and commits."""
    definition.naming_movie_folder = movie_folder if movie_folder and movie_file else None
    definition.naming_movie_file = movie_file if movie_folder and movie_file else None


def version_entry(definition: VersionDefinition, base: Naming, source_id: int | None) -> dict[str, Any]:
    own = has_own(definition)
    return {
        "version_id": definition.id,
        "label": definition.label,
        "own": own,
        "movie_folder": definition.naming_movie_folder if own else base.movie_folder,
        "movie_file": definition.naming_movie_file if own else base.movie_file,
        "source_id": source_id,
    }


def setting_sources(db: OrmSession, app: str) -> dict[int, int]:
    """Version definition id to the connection of this app whose settings can be read.

    A taken-over connection answers too: its key stays stored, and only its settings are read (the rule of the music
    block for indexers and download clients, 19.09.2026). Its library is never read again.
    """
    rows = db.scalars(select(Source).where(Source.app == app).order_by(Source.id))
    return {row.version_id: row.id for row in rows}


def version_entries(db: OrmSession) -> list[dict[str, Any]]:
    """One entry per movie version: the patterns it uses, whether they are its own, the source to take them from."""
    base = load(db)
    feeding = setting_sources(db, "radarr")
    definitions = db.scalars(
        select(VersionDefinition).where(VersionDefinition.kind == "movie").order_by(VersionDefinition.id)
    )
    return [version_entry(definition, base, feeding.get(definition.id)) for definition in definitions]


def problem_of(pattern: str, which: str) -> dict[str, Any] | None:
    """The first code the check gives for a pattern, with its values, or None when the pattern passes."""
    try:
        check(pattern, which)
    except NamingError as exc:
        values = {key: value for key, value in exc.detail.items() if key not in ("code", "message")}
        return {"code": exc.code, "values": values}
    return None


# --- Values -------------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MovieFacts:
    title: str
    original_title: str | None = None
    year: int | None = None
    tmdb_id: int | None = None
    imdb_id: str | None = None


@dataclass(frozen=True)
class ReleaseFacts:
    release_title: str
    #: The matched formats TRaSH marks for renaming, by name.
    formats: tuple[str, ...] = field(default_factory=tuple)
    #: The downloaded video file's name without its extension, for ``{Original Filename}``; empty when unknown.
    original_filename: str = ""
    #: The file's quality as nexcrate stores it (from its media data); None: the release name's.
    quality: str | None = None
    #: The file's media data (``media.from_mediainfo``); None: the release name says codec, HDR and audio.
    media: dict[str, Any] | None = None
    #: The group stored with the file, for a file whose release name is not kept; None: only the name says it.
    release_group: str | None = None


_BRACKETS = re.compile(r"[()\[\]{}]")
_CONTRACTION = re.compile(r"['`´’:?,](?=(?:s|m|t|ve|ll|d|re)\s|\s|$)", re.IGNORECASE)
_LONE_MARK = re.compile(r"(?<=\s)[,<>/\\;:'\"|`~!?@$%^*\-_=](?=\s)")
_TITLE_THE = re.compile(r"^(The|A|An)\s(.+?)((?:\s\(\d{4}\))?)$", re.IGNORECASE)


def clean_title(title: str) -> str:
    """A title without most punctuation and brackets, "&" as "and". Umlauts and accents stay."""
    text = title.replace("&", "and").replace("/", " ").replace("\\", " ")
    text = _BRACKETS.sub("", text)
    text = _CONTRACTION.sub("", text)
    text = _LONE_MARK.sub("", text)
    return " ".join(text.split())


def title_the(title: str) -> str:
    """A leading article moves to the end: "The Summit" as "Summit, The"."""
    found = _TITLE_THE.match(title.strip())
    if found is None:
        return title
    return f"{found.group(2)}, {found.group(1)}{found.group(3)}"


def clean_title_the(title: str) -> str:
    """The clean title with a leading article at the end: the parts cleaned first, then ", The" added, as in Radarr."""
    found = _TITLE_THE.match(title.strip())
    if found is None:
        return clean_title(title)
    tail = clean_title(found.group(3))
    return f"{clean_title(found.group(2))}, {found.group(1)}" + (f" {tail}" if tail else "")


def first_character(title: str) -> str:
    text = title_the(title).strip()
    return text[0].upper() if text and text[0].isalnum() else "_"


_UPPER_EDITION_WORDS = frozenset({"imax", "3d", "sdr", "hdr", "dv"})
_ORDINAL = re.compile(r"\d+(?:st|nd|rd|th)", re.IGNORECASE)


def edition_tags(edition: str | None) -> str:
    """Title case, with IMAX, 3D, SDR, HDR and DV in capitals and ordinals in lower case."""
    words: list[str] = []
    for word in (edition or "").split():
        if word.casefold() in _UPPER_EDITION_WORDS:
            words.append(word.upper())
        elif _ORDINAL.fullmatch(word):
            words.append(word.lower())
        else:
            words.append(word[:1].upper() + word[1:].lower())
    return " ".join(words)


def _word(pattern: str, *, digits_may_follow: bool = False) -> re.Pattern[str]:
    after = r"(?![a-z])" if digits_may_follow else r"(?![a-z0-9])"
    return re.compile(rf"(?<![a-z0-9])(?:{pattern}){after}", re.IGNORECASE)


_THREE_D = _word(r"3d|h-?sbs|h-?ou|sbs|hou|mvc")
_VIDEO_CODECS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("x265", _word(r"x[ .]?265")),
    ("h265", _word(r"h[ .]?265|hevc")),
    ("x264", _word(r"x[ .]?264")),
    ("h264", _word(r"h[ .]?264|avc")),
    ("AV1", _word(r"av1")),
    ("VP9", _word(r"vp9")),
    ("XviD", _word(r"xvid")),
    ("DivX", _word(r"divx")),
    ("VC1", _word(r"vc-?1")),
    ("MPEG2", _word(r"mpeg-?2")),
)
_DOLBY_VISION = _word(r"dv|dovi|dolby[ ._-]?vision")
_HDR10_PLUS = re.compile(r"(?<![a-z0-9])hdr10(?:\+|p|plus)(?![a-z0-9])", re.IGNORECASE)
_HDR = _word(r"hdr10|hdr")
_HLG = _word(r"hlg")
_ATMOS = _word(r"atmos")
_EAC3 = _word(r"ddp|dd\+|e-?ac-?3", digits_may_follow=True)
_AUDIO_CODECS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("TrueHD", _word(r"true[ ._-]?hd", digits_may_follow=True)),
    ("DTS-X", _word(r"dts[ ._:-]?x", digits_may_follow=True)),
    ("DTS-HD MA", _word(r"dts[ ._-]?hd[ ._-]?ma|dts[ ._-]?ma", digits_may_follow=True)),
    ("DTS-HD HRA", _word(r"dts[ ._-]?hd[ ._-]?hra|dts[ ._-]?hr", digits_may_follow=True)),
    ("DTS-ES", _word(r"dts[ ._-]?es", digits_may_follow=True)),
    ("DTS", _word(r"dts(?:[ ._-]?hd)?", digits_may_follow=True)),
    ("EAC3", _EAC3),
    ("AC3", _word(r"dd|ac-?3|dolby[ ._-]?digital", digits_may_follow=True)),
    ("AAC", _word(r"aac", digits_may_follow=True)),
    ("FLAC", _word(r"flac", digits_may_follow=True)),
    ("PCM", _word(r"l?pcm", digits_may_follow=True)),
    ("Opus", _word(r"opus", digits_may_follow=True)),
    ("MP3", _word(r"mp3")),
)
_CHANNELS = re.compile(r"(?<![0-9])([125-7])[ ._]([01])(?![0-9])")
_CHANNEL_VALUES = frozenset({"1.0", "2.0", "2.1", "5.1", "6.1", "7.1"})


def media_facts(masked: str) -> dict[str, str]:
    """3D, video codec, HDR type, audio codec and channels as a release name says them, empty where it says nothing."""
    video = next((name for name, pattern in _VIDEO_CODECS if pattern.search(masked)), "")
    dolby = bool(_DOLBY_VISION.search(masked))
    if _HDR10_PLUS.search(masked):
        hdr = "HDR10Plus"
    elif _HDR.search(masked):
        hdr = "HDR10"
    elif _HLG.search(masked):
        hdr = "HLG"
    else:
        hdr = ""
    dynamic = " ".join(part for part in ("DV" if dolby else "", hdr) if part)
    audio = next((name for name, pattern in _AUDIO_CODECS if pattern.search(masked)), "")
    if audio in ("TrueHD", "EAC3") and _ATMOS.search(masked):
        audio = f"{audio} Atmos"
    channels = ""
    for found in _CHANNELS.finditer(masked):
        value = f"{found.group(1)}.{found.group(2)}"
        if value in _CHANNEL_VALUES:
            channels = value
            break
    return {
        "mediainfo3d": "3D" if _THREE_D.search(masked) else "",
        "mediainfovideocodec": video,
        "mediainfovideodynamicrangetype": dynamic,
        "mediainfoaudiocodec": audio,
        "mediainfoaudiochannels": channels if audio else "",
    }


_AVC = _word(r"avc")
_X264 = _word(r"x[ .]?264")
_H264 = _word(r"h[ .]?264")
_HEVC = _word(r"hevc")
_X265 = _word(r"x[ .]?265")
_H265 = _word(r"h[ .]?265")
_XVID = _word(r"xvid")
_DIVX = _word(r"divx")


def _named_as(masked: str, choices: tuple[tuple[str, re.Pattern[str]], ...], default: str) -> str:
    """The first spelling the release name uses, else the default: Radarr's and Sonarr's scene name match."""
    return next((name for name, pattern in choices if pattern.search(masked)), default)


def _video_codec(video: dict[str, Any], masked: str) -> str:
    """Radarr's and Sonarr's video codec name from the media data's format: x264 or x265 when the file's encoder says
    so, else the spelling the release name uses, else h264 or h265."""
    codec, encoder = video.get("codec"), video.get("encoder")
    text = codec.strip() if isinstance(codec, str) else ""
    library = encoder.strip().lower() if isinstance(encoder, str) else ""
    upper = text.upper()
    if not text:
        return ""
    if upper in ("AVC", "H264", "X264"):
        if library.startswith("x264"):
            return "x264"
        return _named_as(masked, (("AVC", _AVC), ("x264", _X264), ("h264", _H264)), "h264")
    if upper in ("HEVC", "H265", "X265"):
        if library.startswith("x265"):
            return "x265"
        return _named_as(masked, (("HEVC", _HEVC), ("x265", _X265), ("h265", _H265)), "h265")
    if upper in ("MPEG VIDEO", "MPEG-2 VIDEO"):
        return "MPEG2"
    if upper == "MPEG-4 VISUAL":
        return _named_as(masked, (("XviD", _XVID), ("DivX", _DIVX)), "XviD")
    if upper == "VC-1":
        return "VC1"
    if upper in ("VP6", "VP7", "VP8", "VP9", "AV1"):
        return upper
    return text


def _arr_codec(video: dict[str, Any]) -> str:
    """The codec of media data taken over from an Arr: it already spells the name as it wants it written."""
    codec = video.get("codec")
    return codec.strip() if isinstance(codec, str) else ""


def file_media_facts(media: dict[str, Any], masked: str) -> dict[str, str]:
    """The same tokens as ``media_facts``, from the file's media data as Radarr and Sonarr name them (the owner's answer
    of 17.09.2026): codec, HDR type and the first audio stream's codec and channels. The media data knows no 3D; the
    release name says it."""
    video = media.get("video") if isinstance(media.get("video"), dict) else {}
    tracks = [track for track in (media.get("audio") or []) if isinstance(track, dict)]
    named_by_an_arr = media.get("tool") in ARR_TOOLS
    first = tracks[0] if tracks else {}
    audio = first.get("codec") if isinstance(first.get("codec"), str) else ""
    if audio == "Unknown":
        audio = ""
    channels = first.get("channels") if isinstance(first.get("channels"), str) else ""
    dynamic = video.get("dynamic_range") if isinstance(video.get("dynamic_range"), str) else ""
    return {
        "mediainfo3d": "3D" if _THREE_D.search(masked) else "",
        "mediainfovideocodec": (_arr_codec(video) if named_by_an_arr else _video_codec(video, masked)),
        "mediainfovideodynamicrangetype": dynamic,
        "mediainfoaudiocodec": audio,
        "mediainfoaudiochannels": channels if audio else "",
    }


def release_media_facts(release: ReleaseFacts, masked: str) -> dict[str, str]:
    """The media tokens of a filed file: from its media data when there is some, else as the release name says them.

    Media data taken over from Radarr, Sonarr or Lidarr is brought into nexcrate's shape first (their own shape lies
    in the database since the takeover of Sonarr).
    """
    stored = release.media
    if is_arr_shape(stored):
        stored = from_arr(stored, "sonarr")
    if isinstance(stored, dict):
        return file_media_facts(stored, masked)
    return media_facts(masked)


def token_values(movie: MovieFacts, release: ReleaseFacts | None) -> dict[str, str]:
    """Every token's raw value, by lookup key."""
    title = schreibweisen.nfc(movie.title or "")
    original = schreibweisen.nfc(movie.original_title or "") or title
    values = {
        token_key("Movie Title"): title,
        token_key("Movie CleanTitle"): clean_title(title),
        token_key("Movie TitleThe"): title_the(title),
        token_key("Movie CleanTitleThe"): clean_title_the(title),
        token_key("Movie TitleFirstCharacter"): first_character(title),
        token_key("Movie OriginalTitle"): original,
        token_key("Movie CleanOriginalTitle"): clean_title(original),
        YEAR_KEY: str(movie.year) if movie.year else "",
        token_key("ImdbId"): movie.imdb_id or "",
        token_key("TmdbId"): str(movie.tmdb_id) if movie.tmdb_id else "",
    }
    if release is None:
        return values
    parsed = releases.parse(release.release_title)
    quality = release.quality or parsed.movie.quality.name
    proper = "Proper" if parsed.movie.revision.version > 1 else ""
    real = "REAL" if parsed.movie.revision.real > 0 else ""
    values.update(
        {
            token_key("Edition Tags"): edition_tags(parsed.movie.edition),
            token_key("Quality Full"): " ".join(part for part in (quality, proper, real) if part),
            token_key("Quality Title"): quality,
            token_key("Quality Proper"): proper,
            token_key("Quality Real"): real,
            token_key("Custom Formats"): " ".join(release.formats),
            token_key("Release Group"): parsed.movie.group or (release.release_group or ""),
            ORIGINAL_KEY: schreibweisen.nfc(release.release_title),
            ORIGINAL_FILENAME_KEY: schreibweisen.nfc(release.original_filename or ""),
            FORMAT_NAMES_KEY: FORMAT_NAMES_SEPARATOR.join(release.formats),
            **release_media_facts(release, parsed.movie.release_title),
        }
    )
    return values


# --- Rendering ---------------------------------------------------------------------------------------- #

_BAD_CHARACTERS = (("\\", "+"), ("/", "+"), ("<", ""), (">", ""), ("?", "!"), ("*", "-"), ("|", ""), ('"', ""))
_RESERVED = re.compile(r"^(aux|com[1-9]|con|lpt[1-9]|nul|prn)\.", re.IGNORECASE)


def clean(text: str) -> str:
    """Radarr's smart colon and its replacements for ``\\ / < > ? * | "``; control characters go."""
    text = text.replace(": ", " - ").replace(":", "-")
    for bad, good in _BAD_CHARACTERS:
        text = text.replace(bad, good)
    return "".join(character for character in text if unicodedata.category(character) != "Cc")


def _shorten(value: str, length: int) -> str:
    """Radarr's ``:N``: N characters, the last three the ellipsis; ``:-N`` keeps the end. Spaces and dots at the cut go.

    The ellipsis is ``ELLIPSIS`` until the path part is trimmed, as in Radarr: trimming would take its dots away.
    """
    if length == 0 or len(value) <= abs(length):
        return value
    keep = max(abs(length) - 3, 0)
    if length > 0:
        return value[:keep].rstrip(" .") + ELLIPSIS
    return ELLIPSIS + value[len(value) - keep :].lstrip(" .")


def _apply_case(value: str, name: str) -> str:
    letters = [character for character in name if character.isalpha()]
    if letters and all(character.islower() for character in letters):
        return value.lower()
    if letters and all(character.isupper() for character in letters):
        return value.upper()
    return value


def _format_named(values: dict[str, str], wanted: str | None) -> str:
    """``{Custom Format:Name}``: that format's name when it matched and TRaSH marks it for renaming, else empty."""
    target = (wanted or "").strip().casefold()
    names = values.get(FORMAT_NAMES_KEY, "").split(FORMAT_NAMES_SEPARATOR)
    return next((name for name in names if name and target and name.casefold() == target), "")


def _assemble(pattern: str, values: dict[str, str], shorten: Callable[[str, str], str]) -> str:
    parts: list[str] = []
    position = 0
    # As in Radarr, {Original Filename} stays empty in a pattern with more than one token.
    several = sum(1 for match in _PATTERN.finditer(pattern) if not match.group("other")) > 1
    for match in _PATTERN.finditer(pattern):
        parts.append(clean(pattern[position : match.start()]))
        position = match.end()
        if match.group("other"):
            continue
        if match.group("bracket"):
            key = token_key(match.group("binner"))
            value = clean(shorten(key, values.get(key, "")))
            parts.append("[" + match.group("bwrap") + match.group("bsep") + value + "]" if value else "")
            continue
        if match.group("wrapper"):
            key = token_key(match.group("inner"))
            value = clean(shorten(key, values.get(key, "")))
            parts.append("{" + match.group("wrap") + "-" + value + "}" if value else "")
            continue
        name = match.group("name")
        key = token_key(name)
        modifier = match.group("modifier")
        if key == CUSTOM_FORMAT_KEY:
            value = _format_named(values, modifier)
        elif key == ORIGINAL_FILENAME_KEY and several:
            value = ""
        elif key in PAD_KEYS:
            # Sonarr's {season:00}: zero padded; a multi-episode block was replaced before (``naming_series``).
            raw = values.get(key, "")
            value = raw.zfill(len(modifier)) if modifier and raw.isdigit() else raw
        else:
            value = shorten(key, values.get(key, ""))
            if modifier is not None and _LENGTH.fullmatch(modifier):
                value = _shorten(value, int(modifier))
        value = _apply_case(value, name)
        separator = match.group("separator") or " "
        if separator != " ":
            value = value.replace(" ", separator)
        value = clean(value)
        parts.append(match.group("prefix") + value + match.group("suffix") if value else "")
    parts.append(clean(pattern[position:]))
    return "".join(parts)


def _finish(text: str, umlauts: str) -> str:
    text = schreibweisen.replaced(text) if umlauts == "replace" else schreibweisen.nfc(text)
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"-{2,}", "-", text)
    text = re.sub(r"_{2,}", "_", text)
    text = re.sub(r"(?<!\.)\.\.(?!\.)", ".", text)
    text = text.lstrip(" .").rstrip(" .-_")
    return _RESERVED.sub(lambda match: match.group(1) + "_", text) or "_"


def _cut_bytes(text: str, limit: int) -> str:
    encoded = text.encode("utf-8")[: max(limit, 1)]
    return encoded.decode("utf-8", "ignore").rstrip(" .-_") or "_"


def render(
    pattern: str,
    values: dict[str, str],
    umlauts: str,
    *,
    max_bytes: int,
    suffix: str = "",
    tiers: tuple[frozenset[str], ...] | None = None,
) -> str:
    """One path part from a pattern, within ``max_bytes`` including ``suffix``.

    The values of ``tiers`` are shortened in turn: the first set until nothing is left to cut, then the next one with
    the first kept short. Without tiers the title is shortened first. A series shortens the episode title before the
    series title (S4.2).
    """
    order = tiers if tiers is not None else (frozenset(SHORTENED_KEYS),)
    budgets: list[int | None] = [None] * len(order)

    def shorten(key: str, value: str) -> str:
        for index, keys in enumerate(order):
            if key in keys and budgets[index] is not None:
                return value[: budgets[index]].rstrip()
        return value

    tier = 0
    while True:
        # The ellipsis only now, after the trim: as the placeholder it kept its place at either end.
        text = _finish(_assemble(pattern, values, shorten), umlauts).replace(ELLIPSIS, "...")
        overflow = len((text + suffix).encode("utf-8")) - max_bytes
        if overflow <= 0:
            return text + suffix
        longest = max((len(values.get(key, "")) for key in order[tier]), default=0)
        current = longest if budgets[tier] is None else budgets[tier]
        if current is None or current <= 1:
            if tier + 1 < len(order):
                budgets[tier] = 1 if longest else budgets[tier]
                tier += 1
                continue
            return _cut_bytes(text, max_bytes - len(suffix.encode("utf-8"))) + suffix
        budgets[tier] = max(1, current - overflow)


def folder_name(naming: Naming, movie: MovieFacts) -> str:
    return render(naming.movie_folder, token_values(movie, None), naming.umlauts, max_bytes=MAX_PART_BYTES)


def file_name(naming: Naming, movie: MovieFacts, release: ReleaseFacts, extension: str) -> str:
    """The file name with its extension, leaving room for the temporary suffix of a transfer."""
    clean_extension = extension.lower() if re.fullmatch(r"\.[A-Za-z0-9-]{1,10}", extension) else ""
    limit = MAX_PART_BYTES - len(PARTIAL_SUFFIX)
    values = token_values(movie, release)
    return render(naming.movie_file, values, naming.umlauts, max_bytes=limit, suffix=clean_extension)


def clean_file_name(text: str) -> str:
    """A release title as the name of the file handed to a client: clean characters, within 200 bytes."""
    return _cut_bytes(_finish(clean(text), "keep"), 200)


#: The made-up movie and release of the preview.
PREVIEW_MOVIE = MovieFacts(
    title="Example Summit: Die Rückkehr", original_title="Example Summit: Die Rückkehr", year=2021, tmdb_id=900002,
    imdb_id="tt0000002",
)
PREVIEW_RELEASE = ReleaseFacts(
    release_title="Example.Summit.Die.Rueckkehr.2021.Directors.Cut.2160p.UHD.BluRay.REMUX.DV.HDR10.TrueHD.Atmos.7.1.HEVC-EXAMPLE",
    formats=("Example Format",),
    original_filename="Example.Summit.Die.Rueckkehr.2021.Directors.Cut.2160p.UHD.BluRay.REMUX.DV.HDR10.TrueHD.Atmos.7.1.HEVC-EXAMPLE",
)


def preview(naming: Naming) -> dict[str, str]:
    return {
        "folder": folder_name(naming, PREVIEW_MOVIE),
        "file": file_name(naming, PREVIEW_MOVIE, PREVIEW_RELEASE, ".mkv"),
    }
