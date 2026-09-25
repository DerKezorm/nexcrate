"""What lies in nexcrate's category of a download client (the owner's findings 1 and 2 of 22.09.2026).

**The look** runs with the tracking once a minute, and five seconds after a hand-over the client did not answer in
time: every enabled client lists every job in nexcrate's category (``category_jobs``).

* **A hand-over without an answer** (``Download.handed_unsure_at``): on 21.09.2026 SABnzbd took five NZBs and answered
  after more than 10 seconds; nexcrate forgot them, they finished in the category and two were ordered again. Now the
  download is recorded all the same. A SABnzbd job named like the uploaded NZB (the cleaned release name, see
  ``same_name``) that no other download follows becomes its job; a torrent is found by its info hash. When the client
  answers and shows no such job for ``NOT_TAKEN_AFTER``, the download ends as failed with ``not_taken``: its release
  is not blocked, and the replacement searches again.
* **A foreign job** is one no download of nexcrate follows, in any state: put there by hand, by another program, or
  left from before. It is kept in ``foreign_jobs`` while the client lists it and shows under Downloads, Problems, as
  in Radarr's "manual import required": with a proposal from its name, "assign and import" (``adopt``) and "remove"
  (``remove``, with its files). ⚠️ nexcrate never imports a foreign job by itself.
* **An imported download still in SABnzbd** (``clean_after_import``): the import deletes the job folder and takes the
  job out of the history. When that did not happen (an album's ``.m3u`` counted as a video until 22.09.2026), the
  look tries once more per start, ``CLEAN_AFTER`` after the import, with the same checks.

Log lines carry ids and counts, never a name or a path.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select

from ... import crypto
from ...db import SessionLocal
from ...meldungen import meldung
from ...models import Download, DownloadClient, ForeignJob, Title, Version, VersionDefinition
from ...models.downloads import PROTOCOL_OF_KIND
from .. import downloaders, folders, naming, releases
from ..automatic import replacement
from ..schreibweisen import nfc
from . import files, store

logger = logging.getLogger("nexcrate.downloads")

LOOK_EVERY_SECONDS = 60.0
SOON_SECONDS = 5.0
#: How long a client that answers may go without showing a job it was handed, before the download is ``not_taken``.
NOT_TAKEN_AFTER_SECONDS = 10 * 60.0
#: How long after its import a download's SABnzbd job may stay before the look cleans it up: the import does it itself.
CLEAN_AFTER_SECONDS = 10 * 60.0

_lock = threading.Lock()
_next_look: float | None = None
#: Downloads whose job the look tried to clean up since the start: once each, whatever came of it.
_cleaned: set[int] = set()


def clock() -> float:
    return time.monotonic()


def soon() -> None:
    """Look into the categories five seconds from now, after a hand-over without an answer."""
    global _next_look
    with _lock:
        moment = clock() + SOON_SECONDS
        _next_look = moment if _next_look is None else min(_next_look, moment)


def reset() -> None:
    global _next_look
    with _lock:
        _next_look = None


def forget_cleaned() -> None:
    """Only for the tests: a new database starts its download ids at 1 again."""
    with _lock:
        _cleaned.clear()


def due() -> bool:
    with _lock:
        return _next_look is None or clock() >= _next_look


_SEPARATORS = re.compile(r"[\s._\-]+")


def same_name(first: str, second: str) -> bool:
    """Whether two job names are the same release: case, dots, spaces, underscores and dashes aside."""

    def key(name: str) -> str:
        return _SEPARATORS.sub(" ", nfc(name)).strip().casefold()

    return bool(first.strip()) and key(first) == key(second)


# --- The look ------------------------------------------------------------------------------------------------------ #


@dataclass(frozen=True)
class _Client:
    id: int
    kind: str
    #: ⚠️ With the decrypted secret.
    target: downloaders.Target = field(repr=False)


def _clients() -> list[_Client]:
    with SessionLocal() as db:
        found: list[_Client] = []
        for row in db.scalars(
            select(DownloadClient).where(DownloadClient.enabled.is_(True)).order_by(DownloadClient.id)
        ):
            if store.login_blocked(row):
                continue
            target = downloaders.Target(
                kind=row.kind,
                url=row.url,
                username=row.username or "",
                secret=crypto.decrypt(row.secret) if row.secret else "",
                category=row.category,
            )
            found.append(_Client(id=row.id, kind=row.kind, target=target))
    return found


async def look() -> None:
    """List the category of every enabled client and settle what it holds."""
    global _next_look
    with _lock:
        _next_look = clock() + LOOK_EVERY_SECONDS
    for client in await asyncio.to_thread(_clients):
        try:
            async with downloaders.open_client(client.target) as opened:
                listing = await opened.category_jobs()
                left = await asyncio.to_thread(settle, client.id, listing)
                for download_id, job_id, reported in left:
                    if await asyncio.to_thread(clean_after_import, download_id, client.id, reported):
                        await opened.remove_imported(job_id)
                        logger.info("Download %d: the job left the client's history", download_id)
        except downloaders.ClientError as exc:
            logger.info("Download client %d: its category could not be listed: %s", client.id, exc.code)
            continue


def settle(client_id: int, listing: list[downloaders.CategoryJob]) -> list[tuple[int, str, str]]:
    """Hand-overs without an answer find their job or end as ``not_taken``; the rest is kept as foreign jobs."""
    moment = store.now()
    with SessionLocal() as db:
        client = db.get(DownloadClient, client_id)
        if client is None:
            return
        torrent = PROTOCOL_OF_KIND.get(client.kind) == "torrent"

        def key(value: str) -> str:
            return value.lower() if torrent else value

        known = {
            key(value)
            for value in db.scalars(select(Download.client_download_id).where(Download.client_id == client_id))
            if value
        }
        unsure = list(
            db.scalars(
                select(Download)
                .where(Download.client_id == client_id, Download.handed_unsure_at.is_not(None))
                .order_by(Download.id)
            )
        )
        by_id = {key(item.job.download_id): item for item in listing}
        for row in unsure:
            if row.client_download_id and key(row.client_download_id) in by_id:
                _confirmed(row, row.client_download_id)
                continue
            stem = naming.clean_file_name(row.release_title)
            match = next(
                (
                    item
                    for item in listing
                    if key(item.job.download_id) not in known
                    and (same_name(item.name, stem) or same_name(item.name, row.release_title))
                ),
                None,
            )
            if match is not None:
                known.add(key(match.job.download_id))
                _confirmed(row, match.job.download_id)
                continue
            waited = (moment - row.handed_unsure_at).total_seconds() if row.handed_unsure_at else 0.0
            if waited >= NOT_TAKEN_AFTER_SECONDS and row.state in ("queued",):
                _not_taken(db, row, moment)
        db.flush()
        _keep_foreign(db, client_id, listing, known, key, moment)
        left = _left_after_import(db, client, listing, moment) if downloaders.is_usenet(client.kind) else []
        db.commit()
    return left


def _left_after_import(
    db: Any, client: DownloadClient, listing: list[downloaders.CategoryJob], moment: Any
) -> list[tuple[int, str, str]]:
    """Finished jobs of downloads imported more than ``CLEAN_AFTER`` ago, not tried since the start: ``(download id,
    job id, the path the client reports)``. The import cleans up itself; these are the ones where it did not."""
    finished = {item.job.download_id: item for item in listing if item.job.state == "completed"}
    if not finished:
        return []
    rows = db.scalars(
        select(Download).where(
            Download.client_id == client.id,
            Download.client_download_id.in_(list(finished)),
            Download.state == "imported",
        )
    )
    left: list[tuple[int, str, str]] = []
    with _lock:
        for row in rows:
            reported = finished[row.client_download_id].job.path
            if row.id in _cleaned or not reported or (moment - row.updated_at).total_seconds() < CLEAN_AFTER_SECONDS:
                continue
            _cleaned.add(row.id)
            left.append((row.id, row.client_download_id, reported))
    return left


def clean_after_import(download_id: int, client_id: int, reported: str) -> bool:
    """Delete an imported download's SABnzbd job folder with the import's checks (``files.remove_job_folder``): inside
    the category folder, strictly inside a mount point, no other video, not around the version's root folder. Returns
    whether it went; then the job may leave the history.

    A job folder that is gone already (the category folder is there, the job's folder not) counts as gone: the job only
    leaves the history, nothing is deleted. The owner's albums of 20.09.2026 stayed in SABnzbd's history so.
    """
    with SessionLocal() as db:
        row = db.get(Download, download_id)
        client = db.get(DownloadClient, client_id)
        version = db.get(Version, row.version_id) if row is not None and row.version_id is not None else None
        if row is None or client is None or version is None or not version.root_folder:
            return False
        mapped = files.map_remote(reported, list(client.path_mappings or []))
        keep = Path(version.root_folder)
        category = client.category
    # SABnzbd names the job folder, or the video in it (as the import reads it: a file stands for its folder).
    folder = None
    for candidate in ([mapped] if mapped is not None else []) + [reported]:
        raw = Path(candidate)
        job_raw = raw if raw.parent.name.casefold() == category.casefold() else raw.parent
        if job_raw.parent.name.casefold() != category.casefold():
            continue
        # The category folder must be seen; else a wrong mapping would read as "gone".
        category_folder = folders.visible_path(job_raw.parent)
        if category_folder is None or not category_folder.is_dir():
            continue
        job = category_folder / job_raw.name
        if not job.exists() and not files.is_link(job):
            logger.info("Download %d: its job folder is gone already; the job leaves the client", download_id)
            return True
        folder = job
        break
    if folder is None:
        logger.info("Download %d: its job folder is not one to delete; it stays in the client", download_id)
        return False
    mount = folders.containing_mount(files.resolved(folder) or folder)
    if mount is None or not files.remove_job_folder(folder, category=category, mount=mount, keep=keep):
        logger.info("Download %d: its job folder is not one to delete; it stays in the client", download_id)
        return False
    logger.info("Download %d: its job folder is deleted", download_id)
    return True


def _confirmed(row: Download, download_id: str) -> None:
    row.client_download_id = download_id[:128]
    row.handed_unsure_at = None
    row.missing_count = 0
    logger.info("Download %d: the client took the release after all; nexcrate follows its job", row.id)


def _not_taken(db: Any, row: Download, moment: Any) -> None:
    """The client answers and never showed the job: the hand-over failed. The release is not to blame and stays
    unblocked; the replacement searches again (``replacement.after_failure``)."""
    row.state, row.problem_code, row.problem_values = "failed", None, None
    row.failed_reason = "not_taken"
    row.handed_unsure_at = None
    row.updated_at = moment
    store.add_history(db, row, "failed", "not_taken", moment, {"detail": None})
    row.failure_handling = replacement.after_failure(db, row, moment)
    store.follow(db, row, moment)
    logger.info("Download %d: the client never showed the release it was handed; the hand-over failed", row.id)


def _keep_foreign(
    db: Any, client_id: int, listing: list[downloaders.CategoryJob], known: set[str], key: Any, moment: Any
) -> None:
    rows = {
        row.client_download_id: row for row in db.scalars(select(ForeignJob).where(ForeignJob.client_id == client_id))
    }
    seen: set[str] = set()
    added = 0
    for item in listing:
        job_id = key(item.job.download_id)
        if job_id in known or job_id in seen:
            continue
        seen.add(job_id)
        row = rows.get(job_id)
        if row is None:
            row = ForeignJob(client_id=client_id, client_download_id=job_id[:128], first_seen_at=moment)
            db.add(row)
            added += 1
        row.name = item.name[:1024] or job_id[:1024]
        row.state = item.job.state
        row.progress = item.job.progress
        row.size_bytes = item.job.size_bytes
        row.reported_path = (item.job.path or "")[:4096] or None
        row.last_seen_at = moment
    gone = [row.id for job_id, row in rows.items() if job_id not in seen]
    if gone:
        db.execute(delete(ForeignJob).where(ForeignJob.id.in_(gone)))
    if added or gone:
        logger.info(
            "Download client %d: %d foreign jobs in the category, %d new, %d gone",
            client_id,
            len(seen),
            added,
            len(gone),
        )


# --- What the owner does with one ---------------------------------------------------------------------------------- #


class ForeignError(Exception):
    def __init__(self, detail: dict[str, Any], status: int) -> None:
        super().__init__(detail["code"])
        self.detail = detail
        self.status = status


def not_found() -> ForeignError:
    return ForeignError(meldung("not_found", "This does not exist, or not any more."), 404)


def _series_reading(name: str) -> Any:
    """The name read as a series release; None when it carries no season or episode numbering."""
    from ..releases import series_parser

    parsed = series_parser.parse_series(name)
    if parsed.series_title is None or parsed.refused is not None:
        return None
    if parsed.season is None and not parsed.seasons and parsed.pack_scope != "series":
        return None
    return parsed


def _series_matches(db: Any, reading: Any) -> list[dict[str, Any]]:
    """Series of the library that share a spelling with the name, the likeliest first, at most five."""
    keys = [key for key in reading.title_keys if key]
    if not keys:
        return []
    found: list[dict[str, Any]] = []
    for title in db.scalars(select(Title).where(Title.kind == "series").order_by(Title.id)):
        stored = f"{title.search_keys or ''}{title.tmdb_search_keys or ''}"
        if any(f"|{key}|" in stored for key in keys):
            found.append({"title_id": title.id, "title": title.title, "year": title.year, "kind": "series"})
        if len(found) >= 5:
            break
    return found


def listing() -> list[dict[str, Any]]:
    """Every foreign job with its client, a guess of its kind and proposals from its name: titles of the library only
    (the dialog searches TMDB and the library itself)."""
    from ..disk import proposals as proposing
    from ..releases import parser as release_parser

    with SessionLocal() as db:
        rows = list(db.scalars(select(ForeignJob).order_by(ForeignJob.first_seen_at.desc(), ForeignJob.id.desc())))
        if not rows:
            return []
        clients = {row.id: row for row in db.scalars(select(DownloadClient))}
        library = proposing.Library.load(db)
        found: list[dict[str, Any]] = []
        for row in rows:
            parsed = release_parser.parse_movie(row.name)
            reading = _series_reading(row.name)
            client = clients.get(row.client_id)
            found.append(
                {
                    "id": row.id,
                    "client": {"id": client.id, "name": client.name, "kind": client.kind} if client else None,
                    "name": row.name,
                    "state": row.state,
                    "progress": row.progress,
                    "size_bytes": row.size_bytes,
                    "first_seen_at": row.first_seen_at,
                    "kind": "series" if reading is not None else "movie",
                    "parsed": {
                        "title": reading.series_title if reading is not None else parsed.title,
                        "year": reading.year if reading is not None else parsed.year,
                    },
                    "proposals": proposing.library_proposals(library, parsed.title, parsed.year)
                    if reading is None
                    else [],
                    "series_proposals": _series_matches(db, reading) if reading is not None else [],
                }
            )
        return found


def count() -> int:
    with SessionLocal() as db:
        return len(list(db.scalars(select(ForeignJob.id))))


@dataclass(frozen=True)
class Adopting:
    foreign_id: int
    client_id: int
    #: ⚠️ With the decrypted secret.
    target: downloaders.Target = field(repr=False)
    client_download_id: str = ""
    name: str = ""


def title_of_tmdb(tmdb_id: int, data: Any) -> int | None:
    """The library's movie of a TMDB number; with TMDB's ``data`` a missing one is added, as the folder assignment
    does. None when it is missing and there is no data."""
    from .. import tmdb

    moment = store.now()
    with SessionLocal() as db:
        title = db.scalar(select(Title).where(Title.kind == "movie", Title.tmdb_id == tmdb_id))
        if title is not None:
            return title.id
        if data is None:
            return None
        title = Title(kind="movie", tmdb_id=tmdb_id, added=moment, updated_at=moment)
        tmdb.apply_movie_data(title, data, moment)
        db.add(title)
        db.commit()
        logger.info("Title %d added from TMDB for a foreign job", title.id)
        return title.id


def _same_release(version: Any, name: str) -> bool:
    return bool(
        version is not None and version.has_file and version.release_title and same_name(version.release_title, name)
    )


def _refuse(code: str, message: str, status: int = 409, **values: Any) -> ForeignError:
    return ForeignError(meldung(code, message, **values), status)


def _series_episodes(db: Any, title: Title, version: Any, name: str) -> dict[int, str]:
    """The episodes a series job is for, read from its name: the episodes it names, the seasons of a pack, or every
    regular season. ``replaces`` where the version has a file, else ``fills``. The import files what it finds and asks
    the owner about the rest (``files_unassigned``)."""
    from ...models import Episode, EpisodeVersion
    from ..releases import series_parser

    names = tuple(item for item in (title.title, title.original_title, title.title_en) if item)
    reading = series_parser.parse_series(name, titles=names, anime=title.series_type == "anime")
    rows = list(db.scalars(select(Episode).where(Episode.title_id == title.id)))
    if reading.refused is None and reading.form == "anime" and reading.absolute:
        # Counted through (A5): the episodes of the numbers the series has.
        from ..series import release_match

        counted = release_match.load(db, title).by_absolute
        wanted = {counted[number] for number in reading.absolute if number in counted}
        chosen = [row for row in rows if row.id in wanted]
    elif reading.refused is None and reading.season is not None and reading.episodes:
        chosen = [row for row in rows if row.season_number == reading.season and row.episode_number in reading.episodes]
    elif reading.refused is None and (reading.season is not None or reading.seasons):
        seasons = set(reading.seasons) | ({reading.season} if reading.season is not None else set())
        chosen = [row for row in rows if row.season_number in seasons]
    else:
        chosen = [row for row in rows if row.season_number > 0]
    with_file = set(
        db.scalars(
            select(EpisodeVersion.episode_id).where(
                EpisodeVersion.version_id == version.id, EpisodeVersion.episode_file_id.is_not(None)
            )
        )
    )
    return {row.id: ("replaces" if row.id in with_file else "fills") for row in chosen}


def adopt(foreign_id: int, title_id: int, definition_id: int) -> int:
    """ "Assign and import": the foreign job becomes a download of the version, as if nexcrate had loaded it. A finished
    one is imported at once, one still loading is followed by the tracking. Returns the download's id.

    A movie takes any movie version (a missing one is added); a series one of the versions it has, and the episodes of
    its name (``_series_episodes``); an album the music version (added when missing), the import matches the files to
    the tracks itself.

    Raises ``ForeignError``: 404 when the job or the title is gone, 422 for a version of another kind, 409
    ``version_owned_by_source`` for a version a connection feeds, ``foreign_same_release`` when the version has exactly
    this release already (remove the job instead), ``foreign_job_failed`` for a job the client gave up on,
    ``foreign_no_version`` for a series without this version, ``foreign_no_episodes`` for a series without episodes."""
    from ...models import DownloadEpisode
    from . import importing, tracking

    moment = store.now()
    with SessionLocal() as db:
        row = db.get(ForeignJob, foreign_id)
        title = db.get(Title, title_id)
        definition = db.get(VersionDefinition, definition_id)
        client = db.get(DownloadClient, row.client_id) if row is not None else None
        if row is None or title is None or definition is None or client is None:
            raise not_found()
        if title.kind not in ("movie", "series", "album") or definition.kind != title.kind:
            raise _refuse("invalid_input", "The input is not valid.", 422, fields=["version_id"])
        if row.state in ("failed", "problem"):
            raise _refuse("foreign_job_failed", "The download client gave up on this job. Remove it instead.")
        version = store.version_of(db, title.id, definition.id)
        if version is not None and version.source_id is not None:
            raise _refuse(
                "version_owned_by_source", "This version comes from a Radarr connection; nexcrate files nothing there."
            )
        if title.kind != "series" and _same_release(version, row.name):
            raise _refuse(
                "foreign_same_release", "This version has exactly this release already. Remove the job instead."
            )
        episodes: dict[int, str] = {}
        if title.kind == "series":
            if version is None:
                raise _refuse(
                    "foreign_no_version", "This series does not have this version. Add it to the series first."
                )
            episodes = _series_episodes(db, title, version, row.name)
            if not episodes:
                raise _refuse("foreign_no_episodes", "nexcrate knows no episodes of this series yet.")
        elif version is None and title.kind == "album":
            from ..music import store as music_store

            version = music_store.add_version(db, title, definition, moment)
        elif version is None:
            from .. import library

            version = library.add_owner_version(db, title, definition, moment)
        completed = row.state == "completed"
        running = row.state if row.state in ("queued", "downloading", "paused") else "queued"
        download = Download(
            title_id=title.id,
            version_id=version.id,
            version_definition_id=definition.id,
            version_label=definition.label,
            client_id=client.id,
            protocol=PROTOCOL_OF_KIND.get(client.kind, "usenet"),
            client_download_id=row.client_download_id,
            release_title=row.name[:1024],
            indexer_name="",
            release_key="",
            size_bytes=row.size_bytes,
            quality=_quality(title.kind, row.name),
            languages=[],
            formats=[],
            confirmed=[],
            origin="manual",
            state="completed" if completed else running,
            progress=100.0 if completed else row.progress,
            reported_path=row.reported_path if completed else None,
            completed_at=moment if completed else None,
            grabbed_at=moment,
            updated_at=moment,
        )
        if title.kind == "album":
            download.scope = store.ALBUM_SCOPE
        elif title.kind == "series":
            seasons = {season for (season,) in db.execute(_seasons_of(list(episodes)))}
            download.scope = "episode" if len(episodes) == 1 else ("season" if len(seasons) == 1 else "series")
            download.season = next(iter(seasons)) if len(seasons) == 1 else None
        db.add(download)
        db.flush()
        db.add_all(
            DownloadEpisode(download_id=download.id, episode_id=episode_id, action=action, state="expected")
            for episode_id, action in sorted(episodes.items())
        )
        store.add_history(db, download, "grabbed", download.quality, moment, {"origin": "manual", "foreign": True})
        store.follow(db, download, moment)
        db.delete(row)
        db.commit()
        download_id = download.id
        kind = title.kind
    logger.info("Foreign job %d becomes download %d of %s %d", foreign_id, download_id, kind, title_id)
    if kind == "album":
        from ..music import loading as music_loading

        music_loading.hurry_album(title_id)
    if completed:
        importing.request(download_id)
    else:
        tracking.soon()
    return download_id


def _seasons_of(episode_ids: list[int]) -> Any:
    from ...models import Episode

    return select(Episode.season_number).where(Episode.id.in_(episode_ids)).distinct()


def _quality(kind: str, name: str) -> str | None:
    if kind == "series":
        from ..releases import series_parser

        quality = series_parser.parse_series(name).quality.name
    elif kind == "album":
        return None
    else:
        quality = releases.parse(name).movie.quality.name
    return quality if quality != "Unknown" else None


def _job_folder(row: ForeignJob, client: DownloadClient) -> Path | None:
    """The finished job's folder or file as nexcrate sees it, when it lies directly in a folder named like the
    category inside a mount point; else None: then nothing on disk is touched."""
    if not row.reported_path:
        return None
    mapped = files.map_remote(row.reported_path, list(client.path_mappings or []))
    for candidate in ([mapped] if mapped is not None else []) + [row.reported_path]:
        path = folders.visible_path(candidate)
        if path is None or not path.exists():
            continue
        mount = folders.containing_mount(path)
        if mount is None or files.is_link(path) or not files.strictly_inside(path, mount):
            continue
        if path.parent.name.casefold() != client.category.casefold():
            continue
        return path
    return None


async def remove(foreign_id: int) -> None:
    """ "Remove": the job leaves the client with its files, and nexcrate forgets it. A finished SABnzbd job's files
    are nexcrate's to delete (SABnzbd keeps them on a history delete); only inside the category folder."""
    with SessionLocal() as db:
        row = db.get(ForeignJob, foreign_id)
        client = db.get(DownloadClient, row.client_id) if row is not None else None
        if row is None or client is None:
            raise not_found()
        target = downloaders.Target(
            kind=client.kind,
            url=client.url,
            username=client.username or "",
            secret=crypto.decrypt(client.secret) if client.secret else "",
            category=client.category,
            login_blocked=store.login_blocked(client),
        )
        folder = _job_folder(row, client) if row.state == "completed" and downloaders.is_usenet(client.kind) else None
        job_id = row.client_download_id
    try:
        async with downloaders.open_client(target) as opened:
            await opened.remove(job_id, delete_files=True)
    except downloaders.ClientError as exc:
        raise ForeignError(exc.detail, exc.status) from exc
    if folder is not None:
        await asyncio.to_thread(_delete, foreign_id, folder)
    with SessionLocal() as db:
        db.execute(delete(ForeignJob).where(ForeignJob.id == foreign_id))
        db.commit()
    logger.info("Foreign job %d removed from its download client with its files", foreign_id)


def _delete(foreign_id: int, path: Path) -> None:
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    except OSError:
        logger.warning("Foreign job %d: its files could not be deleted", foreign_id)
        return
    logger.info("Foreign job %d: its files in the category folder are deleted", foreign_id)
