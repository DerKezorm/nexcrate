"""TMDB: the owner's token, the movie search, movie details, posters, the refresh of titles.

What TMDB taught (researched 13.09.2026, see the design notes):

* ⚠️ **The token travels only as ``Authorization: Bearer``**, never as ``api_key`` in a URL. It is
  TMDB's "API Read Access Token". Stored encrypted; answers only say ``configured: true``.
* **About 40 requests per second**, 429 without Retry-After. At most 8 requests run at once. After
  a 429 the call waits 10 seconds and tries once more, then gives up with ``tmdb_rate_limited``.
* **No language fallback.** A missing German text comes back empty. An empty title or overview is
  filled from ``en-US``; ``original_title`` is always kept.
* **Genres** are stored by their English names, the names titles from Radarr carry.
* **Terms:** nothing cached longer than six months, images included. Search answers 30 minutes,
  details 7 days, the genre list 30 days, poster files at most 180 days. Removing the token stops
  every call and purges cached answers and poster files.
* The image CDN needs no credential. ⚠️ The token is never sent there.

Log lines carry ids and counts, never titles or search text.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import secrets
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from fastapi import HTTPException
from sqlalchemy import delete, exists, or_, select
from sqlalchemy.exc import IntegrityError

from .. import crypto
from ..config import get_settings
from ..db import SessionLocal, get_setting, set_setting
from ..meldungen import error, meldung
from ..models import ACCOUNT_ID, Account, HistoryEntry, Setting, Source, Title, TmdbCacheEntry, Version, utcnow
from . import http_log, logs
from .releases.languages import normalize_iso
from .schreibweisen import nfc, search_text, sort_key

logger = logging.getLogger("nexcrate.tmdb")

API_BASE = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p"
TIMEOUT = httpx.Timeout(12.0, connect=6.0)
MAX_PARALLEL = 8
RETRY_WAIT_SECONDS = 10.0
UNAVAILABLE_STATUSES = (500, 503, 504)

SEARCH_TTL = timedelta(minutes=30)
DETAIL_TTL = timedelta(days=7)
GENRE_TTL = timedelta(days=30)
POSTER_MAX_AGE = timedelta(days=180)

JOB_NAME = "tmdb_refresh"
REFRESH_INTERVAL_SECONDS = 6 * 3600
REFRESH_BATCH = 50
REFRESH_AFTER = timedelta(days=30)
#: The one-off fill after a takeover: this many titles per run, and the job's interval is the pause between two runs.
#: The regular refresh would take months for thousands of titles. The titles are found in the database, so a restart
#: goes on with them.
FILL_JOB = "tmdb_fill"
FILL_BATCH = 50
FILL_INTERVAL_SECONDS = 60

POSTER_SIZES = ("w185", "w500")
POSTER_FILE = re.compile(r"^[A-Za-z0-9_-]+\.(jpg|png)$")
POSTER_TYPES = {"image/jpeg": ".jpg", "image/png": ".png"}
POSTER_MAX_BYTES = 10 * 1024 * 1024

SETTING_TOKEN = "tmdb_token"
SETTING_CHECKED_AT = "tmdb_checked_at"
#: What a token may look like. TMDB's tokens are JWTs; anything else cannot travel in a header.
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9._~+/=-]{1,2048}$")

#: The account language as TMDB locale. A code with a region (``pt-BR``) is used as it is.
LOCALES = {"de": "de-DE", "en": "en-US"}
FALLBACK_LOCALE = "en-US"
_REGION_LOCALE = re.compile(r"^([a-z]{2})-([A-Za-z]{2})$")
_YEAR = re.compile(r"^(\d{4})-")


# --- Time, replaceable in tests ------------------------------------------------------ #


def now() -> datetime:
    return utcnow()


async def sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


# --- Errors ------------------------------------------------------------------------------ #


class TmdbError(Exception):
    """An answer of TMDB nexcrate cannot use, with the error the API sends for it."""

    def __init__(self, detail: dict[str, Any], status: int) -> None:
        super().__init__(detail["code"])
        self.detail = detail
        self.status = status

    @property
    def code(self) -> str:
        return str(self.detail["code"])

    def http(self) -> HTTPException:
        return HTTPException(status_code=self.status, detail=self.detail)


def not_configured() -> HTTPException:
    return error("tmdb_not_configured", "No TMDB token is stored yet. Add one under Settings, TMDB.", 409)


def token_unreadable() -> HTTPException:
    return error("tmdb_token_unreadable", "The stored TMDB token cannot be read. Please enter it again.", 409)


def _token_rejected() -> TmdbError:
    return TmdbError(
        meldung(
            "tmdb_token_rejected",
            "TMDB did not accept the token. It has to be the API Read Access Token, not the API key.",
        ),
        502,
    )


def _rate_limited() -> TmdbError:
    return TmdbError(meldung("tmdb_rate_limited", "TMDB receives too many requests. Please try again shortly."), 502)


def _unavailable() -> TmdbError:
    return TmdbError(meldung("tmdb_unavailable", "TMDB is not available at the moment."), 502)


def _timed_out() -> TmdbError:
    return TmdbError(meldung("tmdb_timeout", "TMDB did not answer in time."), 504)


def _unreachable() -> TmdbError:
    return TmdbError(
        meldung("tmdb_unreachable", "TMDB cannot be reached. Is this server connected to the internet?"), 502
    )


def _http_error(status: int) -> TmdbError:
    return TmdbError(meldung("tmdb_http_error", f"TMDB reports an error (HTTP {status}).", status=status), 502)


def _not_found() -> TmdbError:
    return TmdbError(meldung("not_found", "This does not exist, or not any more."), 404)


#: For the OpenAPI ``responses`` of every route that calls TMDB.
ERRORS = (
    (502, "tmdb_token_rejected"),
    (502, "tmdb_rate_limited"),
    (502, "tmdb_unavailable"),
    (502, "tmdb_unreachable"),
    (502, "tmdb_http_error"),
    (504, "tmdb_timeout"),
    (409, "tmdb_not_configured"),
    (409, "tmdb_token_unreadable"),
)


# --- The token ------------------------------------------------------------------------------ #


def valid_token(token: str) -> bool:
    return bool(TOKEN_PATTERN.match(token))


def token_state() -> tuple[bool, str]:
    """Whether a token is stored, and the token; empty when it cannot be decrypted."""
    with SessionLocal() as db:
        stored = get_setting(db, SETTING_TOKEN)
    if not stored:
        return False, ""
    return True, crypto.decrypt(stored)


def require_token() -> str:
    """The token, or 409 ``tmdb_not_configured`` or ``tmdb_token_unreadable``."""
    stored, token = token_state()
    if not stored:
        raise not_configured()
    if not token:
        raise token_unreadable()
    return token


def status() -> tuple[bool, datetime | None]:
    with SessionLocal() as db:
        configured = bool(get_setting(db, SETTING_TOKEN))
        raw = get_setting(db, SETTING_CHECKED_AT)
    checked_at: datetime | None = None
    if configured and raw:
        try:
            checked_at = datetime.fromisoformat(raw)
        except ValueError:
            checked_at = None
        if checked_at is not None and checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=UTC)
    return configured, checked_at


def save_token(token: str) -> None:
    """Store a tested token. A new token starts with an empty cache."""
    with SessionLocal() as db:
        set_setting(db, SETTING_TOKEN, crypto.encrypt(token))
        set_setting(db, SETTING_CHECKED_AT, now().isoformat())
        db.commit()
    purge()
    logger.info("TMDB token stored")


def remove_token() -> None:
    """Forget the token and purge everything TMDB delivered into the cache."""
    with SessionLocal() as db:
        db.execute(delete(Setting).where(Setting.key.in_((SETTING_TOKEN, SETTING_CHECKED_AT))))
        db.commit()
    purge()
    logger.info("TMDB token removed, cache and poster files purged")


def locale_for(language: str | None) -> str:
    code = (language or "").strip()
    if code.lower() in LOCALES:
        return LOCALES[code.lower()]
    match = _REGION_LOCALE.match(code)
    if match:
        return f"{match.group(1).lower()}-{match.group(2).upper()}"
    return FALLBACK_LOCALE


def account_locale() -> str:
    with SessionLocal() as db:
        account = db.get(Account, ACCOUNT_ID)
        return locale_for(account.language if account is not None else None)


# --- The shared client ------------------------------------------------------------------------ #


@dataclass
class _Shared:
    client: httpx.AsyncClient
    gate: asyncio.Semaphore
    loop: asyncio.AbstractEventLoop
    transport: object


_shared: _Shared | None = None


def _connection() -> _Shared:
    """One client with keep-alive for every call, made again for another event loop or transport."""
    global _shared
    loop = asyncio.get_running_loop()
    transport = http_log.current_transport()
    current = _shared
    if current is None or current.loop is not loop or current.transport is not transport or current.client.is_closed:
        client = http_log.client(
            "tmdb",
            base_url=API_BASE,
            timeout=TIMEOUT,
            follow_redirects=False,
            limits=httpx.Limits(max_connections=MAX_PARALLEL, max_keepalive_connections=MAX_PARALLEL),
        )
        current = _Shared(client=client, gate=asyncio.Semaphore(MAX_PARALLEL), loop=loop, transport=transport)
        _shared = current
    return current


async def close() -> None:
    """Close the shared client, at shutdown."""
    global _shared
    current, _shared = _shared, None
    if current is not None and current.loop is asyncio.get_running_loop():
        await current.client.aclose()


async def _get(token: str, path: str, params: dict[str, Any], *, title: bool = False) -> Any:
    """One API call with the error mapping. ``title`` turns a 404 into ``not_found``."""
    shared = _connection()
    for attempt in (1, 2):
        async with shared.gate:
            try:
                response = await http_log.send(
                    shared.client,
                    "GET",
                    path,
                    params=params,
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                )
            except httpx.TimeoutException as exc:
                raise _timed_out() from exc
            except httpx.RequestError as exc:
                raise _unreachable() from exc
        status_code = response.status_code
        if status_code == 429:
            if attempt == 1:
                logger.info("TMDB answered 429, trying once more in %d seconds", RETRY_WAIT_SECONDS)
                await sleep(RETRY_WAIT_SECONDS)
                continue
            raise _rate_limited()
        if status_code == 401:
            raise _token_rejected()
        if status_code == 404 and title:
            raise _not_found()
        if status_code in UNAVAILABLE_STATUSES:
            raise _unavailable()
        if not 200 <= status_code < 300:
            raise _http_error(status_code)
        try:
            return response.json()
        except ValueError as exc:
            raise _http_error(status_code) from exc
    raise _rate_limited()


# --- The cache -------------------------------------------------------------------------------- #


def cache_key(kind: str, *parts: object) -> str:
    raw = "\x1f".join(str(part) for part in parts)
    return f"{kind}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def cache_get(key: str) -> Any | None:
    with SessionLocal() as db:
        row = db.get(TmdbCacheEntry, key)
        if row is None or row.expires_at <= now():
            return None
        return row.value.get("data")


def cache_put(key: str, data: Any, ttl: timedelta) -> None:
    moment = now()
    with SessionLocal() as db:
        # A token removed while the call ran must not leave its answer behind.
        if not get_setting(db, SETTING_TOKEN):
            return
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


async def _cached(
    token: str,
    key: str,
    ttl: timedelta,
    path: str,
    params: dict[str, Any],
    *,
    title: bool = False,
    refresh: bool = False,
    slim: Callable[[Any], Any] | None = None,
) -> Any:
    """A cached answer, or a call. Only answers are cached, never failures. ``slim`` keeps only what the caller reads,
    before the answer is stored and returned, so a hit and a call give the same."""
    if not refresh:
        hit = await asyncio.to_thread(cache_get, key)
        if hit is not None:
            return hit
    data = await _get(token, path, params, title=title)
    if slim is not None:
        data = slim(data)
    await asyncio.to_thread(cache_put, key, data, ttl)
    return data


#: The fields of a movie's answer ``fetch_movie`` reads; the rest is not stored (24.09.2026: 44 KB per movie, 182 MB
#: on the owner's installation, mostly the overviews of every translation, of which only the titles are read).
MOVIE_FIELDS = (
    "title", "overview", "original_title", "runtime", "imdb_id", "release_date", "genres", "poster_path",
    "original_language", "production_companies", "alternative_titles", "translations", "release_dates", "keywords",
)  # fmt: skip


def slim_movie(data: Any) -> Any:
    """A movie's answer with only ``MOVIE_FIELDS``, the translations with their titles and the named lists with their
    names. Release dates stay whole: the anchors read them per country and type."""
    if not isinstance(data, dict):
        return data
    kept: dict[str, Any] = {key: data[key] for key in MOVIE_FIELDS if key in data}
    if "genres" in kept:
        kept["genres"] = [{"id": item.get("id")} for item in map(_dict, _list(kept["genres"]))]
    if "production_companies" in kept:
        kept["production_companies"] = [
            {"name": item.get("name")} for item in map(_dict, _list(kept["production_companies"]))
        ]
    if "keywords" in kept:
        words = _list(_dict(kept["keywords"]).get("keywords"))
        kept["keywords"] = {"keywords": [{"name": item.get("name")} for item in map(_dict, words)]}
    if "alternative_titles" in kept:
        titles = _list(_dict(kept["alternative_titles"]).get("titles"))
        kept["alternative_titles"] = {"titles": [{"title": item.get("title")} for item in map(_dict, titles)]}
    if "translations" in kept:
        translations = _list(_dict(kept["translations"]).get("translations"))
        kept["translations"] = {
            "translations": [
                {"data": {"title": _dict(item.get("data")).get("title")}}
                for item in map(_dict, translations)
                if _dict(item.get("data")).get("title")
            ]
        }
    return kept


def purge() -> None:
    """Empty the cache and remove every poster file."""
    with SessionLocal() as db:
        db.execute(delete(TmdbCacheEntry))
        db.commit()
    shutil.rmtree(poster_dir(), ignore_errors=True)


def prune() -> int:
    """Remove expired answers and poster files older than 180 days. Returns how many went."""
    moment = now()
    with SessionLocal() as db:
        result = db.execute(delete(TmdbCacheEntry).where(TmdbCacheEntry.expires_at <= moment))
        db.commit()
    removed = int(getattr(result, "rowcount", 0) or 0)
    limit = moment.timestamp() - POSTER_MAX_AGE.total_seconds()
    for path in poster_dir().glob("*/*"):
        try:
            if path.is_file() and path.stat().st_mtime < limit:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


# --- Parsing --------------------------------------------------------------------------------- #


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _text(value: Any) -> str:
    return nfc(value).strip() if isinstance(value, str) else ""


def year_of(value: Any) -> int | None:
    match = _YEAR.match(value) if isinstance(value, str) else None
    return int(match.group(1)) if match else None


def poster_file_of(value: Any) -> str | None:
    """``/abc.jpg`` from TMDB as ``abc.jpg``; None for anything else."""
    if not isinstance(value, str):
        return None
    name = value.removeprefix("/")
    return name if POSTER_FILE.match(name) else None


@dataclass(frozen=True)
class SearchResult:
    tmdb_id: int
    title: str
    original_title: str | None
    year: int | None
    overview: str | None
    poster_file: str | None


@dataclass(frozen=True)
class SearchPage:
    page: int
    total_pages: int
    total: int
    results: list[SearchResult]


@dataclass(frozen=True)
class MovieData:
    tmdb_id: int
    imdb_id: str | None
    title: str
    original_title: str | None
    year: int | None
    runtime: int | None
    genres: list[str]
    overview: str | None
    poster_file: str | None
    #: Alternative and translated titles, for the search keys.
    other_titles: list[str]
    #: ``release_dates.results`` as TMDB sends it: per country, every entry.
    release_dates: list[dict[str, Any]]
    #: ISO 639-1 as TMDB's ``original_language`` names it.
    original_language: str | None = None
    #: TMDB's keywords and the production companies, for auto tags.
    keywords: tuple[str, ...] = ()
    studios: tuple[str, ...] = ()


# --- Calls ------------------------------------------------------------------------------------ #


async def check_token(token: str) -> None:
    """``GET /configuration``: proves the token works. Raises ``TmdbError``."""
    data = await _get(token, "/configuration", {})
    if not isinstance(data, dict):
        raise _http_error(200)


async def search_movies(token: str, query: str, *, year: int | None, page: int, locale: str) -> SearchPage:
    text = nfc(query).strip()
    params: dict[str, Any] = {"query": text, "include_adult": "false", "language": locale, "page": page}
    if year:
        params["year"] = year
    data = _dict(
        await _cached(token, cache_key("search", locale, year or "", page, text), SEARCH_TTL, "/search/movie", params)
    )
    items = [_dict(item) for item in _list(data.get("results"))]
    english: dict[int, dict[str, Any]] = {}
    incomplete = any(not _text(item.get("title")) or not _text(item.get("overview")) for item in items)
    if locale != FALLBACK_LOCALE and incomplete:
        fallback = _dict(
            await _cached(
                token,
                cache_key("search", FALLBACK_LOCALE, year or "", page, text),
                SEARCH_TTL,
                "/search/movie",
                {**params, "language": FALLBACK_LOCALE},
            )
        )
        for item in map(_dict, _list(fallback.get("results"))):
            tmdb_id = _int(item.get("id"))
            if tmdb_id is not None:
                english[tmdb_id] = item
    results: list[SearchResult] = []
    for item in items:
        tmdb_id = _int(item.get("id"))
        if tmdb_id is None or tmdb_id <= 0:
            continue
        other = english.get(tmdb_id, {})
        original = _text(item.get("original_title")) or None
        results.append(
            SearchResult(
                tmdb_id=tmdb_id,
                title=_text(item.get("title")) or _text(other.get("title")) or original or f"TMDB {tmdb_id}",
                original_title=original,
                year=year_of(item.get("release_date")),
                overview=_text(item.get("overview")) or _text(other.get("overview")) or None,
                poster_file=poster_file_of(item.get("poster_path")),
            )
        )
    return SearchPage(
        page=_int(data.get("page")) or page,
        total_pages=max(0, _int(data.get("total_pages")) or 0),
        total=max(0, _int(data.get("total_results")) or 0),
        results=results,
    )


async def find_by_imdb(token: str, imdb_id: str, locale: str) -> SearchResult | None:
    """The movie TMDB knows under an IMDb number (``/find/{imdb_id}`` with ``external_source=imdb_id``), or None.

    Cached like the details. Raises ``TmdbError``; a number TMDB does not know is None, not an error.
    """
    number = nfc(imdb_id).strip().lower()
    if not re.fullmatch(r"tt\d{7,10}", number):
        return None
    data = _dict(
        await _cached(
            token,
            cache_key("find", number, locale),
            DETAIL_TTL,
            f"/find/{number}",
            {"external_source": "imdb_id", "language": locale},
        )
    )
    for item in map(_dict, _list(data.get("movie_results"))):
        tmdb_id = _int(item.get("id"))
        if tmdb_id is None or tmdb_id <= 0:
            continue
        original = _text(item.get("original_title")) or None
        return SearchResult(
            tmdb_id=tmdb_id,
            title=_text(item.get("title")) or original or f"TMDB {tmdb_id}",
            original_title=original,
            year=year_of(item.get("release_date")),
            overview=_text(item.get("overview")) or None,
            poster_file=poster_file_of(item.get("poster_path")),
        )
    return None


#: How long the IMDb number of a title outside the library is kept: it hardly ever changes.
EXTERNAL_TTL = timedelta(days=30)


async def imdb_of(token: str, kind: str, tmdb_id: int) -> str | None:
    """The IMDb number TMDB knows for a movie or series (``/external_ids``), kept 30 days; None when there is none.
    For ratings of titles outside the library (Nexview's wish of 24.09.2026). Raises ``TmdbError`` but for 404."""
    path = f"/{'movie' if kind == 'movie' else 'tv'}/{tmdb_id}/external_ids"
    try:
        data = await _cached(
            token,
            cache_key("external_ids", kind, tmdb_id),
            EXTERNAL_TTL,
            path,
            {},
            slim=lambda answer: {"imdb_id": _dict(answer).get("imdb_id")},
        )
    except TmdbError as problem:
        if problem.status == 404:
            return None
        raise
    number = _text(_dict(data).get("imdb_id")).strip().lower()
    return number if number.startswith("tt") and number[2:].isdigit() else None


async def genre_names(token: str) -> dict[int, str]:
    """TMDB's movie genres by id, in English, cached 30 days."""
    data = _dict(
        await _cached(
            token, cache_key("genres", FALLBACK_LOCALE), GENRE_TTL, "/genre/movie/list", {"language": FALLBACK_LOCALE}
        )
    )
    names: dict[int, str] = {}
    for genre in map(_dict, _list(data.get("genres"))):
        genre_id, name = _int(genre.get("id")), _text(genre.get("name"))
        if genre_id is not None and name:
            names[genre_id] = name
    return names


async def fetch_movie(token: str, tmdb_id: int, locale: str, *, refresh: bool = False) -> MovieData:
    """The details of one movie in the account language, with the ``en-US`` fallback. Raises ``TmdbError``."""
    details = _dict(
        await _cached(
            token,
            cache_key("movie", tmdb_id, locale),
            DETAIL_TTL,
            f"/movie/{tmdb_id}",
            {"language": locale, "append_to_response": "alternative_titles,translations,release_dates,keywords"},
            title=True,
            refresh=refresh,
            slim=slim_movie,
        )
    )
    title, overview = _text(details.get("title")), _text(details.get("overview"))
    if locale != FALLBACK_LOCALE and (not title or not overview):
        english = _dict(
            await _cached(
                token,
                cache_key("movie", tmdb_id, FALLBACK_LOCALE, "plain"),
                DETAIL_TTL,
                f"/movie/{tmdb_id}",
                {"language": FALLBACK_LOCALE},
                title=True,
                refresh=refresh,
                slim=slim_movie,
            )
        )
        title = title or _text(english.get("title"))
        overview = overview or _text(english.get("overview"))
    names = await genre_names(token)
    genres: list[str] = []
    for genre in map(_dict, _list(details.get("genres"))):
        genre_id = _int(genre.get("id"))
        name = names.get(genre_id) if genre_id is not None else None
        if name and name not in genres:
            genres.append(name)
    others: list[str] = []
    alternatives = _list(_dict(details.get("alternative_titles")).get("titles"))
    translations = _list(_dict(details.get("translations")).get("translations"))
    for text in [
        *(_text(_dict(item).get("title")) for item in alternatives),
        *(_text(_dict(_dict(item).get("data")).get("title")) for item in translations),
    ]:
        if text and text not in others:
            others.append(text)
    original = _text(details.get("original_title")) or None
    runtime = _int(details.get("runtime"))
    imdb_id = _text(details.get("imdb_id"))
    release_dates = _list(_dict(details.get("release_dates")).get("results"))
    return MovieData(
        tmdb_id=tmdb_id,
        imdb_id=imdb_id[:32] if imdb_id else None,
        title=title or original or f"TMDB {tmdb_id}",
        original_title=original,
        year=year_of(details.get("release_date")),
        runtime=runtime if runtime is not None and runtime > 0 else None,
        genres=genres,
        overview=overview or None,
        poster_file=poster_file_of(details.get("poster_path")),
        other_titles=others,
        release_dates=[entry for entry in release_dates if isinstance(entry, dict)],
        original_language=normalize_iso(details.get("original_language")),
        keywords=_names_of(_dict(details.get("keywords")).get("keywords")),
        studios=_names_of(details.get("production_companies")),
    )


def _names_of(value: object) -> tuple[str, ...]:
    """The names of TMDB's ``[{id, name}]``, each once, in order."""
    found: list[str] = []
    for item in map(_dict, _list(value)):
        name = _text(item.get("name"))
        if name and name not in found:
            found.append(name[:200])
    return tuple(found)


def apply_movie_data(title: Title, data: MovieData, moment: datetime, *, replace_source_poster: bool = False) -> None:
    """Write TMDB's data into a title that no source owns.

    ``replace_source_poster``: the poster came from a taken-over source and gives way to TMDB's poster, when TMDB has
    one.
    """
    title.title = data.title[:1024]
    title.original_title = data.original_title[:1024] if data.original_title else None
    title.imdb_id = data.imdb_id
    title.year = data.year
    title.runtime = data.runtime
    title.genres = list(data.genres)
    title.overview = data.overview
    title.sort_key = sort_key(title.title)[:1024]
    title.search_keys = search_text([title.title, title.original_title])
    title.tmdb_search_keys = search_text(data.other_titles)
    title.release_dates = list(data.release_dates)
    title.original_language = data.original_language
    title.keywords = list(data.keywords)
    title.studios = list(data.studios)
    title.tmdb_poster_path = data.poster_file
    if title.poster_source_id is None:
        title.poster_origin = "tmdb" if data.poster_file else None
    elif replace_source_poster and data.poster_file:
        title.poster_source_id = None
        title.poster_url = None
        title.poster_key = None
        title.poster_origin = "tmdb"
    title.tmdb_refreshed_at = moment
    title.updated_at = moment


# --- Posters -------------------------------------------------------------------------------- #


def poster_dir() -> Path:
    """``data/tmdb-images/<size>/<file>``. Not under ``data/images``, which holds files only."""
    return get_settings().data_dir / "tmdb-images"


def valid_poster(size: str, file: str) -> bool:
    return size in POSTER_SIZES and bool(POSTER_FILE.match(file))


def cached_poster(size: str, file: str) -> tuple[Path, str] | None:
    path = poster_dir() / size / file
    try:
        if not path.is_file():
            return None
        if path.stat().st_mtime < now().timestamp() - POSTER_MAX_AGE.total_seconds():
            path.unlink(missing_ok=True)
            return None
    except OSError:
        return None
    return path, "image/png" if file.endswith(".png") else "image/jpeg"


def _store_poster(size: str, file: str, content: bytes) -> Path:
    directory = poster_dir() / size
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / file
    temporary = directory / f".tmp-{secrets.token_hex(8)}"
    temporary.write_bytes(content)
    os.replace(temporary, target)
    stamp = now().timestamp()
    os.utime(target, (stamp, stamp))
    return target


async def _download_poster(size: str, file: str) -> tuple[bytes, str] | None:
    shared = _connection()
    url = f"{IMAGE_BASE}/{size}/{file}"
    try:
        async with shared.gate, shared.client.stream("GET", url, headers={"Accept": "image/*"}) as response:
            if response.status_code != 200:
                logger.info("TMDB poster not available: HTTP %d", response.status_code)
                return None
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type not in POSTER_TYPES:
                logger.info("TMDB poster refused: not a JPEG or PNG")
                return None
            chunks: list[bytes] = []
            size_read = 0
            async for chunk in response.aiter_bytes():
                size_read += len(chunk)
                if size_read > POSTER_MAX_BYTES:
                    logger.info("TMDB poster refused: larger than the limit")
                    return None
                chunks.append(chunk)
    except httpx.TransportError as exc:
        http_log.unreachable("tmdb", "GET", url, exc)
        return None
    except httpx.RequestError:
        return None
    return (b"".join(chunks), content_type) if chunks else None


async def poster(size: str, file: str) -> tuple[Path, str] | None:
    """A TMDB poster from the cache or the CDN. None without a token, for a bad name, or when missing."""
    if not valid_poster(size, file):
        return None
    stored, token = await asyncio.to_thread(token_state)
    if not stored or not token:
        return None
    hit = await asyncio.to_thread(cached_poster, size, file)
    if hit is not None:
        return hit
    downloaded = await _download_poster(size, file)
    if downloaded is None:
        return None
    content, content_type = downloaded
    path = await asyncio.to_thread(_store_poster, size, file, content)
    return path, content_type


# --- The refresh job ------------------------------------------------------------------------- #


def _due_titles(limit: int) -> list[tuple[int, int]]:
    """Titles no source feeds whose TMDB data is missing or older than 30 days, oldest first."""
    cutoff = now() - REFRESH_AFTER
    fed = exists().where(Version.title_id == Title.id, Version.source_id.is_not(None))
    with SessionLocal() as db:
        rows = db.execute(
            select(Title.id, Title.tmdb_id)
            .where(
                Title.kind == "movie",
                Title.meta_source_id.is_(None),
                ~fed,
                or_(Title.tmdb_refreshed_at.is_(None), Title.tmdb_refreshed_at < cutoff),
            )
            .order_by(Title.tmdb_refreshed_at.asc().nulls_first(), Title.id)
            .limit(limit)
        ).all()
    return [(row.id, row.tmdb_id) for row in rows]


def _apply_refresh(title_id: int, data: MovieData | None) -> bool:
    moment = now()
    replaced = False
    with SessionLocal() as db:
        title = db.get(Title, title_id)
        if title is None or title.meta_source_id is not None:
            return False
        if db.scalar(select(Version.id).where(Version.title_id == title_id, Version.source_id.is_not(None)).limit(1)):
            return False
        if data is None:
            title.tmdb_refreshed_at = moment
        else:
            taken_over = title.poster_source_id is not None and (
                db.scalar(select(Source.taken_over_at).where(Source.id == title.poster_source_id)) is not None
            )
            replaced = taken_over and bool(data.poster_file)
            apply_movie_data(title, data, moment, replace_source_poster=taken_over)
        db.commit()
    if replaced:
        # The cached copy of the taken-over source's poster is not needed any more. A late import: images uses tmdb.
        from . import images

        images.forget([title_id])
    return data is not None


async def _refresh_due(token: str, due: list[tuple[int, int]], what: str) -> int:
    """Fetch and apply the due titles one by one. A title TMDB does not know keeps its data; any other error stops."""
    locale = await asyncio.to_thread(account_locale)
    refreshed = 0
    for title_id, tmdb_id in due:
        try:
            data = await fetch_movie(token, tmdb_id, locale, refresh=True)
        except TmdbError as exc:
            if exc.code == "not_found":
                logger.warning("TMDB no longer knows movie %d of title %d; the title keeps its data", tmdb_id, title_id)
                await asyncio.to_thread(_apply_refresh, title_id, None)
                continue
            logger.warning("TMDB %s stopped after %d titles: %s", what, refreshed, exc.code)
            break
        if await asyncio.to_thread(_apply_refresh, title_id, data):
            refreshed += 1
    return refreshed


async def refresh_titles(limit: int = REFRESH_BATCH) -> int:
    """Refresh up to ``limit`` titles from TMDB. Returns how many were refreshed."""
    stored, token = await asyncio.to_thread(token_state)
    if not stored or not token:
        return 0
    await asyncio.to_thread(prune)
    due = await asyncio.to_thread(_due_titles, limit)
    refreshed = 0
    if due:
        refreshed = await _refresh_due(token, due, "refresh")
        logger.info("TMDB refresh: %d of %d due titles refreshed", refreshed, len(due))
    # Series since S1, with their own due rules (decision 18). Imported here: it builds on this.
    from .series import refresh as series_refresh

    return refreshed + await series_refresh.refresh_series(token, limit)


#: The history events whose titles the fill takes: taken over from Radarr, or restored from a ``release.nex``.
FILL_EVENTS = ("taken_over", "restored")


def _fill_due(limit: int) -> list[tuple[int, int]]:
    """Taken-over and restored titles without TMDB data: a history entry of ``FILL_EVENTS``, no source owns or feeds
    them, no refresh yet."""
    fed = exists().where(Version.title_id == Title.id, Version.source_id.is_not(None))
    taken = exists().where(HistoryEntry.title_id == Title.id, HistoryEntry.event.in_(FILL_EVENTS))
    with SessionLocal() as db:
        rows = db.execute(
            select(Title.id, Title.tmdb_id)
            .where(
                Title.kind == "movie",
                Title.meta_source_id.is_(None),
                Title.tmdb_refreshed_at.is_(None),
                ~fed,
                taken,
            )
            .order_by(Title.id)
            .limit(limit)
        ).all()
    return [(row.id, row.tmdb_id) for row in rows]


async def fill_titles(limit: int = FILL_BATCH) -> int:
    """One run of the fill after a takeover. Without a token it waits; the titles keep Radarr's data meanwhile."""
    stored, token = await asyncio.to_thread(token_state)
    if not stored or not token:
        return 0
    due = await asyncio.to_thread(_fill_due, limit)
    if not due:
        return 0
    refreshed = await _refresh_due(token, due, "fill")
    logger.info("TMDB fill of taken-over titles: %d of %d refreshed", refreshed, len(due))
    return refreshed


async def fill_job() -> None:
    """The background job, every minute: a pause of a minute between two runs of 50 titles."""
    token = logs.bind_request(secrets.token_hex(4))
    try:
        await fill_titles()
    finally:
        logs.unbind_request(token)


async def refresh_job() -> None:
    """The background job, every 6 hours."""
    token = logs.bind_request(secrets.token_hex(4))
    try:
        await refresh_titles()
    finally:
        logs.unbind_request(token)
