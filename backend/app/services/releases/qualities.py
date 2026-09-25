"""Radarr's quality table for movies: names, sources, resolutions, modifiers.

Facts from Radarr's source (read 13.09.2026, see the design notes), written down as data. TRaSH's
profiles and custom formats name qualities by these names and compare sources, resolutions and modifiers by
these numbers, so nexcrate uses the same ones.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Radarr's source numbers, by position. TRaSH's SourceSpecification uses 5, 7, 8 and 9.
SOURCES = ("UNKNOWN", "CAM", "TELESYNC", "TELECINE", "WORKPRINT", "DVD", "TV", "WEBDL", "WEBRIP", "BLURAY")
#: Radarr's modifier numbers, by position. TRaSH's QualityModifierSpecification uses 5 (REMUX).
MODIFIERS = ("NONE", "REGIONAL", "SCREENER", "RAWHD", "BRDISK", "REMUX")

UNKNOWN, CAM, TELESYNC, TELECINE, WORKPRINT, DVD, TV, WEBDL, WEBRIP, BLURAY = range(10)
NONE, REGIONAL, SCREENER, RAWHD, BRDISK, REMUX = range(6)


@dataclass(frozen=True)
class Quality:
    id: int
    name: str
    source: int
    resolution: int
    modifier: int = NONE


QUALITIES: tuple[Quality, ...] = (
    Quality(0, "Unknown", UNKNOWN, 0),
    Quality(24, "WORKPRINT", WORKPRINT, 0),
    Quality(25, "CAM", CAM, 0),
    Quality(26, "TELESYNC", TELESYNC, 0),
    Quality(27, "TELECINE", TELECINE, 0),
    Quality(28, "DVDSCR", DVD, 480, SCREENER),
    Quality(29, "REGIONAL", DVD, 480, REGIONAL),
    Quality(1, "SDTV", TV, 480),
    Quality(2, "DVD", DVD, 0),
    Quality(23, "DVD-R", DVD, 480, REMUX),
    Quality(4, "HDTV-720p", TV, 720),
    Quality(9, "HDTV-1080p", TV, 1080),
    Quality(16, "HDTV-2160p", TV, 2160),
    Quality(8, "WEBDL-480p", WEBDL, 480),
    Quality(5, "WEBDL-720p", WEBDL, 720),
    Quality(3, "WEBDL-1080p", WEBDL, 1080),
    Quality(18, "WEBDL-2160p", WEBDL, 2160),
    Quality(12, "WEBRip-480p", WEBRIP, 480),
    Quality(14, "WEBRip-720p", WEBRIP, 720),
    Quality(15, "WEBRip-1080p", WEBRIP, 1080),
    Quality(17, "WEBRip-2160p", WEBRIP, 2160),
    Quality(20, "Bluray-480p", BLURAY, 480),
    Quality(21, "Bluray-576p", BLURAY, 576),
    Quality(6, "Bluray-720p", BLURAY, 720),
    Quality(7, "Bluray-1080p", BLURAY, 1080),
    Quality(19, "Bluray-2160p", BLURAY, 2160),
    Quality(30, "Remux-1080p", BLURAY, 1080, REMUX),
    Quality(31, "Remux-2160p", BLURAY, 2160, REMUX),
    Quality(22, "BR-DISK", BLURAY, 1080, BRDISK),
    Quality(10, "Raw-HD", TV, 1080, RAWHD),
)

BY_NAME = {quality.name: quality for quality in QUALITIES}
UNKNOWN_QUALITY = BY_NAME["Unknown"]

#: Radarr's default order, lowest first. WEB-DL and WEBRip of one resolution share a place.
WEIGHTS: dict[str, int] = {
    "Unknown": 1,
    "WORKPRINT": 2,
    "CAM": 3,
    "TELESYNC": 4,
    "TELECINE": 5,
    "REGIONAL": 6,
    "DVDSCR": 7,
    "SDTV": 8,
    "DVD": 9,
    "DVD-R": 10,
    "WEBDL-480p": 11,
    "WEBRip-480p": 11,
    "Bluray-480p": 12,
    "Bluray-576p": 13,
    "HDTV-720p": 14,
    "WEBDL-720p": 15,
    "WEBRip-720p": 15,
    "Bluray-720p": 16,
    "HDTV-1080p": 17,
    "WEBDL-1080p": 18,
    "WEBRip-1080p": 18,
    "Bluray-1080p": 19,
    "Remux-1080p": 20,
    "HDTV-2160p": 21,
    "WEBDL-2160p": 22,
    "WEBRip-2160p": 22,
    "Bluray-2160p": 23,
    "Remux-2160p": 24,
    "BR-DISK": 25,
    "Raw-HD": 26,
}


def named(name: str) -> Quality:
    return BY_NAME[name]


def find(source: int, resolution: int, modifier: int = NONE) -> Quality | None:
    """The quality with exactly this source, resolution and modifier, or None."""
    for quality in QUALITIES:
        if quality.source == source and quality.resolution == resolution and quality.modifier == modifier:
            return quality
    return None


def same_kind_at(quality: Quality, resolution: int) -> Quality | None:
    """The same source and modifier at another resolution, if Radarr has it. There is no Remux-720p."""
    return find(quality.source, resolution, quality.modifier)
