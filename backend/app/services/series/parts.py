"""Double episodes that TMDB lists as one while a pack ships them as two files.

TMDB keeps "The One Where Rachel Has a Baby" as one episode of 44 minutes; TVDB, Sonarr and the packers count its two
halves as two episodes, so the season has one number more, and every number after a double in the middle of a
season is one higher (``S06E16`` of a pack is the second half of TMDB's ``S06E15``, not TMDB's ``S06E16``).

The split is read off TMDB alone: an episode at least ``DOUBLE_FACTOR`` times as long as the usual one of its season
takes two numbers, part 1 and part 2, everything after it moves up. Measured against Sonarr's own counting on the
owner's library (23.09.2026): in every one of the nine seasons where Sonarr counts more episodes than TMDB, the long
episodes explain the difference exactly. ⚠️ In three other seasons an episode is as long, and TVDB keeps it as one
too. The split is therefore never a numbering of its own that a name is read by first: it counts only when the
download itself names a number TMDB's season does not have and the split explains every number of that season.

Pure: no database.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from statistics import median

from .release_match import Numbering

#: How much longer than the usual episode of its season an episode has to be to count as two.
DOUBLE_FACTOR = 1.6
#: Fewer runtimes than this say nothing about what is usual.
LEAST_RUNTIMES = 3
#: The ``via`` of a reading through the split.
VIA = "parts"


@dataclass(frozen=True)
class Part:
    episode_id: int
    #: 1 or 2 for a half of a double episode; None for an episode that is one number.
    part: int | None = None


def as_whole(size: int, part: int | None) -> int:
    """The size a half of a double episode is judged by: twice its own, against the runtime of the whole episode.

    The rules measure megabytes per minute of the episodes a file holds. A half holds half the minutes of its
    episode, so twice its size against the whole runtime is exactly its own size against its own half.
    """
    return size * 2 if part in (1, 2) else size


def split(numbering: Numbering, season: int) -> dict[int, Part] | None:
    """The numbers of a season when its long episodes count twice; None for a season without a long episode."""
    ids = numbering.schemes["tmdb"].episodes_of(season)
    rows = sorted(
        (numbering.episodes[item] for item in ids if item in numbering.episodes), key=lambda row: (row.episode, row.id)
    )
    runtimes = [row.runtime for row in rows if row.runtime]
    if season <= 0 or len(runtimes) < LEAST_RUNTIMES:
        return None
    usual = median(runtimes)
    numbers: dict[int, Part] = {}
    number = 0
    doubles = 0
    for row in rows:
        if row.runtime and row.runtime >= DOUBLE_FACTOR * usual:
            doubles += 1
            numbers[number + 1] = Part(row.id, 1)
            numbers[number + 2] = Part(row.id, 2)
            number += 2
        else:
            numbers[number + 1] = Part(row.id)
            number += 1
    return numbers if doubles else None


def explained(numbering: Numbering, named: Iterable[tuple[int, int]]) -> dict[int, dict[int, Part]]:
    """The seasons a download counts by the split: those where it names a number TMDB's season lacks, and the split
    has every number it names there. Empty when the split explains nothing, or not all of it."""
    by_season: dict[int, set[int]] = {}
    for season, number in named:
        by_season.setdefault(season, set()).add(number)
    tmdb = numbering.schemes["tmdb"].by_number
    found: dict[int, dict[int, Part]] = {}
    for season, numbers in by_season.items():
        if all((season, number) in tmdb for number in numbers):
            continue
        numbers_of = split(numbering, season)
        if numbers_of is None or not numbers <= set(numbers_of):
            return {}
        found[season] = numbers_of
    return found
