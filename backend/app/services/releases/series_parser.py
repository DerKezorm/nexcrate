"""Reading a series release name: series title and year, the numbering form, and the movie reader's features.

nexcrate's own reading, written from the forms in the design notes (part S2.3, decisions 12 to 15). Nothing is
taken from Sonarr's source (GPL-3.0): no code, no pattern, no test. Group, languages, quality, revision and hardcoded
subtitles come from the movie parser (``parser.py``).

Order of work
-------------
1. As for movies: the file extension goes, then dashes and underscores at the ends, then ``<>?*|``. What is left is
   the release title.
2. From the left, the first place after at least one title word where a numbering form matches ends the title
   (decision 13). At that place the most exact form wins, in the order of ``_FORMS``. A year right before the number
   belongs to the title and is returned as ``year``. A subgroup in brackets at the very front is no title word.
3. Group, languages, quality, revision and hardcoded subtitles as for movies. Group and languages are read with the
   series title masked, so "German" in a series title is no language.
4. The release type follows from the form: single episode, multi episode, season pack. Refused forms have none.

Forms
-----
* ``standard``: ``S01E05``, ``s01e05``, ``S01.E05``, ``S01.Ep.05``, ``1x05``, ``[1x05]``, ``Season.1.Episode.5``,
  ``Staffel.1.Folge.5``, a version tag (``S01E05v2``).
* ``multi_episode``: ``S01E05E06`` and ``S01E05-E08`` are spans: everything from the first to the last number
  belongs to it, as Sonarr reads it (measured 16.09.2026: ``S01E05E08`` gives it the episodes 5 to 8). Other
  forms of a range: ``S01E05-06``, ``1x05-1x06``, ``Staffel.1.Folge.5-6``.
* ``season_pack``: ``S01``, ``Season.1``, ``Saison.1``, ``Staffel.1``; ``COMPLETE`` after it is a feature.
* ``daily``: ``2026.09.14``, ``2026-09-14``, ``20260914``, day first ``14.09.2026``, with ``Part.2`` as the part of
  the day. A day first date that reads both ways gives two candidates in ``air_dates`` and no ``air_date``.
* ``mini_series``: ``Part.3``, ``Part.Three``, ``3of6``, ``Teil.3`` and ``Folge.5``: only an episode, no season. The
  mapping (S2.4) takes it only for a series with exactly one regular season.

Refused, each with its own code in ``refused`` (decision 15): ``multi_season`` (``S01-S03``, ``S01-03``,
``Season.1-3``, ``Staffel.1-3``, ``S01.S02``; seasons in ``seasons``), ``complete_series`` (``Complete.Series``,
``Die.komplette.Serie``, ``Komplett`` without a season), ``split_episode`` (``S01E05a``), ``partial_season``
(``S01.Part.1``, ``S01.Vol.1``, ``Staffel.1.Teil.1``), ``season_extras`` (``S01.Extras``, ``S01.SUBPACK``,
``Staffel.1.Bonusmaterial``), ``anime_numbering`` (``Show - 148``, ``Show - 01-12``, ``E148``; numbers in
``absolute``), ``no_numbering``, and ``implausible_range`` for a range of more than 100 episodes or seasons, or one
that runs backwards.

Anime (A2)
------------------------------
Read for a series of the type anime (``anime=True``), the numbering through the whole series is taken, not refused:
``[Group] Show - 148``, ``Show - 148v2``, ``Show - 01-12`` and ``Show - 01 ~ 12``, a batch in brackets
``Show (01-12)`` or ``Show [001-500]``, and ``Show E148``, ``Show EP148``, ``Show Episode 148``. A range may hold up to
``ANIME_MAX_RANGE`` episodes: a batch is often the whole series. ``Show S2 - 05`` and ``Show Season 2 - 05`` are
season 2, episode 5, as the weekly releases of a later season name them. The form stays ``anime`` with the numbers in
``absolute``; the mapping looks them up in the scheme ``absolute`` (``series/release_match.py``). Every other form
reads as for any series. Without
``anime`` nothing changes: a name counted through is refused, and neither the brackets nor ``S2 - 05`` are read.

Added with B6, from 303 real names of the owner's indexer read beside Sonarr on 23.09.2026:
the dash may stand between dots (``One.Piece.-.1179``, ``Sousou.no.Frieren.S2.-.07``); a bare number before a bracket
or at the end is counted through (``[HatSubs] One Piece 1179 (WEB 1080p)``, ``One.Piece.1177.[1080p]``), as Sonarr
reads it, but never a year and never a number followed by a year in brackets (``Mob Psycho 100 (2016)``); and a season
named as an ordinal is a season (``Sousou no Frieren 2nd Season - 10``, ``Haikyuu!! Second Season - 04``, and
``2nd Season`` alone as a season pack).

Limits
------
Season and episode 0 to 9999, a range at most 100 episodes, a date from 1940 to the year after ``today``. Every form is
a pattern without nested repetition; episode chains are read by a loop, one link at a time, so reading time grows
linearly with the length of the name.

Deviations from Sonarr, as the plan states them
-----------------------------------------------
* The German forms ``Staffel N``, ``Staffel N Folge M``, ``Folge M``, ``Staffel N-M``, ``komplette Serie``,
  ``Komplett`` and ``Teil`` after a season: Sonarr knows none of them (decision 14).
* Multi season packs, complete series, split episodes, partial packs, bonus material and anime numbering are
  recognised and refused with a code of their own, so the checker can show what was found (decision 15). A name
  counted through is taken for an anime series (see above).

Kept open here, decided elsewhere
---------------------------------
* Masking: the title is replaced by "A Series" ("A.Series" with dots) before group and languages are read. Whether
  Sonarr masks the series title is not measured yet (decision 18); the placeholder follows the measurement.
* Quality is the movie reader's ``Quality``; the mapping to Sonarr's names (decision 16) is a separate part.
* Languages are left as found (``ORIGINAL`` and ``UNKNOWN`` unresolved), as in the movie parser; the series'
  original language fills in later through ``parser.aggregate_languages``.
* No title is read from a name without a numbering form.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime

from .. import schreibweisen
from . import qualities as q
from . import qualities_series as qs
from .parser import (
    Revision,
    parse_group,
    parse_hardcoded_subs,
    parse_languages,
    parse_quality,
    parse_revision,
    release_title,
)

_I = re.IGNORECASE

#: A range may hold at most this many episodes or seasons.
MAX_RANGE = 100
#: A range of an anime series counted through: a batch is often the whole series (One Piece has over 1,100).
ANIME_MAX_RANGE = 2000
#: The oldest year a daily date may carry.
FIRST_DAILY_YEAR = 1940
#: Links of an episode chain read at most; far more than a 500 character name can hold.
_MAX_LINKS = 300

FORMS = (
    "standard",
    "multi_episode",
    "season_pack",
    "daily",
    "mini_series",
    "multi_season",
    "complete_series",
    "split_episode",
    "partial_season",
    "season_extras",
    "anime",
    "none",
)
IMPLAUSIBLE_RANGE = "implausible_range"

_TOKEN = re.compile(r"[^\s._\-\[\]()]+")
_LEAD_GROUP = re.compile(r"\[([A-Za-z0-9][A-Za-z0-9 ._&-]{0,39})\]")
_YEAR = re.compile(r"(?:18|19|20)\d{2}")
#: What a dashed or bracketed word must not be to count as the group.
_NOT_A_GROUP = re.compile(r"\d{3,4}[pi]|[0-9a-f]{8}|\d{1,2}x\d{1,3}|\d+|[se]\d{1,4}(?:e\d{1,4})?", _I)

_SEP = r"[._ ]"
#: What may follow a form: the end, a separator or a bracket.
_END = r"(?=$|[\s._\-\[\](),+])"
#: What may follow a number inside an episode chain: the end of the form, another link or a version tag.
_LINK = r"(?=$|[\s._\-\[\](),+]|[ev]\d)"
_ENDS = re.compile(_END)
_VERSION = re.compile(r"v\d", _I)

_SEASON_WORD = r"(?:Seasons|Season|Saison|Staffeln|Staffel)"
_PART_WORD = r"(?:Part|Pt|Volume|Vol|Teil)"
_EXTRAS_WORD = r"(?:Extras|Bonusmaterial|Bonus|Subpack)"

# --- Patterns, one per form ---------------------------------------------------------------- #

_S_SPLIT = re.compile(rf"S(\d{{1,4}}){_SEP}?E(\d{{1,4}})[a-z]{_END}", _I)
_S_EPISODE = re.compile(rf"S(\d{{1,4}})(?:{_SEP}?E|{_SEP}?Ep{_SEP}?|{_SEP}Episode{_SEP}?)(\d{{1,4}}){_LINK}", _I)
_W_EPISODE = re.compile(rf"{_SEASON_WORD}{_SEP}?(\d{{1,4}}){_SEP}(?:Episode|Folge|Ep){_SEP}?(\d{{1,4}}){_LINK}", _I)
_X_EPISODE = re.compile(rf"(\d{{1,2}})x(\d{{1,3}}){_LINK}", _I)

_S_SEASON_RANGE = re.compile(rf"S(\d{{1,4}}){_SEP}?-{_SEP}?S?(\d{{1,4}}){_END}", _I)
_S_SEASON_LIST_HEAD = re.compile(rf"S(\d{{1,4}})(?={_SEP}S\d)", _I)
_S_SEASON_LIST_LINK = re.compile(rf"{_SEP}S(\d{{1,4}}){_END}", _I)
_W_SEASON_RANGE = re.compile(rf"{_SEASON_WORD}{_SEP}?(\d{{1,4}}){_SEP}?-{_SEP}?(\d{{1,4}}){_END}", _I)

_S_PARTIAL = re.compile(rf"S(\d{{1,4}}){_SEP}{_PART_WORD}{_SEP}?(\d{{1,3}}){_END}", _I)
_W_PARTIAL = re.compile(rf"{_SEASON_WORD}{_SEP}?(\d{{1,4}}){_SEP}{_PART_WORD}{_SEP}?(\d{{1,3}}){_END}", _I)
_S_EXTRAS = re.compile(rf"S(\d{{1,4}}){_SEP}{_EXTRAS_WORD}{_END}", _I)
_W_EXTRAS = re.compile(rf"{_SEASON_WORD}{_SEP}?(\d{{1,4}}){_SEP}{_EXTRAS_WORD}{_END}", _I)
_S_PACK = re.compile(rf"S(\d{{1,4}}){_END}", _I)
_W_PACK = re.compile(rf"{_SEASON_WORD}{_SEP}?(\d{{1,4}}){_END}", _I)

_DATE_YMD = re.compile(rf"((?:19|20)\d{{2}})([-._ ])(\d{{2}})\2(\d{{2}}){_END}")
_DATE_COMPACT = re.compile(rf"((?:19|20)\d{{2}})(\d{{2}})(\d{{2}}){_END}")
_DATE_DMY = re.compile(rf"(\d{{2}})([-._ ])(\d{{2}})\2((?:19|20)\d{{2}}){_END}")
_DATE_PART = re.compile(rf"{_SEP}(?:Part|Pt|Teil){_SEP}?(\d{{1,2}}){_END}", _I)

_COMPLETE_SERIES = re.compile(rf"(?:(?:The|Die){_SEP})?(?:Complete{_SEP}Series|Komplette{_SEP}Serie){_END}", _I)
_KOMPLETT = re.compile(rf"Komplett(?!{_SEP}(?:S\d|Staffel|Season|Saison)){_END}", _I)

_MINI_PART = re.compile(rf"(?:Part|Pt|Teil){_SEP}?(\d{{1,3}}){_END}", _I)
_NUMBER_WORDS = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")
_MINI_WORD = re.compile(rf"Part{_SEP}({'|'.join(_NUMBER_WORDS)}){_END}", _I)
_MINI_OF = re.compile(rf"(\d{{1,3}})of(\d{{1,3}}){_END}", _I)
_FOLGE = re.compile(rf"Folge{_SEP}?(\d{{1,4}}){_LINK}", _I)

_ANIME_DASH = re.compile(rf"(?<=[\s._]-[\s._])(\d{{1,4}})(?:v\d)?(?:(?:-| ?~ ?)(\d{{1,4}})(?:v\d)?)?{_END}", _I)
_ANIME_E = re.compile(rf"E(\d{{2,4}})(?:v\d)?{_END}", _I)
#: Only for an anime series: a batch in brackets, the episode word, and a later season named before the number.
_ANIME_BATCH = re.compile(r"(?<=[\[(])(\d{1,4}) ?[-~] ?(\d{1,4})(?=[\])])")
_ANIME_WORD = re.compile(rf"(?:EP|Episode){_SEP}?(\d{{1,4}})(?:v\d)?{_END}", _I)
_ANIME_SEASON_DASH = re.compile(rf"(?:S|{_SEASON_WORD}{_SEP}?)(\d{{1,2}}){_SEP}-{_SEP}(\d{{1,4}})(?:v\d)?{_END}", _I)
_ORDINAL_WORDS = ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth")
#: ``2nd Season`` or ``Second Season``, with or without `` - 05`` after it.
_ANIME_ORDINAL_SEASON = re.compile(
    rf"(?:(\d{{1,2}})(?:st|nd|rd|th)|({'|'.join(_ORDINAL_WORDS)})){_SEP}Season"
    rf"(?:{_SEP}-{_SEP}(\d{{1,4}})(?:v\d)?)?{_END}",
    _I,
)
#: A bare number counted through, before a bracket or at the end: ``One Piece 1179 (WEB 1080p)``.
_ANIME_BARE = re.compile(r"(\d{2,4})(?:v\d)?(?=[\s._]*[\[(]|$)(?![\s._]*[\[(](?:19|20)\d{2}[\])])", _I)

#: Links of an episode chain: the pattern and whether it closes a range. A link with two numbers names the season
#: again and counts only for the same season.
_Tail = tuple[re.Pattern[str], bool]
_S_TAILS: tuple[_Tail, ...] = (
    (re.compile(rf"{_SEP}?E(\d{{1,4}}){_LINK}", _I), False),
    (re.compile(rf"-{_SEP}?E(\d{{1,4}}){_LINK}", _I), True),
    (re.compile(rf"-S(\d{{1,4}})E(\d{{1,4}}){_LINK}", _I), True),
    (re.compile(rf"-(\d{{1,4}}){_LINK}"), True),
)
_X_TAILS: tuple[_Tail, ...] = (
    (re.compile(rf"-(\d{{1,2}})x(\d{{1,3}}){_LINK}", _I), True),
    (re.compile(rf"-(\d{{1,4}}){_LINK}"), True),
)
_DASH_TAILS: tuple[_Tail, ...] = ((re.compile(rf"-(\d{{1,4}}){_LINK}"), True),)


# --- Result -------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ParsedSeries:
    #: The series title as the name spells it (dots and underscores as spaces); None without a numbering form.
    series_title: str | None
    #: Comparison forms of the title from ``schreibweisen.keys``.
    title_keys: tuple[str, ...]
    year: int | None
    #: One of ``FORMS``.
    form: str
    #: ``single_episode``, ``multi_episode`` or ``season_pack``; None for a refused form.
    release_type: str | None
    season: int | None
    #: The seasons of a multi season pack.
    seasons: tuple[int, ...]
    episodes: tuple[int, ...]
    #: The date of a daily episode when it reads one way only.
    air_date: date | None
    #: Every reading of a daily date: one, or two for a day first date that reads both ways.
    air_dates: tuple[date, ...]
    #: The part of a daily episode, or of a partial season pack.
    part: int | None
    absolute: tuple[int, ...]
    #: ``season`` (one whole season), ``part_of_season``, ``seasons``, ``series``; None for episodes.
    pack_scope: str | None
    #: The code of a refused form, None when the form is taken.
    refused: str | None
    group: str | None
    quality: q.Quality
    revision: Revision
    #: As found in the name: Radarr's numbers, with ``ORIGINAL`` and ``UNKNOWN`` still unresolved.
    languages: tuple[int, ...]
    hardcoded_subs: str | None
    #: The release title as it stands, the series title included. ⚠️ The custom formats see exactly this:
    #: Sonarr does not mask the series title (measured 16.09.2026 at the bench, decision 18).
    release_title: str
    #: The same with the series title replaced, used to read group and languages.
    masked_title: str
    #: The resolution the name carries (2160, 1080, 720, ...), or None. Sonarr falls back to it for a source it
    #: does not know.
    resolution_hint: int | None

    @property
    def is_pack(self) -> bool:
        """A season pack, or a batch of an anime series counted through (``01-12``): nexcrate's own pack rules treat
        both alike (decision 10). ``release_type`` stays Sonarr's: a batch is a multi episode."""
        return self.release_type == "season_pack" or (
            self.form == "anime" and self.refused is None and len(self.absolute) > 1
        )


@dataclass(frozen=True)
class _Hit:
    form: str
    end: int
    season: int | None = None
    seasons: tuple[int, ...] = ()
    episodes: tuple[int, ...] = ()
    air_dates: tuple[date, ...] = ()
    part: int | None = None
    absolute: tuple[int, ...] = ()
    pack_scope: str | None = None
    refused: str | None = None


_NO_HIT = _Hit("none", 0, refused="no_numbering")
_Form = Callable[[str, int, date], "_Hit | None"]


# --- Episode chains ------------------------------------------------------------------------ #


def _next_link(text: str, pos: int, season: int | None, tails: tuple[_Tail, ...]) -> tuple[int, bool, int] | None:
    """The episode number of the link at ``pos``, whether it closes a range, and where it ends."""
    for pattern, ranged in tails:
        match = pattern.match(text, pos)
        if match is None:
            continue
        numbers = [int(value) for value in match.groups()]
        if len(numbers) == 2:
            if numbers[0] != season:
                continue
            return numbers[1], ranged, match.end()
        return numbers[0], ranged, match.end()
    return None


def _chain(
    text: str, pos: int, season: int | None, first: int, tails: tuple[_Tail, ...]
) -> tuple[tuple[int, ...], int, bool] | None:
    """Episodes from the first number on, where the chain ends and whether it is implausible.

    None when the chain does not end at a boundary (after an optional version tag).
    """
    episodes = [first]
    implausible = False
    for _ in range(_MAX_LINKS):
        link = _next_link(text, pos, season, tails)
        if link is None:
            break
        value, _ranged, pos = link
        # Every link opens a span to the number before it, with or without a dash: Sonarr reads S01E05E08 as
        # the episodes 5 to 8 (measured 16.09.2026), and so does nexcrate.
        start = episodes[-1]
        if value <= start or value - start >= MAX_RANGE:
            implausible = True
        else:
            episodes.extend(range(start + 1, value + 1))
    version = _VERSION.match(text, pos)
    if version is not None:
        pos = version.end()
    if _ENDS.match(text, pos) is None:
        return None
    return tuple(episodes), pos, implausible or len(episodes) > MAX_RANGE


def _episodes_hit(season: int | None, chain: tuple[tuple[int, ...], int, bool] | None, single: str) -> _Hit | None:
    if chain is None:
        return None
    episodes, end, implausible = chain
    if implausible:
        return _Hit("multi_episode" if single == "standard" else single, end, season=season, refused=IMPLAUSIBLE_RANGE)
    form = single if single != "standard" or len(episodes) == 1 else "multi_episode"
    return _Hit(form, end, season=season, episodes=episodes)


def _seasons_hit(end: int, seasons: list[int], ranged: bool) -> _Hit:
    if ranged:
        first, last = seasons
        if last <= first or last - first >= MAX_RANGE:
            return _Hit("multi_season", end, pack_scope="seasons", refused=IMPLAUSIBLE_RANGE)
        seasons = list(range(first, last + 1))
    unique = tuple(dict.fromkeys(seasons))
    return _Hit("multi_season", end, seasons=unique, pack_scope="seasons", refused="multi_season")


# --- Forms, most exact first --------------------------------------------------------------- #


def _split_episode(text: str, pos: int, today: date) -> _Hit | None:
    match = _S_SPLIT.match(text, pos)
    if match is None:
        return None
    return _Hit("split_episode", match.end(), season=int(match[1]), episodes=(int(match[2]),), refused="split_episode")


def _s_episode(text: str, pos: int, today: date) -> _Hit | None:
    match = _S_EPISODE.match(text, pos)
    if match is None:
        return None
    season = int(match[1])
    return _episodes_hit(season, _chain(text, match.end(), season, int(match[2]), _S_TAILS), "standard")


def _written_episode(text: str, pos: int, today: date) -> _Hit | None:
    match = _W_EPISODE.match(text, pos)
    if match is None:
        return None
    season = int(match[1])
    return _episodes_hit(season, _chain(text, match.end(), season, int(match[2]), _DASH_TAILS), "standard")


def _s_seasons(text: str, pos: int, today: date) -> _Hit | None:
    match = _S_SEASON_RANGE.match(text, pos)
    if match is not None:
        return _seasons_hit(match.end(), [int(match[1]), int(match[2])], ranged=True)
    head = _S_SEASON_LIST_HEAD.match(text, pos)
    if head is None:
        return None
    seasons = [int(head[1])]
    end = head.end()
    for _ in range(_MAX_LINKS):
        link = _S_SEASON_LIST_LINK.match(text, end)
        if link is None:
            break
        seasons.append(int(link[1]))
        end = link.end()
    if len(seasons) == 1:
        return None
    return _seasons_hit(end, seasons, ranged=False)


def _written_seasons(text: str, pos: int, today: date) -> _Hit | None:
    match = _W_SEASON_RANGE.match(text, pos)
    if match is None:
        return None
    return _seasons_hit(match.end(), [int(match[1]), int(match[2])], ranged=True)


def _partial_season(text: str, pos: int, today: date) -> _Hit | None:
    match = _S_PARTIAL.match(text, pos) or _W_PARTIAL.match(text, pos)
    if match is None:
        return None
    return _Hit(
        "partial_season",
        match.end(),
        season=int(match[1]),
        part=int(match[2]),
        pack_scope="part_of_season",
        refused="partial_season",
    )


def _season_extras(text: str, pos: int, today: date) -> _Hit | None:
    match = _S_EXTRAS.match(text, pos) or _W_EXTRAS.match(text, pos)
    if match is None:
        return None
    return _Hit("season_extras", match.end(), season=int(match[1]), refused="season_extras")


def _season_pack(text: str, pos: int, today: date) -> _Hit | None:
    match = _S_PACK.match(text, pos) or _W_PACK.match(text, pos)
    if match is None:
        return None
    return _Hit("season_pack", match.end(), season=int(match[1]), pack_scope="season")


def _x_episode(text: str, pos: int, today: date) -> _Hit | None:
    match = _X_EPISODE.match(text, pos)
    if match is None:
        return None
    season = int(match[1])
    return _episodes_hit(season, _chain(text, match.end(), season, int(match[2]), _X_TAILS), "standard")


def _valid_dates(today: date, *readings: tuple[int, int, int]) -> tuple[date, ...]:
    found: list[date] = []
    for year, month, day in readings:
        if not FIRST_DAILY_YEAR <= year <= today.year + 1:
            continue
        try:
            value = date(year, month, day)
        except ValueError:
            continue
        if value not in found:
            found.append(value)
    return tuple(found)


def _daily(text: str, pos: int, today: date) -> _Hit | None:
    dates: tuple[date, ...] = ()
    end = pos
    ymd = _DATE_YMD.match(text, pos)
    compact = _DATE_COMPACT.match(text, pos)
    dmy = _DATE_DMY.match(text, pos)
    if ymd is not None:
        dates, end = _valid_dates(today, (int(ymd[1]), int(ymd[3]), int(ymd[4]))), ymd.end()
    elif compact is not None:
        dates, end = _valid_dates(today, (int(compact[1]), int(compact[2]), int(compact[3]))), compact.end()
    elif dmy is not None:
        first, second, year = int(dmy[1]), int(dmy[3]), int(dmy[4])
        dates, end = _valid_dates(today, (year, second, first), (year, first, second)), dmy.end()
    if not dates:
        return None
    part = _DATE_PART.match(text, end)
    if part is not None:
        return _Hit("daily", part.end(), air_dates=dates, part=int(part[1]))
    return _Hit("daily", end, air_dates=dates)


def _complete_series(text: str, pos: int, today: date) -> _Hit | None:
    match = _COMPLETE_SERIES.match(text, pos) or _KOMPLETT.match(text, pos)
    if match is None:
        return None
    return _Hit("complete_series", match.end(), pack_scope="series", refused="complete_series")


def _mini_series(text: str, pos: int, today: date) -> _Hit | None:
    match = _MINI_PART.match(text, pos)
    if match is not None:
        return _Hit("mini_series", match.end(), episodes=(int(match[1]),))
    match = _MINI_WORD.match(text, pos)
    if match is not None:
        return _Hit("mini_series", match.end(), episodes=(_NUMBER_WORDS.index(match[1].lower()) + 1,))
    match = _MINI_OF.match(text, pos)
    if match is not None:
        number, total = int(match[1]), int(match[2])
        if 1 <= number <= total:
            return _Hit("mini_series", match.end(), episodes=(number,))
        return None
    match = _FOLGE.match(text, pos)
    if match is not None:
        return _episodes_hit(None, _chain(text, match.end(), None, int(match[1]), _DASH_TAILS), "mini_series")
    return None


def _absolute_range(first: int, last: int, end: int, limit: int) -> _Hit:
    if last <= first or last - first >= limit:
        return _Hit("anime", end, refused=IMPLAUSIBLE_RANGE)
    return _Hit("anime", end, absolute=tuple(range(first, last + 1)), refused="anime_numbering")


def _anime(text: str, pos: int, today: date, limit: int = MAX_RANGE) -> _Hit | None:
    dash = _ANIME_DASH.match(text, pos)
    if dash is not None:
        first = int(dash[1])
        if len(dash[1]) == 4 and _YEAR.fullmatch(dash[1]):
            # A year after " - " belongs to the title.
            return None
        if dash[2] is None:
            return _Hit("anime", dash.end(), absolute=(first,), refused="anime_numbering")
        return _absolute_range(first, int(dash[2]), dash.end(), limit)
    match = _ANIME_E.match(text, pos)
    if match is None:
        return None
    return _Hit("anime", match.end(), absolute=(int(match[1]),), refused="anime_numbering")


def _anime_series(text: str, pos: int, today: date) -> _Hit | None:
    """The forms of a series counted through, read only for an anime series; taken, not refused."""
    match = _ANIME_SEASON_DASH.match(text, pos)
    if match is not None:
        return _Hit("standard", match.end(), season=int(match[1]), episodes=(int(match[2]),))
    match = _ANIME_ORDINAL_SEASON.match(text, pos)
    if match is not None:
        season = int(match[1]) if match[1] else _ORDINAL_WORDS.index(match[2].casefold()) + 1
        if not 0 < season <= 99:
            return None
        if match[3] is None:
            return _Hit("season_pack", match.end(), season=season)
        return _Hit("standard", match.end(), season=season, episodes=(int(match[3]),))
    return None


def _anime_numbers(text: str, pos: int, today: date) -> _Hit | None:
    match = _ANIME_BATCH.match(text, pos)
    if match is not None and not (_YEAR.fullmatch(match[1]) and _YEAR.fullmatch(match[2])):
        return _absolute_range(int(match[1]), int(match[2]), match.end(), ANIME_MAX_RANGE)
    match = _ANIME_WORD.match(text, pos)
    if match is not None:
        return _Hit("anime", match.end(), absolute=(int(match[1]),), refused="anime_numbering")
    found = _anime(text, pos, today, ANIME_MAX_RANGE)
    if found is not None:
        return found
    match = _ANIME_BARE.match(text, pos)
    if match is not None and not _YEAR.fullmatch(match[1]):
        return _Hit("anime", match.end(), absolute=(int(match[1]),), refused="anime_numbering")
    return None


#: Tried in this order at each place; the first that matches is the most exact.
_FORMS: tuple[_Form, ...] = (
    _split_episode,
    _s_episode,
    _written_episode,
    _s_seasons,
    _written_seasons,
    _partial_season,
    _season_extras,
    _season_pack,
    _x_episode,
    _daily,
    _complete_series,
    _mini_series,
    _anime,
)
#: The same for an anime series: a later season named before the number (``S2 - 05``) goes before the season ranges,
#: which would read it as seasons 2 to 5, and a name counted through is read with its batches.
_ANIME_FORMS: tuple[_Form, ...] = (
    _split_episode,
    _s_episode,
    _written_episode,
    _anime_series,
    _s_seasons,
    _written_seasons,
    _partial_season,
    _season_extras,
    _season_pack,
    _x_episode,
    _daily,
    _complete_series,
    _mini_series,
    _anime_numbers,
)


# --- All together -------------------------------------------------------------------------- #


def _release_type(hit: _Hit) -> str | None:
    if hit.refused is not None:
        return None
    if hit.form == "season_pack":
        return "season_pack"
    if hit.form == "multi_episode" or len(hit.episodes) > 1 or len(hit.absolute) > 1:
        return "multi_episode"
    return "single_episode"


def _find(
    text: str, tokens: list[re.Match[str]], first: int, today: date, forms: tuple[_Form, ...] = _FORMS
) -> tuple[int, _Hit] | None:
    for index in range(first + 1, len(tokens)):
        pos = tokens[index].start()
        for form in forms:
            hit = form(text, pos, today)
            if hit is not None:
                return index, hit
    return None


#: Forms that name episodes exactly. One of them later in the name takes a weaker form before it into the title
#: (decision 1).
_EXACT_FORMS = frozenset({"standard", "multi_episode", "daily"})
#: A word that starts the features: resolution, source, language, codec or release tag.
_FEATURE = re.compile(
    r"\d{3,4}[pi]|web|web-?dl|web-?rip|webrip|webdl|blu-?ray|bdrip|brrip|hdtv|dvd(?:rip)?|remux|uhd|hdr\d*|dv|sdr"
    r"|german|english|dl|ml|multi|dual|x26[45]|h\.?26[45]|hevc|avc|av1|xvid|repack\d?|proper|internal|real|dubbed|subbed",
    _I,
)


def _exact_later(
    text: str, tokens: list[re.Match[str]], index: int, today: date, forms: tuple[_Form, ...] = _FORMS
) -> tuple[int, _Hit] | None:
    """An exact form after ``index`` and before the first feature word, or None."""
    for later in range(index + 1, len(tokens)):
        if _FEATURE.fullmatch(tokens[later].group()):
            return None
        pos = tokens[later].start()
        for form in forms:
            hit = form(text, pos, today)
            if hit is not None and hit.form in _EXACT_FORMS:
                return later, hit
    return None


#: Title words a known series title may hold at most, when it is read past (decision 2).
_MAX_TITLE_WORDS = 16


def _known_title_end(tokens: list[re.Match[str]], first: int, titles: tuple[str, ...]) -> int:
    """The index of the last word of the longest known series title the name starts with, or ``first - 1``."""
    wanted: set[str] = set()
    for title in titles:
        wanted.update(schreibweisen.keys(title))
    if not wanted:
        return first - 1
    best = first - 1
    words: list[str] = []
    for index in range(first, min(len(tokens), first + _MAX_TITLE_WORDS)):
        words.append(tokens[index].group())
        if not wanted.isdisjoint(schreibweisen.keys(" ".join(words))):
            best = index
    return best


_RESOLUTION = re.compile(r"(?<![a-z0-9])(\d{3,4})[pi](?![a-z0-9])", _I)


def _resolution_hint(text: str) -> int | None:
    """The last resolution the name names, as Sonarr reads it for a source it does not know."""
    found = _RESOLUTION.findall(text)
    return int(found[-1]) if found else None


def _quality(name: str) -> q.Quality:
    """The quality of the name. Where Radarr finds one Sonarr does not have, the name is read again as Sonarr does."""
    quality = parse_quality(name)
    return parse_quality(name, as_sonarr=True) if quality.name in qs.READ_AS_SONARR else quality


def _group(masked: str, lead: re.Match[str] | None) -> str | None:
    group = parse_group(masked)
    if group is not None and _NOT_A_GROUP.fullmatch(group):
        group = None
    if group is None and lead is not None:
        group = lead[1].strip()
    return group


def parse_series(
    name: str, *, today: date | None = None, titles: tuple[str, ...] = (), anime: bool = False
) -> ParsedSeries:
    """Read a series release name. ``today`` sets the latest year a daily date may carry (the year after it).

    ``titles`` are the titles of the series the name is read for, when it is known (search, checker with a series):
    a name that starts with one of them is read from behind it, so a title such as "Season 2" stays a title.
    ``anime``: the name is read for an anime series, and a name counted through is taken (see the module).
    """
    today = today or datetime.now(UTC).date()
    text = release_title(name or "")
    lead = _LEAD_GROUP.match(text)
    tokens = list(_TOKEN.finditer(text))
    first = next((index for index, token in enumerate(tokens) if lead is None or token.start() >= lead.end()), None)
    found = None
    if first is not None:
        title_end = _known_title_end(tokens, first, titles)
        forms = _ANIME_FORMS if anime else _FORMS
        found = _find(text, tokens, max(first, title_end), today, forms)
        if found is not None and found[1].form not in _EXACT_FORMS:
            found = _exact_later(text, tokens, found[0], today, forms) or found
        if found is not None and anime and found[1].refused == "anime_numbering":
            found = (found[0], replace(found[1], refused=None))

    title: str | None = None
    year: int | None = None
    masked = text
    hit = _NO_HIT
    if found is not None and first is not None:
        index, hit = found
        last = index - 1
        if last > first and _YEAR.fullmatch(tokens[last].group()):
            year = int(tokens[last].group())
            last -= 1
        start, end = tokens[first].start(), tokens[last].end()
        title = " ".join(text[start:end].replace(".", " ").replace("_", " ").split()) or None
        masked = text[:start] + ("A.Series" if "." in text[start:end] else "A Series") + text[end:]

    group = _group(masked, lead)
    language_text = masked.replace(group, "RlsGrp") if group else masked
    return ParsedSeries(
        series_title=title,
        title_keys=tuple(schreibweisen.keys(title)),
        year=year,
        form=hit.form,
        release_type=_release_type(hit),
        season=hit.season,
        seasons=hit.seasons,
        episodes=hit.episodes,
        air_date=hit.air_dates[0] if len(hit.air_dates) == 1 else None,
        air_dates=hit.air_dates,
        part=hit.part,
        absolute=hit.absolute,
        pack_scope=hit.pack_scope,
        refused=hit.refused,
        group=group,
        quality=_quality(name or ""),
        revision=parse_revision(name or ""),
        languages=parse_languages(language_text),
        hardcoded_subs=parse_hardcoded_subs(name or ""),
        release_title=text,
        masked_title=masked,
        resolution_hint=_resolution_hint(text),
    )
