"""TheXEM: scene and TVDB numbers for series whose releases count differently (S3.2).

TheXEM (``https://thexem.info/map/``) lists, for about 1,900 TVDB series, every TVDB episode with its scene number.
nexcrate asks it with TVDB as origin, as Sonarr does:

* ``all?id=<tvdb>&origin=tvdb``: the mapping of one series, a list of ``{"scene": {...}, "tvdb": {...}}`` with
  ``season``, ``episode`` and ``absolute``. A series TheXEM does not know answers ``result: failure``: no mapping,
  no error.
* ``allNames?origin=tvdb&seasonNumbers=1``: scene names per TVDB series, ``{"<name>": <season>}``, -1 for all seasons.

**The bridge to TMDB** (decision 9, measured 16.09.2026: 982 episodes right, none wrong): nexcrate has TMDB's
episodes, TheXEM speaks TVDB. When TheXEM lists as many regular episodes as TMDB, the n-th of each, sorted by season
and episode, are the same episode. Otherwise an episode takes TheXEM's entry whose TVDB number equals TMDB's number,
or none. TheXEM's TVDB ``absolute`` is not used: at one measured series it was off for 148 of 404 entries. The
scene's ``absolute`` is kept on the scene row (B6): it is the number Sonarr stores as
``sceneAbsoluteEpisodeNumber`` and reads anime releases by. It mostly starts again with each scene season; that is its
purpose, not a fault (American Dad! has 148 of 405 scene numbers counted through that differ from TVDB's, by design,
measured at the throwaway Sonarr on 23.09.2026).

**Failures** keep the stored numbers and are shown with their own code (decision 15): ``xem_timeout``,
``xem_certificate`` (a filter in the owner's network, typically), ``xem_blocked`` (an HTML page instead of data, a
403 from Cloudflare), ``xem_unreachable``. ⚠️ Requests carry ``User-Agent: nexcrate/<version> ( <project address> )``:
TheXEM answers Python's default agent with a Cloudflare page (measured 16.09.2026), and its operator asked for the
project's address in the agent (answered with 200, measured 25.09.2026).

TheXEM is a way out of the house, so it has a switch (``xem_enabled``, on by default). Off means no request at all.

**Asking as little as Sonarr** (23.09.2026 evening, the design notes, "Nebenbefund: TheXEM sperrt nexcrate"):
TheXEM answered the agent ``nexcrate/…`` with 403 while other agents got 200. nexcrate had asked every series it
refreshed, whether TheXEM maps it or not, and a series whose last answer failed was due again at every search. Now:

* ``havemap?origin=tvdb``, the TVDB numbers TheXEM maps, is fetched once a day; only those series are asked. Sonarr
  does the same. An empty list is no answer: it would take every stored number away.
* After a failure nothing is asked for ``PAUSE_AFTER_FAILURE``, neither by a refresh nor by a search. Switching TheXEM
  off and on again ends the pause.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as OrmSession

from ... import __version__
from ...db import SessionLocal, get_setting, set_setting
from ...models import Episode, EpisodeNumber, Title, XemName, utcnow
from .. import http_log, schreibweisen

logger = logging.getLogger("nexcrate.xem")

BASE_URL = "https://thexem.info/map/"
USER_AGENT = f"nexcrate/{__version__} ( https://github.com/DerKezorm/nexcrate )"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
#: ``allNames`` measured 172 KB; far more is no answer of TheXEM.
MAX_BYTES = 4 * 1024 * 1024
#: A mapping, the names or the list of mapped series older than this are fetched again.
MAX_AGE = timedelta(days=1)
#: After a failure TheXEM is not asked for this long.
PAUSE_AFTER_FAILURE = timedelta(hours=6)
#: TheXEM limits a client to 44 requests in 10 seconds (its operator, 24.09.2026). nexcrate keeps this much time between
#: two of its requests, in the whole process: at most 20 in 10 seconds, however many series a refresh asks for.
SPACING_SECONDS = 0.5
clock = time.monotonic
sleep = asyncio.sleep
_spacing_lock = threading.Lock()
_next_at = 0.0

SETTING_ENABLED = "xem_enabled"
SETTING_LAST_OK = "xem_last_ok_at"
SETTING_LAST_ERROR = "xem_last_error_code"
SETTING_NAMES_AT = "xem_names_at"
SETTING_LAST_ERROR_AT = "xem_last_error_at"
SETTING_LISTED = "xem_listed"
SETTING_LISTED_AT = "xem_listed_at"

#: What ``titles.xem_state`` holds besides a failure code.
MAPPED = "mapped"
NOT_LISTED = "not_listed"
BRIDGE_FAILED = "bridge_failed"
NO_TVDB = "no_tvdb"
CODES = ("xem_timeout", "xem_certificate", "xem_blocked", "xem_unreachable")
ORIGIN = "xem"


class XemError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# --- Settings ------------------------------------------------------------------------------ #


def enabled(db: OrmSession) -> bool:
    return get_setting(db, SETTING_ENABLED, "1") != "0"


def state(db: OrmSession) -> dict[str, Any]:
    return {
        "enabled": enabled(db),
        "last_ok_at": get_setting(db, SETTING_LAST_OK, "") or None,
        "last_error_code": get_setting(db, SETTING_LAST_ERROR, "") or None,
    }


def _record(ok: bool, code: str | None = None) -> None:
    with SessionLocal() as db:
        if ok:
            set_setting(db, SETTING_LAST_OK, utcnow().isoformat())
            set_setting(db, SETTING_LAST_ERROR, "")
            set_setting(db, SETTING_LAST_ERROR_AT, "")
        else:
            set_setting(db, SETTING_LAST_ERROR, code or "xem_unreachable")
            set_setting(db, SETTING_LAST_ERROR_AT, utcnow().isoformat())
        db.commit()


def _since(db: OrmSession, key: str, span: timedelta) -> bool:
    """Whether the moment stored under ``key`` lies within ``span`` before now."""
    stored = get_setting(db, key, "")
    try:
        return bool(stored) and datetime.fromisoformat(stored) > utcnow() - span
    except ValueError:
        return False


def paused(db: OrmSession) -> bool:
    """A failure less than ``PAUSE_AFTER_FAILURE`` ago: nothing is asked."""
    return bool(get_setting(db, SETTING_LAST_ERROR, "")) and _since(db, SETTING_LAST_ERROR_AT, PAUSE_AFTER_FAILURE)


def end_pause(db: OrmSession) -> None:
    """The owner switched TheXEM on: the next refresh or search asks again. The caller commits."""
    set_setting(db, SETTING_LAST_ERROR_AT, "")


# --- Requests ------------------------------------------------------------------------------ #


def _client() -> httpx.AsyncClient:
    return http_log.client(
        "xem",
        base_url=BASE_URL,
        timeout=TIMEOUT,
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )


def _failure(exc: httpx.HTTPError) -> XemError:
    if isinstance(exc, httpx.TimeoutException):
        return XemError("xem_timeout")
    cause: BaseException | None = exc
    while cause is not None:
        if isinstance(cause, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in str(cause):
            return XemError("xem_certificate")
        cause = cause.__cause__ or cause.__context__
    return XemError("xem_unreachable")


def reset_spacing() -> None:
    global _next_at
    with _spacing_lock:
        _next_at = 0.0


async def _wait_turn() -> None:
    """Wait until ``SPACING_SECONDS`` after the previous request; callers from several threads queue up."""
    global _next_at
    with _spacing_lock:
        now = clock()
        start = max(now, _next_at)
        _next_at = start + SPACING_SECONDS
    if start > now:
        await sleep(start - now)


async def _get(path: str, params: dict[str, str | int]) -> dict[str, Any]:
    await _wait_turn()
    try:
        async with _client() as client, client.stream("GET", path, params=params) as response:
            declared = response.headers.get("content-length", "").strip()
            if declared.isdigit() and int(declared) > MAX_BYTES:
                raise XemError("xem_blocked")
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_BYTES:
                    raise XemError("xem_blocked")
                chunks.append(chunk)
            status = response.status_code
            kind = response.headers.get("content-type", "").lower()
    except httpx.HTTPError as exc:
        raise _failure(exc) from exc
    body = b"".join(chunks)
    if status in (403, 429, 503) or "html" in kind:
        raise XemError("xem_blocked")
    if status != 200:
        raise XemError("xem_unreachable")
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise XemError("xem_blocked") from exc
    if not isinstance(data, dict) or "result" not in data:
        raise XemError("xem_blocked")
    return data


async def fetch_mapping(tvdb_id: int) -> list[dict[str, Any]] | None:
    """TheXEM's mapping of a series, or None when TheXEM does not know it. Raises ``XemError``."""
    data = await _get("all", {"id": tvdb_id, "origin": "tvdb"})
    if data.get("result") != "success":
        return None
    entries = data.get("data")
    return [entry for entry in entries if isinstance(entry, dict)] if isinstance(entries, list) else []


async def fetch_listed() -> set[int]:
    """The TVDB numbers of the series TheXEM maps. Raises ``XemError``; an empty list is no answer."""
    data = await _get("havemap", {"origin": "tvdb"})
    listed = data.get("data") if data.get("result") == "success" else None
    found = {
        int(value)
        for value in (listed if isinstance(listed, list) else [])
        if (isinstance(value, int) and not isinstance(value, bool)) or (isinstance(value, str) and value.isdigit())
    }
    if not found:
        raise XemError("xem_unreachable")
    return found


async def fetch_names() -> dict[int, list[tuple[str, int | None]]]:
    """Scene names per TVDB series: name and season, None for all seasons. Raises ``XemError``."""
    data = await _get("allNames", {"origin": "tvdb", "seasonNumbers": 1})
    found: dict[int, list[tuple[str, int | None]]] = {}
    listed = data.get("data") if data.get("result") == "success" else None
    for key, names in (listed or {}).items() if isinstance(listed, dict) else ():
        if not str(key).isdigit() or not isinstance(names, list):
            continue
        for item in names:
            if not isinstance(item, dict):
                continue
            for name, season in item.items():
                text = str(name).strip()[:1024]
                if text:
                    usable = isinstance(season, int) and not isinstance(season, bool) and season >= 0
                    number = season if usable else None
                    found.setdefault(int(key), []).append((text, number))
    return found


# --- The bridge (pure) ----------------------------------------------------------------------- #


Numbers = tuple[int, int]


@dataclass(frozen=True)
class Mapped:
    tvdb: Numbers
    scene: Numbers


def _numbers(value: Any) -> Numbers | None:
    if not isinstance(value, dict):
        return None
    season, episode = value.get("season"), value.get("episode")
    if isinstance(season, int) and isinstance(episode, int) and season >= 0 and episode >= 0:
        return season, episode
    return None


def parse_entries(entries: list[dict[str, Any]]) -> dict[Numbers, Numbers]:
    """TVDB number to scene number, for every usable entry."""
    found: dict[Numbers, Numbers] = {}
    for entry in entries:
        tvdb, scene = _numbers(entry.get("tvdb")), _numbers(entry.get("scene"))
        if tvdb is not None and scene is not None:
            found.setdefault(tvdb, scene)
    return found


def parse_scene_absolute(entries: list[dict[str, Any]]) -> dict[Numbers, int]:
    """TVDB number to the scene's number counted through, for every entry that has one above 0."""
    found: dict[Numbers, int] = {}
    for entry in entries:
        tvdb, scene = _numbers(entry.get("tvdb")), entry.get("scene")
        number = scene.get("absolute") if isinstance(scene, dict) else None
        if tvdb is not None and isinstance(number, int) and not isinstance(number, bool) and number > 0:
            found.setdefault(tvdb, number)
    return found


def bridge(episodes: list[tuple[int, int, int]], entries: dict[Numbers, Numbers]) -> dict[int, Mapped]:
    """TMDB episodes ``(id, season, episode)`` to TheXEM's TVDB and scene numbers (decision 9)."""
    regular = sorted((row for row in episodes if row[1] > 0), key=lambda row: (row[1], row[2]))
    listed = sorted(numbers for numbers in entries if numbers[0] > 0)
    result: dict[int, Mapped] = {}
    if regular and len(listed) == len(regular):
        for (episode_id, _season, _episode), tvdb in zip(regular, listed, strict=True):
            result[episode_id] = Mapped(tvdb=tvdb, scene=entries[tvdb])
    else:
        for episode_id, season, episode in regular:
            if (season, episode) in entries:
                result[episode_id] = Mapped(tvdb=(season, episode), scene=entries[(season, episode)])
    for episode_id, season, episode in episodes:
        if season == 0 and (0, episode) in entries:
            result[episode_id] = Mapped(tvdb=(0, episode), scene=entries[(0, episode)])
    return result


# --- Storing --------------------------------------------------------------------------------- #


def store_mapping(
    db: OrmSession,
    title: Title,
    mapped: dict[int, Mapped],
    moment: datetime,
    scene_absolute: dict[Numbers, int] | None = None,
) -> None:
    """TheXEM's numbers as schemes ``tvdb`` and ``scene``. They replace Sonarr's values of the same episode; numbers
    TheXEM no longer gives are removed, Sonarr's stay. ``scene_absolute`` (TVDB number to the scene's number counted
    through) goes on the scene row."""
    rows = {
        (row.episode_id, row.scheme): row
        for row in db.scalars(
            select(EpisodeNumber).where(EpisodeNumber.title_id == title.id, EpisodeNumber.scheme.in_(("tvdb", "scene")))
        )
    }
    for episode_id, numbers in mapped.items():
        for scheme, value in (("tvdb", numbers.tvdb), ("scene", numbers.scene)):
            row = rows.pop((episode_id, scheme), None)
            if row is None:
                row = EpisodeNumber(episode_id=episode_id, title_id=title.id, scheme=scheme)
                db.add(row)
            row.season, row.episode = value
            row.episode_end = None
            row.absolute = (scene_absolute or {}).get(numbers.tvdb) if scheme == "scene" else None
            row.verified = True
            row.origin = ORIGIN
            row.updated_at = moment
    for row in rows.values():
        if row.origin == ORIGIN:
            db.delete(row)


def clear_mapping(db: OrmSession, title: Title) -> None:
    db.execute(delete(EpisodeNumber).where(EpisodeNumber.title_id == title.id, EpisodeNumber.origin == ORIGIN))


def _episodes(db: OrmSession, title_id: int) -> list[tuple[int, int, int]]:
    rows = db.execute(
        select(Episode.id, Episode.season_number, Episode.episode_number).where(
            Episode.title_id == title_id, Episode.tmdb_gone_at.is_(None)
        )
    ).all()
    return [(row.id, row.season_number, row.episode_number) for row in rows]


def _due(title_id: int, force: bool) -> tuple[int | None, bool]:
    with SessionLocal() as db:
        if not enabled(db) or paused(db):
            return None, False
        title = db.get(Title, title_id)
        if title is None or title.kind != "series":
            return None, False
        if title.tvdb_id is None:
            title.xem_state, title.xem_checked_at = NO_TVDB, utcnow()
            db.commit()
            return None, False
        fresh = title.xem_checked_at is not None and title.xem_checked_at > utcnow() - MAX_AGE
        if fresh and not force and title.xem_state not in CODES:
            return None, False
        return title.tvdb_id, True


def _apply(title_id: int, tvdb_id: int, entries: list[dict[str, Any]] | None, failure: str | None) -> str | None:
    moment = utcnow()
    with SessionLocal() as db:
        title = db.get(Title, title_id)
        if title is None or title.tvdb_id != tvdb_id:
            return None
        if failure is not None:
            outcome = failure
        elif entries is None:
            clear_mapping(db, title)
            outcome = NOT_LISTED
        else:
            mapped = bridge(_episodes(db, title_id), parse_entries(entries))
            if mapped:
                store_mapping(db, title, mapped, moment, parse_scene_absolute(entries))
                outcome = MAPPED
            else:
                clear_mapping(db, title)
                outcome = BRIDGE_FAILED
        title.xem_state, title.xem_checked_at = outcome, moment
        db.commit()
        return outcome


def _stored_listed() -> set[int] | None:
    """The list of mapped series fetched less than a day ago, else None."""
    with SessionLocal() as db:
        if not _since(db, SETTING_LISTED_AT, MAX_AGE):
            return None
        try:
            values = json.loads(get_setting(db, SETTING_LISTED, "[]"))
        except ValueError:
            return None
    found = {value for value in values if isinstance(value, int)} if isinstance(values, list) else set()
    return found or None


def _store_listed(found: set[int]) -> None:
    with SessionLocal() as db:
        set_setting(db, SETTING_LISTED, json.dumps(sorted(found)))
        set_setting(db, SETTING_LISTED_AT, utcnow().isoformat())
        db.commit()


async def _listed() -> set[int]:
    """The series TheXEM maps, fetched once a day. Raises ``XemError`` after recording it, which pauses TheXEM."""
    stored = await asyncio.to_thread(_stored_listed)
    if stored is not None:
        return stored
    try:
        found = await fetch_listed()
    except XemError as exc:
        logger.warning("TheXEM's list of mapped series not usable: %s", exc.code)
        await asyncio.to_thread(_record, False, exc.code)
        raise
    await asyncio.to_thread(_record, True)
    await asyncio.to_thread(_store_listed, found)
    logger.info("TheXEM maps %d series", len(found))
    return found


async def refresh_title(title_id: int, *, force: bool = False) -> str | None:
    """Fetch and store TheXEM's numbers of a series when due. Returns the state, None when nothing was asked."""
    tvdb_id, due = await asyncio.to_thread(_due, title_id, force)
    if not due or tvdb_id is None:
        return None
    try:
        listed = await _listed()
    except XemError as exc:
        # The series shows the failure as well: its stored numbers may be old.
        return await asyncio.to_thread(_apply, title_id, tvdb_id, None, exc.code)
    if tvdb_id not in listed:
        # As Sonarr: a series TheXEM does not map is not asked; its numbers from TheXEM go.
        return await asyncio.to_thread(_apply, title_id, tvdb_id, None, None)
    try:
        entries = await fetch_mapping(tvdb_id)
    except XemError as exc:
        logger.warning("TheXEM not usable for title %d: %s", title_id, exc.code)
        await asyncio.to_thread(_record, False, exc.code)
        return await asyncio.to_thread(_apply, title_id, tvdb_id, None, exc.code)
    await asyncio.to_thread(_record, True)
    outcome = await asyncio.to_thread(_apply, title_id, tvdb_id, entries, None)
    logger.info("TheXEM for title %d: %s", title_id, outcome)
    return outcome


def _names_due() -> list[int] | None:
    with SessionLocal() as db:
        if not enabled(db) or paused(db):
            return None
        last = get_setting(db, SETTING_NAMES_AT, "")
        try:
            if last and datetime.fromisoformat(last) > utcnow() - MAX_AGE:
                return None
        except ValueError:
            pass
        ids = db.scalars(select(Title.tvdb_id).where(Title.kind == "series", Title.tvdb_id.is_not(None))).all()
        return sorted({tvdb_id for tvdb_id in ids if tvdb_id is not None})


def _store_names(wanted: list[int], names: dict[int, list[tuple[str, int | None]]]) -> int:
    count = 0
    with SessionLocal() as db:
        db.execute(delete(XemName))
        for tvdb_id in wanted:
            for text, season in names.get(tvdb_id, []):
                db.add(
                    XemName(tvdb_id=tvdb_id, season=season, text=text, search_keys=schreibweisen.search_text([text]))
                )
                count += 1
        set_setting(db, SETTING_NAMES_AT, utcnow().isoformat())
        db.commit()
    return count


async def refresh_names() -> int | None:
    """TheXEM's scene names of the series in the library, once a day. Returns how many were stored."""
    wanted = await asyncio.to_thread(_names_due)
    if not wanted:
        return None
    try:
        names = await fetch_names()
    except XemError as exc:
        logger.warning("TheXEM names not usable: %s", exc.code)
        await asyncio.to_thread(_record, False, exc.code)
        return None
    await asyncio.to_thread(_record, True)
    count = await asyncio.to_thread(_store_names, wanted, names)
    logger.info("TheXEM names: %d for %d series", count, len(wanted))
    return count
