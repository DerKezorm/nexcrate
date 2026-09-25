"""Media data of a video file through ``mediainfo``, and the quality rule of decision 22 (plan-library-from-disk, L7).

* **The tool** runs as a child process: ``mediainfo --Output=JSON <absolute path>``, an argument list and no shell,
  stdin closed, a small environment (``PATH`` and ``LANG=C.UTF-8``), its own process group, a timeout after which it
  is killed with its group, standard output read up to 4 MiB. Only absolute local paths are passed, never an address.
  Files Radarr does not read either (``.img``, ``.iso``, ``.vob``, ``.m3u``, ``.strm``) are not read. A failure gives
  ``media_unreadable``, a timeout ``media_timeout``; the name decides then.
* **What is kept** is nexcrate's own shape, independent of the tool (``schema`` 1): container, title tag, duration,
  the video's codec, size, bit depth, dynamic range, Dolby Vision profile and encoder (``x264``; since 17.09.2026, for
  the codec of a file name as Sonarr writes it, older data lacks it), every audio stream's codec, channels and
  language, and the subtitle languages. The mapping of dynamic range and audio codec follows Radarr's names; it was
  checked against MediaInfo 25.04 on the bench samples (15.09.2026), the recorded answers lie in
  ``tests/fixtures/mediainfo``.
* **The quality** (``quality_of``) is one function with the six steps of decision 22: the resolution from the media data
  with Radarr's thresholds, source and resolution from a container title tag, source, modifier and revision from the
  file name and then the folder name, Radarr's source by extension when no name says one, ``qualities.find`` and then
  the nearest quality as Radarr's ``QualityFinder`` picks it, and where the quality came from.
* **Languages** (decision 24): the known audio stream languages win; ``und`` and unknown codes are left out; without
  one, the name's languages as the release checker resolves them.

Log lines carry codes and counts, never names or paths.
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import releases
from .releases import languages as release_languages
from .releases import parser as release_parser
from .releases import qualities as q
from .subtitles import languages as subtitle_languages

logger = logging.getLogger("nexcrate.media")

#: The command, the path appended. Tests replace it with a Python script that prints a recorded answer.
COMMAND: tuple[str, ...] = ("mediainfo", "--Output=JSON")
#: The start value from the plan; the bench sets it.
TIMEOUT_SECONDS = 30.0
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
#: After the timeout the group is killed; this long it may take to die before the reader gives up.
KILL_GRACE_SECONDS = 5.0
#: Not read, as in Radarr: their quality comes from the name.
NOT_READ = frozenset({".img", ".iso", ".vob", ".m3u", ".strm"})
SCHEMA = 1
TOOL = "mediainfo"

MEDIA_TIMEOUT = "media_timeout"
MEDIA_UNREADABLE = "media_unreadable"
#: The container says it holds more than the file has: cut off, as by a copy that stopped (24.09.2026).
MEDIA_TRUNCATED = "media_truncated"

#: Radarr's source by extension for a name without a source (decision 22): source and the resolution Radarr's fallback
#: quality carries, which counts only when neither the media data nor a name gives one.
EXTENSION_SOURCES: dict[str, tuple[int, int]] = {
    ".mkv": (q.WEBDL, 720),
    ".mk3d": (q.WEBDL, 720),
    ".m2ts": (q.BLURAY, 720),
}
#: Every other extension, ``.webm`` too (measured against Radarr 6.3 on 24.09.2026: SDTV at 480, HDTV-1080p at 1080).
EXTENSION_DEFAULT = (q.TV, 480)

_DV_PROFILE = re.compile(r"dv(?:he|av|a1|hE|AV)\.(\d{1,2})", re.IGNORECASE)
_CHUNK = 64 * 1024


# --- The child process ------------------------------------------------------------------------------------------ #


@dataclass(frozen=True)
class ToolAnswer:
    """What the tool gave: the JSON text, or the error code."""

    text: str | None
    error_code: str | None
    tool_version: str | None = None


def readable(path: Path) -> bool:
    """Whether the tool is run for this file at all."""
    return path.suffix.lower() not in NOT_READ


def _environment() -> dict[str, str]:
    env = {"LANG": "C.UTF-8"}
    path = os.environ.get("PATH")
    if path:
        env["PATH"] = path
    if os.name == "nt":
        # Windows needs SYSTEMROOT to start most executables at all.
        for name in ("SYSTEMROOT", "SystemRoot", "TEMP", "TMP"):
            value = os.environ.get(name)
            if value:
                env[name] = value
    return env


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    """Kill the child with its process group; on Windows the child alone (there is no group kill)."""
    try:
        if os.name != "nt":
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        else:
            process.kill()
    except (OSError, ProcessLookupError):
        try:
            process.kill()
        except OSError:
            pass


def _read_output(stream: Any, chunks: list[bytes], state: dict[str, bool]) -> None:
    total = 0
    try:
        while True:
            chunk = stream.read(_CHUNK)
            if not chunk:
                return
            total += len(chunk)
            if total > MAX_OUTPUT_BYTES:
                state["too_large"] = True
                chunks.clear()
                return
            chunks.append(chunk)
    except (OSError, ValueError):
        state["failed"] = True


def run_tool(path: Path) -> ToolAnswer:
    """Run the tool on one absolute local path. Never raises."""
    if not path.is_absolute():
        return ToolAnswer(None, MEDIA_UNREADABLE)
    if not readable(path):
        return ToolAnswer(None, MEDIA_UNREADABLE)
    arguments = [*COMMAND, str(path)]
    options: dict[str, Any] = {}
    if os.name != "nt":
        options["start_new_session"] = True
    else:
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        # An argument list, no shell, only a local path.
        process = subprocess.Popen(
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_environment(),
            close_fds=True,
            **options,
        )
    except (OSError, ValueError):
        logger.warning("The media tool could not be started")
        return ToolAnswer(None, MEDIA_UNREADABLE)
    chunks: list[bytes] = []
    state: dict[str, bool] = {}
    reader = threading.Thread(target=_read_output, args=(process.stdout, chunks, state), daemon=True)
    reader.start()
    timed_out = False
    try:
        process.wait(timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_group(process)
        try:
            process.wait(timeout=KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            logger.warning("The media tool did not die after its timeout")
    if state.get("too_large"):
        _kill_group(process)
    reader.join(KILL_GRACE_SECONDS)
    try:
        if process.stdout is not None:
            process.stdout.close()
    except OSError:
        pass
    if timed_out:
        logger.info("The media tool ran into its timeout of %d seconds", TIMEOUT_SECONDS)
        return ToolAnswer(None, MEDIA_TIMEOUT)
    if state.get("too_large") or state.get("failed") or process.returncode != 0:
        logger.info("The media tool failed: exit %s", process.returncode)
        return ToolAnswer(None, MEDIA_UNREADABLE)
    try:
        return ToolAnswer(b"".join(chunks).decode("utf-8"), None)
    except UnicodeDecodeError:
        return ToolAnswer(None, MEDIA_UNREADABLE)


# --- The mapping -------------------------------------------------------------------------------------------------- #


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = _text(value)
    if text:
        try:
            return int(float(text))
        except ValueError:
            return None
    return None


def _language(value: Any) -> str | None:
    """ISO 639-1 through the subtitle table; ``und`` and unknown codes are None."""
    text = _text(value)
    if not text:
        return None
    code = text.split("-")[0]
    return subtitle_languages.by_code(code)


def dynamic_range(video: dict[str, Any]) -> tuple[str | None, int | None]:
    """Radarr's name of the dynamic range and the Dolby Vision profile from a MediaInfo video track."""
    bit_depth = _int(video.get("BitDepth"))
    if bit_depth is None or bit_depth < 10:
        return None, None
    hdr_format = _text(video.get("HDR_Format"))
    compatibility = _text(video.get("HDR_Format_Compatibility"))
    transfer = _text(video.get("transfer_characteristics")).upper()
    if "dolby vision" in hdr_format.lower():
        match = _DV_PROFILE.search(_text(video.get("HDR_Format_Profile")))
        profile = int(match.group(1)) if match else None
        lowered = compatibility.lower()
        if "hdr10+" in lowered:
            return "DV HDR10Plus", profile
        if "hdr10" in lowered:
            return "DV HDR10", profile
        if "hlg" in lowered:
            return "DV HLG", profile
        if "sdr" in lowered:
            return "DV SDR", profile
        return "DV", profile
    if "2094 app 4" in hdr_format.lower() or "hdr10+" in compatibility.lower():
        return "HDR10Plus", None
    if transfer == "PQ":
        mastering = any(
            _text(video.get(key)) for key in ("MasteringDisplay_ColorPrimaries", "MasteringDisplay_Luminance", "MaxCLL")
        )
        return ("HDR10" if mastering or "2086" in hdr_format else "PQ"), None
    if transfer == "HLG":
        return "HLG", None
    return None, None


def audio_codec(audio: dict[str, Any]) -> str:
    """Radarr's audio codec name from MediaInfo's ``Format``, ``Format_Commercial_IfAny`` and
    ``Format_AdditionalFeatures``."""
    fmt = _text(audio.get("Format"))
    commercial = _text(audio.get("Format_Commercial_IfAny")).lower()
    features = _text(audio.get("Format_AdditionalFeatures")).upper().split()
    profile = _text(audio.get("Format_Profile")).lower()
    upper = fmt.upper()
    if upper in ("MLP FBA", "TRUEHD", "MLP"):
        return "TrueHD Atmos" if "atmos" in commercial or "16-CH" in features else "TrueHD"
    if upper == "DTS":
        if "X" in features or "dts:x" in commercial:
            return "DTS-X"
        if "XLL" in features or "master audio" in commercial:
            return "DTS-HD MA"
        if "XBR" in features or "high resolution" in commercial:
            return "DTS-HD HRA"
        if "ES" in features or "dts-es" in commercial:
            return "DTS-ES"
        return "DTS"
    if upper == "E-AC-3":
        return "EAC3 Atmos" if "JOC" in features or "atmos" in commercial else "EAC3"
    if upper == "AC-3":
        return "AC3"
    if upper == "MPEG AUDIO":
        if "layer 3" in profile:
            return "MP3"
        if "layer 2" in profile:
            return "MP2"
        return fmt
    if upper in ("AAC", "FLAC", "PCM", "OPUS"):
        return {"AAC": "AAC", "FLAC": "FLAC", "PCM": "PCM", "OPUS": "Opus"}[upper]
    return fmt or "Unknown"


def audio_channels(audio: dict[str, Any]) -> str | None:
    """The channel count as ``n.1`` when the layout has an LFE, else ``n.0``; None without a count."""
    count = _int(audio.get("Channels"))
    if count is None or count <= 0:
        return None
    layout = f"{_text(audio.get('ChannelLayout'))} {_text(audio.get('ChannelPositions'))}".upper()
    if "LFE" in layout.split() or " LFE" in layout or "LFE," in layout:
        return f"{count - 1}.1"
    if not layout.strip() and count >= 6:
        return f"{count - 1}.1"
    return f"{count}.0"


def from_mediainfo(text: str) -> dict[str, Any] | None:
    """nexcrate's shape from the tool's JSON text; None when the text is no MediaInfo answer."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    media = data.get("media") if isinstance(data.get("media"), dict) else {}
    tracks = [track for track in (media.get("track") or []) if isinstance(track, dict)]
    if not tracks:
        return None
    library = data.get("creatingLibrary") if isinstance(data.get("creatingLibrary"), dict) else {}
    general = next((track for track in tracks if track.get("@type") == "General"), {})
    video = next((track for track in tracks if track.get("@type") == "Video"), None)
    audios = [track for track in tracks if track.get("@type") == "Audio"]
    if video is None and not audios:
        # The tool answers for any file it can open, a text file or random bytes included: without a single video or
        # audio track there is no media data to store (seen on the bench, 15.09.2026).
        return None
    texts = [track for track in tracks if track.get("@type") == "Text"]
    duration = _int(general.get("Duration"))
    video_out: dict[str, Any] | None = None
    if video is not None:
        hdr, profile = dynamic_range(video)
        video_out = {
            "codec": _text(video.get("Format")) or None,
            "width": _int(video.get("Width")),
            "height": _int(video.get("Height")),
            "bit_depth": _int(video.get("BitDepth")),
            "dynamic_range": hdr,
            "dv_profile": profile,
            "encoder": _text(video.get("Encoded_Library_Name")) or None,
        }
    subtitles: list[str] = []
    for track in texts:
        code = _language(track.get("Language"))
        if code and code not in subtitles:
            subtitles.append(code)
    return {
        "schema": SCHEMA,
        "tool": TOOL,
        "tool_version": _text(library.get("version")) or None,
        "container": _text(general.get("Format")) or None,
        "title_tag": _text(general.get("Title")) or None,
        "duration_seconds": duration,
        "video": video_out,
        "audio": [
            {
                "codec": audio_codec(track),
                "channels": audio_channels(track),
                "language": _language(track.get("Language")),
            }
            for track in audios
        ],
        "subtitles": subtitles,
    }


#: The names Radarr, Sonarr and Lidarr use in their own short media data. They answer a flat record of strings and
#: numbers, not the tool's tracks; ``from_arr`` brings it into nexcrate's shape.
ARR_TOOLS = frozenset({"radarr", "sonarr", "lidarr"})
ARR_KEYS = frozenset(
    {"audioChannels", "audioCodec", "audioLanguages", "subtitles", "videoBitDepth", "videoCodec",
     "videoDynamicRangeType", "resolution", "runTime"}
)
_RESOLUTION = re.compile(r"^\s*(\d{2,5})\s*[x×]\s*(\d{2,5})\s*$")


def is_arr_shape(media: Any) -> bool:
    """Whether this is an Arr's own media record and not nexcrate's shape."""
    return isinstance(media, dict) and "schema" not in media and bool(ARR_KEYS & set(media))


def _arr_channels(value: Any) -> str | None:
    """Radarr's channel count as it writes it: ``5.1``, ``2.0``; None without one."""
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        count = float(value)
    except ValueError:
        return None
    return f"{count:.1f}" if count > 0 else None


def _arr_seconds(value: Any) -> int | None:
    """``runTime`` as ``h:mm:ss`` or ``mm:ss`` in seconds; None when it says nothing."""
    text = _text(value)
    if not text:
        return None
    parts = text.split(":")
    if not all(part.strip().isdigit() for part in parts) or not 1 < len(parts) < 4:
        return None
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds


def from_arr(media: Any, tool: str) -> dict[str, Any] | None:
    """nexcrate's shape from the short media data of Radarr, Sonarr or Lidarr; None when there is nothing in it.

    The Arr already spells the codec as it wants it written (``x265``, ``AVC``); ``tool`` says so, and the naming
    keeps that spelling instead of deriving one. Languages and subtitles are left out: the Arr names them in words,
    and the import stores its languages on the version anyway.
    """
    if not isinstance(media, dict):
        return None
    codec = _text(media.get("videoCodec"))
    dynamic = _text(media.get("videoDynamicRangeType"))
    audio = _text(media.get("audioCodec"))
    channels = _arr_channels(media.get("audioChannels"))
    found = _RESOLUTION.match(_text(media.get("resolution")))
    width = int(found.group(1)) if found else None
    height = int(found.group(2)) if found else None
    depth = _int(media.get("videoBitDepth"))
    if not any((codec, dynamic, audio, channels, width, depth)):
        return None
    video = None
    if codec or dynamic or width or depth:
        video = {
            "codec": codec or None,
            "width": width,
            "height": height,
            "bit_depth": depth or None,
            "dynamic_range": dynamic,
            "dv_profile": None,
            "encoder": None,
        }
    return {
        "schema": SCHEMA,
        "tool": tool,
        "tool_version": None,
        "container": None,
        "title_tag": None,
        "duration_seconds": _arr_seconds(media.get("runTime")),
        "video": video,
        "audio": [{"codec": audio or None, "channels": channels, "language": None}] if audio or channels else [],
        "subtitles": [],
    }


@dataclass(frozen=True)
class Read:
    """The outcome of reading one file: the media data, or the error code."""

    media: dict[str, Any] | None
    error_code: str | None


def truncated(text: str) -> bool:
    """MediaInfo's ``IsTruncated``: the container declares more bytes than the file has.

    Measured on 24.09.2026 with MediaInfo 25.04: set for every cut MKV and MP4 (index at the end or at the start) from
    one percent lost on, never for a whole file (the eight samples, 150 movies and 145 seasons of a real library). A cut
    MKV keeps its whole duration and resolution in the header, so without this it reads like a whole file.
    """
    try:
        data = json.loads(text)
    except ValueError:
        return False
    tracks = (data.get("media") or {}).get("track") if isinstance(data, dict) else None
    for track in tracks if isinstance(tracks, list) else []:
        if isinstance(track, dict) and track.get("@type") == "General":
            extra = track.get("extra")
            return isinstance(extra, dict) and str(extra.get("IsTruncated", "")).strip().lower() == "yes"
    return False


def read(path: Path) -> Read:
    """Read the media data of one file. Never raises; a file that is not read gives ``media_unreadable``, a cut off one
    ``media_truncated`` (its header's data is not kept: the name decides, as for an unreadable file)."""
    answer = run_tool(path)
    if answer.text is None:
        return Read(None, answer.error_code or MEDIA_UNREADABLE)
    if truncated(answer.text):
        logger.info("The media tool says the file is cut off")
        return Read(None, MEDIA_TRUNCATED)
    media = from_mediainfo(answer.text)
    if media is None:
        logger.info("The media tool answered, but not with media data")
        return Read(None, MEDIA_UNREADABLE)
    return Read(media, None)


# --- Quality (decision 22) ------------------------------------------------------------------------------------------ #


def resolution_of(media: dict[str, Any] | None) -> int:
    """Radarr's resolution from the measured size: 2160, 1080, 720, 576, 480, or 0 without media data."""
    video = (media or {}).get("video") if isinstance(media, dict) else None
    if not isinstance(video, dict):
        return 0
    width, height = video.get("width") or 0, video.get("height") or 0
    if not width and not height:
        return 0
    if width >= 3200 or height >= 2100:
        return 2160
    if width >= 1800 or height >= 1000:
        return 1080
    if width >= 1200 or height >= 700:
        return 720
    if width >= 1000 or height >= 560:
        return 576
    return 480


def nearest_quality(source: int, resolution: int, modifier: int) -> q.Quality:
    """Radarr's ``QualityFinder``: the exact match; else a quality of this source and modifier without a resolution
    (DVD, CAM, TELESYNC and the like, whatever the file measures); else the quality of this resolution and modifier with
    the lowest source at or above the wanted one; else Unknown. Measured against Radarr 6.3 on 24.09.2026: a DVDRip of
    1080 lines is DVD, a Remux of 720 lines is Unknown."""
    exact = q.find(source, resolution, modifier)
    if exact is not None:
        return exact
    for quality in q.QUALITIES:
        if (
            quality.source == source
            and quality.resolution == 0
            and quality.modifier == modifier
            and quality is not q.UNKNOWN_QUALITY
        ):
            return quality
    candidates = sorted(
        (quality for quality in q.QUALITIES if quality.resolution == resolution and quality.modifier == modifier),
        key=lambda quality: quality.source,
    )
    for quality in candidates:
        if quality.source >= source:
            return quality
    return q.UNKNOWN_QUALITY


@dataclass(frozen=True)
class QualityDecision:
    quality: str
    #: ``media`` or ``name``.
    quality_from: str
    #: What the file name alone would have given, for the interface.
    name_quality: str
    #: Which parts came from where: ``resolution`` and ``source`` each ``media``, ``tag``, ``name``, ``folder``,
    #: ``extension`` or ``none``.
    resolution_from: str
    source_from: str


def quality_of(file_name: str, folder_name: str | None, media: dict[str, Any] | None) -> QualityDecision:
    """The quality of a file as Radarr decides it (decision 22), in one place for assigning, restoring and filing
    away."""
    parsed_file = release_parser.parse_movie(file_name)
    parsed_folder = release_parser.parse_movie(folder_name) if folder_name else None
    folder_quality = parsed_folder.quality if parsed_folder is not None else q.UNKNOWN_QUALITY
    # A bare resolution makes the parser guess HDTV; Radarr trusts that guess below any source a name states.
    file_source = parsed_file.quality.source if release_parser.names_source(file_name) else q.UNKNOWN
    folder_source = (
        folder_quality.source if folder_name is not None and release_parser.names_source(folder_name) else q.UNKNOWN
    )
    # What the names alone say: the file name, the folder name where the file name says nothing.
    if file_source != q.UNKNOWN:
        name_quality = parsed_file.quality
    elif folder_source != q.UNKNOWN:
        name_quality = folder_quality
    else:
        name_quality = parsed_file.quality if parsed_file.quality.source != q.UNKNOWN else folder_quality
    if name_quality.source == q.UNKNOWN:
        name_quality = parsed_file.quality
    name_resolution = parsed_file.quality.resolution or folder_quality.resolution

    # 1. The resolution from the media data wins over every name.
    measured = resolution_of(media)
    # 2. A container title tag that names a source counts like Radarr's media data does: its confidence lies above a
    # name's, so its source and resolution replace what the names say (the measured resolution still wins).
    tag_quality: q.Quality | None = None
    tag = media.get("title_tag") if isinstance(media, dict) else None
    # ⚠️ Only a tag that names its source: "THE KING OF QUEENS - S09 E05 - ... - 720P - JAJUNGE" names a resolution
    # alone, the parser guesses HDTV from it, and that guess outranked the BluRay of the release name (23.09.2026).
    if isinstance(tag, str) and tag.strip() and release_parser.names_source(tag):
        candidate = release_parser.parse_movie(tag).quality
        if candidate.source != q.UNKNOWN:
            tag_quality = candidate
    # 3. Source, modifier and revision from the file name, then the folder name for what the file name lacks.
    source, source_from = q.UNKNOWN, "none"
    resolution, resolution_from = 0, "none"
    if file_source != q.UNKNOWN:
        source, source_from = file_source, "name"
    elif folder_source != q.UNKNOWN:
        source, source_from = folder_source, "folder"
    if parsed_file.quality.resolution:
        resolution, resolution_from = parsed_file.quality.resolution, "name"
    elif folder_quality.resolution:
        resolution, resolution_from = folder_quality.resolution, "folder"
    # Remux and BR-DISK come only from a name; of two modifiers the higher counts, as in Radarr.
    modifier = max(parsed_file.quality.modifier, folder_quality.modifier)
    if tag_quality is not None:
        source, source_from = tag_quality.source, "tag"
        if tag_quality.resolution:
            resolution, resolution_from = tag_quality.resolution, "tag"
    if measured:
        resolution, resolution_from = measured, "media"
    # 4. No source named anywhere: Radarr's source by extension. A bare resolution's guess (HDTV) does not count: a
    # "1080p-GROUP.mkv" is WEBDL-1080p in Radarr (measured 24.09.2026).
    if source == q.UNKNOWN:
        source, fallback_resolution = EXTENSION_SOURCES.get(Path(file_name).suffix.lower(), EXTENSION_DEFAULT)
        source_from = "extension"
        if not resolution:
            resolution, resolution_from = fallback_resolution, "extension"
    # 5. Together, else the nearest as Radarr's QualityFinder picks it, else Unknown.
    if source == q.UNKNOWN:
        quality = q.UNKNOWN_QUALITY
    elif modifier == q.BRDISK:
        quality = q.named("BR-DISK")
    elif modifier == q.RAWHD:
        quality = q.named("Raw-HD")
    else:
        # No fallback to the encode for a Remux of a resolution without one: Radarr gives Unknown (measured 24.09.2026).
        quality = nearest_quality(source, resolution, modifier)
    # 6. Where it came from: media when the measured resolution decided or changed the name's quality, or the tag gave
    # the source.
    from_media = (bool(measured) and measured != name_resolution) or (
        source_from == "tag" and tag_quality is not None and tag_quality.source != name_quality.source
    )
    return QualityDecision(
        quality=quality.name,
        quality_from="media" if from_media else "name",
        name_quality=name_quality.name,
        resolution_from=resolution_from,
        source_from=source_from,
    )


# --- Languages (decision 24) ---------------------------------------------------------------------------------------- #


def audio_languages(media: dict[str, Any] | None) -> list[str]:
    """The known audio languages in Radarr's names, in stream order without repeats."""
    found: list[str] = []
    for track in (media or {}).get("audio") or []:
        if not isinstance(track, dict):
            continue
        iso = track.get("language")
        number = release_languages.radarr_id(iso if isinstance(iso, str) else None)
        if number == release_languages.UNKNOWN:
            continue
        name = release_languages.name(number)
        if name not in found:
            found.append(name)
    return found


def file_languages(media: dict[str, Any] | None, named: list[str]) -> list[str]:
    """What a file keeps as its languages (decision 24): the audio streams' known languages, else ``named`` (the
    languages of its release or its name). Since 17.09.2026, when one stream carries no language (``und``, nothing), the
    named ones are added: Radarr keeps only the tagged ones, so an untagged English stream of a DL release makes the
    file look worse than it is, and every DL release is loaded again (measured against Radarr 6.3)."""
    known = audio_languages(media)
    if not known:
        return list(dict.fromkeys(named))
    tracks = [track for track in (media or {}).get("audio") or [] if isinstance(track, dict)]
    untagged = any(
        release_languages.radarr_id(track.get("language") if isinstance(track.get("language"), str) else None)
        == release_languages.UNKNOWN
        for track in tracks
    )
    if untagged:
        return list(dict.fromkeys([*known, *named]))
    return known


def languages_of(media: dict[str, Any] | None, name: str, original_language: str | None) -> list[str]:
    """Decision 24 for a file found on disk: ``file_languages`` with the name's languages as the checker resolves
    them."""
    parsed = releases.parse(name, original_language)
    names: list[str] = []
    for number in parsed.languages:
        if number in (release_languages.UNKNOWN, release_languages.ANY, release_languages.ORIGINAL):
            continue
        label = release_languages.name(number)
        if label not in names:
            names.append(label)
    return file_languages(media, names)


def summary(media: dict[str, Any] | None) -> str | None:
    """One line for the interface, such as ``1920 × 800, HEVC 10 Bit, HDR10, German TrueHD Atmos 7.1``."""
    if not isinstance(media, dict):
        return None
    parts: list[str] = []
    video = media.get("video")
    if isinstance(video, dict):
        if video.get("width") and video.get("height"):
            parts.append(f"{video['width']} × {video['height']}")
        codec = " ".join(
            part
            for part in (video.get("codec"), f"{video['bit_depth']} Bit" if video.get("bit_depth") else None)
            if part
        )
        if codec:
            parts.append(codec)
        if video.get("dynamic_range"):
            parts.append(str(video["dynamic_range"]))
    for track in media.get("audio") or []:
        if not isinstance(track, dict):
            continue
        language = track.get("language")
        label = release_languages.name(release_languages.radarr_id(language)) if language else None
        text = " ".join(
            part
            for part in (label if label and label != "Unknown" else None, track.get("codec"), track.get("channels"))
            if part
        )
        if text:
            parts.append(text)
    return ", ".join(parts) or None
