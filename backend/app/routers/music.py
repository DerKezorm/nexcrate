"""Music (M1.4): searching and adding artists and albums, the artist pages, the loading
status, the target release of an album, and the switches for MusicBrainz and the Cover Art Archive.

Every call to MusicBrainz here runs on the owner lane: it takes the next free slot before the background (decision
13). Adding an artist loads its release groups right away from the preview's cache and creates the artist with
every group as a title and the watched albums as versions (decision 33); the releases follow in the background.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from ..db import SessionLocal, set_setting
from ..deps import DbSession
from ..meldungen import error, error_responses
from ..models import Artist, Release, Title, Version, utcnow
from ..models.music import LOAD_STATES, MONITOR_NEW
from ..services import auto_tags, images, tags
from ..services.automatic import planning
from ..services.music import album_read, fingerprint, kinds, loading, retag, store, tag_writing, target
from ..services.music import musicbrainz as mb
from ..services.schreibweisen import nfc, query_keys

logger = logging.getLogger("nexcrate.music")

router = APIRouter(prefix="/api/music", tags=["music"])

QUERY_MAX = 200
_COUNTRY = re.compile(r"^[A-Z]{2}$")
#: The groups of an artist page that start open (decision 39).
OPEN_GROUPS = ("studio", "ep")


# --- Models ------------------------------------------------------------------------------ #


class ArtistHit(BaseModel):
    mbid: str
    name: str
    disambiguation: str | None
    artist_type: str | None
    country: str | None
    begin_year: int | None
    end_year: int | None
    score: int | None
    in_library: int | None = Field(description="The id of the artist when it is in the library already.")


class GroupCount(BaseModel):
    group: str = Field(description="studio, ep, single, live, compilation, soundtrack, remix, spoken, other.")
    count: int


class ArtistPreview(BaseModel):
    mbid: str
    name: str
    disambiguation: str | None
    artist_type: str | None
    country: str | None
    begin_year: int | None
    end_year: int | None
    groups_total: int
    pages: int = Field(description="How many pages of release groups were read.")
    groups: list[GroupCount]
    would_watch: int = Field(description="How many albums the choice studio would watch.")
    in_library: int | None


WatchChoice = Literal["studio", "future", "none", "missing", "existing", "first", "latest"]
AlbumType = Literal["studio", "ep", "single", "live", "compilation", "soundtrack", "remix", "spoken", "other"]


class ArtistIn(BaseModel):
    mbid: str = Field(max_length=36)
    monitor: WatchChoice = Field(
        default="studio",
        description="What to watch (decision 32): every album of the types, only future ones, "
        "none, those without files, those with files, the first or the latest album.",
    )
    types: list[AlbumType] | None = Field(
        default=None,
        max_length=9,
        description="The groups of the artist page that count, like Lidarr's metadata profile; null is studio albums "
        "and EPs. New albums follow the same groups.",
    )


class AlbumHit(BaseModel):
    mbid: str
    title: str
    primary_type: str | None
    secondary_types: list[str]
    first_release_date: str | None
    artist: str = Field(description="The credit as one text.")
    artist_mbids: list[str]
    score: int | None
    in_library: int | None = Field(description="The id of the title when the album is in the library already.")


class AlbumIn(BaseModel):
    mbid: str = Field(max_length=36)


class ArtistPatch(BaseModel):
    monitor_new: Literal["all", "none"] | None = None
    monitored: bool | None = Field(
        default=None,
        description="False freezes the artist: nothing of it is searched on its own, the albums keep their switches.",
    )


class ArtistSummary(BaseModel):
    id: int
    mbid: str
    name: str
    alias_display: str | None
    disambiguation: str | None
    artist_type: str | None
    country: str | None
    begin_year: int | None
    end_year: int | None
    is_various: bool
    monitor_new: str
    load_state: str
    load_error: str | None
    load_done: int
    load_total: int | None
    groups_total: int | None
    monitored: bool = Field(default=True, description="False while the artist is frozen.")
    tags: list[str] = Field(default_factory=list, description="Its tags, by name.")
    album_types: list[str] | None = Field(default=None, description="The groups new albums are watched of; null "
                                          "is studio albums and EPs.")  # fmt: skip
    albums: int = Field(description="Albums with a version.")
    complete: int = Field(description="Albums whose version has every track of its target.")
    missing: int = Field(default=0, description="Albums without a single file, and searched for.")
    incomplete: int = Field(default=0, description="Albums whose version misses tracks.")
    upgrade: int = Field(default=0, description="Albums that could be better than the profile's target.")
    downloading: int = Field(default=0, description="Albums being loaded right now.")
    problem: int = Field(default=0, description="Albums with a problem.")
    size_bytes: int = Field(default=0, description="What this artist's albums take on disk.")
    state: str | None = Field(
        default=None,
        description="The one state the row shows, most pressing first: problem, downloading, wanted, incomplete, "
        "upgrade; null when every album is there and good.",
    )
    cover_title_id: int | None = Field(description="The album whose cover the tile shows (decision 38).")
    cover_url: str | None
    mb_gone_at: datetime | None


class ArtistList(BaseModel):
    items: list[ArtistSummary]
    total: int


class ArtistAlbum(BaseModel):
    id: int
    mbid: str | None
    title: str
    year: int | None
    first_release_date: str | None
    primary_type: str | None
    secondary_types: list[str]
    disambiguation: str | None
    group: str
    cover_url: str | None
    version_id: int | None
    monitored: bool
    state: str | None
    track_counts: dict[str, int] | None
    target_tracks: int | None
    mb_gone_at: datetime | None


class ArtistGroup(BaseModel):
    group: str
    open: bool
    albums: list[ArtistAlbum]


class ArtistDetail(ArtistSummary):
    groups: list[ArtistGroup]
    groups_refreshed_at: datetime | None
    groups_due_at: datetime | None


class LoadingCurrent(BaseModel):
    artist_id: int
    name: str
    step: str
    done: int
    total: int | None


class LoadingFailed(BaseModel):
    artist_id: int
    name: str
    code: str | None


class LoadingStatus(BaseModel):
    enabled: bool
    queued: int
    current: LoadingCurrent | None
    failed: list[LoadingFailed]
    failed_albums: int
    requests_last_hour: int
    busy_last_hour: int


class TargetIn(BaseModel):
    release_id: int | None = Field(description="A release of the album, or null for the rule again (decision 30).")


class WatchIn(BaseModel):
    monitored: bool


class WatchOut(BaseModel):
    title_id: int
    version_id: int | None
    monitored: bool
    state: str | None


class CountriesOut(BaseModel):
    countries: list[str] | None = Field(description="The owner's order, or null for the default of the language.")
    default: list[str] = Field(description="The order the language gives (decision 28).")


class CountriesIn(BaseModel):
    countries: list[str] | None = Field(default=None, max_length=12)


class MusicBrainzState(BaseModel):
    enabled: bool
    covers_enabled: bool
    last_ok_at: str | None
    last_error_code: str | None


class MusicBrainzIn(BaseModel):
    enabled: bool | None = None
    covers_enabled: bool | None = None


class MusicFilesState(BaseModel):
    write_tags: bool = Field(description="Tags and cover are written into filed music (24).")


class AcoustIdState(BaseModel):
    enabled: bool = Field(description="The operator's switch; nothing is asked without a key either.")
    key_set: bool = Field(description="The installation's own application key is stored; it is never shown.")
    key_source: Literal["own", "shipped"] | None = Field(
        description="Which key a lookup uses: the installation's own, nexcrate's shipped one, or none."
    )
    fpcalc_available: bool = Field(description="The fingerprint tool is in the image.")
    last_error_code: str | None = Field(description="acoustid_key_invalid, acoustid_unreachable or acoustid_error.")
    last_ok_at: str | None


class AcoustIdIn(BaseModel):
    enabled: bool | None = None
    key: str | None = Field(
        default=None,
        max_length=64,
        description="The installation's AcoustID application key; an empty text removes it. Never answered back.",
    )


class TagChange(BaseModel):
    field: str = Field(description="Picard's name of the field, lower case.")
    before: list[str]
    after: list[str]


class TagFilePreview(BaseModel):
    track_file_id: int
    file: str = Field(description="The file name in the album folder.")
    tags_state: str | None = Field(description="download, written, linked, format, off or failed.")
    refusal: str | None = Field(description="Why the file cannot be written: linked, format or unreadable.")
    changes: list[TagChange]


class TagPreviewOut(BaseModel):
    title_id: int
    write_enabled: bool
    cover: bool = Field(description="Writing embeds the cover of the Cover Art Archive.")
    files: list[TagFilePreview]
    changed: int = Field(description="Files with changes that can be written.")
    written: int | None = Field(default=None, description="After writing: how many files were written.")


class ReleaseTrackOut(BaseModel):
    id: int
    medium: int
    position: int
    number: str | None
    name: str
    length_ms: int | None
    artist_credit: list[dict[str, Any]] | None


class ReleaseMediumOut(BaseModel):
    position: int
    format: str | None
    name: str | None
    track_count: int
    tracks: list[ReleaseTrackOut]


class ReleaseDetail(BaseModel):
    id: int
    mbid: str
    name: str
    date: str | None
    country: str | None
    formats: list[str]
    media_count: int
    track_count: int
    labels: list[dict[str, Any]]
    disambiguation: str | None
    tracks_loaded: bool
    media: list[ReleaseMediumOut]


# --- Helpers ------------------------------------------------------------------------------ #


def _mbid(value: str) -> str:
    cleaned = value.strip().lower()
    if not mb.valid_mbid(cleaned):
        raise error("invalid_input", "The input is not valid.", 422, fields=["mbid"])
    return cleaned


def _query(value: str | None) -> str:
    cleaned = nfc(value or "").strip()
    if not cleaned or len(cleaned) > QUERY_MAX:
        raise error("invalid_input", "The input is not valid.", 422, fields=["q"])
    return cleaned


def _artist_or_404(db: OrmSession, artist_id: int) -> Artist:
    artist = db.get(Artist, artist_id)
    if artist is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    return artist


def _cover_of(db: OrmSession, artist_id: int) -> Title | None:
    """The newest studio album with a file, else the newest studio album (decision 38)."""
    return _covers_of(db, [artist_id]).get(artist_id)


def _covers_of(db: OrmSession, artist_ids: list[int]) -> dict[int, Title]:
    """``_cover_of`` for a page of artists at once.

    ⚠️ Only the columns the choice needs, and then the chosen albums: loading every album whole, artist by artist, took
    1.7 of the 2 seconds the owner's first page of artists needed (22.09.2026, 60 artists, 5,808 albums).
    """
    if not artist_ids:
        return {}
    rows = db.execute(
        select(Title.artist_id, Title.id, Title.primary_type, Title.secondary_types, Version.has_file)
        .outerjoin(Version, Version.title_id == Title.id)
        .where(Title.kind == "album", Title.artist_id.in_(artist_ids), Title.mb_gone_at.is_(None))
        .order_by(Title.first_release_date.desc().nulls_last(), Title.id.desc())
    ).tuples()
    newest: dict[int, int] = {}
    newest_studio: dict[int, int] = {}
    with_file: dict[int, int] = {}
    for artist_id, title_id, primary_type, secondary_types, has_file in rows:
        newest.setdefault(artist_id, title_id)
        if kinds.is_studio_or_ep(primary_type, secondary_types):
            newest_studio.setdefault(artist_id, title_id)
            if has_file:
                with_file.setdefault(artist_id, title_id)
    chosen = {
        artist_id: with_file.get(artist_id) or newest_studio.get(artist_id) or newest[artist_id] for artist_id in newest
    }
    albums = {title.id: title for title in db.scalars(select(Title).where(Title.id.in_(set(chosen.values()))))}
    return {artist_id: albums[title_id] for artist_id, title_id in chosen.items()}


def _summary(db: OrmSession, artist: Artist) -> dict[str, Any]:
    return _summaries(db, [artist])[0]


def _summaries(db: OrmSession, artists: list[Artist]) -> list[dict[str, Any]]:
    """The rows of a page of artists, with one query each for counts, covers and tags instead of three per artist."""
    ids = [artist.id for artist in artists]
    counts = store.counts_of_many(db, ids)
    covers = _covers_of(db, ids)
    labels = tags.of_artists(db, ids)
    return [
        _summary_of(artist, counts[artist.id], covers.get(artist.id), labels.get(artist.id, [])) for artist in artists
    ]


def _summary_of(artist: Artist, counts: dict[str, Any], cover: Title | None, labels: list[str]) -> dict[str, Any]:
    return {
        "id": artist.id,
        "mbid": artist.mbid,
        "name": artist.name,
        "alias_display": artist.alias_display,
        "disambiguation": artist.disambiguation,
        "artist_type": artist.artist_type,
        "country": artist.country,
        "begin_year": artist.begin_year,
        "end_year": artist.end_year,
        "is_various": artist.is_various,
        "monitor_new": artist.monitor_new,
        "load_state": artist.load_state,
        "load_error": artist.load_error,
        "load_done": artist.load_done,
        "load_total": artist.load_total,
        "groups_total": artist.groups_total,
        "monitored": artist.frozen_at is None,
        "tags": labels,
        "album_types": artist.album_types,
        **counts,
        "cover_title_id": cover.id if cover is not None else None,
        "cover_url": images.cover_url(cover) if cover is not None else None,
        "mb_gone_at": artist.mb_gone_at,
    }


def _album_row(title: Title, version: Version | None, target_tracks: int | None) -> dict[str, Any]:
    return {
        "id": title.id,
        "mbid": title.mbid,
        "title": title.title,
        "year": title.year,
        "first_release_date": title.first_release_date,
        "primary_type": title.primary_type,
        "secondary_types": list(title.secondary_types or []),
        "disambiguation": title.release_group_disambiguation,
        "group": kinds.group_of(title.primary_type, title.secondary_types),
        "cover_url": images.cover_url(title),
        "version_id": version.id if version is not None else None,
        "monitored": bool(version is not None and version.monitored),
        "state": version.state if version is not None else None,
        "track_counts": version.track_counts if version is not None else None,
        "target_tracks": target_tracks,
        "mb_gone_at": title.mb_gone_at,
    }


def _groups_of(db: OrmSession, artist: Artist) -> list[dict[str, Any]]:
    rows = db.execute(
        select(Title, Version)
        .outerjoin(Version, Version.title_id == Title.id)
        .where(Title.kind == "album", Title.artist_id == artist.id)
        .order_by(Title.first_release_date.desc().nulls_last(), Title.sort_key, Title.id)
    ).tuples()
    joint = db.execute(
        select(Title, Version)
        .join(store.AlbumArtist, store.AlbumArtist.title_id == Title.id)
        .outerjoin(Version, Version.title_id == Title.id)
        .where(store.AlbumArtist.artist_id == artist.id)
        .order_by(Title.first_release_date.desc().nulls_last(), Title.sort_key, Title.id)
    ).tuples()
    target_ids = {}
    grouped: dict[str, list[dict[str, Any]]] = {name: [] for name in kinds.GROUPS}
    seen: set[int] = set()
    for title, version in [*rows, *joint]:
        if title.id in seen:
            continue
        seen.add(title.id)
        if version is not None and version.target_release_id is not None:
            target_ids[title.id] = version.target_release_id
        grouped[kinds.group_of(title.primary_type, title.secondary_types)].append((title, version))  # type: ignore[arg-type]
    track_counts = (
        dict(
            db.execute(select(Release.id, Release.track_count).where(Release.id.in_(list(target_ids.values()))))
            .tuples()
            .all()
        )
        if target_ids
        else {}
    )
    return [
        {
            "group": name,
            "open": name in OPEN_GROUPS,
            "albums": [
                _album_row(title, version, track_counts.get(target_ids.get(title.id, -1)))
                for title, version in items  # type: ignore[misc]
            ],
        }
        for name, items in grouped.items()
    ]


def _in_library_artists(db: OrmSession, mbids: list[str]) -> dict[str, int]:
    found: dict[str, int] = {}
    for artist in db.scalars(select(Artist).where(Artist.mbid.in_(mbids))):
        found[artist.mbid] = artist.id
    for artist in db.scalars(select(Artist).where(Artist.mbid_old.is_not(None))):
        for old in artist.mbid_old or []:
            if old in mbids:
                found[old] = artist.id
    return found


def _in_library_albums(db: OrmSession, mbids: list[str]) -> dict[str, int]:
    return dict(
        db.execute(select(Title.mbid, Title.id).where(Title.kind == "album", Title.mbid.in_(mbids))).tuples().all()
    )


def _hit(artist: mb.ArtistData, known: dict[str, int]) -> dict[str, Any]:
    return {
        "mbid": artist.mbid,
        "name": artist.name,
        "disambiguation": artist.disambiguation,
        "artist_type": artist.artist_type,
        "country": artist.country,
        "begin_year": artist.begin_year,
        "end_year": artist.end_year,
        "score": artist.score,
        "in_library": known.get(artist.mbid),
    }


async def _all_groups(mbid: str) -> tuple[list[mb.ReleaseGroupData], int, int]:
    groups: list[mb.ReleaseGroupData] = []
    offset = 0
    pages = 0
    total = 0
    while True:
        page = await mb.browse_release_groups(mbid, offset, mb.OWNER)
        groups.extend(page.items)
        total = page.total
        pages += 1
        if page.next_offset is None:
            return groups, total, pages
        offset = page.next_offset


def _group_counts(groups: list[mb.ReleaseGroupData]) -> tuple[list[dict[str, Any]], int]:
    counted = {name: 0 for name in kinds.GROUPS}
    watched = 0
    for group in groups:
        counted[kinds.group_of(group.primary_type, group.secondary_types)] += 1
        if kinds.is_studio_or_ep(group.primary_type, group.secondary_types):
            watched += 1
    return [{"group": name, "count": count} for name, count in counted.items()], watched


# --- Searching and adding ------------------------------------------------------------------------------ #


@router.get(
    "/search/artists",
    response_model=list[ArtistHit],
    summary="Search artists at MusicBrainz",
    description=(
        "Up to ten artists for a free text; MusicBrainz folds umlauts and accents itself. Various Artists is never a "
        "hit. `in_library` carries the id of an artist that is in the library already."
    ),
    responses=error_responses((422, "invalid_input"), *mb.ERRORS),
)
async def search_artists(
    q: Annotated[str | None, Query(max_length=QUERY_MAX, description="Search text.")] = None,
) -> list[ArtistHit]:
    query = _query(q)
    try:
        hits = await mb.search_artists(query, mb.OWNER)
    except mb.MusicBrainzError as exc:
        raise exc.http() from exc

    def known() -> dict[str, int]:
        with SessionLocal() as db:
            return _in_library_artists(db, [hit.mbid for hit in hits])

    found = await asyncio.to_thread(known)
    return [ArtistHit.model_validate(_hit(hit, found)) for hit in hits]


@router.post(
    "/artists/preview",
    response_model=ArtistPreview,
    summary="Preview an artist before adding it",
    description=(
        "Reads every page of the artist's release groups (Radiohead: 585 groups, six pages, a few seconds) and "
        "counts them per group of the artist page, with how many albums the choice studio would watch (decision 31)."
    ),
    responses=error_responses((422, "invalid_input"), *mb.ERRORS),
)
async def preview_artist(payload: ArtistIn) -> ArtistPreview:
    mbid = _mbid(payload.mbid)
    try:
        artist = await mb.lookup_artist(mbid, mb.OWNER)
        groups, total, pages = await _all_groups(artist.mbid)
    except mb.MusicBrainzError as exc:
        raise exc.http() from exc
    counts, watched = _group_counts(groups)

    def known() -> dict[str, int]:
        with SessionLocal() as db:
            return _in_library_artists(db, [artist.mbid, mbid])

    found = await asyncio.to_thread(known)
    return ArtistPreview(
        mbid=artist.mbid,
        name=artist.name,
        disambiguation=artist.disambiguation,
        artist_type=artist.artist_type,
        country=artist.country,
        begin_year=artist.begin_year,
        end_year=artist.end_year,
        groups_total=total,
        pages=pages,
        groups=[GroupCount.model_validate(item) for item in counts],
        would_watch=watched,
        in_library=found.get(artist.mbid) or found.get(mbid),
    )


def _create_artist(
    data: mb.ArtistData,
    groups: list[mb.ReleaseGroupData],
    requested: str,
    monitor: str,
    types: list[str] | None = None,
    mark: Callable[[OrmSession, Artist, datetime], None] | None = None,
) -> tuple[dict[str, Any], bool]:
    """The artist with every group as a title and the watched albums as versions, in one transaction.

    ``mark`` lets ``/api/v1`` put its origin on a new artist and the versions made with it, in the same transaction.

    ⚠️ Two requests for the same new artist at once (nexbeat's finding 17 of 22.09.2026: two "whole artist" requests
    ended in a 500 with ``UNIQUE constraint failed: artists.mbid``): the transaction takes SQLite's write lock before
    it looks for the artist, so the second waits and finds the first's; a clash all the same answers with the artist.
    """
    try:
        return _create_artist_once(data, groups, requested, monitor, types, mark)
    except IntegrityError:
        with SessionLocal() as db:
            existing = store.find_artist(db, data.mbid) or store.find_artist(db, requested)
            if existing is None:
                raise
            logger.info("Artist %d was added by another request at the same moment", existing.id)
            return _summary(db, existing), False


def _create_artist_once(
    data: mb.ArtistData,
    groups: list[mb.ReleaseGroupData],
    requested: str,
    monitor: str,
    types: list[str] | None,
    mark: Callable[[OrmSession, Artist, datetime], None] | None,
) -> tuple[dict[str, Any], bool]:
    with SessionLocal() as db:
        # A write first, though it matches no row: the lock is taken before anything is read (see above).
        db.execute(
            update(Artist).where(Artist.id < 1).values(name=Artist.name),
            execution_options={"synchronize_session": False},
        )
        moment = utcnow()
        language = store.account_language(db)
        definition = store.ensure_definition(db, language)
        existing = store.find_artist(db, data.mbid) or store.find_artist(db, requested)
        if existing is not None and not existing.is_various:
            if requested != existing.mbid and requested not in (existing.mbid_old or []):
                # Asked for by an id MusicBrainz merged away: remember it, so the next click finds the row at once.
                existing.mbid_old = [*(existing.mbid_old or []), requested]
                db.commit()
            return _summary(db, existing), False
        monitor_new = store.new_albums_for(monitor)
        artist, _new = store.upsert_artist(
            db, data, moment=moment, language=language, requested_mbid=requested, monitor_new=monitor_new,
            priority=loading.PRIORITY_OWNER,
        )  # fmt: skip
        artist.album_types = sorted(set(types)) if types else None
        while_making = monitor if monitor in store.WATCH_WHILE_MAKING else "none"
        known = store.library_artists(db)
        seen: set[str] = set()
        for group in groups:
            store.apply_release_group(
                db, artist, group, moment=moment, first_load=True, watch=while_making, definition=definition,
                artists_by_mbid=known,
            )  # fmt: skip
            seen.add(group.mbid)
        db.flush()
        if monitor not in store.WATCH_WHILE_MAKING:
            store.apply_choice(db, artist, monitor, moment=moment, definition=definition)
        if mark is not None:
            mark(db, artist, moment)
        artist.groups_total = len(groups)
        artist.groups_refreshed_at = moment
        artist.groups_due_at = store.groups_due(artist, loading.newest_album(db, artist.id), moment)
        artist.load_state = "releases"
        artist.load_done = 0
        artist.load_total = None
        artist.updated_at = moment
        db.commit()
        logger.info("Artist %d added: %s, %d release groups, watching %s", artist.id, artist.name, len(groups), monitor)
        return _summary(db, artist), True


@router.post(
    "/artists",
    response_model=ArtistSummary,
    status_code=201,
    summary="Add an artist",
    description=(
        "Creates the artist with every release group as a title and, for the choice studio, one version per studio "
        "album or EP (decision 33). The releases follow in the background. An artist that is in the library already "
        "answers 200 with it: a second click adds nothing twice."
    ),
    responses=error_responses((422, "invalid_input"), *mb.ERRORS),
)
async def add_artist(payload: ArtistIn) -> ArtistSummary:
    mbid = _mbid(payload.mbid)
    if mbid == store.VARIOUS_ARTISTS_MBID:
        raise error("invalid_input", "The input is not valid.", 422, fields=["mbid"])
    try:
        data = await mb.lookup_artist(mbid, mb.OWNER)
        groups, _total, _pages = await _all_groups(data.mbid)
    except mb.MusicBrainzError as exc:
        raise exc.http() from exc
    summary, created = await asyncio.to_thread(_create_artist, data, groups, mbid, payload.monitor, payload.types)
    if created:
        await asyncio.to_thread(_auto_tag_artist, summary["id"])
    return ArtistSummary.model_validate(summary)


def _auto_tag_artist(artist_id: int) -> None:
    """The auto tags for an artist just added; never fails the adding."""
    with SessionLocal() as db:
        if auto_tags.apply_new(db, "album", [artist_id]):
            db.commit()


@router.get(
    "/search/albums",
    response_model=list[AlbumHit],
    summary="Search albums at MusicBrainz",
    description=(
        "Up to ten release groups for a title, optionally of one artist (`artist` is a MusicBrainz id) and of one "
        "kind: album, compilation, single or any. Without a kind, singles of other artists come first (decision 34)."
    ),
    responses=error_responses((422, "invalid_input"), *mb.ERRORS),
)
async def search_albums(
    q: Annotated[str | None, Query(max_length=QUERY_MAX, description="Search text.")] = None,
    artist: Annotated[str | None, Query(max_length=36, description="A MusicBrainz artist id.")] = None,
    kind: Annotated[str | None, Query(max_length=16, description="album, compilation, single or empty.")] = None,
) -> list[AlbumHit]:
    query = _query(q)
    artist_mbid = _mbid(artist) if artist else None
    chosen = (kind or "").strip().lower() or None
    if chosen not in (None, "album", "compilation", "single"):
        raise error("invalid_input", "The input is not valid.", 422, fields=["kind"])
    try:
        groups = await mb.search_release_groups(query, artist_mbid, chosen, mb.OWNER)
    except mb.MusicBrainzError as exc:
        raise exc.http() from exc

    def known() -> dict[str, int]:
        with SessionLocal() as db:
            return _in_library_albums(db, [group.mbid for group in groups])

    found = await asyncio.to_thread(known)
    return [
        AlbumHit(
            mbid=group.mbid,
            title=group.title,
            primary_type=group.primary_type,
            secondary_types=group.secondary_types,
            first_release_date=group.first_release_date,
            artist=mb.credit_text(group.credit),
            artist_mbids=[str(part.get("mbid") or "") for part in group.credit if part.get("mbid")],
            score=group.score,
            in_library=found.get(group.mbid),
        )
        for group in groups
    ]


def _create_album(group: mb.ReleaseGroupData, artists: list[mb.ArtistData]) -> tuple[int, bool]:
    """One album with its version, watched; its artist made with ``monitor_new`` none when new (decision 34)."""
    with SessionLocal() as db:
        moment = utcnow()
        language = store.account_language(db)
        definition = store.ensure_definition(db, language)
        existing = store.find_album(db, group.mbid)
        if existing is not None:
            if store.album_version(db, existing.id) is None:
                store.add_version(db, existing, definition, moment)
                artist = db.get(Artist, existing.artist_id) if existing.artist_id else None
                if artist is not None:
                    loading.queue(db, artist, loading.PRIORITY_OWNER)
                db.commit()
            return existing.id, False
        home: Artist | None = None
        if any(part.get("mbid") == store.VARIOUS_ARTISTS_MBID for part in group.credit):
            home = store.various_artist(db, moment)
        for data in artists:
            artist, _new = store.upsert_artist(
                db, data, moment=moment, language=language, monitor_new="none", priority=loading.PRIORITY_OWNER
            )
            if home is None:
                home = artist
            if artist.load_state == "queued" and artist.groups_refreshed_at is None:
                # A new artist made for its album: its catalogue is not loaded, only this album (decision 34).
                artist.load_state = "ready"
        if home is None:
            raise error("invalid_input", "The input is not valid.", 422, fields=["mbid"])
        title, _new = store.apply_release_group(
            db, home, group, moment=moment, first_load=True, watch="none", definition=definition,
            artists_by_mbid=store.library_artists(db),
        )  # fmt: skip
        store.add_version(db, title, definition, moment)
        loading.queue(db, home, loading.PRIORITY_OWNER)
        db.commit()
        logger.info("Album %d added by hand: %s", title.id, title.title)
        return title.id, True


class AlbumCreated(BaseModel):
    id: int = Field(description="The title id of the album.")
    created: bool


@router.post(
    "/albums",
    response_model=AlbumCreated,
    status_code=201,
    summary="Add one album",
    description=(
        "Adds exactly this release group with a watched version. An artist of the credit that is not in the library "
        "is made with `monitor_new` none, so the album has a home; a compilation hangs on Various Artists "
        "(decisions 34, 35). The releases follow in the background."
    ),
    responses=error_responses((422, "invalid_input"), *mb.ERRORS),
)
async def add_album(payload: AlbumIn) -> AlbumCreated:
    mbid = _mbid(payload.mbid)
    try:
        group = await mb.lookup_release_group(mbid, mb.OWNER)
        artists: list[mb.ArtistData] = []
        for part in group.credit:
            credited = str(part.get("mbid") or "")
            if credited and credited != store.VARIOUS_ARTISTS_MBID and mb.valid_mbid(credited):
                artists.append(await mb.lookup_artist(credited, mb.OWNER))
    except mb.MusicBrainzError as exc:
        raise exc.http() from exc
    title_id, created = await asyncio.to_thread(_create_album, group, artists)
    return AlbumCreated(id=title_id, created=created)


# --- Artists ------------------------------------------------------------------------------ #


@router.get(
    "/artists",
    response_model=ArtistList,
    summary="List the artists",
    description="The artists of the library with the counts of their tiles (decision 38), sorted by sort name.",
    responses=error_responses((422, "invalid_input")),
)
def list_artists(
    db: DbSession,
    q: Annotated[str | None, Query(max_length=QUERY_MAX, description="Search text.")] = None,
    sort: Annotated[str | None, Query(max_length=16, description="name or added. Default name.")] = None,
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 60,
    tag: Annotated[str | None, Query(max_length=64, description="Only artists carrying this tag.")] = None,
) -> ArtistList:
    conditions = []
    forms = query_keys(q)
    if forms:
        conditions.append(or_(*(Artist.search_keys.contains(form, autoescape=True) for form in forms)))
    label = tags.clean(tag) if tag else None
    if label is not None:
        conditions.append(Artist.id.in_(tags.artist_ids_with(label)))
    chosen_sort = (sort or "").strip() or "name"
    if chosen_sort not in ("name", "added"):
        raise error("invalid_input", "The input is not valid.", 422, fields=["sort"])
    order = (Artist.added.desc(), Artist.id.desc()) if chosen_sort == "added" else (Artist.sort_key, Artist.id)
    total = int(db.scalar(select(func.count(Artist.id)).where(*conditions)) or 0)
    query = select(Artist).where(*conditions).order_by(*order).offset((page - 1) * page_size).limit(page_size)
    rows = list(db.scalars(query))
    return ArtistList(items=[ArtistSummary.model_validate(summary) for summary in _summaries(db, rows)], total=total)


@router.get(
    "/artists/{artist_id}",
    response_model=ArtistDetail,
    summary="Read an artist page",
    description=(
        "The artist with its albums in the groups of decision 26; studio albums and EPs come open, the others closed "
        "with their count. Joint albums of other artists of the library appear too (decision 3)."
    ),
    responses=error_responses((404, "not_found")),
)
def read_artist(db: DbSession, artist_id: int) -> ArtistDetail:
    artist = _artist_or_404(db, artist_id)
    return ArtistDetail.model_validate(
        {
            **_summary(db, artist),
            "groups": _groups_of(db, artist),
            "groups_refreshed_at": artist.groups_refreshed_at,
            "groups_due_at": artist.groups_due_at,
        }
    )


class ArtistsMonitorIn(BaseModel):
    monitor_new: Literal["all", "none"]
    artist_ids: list[int] | None = Field(
        default=None, max_length=5000, description="The artists the owner marked; null means every artist."
    )
    q: str | None = Field(default=None, max_length=200, description="The search text of the view, without ids.")
    tag: str | None = Field(default=None, max_length=64, description="The tag filter of the view, without ids.")


class ArtistsMonitorOut(BaseModel):
    changed: int = Field(description="Artists whose setting changed.")


@router.put(
    "/artists/monitor-new",
    response_model=ArtistsMonitorOut,
    summary="Set what several artists do with new albums",
    description=(
        "`monitor_new` for the artists the owner marked, or for every artist the search finds. all watches new "
        "studio albums and EPs, none nothing."
    ),
    responses=error_responses((422, "invalid_input")),
)
def change_artists_monitor(db: DbSession, payload: ArtistsMonitorIn) -> ArtistsMonitorOut:
    conditions = [] if payload.artist_ids is None else [Artist.id.in_(payload.artist_ids)]
    forms = query_keys(payload.q) if payload.artist_ids is None else []
    if forms:
        conditions.append(or_(*(Artist.search_keys.contains(form, autoescape=True) for form in forms)))
    label = tags.clean(payload.tag) if payload.tag and payload.artist_ids is None else None
    if label is not None:
        conditions.append(Artist.id.in_(tags.artist_ids_with(label)))
    rows = list(db.scalars(select(Artist).where(*conditions)))
    moment = utcnow()
    changed = 0
    for artist in rows:
        if artist.monitor_new != payload.monitor_new:
            artist.monitor_new = payload.monitor_new
            artist.updated_at = moment
            changed += 1
    if changed:
        db.commit()
    logger.info("New albums: %d artists are now %s", changed, payload.monitor_new)
    return ArtistsMonitorOut(changed=changed)


@router.patch(
    "/artists/{artist_id}",
    response_model=ArtistSummary,
    summary="Change an artist",
    description=(
        "`monitor_new`: all watches new albums of the artist's types, none nothing (decision 24). `monitored` false "
        "freezes the artist as unmonitoring one in Lidarr: nothing of it is searched on its own any more, and the "
        "albums keep their switches, so thawing brings back what was watched."
    ),
    responses=error_responses((404, "not_found"), (422, "invalid_input")),
)
def change_artist(db: DbSession, artist_id: int, payload: ArtistPatch) -> ArtistSummary:
    artist = _artist_or_404(db, artist_id)
    if payload.monitor_new is not None:
        if payload.monitor_new not in MONITOR_NEW:
            raise error("invalid_input", "The input is not valid.", 422, fields=["monitor_new"])
        artist.monitor_new = payload.monitor_new
        artist.updated_at = utcnow()
        db.commit()
    if payload.monitored is not None and payload.monitored != (artist.frozen_at is None):
        freeze(db, artist, not payload.monitored)
        db.commit()
    return ArtistSummary.model_validate(_summary(db, artist))


def freeze(db: OrmSession, artist: Artist, frozen: bool) -> None:
    """Freeze or thaw an artist (fork 3). The albums keep their switches; the planned search
    passes by every album of a frozen artist, so thawing brings back exactly what was watched."""
    moment = utcnow()
    artist.frozen_at = moment if frozen else None
    artist.updated_at = moment
    albums = list(db.scalars(select(Title.id).where(Title.kind == "album", Title.artist_id == artist.id)))
    # The plan of each album changes with it: a frozen artist's album is not due, a thawed one is planned again.
    planning.replan(db, albums, moment)
    logger.info("Artist %d %s", artist.id, "frozen" if frozen else "thawed")


@router.post(
    "/artists/{artist_id}/refresh",
    response_model=ArtistSummary,
    summary="Refresh an artist now",
    description="Queues the artist ahead of every background load (decision 39); the job takes it within 30 seconds.",
    responses=error_responses((404, "not_found"), (422, "invalid_input")),
)
def refresh_artist(db: DbSession, artist_id: int) -> ArtistSummary:
    artist = _artist_or_404(db, artist_id)
    if artist.is_various:
        raise error("invalid_input", "The input is not valid.", 422, fields=["artist_id"])
    loading.queue(db, artist, loading.PRIORITY_OWNER)
    db.commit()
    return ArtistSummary.model_validate(_summary(db, artist))


class ArtistRemoved(BaseModel):
    artist_id: int
    albums_removed: int
    versions_removed: int


@router.delete(
    "/artists/{artist_id}",
    response_model=ArtistRemoved,
    summary="Remove an artist",
    description=(
        "Removes the artist and every album of it from the library; files stay where they are (decision 43). "
        "A joint album another artist of the library also claims stays with that artist. Various Artists cannot be "
        "removed; a sampler goes like any other title."
    ),
    responses=error_responses((404, "not_found"), (422, "invalid_input")),
)
def remove_artist(db: DbSession, artist_id: int) -> ArtistRemoved:
    artist = _artist_or_404(db, artist_id)
    if artist.is_various:
        # The collection point of every sampler (decision 35) stays; a sampler goes like any other title.
        raise error("invalid_input", "The input is not valid.", 422, fields=["artist_id"])
    titles = list(db.scalars(select(Title).where(Title.kind == "album", Title.artist_id == artist.id)))
    albums = 0
    versions = 0
    for title in titles:
        other = db.scalar(
            select(store.AlbumArtist.artist_id)
            .where(store.AlbumArtist.title_id == title.id, store.AlbumArtist.artist_id != artist.id)
            .order_by(store.AlbumArtist.position)
        )
        if other is not None:
            title.artist_id = other
            continue
        versions += len(list(db.scalars(select(Version.id).where(Version.title_id == title.id))))
        db.delete(title)
        albums += 1
    db.delete(artist)
    db.commit()
    logger.info("Artist %d removed with %d albums and %d versions", artist_id, albums, versions)
    return ArtistRemoved(artist_id=artist_id, albums_removed=albums, versions_removed=versions)


# --- Loading, releases, the target ------------------------------------------------------------------------------ #


@router.get(
    "/loading",
    response_model=LoadingStatus,
    summary="Read the loading status",
    description="The queue of artists, the one being loaded with its step, requests and 503 answers of the last hour.",
)
def read_loading(db: DbSession) -> LoadingStatus:
    return LoadingStatus.model_validate(loading.status(db))


def _release_detail(db: OrmSession, release: Release) -> dict[str, Any]:
    from ..models import ReleaseMedium, ReleaseTrack

    media = list(
        db.scalars(select(ReleaseMedium).where(ReleaseMedium.release_id == release.id).order_by(ReleaseMedium.position))
    )
    tracks = list(
        db.scalars(
            select(ReleaseTrack)
            .where(ReleaseTrack.release_id == release.id)
            .order_by(ReleaseTrack.medium_id, ReleaseTrack.position)
        )
    )
    by_medium: dict[int, list[ReleaseTrack]] = {}
    for track in tracks:
        by_medium.setdefault(track.medium_id, []).append(track)
    return {
        "id": release.id,
        "mbid": release.mbid,
        "name": release.name,
        "date": release.date,
        "country": release.country,
        "formats": list(release.formats or []),
        "media_count": release.media_count,
        "track_count": release.track_count,
        "labels": list(release.labels or []),
        "disambiguation": release.disambiguation,
        "tracks_loaded": release.tracks_loaded,
        "media": [
            {
                "position": medium.position,
                "format": medium.format,
                "name": medium.name,
                "track_count": medium.track_count,
                "tracks": [
                    {
                        "id": track.id,
                        "medium": medium.position,
                        "position": track.position,
                        "number": track.number,
                        "name": track.name,
                        "length_ms": track.length_ms,
                        "artist_credit": track.artist_credit,
                    }
                    for track in by_medium.get(medium.id, [])
                ],
            }
            for medium in media
        ],
    }


@router.get(
    "/releases/{release_id}",
    response_model=ReleaseDetail,
    summary="Read one release with its tracks",
    description="One official release of an album with its media and tracks, for the dialog that chooses the target.",
    responses=error_responses((404, "not_found")),
)
def read_release(db: DbSession, release_id: int) -> ReleaseDetail:
    release = db.get(Release, release_id)
    if release is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    return ReleaseDetail.model_validate(_release_detail(db, release))


def _load_now(title_id: int) -> None:
    """The releases of one album on the owner lane, for an album page that waits (decision 20)."""
    with SessionLocal() as db:
        title = db.get(Title, title_id)
        if title is None or title.kind != "album" or not title.mbid:
            return
        loading.load_releases_of(db, title, lane=mb.OWNER)


@router.post(
    "/albums/{title_id}/releases",
    response_model=dict[str, int],
    summary="Load the releases of an album now",
    description="Asks MusicBrainz for every official release with its tracks on the owner lane, then the target rule.",
    responses=error_responses((404, "not_found"), *mb.ERRORS),
)
async def load_album_releases(title_id: int) -> dict[str, int]:
    def check() -> None:
        with SessionLocal() as db:
            title = db.get(Title, title_id)
            if title is None or title.kind != "album":
                raise error("not_found", "This does not exist, or not any more.", 404)

    await asyncio.to_thread(check)
    try:
        await asyncio.to_thread(_load_now, title_id)
    except mb.MusicBrainzError as exc:
        raise exc.http() from exc
    with SessionLocal() as db:
        return {"releases": int(db.scalar(select(func.count(Release.id)).where(Release.title_id == title_id)) or 0)}


class TargetOut(BaseModel):
    version_id: int
    target_release_id: int | None
    target_set_by: str | None
    target_reason: dict[str, Any] | None
    target_suggestion_id: int | None
    track_counts: dict[str, int] | None


@router.patch(
    "/albums/{title_id}/target",
    response_model=TargetOut,
    summary="Choose the target release",
    description=(
        "The owner picks any official release of the album, a box too; the rule never changes it afterwards. Null "
        "hands the choice back to the rule (decision 30)."
    ),
    responses=error_responses((404, "not_found"), (422, "invalid_input")),
)
def change_target(db: DbSession, title_id: int, payload: TargetIn) -> TargetOut:
    title = db.get(Title, title_id)
    if title is None or title.kind != "album":
        raise error("not_found", "This does not exist, or not any more.", 404)
    version = store.album_version(db, title_id)
    if version is None:
        raise error("invalid_input", "The input is not valid.", 422, fields=["title_id"])
    release = None
    if payload.release_id is not None:
        release = db.get(Release, payload.release_id)
        if release is None or release.title_id != title_id or (release.status or "official") != "official":
            raise error("invalid_input", "The input is not valid.", 422, fields=["release_id"])
    store.set_owner_target(db, title, version, release, moment=utcnow(), language=store.account_language(db))
    db.commit()
    return TargetOut(
        version_id=version.id,
        target_release_id=version.target_release_id,
        target_set_by=version.target_set_by,
        target_reason=version.target_reason,
        target_suggestion_id=version.target_suggestion_id,
        track_counts=version.track_counts,
    )


@router.patch(
    "/albums/{title_id}/watch",
    response_model=WatchOut,
    summary="Watch an album or stop watching it",
    description=(
        "On: the album gets its version when it has none (E5: a single is watched only by hand like this) and the "
        "artist's releases are loaded ahead of the background. Off: the version stays, unwatched. A version a Lidarr "
        "connection feeds is watched there; here it answers 409."
    ),
    responses=error_responses((404, "not_found"), (409, "album_fed_by_source")),
)
def change_watch(db: DbSession, title_id: int, payload: WatchIn) -> WatchOut:
    title = db.get(Title, title_id)
    if title is None or title.kind != "album":
        raise error("not_found", "This does not exist, or not any more.", 404)
    version = store.album_version(db, title_id)
    moment = utcnow()
    if version is not None and version.source_id is not None:
        raise error("album_fed_by_source", "This album is watched in Lidarr; change it there.", 409)
    if version is None:
        if not payload.monitored:
            return WatchOut(title_id=title_id, version_id=None, monitored=False, state=None)
        definition = store.ensure_definition(db, store.account_language(db))
        version = store.add_version(db, title, definition, moment)
        store.refresh_target(db, title, version, moment=moment, language=store.account_language(db))
        artist = db.get(Artist, title.artist_id) if title.artist_id is not None else None
        if artist is not None:
            loading.queue(db, artist, loading.PRIORITY_OWNER)
    else:
        version.monitored = payload.monitored
        version.state = store.state_of(version)
        version.updated_at = moment
    db.commit()
    return WatchOut(title_id=title_id, version_id=version.id, monitored=version.monitored, state=version.state)


# --- Settings ------------------------------------------------------------------------------ #


def _countries_out(db: OrmSession) -> CountriesOut:
    definition = store.definition(db)
    language = store.account_language(db)
    own = list(definition.music_countries) if definition is not None and definition.music_countries else None
    return CountriesOut(countries=own, default=list(target.countries_for(language, None)))


@router.get(
    "/settings/countries",
    response_model=CountriesOut,
    summary="Read the country order for the target release",
    description="The countries that break a tie between releases with the same number of tracks (decision 28).",
)
def read_countries(db: DbSession) -> CountriesOut:
    return _countries_out(db)


@router.put(
    "/settings/countries",
    response_model=CountriesOut,
    summary="Change the country order for the target release",
    description=(
        "Up to twelve ISO codes in order, MusicBrainz's XE (Europe) and XW (worldwide) included; null returns to the "
        "default of the language. The rule runs again for every album without a file or an owner's choice."
    ),
    responses=error_responses((422, "invalid_input")),
)
def change_countries(db: DbSession, payload: CountriesIn) -> CountriesOut:
    codes: list[str] | None = None
    if payload.countries is not None:
        codes = []
        for raw in payload.countries:
            code = raw.strip().upper()
            if not _COUNTRY.match(code):
                raise error("invalid_input", "The input is not valid.", 422, fields=["countries"])
            if code not in codes:
                codes.append(code)
    definition = store.ensure_definition(db, store.account_language(db))
    definition.music_countries = codes or None
    db.flush()
    moment = utcnow()
    language = store.account_language(db)
    for title in db.scalars(select(Title).where(Title.kind == "album", Title.releases_state == "tracks")):
        store.refresh_targets_of_title(db, title, moment=moment, language=language)
    db.commit()
    logger.info("Music: country order %s", "set" if codes else "back to the default")
    return _countries_out(db)


@router.get(
    "/settings/musicbrainz",
    response_model=MusicBrainzState,
    summary="Read the MusicBrainz and Cover Art Archive switches",
    description="Both are on from the start. Off, nexcrate sends nothing to musicbrainz.org or coverartarchive.org.",
)
def read_musicbrainz(db: DbSession) -> MusicBrainzState:
    return MusicBrainzState.model_validate(mb.state(db))


@router.put(
    "/settings/musicbrainz",
    response_model=MusicBrainzState,
    summary="Switch MusicBrainz or the Cover Art Archive",
    description=(
        "MusicBrainz off: nothing is asked; adding and refreshing answer musicbrainz_disabled; everything stored stays "
        "visible. Cover Art Archive off: only covers already on disk are shown (decision 16)."
    ),
)
def change_musicbrainz(db: DbSession, payload: MusicBrainzIn) -> MusicBrainzState:
    if payload.enabled is not None:
        set_setting(db, mb.SETTING_ENABLED, "1" if payload.enabled else "0")
        logger.info("MusicBrainz switched %s", "on" if payload.enabled else "off")
    if payload.covers_enabled is not None:
        set_setting(db, mb.SETTING_COVERS_ENABLED, "1" if payload.covers_enabled else "0")
        logger.info("Cover Art Archive switched %s", "on" if payload.covers_enabled else "off")
    db.commit()
    return MusicBrainzState.model_validate(mb.state(db))


@router.get(
    "/settings/files",
    response_model=MusicFilesState,
    summary="Read the settings of filed music",
    description="Whether tags and the cover are written into filed music; on from the start.",
)
def read_music_files(db: DbSession) -> MusicFilesState:
    return MusicFilesState(write_tags=tag_writing.write_enabled(db))


@router.put(
    "/settings/files",
    response_model=MusicFilesState,
    summary="Change the settings of filed music",
    description=(
        "Off: nothing is written into the files, and a torrent is linked instead of copied. Files already filed keep "
        "their tags."
    ),
)
def change_music_files(db: DbSession, payload: MusicFilesState) -> MusicFilesState:
    tag_writing.set_write_enabled(db, payload.write_tags)
    db.commit()
    logger.info("Music: writing tags switched %s", "on" if payload.write_tags else "off")
    return MusicFilesState(write_tags=tag_writing.write_enabled(db))


@router.get(
    "/settings/acoustid",
    response_model=AcoustIdState,
    summary="Read the fingerprint settings",
    description=(
        "Fingerprinting is the last fallback of mapping files to tracks (M4.9). It needs an "
        "AcoustID application key (the installation's own, else the one nexcrate ships) and fpcalc; without them the "
        "owner assigns by hand."
    ),
)
def read_acoustid(db: DbSession) -> AcoustIdState:
    return AcoustIdState.model_validate(fingerprint.state(db))


@router.put(
    "/settings/acoustid",
    response_model=AcoustIdState,
    summary="Change the fingerprint settings",
    description=(
        "A new key is first checked at acoustid.org (a made-up track id, no fingerprint) and stored encrypted only "
        "when AcoustID knows it; an empty text removes it, and the shipped key applies again. Off, nothing is sent "
        "to acoustid.org."
    ),
    responses=error_responses(
        (422, "invalid_input"), (422, "acoustid_key_invalid"), (502, "acoustid_unreachable"), (502, "acoustid_error")
    ),
)
async def change_acoustid(payload: AcoustIdIn) -> AcoustIdState:
    value = payload.key.strip() if payload.key is not None else None
    if value and not re.fullmatch(r"[A-Za-z0-9_-]{4,64}", value):
        raise error("invalid_input", "The input is not valid.", 422, fields=["key"])
    if value:
        try:
            await fingerprint.check_key(value)
        except fingerprint.LookupFailed as exc:
            logger.info("AcoustID key test failed: %s", exc.code)
            status = 422 if exc.code == "acoustid_key_invalid" else 502
            raise error(exc.code, _ACOUSTID_MESSAGES[exc.code], status, fields=["key"]) from exc
    return await asyncio.to_thread(_change_acoustid, value, payload.enabled)


_ACOUSTID_MESSAGES = {
    "acoustid_key_invalid": "AcoustID does not know this application key.",
    "acoustid_unreachable": "acoustid.org could not be reached.",
    "acoustid_error": "AcoustID answered with an error.",
}


def _change_acoustid(value: str | None, enabled: bool | None) -> AcoustIdState:
    with SessionLocal() as db:
        if value is not None:
            fingerprint.set_key(db, value or None)
            # A new key was checked just now; without one the shipped key applies, not checked yet.
            set_setting(db, fingerprint.SETTING_LAST_OK, utcnow().isoformat() if value else "")
            logger.info("AcoustID key %s", "stored" if value else "removed")
        if enabled is not None:
            fingerprint.set_enabled(db, enabled)
            logger.info("AcoustID switched %s", "on" if enabled else "off")
        db.commit()
        return AcoustIdState.model_validate(fingerprint.state(db))


@router.post(
    "/settings/acoustid/check",
    response_model=AcoustIdState,
    summary="Check the AcoustID key in use",
    description=(
        "Asks acoustid.org whether it knows the key a lookup would use now (the installation's own, else the one "
        "nexcrate ships), with a made-up track id and no fingerprint. The answer lands in `last_ok_at` or "
        "`last_error_code`; the key is never shown. 409 `acoustid_no_key` without any key."
    ),
    responses=error_responses((409, "acoustid_no_key")),
)
async def check_acoustid() -> AcoustIdState:
    def current() -> str | None:
        with SessionLocal() as db:
            return fingerprint.key(db)

    key = await asyncio.to_thread(current)
    if key is None:
        raise error("acoustid_no_key", "No AcoustID key is stored or shipped.", 409)
    code: str | None = None
    try:
        await fingerprint.check_key(key)
    except fingerprint.LookupFailed as exc:
        code = exc.code
    logger.info("AcoustID key checked: %s", code or "ok")

    def store_outcome() -> AcoustIdState:
        with SessionLocal() as db:
            set_setting(db, fingerprint.SETTING_LAST_ERROR, code or "")
            if code is None:
                set_setting(db, fingerprint.SETTING_LAST_OK, utcnow().isoformat())
            db.commit()
            return AcoustIdState.model_validate(fingerprint.state(db))

    return await asyncio.to_thread(store_outcome)


_RETAG_MESSAGES = {
    "album_fed_by_source": "This album comes from a Lidarr connection; nexcrate does not touch its files.",
    "album_without_files": "This album has no files nexcrate filed.",
    "album_folder_missing": "The album folder cannot be seen.",
}


def _retag_error(exc: retag.RetagRefused) -> Exception:
    return error(exc.code, _RETAG_MESSAGES[exc.code], 409)


@router.get(
    "/albums/{title_id}/tags",
    response_model=TagPreviewOut,
    summary="Preview the tags of an album",
    description=(
        "Per file of an album nexcrate filed the fields that would change, the MusicBrainz ids included, and whether "
        "the file can be written (decision 29). Nothing is written."
    ),
    responses=error_responses(*((409, code) for code in _RETAG_MESSAGES)),
)
async def preview_album_tags(title_id: int) -> TagPreviewOut:
    try:
        return TagPreviewOut.model_validate(await asyncio.to_thread(retag.preview, title_id))
    except retag.RetagRefused as exc:
        raise _retag_error(exc) from exc


@router.post(
    "/albums/{title_id}/tags",
    response_model=TagPreviewOut,
    summary="Write the tags of an album",
    description=(
        "Writes every file whose fields differ, with the cover when the Cover Art Archive is on. Never a file with a "
        "second link, never a file of a Lidarr connection. The answer is the preview afterwards."
    ),
    responses=error_responses(*((409, code) for code in _RETAG_MESSAGES)),
)
async def write_album_tags(title_id: int) -> TagPreviewOut:
    try:
        return TagPreviewOut.model_validate(await asyncio.to_thread(retag.write, title_id))
    except retag.RetagRefused as exc:
        raise _retag_error(exc) from exc


_FOLDER_MESSAGES = {
    "not_found": ("Not found.", 404),
    "album_fed_by_source": ("This album comes from a Lidarr connection; nexcrate does not touch its files.", 409),
    "album_folder_missing": ("The album folder cannot be seen.", 409),
    "track_not_in_album": ("This track is not a track of the album.", 422),
    "track_has_file": ("This track has a file already.", 409),
}


def _folder_error(exc: album_read.Refused) -> Exception:
    message, status = _FOLDER_MESSAGES[exc.code]
    return error(exc.code, message, status)


class FolderReadOut(BaseModel):
    linked: int = Field(description="New files linked to a track by their tags or names.")
    unclear: int = Field(description="New files without a track; the owner settles them.")
    gone: int = Field(description="Files no longer on the disk, forgotten.")
    known: int = Field(description="Files read before and still there.")


class SettleIn(BaseModel):
    track_id: int | None = Field(
        default=None, description="The track the file holds; null: the file stays in the folder without a track."
    )


class SettleOut(BaseModel):
    unclear_left: int = Field(description="Unclear files of the album afterwards.")


@router.post(
    "/albums/{title_id}/read",
    response_model=FolderReadOut,
    summary="Read the album folder again",
    description=(
        "Only an own album with a folder (decision 12): new files are linked by their tags and "
        "names or stay unclear, files gone from the disk are forgotten. Files are never moved or renamed."
    ),
    responses=error_responses((404, "not_found"), (409, "album_fed_by_source"), (409, "album_folder_missing")),
)
async def read_album_folder(title_id: int) -> FolderReadOut:
    try:
        counts = await asyncio.to_thread(album_read.read_title, title_id)
    except album_read.Refused as exc:
        raise _folder_error(exc) from exc
    return FolderReadOut.model_validate(counts)


@router.put(
    "/albums/{title_id}/files/{file_id}",
    response_model=SettleOut,
    summary="Settle an unclear file of an album",
    description=(
        "Links an unclear file to a track of the album, or with track_id null leaves it in the folder without a track "
        "(decision 15). A track whose song holds a file already is refused."
    ),
    responses=error_responses(*((status, code) for code, (_, status) in _FOLDER_MESSAGES.items())),
)
async def settle_album_file(title_id: int, file_id: int, payload: SettleIn) -> SettleOut:
    try:
        left = await asyncio.to_thread(album_read.settle, title_id, file_id, payload.track_id)
    except album_read.Refused as exc:
        raise _folder_error(exc) from exc
    return SettleOut(unclear_left=left)


__all__ = ["LOAD_STATES", "router"]
