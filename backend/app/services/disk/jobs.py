"""The jobs of the disk pages: the scan, restoring and assigning many at once, in memory like the takeover's jobs.

At most one job runs at a time, whatever its kind (``DiskJobRunning`` otherwise); a finished job stays readable for 30
minutes and is lost on a restart. The rows a job wrote stay in the database.
"""

from __future__ import annotations

import contextvars
import copy
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ...models import utcnow
from .. import logs

logger = logging.getLogger("nexcrate.disk")

KINDS = ("scan", "restore", "assign")
KEEP_FINISHED = timedelta(minutes=30)


class DiskJobRunning(Exception):
    """A scan, a restore or an assign job runs already."""


class JobFailed(Exception):
    """Ends a job with an error code; what was committed before stays."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class DiskJob:
    id: int
    kind: str
    started_at: datetime
    state: str = "running"
    phase: str | None = None
    done: int | None = None
    total: int | None = None
    finished_at: datetime | None = None
    error_code: str | None = None
    result: dict[str, Any] | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def set(self, **values: Any) -> None:
        with self.lock:
            for name, value in values.items():
                setattr(self, name, value)

    def progress(self, done: int, total: int | None = None) -> None:
        with self.lock:
            self.done = done
            if total is not None:
                self.total = total


Work = Callable[[DiskJob], dict[str, Any]]

_lock = threading.Lock()
_jobs: dict[int, DiskJob] = {}
_threads: list[threading.Thread] = []
_counter = {"last": 0}


def now() -> datetime:
    return utcnow()


def _expire() -> None:
    moment = now()
    for job_id, job in list(_jobs.items()):
        if job.finished_at is not None and moment - job.finished_at > KEEP_FINISHED:
            del _jobs[job_id]


def is_running() -> bool:
    with _lock:
        return any(job.state == "running" for job in _jobs.values())


def start(kind: str, work: Work) -> dict[str, Any]:
    """Run ``work`` in a thread of its own and answer at once with the running job. Raises ``DiskJobRunning``."""
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    with _lock:
        _expire()
        if any(job.state == "running" for job in _jobs.values()):
            raise DiskJobRunning
        _counter["last"] += 1
        job = DiskJob(id=_counter["last"], kind=kind, started_at=now())
        _jobs[job.id] = job
        thread = threading.Thread(
            target=contextvars.copy_context().run, args=(_execute, job, work), name=f"disk-{kind}-{job.id}", daemon=True
        )
        _threads[:] = [existing for existing in _threads if existing.is_alive()]
        _threads.append(thread)
        try:
            thread.start()
        except BaseException:
            del _jobs[job.id]
            raise
    logger.info("Disk %s %d started", kind, job.id)
    return snapshot(job)


def _execute(job: DiskJob, work: Work) -> None:
    token = logs.bind_request(f"disk{job.id}")
    started = time.perf_counter()
    fields: dict[str, Any]
    try:
        fields = {"state": "done", "result": work(job)}
    except JobFailed as exc:
        fields = {"state": "failed", "error_code": exc.code}
    except Exception:
        logger.exception("Disk %s %d failed unexpectedly", job.kind, job.id)
        fields = {"state": "failed", "error_code": "internal_error"}
    finally:
        logs.unbind_request(token)
    job.set(phase=None, finished_at=now(), **fields)
    logger.info(
        "Disk %s %d %s in %dms%s",
        job.kind,
        job.id,
        fields["state"],
        (time.perf_counter() - started) * 1000,
        f": {fields['error_code']}" if fields["state"] == "failed" else "",
    )


def snapshot(job: DiskJob) -> dict[str, Any]:
    with job.lock:
        return {
            "id": job.id,
            "kind": job.kind,
            "state": job.state,
            "phase": job.phase,
            "progress": {"done": job.done or 0, "total": job.total} if job.total is not None else None,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "error_code": job.error_code,
            "result": copy.deepcopy(job.result),
        }


def latest() -> dict[str, Any] | None:
    """The newest job that runs or ended less than 30 minutes ago."""
    with _lock:
        _expire()
        found = list(_jobs.values())
    if not found:
        return None
    return snapshot(max(found, key=lambda job: job.id))


def wait_idle(timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    with _lock:
        threads = list(_threads)
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    with _lock:
        _threads[:] = [thread for thread in _threads if thread.is_alive()]
        return not _threads


def reset() -> None:
    """Forget every job. For the tests, after ``wait_idle``."""
    with _lock:
        _jobs.clear()
