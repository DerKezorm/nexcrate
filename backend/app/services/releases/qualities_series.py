"""Sonarr's quality table for series: names, sources, resolutions, and the order of the qualities.

Decision 16 of the design notes. Sonarr names and numbers qualities differently from Radarr, and TRaSH's
Sonarr formats compare sources by Sonarr's numbers, so series need their own table:

* **Names:** ``Bluray-1080p Remux`` instead of ``Remux-1080p``, ``HDTV-2160p``, ``Raw-HD``. Sonarr has no CAM,
  TELESYNC, TELECINE, WORKPRINT, DVDSCR, REGIONAL, DVD-R or BR-DISK.
* **Sources:** Sonarr counts television 1, television raw 2, WEB-DL 3, WEBRip 4, DVD 5, Blu-ray 6, Blu-ray remux 7.
  Radarr counts DVD 5, WEB-DL 7, WEBRip 8, Blu-ray 9 and keeps remux as a modifier. TRaSH's Sonarr formats use
  3, 4, 5, 6 and 7; only the anime formats use 1, labelled "WEB" (measured against a Sonarr, see "Noch zu messen"
  in the plan). Series rules never carry a ``QualityModifierSpecification``: TRaSH's Sonarr data has none.
* **Order** (``WEIGHTS``), lowest first, as TRaSH's base profile lists Sonarr's own order turned round. WEB-DL and
  WEBRip of one resolution share a place, as they share an item there. Measured on 16.09.2026 against the
  throwaway Sonarr's ``GET /api/v3/qualitydefinition`` (4.0.19): the order below is Sonarr's own, weight for
  weight.

``FROM_MOVIE`` maps what the movie reader (``qualities.py``) finds onto Sonarr's names, so one parser serves both
kinds. A quality Sonarr does not know (CAM, TELESYNC, TELECINE, WORKPRINT, DVDSCR, REGIONAL, BR-DISK) keeps the
resolution of the name and becomes ``HDTV-<resolution>``, and only without one it becomes ``Unknown``: measured
on 16.09.2026, Sonarr reads ``…1080p.CAM.x264-EXAMPLE`` as HDTV-1080p, while Radarr keeps CAM. None of these
turned up in 7,754 real names of the owner's library.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import qualities as q

#: Sonarr's source numbers, by position.
SOURCES = ("UNKNOWN", "TELEVISION", "TELEVISION_RAW", "WEBDL", "WEBRIP", "DVD", "BLURAY", "BLURAY_RAW")
UNKNOWN, TELEVISION, TELEVISION_RAW, WEBDL, WEBRIP, DVD, BLURAY, BLURAY_RAW = range(8)


@dataclass(frozen=True)
class Quality:
    name: str
    source: int
    resolution: int


#: Sonarr's qualities, lowest first.
QUALITIES: tuple[Quality, ...] = (
    Quality("Unknown", UNKNOWN, 0),
    Quality("SDTV", TELEVISION, 480),
    Quality("WEBDL-480p", WEBDL, 480),
    Quality("WEBRip-480p", WEBRIP, 480),
    Quality("DVD", DVD, 480),
    Quality("Bluray-480p", BLURAY, 480),
    Quality("Bluray-576p", BLURAY, 576),
    Quality("HDTV-720p", TELEVISION, 720),
    Quality("HDTV-1080p", TELEVISION, 1080),
    Quality("Raw-HD", TELEVISION_RAW, 1080),
    Quality("WEBDL-720p", WEBDL, 720),
    Quality("WEBRip-720p", WEBRIP, 720),
    Quality("Bluray-720p", BLURAY, 720),
    Quality("WEBDL-1080p", WEBDL, 1080),
    Quality("WEBRip-1080p", WEBRIP, 1080),
    Quality("Bluray-1080p", BLURAY, 1080),
    Quality("Bluray-1080p Remux", BLURAY_RAW, 1080),
    Quality("HDTV-2160p", TELEVISION, 2160),
    Quality("WEBDL-2160p", WEBDL, 2160),
    Quality("WEBRip-2160p", WEBRIP, 2160),
    Quality("Bluray-2160p", BLURAY, 2160),
    Quality("Bluray-2160p Remux", BLURAY_RAW, 2160),
)

BY_NAME = {quality.name: quality for quality in QUALITIES}
UNKNOWN_QUALITY = BY_NAME["Unknown"]

#: Sonarr's default order, lowest first. WEB-DL and WEBRip of one resolution share a place.
WEIGHTS: dict[str, int] = {
    "Unknown": 1,
    "SDTV": 2,
    "WEBDL-480p": 3,
    "WEBRip-480p": 3,
    "DVD": 4,
    "Bluray-480p": 5,
    "Bluray-576p": 6,
    "HDTV-720p": 7,
    "HDTV-1080p": 8,
    "Raw-HD": 9,
    "WEBDL-720p": 10,
    "WEBRip-720p": 10,
    "Bluray-720p": 11,
    "WEBDL-1080p": 12,
    "WEBRip-1080p": 12,
    "Bluray-1080p": 13,
    "Bluray-1080p Remux": 14,
    "HDTV-2160p": 15,
    "WEBDL-2160p": 16,
    "WEBRip-2160p": 16,
    "Bluray-2160p": 17,
    "Bluray-2160p Remux": 18,
}

#: What the movie reader finds, under Sonarr's name.
FROM_MOVIE: dict[str, str] = {
    "Remux-1080p": "Bluray-1080p Remux",
    "Remux-2160p": "Bluray-2160p Remux",
    "DVD-R": "DVD",
}
#: Qualities of the movie table Sonarr does not have. They keep the resolution of the name (see the module text).
UNKNOWN_TO_SONARR = frozenset({"CAM", "TELESYNC", "TELECINE", "WORKPRINT", "DVDSCR", "REGIONAL", "BR-DISK"})
#: What the series reader reads a second time, the way Sonarr does (``parser.parse_quality``). DVD-R is among them:
#: ``FROM_MOVIE`` calls it DVD for a file that is already there, but of a name Sonarr makes a DVD only when the word
#: DVD stands on its own in it (measured on 20.09.2026).
READ_AS_SONARR = UNKNOWN_TO_SONARR | {"DVD-R"}


def named(name: str) -> Quality:
    return BY_NAME[name]


def from_movie(quality: q.Quality, resolution: int | None = None) -> Quality:
    """The Sonarr quality of what the movie reader found.

    ``resolution`` is the one the name carries. Sonarr knows no CAM, TELESYNC and the like: it keeps the
    resolution and calls such a release HDTV. Without a resolution nothing is left but ``Unknown``.
    """
    name = FROM_MOVIE.get(quality.name, quality.name)
    if name in BY_NAME:
        return BY_NAME[name]
    from_resolution = find(TELEVISION, resolution) if resolution else None
    return from_resolution or UNKNOWN_QUALITY


def find(source: int, resolution: int) -> Quality | None:
    """The quality with exactly this source and resolution, or None."""
    for quality in QUALITIES:
        if quality.source == source and quality.resolution == resolution:
            return quality
    return None


def same_kind_at(quality: Quality, resolution: int) -> Quality | None:
    """The same source at another resolution, if Sonarr has it. There is no Bluray-720p Remux."""
    return find(quality.source, resolution)
