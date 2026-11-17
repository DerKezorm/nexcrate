"""Which release came, and which file is which track (decisions 8 to 13). A pure function.

nexcrate knows the album from loading; tags and folder names decide only the release and the order, never whether a
download is refused (research A4). Everything comes from the database; nothing asks MusicBrainz (research A10).

**A file is clear** when exactly one track of the release fits and no other
file claims the same track, by one of these ways:

* ``id``: the recording or release track id of its tags. Beats the other ways: a file with the right id and a wrong
  number in its tags is still the right file.
* ``position``: medium and position (tag, else a folder such as ``CD2``, else the name ``1-03`` or ``203``) and a
  length at most 10 s off. Without a known length of the track the name has to agree as well.
* ``name``: the title (keys of ``schreibweisen``) and a length at most 10 s off.

When position and name point at different tracks the file is not clear. The length is a check, never a reason alone
(research A5).

**A name against its position.** A file that fits a track by position and length only is not clear when its title
(tag, else name) and its file name both name another song (``names_differ``): no word of weight in common, typos
and censored words ("D*mn") forgiven. It stays open, so AcoustID is asked next (``album_import.with_fingerprints``):
a fingerprint that confirms the track files it, else the owner decides with the track proposed. Names that say nothing
("Intro", "Interlude", "Bonus Track", "[untitled]") and names in another script never differ. Found on 17.11.2026: 11
of 13 wrongly filed files of the owner's library came by position, a song of another release about as long.

**The same version.** Position and name count only when the file and the track are the same version of the song
(``versions``): a file whose title, name or album tag says "(Live)", "- Instrumental" and so on fits no track that does
not say so, and the other way round, unless the album expects it (its title or MusicBrainz type). Such a file stays
open and is never proposed for that track; only its id may still file it. Found on 16.11.2026: a live release was
loaded for a studio album and one live track went onto the studio track of its position, 10 s off at most.

**The release**: when most files name a release of the album in their tags and its track count fits the
files, that one. Otherwise the release with the most clear files, then one of the album's own version (not "Nachtfähre
Instrumentals" for the album "Nachtfähre"), then the fewest gaps on both sides, then the target,
then the closer track count, then the lowest id.

A file whose tags name another album of the artist and that fits no track stays out as ``other_album``.
One audio file per medium that runs as long as the whole medium is a single file with a cue sheet.
"""

from __future__ import annotations

import html
import os
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from .. import schreibweisen
from . import versions

#: How far a file's length may be off the track's; Lidarr's distance starts counting at 10 s as well.
TOLERANCE_MS = 10_000
#: A file this long compared to its whole medium is the medium in one piece.
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
#: A credit in brackets anywhere in a title: "(Produced By …)", "[prod. …]", "(ft. …)".
_CREDIT = re.compile(
    r"\s*[(\[]\s*(?:feat\.?|ft\.?|featuring|produced\s+by|prod\.?(?:\s+by)?)\s[^)\]]*[)\]]", re.IGNORECASE
)


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
    #: The release's name: "Nachtfähre Instrumentals" is another version than the album "Nachtfähre".
    name: str = ""


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
    stem = os.path.splitext(parts[-1])[0] if parts else ""
    # Some releases name their files with HTML entities: "i&#39;m_counting_on_you", "1&#43;1".
    return parts[:-1], html.unescape(stem) if "&" in stem else stem


def _folded(text: str) -> str:
    """Letters and digits only, lower case, without accents: how a scene name spells an artist."""
    plain = unicodedata.normalize("NFKD", text)
    return "".join(char for char in plain.casefold() if char.isalnum())


def _without_artist(rest: str, artist: str | None) -> str:
    """A scene name ``artist_name-title_name`` without its artist: the known artist when the name starts with it,
    else the part after the only hyphen of a name with underscores."""
    # "Die Kältefront" is "die_kaeltefront" in a scene name: every spelling of ``schreibweisen`` counts.
    wanted = {_folded(form) for form in schreibweisen.keys(artist)} | {_folded(artist or "")}
    wanted.discard("")
    seen = ""
    for index, char in enumerate(rest if wanted else ""):
        seen += _folded(char)
        if seen in wanted and rest[index + 1 : index + 2] == "-":
            return rest[index + 2 :]
        if not any(form.startswith(seen) for form in wanted):
            break
    if "_" in rest and rest.count("-") == 1:
        return rest.split("-", 1)[1]
    return rest


def _title_from_name(stem: str, number_end: int, artist: str | None = None) -> str | None:
    rest = _LEADING_JUNK.sub("", stem[number_end:])
    if " " not in stem:
        # A scene name: "01-artist_name-title_name".
        rest = _without_artist(rest, artist).replace("_", " ")
    # "Artist - Title" after the number: the last part is the title.
    if " - " in rest:
        rest = rest.rsplit(" - ", 1)[1]
    rest = rest.strip(" ._-")
    return rest or None


def read(file: AudioFile, several_media: bool, artist: str | None = None) -> Reading:
    """Medium, position and title of a file: tags first, then the folder, then the name. ``artist`` is cut from the
    front of a scene name."""
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
        return Reading(medium, position, "tag", tag_title or _name_title(stem, artist))
    if name_position is None:
        return Reading(folder_medium, None, None, tag_title or stem.strip() or None)
    source = "folder" if folder_medium is not None else "name"
    title = tag_title or _title_from_name(stem, end, artist)
    return Reading(folder_medium or name_medium, name_position, source, title)


def _name_title(stem: str, artist: str | None = None) -> str | None:
    _medium, _position, end = _numbers_of_name(stem, False)
    return _title_from_name(stem, end, artist) if end else (stem.strip() or None)


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
    plain = _FEAT.sub("", _CREDIT.sub("", text))
    return frozenset(schreibweisen.keys(plain)) | frozenset(schreibweisen.keys(text))


# --- A name against its position ---------------------------------------------------------------------------------- #

#: Words that name no song: a release may call its intro "Der Geburtstag" and its hidden track "[untitled]".
_GENERIC = frozenset({"intro", "outro", "interlude", "skit", "prelude", "hidden", "bonus", "untitled", "unknown"})
#: Words of a version or a credit: "Song A (One Remix)" and "Song B (Other Remix)" share no song.
_FILLER = frozenset(
    {
        "mix", "remix", "rmx", "version", "edit", "radio", "remaster", "remastered", "original", "extended", "album",
        "single", "feat", "ft", "featuring", "prod", "produced", "explicit", "clean", "dirty", "mono", "stereo", "take",
        "alternate", "alternative", "dub", "vocal", "club", "short", "long", "part", "pt", "vol", "volume", "live",
        "demo", "instrumental", "instr", "acoustic", "unplugged", "acapella", "acappella", "accapella", "karaoke",
        "track", "titel", "piste", "reprise",
    }
)  # fmt: skip
#: Short words of English, German, Spanish, French: "You" names no song; "Have I Told You Lately" and "How's the
#: World Treating You" share nothing but it.
_SMALL = frozenset(
    {
        "a", "an", "the", "and", "or", "of", "to", "in", "on", "at", "for", "from", "by", "with", "is", "are", "be",
        "i", "im", "me", "my", "you", "your", "youre", "we", "us", "our", "he", "she", "it", "its", "they", "them",
        "this", "that", "thats", "not", "no", "dont", "do", "what", "when", "where", "who", "how", "all", "so", "but",
        "if", "as", "up", "out", "just", "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "und",
        "oder", "zu", "am", "auf", "aus", "mit", "von", "fur", "fuer", "ist", "bin", "ich", "du", "er", "sie", "es",
        "wir", "ihr", "mich", "dich", "mir", "dir", "nicht", "wie", "was", "el", "la", "los", "las", "un", "una", "y",
        "de", "del", "en", "con", "por", "para", "que", "mi", "tu", "yo", "te", "se", "lo", "le", "les", "et", "dans",
        "avec", "pour", "qui", "je", "il", "elle", "mon", "ma", "ne", "pas",
    }
)  # fmt: skip
_APOSTROPHES = re.compile(r"['’‘`´]")
_ALNUM = re.compile(r"[a-z0-9]+")
#: A word with stars is censored: "D*mn", "D**n".
_TOKEN = re.compile(r"[a-z0-9*]+")
_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
#: Two words this alike are one word written twice ("Moleasses", "Buddah", "chilln"): a typo names no other song.
_ALIKE = 0.8
_ALIKE_LONG = 0.75
_LONG_WORD = 6


def _forms(text: str) -> tuple[str, str]:
    """A name in lower case twice: umlauts written out ("duesseldorfer") and accents dropped ("dusseldorfer")."""
    low = _APOSTROPHES.sub("", unicodedata.normalize("NFC", text).casefold())

    def ascii_of(value: str) -> str:
        plain = unicodedata.normalize("NFKD", value)
        return "".join(char for char in plain if not unicodedata.combining(char))

    return ascii_of(low.translate(_UMLAUTS)), ascii_of(low)


@dataclass(frozen=True)
class _Words:
    #: Words that can tell songs apart.
    weight: frozenset[str]
    #: All words, small ones too.
    every: frozenset[str]
    #: Censored words as patterns.
    masks: tuple[re.Pattern[str], ...]


def _words(text: str, artist: frozenset[str]) -> _Words | None:
    """The words of a name without credits, versions and the artist; None when it has no Latin letters to compare
    (another script, only numbers)."""
    plain = _FEAT.sub("", _CREDIT.sub(" ", text))
    words: set[str] = set()
    masks: list[re.Pattern[str]] = []
    for form in _forms(plain):
        for token in _TOKEN.findall(form):
            if "*" in token and token.strip("*"):
                masks.append(re.compile("[a-z0-9]+".join(re.escape(part) for part in token.split("*")) + "$"))
                words.add(token)
            elif len(token) > 1 and not token.isdigit() and token not in _FILLER and token not in artist:
                words.add(token)
    if not words:
        return None
    return _Words(frozenset(words - _SMALL), frozenset(words), tuple(masks))


def _alike(one: str, other: str, masks: Sequence[re.Pattern[str]]) -> bool:
    if one == other or any(mask.match(other) for mask in masks):
        return True
    short, long = sorted((one, other), key=len)
    if len(short) >= 3 and long.startswith(short):
        return True
    if len(short) < 4:
        return False
    ratio = SequenceMatcher(None, one, other).ratio()
    return ratio >= _ALIKE or (len(short) >= _LONG_WORD and ratio >= _ALIKE_LONG)


def _shared(mine: _Words, theirs: _Words) -> bool:
    # Words of weight first; a name made of small words only ("Do It", "You & Me") compares all of them.
    left, right = (mine.weight, theirs.weight) if mine.weight and theirs.weight else (mine.every, theirs.every)
    return any(_alike(one, other, mine.masks) or _alike(other, one, theirs.masks) for one in left for other in right)


def _generic(text: str) -> bool:
    return any(word in _GENERIC for form in _forms(text) for word in _ALNUM.findall(form))


def _one_differs(name: str, track: str, artist: frozenset[str]) -> bool | None:
    """Whether a name names another song than a track; None when it cannot tell."""
    if _generic(name) or _generic(track):
        return False
    mine, theirs = _words(name, artist), _words(track, artist)
    if mine is None or theirs is None:
        return None
    return not _shared(mine, theirs)


def names_differ(names: Sequence[str | None], track: str, artist: str | None = None) -> bool:
    """Whether every name a file gives (its title, its file name) clearly names another song than ``track``. A name
    that matches, says nothing or cannot be compared keeps the file on its position."""
    artist_words = frozenset(word for form in _forms(artist or "") for word in _ALNUM.findall(form))
    verdicts = [_one_differs(name, track, artist_words) for name in dict.fromkeys(name for name in names if name)]
    return bool(verdicts) and all(verdict is True for verdict in verdicts)


def given_names(file: AudioFile, reading: Reading, artist: str | None = None) -> tuple[str | None, ...]:
    """The names a file gives for its song: the title it was read with and the title in its file name."""
    _folders, stem = _parts(file.path)
    return reading.title, _name_title(stem, artist)


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


def file_versions(file: AudioFile, reading: Reading) -> frozenset[str]:
    """The versions a file says: in its title, its name, and its album tag."""
    _folders, stem = _parts(file.path)
    album = (file.tags.get("album") or [None])[0]
    return versions.marks(reading.title) | versions.marks(stem) | versions.marks(album)


def _same_version(track: Track, marks: frozenset[str], allowed: frozenset[str]) -> bool:
    return not versions.differs(marks, versions.marks(track.name), allowed)


def _candidates(
    file: AudioFile,
    reading: Reading,
    edition: Edition,
    keys: frozenset[str],
    marks: frozenset[str] = frozenset(),
    allowed: frozenset[str] = frozenset(),
    artist: str | None = None,
) -> tuple[set[int], str | None]:
    """The tracks a file could be and the way that says so; an empty set when the ways disagree or nothing fits.
    Position and name count only for a track of the same version (``marks`` of the file, ``allowed`` by the album).
    Position alone counts only for a track the file's names do not contradict (``names_differ``)."""
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
    tracks = [track for track in edition.tracks if _same_version(track, marks, allowed)]
    by_name = {
        track.id for track in tracks if keys and keys & title_keys(track.name) and _close(file, track) is not False
    }
    by_position: set[int] = set()
    if reading.position is not None:
        for track in tracks:
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
                if _close(file, track) is True and _same_version(track, marks, allowed):
                    by_position.add(track.id)
    if by_position and by_name:
        both = by_position & by_name
        return (both, "position") if both else (set(), None)
    if by_position:
        names = given_names(file, reading, artist)
        named = {track.id: track.name for track in edition.tracks}
        by_position = {track_id for track_id in by_position if not names_differ(names, named[track_id], artist)}
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
    marks: frozenset[str] = frozenset(),
    allowed: frozenset[str] = frozenset(),
) -> tuple[int, ...]:
    """Tracks for the dialog: those that fit, else a track no clear file holds before one that is held, then the same
    name or position, then the nearest length. Never a track of another version: a live file proposes no studio
    track."""
    if fits:
        return tuple(sorted(fits, key=lambda track_id: (track_id in held, track_id)))

    def distance(track: Track) -> tuple[bool, int, int, int]:
        named = 0 if keys and keys & title_keys(track.name) else 1
        placed = 0 if reading.position == track.position and (reading.medium in (None, track.medium)) else 1
        length = (
            abs((file.duration_ms or 0) - (track.length_ms or 0)) if file.duration_ms and track.length_ms else 10**9
        )
        return (track.id in held, named + placed, length, track.id)

    ordered = sorted((track for track in edition.tracks if _same_version(track, marks, allowed)), key=distance)
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
    artist_name: str | None = None,
    album_types: Sequence[str] = (),
) -> EditionResult:
    """Every file against one release. ``album_types``: the album's MusicBrainz secondary types."""
    several = edition.media > 1
    readings = {file.key: read(file, several, artist_name) for file in files}
    own_keys = title_keys(album_name)
    allowed = versions.expected(album_name, album_types)
    marks = {file.key: file_versions(file, readings[file.key]) for file in files}
    fits: dict[int, tuple[set[int], str | None]] = {}
    for file in files:
        reading = readings[file.key]
        keys = title_keys(reading.title)
        fits[file.key] = _candidates(file, reading, edition, keys, marks[file.key], allowed, artist_name)
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
        proposal = _proposal(file, reading, edition, keys, tracks, held, marks[file.key], allowed)
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
    #: Whether the files are single files with a cue sheet.
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
    """One audio file per medium that runs as long as its medium; with a cue sheet, one file for a whole
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
    artist_name: str | None = None,
    cue_sheet: bool = False,
    album_types: Sequence[str] = (),
) -> Result:
    """The release that came and the decision for every file. ``chosen_id``: the owner's release in the dialog;
    ``album_types``: the album's MusicBrainz secondary types (a live album expects live tracks)."""

    def match_one(edition: Edition) -> EditionResult:
        return match_edition(files, edition, others, album_name, artist_name, album_types)

    usable = [edition for edition in editions if edition.tracks]
    if not files or not usable:
        return Result(None, {"code": "no_tracks"} if not usable else {"code": "no_files"})
    if chosen_id is not None:
        chosen = next((edition for edition in usable if edition.id == chosen_id), None)
        if chosen is not None:
            return Result(match_one(chosen), {"code": "owner"})
    if single_file(files, usable, cue_sheet):
        return Result(None, {"code": "single_file"}, single_file=True)
    named = _named_edition(files, usable)
    if named is not None:
        return Result(match_one(named), {"code": "by_tags"})
    results = [(edition, match_one(edition)) for edition in usable]
    allowed = versions.expected(album_name, album_types)

    def score(item: tuple[Edition, EditionResult]) -> tuple[int, int, int, int, int, int]:
        edition, result = item
        return (
            result.filed,
            # A release of another version ("Nachtfähre Instrumentals") only when it fits more files.
            0 if versions.of_release(edition.name, album_name) - allowed else 1,
            -(result.open + len(result.missing)),
            1 if edition.id == target_id else 0,
            -abs(len(edition.tracks) - len(files)),
            -edition.id,
        )

    edition, result = max(results, key=score)
    return Result(result, {"code": "by_tracks", "filed": result.filed, "tracks": len(edition.tracks)})
