"""Absolute numbers of an anime series (fork 1, slice A1).

Release groups count anime through: ``[Group] Title - 148``. TMDB has no such number, so nexcrate keeps it as the scheme
``absolute`` in ``episode_numbers``: one row per regular episode of an anime series, only ``absolute`` set, ``origin``
saying where the number came from. The episode itself stays in TMDB's numbering, and so does ``/api/v1``.

Where the numbers come from is a rank per series (the owner left the choice to the plan):

1. ``owner``: the owner's correction of the episode (scheme ``owner``) with an absolute number. Always wins.
2. ``sonarr``: the takeover from Sonarr brought TVDB's number (scheme ``tvdb``, origin ``sonarr``).
3. ``tvdb``: TVDB itself with the user's PIN. nexcrate has no TVDB client yet; the place in the rank is kept.
4. ``tmdb_order``: TMDB's regular episodes in aired order counted through, specials left out.

The first of 2 to 4 that numbers any episode of the series numbers all of them; the owner's numbers go before it
episode by episode. An episode after the last one the chosen source numbers counts on from the highest number
(``counted_on``): a new episode after a takeover from Sonarr. A number already taken is never given twice.

Measured on 22.09.2026 at 33 known anime series against TVDB's numbers in a throwaway Sonarr, episode by episode over
TMDB's TVDB id of each episode: the counted order is TVDB's number
for every paired episode of 26 series, among them every long one (One Piece 1,178, Detective Conan 1,202, Naruto
Shippuden 500, Bleach 416, Gintama 367, Fairy Tail 328). Where it is not, TVDB gives a special an absolute number (an
OVA or a movie between two seasons) or counts in production order. TMDB's episode groups of the type "Absolute", which
the plan had put before the counted order, were never better and at Detective Conan far worse (113 of 1,202), so they
are not a source.

Only a series of the type ``anime`` has these rows; another type loses them. They are worked out again from their
sources whenever the series changes, so nothing else hangs on them (``store._hanging`` leaves them out).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session as OrmSession

from ...models import Episode, EpisodeNumber, Title

SCHEME = "absolute"
#: The sources in the order of the rank.
RANK = ("owner", "sonarr", "tvdb", "tmdb_order")
#: An episode after the last one its source numbers.
COUNTED_ON = "counted_on"
ORIGINS = (*RANK, COUNTED_ON)


@dataclass(frozen=True)
class Place:
    """A regular episode in TMDB's numbering (season 1 and later)."""

    episode_id: int
    season: int
    episode: int


def rank(places: Iterable[Place], sources: dict[str, dict[int, int]]) -> dict[int, tuple[int, str]]:
    """Episode id to (absolute number, origin). ``sources`` maps an origin of ``RANK`` to episode id and number;
    ``tmdb_order`` is counted here. Pure."""
    ordered = sorted(places, key=lambda place: (place.season, place.episode, place.episode_id))
    known = {place.episode_id for place in ordered}
    filled = {
        origin: {key: value for key, value in (sources.get(origin) or {}).items() if key in known and value > 0}
        for origin in RANK
    }
    filled["tmdb_order"] = {place.episode_id: number for number, place in enumerate(ordered, start=1)}
    result: dict[int, tuple[int, str]] = {}
    taken: set[int] = set()
    for place in ordered:
        number = filled["owner"].get(place.episode_id)
        if number is not None and number not in taken:
            result[place.episode_id] = (number, "owner")
            taken.add(number)
    chosen = next((origin for origin in RANK[1:] if filled[origin]), None)
    if chosen is None:
        return result
    numbers = filled[chosen]
    last_index = max((index for index, place in enumerate(ordered) if place.episode_id in numbers), default=-1)
    for index, place in enumerate(ordered):
        if place.episode_id in result:
            continue
        number = numbers.get(place.episode_id)
        if number is not None and number not in taken:
            result[place.episode_id] = (number, chosen)
            taken.add(number)
        elif number is None and index > last_index:
            following = max(taken, default=0) + 1
            result[place.episode_id] = (following, COUNTED_ON)
            taken.add(following)
    return result


def _sources(db: OrmSession, title: Title) -> dict[str, dict[int, int]]:
    """The stored sources. Sonarr's number sits on its ``tvdb`` row; TheXEM takes that row over later and drops the
    number (``xem.store_mapping``), so a number already taken from Sonarr stays a source by itself."""
    sources: dict[str, dict[int, int]] = {origin: {} for origin in RANK}
    kept: dict[int, int] = {}
    for row in db.scalars(
        select(EpisodeNumber).where(
            EpisodeNumber.title_id == title.id,
            EpisodeNumber.scheme.in_(("owner", "tvdb", SCHEME)),
            EpisodeNumber.absolute.is_not(None),
        )
    ):
        if row.scheme == "owner":
            sources["owner"][row.episode_id] = row.absolute
        elif row.scheme == "tvdb" and row.origin == "sonarr":
            sources["sonarr"][row.episode_id] = row.absolute
        elif row.scheme == SCHEME and row.origin == "sonarr":
            kept[row.episode_id] = row.absolute
    for episode_id, number in kept.items():
        sources["sonarr"].setdefault(episode_id, number)
    return sources


def store(db: OrmSession, title: Title, moment: datetime) -> int:
    """Works out the absolute numbers of an anime series again and writes them; another type loses them. Returns how
    many episodes have one. The caller commits."""
    # Sessions do not flush by themselves: the numbers the caller just staged must be read too.
    db.flush()
    existing = {
        row.episode_id: row
        for row in db.scalars(
            select(EpisodeNumber).where(EpisodeNumber.title_id == title.id, EpisodeNumber.scheme == SCHEME)
        )
    }
    if title.kind != "series" or title.series_type != "anime":
        if existing:
            db.execute(delete(EpisodeNumber).where(EpisodeNumber.title_id == title.id, EpisodeNumber.scheme == SCHEME))
        return 0
    places = [
        Place(row.id, row.season_number, row.episode_number)
        for row in db.execute(
            select(Episode.id, Episode.season_number, Episode.episode_number).where(
                Episode.title_id == title.id, Episode.tmdb_gone_at.is_(None), Episode.season_number > 0
            )
        ).all()
    ]
    wanted = rank(places, _sources(db, title))
    for episode_id, row in existing.items():
        if episode_id not in wanted:
            db.delete(row)
    for episode_id, (number, origin) in wanted.items():
        verified = origin != COUNTED_ON
        row = existing.get(episode_id)
        if row is None:
            db.add(
                EpisodeNumber(
                    episode_id=episode_id,
                    title_id=title.id,
                    scheme=SCHEME,
                    absolute=number,
                    verified=verified,
                    origin=origin,
                    updated_at=moment,
                )
            )
        elif (row.absolute, row.origin) != (number, origin):
            row.absolute, row.origin, row.verified, row.updated_at = number, origin, verified, moment
    db.flush()
    return len(wanted)
