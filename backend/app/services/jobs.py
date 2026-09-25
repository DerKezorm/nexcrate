"""Periodic background jobs, started and stopped in the lifespan.

Register a job once, in ``main.py``::

    jobs.register("log_mode_expiry", 30, logs.enforce_expiry)

A job is a function or a coroutine function without arguments. A plain function
runs in a worker thread, so a database call does not block the event loop. A job
that raises is logged and runs again at its next turn: one failure must not end
the loop for good.

The first run comes after one interval, not at start. What has to happen at start
belongs in the lifespan itself, where its order is visible.

⚠️ A job may name a ``ready`` check. While it says no, the round is skipped without
the job running at all. That is for the heavy work of the start: judging every
stored file again holds SQLite's write lock for half a minute, and a job that
writes meanwhile waits for the lock and then fails with "database is locked"
(measured on the owner's instance on 20.09.2026, 31.7 seconds of judging).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass

from ..db import database_locked

logger = logging.getLogger("nexcrate.jobs")


@dataclass(frozen=True)
class Job:
    name: str
    interval_seconds: float
    func: Callable[[], object]
    #: Asked before every round; while it says no the round is skipped. None means: always run.
    ready: Callable[[], bool] | None = None


_registry: dict[str, Job] = {}


def register(
    name: str,
    interval_seconds: float,
    func: Callable[[], object],
    ready: Callable[[], bool] | None = None,
) -> Job:
    """Add a job, or replace the one with the same name."""
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    job = Job(name=name, interval_seconds=interval_seconds, func=func, ready=ready)
    _registry[name] = job
    return job


def unregister(name: str) -> None:
    _registry.pop(name, None)


def registered() -> list[Job]:
    return list(_registry.values())


async def run_once(job: Job) -> None:
    if inspect.iscoroutinefunction(job.func):
        await job.func()
        return
    result = await asyncio.to_thread(job.func)
    if inspect.isawaitable(result):
        await result


class Runner:
    """The running loops of a set of jobs."""

    def __init__(self, job_list: list[Job]) -> None:
        self._jobs = job_list
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []

    def start(self) -> None:
        for job in self._jobs:
            self._tasks.append(asyncio.create_task(self._loop(job), name=f"job:{job.name}"))

    async def _loop(self, job: Job) -> None:
        while True:
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=job.interval_seconds)
                return
            except TimeoutError:
                pass
            if job.ready is not None and not job.ready():
                # Something heavier holds the database; the next round asks again.
                logger.debug("Background job %s waits: something heavier is running", job.name)
                continue
            try:
                await run_once(job)
            except Exception as exc:
                if database_locked(exc):
                    # Another writer held the database longer than SQLite waits; the next run tries again. The
                    # writer names itself in the log when it held the lock that long (``db.watch_writes``).
                    logger.warning("Background job %s found the database locked; the next run tries again", job.name)
                    continue
                logger.exception("Background job %s failed", job.name)

    async def stop(self, timeout: float = 5.0) -> None:
        """Ask every loop to end; whatever still runs after ``timeout`` is cancelled."""
        self._stop.set()
        if not self._tasks:
            return
        _done, pending = await asyncio.wait(self._tasks, timeout=timeout)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.clear()


def start(job_list: list[Job] | None = None) -> Runner:
    runner = Runner(registered() if job_list is None else job_list)
    runner.start()
    return runner
