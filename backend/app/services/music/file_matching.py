"""Which release came, and which file is which track (decisions 8 to 13). A pure function.

nexcrate knows the album from loading; tags and folder names decide only the release and the order, never whether a
download is refused (research A4). Everything comes from the database; nothing asks MusicBrainz (research A10).

**A file is clear** (decision 10, the owner's choice E3 (b)) when exactly one track of the release fits and no other
file claims the same track, by one of these ways:

* ``id``: the recording or release track id of its tags. Beats the other ways: a file with the right id and a wrong
  number in its tags is still the right file.
* ``position``: medium and position (tag, else a folder such as ``CD2``, else the name ``1-03`` or ``203``) and a
  length at most 10 s off. Without a known length of the track the name has to agree as well.
* ``name``: the title (keys of ``schreibweisen``) and a length at most 10 s off.

When position and name point at different tracks the file is not clear. The length is a check, never a reason alone
(research A5).

**The release** (decision 9): when most files name a release of the album in their tags and its track count fits the
files, that one. Otherwise the release with the most clear files, then the fewest gaps on both sides, then the target,
then the closer track count, then the lowest id.

A file whose tags name another album of the artist and that fits no track stays out as ``other_album`` (decision 12).
One audio file per medium that runs as long as the whole medium is a single file with a cue sheet (decision 13).
"""

from __future__ import annotations

import os
import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .. import schreibweisen

#: How far a file's length may be off the track's (decision 10); Lidarr's distance starts counting at 10 s as well.
TOLERANCE_MS = 10_000
#: A file this long compared to its whole medium is the medium in one piece (decision 13).
SINGLE_FILE_SHARE = 0.8
#: A medium needs this many tracks before one file for it counts as a single file with a cue sheet.
SINGLE_FILE_MIN_TRACKS = 3

FILED = "filed"
OPEN = "open"
OTHER_ALBUM = "other_album"

_FOLDER_MEDIUM = re.compile(
    r"(?<![a-z0-9])(?:cd|disc|disk|side|medium|part|pt)[ ._-]*(\d{1,2})(?![0-9])", re.IGNORECASE
)
_NAME_MEDIUM_TRACK = re.compile(r"^\s*(?:cd|disc|disk|d)?[ ._-]*(\d{1,2})[-._](\d{1,3})(?=[\s._)\-]|$)", re.IGNORECASE)
_NAME_DT = re.compile(r"^\s*d(\d{1,2})t(\d{1,3})(?![0-9])", re.IGNORECASE)
_NAME_NUMBER = re.compile(r"^\s*\(?(\d{1,3})\)?(?=[\s._)\-]|$)")
_NAME_INNER_NUMBER = re.compile(r"\s-\s(\d{1,3})\s*[-.]\s")
_LEADING_JUNK = re.compile(r"^[\s._\-()\[\]]+")
_FEAT = re.compile(r"\s*[(\[]?\s*(?:feat\.?|ft\.?|featuring)\s.*$", re.IGNORECASE)


# --- Input -------------------------------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AudioFile:
    key: int
    #: Relative to the download, with slashes.
    path: str
    duration_ms: int | None
    tags: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class Track:
    id: int
    mbid: str
    recording_mbid: str | None
    medium: int
    position: int
    name: str
    length_ms: int | None


@dataclass(frozen=True)
class Edition:
    """One release of the album with its tracks."""

    id: int
    mbid: str
    media: int
    tracks: tuple[Track, ...]


@dataclass(frozen=True)
class OtherAlbum:
    title_id: int
    mbid: str | None
    name: str


# --- Reading a file ----------------------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Reading:
    medium: int | None
    position: int | None
    #: Where the numbers came from: ``tag``, ``folder``, ``name``.
    source: str | None
    title: str | None

    def as_dict(self) -> dict[str, Any]:
        return {"medium": self.medium, "position": self.position, "source": self.source, "title": self.title}


def _number(values: list[str] | None) -> int | None:
    if not values:
        return None
    head = values[0].split("/", 1)[0].strip()
    return int(head) if head.isdigit() and 0 < int(head) < 1000 else None


def _parts(path: str) -> tuple[list[str], str]:
    parts = [part for part in path.replace("\\", "/").split("/") if part]
    return parts[:-1], os.path.splitext(parts[-1])[0] if parts else ""


def _title_from_name(stem: str, number_end: int) -> str | None:
    rest = _LEADING_JUNK.sub("", stem[number_end:])
    # "Artist - Title" after the number: the last part is the title.
    if " - " in rest:
        rest = rest.rsplit(" - ", 1)[1]
    rest = rest.strip(" ._-")
    return rest or None


def read(file: AudioFile, several_media: bool) -> Reading:
    """Medium, position and title of a file: tags first, then the folder, then the name."""
    tags = file.tags
    folders, stem = _parts(file.path)
    folder_medium: int | None = None
    for folder in reversed(folders[-2:]):
        found = _FOLDER_MEDIUM.search(folder)
        if found:
            folder_medium = int(found.group(1))
            break
    tag_title = (tags.get("title") or [None])[0]
    position = _number(tags.get("tracknumber"))
    medium = _number(tags.get("discnumber"))
    name_medium, name_position, end = _numbers_of_name(stem, several_media)
    if position is not None:
        # A tag without the medium: the folder says it, or the name when it names the same track.
        if medium is None:
            medium = folder_medium or (name_medium if name_position == position else None)
        return Reading(medium, position, "tag", tag_title or _name_title(stem))
    if name_position is None:
        return Reading(folder_medium, None, None, tag_title or stem.strip() or None)
    source = "folder" if folder_medium is not None else "name"
    title = tag_title or _title_from_name(stem, end)
    return Reading(folder_medium or name_medium, name_position, source, title)


def _name_title(stem: str) -> str | None:
    _medium, _position, end = _numbers_of_name(stem, False)
    return _title_from_name(stem, end) if end else (stem.strip() or None)


def _numbers_of_name(stem: str, several_media: bool) -> tuple[int | None, int | None, int]:
    """Medium, position and where the numbers end in a file name without its extension."""
    found = _NAME_DT.match(stem)
    if found:
        return int(found.group(1)), int(found.group(2)), found.end()
    found = _NAME_MEDIUM_TRACK.match(stem)
    if found and int(found.group(1)) <= 20:
        return int(found.group(1)), int(found.group(2)), found.end()
    found = _NAME_NUMBER.match(stem)
    if found is None:
        found = _NAME_INNER_NUMBER.search(stem)
        if found is None:
            return None, None, 0
    value = int(found.group(1))
    if several_media and value > 100 and value % 100:
        # "203": the third track of the second medium, only for a release with several media.
        return value // 100, value % 100, found.end()
    return None, value, found.end()


def title_keys(text: str | None) -> frozenset[str]:
    if not text:
        return frozenset()
    plain = _FEAT.sub("", text)
    return frozenset(schreibweisen.keys(plain)) | frozenset(schreibweisen.keys(text))


# --- One release -------------------------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Decision:
    key: int
    decision: str
    track_id: int | None
    #: ``id``, ``position`` or ``name`` for a filed file.
    via: str | None
    #: Tracks that could fit an open file, best first; the dialog proposes the first.
    proposal: tuple[int, ...]
    reading: Reading
    other_title_id: int | None = None


@dataclass(frozen=True)
class EditionResult:
    edition_id: int
    decisions: tuple[Decision, ...]
    #: Tracks of the release no file holds.
    missing: tuple[int, ...]

    @property
    def filed(self) -> int:
        return sum(1 for item in self.decisions if item.decision == FILED)

    @property
    def open(self) -> int:
        return sum(1 for item in self.decisions if item.decision == OPEN)


def _close(file: AudioFile, track: Track) -> bool | None:
    """Whether the lengths agree; None when one is unknown."""
    if file.duration_ms is None or track.length_ms is None:
        return None
    return abs(file.duration_ms - track.length_ms) <= TOLERANCE_MS


def _by_id(file: AudioFile, edition: Edition) -> set[int]:
    track_ids = {value.casefold() for value in file.tags.get("musicbrainz_releasetrackid") or []}
    recordings = {value.casefold() for value in file.tags.get("musicbrainz_trackid") or []}
    by_track = {track.id for track in edition.tracks if track.mbid.casefold() in track_ids}
    if by_track:
        return by_track
    return {track.id for track in edition.tracks if (track.recording_mbid or "").casefold() in recordings}


def _candidates(
    file: AudioFile, reading: Reading, edition: Edition, keys: frozenset[str]
) -> tuple[set[int], str | None]:
    """The tracks a file could be and the way that says so; an empty set when the ways disagree or nothing fits."""
    by_id = _by_id(file, edition)
    if by_id:
        # One recording can stand twice in a release: the position then picks among them.
        if len(by_id) > 1 and reading.position is not None:
            narrowed = {
                track.id
                for track in edition.tracks
                if track.id in by_id
                and track.position == reading.position
                and (reading.medium is None or track.medium == reading.medium)
            }
            if len(narrowed) == 1:
                return narrowed, "id"
        return by_id, "id"
    by_name = {
        track.id
        for track in edition.tracks
        if keys and keys & title_keys(track.name) and _close(file, track) is not False
    }
    by_position: set[int] = set()
    if reading.position is not None:
        for track in edition.tracks:
            # Without a medium the position may stand on any medium of the release: the length decides.
            same_medium = reading.medium is None or track.medium == reading.medium
            if not same_medium or track.position != reading.position:
                continue
            close = _close(file, track)
            if close is True or (close is None and track.id in by_name):
                by_position.add(track.id)
        if not by_position and reading.medium is None and edition.media > 1:
            # Numbers running through all media: the n-th track of the release.
            ordered = sorted(edition.tracks, key=lambda item: (item.medium, item.position))
            if 0 < reading.position <= len(ordered):
                track = ordered[reading.position - 1]
                if _close(file, track) is True:
                    by_position.add(track.id)
    if by_position and by_name:
        both = by_position & by_name
        return (both, "position") if both else (set(), None)
    if by_position:
        return by_position, "position"
    return by_name, ("name" if by_name else None)


def _proposal(
    file: AudioFile,
    reading: Reading,
    edition: Edition,
    keys: frozenset[str],
    fits: set[int],
    held: frozenset[int] = frozenset(),
) -> tuple[int, ...]:
    """Tracks for the dialog: those that fit, else a track no clear file holds before one that is held, then the same
    name or position, then the nearest length."""
    if fits:
        return tuple(sorted(fits, key=lambda track_id: (track_id in held, track_id)))

    def distance(track: Track) -> tuple[bool, int, int, int]:
        named = 0 if keys and keys & title_keys(track.name) else 1
        placed = 0 if reading.position == track.position and (reading.medium in (None, track.medium)) else 1
        length = (
            abs((file.duration_ms or 0) - (track.length_ms or 0)) if file.duration_ms and track.length_ms else 10**9
        )
        return (track.id in held, named + placed, length, track.id)

    ordered = sorted(edition.tracks, key=distance)
    return tuple(track.id for track in ordered[:3])


def _other_album(file: AudioFile, others: Sequence[OtherAlbum], own_keys: frozenset[str]) -> int | None:
    group = {value.casefold() for value in file.tags.get("musicbrainz_releasegroupid") or []}
    album_keys = title_keys((file.tags.get("album") or [None])[0])
    for other in others:
        if other.mbid and other.mbid.casefold() in group:
            return other.title_id
    if album_keys and not album_keys & own_keys:
        for other in others:
            if album_keys & title_keys(other.name):
                return other.title_id
    return None


def match_edition(
    files: Sequence[AudioFile],
    edition: Edition,
    others: Sequence[OtherAlbum] = (),
    album_name: str | None = None,
) -> EditionResult:
    """Every file against one release."""
    several = edition.media > 1
    readings = {file.key: read(file, several) for file in files}
    own_keys = title_keys(album_name)
    fits: dict[int, tuple[set[int], str | None]] = {}
    for file in files:
        reading = readings[file.key]
        keys = title_keys(reading.title)
        fits[file.key] = _candidates(file, reading, edition, keys)
    claims: Counter[int] = Counter()
    for tracks, _way in fits.values():
        if len(tracks) == 1:
            claims[next(iter(tracks))] += 1
    clear = {
        file.key: next(iter(fits[file.key][0]))
        for file in files
        if len(fits[file.key][0]) == 1 and claims[next(iter(fits[file.key][0]))] == 1
    }
    held = frozenset(clear.values())
    decisions: list[Decision] = []
    for file in files:
        reading = readings[file.key]
        tracks, way = fits[file.key]
        if file.key in clear:
            track_id = clear[file.key]
            decisions.append(Decision(file.key, FILED, track_id, way, (track_id,), reading))
            continue
        other = _other_album(file, others, own_keys) if not tracks else None
        if other is not None:
            decisions.append(Decision(file.key, OTHER_ALBUM, None, None, (), reading, other_title_id=other))
            continue
        keys = title_keys(reading.title)
        proposal = _proposal(file, reading, edition, keys, tracks, held)
        decisions.append(Decision(file.key, OPEN, None, None, proposal, reading))
    missing = tuple(
        track.id
        for track in sorted(edition.tracks, key=lambda item: (item.medium, item.position))
        if track.id not in held
    )
    return EditionResult(edition.id, tuple(decisions), missing)


# --- Choosing the release ----------------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Result:
    edition: EditionResult | None
    #: Why this release: ``{"code": "by_tags"}``, ``{"code": "by_tracks", "filed": 12, "tracks": 12}``, ``owner``.
    reason: dict[str, Any]
    #: Whether the files are single files with a cue sheet (decision 13).
    single_file: bool = False


def _named_edition(files: Sequence[AudioFile], editions: Sequence[Edition]) -> Edition | None:
    votes: Counter[str] = Counter()
    for file in files:
        for value in file.tags.get("musicbrainz_albumid") or []:
            votes[value.casefold()] += 1
    if not votes:
        return None
    mbid, count = votes.most_common(1)[0]
    if count * 2 <= len(files):
        return None
    edition = next((item for item in editions if item.mbid.casefold() == mbid), None)
    if edition is None or len(edition.tracks) != len(files):
        return None
    return edition


def single_file(files: Sequence[AudioFile], editions: Sequence[Edition], cue_sheet: bool) -> bool:
    """One audio file per medium that runs as long as its medium (decision 13); with a cue sheet, one file for a whole
    release of several tracks is enough."""
    if not files or not editions or len(files) > max(edition.media for edition in editions):
        return False
    for edition in editions:
        per_medium: dict[int, int] = defaultdict(int)
        counts: dict[int, int] = defaultdict(int)
        for track in edition.tracks:
            per_medium[track.medium] += track.length_ms or 0
            counts[track.medium] += 1
        if min(counts.values(), default=0) < SINGLE_FILE_MIN_TRACKS:
            continue
        if cue_sheet and len(files) <= edition.media:
            return True
        lengths = sorted(per_medium.values())
        durations = sorted(file.duration_ms or 0 for file in files)
        if len(durations) == len(lengths) and all(
            length and duration >= SINGLE_FILE_SHARE * length
            for duration, length in zip(durations, lengths, strict=True)
        ):
            return True
    return False


def match(
    files: Sequence[AudioFile],
    editions: Sequence[Edition],
    *,
    target_id: int | None = None,
    chosen_id: int | None = None,
    others: Sequence[OtherAlbum] = (),
    album_name: str | None = None,
    cue_sheet: bool = False,
) -> Result:
    """The release that came and the decision for every file. ``chosen_id``: the owner's release in the dialog."""
    usable = [edition for edition in editions if edition.tracks]
    if not files or not usable:
        return Result(None, {"code": "no_tracks"} if not usable else {"code": "no_files"})
    if chosen_id is not None:
        chosen = next((edition for edition in usable if edition.id == chosen_id), None)
        if chosen is not None:
            return Result(match_edition(files, chosen, others, album_name), {"code": "owner"})
    if single_file(files, usable, cue_sheet):
        return Result(None, {"code": "single_file"}, single_file=True)
    named = _named_edition(files, usable)
    if named is not None:
        return Result(match_edition(files, named, others, album_name), {"code": "by_tags"})
    results = [(edition, match_edition(files, edition, others, album_name)) for edition in usable]

    def score(item: tuple[Edition, EditionResult]) -> tuple[int, int, int, int, int]:
        edition, result = item
        return (
            result.filed,
            -(result.open + len(result.missing)),
            1 if edition.id == target_id else 0,
            -abs(len(edition.tracks) - len(files)),
            -edition.id,
        )

    edition, result = max(results, key=score)
    return Result(result, {"code": "by_tracks", "filed": result.filed, "tracks": len(edition.tracks)})
