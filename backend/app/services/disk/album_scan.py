"""Album folders on the page "Ordner" (decisions 17 to 20).

Below a music root (the folder of the music version definition, the ``root_folder`` of an own album version, or a
folder the owner added as a music folder) the scan walks artist folders and album folders, at most three levels deep:
every folder with audio files (directly or in a medium folder such as ``CD 01``) is an album folder. An artist folder
Lidarr filed flat into is one too. The scan reads no file but the first audio file's tags and the ``release.nex``.

**Matching**, in this order (decision 18): the stored album folder of an own version (``library``); ``release.nex``
(``restorable``, or ``conflict`` when that album has a folder with files elsewhere); a release group or release id in
the tags (Picard, Lidarr); artist and album from the tags, else from the folder names, against the library. An album a
Lidarr connection feeds is ``lidarr`` and never offered. Unknown albums are added over MusicBrainz on the page.

What an album row carries lies in columns the movie scan fills otherwise: ``numbers`` holds what the folder says
(``audio``, ``artist``, ``album``, ``release_group``, ``release``, ``from``), ``proposals`` the album proposals, and
``companion`` the outcome and ids of the ``release.nex``. Nothing on disk is written. Log lines carry ids and counts.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ...db import SessionLocal
from ...models import Artist, DiskFolder, DiskRoot, Release, Title, Version, utcnow
from .. import companions, companions_album, schreibweisen
from ..downloads import files
from ..music import album_read, paths, tags
from . import roots as root_service
from .jobs import DiskJob

logger = logging.getLogger("nexcrate.disk")

MAX_FOLDERS = 20_000
#: Artist folder, album folder, and one more for ``Artist/Year/Album`` and the like.
MAX_DEPTH = 3
MAX_PROPOSALS = 5
_MBID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_BRACKETS = re.compile(r"\s*[\[({][^\])}]*[\])}]")
_YEAR_FIRST = re.compile(r"^\s*((?:19|20)\d{2})\s*[-\u2013.]\s*")
_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")


def _mbid(value: object) -> str | None:
    text = str(value or "").strip().lower()
    return text if _MBID.match(text) else None


def _key(text: str) -> str:
    return schreibweisen.nfc(text).casefold()


@dataclass
class Candidate:
    relative: str
    path: Path
    audio: int
    artist: str | None
    album: str
    year: int | None
    release_group: str | None = None
    release: str | None = None
    #: ``companion`` or ``tags``: where the ids came from.
    ids_from: str | None = None
    companion: dict[str, Any] | None = None


def album_name(folder_name: str) -> tuple[str, int | None]:
    """The album's name and year as a folder spells them: ``1997 - OK Computer [FLAC]`` → ``OK Computer``, 1997."""
    year_match = _YEAR_FIRST.match(folder_name) or _YEAR.search(folder_name)
    year = int(year_match.group(1)) if year_match else None
    name = _BRACKETS.sub("", _YEAR_FIRST.sub("", folder_name)).strip(" -\u2013.")
    return (name or folder_name.strip()), year


def _folders_in(folder: Path) -> list[os.DirEntry[str]]:
    try:
        with os.scandir(folder) as found:
            entries = sorted(found, key=lambda entry: entry.name)
    except OSError:
        return []
    chosen = []
    for entry in entries:
        try:
            if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                continue
        except OSError:
            continue
        name = entry.name
        if name.startswith((".", "@", *album_read.OWN_PREFIXES)) or files._left_out_folder(name):
            continue
        chosen.append(entry)
    return chosen


def list_candidates(root: Path) -> list[tuple[list[str], Path, int]]:
    """Every album folder below the root: its names below the root, its path and how many audio files it holds."""
    found: list[tuple[list[str], Path, int]] = []

    def walk(folder: Path, names: list[str]) -> None:
        if len(found) >= MAX_FOLDERS:
            return
        if names:
            try:
                audio = len(album_read.audio_in(folder))
            except OSError:
                audio = 0
            if audio:
                found.append((names, folder, audio))
        if len(names) >= MAX_DEPTH:
            return
        for entry in _folders_in(folder):
            if paths.MEDIUM_FOLDER.match(entry.name):
                continue
            walk(Path(entry.path), [*names, entry.name])

    walk(root, [])
    if len(found) >= MAX_FOLDERS:
        logger.warning("A music root holds more than %d album folders; the rest is not looked at", MAX_FOLDERS)
    return found[:MAX_FOLDERS]


def inspect(names: list[str], path: Path, audio: int, installation: str) -> Candidate:
    album, year = album_name(names[-1])
    candidate = Candidate(
        relative="/".join(names), path=path, audio=audio, artist=names[0] if len(names) > 1 else None,
        album=album, year=year,
    )  # fmt: skip
    read = companions_album.read(path, installation)
    outcome = read.get("outcome")
    if outcome in (companions.OURS, companions.OTHER_INSTALLATION):
        block = read.get("album") if isinstance(read.get("album"), dict) else {}
        group = _mbid(block.get("mbid"))
        target = block.get("target_release") if isinstance(block.get("target_release"), dict) else {}
        candidate.companion = {
            "outcome": outcome,
            "mbid": group,
            "title": str(block.get("title") or "")[:512] or None,
            "target_release": _mbid(target.get("mbid")),
            "files": len(read.get("entries") or []),
        }
        if group:
            candidate.release_group, candidate.ids_from = group, "companion"
            candidate.release = _mbid(target.get("mbid"))
    elif outcome not in (None, companions.MISSING):
        candidate.companion = {"outcome": outcome}
    first = next(iter(album_read.audio_in(path)), None)
    first_path = paths.file_in(path, first) if first else None
    found = tags.read(first_path) if first_path is not None else None
    values = (found.tags or {}) if found is not None else {}

    def tag(name: str) -> str | None:
        items = values.get(name) or []
        text = str(items[0]).strip() if items else ""
        return text[:512] or None

    if candidate.release_group is None:
        group, release = _mbid(tag("musicbrainz_releasegroupid")), _mbid(tag("musicbrainz_albumid"))
        if group or release:
            candidate.release_group, candidate.release, candidate.ids_from = group, release, "tags"
    candidate.album = tag("album") or candidate.album
    candidate.artist = tag("albumartist") or tag("artist") or candidate.artist
    return candidate


# --- Matching ------------------------------------------------------------------------------------------------------- #


@dataclass
class Index:
    #: (root key, album folder key) to (version id, title id) of an own album version.
    own: dict[tuple[str, str], tuple[int, int]]
    #: Titles whose own version has an album folder with files.
    placed: set[int]
    #: Title id to (source id, version id) of a version a Lidarr connection feeds.
    fed: dict[int, tuple[int, int]]
    by_group: dict[str, Title]
    by_release: dict[str, int]
    titles: dict[int, Title]
    artist_names: dict[int, str]
    #: Album name key to titles.
    by_name: dict[str, list[Title]]


def build_index(db: OrmSession) -> Index:
    index = Index({}, set(), {}, {}, {}, {}, {}, {})
    index.artist_names = dict(db.execute(select(Artist.id, Artist.name)).tuples().all())
    for title in db.scalars(select(Title).where(Title.kind == "album")):
        index.titles[title.id] = title
        if title.mbid:
            index.by_group[title.mbid] = title
        for old in title.mbid_old or []:
            index.by_group.setdefault(str(old), title)
        for key in schreibweisen.keys(title.title):
            index.by_name.setdefault(key, []).append(title)
    albums = select(Title.id).where(Title.kind == "album")
    for version in db.scalars(select(Version).where(Version.title_id.in_(albums))):
        if version.source_id is not None:
            index.fed[version.title_id] = (version.source_id, version.id)
        elif version.root_folder and version.relative_path:
            root = files.resolved(version.root_folder)
            root_key = _key(str(root) if root is not None else version.root_folder)
            index.own[(root_key, _key(version.relative_path))] = (version.id, version.title_id)
            if version.has_file:
                index.placed.add(version.title_id)
    for mbid, title_id in db.execute(select(Release.mbid, Release.title_id).where(Release.mbid.is_not(None))):
        index.by_release[str(mbid)] = title_id
    return index


def artist_of(index: Index, title: Title) -> str | None:
    if title.artist_id is not None and title.artist_id in index.artist_names:
        return index.artist_names[title.artist_id]
    credit = title.artist_credit or []
    names = [str(part.get("name") or "") for part in credit if isinstance(part, dict)]
    return " ".join(name for name in names if name) or None


def _year(title: Title) -> int | None:
    text = title.first_release_date or ""
    return int(text[:4]) if text[:4].isdigit() else None


def proposal_of(index: Index, title: Title, source: str, unambiguous: bool) -> dict[str, Any]:
    return {
        "title_id": title.id,
        "mbid": title.mbid,
        "title": title.title,
        "artist": artist_of(index, title),
        "year": _year(title),
        "from": source,
        "unambiguous": unambiguous,
    }


def _unknown_proposal(candidate: Candidate) -> dict[str, Any]:
    """An album the library does not have, named by the id of the folder: added over MusicBrainz on the page."""
    return {
        "title_id": None,
        "mbid": candidate.release_group,
        "release": candidate.release,
        "title": (candidate.companion or {}).get("title") or candidate.album,
        "artist": candidate.artist,
        "year": candidate.year,
        "from": candidate.ids_from or "tags",
        "unambiguous": candidate.release_group is not None,
    }


def _by_names(index: Index, candidate: Candidate) -> list[Title]:
    artist_keys = set(schreibweisen.keys(candidate.artist)) if candidate.artist else set()
    found: list[Title] = []
    for key in schreibweisen.keys(candidate.album):
        for title in index.by_name.get(key, []):
            if title in found:
                continue
            names = [artist_of(index, title) or ""] + [
                str(part.get("name") or "") for part in (title.artist_credit or []) if isinstance(part, dict)
            ]
            if artist_keys and not any(artist_keys & set(schreibweisen.keys(name)) for name in names if name):
                continue
            found.append(title)
    # Without an artist a name alone is a proposal only, never unambiguous; the year narrows it.
    if candidate.year is not None and len(found) > 1:
        same_year = [title for title in found if _year(title) == candidate.year]
        found = same_year or found
    return found


def match(index: Index, root: DiskRoot, candidate: Candidate) -> dict[str, Any]:
    """The row values of one album folder."""
    numbers = {
        "audio": candidate.audio,
        "artist": candidate.artist,
        "album": candidate.album,
        "release_group": candidate.release_group,
        "release": candidate.release,
        "from": candidate.ids_from,
    }
    values: dict[str, Any] = {
        "state": "unknown",
        "proposals": None,
        "tmdb_id": None,
        "title_id": None,
        "version_id": None,
        "source_id": None,
        "companion": candidate.companion,
        "numbers": numbers,
    }
    own = index.own.get((_key(root.path), _key(candidate.relative)))
    if own is not None:
        values.update(state="library", version_id=own[0], title_id=own[1])
        return values
    title: Title | None = None
    if candidate.release_group is not None:
        title = index.by_group.get(candidate.release_group)
    if title is None and candidate.release is not None:
        title_id = index.by_release.get(candidate.release)
        title = index.titles.get(title_id) if title_id is not None else None
    if title is not None and title.id in index.fed:
        source_id, version_id = index.fed[title.id]
        values.update(state="lidarr", title_id=title.id, version_id=version_id, source_id=source_id)
        return values
    if candidate.ids_from == "companion":
        values["state"] = "conflict" if title is not None and title.id in index.placed else "restorable"
        values["title_id"] = title.id if title is not None else None
        values["proposals"] = [
            proposal_of(index, title, "companion", True) if title is not None else _unknown_proposal(candidate)
        ]
        return values
    if title is not None:
        values.update(state="proposal", title_id=title.id)
        values["proposals"] = [proposal_of(index, title, "tags", True)]
        return values
    if candidate.release_group is not None:
        values["state"] = "proposal"
        values["proposals"] = [_unknown_proposal(candidate)]
        return values
    found = _by_names(index, candidate)
    if len(found) == 1 and found[0].id in index.fed:
        source_id, version_id = index.fed[found[0].id]
        values.update(state="lidarr", title_id=found[0].id, version_id=version_id, source_id=source_id)
        return values
    if found:
        unambiguous = len(found) == 1 and candidate.artist is not None
        values.update(state="proposal", title_id=found[0].id if len(found) == 1 else None)
        values["proposals"] = [proposal_of(index, title, "library", unambiguous) for title in found[:MAX_PROPOSALS]]
    return values


def store_root(
    db: OrmSession, root: DiskRoot, found: list[tuple[Candidate, dict[str, Any]]], moment: datetime
) -> dict[str, int]:
    existing = {row.relative_path: row for row in db.scalars(select(DiskFolder).where(DiskFolder.root_id == root.id))}
    seen: set[str] = set()
    for candidate, values in found:
        seen.add(candidate.relative)
        row = existing.get(candidate.relative)
        if row is None:
            row = DiskFolder(root_id=root.id, relative_path=candidate.relative, kind="folder")
            db.add(row)
        row.kind = "folder"
        row.videos = []
        row.series = None
        row.parsed_title = candidate.album[:512]
        row.parsed_year = candidate.year
        for name, value in values.items():
            setattr(row, name, value)
        row.seen_at = moment
    for name, row in existing.items():
        if name not in seen:
            db.delete(row)
    db.flush()
    return root_service.counts_of(db, root.id)


# --- The job -------------------------------------------------------------------------------------------------------- #


def run(job: DiskJob, root_ids: list[int]) -> dict[str, Any]:
    """Scan these music roots. Returns what the scan job's result adds."""
    moment = utcnow()
    counts: dict[str, int] = {}
    scanned: list[int] = []
    skipped: list[int] = []
    with SessionLocal() as db:
        installation = companions.installation_id(db)
        roots = [db.get(DiskRoot, root_id) for root_id in root_ids]
        visible = {root.id: root_service.visible_path(root) for root in roots if root is not None}
    job.set(phase="albums", done=0, total=len(visible))
    for number, (root_id, path) in enumerate(visible.items(), start=1):
        if path is None:
            skipped.append(root_id)
            with SessionLocal() as db:
                row = db.get(DiskRoot, root_id)
                if row is not None:
                    row.last_error_code, row.last_scan_at = "folder_not_visible", moment
                db.commit()
            continue
        candidates = [inspect(names, folder, audio, installation) for names, folder, audio in list_candidates(path)]
        with SessionLocal() as db:
            row = db.get(DiskRoot, root_id)
            if row is None:
                continue
            index = build_index(db)
            found = [(candidate, match(index, row, candidate)) for candidate in candidates]
            root_counts = store_root(db, row, found, moment)
            row.last_scan_at, row.last_counts, row.last_error_code = moment, root_counts, None
            db.commit()
        for key, value in root_counts.items():
            counts[key] = counts.get(key, 0) + value
        scanned.append(root_id)
        job.progress(number)
        logger.info("Scanned music folder %d: %d album folders", root_id, len(candidates))
    return {"album_roots": scanned, "skipped": skipped, "counts": counts}
