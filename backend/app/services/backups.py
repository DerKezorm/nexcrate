"""Backups: made by hand, on a schedule and before a schema change; downloaded as an encrypted archive; restored at
the next start (the owner's wish of 24.09.2026: "Backup und Restore wie bei Nexview, gleiches look and feel",
the design notes).

Built after Nexview's ``services/sicherung.py``, with its lessons:

* **A copy next to the database is no backup yet.** Both lie on the same volume. ``archive`` makes a downloadable
  AES-ZIP (7-Zip and WinRAR open it without nexcrate; the Windows Explorer cannot); only then is it one.
* **The key goes into the archive.** Every stored credential (TMDB, indexers, download clients, sources) is encrypted
  with ``secret.key``; a database without it comes back with nothing readable. Therefore the archive has a password.
* **What lies in the data folder either goes into the archive or stands in ``NOT_IN_ARCHIVE`` with a reason**
  (``unassigned``, and a test that keeps it so). A new folder is a decision, not an oversight.
* **Caches are emptied in the copy** (``CACHES``): on the owner's installation 205 MB of 801 MB were TMDB answers.
  The running database is never touched.
* **The manifest lies next to the copy** (``<stem>.json``): version, schema fingerprint, kind, comment.
* **Only automatic copies are pruned**; one made by hand stays until the owner deletes it.

Different from Nexview: **a restore happens at the next start**, not in the running process. nexcrate runs imports,
the tracking of downloads, the music loader and searches at the same time, each with state in memory; a database
swapped under them would meet all of it. ``stage_restore`` checks the archive and lays it out in
``backups/restore-pending/``, the process ends (the container restarts it), and ``apply_pending`` swaps the files
before anything opens the database: a copy of the current state first, then database, key and TRaSH state, then
every login ends.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import signal
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path

import pyzipper

from .. import __version__
from ..config import get_settings

logger = logging.getLogger("nexcrate.backups")

FOLDER_NAME = "backups"
MANUAL = "manual"
SCHEDULED = "scheduled"
#: Before a schema change (``db.init_db``) and before a restore.
UPDATE = "update"
KINDS = (MANUAL, SCHEDULED, UPDATE)
AUTOMATIC_KINDS = (SCHEDULED, UPDATE)
#: Automatic copies kept when nothing is set.
KEEP_DEFAULT = 5
KEEP_MIN, KEEP_MAX = 2, 50
SCHEDULES = ("off", "daily", "weekly", "monthly")
#: The owner's answer of 24.09.2026: weekly, at night.
SCHEDULE_DEFAULT = "weekly"
SETTING_SCHEDULE = "backup_schedule"
SETTING_KEEP = "backup_keep"
SETTING_WAITING = "backup_waiting_since"
#: Days between two scheduled copies.
INTERVALS = {"daily": 1, "weekly": 7, "monthly": 30}
#: Local hours a scheduled copy starts in: the copy is long on a NAS and should not run beside downloads.
NIGHT = range(3, 6)
#: A copy overdue by this many days more starts at any hour: a server that is off at night still gets one.
CATCH_UP_DAYS = 1
#: The job looks once an hour.
JOB_NAME = "backups"
INTERVAL_SECONDS = 3600

#: Tables emptied in the copy. They fill again by themselves.
CACHES = ("tmdb_cache",)
#: The copy is rewritten after emptying only when that frees this much: a rewrite of a large file on a NAS takes
#: minutes, and a copy before a schema change is downtime.
SHRINK_MIN_BYTES = 20 * 1024 * 1024
#: The manifest inside the archive.
MANIFEST = "nexcrate-backup.json"
#: What goes into the archive instead of the key when it comes from ``NEXCRATE_SECRET_KEY``.
NO_KEY = (
    "This installation has no secret.key: the key comes from the environment variable NEXCRATE_SECRET_KEY.\n\n"
    "When restoring, the same value must be set again; otherwise the stored credentials of TMDB, the indexers,\n"
    "the download clients and the connections cannot be decrypted.\n"
)
NO_KEY_FILE = "KEY-MISSING.txt"
#: Folders in the data folder that belong to a backup, next to the database. ``trash`` is the fetched TRaSH state:
#: every profile records the state it was built against; without the folder nexcrate falls back to the bundled one
#: and reports differences nobody made.
EXTRAS = ("trash",)
#: The folder of a copy's extras: ``<stem>-files``.
EXTRAS_SUFFIX = "-files"
DATABASE = "nexcrate.db"
KEY = "secret.key"
IN_ARCHIVE = (DATABASE, KEY, *EXTRAS)
#: What lies in the data folder and does not go into the archive, with the reason. ⚠️ A red test here asks for a
#: decision: a new name goes into the archive or here, with its reason.
NOT_IN_ARCHIVE = {
    FOLDER_NAME: "The backups themselves; a backup holding every earlier one grows by itself each time.",
    "logs": "The log describes the running, not the state; weeks old lines in a restored installation mislead.",
    "images": "Copies of posters from the sources; fetched again when shown.",
    "covers": "Album covers from the Cover Art Archive; fetched again when shown.",
    "tmdb-images": "TMDB's posters; fetched again when shown, and TMDB's terms allow no long keeping.",
    "ratings": "IMDb's daily ratings file; fetched again by its daily job.",
    "nexcrate.db-wal": "SQLite's side file; VACUUM INTO writes a consistent copy, another database's WAL would harm.",
    "nexcrate.db-shm": "SQLite's side file, as -wal.",
    "nexcrate.db-journal": "SQLite's side file, as -wal.",
}
#: Where a checked archive waits for the next start.
PENDING = "restore-pending"
#: Upload limit of an archive: the owner's database is 800 MB before compacting.
MAX_UPLOAD = 4 * 1024**3
PASSWORD_MIN = 8
_NAME = re.compile(r"^nexcrate-[A-Za-z0-9_.-]+\.db$")

#: The schedule, a copy by hand and staging a restore share this lock.
_lock = threading.Lock()


class BackupError(Exception):
    """Something is wrong with a backup or an archive. Carries a code for the interface."""

    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(slots=True)
class Manifest:
    version: str
    schema: str
    created: str
    kind: str
    comment: str = ""
    contains: list[str] = field(default_factory=list)
    emptied: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str | bytes) -> Manifest:
        data = json.loads(raw)
        allowed = {item.name for item in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in allowed})


@dataclass(slots=True)
class Entry:
    name: str
    size: int
    created: str
    kind: str
    comment: str
    version: str
    restorable: bool
    reason: str


# --- Places ---------------------------------------------------------------------------------------------------- #


def folder() -> Path:
    return get_settings().data_dir / FOLDER_NAME


def unassigned(data_dir: Path | None = None) -> list[str]:
    """What lies in the data folder without a decision whether it belongs into a backup."""
    root = data_dir or get_settings().data_dir
    if not root.is_dir():
        return []
    known = set(IN_ARCHIVE) | set(NOT_IN_ARCHIVE)
    return sorted(entry.name for entry in root.iterdir() if entry.name not in known)


def _manifest_path(copy: Path) -> Path:
    return copy.with_suffix(".json")


def _extras_path(copy: Path) -> Path:
    return copy.with_name(copy.stem + EXTRAS_SUFFIX)


def path_of(name: str) -> Path:
    """The copy of this name in the backup folder; refuses anything that could lead out of it."""
    if not _NAME.match(name) or "/" in name or "\\" in name or ".." in name:
        raise BackupError("backup_not_found", "There is no such backup.", 404)
    path = folder() / name
    if not path.is_file():
        raise BackupError("backup_not_found", "There is no such backup.", 404)
    return path


# --- Settings -------------------------------------------------------------------------------------------------- #


def schedule(db: object) -> str:
    from ..db import get_setting

    value = get_setting(db, SETTING_SCHEDULE, SCHEDULE_DEFAULT)  # type: ignore[arg-type]
    return value if value in SCHEDULES else SCHEDULE_DEFAULT


def keep(db: object) -> int:
    from ..db import get_setting

    value = get_setting(db, SETTING_KEEP, str(KEEP_DEFAULT))  # type: ignore[arg-type]
    return max(KEEP_MIN, min(KEEP_MAX, int(value))) if value.isdigit() else KEEP_DEFAULT


def _keep_setting() -> int:
    try:
        from ..db import SessionLocal

        with SessionLocal() as db:
            return keep(db)
    except Exception:  # noqa: BLE001 - before the first start there is no settings table
        return KEEP_DEFAULT


# --- Making a copy --------------------------------------------------------------------------------------------- #


def fingerprint(connection: sqlite3.Connection) -> str:
    """A fingerprint over every table and column: what the file looks like, not only who wrote it."""
    parts: list[str] = []
    for (table,) in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall():
        columns = sorted(row[1] for row in connection.execute(f'PRAGMA table_info("{table}")'))
        parts.append(f"{table}({','.join(columns)})")
    return "sha256:" + hashlib.sha256(";".join(parts).encode("utf-8")).hexdigest()[:32]


def _slug(comment: str) -> str:
    """A piece of file name from the comment, or nothing: letters, digits and hyphens only."""
    return re.sub(r"[^\w-]+", "-", comment.strip(), flags=re.UNICODE).strip("-")[:40].lower()


def _empty_caches(copy: Path) -> list[str]:
    emptied: list[str] = []
    connection = sqlite3.connect(copy)
    try:
        present = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in CACHES:
            if table in present:
                # The name comes from the fixed list CACHES, not from outside.
                connection.execute(f'DELETE FROM "{table}"')  # noqa: S608
                emptied.append(table)
        connection.commit()
        page = connection.execute("PRAGMA page_size").fetchone()[0]
        free = connection.execute("PRAGMA freelist_count").fetchone()[0]
        if emptied and free * page >= SHRINK_MIN_BYTES:
            connection.execute("VACUUM")
    finally:
        connection.close()
    return emptied


def _copy_extras(copy: Path) -> list[str]:
    """The TRaSH state as it is now, next to this copy: a four weeks old copy needs the state of then."""
    target = _extras_path(copy)
    target.mkdir(parents=True, exist_ok=True)
    data = get_settings().data_dir
    copied: list[str] = []
    for name in EXTRAS:
        source = data / name
        if not source.is_dir():
            continue
        for item in sorted(source.iterdir()):
            if not item.is_file():
                continue
            try:
                (target / name).mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target / name / item.name)
            except OSError as exc:
                logger.warning("Could not copy %s into the backup: %s", item.name, exc)
                continue
            copied.append(f"{name}/{item.name}")
    return copied


def create(*, kind: str = MANUAL, comment: str = "", bind: object | None = None, base: Path | None = None) -> Path:
    """Write a copy with ``VACUUM INTO`` (consistent also while others write), empty its caches, lay its extras and
    manifest next to it. Automatic copies prune the older automatic ones."""
    from ..db import engine

    if kind not in KINDS:
        raise ValueError(kind)
    target_folder = base or folder()
    target_folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    slug = _slug(comment)
    stem = f"nexcrate-{kind}-{__version__}-{stamp}" + (f"-{slug}" if slug else "")
    target = target_folder / f"{stem}.db"
    counter = 2
    while target.exists():
        target = target_folder / f"{stem}-{counter}.db"
        counter += 1
    source = bind if bind is not None else engine
    started = time.monotonic()
    with source.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:  # type: ignore[attr-defined]
        connection.exec_driver_sql("VACUUM INTO ?", (str(target),))
    emptied = _empty_caches(target)
    extras = _copy_extras(target)
    raw = sqlite3.connect(target)
    try:
        schema = fingerprint(raw)
    finally:
        raw.close()
    manifest = Manifest(
        version=__version__,
        schema=schema,
        created=datetime.now(UTC).isoformat(timespec="seconds"),
        kind=kind,
        comment=comment.strip()[:200],
        contains=[DATABASE, *extras],
        emptied=emptied,
    )
    _manifest_path(target).write_text(manifest.to_json(), encoding="utf-8")
    logger.info(
        "Backup created: %s (%s, %.1f MB in %.0f s)",
        target.name,
        kind,
        target.stat().st_size / 1048576,
        time.monotonic() - started,
    )
    if kind in AUTOMATIC_KINDS:
        prune(_keep_setting(), base=target_folder)
    return target


def remove(copy: Path) -> None:
    """A copy with everything that belongs to it: database, manifest, extras."""
    copy.unlink(missing_ok=True)
    _manifest_path(copy).unlink(missing_ok=True)
    shutil.rmtree(_extras_path(copy), ignore_errors=True)


# --- The list -------------------------------------------------------------------------------------------------- #


def _numbers(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version)) or (0,)


def compatible(version: str) -> tuple[bool, str]:
    """Only a copy of this version or an older one: the migrations only go forward."""
    if not version:
        return False, "unknown_version"
    if _numbers(version) > _numbers(__version__):
        return False, "backup_newer"
    return True, "ok"


_LEGACY = re.compile(r"^nexcrate-(\d{8})-(\d{6})-\d+\.db$")
#: What a copy from before the manifests was made by (``db.backup_database`` until 0.2.0).
LEGACY_VERSION = "0.1.0"


def _manifest_of(copy: Path) -> Manifest:
    """The manifest, or what can be told without one: a copy from before this feature is one made before a schema
    change, of an unknown version."""
    path = _manifest_path(copy)
    if path.is_file():
        try:
            return Manifest.from_json(path.read_text(encoding="utf-8"))
        except ValueError, TypeError:
            logger.warning("The manifest of %s is unreadable", copy.name)
    created = datetime.fromtimestamp(copy.stat().st_mtime, UTC).isoformat(timespec="seconds")
    legacy = _LEGACY.match(copy.name)
    if legacy is None:
        return Manifest(version="", schema="", created=created, kind=UPDATE)
    # A copy from before a schema change of 0.1.0: the only version before backups had manifests.
    created = datetime.strptime(legacy[1] + legacy[2], "%Y%m%d%H%M%S").replace(tzinfo=UTC).isoformat()
    return Manifest(version=LEGACY_VERSION, schema="", created=created, kind=UPDATE)


def _size(copy: Path) -> int:
    total = copy.stat().st_size
    extras = _extras_path(copy)
    if extras.is_dir():
        total += sum(item.stat().st_size for item in extras.rglob("*") if item.is_file())
    return total


def entries(base: Path | None = None) -> list[Entry]:
    """Every copy, newest first."""
    found: list[Entry] = []
    base = base or folder()
    if not base.is_dir():
        return []
    for copy in base.glob("nexcrate-*.db"):
        if not copy.is_file():
            continue
        manifest = _manifest_of(copy)
        restorable, reason = compatible(manifest.version)
        found.append(
            Entry(
                name=copy.name,
                size=_size(copy),
                created=manifest.created,
                kind=manifest.kind if manifest.kind in KINDS else UPDATE,
                comment=manifest.comment,
                version=manifest.version,
                restorable=restorable,
                reason=reason,
            )
        )
    return sorted(found, key=lambda entry: entry.created, reverse=True)


def prune(keep_count: int = KEEP_DEFAULT, base: Path | None = None) -> int:
    """Only the newest ``keep_count`` automatic copies stay; copies by hand are never pruned."""
    base = base or folder()
    automatic = [entry for entry in entries(base) if entry.kind in AUTOMATIC_KINDS]
    removed = 0
    for entry in automatic[keep_count:]:
        try:
            remove(base / entry.name)
            removed += 1
        except OSError:
            logger.warning("Could not remove the old backup %s", entry.name)
    return removed


# --- The schedule ---------------------------------------------------------------------------------------------- #


def _last_scheduled() -> Entry | None:
    return next((entry for entry in entries() if entry.kind == SCHEDULED), None)


def due(every: str, *, now: datetime | None = None, waiting_since: datetime | None = None) -> bool:
    """Whether a scheduled copy is due: the time since the last one comes from the files, not from the database,
    which a restore would set back. Without any scheduled copy the first comes the first night, or a day after the
    job began to wait for one (``waiting_since``) at any hour: a server that is off at night still gets one."""
    if every not in INTERVALS:
        return False
    moment = now or datetime.now().astimezone()
    last = _last_scheduled()
    if last is None:
        waited = (moment - waiting_since).total_seconds() / 86400 if waiting_since is not None else 0
        return moment.hour in NIGHT or waited >= CATCH_UP_DAYS
    try:
        previous = datetime.fromisoformat(last.created)
    except ValueError:
        return True
    if previous.tzinfo is None:
        previous = previous.replace(tzinfo=UTC)
    days = (moment - previous).total_seconds() / 86400
    interval = INTERVALS[every] - 0.25
    return days >= interval and (moment.hour in NIGHT or days >= interval + CATCH_UP_DAYS)


def _waiting_since(db: object) -> datetime:
    """Since when the job waits for the first scheduled copy; noted the first time it finds none."""
    from ..db import get_setting, set_setting

    stored = get_setting(db, SETTING_WAITING, "")  # type: ignore[arg-type]
    try:
        return datetime.fromisoformat(stored)
    except ValueError:
        moment = datetime.now(UTC)
        set_setting(db, SETTING_WAITING, moment.isoformat(timespec="seconds"))  # type: ignore[arg-type]
        db.commit()  # type: ignore[attr-defined]
        return moment


def run_job() -> None:
    """Once an hour: a scheduled copy when one is due."""
    from ..db import SessionLocal

    with _lock:
        sweep_temporary()
        with SessionLocal() as db:
            every = schedule(db)
            waiting_since = _waiting_since(db) if every in INTERVALS and _last_scheduled() is None else None
        if due(every, waiting_since=waiting_since):
            logger.info("Scheduled backup due (%s)", every)
            create(kind=SCHEDULED)


# --- The archive ----------------------------------------------------------------------------------------------- #
#
# Written to and read from files, never held whole in memory: the owner's database was 800 MB before compacting, and a
# NAS has little memory to spare. Temporary files lie in the backup folder with a leading dot (``entries`` ignores
# them); ``sweep_temporary`` removes what a stopped process left behind.

TEMPORARY_PREFIXES = (".archive-", ".upload-")
#: A temporary file older than this was left behind by a stopped process.
TEMPORARY_MAX_AGE = 6 * 3600
_CHUNK = 1024 * 1024


def temporary(prefix: str) -> Path:
    """A fresh temporary file name in the backup folder, on the same volume as the database."""
    base = folder()
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{prefix}{os.getpid()}-{time.time_ns()}.zip"


def sweep_temporary(now: float | None = None) -> int:
    """Remove archives and uploads a stopped process left behind."""
    base = folder()
    if not base.is_dir():
        return 0
    moment = now if now is not None else time.time()
    removed = 0
    for item in base.iterdir():
        stale = item.is_file() and item.name.startswith(TEMPORARY_PREFIXES)
        if stale and moment - item.stat().st_mtime > TEMPORARY_MAX_AGE:
            item.unlink(missing_ok=True)
            removed += 1
    return removed


def archive(name: str, password: str) -> Path:
    """The copy as an AES-ZIP with the key and the TRaSH state of the copy, protected by the password. Returns a
    temporary file; the caller removes it after sending."""
    if len(password) < PASSWORD_MIN:
        raise BackupError("backup_password_short", "The password needs at least 8 characters.", 422)
    copy = path_of(name)
    manifest = _manifest_of(copy)
    settings = get_settings()
    target = temporary(".archive-")
    contains = [DATABASE]
    try:
        with pyzipper.AESZipFile(target, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as zipped:
            zipped.setpassword(password.encode("utf-8"))
            zipped.write(copy, DATABASE)
            if settings.secret_key:
                zipped.writestr(NO_KEY_FILE, NO_KEY)
                contains.append(NO_KEY_FILE)
            elif settings.secret_key_file.is_file():
                zipped.write(settings.secret_key_file, KEY)
                contains.append(KEY)
            extras = _extras_path(copy)
            for item in sorted(extras.rglob("*")) if extras.is_dir() else []:
                if item.is_file():
                    relative = item.relative_to(extras).as_posix()
                    zipped.write(item, relative)
                    contains.append(relative)
            written = Manifest(
                version=manifest.version,
                schema=manifest.schema,
                created=manifest.created,
                kind=manifest.kind,
                comment=manifest.comment,
                contains=contains,
                emptied=manifest.emptied,
            )
            zipped.writestr(MANIFEST, written.to_json())
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return target


@dataclass(slots=True)
class Opened:
    manifest: Manifest
    #: The members to lay out: name in the archive to where under the pending folder.
    members: dict[str, str]
    has_key: bool
    key_from_environment: bool


def _extra_members(names: set[str]) -> dict[str, str]:
    """Our own folders, each file by its plain name: ``trash/../../x`` from a crafted archive lands nowhere."""
    found: dict[str, str] = {}
    for under in EXTRAS:
        for name in names:
            plain = name.rsplit("/", 1)[-1]
            if name.startswith(f"{under}/") and plain not in ("", ".", ".."):
                found[name] = f"files/{under}/{plain}"
    return found


def open_archive(path: Path, password: str) -> Opened:
    """What the archive holds, checked with the password, without laying anything out."""
    try:
        zipped = pyzipper.AESZipFile(path)
    except Exception as exc:
        raise BackupError("restore_not_an_archive", "This is no valid archive.", 422) from exc
    zipped.setpassword(password.encode("utf-8"))
    try:
        names = set(zipped.namelist())
        if MANIFEST not in names or DATABASE not in names:
            raise BackupError("restore_not_a_backup", "The archive holds no nexcrate backup.", 422)
        # Reading a member is what checks the password; the manifest is small.
        raw_manifest = zipped.read(MANIFEST)
        with zipped.open(DATABASE) as member:
            header = member.read(16)
    except BackupError:
        raise
    except Exception as exc:
        raise BackupError("restore_wrong_password", "The password does not fit.", 422) from exc
    finally:
        zipped.close()
    if header != b"SQLite format 3\x00":
        raise BackupError("restore_not_a_database", "The file in the archive is no database.", 422)
    try:
        manifest = Manifest.from_json(raw_manifest)
    except (ValueError, TypeError) as exc:
        raise BackupError("restore_no_manifest", "The archive lacks its manifest.", 422) from exc
    members = {DATABASE: DATABASE, **_extra_members(names)}
    if KEY in names:
        members[KEY] = KEY
    return Opened(manifest, members, KEY in names, NO_KEY_FILE in names)


def check(path: Path, password: str) -> tuple[Opened, bool, str]:
    """Only look: what is it, and may it be restored?"""
    opened = open_archive(path, password)
    restorable, reason = compatible(opened.manifest.version)
    return opened, restorable, reason


def _pending() -> Path:
    return folder() / PENDING


def stage_restore(path: Path, password: str) -> Opened:
    """Check the archive and lay it out for the next start. Raises ``BackupError`` before anything is laid out."""
    opened, _restorable, _reason = check(path, password)
    problem = _refused(opened.manifest)
    if problem is not None:
        raise problem
    with _lock:
        pending = _pending()
        shutil.rmtree(pending, ignore_errors=True)
        (pending / "files").mkdir(parents=True)
        try:
            with pyzipper.AESZipFile(path) as zipped:
                zipped.setpassword(password.encode("utf-8"))
                for member, relative in opened.members.items():
                    target = pending / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zipped.open(member) as source, target.open("wb") as sink:
                        shutil.copyfileobj(source, sink, _CHUNK)
            if (pending / KEY).is_file():
                try:
                    os.chmod(pending / KEY, 0o600)
                except OSError:
                    pass
            # Last: the manifest says the rest is complete. A start finding a folder without it throws it away.
            (pending / MANIFEST).write_text(opened.manifest.to_json(), encoding="utf-8")
        except BaseException:
            shutil.rmtree(pending, ignore_errors=True)
            raise
    logger.info(
        "A backup of version %s from %s waits for the next start", opened.manifest.version, opened.manifest.created
    )
    return opened


def _refused(manifest: Manifest) -> BackupError | None:
    restorable, reason = compatible(manifest.version)
    if restorable:
        return None
    if reason == "backup_newer":
        return BackupError(
            "restore_backup_newer",
            f"This backup comes from version {manifest.version} and is newer than {__version__}.",
        )
    return BackupError("restore_unknown_version", "This backup does not say which version it comes from.")


def stage_local(name: str) -> Manifest:
    """A copy of the list laid out for the next start, without an archive (as Nexview restores from its list).

    The copy lies next to this installation's database, so its key is this installation's own: ``secret.key`` stays as
    it is. The TRaSH state comes from the copy's extras. Raises ``BackupError`` before anything is laid out.
    """
    copy = path_of(name)
    manifest = _manifest_of(copy)
    problem = _refused(manifest)
    if problem is not None:
        raise problem
    with _lock:
        pending = _pending()
        shutil.rmtree(pending, ignore_errors=True)
        (pending / "files").mkdir(parents=True)
        try:
            shutil.copyfile(copy, pending / DATABASE)
            extras = _extras_path(copy)
            for item in sorted(extras.rglob("*")) if extras.is_dir() else []:
                if item.is_file():
                    target = pending / "files" / item.relative_to(extras)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(item, target)
            # Last: the manifest says the rest is complete.
            (pending / MANIFEST).write_text(manifest.to_json(), encoding="utf-8")
        except BaseException:
            shutil.rmtree(pending, ignore_errors=True)
            raise
    logger.info("The backup %s from %s waits for the next start", copy.name, manifest.created)
    return manifest


def restart_soon(delay: float = 1.5) -> None:
    """End the process shortly, after the answer went out; Docker starts it again (``restart: unless-stopped``)."""

    def stop() -> None:
        time.sleep(delay)
        logger.info("nexcrate stops to restore a backup")
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=stop, name="restart-for-restore", daemon=True).start()


def apply_pending() -> bool:
    """At the start, before anything opens the database: swap in a staged backup. Returns whether one was applied.

    A copy of the current state goes first; it is the way back when the restored backup is broken.
    """
    from ..crypto import forget_key

    pending = _pending()
    if not pending.is_dir():
        return False
    if not (pending / MANIFEST).is_file() or not (pending / DATABASE).is_file():
        logger.warning("An incomplete restore was found and thrown away")
        shutil.rmtree(pending, ignore_errors=True)
        return False
    settings = get_settings()
    target = settings.database_path
    manifest = Manifest.from_json((pending / MANIFEST).read_text(encoding="utf-8"))
    if target.is_file():
        try:
            from sqlalchemy import create_engine

            current = create_engine(f"sqlite:///{target}")
            try:
                create(kind=UPDATE, comment="before restore", bind=current)
            finally:
                current.dispose()
        except Exception:
            logger.exception("Could not back up the current state before the restore")
    for suffix in ("-wal", "-shm", "-journal"):
        target.with_name(target.name + suffix).unlink(missing_ok=True)
    shutil.copyfile(pending / DATABASE, target)
    key = pending / KEY
    if key.is_file():
        if settings.secret_key:
            logger.warning(
                "The restored backup holds a secret.key, but NEXCRATE_SECRET_KEY is set: the variable wins. Stored "
                "credentials stay unreadable unless it holds the same value."
            )
        else:
            shutil.copyfile(key, settings.secret_key_file)
            try:
                os.chmod(settings.secret_key_file, 0o600)
            except OSError:
                pass
    files = pending / "files"
    for under in EXTRAS:
        source = files / under
        if source.is_dir():
            destination = settings.data_dir / under
            destination.mkdir(parents=True, exist_ok=True)
            for item in source.iterdir():
                if item.is_file():
                    shutil.copyfile(item, destination / item.name)
    shutil.rmtree(pending, ignore_errors=True)
    forget_key()
    logger.info("Backup restored: version %s from %s", manifest.version, manifest.created)
    return True


def end_sessions() -> None:
    """After a restore nobody is logged in: the sessions in the restored database are of then."""
    from sqlalchemy import delete

    from ..db import SessionLocal
    from ..models import Session

    with SessionLocal() as db:
        db.execute(delete(Session))
        db.commit()
