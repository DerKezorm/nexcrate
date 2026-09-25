"""SQLite connection and the schema upkeep at start.

There is no Alembic. New tables come from ``create_all``, new columns from
``_add_missing_columns``, new indexes from ``_add_missing_indexes``. What those cannot do
(a column that becomes nullable) is a numbered migration in ``migrations.py``, run before
them. Renaming or dropping never happens automatically.

⚠️ An existing database is copied before any of that changes it: ``services.backups.create`` with the kind
``update`` into ``data/backups/``, pruned with the other automatic copies. A fresh installation is not copied; a copy
of an empty database protects nothing and takes one of the kept slots.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import traceback
from collections.abc import Iterator
from enum import Enum
from pathlib import Path
from typing import Any

from sqlalchemy import URL, Column, Engine, create_engine, event, inspect, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool
from sqlalchemy.sql.elements import TextClause

from . import migrations
from .config import get_settings
from .models import Base, Setting

logger = logging.getLogger("nexcrate.db")

_settings = get_settings()
_settings.data_dir.mkdir(parents=True, exist_ok=True)


#: The WAL file is cut back to this size after a checkpoint. Without the last connection closing (see ``hold_open``)
#: nothing else would ever shrink it after a large import.
WAL_SIZE_LIMIT = 64 * 1024 * 1024
#: How long a writer waits for the one before it. SQLite has a single writer; readers never wait in WAL mode, so this
#: only queues writers. ⚠️ It was 5 s until 20.09.2026, and that was too little: on the owner's instance four undone
#: takeovers, their imports and the background jobs wrote at once, and three writes gave up with "database is locked"
#: (the release.nex files of a takeover, the closing of its run, and a whole Lidarr import).
BUSY_TIMEOUT_MS = 30_000


def _pragmas(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute(f"PRAGMA journal_size_limit={WAL_SIZE_LIMIT}")
    cursor.close()


#: A write that holds SQLite's lock, or waits for it, longer than this is named in the log, with the place it began
#: (finding 3b of 22.09.2026: nothing told who held the lock when an import gave up after ``BUSY_TIMEOUT_MS``).
HELD_WARNING_SECONDS = 10.0
_WRITE = re.compile(r"^\s*(INSERT|UPDATE|DELETE|REPLACE)\b", re.IGNORECASE)
_APP_FOLDER = str(Path(__file__).resolve().parent)


def _where() -> str:
    """The innermost frame of nexcrate's own code outside this module, and the thread, for the log."""
    for frame in reversed(traceback.extract_stack()):
        filename = str(Path(frame.filename).resolve()) if frame.filename else ""
        if filename.startswith(_APP_FOLDER) and not filename.endswith("db.py"):
            relative = Path(filename).relative_to(_APP_FOLDER).as_posix()
            return f"{relative}:{frame.lineno} {frame.name}, thread {threading.current_thread().name}"
    return f"thread {threading.current_thread().name}"


_holders_lock = threading.Lock()
#: Writes that hold the lock now, by connection: where each began and since when. All writers live in this process.
_holders: dict[int, tuple[str, float]] = {}


def _others(own: int, moment: float) -> str:
    with _holders_lock:
        found = [(where, moment - since) for key, (where, since) in _holders.items() if key != own]
    if not found:
        return "nobody nexcrate knows (a copy, a checkpoint or another program)"
    return "; ".join(f"{where}, for {held:.1f} s" for where, held in sorted(found, key=lambda item: -item[1]))


def _first_write(conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
    if "write_try" not in conn.info and _WRITE.match(statement):
        moment = time.monotonic()
        conn.info["write_try"] = moment
        conn.info["write_where"] = _where()
        # Who holds the lock while this write may have to wait: named when the wait is long.
        conn.info["write_others"] = _others(id(conn.info), moment)


def _write_started(conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
    """The first write went through: from now on this connection holds SQLite's write lock."""
    if "write_since" in conn.info or "write_try" not in conn.info:
        return
    moment = time.monotonic()
    conn.info["write_since"] = moment
    waited = moment - conn.info["write_try"]
    with _holders_lock:
        _holders[id(conn.info)] = (conn.info.get("write_where", ""), moment)
    if waited > HELD_WARNING_SECONDS:
        logger.warning(
            "A write waited %.1f s for the database, in %s; it was held by %s",
            waited,
            conn.info.get("write_where", ""),
            conn.info.get("write_others", ""),
        )


def _write_ended(conn: Any) -> None:
    tried = conn.info.pop("write_try", None)
    since = conn.info.pop("write_since", None)
    where = conn.info.pop("write_where", "")
    others = conn.info.pop("write_others", "")
    with _holders_lock:
        _holders.pop(id(conn.info), None)
    moment = time.monotonic()
    if since is None:
        if tried is not None and moment - tried > HELD_WARNING_SECONDS:
            logger.warning(
                "A write gave up after %.1f s waiting for the database, in %s; it was held by %s",
                moment - tried,
                where,
                others,
            )
        return
    held = moment - since
    if held > HELD_WARNING_SECONDS:
        logger.warning("A write held the database for %.1f s; it began in %s", held, where)


def database_locked(exc: BaseException) -> bool:
    """SQLite gave up waiting for another writer (``database is locked``): nothing is broken, the write can come
    later. Looks through the causes, since SQLAlchemy wraps the driver's error."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if "database is locked" in str(current).lower():
            return True
        current = current.__cause__ or current.__context__
    return False


def watch_writes(bind: Engine) -> None:
    """Name every write that holds the lock for longer than ``HELD_WARNING_SECONDS``, and every write that waits that
    long, with the writes that held the lock meanwhile (22.09.2026: the first version counted the wait as holding)."""
    event.listen(bind, "before_cursor_execute", _first_write)
    event.listen(bind, "after_cursor_execute", _write_started)
    event.listen(bind, "commit", _write_ended)
    event.listen(bind, "rollback", _write_ended)


def make_engine(path: Path) -> Engine:
    # ⚠️ No pool with an upper limit (lesson from nexbeat). A request holds its connection
    # while it waits for Radarr; with the default pool the sixteenth request blocked the
    # event loop for everybody. Opening a SQLite connection costs a fraction of a millisecond.
    # ⚠️ hide_parameters: the log mode "trace" writes SQLAlchemy's statements, and without this
    # also their values. A password change would put the new bcrypt hash into the log, a new
    # session its token hash (measured on the test bench on 13.09.2026: every value was there).
    new_engine = create_engine(
        URL.create("sqlite", database=str(path)),
        connect_args={"check_same_thread": False, "timeout": 5},
        poolclass=NullPool,
        hide_parameters=True,
    )
    event.listen(new_engine, "connect", _pragmas)
    watch_writes(new_engine)
    return new_engine


engine = make_engine(_settings.database_path)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


_held: Any = None


def hold_open(bind: Engine | None = None) -> None:
    """Keep one connection open for as long as the app runs.

    ⚠️ With ``NullPool`` every request closes its connection, and whenever the last one of the process closes,
    SQLite checkpoints the whole WAL into the database and deletes it, under an exclusive lock on the file. Every
    connection opened meanwhile waits in ``_pragmas`` and gives up after 5 s with "database is locked". Seen on the
    owner's instance on 18.09.2026 while the MusicBrainz loader wrote; reproduced with a WAL of 2.6 GB (last close
    13.5 s, the new connection failed after 5.5 s). With one connection that has read the database and stays open,
    no close is ever the last one: the WAL is checkpointed after commits (``wal_autocheckpoint``, never blocking a
    reader) and cut back to ``WAL_SIZE_LIMIT``.
    """
    global _held
    if _held is not None:
        return
    connection = (bind if bind is not None else engine).raw_connection()
    # Reading is what opens the WAL and takes the shared lock; "SELECT 1" never touches the file.
    connection.cursor().execute("SELECT count(*) FROM sqlite_master").fetchall()
    connection.rollback()
    _held = connection


def release() -> None:
    global _held
    if _held is not None:
        _held.close()
        _held = None


def get_db() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


def get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.get(Setting, key)
    return row.value if row is not None else default


def set_setting(db: Session, key: str, value: str) -> None:
    """Stage a setting; the caller commits.

    ⚠️ An upsert, not an insert: two threads may write the same key at the same time (found on the bench on
    17.09.2026, when two series searches recorded TheXEM's state and the second one died with a UNIQUE violation).
    """
    statement = sqlite_insert(Setting).values(key=key, value=value)
    db.execute(statement.on_conflict_do_update(index_elements=[Setting.key], set_={"value": value}))
    known = db.get(Setting, key)
    if known is not None:
        db.expire(known)


# --- Schema upkeep --------------------------------------------------------- #


def init_db(bind: Engine | None = None, backup_dir: Path | None = None) -> list[str]:
    """Bring the database to the schema of the running version. Returns what had to change.

    Order: copy, numbered migrations, new tables, new columns, new indexes, the schema number.
    A fresh installation gets the newest schema and number without a copy.
    """
    target = bind if bind is not None else engine
    backups = backup_dir if backup_dir is not None else _settings.data_dir / "backups"
    if not inspect(target).get_table_names():
        Base.metadata.create_all(target)
        migrations.store_latest(target)
        return []
    changes = [f"migration {migration.number} ({migration.name})" for migration in migrations.due(target)]
    changes.extend(pending_changes(target))
    if changes:
        logger.info("Schema change needed: %s", ", ".join(changes))
        backup_database(target, backups)
    migrations.run(target)
    Base.metadata.create_all(target)
    _add_missing_columns(target)
    _add_missing_indexes(target)
    migrations.store_latest(target)
    return changes


#: The one-time emptying of the TMDB cache from before the slim answers (``tmdb.slim_movie``, 24.09.2026).
SETTING_CACHE_SLIMMED = "tmdb_cache_slimmed"
#: The file is compacted at the start when this much of it is empty, and at least ``COMPACT_MIN_BYTES``.
COMPACT_SHARE = 0.2
COMPACT_MIN_BYTES = 50 * 1024 * 1024


def compact_if_worth(bind: Engine | None = None) -> dict[str, int] | None:
    """At the start, before anything else writes: empty the old, fat TMDB cache once, then give the empty pages back.

    24.09.2026, the owner's installation: 801 MB, of it 312 MB empty pages (SQLite keeps what it once used until
    ``VACUUM``) and 205 MB cached answers, 182 MB of them whole movie answers of which ``fetch_movie`` reads a fraction.
    The cache fills again, slim, on demand. ``VACUUM`` rewrites the file and holds it the whole time; that is why it
    runs here and only when a fifth is empty. Returns the sizes before and after, None when nothing was compacted.
    """
    target = bind if bind is not None else engine
    with target.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        slimmed = connection.execute(
            text("SELECT value FROM settings WHERE key = :key"), {"key": SETTING_CACHE_SLIMMED}
        ).scalar()
        if slimmed != "1":
            removed = connection.execute(text("DELETE FROM tmdb_cache")).rowcount
            connection.execute(
                text("INSERT INTO settings (key, value) VALUES (:key, '1') ON CONFLICT(key) DO UPDATE SET value = '1'"),
                {"key": SETTING_CACHE_SLIMMED},
            )
            logger.info("The TMDB cache from before the slim answers was emptied: %s answers", removed)
        page = connection.exec_driver_sql("PRAGMA page_size").scalar() or 4096
        pages = connection.exec_driver_sql("PRAGMA page_count").scalar() or 0
        free = connection.exec_driver_sql("PRAGMA freelist_count").scalar() or 0
        if not pages or free / pages < COMPACT_SHARE or free * page < COMPACT_MIN_BYTES:
            return None
        before = pages * page
        logger.info(
            "The database is compacted: %.0f MB of %.0f MB are empty; nexcrate starts once it is done",
            free * page / 1048576,
            before / 1048576,
        )
        started = time.monotonic()
        connection.exec_driver_sql("VACUUM")
        after = (connection.exec_driver_sql("PRAGMA page_count").scalar() or 0) * page
    logger.info(
        "The database was compacted from %.0f MB to %.0f MB in %.0f s",
        before / 1048576,
        after / 1048576,
        time.monotonic() - started,
    )
    return {"before": before, "after": after}


def pending_changes(bind: Engine) -> list[str]:
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())
    changes: list[str] = []
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            changes.append(f"table {table.name}")
            continue
        columns = {column["name"] for column in inspector.get_columns(table.name)}
        changes.extend(f"column {table.name}.{column.name}" for column in table.columns if column.name not in columns)
        indexes = {index["name"] for index in inspector.get_indexes(table.name)}
        changes.extend(f"index {index.name}" for index in table.indexes if index.name and index.name not in indexes)
    return changes


def backup_database(bind: Engine, directory: Path) -> Path:
    """A consistent copy before the schema changes, listed and restorable like every other backup.

    ``VACUUM INTO`` reads through the database, so pages still waiting in the WAL file
    are part of the copy. A plain file copy of the main file would miss them and still
    look complete.
    """
    from .services import backups

    target = backups.create(kind=backups.UPDATE, comment="before schema change", bind=bind, base=directory)
    logger.info("Database copied to backups/%s before the schema change", target.name)
    return target


def _sql_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, Enum):
        value = value.value
    return "'" + str(value).replace("'", "''") + "'"


def _column_default(column: Column[Any]) -> str | None:
    server_default = column.server_default
    if server_default is not None:
        argument = getattr(server_default, "arg", None)
        if isinstance(argument, TextClause):
            return argument.text
        if isinstance(argument, str):
            return _sql_literal(argument)
    if column.default is not None:
        argument = getattr(column.default, "arg", None)
        if argument is not None and not callable(argument):
            return _sql_literal(argument)
    return None


def _add_missing_columns(bind: Engine) -> None:
    inspector = inspect(bind)
    with bind.begin() as connection:
        for table in Base.metadata.sorted_tables:
            existing = {column["name"] for column in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                default = _column_default(column)
                if not column.nullable and default is None:
                    # ⚠️ Rather stop loudly than add a required column without a value, which
                    # would make every existing row invalid.
                    raise RuntimeError(
                        f"Column {table.name}.{column.name} is required but has no scalar default. "
                        "Give it a default or make it nullable."
                    )
                column_type = column.type.compile(dialect=bind.dialect)
                statement = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column_type}'
                if not column.nullable:
                    statement += " NOT NULL"
                if default is not None:
                    statement += f" DEFAULT {default}"
                # Table and column names come from our own models, never from outside.
                connection.execute(text(statement))
                logger.info("Added column %s.%s", table.name, column.name)


def _add_missing_indexes(bind: Engine) -> None:
    """⚠️ ``create_all`` creates an index only together with a new table.

    An index added to an existing model later would exist on fresh installations and on
    no grown one, exactly where it is needed. Lesson from nexmail.
    """
    inspector = inspect(bind)
    for table in Base.metadata.sorted_tables:
        existing = {index["name"] for index in inspector.get_indexes(table.name)}
        for index in table.indexes:
            if index.name and index.name not in existing:
                logger.info("Creating index %s", index.name)
                index.create(bind)
