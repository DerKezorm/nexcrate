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
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath
from typing import Any

from .. import releases
from ..series import parts as series_parts
from ..series import release_match

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


@dataclass
class Decision:
    video: Video
    reading: Reading | None
    decision: str
    episode_ids: tuple[int, ...] = ()
    #: The half of a double episode this video is; None for a whole episode.
    part: int | None = None


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
    for source, text in texts:
        parsed, titled = _parse(text, context)
        series = parsed.series
        if series.form == "none" and (packed := packed_numbers(text, context)) is not None:
            parsed, titled = _parse(f"S{packed[0]:02d}E{packed[1]:02d}", context)
            series = parsed.series
        if series.refused is not None or series.form not in episode_forms(context.anime):
            continue
        found = release_match.match(context.numbering, series, prefer=context.via)
        return Reading(
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


def assign(videos: Sequence[Video], context: Context, rank: Ranker | None = None) -> Result:
    """A decision per video (S4.3). Pure."""
    counting = [video for video in videos if video.skip in (None, SKIP_EXTRA_NAME)]
    single = len(counting) == 1
    readings = {video.key: read(video, context, single=single and video in counting) for video in videos}
    counted = counted_otherwise(context, [readings[video.key] for video in counting])
    if counted is not None:
        context = replace(context, via=counted)
        readings = {video.key: read(video, context, single=single and video in counting) for video in videos}
    readings = by_parts(context, readings)
    result = Result(counted=counted)
    if other_series(
        [readings[video.key] for video in counting],
        context.keys,
        initials(context.titles),
        titles_inside(counting),
    ):
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
        result.uncovered = tuple(sorted(context.expected - context.filed))
        result.problem = "other_series_suspected"
        return result

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
    result.uncovered = uncovered
    result.unknown = tuple(unknown)
    result.problem = "files_unassigned" if result.open and uncovered else None
    return result
