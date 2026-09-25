"""The preview, the run and the undo as background jobs (decisions 10, 14, 33 and 34).

One job at a time, in a thread, like a takeover: the preview computes every title of a kind and keeps the plans in
memory until the next preview or run of that kind; a run computes each chosen title again right before it moves it
(the disk may have changed since the preview) and records itself in ``rename_runs``; the undo takes back the last run.
None starts while ``release.nex`` files are checked or written for the whole library, and that job waits for this one.

The unit of a kind is a title for movies and series, an artist for music (decision 24). A single album from its page
is renamed inside its artist folder.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ...db import SessionLocal
from ...models import RenameRun, RenameStep, RenameTitle, utcnow
from ..mediaservers import notify as mediaserver_notify
from . import apply, guard, movies, music, series
from .plan import STEPS_SHOWN, TitlePlan

logger = logging.getLogger("nexcrate.rename")

KINDS = ("movie", "series", "music")
KEEP_FINISHED = timedelta(minutes=30)


class JobRunning(Exception):
    """Another rename job runs, or the ``release.nex`` job does."""


@dataclass
class Preview:
    kind: str
    computed_at: datetime
    plans: list[TitlePlan]


@dataclass
class Job:
    id: str
    action: str
    kind: str
    state: str = "running"
    done: int = 0
    total: int = 0
    started_at: datetime = field(default_factory=utcnow)
    finished_at: datetime | None = None
    run_id: int | None = None
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


_lock = threading.Lock()
_job: Job | None = None
_previews: dict[str, Preview] = {}


def is_running() -> bool:
    with _lock:
        return _job is not None and _job.state == "running"


def _begin(action: str, kind: str) -> Job:
    from .. import companion_jobs

    global _job
    with _lock:
        if (_job is not None and _job.state == "running") or companion_jobs.is_running():
            raise JobRunning
        _job = Job(id=uuid.uuid4().hex, action=action, kind=kind)
        return _job


def _end(job: Job, state: str, error: str | None = None) -> None:
    with _lock:
        job.state = state
        job.error = error
        job.finished_at = utcnow()


def latest() -> Job | None:
    with _lock:
        if _job is None:
            return None
        if _job.finished_at is not None and utcnow() - _job.finished_at > KEEP_FINISHED:
            return None
        return _job


def preview(kind: str) -> Preview | None:
    with _lock:
        return _previews.get(kind)


def reset() -> None:
    """For tests."""
    global _job
    with _lock:
        _job = None
        _previews.clear()


# --- Planning ------------------------------------------------------------------------------------------------------- #


def unit_ids(kind: str) -> list[int]:
    with SessionLocal() as db:
        if kind == "movie":
            return movies.title_ids(db)
        if kind == "series":
            return series.title_ids(db)
        return music.artist_ids(db)


def plan_unit(kind: str, unit_id: int, claimed: set[Path] | None = None, *, album: bool = False) -> TitlePlan | None:
    """The plan of one title (movies, series), one artist (music), or with ``album`` one album in its artist folder."""
    with SessionLocal() as db:
        if kind == "movie":
            found = movies.plan(db, unit_id, claimed)
        elif kind == "series":
            found = series.plan(db, unit_id, claimed)
        elif album:
            found = music.plan_album(db, unit_id)
        else:
            found = music.plan(db, unit_id, claimed)
    if found is not None and found.skip is None and apply._busy(found.title_ids):
        found.skipped("busy")
    return found


def _compute(job: Job) -> None:
    ids = unit_ids(job.kind)
    with _lock:
        job.total = len(ids)
    plans: list[TitlePlan] = []
    claimed: set[Path] = set()
    for unit_id in ids:
        try:
            found = plan_unit(job.kind, unit_id, claimed)
        except Exception:
            logger.exception("Planning the rename of unit %d failed", unit_id)
            found = None
        if found is not None and (found.skip is not None or found.changed):
            plans.append(found)
            if found.skip is None:
                claimed.update(move.new for move in found.moves)
        with _lock:
            job.done += 1
    counts = summary(plans)
    with _lock:
        _previews[job.kind] = Preview(kind=job.kind, computed_at=utcnow(), plans=plans)
        job.result = counts
    logger.info("Rename preview of %s: %d units differ, %d skipped", job.kind, counts["titles"], counts["skipped"])


def summary(plans: list[TitlePlan]) -> dict[str, Any]:
    ready = [item for item in plans if item.skip is None]
    return {
        "titles": len(ready),
        "files": sum(item.file_count() for item in ready),
        "moves": sum(len(item.moves) for item in ready),
        "folders": sum(len(item.folders) for item in ready),
        "skipped": len(plans) - len(ready),
    }


def start_preview(kind: str) -> Job:
    job = _begin("preview", kind)
    _thread(job, _compute)
    return job


# --- Running -------------------------------------------------------------------------------------------------------- #


def start_run(kind: str, ids: list[int] | None, *, album: bool = False) -> Job:
    """Rename the units ``ids`` of a kind, or every unit of the stored preview that is ready when ``ids`` is None."""
    if ids is None:
        stored = preview(kind)
        ids = [item.artist_id or item.title_id for item in stored.plans if item.skip is None] if stored else []
    job = _begin("run", kind)
    job.total = len(ids)
    _thread(job, lambda current: _run(current, ids, album))
    return job


def _run(job: Job, ids: list[int], album: bool) -> None:
    with SessionLocal() as db:
        row = RenameRun(kind=job.kind, state="running", started_at=utcnow())
        db.add(row)
        db.commit()
        run_id = row.id
    job.run_id = run_id
    counts: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    try:
        for unit_id in ids:
            outcome = _one(job.kind, unit_id, run_id, album)
            counts[outcome.state] += 1
            counts["files"] += outcome.files
            counts["folders"] += outcome.folders
            if outcome.reason:
                reasons[outcome.reason] += 1
            with _lock:
                job.done += 1
    finally:
        result = {
            "done": counts["done"],
            "skipped": counts["skipped"],
            "failed": counts["failed"],
            "files": counts["files"],
            "folders": counts["folders"],
            "reasons": dict(reasons),
        }
        with SessionLocal() as db:
            stored = db.get(RenameRun, run_id)
            if stored is not None:
                stored.state = "done"
                stored.finished_at = utcnow()
                stored.counts = result
                db.commit()
        with _lock:
            job.result = result
            _previews.pop(job.kind, None)
    logger.info(
        "Rename run %d: %d done, %d skipped, %d failed", run_id, counts["done"], counts["skipped"], counts["failed"]
    )
    _tell_media_servers(job.kind, run_id, ("done",))


def _one(kind: str, unit_id: int, run_id: int, album: bool) -> apply.Outcome:
    try:
        found = plan_unit(kind, unit_id, album=album)
    except Exception:
        logger.exception("Planning the rename of unit %d failed", unit_id)
        return apply.Outcome("failed", "planning")
    if found is None:
        return apply.Outcome("skipped", "gone")
    try:
        with guard.renaming(found.title_ids):
            return apply.execute(found, run_id)
    except guard.Busy:
        return apply.Outcome("skipped", "busy")
    except Exception:
        logger.exception("Renaming unit %d failed", unit_id)
        return apply.Outcome("failed", "unexpected")


# --- The last run and its undo -------------------------------------------------------------------------------------- #


def last_run() -> dict[str, Any] | None:
    """The newest finished run with titles renamed, and whether it can still be undone."""
    with SessionLocal() as db:
        run = db.scalar(
            select(RenameRun).where(RenameRun.state.in_(("done", "undone"))).order_by(RenameRun.id.desc()).limit(1)
        )
        if run is None:
            return None
        renamed = db.scalar(
            select(RenameTitle.id).where(RenameTitle.run_id == run.id, RenameTitle.state == "done").limit(1)
        )
        return {
            "id": run.id,
            "kind": run.kind,
            "state": run.state,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "undone_at": run.undone_at,
            "counts": run.counts or {},
            "can_undo": run.state == "done" and renamed is not None,
        }


def start_undo() -> Job:
    last = last_run()
    if last is None or not last["can_undo"]:
        raise LookupError
    job = _begin("undo", last["kind"])
    job.run_id = last["id"]
    _thread(job, lambda current: _undo(current, last["id"]))
    return job


def _undo(job: Job, run_id: int) -> None:
    with SessionLocal() as db:
        run = db.get(RenameRun, run_id)
        finished = run.finished_at if run is not None else None
        if run is not None:
            run.state = "undoing"
            db.commit()
        rows = [
            row.id
            for row in db.scalars(
                select(RenameTitle)
                .where(RenameTitle.run_id == run_id, RenameTitle.state == "done")
                .order_by(RenameTitle.id.desc())
            )
        ]
    with _lock:
        job.total = len(rows)
    results: Counter[str] = Counter()
    try:
        for row_id in rows:
            try:
                results[apply.undo_title(row_id, finished)] += 1
            except Exception:
                logger.exception("Undoing rename title %d failed", row_id)
                results["unexpected"] += 1
            with _lock:
                job.done += 1
    finally:
        with SessionLocal() as db:
            run = db.get(RenameRun, run_id)
            if run is not None:
                run.state = "undone"
                run.undone_at = utcnow()
                run.counts = {**(run.counts or {}), "undo": dict(results)}
                db.commit()
        with _lock:
            undone = results.get("undone", 0)
            job.result = {
                "undone": undone,
                "kept": sum(results.values()) - undone,
                "reasons": {key: value for key, value in results.items() if key != "undone"},
            }
            _previews.clear()
    back = results.get("undone", 0)
    logger.info("Rename run %d undone: %d titles back, %d kept", run_id, back, sum(results.values()) - back)
    _tell_media_servers(job.kind, run_id, ("undone",))


def touched_folders(run_id: int, states: tuple[str, ...]) -> list[Path]:
    """The folders a run's titles in ``states`` moved files out of and into: what a media server has to read again."""
    with SessionLocal() as db:
        steps = db.execute(
            select(RenameStep.old_path, RenameStep.new_path)
            .join(RenameTitle, RenameTitle.id == RenameStep.run_title_id)
            .where(RenameTitle.run_id == run_id, RenameTitle.state.in_(states))
        ).all()
    return sorted({Path(path).parent for step in steps for path in step if path})


def _tell_media_servers(kind: str, run_id: int, states: tuple[str, ...]) -> None:
    """Once per run, after everything is committed and in a thread of its own; a failure is a log line (the
    notification never raises)."""
    try:
        folders = touched_folders(run_id, states)
    except Exception:
        logger.warning("The folders of rename run %d could not be read for the media servers", run_id, exc_info=True)
        return
    mediaserver_notify.request(kind, folders)


def _thread(job: Job, work: Any) -> None:
    def target() -> None:
        try:
            work(job)
        except Exception as exc:
            logger.exception("Rename job %s failed", job.action)
            _end(job, "failed", type(exc).__name__)
            return
        _end(job, "done")

    threading.Thread(target=target, name=f"rename-{job.action}", daemon=True).start()


def snapshot(job: Job) -> dict[str, Any]:
    with _lock:
        return {
            "id": job.id,
            "action": job.action,
            "kind": job.kind,
            "state": job.state,
            "done": job.done,
            "total": job.total,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "run_id": job.run_id,
            "result": dict(job.result),
            "error": job.error,
        }


def wait_idle(timeout: float = 30.0) -> bool:
    """For tests: wait until no job runs."""
    import time

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not is_running():
            return True
        time.sleep(0.02)
    return False


def shown_steps(found: TitlePlan, limit: int = STEPS_SHOWN) -> list[dict[str, str]]:
    """The moves as the preview shows them, relative to their root folder."""
    steps: list[dict[str, str]] = []
    for move in found.moves[:limit]:
        root = next((item for item in found.roots if move.old.is_relative_to(item)), None)
        if root is None:
            continue
        old, new = move.old.relative_to(root).as_posix(), move.new.relative_to(root).as_posix()
        steps.append({"what": move.what, "old": old, "new": new})
    return steps
