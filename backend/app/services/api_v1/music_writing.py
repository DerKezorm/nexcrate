"""What other programs may change about an artist through ``/api/v1``.

An artist is no title: it has no version of its own, its albums have. Requesting one is "Add artist" of the interface
with every choice Lidarr offers (fork 1); taking it back takes back every album of it and stops new ones; freezing is
Lidarr's unmonitoring (fork 3): nothing of it is searched on its own, its albums keep their switches.

Every change goes through the functions the interface uses: ``routers.music._create_artist``, ``store.apply_choice``,
``routers.music.freeze`` and ``remove_artist``, the recycle bin.

⚠️ Log lines carry ids and counts, never a name, the program's ``origin`` or the key's name.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ...db import SessionLocal
from ...meldungen import error
from ...models import Artist, Title, Version, utcnow
from .. import recycle_bin
from ..automatic import clock as automatic_clock
from ..automatic import planning as automatic_planning
from ..downloads import store as download_store
from ..music import kinds as music_kinds
from ..music import musicbrainz as mb
from ..music import store as music_store
from ..search import jobs as search_jobs
from . import titles, writing

logger = logging.getLogger("nexcrate.api_v1")


@dataclass(frozen=True)
class ArtistScope:
    """The scope under ``artist`` (rule 4): which albums to watch now, and the groups that count."""

    albums: str = "studio"
    types: tuple[str, ...] | None = None


def find_artist(db: OrmSession, raw: str) -> Artist | None:
    found = titles.find(db, "artist", writing.parse_ref("artist", raw))
    return db.get(Artist, -found[0]) if found else None


def artist_of(db: OrmSession, raw: str) -> Artist:
    artist = find_artist(db, raw)
    if artist is None:
        raise error("title_not_found", "nexcrate does not have this artist.", 404, kind="artist", ref=raw)
    return artist


def _albums(db: OrmSession, artist: Artist) -> list[Title]:
    return list(
        db.scalars(select(Title).where(Title.kind == "album", Title.artist_id == artist.id).order_by(Title.id))
    )


def _own_versions(db: OrmSession, title_ids: list[int]) -> list[Version]:
    if not title_ids:
        return []
    return list(
        db.scalars(
            select(Version).where(Version.title_id.in_(title_ids), Version.source_id.is_(None)).order_by(Version.id)
        )
    )


def _label(db: OrmSession) -> str:
    definition = music_store.definition(db)
    return definition.label if definition is not None else ""


def _mark_new(db: OrmSession, artist: Artist, moment: datetime, origin: str | None, caller: writing.Caller) -> int:
    """Put the program's marks on the versions this request made, with a history line each. Returns how many."""
    label = _label(db)
    made = [
        version
        for version in _own_versions(db, [title.id for title in _albums(db, artist)])
        if version.created_at == moment and version.origin_key is None
    ]
    for version in made:
        version.origin = origin
        version.origin_key = caller.key_name[: writing.ORIGIN_MAX_LENGTH]
        db.add(writing._history_entry(version, label, "requested", caller, origin, moment))
    return len(made)


def _check_scope(scope: ArtistScope) -> None:
    if scope.albums not in music_store.WATCH_CHOICES:
        raise error("invalid_input", "The input is not valid.", 422, fields=["artist.albums"])
    if scope.types is not None and (not scope.types or any(item not in music_kinds.GROUPS for item in scope.types)):
        raise error("invalid_input", "The input is not valid.", 422, fields=["artist.types"])


def _apply_existing(artist_id: int, scope: ArtistScope, origin: str | None, caller: writing.Caller) -> int:
    """An artist that is there already gets the choice applied, and is thawed. Returns how many albums it watched."""
    from ...routers import music as music_router

    moment = utcnow()
    with SessionLocal() as db:
        artist = db.get(Artist, artist_id)
        if artist is None:
            raise error("title_not_found", "nexcrate does not have this artist.", 404, kind="artist")
        if scope.types is not None:
            artist.album_types = sorted(set(scope.types))
        artist.monitor_new = music_store.new_albums_for(scope.albums)
        artist.updated_at = moment
        if artist.frozen_at is not None:
            music_router.freeze(db, artist, False)
        definition = music_store.ensure_definition(db, music_store.account_language(db))
        watched = music_store.apply_choice(db, artist, scope.albums, moment=moment, definition=definition)
        db.flush()
        _mark_new(db, artist, moment, origin, caller)
        automatic_planning.replan(db, [title.id for title in _albums(db, artist)], automatic_clock.now())
        db.commit()
        return watched


def _wish(db: OrmSession, artist: Artist) -> str:
    """A search wish on every album of the artist that wants something; each searches at once (``wishes``), a frozen
    artist's albums too (V5, fork 3)."""
    moment = utcnow()
    wished = 0
    for title in _albums(db, artist):
        if writing._wanting(db, title.id):
            title.search_wish_at = moment
            wished += 1
    return "queued" if wished else "nothing_wanted"


async def request_artist(raw: str, scope: ArtistScope, search_now: bool, origin: str | None,
                         caller: writing.Caller) -> dict[str, Any]:  # fmt: skip
    """Add an artist, or apply a choice to one that is there (N17 for music). Idempotent: asking twice adds nothing
    twice."""
    from ...routers import music as music_router

    _check_scope(scope)
    ref = writing.parse_ref("artist", raw)

    def existing() -> int | None:
        with SessionLocal() as db:
            found = find_artist(db, raw)
            return found.id if found is not None else None

    artist_id = await asyncio.to_thread(existing)
    created = False
    if artist_id is None:
        if ref.value == music_store.VARIOUS_ARTISTS_MBID:
            raise error("ref_not_addable", "Various Artists cannot be added; add its albums.", 422, kind="artist")
        try:
            data = await mb.lookup_artist(ref.value, mb.OWNER)
            groups, _total, _pages = await music_router._all_groups(data.mbid)
        except mb.MusicBrainzError as exc:
            logger.info("A request for an artist failed at MusicBrainz: %s", exc.code)
            raise exc.http() from exc

        def mark(db: OrmSession, artist: Artist, moment: datetime) -> None:
            artist.origin = origin
            artist.origin_key = caller.key_name[: writing.ORIGIN_MAX_LENGTH]
            _mark_new(db, artist, moment, origin, caller)

        summary, created = await asyncio.to_thread(
            music_router._create_artist, data, groups, ref.value, scope.albums, list(scope.types or []) or None, mark
        )
        artist_id = int(summary["id"])
        watched = int(summary["albums"])
    if not created:
        watched = await asyncio.to_thread(_apply_existing, artist_id, scope, origin, caller)

    def finish() -> tuple[str, list[dict[str, Any]], dict[str, Any] | None]:
        with SessionLocal() as db:
            artist = db.get(Artist, artist_id)
            if artist is None:
                raise error("title_not_found", "nexcrate does not have this artist.", 404, kind="artist")
            automatic_planning.replan(db, [title.id for title in _albums(db, artist)], automatic_clock.now())
            search = _wish(db, artist) if search_now else "not_asked"
            db.commit()
            if search == "queued":
                from ..automatic import wishes as automatic_wishes

                # At once, not with the automatic's order (the owner's answer of 22.09.2026).
                automatic_wishes.kick()
            definition = music_store.definition(db)
            notes = writing._notes(db, [definition]) if definition is not None else []
            return search, notes, titles.detail(db, -artist_id)

    search, notes, detail = await asyncio.to_thread(finish)
    logger.info("Artist %d requested through key %d: created %s, %d albums watched", artist_id, caller.key_id,
                created, watched)  # fmt: skip
    return {
        "created": created,
        "versions": [],
        "albums_watched": watched,
        "search": search,
        "notes": notes,
        "title": detail,
    }


# --- Taking back, freezing, searching, removing ------------------------------------------------------------------- #


def _running(raw: str) -> tuple[int, list[tuple[int, str, int | None]], dict[int, int]]:
    """The artist, the running downloads of its albums' own versions, and how many per album."""
    with SessionLocal() as db:
        artist = artist_of(db, raw)
        running: list[tuple[int, str, int | None]] = []
        per_album: dict[int, int] = {}
        for title in _albums(db, artist):
            own = {version.version_definition_id for version in _own_versions(db, [title.id])}
            found = [
                (download.id, download.state, download.version_definition_id)
                for download in download_store.pending_downloads(db, title.id)
                if download.version_definition_id in own
            ]
            if found:
                per_album[title.id] = len(found)
            running += found
        return artist.id, running, per_album


def _apply_withdraw(artist_id: int, delete_files: bool, caller: writing.Caller,
                    stopped: dict[int, int]) -> dict[str, Any]:  # fmt: skip
    """``stopped`` holds per album how many of its downloads were stopped."""
    from ...routers import music as music_router

    moment = utcnow()
    moves: list[recycle_bin.Move] = []
    folders: list[tuple[str, str]] = []
    with SessionLocal() as db:
        artist = db.get(Artist, artist_id)
        if artist is None:
            raise error("title_not_found", "nexcrate does not have this artist.", 404, kind="artist")
        label = _label(db)
        artist.monitor_new = "none"
        artist.updated_at = moment
        albums = _albums(db, artist)
        outcomes: list[dict[str, Any]] = []
        try:
            for title in albums:
                versions = _own_versions(db, [title.id])
                if not versions:
                    continue
                version = versions[0]
                outcome = {
                    "ref": f"mbid:{title.mbid}",
                    "monitoring_off": False,
                    "downloads_cancelled": stopped.get(title.id, 0),
                    "files_recycled": 0,
                    "version_removed": False,
                }
                if version.monitored:
                    version.monitored = False
                    version.state = music_store.state_of(version)
                    version.updated_at = moment
                    db.flush()
                    download_store.follow_version(db, title.id, version.version_definition_id, moment)
                    outcome["monitoring_off"] = True
                if delete_files:
                    result = recycle_bin.delete_in(db, title, recycle_bin.Scope(), caller.actor, moment)
                    moves += result.moves
                    folders += result.folders
                    outcome["files_recycled"] = result.files
                if outcome["monitoring_off"] or outcome["files_recycled"]:
                    db.add(writing._history_entry(version, label, "withdrawn", caller, None, moment))
                db.flush()
                if version.origin_key is not None and writing._empty(db, title, version):
                    db.delete(version)
                    outcome["version_removed"] = True
                outcomes.append(outcome)
            db.flush()
            left = _own_versions(db, [title.id for title in albums])
            fed = db.scalar(
                select(Version.id)
                .where(Version.title_id.in_([title.id for title in albums]), Version.source_id.is_not(None))
                .limit(1)
            )
            # Answer 4 of V2 for an artist: one a program brought goes when nothing of it is left.
            removing = artist.origin_key is not None and not left and fed is None
            automatic_planning.replan(db, [title.id for title in albums], automatic_clock.now())
            db.commit()
        except BaseException:
            db.rollback()
            recycle_bin.undo(moves)
            raise
        if removing:
            music_router.remove_artist(db, artist_id)
    recycle_bin.tell_media_servers(folders)
    logger.info("Artist %d taken back through key %d: %d albums, %d downloads stopped, artist removed %s", artist_id,
                caller.key_id, len(outcomes), sum(stopped.values()), removing)  # fmt: skip
    return {"title_removed": removing, "versions": [], "albums": outcomes}


async def withdraw_artist(raw: str, delete_files: bool, caller: writing.Caller) -> dict[str, Any]:
    """Take an artist back: new albums off, every album of it taken back as an album is."""
    artist_id, running, per_album = await asyncio.to_thread(_running, raw)
    await writing.cancel_downloads(running)
    return await asyncio.to_thread(_apply_withdraw, artist_id, delete_files, caller, per_album)


def set_monitoring(raw: str, monitored: bool, new_albums: str | None, caller: writing.Caller) -> dict[str, Any]:
    """Freeze or thaw an artist as Lidarr unmonitors one; ``new_albums`` switches the rule for new albums."""
    from ...routers import music as music_router

    with SessionLocal() as db:
        artist = artist_of(db, raw)
        changed = False
        if monitored != (artist.frozen_at is None):
            music_router.freeze(db, artist, not monitored)
            changed = True
        if new_albums is not None and new_albums != artist.monitor_new:
            artist.monitor_new = new_albums
            artist.updated_at = utcnow()
            changed = True
        db.commit()
        artist_id = artist.id
    logger.info("Artist %d %s through key %d", artist_id, "thawed" if monitored else "frozen", caller.key_id)
    with SessionLocal() as db:
        detail = titles.detail(db, -artist_id)
    return {"versions": [], "changed": changed, "title": detail}


def wish_search(raw: str) -> str:
    with SessionLocal() as db:
        artist = artist_of(db, raw)
        if any(search_jobs.running_search(title.id) is not None for title in _albums(db, artist)):
            return "running"
        answer = _wish(db, artist)
        db.commit()
    if answer == "queued":
        from ..automatic import wishes as automatic_wishes

        automatic_wishes.kick()
    return answer


async def remove_artist(raw: str, delete_files: bool, caller: writing.Caller) -> None:
    """As the interface's button: the artist and its albums go; with ``delete_files`` their files first into the bin."""
    from ...routers import music as music_router

    artist_id, running, _per_album = await asyncio.to_thread(_running, raw)
    await writing.cancel_downloads(running)

    def remove() -> None:
        with SessionLocal() as db:
            artist = db.get(Artist, artist_id)
            if artist is None:
                return
            if delete_files:
                for title in _albums(db, artist):
                    if _own_versions(db, [title.id]):
                        recycle_bin.delete(title.id, recycle_bin.Scope(), caller.actor)
            fed = db.scalar(
                select(Version.id)
                .join(Title, Title.id == Version.title_id)
                .where(Title.artist_id == artist_id, Version.source_id.is_not(None))
                .limit(1)
            )
            if fed is not None:
                raise error(
                    "title_has_source_versions",
                    "A connection to Lidarr feeds albums of this artist; they stay until it is removed there.",
                    409,
                )
            music_router.remove_artist(db, artist_id)

    await asyncio.to_thread(remove)


def remove_album_version(title_id: int) -> None:
    """``DELETE`` of an album: its own version goes, the album stays in its artist's catalogue, unwatched."""
    with SessionLocal() as db:
        title = db.get(Title, title_id)
        if title is None:
            return
        rows = list(db.scalars(select(Version).where(Version.title_id == title_id)))
        if any(version.source_id is not None for version in rows):
            raise error(
                "title_has_source_versions",
                "A connection to Lidarr feeds this album; it stays until it is removed there.",
                409,
            )
        for version in rows:
            db.delete(version)
        db.flush()
        automatic_planning.replan(db, [title_id], automatic_clock.now())
        title.updated_at = utcnow()
        db.commit()
    logger.info("Album %d: its version removed", title_id)
