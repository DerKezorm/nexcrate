"""Which episode a video of a series download is (S4.3, decisions 17 to 23, 33, 34).

No database and no disk here: ``series_import`` hands in the videos and what the download was loaded for, and gets back
a decision per file, the episodes that stay without a file, and whether the owner is needed.

* **The reading of a video**, the first that gives a numbering form: its file name; the name of its folder when that
  folder holds this one video (obfuscated names in Usenet); for a download of exactly one episode with exactly one
  video, that episode. Numbers are mapped through the series' numberings, the one the release matched at loading first.
  An anime series also reads names counted through (``Show - 148``, the design notes, A5): the numbers are
  ``absolute`` ones, mapped through the scheme ``absolute``, as each video of a batch names its own.
* **Safe** means: the episodes read are all episodes of this download. Only numbers decide; a series title in the name
  is not required. A file whose episodes do not all belong is never filed by itself.
* **Another series:** when every video with a readable title reads a title of no title of the series, nothing is filed
  and the owner is asked (``other_series_suspected``).
* **Samples** by runtime, with Sonarr's limits per expected runtime; specials never; an unknown runtime is no sample.
  Samples and extras by name or folder come in marked (``skip``); a name like ``Bonus`` counts only without an episode.
* **Two videos for one episode:** the better one by the profile, then the larger one; the other is ``duplicate``.
* **Numbers the series does not have** (``S02E13`` of a season TMDB lists with 10): the pack counts differently, so
  none of its videos is filed by itself; they all wait for the owner with what they read (``unknown``).
* **Files that count by another numbering** than the one the release matched at loading (the run from zero,
  17.09.2026): a video names an episode that numbering lacks in a season it has (``S02E06`` of a pack read as the
  scene numbering's season 2 with five episodes), another numbering has every video's numbers, and there some number
  is another episode. Then every video is read by that numbering (``counted``), and the rules above decide again: the
  videos rarely are what the download was loaded for, so they wait for the owner instead of landing on wrong episodes.
  Sonarr files such a pack by scene numbers.
* **Double episodes in two files**: TMDB lists one episode where the pack has two halves,
  so the pack names a number TMDB's season lacks and every number after the double moves up. When the split of
  ``series/parts.py`` explains every number of such a season, the videos of that season are read by it, each half
  with its ``part``. Two halves of one episode are no duplicates.
* **Open** is a video nexcrate cannot file safely while an episode of the download has no file yet; without such a gap
  it is ``not_needed``. A sample, extra or duplicate that holds an episode still without a file is open too: nothing
  that could be the missing episode is left out silently. Open videos with a gap need the owner
  (``files_unassigned``); episodes without a file and without an open video are missing, a line in the history.

**Episode names** (``episode_names``, 09.10.2026): the name behind a video's number, against TMDB's names.

* **Another series, once more:** at least ``NAMES_FOR_SERIES`` videos whose names are those of the episodes their
  numbers read, and no video whose name names another episode, make the download this series after all. A plain name
  ("Pilot", "Teil 1") never counts.
* **Filed by name** (``via`` ``name``): when every video with a number carries a name, the names give each one other
  episodes, all of this download, none twice and none filed before, and at least one name is not plain. A plain name
  counts there only where its number reads the same. A video whose names name only part of what its numbers read
  keeps the rest, unless another video's name claims it (the pack's double episode is TMDB's one episode).
* **Otherwise a name against its number goes to the owner:** the video is open, its name's episodes are the proposal.
* **A shift without names** (``shift``): a season whose pack names numbers TMDB's season lacks, where every file with
  two numbers is twice as long as the others (by its runtime or TMDB's) and the pack's count less those files is
  TMDB's, reads every number after such a file one lower. Only a proposal for the owner, never filed by itself; the
  opposite case, TMDB's one episode in two files, is ``series/parts.py``.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath
from statistics import median
from typing import Any

from .. import releases
from ..series import parts as series_parts
from ..series import release_match
from . import episode_names

#: Sonarr's sample limits in seconds, by the expected runtime in minutes (``DetectSample``).
SAMPLE_LIMITS = ((3, 15), (10, 90), (30, 300))
SAMPLE_LIMIT_LONG = 600
#: Forms that name episodes; a pack names none.
EPISODE_FORMS = ("standard", "multi_episode", "daily", "mini_series")
#: The same for an anime series, whose names may count through (A5).
ANIME_EPISODE_FORMS = (*EPISODE_FORMS, "anime")


def episode_forms(anime: bool) -> tuple[str, ...]:
    return ANIME_EPISODE_FORMS if anime else EPISODE_FORMS

#: The path prefix of a video that came out of the download's archives.
UNPACKED_PREFIX = "unpacked:"

FILED = "filed"
OPEN = "open"
SAMPLE = "sample"
EXTRA = "extra"
DUPLICATE = "duplicate"
NOT_NEEDED = "not_needed"
#: Marks of ``files.scan``: a sample or extra by name or folder, and a name like ``Bonus`` that may still be an episode.
SKIP_SAMPLE = "sample"
SKIP_EXTRA = "extra"
SKIP_EXTRA_NAME = "extra_name"
#: The ``via`` of a reading the episode names gave.
NAME_VIA = "name"
#: How a proposal for the owner came about: the episode names, or the shift after a double episode.
BY_NAME = "name"
BY_SHIFT = "shift"
#: Videos whose names agree with their numbers that make a download "another series" this series after all.
NAMES_FOR_SERIES = 2
#: How much longer than the others a file with two numbers has to be to be one episode of double length.
DOUBLE_FACTOR = series_parts.DOUBLE_FACTOR


@dataclass(frozen=True)
class Video:
    """A video of the download. ``key`` names it for the owner's dialog; ``path`` is relative to the download."""

    key: int
    path: str
    size: int
    duration_seconds: int | None = None
    #: The name of the folder the video lies in (the download's own name at its top), and how many videos it holds.
    folder_name: str | None = None
    folder_videos: int = 1
    #: Left out by ``files.scan``: ``sample``, ``extra`` or ``extra_name``; None for a video that counts.
    skip: str | None = None


@dataclass(frozen=True)
class Reading:
    #: The parser's form: standard, multi_episode, daily or mini_series; ``download`` for decision 19.3.
    form: str
    #: ``file``, ``folder`` or ``download``.
    source: str
    season: int | None
    numbers: tuple[int, ...]
    air_date: str | None
    #: The series title the name spells, and its comparison keys; empty without one.
    title: str | None
    title_keys: tuple[str, ...]
    episode_ids: tuple[int, ...]
    via: str | None
    #: 1 or 2 for a half of a double episode read through the split; None otherwise.
    part: int | None = None
    #: The episodes the name behind the number names (``episode_names``), and whether only plain names did.
    named: tuple[int, ...] = ()
    named_plain: bool = False

    def as_dict(self) -> dict[str, Any]:
        found: dict[str, Any] = {
            "form": self.form,
            "from": self.source,
            "season": self.season,
            "numbers": list(self.numbers),
            "air_date": self.air_date,
            "via": self.via,
        }
        if self.part is not None:
            found["part"] = self.part
        if self.named:
            found["named"] = list(self.named)
            found["named_plain"] = self.named_plain
        return found


@dataclass(frozen=True)
class Context:
    numbering: release_match.Numbering
    #: Every title of the series, for the parser.
    titles: tuple[str, ...]
    #: The comparison keys of every title of the series.
    keys: frozenset[str]
    #: The episodes the download is for (decision 2).
    expected: frozenset[int]
    #: The numbering the release matched through when it was loaded.
    via: str | None = None
    original_language: str | None = None
    #: Episodes of the download filed by an earlier round.
    filed: frozenset[int] = frozenset()
    #: The series is an anime series: names counted through are read (A5).
    anime: bool = False
    #: The episode names of the series; ``assign`` makes them when they are missing.
    names: episode_names.Index | None = None


@dataclass
class Decision:
    video: Video
    reading: Reading | None
    decision: str
    episode_ids: tuple[int, ...] = ()
    #: The half of a double episode this video is; None for a whole episode.
    part: int | None = None
    #: What the owner's dialog proposes for the video, and how it came about (``BY_NAME``, ``BY_SHIFT``).
    proposal: tuple[int, ...] = ()
    proposed_by: str | None = None


@dataclass
class Result:
    files: list[Decision] = field(default_factory=list)
    #: Episodes of the download no filed video covers.
    uncovered: tuple[int, ...] = ()
    #: ``files_unassigned``, ``other_series_suspected`` or None.
    problem: str | None = None
    #: What videos read that the series does not have, as codes (``S02E13``): the pack counts differently.
    unknown: tuple[str, ...] = ()
    #: The numbering the videos were read by when it is not the one the release matched at loading.
    counted: str | None = None
    #: The videos were read by their episode names.
    by_names: bool = False

    @property
    def filed(self) -> list[Decision]:
        return [item for item in self.files if item.decision == FILED]

    @property
    def open(self) -> list[Decision]:
        return [item for item in self.files if item.decision == OPEN]

    @property
    def missing(self) -> tuple[int, ...]:
        """Episodes without a file when no open video could be one of them."""
        return () if self.open else self.uncovered


def sample_limit(runtime_minutes: int) -> int:
    for minutes, seconds in SAMPLE_LIMITS:
        if runtime_minutes <= minutes:
            return seconds
    return SAMPLE_LIMIT_LONG


def is_sample(duration_seconds: int | None, runtime_minutes: int | None, *, special: bool) -> bool:
    """Shorter than Sonarr's limit for the expected runtime. Unknown duration or runtime, or a special: never."""
    if special or duration_seconds is None or not runtime_minutes or runtime_minutes <= 0:
        return False
    return duration_seconds < sample_limit(runtime_minutes)


#: A name that starts with its numbers and has no series title, as files in packs often do: ``S02E05 - Title.mkv``.
_BARE_NUMBERS = re.compile(r"^\s*s\d{1,4}\s*e\d{1,4}", re.IGNORECASE)


#: A number standing for season and episode together, as scene packs name their files: ``tvs-show-201`` is S02E01,
#: ``show-1012`` S10E12. Sonarr reads these (measured on the throwaway Sonarr, 23.09.2026) when the number stands
#: apart at the end of the name or before its tags; a resolution or a codec (``1080p``, ``x264``) never matches.
_PACKED_NUMBER = re.compile(r"(?:^|[ ._-])(\d{3,4})(?=$|[ ._-])")


def packed_numbers(text: str, context: Context) -> tuple[int, int] | None:
    """Season and episode of a name like ``tvs-got-dts-dl-18p-bd-x264-201`` (beside it).

    Stricter than Sonarr: only in a download of exactly one season, only a number whose season is that season, and only
    when the series has that episode. ``2012`` in a pack of season 3 is a year, not an episode.
    """
    seasons = {
        context.numbering.episodes[item].season for item in context.expected if item in context.numbering.episodes
    }
    if len(seasons) != 1:
        return None
    (season,) = seasons
    for found in reversed(_PACKED_NUMBER.findall(text)):
        digits = found
        season_digits = len(digits) - 2
        number_season, number = int(digits[:season_digits]), int(digits[season_digits:])
        if number_season != season or number < 1:
            continue
        if (season, number) in context.numbering.schemes["tmdb"].by_number or any(
            (season, number) in scheme.by_number for scheme in context.numbering.schemes.values()
        ):
            return season, number
        return None
    return None


def _parse(text: str, context: Context) -> Any:
    parsed = releases.parse_series(text, context.original_language, titles=context.titles, anime=context.anime)
    if parsed.series.form == "none" and context.titles and _BARE_NUMBERS.match(text):
        # Read with the series' title in front; the title then counts as no title of its own.
        parsed = releases.parse_series(
            f"{context.titles[0]} {text}", context.original_language, titles=context.titles, anime=context.anime
        )
        return parsed, False
    return parsed, True


def read(video: Video, context: Context, *, single: bool) -> Reading | None:
    """What a video is, by file name, then folder name, then the download (decision 19)."""
    texts = [("file", PurePosixPath(video.path.removeprefix(UNPACKED_PREFIX)).stem)]
    if video.folder_name and video.folder_videos == 1:
        texts.append(("folder", video.folder_name))
    reading: Reading | None = None
    #: What stands behind the number in the file name and in the folder name: the episode's name, if any.
    afters: list[str] = []
    for source, text in texts:
        parsed, titled = _parse(text, context)
        series = parsed.series
        if series.form == "none" and (packed := packed_numbers(text, context)) is not None:
            parsed, titled = _parse(f"S{packed[0]:02d}E{packed[1]:02d}", context)
            series = parsed.series
        if series.refused is not None or series.form not in episode_forms(context.anime):
            continue
        afters.append(series.after)
        if reading is not None:
            continue
        found = release_match.match(context.numbering, series, prefer=context.via)
        reading = Reading(
            form=series.form,
            source=source,
            season=series.season,
            numbers=tuple(series.episodes or series.absolute),
            air_date=series.air_date.isoformat() if series.air_date else None,
            title=series.series_title if titled else None,
            title_keys=tuple(series.title_keys) if titled else (),
            episode_ids=tuple(found.episode_ids),
            via=found.via,
        )
    if reading is not None:
        named = episode_names.of_texts(afters, context.names) if context.names is not None else None
        if named is not None:
            reading = replace(reading, named=named.episode_ids, named_plain=named.plain)
        return reading
    if single and len(context.expected) == 1:
        (episode_id,) = tuple(context.expected)
        episode = context.numbering.episodes.get(episode_id)
        return Reading(
            form="download",
            source="download",
            season=episode.season if episode is not None else None,
            numbers=(episode.episode,) if episode is not None else (),
            air_date=None,
            title=None,
            title_keys=(),
            episode_ids=(episode_id,),
            via=None,
        )
    return None


_WORDS = re.compile(r"[^\W_]+")


def initials(titles: Sequence[str]) -> frozenset[str]:
    """The initials of every title of the series with at least two words (``ebs`` for "Example Big Show"): scene packs
    shorten long titles so."""
    found = set()
    for title in titles:
        words = _WORDS.findall(title or "")
        if len(words) >= 2:
            found.add("".join(word[0] for word in words).casefold())
    return frozenset(found)


def titles_inside(videos: Sequence[Video]) -> frozenset[str]:
    """The series titles read off the folders the videos lie in, below the download's own folder.

    ⚠️ The download's own name is left out on purpose: it comes from the search for this very series and would say
    yes to everything. A folder inside the pack is the packer's own word about what lies there.
    """
    found: set[str] = set()
    for video in videos:
        path = PurePosixPath(video.path.removeprefix(UNPACKED_PREFIX))
        if video.folder_name and len(path.parts) > 1:
            found.update(releases.parse_series(video.folder_name).series.title_keys)
    return frozenset(found)


def other_series(
    readings: Sequence[Reading | None],
    keys: frozenset[str],
    short: frozenset[str] = frozenset(),
    inside: frozenset[str] = frozenset(),
) -> bool:
    """Every video with a readable title reads a title of no title of the series (decision 21). A title that is the
    initials of a title of the series counts as the series.

    ⚠️ A scene pack names its folders after the series and its files after the group: ``Friends.S08E01.Das.Geruecht``
    holds ``tvr-friends-s08e01-720p.mkv``, which reads as the series "tvr friends" (a finding of 23.09.2026, 42
    packs at once). A folder inside the pack that does name the series therefore settles it.
    """
    titled = [reading for reading in readings if reading is not None and reading.title_keys]

    def without_group(title_keys: Sequence[str]) -> set[str]:
        """The keys again without their first word.

        ⚠️ A scene file carries its group in front of the title: ``tvr-friends-s08e01``, ``rsg-airwolf-s01e04``,
        ``jajunge-tkoq.s01e01``. What is read is then "tvr friends", and no title of the series fits it. Dropping
        the first word can only ever let the series' own title through, never a foreign one.
        """
        return {" ".join(key.split()[1:]) for key in title_keys if len(key.split()) > 1}

    def foreign(reading: Reading) -> bool:
        spelled = "".join(_WORDS.findall(reading.title or "")).casefold()
        shorter = without_group(reading.title_keys)
        return (
            keys.isdisjoint(reading.title_keys)
            and keys.isdisjoint(shorter)
            and spelled not in short
            and shorter.isdisjoint(short)
        )

    if not keys.isdisjoint(inside):
        return False
    return bool(titled) and all(foreign(reading) for reading in titled)


def _runtime(context: Context, episode_ids: Sequence[int]) -> tuple[int | None, bool]:
    """The expected runtime of the episodes, and whether they are all specials."""
    rows = [context.numbering.episodes[item] for item in episode_ids if item in context.numbering.episodes]
    if rows and all(row.special for row in rows):
        return None, True
    total = 0
    for row in rows:
        minutes = row.runtime or context.numbering.runtime_min
        if not minutes:
            return None, False
        total += minutes
    return (total or context.numbering.runtime_min), False


#: Orders filed candidates of one episode: higher is better. The caller brings the profile.
Ranker = Callable[[Video, Reading], tuple[int, ...]]


def unknown_code(reading: Reading | None) -> str | None:
    """The code of a reading whose numbers the series does not have; None for any other reading.

    Only season and episode forms count, with numbers above 0: ``S02E00`` or a date TMDB lacks says nothing about how a
    pack counts.
    """
    if reading is None or reading.episode_ids or reading.source == "download":
        return None
    if reading.form not in ("standard", "multi_episode", "mini_series") or not reading.numbers:
        return None
    if min(reading.numbers) <= 0:
        return None
    season = f"S{reading.season:02d}" if reading.season is not None else ""
    return season + "".join(f"E{number:02d}" for number in reading.numbers)


def _left_out(video: Video, ids: tuple[int, ...]) -> str | None:
    """The decision of a video ``files.scan`` marked, or None when it counts: a name like ``Bonus`` with an episode."""
    if video.skip == SKIP_SAMPLE:
        return SAMPLE
    if video.skip == SKIP_EXTRA or (video.skip == SKIP_EXTRA_NAME and not ids):
        return EXTRA
    return None


def counted_otherwise(context: Context, readings: Sequence[Reading | None]) -> str | None:
    """The numbering the videos count by, when it is not the one the release matched at loading (see the module's
    docstring); None when they fit that numbering."""
    loaded = context.numbering.schemes.get(context.via or "")
    if loaded is None or context.via == "owner":
        return None
    named = [
        (reading.season, number)
        for reading in readings
        if reading is not None and reading.form in ("standard", "multi_episode") and reading.season is not None
        for number in reading.numbers
    ]
    if not any(pair not in loaded.by_number and loaded.episodes_of(pair[0]) for pair in named):
        return None
    for scheme in release_match.COUNTING_SCHEMES:
        other = context.numbering.schemes.get(scheme)
        if scheme == context.via or other is None or not all(pair in other.by_number for pair in named):
            continue
        if any(pair in loaded.by_number and loaded.by_number[pair] != other.by_number[pair] for pair in named):
            return scheme
    return None


def by_parts(context: Context, readings: dict[int, Reading | None]) -> dict[int, Reading | None]:
    """The readings again through the split of double episodes, where it explains a season (see the module)."""
    named = [
        (reading.season, number)
        for reading in readings.values()
        if reading is not None and reading.form in ("standard", "multi_episode") and reading.season is not None
        for number in reading.numbers
    ]
    seasons = series_parts.explained(context.numbering, named)
    if not seasons:
        return readings
    found: dict[int, Reading | None] = {}
    for key, reading in readings.items():
        if reading is None or reading.season not in seasons or reading.form not in ("standard", "multi_episode"):
            found[key] = reading
            continue
        numbers = seasons[reading.season]
        halves = [numbers[number] for number in reading.numbers]
        episode_ids = tuple(dict.fromkeys(half.episode_id for half in halves))
        if len(halves) == 1:
            found[key] = replace(reading, episode_ids=episode_ids, via=series_parts.VIA, part=halves[0].part)
            continue
        # A file with several numbers holds whole episodes only: both halves of a double, or none of it.
        whole = all(
            {half.part for half in halves if half.episode_id == episode_id} in ({None}, {1, 2})
            for episode_id in episode_ids
        )
        found[key] = replace(reading, episode_ids=episode_ids if whole else (), via=series_parts.VIA if whole else None)
    return found


def _agrees(reading: Reading) -> bool:
    """The name names (part of) what the number reads."""
    return bool(reading.episode_ids) and set(reading.named) <= set(reading.episode_ids)


def names_confirm(readings: Sequence[Reading | None]) -> bool:
    """Names that make "another series" this series after all (see the module): enough videos whose names that are not
    plain agree with their numbers, and none whose name names another episode."""
    named = [reading for reading in readings if reading is not None and reading.named and not reading.named_plain]
    if any(not _agrees(reading) for reading in named if reading.episode_ids):
        return False
    return sum(1 for reading in named if _agrees(reading)) >= NAMES_FOR_SERIES


def counting_more(context: Context, readings: Sequence[Reading | None]) -> set[int]:
    """The seasons where the videos name numbers TMDB's season does not have: the pack counts more episodes."""
    tmdb = context.numbering.schemes["tmdb"].by_number
    return {
        reading.season
        for reading in readings
        if reading is not None
        and reading.form in ("standard", "multi_episode")
        and reading.season is not None
        and any((reading.season, number) not in tmdb for number in reading.numbers)
    }


def by_name(
    context: Context, videos: Sequence[Video], readings: dict[int, Reading | None]
) -> dict[int, tuple[int, ...]]:
    """The episodes of each video that carries a usable name, by that name (see the module). A plain name is usable only
    where its number reads the same."""
    more = counting_more(context, [readings.get(video.key) for video in videos])
    usable: dict[int, Reading] = {}
    for video in videos:
        reading = readings.get(video.key)
        if reading is None or not reading.named or (reading.named_plain and not _agrees(reading)):
            continue
        usable[video.key] = reading
    claimed = Counter(episode_id for reading in usable.values() for episode_id in reading.named)
    found: dict[int, tuple[int, ...]] = {}
    for key, reading in usable.items():
        found[key] = reading.named
        rest = [episode_id for episode_id in reading.episode_ids if episode_id not in reading.named]
        if rest and _agrees(reading) and reading.season not in more and not any(claimed[item] for item in rest):
            # The name names the first episode of a file holding two; the number gives the other. Not where the pack
            # counts more than TMDB: there a file with two numbers is rather TMDB's one episode.
            found[key] = reading.episode_ids
    return found


def explained_by_names(
    context: Context, videos: Sequence[Video], readings: dict[int, Reading | None], named: dict[int, tuple[int, ...]]
) -> bool:
    """Whether the names explain the download so that nexcrate files by them (see the module)."""
    considered: list[tuple[int, tuple[int, ...]]] = []
    for video in videos:
        reading = readings.get(video.key)
        if video.key not in named:
            if reading is not None and reading.source != "download" and reading.numbers:
                # A video with a number and without a name: its number is all there is, and the pack may count
                # differently.
                return False
            continue
        runtime, specials = _runtime(context, named[video.key])
        if is_sample(video.duration_seconds, runtime, special=specials):
            continue
        considered.append((video.key, named[video.key]))
    # A second floor: a plain name counts only where its number reads the same, so plain names alone file nothing
    # other than the numbers do.
    if not considered or all(readings[key].named_plain for key, _ids in considered):  # type: ignore[union-attr]
        return False
    seen = set(context.filed)
    for _key, ids in considered:
        if not set(ids) <= context.expected or seen & set(ids):
            return False
        seen.update(ids)
    return True


def _double_length(
    context: Context, video: Video, episode_id: int, singles: Sequence[int], season: int
) -> bool:
    """The Gegenprobe of a file with two numbers: twice as long as the others by its own runtime; only when that is not
    known, by TMDB's runtime of the episode it would be.

    ⚠️ TMDB's runtime does not decide against a measured file: where it marks the episode long, ``series/parts.py``
    explains the season already, and this shift is the case where it does not (09.10.2026, a pack of 26 numbers for a
    season of 24 equally long episodes on TMDB).
    """
    if video.duration_seconds and singles:
        return video.duration_seconds >= DOUBLE_FACTOR * median(singles)
    rows = [
        context.numbering.episodes[item]
        for item in context.numbering.schemes["tmdb"].episodes_of(season)
        if item in context.numbering.episodes
    ]
    runtimes = [row.runtime for row in rows if row.runtime]
    own = context.numbering.episodes[episode_id].runtime if episode_id in context.numbering.episodes else None
    return bool(own) and len(runtimes) >= series_parts.LEAST_RUNTIMES and own >= DOUBLE_FACTOR * median(runtimes)


def shift_proposals(
    context: Context, videos: Sequence[Video], readings: dict[int, Reading | None]
) -> dict[int, tuple[int, ...]]:
    """The episodes of each video when a double episode in one file shifts every number after it (see the module).
    Only for the owner's dialog; empty where the shift does not explain a season."""
    tmdb = context.numbering.schemes["tmdb"]
    by_season: dict[int, list[tuple[Video, Reading]]] = defaultdict(list)
    for video in videos:
        reading = readings.get(video.key)
        if (
            reading is None
            or reading.form not in ("standard", "multi_episode")
            or reading.season is None
            or not reading.numbers
            or reading.part is not None
        ):
            continue
        by_season[reading.season].append((video, reading))
    found: dict[int, tuple[int, ...]] = {}
    for season, items in by_season.items():
        numbers = [number for _video, reading in items for number in reading.numbers]
        if all((season, number) in tmdb.by_number for number in numbers) or len(numbers) != len(set(numbers)):
            continue
        pairs = [reading.numbers for _video, reading in items if len(reading.numbers) == 2]
        longer = any(len(reading.numbers) > 2 for _video, reading in items)
        if longer or any(last != first + 1 for first, last in pairs):
            # Only files of one number and files of two numbers in a row.
            continue
        doubles = {first for first, _last in pairs}
        if not doubles:
            continue
        top = max(numbers)
        if top - len(doubles) != len(tmdb.episodes_of(season)):
            continue
        target: dict[int, int] = {}
        offset = 0
        number = 1
        while number <= top:
            target[number] = number - offset
            if number in doubles:
                target[number + 1] = number - offset
                offset += 1
                number += 2
            else:
                number += 1
        if any((season, value) not in tmdb.by_number for value in target.values()):
            continue
        singles = [
            video.duration_seconds for video, reading in items if len(reading.numbers) == 1 and video.duration_seconds
        ]
        if not all(
            _double_length(context, video, tmdb.by_number[(season, target[reading.numbers[0]])], singles, season)
            for video, reading in items
            if len(reading.numbers) == 2
        ):
            continue
        for video, reading in items:
            found[video.key] = tuple(
                dict.fromkeys(tmdb.by_number[(season, target[number])] for number in reading.numbers)
            )
    return found


def _propose(
    decisions: Sequence[Decision], named: dict[int, tuple[int, ...]], shifted: dict[int, tuple[int, ...]]
) -> None:
    for item in decisions:
        key = item.video.key
        if key in named:
            item.proposal, item.proposed_by = named[key], BY_NAME
        elif key in shifted:
            item.proposal, item.proposed_by = shifted[key], BY_SHIFT


def assign(videos: Sequence[Video], context: Context, rank: Ranker | None = None) -> Result:
    """A decision per video (S4.3). Pure."""
    if context.names is None:
        context = replace(context, names=episode_names.index(context.numbering))
    counting = [video for video in videos if video.skip in (None, SKIP_EXTRA_NAME)]
    single = len(counting) == 1
    readings = {video.key: read(video, context, single=single and video in counting) for video in videos}
    counted = counted_otherwise(context, [readings[video.key] for video in counting])
    if counted is not None:
        context = replace(context, via=counted)
        readings = {video.key: read(video, context, single=single and video in counting) for video in videos}
    readings = by_parts(context, readings)
    result = Result(counted=counted)
    named = by_name(context, counting, readings)
    if other_series(
        [readings[video.key] for video in counting],
        context.keys,
        initials(context.titles),
        titles_inside(counting),
    ) and not names_confirm([readings[video.key] for video in counting]):
        # The episodes read stay with each video: the owner can take them over in one step if it is this series.
        result.files = [
            Decision(
                video,
                readings[video.key],
                OPEN if video in counting else (_left_out(video, ()) or OPEN),
                readings[video.key].episode_ids if readings[video.key] is not None else (),
            )
            for video in videos
        ]
        _propose(result.files, {key: ids for key, ids in named.items() if not readings[key].named_plain}, {})  # type: ignore[union-attr]
        result.uncovered = tuple(sorted(context.expected - context.filed))
        result.problem = "other_series_suspected"
        return result

    #: Videos whose name names another episode than their number, without the names explaining the download.
    against: set[int] = set()
    shifted: dict[int, tuple[int, ...]] = {}
    if named and explained_by_names(context, counting, readings, named):
        readings = {
            key: replace(reading, episode_ids=named[key], via=NAME_VIA)
            if key in named and reading is not None
            else reading
            for key, reading in readings.items()
        }
        result.by_names = True
    else:
        for video in counting:
            reading = readings[video.key]
            if reading is None or not reading.named or reading.named_plain or not reading.episode_ids:
                continue
            if not _agrees(reading):
                against.add(video.key)
        named = {key: ids for key, ids in named.items() if not readings[key].named_plain}  # type: ignore[union-attr]
        shifted = shift_proposals(context, counting, readings)

    decisions: dict[int, Decision] = {}
    candidates: list[Decision] = []
    unknown: list[str] = []
    for video in videos:
        reading = readings[video.key]
        ids = reading.episode_ids if reading is not None else ()
        left_out = _left_out(video, ids)
        if left_out is not None:
            decisions[video.key] = Decision(video, reading, left_out, ids)
            continue
        code = unknown_code(reading)
        if code is not None and code not in unknown:
            unknown.append(code)
        runtime, specials = _runtime(context, ids)
        if ids and is_sample(video.duration_seconds, runtime, special=specials):
            decisions[video.key] = Decision(video, reading, SAMPLE, ids)
            continue
        if not ids:
            decisions[video.key] = Decision(video, reading, OPEN)
            continue
        if video.key in against:
            # The name names another episode than the number, and nothing else says which is right: the owner decides.
            decisions[video.key] = Decision(video, reading, OPEN, ids)
            continue
        if set(ids) <= context.expected:
            candidate = Decision(video, reading, FILED, ids, reading.part if reading is not None else None)
            candidates.append(candidate)
            decisions[video.key] = candidate
            continue
        # Episodes of the series, not (all) of this download: never filed by itself (decisions 20 and 23).
        decisions[video.key] = Decision(video, reading, OPEN, ids)

    def order(item: Decision) -> tuple[Any, ...]:
        ranked = rank(item.video, item.reading) if rank is not None and item.reading is not None else ()
        return (tuple(-value for value in ranked), -item.video.size, item.video.path)

    covered: set[int] = set(context.filed)
    #: What is filed of each episode: 0 for the whole, 1 and 2 for the halves of a double.
    taken: dict[int, set[int]] = {episode_id: {0} for episode_id in context.filed}
    if unknown:
        # The pack counts differently than the series: a number that fits may still be another episode. The owner
        # decides with what the videos read; nothing is filed by itself.
        for candidate in candidates:
            candidate.decision = OPEN
    else:
        for candidate in sorted(candidates, key=order):
            half = candidate.part or 0
            if any(
                taken.get(episode_id) and (half == 0 or taken[episode_id] & {0, half})
                for episode_id in candidate.episode_ids
            ):
                candidate.decision = DUPLICATE
                continue
            covered.update(candidate.episode_ids)
            for episode_id in candidate.episode_ids:
                taken.setdefault(episode_id, set()).add(half)

    uncovered = tuple(sorted(context.expected - covered))
    gap = set(uncovered)
    for item in decisions.values():
        # Nothing that could be an episode still without a file is left out silently: a duplicate that holds a second
        # episode, a short episode taken for a sample, a name like "Featurette" with an episode number.
        if item.decision in (DUPLICATE, SAMPLE, EXTRA) and gap & set(item.episode_ids):
            item.decision = OPEN
    if gap and not covered and not any(item.decision == OPEN for item in decisions.values()):
        # Not one episode found, only samples and extras: the download is not what it was loaded for.
        for item in decisions.values():
            if item.decision in (SAMPLE, EXTRA, DUPLICATE):
                item.decision = OPEN
    for item in decisions.values():
        if item.decision == OPEN and not uncovered:
            item.decision = NOT_NEEDED
    result.files = [decisions[video.key] for video in videos]
    _propose(result.files, named, shifted)
    result.uncovered = uncovered
    result.unknown = tuple(unknown)
    result.problem = "files_unassigned" if result.open and uncovered else None
    return result
