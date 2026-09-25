"""Which episodes of a series a parsed release name means (S2.4, decision 19).

The parser gives numbers, a date or the scope of a pack; this maps them onto the episodes nexcrate stores,
through the numberings S1 kept. The order is fixed:

1. ``owner``, the owner's corrections (S3), which no other scheme makes ambiguous;
2. ``scene``, when the series has scene numbers (from TheXEM or Sonarr), confirmed ones before unconfirmed;
3. ``tvdb``;
4. ``group``, the TMDB episode group the owner chose (S3);
5. TMDB's aired numbers on the episode itself;
6. for a daily show the air date, to the day, regular episodes before specials of that day. A release named by date
   means nothing for a series that is not daily (note ``not_daily``), as in Sonarr: in a series whose episodes came
   out on one day it would mean all of them.
7. for an anime series a name counted through (``Show - 148``, form ``anime``): the scheme ``absolute``
   (``series/absolute.py``, the design notes, A2), via ``absolute``. A name counted through means nothing for a
   series of another type (note ``not_anime``). Numbers the series does not have stand in ``missing``.

**The scene's count of an anime series** (B6, measured at the throwaway Sonarr on 23.09.2026):
the scene numbering carries a number counted through as well (Sonarr's ``sceneAbsoluteEpisodeNumber``, from TheXEM or
Sonarr), which mostly starts again with each scene season (Frieren's S02E02 is 30 counted through, 2 in the scene).
A release name counted through is read first through it, number by number, in Sonarr's order:

* a scene name of the series that stands for one season (TheXEM's names, ``Bleach Thousand-Year Blood War`` for
  season 17) gives the scene season, and the number is looked up there: first as a scene number counted through, then
  as the scene episode. The name says which part of the series is meant, so the reading counted through does not make
  it ambiguous (note ``scene_season``);
* otherwise the number is taken from the scene when exactly one episode has it there (Pokémon's 300 is S06E24, not
  the 300th episode counted through);
* otherwise, and for every number the scene does not have, the number counted through.

Where both readings differ, the scene's comes first and the other is named, as for season and episode. A disk read
(``scene=False``) leaves the scene's count out, as Sonarr does for files already on the disk.

**Counted through in a season's place** (B6): an anime release named ``S01E1179`` (NanakoRaws, VARYG, measured on
the owner's indexer) means episode 1179 counted through. When no numbering has the number in season 1, the scheme
``absolute`` reads it; Sonarr finds nothing there.

Without the owner's switch (``titles.use_scene_numbering`` false) the scene numbering is not read at all, as with
Sonarr's ``useSceneNumbering`` off.

The first scheme that finds episodes wins. When another scheme would find other episodes, the result is
``ambiguous`` and names the other scheme and its episodes; the search of a later stage does not take such a
release on its own.

**How a release group counts** (the run from zero, 17.09.2026): a search answer shows it. A group that names an
episode a numbering does not have (``S02E06`` where the scene numbering's season 2 has five episodes) counts by the
numberings that have it, and so do its other releases of that season. Where those numberings read another episode than
the first scheme, they decide (note ``group_counting``). Releases of one group and season that show different
numberings show nothing. Without such releases the order above stands, as in Sonarr, which reads a name by scene
numbers whenever the series has them.

The same holds for the numbers counted through of an anime series (B6): a group whose name counted through only one
of the two counts has (the scene's and the series' own) counts by that one, and so do its other names counted through.
A group naming ``S01E1179``, which only the count through has, counts through in season 1 as well. Sonarr knows
neither.

``load`` reads the database once, ``match`` is pure, so the checker and the later search share one answer.
Episodes TMDB no longer lists are left out.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ...models import Episode, EpisodeNumber, Title, XemName
from .. import schreibweisen
from ..releases.series_parser import ParsedSeries
from . import absolute
from .store import code as episode_code

#: The schemes in the order of decision 13 of the design notes: the owner's corrections, scene, TVDB, the chosen
#: TMDB episode group, and ``tmdb``, the numbering on the episode itself.
SCHEMES = ("owner", "scene", "tvdb", "group", "tmdb")
#: Schemes read from ``episode_numbers``.
STORED_SCHEMES = ("owner", "scene", "tvdb", "group")
#: Numberings a release group can count by. The owner's corrections are single episodes: they say nothing about a group.
COUNTING_SCHEMES = ("scene", "tvdb", "group", "tmdb")
#: How the groups of one answer count: the numberings by (group in lower case, season).
Counting = dict[tuple[str, int], frozenset[str]]
#: The season key of a name counted through without a season, in ``Counting``.
THROUGH = -1


@dataclass(frozen=True)
class EpisodeRow:
    id: int
    season: int
    episode: int
    name: str
    air_date: str | None
    runtime: int | None

    @property
    def special(self) -> bool:
        return self.season == 0

    @property
    def code(self) -> str:
        return episode_code(self.season, self.episode)


@dataclass
class Scheme:
    """One numbering of a series: where each number stands, and which numbers are only extrapolated."""

    by_number: dict[tuple[int, int], int] = field(default_factory=dict)
    seasons: dict[int, list[int]] = field(default_factory=lambda: defaultdict(list))
    unverified: set[int] = field(default_factory=set)

    def episodes_of(self, season: int) -> list[int]:
        return list(self.seasons.get(season) or [])


@dataclass(frozen=True)
class Numbering:
    """Everything the mapping needs of one series."""

    title_id: int
    series_type: str | None
    episodes: dict[int, EpisodeRow]
    schemes: dict[str, Scheme]
    #: Episode ids by air date, in the order of the season and episode numbers.
    by_date: dict[str, list[int]]
    #: The regular seasons TMDB lists (season 0 is the specials).
    regular_seasons: tuple[int, ...]
    #: The usual runtime of the series, for episodes without one of their own.
    runtime_min: int | None = None
    #: An anime series' absolute numbers: number to episode id (A2).
    by_absolute: dict[int, int] = field(default_factory=dict)
    #: The scene's numbers counted through: number to (scene season, episode id), in the order of the scene numbers
    #: (B6).
    by_scene_absolute: dict[int, tuple[tuple[int, int], ...]] = field(default_factory=dict)
    #: TheXEM's names that stand for one scene season: spelling key to season. A key two seasons share is left out.
    scene_seasons: dict[str, int] = field(default_factory=dict)

    def has(self, scheme: str) -> bool:
        return scheme in self.schemes and bool(self.schemes[scheme].by_number)


@dataclass(frozen=True)
class Match:
    """The episodes a release means, and what is not certain about it."""

    episode_ids: tuple[int, ...] = ()
    #: ``scene``, ``tvdb``, ``tmdb``, ``air_date``, ``absolute`` or None when nothing matched.
    via: str | None = None
    ambiguous: bool = False
    #: The other reading: its scheme and the codes of its episodes.
    other: dict[str, object] | None = None
    #: Numbers of a range that the series does not have.
    missing: tuple[int, ...] = ()
    #: ``unverified_scene``, ``two_dates`` (a day first date that reads both ways), ``group_counting`` (the release's
    #: group counts by another numbering than the first scheme), ``scene_season`` (a scene name gave the season of a
    #: name counted through).
    notes: tuple[str, ...] = ()


def load(db: OrmSession, title: Title) -> Numbering:
    """The numbering of one series, read once."""
    rows = list(
        db.scalars(
            select(Episode)
            .where(Episode.title_id == title.id, Episode.tmdb_gone_at.is_(None))
            .order_by(Episode.season_number, Episode.episode_number, Episode.id)
        )
    )
    episodes = {
        row.id: EpisodeRow(
            id=row.id,
            season=row.season_number,
            episode=row.episode_number,
            name=row.name or "",
            air_date=row.air_date,
            runtime=row.runtime,
        )
        for row in rows
    }
    schemes: dict[str, Scheme] = {"tmdb": Scheme()}
    for episode in episodes.values():
        schemes["tmdb"].by_number[(episode.season, episode.episode)] = episode.id
        schemes["tmdb"].seasons[episode.season].append(episode.id)
    use_scene = title.use_scene_numbering is not False
    stored = STORED_SCHEMES if use_scene else tuple(scheme for scheme in STORED_SCHEMES if scheme != "scene")
    scene_absolute: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for number in db.scalars(
        select(EpisodeNumber)
        .where(EpisodeNumber.title_id == title.id, EpisodeNumber.scheme.in_(stored))
        .order_by(EpisodeNumber.season, EpisodeNumber.episode, EpisodeNumber.id)
    ):
        if number.episode_id not in episodes or number.season is None or number.episode is None:
            continue
        if number.scheme == "scene" and number.absolute is not None and number.absolute > 0:
            scene_absolute[number.absolute].append((number.season, number.episode_id))
        scheme = schemes.setdefault(number.scheme, Scheme())
        last = number.episode_end if number.episode_end is not None else number.episode
        for value in range(number.episode, max(number.episode, last) + 1):
            scheme.by_number.setdefault((number.season, value), number.episode_id)
        if number.episode_id not in scheme.seasons[number.season]:
            scheme.seasons[number.season].append(number.episode_id)
        if not number.verified:
            scheme.unverified.add(number.episode_id)
    by_date: dict[str, list[int]] = defaultdict(list)
    for episode in episodes.values():
        if episode.air_date:
            by_date[episode.air_date].append(episode.id)
    by_absolute = {
        row.absolute: row.episode_id
        for row in db.execute(
            select(EpisodeNumber.absolute, EpisodeNumber.episode_id).where(
                EpisodeNumber.title_id == title.id,
                EpisodeNumber.scheme == absolute.SCHEME,
                EpisodeNumber.absolute.is_not(None),
            )
        ).all()
        if row.episode_id in episodes
    }
    return Numbering(
        title_id=title.id,
        series_type=title.series_type,
        episodes=episodes,
        schemes=schemes,
        by_date=dict(by_date),
        regular_seasons=tuple(sorted({episode.season for episode in episodes.values() if episode.season > 0})),
        runtime_min=title.runtime,
        by_absolute=by_absolute,
        by_scene_absolute={number: tuple(places) for number, places in scene_absolute.items()},
        scene_seasons=_scene_seasons(db, title) if use_scene and scene_absolute else {},
    )


def _scene_seasons(db: OrmSession, title: Title) -> dict[str, int]:
    """TheXEM's names of the series that stand for one season, by spelling key."""
    if title.tvdb_id is None:
        return {}
    found: dict[str, int] = {}
    shared: set[str] = set()
    for text_keys, season in db.execute(
        select(XemName.search_keys, XemName.season).where(XemName.tvdb_id == title.tvdb_id, XemName.season.is_not(None))
    ).all():
        for key in (text_keys or "").split(schreibweisen.SEPARATOR):
            if not key:
                continue
            if found.get(key, season) != season:
                shared.add(key)
            found[key] = season
    return {key: season for key, season in found.items() if key not in shared}


def _by_numbers(numbering: Numbering, scheme: str, parsed: ParsedSeries) -> tuple[list[int], list[int]]:
    """The episodes of a numbered release in one scheme, and the numbers the series does not have."""
    found = numbering.schemes.get(scheme)
    if found is None or parsed.season is None:
        return [], []
    ids: list[int] = []
    missing: list[int] = []
    for number in parsed.episodes:
        episode_id = found.by_number.get((parsed.season, number))
        if episode_id is None:
            missing.append(number)
        elif episode_id not in ids:
            ids.append(episode_id)
    return ids, missing


def _pack(numbering: Numbering, scheme: str, parsed: ParsedSeries) -> list[int]:
    found = numbering.schemes.get(scheme)
    if found is None or parsed.season is None:
        return []
    return found.episodes_of(parsed.season)


def _mini_series(numbering: Numbering, parsed: ParsedSeries) -> list[int]:
    """An episode without a season: only for a series with exactly one regular season."""
    if len(numbering.regular_seasons) != 1 or not parsed.episodes:
        return []
    season = numbering.regular_seasons[0]
    scheme = numbering.schemes["tmdb"]
    ids = [scheme.by_number.get((season, number)) for number in parsed.episodes]
    return [episode_id for episode_id in ids if episode_id is not None]


def _counted_through(numbering: Numbering, numbers: tuple[int, ...]) -> tuple[list[int], list[int]]:
    """The episodes of numbers counted through, and the numbers the series does not have."""
    ids: list[int] = []
    missing: list[int] = []
    for number in numbers:
        episode_id = numbering.by_absolute.get(number)
        if episode_id is None:
            missing.append(number)
        elif episode_id not in ids:
            ids.append(episode_id)
    return ids, missing


def scene_season(numbering: Numbering, parsed: ParsedSeries) -> int | None:
    """The scene season a scene name in the release stands for, or None."""
    seasons = {numbering.scene_seasons[key] for key in parsed.title_keys if key in numbering.scene_seasons}
    return seasons.pop() if len(seasons) == 1 else None


def _in_scene(numbering: Numbering, number: int, season: int | None) -> int | None:
    """The episode the scene gives a number counted through: in the scene season when a name gave one (as a number
    counted through, else as the scene episode), otherwise only when exactly one episode has it."""
    places = numbering.by_scene_absolute.get(number, ())
    if season is not None:
        inside = [episode_id for place_season, episode_id in places if place_season == season]
        if inside:
            return inside[0]
        scheme = numbering.schemes.get("scene")
        return scheme.by_number.get((season, number)) if scheme is not None else None
    return places[0][1] if len(places) == 1 else None


def _scene_counted(numbering: Numbering, parsed: ParsedSeries) -> tuple[list[int], list[int], bool]:
    """Sonarr's reading of a name counted through: the scene first, number by number, the count through for what the
    scene does not have. Returns the episodes, the missing numbers and whether the scene gave any of them."""
    season = scene_season(numbering, parsed)
    ids: list[int] = []
    missing: list[int] = []
    from_scene = False
    for number in parsed.absolute:
        episode_id = _in_scene(numbering, number, season)
        if episode_id is not None:
            from_scene = True
        else:
            episode_id = numbering.by_absolute.get(number)
        if episode_id is None:
            missing.append(number)
        elif episode_id not in ids:
            ids.append(episode_id)
    return ids, missing, from_scene


def _through_in_season(numbering: Numbering, parsed: ParsedSeries) -> bool:
    """A standard name of an anime series whose episode may be a number counted through: season 1 only."""
    return (
        numbering.series_type == "anime"
        and parsed.form in ("standard", "multi_episode")
        and parsed.season == 1
        and bool(parsed.episodes)
        and bool(numbering.by_absolute)
    )


def _daily(numbering: Numbering, parsed: ParsedSeries) -> tuple[list[int], bool]:
    """The episodes of a daily release, and whether the date reads two ways with episodes on both days."""
    days = [day.isoformat() for day in parsed.air_dates]
    hits: dict[str, list[int]] = {}
    for day in days:
        on_day = list(numbering.by_date.get(day) or [])
        regular = [episode_id for episode_id in on_day if not numbering.episodes[episode_id].special]
        hits[day] = regular or on_day
    filled = [day for day in days if hits[day]]
    if not filled:
        return [], False
    if len(filled) > 1:
        return list(hits[filled[0]]), True
    found = hits[filled[0]]
    if parsed.part is not None and len(found) >= parsed.part:
        return [found[parsed.part - 1]], False
    return found, False


def _codes(numbering: Numbering, ids: list[int]) -> list[str]:
    return [numbering.episodes[episode_id].code for episode_id in ids if episode_id in numbering.episodes]


def _counting_season(numbering: Numbering, parsed: ParsedSeries) -> int | None:
    """The season a release counts in for ``Counting``: its own, or for a name counted through the scene season a
    name gives, else ``THROUGH``."""
    if parsed.form == "anime":
        season = scene_season(numbering, parsed)
        return season if season is not None else THROUGH
    return parsed.season


def _holding_through(numbering: Numbering, parsed: ParsedSeries) -> frozenset[str] | None:
    """The counts through that have every number of a name counted through: ``scene`` and ``absolute``."""
    if numbering.series_type != "anime" or not parsed.absolute:
        return None
    season = scene_season(numbering, parsed)
    known = [
        name
        for name, present in (("scene", bool(numbering.by_scene_absolute)), ("absolute", bool(numbering.by_absolute)))
        if present
    ]
    holding: set[str] = set()
    if "scene" in known and all(
        number in numbering.by_scene_absolute
        and (season is None or any(place[0] == season for place in numbering.by_scene_absolute[number]))
        for number in parsed.absolute
    ):
        holding.add("scene")
    if "absolute" in known and all(number in numbering.by_absolute for number in parsed.absolute):
        holding.add("absolute")
    return frozenset(holding) if holding and len(holding) < len(known) else None


def _holding(numbering: Numbering, parsed: ParsedSeries) -> frozenset[str] | None:
    """The numberings that have what a release names, when some numbering of the series does not; None when the
    release shows nothing about how its group counts."""
    if parsed.refused is not None:
        return None
    if parsed.form == "anime":
        return _holding_through(numbering, parsed)
    if parsed.season is None:
        return None
    known = [scheme for scheme in COUNTING_SCHEMES if numbering.has(scheme)]
    if parsed.form == "season_pack":
        holding = {scheme for scheme in known if numbering.schemes[scheme].episodes_of(parsed.season)}
    elif parsed.form in ("standard", "multi_episode") and parsed.episodes:
        holding = {
            scheme
            for scheme in known
            if all((parsed.season, number) in numbering.schemes[scheme].by_number for number in parsed.episodes)
        }
        if _through_in_season(numbering, parsed):
            known.append("absolute")
            if all(number in numbering.by_absolute for number in parsed.episodes):
                holding.add("absolute")
    else:
        return None
    return frozenset(holding) if holding and len(holding) < len(known) else None


def group_counting(numbering: Numbering, releases: list[ParsedSeries]) -> Counting:
    """How the release groups of one search answer count the series, season by season (see the module's docstring).
    ``releases`` are the answer's releases of this series."""
    found: dict[tuple[str, int], frozenset[str]] = {}
    contradicted: set[tuple[str, int]] = set()
    for parsed in releases:
        holding = _holding(numbering, parsed)
        season = _counting_season(numbering, parsed)
        if holding is None or not parsed.group or season is None:
            continue
        key = (parsed.group.casefold(), season)
        if key in contradicted:
            continue
        both = found[key] & holding if key in found else holding
        if both:
            found[key] = both
        else:
            found.pop(key, None)
            contradicted.add(key)
    return found


def match(
    numbering: Numbering,
    parsed: ParsedSeries,
    prefer: str | None = None,
    counting: Counting | None = None,
    scene: bool = True,
) -> Match:
    """The episodes the release means. Refused forms match nothing.

    ``prefer`` puts the reading of one scheme first when it has one: filing a download reads its files through the
    numbering its release matched when it was loaded (decision 19). ``counting``, from
    ``group_counting`` over the same answer, puts the numbering the release's group counts by first. ``scene`` false
    leaves the scene's count through out: a file already on the disk, as in Sonarr.
    """
    if parsed.refused is not None:
        return Match()
    readings: list[tuple[str, list[int], list[int]]] = []
    notes: list[str] = []
    decisive = False
    season_key = _counting_season(numbering, parsed)
    counts = (
        counting.get((parsed.group.casefold(), season_key)) or frozenset()
        if counting and parsed.group and season_key is not None
        else frozenset()
    )

    if parsed.form == "daily" and numbering.series_type != "daily":
        notes.append("not_daily")
    elif parsed.form == "daily":
        found, two_dates = _daily(numbering, parsed)
        if two_dates:
            notes.append("two_dates")
        if found:
            readings.append(("air_date", found, []))
    elif parsed.form == "anime" and numbering.series_type != "anime":
        notes.append("not_anime")
    elif parsed.form == "anime":
        found, missing = _counted_through(numbering, parsed.absolute)
        if scene and numbering.by_scene_absolute:
            scened, scened_missing, from_scene = _scene_counted(numbering, parsed)
            if from_scene and scened and set(scened) != set(found):
                readings.append(("scene", scened, scened_missing))
                if scene_season(numbering, parsed) is not None:
                    decisive = True
                    notes.append("scene_season")
            elif from_scene:
                # The same episodes either way: missing is only what neither count has.
                missing = [number for number in missing if number in scened_missing]
        if found:
            readings.append(("absolute", found, missing))
    elif parsed.form == "mini_series":
        found = _mini_series(numbering, parsed)
        if found:
            readings.append(("tmdb", found, []))
    elif parsed.form == "season_pack":
        for scheme in SCHEMES:
            found = _pack(numbering, scheme, parsed)
            if found:
                readings.append((scheme, found, []))
    elif parsed.form in ("standard", "multi_episode"):
        for scheme in SCHEMES:
            found, missing = _by_numbers(numbering, scheme, parsed)
            if found:
                readings.append((scheme, found, missing))
        # ``S01E1179`` of an anime series: only when no numbering has it, or its group counts through.
        if _through_in_season(numbering, parsed) and (not readings or "absolute" in counts or prefer == "absolute"):
            found, missing = _counted_through(numbering, parsed.episodes)
            if found:
                readings.append(("absolute", found, missing))

    if not readings:
        return Match(notes=tuple(notes))
    if prefer is not None:
        readings.sort(key=lambda entry: 0 if entry[0] == prefer else 1)
    elif counts and readings[0][0] != "owner" and not decisive:
        chosen = next((entry for entry in readings if entry[0] in counts), None)
        # The first reading in ``counts`` is the first reading itself when that one is in them: nothing moves then.
        if chosen is not None and set(chosen[1]) != set(readings[0][1]):
            readings.remove(chosen)
            readings.insert(0, chosen)
            notes.append("group_counting")
    via, ids, missing = readings[0]
    # A correction of the owner decides: no other reading makes it ambiguous (decision 13). So does a scene name that
    # says the season of a name counted through (B6).
    settled = via == "owner" or (decisive and via == "scene")
    other = None if settled else next((entry for entry in readings[1:] if set(entry[1]) != set(ids)), None)
    scene_scheme = numbering.schemes.get("scene")
    if via == "scene" and scene_scheme is not None and any(episode_id in scene_scheme.unverified for episode_id in ids):
        notes.append("unverified_scene")
    return Match(
        episode_ids=tuple(ids),
        via=via,
        ambiguous=other is not None,
        other={"via": other[0], "codes": _codes(numbering, other[1])} if other is not None else None,
        missing=tuple(missing),
        notes=tuple(notes),
    )


def season_of(numbering: Numbering, match_result: Match) -> int | None:
    """The TMDB season the matched episodes lie in, or None when they lie in several."""
    seasons = {numbering.episodes[episode_id].season for episode_id in match_result.episode_ids}
    return seasons.pop() if len(seasons) == 1 else None
