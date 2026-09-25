"""The backfill of ``release.nex`` (L3): the check, the run, and "replace".

* **The check** looks at every version nexcrate owns with a file, classifies its ``release.nex`` and stores the state on
  the version. Nothing on disk is written. Since S6 (P4) every season folder of a series version
  of nexcrate's own counts the same way.
* **The run** does the check's work and writes the files that are ``missing`` or ``outdated``. One version after
  another, a pause after each write to spare a NAS and the media servers' folder watchers.
* **One job at a time**, and none while a takeover writes its companion files. Imports go on meanwhile: the folder lock
  in ``companions`` keeps an import and the job apart. Jobs live in memory like takeover jobs, readable for 30 minutes
  after they ended, lost on a restart.
* **Replace** moves a ``release.nex`` nexcrate refuses to overwrite (foreign, broken, newer, of another installation or
  changed by hand) into the recycle folder of the version's root folder, then writes nexcrate's file.

Log lines carry ids, counts and codes, never titles, names or paths.
"""

from __future__ import annotations

import contextvars
import copy
import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ..db import SessionLocal
from ..models import Version, VersionDefinition, utcnow
from . import companions, companions_series

logger = logging.getLogger("nexcrate.companions")

KINDS = ("check", "backfill")
KEEP_FINISHED = timedelta(minutes=30)
#: The pause after every written file; the bench measurement of the media servers' watchers sets it (decision 11).
WRITE_PAUSE_SECONDS = 0.02
#: Example folders per state in the report.
EXAMPLES = 20
#: States the report names no examples for.
NO_EXAMPLES = ("current", "written")


class CompanionJobRunning(Exception):
    """A check or a run is already running, or a takeover writes its companion files."""


class CompanionsOff(Exception):
    """The switch "Begleitdateien schreiben" is off."""


class VersionMissing(Exception):
    """The version does not exist."""


class NotReplaceable(Exception):
    """The version's stored state is none "replace" acts on."""

    def __init__(self, state: str | None) -> None:
        super().__init__(state)
        self.state = state


# --- The jobs -------------------------------------------------------------------------------------------------- #


@dataclass
class Job:
    id: int
    kind: str
    started_at: datetime
    #: running, done or failed.
    state: str = "running"
    done: int = 0
    total: int = 0
    finished_at: datetime | None = None
    error_code: str | None = None
    result: dict[str, Any] | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def set(self, **values: Any) -> None:
        with self.lock:
            for name, value in values.items():
                setattr(self, name, value)


_lock = threading.Lock()
_jobs: dict[int, Job] = {}
_threads: list[threading.Thread] = []
_counter = {"last": 0}
#: How many takeovers write their companion files right now (their phase ``companions``).
_phases = {"active": 0}


def now() -> datetime:
    return utcnow()


def _expire() -> None:
    """Forget jobs that ended longer ago than ``KEEP_FINISHED``. Call with ``_lock`` held."""
    moment = now()
    for job_id, job in list(_jobs.items()):
        if job.finished_at is not None and moment - job.finished_at > KEEP_FINISHED:
            del _jobs[job_id]


def is_running() -> bool:
    """Whether a check or a run is running, or a takeover writes its companion files."""
    with _lock:
        return _phases["active"] > 0 or any(job.state == "running" for job in _jobs.values())


@contextmanager
def takeover_phase() -> Iterator[None]:
    """A takeover's phase ``companions``: no check or run starts while it runs."""
    with _lock:
        _phases["active"] += 1
    try:
        yield
    finally:
        with _lock:
            _phases["active"] -= 1


def start(kind: str) -> dict[str, Any]:
    """Run a check or a backfill in a thread of its own. Answers at once with the running job.

    Raises ``CompanionJobRunning``, and for a backfill ``CompanionsOff``.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    if kind == "backfill":
        with SessionLocal() as db:
            if not companions.enabled(db):
                raise CompanionsOff
    from .rename import jobs as rename_jobs

    with _lock:
        _expire()
        busy = _phases["active"] > 0 or any(job.state == "running" for job in _jobs.values())
        if busy or rename_jobs.is_running():
            raise CompanionJobRunning
        _counter["last"] += 1
        job = Job(id=_counter["last"], kind=kind, started_at=now())
        _jobs[job.id] = job
        thread = threading.Thread(
            target=contextvars.copy_context().run,
            args=(_execute, job),
            name=f"companions-{job.id}",
            daemon=True,
        )
        _threads[:] = [existing for existing in _threads if existing.is_alive()]
        _threads.append(thread)
        try:
            thread.start()
        except BaseException:
            del _jobs[job.id]
            raise
    logger.info("Companion %s %d started", kind, job.id)
    return snapshot(job)


def snapshot(job: Job) -> dict[str, Any]:
    with job.lock:
        return {
            "id": job.id,
            "kind": job.kind,
            "state": job.state,
            "progress": {"done": job.done, "total": job.total},
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
        _phases["active"] = 0


def _execute(job: Job) -> None:
    started = time.perf_counter()
    fields: dict[str, Any]
    try:
        fields = {"state": "done", "result": _work(job)}
    except CompanionsOff:
        fields = {"state": "failed", "error_code": "companions_off"}
    except Exception:
        logger.exception("Companion %s %d failed unexpectedly", job.kind, job.id)
        fields = {"state": "failed", "error_code": "internal_error"}
    job.set(finished_at=now(), **fields)
    logger.info(
        "Companion %s %d %s in %dms%s",
        job.kind,
        job.id,
        fields["state"],
        (time.perf_counter() - started) * 1000,
        f": {fields['error_code']}" if fields["state"] == "failed" else "",
    )


# --- The work -------------------------------------------------------------------------------------------------- #


def owned_version_ids(db: OrmSession) -> list[int]:
    """Every movie version nexcrate owns with a file, by id. Series have ``series_version_ids``; albums none yet."""
    rows = db.scalars(
        select(Version)
        .join(VersionDefinition, VersionDefinition.id == Version.version_definition_id)
        .where(
            VersionDefinition.kind == "movie",
            Version.source_id.is_(None),
            Version.has_file.is_(True),
            Version.file_ref.is_not(None),
        )
        .order_by(Version.id)
    )
    return [version.id for version in rows if companions.owned(version)]


def empty_counts() -> dict[str, int]:
    return {state: 0 for state in companions.STATES}


def series_version_ids(db: OrmSession) -> list[int]:
    """Every series version of nexcrate's own with a series folder (P4)."""
    rows = db.execute(
        select(Version.id)
        .join(VersionDefinition, VersionDefinition.id == Version.version_definition_id)
        .where(
            VersionDefinition.kind == "series",
            Version.source_id.is_(None),
            Version.relative_path.is_not(None),
        )
        .order_by(Version.id)
    ).tuples()
    return [version_id for (version_id,) in rows]


def _series_part(
    job: Job, version_ids: list[int], counts: dict[str, int], examples: dict[str, list[dict[str, Any]]], done: int
) -> int:
    """The season folders of the series versions, counted like the movie versions. Returns the progress."""
    for version_id in version_ids:
        if job.kind == "backfill":
            with SessionLocal() as db:
                if not companions.enabled(db):
                    raise CompanionsOff
            states = companions_series.write_version(version_id)
        else:
            states = companions_series.check_version(version_id)
        with SessionLocal() as db:
            version = db.get(Version, version_id)
            title_id = version.title_id if version is not None else None
            folder = version.relative_path if version is not None else None
        for state, count in states.items():
            counts[state] = counts.get(state, 0) + count
            if state not in NO_EXAMPLES:
                listed = examples.setdefault(state, [])
                if len(listed) < EXAMPLES:
                    listed.append({"version_id": version_id, "title_id": title_id, "folder": folder})
        done += 1
        job.set(done=done)
        if job.kind == "backfill" and states.get("written") and WRITE_PAUSE_SECONDS > 0:
            time.sleep(WRITE_PAUSE_SECONDS)
    return done


def _work(job: Job) -> dict[str, Any]:
    with SessionLocal() as db:
        version_ids = owned_version_ids(db)
        series_ids = series_version_ids(db)
        installation = companions.installation_id(db)
    job.set(total=len(version_ids) + len(series_ids))
    counts = empty_counts()
    examples: dict[str, list[dict[str, Any]]] = {}
    for index, version_id in enumerate(version_ids, start=1):
        wrote = False
        with SessionLocal() as db:
            version = db.get(Version, version_id)
            if version is None or not companions.owned(version):
                job.set(done=index)
                continue
            if job.kind == "backfill":
                if not companions.enabled(db):
                    raise CompanionsOff
                state = companions.write_version(version_id, db)
                wrote = state == "written"
            else:
                state = companions.check_version(db, version, installation)
            folder = companions.folder_name_of(version)
            title_id = version.title_id
            db.commit()
        if state in counts:
            counts[state] += 1
            if state not in NO_EXAMPLES:
                listed = examples.setdefault(state, [])
                if len(listed) < EXAMPLES:
                    listed.append({"version_id": version_id, "title_id": title_id, "folder": folder})
        job.set(done=index)
        if wrote and WRITE_PAUSE_SECONDS > 0:
            time.sleep(WRITE_PAUSE_SECONDS)
    _series_part(job, series_ids, counts, examples, len(version_ids))
    logger.info(
        "Companion %s %d: %d versions, %d of them series, %s",
        job.kind,
        job.id,
        len(version_ids) + len(series_ids),
        len(series_ids),
        ", ".join(f"{key}={value}" for key, value in sorted(counts.items()) if value),
    )
    return {"versions": len(version_ids) + len(series_ids), "counts": counts, "examples": examples}


# --- Replace ----------------------------------------------------------------------------------------------------- #


def replace(version_id: int) -> str:
    """Move the version's refused ``release.nex`` into the recycle folder, then write nexcrate's file.

    Returns ``written`` or the state that stopped it. Raises ``VersionMissing``, ``CompanionsOff`` or
    ``NotReplaceable``. Only for a version nexcrate owns whose stored state is one of ``companions.REPLACEABLE``.
    """
    moment = now()
    with SessionLocal() as db:
        version = db.get(Version, version_id)
        if version is None:
            raise VersionMissing(version_id)
        if not companions.enabled(db):
            raise CompanionsOff
        if not companions.owned(version) or version.companion_state not in companions.REPLACEABLE:
            raise NotReplaceable(version.companion_state)
        located = companions.locate(version)
        if located.folder is None or located.problem is not None:
            state = located.problem or "folder_missing"
        elif not companions.recycle_file(located.folder, version.root_folder, moment):
            state = "failed"
        else:
            state = companions.write_version(version_id, db)
        if state != "written":
            version.companion_state = state
            version.updated_at = moment
        db.commit()
    logger.info("Version %d: release.nex replaced: %s", version_id, state)
    return state
