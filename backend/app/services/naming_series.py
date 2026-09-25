"""Names of filed episodes: Sonarr's tokens, TRaSH's default patterns for Sonarr (S4.2).

The renderer is the one of movies (``naming``): the same syntax for prefix, suffix, shortening, case, separators,
clean characters and umlauts. What series add:

* **Six patterns and a style:** series folder, season folder, specials folder, episode file, file of a daily show,
  file of an anime series (A5), and the style of multi-episode files. Per series version each
  pattern may be its own; an empty one is the default. The umlaut rule is the one of movies.
* **Tokens** in Sonarr's names (decision 10). ``{season:00}``, ``{episode:00}`` and ``{absolute:000}`` are zero
  padded numbers. ``{absolute}`` is the number an anime series counts through (scheme ``absolute``), and only the
  anime pattern takes it, as in Sonarr; a file of several episodes names the first and the last (``001-003``) in the
  range styles and every number in the others (``001-002-003``), as Sonarr's naming examples show.
  ``{TvMazeId}`` stays empty: nexcrate has no such number.
* **Titles** in the account language with the English fallback (decision 13). A placeholder ("Folge 9") is no title;
  a missing title is ``TBA`` as in Sonarr, and the file is marked for renaming later (decision 27). Several episodes:
  ``{Episode Title}`` joins with " + ", ``{Episode CleanTitle}`` with " and "; titles that only differ by "(1)", "(2)"
  become one.
* **Numbers** after TMDB or TVDB, per version (decision 14). Without a TVDB number for every episode of a file, the
  file is named after TMDB. The season folder follows the number of the name.
* **Multi-episode styles** as Sonarr's six: the block ``S{season:00}E{episode:00}`` is written out for every episode.
* **Length:** each part within 255 bytes, the episode title shortened first, then the series title.
* **Unknown stays empty:** ``{Release Group}`` is the group the release name says, else the one stored with the
  file, and empty when neither knows one (Sonarr writes "Sonarr").
* **Quality and media info** of a filed file from its media data, as for movies (the owner's answer of 17.09.2026):
  the name says what the file is, and what nexcrate stores.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ..db import get_setting, set_setting
from ..meldungen import meldung
from ..models import VersionDefinition
from . import naming, releases, schreibweisen
from .naming import NamingError, token_key

# --- Patterns and defaults ------------------------------------------------------------------------------------ #

#: The five patterns, in the order the interface shows them.
PATTERNS = ("series_folder", "season_folder", "specials_folder", "episode_file", "daily_file", "anime_file")
SETTING_KEYS = {which: f"naming_{which}" for which in PATTERNS}
SETTING_STYLE = "naming_multi_episode_style"

#: TRaSH's recommendation for Sonarr (TRaSH-Guides, MIT), the series folder without an id (decision 9).
_FILE_TAIL = (
    "{[Custom Formats]}{[Quality Full]}{[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}"
    "{[MediaInfo VideoDynamicRangeType]}{[Mediainfo VideoCodec]}{-Release Group}"
)
DEFAULTS: dict[str, str] = {
    "series_folder": "{Series CleanTitleWithoutYear} {(Series Year)}",
    "season_folder": "Season {season:00}",
    "specials_folder": "Specials",
    "episode_file": "{Series CleanTitleWithoutYear} {(Series Year)} - S{season:00}E{episode:00} - "
    "{Episode CleanTitle:90} " + _FILE_TAIL,
    "daily_file": "{Series CleanTitleWithoutYear} {(Series Year)} - {Air-Date} - {Episode CleanTitle:90} " + _FILE_TAIL,
    # TRaSH's anime format with the number counted through (the owner's answer to fork 5 of the design notes).
    "anime_file": "{Series CleanTitleWithoutYear} {(Series Year)} - S{season:00}E{episode:00} - {absolute:000} - "
    "{Episode CleanTitle:90} " + _FILE_TAIL,
}
#: Sonarr's multi-episode styles.
STYLES = ("extend", "duplicate", "repeat", "scene", "range", "prefixed_range")
DEFAULT_STYLE = "prefixed_range"
NUMBERINGS = ("tmdb", "tvdb")
DEFAULT_NUMBERING = "tmdb"
#: The additions for the series folder the dialog offers, measured in B1 (decision 9). Each fits its server only.
FOLDER_ADDITIONS = {"plex": "{tmdb-{TmdbId}}", "jellyfin": "[tmdbid-{TmdbId}]", "emby": "[tvdbid={TvdbId}]"}
TBA = "TBA"

#: Sonarr's names of the tokens nexcrate fills for series, in the order the interface shows them.
TOKENS: tuple[str, ...] = (
    "{Series Title}",
    "{Series CleanTitle}",
    "{Series TitleYear}",
    "{Series CleanTitleYear}",
    "{Series TitleWithoutYear}",
    "{Series CleanTitleWithoutYear}",
    "{Series TitleThe}",
    "{Series CleanTitleThe}",
    "{Series TitleTheYear}",
    "{Series CleanTitleTheYear}",
    "{Series TitleTheWithoutYear}",
    "{Series CleanTitleTheWithoutYear}",
    "{Series TitleFirstCharacter}",
    "{Series Year}",
    "{TvdbId}",
    "{TmdbId}",
    "{ImdbId}",
    "{TvMazeId}",
    "{season:00}",
    "{episode:00}",
    "{absolute:000}",
    "{Air-Date}",
    "{Air Date}",
    "{Episode Title}",
    "{Episode CleanTitle}",
    "{Quality Full}",
    "{Quality Title}",
    "{Quality Proper}",
    "{Quality Real}",
    "{MediaInfo VideoCodec}",
    "{MediaInfo VideoDynamicRangeType}",
    "{MediaInfo AudioCodec}",
    "{MediaInfo AudioChannels}",
    "{MediaInfo 3D}",
    "{Custom Formats}",
    "{Release Group}",
    "{Original Title}",
    "{Original Filename}",
    "{Custom Format:Name}",
)
KNOWN_KEYS = frozenset(token_key(token[1:-1].split(":", 1)[0]) for token in TOKENS)
SERIES_TITLE_KEYS = frozenset(
    token_key(token[1:-1]) for token in TOKENS[:13] if token != "{Series TitleFirstCharacter}"
)
SEASON_KEY = "season"
EPISODE_KEY = "episode"
ABSOLUTE_KEY = "absolute"
AIR_DATE_KEY = token_key("Air Date")
EPISODE_TITLE_KEYS = frozenset({token_key("Episode Title"), token_key("Episode CleanTitle")})
#: Tokens of one file or episode: never in the series folder; the season folder allows ``{season}`` only.
FILE_KEYS = frozenset(
    {
        *naming.FILE_KEYS,
        EPISODE_KEY,
        ABSOLUTE_KEY,
        AIR_DATE_KEY,
        *EPISODE_TITLE_KEYS,
    }
)
ORIGINAL_KEYS = frozenset({naming.ORIGINAL_KEY, naming.ORIGINAL_FILENAME_KEY})
#: Shortened in this order when a name is too long.
TIERS = (
    EPISODE_TITLE_KEYS,
    SERIES_TITLE_KEYS | ORIGINAL_KEYS,
)

#: The block Sonarr repeats for several episodes: ``S{season:00}E{episode:00}`` or ``{season}x{episode:00}``.
_BLOCK = re.compile(
    r"(?P<s>s?)\{season(?::(?P<season_pad>0+))?\}(?P<e>e|x)\{episode(?::(?P<episode_pad>0+))?\}", re.IGNORECASE
)
_EPISODE = re.compile(r"\{episode(?::(?P<pad>0+))?\}", re.IGNORECASE)
_ABSOLUTE = re.compile(r"\{absolute(?::0+)?\}", re.IGNORECASE)
_ABSOLUTE_PADDED = re.compile(r"\{absolute(?::(?P<pad>0+))?\}", re.IGNORECASE)
_TRAILING_YEAR = re.compile(r"\s*\((\d{4})\)$")
_PART_NUMBER = re.compile(r"^(?P<title>.+?)\s*\((?P<number>\d+)\)$")


# --- Checks --------------------------------------------------------------------------------------------------- #


def _incomplete(which: str, text: str) -> NamingError:
    return NamingError(meldung("naming_pattern_incomplete", text, pattern=which))


_NEEDS = {
    "series_folder": "The series folder needs a series title token.",
    "season_folder": "The season folder needs {season}.",
    "specials_folder": "The specials folder must not be empty.",
    "episode_file": "An episode file needs {season} and {episode}, or {Original Title}.",
    "daily_file": "The file of a daily show needs {Air-Date} or {Air Date}, or {Original Title}.",
    "anime_file": "An anime file needs {absolute}, or {season} and {episode}, or {Original Title}.",
}


def check(pattern: str, which: str) -> None:
    """Raises ``NamingError`` for an empty pattern, an unknown token (``{absolute}`` outside the anime pattern too), a
    file token in a folder, or what the pattern needs (decision 11)."""
    if not pattern.strip():
        raise NamingError(meldung("naming_pattern_empty", "A naming pattern must not be empty.", pattern=which))
    absolute = _ABSOLUTE.search(pattern)
    if absolute is not None and which != "anime_file":
        raise NamingError(
            meldung("naming_token_unknown", f"The token {absolute.group(0)} is not known.", token=absolute.group(0))
        )
    folder = which in ("series_folder", "season_folder", "specials_folder")
    file_keys = FILE_KEYS | ({SEASON_KEY} if which == "series_folder" else set())
    keys = set(naming.scan(pattern, KNOWN_KEYS, frozenset(file_keys), folder=folder))
    original = bool(ORIGINAL_KEYS & keys)
    if which == "series_folder" and not SERIES_TITLE_KEYS & keys:
        raise _incomplete(which, _NEEDS[which])
    if which == "season_folder" and SEASON_KEY not in keys:
        raise _incomplete(which, _NEEDS[which])
    if which == "episode_file" and not ((SEASON_KEY in keys and EPISODE_KEY in keys) or original):
        raise _incomplete(which, _NEEDS[which])
    if which == "daily_file" and not (AIR_DATE_KEY in keys or original):
        raise _incomplete(which, _NEEDS[which])
    if which == "anime_file" and not (
        ABSOLUTE_KEY in keys or (SEASON_KEY in keys and EPISODE_KEY in keys) or original
    ):
        raise _incomplete(which, _NEEDS[which])


def problem_of(pattern: str, which: str) -> dict[str, Any] | None:
    try:
        check(pattern, which)
    except NamingError as exc:
        values = {key: value for key, value in exc.detail.items() if key not in ("code", "message")}
        return {"code": exc.code, "values": values}
    return None


# --- Settings ------------------------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SeriesNaming:
    series_folder: str = DEFAULTS["series_folder"]
    season_folder: str = DEFAULTS["season_folder"]
    specials_folder: str = DEFAULTS["specials_folder"]
    episode_file: str = DEFAULTS["episode_file"]
    daily_file: str = DEFAULTS["daily_file"]
    anime_file: str = DEFAULTS["anime_file"]
    style: str = DEFAULT_STYLE
    umlauts: str = naming.DEFAULT_UMLAUTS
    numbering: str = DEFAULT_NUMBERING

    def pattern(self, which: str) -> str:
        return str(getattr(self, which))


def load(db: OrmSession) -> SeriesNaming:
    base = naming.load(db)
    style = get_setting(db, SETTING_STYLE, DEFAULT_STYLE)
    return SeriesNaming(
        **{which: get_setting(db, SETTING_KEYS[which], "") or DEFAULTS[which] for which in PATTERNS},
        style=style if style in STYLES else DEFAULT_STYLE,
        umlauts=base.umlauts,
    )


def save(db: OrmSession, patterns: dict[str, str], style: str) -> None:
    """Stage the default patterns and the style; the caller checks and commits. A default pattern is stored empty."""
    for which in PATTERNS:
        value = patterns[which]
        set_setting(db, SETTING_KEYS[which], "" if value == DEFAULTS[which] else value)
    set_setting(db, SETTING_STYLE, style if style in STYLES else DEFAULT_STYLE)


def own_patterns(definition: VersionDefinition) -> dict[str, str | None]:
    return {which: getattr(definition, f"naming_{which}") for which in PATTERNS}


def for_version(db: OrmSession, definition: VersionDefinition | None) -> SeriesNaming:
    """The naming a series version files with: each of its own patterns, else the default; its numbering."""
    base = load(db)
    if definition is None:
        return base
    own = own_patterns(definition)
    numbering = definition.episode_numbering if definition.episode_numbering in NUMBERINGS else DEFAULT_NUMBERING
    return SeriesNaming(
        **{which: own[which] or base.pattern(which) for which in PATTERNS},
        style=base.style,
        umlauts=base.umlauts,
        numbering=numbering,
    )


def set_version(definition: VersionDefinition, patterns: dict[str, str | None], numbering: str | None) -> None:
    """Stage a series version's own patterns (None or the default pattern: none of its own) and its numbering."""
    for which in PATTERNS:
        value = patterns.get(which)
        setattr(definition, f"naming_{which}", value or None)
    definition.episode_numbering = numbering if numbering in NUMBERINGS and numbering != DEFAULT_NUMBERING else None


def version_entries(db: OrmSession) -> list[dict[str, Any]]:
    """One entry per series version: its own patterns (null where none), what it uses, its numbering and the Sonarr
    its naming could be taken from."""
    base = load(db)
    sources = naming.setting_sources(db, "sonarr")
    rows = db.scalars(
        select(VersionDefinition).where(VersionDefinition.kind == "series").order_by(VersionDefinition.id)
    )
    return [version_entry(row, base, sources.get(row.id)) for row in rows]


def version_entry(definition: VersionDefinition, base: SeriesNaming, source_id: int | None = None) -> dict[str, Any]:
    own = own_patterns(definition)
    return {
        "version_id": definition.id,
        "label": definition.label,
        "own": own,
        "patterns": {which: own[which] or base.pattern(which) for which in PATTERNS},
        "episode_numbering": definition.episode_numbering or DEFAULT_NUMBERING,
        "source_id": source_id,
    }


# --- Values --------------------------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SeriesFacts:
    #: In the account language; the English title when the account's is empty.
    title: str
    year: int | None = None
    tmdb_id: int | None = None
    tvdb_id: int | None = None
    imdb_id: str | None = None


@dataclass(frozen=True)
class EpisodeFacts:
    #: The numbers of the name's numbering.
    season: int
    episode: int
    #: ``YYYY-MM-DD``.
    air_date: str | None = None
    #: A real title, or None: ``TBA``.
    title: str | None = None
    #: The number an anime series counts the episode by; None without one.
    absolute: int | None = None


def title_year(title: str, year: int | None) -> str:
    """Sonarr's ``{Series TitleYear}``: the year in brackets unless the title already ends with it."""
    if not year or _TRAILING_YEAR.search(title):
        return title
    return f"{title} ({year})"


def title_without_year(title: str) -> str:
    return _TRAILING_YEAR.sub("", title).strip() or title


def episode_title(titles: Sequence[str | None], joiner: str) -> str:
    """One title for the episodes of a file: ``TBA`` for a missing one; "Part (1)" and "Part (2)" become "Part"."""
    texts = [title if title else TBA for title in titles]
    if len(texts) > 1:
        parts = [_PART_NUMBER.match(text) for text in texts]
        if all(part is not None for part in parts) and len({part.group("title") for part in parts if part}) == 1:
            first = parts[0]
            return first.group("title") if first is not None else texts[0]
    return f" {joiner} ".join(texts)


def token_values(
    series: SeriesFacts, episodes: Sequence[EpisodeFacts], release: naming.ReleaseFacts | None
) -> dict[str, str]:
    """Every token's raw value, by lookup key. Numbers are raw; ``render`` pads them."""
    title = schreibweisen.nfc(series.title or "")
    without = title_without_year(title)
    with_year = title_year(title, series.year)
    values = {
        token_key("Series Title"): title,
        token_key("Series CleanTitle"): naming.clean_title(title),
        token_key("Series TitleYear"): with_year,
        token_key("Series CleanTitleYear"): naming.clean_title(with_year),
        token_key("Series TitleWithoutYear"): without,
        token_key("Series CleanTitleWithoutYear"): naming.clean_title(without),
        token_key("Series TitleThe"): naming.title_the(title),
        token_key("Series CleanTitleThe"): naming.clean_title_the(title),
        token_key("Series TitleTheYear"): naming.title_the(with_year),
        token_key("Series CleanTitleTheYear"): naming.clean_title_the(with_year),
        token_key("Series TitleTheWithoutYear"): naming.title_the(without),
        token_key("Series CleanTitleTheWithoutYear"): naming.clean_title_the(without),
        token_key("Series TitleFirstCharacter"): naming.first_character(title),
        token_key("Series Year"): str(series.year) if series.year else "",
        token_key("TvdbId"): str(series.tvdb_id) if series.tvdb_id else "",
        token_key("TmdbId"): str(series.tmdb_id) if series.tmdb_id else "",
        token_key("ImdbId"): series.imdb_id or "",
        token_key("TvMazeId"): "",
    }
    ordered = sorted(episodes, key=lambda item: (item.season, item.episode))
    if ordered:
        titles = [schreibweisen.nfc(item.title) if item.title else None for item in ordered]
        air = ordered[0].air_date or ""
        values.update(
            {
                SEASON_KEY: str(ordered[0].season),
                EPISODE_KEY: str(ordered[0].episode),
                AIR_DATE_KEY: air.replace("-", " "),
                token_key("Episode Title"): episode_title(titles, "+"),
                token_key("Episode CleanTitle"): naming.clean_title(episode_title(titles, "and")),
                ABSOLUTE_KEY: str(ordered[0].absolute) if ordered[0].absolute is not None else "",
            }
        )
    if release is None:
        return values
    parsed = releases.parse_series(release.release_title)
    quality = release.quality or parsed.quality.name
    proper = "Proper" if parsed.series.revision.version > 1 else ""
    real = "REAL" if parsed.series.revision.real > 0 else ""
    media = naming.release_media_facts(release, parsed.series.masked_title or release.release_title)
    values.update(
        {
            token_key("Quality Full"): " ".join(part for part in (quality, proper, real) if part),
            token_key("Quality Title"): quality,
            token_key("Quality Proper"): proper,
            token_key("Quality Real"): real,
            token_key("Custom Formats"): " ".join(release.formats),
            token_key("Release Group"): parsed.series.group or (release.release_group or ""),
            naming.ORIGINAL_KEY: schreibweisen.nfc(release.release_title),
            naming.ORIGINAL_FILENAME_KEY: schreibweisen.nfc(release.original_filename or ""),
            naming.FORMAT_NAMES_KEY: naming.FORMAT_NAMES_SEPARATOR.join(release.formats),
            **media,
        }
    )
    return values


# --- Multi-episode styles --------------------------------------------------------------------------------------- #


def _pad(number: int, pad: str | None) -> str:
    return str(number).zfill(len(pad)) if pad else str(number)


def _block(match: re.Match[str], season: int, numbers: list[int], style: str) -> str:
    prefix, letter = match.group("s"), match.group("e")
    season_text = _pad(season, match.group("season_pad"))
    episode_pad = match.group("episode_pad")
    first = prefix + season_text + letter + _pad(numbers[0], episode_pad)
    if len(numbers) == 1:
        return first
    rest = numbers[1:]
    if style == "extend":
        return first + "".join("-" + _pad(number, episode_pad) for number in rest)
    if style == "duplicate":
        return first + "".join("." + prefix + season_text + letter + _pad(number, episode_pad) for number in rest)
    if style == "repeat":
        return first + "".join(letter + _pad(number, episode_pad) for number in rest)
    if style == "scene":
        return first + "".join("-" + letter + _pad(number, episode_pad) for number in rest)
    if style == "range":
        return first + "-" + _pad(numbers[-1], episode_pad)
    return first + "-" + letter + _pad(numbers[-1], episode_pad)


def expand_episodes(pattern: str, episodes: Sequence[EpisodeFacts], style: str) -> str:
    """The pattern with the episode block written out for several episodes (braces of the block go, text stays).

    A single episode keeps its tokens: the renderer pads them as for any other pattern.
    """
    ordered = sorted(episodes, key=lambda item: (item.season, item.episode))
    if len(ordered) < 2:
        return pattern
    numbers = [item.episode for item in ordered]
    season = ordered[0].season
    replaced = _BLOCK.sub(lambda match: _block(match, season, numbers, style), pattern)
    counted = [item.absolute for item in ordered if item.absolute is not None]
    if len(counted) == len(ordered):
        # Numbers counted through as Sonarr writes them (its naming examples, measured 22.09.2026): the first and the
        # last in the range styles (``001-003``), every one in the others (``001-002-003``).
        shown = [counted[0], counted[-1]] if style in ("range", "prefixed_range") else counted
        replaced = _ABSOLUTE_PADDED.sub(
            lambda match: "-".join(_pad(number, match.group("pad")) for number in shown), replaced
        )
    # A lone {episode} outside the block: the first and the last, as a range.
    return _EPISODE.sub(
        lambda match: _pad(numbers[0], match.group("pad")) + "-" + _pad(numbers[-1], match.group("pad")), replaced
    )


# --- Names ---------------------------------------------------------------------------------------------------- #


def series_folder_name(current: SeriesNaming, series: SeriesFacts) -> str:
    return naming.render(
        current.series_folder,
        token_values(series, (), None),
        current.umlauts,
        max_bytes=naming.MAX_PART_BYTES,
        tiers=TIERS,
    )


def season_folder_name(current: SeriesNaming, series: SeriesFacts, season: int) -> str:
    pattern = current.specials_folder if season == 0 else current.season_folder
    values = token_values(series, (EpisodeFacts(season=season, episode=0),), None)
    return naming.render(pattern, values, current.umlauts, max_bytes=naming.MAX_PART_BYTES, tiers=TIERS)


@dataclass(frozen=True)
class FileName:
    name: str
    #: A title was missing: ``TBA`` stands in the name when the pattern names the title.
    named_tba: bool
    numbering: str = DEFAULT_NUMBERING
    episodes: tuple[EpisodeFacts, ...] = field(default_factory=tuple)


def file_name(
    current: SeriesNaming,
    series: SeriesFacts,
    episodes: Sequence[EpisodeFacts],
    release: naming.ReleaseFacts,
    extension: str,
    *,
    daily: bool = False,
    numbering: str = DEFAULT_NUMBERING,
    anime: bool = False,
    part: int | None = None,
) -> FileName:
    """The file name with its extension, leaving room for the temporary suffix of a transfer. An anime series files by
    the anime pattern (A5).

    A half of a double episode ends in `` - pt1`` or `` - pt2``, the way Plex reads an
    episode split over two files; the pattern stays as the owner set it, the part comes after it."""
    clean_extension = extension.lower() if re.fullmatch(r"\.[A-Za-z0-9-]{1,10}", extension) else ""
    if part in (1, 2):
        clean_extension = f" - pt{part}{clean_extension}"
    pattern = current.anime_file if anime else current.daily_file if daily else current.episode_file
    expanded = expand_episodes(pattern, episodes, current.style)
    values = token_values(series, episodes, release)
    name = naming.render(
        expanded,
        values,
        current.umlauts,
        max_bytes=naming.MAX_PART_BYTES - len(naming.PARTIAL_SUFFIX),
        suffix=clean_extension,
        tiers=TIERS,
    )
    names_title = any(key in _keys_of(pattern) for key in EPISODE_TITLE_KEYS)
    tba = names_title and any(not item.title for item in episodes)
    return FileName(name=name, named_tba=tba, numbering=numbering, episodes=tuple(episodes))


def _keys_of(pattern: str) -> set[str]:
    keys: set[str] = set()
    for match in naming._PATTERN.finditer(pattern):
        name = match.group("binner") or match.group("inner") or match.group("name")
        if name:
            keys.add(token_key(name))
    return keys


# --- The preview (decision 53) ---------------------------------------------------------------------------------- #

PREVIEW_SERIES = SeriesFacts(title="Beispielserie", year=2019, tmdb_id=900002, tvdb_id=800002, imdb_id="tt0000002")
PREVIEW_DAILY = SeriesFacts(title="Example Late Show", year=2015, tmdb_id=900003, tvdb_id=800003)
PREVIEW_RELEASE = naming.ReleaseFacts(release_title="Beispielserie.S02E05.1080p.WEB-DL-EXAMPLE")
PREVIEW_DAILY_RELEASE = naming.ReleaseFacts(release_title="Example.Late.Show.2026.09.16.720p.HDTV")


@dataclass(frozen=True)
class _Example:
    key: str
    series: SeriesFacts
    #: TMDB's numbers and TVDB's numbers (None where TVDB has none).
    tmdb: tuple[EpisodeFacts, ...]
    tvdb: tuple[EpisodeFacts, ...] | None
    release: naming.ReleaseFacts
    daily: bool = False
    anime: bool = False


PREVIEW_ANIME = SeriesFacts(title="Example Anime", year=2023, tmdb_id=900004, tvdb_id=800004)
PREVIEW_ANIME_RELEASE = naming.ReleaseFacts(release_title="[EXAMPLE] Example Anime - 17 (1080p)")


def _examples() -> list[_Example]:
    bridge = EpisodeFacts(2, 5, "2021-03-05", "Die Brücke")
    river = EpisodeFacts(2, 6, "2021-03-12", "Der Fluss")
    return [
        _Example("episode", PREVIEW_SERIES, (bridge,), (bridge,), PREVIEW_RELEASE),
        _Example("double", PREVIEW_SERIES, (bridge, river), (bridge, river), PREVIEW_RELEASE),
        _Example("tba", PREVIEW_SERIES, (EpisodeFacts(2, 7, "2021-03-19", None),), None, PREVIEW_RELEASE),
        _Example(
            "daily", PREVIEW_DAILY, (EpisodeFacts(12, 140, "2026-09-16", "Gast"),), None, PREVIEW_DAILY_RELEASE, True
        ),
        _Example("special", PREVIEW_SERIES, (EpisodeFacts(0, 1, "2020-12-24", "Rückblick"),), None, PREVIEW_RELEASE),
        # The same special after TMDB (S00E09) and TVDB (S00E03): the numbers of the name follow the version.
        _Example(
            "special_numbering",
            PREVIEW_SERIES,
            (EpisodeFacts(0, 9, "2021-06-01", "Hinter den Kulissen"),),
            (EpisodeFacts(0, 3, "2021-06-01", "Hinter den Kulissen"),),
            PREVIEW_RELEASE,
        ),
        # TMDB's German name is a placeholder ("Folge 4"): the English one stands in the name.
        _Example(
            "special_placeholder",
            PREVIEW_SERIES,
            (EpisodeFacts(0, 4, "2021-07-01", "A Look Back"),),
            None,
            PREVIEW_RELEASE,
        ),
        # An anime series: the number counted through next to season and episode (A5).
        _Example(
            "anime",
            PREVIEW_ANIME,
            (EpisodeFacts(2, 5, "2024-02-02", "Neuer Morgen", absolute=17),),
            None,
            PREVIEW_ANIME_RELEASE,
            anime=True,
        ),
    ]


def preview(current: SeriesNaming) -> dict[str, Any]:
    """The series folder and, per example, season folder and file name, in the numbering of ``current``."""
    items = []
    for example in _examples():
        episodes = example.tvdb if current.numbering == "tvdb" and example.tvdb is not None else example.tmdb
        season = episodes[0].season
        built = file_name(
            current, example.series, episodes, example.release, ".mkv", daily=example.daily, anime=example.anime
        )
        items.append(
            {
                "key": example.key,
                "season_folder": season_folder_name(current, example.series, season),
                "file": built.name,
            }
        )
    return {"series_folder": series_folder_name(current, PREVIEW_SERIES), "examples": items}
