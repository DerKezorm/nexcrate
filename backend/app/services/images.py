"""Posters: loaded from the source or from TMDB, cached, always served by nexcrate itself.

A title that follows a source shows the source's poster, through its relative ``/MediaCover/...``
address, never TMDB's copy of it. A title the owner added and no source feeds has the poster origin
``tmdb``: its w500 poster comes through ``services/tmdb.py``, only while a TMDB token exists. The
browser only talks to nexcrate; the Content-Security-Policy allows images from our own origin and
nothing else.

A poster of a taken-over source is served from the cache as before; nexcrate never asks that source
again. When the source cannot deliver a poster at all (an error, or an answer that is not an image,
found 25./26.09.2026: a Radarr/Sonarr behind forms login answers a redirect instead of the file),
TMDB's poster of the title takes its place, while a TMDB token exists; otherwise the interface shows
its placeholder, honestly. A movie or series fed by a live source never had its TMDB poster file
looked up before (the TMDB refresh only runs for titles no source feeds), so the fallback looks it
up once, by the title's TMDB id, and keeps it in ``titles.tmdb_poster_path`` for next time.

⚠️ Extended 26.09.2026 to series (Sonarr): the same gap, the same fallback, only the TMDB call
differs (``/tv/<id>`` instead of ``/movie/<id>``). An album's cover (Lidarr) likely has the same gap
(MusicBrainz/Cover Art Archive instead of a fallback), but was not measured or built here; see
``.music.covers``.

The file name carries the cache key (source id and ``lastWrite``): ``<title>-poster-<key>.jpg``.
A new ``lastWrite`` means a new file, and the old ones of the title are removed. The address the
interface uses carries the same key (``?v=``), because a browser does not load an image again
under the same address.

Only image types from ``radarr.IMAGE_TYPES`` (no SVG), at most 10 MB.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import case, select, update

from .. import crypto
from ..config import get_settings
from ..db import SessionLocal
from ..models import Source, Title
from . import tmdb
from .radarr import IMAGE_TYPES, Image, ImageUnavailable, RadarrClient, RadarrError, SourceUrlInvalid

logger = logging.getLogger("nexcrate.images")

#: The address changes with the image, so the browser may keep it for a year. Private: it needs a login.
CACHE_CONTROL = "private, max-age=31536000, immutable"
#: TMDB images may not be kept longer than six months, in no cache; 30 days in the browser.
TMDB_CACHE_CONTROL = "private, max-age=2592000"
TMDB_POSTER_SIZE = "w500"
_UNSAFE = re.compile(r"[^A-Za-z0-9-]")


def image_dir() -> Path:
    directory = get_settings().data_dir / "images"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def cache_key(poster_source_id: int | None, poster_key: str | None) -> str:
    return _UNSAFE.sub("", f"{poster_source_id}-{poster_key}")[:64]


def tmdb_cache_key(poster_file: str) -> str:
    return ("tmdb-" + _UNSAFE.sub("", poster_file.rsplit(".", 1)[0]))[:64]


def cover_url(title: Title) -> str | None:
    """The cover of an album from the Cover Art Archive (decision 42); None for other kinds."""
    from .music import covers

    return covers.cover_url(title)


def is_album(title_id: int) -> bool:
    with SessionLocal() as db:
        return db.scalar(select(Title.kind).where(Title.id == title_id)) == "album"


def poster_url(title: Title) -> str | None:
    """``/api/images/<id>/poster?v=<cache key>``, or None without a poster. An album's cover comes from the Cover Art
    Archive under the same address.

    ⚠️ A series never gets a live ``poster_source_id`` (Sonarr import never sets one, unlike Radarr's movies): its
    only address today is TMDB's, filled in at import from the same call that matches Sonarr to TMDB (S1.2). When
    that call found no poster then, nothing ever asks again (the refresh job skips a title a source feeds, and a
    series counts as fed the moment it has a version). Found 26.09.2026: a series address of its own, ``pending``,
    so the interface still asks once; ``poster()`` looks TMDB up there and then, and once it finds one, the row's
    own address takes over on the next listing (``_store_tmdb_poster_path``).
    """
    if title.kind == "album":
        return cover_url(title)
    if title.poster_origin == "tmdb" and title.tmdb_poster_path:
        return f"/api/images/{title.id}/poster?v={tmdb_cache_key(title.tmdb_poster_path)}"
    if title.poster_url and title.poster_source_id and title.poster_key:
        return f"/api/images/{title.id}/poster?v={cache_key(title.poster_source_id, title.poster_key)}"
    if title.kind == "series":
        return f"/api/images/{title.id}/poster?v=pending"
    return None


@dataclass(frozen=True)
class PosterReference:
    title_id: int
    key: str
    relative_url: str
    source_url: str
    stored_api_key: str
    #: Set for the poster origin ``tmdb``: the TMDB file; the other fields are empty then.
    tmdb_file: str | None = None
    #: The source is taken over: never asked again, TMDB's poster (``fallback_tmdb_file``) when the copy is gone.
    taken_over: bool = False
    fallback_tmdb_file: str | None = None


def reference(title_id: int) -> PosterReference | None:
    with SessionLocal() as db:
        row = db.execute(
            select(
                Title.poster_origin,
                Title.tmdb_poster_path,
                Title.poster_url,
                Title.poster_key,
                Title.poster_source_id,
                Source.url,
                Source.api_key,
                Source.taken_over_at,
            )
            .outerjoin(Source, Source.id == Title.poster_source_id)
            .where(Title.id == title_id)
        ).first()
    if row is None:
        return None
    if row.poster_origin == "tmdb" and row.tmdb_poster_path:
        return PosterReference(
            title_id=title_id,
            key=tmdb_cache_key(row.tmdb_poster_path),
            relative_url="",
            source_url="",
            stored_api_key="",
            tmdb_file=row.tmdb_poster_path,
        )
    if not row.poster_url or not row.poster_key or row.url is None:
        return None
    return PosterReference(
        title_id=title_id,
        key=cache_key(row.poster_source_id, row.poster_key),
        relative_url=row.poster_url,
        source_url=row.url,
        stored_api_key=row.api_key,
        taken_over=row.taken_over_at is not None,
        fallback_tmdb_file=row.tmdb_poster_path,
    )


def cached(title_id: int, key: str) -> tuple[Path, str] | None:
    directory = image_dir()
    for media_type, extension in IMAGE_TYPES.items():
        path = directory / f"{title_id}-poster-{key}{extension}"
        if path.is_file():
            return path, media_type
    return None


def store(title_id: int, key: str, image: Image) -> Path:
    """Write the image under its key and remove every other poster file of the title."""
    directory = image_dir()
    target = directory / f"{title_id}-poster-{key}{IMAGE_TYPES[image.content_type]}"
    # A name the cleanup below does not match, and a rename, so no reader ever sees half a file.
    temporary = directory / f".tmp-{secrets.token_hex(8)}"
    temporary.write_bytes(image.content)
    os.replace(temporary, target)
    for stale in directory.glob(f"{title_id}-poster-*"):
        if stale != target:
            stale.unlink(missing_ok=True)
    logger.debug("Poster of title %d cached, %d bytes", title_id, len(image.content))
    return target


def forget(title_ids: Iterable[int]) -> int:
    """Remove the cached posters of titles that are gone."""
    directory = image_dir()
    removed = 0
    for title_id in title_ids:
        for path in directory.glob(f"{int(title_id)}-poster-*"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                logger.warning("Could not remove a cached poster of title %d", title_id)
    return removed


def smaller_poster(relative_url: str) -> str | None:
    """The 500 pixel copy Radarr keeps next to every poster, or None for other addresses.

    A poster in the grid needs no more. Measured on Radarr 6.3 on 13.09.2026: ``poster-500.jpg``
    had 45 KB where ``poster.jpg`` had 232 KB, and originals reach 900 KB.
    """
    path, separator, query = relative_url.partition("?")
    if not path.endswith("/poster.jpg"):
        return None
    return f"{path[: -len('poster.jpg')]}poster-500.jpg{separator}{query}"


async def fetch_poster(radarr: RadarrClient, relative_url: str) -> Image:
    """The smaller copy first, the original when the source has no copy."""
    smaller = smaller_poster(relative_url)
    if smaller is not None:
        try:
            return await radarr.image(smaller)
        except ImageUnavailable as exc:
            logger.debug("No smaller poster copy at the source, loading the original: %s", exc)
    return await radarr.image(relative_url)


def _store_tmdb_poster_path(title_id: int, poster_file: str) -> None:
    """Kept for next time. A title a live source still owns (``poster_source_id`` set) keeps its ``poster_origin``
    as it is: the source may recover, and this is only a fallback for while it does not. A title with none (a
    series, or a movie added by hand) switches fully to TMDB, exactly as ``apply_movie_data`` already would."""
    with SessionLocal() as db:
        db.execute(
            update(Title)
            .where(Title.id == title_id)
            .values(
                tmdb_poster_path=poster_file,
                poster_origin=case((Title.poster_source_id.is_(None), "tmdb"), else_=Title.poster_origin),
            )
        )
        db.commit()


async def _series_poster_file(token: str, tmdb_id: int) -> str | None:
    """Only the poster path of a TV series (``/tv/<id>``), never the seasons and episodes ``fetch_series``
    also pulls in: fetching those just for a fallback poster would be slow for a show with many seasons.

    A cache key of its own (``tv_poster``, not ``fetch_series``'s ``tv``): its answer is unslimmed and holds
    every season, so sharing the key would either waste that or hand a season lookup a poster-only stub.
    """
    data = await tmdb._cached(  # the same private helper `series/tmdb_series.py` already imports from this module
        token,
        tmdb.cache_key("tv_poster", tmdb_id),
        tmdb.DETAIL_TTL,
        f"/tv/{tmdb_id}",
        {"language": tmdb.FALLBACK_LOCALE},
        title=True,
        slim=lambda answer: {"poster_path": answer.get("poster_path")} if isinstance(answer, dict) else answer,
    )
    return tmdb.poster_file_of(data.get("poster_path")) if isinstance(data, dict) else None


async def _looked_up_tmdb_file(title_id: int) -> str | None:
    """A TMDB poster file looked up on demand for a movie or series a source feeds, and kept for next time.

    A source-fed title never has ``tmdb_poster_path`` from the refresh job (it only refreshes titles no
    source feeds), so the fallback for it has to ask TMDB itself, once. None without a TMDB id, without
    a stored token, or when TMDB knows no poster either. An album's cover never comes from here (M1.0).
    """
    with SessionLocal() as db:
        row = db.execute(select(Title.kind, Title.tmdb_id).where(Title.id == title_id)).first()
    if row is None or row.kind not in ("movie", "series") or not row.tmdb_id:
        return None
    stored, token = await asyncio.to_thread(tmdb.token_state)
    if not stored or not token:
        return None
    try:
        if row.kind == "movie":
            data = await tmdb.fetch_movie(token, row.tmdb_id, tmdb.FALLBACK_LOCALE)
            poster_file = data.poster_file
        else:
            poster_file = await _series_poster_file(token, row.tmdb_id)
    except tmdb.TmdbError as exc:
        logger.info("Could not look up a fallback TMDB poster for title %d: %s", title_id, exc)
        return None
    if not poster_file:
        return None
    await asyncio.to_thread(_store_tmdb_poster_path, title_id, poster_file)
    return poster_file


async def _fallback_poster(title_id: int, known_file: str | None = None) -> tuple[Path, str] | None:
    """TMDB's poster in the source's place, while a TMDB token exists; None for an honest placeholder.

    ``known_file``: a poster file ``reference()`` already read from the row (a taken-over source, or a live one
    that once had a TMDB poster on record); without one, this asks TMDB itself, once (``_looked_up_tmdb_file``).
    """
    fallback_file = known_file or await _looked_up_tmdb_file(title_id)
    if fallback_file is None:
        return None
    return await tmdb.poster(TMDB_POSTER_SIZE, fallback_file)


async def poster(title_id: int) -> tuple[Path, str] | None:
    """The cached poster file and its type, fetched from the source when needed. None if there is none."""
    found = await asyncio.to_thread(reference, title_id)
    if found is None:
        from .music import covers

        cover = await covers.cover(title_id)
        if cover is not None:
            return cover
        # Not an album either (or one with no cover): a series (``poster_url``'s ``pending`` address) or a movie
        # added by hand, with nothing on record yet. ``_looked_up_tmdb_file`` itself says no for anything else.
        return await _fallback_poster(title_id)
    if found.tmdb_file is not None:
        return await tmdb.poster(TMDB_POSTER_SIZE, found.tmdb_file)
    hit = cached(found.title_id, found.key)
    if hit is not None:
        return hit
    if found.taken_over:
        # ⚠️ A taken-over source is never asked again; TMDB's poster takes the place of the copy that is gone.
        return await _fallback_poster(title_id, found.fallback_tmdb_file)
    api_key = await asyncio.to_thread(crypto.decrypt, found.stored_api_key)
    try:
        async with RadarrClient(found.source_url, api_key) as radarr:
            image = await fetch_poster(radarr, found.relative_url)
    except (ImageUnavailable, RadarrError, SourceUrlInvalid) as exc:
        logger.info("Poster of title %d is not available: %s", title_id, exc)
        return await _fallback_poster(title_id, found.fallback_tmdb_file)
    path = await asyncio.to_thread(store, found.title_id, found.key, image)
    return path, image.content_type
