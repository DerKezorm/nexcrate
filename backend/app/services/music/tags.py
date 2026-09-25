"""Tags and audio facts of music files (M4.2 and M4.5), with ``mutagen``.

One table of fields serves reading and writing, so both speak the same names. The names are Picard's
(research T4): ``musicbrainz_trackid`` is the **recording** id, the id of the track within a release is
``musicbrainz_releasetrackid`` (Lidarr notes the same as SIC).

* **Reading** never raises: a file mutagen cannot open is ``unreadable``; a format without tags nexcrate knows gives
  empty tags. The audio facts come from the stream (codec, length, bit depth, sample rate, channels, bitrate), never
  from the name.
* **Writing** sets only the fields of the table and the front cover; every other field stays (ReplayGain, lyrics,
  ISRC, other pictures: decision 26). An MP3 keeps its ID3 version: 2.3 stays 2.3, a file without ID3 gets 2.4.
  Formats nexcrate writes: FLAC, MP3, M4A, Ogg Vorbis, Ogg Opus (decision 6); the others are read only.
* **Comparing** covers every field of the table, the MusicBrainz ids included (decision 27), and the front cover by a
  hash of its bytes.

Values are lists of strings throughout, since several fields hold more than one value (``artists``,
``musicbrainz_artistid``). ``tracknumber`` and ``discnumber`` hold the number alone; the totals are fields of their own.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import mutagen
from mutagen.apev2 import APEv2File
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, TXXX, UFID, Frames
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

from .. import schreibweisen

logger = logging.getLogger("nexcrate.music")

#: What counts as audio in a download (decision 6).
AUDIO_EXTENSIONS = frozenset(
    {".flac", ".mp3", ".m4a", ".ogg", ".oga", ".opus", ".wav", ".aiff", ".aif", ".ape", ".wv", ".dsf", ".alac"}
)
#: The formats nexcrate writes tags into.
WRITABLE = ("FLAC", "MP3", "M4A", "Vorbis", "Opus")
#: The front cover in ID3, FLAC and Ogg pictures.
FRONT_COVER = 3
UNREADABLE = "unreadable"

# --- The fields ------------------------------------------------------------------------------------------------ #

#: Picard's names (lower case) of every field nexcrate writes and compares, in the order the preview shows them.
FIELDS = (
    "title",
    "artist",
    "artists",
    "album",
    "albumartist",
    "albumartistsort",
    "tracknumber",
    "totaltracks",
    "discnumber",
    "totaldiscs",
    "date",
    "originaldate",
    "releasecountry",
    "releasestatus",
    "releasetype",
    "media",
    "label",
    "catalognumber",
    "barcode",
    "compilation",
    "musicbrainz_trackid",
    "musicbrainz_releasetrackid",
    "musicbrainz_albumid",
    "musicbrainz_releasegroupid",
    "musicbrainz_artistid",
    "musicbrainz_albumartistid",
)
#: Fields that are ids: compared as they are, case ignored.
ID_FIELDS = frozenset(name for name in FIELDS if name.startswith("musicbrainz_"))

#: Vorbis comments (FLAC, Ogg): the name read first, then further names read and written alike.
_VORBIS: dict[str, tuple[str, ...]] = {
    "title": ("TITLE",),
    "artist": ("ARTIST",),
    "artists": ("ARTISTS",),
    "album": ("ALBUM",),
    "albumartist": ("ALBUMARTIST",),
    "albumartistsort": ("ALBUMARTISTSORT",),
    "tracknumber": ("TRACKNUMBER",),
    "totaltracks": ("TRACKTOTAL", "TOTALTRACKS"),
    "discnumber": ("DISCNUMBER",),
    "totaldiscs": ("DISCTOTAL", "TOTALDISCS"),
    "date": ("DATE",),
    "originaldate": ("ORIGINALDATE",),
    "releasecountry": ("RELEASECOUNTRY",),
    "releasestatus": ("RELEASESTATUS",),
    "releasetype": ("RELEASETYPE",),
    "media": ("MEDIA",),
    "label": ("LABEL",),
    "catalognumber": ("CATALOGNUMBER",),
    "barcode": ("BARCODE",),
    "compilation": ("COMPILATION",),
    "musicbrainz_trackid": ("MUSICBRAINZ_TRACKID",),
    "musicbrainz_releasetrackid": ("MUSICBRAINZ_RELEASETRACKID",),
    "musicbrainz_albumid": ("MUSICBRAINZ_ALBUMID",),
    "musicbrainz_releasegroupid": ("MUSICBRAINZ_RELEASEGROUPID",),
    "musicbrainz_artistid": ("MUSICBRAINZ_ARTISTID",),
    "musicbrainz_albumartistid": ("MUSICBRAINZ_ALBUMARTISTID",),
}
#: ID3 text frames; ``TXXX:<description>`` for user text, ``UFID`` for the recording id.
_ID3_TEXT = {
    "title": "TIT2",
    "artist": "TPE1",
    "album": "TALB",
    "albumartist": "TPE2",
    "albumartistsort": "TSO2",
    "date": "TDRC",
    "originaldate": "TDOR",
    "media": "TMED",
    "label": "TPUB",
    "compilation": "TCMP",
}
_ID3_TXXX = {
    "artists": "ARTISTS",
    "releasecountry": "MusicBrainz Album Release Country",
    "releasestatus": "MusicBrainz Album Status",
    "releasetype": "MusicBrainz Album Type",
    "catalognumber": "CATALOGNUMBER",
    "barcode": "BARCODE",
    "musicbrainz_releasetrackid": "MusicBrainz Release Track Id",
    "musicbrainz_albumid": "MusicBrainz Album Id",
    "musicbrainz_releasegroupid": "MusicBrainz Release Group Id",
    "musicbrainz_artistid": "MusicBrainz Artist Id",
    "musicbrainz_albumartistid": "MusicBrainz Album Artist Id",
}
_UFID_OWNER = "http://musicbrainz.org"
#: MP4 atoms; ``----:com.apple.iTunes:<name>`` for free-form fields.
_MP4_ATOM = {
    "title": "\xa9nam",
    "artist": "\xa9ART",
    "album": "\xa9alb",
    "albumartist": "aART",
    "albumartistsort": "soaa",
    "date": "\xa9day",
}
_MP4_FREE = {
    "artists": "ARTISTS",
    "originaldate": "ORIGINALDATE",
    "releasecountry": "MusicBrainz Album Release Country",
    "releasestatus": "MusicBrainz Album Status",
    "releasetype": "MusicBrainz Album Type",
    "media": "MEDIA",
    "label": "LABEL",
    "catalognumber": "CATALOGNUMBER",
    "barcode": "BARCODE",
    "musicbrainz_trackid": "MusicBrainz Track Id",
    "musicbrainz_releasetrackid": "MusicBrainz Release Track Id",
    "musicbrainz_albumid": "MusicBrainz Album Id",
    "musicbrainz_releasegroupid": "MusicBrainz Release Group Id",
    "musicbrainz_artistid": "MusicBrainz Artist Id",
    "musicbrainz_albumartistid": "MusicBrainz Album Artist Id",
}
_MP4_PREFIX = "----:com.apple.iTunes:"
#: APEv2 (APE, WavPack) uses its own names for some fields; read only.
_APE = {
    "title": "Title",
    "artist": "Artist",
    "album": "Album",
    "albumartist": "Album Artist",
    "tracknumber": "Track",
    "discnumber": "Disc",
    "date": "Year",
    "musicbrainz_trackid": "MUSICBRAINZ_TRACKID",
    "musicbrainz_releasetrackid": "MUSICBRAINZ_RELEASETRACKID",
    "musicbrainz_albumid": "MUSICBRAINZ_ALBUMID",
    "musicbrainz_releasegroupid": "MUSICBRAINZ_RELEASEGROUPID",
    "musicbrainz_artistid": "MUSICBRAINZ_ARTISTID",
}


# --- What a file is --------------------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Audio:
    """The stream as mutagen reads it."""

    #: ``FLAC``, ``MP3``, ``AAC``, ``ALAC``, ``Vorbis``, ``Opus``, ``WAV``, ``AIFF``, ``APE``, ``WavPack``, ``DSD``.
    codec: str | None
    duration_ms: int | None
    bit_depth: int | None = None
    sample_rate: int | None = None
    channels: int | None = None
    #: Average kbit/s.
    bitrate: int | None = None
    #: MP3 only: whether the bitrate varies.
    vbr: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "codec": self.codec,
            "duration_ms": self.duration_ms,
            "bit_depth": self.bit_depth,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "bitrate": self.bitrate,
            "vbr": self.vbr,
        }


@dataclass(frozen=True)
class Read:
    #: None when the file could not be read.
    audio: Audio | None
    #: Field name to its values, only fields with a value.
    tags: dict[str, list[str]] = field(default_factory=dict)
    #: The container nexcrate writes tags into (``WRITABLE``), else None.
    writable: str | None = None
    #: SHA-256 of the embedded front cover, when there is one.
    cover_sha256: str | None = None
    error: str | None = None

    def first(self, name: str) -> str | None:
        values = self.tags.get(name) or []
        return values[0] if values else None


def is_audio(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS


# --- Reading ---------------------------------------------------------------------------------------------------- #


def _texts(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        if isinstance(value, bytes):
            value = value.decode("utf-8", "replace")
        text = schreibweisen.nfc(str(value)).strip().replace("\x00", "")
        if text:
            result.append(text)
    return result


def _split_number(values: list[str], number: str, total: str, into: dict[str, list[str]]) -> None:
    """``3/12`` into ``3`` and ``12``; a total already read stays."""
    if not values:
        return
    head, _slash, tail = values[0].partition("/")
    if head.strip():
        into[number] = [head.strip()]
    if tail.strip() and total not in into:
        into[total] = [tail.strip()]


def _read_vorbis(tags: Any) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    if tags is None:
        return found
    upper: dict[str, list[str]] = {}
    for key, value in tags.items():
        upper.setdefault(str(key).upper(), []).extend(_texts(value if isinstance(value, list) else [value]))
    for name, keys in _VORBIS.items():
        for key in keys:
            values = upper.get(key)
            if values:
                found[name] = values
                break
    _split_number(found.pop("tracknumber", []), "tracknumber", "totaltracks", found)
    _split_number(found.pop("discnumber", []), "discnumber", "totaldiscs", found)
    return found


def _id3_values(frame: Any) -> list[str]:
    text = getattr(frame, "text", None)
    if text is None:
        return []
    values: list[str] = []
    for item in text:
        values += _texts(str(item).split("\x00"))
    return values


def _read_id3(tags: Any) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    if tags is None:
        return found
    for name, frame_id in _ID3_TEXT.items():
        frame = tags.get(frame_id)
        if frame_id == "TDOR" and frame is None:
            frame = tags.get("TORY")
        if frame is not None and _id3_values(frame):
            found[name] = _id3_values(frame)
    for name, description in _ID3_TXXX.items():
        wanted = description.casefold()
        for frame in tags.getall("TXXX"):
            if str(frame.desc).casefold() == wanted and _id3_values(frame):
                values = _id3_values(frame)
                # ID3 2.3 joins several ids with "/".
                if name in ID_FIELDS and len(values) == 1 and "/" in values[0]:
                    values = _texts(values[0].split("/"))
                found[name] = values
                break
    for frame in tags.getall("UFID"):
        if frame.owner == _UFID_OWNER and frame.data:
            found["musicbrainz_trackid"] = _texts([frame.data])
    track = tags.get("TRCK")
    if track is not None:
        _split_number(_id3_values(track), "tracknumber", "totaltracks", found)
    disc = tags.get("TPOS")
    if disc is not None:
        _split_number(_id3_values(disc), "discnumber", "totaldiscs", found)
    return found


def _read_mp4(tags: Any) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    if tags is None:
        return found
    for name, atom in _MP4_ATOM.items():
        values = _texts(tags.get(atom) or [])
        if values:
            found[name] = values
    for name, free in _MP4_FREE.items():
        values = _texts(tags.get(_MP4_PREFIX + free) or [])
        if values:
            found[name] = values
    for atom, number, total in (("trkn", "tracknumber", "totaltracks"), ("disk", "discnumber", "totaldiscs")):
        pairs = tags.get(atom) or []
        if pairs and isinstance(pairs[0], tuple):
            first, count = (list(pairs[0]) + [0, 0])[:2]
            if first:
                found[number] = [str(first)]
            if count:
                found[total] = [str(count)]
    if tags.get("cpil"):
        found["compilation"] = ["1"]
    return found


def _read_ape(tags: Any) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    if tags is None:
        return found
    lower = {str(key).casefold(): value for key, value in tags.items()}
    for name, key in _APE.items():
        value = lower.get(key.casefold())
        if value is not None:
            values = _texts(str(value).split("\x00"))
            if values:
                found[name] = values
    _split_number(found.pop("tracknumber", []), "tracknumber", "totaltracks", found)
    _split_number(found.pop("discnumber", []), "discnumber", "totaldiscs", found)
    return found


def _codec(kind: Any, info: Any, path: Path) -> str | None:
    if isinstance(kind, FLAC):
        return "FLAC"
    if isinstance(kind, MP3):
        return "MP3"
    if isinstance(kind, MP4):
        codec = str(getattr(info, "codec", "") or "")
        return "ALAC" if codec.startswith("alac") else "AAC"
    if isinstance(kind, OggOpus):
        return "Opus"
    if isinstance(kind, OggVorbis):
        return "Vorbis"
    extension = path.suffix.lower()
    return {
        ".wav": "WAV",
        ".aiff": "AIFF",
        ".aif": "AIFF",
        ".ape": "APE",
        ".wv": "WavPack",
        ".dsf": "DSD",
    }.get(extension, type(kind).__name__ or None)


def _writable(kind: Any) -> str | None:
    if isinstance(kind, FLAC):
        return "FLAC"
    if isinstance(kind, MP3):
        return "MP3"
    if isinstance(kind, MP4):
        return "M4A"
    if isinstance(kind, OggOpus):
        return "Opus"
    if isinstance(kind, OggVorbis):
        return "Vorbis"
    return None


def _audio(kind: Any, path: Path) -> Audio:
    info = kind.info
    length = getattr(info, "length", None)
    bitrate = getattr(info, "bitrate", None)
    bits = getattr(info, "bits_per_sample", None) or getattr(info, "bits_per_raw_sample", None)
    vbr: bool | None = None
    if isinstance(kind, MP3):
        mode = getattr(info, "bitrate_mode", None)
        vbr = mode is not None and int(mode) in (2, 3)
    return Audio(
        codec=_codec(kind, info, path),
        duration_ms=round(length * 1000) if isinstance(length, int | float) and length > 0 else None,
        bit_depth=int(bits) if isinstance(bits, int) and bits > 0 else None,
        sample_rate=int(info.sample_rate) if getattr(info, "sample_rate", None) else None,
        channels=int(info.channels) if getattr(info, "channels", None) else None,
        bitrate=round(bitrate / 1000) if isinstance(bitrate, int | float) and bitrate > 0 else None,
        vbr=vbr,
    )


def _front_cover(kind: Any) -> bytes | None:
    if isinstance(kind, FLAC):
        for picture in kind.pictures:
            if picture.type == FRONT_COVER:
                return bytes(picture.data)
        return None
    if isinstance(kind, MP3):
        tags = kind.tags
        for frame in tags.getall("APIC") if tags is not None else []:
            if frame.type == FRONT_COVER:
                return bytes(frame.data)
        return None
    if isinstance(kind, MP4):
        covers = (kind.tags or {}).get("covr") or []
        return bytes(covers[0]) if covers else None
    if isinstance(kind, OggOpus | OggVorbis):
        for value in (kind.tags or {}).get("metadata_block_picture", []):
            try:
                picture = Picture(base64.b64decode(value))
            except ValueError, TypeError, mutagen.MutagenError:
                continue
            if picture.type == FRONT_COVER:
                return bytes(picture.data)
    return None


def read(path: Path) -> Read:
    """The tags and audio facts of one file. Never raises."""
    try:
        kind = mutagen.File(path)
    except mutagen.MutagenError, OSError, ValueError, KeyError, IndexError, TypeError, EOFError:
        return Read(audio=None, error=UNREADABLE)
    if kind is None:
        return Read(audio=None, error=UNREADABLE)
    try:
        audio = _audio(kind, path)
        tags = kind.tags
        if isinstance(kind, FLAC | OggOpus | OggVorbis):
            found = _read_vorbis(tags)
        elif isinstance(kind, MP4):
            found = _read_mp4(tags)
        elif isinstance(tags, ID3):
            found = _read_id3(tags)
        elif isinstance(kind, APEv2File) or tags is not None and hasattr(tags, "items"):
            found = _read_ape(tags)
        else:
            found = {}
        cover = _front_cover(kind)
    except mutagen.MutagenError, ValueError, KeyError, IndexError, TypeError, AttributeError:
        logger.info("The tags of a music file could not be read")
        return Read(audio=None, error=UNREADABLE)
    return Read(
        audio=audio,
        tags=found,
        writable=_writable(kind),
        cover_sha256=hashlib.sha256(cover).hexdigest() if cover else None,
    )


def quality_name(audio: Audio | None) -> str | None:
    """The quality of a file in the shape Lidarr names it (``FLAC``, ``FLAC 24bit``, ``MP3-320``, ``MP3-VBR-V0``,
    ``AAC-256``, ``OGG Vorbis Q8``, ``OPUS-160``), so ``music_qualities.step_of_lidarr`` reads files of both sources
    alike (decision 23). None when the stream says too little."""
    if audio is None or audio.codec is None:
        return None
    codec = audio.codec
    if codec in ("FLAC", "ALAC", "WAV", "APE", "WavPack", "AIFF"):
        name = {"AIFF": "WAV"}.get(codec, codec)
        return f"{name} 24bit" if (audio.bit_depth or 16) >= 24 else name
    if codec == "DSD":
        return "FLAC 24bit"
    rate = audio.bitrate
    if rate is None:
        return None
    if codec == "MP3":
        if audio.vbr:
            return "MP3-VBR-V0" if rate >= 220 else "MP3-VBR-V2" if rate >= 170 else "MP3-VBR-V4"
        return f"MP3-{_nearest(rate, (96, 128, 160, 192, 224, 256, 320))}"
    if codec == "AAC":
        return f"AAC-{_nearest(rate, (96, 128, 160, 192, 256, 320))}"
    if codec == "Opus":
        return f"OPUS-{_nearest(rate, (64, 96, 128, 160, 192, 256))}"
    if codec == "Vorbis":
        # Vorbis quality levels by their nominal bitrate: Q5 160, Q6 192, Q8 256, Q10 500.
        level = 10 if rate >= 400 else 8 if rate >= 240 else 6 if rate >= 180 else 5 if rate >= 150 else 3
        return f"OGG Vorbis Q{level}"
    return None


def _nearest(rate: int, known: tuple[int, ...]) -> int:
    """The nominal bitrate at or below the measured one; a stream measures a few kbit/s off its setting."""
    below = [value for value in known if value <= rate + 8]
    return max(below) if below else known[0]


# --- Comparing -------------------------------------------------------------------------------------------------- #


def _comparable(name: str, values: list[str]) -> list[str]:
    cleaned = [value.strip() for value in values if value and value.strip()]
    if name in ID_FIELDS:
        return sorted(value.casefold() for value in cleaned)
    if name in ("tracknumber", "discnumber", "totaltracks", "totaldiscs"):
        return [str(int(value)) if value.isdigit() else value for value in cleaned]
    if name == "compilation":
        return ["1"] if cleaned and cleaned[0] not in ("0", "") else []
    return [schreibweisen.nfc(value) for value in cleaned]


@dataclass(frozen=True)
class Change:
    field: str
    before: list[str]
    after: list[str]


def differences(current: dict[str, list[str]], wanted: dict[str, list[str]]) -> list[Change]:
    """The fields that would change, in the order of ``FIELDS``. A field ``wanted`` leaves out stays as it is."""
    changes: list[Change] = []
    for name in FIELDS:
        if name not in wanted:
            continue
        after = _comparable(name, wanted[name])
        before = _comparable(name, current.get(name, []))
        if before != after:
            changes.append(Change(name, list(current.get(name, [])), list(wanted[name])))
    return changes


# --- Writing ---------------------------------------------------------------------------------------------------- #


class WriteRefused(Exception):
    """The file is not written: ``linked`` (more than one link), ``format`` (not writable), ``unreadable``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def links(path: Path) -> int:
    try:
        return os.stat(path, follow_symlinks=False).st_nlink
    except OSError:
        return 0


def _vorbis_write(tags: Any, wanted: dict[str, list[str]]) -> None:
    for name, values in wanted.items():
        keys = _VORBIS.get(name)
        if keys is None:
            continue
        for key in keys:
            if key in tags:
                del tags[key]
            if values:
                tags[key] = list(values)
    # A number read as "3/12" keeps no slash: the totals are fields of their own now.


def _cover_picture(cover: bytes, mime: str) -> Picture:
    picture = Picture()
    picture.type = FRONT_COVER
    picture.mime = mime
    picture.desc = "Cover"
    picture.data = cover
    return picture


def _write_flac(kind: FLAC, wanted: dict[str, list[str]], cover: tuple[bytes, str] | None) -> None:
    if kind.tags is None:
        kind.add_tags()
    _vorbis_write(kind.tags, wanted)
    if cover is not None:
        kept = [picture for picture in kind.pictures if picture.type != FRONT_COVER]
        kind.clear_pictures()
        for picture in kept:
            kind.add_picture(picture)
        kind.add_picture(_cover_picture(*cover))
    kind.save()


def _write_ogg(kind: OggOpus | OggVorbis, wanted: dict[str, list[str]], cover: tuple[bytes, str] | None) -> None:
    if kind.tags is None:
        kind.add_tags()
    _vorbis_write(kind.tags, wanted)
    if cover is not None:
        kept: list[str] = []
        for value in kind.tags.get("metadata_block_picture", []):
            try:
                if Picture(base64.b64decode(value)).type != FRONT_COVER:
                    kept.append(value)
            except ValueError, TypeError, mutagen.MutagenError:
                kept.append(value)
        encoded = base64.b64encode(_cover_picture(*cover).write()).decode("ascii")
        kind.tags["metadata_block_picture"] = [*kept, encoded]
    kind.save()


def _id3_text(frame_id: str, values: list[str]) -> Any:
    return Frames[frame_id](encoding=3, text=values)


def _write_mp3(kind: MP3, wanted: dict[str, list[str]], cover: tuple[bytes, str] | None) -> None:
    if kind.tags is None:
        kind.add_tags()
    tags = kind.tags
    version = 3 if tags.version[:2] == (2, 3) else 4
    for name, frame_id in _ID3_TEXT.items():
        if name not in wanted:
            continue
        tags.delall(frame_id)
        if frame_id == "TDOR":
            tags.delall("TORY")
        if wanted[name]:
            tags.add(_id3_text(frame_id, wanted[name]))
    for name, description in _ID3_TXXX.items():
        if name not in wanted:
            continue
        for frame in list(tags.getall("TXXX")):
            if str(frame.desc).casefold() == description.casefold():
                tags.delall(f"TXXX:{frame.desc}")
        if wanted[name]:
            tags.add(TXXX(encoding=3, desc=description, text=list(wanted[name])))
    if "musicbrainz_trackid" in wanted:
        tags.delall(f"UFID:{_UFID_OWNER}")
        if wanted["musicbrainz_trackid"]:
            tags.add(UFID(owner=_UFID_OWNER, data=wanted["musicbrainz_trackid"][0].encode("ascii", "replace")))
    for frame_id, number, total in (("TRCK", "tracknumber", "totaltracks"), ("TPOS", "discnumber", "totaldiscs")):
        if number in wanted or total in wanted:
            value = (wanted.get(number) or [""])[0]
            count = (wanted.get(total) or [""])[0]
            tags.delall(frame_id)
            if value:
                tags.add(_id3_text(frame_id, [f"{value}/{count}" if count else value]))
    if cover is not None:
        for frame in list(tags.getall("APIC")):
            if frame.type == FRONT_COVER:
                tags.delall(frame.HashKey)
        tags.add(APIC(encoding=3, mime=cover[1], type=FRONT_COVER, desc="Cover", data=cover[0]))
    kind.save(v2_version=version, v23_sep="/")


def _write_mp4(kind: MP4, wanted: dict[str, list[str]], cover: tuple[bytes, str] | None) -> None:
    if kind.tags is None:
        kind.add_tags()
    tags = kind.tags
    for name, atom in _MP4_ATOM.items():
        if name in wanted:
            tags.pop(atom, None)
            if wanted[name]:
                tags[atom] = list(wanted[name])
    for name, free in _MP4_FREE.items():
        if name in wanted:
            tags.pop(_MP4_PREFIX + free, None)
            if wanted[name]:
                tags[_MP4_PREFIX + free] = [MP4FreeForm(value.encode("utf-8")) for value in wanted[name]]
    for atom, number, total in (("trkn", "tracknumber", "totaltracks"), ("disk", "discnumber", "totaldiscs")):
        if number in wanted or total in wanted:
            value = (wanted.get(number) or ["0"])[0]
            count = (wanted.get(total) or ["0"])[0]
            tags.pop(atom, None)
            if value.isdigit() and int(value) > 0:
                tags[atom] = [(int(value), int(count) if count.isdigit() else 0)]
    if "compilation" in wanted:
        tags.pop("cpil", None)
        if wanted["compilation"]:
            tags["cpil"] = True
    if cover is not None:
        image_format = MP4Cover.FORMAT_PNG if cover[1] == "image/png" else MP4Cover.FORMAT_JPEG
        tags["covr"] = [MP4Cover(cover[0], imageformat=image_format)]
    kind.save()


def write(path: Path, wanted: dict[str, list[str]], cover: tuple[bytes, str] | None = None) -> None:
    """Write the fields of ``wanted`` (and the front cover, as bytes and MIME type) into the file in place.

    ⚠️ Refuses a file with more than one link (a hardlink into a torrent, research T1): its bytes are the torrent's.
    Raises ``WriteRefused``; a failure while saving raises ``OSError`` or ``mutagen.MutagenError``.
    """
    if links(path) != 1:
        raise WriteRefused("linked")
    try:
        kind = mutagen.File(path)
    except (mutagen.MutagenError, ValueError, KeyError, IndexError, TypeError, EOFError) as exc:
        raise WriteRefused(UNREADABLE) from exc
    if kind is None:
        raise WriteRefused(UNREADABLE)
    wanted = {name: list(values) for name, values in wanted.items() if name in FIELDS}
    if isinstance(kind, FLAC):
        _write_flac(kind, wanted, cover)
    elif isinstance(kind, MP3):
        _write_mp3(kind, wanted, cover)
    elif isinstance(kind, MP4):
        _write_mp4(kind, wanted, cover)
    elif isinstance(kind, OggOpus | OggVorbis):
        _write_ogg(kind, wanted, cover)
    else:
        raise WriteRefused("format")
