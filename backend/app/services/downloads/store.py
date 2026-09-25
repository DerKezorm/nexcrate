"""The database side of downloads: the answer shape, a version's state, history, the blocklist (step 3).

* **Problems** carry whether they need the owner. ``dangerous_file``, ``encrypted``, ``client_unreachable`` and
  ``stalled`` do not: nexcrate refused and blocked the release, keeps asking the client, or waits for a peer.
* **The step** of an import (``unpacking``) lives in memory in ``unpacking`` and shows only while ``importing``.
* **Blocking a new load** for a version: a download from queued to importing, or a problem that needs the owner. A
  refused dangerous download does not block; its release is on the blocklist already.
* **A version's state** follows its blocking download: ``problem`` while that download has a problem, else
  ``downloading``; without one it is back to what its file says. ⚠️ A version fed by a source is never touched.
* **The blocklist** holds releases of a title. A release counts as blocked when an entry has its info hash, or its
  release title (case ignored) with the same protocol.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session as OrmSession

from ...db import SessionLocal
from ...models import (
    Artist,
    BlocklistEntry,
    Download,
    DownloadClient,
    DownloadEpisode,
    Episode,
    EpisodeVersion,
    HistoryEntry,
    Indexer,
    Title,
    Version,
    utcnow,
)
from ...models.downloads import ACTIVE_STATES
from ..music import store as music_store
from ..schreibweisen import nfc
from . import unpacking

logger = logging.getLogger("nexcrate.downloads")

#: Every problem and whether it needs the owner.
PROBLEMS: dict[str, bool] = {
    "path_not_found": True,
    "packed": True,
    "no_video": True,
    "no_space": True,
    "gone_from_client": True,
    "client_error": True,
    "import_failed": True,
    # A video the container says is cut off: never filed, never replaces a file (24.09.2026).
    "file_truncated": True,
    "dangerous_file": False,
    "encrypted": False,
    "client_unreachable": False,
    "stalled": False,
    # S4 (decisions 32 and 33): every code has a way out on its card.
    "files_unassigned": True,
    "other_series_suspected": True,
    "several_videos": True,
    "import_stalled": True,
    "too_many_files": True,
    "multi_part": False,
    # M4: an album's files. The single file with a cue sheet was refused and blocked.
    "no_audio": True,
    "album_single_file": False,
    "album_not_better": True,
    "album_tracks_missing": True,
    # A download the client gave up on (the owner's finding of 20.09.2026): it waits in the problems until the
    # owner takes it off, so a failure is not only in the history. Since 22.09.2026 only when nothing takes care of it
    # by itself (``failure_handling`` owner); a replaced one is an event in the history.
    "download_failed": True,
}
#: The code a failed download carries while it waits for the owner.
FAILED_CODE = "download_failed"
#: Downloads still in a client or waiting for the owner.
UNFINISHED_STATES = (*ACTIVE_STATES, "problem")


#: The scopes of series downloads; ``album`` is an album download (decision 3).
SERIES_SCOPES = ("episode", "season", "series")
ALBUM_SCOPE = "album"

_step_lock = threading.Lock()
_steps: dict[int, str] = {}


def set_step(download_id: int, step: str | None) -> None:
    """The step an album import is at (``waiting_tracks``, ``matching``, ``filing``, ``tagging``), in memory."""
    with _step_lock:
        if step is None:
            _steps.pop(download_id, None)
        else:
            _steps[download_id] = step


def step_of(download_id: int) -> str | None:
    with _step_lock:
        return _steps.get(download_id)


def is_series(row: Download) -> bool:
    return row.scope in SERIES_SCOPES


def now() -> datetime:
    return utcnow()


def needs_owner(code: str | None) -> bool:
    return PROBLEMS.get(code or "", True)


def blocks_loading(row: Download) -> bool:
    return row.state in ACTIVE_STATES or (row.state == "problem" and needs_owner(row.problem_code))


def waits_for_owner(row: Download) -> bool:
    """A failed download is a problem only while nothing takes care of it by itself and the owner did not take it off
    (the owner's findings of 22.09.2026). A row from before has no ``failure_handling`` until the start settles it."""
    return row.state == "failed" and row.cleared_at is None and row.failure_handling in (None, "owner")


def waiting_for_owner() -> Any:
    """``waits_for_owner`` as a condition of a query."""
    return (
        (Download.state == "failed")
        & Download.cleared_at.is_(None)
        & (Download.failure_handling.is_(None) | (Download.failure_handling == "owner"))
    )


def settle_old_failures(moment: datetime) -> int:
    """At the start: a failed download from before 22.09.2026 that still waits for the owner gets its
    ``failure_handling``: the replacement took care of it when automatic loading of its kind is on. Returns how many
    stopped waiting."""
    from ..automatic import settings as automatic_settings

    with SessionLocal() as db:
        rows = list(
            db.scalars(
                select(Download).where(
                    Download.state == "failed", Download.cleared_at.is_(None), Download.failure_handling.is_(None)
                )
            )
        )
        if not rows:
            return 0
        kinds = dict(db.execute(select(Title.id, Title.kind).where(Title.id.in_({row.title_id for row in rows}))).all())
        enabled = {kind: automatic_settings.load_enabled(db, kind) for kind in set(kinds.values())}
        settled = 0
        for row in rows:
            row.failure_handling = "replacement" if enabled.get(kinds.get(row.title_id, ""), False) else "owner"
            settled += row.failure_handling != "owner"
        db.commit()
        return settled


def problem_of(row: Download, client_error_code: str | None) -> dict[str, Any] | None:
    """The problem as the API shows it.

    An unreachable client is a hint on its active downloads, which keep their state; the hint goes with the client's
    next answer, which clears its error code. A failed download is a problem until the owner takes it off: its
    release is blocked, and without the automatic nothing else happens on its own.
    """
    if waits_for_owner(row):
        values = {"reason": row.failed_reason or "client_failed", "detail": row.failed_detail}
        return {"code": FAILED_CODE, "needs_owner": True, "values": values}
    if row.problem_code:
        return {
            "code": row.problem_code,
            "needs_owner": needs_owner(row.problem_code),
            "values": dict(row.problem_values or {}),
        }
    if row.state in ACTIVE_STATES and client_error_code == "client_unreachable":
        return {"code": "client_unreachable", "needs_owner": False, "values": {}}
    return None


def scope_of(row: Download, codes: list[str], by_state: dict[str, list[str]] | None = None) -> dict[str, Any] | None:
    """What a series download covers, for the card: ``S02E05`` or a season with its count; None for a movie. With the
    codes of the episodes left out (the file there is not worse), not filed on request, and missing from the pack."""
    if row.scope is None:
        return None
    if row.scope == ALBUM_SCOPE:
        return {
            "kind": row.scope,
            "season": None,
            "episodes": [],
            "release_id": row.release_id,
            "filed": row.filed_count or 0,
            "open": row.open_count or 0,
            "missing": row.absent_count or 0,
        }
    states = by_state or {}
    return {
        "kind": row.scope,
        "season": row.season,
        "episodes": codes,
        "filed": row.filed_count or 0,
        "open": row.open_count or 0,
        "missing": row.absent_count or 0,
        "skipped_codes": states.get("skipped_not_better", []),
        "not_filed_codes": states.get("not_filed", []),
        "missing_codes": states.get("missing", []),
        "waiting_codes": states.get("expected", []),
    }


#: The summary's code of a release only the indexer's grab limit held back (``automatic.scheduler.GRAB_STOPPED``).
_GRAB_STOPPED = "indexer_limit_reached"


def _summary_entry(summary: Any, definition_id: int | None) -> dict[str, Any] | None:
    """The version's entry of a movie's or an album's search summary; None for a series or without one."""
    if not isinstance(summary, dict) or not isinstance(summary.get("versions"), list):
        return None
    entries = [entry for entry in summary["versions"] if isinstance(entry, dict)]
    return next((entry for entry in entries if entry.get("version_id") == definition_id), None)


def aftermath_of(db: OrmSession, row: Download, title: Title | None) -> dict[str, Any] | None:
    """What came of a failed download, for the history (the owner's finding of 22.09.2026): ``replaced`` by a later
    download of the version, the file there stays (``kept_file``, the search found nothing better), ``waiting_limit``
    for the indexer's grab limit, ``nothing_found`` so far, ``searching`` (the replacement is due), ``schedule`` (three
    replacements in a day), or ``owner`` (nothing happens by itself). None for anything but a failed download."""
    if row.state != "failed" or title is None:
        return None
    later = db.scalar(
        select(Download)
        .where(
            Download.title_id == row.title_id,
            Download.version_definition_id == row.version_definition_id,
            Download.id > row.id,
            Download.state != "removed",
        )
        .order_by(Download.id)
        .limit(1)
    )
    if later is not None:
        return {"kind": "replaced", "release": later.release_title, "state": later.state, "at": None}
    version = version_of(db, row.title_id, row.version_definition_id)
    searched_since = title.last_search_at is not None and title.last_search_at >= row.updated_at
    entry = _summary_entry(title.search_summary, row.version_definition_id) if searched_since else None
    if entry is not None and entry.get("load_code") == _GRAB_STOPPED and not entry.get("loaded"):
        free_at = title.search_summary.get("grab_free_at") if isinstance(title.search_summary, dict) else None
        return {"kind": "waiting_limit", "release": entry.get("best_title"), "state": None, "at": free_at}
    if searched_since or row.failure_handling == "schedule":
        kind = "kept_file" if version is not None and version.has_file else "nothing_found"
        return {"kind": kind, "release": None, "state": None, "at": title.next_search_at}
    if row.failure_handling == "replacement":
        return {"kind": "searching", "release": None, "state": None, "at": None}
    if row.failure_handling == "owner":
        return {"kind": "owner", "release": None, "state": None, "at": None}
    return None


def download_out(
    row: Download,
    title: Title | None,
    client: DownloadClient | None,
    codes: list[str] | None = None,
    by_state: dict[str, list[str]] | None = None,
    artist: str | None = None,
    aftermath: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": {
            "id": row.title_id,
            "title": title.title if title is not None else "",
            "year": title.year if title is not None else None,
            "kind": title.kind if title is not None else None,
            # An album names its artist (decision 43).
            "artist": artist,
        },
        "version": {"id": row.version_definition_id, "label": row.version_label},
        "client": {"id": client.id, "name": client.name, "kind": client.kind} if client is not None else None,
        "protocol": row.protocol,
        "release": {
            "title": row.release_title,
            "indexer_id": row.indexer_id,
            "indexer": row.indexer_name,
            "size_bytes": row.size_bytes,
            "quality": row.quality,
            "score": row.score,
            "below_target": bool(row.below_target),
        },
        "state": row.state,
        "step": (unpacking.step_of(row.id) or step_of(row.id)) if row.state in ("importing", "completed") else None,
        "progress": row.progress,
        "remaining_seconds": row.remaining_seconds,
        "problem": problem_of(row, client.last_error_code if client is not None else None),
        "transfer": row.transfer,
        # ⚠️ The file name only, never a path.
        "imported_file": PurePosixPath(row.imported_path).name if row.imported_path else None,
        # Relative to the folder it was filed into, never a full path: the interface names the movie folder from it.
        "imported_path": row.imported_path or None,
        "failed_reason": row.failed_reason if row.state == "failed" else None,
        "failed_detail": row.failed_detail if row.state == "failed" else None,
        "aftermath": aftermath,
        "confirmed": list(row.confirmed or []),
        "origin": row.origin or "manual",
        "grabbed_at": row.grabbed_at,
        "completed_at": row.completed_at,
        "imported_at": row.imported_at,
        "updated_at": row.updated_at,
        "scope": scope_of(row, codes or [], by_state),
    }


def episode_codes(db: OrmSession, download_ids: list[int]) -> dict[int, list[str]]:
    """The codes of each series download's episodes, in order."""
    return {key: value["all"] for key, value in episode_states(db, download_ids).items()}


def episode_states(db: OrmSession, download_ids: list[int]) -> dict[int, dict[str, list[str]]]:
    """Per series download: the codes of its episodes in order (``all``), and by the state of each episode."""
    if not download_ids:
        return {}
    found: dict[int, list[tuple[int, int, str]]] = {}
    for download_id, season, number, state in db.execute(
        select(DownloadEpisode.download_id, Episode.season_number, Episode.episode_number, DownloadEpisode.state)
        .join(Episode, Episode.id == DownloadEpisode.episode_id)
        .where(DownloadEpisode.download_id.in_(download_ids))
    ).tuples():
        found.setdefault(download_id, []).append((season, number, state))
    result: dict[int, dict[str, list[str]]] = {}
    for key, value in found.items():
        grouped: dict[str, list[str]] = {"all": []}
        for season, number, state in sorted(value):
            code = f"S{season:02d}E{number:02d}"
            grouped["all"].append(code)
            grouped.setdefault(state, []).append(code)
        result[key] = grouped
    return result


def outs(db: OrmSession, rows: Iterable[Download]) -> list[dict[str, Any]]:
    listed = list(rows)
    title_ids = {row.title_id for row in listed}
    client_ids = {row.client_id for row in listed if row.client_id is not None}
    titles = {row.id: row for row in db.scalars(select(Title).where(Title.id.in_(title_ids)))} if title_ids else {}
    clients = (
        {row.id: row for row in db.scalars(select(DownloadClient).where(DownloadClient.id.in_(client_ids)))}
        if client_ids
        else {}
    )
    states = episode_states(db, [row.id for row in listed if is_series(row)])
    artist_ids = {title.artist_id for title in titles.values() if title.kind == "album" and title.artist_id}
    artists = (
        dict(db.execute(select(Artist.id, Artist.name).where(Artist.id.in_(artist_ids))).tuples().all())
        if artist_ids
        else {}
    )
    return [
        download_out(
            row,
            titles.get(row.title_id),
            clients.get(row.client_id or 0),
            states.get(row.id, {}).get("all"),
            states.get(row.id),
            artists.get(titles[row.title_id].artist_id or 0) if row.title_id in titles else None,
            aftermath_of(db, row, titles.get(row.title_id)) if row.state == "failed" else None,
        )
        for row in listed
    ]


def blocking_download(db: OrmSession, title_id: int, definition_id: int) -> Download | None:
    """A movie or album version's download that blocks a new load. Series downloads block only their own episodes."""
    rows = db.scalars(
        select(Download)
        .where(
            Download.title_id == title_id,
            Download.version_definition_id == definition_id,
            Download.state.in_(UNFINISHED_STATES),
            or_(Download.scope.is_(None), Download.scope == ALBUM_SCOPE),
        )
        .order_by(Download.grabbed_at.desc(), Download.id.desc())
    )
    return next((row for row in rows if blocks_loading(row)), None)


def downloading_episodes(db: OrmSession, title_id: int, definition_id: int) -> dict[int, int]:
    """Episode id to the series download of this version that holds it and blocks (decision 5). An episode the
    download filed already, left out or gave up is free for another download."""
    rows = db.execute(
        select(DownloadEpisode.episode_id, Download)
        .join(Download, Download.id == DownloadEpisode.download_id)
        .where(
            Download.title_id == title_id,
            Download.version_definition_id == definition_id,
            Download.state.in_(UNFINISHED_STATES),
            DownloadEpisode.state == "expected",
        )
    ).tuples()
    return {episode_id: row.id for episode_id, row in rows if blocks_loading(row)}


def follow_series(db: OrmSession, row: Download, moment: datetime) -> None:
    """The queue state of a series download's episodes in its version, then the version's counts and state."""
    follow_episodes(db, row.title_id, row.version_definition_id, moment)


def follow_episodes(db: OrmSession, title_id: int, definition_id: int | None, moment: datetime) -> int:
    """The queue state of a series version's episodes, then the version's counts and state. Returns how many episodes
    changed their queue state.

    Every unfinished download of the version is looked at, so an episode shows the download that still holds it.
    """
    from ..series import watching

    # The session writes nothing before a query (autoflush=False): unflushed, a download just marked removed or
    # failed still matched the unfinished ones below and kept holding its episodes (owner's finding, 24.09.2026).
    db.flush()
    version = version_of(db, title_id, definition_id)
    if version is None or version.source_id is not None:
        return 0
    held: dict[int, tuple[str, float | None, str | None]] = {}
    for episode_id, download in db.execute(
        select(DownloadEpisode.episode_id, Download)
        .join(Download, Download.id == DownloadEpisode.download_id)
        .where(
            Download.title_id == title_id,
            Download.version_definition_id == definition_id,
            Download.state.in_(UNFINISHED_STATES),
            DownloadEpisode.state.in_(("expected", "not_filed")),
        )
    ).tuples():
        if download.state == "problem":
            held[episode_id] = ("problem", None, download.problem_code)
        elif episode_id not in held:
            held[episode_id] = ("downloading", download.progress, None)
    changed = 0
    for link in db.scalars(select(EpisodeVersion).where(EpisodeVersion.version_id == version.id)):
        state, progress, code = held.get(link.episode_id, (None, None, None))
        changed += link.queue_state != state
        if (link.queue_state, link.progress, link.problem_code) != (state, progress, code):
            link.queue_state, link.progress, link.problem_code = state, progress, code
    watching.recount(db, version, watching.today())
    version.updated_at = moment
    return changed


def release_held_episodes(moment: datetime) -> int:
    """At the start: episodes still shown as loading or as a problem are looked at once more. Before 24.09.2026 a
    removed or failed series download kept holding its episodes (``follow_episodes``), and nothing looked again.
    Returns how many episodes changed their queue state."""
    with SessionLocal() as db:
        versions = db.execute(
            select(Version.title_id, Version.version_definition_id)
            .join(EpisodeVersion, EpisodeVersion.version_id == Version.id)
            .where(EpisodeVersion.queue_state.is_not(None), Version.source_id.is_(None))
            .distinct()
        ).all()
        changed = sum(follow_episodes(db, title_id, definition_id, moment) for title_id, definition_id in versions)
        db.commit()
        return changed


def follow(db: OrmSession, row: Download, moment: datetime) -> None:
    """The version of a download follows it: a series version by its episodes, a movie version by its download."""
    if is_series(row):
        follow_series(db, row, moment)
    else:
        follow_version(db, row.title_id, row.version_definition_id, moment)


def pending_downloads(db: OrmSession, title_id: int) -> list[Download]:
    """The title's downloads still in their client and not filed away: queued to importing, or a problem (hints too).

    ⚠️ One place for this set: removing a title or a version checks and removes exactly these, and the library counts
    them per version as ``pending_downloads`` ("Changes after the owner's live test", B).
    """
    return list(
        db.scalars(
            select(Download)
            .where(Download.title_id == title_id, Download.state.in_(UNFINISHED_STATES))
            .order_by(Download.id)
        )
    )


def pending_count(db: OrmSession, definition_id: int) -> int:
    """How many downloads of one version definition, over all titles, are in the set of ``pending_downloads``."""
    return int(
        db.scalar(
            select(func.count(Download.id)).where(
                Download.version_definition_id == definition_id, Download.state.in_(UNFINISHED_STATES)
            )
        )
        or 0
    )


def version_of(db: OrmSession, title_id: int, definition_id: int | None) -> Version | None:
    if definition_id is None:
        return None
    return db.scalar(
        select(Version).where(Version.title_id == title_id, Version.version_definition_id == definition_id)
    )


def follow_version(db: OrmSession, title_id: int, definition_id: int | None, moment: datetime) -> None:
    """The version's state, progress and problem from its blocking download, or from its file without one."""
    db.flush()  # as in follow_series: the query for the blocking download must see a state set just before
    version = version_of(db, title_id, definition_id)
    if version is None or version.source_id is not None or definition_id is None:
        return
    download = blocking_download(db, title_id, definition_id)
    if download is not None and download.state == "problem":
        state, progress, problem = "problem", None, download.problem_code
    elif download is not None:
        state, progress, problem = "downloading", download.progress, None
    elif version.has_file:
        state, progress, problem = ("upgrade" if version.cutoff_not_met else "available"), None, None
        if version.track_counts is not None and music_store.incomplete(version):
            # Only an album version counts tracks.
            state = "incomplete"
    else:
        state, progress, problem = ("wanted" if version.monitored else "unmonitored"), None, None
    if (version.state, version.progress, version.problem_code) != (state, progress, problem):
        version.state, version.progress, version.problem_code = state, progress, problem
        version.updated_at = moment


def _moot_failures(title_id: int, definition_id: int | None) -> Any:
    """Failed downloads of this version that a later import made moot: they wait for nobody."""
    return (
        Download.title_id == title_id,
        Download.version_definition_id == definition_id,
        Download.state == "failed",
        Download.cleared_at.is_(None),
    )


def close_failures_of(db: OrmSession, title_id: int, definition_id: int | None, moment: datetime) -> None:
    """A download of this version was imported, so the failures before it are done (the owner's finding of
    20.09.2026: the version had its file and still said the last attempt had failed)."""
    db.execute(
        update(Download).where(*_moot_failures(title_id, definition_id)).values(cleared_at=moment),
        execution_options={"synchronize_session": False},
    )


def close_moot_failures(moment: datetime) -> int:
    """At the start: every failure a later import of the same version made moot. Returns how many were closed."""
    with SessionLocal() as db:
        rows = list(db.scalars(select(Download).where(Download.state == "failed", Download.cleared_at.is_(None))))
        closed = 0
        for row in rows:
            newer = db.scalar(
                select(func.count())
                .select_from(Download)
                .where(
                    Download.title_id == row.title_id,
                    Download.version_definition_id == row.version_definition_id,
                    Download.state == "imported",
                    Download.imported_at.is_not(None),
                    Download.imported_at > row.updated_at,
                )
            )
            if newer:
                row.cleared_at = moment
                closed += 1
        if closed:
            db.commit()
        return closed


def add_history(
    db: OrmSession,
    row: Download,
    event: str,
    detail: str | None,
    moment: datetime,
    data: dict[str, Any] | None = None,
) -> None:
    """A history line of a download. ``data`` is what ``/api/v1`` tells about it (N29): always the
    download, for ``grabbed`` its quality and size, for ``failed`` the code; the caller adds what only it knows."""
    written: dict[str, Any] = {"download_id": row.id}
    if event == "grabbed":
        written |= {"quality": row.quality, "size_bytes": row.size_bytes}
    elif event == "failed":
        written |= {"code": detail}
    written |= data or {}
    if event == "imported":
        # A success ends what the failures before it were waiting for.
        close_failures_of(db, row.title_id, row.version_definition_id, moment)
    version = version_of(db, row.title_id, row.version_definition_id)
    db.add(
        HistoryEntry(
            title_id=row.title_id,
            version_id=version.id if version is not None else None,
            version_definition_id=row.version_definition_id,
            version_label=row.version_label,
            event=event,
            at=moment,
            detail=(detail or None) and detail[:1024],
            data=written,
        )
    )


def block(db: OrmSession, row: Download, reason: str, moment: datetime) -> BlocklistEntry:
    """Put the release of a download on its title's blocklist."""
    indexer_id = row.indexer_id if row.indexer_id is not None and db.get(Indexer, row.indexer_id) else None
    entry = BlocklistEntry(
        title_id=row.title_id,
        release_title=row.release_title,
        protocol=row.protocol,
        indexer_id=indexer_id,
        indexer_name=row.indexer_name,
        release_key=row.release_key,
        info_hash=row.client_download_id.lower() if row.protocol == "torrent" and row.client_download_id else None,
        size_bytes=row.size_bytes,
        reason=reason,
        created_at=moment,
    )
    db.add(entry)
    logger.info("Download %d: the release is on the blocklist of title %d (%s)", row.id, row.title_id, reason)
    return entry


def is_blocked(
    entries: Iterable[BlocklistEntry], *, protocol: str, release_title: str, info_hash: str | None
) -> bool:
    wanted = nfc(release_title).casefold()
    for entry in entries:
        if info_hash and entry.info_hash and entry.info_hash.lower() == info_hash.lower():
            return True
        if entry.protocol == protocol and nfc(entry.release_title).casefold() == wanted:
            return True
    return False


def is_blocked_hash(entries: Iterable[BlocklistEntry], info_hash: str | None) -> bool:
    return bool(info_hash) and any(
        entry.info_hash and entry.info_hash.lower() == (info_hash or "").lower() for entry in entries
    )


def blocklist(db: OrmSession, title_id: int) -> list[BlocklistEntry]:
    return list(db.scalars(select(BlocklistEntry).where(BlocklistEntry.title_id == title_id)))


def following(db: OrmSession, client_id: int, download_id: str) -> bool:
    """Whether a download that is not finished follows this id in this client."""
    return (
        db.scalar(
            select(Download.id)
            .where(
                Download.client_id == client_id,
                Download.client_download_id == download_id,
                Download.state.in_(UNFINISHED_STATES),
            )
            .limit(1)
        )
        is not None
    )


def login_blocked(client: DownloadClient) -> bool:
    """qBittorrent refused the password before: no login until the client is saved or tested again."""
    return client.kind == "qbittorrent" and client.last_error_code == "client_auth_failed"


def record_client_error(client_id: int, code: str | None) -> None:
    with SessionLocal() as db:
        client = db.get(DownloadClient, client_id)
        if client is not None and client.last_error_code != code:
            client.last_error_code = code
            db.commit()


def pause_indexer(indexer_id: int, seconds: int) -> None:
    """A fetch hit the indexer's limit: it pauses as after a search."""
    with SessionLocal() as db:
        indexer = db.get(Indexer, indexer_id)
        if indexer is None:
            return
        indexer.paused_until = now() + timedelta(seconds=seconds)
        indexer.last_error_code = "indexer_limit_reached"
        db.commit()
    logger.warning("Indexer %d: the grab limit is reached, no requests for %d seconds", indexer_id, seconds)
