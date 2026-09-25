"""Reading a series folder into a series version of nexcrate's own (P1, decisions 19 to 24).

One core for every way a series comes onto nexcrate's disk from elsewhere: a takeover from Sonarr (files Sonarr did not
know), a folder assigned or restored on the page "Ordner", a series added whose folder already lies there, and the
button "Ordner neu einlesen".

* **Where:** the version's series folder (``root_folder`` and ``relative_path``). A version without one looks for the
  folder its naming would give below its definition's folder, then for exactly one series folder the last disk scan
  matched to the title; a folder another version of the title uses already is never taken.
* **What:** videos directly in the series folder and one level below (season folders, ``Specials``); no deeper, no
  links, no dot names, no left-out folders, no samples or extras by name (``files.scan``'s rules). A path the version
  knows already is never read twice, so reading again is always safe.
* **Which episodes:** a season folder's ``release.nex`` first (episodes by TMDB episode id), else the name through the
  series parser and every numbering of the series; a name without a season (``05 - Title``) takes its season folder's
  number. Only a single unambiguous reading links; the rest is unclear and stays on the series page (Ü3). A season
  folder with a name whose numbers the series does not have counts otherwise: none of its names links by itself. An
  episode with a file keeps it; of two new files for one episode the better by the profile, then the larger, links.
* **Media data** is read for every counted video, except a file whose ``release.nex`` entry has the same size. A sample
  by runtime (Sonarr's limits) is left out.
* **Gone files:** an unclear or left-out file no longer on disk leaves the version while the series folder is there
  (18.09.2026: the owner deleted duplicate season folders, and their left-out files stayed listed). A linked file is
  never dropped here: a missing episode file is for the owner to see, not to vanish.
* **The end:** ``files_read_at`` is set, the version is counted and planned again, ``release.nex`` is written for the
  season folders that got a file. Until then the version wants nothing (decision 23).

One worker thread reads one version after another. Log lines carry ids and counts, never names or paths.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import os
import queue
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ...db import SessionLocal
from ...models import (
    DiskFolder,
    DiskRoot,
    Episode,
    EpisodeFile,
    EpisodeVersion,
    HistoryEntry,
    SeasonFolder,
    Title,
    Version,
    VersionDefinition,
    utcnow,
)
from .. import companions, companions_series, folders, media, naming_series, releases
from ..downloads import episodes as episode_reading
from ..downloads import files, series_import
from ..profiles import store as profile_store
from ..releases import series_decision
from . import release_match, watching
from .parts import as_whole

logger = logging.getLogger("nexcrate.series.read")

#: More videos than this in one series folder: the rest is not looked at.
MAX_VIDEOS = 5_000
#: Files written per transaction.
BATCH = 50
FILE_REF = "found"
_SEASON_FOLDER = re.compile(
    r"^(?:season|staffel|saison|temporada|stagione|seizoen|sezon|series|s)[ ._-]*0*(\d{1,4})$", re.IGNORECASE
)
_SPECIALS = frozenset({"specials", "special", "extras season", "season 0", "staffel 0"})
#: A name without a season: ``05 - Title``, ``E05``, ``Episode 5``, ``Folge 5``.
_BARE_EPISODE = re.compile(r"^(?:e|ep|episode|folge|episodio|épisode)?[ ._-]*0*(\d{1,3})(?:[ ._-]|$)", re.IGNORECASE)


def season_of_folder(name: str) -> int | None:
    """The season a folder name says: ``Season 01``, ``Staffel 2``, ``S03``, ``Specials`` (0); None otherwise."""
    text = unicodedata.normalize("NFC", name).strip()
    if text.casefold() in _SPECIALS:
        return 0
    found = _SEASON_FOLDER.match(text)
    return int(found.group(1)) if found else None


# --- Listing -------------------------------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Listed:
    #: Below the series folder, with ``/``, spelled as on disk.
    relative: str
    path: Path
    size: int
    #: The folder below the series folder that holds it; None for a video directly in the series folder.
    folder: str | None
    #: ``sample``, ``extra`` or ``extra_name`` as ``files.scan`` marks it; None for a video that counts.
    skip: str | None


def _entries(folder: Path) -> list[os.DirEntry[str]]:
    try:
        with os.scandir(folder) as found:
            return sorted(found, key=lambda entry: entry.name)
    except OSError:
        return []


def _video(entry: os.DirEntry[str], relative: str, folder: str | None) -> Listed | None:
    name = entry.name
    if name.startswith(".") or files.extension_of(name) not in files.VIDEO_EXTENSIONS:
        return None
    try:
        if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
            return None
        size = entry.stat(follow_symlinks=False).st_size
    except OSError:
        return None
    skip = files._skip_reason(name, candidate=True, series=True)
    return Listed(relative=relative, path=Path(entry.path), size=size, folder=folder, skip=skip)


def list_videos(series_folder: Path) -> list[Listed]:
    """The videos of a series folder and of its direct subfolders, by path."""
    found: list[Listed] = []
    for entry in _entries(series_folder):
        if len(found) >= MAX_VIDEOS:
            logger.warning("A series folder holds more than %d videos; the rest is not looked at", MAX_VIDEOS)
            break
        try:
            is_folder = entry.is_dir(follow_symlinks=False) and not entry.is_symlink()
        except OSError:
            continue
        if not is_folder:
            item = _video(entry, entry.name, None)
            if item is not None:
                found.append(item)
            continue
        if files._left_out_folder(entry.name):
            continue
        for inner in _entries(Path(entry.path)):
            item = _video(inner, f"{entry.name}/{inner.name}", entry.name)
            if item is not None:
                found.append(item)
    return sorted(found, key=lambda item: item.relative)[:MAX_VIDEOS]


# --- Deciding ------------------------------------------------------------------------------------------------------- #


@dataclass
class Found:
    """One counted video and what reading it gave."""

    listed: Listed
    episode_ids: tuple[int, ...] = ()
    #: ``companion`` or the numbering that matched (``tmdb``, ``tvdb``, ``scene``, ``air_date``); None without.
    via: str | None = None
    #: Why it does not link by itself; None when it links.
    reason: str | None = None
    season: int | None = None
    numbers: tuple[int, ...] = ()
    #: The code of numbers the series does not have (``S02E13``).
    unknown: str | None = None
    entry: dict[str, Any] | None = None
    answer: media.Read | None = None
    quality: str | None = None
    quality_from: str = "name"
    languages: list[str] = field(default_factory=list)
    group: str | None = None

    def read_as(self) -> dict[str, Any]:
        return {
            "season": self.season,
            "episodes": list(self.numbers),
            "numbering": self.via,
            "reason": self.reason,
        }


def _parse(
    stem: str, titles: tuple[str, ...], language: str | None, folder_season: int | None, anime: bool = False
) -> Any:
    forms = episode_reading.episode_forms(anime)
    parsed = releases.parse_series(stem, language, titles=titles, anime=anime)
    if parsed.series.form not in forms and titles:
        prefixed = releases.parse_series(f"{titles[0]} {stem}", language, titles=titles, anime=anime)
        if prefixed.series.form in forms:
            return prefixed
        bare = _BARE_EPISODE.match(stem)
        if bare is not None and folder_season is not None:
            return releases.parse_series(
                f"{titles[0]} S{folder_season:02d}E{int(bare.group(1)):02d}", language, titles=titles
            )
    return parsed


def read_name(item: Found, numbering: release_match.Numbering, titles: tuple[str, ...], language: str | None) -> None:
    """Fill ``item`` from the video's name."""
    folder_season = season_of_folder(item.listed.folder) if item.listed.folder else None
    stem = PurePosixPath(item.listed.relative).stem
    anime = numbering.series_type == "anime"
    parsed = _parse(stem, titles, language, folder_season, anime)
    series = parsed.series
    item.group = series.group
    if series.refused is not None or series.form not in episode_reading.episode_forms(anime):
        item.reason = "no_numbers"
        return
    item.season = series.season
    # An anime name counted through carries its numbers as ``absolute`` (A5).
    item.numbers = tuple(series.episodes or series.absolute)
    # A file already on the disk is read without the scene's count through, as Sonarr's disk scan does (B6).
    found = release_match.match(numbering, series, scene=False)
    item.via = found.via
    if not found.episode_ids:
        if series.form in ("standard", "multi_episode") and item.numbers and min(item.numbers) > 0:
            season = f"S{series.season:02d}" if series.season is not None else ""
            item.unknown = season + "".join(f"E{number:02d}" for number in item.numbers)
        item.reason = "unknown_numbers"
        return
    item.episode_ids = tuple(found.episode_ids)
    if found.ambiguous:
        item.reason = "ambiguous"
    elif found.missing:
        item.reason = "unknown_numbers"


def _key(text: str) -> str:
    """A name compared without case: for ``release.nex`` entries and folder names, never for a stored path."""
    return unicodedata.normalize("NFC", text).replace("\\", "/").casefold()


def _path_key(text: str) -> str:
    """A stored path compared in NFC only: on a case sensitive disk ``A.mkv`` and ``a.mkv`` are two files."""
    return unicodedata.normalize("NFC", text).replace("\\", "/")


def companion_entries(folder: Path, installation: str, label: str) -> dict[str, dict[str, Any]]:
    """The entries of a folder's ``release.nex`` by file name: the version's own entry, else a file's only entry."""
    outcome = companions_series.read(folder, installation)
    if outcome["outcome"] not in (companions.OURS, companions.OTHER_INSTALLATION):
        return {}
    by_file: dict[str, list[dict[str, Any]]] = {}
    for entry in outcome.get("entries") or []:
        name = entry.get("file")
        if isinstance(name, str) and name:
            by_file.setdefault(_key(name), []).append(entry)
    chosen: dict[str, dict[str, Any]] = {}
    for name, entries in by_file.items():
        own = [entry for entry in entries if entry.get("version") == label]
        if len(own) == 1:
            chosen[name] = own[0]
        elif len(entries) == 1:
            chosen[name] = entries[0]
    return chosen


def from_entry(item: Found, entry: dict[str, Any], by_tmdb: dict[int, int]) -> bool:
    """Fill ``item`` from a ``release.nex`` entry; False when an episode of it is not the series' (read the name)."""
    ids: list[int] = []
    for episode in entry.get("episodes") or []:
        tmdb_id = episode.get("tmdb_episode_id") if isinstance(episode, dict) else None
        if isinstance(tmdb_id, bool) or not isinstance(tmdb_id, int) or tmdb_id not in by_tmdb:
            return False
        ids.append(by_tmdb[tmdb_id])
    if not ids:
        return False
    item.episode_ids = tuple(dict.fromkeys(ids))
    item.via = "companion"
    item.entry = entry
    return True


def decide(found: list[Found], linked: set[int], rank: Any) -> None:
    """The rules that need every video: a season folder that counts otherwise, episodes with a file, duplicates."""
    otherwise = {item.listed.folder for item in found if item.unknown is not None and item.listed.folder is not None}
    for item in found:
        if item.reason is None and item.via != "companion" and item.listed.folder in otherwise:
            item.reason = "counted_otherwise"

    def half(item: Found) -> int:
        # The half of a double episode a file is; 0 for a whole file.
        return (part_of(item.listed.relative, item.entry) or 0) if len(item.episode_ids) == 1 else 0

    for item in found:
        # A second half does not need the episode free: it stands beside the first (the store checks it is alone).
        if item.reason is None and set(item.episode_ids) & linked and half(item) != 2:
            item.reason = "episode_has_file"
    candidates = [item for item in found if item.reason is None]

    def order(item: Found) -> tuple[Any, ...]:
        ranked = rank(item) if rank is not None else ()
        # release.nex before a name; then the profile, then the size.
        return (0 if item.via == "companion" else 1, tuple(-value for value in ranked), -item.listed.size)

    taken: dict[int, set[int]] = {}
    for item in sorted(candidates, key=lambda entry: (order(entry), entry.listed.relative)):
        piece = half(item)
        if any(
            taken.get(episode_id) and (piece == 0 or taken[episode_id] & {0, piece}) for episode_id in item.episode_ids
        ):
            item.reason = "duplicate"
            continue
        for episode_id in item.episode_ids:
            taken.setdefault(episode_id, set()).add(piece)


# --- Reading one version -------------------------------------------------------------------------------------------- #


@dataclass
class Outcome:
    #: ``done``, ``not_own``, ``no_folder`` (nothing to read), ``failed``.
    state: str
    linked: int = 0
    unclear: int = 0
    skipped: int = 0
    #: Unclear or left-out files no longer on disk, removed from the version.
    removed: int = 0


def _visible(path: str | None) -> Path | None:
    if not path:
        return None
    try:
        folder, _mount = folders.visible(path)
    except folders.NotVisible:
        return None
    return folder if folder.is_dir() else None


def _facts(title: Title) -> naming_series.SeriesFacts:
    return naming_series.SeriesFacts(
        title=title.title or title.title_en or title.original_title or "",
        year=title.year,
        tmdb_id=title.tmdb_id,
        tvdb_id=title.tvdb_id,
        imdb_id=title.imdb_id,
    )


def _lookup(base: Path, name: str) -> Path | None:
    """A folder below ``base`` by name, compared in NFC and without case; None when there is none or it is a link."""
    wanted = unicodedata.normalize("NFC", name).casefold()
    for entry in _entries(base):
        if unicodedata.normalize("NFC", entry.name).casefold() != wanted:
            continue
        try:
            if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                return None
        except OSError:
            return None
        return Path(entry.path)
    return None


def _taken(db: OrmSession, version: Version, root: str, name: str) -> bool:
    """Another version of the title uses this series folder already."""
    for other in db.scalars(select(Version).where(Version.title_id == version.title_id, Version.id != version.id)):
        if (
            other.relative_path
            and other.root_folder
            and _key(other.relative_path) == _key(name)
            and _key(other.root_folder.rstrip("/\\")) == _key(root.rstrip("/\\"))
        ):
            return True
    return False


def find_folder(
    db: OrmSession,
    definition: VersionDefinition,
    facts: naming_series.SeriesFacts,
    taken: Any = None,
) -> tuple[Path, str] | None:
    """The series folder a version of this definition would take (decision 31): the folder its naming gives below the
    definition's folder, else the one series folder the last scan matched to the series. ``taken(root, name)`` says a
    folder is used by another version already. Nothing is staged."""
    base = _visible(definition.folder)
    if base is not None:
        name = naming_series.series_folder_name(naming_series.for_version(db, definition), facts)
        folder = _lookup(base, name) if name and "/" not in name else None
        if folder is not None and not (taken is not None and taken(str(base), folder.name)):
            return base, folder.name
    if facts.tmdb_id is None:
        return None
    rows = list(
        db.execute(
            select(DiskFolder, DiskRoot.path)
            .join(DiskRoot, DiskRoot.id == DiskFolder.root_id)
            .where(
                DiskRoot.kind == "series",
                DiskFolder.tmdb_id == facts.tmdb_id,
                DiskFolder.ignored.is_(False),
                DiskFolder.version_id.is_(None),
            )
        ).tuples()
    )
    places = []
    for row, root_path in rows:
        root = _visible(root_path)
        if root is None or "/" in row.relative_path or (taken is not None and taken(str(root), row.relative_path)):
            continue
        folder = _lookup(root, row.relative_path)
        if folder is not None and (root, folder.name) not in places:
            places.append((root, folder.name))
    return places[0] if len(places) == 1 else None


def locate(db: OrmSession, version: Version) -> tuple[Path, str] | None:
    """``find_folder`` for a version without a series folder; stages ``root_folder`` and ``relative_path``."""
    title = db.get(Title, version.title_id)
    definition = db.get(VersionDefinition, version.version_definition_id)
    if title is None or definition is None:
        return None
    found = find_folder(db, definition, _facts(title), lambda root, name: _taken(db, version, root, name))
    if found is not None:
        version.root_folder, version.relative_path = str(found[0]), found[1]
    return found


def claim(db: OrmSession, version: Version, moment: Any) -> bool:
    """A series version of nexcrate's own that just came to be (added, or a version added to a series): it is
    nexcrate's from now on; with a series folder on disk it waits for the read, without one it counts as read at once.
    Returns whether the caller enqueues it after its commit."""
    version.own_since = moment
    if version.relative_path or locate(db, version) is not None:
        version.files_read_at = None
        return True
    version.files_read_at = moment
    return False


def videos_in(folder: Path) -> int:
    """How many videos reading this folder would look at."""
    return sum(1 for item in list_videos(folder) if item.skip in (None, episode_reading.SKIP_EXTRA_NAME))


def _series_folder(db: OrmSession, version: Version) -> tuple[Path | None, bool]:
    """The version's series folder and whether it is settled that there is none.

    ⚠️ A folder nexcrate cannot see right now (a share not mounted yet after a start) is **not** "no folder": the
    version keeps waiting, or it would want every episode that already lies there.
    """
    if not version.relative_path:
        located = locate(db, version)
        if located is None:
            return None, True
        return located[0] / located[1], False
    if "/" in version.relative_path or "\\" in version.relative_path:
        return None, True
    root = _visible(version.root_folder)
    if root is None:
        return None, False
    found = _lookup(root, version.relative_path)
    # The root folder is there and the series folder is not: it is gone, and nothing can be read.
    return (found, False) if found is not None else (None, True)


#: The end of a name that says which half of a double episode a file is: `` - pt1``, ``.pt2``, Plex's way.
_PART_NAME = re.compile(r"[ ._-]pt([12])$", re.IGNORECASE)


def part_of(relative: str, entry: dict[str, Any] | None) -> int | None:
    """The half of a double episode a file is: what its ``release.nex`` entry says, else
    the end of its name; None for a whole file."""
    if entry is not None and entry.get("part") in (1, 2):
        return int(entry["part"])
    found = _PART_NAME.search(PurePosixPath(relative).stem)
    return int(found.group(1)) if found else None


def _finish(db: OrmSession, version: Version, moment: Any) -> None:
    version.files_read_at = moment
    version.updated_at = moment
    # The second halves of double episodes the reading could not place (D4).
    from . import halves

    db.flush()
    halves.link(db, version)
    watching.recount(db, version, watching.today())
    from ..automatic import clock as automatic_clock
    from ..automatic import planning as automatic_planning

    db.flush()
    automatic_planning.replan(db, [version.title_id], automatic_clock.now())


def read_version(version_id: int, progress: Any = None) -> Outcome:
    """Read the series folder of one version of nexcrate's own. Never raises; see the module's docstring."""
    try:
        return _read_version(version_id, progress)
    except Exception:
        logger.exception("Reading the series folder of version %d failed", version_id)
        return Outcome("failed")


def read_tracked(version_id: int) -> Outcome:
    """``read_version`` registered like a queued read, so a second one (the button, the worker) does not start while
    it runs. ``busy`` when one runs already."""
    with _lock:
        if version_id in _states:
            return Outcome("busy")
        _states[version_id] = Reading(version_id, state="reading")

    def progress(done: int, total: int) -> None:
        reading = _states.get(version_id)
        if reading is not None:
            reading.done, reading.total = done, total

    try:
        return read_version(version_id, progress)
    finally:
        with _lock:
            _states.pop(version_id, None)


def _drop_gone(db: OrmSession, version: Version, folder: Path) -> int:
    """Remove the version's unclear and left-out files that are no longer in the series folder. Returns how many."""
    linked = set(
        db.scalars(
            select(EpisodeVersion.episode_file_id).where(
                EpisodeVersion.version_id == version.id, EpisodeVersion.episode_file_id.is_not(None)
            )
        )
    )
    gone = [
        row
        for row in db.scalars(select(EpisodeFile).where(EpisodeFile.version_id == version.id))
        if row.id not in linked and not (folder / row.relative_path).exists()
    ]
    for row in gone:
        db.delete(row)
    if gone:
        db.flush()
        logger.info("Version %d: %d unclear or left-out files no longer on disk removed", version.id, len(gone))
    return len(gone)


def _read_version(version_id: int, progress: Any) -> Outcome:
    moment = utcnow()
    with SessionLocal() as db:
        version = db.get(Version, version_id)
        if version is None or version.source_id is not None:
            return Outcome("not_own")
        title = db.get(Title, version.title_id)
        definition = db.get(VersionDefinition, version.version_definition_id)
        if title is None or title.kind != "series" or definition is None:
            return Outcome("not_own")
        folder, settled = _series_folder(db, version)
        if folder is None:
            if settled:
                _finish(db, version, moment)
                db.commit()
                return Outcome("no_folder")
            logger.info("The series folder of version %d is not visible right now; it stays unread", version_id)
            return Outcome("not_visible")
        gone = _drop_gone(db, version, folder)
        known = {
            _path_key(path)
            for path in db.scalars(select(EpisodeFile.relative_path).where(EpisodeFile.version_id == version.id))
        }
        numbering = release_match.load(db, title)
        titles, _keys = series_import._titles(db, title)
        language = title.original_language
        by_tmdb = {
            tmdb_id: episode_id
            for episode_id, tmdb_id in db.execute(
                select(Episode.id, Episode.tmdb_episode_id).where(
                    Episode.title_id == title.id, Episode.tmdb_gone_at.is_(None)
                )
            ).tuples()
            if tmdb_id is not None
        }
        linked = set(
            db.scalars(
                select(EpisodeVersion.episode_id).where(
                    EpisodeVersion.version_id == version.id, EpisodeVersion.episode_file_id.is_not(None)
                )
            )
        )
        profile = profile_store.of_version(db, definition.id)
        series_type = db.scalar(select(Title.series_type).where(Title.id == version.title_id))
        rules = profile_store.series_rules(profile, series_type)
        installation = companions.installation_id(db)
        label = definition.label
        root_folder, series_name = version.root_folder, version.relative_path
        db.commit()

    listed = [item for item in list_videos(folder) if _path_key(item.relative) not in known]
    if listed:
        # ⚠️ TheXEM first (measured on the bench on 17.09.2026): a library named after TVDB's numbers (Sonarr's
        # names) reads as unclear when TMDB counts the seasons differently and nothing bridges the numbers.
        numbering = _with_xem(version.title_id, numbering)
    counting = [item for item in listed if item.skip in (None, episode_reading.SKIP_EXTRA_NAME)]
    left_aside: dict[str, int] = {}
    for item in listed:
        if item.skip not in (None, episode_reading.SKIP_EXTRA_NAME):
            left_aside[item.skip] = left_aside.get(item.skip, 0) + 1
    entries: dict[str | None, dict[str, dict[str, Any]]] = {}
    found: list[Found] = []
    total = len(counting)
    for index, item in enumerate(counting, start=1):
        if item.folder not in entries:
            place = folder / item.folder if item.folder else folder
            entries[item.folder] = companion_entries(place, installation, label)
        current = Found(listed=item)
        entry = entries[item.folder].get(_key(PurePosixPath(item.relative).name))
        if entry is None or not from_entry(current, entry, by_tmdb):
            read_name(current, numbering, titles, language)
        if item.skip == episode_reading.SKIP_EXTRA_NAME and not current.episode_ids:
            # A name like "Featurette" without an episode is an extra.
            left_aside["extra"] = left_aside.get("extra", 0) + 1
            if progress is not None:
                progress(index, total)
            continue
        _measure(current, language)
        if _is_sample(current, numbering):
            left_aside["sample"] = left_aside.get("sample", 0) + 1
            if progress is not None:
                progress(index, total)
            continue
        found.append(current)
        if progress is not None:
            progress(index, total)

    def rank(item: Found) -> tuple[int, ...]:
        if rules is None or rules.get("kind") != "series":
            return ()
        _quality, place, score = series_decision.file_standing(rules, _current(item, None), language)
        return (place, score)

    decide(found, linked, rank)
    outcome = _store(version_id, root_folder, series_name, found, rules, language, moment)
    outcome.skipped = len(listed) - len(found)
    outcome.removed = gone
    if outcome.skipped:
        # ⚠️ Say it (found on the bench on 17.09.2026): a folder whose videos are all left aside read as "0 files"
        # with nothing anywhere saying why, and a folder of samples looked exactly like an empty one.
        rest = outcome.skipped - sum(left_aside.values())
        if rest > 0:
            left_aside["other"] = rest
        logger.info(
            "Series folder of version %d: %d of %d videos left aside (%s)",
            version_id,
            outcome.skipped,
            len(listed),
            ", ".join(f"{reason} {count}" for reason, count in sorted(left_aside.items())),
        )
    return outcome


def _with_xem(title_id: int, numbering: release_match.Numbering) -> release_match.Numbering:
    """TheXEM's numbers of the series, fetched when due, then the numbering read again. Never raises."""
    try:
        from . import xem

        if asyncio.run(xem.refresh_title(title_id)) is None:
            return numbering
        with SessionLocal() as db:
            title = db.get(Title, title_id)
            return release_match.load(db, title) if title is not None else numbering
    except Exception:
        logger.exception("TheXEM could not be asked before reading the folder of title %d", title_id)
        return numbering


def _measure(item: Found, language: str | None) -> None:
    entry = item.entry
    stem = PurePosixPath(item.listed.relative).stem
    folder_name = item.listed.folder
    if entry is not None and entry.get("size_bytes") == item.listed.size:
        item.quality = entry.get("quality") if isinstance(entry.get("quality"), str) else None
        item.quality_from = entry.get("quality_from") if entry.get("quality_from") in ("name", "media") else "name"
        item.languages = [value for value in entry.get("languages") or [] if isinstance(value, str)]
        item.group = entry.get("release_group") if isinstance(entry.get("release_group"), str) else item.group
        return
    answer = series_import.read_media(item.listed.path)
    item.answer = answer
    name = (entry or {}).get("release_title") if isinstance((entry or {}).get("release_title"), str) else stem
    measured = series_import.media_quality(name, language, answer, folder_name, stem)
    item.quality, item.quality_from = measured.quality, measured.quality_from
    item.languages = media.languages_of(answer.media, name, language)
    if entry is not None and isinstance(entry.get("release_group"), str):
        item.group = entry["release_group"]


def _is_sample(item: Found, numbering: release_match.Numbering) -> bool:
    if item.answer is None or item.answer.media is None or not item.episode_ids:
        return False
    value = item.answer.media.get("duration_seconds")
    duration = int(value) if isinstance(value, int | float) and value > 0 else None
    rows = [numbering.episodes[episode_id] for episode_id in item.episode_ids if episode_id in numbering.episodes]
    if not rows or all(row.season == 0 for row in rows):
        return False
    runtime = sum((row.runtime or numbering.runtime_min or 0) for row in rows)
    return episode_reading.is_sample(duration, runtime or None, special=False)


def _current(item: Found, file_id: int | None) -> releases.CurrentEpisodeFile:
    entry = item.entry or {}
    release_title = entry.get("release_title") if isinstance(entry.get("release_title"), str) else None
    return releases.CurrentEpisodeFile(
        release_title=release_title,
        name=item.listed.relative,
        quality=item.quality,
        release_type="multiEpisode" if len(item.episode_ids) > 1 else "singleEpisode",
        size_bytes=as_whole(item.listed.size, part_of(item.listed.relative, item.entry)),
        episode_count=max(1, len(item.episode_ids)),
        file_id=file_id,
        languages=releases.stored_languages(item.languages),
    )


def _store(
    version_id: int,
    root_folder: str | None,
    series_name: str | None,
    found: list[Found],
    rules: dict[str, Any] | None,
    language: str | None,
    moment: Any,
) -> Outcome:
    outcome = Outcome("done")
    seasons: set[int] = set()
    for start in range(0, max(len(found), 1), BATCH):
        chunk = found[start : start + BATCH]
        with SessionLocal() as db:
            version = db.get(Version, version_id)
            if version is None or version.source_id is not None:
                return Outcome("not_own")
            if version.root_folder != root_folder or version.relative_path != series_name:
                logger.info("The series folder of version %d changed while it was read; reading stops", version_id)
                return Outcome("failed")
            known = {
                _path_key(path)
                for path in db.scalars(select(EpisodeFile.relative_path).where(EpisodeFile.version_id == version.id))
            }
            links = {
                row.episode_id: row
                for row in db.scalars(select(EpisodeVersion).where(EpisodeVersion.version_id == version.id))
            }
            folders_known = {
                row.season_number
                for row in db.scalars(select(SeasonFolder).where(SeasonFolder.version_id == version.id))
            }
            seconds = set(
                db.scalars(
                    select(EpisodeFile.part_of_episode_id).where(
                        EpisodeFile.version_id == version.id, EpisodeFile.part == 2
                    )
                )
            )
            for item in chunk:
                if _path_key(item.listed.relative) in known:
                    continue
                entry = item.entry or {}
                ids = [episode_id for episode_id in item.episode_ids if episode_id in links]
                part = part_of(item.listed.relative, item.entry) if len(ids) == 1 == len(item.episode_ids) else None
                # A second half does not take the episode's link: it names its episode itself, once.
                second = item.reason is None and part == 2 and ids[0] not in seconds
                links_free = (
                    item.reason is None
                    and len(ids) == len(item.episode_ids)
                    and all(links[episode_id].episode_file_id is None for episode_id in ids)
                )
                if item.reason is None and not links_free and not second:
                    item.reason = "episode_has_file"
                row = EpisodeFile(
                    version_id=version.id,
                    relative_path=item.listed.relative[:2048],
                    size=item.listed.size,
                    quality=item.quality,
                    quality_from=item.quality_from,
                    languages=list(item.languages),
                    release_group=(item.group or None) and item.group[:200],
                    release_title=entry.get("release_title")[:1024]
                    if isinstance(entry.get("release_title"), str)
                    else None,
                    release_type="multiEpisode" if len(item.episode_ids) > 1 else "singleEpisode",
                    media_info=item.answer.media if item.answer is not None else None,
                    file_ref=FILE_REF,
                    name_numbering=entry.get("numbering") if entry.get("numbering") in ("tmdb", "tvdb") else None,
                    read_as=item.read_as(),
                    added_at=moment,
                    updated_at=moment,
                )
                if item.reason is None and part in (1, 2):
                    row.part = part
                    row.part_of_episode_id = ids[0] if second else None
                db.add(row)
                db.flush()
                known.add(_path_key(item.listed.relative))
                if item.reason is not None:
                    outcome.unclear += 1
                    continue
                if rules is not None and rules.get("kind") == "series":
                    judged = series_decision.judge_episode_file(rules, _current(item, row.id), language)
                    row.cutoff_not_met = judged is not None and judged.reason is not None
                if second:
                    seconds.add(ids[0])
                else:
                    for episode_id in item.episode_ids:
                        links[episode_id].episode_file_id = row.id
                outcome.linked += 1
                season_numbers = {
                    number
                    for (number,) in db.execute(
                        select(Episode.season_number).where(Episode.id.in_(list(item.episode_ids)))
                    ).tuples()
                }
                if len(season_numbers) == 1 and item.listed.folder:
                    (number,) = season_numbers
                    seasons.add(number)
                    named = season_of_folder(item.listed.folder)
                    # ⚠️ The name of a season folder is fixed the first time (S4, decision 15): a file that lies in
                    # "Season 02" but holds an episode of season 1 must not make it the folder of season 1.
                    if number not in folders_known and named in (None, number):
                        db.add(SeasonFolder(version_id=version.id, season_number=number, name=item.listed.folder))
                        folders_known.add(number)
            db.commit()
    with SessionLocal() as db:
        version = db.get(Version, version_id)
        if version is None or version.source_id is not None:
            return Outcome("not_own")
        if outcome.linked or outcome.unclear:
            definition = db.get(VersionDefinition, version.version_definition_id)
            db.add(
                HistoryEntry(
                    title_id=version.title_id,
                    version_id=version.id,
                    version_definition_id=version.version_definition_id,
                    version_label=definition.label if definition is not None else "",
                    event="found_on_disk",
                    at=moment,
                    detail=f"{outcome.linked} {outcome.unclear}",
                )
            )
        _finish(db, version, moment)
        db.commit()
    if seasons:
        companions_series.write_version(version_id, seasons)
    logger.info(
        "Series folder of version %d read: %d files linked, %d unclear", version_id, outcome.linked, outcome.unclear
    )
    return outcome


# --- The worker ----------------------------------------------------------------------------------------------------- #


@dataclass
class Reading:
    version_id: int
    state: str = "queued"
    done: int = 0
    total: int | None = None


_lock = threading.Lock()
_queue: queue.Queue[int] = queue.Queue()
_states: dict[int, Reading] = {}
#: Versions whose read is followed by ``keep_as_is``.
_keep: set[int] = set()
_worker: dict[str, threading.Thread | None] = {"thread": None}


def keep_as_is(version_ids: list[int] | set[int]) -> None:
    """What the read of these versions finds stays as it is (``services/keep_as_is.py``): a version queued or being
    read is kept once its read is done, one read already at once. ⚠️ Held in memory: a restart before the read forgets
    it, and the read finds the files as usual."""
    now: list[int] = []
    with _lock:
        for version_id in set(version_ids):
            if version_id in _states:
                _keep.add(version_id)
            else:
                now.append(version_id)
    if now:
        _keep_now(now)


def _keep_now(version_ids: list[int]) -> None:
    from .. import keep_as_is as keeping

    with SessionLocal() as db:
        kept = keeping.apply(db, version_ids, utcnow())
        db.commit()
    if kept.episodes:
        logger.info("Series versions %s keep what their folder holds: %d episodes", version_ids, kept.episodes)


def enqueue(version_ids: list[int] | set[int]) -> None:
    """Read these versions one after another in the worker thread; a version queued or read already is not queued
    again."""
    with _lock:
        for version_id in sorted(set(version_ids)):
            if version_id in _states:
                continue
            _states[version_id] = Reading(version_id)
            _queue.put(version_id)
        thread = _worker["thread"]
        if thread is None or not thread.is_alive():
            thread = threading.Thread(
                target=contextvars.copy_context().run, args=(_work,), name="series-folder-read", daemon=True
            )
            _worker["thread"] = thread
            thread.start()


def _work() -> None:
    while True:
        try:
            version_id = _queue.get(timeout=1.0)
        except queue.Empty:
            with _lock:
                if _queue.empty():
                    _worker["thread"] = None
                    return
            continue
        state = _states.get(version_id)
        if state is not None:
            state.state = "reading"

        def progress(done: int, total: int, reading: Reading | None = state) -> None:
            if reading is not None:
                reading.done, reading.total = done, total

        try:
            read_version(version_id, progress)
            with _lock:
                keep = version_id in _keep
                _keep.discard(version_id)
            if keep:
                _keep_now([version_id])
        finally:
            with _lock:
                _states.pop(version_id, None)
                _keep.discard(version_id)
            _queue.task_done()


def state_of(version_id: int) -> dict[str, Any] | None:
    """``{"state": "queued"|"reading", "done", "total"}`` while a version waits or is read; None otherwise."""
    with _lock:
        reading = _states.get(version_id)
        if reading is None:
            return None
        return {"state": reading.state, "done": reading.done, "total": reading.total}


def wait_idle(timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with _lock:
            if not _states:
                return True
        time.sleep(0.02)
    return False


def resume() -> int:
    """At start: every series version of nexcrate's own that is not read yet. Returns how many were queued."""
    with SessionLocal() as db:
        ids = list(
            db.scalars(
                select(Version.id)
                .join(VersionDefinition, VersionDefinition.id == Version.version_definition_id)
                .where(
                    VersionDefinition.kind == "series",
                    Version.source_id.is_(None),
                    Version.files_read_at.is_(None),
                )
            )
        )
    if ids:
        enqueue(ids)
        logger.info("%d series folders are read after the start", len(ids))
    return len(ids)
