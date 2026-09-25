"""The TRaSH Guides state nexcrate works with: bundled or fetched, the daily check, adopting a newer one.

* **Bundled:** ``app/data/trash-radarr.json``, built by ``tools/trash_snapshot.py``. The start needs no internet.
  Series have ``app/data/trash-sonarr.json`` from the same tool and commit, loaded with ``current("series")``.
* **Fetched:** ``data/trash/trash-radarr.json`` and ``trash-sonarr.json`` in the data directory, so they
  survive a new container. A readable fetched state wins over the bundled one; a broken one is logged loudly
  and ignored.
* **One state for both apps** (decision 2 of the design notes): one commit, one daily check, one
  download, both files out of the same tarball. The daily check (job ``trash_check``) asks for the newest
  commit under ``docs/json/radarr`` and under ``docs/json/sonarr``, two API calls a day, and keeps the newer
  one, only while ``updates_enabled``. It only looks, it never fetches: a state that changed on its own would
  shift profiles silently.
* **Adopting** is the owner's explicit step: one tarball download with a size cap, only the needed files read.
  Every stored profile of either kind is built against the new state first; if one fails, the state is refused
  with the labels of the versions and nothing is written. Otherwise both files are written next to their target
  and renamed over them, and the cache is cleared. Profiles keep their rules until they are saved again and are
  ``outdated`` meanwhile.
* **Unchanged rules keep no old commit** (decision 3): a profile whose freshly built rules equal the stored ones
  except for ``trash_commit`` quietly takes the new commit, so a change in Radarr's data does not mark every
  series profile outdated, or the other way round. Nothing about its rules changes.

Settings: ``trash_updates_enabled`` (default on), ``trash_checked_at``, ``trash_latest_commit``,
``trash_latest_date``, ``trash_fetched_commit``, ``trash_fetched_date``. GitHub is contacted only here, through
the clients of ``http_log``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select

from ..config import get_settings
from ..db import SessionLocal, get_setting, set_setting
from ..models import Profile, utcnow
from . import http_log, logs, trash_data
from .trash_data import SnapshotInvalid, TrashSnapshot

logger = logging.getLogger("nexcrate.trash")

#: The snapshot file per kind of title, bundled under ``app/data`` and fetched under ``data/trash``.
FILE_NAMES = {"movie": "trash-radarr.json", "series": "trash-sonarr.json"}
BUNDLED_FILES = {kind: Path(__file__).resolve().parent.parent / "data" / name for kind, name in FILE_NAMES.items()}
BUNDLED = BUNDLED_FILES["movie"]
FILE_NAME = FILE_NAMES["movie"]
REPOSITORY_URL = trash_data.SOURCE_URL
COMMITS_URL = "https://api.github.com/repos/TRaSH-Guides/Guides/commits"
TARBALL_URL = "https://codeload.github.com/TRaSH-Guides/Guides/tar.gz/{commit}"
CHECK_JOB = "trash_check"
CHECK_INTERVAL_SECONDS = 24 * 3600
API_TIMEOUT = httpx.Timeout(15.0, connect=8.0)
DOWNLOAD_TIMEOUT = httpx.Timeout(180.0, connect=10.0)
#: The tarball is about 25 MB for 3 MB of JSON; far more is not what GitHub normally sends.
TARBALL_MAX_BYTES = 120 * 1024 * 1024

SETTING_UPDATES_ENABLED = "trash_updates_enabled"
SETTING_CHECKED_AT = "trash_checked_at"
SETTING_LATEST_COMMIT = "trash_latest_commit"
SETTING_LATEST_DATE = "trash_latest_date"
SETTING_FETCHED_COMMIT = "trash_fetched_commit"
SETTING_FETCHED_DATE = "trash_fetched_date"
#: Older than any commit date, for comparing two dates of which one may be unreadable.
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def now() -> datetime:
    return utcnow()


class TrashUnreachable(Exception):
    """GitHub did not answer as expected."""


class TrashUpdateInvalid(Exception):
    """The download is no usable TRaSH state."""


class TrashUpdateBreaks(Exception):
    """The new state cannot build these profiles."""

    def __init__(self, versions: list[str]) -> None:
        super().__init__(", ".join(versions))
        self.versions = versions


# --- The state in use ---------------------------------------------------------------------- #

_lock = threading.Lock()
#: The loaded state per kind.
_current: dict[str, TrashSnapshot] = {}
_adopting = threading.Lock()


def fetched_path(kind: str = "movie") -> Path:
    return get_settings().data_dir / "trash" / FILE_NAMES[kind]


def _load(kind: str = "movie") -> TrashSnapshot:
    fetched = fetched_path(kind)
    if fetched.is_file():
        try:
            return TrashSnapshot(trash_data.read_snapshot(fetched), origin="fetched")
        except (SnapshotInvalid, OSError) as exc:
            logger.error("The fetched TRaSH state for %s cannot be read (%s); the bundled state is used", kind, exc)
    return TrashSnapshot(trash_data.read_snapshot(BUNDLED_FILES[kind]), origin="bundled")


def current(kind: str = "movie") -> TrashSnapshot:
    """The TRaSH state in use for one kind of title, loaded once. An unknown kind raises ``KeyError``."""
    if kind not in FILE_NAMES:
        raise KeyError(kind)
    with _lock:
        if kind not in _current:
            _current[kind] = _load(kind)
        return _current[kind]


def clear_cache() -> None:
    with _lock:
        _current.clear()


def loaded_origin(kind: str = "movie") -> str | None:
    """``bundled`` or ``fetched`` for the state in memory, None before the first use."""
    with _lock:
        snapshot = _current.get(kind)
        return snapshot.origin if snapshot is not None else None


def is_outdated(trash_commit: str | None) -> bool:
    return trash_commit != current().commit


# --- Settings and the answer of GET /api/trash --------------------------------------------------- #


def _parse_time(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def updates_enabled() -> bool:
    with SessionLocal() as db:
        return get_setting(db, SETTING_UPDATES_ENABLED, "1") != "0"


def set_updates_enabled(enabled: bool) -> None:
    with SessionLocal() as db:
        set_setting(db, SETTING_UPDATES_ENABLED, "1" if enabled else "0")
        db.commit()
    logger.info("Daily TRaSH Guides check switched %s", "on" if enabled else "off")


def state() -> dict[str, Any]:
    snapshot = current()
    with SessionLocal() as db:
        enabled = get_setting(db, SETTING_UPDATES_ENABLED, "1") != "0"
        checked_at = _parse_time(get_setting(db, SETTING_CHECKED_AT))
        latest_commit = get_setting(db, SETTING_LATEST_COMMIT) or None
        latest_date = _parse_time(get_setting(db, SETTING_LATEST_DATE))
    current_date = _parse_time(snapshot.date)
    update_available = bool(
        latest_commit
        and latest_commit != snapshot.commit
        and latest_date is not None
        and current_date is not None
        and latest_date > current_date
    )
    return {
        "commit": snapshot.commit,
        "date": current_date or snapshot.date,
        "source": snapshot.origin,
        "update_available": update_available,
        "latest_commit": latest_commit,
        "checked_at": checked_at,
        "updates_enabled": enabled,
        "license": snapshot.license,
        "copyright": snapshot.copyright,
        "url": REPOSITORY_URL,
    }


# --- GitHub ---------------------------------------------------------------------------------- #


def _client(*, log_bodies: bool, timeout: httpx.Timeout) -> httpx.AsyncClient:
    return http_log.client(
        "github",
        timeout=timeout,
        follow_redirects=True,
        log_bodies=log_bodies,
        headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
    )


async def _latest_under(path: tuple[str, ...]) -> tuple[str, str]:
    """The newest commit that touched one folder of TRaSH's data, and its date: one API call."""
    params = {"path": "/".join(path), "per_page": 1}
    try:
        async with _client(log_bodies=True, timeout=API_TIMEOUT) as client:
            response = await http_log.send(client, "GET", COMMITS_URL, params=params)
    except httpx.RequestError as exc:
        raise TrashUnreachable(type(exc).__name__) from exc
    if response.status_code != 200:
        raise TrashUnreachable(f"HTTP {response.status_code}")
    try:
        entries = response.json()
        entry = entries[0]
        sha = str(entry["sha"]).lower()
        date = str(entry["commit"]["committer"]["date"])
    except (ValueError, LookupError, TypeError) as exc:
        raise TrashUnreachable("unexpected answer") from exc
    if not trash_data.COMMIT.match(sha) or _parse_time(date) is None:
        raise TrashUnreachable("unexpected answer")
    return sha, date


async def latest_commit() -> tuple[str, str]:
    """The newest commit that touched the data of either app, and its date (decision 2): one API call per app."""
    newest: tuple[str, str] | None = None
    for path in trash_data.DATA_PATHS.values():
        found = await _latest_under(path)
        if newest is None or (_parse_time(found[1]) or _EPOCH) > (_parse_time(newest[1]) or _EPOCH):
            newest = found
    if newest is None:
        raise TrashUnreachable("no data folder to ask about")
    return newest


def _store_latest(sha: str, date: str) -> None:
    with SessionLocal() as db:
        set_setting(db, SETTING_LATEST_COMMIT, sha)
        set_setting(db, SETTING_LATEST_DATE, date)
        set_setting(db, SETTING_CHECKED_AT, now().isoformat())
        db.commit()


async def check() -> None:
    sha, date = await latest_commit()
    await asyncio.to_thread(_store_latest, sha, date)
    logger.info("TRaSH Guides checked: newest data commit %s of %s", sha[:12], date)


async def check_job() -> None:
    """The daily job. Nothing happens while the check is switched off."""
    token = logs.bind_request(secrets.token_hex(4))
    try:
        if not await asyncio.to_thread(updates_enabled):
            return
        try:
            await check()
        except TrashUnreachable as exc:
            logger.info("Checking the TRaSH Guides failed (%s); the state in use stays", exc)
    finally:
        logs.unbind_request(token)


async def _download(commit: str) -> bytes:
    url = TARBALL_URL.format(commit=commit)
    chunks: list[bytes] = []
    size = 0
    client = _client(log_bodies=False, timeout=DOWNLOAD_TIMEOUT)
    try:
        async with client, client.stream("GET", url) as response:
            if response.status_code != 200:
                raise TrashUnreachable(f"HTTP {response.status_code}")
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > TARBALL_MAX_BYTES:
                    raise TrashUpdateInvalid("the tarball is larger than the limit")
                chunks.append(chunk)
    except httpx.TransportError as exc:
        http_log.unreachable("github", "GET", url, exc)
        raise TrashUnreachable(type(exc).__name__) from exc
    except httpx.RequestError as exc:
        raise TrashUnreachable(type(exc).__name__) from exc
    return b"".join(chunks)


# --- Adopting -------------------------------------------------------------------------------- #


def _same_rules(stored: Any, built: dict[str, Any]) -> bool:
    """Whether two rule sets differ in nothing but their ``trash_commit`` (decision 3)."""
    if not isinstance(stored, dict):
        return False
    left = {key: value for key, value in stored.items() if key != "trash_commit"}
    right = {key: value for key, value in built.items() if key != "trash_commit"}
    return left == right


@dataclass(frozen=True)
class ProfileCheck:
    """What a new TRaSH state would mean for the stored profiles."""

    #: Names of the profiles that cannot be built with it.
    broken: list[str]
    #: Ids of the profiles whose rules stay the same, so they may take the new commit quietly.
    unchanged: list[int]


def check_profiles(snapshots: dict[str, TrashSnapshot]) -> ProfileCheck:
    """Build every stored profile against the new state of its own kind."""
    from . import profiles

    with SessionLocal() as db:
        rows = list(db.scalars(select(Profile).order_by(Profile.kind, Profile.name)))
    broken: list[str] = []
    unchanged: list[int] = []
    for profile in rows:
        if profile.kind not in FILE_NAMES:
            # The music profile is built without the TRaSH Guides: no state of them can break it.
            continue
        snapshot = snapshots.get(profile.kind)
        try:
            if snapshot is None:
                raise profiles.KindUnsupported(profile.kind)
            built = profiles.build(profile.kind, profile.answers, snapshot)
        except (profiles.ProfileBuildError, profiles.AnswersInvalid, profiles.KindUnsupported) as exc:
            logger.warning("The new TRaSH state cannot build the profile %s: %s", profile.name, exc)
            broken.append(profile.name)
            continue
        if _same_rules(profile.rules, built.rules):
            unchanged.append(profile.id)
    return ProfileCheck(broken=broken, unchanged=unchanged)


def broken_profiles(snapshot: TrashSnapshot) -> list[str]:
    """The labels of ``check_profiles`` for one movie state; kept for the tests of step 2b."""
    return check_profiles({"movie": snapshot}).broken


def _stamp_unchanged(profile_ids: list[int], commit: str) -> int:
    """Write the new commit onto the profiles whose rules did not change (decision 3)."""
    if not profile_ids:
        return 0
    with SessionLocal() as db:
        rows = list(db.scalars(select(Profile).where(Profile.id.in_(profile_ids))))
        for profile in rows:
            if isinstance(profile.rules, dict):
                profile.rules = {**profile.rules, "trash_commit": commit}
            profile.trash_commit = commit
        db.commit()
    return len(rows)


def _write(snapshot: dict[str, Any], kind: str = "movie") -> None:
    target = fetched_path(kind)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{FILE_NAMES[kind]}.{secrets.token_hex(6)}.tmp"
    try:
        temporary.write_bytes(trash_data.snapshot_bytes(snapshot))
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _write_all(snapshots: dict[str, dict[str, Any]]) -> None:
    """Both files, each written next to its target and renamed over it."""
    for kind, snapshot in snapshots.items():
        _write(snapshot, kind)


def _store_adopted(snapshot: dict[str, Any]) -> None:
    with SessionLocal() as db:
        set_setting(db, SETTING_FETCHED_COMMIT, snapshot["commit"])
        set_setting(db, SETTING_FETCHED_DATE, snapshot["date"])
        set_setting(db, SETTING_LATEST_COMMIT, snapshot["commit"])
        set_setting(db, SETTING_LATEST_DATE, snapshot["date"])
        set_setting(db, SETTING_CHECKED_AT, now().isoformat())
        db.commit()


async def adopt() -> None:
    """Fetch the newest state, check every profile against it, then use it.

    Raises ``TrashUnreachable``, ``TrashUpdateInvalid`` or ``TrashUpdateBreaks``; on each of them nothing changed.
    """
    await asyncio.to_thread(_adopting.acquire)
    try:
        sha, date = await latest_commit()
        in_use = await asyncio.to_thread(current)
        if sha == in_use.commit:
            await asyncio.to_thread(_store_latest, sha, date)
            logger.info("TRaSH Guides: the state in use is the newest one")
            return
        raw = await _download(sha)
        built: dict[str, dict[str, Any]] = {}
        try:
            for kind in FILE_NAMES:
                built[kind] = await asyncio.to_thread(trash_data.build_snapshot, raw, kind=kind, expected_commit=sha)
        except SnapshotInvalid as exc:
            logger.warning("The downloaded TRaSH state was refused: %s", exc)
            raise TrashUpdateInvalid(str(exc)) from exc
        candidates = {kind: TrashSnapshot(snapshot, origin="fetched") for kind, snapshot in built.items()}
        checked = await asyncio.to_thread(check_profiles, candidates)
        if checked.broken:
            logger.warning(
                "The TRaSH state %s was refused: %d profiles cannot be built with it", sha[:12], len(checked.broken)
            )
            raise TrashUpdateBreaks(checked.broken)
        await asyncio.to_thread(_write_all, built)
        await asyncio.to_thread(_store_adopted, built["movie"])
        clear_cache()
        stamped = await asyncio.to_thread(_stamp_unchanged, checked.unchanged, sha)
        logger.info(
            "TRaSH Guides state %s of %s adopted for %d apps, %d profiles unchanged",
            sha[:12],
            built["movie"]["date"],
            len(built),
            stamped,
        )
    finally:
        _adopting.release()
