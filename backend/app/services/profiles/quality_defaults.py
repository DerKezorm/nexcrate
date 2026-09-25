"""What a quality may weigh when nobody has said anything: Radarr's and Sonarr's own defaults.

Measured on 20.09.2026 against two throwaway instances (Radarr 5 and Sonarr 4.0.19, both untouched) with
``GET /api/v3/qualitydefinition``: ``minSize``, ``maxSize`` and ``preferredSize`` in MB per minute, exactly as the
interface shows them. Written down as data, like the quality tables themselves, because a switcher expects the
numbers of the app they come from.

Radarr gives every quality the same pair and leaves the top ones without an upper limit; Sonarr's grow with the
quality. ``None`` means no upper limit.

The names are nexcrate's, which are Radarr's for movies and Sonarr's for series, so they line up quality by
quality with what was measured.
"""

from __future__ import annotations

#: quality -> (minimum, maximum, preferred) in MB per minute. Preferred decides between releases that are equal in
#: everything else (``search/ranking.py``, step 8); ``None`` there means the larger one wins, as in Radarr.
MOVIE: dict[str, tuple[float, float | None, float | None]] = {
    "Unknown": (0, 100, 95),
    "WORKPRINT": (0, 100, 95),
    "CAM": (0, 100, 95),
    "TELESYNC": (0, 100, 95),
    "TELECINE": (0, 100, 95),
    "REGIONAL": (0, 100, 95),
    "DVDSCR": (0, 100, 95),
    "SDTV": (0, 100, 95),
    "DVD": (0, 100, 95),
    "DVD-R": (0, 100, 95),
    "WEBDL-480p": (0, 100, 95),
    "WEBRip-480p": (0, 100, 95),
    "Bluray-480p": (0, 100, 95),
    "Bluray-576p": (0, 100, 95),
    "HDTV-720p": (0, 100, 95),
    "WEBDL-720p": (0, 100, 95),
    "WEBRip-720p": (0, 100, 95),
    "Bluray-720p": (0, 100, 95),
    "HDTV-1080p": (0, 100, 95),
    "WEBDL-1080p": (0, 100, 95),
    "WEBRip-1080p": (0, 100, 95),
    "Bluray-1080p": (0, None, None),
    "Remux-1080p": (0, None, None),
    "HDTV-2160p": (0, None, None),
    "WEBDL-2160p": (0, None, None),
    "WEBRip-2160p": (0, None, None),
    "Bluray-2160p": (0, None, None),
    "Remux-2160p": (0, None, None),
    "BR-DISK": (0, None, None),
    "Raw-HD": (0, None, None),
}

SERIES: dict[str, tuple[float, float | None, float | None]] = {
    "Unknown": (1, 199.9, 95),
    "SDTV": (2, 100, 95),
    "WEBRip-480p": (2, 100, 95),
    "WEBDL-480p": (2, 100, 95),
    "DVD": (2, 100, 95),
    "Bluray-480p": (2, 100, 95),
    "Bluray-576p": (2, 100, 95),
    "HDTV-720p": (3, 125, 95),
    "HDTV-1080p": (4, 125, 95),
    "Raw-HD": (4, None, 95),
    "WEBRip-720p": (3, 130, 95),
    "WEBDL-720p": (3, 130, 95),
    "Bluray-720p": (4, 130, 95),
    "WEBRip-1080p": (4, 130, 95),
    "WEBDL-1080p": (4, 130, 95),
    "Bluray-1080p": (4, 155, 95),
    "Bluray-1080p Remux": (35, None, 95),
    "HDTV-2160p": (35, 199.9, 95),
    "WEBRip-2160p": (35, None, 95),
    "WEBDL-2160p": (35, None, 95),
    "Bluray-2160p": (35, None, 95),
    "Bluray-2160p Remux": (35, None, 95),
}

BY_KIND: dict[str, dict[str, tuple[float, float | None, float | None]]] = {"movie": MOVIE, "series": SERIES}
