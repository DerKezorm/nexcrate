"""The MusicBrainz client (M1.1): one gate for every request in the process.

MusicBrainz allows about one request per second per address and refuses every request, not just the surplus, when
a client exceeds it (measured 16.09.2026, planungsgrundlage-musik.md, c). So there is exactly one gate here, shared
by every thread and event loop: at least ``PACE_SECONDS`` between two requests, never two at once, and a request
of the owner (``OWNER`` lane: a search, a preview, an album page) takes the next free slot before any background
request (``BACKGROUND`` lane: loading and refreshing artists), decisions 12 and 13.

A 503 is MusicBrainz saying "slow down" and came for 11 of 89 requests on the bench even at 1.1 s (plan, B1). The
background retries five times with growing waits; the owner twice, then ``musicbrainz_busy`` (decision 14).

⚠️ A merged id answers **301** with the surviving id in ``Location`` (measured 18.09.2026 on the owner's library; the
first measurement said 200 because its tool followed the redirect without a word). The client follows it itself,
through the gate like any request, and only within MusicBrainz's own API.

Answers are cached in the TMDB cache table under ``mb_`` keys: a search for 30 minutes, a lookup or a page of a browse
for 24 hours (decision 17). The data itself lives in the music tables; the cache only stops the same question twice
in one run. Only the core data (CC0) is asked for: no ratings, tags or genres.

The switch under Settings, Privacy (decision 16): off means no request at all; every call raises
``musicbrainz_disabled``, and what is stored stays visible.

``clock`` and ``sleep`` are module attributes so that tests can replace them, as for the indexers.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session as OrmSession

from ... import __version__
from ...db import SessionLocal, get_setting, set_setting
from ...meldungen import meldung
from ...models import TmdbCacheEntry, utcnow
from ...models.music import VARIOUS_ARTISTS_MBID
from .. import http_log

logger = logging.getLogger("nexcrate.musicbrainz")

API_BASE = "https://musicbrainz.org/ws/2"
#: MusicBrainz wants a name with a contact address; generic names share one global allowance (c, section 1).
USER_AGENT = f"nexcrate/{__version__} ( https://github.com/DerKezorm/nexcrate )"
TIMEOUT = httpx.Timeout(60.0, connect=10.0)
#: A page with tracks measured 800 KB (Nevermind); far more is no answer of MusicBrainz.
MAX_BYTES = 16 * 1024 * 1024
PACE_SECONDS = 1.1
#: How long a request waits for the gate while an owner request is ahead, before it looks again.
POLL_SECONDS = 0.05
BACKGROUND_WAITS = (2.0, 4.0, 8.0, 16.0, 30.0)
#: ⚠️ The patience for a full page of releases with tracks. Measured on the owner's instance (18.09.2026): such a
#: request stayed at 503 through all six tries of a minute, only for albums with many releases, while every other
#: request went through, and the same album came at once in pages of 25. So a busy full page is a heavy question,
#: not a busy MusicBrainz: the loading job gives up early and asks for smaller pages (``loading.load_releases_of``).
FULL_PAGE_WAITS = (2.0, 4.0)
OWNER_WAITS = (1.5, 3.0)
PAGE = 100
SEARCH_LIMIT = 10
#: How many redirects one call follows: a merged id needs one, an id merged twice two.
MAX_REDIRECTS = 3
REDIRECT_STATUSES = (301, 302, 307, 308)
SEARCH_TTL = timedelta(minutes=30)
LOOKUP_TTL = timedelta(hours=24)
#: How far back the counters for the loading status look.
COUNTER_WINDOW = timedelta(hours=1)

OWNER = "owner"
BACKGROUND = "background"
LANES = (OWNER, BACKGROUND)

SETTING_ENABLED = "musicbrainz_enabled"
SETTING_COVERS_ENABLED = "coverart_enabled"
SETTING_LAST_OK = "musicbrainz_last_ok_at"
SETTING_LAST_ERROR = "musicbrainz_last_error_code"

#: A release with this many release events is a worldwide one: MusicBrainz names its ``country`` as the first of
#: them alphabetically (Afghanistan for a digital release in 190 countries, measured on the owner's Lidarr and in
#: the recording of Nevermind), which would rank it last in the country order (decision 28).
WORLDWIDE_EVENTS = 10
#: The includes of a release page with tracks (decision 6); the bench showed they do not shorten the page (B1).
RELEASE_INCLUDES = "recordings+artist-credits+labels+media"
_LUCENE_SPECIAL = re.compile(r'([+\-!(){}\[\]^"~*?:\\/]|&&|\|\|)')
_MBID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def clock() -> float:
    return time.monotonic()


async def sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def now() -> datetime:
    return utcnow()


async def run_blocking[T](call: Callable[..., T], *args: Any) -> T:
    """A database call off the event loop. Tests replace it to keep the order of tasks deterministic."""
    return await asyncio.to_thread(call, *args)


# --- Errors ------------------------------------------------------------------------------ #


class MusicBrainzError(Exception):
    """An answer of MusicBrainz nexcrate cannot use, with the error the API sends for it."""

    def __init__(self, detail: dict[str, Any], status: int) -> None:
        super().__init__(detail["code"])
        self.detail = detail
        self.status = status

    @property
    def code(self) -> str:
        return str(self.detail["code"])

    def http(self) -> HTTPException:
        return HTTPException(status_code=self.status, detail=self.detail)


def _disabled() -> MusicBrainzError:
    return MusicBrainzError(
        meldung("musicbrainz_disabled", "MusicBrainz is switched off under Settings, Privacy."), 409
    )


def _busy() -> MusicBrainzError:
    return MusicBrainzError(
        meldung("musicbrainz_busy", "MusicBrainz is busy right now. Please try again shortly."), 502
    )


def _unavailable() -> MusicBrainzError:
    return MusicBrainzError(meldung("musicbrainz_unavailable", "MusicBrainz is not available at the moment."), 502)


def _timed_out() -> MusicBrainzError:
    return MusicBrainzError(meldung("musicbrainz_timeout", "MusicBrainz did not answer in time."), 504)


def _unreachable() -> MusicBrainzError:
    return MusicBrainzError(
        meldung("musicbrainz_unreachable", "MusicBrainz cannot be reached. Is this server connected to the internet?"),
        502,
    )


def _http_error(status: int) -> MusicBrainzError:
    return MusicBrainzError(
        meldung("musicbrainz_http_error", f"MusicBrainz reports an error (HTTP {status}).", status=status), 502
    )


def _not_found() -> MusicBrainzError:
    return MusicBrainzError(
        meldung("musicbrainz_not_found", "MusicBrainz does not know this entry, or not any more."), 404
    )


def _bad_answer() -> MusicBrainzError:
    return MusicBrainzError(meldung("musicbrainz_bad_answer", "MusicBrainz sent an answer nexcrate cannot read."), 502)


#: For the OpenAPI ``responses`` of every route that calls MusicBrainz.
ERRORS = (
    (409, "musicbrainz_disabled"),
    (502, "musicbrainz_busy"),
    (502, "musicbrainz_unavailable"),
    (502, "musicbrainz_unreachable"),
    (502, "musicbrainz_http_error"),
    (502, "musicbrainz_bad_answer"),
    (504, "musicbrainz_timeout"),
    (404, "musicbrainz_not_found"),
)


# --- Settings ------------------------------------------------------------------------------ #


def enabled(db: OrmSession) -> bool:
    return get_setting(db, SETTING_ENABLED, "1") != "0"


def covers_enabled(db: OrmSession) -> bool:
    return get_setting(db, SETTING_COVERS_ENABLED, "1") != "0"


def state(db: OrmSession) -> dict[str, Any]:
    return {
        "enabled": enabled(db),
        "covers_enabled": covers_enabled(db),
        "last_ok_at": get_setting(db, SETTING_LAST_OK, "") or None,
        "last_error_code": get_setting(db, SETTING_LAST_ERROR, "") or None,
    }


def _enabled_now() -> bool:
    with SessionLocal() as db:
        return enabled(db)


def database_busy(exc: OperationalError) -> bool:
    """SQLite gave up waiting for another writer: nothing is broken, the write can come later."""
    return "database is locked" in str(exc.orig).lower()


def _record(ok: bool, code: str | None = None) -> None:
    """Remember how the last request went, for the settings page. ⚠️ Bookkeeping: a busy database never fails the
    request it belongs to (found on the owner's import, where this line ended the loading of an artist)."""
    try:
        with SessionLocal() as db:
            if ok:
                set_setting(db, SETTING_LAST_OK, now().isoformat())
                set_setting(db, SETTING_LAST_ERROR, "")
            else:
                set_setting(db, SETTING_LAST_ERROR, code or "musicbrainz_unreachable")
            db.commit()
    except OperationalError as exc:
        if not database_busy(exc):
            raise
        logger.debug("The database was busy, the state of MusicBrainz is not written this time")


# --- The gate ------------------------------------------------------------------------------ #


@dataclass
class Gate:
    """The one pace for the process. ``lock`` guards the fields; waiting happens outside it, in ``sleep``."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    #: The clock value at which the next request may go.
    next_free: float = 0.0
    #: How many owner requests wait for a slot: while one does, the background stands back (decision 13).
    owner_waiting: int = 0
    #: Wall-clock moments of requests sent and 503 answers received, for the loading status.
    sent: deque[datetime] = field(default_factory=deque)
    busy: deque[datetime] = field(default_factory=deque)


_gate = Gate()


def reset_state() -> None:
    """For tests: a fresh gate."""
    global _gate
    _gate = Gate()


def gate() -> Gate:
    return _gate


async def acquire(lane: str) -> None:
    """Wait for the next free slot and take it. An owner request goes before every waiting background request."""
    if lane not in LANES:
        raise ValueError(lane)
    current = _gate
    if lane == OWNER:
        with current.lock:
            current.owner_waiting += 1
    try:
        while True:
            with current.lock:
                moment = clock()
                wait = current.next_free - moment
                if wait <= 0 and (lane == OWNER or current.owner_waiting == 0):
                    current.next_free = moment + PACE_SECONDS
                    return
                if wait <= 0:
                    wait = POLL_SECONDS
            await sleep(wait)
    finally:
        if lane == OWNER:
            with current.lock:
                current.owner_waiting -= 1


def _count(moments: deque[datetime], moment: datetime) -> None:
    moments.append(moment)
    horizon = moment - COUNTER_WINDOW
    while moments and moments[0] < horizon:
        moments.popleft()


def counters() -> dict[str, int]:
    """Requests sent and 503 answers within the last hour, for the loading status (M1.2)."""
    current = _gate
    with current.lock:
        horizon = now() - COUNTER_WINDOW
        return {
            "requests_last_hour": sum(1 for moment in current.sent if moment >= horizon),
            "busy_last_hour": sum(1 for moment in current.busy if moment >= horizon),
        }


# --- Requests ------------------------------------------------------------------------------ #


@dataclass
class _Shared:
    client: httpx.AsyncClient
    loop: asyncio.AbstractEventLoop
    transport: object


#: One client per event loop: the app's loop, and the short loops of ``run_sync`` in worker threads.
_clients: dict[asyncio.AbstractEventLoop, _Shared] = {}
_clients_lock = threading.Lock()


def _connection() -> _Shared:
    """One client with keep-alive per event loop, made again for another transport, as for TMDB."""
    loop = asyncio.get_running_loop()
    transport = http_log.current_transport()
    with _clients_lock:
        for stale in [known for known in _clients if known.is_closed()]:
            # A loop that ended without ``close`` (a test's, an aborted thread's): its client cannot be closed any more.
            _clients.pop(stale, None)
        current = _clients.get(loop)
        if current is None or current.transport is not transport or current.client.is_closed:
            client = http_log.client(
                "musicbrainz",
                base_url=API_BASE,
                timeout=TIMEOUT,
                follow_redirects=False,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
                # A 503 is "slow down" and retried here; one warning follows when a request is given up (M1.1).
                quiet_statuses=(503,),
            )
            current = _Shared(client=client, loop=loop, transport=transport)
            _clients[loop] = current
        return current


async def close() -> None:
    """Close the client of the running loop: at shutdown, and at the end of every ``run_sync``."""
    with _clients_lock:
        current = _clients.pop(asyncio.get_running_loop(), None)
    if current is not None:
        await current.client.aclose()


def _redirect_path(response: httpx.Response) -> str | None:
    """The path a redirect names, relative to the API, or None when it leads anywhere else: never followed then."""
    location = response.headers.get("location", "")
    if not location:
        return None
    target = response.request.url.join(location)
    base = httpx.URL(API_BASE)
    if target.scheme != "https" or target.host != base.host or not target.path.startswith(base.path + "/"):
        return None
    return target.path[len(base.path) :]


def _cache_key(kind: str, path: str, params: dict[str, Any]) -> str:
    raw = "\x1f".join([path, *(f"{key}={params[key]}" for key in sorted(params))])
    return f"mb_{kind}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def _cache_get(key: str) -> Any | None:
    with SessionLocal() as db:
        row = db.get(TmdbCacheEntry, key)
        if row is None or row.expires_at <= now():
            return None
        return row.value.get("data")


def _cache_put(key: str, data: Any, ttl: timedelta) -> None:
    moment = now()
    with SessionLocal() as db:
        row = db.get(TmdbCacheEntry, key)
        if row is None:
            db.add(TmdbCacheEntry(key=key, value={"data": data}, expires_at=moment + ttl, created_at=moment))
        else:
            row.value = {"data": data}
            row.expires_at = moment + ttl
            row.created_at = moment
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
        except OperationalError as exc:
            # A busy database: the answer is used all the same, only not remembered.
            db.rollback()
            if not database_busy(exc):
                raise
            logger.debug("The database was busy, an answer of MusicBrainz is not cached this time")


async def _send(path: str, params: dict[str, Any]) -> httpx.Response:
    shared = _connection()
    try:
        return await http_log.send(shared.client, "GET", path, params=params)
    except httpx.TimeoutException as exc:
        raise _timed_out() from exc
    except httpx.RequestError as exc:
        raise _unreachable() from exc


async def _get(
    kind: str,
    path: str,
    params: dict[str, Any],
    lane: str,
    ttl: timedelta,
    *,
    lookup: bool = False,
    waits: tuple[float, ...] | None = None,
) -> Any:
    """One call through the gate with the retries of its lane and the error mapping. ``lookup`` turns a 404 into
    ``musicbrainz_not_found``. Only answers are cached, never failures."""
    if not await run_blocking(_enabled_now):
        raise _disabled()
    params = dict(params, fmt="json")
    key = _cache_key(kind, path, params)
    hit = await run_blocking(_cache_get, key)
    if hit is not None:
        return hit
    # A caller that brings its own patience has a second way (smaller pages): giving up is no warning then.
    gave_up = logging.WARNING if waits is None else logging.INFO
    if waits is None:
        waits = OWNER_WAITS if lane == OWNER else BACKGROUND_WAITS
    current = _gate
    attempt = 0
    redirects = 0
    while attempt <= len(waits):
        await acquire(lane)
        with current.lock:
            _count(current.sent, now())
        started = clock()
        try:
            response = await _send(path, params)
        except MusicBrainzError as exc:
            await run_blocking(_record, False, exc.code)
            raise
        elapsed = clock() - started
        status_code = response.status_code
        if status_code == 503:
            with current.lock:
                _count(current.busy, now())
            if attempt < len(waits):
                logger.debug("MusicBrainz answered 503 for %s (%s), trying again in %.1f s", path, lane, waits[attempt])
                await sleep(waits[attempt])
                attempt += 1
                continue
            logger.log(gave_up, "MusicBrainz stayed busy for %s after %d tries (%s)", path, attempt + 1, lane)
            await run_blocking(_record, False, "musicbrainz_busy")
            raise _busy()
        if status_code in REDIRECT_STATUSES:
            target = _redirect_path(response)
            if target is None or redirects >= MAX_REDIRECTS:
                await run_blocking(_record, False, "musicbrainz_http_error")
                raise _http_error(status_code)
            logger.info("MusicBrainz moved %s to %s (a merged entry)", path, target)
            path = target
            redirects += 1
            continue
        if status_code == 404 and lookup:
            raise _not_found()
        if status_code in (500, 502, 504):
            await run_blocking(_record, False, "musicbrainz_unavailable")
            raise _unavailable()
        if not 200 <= status_code < 300:
            await run_blocking(_record, False, "musicbrainz_http_error")
            raise _http_error(status_code)
        if len(response.content) > MAX_BYTES:
            raise _bad_answer()
        try:
            data = response.json()
        except ValueError as exc:
            raise _bad_answer() from exc
        if not isinstance(data, dict):
            raise _bad_answer()
        logger.info("MusicBrainz %s answered in %.2f s (%s)", path, elapsed, lane)
        await run_blocking(_record, True)
        await run_blocking(_cache_put, key, data, ttl)
        return data
    raise _busy()


# --- Data ------------------------------------------------------------------------------ #


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _text_or_none(value: Any) -> str | None:
    text = _text(value)
    return text or None


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None and value != "" else None
    except TypeError, ValueError:
        return None


def _mbid(value: Any) -> str:
    """An id from an answer, or empty when it is not one: never stored, never used in a path or an address."""
    text = _text(value).lower()
    return text if _MBID.match(text) else ""


def _year(date: Any) -> int | None:
    text = _text(date)
    return int(text[:4]) if len(text) >= 4 and text[:4].isdigit() else None


def credit_of(value: Any) -> list[dict[str, Any]]:
    """MusicBrainz's ``artist-credit`` as nexcrate stores it: id, name on the release, join phrase."""
    credits: list[dict[str, Any]] = []
    for part in value if isinstance(value, list) else []:
        if not isinstance(part, dict):
            continue
        artist = part.get("artist") if isinstance(part.get("artist"), dict) else {}
        credits.append(
            {
                "mbid": _text(artist.get("id")),
                "name": _text(part.get("name")) or _text(artist.get("name")),
                "artist_name": _text(artist.get("name")),
                "join": _text(part.get("joinphrase")),
            }
        )
    return credits


def credit_text(credit: list[dict[str, Any]]) -> str:
    return "".join(f"{part.get('name', '')}{part.get('join', '')}" for part in credit).strip()


@dataclass
class ArtistData:
    mbid: str
    name: str
    sort_name: str
    disambiguation: str | None
    artist_type: str | None
    country: str | None
    begin_year: int | None
    end_year: int | None
    ended: bool
    #: ``[{"name": ..., "sort_name": ..., "locale": ..., "primary": ..., "type": ...}]``
    aliases: list[dict[str, Any]]
    score: int | None = None


@dataclass
class ReleaseGroupData:
    mbid: str
    title: str
    primary_type: str | None
    secondary_types: list[str]
    first_release_date: str | None
    disambiguation: str | None
    credit: list[dict[str, Any]]
    score: int | None = None


@dataclass
class TrackData:
    mbid: str
    recording_mbid: str | None
    position: int
    number: str | None
    name: str
    length_ms: int | None
    credit: list[dict[str, Any]]


@dataclass
class MediumData:
    position: int
    format: str | None
    name: str | None
    track_count: int
    tracks: list[TrackData]


@dataclass
class ReleaseData:
    mbid: str
    title: str
    status: str | None
    date: str | None
    country: str | None
    disambiguation: str | None
    barcode: str | None
    labels: list[dict[str, Any]]
    packaging: str | None
    credit: list[dict[str, Any]]
    media: list[MediumData]

    @property
    def track_count(self) -> int:
        return sum(medium.track_count for medium in self.media)

    @property
    def formats(self) -> list[str]:
        return [medium.format or "" for medium in self.media]


@dataclass
class Page[T]:
    """One page of a browse: the items, MusicBrainz's total, and the offset of this page."""

    items: list[T]
    total: int
    offset: int

    @property
    def next_offset(self) -> int | None:
        following = self.offset + len(self.items)
        return following if self.items and following < self.total else None


def artist_of(data: dict[str, Any]) -> ArtistData:
    span = data.get("life-span") if isinstance(data.get("life-span"), dict) else {}
    aliases = []
    for alias in data.get("aliases") if isinstance(data.get("aliases"), list) else []:
        if isinstance(alias, dict) and _text(alias.get("name")):
            aliases.append(
                {
                    "name": _text(alias.get("name")),
                    "sort_name": _text_or_none(alias.get("sort-name")),
                    "locale": _text_or_none(alias.get("locale")),
                    "primary": bool(alias.get("primary")),
                    "type": _text_or_none(alias.get("type")),
                }
            )
    return ArtistData(
        mbid=_mbid(data.get("id")),
        name=_text(data.get("name")),
        sort_name=_text(data.get("sort-name")) or _text(data.get("name")),
        disambiguation=_text_or_none(data.get("disambiguation")),
        artist_type=_text_or_none(data.get("type")),
        country=_text_or_none(data.get("country")),
        begin_year=_year(span.get("begin")),
        end_year=_year(span.get("end")),
        ended=bool(span.get("ended")),
        aliases=aliases,
        score=_int(data.get("score")),
    )


def release_group_of(data: dict[str, Any]) -> ReleaseGroupData:
    types = data.get("secondary-types") if isinstance(data.get("secondary-types"), list) else []
    return ReleaseGroupData(
        mbid=_mbid(data.get("id")),
        title=_text(data.get("title")),
        primary_type=_text_or_none(data.get("primary-type")),
        secondary_types=[_text(item) for item in types if _text(item)],
        first_release_date=_text_or_none(data.get("first-release-date")),
        disambiguation=_text_or_none(data.get("disambiguation")),
        credit=credit_of(data.get("artist-credit")),
        score=_int(data.get("score")),
    )


def _track_of(data: dict[str, Any], index: int) -> TrackData:
    recording = data.get("recording") if isinstance(data.get("recording"), dict) else {}
    return TrackData(
        mbid=_mbid(data.get("id")),
        recording_mbid=_mbid(recording.get("id")) or None,
        position=_int(data.get("position")) or index + 1,
        number=_text_or_none(data.get("number")),
        name=_text(data.get("title")) or _text(recording.get("title")),
        length_ms=_int(data.get("length")) or _int(recording.get("length")),
        credit=credit_of(data.get("artist-credit")) or credit_of(recording.get("artist-credit")),
    )


def _medium_of(data: dict[str, Any], index: int) -> MediumData:
    raw_tracks = data.get("tracks") if isinstance(data.get("tracks"), list) else []
    tracks = [_track_of(track, number) for number, track in enumerate(raw_tracks) if isinstance(track, dict)]
    return MediumData(
        position=_int(data.get("position")) or index + 1,
        format=_text_or_none(data.get("format")),
        name=_text_or_none(data.get("title")),
        track_count=_int(data.get("track-count")) or len(tracks),
        tracks=tracks,
    )


def release_of(data: dict[str, Any]) -> ReleaseData:
    labels: list[dict[str, Any]] = []
    for info in data.get("label-info") if isinstance(data.get("label-info"), list) else []:
        if not isinstance(info, dict):
            continue
        label = info.get("label") if isinstance(info.get("label"), dict) else {}
        name = _text(label.get("name"))
        number = _text_or_none(info.get("catalog-number"))
        if name or number:
            labels.append({"name": name, "catalog_number": number})
    raw_media = data.get("media") if isinstance(data.get("media"), list) else []
    events = data.get("release-events") if isinstance(data.get("release-events"), list) else []
    country = "XW" if len(events) >= WORLDWIDE_EVENTS else _text_or_none(data.get("country"))
    return ReleaseData(
        mbid=_mbid(data.get("id")),
        title=_text(data.get("title")),
        status=_text(data.get("status")).lower() or None,
        date=_text_or_none(data.get("date")),
        country=country,
        disambiguation=_text_or_none(data.get("disambiguation")),
        barcode=_text_or_none(data.get("barcode")),
        labels=labels,
        packaging=_text_or_none(data.get("packaging")),
        credit=credit_of(data.get("artist-credit")),
        media=[_medium_of(medium, index) for index, medium in enumerate(raw_media) if isinstance(medium, dict)],
    )


# --- Calls ------------------------------------------------------------------------------ #


def valid_mbid(value: str) -> bool:
    return bool(_MBID.match(value.strip().lower()))


def _escaped(query: str) -> str:
    return _LUCENE_SPECIAL.sub(r"\\\1", query.strip())


def _page(
    data: dict[str, Any], key: str, count_key: str, offset: int, of: Callable[[dict[str, Any]], Any]
) -> Page[Any]:
    raw = data.get(key)
    if not isinstance(raw, list):
        raise _bad_answer()
    items = [of(item) for item in raw if isinstance(item, dict)]
    total = _int(data.get(count_key))
    return Page(items=items, total=total if total is not None else len(items) + offset, offset=offset)


async def search_artists(query: str, lane: str = OWNER) -> list[ArtistData]:
    """Up to 10 artists for a free text. MusicBrainz folds umlauts and accents itself (B1). Various Artists is never a
    hit (decision 18)."""
    text = _escaped(query)
    if not text:
        return []
    data = await _get("search", "/artist", {"query": text, "limit": str(SEARCH_LIMIT)}, lane, SEARCH_TTL)
    page = _page(data, "artists", "count", 0, artist_of)
    return [artist for artist in page.items if artist.mbid and artist.mbid != VARIOUS_ARTISTS_MBID]


async def lookup_artist(mbid: str, lane: str = OWNER) -> ArtistData:
    """One artist with its aliases. A merged id answers with the surviving id in ``mbid`` (B1, decision 22)."""
    data = await _get("artist", f"/artist/{mbid}", {"inc": "aliases"}, lane, LOOKUP_TTL, lookup=True)
    artist = artist_of(data)
    if not artist.mbid:
        raise _bad_answer()
    return artist


async def browse_release_groups(artist_mbid: str, offset: int = 0, lane: str = BACKGROUND) -> Page[ReleaseGroupData]:
    """One page of an artist's release groups, 100 at a time, with their types and credits (decision 3).

    ⚠️ ``release-group-status=website-default`` is what MusicBrainz's own artist page shows: without it every release
    group with nothing but bootlegs, promotions and pseudo-releases comes along, typed "Album" like a studio album
    (measured 18.09.2026 on the owner's library: 212 groups against 106, and six bootleg collections among ten studio
    albums).
    """
    params = {
        "artist": artist_mbid,
        "inc": "artist-credits",
        "limit": str(PAGE),
        "offset": str(offset),
        "release-group-status": "website-default",
    }
    data = await _get("rg-browse", "/release-group", params, lane, LOOKUP_TTL)
    return _page(data, "release-groups", "release-group-count", offset, release_group_of)


async def search_release_groups(
    query: str, artist_mbid: str | None = None, kind: str | None = None, lane: str = OWNER
) -> list[ReleaseGroupData]:
    """Up to 10 release groups for a title, optionally of one artist and of one kind: ``album``, ``compilation``,
    ``single``, or any (decision 34, B1: without the type, singles of other artists come first)."""
    text = _escaped(query)
    if not text:
        return []
    parts = [f"releasegroup:({text})"]
    if artist_mbid:
        parts.append(f"arid:{artist_mbid}")
    if kind == "album":
        parts.append("primarytype:album")
    elif kind == "compilation":
        parts.append("primarytype:album AND secondarytype:compilation")
    elif kind == "single":
        parts.append("primarytype:single")
    params = {"query": " AND ".join(parts), "limit": str(SEARCH_LIMIT)}
    data = await _get("rg-search", "/release-group", params, lane, SEARCH_TTL)
    return [group for group in _page(data, "release-groups", "count", 0, release_group_of).items if group.mbid]


async def lookup_release_group(mbid: str, lane: str = OWNER) -> ReleaseGroupData:
    data = await _get("rg", f"/release-group/{mbid}", {"inc": "artist-credits"}, lane, LOOKUP_TTL, lookup=True)
    group = release_group_of(data)
    if not group.mbid:
        raise _bad_answer()
    return group


async def browse_releases(
    release_group_mbid: str,
    offset: int = 0,
    *,
    tracks: bool = True,
    lane: str = BACKGROUND,
    limit: int = PAGE,
    waits: tuple[float, ...] | None = None,
) -> Page[ReleaseData]:
    """One page of the official releases of a release group (decision 5), with media and tracks when ``tracks``.
    A page ends after about 500 tracks, never inside a release (c). ``limit`` asks for a smaller page and ``waits``
    sets the patience with 503: the loading job's way with an album MusicBrainz stays busy for."""
    size = max(1, min(limit, PAGE))
    params = {"release-group": release_group_mbid, "status": "official", "limit": str(size), "offset": str(offset)}
    if tracks:
        params["inc"] = RELEASE_INCLUDES
    else:
        params["inc"] = "media+labels+artist-credits"
    data = await _get("releases", "/release", params, lane, LOOKUP_TTL, waits=waits)
    return _page(data, "releases", "release-count", offset, release_of)


def run_sync[T](call: Coroutine[Any, Any, T]) -> T:
    """Run one call from a worker thread of its own, as the loading job does; its loop's client closes with it."""

    async def wrapped() -> T:
        try:
            return await call
        finally:
            await close()

    return asyncio.run(wrapped())
