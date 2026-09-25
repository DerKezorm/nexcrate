"""Names of filed music: Lidarr's tokens, flat album folders (decisions 14 to 17).

The renderer is the one of movies and series (``naming``): the same syntax, clean characters, umlaut rule and a limit
of 255 bytes per path part (research T14). What music adds:

* **Four patterns:** artist folder, album folder, file of an album with one medium, file of an album with several.
  There is exactly one music version, so the patterns live in the settings only, not per version.
* **Flat by default:** ``Artist/Album (Year)/1-01 Title.flac``; the medium stands in the name only when the release
  has more than one (decision 14, research T6).
* **A folder per medium, for whoever wants it** (20.09.2026): the pattern of an album with several media may hold
  one ``/``, as Lidarr's own does (``{Medium Format} {medium:00}/{track:00} {Track Title}``). What stands before it
  is the folder of the medium below the album folder and needs ``{medium}``; the file after it then needs only
  ``{track}``. The owner's library came from Lidarr with such folders (193 albums), and a newer version of an album
  landed flat beside them, the old folder left empty. ``file_name`` answers ``CD 02/03 Title.flac`` then, and
  ``track_files.relative_path`` has held a medium folder since M6.
* **Tokens** in Lidarr's names where Lidarr has them. ``{Release Year}`` is the album's first year, not the year of
  the release (decision 15), so a discography sorts by when albums came out, not by reissues.
* **Length:** the track title is shortened first, then the album title, then the artist's name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session as OrmSession

from ..db import get_setting, set_setting
from ..meldungen import meldung
from . import naming, schreibweisen
from .naming import NamingError, token_key

PATTERNS = ("artist_folder", "album_folder", "track_file", "multi_disc_file")
SETTING_KEYS = {which: f"naming_music_{which}" for which in PATTERNS}
DEFAULTS: dict[str, str] = {
    "artist_folder": "{Artist Name}",
    "album_folder": "{Album Title} ({Release Year})",
    "track_file": "{track:00} {Track Title}",
    "multi_disc_file": "{medium:0}-{track:00} {Track Title}",
}
#: Lidarr's names of the tokens nexcrate fills, in the order the interface shows them.
TOKENS: tuple[str, ...] = (
    "{Artist Name}",
    "{Artist CleanName}",
    "{Artist NameThe}",
    "{Artist NameFirstCharacter}",
    "{Artist MbId}",
    "{Album Title}",
    "{Album CleanTitle}",
    "{Album TitleThe}",
    "{Album Type}",
    "{Album Disambiguation}",
    "{Album MbId}",
    "{Release Year}",
    "{medium:0}",
    "{Medium Format}",
    "{track:00}",
    "{Track Title}",
    "{Track CleanTitle}",
    "{Track ArtistName}",
    "{Quality Title}",
    "{Original Filename}",
)
KNOWN_KEYS = frozenset(token_key(token[1:-1].split(":", 1)[0]) for token in TOKENS)
ARTIST_KEYS = frozenset(token_key(name) for name in ("Artist Name", "Artist CleanName", "Artist NameThe"))
ALBUM_KEYS = frozenset(token_key(name) for name in ("Album Title", "Album CleanTitle", "Album TitleThe"))
TRACK_TITLE_KEYS = frozenset(token_key(name) for name in ("Track Title", "Track CleanTitle"))
MEDIUM_KEY = "medium"
TRACK_KEY = "track"
ORIGINAL_KEY = token_key("Original Filename")
#: Tokens of one file: never in a folder pattern.
FILE_KEYS = frozenset(
    {
        MEDIUM_KEY,
        TRACK_KEY,
        *TRACK_TITLE_KEYS,
        token_key("Medium Format"),
        token_key("Track ArtistName"),
        token_key("Quality Title"),
        ORIGINAL_KEY,
    }
)
#: Shortened in this order when a name is too long.
TIERS = (TRACK_TITLE_KEYS | {ORIGINAL_KEY}, ALBUM_KEYS, ARTIST_KEYS)

#: What separates the folder of a medium from the file in ``multi_disc_file``; Lidarr takes either.
_SLASHES = ("/", "\\")


def split_medium_folder(pattern: str) -> tuple[str, str]:
    """The folder of the medium and the file of a pattern for several media; no folder is ``""``."""
    folder, _slash, file = pattern.replace("\\", "/").rpartition("/")
    return folder.strip(), file.strip()


_NEEDS = {
    "artist_folder": "The artist folder needs an artist token.",
    "album_folder": "The album folder needs an album title token.",
    "track_file": "A track file needs {track}, or {Original Filename}.",
    "multi_disc_file": "The file of an album with several media needs {medium} and {track}, or {Original Filename}.",
    "medium_folder": (
        "An album with several media takes one folder at most, the one of the medium, and that folder needs {medium}."
    ),
}


def check(pattern: str, which: str) -> None:
    """Raises ``NamingError`` for an empty pattern, an unknown token, a file token in a folder, or what it needs."""
    if not pattern.strip():
        raise NamingError(meldung("naming_pattern_empty", "A naming pattern must not be empty.", pattern=which))
    folder = which in ("artist_folder", "album_folder")
    if which == "multi_disc_file" and any(slash in pattern for slash in _SLASHES):
        medium_folder, file = split_medium_folder(pattern)
        if any(slash in medium_folder for slash in _SLASHES) or not medium_folder or not file:
            raise NamingError(meldung("naming_pattern_incomplete", _NEEDS["medium_folder"], pattern=which))
        if MEDIUM_KEY not in set(naming.scan(medium_folder, KNOWN_KEYS, FILE_KEYS, folder=False)):
            raise NamingError(meldung("naming_pattern_incomplete", _NEEDS["medium_folder"], pattern=which))
        file_keys = set(naming.scan(file, KNOWN_KEYS, FILE_KEYS, folder=False))
        if not (TRACK_KEY in file_keys or ORIGINAL_KEY in file_keys):
            raise NamingError(meldung("naming_pattern_incomplete", _NEEDS["track_file"], pattern=which))
        return
    keys = set(naming.scan(pattern, KNOWN_KEYS, FILE_KEYS, folder=folder))
    original = ORIGINAL_KEY in keys
    if which == "artist_folder" and not ARTIST_KEYS & keys:
        raise NamingError(meldung("naming_pattern_incomplete", _NEEDS[which], pattern=which))
    if which == "album_folder" and not ALBUM_KEYS & keys:
        raise NamingError(meldung("naming_pattern_incomplete", _NEEDS[which], pattern=which))
    if which == "track_file" and not (TRACK_KEY in keys or original):
        raise NamingError(meldung("naming_pattern_incomplete", _NEEDS[which], pattern=which))
    if which == "multi_disc_file" and not ((TRACK_KEY in keys and MEDIUM_KEY in keys) or original):
        raise NamingError(meldung("naming_pattern_incomplete", _NEEDS[which], pattern=which))


def problem_of(pattern: str, which: str) -> dict[str, Any] | None:
    try:
        check(pattern, which)
    except NamingError as exc:
        values = {key: value for key, value in exc.detail.items() if key not in ("code", "message")}
        return {"code": exc.code, "values": values}
    return None


# --- Settings ----------------------------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MusicNaming:
    artist_folder: str = DEFAULTS["artist_folder"]
    album_folder: str = DEFAULTS["album_folder"]
    track_file: str = DEFAULTS["track_file"]
    multi_disc_file: str = DEFAULTS["multi_disc_file"]
    umlauts: str = naming.DEFAULT_UMLAUTS

    def pattern(self, which: str) -> str:
        return str(getattr(self, which))


def load(db: OrmSession) -> MusicNaming:
    base = naming.load(db)
    return MusicNaming(
        **{which: get_setting(db, SETTING_KEYS[which], "") or DEFAULTS[which] for which in PATTERNS},
        umlauts=base.umlauts,
    )


def save(db: OrmSession, patterns: dict[str, str]) -> None:
    """Stage the patterns; the caller checks and commits. A default pattern is stored empty."""
    for which in PATTERNS:
        value = patterns[which]
        set_setting(db, SETTING_KEYS[which], "" if value == DEFAULTS[which] else value)


# --- Values ------------------------------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AlbumFacts:
    artist_name: str
    album_title: str
    #: The album's first year (``first_release_date``), not the release's.
    year: int | None = None
    artist_mbid: str | None = None
    album_mbid: str | None = None
    album_type: str | None = None
    disambiguation: str | None = None


@dataclass(frozen=True)
class TrackFacts:
    medium: int
    position: int
    title: str
    artist_name: str = ""
    medium_format: str | None = None
    quality: str | None = None
    original_filename: str = ""


_ARTICLE = re.compile(r"^(The|A|An|Die|Der|Das)\s(.+)$", re.IGNORECASE)


def name_the(name: str) -> str:
    """Lidarr's ``{Artist NameThe}``: a leading article at the end, "The Band" as "Band, The"."""
    found = _ARTICLE.match(name.strip())
    return f"{found.group(2)}, {found.group(1)}" if found else name


def token_values(album: AlbumFacts, track: TrackFacts | None) -> dict[str, str]:
    """Every token's raw value by lookup key. Numbers are raw; ``render`` pads them."""
    artist = schreibweisen.nfc(album.artist_name or "")
    title = schreibweisen.nfc(album.album_title or "")
    values = {
        token_key("Artist Name"): artist,
        token_key("Artist CleanName"): naming.clean_title(artist),
        token_key("Artist NameThe"): name_the(artist),
        token_key("Artist NameFirstCharacter"): naming.first_character(name_the(artist)),
        token_key("Artist MbId"): album.artist_mbid or "",
        token_key("Album Title"): title,
        token_key("Album CleanTitle"): naming.clean_title(title),
        token_key("Album TitleThe"): naming.title_the(title),
        token_key("Album Type"): album.album_type or "",
        token_key("Album Disambiguation"): schreibweisen.nfc(album.disambiguation or ""),
        token_key("Album MbId"): album.album_mbid or "",
        token_key("Release Year"): str(album.year) if album.year else "",
    }
    if track is not None:
        track_title = schreibweisen.nfc(track.title or "")
        values.update(
            {
                MEDIUM_KEY: str(track.medium),
                TRACK_KEY: str(track.position),
                token_key("Medium Format"): track.medium_format or "",
                token_key("Track Title"): track_title,
                token_key("Track CleanTitle"): naming.clean_title(track_title),
                token_key("Track ArtistName"): schreibweisen.nfc(track.artist_name or ""),
                token_key("Quality Title"): track.quality or "",
                ORIGINAL_KEY: track.original_filename,
            }
        )
    return values


def artist_folder_name(current: MusicNaming, album: AlbumFacts) -> str:
    return naming.render(
        current.artist_folder,
        token_values(album, None),
        current.umlauts,
        max_bytes=naming.MAX_PART_BYTES,
        tiers=TIERS,
    )


def album_folder_name(current: MusicNaming, album: AlbumFacts, addition: str | None = None) -> str:
    """The album folder; ``addition`` goes in brackets at the end when two albums of an artist would share the name
    (decision 15), kept within the limit by shortening the title first."""
    pattern = current.album_folder + (f" ({naming.clean(addition)})" if addition else "")
    return naming.render(
        pattern, token_values(album, None), current.umlauts, max_bytes=naming.MAX_PART_BYTES, tiers=TIERS
    )


#: Which album keeps the plain folder name when several share it (the owner's answer G2 in the design notes).
TYPE_RANK = {"album": 0, "ep": 1, "single": 2, "broadcast": 3, "other": 4}


def additions(album: AlbumFacts, title_id: int | None = None) -> list[str]:
    """What goes in brackets when an album's folder name is taken, in this order: its type unless it is an album
    (``Single``, ``EP``), its disambiguation, the start of its MusicBrainz id, its number."""
    found: list[str] = []
    kind = (album.album_type or "").strip()
    for value in (
        kind if kind and kind.casefold() != "album" else "",
        (album.disambiguation or "").strip(),
        (album.album_mbid or "")[:8],
        str(title_id) if title_id is not None else "",
        kind,
    ):
        if value and value not in found:
            found.append(value)
    return found


def free_album_folder(
    current: MusicNaming, album: AlbumFacts, taken: set[str], title_id: int | None = None
) -> str | None:
    """The album folder name, or the first one with an addition that is not in ``taken`` (compared case-folded);
    None when every one is taken."""
    for addition in [None, *additions(album, title_id)]:
        name = album_folder_name(current, album, addition)
        if name.casefold() not in taken:
            return name
    return None


def file_name(
    current: MusicNaming, album: AlbumFacts, track: TrackFacts, extension: str, *, several_media: bool
) -> str:
    """The file name with its extension, leaving room for the temporary suffix of a transfer.

    ⚠️ For an album with several media this can be ``<folder of the medium>/<file>``, always with a forward slash:
    a path below the album folder, not one path part. Whoever files it makes the folder first.
    """
    clean_extension = extension.lower() if re.fullmatch(r"\.[A-Za-z0-9-]{1,10}", extension) else ""
    pattern = current.multi_disc_file if several_media else current.track_file
    medium_folder, file_pattern = split_medium_folder(pattern) if several_media else ("", pattern)
    if medium_folder:
        values = token_values(album, track)
        folder_name = naming.render(medium_folder, values, current.umlauts, max_bytes=naming.MAX_PART_BYTES)
        file = naming.render(
            file_pattern,
            values,
            current.umlauts,
            max_bytes=naming.MAX_PART_BYTES - len(naming.PARTIAL_SUFFIX),
            suffix=clean_extension,
            tiers=TIERS,
        )
        # A folder that rendered to nothing (no format known and nothing else in it) would be the album folder.
        return f"{folder_name}/{file}" if folder_name else file
    return naming.render(
        pattern,
        token_values(album, track),
        current.umlauts,
        max_bytes=naming.MAX_PART_BYTES - len(naming.PARTIAL_SUFFIX),
        suffix=clean_extension,
        tiers=TIERS,
    )


# --- The preview ---------------------------------------------------------------------------------------------------- #

PREVIEW_ALBUM = AlbumFacts(
    artist_name="Die Beispielband", album_title="Grüße aus dem Keller", year=1997, album_type="Album"
)
PREVIEW_TRACK = TrackFacts(medium=2, position=3, title="Über den Dächern", quality="FLAC", medium_format="CD")


def preview(current: MusicNaming) -> dict[str, str]:
    return {
        "artist_folder": artist_folder_name(current, PREVIEW_ALBUM),
        "album_folder": album_folder_name(current, PREVIEW_ALBUM),
        "track_file": file_name(current, PREVIEW_ALBUM, PREVIEW_TRACK, ".flac", several_media=False),
        "multi_disc_file": file_name(current, PREVIEW_ALBUM, PREVIEW_TRACK, ".flac", several_media=True),
    }
