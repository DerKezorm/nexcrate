"""``release.nex`` in every season folder of a series version nexcrate files into (S4.7).

The same file as next to a movie, with a ``series`` block instead of ``movie`` and one entry per episode file and
version, with its episodes in the numbering of the name. ``format_version`` stays 1: a reader tells the kind by the
block. The rules of L1 and L2 hold: built from the database only, written safely under a temporary name, a file changed
by hand (its hash differs from the one written) is never overwritten, a file of another installation neither, one
writer per folder. Limits: 1,000 entries and 1 MiB (daily shows).

Written after filing, after replacing and after renaming a TBA file. Filling it in for files from elsewhere follows
with S6. Nothing here raises to the caller: a failure becomes the season folder's ``companion_state``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from .. import __version__
from ..db import SessionLocal
from ..models import (
    Download,
    Episode,
    EpisodeFile,
    EpisodeNumber,
    EpisodeVersion,
    ExtraFile,
    SeasonFolder,
    Title,
    Version,
    VersionDefinition,
    utcnow,
)
from . import companions, folders
from .downloads import files

logger = logging.getLogger("nexcrate.companions")

MAX_BYTES = 1024 * 1024
MAX_ENTRIES = 1_000


def _series_block(title: Title) -> dict[str, Any]:
    return {
        "tmdb_id": title.tmdb_id,
        "tvdb_id": title.tvdb_id,
        "imdb_id": title.imdb_id if title.imdb_id and title.imdb_id.startswith("tt") else None,
        "title": title.title or None,
        "year": title.year,
    }


def read(folder: Path, installation: str) -> dict[str, Any]:
    """What a season folder's file is: ``missing``, ``ours`` (with ``entries`` and ``sha256``), ``other_installation``,
    ``newer_format``, ``broken`` or ``foreign``. Never raises; input from outside is checked, no path followed."""
    path = folder / companions.FILE_NAME
    try:
        info = path.lstat()
    except OSError:
        return {"outcome": companions.MISSING}
    if path.is_symlink() or not path.is_file() or info.st_size > MAX_BYTES:
        return {"outcome": companions.FOREIGN}
    try:
        raw = path.read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except OSError, UnicodeDecodeError, ValueError:
        return {"outcome": companions.FOREIGN}
    digest = hashlib.sha256(raw).hexdigest()
    if not isinstance(data, dict) or data.get("format") != companions.FORMAT:
        return {"outcome": companions.FOREIGN, "sha256": digest}
    version = data.get("format_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        return {"outcome": companions.BROKEN, "sha256": digest}
    if version > companions.FORMAT_VERSION:
        return {"outcome": companions.NEWER_FORMAT, "sha256": digest}
    if not isinstance(data.get("series"), dict) or not isinstance(data.get("entries"), list):
        return {"outcome": companions.BROKEN, "sha256": digest}
    written_by = data.get("written_by") if isinstance(data.get("written_by"), dict) else {}
    outcome = companions.OURS if written_by.get("installation") == installation else companions.OTHER_INSTALLATION
    entries = [entry for entry in data["entries"][:MAX_ENTRIES] if isinstance(entry, dict)]
    return {"outcome": outcome, "sha256": digest, "entries": entries, "series": data["series"]}


def _came_from(download: Download | None) -> dict[str, Any] | None:
    if download is None:
        return None
    return {
        "kind": "download",
        "protocol": download.protocol,
        "indexer": download.indexer_name or None,
        "grabbed_at": companions._iso(download.grabbed_at),
        "imported_at": companions._iso(download.imported_at),
    }


def entries_of(db: OrmSession, version: Version, label: str, season_name: str) -> list[dict[str, Any]]:
    """One entry per episode file of the version in this season folder, from the database only."""
    rows = [
        row
        for row in db.scalars(select(EpisodeFile).where(EpisodeFile.version_id == version.id))
        if "/".join(row.relative_path.split("/")[:-1]) == season_name
    ]
    if not rows:
        return []
    ids = [row.id for row in rows]
    links: dict[int, list[tuple[Episode, bool]]] = defaultdict(list)
    for link, episode in db.execute(
        select(EpisodeVersion, Episode)
        .join(Episode, Episode.id == EpisodeVersion.episode_id)
        .where(EpisodeVersion.version_id == version.id, EpisodeVersion.episode_file_id.in_(ids))
    ).tuples():
        links[int(link.episode_file_id)].append((episode, bool(link.watched)))
    # The second half of a double episode names its episode itself.
    watched = {
        link.episode_id: bool(link.watched)
        for link in db.scalars(select(EpisodeVersion).where(EpisodeVersion.version_id == version.id))
    }
    for row in rows:
        if row.part == 2 and row.part_of_episode_id is not None:
            episode = db.get(Episode, row.part_of_episode_id)
            if episode is not None:
                links[row.id].append((episode, watched.get(episode.id, False)))
    tvdb = {
        number.episode_id: (number.season, number.episode)
        for number in db.scalars(
            select(EpisodeNumber).where(EpisodeNumber.title_id == version.title_id, EpisodeNumber.scheme == "tvdb")
        )
    }
    subtitles: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for extra in db.scalars(select(ExtraFile).where(ExtraFile.episode_file_id.in_(ids))):
        subtitles[int(extra.episode_file_id or 0)].append(
            {
                "file": extra.relative_path.rsplit("/", 1)[-1],
                "language": extra.language,
                "forced": bool(extra.forced),
                "sdh": bool(extra.sdh),
            }
        )
    entries = []
    for row in sorted(rows, key=lambda item: item.relative_path):
        episodes = sorted(links.get(row.id, []), key=lambda pair: (pair[0].season_number, pair[0].episode_number))
        numbering = row.name_numbering or "tmdb"
        download = None
        if (row.file_ref or "").startswith("nexcrate:") and row.file_ref[9:].isdigit():
            download = db.get(Download, int(row.file_ref[9:]))
        entry: dict[str, Any] = {}
        if row.part in (1, 2):
            entry["part"] = row.part
        entries.append(
            {
                **entry,
                "version": label,
                "file": row.relative_path.rsplit("/", 1)[-1],
                "episodes": [
                    {
                        "tmdb_episode_id": episode.tmdb_episode_id,
                        "season": tvdb.get(episode.id, (episode.season_number, 0))[0]
                        if numbering == "tvdb" and episode.id in tvdb
                        else episode.season_number,
                        "episode": tvdb.get(episode.id, (0, episode.episode_number))[1]
                        if numbering == "tvdb" and episode.id in tvdb
                        else episode.episode_number,
                    }
                    for episode, _watched in episodes
                ],
                "numbering": numbering,
                "size_bytes": int(row.size or 0),
                "quality": row.quality or "Unknown",
                "quality_from": row.quality_from or "name",
                "release_title": row.release_title,
                "release_group": row.release_group,
                "languages": list(row.languages or []),
                "subtitles": sorted(subtitles.get(row.id, []), key=lambda item: item["file"]),
                "came_from": _came_from(download),
                "monitored": all(watched for _episode, watched in episodes) if episodes else False,
            }
        )
    return entries[:MAX_ENTRIES]


def _folder(version: Version, season_name: str) -> Path | None:
    """The season folder, resolved, strictly inside the series folder inside a visible root folder; never a link."""
    series_name = version.relative_path or ""
    if not series_name or "/" in series_name or not season_name or "/" in season_name:
        return None
    try:
        root, _mount = folders.visible(version.root_folder)
    except folders.NotVisible:
        return None
    candidate = root / series_name / season_name
    folder = files.resolved(candidate)
    if folder is None or files.is_link(candidate) or files.is_link(root / series_name) or not folder.is_dir():
        return None
    return folder if files.strictly_inside(folder, root) else None


def _siblings(db: OrmSession, season: SeasonFolder) -> list[SeasonFolder]:
    """The season folders of the version that share this folder on disk, this one included. TVDB and TMDB count
    seasons differently: one folder can hold several of TMDB's seasons (measured 18.09.2026: TMDB 11 to 13 of a series
    in "Season 13", the specials beside season 3). The folder has one ``release.nex``, so they share its hash."""
    return [
        row
        for row in db.scalars(
            select(SeasonFolder).where(SeasonFolder.version_id == season.version_id, SeasonFolder.name == season.name)
        )
    ] or [season]


def _changed(siblings: list[SeasonFolder], sha256: str | None) -> bool:
    """Changed from outside: nexcrate wrote the file, and it is none of the versions nexcrate wrote there."""
    known = {row.companion_hash for row in siblings if row.companion_hash is not None}
    return bool(known) and sha256 not in known


def _store_all(siblings: list[SeasonFolder], state: str, digest: str | None) -> str:
    for row in siblings:
        _store(row, state, digest)
    return state


def classify_season(db: OrmSession, version: Version, season: SeasonFolder, installation: str) -> str:
    """What one season folder's file is, without writing (P4): ``current``, ``missing``,
    ``outdated``, ``changed``, ``file_missing``, ``folder_missing`` or the outcome of a file nexcrate never writes."""
    title = db.get(Title, version.title_id)
    definition = db.get(VersionDefinition, version.version_definition_id)
    folder = _folder(version, season.name)
    if title is None or definition is None:
        return _store(season, "failed", None)
    if folder is None:
        return _store(season, "folder_missing", None)
    existing = read(folder, installation)
    outcome = existing["outcome"]
    if outcome in (companions.OTHER_INSTALLATION, companions.NEWER_FORMAT, companions.BROKEN, companions.FOREIGN):
        return _store(season, outcome, None)
    entries = entries_of(db, version, definition.label, season.name)
    if not entries:
        return _store(season, "file_missing", None)
    if outcome == companions.MISSING:
        return _store(season, companions.MISSING, None)
    if _changed(_siblings(db, season), existing["sha256"]):
        return _store(season, "changed", None)
    merged = entries + [entry for entry in existing["entries"] if entry.get("version") != definition.label]
    ordered = sorted(merged, key=lambda entry: (str(entry.get("version")), str(entry.get("file"))))[:MAX_ENTRIES]
    if existing.get("series") == _series_block(title) and existing["entries"] == ordered:
        return _store(season, "current", existing["sha256"])
    return _store(season, "outdated", None)


def seasons_of(db: OrmSession, version_id: int) -> list[SeasonFolder]:
    return list(db.scalars(select(SeasonFolder).where(SeasonFolder.version_id == version_id)))


def check_version(version_id: int) -> dict[str, int]:
    """Classify every season folder of a series version without writing. Counts per state; never raises."""
    counts: dict[str, int] = {}
    try:
        with SessionLocal() as db:
            version = db.get(Version, version_id)
            if version is None or version.source_id is not None:
                return counts
            installation = companions.installation_id(db)
            for season in seasons_of(db, version.id):
                state = classify_season(db, version, season, installation)
                counts[state] = counts.get(state, 0) + 1
            db.commit()
    except Exception:  # the check never reaches the caller
        logger.exception("Series version %d: checking release.nex failed", version_id)
        counts["failed"] = counts.get("failed", 0) + 1
    return counts


def write_season(db: OrmSession, version: Version, season: SeasonFolder, installation: str) -> str:
    """Write or refresh one season folder's file. Returns the state stored on the season folder and on every season
    folder that shares it on disk."""
    return _write_season(db, version, season, _siblings(db, season), installation)


def _write_season(
    db: OrmSession, version: Version, season: SeasonFolder, siblings: list[SeasonFolder], installation: str
) -> str:
    title = db.get(Title, version.title_id)
    definition = db.get(VersionDefinition, version.version_definition_id)
    folder = _folder(version, season.name)
    if title is None or definition is None:
        return _store_all(siblings, "failed", None)
    if folder is None:
        return _store_all(siblings, "folder_missing", None)
    with companions._lock_for(folder):
        existing = read(folder, installation)
        outcome = existing["outcome"]
        if outcome in (companions.OTHER_INSTALLATION, companions.NEWER_FORMAT, companions.BROKEN, companions.FOREIGN):
            return _store_all(siblings, outcome, None)
        entries = entries_of(db, version, definition.label, season.name)
        if outcome == companions.OURS:
            if _changed(siblings, existing["sha256"]):
                return _store_all(siblings, "changed", None)
            entries.extend(entry for entry in existing["entries"] if entry.get("version") != definition.label)
        if not entries:
            return _store_all(siblings, "file_missing", None)
        series = _series_block(title)
        ordered = sorted(entries, key=lambda entry: (str(entry.get("version")), str(entry.get("file"))))[:MAX_ENTRIES]
        if outcome == companions.OURS and existing.get("series") == series and existing["entries"] == ordered:
            return _store_all(siblings, "current", existing["sha256"])
        doc = {
            "format": companions.FORMAT,
            "format_version": companions.FORMAT_VERSION,
            "written_at": companions._iso(utcnow()),
            "written_by": {"app": "nexcrate", "version": __version__, "installation": installation},
            "series": series,
            # A folder of several TMDB seasons names the first; its entries carry each episode's own season.
            "season": min(row.season_number for row in siblings),
            "entries": ordered,
        }
        data = companions.encode(doc)
        if len(data) > MAX_BYTES:
            return _store_all(siblings, "failed", None)
        if not companions.writable(folder):
            return _store_all(siblings, "not_writable", None)
        try:
            companions.write_bytes(folder, data)
        except companions._WriteFailed as exc:
            return _store_all(siblings, exc.state, None)
    return _store_all(siblings, "written", hashlib.sha256(data).hexdigest())


def _store(season: SeasonFolder, state: str, digest: str | None) -> str:
    season.companion_state = state
    if state in ("written", "current"):
        season.companion_hash = digest
        if state == "written" or season.companion_written_at is None:
            season.companion_written_at = utcnow()
    return state


def write_version(version_id: int, seasons: set[int] | None = None) -> dict[str, int]:
    """Every season folder of a series version (or the given ones), each committed. Counts per state; never raises."""
    counts: dict[str, int] = {}
    try:
        with SessionLocal() as db:
            version = db.get(Version, version_id)
            if version is None or version.source_id is not None or not companions.enabled(db):
                return counts
            installation = companions.installation_id(db)
            done: set[str] = set()
            for season in db.scalars(select(SeasonFolder).where(SeasonFolder.version_id == version.id)):
                if seasons is not None and season.season_number not in seasons:
                    continue
                if season.name in done:
                    # Written with the season that shares its folder.
                    continue
                done.add(season.name)
                state = write_season(db, version, season, installation)
                counts[state] = counts.get(state, 0) + 1
            db.commit()
    except Exception:  # the write never reaches the caller
        logger.exception("Series version %d: writing release.nex failed", version_id)
        counts["failed"] = counts.get("failed", 0) + 1
    if counts and set(counts) - {"written", "current"}:
        logger.info("Series version %d: release.nex %s", version_id, companions._codes(counts))
    return counts
