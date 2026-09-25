"""Series folders on the page "Ordner" (P2, decisions 25 to 30).

Below a series root (the folder of a series version definition, the ``root_folder`` of an own series version, or a
folder the owner added as a series folder) every direct subfolder is a series folder. The scan looks at each without
reading a video: how many videos reading it would look at (``folder_read``), its season folders, the TMDB number of a
season folder's ``release.nex``, numbers in its name and in ``tvshow.nfo``, and its title and year.

**Matching**, in this order (decision 26): the stored series folder of an own version (``library``); ``release.nex``
(``restorable``, or ``conflict`` when that version has a series folder elsewhere); the series folder of a version a
Sonarr connection feeds, by its folder name (``sonarr``, never offered); numbers in the name or ``tvshow.nfo``; title
and year, the library first. The last two are proposals; TMDB is asked after the matching at the pace of the movie
scan. A folder without a video is ``no_video``.

Nothing on disk is written. Log lines carry ids and counts, never names or paths.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ...db import SessionLocal
from ...models import DiskFolder, DiskRoot, Title, Version, VersionDefinition, utcnow
from .. import companions, companions_series, schreibweisen, tmdb
from ..downloads import files
from ..releases import parser as release_parser
from ..series import folder_read, tmdb_series
from . import proposals as proposing
from . import roots as root_service
from .jobs import DiskJob
from .walk import SafeElementTree

logger = logging.getLogger("nexcrate.disk")

PACE_BATCH = 50
PACE_SECONDS = 60.0
MAX_FOLDERS = 20_000
NFO_MAX_BYTES = 64 * 1024
_TVDB_IN_NAME = re.compile(r"(?<![a-z0-9])tvdb(?:id)?\s*[-=:_ ]\s*(\d{1,10})(?![0-9])", re.IGNORECASE)
_TAGS = re.compile(r"[\[{(]\s*(?:tmdb|tvdb|imdb)(?:id)?\s*[-=:_ ]\s*(?:tt)?\d+\s*[\]})]", re.IGNORECASE)


def pause(seconds: float) -> None:  # replaced in the tests
    import time

    time.sleep(seconds)


@dataclass
class SeriesNumbers:
    tmdb_id: int | None = None
    tvdb_id: int | None = None
    imdb_id: str | None = None
    #: ``name`` or ``nfo``.
    source: str | None = None

    @property
    def empty(self) -> bool:
        return self.tmdb_id is None and self.tvdb_id is None and self.imdb_id is None

    def out(self) -> dict[str, Any]:
        return {"tmdb_id": self.tmdb_id, "tvdb_id": self.tvdb_id, "imdb_id": self.imdb_id, "from": self.source}


def numbers_in_name(name: str) -> SeriesNumbers:
    found = SeriesNumbers()
    movie = proposing.numbers_in_name(name)
    found.tmdb_id, found.imdb_id = movie.tmdb_id, movie.imdb_id
    tvdb = _TVDB_IN_NAME.search(name)
    if tvdb:
        found.tvdb_id = int(tvdb.group(1)) or None
    found.source = None if found.empty else "name"
    return found


def nfo_numbers(raw: bytes) -> SeriesNumbers:
    """TMDB, TVDB and IMDb numbers of a ``tvshow.nfo`` (Kodi, Jellyfin, Emby, Plex's NFO agent)."""
    found = SeriesNumbers()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    try:
        root = SafeElementTree.fromstring(text.strip())
    except Exception:  # noqa: BLE001 - defusedxml raises its own family of errors
        return found

    def number(value: str | None) -> int | None:
        digits = (value or "").strip()
        return int(digits) if digits.isdigit() and 0 < int(digits) <= 2_147_483_647 else None

    for element in root.iter():
        tag = str(element.tag).rsplit("}", 1)[-1].casefold()
        kind = str(element.attrib.get("type", "")).casefold() if tag == "uniqueid" else tag
        value = element.text
        if kind in ("tmdb", "tmdbid") and found.tmdb_id is None:
            found.tmdb_id = number(value)
        elif kind in ("tvdb", "tvdbid") and found.tvdb_id is None:
            found.tvdb_id = number(value)
        elif kind in ("imdb", "imdbid", "imdb_id") and found.imdb_id is None:
            text_value = (value or "").strip().lower()
            found.imdb_id = text_value if re.fullmatch(r"tt\d{7,10}", text_value) else None
    found.source = None if found.empty else "nfo"
    return found


@dataclass
class Candidate:
    name: str
    path: Path
    videos: int = 0
    seasons: list[str] = field(default_factory=list)
    companion_tmdb_id: int | None = None
    #: ``ours`` or ``other_installation``: who wrote the ``release.nex`` the number came from.
    companion_outcome: str | None = None
    #: The version labels the season folders' ``release.nex`` name, for "create the version" before a restore.
    companion_labels: list[str] = field(default_factory=list)
    numbers: SeriesNumbers = field(default_factory=SeriesNumbers)
    parsed_title: str | None = None
    parsed_year: int | None = None


def _key(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def inspect(path: Path, installation: str) -> Candidate:
    candidate = Candidate(name=path.name, path=path)
    candidate.videos = folder_read.videos_in(path)
    try:
        with os.scandir(path) as found:
            entries = sorted(found, key=lambda entry: entry.name)
    except OSError:
        entries = []
    for entry in entries:
        try:
            is_folder = entry.is_dir(follow_symlinks=False) and not entry.is_symlink()
        except OSError:
            continue
        if is_folder and folder_read.season_of_folder(entry.name) is not None:
            candidate.seasons.append(entry.name)
            read = companions_series.read(Path(entry.path), installation)
            readable = read["outcome"] in (companions.OURS, companions.OTHER_INSTALLATION)
            if readable:
                for item in read.get("entries") or []:
                    label = item.get("version") if isinstance(item, dict) else None
                    if isinstance(label, str) and label and label not in candidate.companion_labels:
                        candidate.companion_labels.append(label[:100])
            if candidate.companion_tmdb_id is None:
                series = read.get("series") if readable else None
                tmdb_id = series.get("tmdb_id") if isinstance(series, dict) else None
                if isinstance(tmdb_id, int) and not isinstance(tmdb_id, bool) and tmdb_id > 0:
                    candidate.companion_tmdb_id = tmdb_id
                    candidate.companion_outcome = read["outcome"]
        elif entry.name.casefold() == "tvshow.nfo" and not entry.is_symlink():
            try:
                if entry.stat(follow_symlinks=False).st_size <= NFO_MAX_BYTES:
                    candidate.numbers = nfo_numbers(Path(entry.path).read_bytes())
            except OSError:
                pass
    named = numbers_in_name(path.name)
    if not named.empty:
        candidate.numbers = named
    parsed = release_parser.parse_movie(" ".join(_TAGS.sub(" ", path.name).split()) or path.name)
    candidate.parsed_title = (parsed.title or path.name)[:512]
    candidate.parsed_year = parsed.year
    return candidate


def list_folders(root: Path) -> list[Path]:
    try:
        with os.scandir(root) as found:
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
        if files._left_out_folder(entry.name) or entry.name.startswith((".", "@")):
            continue
        chosen.append(Path(entry.path))
        if len(chosen) >= MAX_FOLDERS:
            logger.warning("A series root holds more than %d folders; the rest is not looked at", MAX_FOLDERS)
            break
    return chosen


# --- Matching ------------------------------------------------------------------------------------------------------- #


@dataclass
class Index:
    #: (root path key, folder name key) to (version id, title id) of an own series version.
    own: dict[tuple[str, str], tuple[int, int]]
    #: Title id to own versions with a series folder, by definition.
    placed: dict[tuple[int, int], tuple[str, str]]
    #: Folder name key to (source id, version id, title id) of a series version a Sonarr connection feeds.
    sonarr: dict[str, list[tuple[int, int, int]]]
    by_tmdb: dict[int, Title]
    by_tvdb: dict[int, Title]
    by_imdb: dict[str, Title]
    by_key: dict[str, list[Title]]


def build_index(db: OrmSession) -> Index:
    index = Index({}, {}, {}, {}, {}, {}, {})
    for version in db.scalars(
        select(Version)
        .join(VersionDefinition, VersionDefinition.id == Version.version_definition_id)
        .where(VersionDefinition.kind == "series")
    ):
        if version.source_id is None and version.root_folder and version.relative_path:
            root = files.resolved(version.root_folder)
            root_key = _key(str(root) if root is not None else version.root_folder)
            index.own[(root_key, _key(version.relative_path))] = (version.id, version.title_id)
            index.placed[(version.title_id, version.version_definition_id)] = (root_key, _key(version.relative_path))
        elif version.source_id is not None and version.source_series_path:
            name = version.source_series_path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
            index.sonarr.setdefault(_key(name), []).append((version.source_id, version.id, version.title_id))
    for title in db.scalars(select(Title).where(Title.kind == "series")):
        index.by_tmdb[title.tmdb_id] = title
        if title.tvdb_id:
            index.by_tvdb[title.tvdb_id] = title
        if title.imdb_id:
            index.by_imdb[title.imdb_id.lower()] = title
        for key in schreibweisen.keys(title.title) + schreibweisen.keys(title.original_title):
            index.by_key.setdefault(key, []).append(title)
    return index


def _library_proposal(title: Title, source: str, unambiguous: bool) -> dict[str, Any]:
    return proposing.from_title(title, source, unambiguous=unambiguous)


def match(index: Index, root: DiskRoot, definition_ids: list[int], candidate: Candidate) -> dict[str, Any]:
    """The row values of one series folder."""
    root_key = _key(root.path)
    values: dict[str, Any] = {
        "state": "unknown",
        "proposals": None,
        "tmdb_id": None,
        "title_id": None,
        "version_id": None,
        "source_id": None,
        "companion": None,
        "numbers": {**candidate.numbers.out(), "tmdb_asked": False},
    }
    if candidate.videos == 0:
        values["state"] = "no_video"
        values["numbers"]["tmdb_asked"] = True
        return values
    own = index.own.get((root_key, _key(candidate.name)))
    if own is not None:
        values.update(state="library", version_id=own[0], title_id=own[1])
        values["numbers"]["tmdb_asked"] = True
        return values
    if candidate.companion_tmdb_id is not None:
        values["companion"] = {
            "outcome": candidate.companion_outcome or companions.OURS,
            "tmdb_id": candidate.companion_tmdb_id,
            # The labels as movie rows carry them, so the restore dialog offers to create a missing version.
            "entries": [{"version": label} for label in candidate.companion_labels[:20]],
        }
        values["tmdb_id"] = candidate.companion_tmdb_id
        title = index.by_tmdb.get(candidate.companion_tmdb_id)
        elsewhere = title is not None and any(
            (title.id, definition_id) in index.placed for definition_id in definition_ids
        )
        values["state"] = "conflict" if elsewhere else "restorable"
        if title is not None:
            values["title_id"] = title.id
            values["proposals"] = [_library_proposal(title, "companion", True)]
        else:
            values["proposals"] = [
                proposing.proposal(
                    tmdb_id=candidate.companion_tmdb_id,
                    imdb_id=None,
                    title=candidate.parsed_title or candidate.name,
                    year=candidate.parsed_year,
                    poster_file=None,
                    source="companion",
                    title_id=None,
                    unambiguous=True,
                )
            ]
        values["numbers"]["tmdb_asked"] = True
        return values
    for source_id, version_id, title_id in index.sonarr.get(_key(candidate.name), []):
        values.update(state="sonarr", source_id=source_id, version_id=version_id, title_id=title_id)
        values["numbers"]["tmdb_asked"] = True
        return values
    numbers = candidate.numbers
    known = (
        (index.by_tmdb.get(numbers.tmdb_id) if numbers.tmdb_id else None)
        or (index.by_tvdb.get(numbers.tvdb_id) if numbers.tvdb_id else None)
        or (index.by_imdb.get(numbers.imdb_id) if numbers.imdb_id else None)
    )
    source = f"{numbers.source}_number" if numbers.source else "library"
    if known is not None:
        values.update(state="proposal", tmdb_id=known.tmdb_id, title_id=known.id)
        values["proposals"] = [_library_proposal(known, source, True)]
        values["numbers"]["tmdb_asked"] = True
        return values
    if not numbers.empty:
        values["state"] = "proposal"
        values["tmdb_id"] = numbers.tmdb_id
        return values
    keys = schreibweisen.keys(candidate.parsed_title)
    titles: list[Title] = []
    for key in keys:
        for title in index.by_key.get(key, []):
            if title not in titles and (
                candidate.parsed_year is None or title.year is None or abs(title.year - candidate.parsed_year) <= 1
            ):
                titles.append(title)
    if titles:
        exact = [title for title in titles if candidate.parsed_year is not None and title.year == candidate.parsed_year]
        # ⚠️ Unambiguous only with the same year: a shared spelling key and a year one off is another series often
        # enough, and the number of a row is what "add a series" later takes its folder from.
        unambiguous = len(titles) == 1 and len(exact) == 1
        values.update(state="proposal", title_id=titles[0].id if len(titles) == 1 else None)
        values["proposals"] = [_library_proposal(title, "library", unambiguous) for title in titles[:5]]
        values["tmdb_id"] = titles[0].tmdb_id if unambiguous else None
        values["numbers"]["tmdb_asked"] = True
    return values


def store_root(
    db: OrmSession, root: DiskRoot, found: list[tuple[Candidate, dict[str, Any]]], moment: datetime
) -> dict[str, int]:
    existing = {row.relative_path: row for row in db.scalars(select(DiskFolder).where(DiskFolder.root_id == root.id))}
    seen: set[str] = set()
    for candidate, values in found:
        seen.add(candidate.name)
        row = existing.get(candidate.name)
        if row is None:
            row = DiskFolder(root_id=root.id, relative_path=candidate.name, kind="folder")
            db.add(row)
        row.kind = "folder"
        row.videos = []
        row.parsed_title = candidate.parsed_title
        row.parsed_year = candidate.parsed_year
        row.series = {
            "videos": candidate.videos,
            "seasons": candidate.seasons[:50],
            "companion_tmdb_id": candidate.companion_tmdb_id,
        }
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
    """Scan these series roots. Returns what the scan job's result adds."""
    moment = utcnow()
    counts: dict[str, int] = {}
    scanned: list[int] = []
    skipped: list[int] = []
    with SessionLocal() as db:
        installation = companions.installation_id(db)
        roots = [db.get(DiskRoot, root_id) for root_id in root_ids]
        paths = {root.id: (root, root_service.visible_path(root)) for root in roots if root is not None}
        db.expunge_all()
    job.set(phase="series", done=0, total=len(paths))
    for index_number, (root_id, (_root, path)) in enumerate(paths.items(), start=1):
        if path is None:
            skipped.append(root_id)
            with SessionLocal() as db:
                row = db.get(DiskRoot, root_id)
                if row is not None:
                    row.last_error_code, row.last_scan_at = "folder_not_visible", moment
                db.commit()
            continue
        candidates = [inspect(folder, installation) for folder in list_folders(path)]
        with SessionLocal() as db:
            row = db.get(DiskRoot, root_id)
            if row is None:
                continue
            index = build_index(db)
            definition_ids = [definition.id for definition in root_service.definitions_of(db, row)]
            found = [(candidate, match(index, row, definition_ids, candidate)) for candidate in candidates]
            root_counts = store_root(db, row, found, moment)
            row.last_scan_at, row.last_counts, row.last_error_code = moment, root_counts, None
            db.commit()
        for key, value in root_counts.items():
            counts[key] = counts.get(key, 0) + value
        scanned.append(root_id)
        job.progress(index_number)
        logger.info("Scanned series folder %d: %d series folders", root_id, len(candidates))
    tmdb_state = tmdb_phase(job, scanned)
    return {"roots": scanned, "skipped": skipped, "counts": counts, **tmdb_state}


def _pending(db: OrmSession, root_ids: list[int]) -> list[int]:
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


async def _ask(token: str, locale: str, row: DiskFolder, index: Index) -> None:
    numbers = dict(row.numbers or {})
    found: list[dict[str, Any]] = []
    tmdb_id = numbers.get("tmdb_id")
    source = f"{numbers.get('from')}_number" if numbers.get("from") else "name_number"
    if not isinstance(tmdb_id, int) and (numbers.get("tvdb_id") or numbers.get("imdb_id")):
        tmdb_id = await tmdb_series.find_series(
            token, tvdb_id=numbers.get("tvdb_id"), imdb_id=numbers.get("imdb_id"), locale=locale
        )
    if isinstance(tmdb_id, int):
        known = index.by_tmdb.get(tmdb_id)
        found.append(
            _library_proposal(known, source, True)
            if known is not None
            else proposing.proposal(
                tmdb_id=tmdb_id,
                imdb_id=numbers.get("imdb_id"),
                title=row.parsed_title or row.relative_path,
                year=row.parsed_year,
                poster_file=None,
                source=source,
                title_id=None,
                unambiguous=True,
            )
        )
    elif row.parsed_title:
        page = await tmdb_series.search_series(token, row.parsed_title, year=row.parsed_year, page=1, locale=locale)
        results = list(page.results)
        if not results and row.parsed_year is not None:
            page = await tmdb_series.search_series(token, row.parsed_title, year=None, page=1, locale=locale)
            results = list(page.results)
        keys = set(schreibweisen.keys(row.parsed_title))
        exact = [
            result
            for result in results
            if keys & set(schreibweisen.keys(result.title) + schreibweisen.keys(result.original_title))
            and (row.parsed_year is None or result.year == row.parsed_year)
        ]
        for result in results[: proposing.MAX_PROPOSALS]:
            known = index.by_tmdb.get(result.tmdb_id)
            found.append(
                proposing.proposal(
                    tmdb_id=result.tmdb_id,
                    imdb_id=None,
                    title=result.title,
                    year=result.year,
                    poster_file=result.poster_file,
                    source="title_year",
                    title_id=known.id if known is not None else None,
                    unambiguous=len(exact) == 1 and result is exact[0],
                )
            )
    row.proposals = found or None
    row.state = "proposal" if found else "unknown"
    unambiguous = [item for item in found if item.get("unambiguous")]
    row.tmdb_id = int(unambiguous[0]["tmdb_id"]) if len(unambiguous) == 1 else row.tmdb_id
    numbers["tmdb_asked"] = True
    row.numbers = numbers


async def _batch(token: str, locale: str, row_ids: list[int]) -> tuple[int, str | None]:
    answered = 0
    try:
        with SessionLocal() as db:
            index = build_index(db)
            for row_id in row_ids:
                row = db.get(DiskFolder, row_id)
                if row is None:
                    continue
                try:
                    await _ask(token, locale, row, index)
                except tmdb.TmdbError as exc:
                    db.commit()
                    return answered, exc.code
                answered += 1
            db.commit()
        return answered, None
    finally:
        await tmdb.close()


def tmdb_phase(job: DiskJob, root_ids: list[int]) -> dict[str, Any]:
    with SessionLocal() as db:
        pending = _pending(db, root_ids)
    if not pending:
        return {}
    stored, token = tmdb.token_state()
    if not stored or not token:
        return {"series_tmdb_pending": len(pending)}
    job.set(phase="tmdb", done=0, total=len(pending))
    locale = tmdb.account_locale()
    asked = 0
    error: str | None = None
    for start in range(0, len(pending), PACE_BATCH):
        if start:
            pause(PACE_SECONDS)
        count, error = asyncio.run(_batch(token, locale, pending[start : start + PACE_BATCH]))
        asked += count
        job.progress(asked)
        if error is not None:
            logger.warning("The TMDB phase of the series scan stopped after %d folders: %s", asked, error)
            break
    return {"series_tmdb_pending": len(pending) - asked, "series_tmdb_asked": asked}
