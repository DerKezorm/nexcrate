"""The quality steps of music (part M2.2, decisions 10 to 12; the owner chose five steps on
18.09.2026).

Five steps, best first, and ``unknown``. MP3 320 and V0 are one step: both are transparent to the ear, and a name
that says "320" is not better than one that says "V0" (research a). FLAC, ALAC, WAV, APE and WavPack are all
lossless; 24 bit is a step of its own because the files are about three times the size.

Two readings end in a step: a parsed release name, and the quality a Lidarr connection reports for a file. Lidarr's
names are read by their pattern (``MP3-320``, ``MP3-VBR-V0``, ``AAC-256``, ``OGG Vorbis Q8``, ``FLAC 24bit``), not
from a table: a name this does not understand stays ``unknown``, never guessed (decision 12).
"""

from __future__ import annotations

import re

from .music_parser import LOSSLESS, ParsedAlbum

LOSSLESS_24 = "lossless_24"
LOSSLESS_16 = "lossless"
LOSSY_HIGH = "lossy_high"
LOSSY_MID = "lossy_mid"
LOSSY_LOW = "lossy_low"
UNKNOWN = "unknown"
#: Best first.
STEPS = (LOSSLESS_24, LOSSLESS_16, LOSSY_HIGH, LOSSY_MID, LOSSY_LOW)
LOSSLESS_STEPS = (LOSSLESS_24, LOSSLESS_16)

#: From which bitrate a lossy format counts as high and as mid (kbit/s), by format. Opus needs far less than MP3.
_THRESHOLDS = {"MP3": (320, 192), "AAC": (256, 192), "Vorbis": (256, 160), "Opus": (160, 96), "WMA": (10_000, 192)}
#: MP3's VBR presets: V0 is about 245 kbit/s and transparent, V1 and V2 about 225 and 190.
_VBR = {"V0": LOSSY_HIGH, "V1": LOSSY_MID, "V2": LOSSY_MID}
_LIDARR_NUMBER = re.compile(r"^(MP3|AAC|WMA|OPUS)[- ](\d{2,3})$", re.IGNORECASE)
_LIDARR_VBR = re.compile(r"^MP3-VBR-(V\d)$", re.IGNORECASE)
_LIDARR_VORBIS = re.compile(r"^OGG Vorbis Q(\d{1,2})$", re.IGNORECASE)
_LIDARR_LOSSLESS = re.compile(r"^(FLAC|ALAC|WAV|APE|WavPack)( 24 ?bit)?$", re.IGNORECASE)


def rank(step: str) -> int:
    """0 for the best step; ``unknown`` comes after the last."""
    return STEPS.index(step) if step in STEPS else len(STEPS)


def _by_bitrate(fmt: str, kbit: int) -> str:
    high, mid = _THRESHOLDS.get(fmt, (10_000, 10_000))
    return LOSSY_HIGH if kbit >= high else LOSSY_MID if kbit >= mid else LOSSY_LOW


def step_of(parsed: ParsedAlbum) -> str:
    """The step a release name says. Lossy without a bitrate is ``unknown``, but for the scene's MP3 names, which
    never carry one and whose rules ask for V0 or 320 (decision 4)."""
    fmt = parsed.format
    if fmt is None:
        return UNKNOWN
    if fmt in LOSSLESS:
        return LOSSLESS_24 if (parsed.bit_depth or 0) >= 24 or fmt == "DSD" else LOSSLESS_16
    rate = parsed.bitrate
    if rate is None:
        return LOSSY_HIGH if fmt == "MP3" and parsed.format_assumed else UNKNOWN
    if rate in _VBR:
        return _VBR[rate] if fmt == "MP3" else UNKNOWN
    if rate.startswith("V"):
        return LOSSY_LOW if fmt == "MP3" else UNKNOWN
    return _by_bitrate(fmt, int(rate))


def bitrate_unknown(parsed: ParsedAlbum) -> bool:
    """The step rests on the scene's rules, not on the name: only the file says the bitrate (M4)."""
    return parsed.format == "MP3" and parsed.format_assumed and parsed.bitrate is None


def step_of_lidarr(name: str | None) -> str:
    """The step of a quality as Lidarr names it for a file (``track_files.quality``)."""
    text = (name or "").strip()
    lossless = _LIDARR_LOSSLESS.match(text)
    if lossless:
        return LOSSLESS_24 if lossless.group(2) else LOSSLESS_16
    number = _LIDARR_NUMBER.match(text)
    if number:
        fmt = {"mp3": "MP3", "aac": "AAC", "wma": "WMA", "opus": "Opus"}[number.group(1).lower()]
        return _by_bitrate(fmt, int(number.group(2)))
    vbr = _LIDARR_VBR.match(text)
    if vbr:
        return _VBR.get(vbr.group(1).upper(), LOSSY_LOW)
    vorbis = _LIDARR_VORBIS.match(text)
    if vorbis:
        level = int(vorbis.group(1))
        return LOSSY_HIGH if level >= 8 else LOSSY_MID if level >= 5 else LOSSY_LOW
    return UNKNOWN


def lowest(steps: list[str]) -> str:
    """The step of an album is the worst of its files (decision 22); nothing known is ``unknown``."""
    known = [step for step in steps if step in STEPS]
    return max(known, key=rank) if known else UNKNOWN
