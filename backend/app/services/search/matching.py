"""Whether a release belongs to the title ("Matching releases to the title").

1. **By number:** the release's ``tmdbid`` equals the title's TMDB number, or its ``imdb`` number equals the title's
   IMDb number (compared as numbers, so leading zeros do not matter), and a year read from the name, if any, is at
   most one year away from the title's.
2. **By name,** only for a release without a number that can be compared: the title read from the name shares a
   spelling key (``schreibweisen.keys``, every form) with the title, the original title or an alternative title,
   and the year read from the name equals the title's year. A missing year does not match.

Decisions taken here:

* A number can be compared when both the release and the title have it. A release with only an IMDb number, for a
  title without one, goes by name.
* A release whose numbers name another movie is not this movie, whatever its name says; neither is one whose number
  fits but whose year is two or more years off. Radarr tries the name in that case; the plan does not.
* With numbers, a title without a year accepts any year.
"""

from __future__ import annotations

from .. import schreibweisen
from .model import TitleInfo

YEAR_TOLERANCE = 1


def belongs(
    title: TitleInfo,
    *,
    tmdb_id: int | None,
    imdb_id: int | None,
    parsed_title: str | None,
    parsed_year: int | None,
) -> bool:
    by_tmdb = tmdb_id is not None and title.tmdb_id is not None
    by_imdb = imdb_id is not None and title.imdb_id is not None
    if by_tmdb or by_imdb:
        same = (by_tmdb and tmdb_id == title.tmdb_id) or (by_imdb and imdb_id == title.imdb_id)
        if not same:
            return False
        return parsed_year is None or title.year is None or abs(parsed_year - title.year) <= YEAR_TOLERANCE
    if parsed_year is None or title.year is None or parsed_year != title.year:
        return False
    return not title.keys.isdisjoint(schreibweisen.keys(parsed_title))
