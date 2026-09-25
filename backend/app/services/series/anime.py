"""Anime.

A series of the type ``anime`` is searched, loaded, filed and named like any other since A6 (22.09.2026);
``SEARCHED`` is the one switch that says so. While it was off, ``/api/v1`` said it honestly instead of promising a
search that never comes (``capabilities.anime``, ``anime_not_supported``); with it on those answers fall away by
themselves, and no field and no shape of ``/api/v1`` changed.

Recognising anime when a series is added (A1, decision 7) goes by TMDB: its keyword ``anime``, or the genre Animation
with Japanese as the original language. Measured on 22.09.2026 at 40 known series against TVDB's genre ``Anime`` as a
throwaway Sonarr shows it: the same answer for all 40. Sonarr
itself gives every series ``standard`` in its lookup, anime too. The proposal waits for ``SEARCHED``: a recognised
series would otherwise be searched less than today.
"""

from __future__ import annotations

from collections.abc import Iterable

#: Whether nexcrate searches, loads and files anime. ``/api/v1/system`` says it as ``capabilities.anime``.
#: On since the anime block is built (A6, 22.09.2026).
SEARCHED = True

#: TMDB's genre Animation, the same id in every language.
ANIMATION_GENRE = 16
#: TMDB's keyword for anime, compared by name: the id is TMDB's (210024) but the name is what the answer carries.
ANIME_KEYWORD = "anime"


def not_searched(kind: str | None, series_type: str | None) -> bool:
    """A series nexcrate takes but does not search yet: anime, while ``SEARCHED`` is off."""
    return kind == "series" and series_type == "anime" and not SEARCHED


def series_type(stored: str | None) -> str:
    """The type as ``/api/v1`` names it: a series stored without one is a standard series."""
    return stored or "standard"


def looks_like_anime(keywords: Iterable[str], genre_ids: Iterable[int], original_language: str | None) -> bool:
    """What TMDB says about a series: the keyword ``anime``, or Animation made in Japanese. Pure."""
    if any(keyword.strip().casefold() == ANIME_KEYWORD for keyword in keywords):
        return True
    return ANIMATION_GENRE in set(genre_ids) and original_language == "ja"


def proposed(looks_anime: bool, otherwise: str) -> str:
    """The type a new series gets: anime once nexcrate searches it, else the proposal from TMDB's type."""
    return "anime" if looks_anime and SEARCHED else otherwise
