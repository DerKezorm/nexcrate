"""Matching a source's episodes to TMDB's (decision 24).

Sonarr counts by TVDB, nexcrate by TMDB. Per series, each episode is used once, in this order:

1. ``tvdb``: the TVDB episode number stored on a TMDB episode by an earlier run. A special on both sides stays paired
   so only as decision 54 would pair it now: on the same day, by its title or TMDB's English name, or while nothing can
   be checked (a date or TMDB's English name missing). An earlier run paired specials a day apart, and the stored
   number kept such a pair for ever (measured on the owner's instance, 17.09.2026: 22 of 631);
2. ``numbers_date``: the same season and number, and the same air date give or take one day (a special: the same day);
3. ``date``: the same air date. On one date with the same count on both sides, by title (spelling keys) when that
   decides every episode, else by order within the day; with other counts only by title. Then single episodes whose
   dates lie one day apart. When one TMDB episode stands alone on a date and every source episode of that date shares
   one file, all of them land on it (``shared_file``: TVDB splits what TMDB keeps whole).
   ⚠️ Specials are matched by date apart from regular episodes: by title, or when a day has exactly one on each side.
   Never one day apart: promotional specials often come out on neighbouring days (decision 54);
3c. ``title_en``: a special by its title against TMDB's English name of a special, unique on both sides (decision 51);
4. ``title``: the same title through the spelling keys, unique on both sides;
5. ``numbers``: the same season and number when neither side has a date and both seasons have as many episodes.

Everything else stays unmatched. Generic titles (``TBA``, ``Episode 5``, ``Folge 5``) never match by title. A TMDB
episode's title is its name in the account language and its English name.

**The title vetoes** a pair of steps 1 to 3 (``title_veto``, the design notes, "Befund: falsche Brücken"): when
Sonarr's title does not fit the chosen TMDB episode (whose English name is known), and exactly one other TMDB episode of
the same kind (special or regular) carries it, that one is free, and no other source episode carries its title, the
source episode takes that one instead. A stored TVDB number or a date otherwise kept a wrong pair for ever (measured
18.09.2026 on 180 series: 46). Other spellings stay paired: they fit nowhere else exactly.

When that episode is taken by a pair of steps 1 to 3 whose own title does not fit it, and all four episodes share one
air date, the two swap: within a day the order proves nothing (measured: two episodes of one day swapped, the other
title only in another spelling, "The Dummy Twins" against "The Dummy Twins (2)"). Across days a taken episode stays
taken: an episode named like another of a later year is no proof.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from ..schreibweisen import keys

STEPS = ("tvdb", "numbers_date", "date", "shared_file", "title_en", "title", "numbers", "title_veto")
#: The steps whose pairs Sonarr's title may veto.
VETO_STEPS = frozenset({"tvdb", "numbers_date", "date"})
_GENERIC = re.compile(r"^(tba|tbd|episode\s*\d+|folge\s*\d+|épisode\s*\d+|capitulo\s*\d+|\d+)$", re.IGNORECASE)


@dataclass(frozen=True)
class SourceEpisode:
    id: int
    season: int
    episode: int
    air_date: str | None
    title: str
    tvdb_id: int | None = None
    file_id: int | None = None


@dataclass(frozen=True)
class TmdbEpisode:
    id: int
    season: int
    episode: int
    air_date: str | None
    name: str
    tvdb_id: int | None = None
    #: TMDB's English name: a title too, and what the veto checks Sonarr's title against.
    name_en: str | None = None


@dataclass
class Matched:
    #: Source episode id to nexcrate's episode id.
    pairs: dict[int, int] = field(default_factory=dict)
    #: Source episode id to the step that matched it.
    steps: dict[int, str] = field(default_factory=dict)
    unmatched: list[int] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        result = dict.fromkeys(STEPS, 0)
        for step in self.steps.values():
            result[step] += 1
        return result


def _days_apart(first: str, second: str) -> int:
    return abs((date.fromisoformat(first) - date.fromisoformat(second)).days)


def _special_holds(source: SourceEpisode, target: TmdbEpisode) -> bool:
    """Whether a stored pair of two specials still holds by decision 54."""
    if not source.air_date or not target.air_date or source.air_date == target.air_date:
        return True
    if target.name_en is None:
        return True
    wanted = _title_keys(source.title)
    return bool(wanted & (_title_keys(target.name) | _title_keys(target.name_en)))


def _pieces(text: str | None) -> list[str]:
    """The titles of a TMDB episode that stands for several of TVDB's: "The Change Constant / The Stockholm
    Syndrome". Empty for a single title."""
    parts = [part.strip() for part in (text or "").split(" / ") if part.strip()]
    return parts if len(parts) > 1 else []


def _episode_keys(episode: TmdbEpisode) -> set[str]:
    keys_found = _title_keys(episode.name) | _title_keys(episode.name_en or "")
    for part in [*_pieces(episode.name), *_pieces(episode.name_en)]:
        keys_found |= _title_keys(part)
    return keys_found


def _parts(episode: TmdbEpisode) -> int:
    return max(len(_pieces(episode.name)), len(_pieces(episode.name_en)))


def _title_keys(text: str) -> set[str]:
    cleaned = text.strip()
    if not cleaned or _GENERIC.match(cleaned):
        return set()
    return set(keys(cleaned))


def match(source: list[SourceEpisode], tmdb: list[TmdbEpisode]) -> Matched:
    result = Matched()
    open_source = {episode.id: episode for episode in source}
    open_tmdb = {episode.id: episode for episode in tmdb}
    #: TMDB episodes that took a pair: one TMDB episode takes one source episode, except in ``shared_file``.
    used: set[int] = set()
    tmdb_keys = {episode.id: _episode_keys(episode) for episode in tmdb}
    source_keys = {episode.id: _title_keys(episode.title) for episode in source}

    by_source_id = {episode.id: episode for episode in source}

    def vetoed(
        source_episode: SourceEpisode, tmdb_episode: TmdbEpisode
    ) -> tuple[TmdbEpisode, SourceEpisode | None, bool] | None:
        """The TMDB episode Sonarr's title names instead of the chosen one, with the source episode that gives it up
        in a swap, and whether this one joins its holder on it; None when the pair stands."""
        wanted = source_keys[source_episode.id]
        if not wanted or tmdb_episode.name_en is None or wanted & tmdb_keys[tmdb_episode.id]:
            return None
        special = source_episode.season == 0
        named = [
            episode
            for episode in tmdb
            if episode.id != tmdb_episode.id and (episode.season == 0) == special and wanted & tmdb_keys[episode.id]
        ]
        if len(named) != 1:
            return None
        claimants = [
            episode
            for episode in source
            if (episode.season == 0) == special and source_keys[episode.id] & tmdb_keys[named[0].id]
        ]
        parts = _parts(named[0])
        if parts:
            # One TMDB episode for several of TVDB's (measured: the last two of a season as one, the next TMDB
            # episode a farewell special): each of its titles' holders joins it, never another episode.
            ids = {episode.id for episode in claimants}
            if source_episode.id not in ids or len(ids) > parts:
                return None
            if named[0].id in open_tmdb:
                return named[0], None, False
            holders = {source_id for source_id, target in result.pairs.items() if target == named[0].id}
            return (named[0], None, True) if holders and holders <= ids else None
        if [episode.id for episode in claimants] != [source_episode.id]:
            return None
        if named[0].id in open_tmdb:
            return named[0], None, False
        holders = [source_id for source_id, target in result.pairs.items() if target == named[0].id]
        if len(holders) != 1 or result.steps[holders[0]] not in VETO_STEPS:
            return None
        holder = by_source_id[holders[0]]
        day = source_episode.air_date
        # A holder whose title fits the episode is a claimant above; one without a usable title proves nothing.
        days = {holder.air_date, tmdb_episode.air_date, named[0].air_date}
        if day is None or not source_keys[holder.id] or days != {day}:
            return None
        return named[0], holder, False

    def pair(source_episode: SourceEpisode, tmdb_episode: TmdbEpisode, step: str) -> None:
        if step in VETO_STEPS:
            other = vetoed(source_episode, tmdb_episode)
            if other is not None and other[2]:
                # Several of TVDB's episodes on one of TMDB's; only one of their files can hold it.
                result.pairs[source_episode.id] = other[0].id
                result.steps[source_episode.id] = "title_veto"
                open_source.pop(source_episode.id, None)
                return
            if other is not None and other[1] is not None and tmdb_episode.id in open_tmdb:
                # A swap within one day: the holder takes the episode this one was given.
                holder = other[1]
                result.pairs[holder.id] = tmdb_episode.id
                result.steps[holder.id] = "title_veto"
                open_tmdb.pop(tmdb_episode.id, None)
                used.add(tmdb_episode.id)
                result.pairs[source_episode.id] = other[0].id
                result.steps[source_episode.id] = "title_veto"
                open_source.pop(source_episode.id, None)
                return
            if other is not None and other[1] is None:
                tmdb_episode, step = other[0], "title_veto"
        if tmdb_episode.id not in open_tmdb:
            # Taken by a vetoed pair of the same day: the source episode waits for the later steps.
            return
        result.pairs[source_episode.id] = tmdb_episode.id
        result.steps[source_episode.id] = step
        open_source.pop(source_episode.id, None)
        open_tmdb.pop(tmdb_episode.id, None)
        used.add(tmdb_episode.id)

    # 1. TVDB numbers stored on TMDB episodes.
    by_tvdb: dict[int, list[TmdbEpisode]] = defaultdict(list)
    for episode in tmdb:
        if episode.tvdb_id is not None:
            by_tvdb[episode.tvdb_id].append(episode)
    for episode in list(open_source.values()):
        found = by_tvdb.get(episode.tvdb_id or -1, [])
        if len(found) == 1 and found[0].id in open_tmdb:
            if episode.season == 0 and found[0].season == 0 and not _special_holds(episode, found[0]):
                continue
            pair(episode, found[0], "tvdb")

    # 2. Same numbers, same date give or take one day.
    by_numbers = {(episode.season, episode.episode): episode for episode in tmdb}
    for episode in list(open_source.values()):
        candidate = by_numbers.get((episode.season, episode.episode))
        if (
            candidate is not None
            and candidate.id in open_tmdb
            and episode.air_date
            and candidate.air_date
            and _days_apart(episode.air_date, candidate.air_date) <= (0 if episode.season == 0 else 1)
        ):
            pair(episode, candidate, "numbers_date")

    # 3. Same date.
    source_by_date: dict[str, list[SourceEpisode]] = defaultdict(list)
    tmdb_by_date: dict[str, list[TmdbEpisode]] = defaultdict(list)
    for episode in open_source.values():
        if episode.air_date:
            source_by_date[episode.air_date].append(episode)
    for episode in open_tmdb.values():
        if episode.air_date:
            tmdb_by_date[episode.air_date].append(episode)
    for day in sorted(source_by_date):
        day_sources = sorted(source_by_date[day], key=lambda item: (item.season, item.episode))
        day_targets = sorted(
            (item for item in tmdb_by_date.get(day, []) if item.id in open_tmdb),
            key=lambda item: (item.season, item.episode),
        )
        # Specials on their own: by title, else only a day with exactly one special on each side.
        special_sources = [item for item in day_sources if item.season == 0]
        special_targets = [item for item in day_targets if item.season == 0]
        for source_episode, tmdb_episode in _title_pairs(special_sources, special_targets):
            pair(source_episode, tmdb_episode, "date")
        alone = len(special_sources) == 1 and len(special_targets) == 1
        if alone and special_sources[0].id in open_source and special_targets[0].id in open_tmdb:
            pair(special_sources[0], special_targets[0], "date")
        sources = [item for item in day_sources if item.season != 0]
        targets = [item for item in day_targets if item.season != 0]
        if not sources or not targets:
            continue
        by_title = _title_pairs(sources, targets)
        if len(sources) == len(targets):
            if len(by_title) == len(sources):
                for source_episode, tmdb_episode in by_title:
                    pair(source_episode, tmdb_episode, "date")
            else:
                for source_episode, tmdb_episode in zip(sources, targets, strict=True):
                    pair(source_episode, tmdb_episode, "date")
            continue
        for source_episode, tmdb_episode in by_title:
            pair(source_episode, tmdb_episode, "date")
        remaining = [item for item in sources if item.id in open_source]
        if (
            len(targets) == 1
            and targets[0].id in open_tmdb
            and len(remaining) > 1
            and remaining[0].file_id is not None
            and all(item.file_id == remaining[0].file_id for item in remaining)
        ):
            for source_episode in remaining:
                result.pairs[source_episode.id] = targets[0].id
                result.steps[source_episode.id] = "shared_file"
                open_source.pop(source_episode.id, None)
            open_tmdb.pop(targets[0].id, None)
            used.add(targets[0].id)

    # 3b. Single regular episodes one day apart, unique on both sides. Never specials (decision 54).
    near: dict[int, list[TmdbEpisode]] = {}
    for episode in open_source.values():
        if not episode.air_date or episode.season == 0:
            continue
        near[episode.id] = [
            candidate
            for candidate in open_tmdb.values()
            if candidate.air_date and candidate.season != 0 and _days_apart(episode.air_date, candidate.air_date) == 1
        ]
    claimed: dict[int, int] = defaultdict(int)
    for candidates in near.values():
        if len(candidates) == 1:
            claimed[candidates[0].id] += 1
    for source_id, candidates in near.items():
        if len(candidates) == 1 and claimed[candidates[0].id] == 1 and source_id in open_source:
            pair(open_source[source_id], candidates[0], "date")

    # 3c. Specials by TMDB's English name, unique on both sides; both sides specials only (decision 51).
    english = [
        TmdbEpisode(
            id=item.id, season=item.season, episode=item.episode, air_date=item.air_date, name=item.name_en or ""
        )
        for item in open_tmdb.values()
        if item.season == 0 and item.name_en
    ]
    special_sources = [item for item in open_source.values() if item.season == 0]
    for source_episode, tmdb_episode in _title_pairs(special_sources, english):
        pair(source_episode, open_tmdb[tmdb_episode.id], "title_en")

    # 4. Same title, unique on both sides.
    for source_episode, tmdb_episode in _title_pairs(list(open_source.values()), list(open_tmdb.values())):
        pair(source_episode, tmdb_episode, "title")

    # 5. Same numbers without dates, seasons of the same size.
    source_sizes: dict[int, int] = defaultdict(int)
    tmdb_sizes: dict[int, int] = defaultdict(int)
    for episode in source:
        source_sizes[episode.season] += 1
    for episode in tmdb:
        tmdb_sizes[episode.season] += 1
    for episode in list(open_source.values()):
        candidate = by_numbers.get((episode.season, episode.episode))
        if (
            candidate is not None
            and candidate.id in open_tmdb
            and not episode.air_date
            and not candidate.air_date
            and source_sizes[episode.season] == tmdb_sizes[episode.season]
        ):
            pair(episode, candidate, "numbers")

    # 6. A source episode sharing a file with a matched one lands on the same TMDB episode (a double episode file).
    target_of_file: dict[int, int] = {}
    for source_id, tmdb_id in result.pairs.items():
        file_id = next((item.file_id for item in source if item.id == source_id), None)
        if file_id is not None:
            target_of_file.setdefault(file_id, tmdb_id)
    for episode in list(open_source.values()):
        target = target_of_file.get(episode.file_id) if episode.file_id is not None else None
        if target is not None:
            result.pairs[episode.id] = target
            result.steps[episode.id] = "shared_file"
            open_source.pop(episode.id, None)

    result.unmatched = sorted(open_source)
    return result


def _title_pairs(sources: list[SourceEpisode], targets: list[TmdbEpisode]) -> list[tuple[SourceEpisode, TmdbEpisode]]:
    """Pairs whose titles share a key, where each side has exactly one partner. TMDB's English name counts only between
    episodes of the same kind: a special's English name never takes a regular episode (B0.2)."""
    local_keys = {target.id: _title_keys(target.name) for target in targets}
    english_keys = {target.id: _title_keys(target.name_en or "") for target in targets}

    def fits(wanted: set[str], source_episode: SourceEpisode, target: TmdbEpisode) -> bool:
        if wanted & local_keys[target.id]:
            return True
        return (source_episode.season == 0) == (target.season == 0) and bool(wanted & english_keys[target.id])

    candidates: dict[int, list[TmdbEpisode]] = {}
    for source_episode in sources:
        wanted = _title_keys(source_episode.title)
        candidates[source_episode.id] = [item for item in targets if wanted and fits(wanted, source_episode, item)]
    counts: dict[int, int] = defaultdict(int)
    for found in candidates.values():
        for target in found:
            counts[target.id] += 1
    pairs: list[tuple[SourceEpisode, TmdbEpisode]] = []
    for source_episode in sources:
        found = candidates[source_episode.id]
        if len(found) == 1 and counts[found[0].id] == 1:
            pairs.append((source_episode, found[0]))
    return pairs


def numbering_note(
    source_name: str, source: list[SourceEpisode], tmdb: list[TmdbEpisode], matched: Matched
) -> dict[str, object] | None:
    """The line of decision 25, or None when both count the regular seasons alike and every regular episode matched.

    Specials without a TMDB episode are no numbering of their own: TVDB and TMDB keep different extras, and nearly every
    series has some. Their files still show as unassigned.
    """
    source_sizes: dict[int, int] = defaultdict(int)
    tmdb_sizes: dict[int, int] = defaultdict(int)
    for episode in source:
        if episode.season > 0:
            source_sizes[episode.season] += 1
    for episode in tmdb:
        if episode.season > 0:
            tmdb_sizes[episode.season] += 1
    by_id = {episode.id: episode for episode in tmdb}
    shifted = any(
        by_id[target].season != episode.season or by_id[target].episode != episode.episode
        for episode in source
        if (target := matched.pairs.get(episode.id)) is not None and episode.season > 0
    )
    regular = {episode.id for episode in source if episode.season > 0}
    unmatched = len([episode_id for episode_id in matched.unmatched if episode_id in regular])
    unmatched_specials = len(matched.unmatched) - unmatched
    differences = [
        {"season": number, "tmdb": tmdb_sizes.get(number, 0), "source": source_sizes.get(number, 0)}
        for number in sorted(set(source_sizes) | set(tmdb_sizes))
        if tmdb_sizes.get(number, 0) != source_sizes.get(number, 0)
    ]
    if not shifted and not unmatched and not differences:
        return None
    return {
        "tmdb": {"seasons": len(tmdb_sizes), "episodes": sum(tmdb_sizes.values()), "sizes": _sizes(tmdb_sizes)},
        "source": {
            "name": source_name,
            "seasons": len(source_sizes),
            "episodes": sum(source_sizes.values()),
            "sizes": _sizes(source_sizes),
        },
        # Specials count in, told apart (decision 47); alone they are no reason for the line.
        "unmatched": unmatched + unmatched_specials,
        "unmatched_specials": unmatched_specials,
        "differences": differences,
    }


def _sizes(sizes: dict[int, int]) -> list[int]:
    return [sizes[number] for number in sorted(sizes)]
