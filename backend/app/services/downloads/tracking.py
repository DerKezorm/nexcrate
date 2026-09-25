"""Following downloads in their clients ("Tracking").

* A background job looks every 5 seconds whether a round is due: every 15 seconds, and 5 seconds after a load.
* A round asks each enabled client that has downloads nexcrate loaded and that the client still works on (queued,
  downloading, paused, or a problem the client reported before the download finished), filtered by the category.
  Anything else in the category is left alone.
* Downloads are matched by the client's download id. What the client says is mapped in ``downloaders``; here it
  becomes the download's state, progress, remaining time, size, reported path and the client's state for the log.
* A download the client does not know twice in a row is the problem ``gone_from_client``, except one whose
  hand-over the client did not answer in time: ``foreign`` looks for it by its name once a minute.
* Once a minute every enabled client lists its whole category (``foreign.look``): jobs no download follows show
  under Problems.
* A client that cannot be asked keeps its downloads as they are and gets its error code.
* Failed (SABnzbd only): the release goes on the title's blocklist, a history entry ``failed``, and the job leaves
  SABnzbd with its files. A ``_FAILED_`` folder SABnzbd leaves anyway goes a minute later (``leftovers``).
* Completed downloads are handed to ``importing`` after each round, also those a folder ``_UNPACK_`` held back before.
* Only a problem that needs the owner makes a version ``problem``; everything else keeps it ``downloading``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field

from sqlalchemy import and_, or_, select

from ... import crypto
from ...db import SessionLocal
from ...models import Download, DownloadClient
from ...models.downloads import CLIENT_STATES, FAILED_DETAILS, FAILED_REASONS
from .. import downloaders
from ..automatic import replacement
from . import foreign, importing, leftovers, store

logger = logging.getLogger("nexcrate.downloads")

JOB_NAME = "download_tracking"
#: How often the job looks whether a round is due.
INTERVAL_SECONDS = 5.0
ROUND_SECONDS = 15.0
SOON_SECONDS = 5.0
#: Rounds in a row a download may be missing before it is a problem.
GONE_AFTER = 2

_lock = threading.Lock()
_last_round: float | None = None
_soon_at: float | None = None


def clock() -> float:
    return time.monotonic()


def soon() -> None:
    """Ask the clients 5 seconds from now, for example after a load."""
    global _soon_at
    with _lock:
        _soon_at = clock() + SOON_SECONDS


def reset() -> None:
    global _last_round, _soon_at
    with _lock:
        _last_round = None
        _soon_at = None
    leftovers.reset()
    foreign.reset()


def due() -> bool:
    moment = clock()
    with _lock:
        if _soon_at is not None and moment >= _soon_at:
            return True
        return _last_round is None or moment - _last_round >= ROUND_SECONDS


def ready() -> bool:
    """Whether a round may run now.

    ⚠️ Not while the start judges every stored file again: that pass holds SQLite's write lock for half a minute
    (31.7 seconds on the owner's instance, 20.09.2026), and a round that writes into it waits for the lock and then
    fails with "database is locked". The next round, five seconds later, asks again; nothing is lost, because the
    client keeps its finished job until nexcrate has filed it.
    """
    from .. import judging

    return not judging.busy()


async def run_job() -> None:
    """The background job."""
    if due():
        await run_round()


@dataclass(frozen=True)
class _Work:
    client_id: int
    #: ⚠️ With the decrypted secret.
    target: downloaders.Target = field(repr=False)
    download_ids: list[str] = field(default_factory=list)


def _tracked():
    return or_(Download.state.in_(CLIENT_STATES), and_(Download.state == "problem", Download.completed_at.is_(None)))


def _work() -> list[_Work]:
    with SessionLocal() as db:
        rows = db.execute(
            select(Download.client_id, Download.client_download_id).where(Download.client_id.is_not(None), _tracked())
        ).all()
        by_client: dict[int, list[str]] = {}
        for client_id, download_id in rows:
            if download_id:
                by_client.setdefault(int(client_id), []).append(download_id)
        work: list[_Work] = []
        for client in db.scalars(select(DownloadClient).where(DownloadClient.id.in_(list(by_client)))):
            # ⚠️ A qBittorrent that refused the password is not asked again: five failures ban the address.
            if not client.enabled or store.login_blocked(client):
                continue
            target = downloaders.Target(
                kind=client.kind,
                url=client.url,
                username=client.username or "",
                secret=crypto.decrypt(client.secret) if client.secret else "",
                category=client.category,
            )
            work.append(_Work(client_id=client.id, target=target, download_ids=by_client[client.id]))
    return work


def _follow(db: object, row: Download, job: downloaders.Job | None, removals: list[str]) -> None:
    moment = store.now()
    if row.handed_unsure_at is not None:
        # The client did not answer the hand-over in time: ``foreign`` finds its job, or ends it as not_taken.
        return
    if job is None:
        row.missing_count = (row.missing_count or 0) + 1
        if row.missing_count >= GONE_AFTER and row.problem_code != "gone_from_client":
            row.state, row.problem_code, row.problem_values = "problem", "gone_from_client", {}
            row.updated_at = moment
            logger.info("Download %d is gone from its client", row.id)
        return
    before = (row.state, row.progress, row.remaining_seconds, row.problem_code, row.reported_path)
    row.missing_count = 0
    row.client_state = job.client_state[:64] or None
    if job.progress is not None:
        row.progress = job.progress
    row.remaining_seconds = job.remaining_seconds
    if job.size_bytes:
        row.size_bytes = job.size_bytes
    if job.state == "completed":
        row.state, row.problem_code, row.problem_values = "completed", None, None
        row.reported_path = (job.path or "")[:4096] or None
        row.completed_at = row.completed_at or moment
        row.progress = 100.0
    elif job.state == "failed":
        row.state, row.problem_code, row.problem_values = "failed", None, None
        reason = job.reason if job.reason in FAILED_REASONS else "client_failed"
        row.failed_reason = reason
        # What SABnzbd said, kept before the job leaves it (the owner's finding of 22.09.2026).
        row.failed_detail = job.detail if job.detail in FAILED_DETAILS else None
        store.block(db, row, reason, moment)  # type: ignore[arg-type]
        store.add_history(db, row, "failed", reason, moment, {"detail": row.failed_detail})  # type: ignore[arg-type]
        # The next fitting release, at most three times a day (C7), also with the switch off; a
        # failure nexcrate takes care of is no problem for the owner.
        row.failure_handling = replacement.after_failure(db, row, moment)  # type: ignore[arg-type]
        if row.protocol == "usenet":
            removals.append(row.client_download_id)
        logger.info("Download %d failed in its client (%s)", row.id, reason)
    elif job.state == "problem":
        row.state, row.problem_code = "problem", job.problem or "client_error"
        if job.problem == "path_not_found":
            row.problem_values = {"proposal": None}
            row.completed_at = row.completed_at or moment
        else:
            row.problem_values = {}
    else:
        row.state = job.state
        row.problem_code = "stalled" if job.problem == "stalled" else None
        row.problem_values = {} if job.problem == "stalled" else None
    if (row.state, row.progress, row.remaining_seconds, row.problem_code, row.reported_path) != before:
        row.updated_at = moment


def _apply(client_id: int, found: dict[str, downloaders.Job]) -> list[str]:
    """Write what the client said. Returns the download ids to remove from the client with their files."""
    removals: list[str] = []
    with SessionLocal() as db:
        client = db.get(DownloadClient, client_id)
        if client is None:
            return removals
        client.last_error_code = None
        rows = list(db.scalars(select(Download).where(Download.client_id == client_id, _tracked())))
        for row in rows:
            key = row.client_download_id.lower() if row.protocol == "torrent" else row.client_download_id
            _follow(db, row, found.get(key), removals)
        db.flush()
        # Once per version, not once per download: a series version follows all its downloads in one pass, and a
        # series with eight downloads counted its episodes eight times while the write lock was held (23.09.2026:
        # 20 to 40 s per round on the owner's instance, and a hand-over after it gave up recording its download).
        followed: set[tuple[int, int | None, bool]] = set()
        for row in rows:
            key = (row.title_id, row.version_definition_id, store.is_series(row))
            if key in followed:
                continue
            followed.add(key)
            store.follow(db, row, store.now())
        db.commit()
    return removals


def _client_failed(client_id: int, code: str) -> None:
    store.record_client_error(client_id, code)


def _completed_ids() -> list[int]:
    with SessionLocal() as db:
        return list(db.scalars(select(Download.id).where(Download.state == "completed").order_by(Download.id)))


async def run_round() -> None:
    """Ask every client once, then hand completed downloads to the import."""
    global _last_round, _soon_at
    with _lock:
        _last_round = clock()
        _soon_at = None
    for work in await asyncio.to_thread(_work):
        try:
            async with downloaders.open_client(work.target) as client:
                found = await client.jobs(work.download_ids)
        except downloaders.ClientError as exc:
            logger.info("Download client %d could not be asked: %s", work.client_id, exc.code)
            await asyncio.to_thread(_client_failed, work.client_id, exc.code)
            continue
        removals = await asyncio.to_thread(_apply, work.client_id, found)
        for download_id in removals:
            try:
                async with downloaders.open_client(work.target) as client:
                    await client.remove(download_id, delete_files=True)
            except downloaders.ClientError as exc:
                logger.info("Download client %d did not remove a failed job: %s", work.client_id, exc.code)
        if removals:
            leftovers.after_removal()
    for download_id in await asyncio.to_thread(_completed_ids):
        # An import that met a locked database pauses before its next try (``importing.waits``).
        if not importing.waits(download_id):
            importing.request(download_id)
    # Nothing stays filed away halfway: 30 minutes without a change is a problem with a way out (S4, decision 33).
    from . import assigning

    await asyncio.to_thread(assigning.watch)
    if foreign.due():
        await foreign.look()
    if leftovers.due():
        await leftovers.sweep()
