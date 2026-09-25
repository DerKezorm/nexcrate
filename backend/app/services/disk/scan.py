"""The disk scan (L4): walking the roots, matching every candidate, storing the result.

Phases of the job: ``listing`` (the roots are walked), ``folders`` (every candidate is looked at: videos,
``release.nex``, ``.nfo``), ``radarr`` (the movie folders of Radarr connections nexcrate still reads, through the
takeover's mapping by files), ``matching`` (the order of decision 17, rows written per root), ``tmdb`` (proposals TMDB
has to answer for, 50 folders and then a minute's pause; without a token the phase ends at once and the rows wait for
the next scan).

Matching order; the first that applies sets the state: ``library``, ``moved``, ``radarr``, ``restorable``, ``proposal``,
``unknown``; a ``release.nex`` of this installation naming another TMDB number than the library version makes a
``conflict``, and so does one whose labels fit no definition of the root. Besides ``file``, ``disc``, ``no_video`` and
``unreadable``. ``ignored`` is a mark of the owner kept on the row.

A scan updates a root's rows in place by relative path, so a folder keeps its id; rows not seen again go. Log lines
carry counts per state and ids, never names or paths.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ... import crypto
from ...db import SessionLocal
from ...models import DiskFolder, DiskRoot, Source, Title, Version, VersionDefinition, utcnow
from .. import companions, folders, schreibweisen, takeover, tmdb
from ..radarr import RadarrClient, RadarrError
from . import proposals as proposing
from . import roots as root_service
from .jobs import DiskJob
from .walk import Candidate, Numbers, walk

logger = logging.getLogger("nexcrate.disk")

#: The pace of the TMDB phase, as the takeover's fill: this many folders, then a pause.
PACE_BATCH = 50
PACE_SECONDS = 60.0
MISSING_EXAMPLES = 20
#: Replaceable in tests: the pause between two batches of the TMDB phase.
pause: Callable[[float], None] = time.sleep


# --- What the matching knows ------------------------------------------------------------------------------ #


@dataclass(frozen=True)
class OwnedFile:
    """A version nexcrate owns with a file, by the resolved root folder and the NFC folder parts of its path."""

    version_id: int
    title_id: int
    tmdb_id: int
    definition_id: int
    root_key: str
    folder_parts: tuple[str, ...]
    folder_as_stored: str
    file_name: str


@dataclass(frozen=True)
class VersionInfo:
    version_id: int
    title_id: int
    has_file: bool
    source_id: int | None


@dataclass
class Index:
    library: proposing.Library
    #: Owned files by (root key, NFC folder parts).
    files: dict[tuple[str, tuple[str, ...]], list[OwnedFile]] = field(default_factory=dict)
    #: Owned files per root key, for the missing count.
    files_by_root: dict[str, list[OwnedFile]] = field(default_factory=dict)
    files_by_version: dict[int, OwnedFile] = field(default_factory=dict)
    #: Every version of a movie title by (TMDB number, definition id).
    versions: dict[tuple[int, int], VersionInfo] = field(default_factory=dict)
    definitions: dict[int, VersionDefinition] = field(default_factory=dict)
    #: Radarr's movie folders by (root key, NFC folder parts): the source's id and name.
    radarr: dict[tuple[str, tuple[str, ...]], tuple[int, str]] = field(default_factory=dict)
    radarr_unmapped: list[str] = field(default_factory=list)
    #: Folders left out of the scan by (root key, NFC folder parts): Radarr's recycle bins (seen on the owner's
    #: instance, 15.09.2026: a folder "Papierkorb" full of replaced files showed up as a folder without video).
    left_out: set[tuple[str, tuple[str, ...]]] = field(default_factory=set)
    installation: str = ""

    def definition_by_label(self, label: str, among: list[VersionDefinition]) -> VersionDefinition | None:
        candidates = among or list(self.definitions.values())
        return next((definition for definition in candidates if definition.label == label), None)


def _parts(relative_path: str) -> tuple[str, ...]:
    return tuple(schreibweisen.nfc(part) for part in relative_path.replace("\\", "/").split("/") if part)


def build_index(db: OrmSession) -> Index:
    index = Index(library=proposing.Library.load(db), installation=companions.installation_id(db))
    index.definitions = {
        definition.id: definition
        for definition in db.scalars(select(VersionDefinition).where(VersionDefinition.kind == "movie"))
    }
    cache: dict[str, str] = {}
    rows = db.execute(
        select(Version, Title.tmdb_id).join(Title, Title.id == Version.title_id).where(Title.kind == "movie")
    ).all()
    for version, tmdb_id in rows:
        index.versions[(tmdb_id, version.version_definition_id)] = VersionInfo(
            version.id, version.title_id, bool(version.has_file), version.source_id
        )
        if not companions.owned(version) or not version.root_folder or not version.relative_path:
            continue
        parts = [part for part in version.relative_path.replace("\\", "/").split("/") if part]
        if len(parts) < 2:
            continue
        if version.root_folder not in cache:
            resolved = folders.resolved(version.root_folder)
            cache[version.root_folder] = str(resolved) if resolved is not None else version.root_folder
        owned = OwnedFile(
            version_id=version.id,
            title_id=version.title_id,
            tmdb_id=tmdb_id,
            definition_id=version.version_definition_id,
            root_key=cache[version.root_folder],
            folder_parts=tuple(schreibweisen.nfc(part) for part in parts[:-1]),
            folder_as_stored="/".join(parts[:-1]),
            file_name=schreibweisen.nfc(parts[-1]),
        )
        index.files.setdefault((owned.root_key, owned.folder_parts), []).append(owned)
        index.files_by_root.setdefault(owned.root_key, []).append(owned)
        index.files_by_version[owned.version_id] = owned
    return index


async def _fetch_recycle_bin(url: str, api_key: str) -> str | None:
    async with RadarrClient(url, api_key) as radarr:
        return (await radarr.media_management()).recycle_bin


def recycle_bin_remote(source: Source) -> str | None:
    """Radarr's recycle bin as Radarr names it, or None when Radarr cannot be asked. Never raises."""
    api_key = crypto.decrypt(source.api_key or "")
    if not api_key:
        return None
    try:
        return asyncio.run(_fetch_recycle_bin(source.url, api_key))
    except RadarrError, OSError, RuntimeError, ValueError:
        logger.info("Source %d: the media management could not be read; the recycle bin is not left out", source.id)
        return None


def _left_out_recycle_bin(index: Index, source: Source, states: dict[str, Any], ordered: list[Path]) -> None:
    """Map Radarr's recycle bin through the mapped root folders and leave it out below every scanned folder."""
    remote = recycle_bin_remote(source)
    if not remote:
        return
    remote_norm = remote.replace("\\", "/").rstrip("/")
    for state in states.values():
        if state.local is None:
            continue
        prefix = state.remote.replace("\\", "/").rstrip("/")
        if remote_norm != prefix and not remote_norm.startswith(prefix + "/"):
            continue
        tail = [part for part in remote_norm[len(prefix) :].split("/") if part]
        resolved = folders.resolved(Path(state.local).joinpath(*tail))
        if resolved is None:
            continue
        for root in ordered:
            if resolved != root and resolved.is_relative_to(root):
                key = (str(root), tuple(schreibweisen.nfc(part) for part in resolved.relative_to(root).parts))
                index.left_out.add(key)
                logger.info("Source %d: Radarr's recycle bin lies below a scanned folder and is left out", source.id)
                break


def radarr_folders(db: OrmSession, index: Index, root_paths: list[Path]) -> None:
    """The movie folders of every Radarr connection nexcrate still reads, mapped by files, keyed by root and folder."""
    sight = takeover.Sight()
    ordered = sorted(root_paths, key=lambda path: len(path.parts), reverse=True)
    # Only Radarr: a Sonarr connection feeds series, and the scan reads movie folders only.
    sources = list(
        db.scalars(select(Source).where(Source.taken_over_at.is_(None), Source.app == "radarr").order_by(Source.id))
    )
    for source in sources:
        versions = list(db.scalars(select(Version).where(Version.source_id == source.id).order_by(Version.id)))
        items = [takeover.item_of(version) for version in versions]
        states = takeover.map_roots(sight, items, ())
        if any(state.files > 0 and state.local is None for state in states.values()):
            index.radarr_unmapped.append(source.name)
        locals_seen: dict[str, Path | None] = {}
        mapped = 0
        for item in items:
            if not (item.has_file and item.locatable and item.root is not None):
                continue
            state = states.get(item.root)
            if state is None or state.local is None:
                continue
            if state.remote not in locals_seen:
                locals_seen[state.remote] = sight.folder(state.local)
            local = locals_seen[state.remote]
            if local is None:
                continue
            movie = sight.lookup(local, item.movie_names)
            resolved = folders.resolved(movie) if movie is not None else None
            if resolved is None:
                continue
            for root in ordered:
                if resolved != root and resolved.is_relative_to(root):
                    key = (str(root), tuple(schreibweisen.nfc(part) for part in resolved.relative_to(root).parts))
                    index.radarr.setdefault(key, (source.id, source.name))
                    mapped += 1
                    break
        logger.info("Source %d: %d movie folders lie below the scanned folders", source.id, mapped)
        _left_out_recycle_bin(index, source, states, ordered)


# --- Matching --------------------------------------------------------------------------------------------- #


@dataclass
class Match:
    candidate: Candidate
    state: str
    title_id: int | None = None
    version_id: int | None = None
    source_id: int | None = None
    tmdb_id: int | None = None
    proposals: list[dict[str, Any]] = field(default_factory=list)
    numbers: dict[str, Any] | None = None


#: What of an entry the row keeps: never the subtitles, never a path.
ENTRY_KEYS = ("version", "file", "size_bytes", "quality", "quality_from", "release_title", "release_group", "languages")


def companion_out(result: companions.ReadResult | None) -> dict[str, Any] | None:
    """The read outcome for the row: the numbers, name and year, and the entries without subtitles; None without a
    file."""
    if result is None or result.outcome == companions.MISSING:
        return None
    movie = result.movie or {}
    return {
        "outcome": result.outcome,
        "installation": result.installation,
        "tmdb_id": movie.get("tmdb_id"),
        "imdb_id": movie.get("imdb_id"),
        "title": movie.get("title"),
        "year": movie.get("year"),
        "entries": [{key: entry.get(key) for key in ENTRY_KEYS} for entry in result.entries],
    }


def _companion_tmdb(candidate: Candidate) -> int | None:
    result = candidate.companion
    if result is None or not result.readable or result.movie is None:
        return None
    return int(result.movie["tmdb_id"])


def _moved_version(
    db: OrmSession,
    index: Index,
    candidate: Candidate,
    root_key: str,
    parts: tuple[str, ...],
    among: list[VersionDefinition],
) -> OwnedFile | None:
    """A version nexcrate owns that a ``release.nex`` of this installation names, whose stored folder is elsewhere and
    whose video is not there."""
    result = candidate.companion
    if result is None or result.outcome != companions.OURS or result.movie is None:
        return None
    tmdb_id = int(result.movie["tmdb_id"])
    for entry in result.entries:
        definition = index.definition_by_label(entry["version"], among)
        if definition is None:
            continue
        info = index.versions.get((tmdb_id, definition.id))
        if info is None or not info.has_file or info.source_id is not None:
            continue
        stored = index.files_by_version.get(info.version_id)
        if stored is None or (stored.root_key, stored.folder_parts) == (root_key, parts):
            continue
        version = db.get(Version, info.version_id)
        if version is None or companions.locate(version).problem is None:
            continue
        if schreibweisen.nfc(entry["file"]) not in candidate.video_names:
            continue
        return stored
    return None


def _restorable(index: Index, candidate: Candidate, among: list[VersionDefinition]) -> tuple[bool, bool]:
    """Whether a readable ``release.nex`` can restore something here, and whether its labels conflict with the root."""
    result = candidate.companion
    if result is None or not result.readable or result.movie is None:
        return False, False
    tmdb_id = int(result.movie["tmdb_id"])
    restorable = False
    conflict = False
    for entry in result.entries:
        definition = index.definition_by_label(entry["version"], among)
        if definition is None:
            if among:
                conflict = True
            else:
                restorable = True
            continue
        info = index.versions.get((tmdb_id, definition.id))
        if info is None or not info.has_file:
            restorable = True
    if not result.entries and result.outcome == companions.NEWER_FORMAT:
        restorable = True
    return restorable, conflict


def match(db: OrmSession, index: Index, root: DiskRoot, among: list[VersionDefinition], candidate: Candidate) -> Match:
    root_key = root.path
    companion_tmdb = _companion_tmdb(candidate)
    if candidate.fixed_state is not None:
        found = Match(candidate, candidate.fixed_state, tmdb_id=companion_tmdb)
        if companion_tmdb is not None:
            title = index.library.by_tmdb.get(companion_tmdb)
            found.title_id = title.id if title is not None else None
        return found
    parts = _parts(candidate.relative_path)
    owned = [item for item in index.files.get((root_key, parts), []) if item.file_name in candidate.video_names]
    if owned:
        first = owned[0]
        state = "library"
        if (
            candidate.companion is not None
            and candidate.companion.outcome == companions.OURS
            and companion_tmdb is not None
            and companion_tmdb != first.tmdb_id
        ):
            state = "conflict"
        return Match(candidate, state, title_id=first.title_id, version_id=first.version_id, tmdb_id=first.tmdb_id)
    moved = _moved_version(db, index, candidate, root_key, parts, among)
    if moved is not None:
        return Match(candidate, "moved", title_id=moved.title_id, version_id=moved.version_id, tmdb_id=moved.tmdb_id)
    radarr = index.radarr.get((root_key, parts))
    if radarr is not None:
        title = index.library.by_tmdb.get(companion_tmdb) if companion_tmdb is not None else None
        return Match(
            candidate, "radarr", source_id=radarr[0], tmdb_id=companion_tmdb, title_id=title.id if title else None
        )
    restorable, conflict = _restorable(index, candidate, among)
    known = index.library.by_tmdb.get(companion_tmdb) if companion_tmdb is not None else None
    if conflict:
        return Match(candidate, "conflict", tmdb_id=companion_tmdb, title_id=known.id if known else None)
    if restorable:
        assert candidate.companion is not None and candidate.companion.movie is not None
        movie = candidate.companion.movie
        found = Match(candidate, "restorable", tmdb_id=companion_tmdb, title_id=known.id if known else None)
        found.proposals = [
            proposing.proposal(
                tmdb_id=int(movie["tmdb_id"]),
                imdb_id=movie.get("imdb_id"),
                title=(known.title if known else None) or movie.get("title") or candidate.parsed_title or "",
                year=(known.year if known else None) or movie.get("year") or candidate.parsed_year,
                poster_file=known.tmdb_poster_path if known else None,
                source="companion",
                title_id=known.id if known else None,
                unambiguous=True,
            )
        ]
        return found
    # Proposals: numbers in the folder name and the largest video's name, then the .nfo, then title and year.
    numbers = proposing.numbers_in_name(candidate.name)
    largest = candidate.largest
    if largest is not None:
        numbers.take(proposing.numbers_in_name(str(largest["name"])))
    numbers.take(candidate.nfo)
    found_proposals: list[dict[str, Any]] = []
    if companion_tmdb is not None and candidate.companion is not None and candidate.companion.movie is not None:
        movie = candidate.companion.movie
        found_proposals.append(
            proposing.proposal(
                tmdb_id=companion_tmdb,
                imdb_id=movie.get("imdb_id"),
                title=(known.title if known else None) or movie.get("title") or candidate.parsed_title or "",
                year=(known.year if known else None) or movie.get("year") or candidate.parsed_year,
                poster_file=known.tmdb_poster_path if known else None,
                source="companion",
                title_id=known.id if known else None,
                unambiguous=True,
            )
        )
    by_number, pending = proposing.number_proposals(index.library, numbers)
    for item in by_number:
        if not any(existing["tmdb_id"] == item["tmdb_id"] for existing in found_proposals):
            found_proposals.append(item)
    needs_tmdb = not pending.empty
    if not found_proposals and pending.empty:
        found_proposals = proposing.library_proposals(index.library, candidate.parsed_title, candidate.parsed_year)
        needs_tmdb = not found_proposals and bool(candidate.parsed_title)
    state = "proposal" if found_proposals or not pending.empty else "unknown"
    return Match(
        candidate,
        state,
        tmdb_id=proposing.best_number(found_proposals, companion_tmdb, numbers),
        title_id=known.id if known else _proposed_title(found_proposals),
        proposals=found_proposals,
        numbers={**numbers.out(), "tmdb_asked": not needs_tmdb},
    )


def _proposed_title(found: list[dict[str, Any]]) -> int | None:
    unambiguous = [item for item in found if item.get("unambiguous") and item.get("title_id") is not None]
    return int(unambiguous[0]["title_id"]) if len(unambiguous) == 1 else None


# --- Storing --------------------------------------------------------------------------------------------- #


def store_root(db: OrmSession, root: DiskRoot, matches: list[Match], index: Index, moment: datetime) -> dict[str, Any]:
    """Write a root's rows in place by relative path; rows not seen again go. Returns the counts. Stages only."""
    existing = {row.relative_path: row for row in db.scalars(select(DiskFolder).where(DiskFolder.root_id == root.id))}
    seen: set[str] = set()
    counts: dict[str, int] = {}
    for found in matches:
        candidate = found.candidate
        row = existing.get(candidate.relative_path)
        if row is None:
            row = DiskFolder(root_id=root.id, relative_path=candidate.relative_path)
            db.add(row)
            existing[candidate.relative_path] = row
        seen.add(candidate.relative_path)
        row.kind = candidate.kind
        row.state = found.state
        row.videos = list(candidate.videos)
        row.parsed_title = candidate.parsed_title
        row.parsed_year = candidate.parsed_year
        row.companion = companion_out(candidate.companion)
        row.numbers = found.numbers
        row.proposals = found.proposals or None
        row.tmdb_id = found.tmdb_id
        row.title_id = found.title_id
        row.version_id = found.version_id
        row.source_id = found.source_id
        row.seen_at = moment
        key = "ignored" if row.ignored else found.state
        counts[key] = counts.get(key, 0) + 1
    for relative_path, row in existing.items():
        if relative_path not in seen:
            db.delete(row)
    # Own files missing on disk: versions whose root folder is this root and whose video no candidate holds.
    present = {(found.candidate.relative_path, name) for found in matches for name in found.candidate.video_names}
    present_nfc = {("/".join(_parts(path)), name) for path, name in present}
    missing = [
        owned
        for owned in index.files_by_root.get(root.path, [])
        if ("/".join(owned.folder_parts), owned.file_name) not in present_nfc
    ]
    return {
        **counts,
        "missing_files": len(missing),
        "missing_examples": [owned.folder_as_stored for owned in missing[:MISSING_EXAMPLES]],
        "radarr_unmapped": list(index.radarr_unmapped),
    }


# --- The job ----------------------------------------------------------------------------------------------- #


@dataclass
class _RootWork:
    root_id: int
    path: Path
    candidates: list[Candidate] = field(default_factory=list)
    truncated: bool = False


def scan_work(root_ids: list[int] | None) -> Callable[[DiskJob], dict[str, Any]]:
    """The work of a scan job over these roots, or every root."""

    def work(job: DiskJob) -> dict[str, Any]:
        return run_scan(job, root_ids)

    return work


def run_scan(job: DiskJob, root_ids: list[int] | None) -> dict[str, Any]:
    moment = utcnow()
    with SessionLocal() as db:
        every = root_service.sync(db)
        db.commit()
        chosen = [
            root
            for root in every
            if (root_ids is None or root.id in root_ids) and root.kind not in ("series", "album")
        ]
        # Series roots are scanned by their own module after the movies (P2).
        series_roots = [
            root.id for root in every if (root_ids is None or root.id in root_ids) and root.kind == "series"
        ]
        # Music roots after them (decision 17).
        album_roots = [root.id for root in every if (root_ids is None or root.id in root_ids) and root.kind == "album"]
        installation = companions.installation_id(db)
        all_paths = {root.id: Path(root.path) for root in every}
    # Phases listing and folders: the roots are walked and every candidate looked at, root after root; a root inside
    # another root is left out of the outer listing.
    job.set(phase="listing", done=0, total=len(chosen))
    works: list[_RootWork] = []
    skipped: list[int] = []
    for index_number, root in enumerate(chosen, start=1):
        path = root_service.visible_path(root)
        if path is None:
            skipped.append(root.id)
            job.progress(index_number)
            continue
        excluded = {
            other
            for other_id, other in all_paths.items()
            if other_id != root.id and other != path and other.is_relative_to(path)
        }
        work = _RootWork(root_id=root.id, path=path)
        works.append(work)
        job.set(phase="folders")
        walked = walk(path, installation, excluded)
        work.candidates, work.truncated = walked.candidates, walked.truncated
        job.set(phase="listing")
        job.progress(index_number)
    total_candidates = sum(len(work.candidates) for work in works)
    if skipped:
        with SessionLocal() as db:
            for root_id in skipped:
                row = db.get(DiskRoot, root_id)
                if row is not None:
                    row.last_error_code = "folder_not_visible"
                    row.last_scan_at = moment
            db.commit()
        logger.info("%d scanned folders are not visible and were skipped", len(skipped))
    # Phase radarr and matching.
    job.set(phase="radarr")
    result_counts: dict[str, int] = {}
    missing_total = 0
    with SessionLocal() as db:
        index = build_index(db)
        radarr_folders(db, index, [work.path for work in works])
        job.set(phase="matching", done=0, total=total_candidates)
        done = 0
        for work in works:
            root = db.get(DiskRoot, work.root_id)
            if root is None:
                continue
            among = root_service.definitions_of(db, root)
            matches = []
            for candidate in work.candidates:
                if (root.path, _parts(candidate.relative_path)) in index.left_out:
                    continue
                matches.append(match(db, index, root, among, candidate))
                done += 1
                if done % 100 == 0:
                    job.progress(done)
            counts = store_root(db, root, matches, index, moment)
            root.last_scan_at = moment
            root.last_counts = counts
            root.last_error_code = None
            db.commit()
            for key, value in counts.items():
                if isinstance(value, int) and key != "missing_files":
                    result_counts[key] = result_counts.get(key, 0) + value
            missing_total += int(counts.get("missing_files", 0))
            logger.info(
                "Scanned folder %d: %d candidates, %s, %d own files missing%s",
                root.id,
                len(work.candidates),
                ", ".join(f"{key}={value}" for key, value in sorted(counts.items()) if isinstance(value, int)),
                counts.get("missing_files", 0),
                ", listing truncated" if work.truncated else "",
            )
        job.progress(done)
    # Phase tmdb.
    tmdb_state = tmdb_phase(job, [work.root_id for work in works], index)
    if series_roots:
        from . import series_scan

        series_state = series_scan.run(job, series_roots)
        for key, value in series_state.pop("counts", {}).items():
            result_counts[key] = result_counts.get(key, 0) + value
        skipped.extend(series_state.pop("skipped", []))
        series_state["series_roots"] = series_state.pop("roots", [])
        tmdb_state = {**tmdb_state, **series_state}
    if album_roots:
        from . import album_scan

        album_state = album_scan.run(job, album_roots)
        for key, value in album_state.pop("counts", {}).items():
            result_counts[key] = result_counts.get(key, 0) + value
        skipped.extend(album_state.pop("skipped", []))
        tmdb_state = {**tmdb_state, **album_state}
    return {
        "roots": [work.root_id for work in works],
        "skipped": skipped,
        "truncated": [work.root_id for work in works if work.truncated],
        "counts": result_counts,
        "missing_files": missing_total,
        **tmdb_state,
    }


# --- The TMDB phase -------------------------------------------------------------------------------------------- #


def tmdb_ready() -> bool:
    stored, token = tmdb.token_state()
    return stored and bool(token)


def _pending_rows(db: OrmSession, root_ids: list[int]) -> list[int]:
    if not root_ids:
        return []
    rows = db.scalars(
        select(DiskFolder)
        .where(
            DiskFolder.root_id.in_(root_ids),
            DiskFolder.state.in_(("proposal", "unknown")),
            DiskFolder.ignored.is_(False),
        )
        .order_by(DiskFolder.id)
    )
    return [row.id for row in rows if isinstance(row.numbers, dict) and not row.numbers.get("tmdb_asked", True)]


async def _ask(token: str, locale: str, library: proposing.Library, row: DiskFolder) -> None:
    numbers_out = dict(row.numbers or {})
    numbers = Numbers(
        tmdb_id=numbers_out.get("tmdb_id"),
        tmdb_from=numbers_out.get("tmdb_from"),
        imdb_id=numbers_out.get("imdb_id"),
        imdb_from=numbers_out.get("imdb_from"),
    )
    pending = Numbers()
    if numbers.tmdb_id is not None and numbers.tmdb_id not in library.by_tmdb:
        pending.tmdb_id, pending.tmdb_from = numbers.tmdb_id, numbers.tmdb_from
    if numbers.imdb_id is not None and proposing.imdb_number(numbers.imdb_id) not in library.by_imdb:
        pending.imdb_id, pending.imdb_from = numbers.imdb_id, numbers.imdb_from
    found = list(row.proposals or [])
    if not pending.empty:
        for item in await proposing.tmdb_number_proposals(token, locale, pending, library):
            if not any(existing["tmdb_id"] == item["tmdb_id"] for existing in found):
                found.append(item)
    if not found and row.parsed_title:
        found = await proposing.tmdb_title_proposals(token, locale, row.parsed_title, row.parsed_year, library)
    row.proposals = found or None
    row.state = "proposal" if found else "unknown"
    companion_tmdb = row.companion.get("tmdb_id") if isinstance(row.companion, dict) else None
    row.tmdb_id = proposing.best_number(found, companion_tmdb, numbers)
    if row.title_id is None:
        row.title_id = _proposed_title(found)
    numbers_out["tmdb_asked"] = True
    row.numbers = numbers_out


async def _ask_batch(token: str, locale: str, library: proposing.Library, row_ids: list[int]) -> tuple[int, str | None]:
    """One batch in one session; stops at the first TMDB error. Returns how many were answered and the code."""
    answered = 0
    with SessionLocal() as db:
        for row_id in row_ids:
            row = db.get(DiskFolder, row_id)
            if row is None:
                continue
            try:
                await _ask(token, locale, library, row)
            except tmdb.TmdbError as exc:
                db.commit()
                return answered, exc.code
            answered += 1
        db.commit()
    return answered, None


def tmdb_phase(job: DiskJob, root_ids: list[int], index: Index) -> dict[str, Any]:
    with SessionLocal() as db:
        pending = _pending_rows(db, root_ids)
    if not pending:
        return {"tmdb_ready": tmdb_ready(), "tmdb_pending": 0, "tmdb_asked": 0}
    stored, token = tmdb.token_state()
    if not stored or not token:
        logger.info("%d folders wait for TMDB: no token is stored", len(pending))
        return {"tmdb_ready": False, "tmdb_pending": len(pending), "tmdb_asked": 0}
    job.set(phase="tmdb", done=0, total=len(pending))
    locale = tmdb.account_locale()
    asked = 0
    error: str | None = None
    for start in range(0, len(pending), PACE_BATCH):
        if start:
            pause(PACE_SECONDS)
        batch = pending[start : start + PACE_BATCH]
        count, error = asyncio.run(_ask_and_close(token, locale, index.library, batch))
        asked += count
        job.progress(asked)
        if error is not None:
            logger.warning("The TMDB phase of the scan stopped after %d folders: %s", asked, error)
            break
    logger.info("TMDB asked for %d of %d folders", asked, len(pending))
    return {"tmdb_ready": True, "tmdb_pending": len(pending) - asked, "tmdb_asked": asked, "tmdb_error": error}


async def _ask_and_close(
    token: str, locale: str, library: proposing.Library, row_ids: list[int]
) -> tuple[int, str | None]:
    try:
        return await _ask_batch(token, locale, library, row_ids)
    finally:
        await tmdb.close()
