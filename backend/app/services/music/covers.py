"""Album covers from the Cover Art Archive (decision 42).

``release-group/<mbid>/front-250`` answers with two redirects to ``archive.org`` and a JPEG of 12 to 41 KB in about
two seconds (plan, B2). The image is kept on disk like a TMDB poster; a 404 is remembered for seven days; at most
four fetches run at once; the switch under Settings, Privacy stops every request, and only covers already on disk
are shown then. Redirects are followed only to ``*.archive.org``: never to an address an answer names.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select

from ...config import get_settings
from ...db import SessionLocal
from ...models import Title, TmdbCacheEntry, utcnow
from .. import http_log
from . import musicbrainz as mb

logger = logging.getLogger("nexcrate.covers")

BASE_URL = "https://coverartarchive.org"
SIZE = "front-250"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
MAX_PARALLEL = 4
MAX_BYTES = 5 * 1024 * 1024
MAX_HOPS = 4
MISSING_TTL = timedelta(days=7)
ALLOWED_HOSTS = ("coverartarchive.org", "archive.org")
TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
CACHE_CONTROL = "private, max-age=2592000"

_gate: asyncio.Semaphore | None = None
_gate_loop: asyncio.AbstractEventLoop | None = None


def _semaphore() -> asyncio.Semaphore:
    global _gate, _gate_loop
    loop = asyncio.get_running_loop()
    if _gate is None or _gate_loop is not loop:
        _gate = asyncio.Semaphore(MAX_PARALLEL)
        _gate_loop = loop
    return _gate


def cover_dir() -> Path:
    """Next to ``data/images``, not inside it: the poster cache there holds files only."""
    directory = get_settings().data_dir / "covers"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def cache_key(mbid: str) -> str:
    return f"caa-{mbid[:8]}"


def cover_url(title: Title) -> str | None:
    """``/api/images/<id>/poster?v=caa-<mbid>`` for an album with a release group; None otherwise."""
    if title.kind != "album" or not title.mbid:
        return None
    return f"/api/images/{title.id}/poster?v={cache_key(title.mbid)}"


def cached(mbid: str) -> tuple[Path, str] | None:
    directory = cover_dir()
    for media_type, extension in TYPES.items():
        path = directory / f"{mbid}{extension}"
        if path.is_file():
            return path, media_type
    return None


def _missing_key(mbid: str) -> str:
    return f"caa404:{mbid}"


def _remembered_missing(mbid: str) -> bool:
    with SessionLocal() as db:
        row = db.get(TmdbCacheEntry, _missing_key(mbid))
        return row is not None and row.expires_at > utcnow()


def _remember_missing(mbid: str) -> None:
    moment = utcnow()
    with SessionLocal() as db:
        row = db.get(TmdbCacheEntry, _missing_key(mbid))
        if row is None:
            db.add(
                TmdbCacheEntry(
                    key=_missing_key(mbid), value={"data": 404}, expires_at=moment + MISSING_TTL, created_at=moment
                )
            )
        else:
            row.expires_at = moment + MISSING_TTL
        db.commit()


def _allowed(url: httpx.URL) -> bool:
    host = url.host.lower()
    return url.scheme == "https" and any(host == item or host.endswith("." + item) for item in ALLOWED_HOSTS)


def _mbid_of(title_id: int) -> tuple[str | None, bool]:
    with SessionLocal() as db:
        row = db.execute(select(Title.kind, Title.mbid).where(Title.id == title_id)).first()
        enabled = mb.covers_enabled(db)
    if row is None or row.kind != "album" or not row.mbid:
        return None, enabled
    return row.mbid, enabled


async def _fetch(mbid: str, path: str | None = None) -> tuple[bytes, str] | None:
    """The image, following redirects by hand and only within the allowed hosts. ``path`` below the archive's base,
    ``release-group/<mbid>/front-250`` when None."""
    client = http_log.client("coverart", timeout=TIMEOUT, follow_redirects=False, log_bodies=False)
    try:
        url = httpx.URL(f"{BASE_URL}/{path or f'release-group/{mbid}/{SIZE}'}")
        for _hop in range(MAX_HOPS + 1):
            if not _allowed(url):
                logger.warning("Cover of %s: redirect to a host outside archive.org refused", mbid)
                return None
            response = await http_log.send(client, "GET", str(url), headers={"User-Agent": mb.USER_AGENT})
            if response.status_code in (301, 302, 303, 307, 308) and response.headers.get("location"):
                url = url.join(response.headers["location"])
                continue
            if response.status_code == 404:
                return None
            if response.status_code != 200:
                logger.info("Cover of %s: HTTP %d", mbid, response.status_code)
                return None
            media_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if media_type not in TYPES or len(response.content) > MAX_BYTES or not response.content:
                return None
            return response.content, media_type
        return None
    except httpx.HTTPError as exc:
        logger.info("Cover of %s not loaded: %s", mbid, type(exc).__name__)
        return None
    finally:
        await client.aclose()


def _store(mbid: str, content: bytes, media_type: str) -> Path:
    directory = cover_dir()
    target = directory / f"{mbid}{TYPES[media_type]}"
    temporary = directory / f".tmp-{mbid}"
    temporary.write_bytes(content)
    temporary.replace(target)
    return target


async def cover(title_id: int) -> tuple[Path, str] | None:
    """The cover file of an album and its type, fetched when needed. None for no album, no cover, or the switch off."""
    mbid, enabled = await asyncio.to_thread(_mbid_of, title_id)
    if mbid is None:
        return None
    hit = cached(mbid)
    if hit is not None:
        return hit
    if not enabled or await asyncio.to_thread(_remembered_missing, mbid):
        return None
    async with _semaphore():
        hit = cached(mbid)
        if hit is not None:
            return hit
        fetched = await _fetch(mbid)
    if fetched is None:
        await asyncio.to_thread(_remember_missing, mbid)
        return None
    content, media_type = fetched
    path = await asyncio.to_thread(_store, mbid, content, media_type)
    return path, media_type


#: The sizes for filed albums (decision 28): embedded in the files, and ``folder.jpg``.
EMBED_SIZE = "front-500"
FOLDER_SIZE = "front-1200"


async def artwork(release_mbid: str | None, group_mbid: str | None, size: str) -> tuple[bytes, str] | None:
    """The front cover of a release in ``size``, else the release group's; None when the archive has none. The
    caller checks the switch. Not kept on disk: it goes into the files and the album folder."""
    for kind, mbid in (("release", release_mbid), ("release-group", group_mbid)):
        if not mbid:
            continue
        async with _semaphore():
            found = await _fetch(mbid, f"{kind}/{mbid}/{size}")
        if found is not None:
            return found
    return None


def forget(mbids: list[str]) -> int:
    removed = 0
    directory = cover_dir()
    for mbid in mbids:
        for extension in TYPES.values():
            path = directory / f"{mbid}{extension}"
            if path.is_file():
                path.unlink(missing_ok=True)
                removed += 1
    return removed


def describe() -> dict[str, Any]:
    return {"size": SIZE, "max_parallel": MAX_PARALLEL, "missing_days": MISSING_TTL.days}
