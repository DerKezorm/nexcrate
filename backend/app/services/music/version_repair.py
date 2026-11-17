"""One run over the music library for albums that lie on another version than their own (16.11.2026).

Found on the owner's library: an instrumental release chosen as the target ("most tracks"), the vocal files of a
download filed onto the tracks of the album's instrumental release ("<album> Instrumentals", same lengths), a
live release taken for a studio album. ``versions`` and the fixes in ``target``, ``file_matching`` and
``hit_matching`` keep it from happening again; this run settles what lies there already. Asked for by the owner
(16.11.2026: "nichts endgültig löschen, Unpassendes in den Papierkorb"), started with a request file
(``REQUEST_FILE`` in the data folder, ``preview`` or ``apply``) and reported into ``logs/``.

Per album version of nexcrate's own:

* **Target of another version.** A target the rule chose goes to the release the files lie on when that one is of
  the album's own version, else to the rule's choice.
* **A file on a track of another version** ("Song (instrumental)") whose download said nothing of it (neither its
  name in the download nor the release name), when that track's whole release is another version or the release
  has the song plainly as well: it goes to the track of the album's own version with the same name
  without the qualifier, in the release that takes every file of the album. That release becomes the files' release
  (and the target unless the owner chose one). Tags are written again and the album is renamed. Without such a
  release nothing changes and the album is reported.
* **A file of another version than its track** (its name in the download says "(Live)", or the download was the live
  release of the album, as the search now refuses): it goes to the track of its own version when one is free, else it
  is reported. Nothing goes into the recycle bin by itself: a live download can lie rightly on the live disc of a
  deluxe edition whose tracks MusicBrainz names plainly (measured on a copy of the owner's library, 16.11.2026).
* **A "+" that was a "/"** (``naming_music.slashes_as_dashes``): the album is renamed when every renaming step only
  turns a "+" into a "-"; so is an artist whose name holds a "/", with all its albums, after the albums.
* **The owner's target of another version** (his word, 17.11.2026): goes to the album's own release as well and stays
  his; the files go along only when that release has every file's very track (``_onto_own_release``).

Files without a download of nexcrate (a Lidarr takeover, a folder from disk) have nothing that says what they are:
they are reported, never changed.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ...config import get_settings
from ...db import SessionLocal
from ...models import (
    Artist,
    Download,
    DownloadAudioFile,
    Release,
    ReleaseMedium,
    ReleaseTrack,
    Title,
    TrackFile,
    Version,
    VersionDefinition,
    utcnow,
)
from .. import logs
from ..releases.music_parser import parse_album
from . import file_matching, hit_matching, store, tag_writing, target, versions

logger = logging.getLogger("nexcrate.music")

REQUEST_FILE = "music-version-repair.request"
JOB_NAME = "music_version_repair"
INTERVAL_SECONDS = 60
#: How far a file's length may be off its new track.
TOLERANCE_MS = file_matching.TOLERANCE_MS

_running = threading.Lock()


# --- What one album needs ------------------------------------------------------------------------------------------- #


@dataclass
class FileMove:
    track_file_id: int
    file: str
    from_track: str
    to_track: str
    to_track_id: int


@dataclass
class FileOut:
    track_file_id: int
    file: str
    track: str
    why: str


@dataclass
class Finding:
    title_id: int
    version_id: int
    artist: str
    album: str
    #: ``{"from": …, "to": …, "to_id": …}`` when the target changes.
    target: dict[str, Any] | None = None
    #: The release the files lie on afterwards, when it changes.
    release: dict[str, Any] | None = None
    moves: list[FileMove] = field(default_factory=list)
    recycle: list[FileOut] = field(default_factory=list)
    #: Renamed for a "+" that was a "/".
    slashes: bool = False
    #: What stays for the owner, in words of the report: ``owner_target``, ``no_release_takes_the_files``, …
    notes: list[str] = field(default_factory=list)

    @property
    def changes(self) -> bool:
        return bool(self.target or self.release or self.moves or self.recycle or self.slashes)


@dataclass
class _Track:
    id: int
    release_id: int
    medium: int
    position: int
    name: str
    length_ms: int | None
    recording_mbid: str | None


def _keys(name: str) -> frozenset[str]:
    return file_matching.title_keys(versions.without_qualifiers(name))


def _stem(path: str) -> str:
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[0] if "." in name else name


def _close(duration_ms: int | None, track: _Track) -> bool:
    return duration_ms is None or track.length_ms is None or abs(duration_ms - track.length_ms) <= TOLERANCE_MS


class _Album:
    """Everything one album version needs, read once."""

    def __init__(self, db: OrmSession, version: Version, title: Title, artist: Artist | None) -> None:
        self.version, self.title = version, title
        self.artist = artist.name if artist is not None else ""
        self.expected = versions.expected(title.title, title.secondary_types)
        self.releases = {
            row.id: row
            for row in db.scalars(select(Release).where(Release.title_id == title.id, Release.tracks_loaded.is_(True)))
        }
        media = {
            row.id: row.position
            for row in db.scalars(select(ReleaseMedium).where(ReleaseMedium.release_id.in_(self.releases)))
        }
        self.tracks: dict[int, _Track] = {}
        for row in db.scalars(select(ReleaseTrack).where(ReleaseTrack.release_id.in_(self.releases))):
            self.tracks[row.id] = _Track(
                row.id, row.release_id, media.get(row.medium_id, 1), row.position, row.name, row.length_ms,
                row.recording_mbid,
            )  # fmt: skip
        self.files = list(db.scalars(select(TrackFile).where(TrackFile.version_id == version.id)))
        self.origins: dict[int, tuple[DownloadAudioFile, str]] = {}
        if self.files:
            rows = db.execute(
                select(DownloadAudioFile, Download.release_title)
                .join(Download, Download.id == DownloadAudioFile.download_id)
                .where(DownloadAudioFile.track_file_id.in_([row.id for row in self.files]))
                .order_by(DownloadAudioFile.id)
            ).tuples()
            for row, release_title in rows:
                self.origins[row.track_file_id] = (row, release_title or "")

    def other(self, release_id: int | None) -> frozenset[str]:
        """The versions a release's name says beyond the album and its artist."""
        release = self.releases.get(release_id or 0)
        if release is None:
            return frozenset()
        return versions.of_release(release.name, f"{self.artist} {self.title.title}") - self.expected

    def another_release_or_twin(self, track: _Track) -> bool:
        """Whether a track of another version is surely not what a plain file is: its whole release is another
        version ("… Instrumentals"), or the same release has the song plainly too (bonus instrumentals beside the
        album). A "Song (live)" of a partly live album whose download named it plainly is the same recording."""
        if self.other(track.release_id):
            return True
        keys = _keys(track.name)
        return any(
            other.release_id == track.release_id
            and other.id != track.id
            and not (versions.marks(other.name) - self.expected)
            and keys & file_matching.title_keys(other.name)
            for other in self.tracks.values()
        )

    def track_versions(self, track: _Track) -> frozenset[str]:
        return (versions.marks(track.name) | self.other(track.release_id)) - self.expected

    def file_says(self, row: TrackFile) -> tuple[frozenset[str], frozenset[str], str | None] | None:
        """What the file's download said of its version: the file itself (its name there, its title as read), the
        release's name, and the version the search now refuses that release for (``hit_matching``). None without a
        download of nexcrate."""
        origin = self.origins.get(row.id)
        if origin is None:
            return None
        audio_row, release_title = origin
        reading = (audio_row.reading or {}).get("title") if isinstance(audio_row.reading, dict) else None
        own = (versions.marks(_stem(audio_row.path)) | versions.marks(reading)) - self.expected
        release = versions.of_release(release_title, f"{self.artist} {self.title.title}") - self.expected
        refused = None
        if release_title and self.artist:
            album = hit_matching.AlbumRef(
                self.title.id, self.title.title or "", self.title.year, self.title.primary_type,
                tuple(self.title.secondary_types or ()),
            )  # fmt: skip
            hit = hit_matching.match(
                parse_album(release_title), hit_matching.ArtistRef((self.artist,)), (album,), self.title.id
            )
            if hit.reason == "other_version" and hit.kind_differs in hit_matching.VERSION_OF_KIND:
                refused = hit_matching.VERSION_OF_KIND[hit.kind_differs]
        return frozenset(own), frozenset(release), refused

    def duration(self, row: TrackFile) -> int | None:
        value = (row.audio or {}).get("duration_ms") if isinstance(row.audio, dict) else None
        if value is None and row.id in self.origins:
            audio = self.origins[row.id][0].audio
            value = audio.get("duration_ms") if isinstance(audio, dict) else None
        return int(value) if isinstance(value, int | float) else None

    def name_of(self, row: TrackFile) -> str:
        return row.relative_path.replace("\\", "/").rsplit("/", 1)[-1]


# --- Looking -------------------------------------------------------------------------------------------------------- #


def _target(album: _Album, finding: Finding, db: OrmSession) -> None:
    version = album.version
    current = version.target_release_id
    if current is None or not album.other(current):
        return
    owner = version.target_set_by == "owner"
    actual = version.actual_release_id
    if actual is not None and actual in album.releases and not album.other(actual):
        chosen: int | None = actual
    else:
        definition = db.get(VersionDefinition, version.version_definition_id)
        countries = store.countries_of(db, definition, store.account_language(db))
        choice = target.choose(store.candidates_of(db, album.title.id), countries)
        chosen = choice.chosen.id if choice.chosen is not None and not album.other(choice.chosen.id) else None
    if chosen is None and owner:
        # The rule takes no release (none official, say): the album's own release closest in size to the files.
        own = sorted(
            (release_id for release_id in album.releases if not album.other(release_id)),
            key=lambda release_id: (abs((album.releases[release_id].track_count or 0) - len(album.files)), release_id),
        )
        chosen = own[0] if own else None
    if chosen is None:
        finding.notes.append("no_release_of_the_album")
        return
    if owner:
        # The owner's word (17.11.2026): his targets of another version go to the album's own release too, and stay
        # his. Found on his library: remix singles and instrumental releases, with files that are what their
        # names say; a file goes along only when that release has its very track, else every file stays.
        finding.notes.append("owner_target_changed")
        onto = _onto_own_release(album, chosen)
        if onto is None:
            if album.files:
                finding.notes.append("files_stay_on_another_version")
        else:
            chosen, mapping = onto
            for row in album.files:
                if row.id in mapping:
                    old, new = album.tracks[row.track_id or 0], album.tracks[mapping[row.id]]
                    finding.moves.append(FileMove(row.id, album.name_of(row), old.name, new.name, new.id))
            if chosen != actual:
                finding.release = {
                    "from": album.releases[actual].name if actual in album.releases else None,
                    "to": album.releases[chosen].name,
                    "to_id": chosen,
                }
    finding.target = {
        "from": album.releases[current].name,
        "to": album.releases[chosen].name,
        "to_id": chosen,
        "owner": owner,
    }


def _onto_own_release(album: _Album, first: int) -> tuple[int, dict[int, int]] | None:
    """A release of the album's own version that has every file's very track: the same name with the same version
    words ("Song (Thin White Duke remix)" only on that remix) and a fitting length. The release closest in size to
    the files first (eleven files of an album take its edition of eleven, not the deluxe of sixteen), ``first`` among
    equals. None when there is no such release."""
    placed = [row for row in album.files if row.track_id is not None or row.track_ids]
    if not placed:
        return None
    own = [release_id for release_id in album.releases if not album.other(release_id)]
    own.sort(
        key=lambda release_id: (
            abs((album.releases[release_id].track_count or 0) - len(placed)),
            release_id != first,
            release_id,
        )
    )
    for release_id in own:
        tracks = [track for track in album.tracks.values() if track.release_id == release_id]
        mapping: dict[int, int] = {}
        taken: set[int] = set()
        for row in placed:
            current = album.tracks.get(row.track_id or 0)
            if current is None:
                break
            words = versions.marks(current.name) - album.expected
            found = [
                track
                for track in tracks
                if track.id not in taken
                and file_matching.title_keys(track.name) & file_matching.title_keys(current.name)
                and versions.marks(track.name) - album.expected == words
                and _close(album.duration(row), track)
            ]
            if len(found) != 1:
                break
            taken.add(found[0].id)
            if found[0].id != row.track_id:
                mapping[row.id] = found[0].id
        else:
            return release_id, mapping
    return None


def _release_for(album: _Album, wrong: set[int]) -> tuple[int, dict[int, int]] | None:
    """The release of the album's own version that takes every file, and per file of ``wrong`` its new track."""
    version = album.version
    order = [version.target_release_id, version.actual_release_id, *sorted(album.releases)]
    seen: set[int] = set()
    for release_id in order:
        if release_id is None or release_id in seen or release_id not in album.releases or album.other(release_id):
            continue
        seen.add(release_id)
        own = [track for track in album.tracks.values() if track.release_id == release_id]
        plain = [track for track in own if not (versions.marks(track.name) - album.expected)]
        mapping: dict[int, int] = {}
        taken: set[int] = set()
        fits = True
        for row in album.files:
            current = album.tracks.get(row.track_id or 0)
            if current is None:
                if row.track_id is None and not row.track_ids:
                    continue  # a loose file stays loose
                fits = False
                break
            if row.id not in wrong and current.release_id == release_id:
                target_id: int | None = current.id
            else:
                keys = _keys(current.name)
                pool = plain if row.id in wrong else own
                found = [
                    track
                    for track in pool
                    if (keys & file_matching.title_keys(track.name) or (
                        row.id not in wrong and track.recording_mbid and track.recording_mbid == current.recording_mbid
                    ))
                    and _close(album.duration(row), track)
                ]  # fmt: skip
                target_id = found[0].id if len(found) == 1 else None
            if target_id is None or target_id in taken:
                fits = False
                break
            taken.add(target_id)
            if target_id != row.track_id:
                mapping[row.id] = target_id
        if fits:
            return release_id, mapping
    return None


def _files(album: _Album, finding: Finding) -> None:
    if finding.moves:
        return  # the owner's target took the files along already
    wrong_track: set[int] = set()
    for row in album.files:
        track = album.tracks.get(row.track_id or 0)
        if track is None:
            continue
        told = album.file_says(row)
        own = album.track_versions(track)
        if told is None:
            if own:
                finding.notes.append("no_download_says_what_it_is")
            continue
        file_level, release_level, refused = told
        # What makes the file another version than its track: its own name, or a release the search now refuses.
        # A word in the release's name alone ("Album + Instrumentals") does not: such a release holds both.
        said = file_level | ({refused} if refused else set())
        if own and not own & (file_level | release_level | said) and album.another_release_or_twin(track):
            wrong_track.add(row.id)
        elif said - own:
            # The file is another version than its track: a free track of its own version, else the bin.
            keys = _keys(track.name)
            free = [
                other
                for other in album.tracks.values()
                if other.release_id == track.release_id
                and other.id not in {item.track_id for item in album.files}
                and keys & _keys(other.name)
                and (versions.marks(other.name) - album.expected) == said - album.expected
                and _close(album.duration(row), other)
            ]
            if len(free) == 1:
                finding.moves.append(FileMove(row.id, album.name_of(row), track.name, free[0].name, free[0].id))
            elif refused and refused not in own:
                # The whole download was the live (demo, mixtape) release of the album. Reported, never thrown away:
                # on the owner's library such a download lay rightly on the live disc of a "Platin Edition", whose
                # tracks MusicBrainz names without "(live)".
                finding.notes.append("download_was_another_version")
            else:
                # A bonus track "Song (Live)" that MusicBrainz names plainly: reported, never thrown away.
                finding.notes.append("file_says_another_version")
    if not wrong_track:
        return
    found = _release_for(album, wrong_track)
    if found is None:
        finding.notes.append("no_release_takes_the_files")
        return
    release_id, mapping = found
    for row in album.files:
        if row.id in mapping:
            old, new = album.tracks[row.track_id or 0], album.tracks[mapping[row.id]]
            finding.moves.append(FileMove(row.id, album.name_of(row), old.name, new.name, new.id))
    if release_id != album.version.actual_release_id:
        finding.release = {
            "from": album.releases[album.version.actual_release_id].name
            if album.version.actual_release_id in album.releases
            else None,
            "to": album.releases[release_id].name,
            "to_id": release_id,
        }
        if album.version.target_set_by != "owner" and album.version.target_release_id != release_id:
            finding.target = {
                "from": album.releases[album.version.target_release_id].name
                if album.version.target_release_id in album.releases
                else None,
                "to": album.releases[release_id].name,
                "to_id": release_id,
            }


def _only_slashes(moves: list[tuple[Path, Path]]) -> bool:
    """Whether every renaming step only turns a "+" into a "-" (" + " into " - "); a real "+" beside it stays."""
    for old, new in moves:
        before, after = old.as_posix(), new.as_posix()
        if len(before) != len(after):
            return False
        differing = [(one, other) for one, other in zip(before, after, strict=True) if one != other]
        if not differing or any(pair != ("+", "-") for pair in differing):
            return False
    return bool(moves)


def _slashes(album: _Album, finding: Finding) -> None:
    names = [album.title.title or ""] + [
        track.name for track in album.tracks.values() if track.id in {row.track_id for row in album.files}
    ]
    if not any("/" in name for name in names) or finding.moves:
        return
    from ..rename import jobs as rename_jobs

    plan = rename_jobs.plan_unit("music", album.title.id, album=True)
    if plan is None or plan.skip is not None or not plan.changed:
        return
    moves = [(move.old, move.new) for move in plan.moves]
    if _only_slashes(moves):
        finding.slashes = True
    else:
        finding.notes.append("slash_rename_has_other_steps")


@dataclass
class ArtistFinding:
    """An artist folder whose "+" was a "/" (the owner's word, 17.11.2026: the same rule as for albums)."""

    artist_id: int
    artist: str
    folder: str
    slashes: bool = False
    notes: list[str] = field(default_factory=list)


def survey_artists() -> list[ArtistFinding]:
    """Every artist with a "/" in the name whose folder is renamed when every step only turns a "+" into a "-"; the
    whole artist is planned, so its albums go along. Reads only."""
    from ..rename import jobs as rename_jobs

    with SessionLocal() as db:
        artists = [
            (row.id, row.name or "", row.folder or "")
            for row in db.scalars(select(Artist).where(Artist.name.contains("/")).order_by(Artist.id))
        ]
    found: list[ArtistFinding] = []
    for artist_id, name, folder in artists:
        try:
            plan = rename_jobs.plan_unit("music", artist_id)
        except Exception:
            logger.exception("Version repair: artist %d could not be planned", artist_id)
            continue
        if plan is None:
            continue  # no album of its own
        finding = ArtistFinding(artist_id, name, folder)
        if plan.skip is not None:
            if "+" in folder:
                finding.notes.append(f"rename_skipped_{plan.skip}")
                found.append(finding)
            continue
        if not any(old != new for old, new in plan.folders if "/" not in old):
            continue  # the artist folder keeps its name (shared with another artist, or named otherwise)
        if _only_slashes([(move.old, move.new) for move in plan.moves]):
            finding.slashes = True
        else:
            finding.notes.append("slash_rename_has_other_steps")
        found.append(finding)
    return found


def survey() -> list[Finding]:
    """Every album version of nexcrate's own that needs something, with what; reads only."""
    found: list[Finding] = []
    with SessionLocal() as db:
        rows = db.execute(
            select(Version, Title)
            .join(Title, Title.id == Version.title_id)
            .where(Title.kind == "album", Version.source_id.is_(None))
            .order_by(Title.id)
        ).tuples()
        for version, title in rows.all():
            artist = db.get(Artist, title.artist_id) if title.artist_id is not None else None
            try:
                album = _Album(db, version, title, artist)
                finding = Finding(title.id, version.id, album.artist, title.title or "")
                _target(album, finding, db)
                _files(album, finding)
                if version.relative_path:
                    _slashes(album, finding)
            except Exception:
                logger.exception("Version repair: album %d could not be looked at", title.id)
                continue
            if finding.changes or finding.notes:
                found.append(finding)
    return found


# --- Changing ------------------------------------------------------------------------------------------------------- #


def _apply_one(finding: Finding) -> dict[str, Any]:
    """Change one album; returns what happened. The caller renames and tells the media servers."""
    from .. import recycle_bin

    outcome: dict[str, Any] = {"title_id": finding.title_id, "state": "done"}
    moment = utcnow()
    with SessionLocal() as db:
        version = db.get(Version, finding.version_id)
        title = db.get(Title, finding.title_id)
        if version is None or title is None:
            return {**outcome, "state": "gone"}
        for move in finding.moves:
            row = db.get(TrackFile, move.track_file_id)
            if row is None or row.version_id != version.id:
                continue
            row.track_id = move.to_track_id
            if row.track_ids:
                row.track_ids = [move.to_track_id]
            row.updated_at = moment
            audio_row = db.scalar(
                select(DownloadAudioFile)
                .where(DownloadAudioFile.track_file_id == row.id)
                .order_by(DownloadAudioFile.id.desc())
            )
            if audio_row is not None:
                audio_row.track_id, audio_row.via = move.to_track_id, "owner"
        if finding.release is not None:
            version.actual_release_id = finding.release["to_id"]
        owner = bool(finding.target and finding.target.get("owner"))
        if finding.target is not None and (owner or version.target_set_by != "owner"):
            old = db.get(Release, version.target_release_id) if version.target_release_id else None
            new = db.get(Release, finding.target["to_id"])
            version.target_release_id = finding.target["to_id"]
            if owner:
                version.target_set_by = "owner"
                version.target_reason = {"codes": [{"code": "owner"}]}
            else:
                version.target_set_by = "rule"
                version.target_reason = {"codes": [{"code": "files"}, {"code": "other_version", "left_out": 1}]}
            version.target_suggestion_id = None
            store._history(
                db, title.id, version.id, db.get(VersionDefinition, version.version_definition_id),
                "target_changed", moment, f"{store._release_text(old)} > {store._release_text(new)}",
            )  # fmt: skip
        db.flush()
        recycle_bin.recount_album(db, version)
        version.updated_at = moment
        db.commit()
        if finding.recycle:
            scope = recycle_bin.Scope(track_file_ids=tuple(item.track_file_id for item in finding.recycle))
            result = recycle_bin.delete_in(db, title, scope, recycle_bin.Actor.key("version repair"), moment)
            db.commit()
            recycle_bin.settle(result)
            outcome["recycled"] = sum(item.files for item in result.versions)
        write_tags = tag_writing.write_enabled(db)
    if finding.moves and write_tags:
        from . import retag

        try:
            outcome["tags_written"] = retag.write(finding.title_id).get("written", 0)
        except Exception as exc:  # noqa: BLE001 - one album never stops the run
            outcome["tags"] = type(exc).__name__
    return outcome


def apply(findings: list[Finding]) -> dict[str, Any]:
    from .. import companions_album
    from ..rename import jobs as rename_jobs

    results: list[dict[str, Any]] = []
    rename_ids: list[int] = []
    for finding in findings:
        if not finding.changes:
            continue
        try:
            results.append(_apply_one(finding))
        except Exception as exc:
            logger.exception("Version repair: album %d could not be changed", finding.title_id)
            results.append({"title_id": finding.title_id, "state": "failed", "error": type(exc).__name__})
            continue
        if finding.moves or finding.slashes:
            rename_ids.append(finding.title_id)
    renamed: dict[str, Any] = {}
    if rename_ids:
        assert rename_jobs.wait_idle(600), "a rename job still runs"
        job = rename_jobs.start_run("music", rename_ids, album=True)
        rename_jobs.wait_idle(3600)
        renamed = dict(job.result)
    for finding in findings:
        if finding.changes:
            companions_album.write_version(finding.version_id)
    # Albums whose tags changed but whose files were not renamed: the media servers hear of the folder.
    told = [finding for finding in findings if finding.moves and finding.title_id not in rename_ids]
    if told:
        from ..mediaservers import notify as mediaserver_notify
        from . import album_read

        folders = []
        with SessionLocal() as db:
            for finding in told:
                version = db.get(Version, finding.version_id)
                folder = album_read.album_folder(version) if version is not None else None
                if folder is not None:
                    folders.append(folder)
        mediaserver_notify.request("music", folders)
    return {"albums": results, "renamed": renamed}


def apply_artists(artists: list[ArtistFinding]) -> dict[str, Any]:
    """Renames the artists whose folder only turns a "+" into a "-"; after the albums, planned again then."""
    from ..rename import jobs as rename_jobs

    ids = [item.artist_id for item in artists if item.slashes]
    if not ids:
        return {}
    assert rename_jobs.wait_idle(600), "a rename job still runs"
    job = rename_jobs.start_run("music", ids)
    rename_jobs.wait_idle(3600)
    return dict(job.result)


# --- The request file ----------------------------------------------------------------------------------------------- #


def report(
    findings: list[Finding],
    artists: list[ArtistFinding],
    mode: str,
    applied: dict[str, Any] | None,
    moment: datetime,
) -> dict[str, Any]:
    return {
        "mode": mode,
        "at": moment.isoformat(),
        "albums": len(findings),
        "changing": sum(1 for item in findings if item.changes),
        "findings": [asdict(item) for item in findings],
        "artists": [asdict(item) for item in artists],
        "applied": applied,
    }


def run(mode: str) -> Path:
    """``preview`` looks, ``apply`` changes; the report lands beside the log. Returns its path.

    ⚠️ Never directly in the data folder: the container's start script refuses a data folder holding a name it does not
    know (``docker/entrypoint.sh``), and on 17.11.2026 three reports of 1.8.0 kept nexcrate from starting."""
    with _running:
        moment = utcnow()
        findings = survey()
        applied = apply(findings) if mode == "apply" else None
        # The artists after the albums: their plan sees the albums as they lie now.
        artists = survey_artists()
        if applied is not None:
            applied["artists"] = apply_artists(artists)
        data = report(findings, artists, mode, applied, moment)
        target = logs.log_dir() / f"music-version-repair-{mode}-{moment:%Y%m%d-%H%M%S}.json"
        target.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        logger.info(
            "Version repair %s: %d albums found, %d to change, %d artist folders, report %s",
            mode,
            data["albums"],
            data["changing"],
            sum(1 for item in artists if item.slashes),
            target.name,
        )
        return target


def run_job() -> None:
    """Every minute: a request file in the data folder starts a run, once."""
    # Written out, so the guard of the start script's known names sees it (``test_entrypoint``).
    request = get_settings().data_dir / "music-version-repair.request"
    if not request.is_file() or _running.locked():
        return
    try:
        mode = request.read_text(encoding="utf-8").strip().lower()
        request.unlink()
    except OSError:
        logger.warning("Version repair: the request file could not be read")
        return
    if mode not in ("preview", "apply"):
        logger.warning("Version repair: unknown request %r, nothing done", mode[:20])
        return
    try:
        run(mode)
    except Exception:
        logger.exception("Version repair %s failed", mode)
