"""Ratings of IMDb for every title, and of Rotten Tomatoes and Metacritic with the owner's own OMDb key
(N39).

**IMDb** hands out ``title.ratings.tsv.gz`` daily, for personal and non-commercial use, with a credit
(``ATTRIBUTION``). Every installation fetches it itself; nexcrate never ships it. On by default (the owner's answer of
21.09.2026), switched off under Settings, Online services: then nexcrate never reaches IMDb.

**Where it lies:** a SQLite file of its own, ``data/ratings/imdb.db``, built as a new file and put in place only when it
is whole. The daily read never holds the lock of the main database (N37), and a failure leaves yesterday's values.

**OMDb** has Rotten Tomatoes and Metacritic, with a key of the owner's (1,000 requests a day for free, CC BY-NC 4.0).
Answers are kept 30 days in ``data/ratings/omdb.db``; nexcrate counts the day's requests and stops at
``OMDB_DAY_LIMIT``.
⚠️ OMDb takes the key only in the address; the log masks it (``http_log``), and nothing else writes the address.
For series OMDb gives no Rotten Tomatoes or Metacritic value.
"""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import json
import logging
import os
import sqlite3
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from .. import __version__, crypto
from .. import db as database
from ..config import get_settings
from ..models import utcnow
from . import http_log

logger = logging.getLogger("nexcrate.ratings")

IMDB_URL = "https://datasets.imdbws.com/title.ratings.tsv.gz"
OMDB_URL = "https://www.omdbapi.com/"
ATTRIBUTION = "Information courtesy of IMDb (https://www.imdb.com). Used with permission."
OMDB_CREDIT = "Rotten Tomatoes and Metacritic through the OMDb API (https://www.omdbapi.com), CC BY-NC 4.0."
IMDB_FILE = "imdb.db"
OMDB_FILE = "omdb.db"
HEADER = ("tconst", "averageRating", "numVotes")
#: A file with fewer rows is broken or not the file: yesterday's values stay.
MIN_ROWS = 100_000
CHUNK = 5_000

JOB_NAME = "ratings_imdb"
INTERVAL_SECONDS = 3600
LOAD_EVERY = timedelta(hours=24)
RETRY_AFTER = timedelta(hours=3)
IMDB_TIMEOUT = httpx.Timeout(120.0, connect=15.0)
OMDB_TIMEOUT = httpx.Timeout(8.0, connect=4.0)
OMDB_DAY_LIMIT = 950
OMDB_KEEP = timedelta(days=30)
OMDB_KEEP_MISSING = timedelta(days=7)

SETTING_IMDB = "ratings_imdb"
SETTING_IMDB_STATE = "ratings_imdb_state"
SETTING_OMDB_KEY = "omdb_key"

LOOKUP_MAX = 100


class RatingsError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def folder() -> Path:
    # Named here as it stands: the start script knows every name the backend makes in its data folder.
    return get_settings().data_dir / "ratings"


def imdb_path() -> Path:
    return folder() / IMDB_FILE


def omdb_path() -> Path:
    return folder() / OMDB_FILE


def imdb_enabled(db: Any) -> bool:
    return database.get_setting(db, SETTING_IMDB, "on") != "off"


def omdb_key(db: Any) -> str | None:
    stored = database.get_setting(db, SETTING_OMDB_KEY, "")
    return crypto.decrypt(stored) if stored else None


def imdb_state(db: Any) -> dict[str, Any]:
    try:
        value = json.loads(database.get_setting(db, SETTING_IMDB_STATE, "") or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _write_state(changes: dict[str, Any]) -> None:
    with database.SessionLocal() as db:
        state = {**imdb_state(db), **changes}
        database.set_setting(db, SETTING_IMDB_STATE, json.dumps(state))
        db.commit()


# --- IMDb ---------------------------------------------------------------------------------------------------------- #


def _rows(path: Path) -> Iterator[tuple[str, float, int]]:
    """The rows of the gzip file; raises ``RatingsError("imdb_file_broken")`` for a file of another shape."""
    try:
        with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
            header = tuple(handle.readline().rstrip("\r\n").split("\t"))
            if header != HEADER:
                raise RatingsError("imdb_file_broken")
            for line in handle:
                parts = line.rstrip("\r\n").split("\t")
                if len(parts) != 3 or not parts[0].startswith("tt") or "\\N" in parts:
                    continue
                try:
                    yield parts[0], float(parts[1]), int(parts[2])
                except ValueError:
                    continue
    except (OSError, EOFError, UnicodeDecodeError) as exc:
        raise RatingsError("imdb_file_broken") from exc


def build(source: Path, target: Path) -> int:
    """A new ratings file from the download, put in place only when whole. Returns its rows."""
    fresh = target.with_name(target.name + ".new")
    with contextlib.suppress(FileNotFoundError):
        fresh.unlink()
    connection = sqlite3.connect(fresh)
    count = 0
    complete = False
    try:
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        connection.execute(
            "CREATE TABLE imdb (tconst TEXT PRIMARY KEY, rating REAL NOT NULL, votes INTEGER NOT NULL) WITHOUT ROWID"
        )
        batch: list[tuple[str, float, int]] = []
        for row in _rows(source):
            batch.append(row)
            if len(batch) >= CHUNK:
                connection.executemany("INSERT OR REPLACE INTO imdb VALUES (?, ?, ?)", batch)
                count += len(batch)
                batch = []
        if batch:
            connection.executemany("INSERT OR REPLACE INTO imdb VALUES (?, ?, ?)", batch)
            count += len(batch)
        connection.commit()
        complete = count >= MIN_ROWS
    finally:
        connection.close()
        if not complete:
            fresh.unlink(missing_ok=True)
    if not complete:
        raise RatingsError("imdb_file_broken")
    os.replace(fresh, target)
    return count


async def _download(target: Path, known: dict[str, Any]) -> tuple[bool, dict[str, str]]:
    """Fetch the file into ``target`` unless IMDb says it did not change. Returns (changed, validators)."""
    headers = {"User-Agent": f"nexcrate/{__version__}"}
    if isinstance(known.get("etag"), str):
        headers["If-None-Match"] = known["etag"]
    if isinstance(known.get("last_modified"), str):
        headers["If-Modified-Since"] = known["last_modified"]
    async with http_log.client("imdb", timeout=IMDB_TIMEOUT, log_bodies=False) as http:
        try:
            async with http.stream("GET", IMDB_URL, headers=headers) as answer:
                if answer.status_code == 304:
                    return False, {}
                if answer.status_code != 200:
                    raise RatingsError(f"imdb_http_{answer.status_code}")
                with target.open("wb") as handle:
                    async for chunk in answer.aiter_bytes():
                        handle.write(chunk)
                validators = {
                    key: value
                    for key, value in (
                        ("etag", answer.headers.get("etag")),
                        ("last_modified", answer.headers.get("last-modified")),
                    )
                    if value
                }
                return True, validators
        except httpx.HTTPError as exc:
            http_log.unreachable("imdb", "GET", IMDB_URL, exc)
            raise RatingsError("imdb_unreachable") from exc


async def refresh(*, force: bool = False) -> dict[str, Any]:
    """Load IMDb's file when on and due (or ``force``). Never raises; the state says what happened."""
    now = utcnow()
    with database.SessionLocal() as db:
        if not imdb_enabled(db):
            return imdb_state(db)
        state = imdb_state(db)
    if not force:
        for key, wait in (("checked_at", LOAD_EVERY), ("failed_at", RETRY_AFTER)):
            value = state.get(key)
            if isinstance(value, str) and now - datetime.fromisoformat(value) < wait:
                return state
    folder().mkdir(parents=True, exist_ok=True)
    download = folder() / "imdb.tsv.gz.part"
    known = state if imdb_path().is_file() else {}
    try:
        changed, validators = await _download(download, known)
        if changed:
            rows = await asyncio.to_thread(build, download, imdb_path())
            _write_state({"checked_at": now.isoformat(), "loaded_at": now.isoformat(), "rows": rows,
                          "problem": None, "failed_at": None, **validators})  # fmt: skip
            logger.info("IMDb ratings loaded: %d titles", rows)
        else:
            _write_state({"checked_at": now.isoformat(), "problem": None, "failed_at": None})
            logger.info("IMDb ratings unchanged since the last load")
    except RatingsError as exc:
        _write_state({"failed_at": now.isoformat(), "problem": exc.code})
        logger.warning("IMDb ratings could not be loaded: %s", exc.code)
    except OSError as exc:
        _write_state({"failed_at": now.isoformat(), "problem": "imdb_write_failed"})
        logger.warning("IMDb ratings could not be written: %s", type(exc).__name__)
    finally:
        download.unlink(missing_ok=True)
    with database.SessionLocal() as db:
        return imdb_state(db)


async def run_job() -> None:
    await refresh()


def _read(path: Path) -> sqlite3.Connection | None:
    if not path.is_file():
        return None
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, check_same_thread=False)


def imdb_many(tconsts: Iterable[str]) -> dict[str, tuple[float, int]]:
    """Rating and votes per IMDb number, from the local file; empty without it."""
    wanted = sorted({value for value in tconsts if value})
    connection = _read(imdb_path())
    if connection is None or not wanted:
        if connection is not None:
            connection.close()
        return {}
    found: dict[str, tuple[float, int]] = {}
    try:
        for start in range(0, len(wanted), 500):
            part = wanted[start : start + 500]
            marks = ",".join("?" * len(part))
            for tconst, rating, votes in connection.execute(
                f"SELECT tconst, rating, votes FROM imdb WHERE tconst IN ({marks})",  # noqa: S608 - only marks
                part,
            ):
                found[tconst] = (float(rating), int(votes))
    except sqlite3.DatabaseError:
        logger.warning("The IMDb ratings file cannot be read")
        return {}
    finally:
        connection.close()
    return found


def imdb_source(db: Any) -> str:
    """``loaded``, ``off`` or ``not_loaded``: why a value may be missing."""
    if not imdb_enabled(db):
        return "off"
    return "loaded" if imdb_path().is_file() else "not_loaded"


# --- OMDb -------------------------------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class OmdbValues:
    """What OMDb says of one title; ``state`` is ok, not_found, no_key, key_invalid, limit or failed."""

    state: str
    rotten_tomatoes: int | None = None
    metacritic: int | None = None


def _omdb_db() -> sqlite3.Connection:
    folder().mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(omdb_path(), check_same_thread=False, timeout=10)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS omdb (tconst TEXT PRIMARY KEY, fetched_at TEXT NOT NULL, found INTEGER NOT NULL, "
        "rotten INTEGER, metacritic INTEGER)"
    )
    connection.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    return connection


def omdb_today() -> int:
    """Requests sent to OMDb today (UTC)."""
    if not omdb_path().is_file():
        return 0
    connection = _omdb_db()
    try:
        day, count = _day_count(connection)
        return count if day == utcnow().date().isoformat() else 0
    finally:
        connection.close()


def _day_count(connection: sqlite3.Connection) -> tuple[str, int]:
    row = connection.execute("SELECT value FROM meta WHERE key = 'day'").fetchone()
    if row is None:
        return "", 0
    day, _space, count = str(row[0]).partition(" ")
    return day, int(count) if count.isdigit() else 0


def _count_one(connection: sqlite3.Connection) -> bool:
    """Count a request against today's limit; False when the limit is reached."""
    today = utcnow().date().isoformat()
    day, count = _day_count(connection)
    count = count if day == today else 0
    if count >= OMDB_DAY_LIMIT:
        return False
    connection.execute("INSERT OR REPLACE INTO meta VALUES ('day', ?)", (f"{today} {count + 1}",))
    connection.commit()
    return True


def _percent(value: str) -> int | None:
    text = value.strip().rstrip("%")
    return int(text) if text.isdigit() else None


def _score(value: str) -> int | None:
    head = value.strip().partition("/")[0]
    return int(head) if head.isdigit() else None


def parse_omdb(body: Any) -> OmdbValues:
    """``Ratings`` of an OMDb answer; ``N/A`` and a missing source are null."""
    if not isinstance(body, dict):
        return OmdbValues("failed")
    if str(body.get("Response", "")).lower() != "true":
        text = str(body.get("Error", "")).lower()
        if "api key" in text:
            return OmdbValues("key_invalid")
        if "limit" in text:
            return OmdbValues("limit")
        if "not found" in text or "incorrect imdb" in text:
            return OmdbValues("not_found")
        return OmdbValues("failed")
    rotten = metacritic = None
    for entry in body.get("Ratings") or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("Value"), str):
            continue
        if entry.get("Source") == "Rotten Tomatoes":
            rotten = _percent(entry["Value"])
        elif entry.get("Source") == "Metacritic":
            metacritic = _score(entry["Value"])
    if metacritic is None and isinstance(body.get("Metascore"), str):
        metacritic = _score(body["Metascore"])
    return OmdbValues("ok", rotten, metacritic)


async def ask_omdb(key: str, tconst: str) -> OmdbValues:
    """One request to OMDb, without the cache and without counting. Never raises."""
    async with http_log.client("omdb", timeout=OMDB_TIMEOUT) as http:
        try:
            answer = await http_log.send(http, "GET", OMDB_URL, params={"i": tconst, "apikey": key})
        except httpx.HTTPError:
            return OmdbValues("failed")
    if answer.status_code == 401:
        return OmdbValues("key_invalid")
    try:
        return parse_omdb(answer.json())
    except ValueError:
        return OmdbValues("failed")


def omdb_cached_many(tconsts: Iterable[str]) -> dict[str, OmdbValues]:
    """Rotten Tomatoes and Metacritic of many titles from the 30-day store only, never OMDb: a batch of a hundred would
    eat a tenth of the day's requests. What the store does not hold, or holds too long, is ``not_cached``."""
    wanted = sorted(set(tconsts))
    if not wanted or not omdb_path().is_file():
        return {tconst: OmdbValues("not_cached") for tconst in wanted}
    connection = _omdb_db()
    try:
        now = utcnow()
        found: dict[str, OmdbValues] = {}
        for start in range(0, len(wanted), 500):
            part = wanted[start : start + 500]
            marks = ",".join("?" * len(part))
            for tconst, fetched_at, hit, rotten, metacritic in connection.execute(
                f"SELECT tconst, fetched_at, found, rotten, metacritic FROM omdb WHERE tconst IN ({marks})",  # noqa: S608
                part,
            ):
                if now - datetime.fromisoformat(fetched_at) >= (OMDB_KEEP if hit else OMDB_KEEP_MISSING):
                    continue
                found[tconst] = OmdbValues("ok", rotten, metacritic) if hit else OmdbValues("not_found")
    finally:
        connection.close()
    return {tconst: found.get(tconst, OmdbValues("not_cached")) for tconst in wanted}


async def omdb_one(key: str | None, tconst: str) -> OmdbValues:
    """Rotten Tomatoes and Metacritic of one title, from the cache or OMDb within the day's limit."""
    if not key:
        return OmdbValues("no_key")
    connection = await asyncio.to_thread(_omdb_db)
    try:
        row = connection.execute(
            "SELECT fetched_at, found, rotten, metacritic FROM omdb WHERE tconst = ?", (tconst,)
        ).fetchone()
        now = utcnow()
        if row is not None:
            age = now - datetime.fromisoformat(row[0])
            if age < (OMDB_KEEP if row[1] else OMDB_KEEP_MISSING):
                return OmdbValues("ok", row[2], row[3]) if row[1] else OmdbValues("not_found")
        if not _count_one(connection):
            return OmdbValues("limit")
    finally:
        connection.close()
    values = await ask_omdb(key, tconst)
    if values.state in ("ok", "not_found"):
        connection = await asyncio.to_thread(_omdb_db)
        try:
            connection.execute(
                "INSERT OR REPLACE INTO omdb VALUES (?, ?, ?, ?, ?)",
                (tconst, utcnow().isoformat(), 1 if values.state == "ok" else 0, values.rotten_tomatoes,
                 values.metacritic),
            )  # fmt: skip
            connection.commit()
        finally:
            connection.close()
    return values


#: An IMDb number OMDb knows for sure, for checking a key.
CHECK_TCONST = "tt0133093"


async def check_key(key: str) -> str:
    """``ok`` or the problem of a key: key_invalid, limit or failed."""
    values = await ask_omdb(key, CHECK_TCONST)
    return "ok" if values.state in ("ok", "not_found") else values.state
