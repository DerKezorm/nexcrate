"""``/api/v1``, stage V2: other programs request, take back, freeze, delete through the bin, and ask for a search
.

Every route here needs the scope ``request``; reading the bin needs ``read`` only. The six rules of ``v1.py`` hold:
a title is named by ``kind`` and ``ref``, a version by its fixed ``version_id``, and the scope of a series lies under
``series`` (rule 4). Errors are flat.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..db import SessionLocal
from ..deps import DbSession, require_scope
from ..meldungen import error, v1_error_responses
from ..models import ApiKey, ReleaseTrack, Title, Version
from ..services import recycle_bin, tmdb
from ..services.api_v1 import music_writing, titles, writing
from ..services.api_v1 import versions as v1_versions
from ..services.downloads import store as download_store
from . import library as library_router
from .v1 import TitleOut

router = APIRouter(prefix="/api/v1", tags=["v1"], dependencies=[Depends(require_scope("read"))])

RequestKey = Annotated[ApiKey, Depends(require_scope("request"))]

_TITLE_ERRORS = (
    (422, "kind_unsupported"),
    (422, "ref_invalid"),
    (422, "ref_source_unknown"),
    (404, "title_not_found"),
    (409, "ref_ambiguous"),
)
_SCOPE_ERRORS = (
    (422, "version_unknown"),
    (422, "version_kind_mismatch"),
    (404, "version_not_found"),
    (409, "version_fed_by_source"),
    (422, "scope_not_for_kind"),
    (422, "season_not_found"),
    (422, "episode_not_found"),
    (422, "track_not_found"),
)


def _caller(key: ApiKey) -> writing.Caller:
    return writing.Caller(key_id=key.id, key_name=key.name)


# --- The scope of a series (rule 4) ------------------------------------------------------------------------------ #


class EpisodeIn(BaseModel):
    season: int = Field(ge=0, le=10_000, description="TMDB's season number.")
    episode: int = Field(ge=0, le=100_000, description="TMDB's episode number.")


class SeriesScopeIn(BaseModel):
    seasons: Literal["all"] | list[Annotated[int, Field(ge=0, le=10_000)]] | None = Field(
        default=None,
        max_length=writing.EPISODES_MAX,
        description="`all`, or TMDB's season numbers; 0 are the specials, which `all` leaves out. Without it and "
        "without `episodes` the whole series is meant; with `episodes` only those episodes. `[]` names no season.",
    )
    future_seasons: bool | None = Field(
        default=None,
        description="Requesting: whether seasons TMDB names later come by themselves; without it only when the whole "
        "series is asked. Ignored elsewhere.",
    )
    episodes: list[EpisodeIn] = Field(default_factory=list, max_length=writing.EPISODES_MAX)

    def scope(self) -> writing.SeriesScope:
        seasons = None if self.seasons in (None, "all") else tuple(dict.fromkeys(self.seasons or []))
        pairs = tuple(dict.fromkeys((item.season, item.episode) for item in self.episodes))
        return writing.SeriesScope(seasons=seasons, future_seasons=self.future_seasons, episodes=pairs)


def _scope(series: SeriesScopeIn | None) -> writing.SeriesScope | None:
    return series.scope() if series is not None else None


# --- The scope of an artist and of an album ---------------------------------------------- #


class ArtistScopeIn(BaseModel):
    albums: Literal["studio", "future", "none", "missing", "existing", "first", "latest"] = Field(
        default="studio",
        description="Which albums to watch now, as Lidarr's add dialog: every album of `types`, only those that "
        "appear from now on, none, those without files, those with files, the first or the latest. New albums "
        "are watched afterwards with every choice but none and existing.",
    )
    types: list[Literal["studio", "ep", "single", "live", "compilation", "soundtrack", "remix", "spoken", "other"]] | None = Field(  # noqa: E501
        default=None,
        max_length=9,
        description="The groups of the artist page that count, like Lidarr's metadata profile; without it studio "
        "albums and EPs.",
    )

    def scope(self) -> music_writing.ArtistScope:
        return music_writing.ArtistScope(
            albums=self.albums, types=tuple(dict.fromkeys(self.types)) if self.types is not None else None
        )


class AlbumScopeIn(BaseModel):
    tracks: list[Annotated[str, Field(max_length=64)]] | None = Field(
        default=None,
        max_length=writing.TRACKS_MAX,
        description="`mbid:<track>` as the album's `media` lists them; without it the whole album.",
    )


class ArtistMonitoringIn(BaseModel):
    new_albums: Literal["all", "none"] | None = Field(
        default=None, description="Whether albums that appear later get watched; without it unchanged."
    )


VersionIds = Annotated[
    list[Annotated[str, Field(max_length=16)]] | None,
    Field(
        default=None,
        max_length=writing.VERSIONS_MAX,
        description="Fixed version ids (`v_…`); without it every version of nexcrate's own.",
    ),
]


# --- Requesting ------------------------------------------------------------------------------------------------- #


class RequestIn(BaseModel):
    kind: str = Field(max_length=16, examples=["series"])
    ref: str = Field(
        max_length=64,
        description="A new title only by `tmdb:`, a new album or artist by `mbid:` (its release group or artist).",
        examples=["tmdb:1399"],
    )
    versions: list[Annotated[str, Field(max_length=16)]] | None = Field(
        default=None,
        max_length=writing.VERSIONS_MAX,
        description="Fixed version ids. Needed for a movie or series; an album takes the one music version "
        "without it, an artist has none.",
        examples=[["v_7c1e90ab"]],
    )
    series: SeriesScopeIn | None = Field(default=None, description="Only for a series; a movie answers 422.")
    artist: ArtistScopeIn | None = Field(default=None, description="Only for an artist: which albums to watch.")
    search_now: bool = Field(default=True, description="Search as soon as the order of the automatic allows.")
    origin: str | None = Field(
        default=None,
        max_length=200,
        description=f"The program's own reference, at most {writing.ORIGIN_MAX_LENGTH} printable characters; listed "
        "back with the title and its versions.",
        examples=["nexview:request:123"],
    )


class RequestedVersionOut(BaseModel):
    version_id: str | None
    outcome: str = Field(description="added, extended or unchanged.")


class NoteOut(BaseModel):
    code: str = Field(
        description="version_not_ready, with the reasons of GET /api/v1/versions; anime_not_supported, an anime "
        "series nexcrate takes but does not search yet (`capabilities.anime` false). Skip a code you do not know."
    )
    params: dict[str, Any]


class RequestOut(BaseModel):
    created: bool = Field(description="True when the title came in with this request.")
    versions: list[RequestedVersionOut]
    albums_watched: int | None = Field(
        default=None, description="An artist: how many of its albums this request watched; null otherwise."
    )
    search: str = Field(
        description="queued, not_asked, nothing_wanted, or not_possible (an anime series while `capabilities.anime` "
        "is false; the note anime_not_supported says it)."
    )
    notes: list[NoteOut]
    title: TitleOut


@router.post(
    "/requests",
    response_model=RequestOut,
    summary="Request a title, or request more of it",
    description=(
        "One call for both: a title nexcrate lacks is added from TMDB, versions it lacks are added, and the seasons "
        "and episodes under `series` are switched on. Asking twice gives the same, never a double. Requesting more "
        "never switches anything off, and what the owner switched by hand stays. A version a Radarr or Sonarr "
        "connection feeds answers 409. `search_now` puts the title into the order of the automatic, even with its "
        "switch off, within the indexers' budget. 200 when the title was there, 201 when it came with this call."
    ),
    responses=v1_error_responses(
        *_TITLE_ERRORS,
        *_SCOPE_ERRORS,
        (422, "ref_not_addable"),
        (422, "invalid_input"),
        *tmdb.ERRORS,
    ),
    status_code=200,
)
async def make_request(payload: RequestIn, key: RequestKey) -> JSONResponse:
    if payload.artist is not None and payload.kind != "artist":
        raise error("scope_not_for_kind", f"A {payload.kind} has no albums to choose.", 422, kind=payload.kind)
    if payload.kind == "artist":
        if payload.series is not None or payload.versions:
            raise error("scope_not_for_kind", "An artist has no versions, seasons or episodes.", 422, kind="artist")
        found = await music_writing.request_artist(
            payload.ref,
            (payload.artist or ArtistScopeIn()).scope(),
            payload.search_now,
            writing.clean_origin(payload.origin),
            _caller(key),
        )
    else:
        request = writing.RequestIn(
            kind=payload.kind,
            ref=payload.ref,
            versions=list(payload.versions or []),
            series=_scope(payload.series),
            search_now=payload.search_now,
            origin=writing.clean_origin(payload.origin),
        )
        found = await writing.request(request, _caller(key))
    body = RequestOut.model_validate(found).model_dump(mode="json")
    return JSONResponse(status_code=201 if found["created"] else 200, content=body)


# --- Taking back, freezing, deleting ------------------------------------------------------------------------------ #


class WithdrawIn(BaseModel):
    versions: VersionIds = None
    series: SeriesScopeIn | None = None
    delete_files: bool = Field(default=False, description="Also move the files of the scope into the recycle bin.")


class WithdrawnAlbumOut(BaseModel):
    ref: str = Field(description="`mbid:<release group>`.")
    monitoring_off: bool
    downloads_cancelled: int
    files_recycled: int
    version_removed: bool


class WithdrawnVersionOut(BaseModel):
    version_id: str | None
    monitoring_off: bool
    downloads_cancelled: int
    files_recycled: int
    version_removed: bool = Field(description="A version a program added, empty afterwards, goes.")


class WithdrawOut(BaseModel):
    title_removed: bool = Field(
        description="A title a program added goes when nothing of it is watched and no file is left. An album stays in "
        "its artist's catalogue; an artist a program added goes when none of its albums is left."
    )
    versions: list[WithdrawnVersionOut]
    albums: list[WithdrawnAlbumOut] | None = Field(default=None, description="An artist: what happened per album.")


@router.post(
    "/titles/{kind}/{ref}/withdraw",
    response_model=WithdrawOut,
    summary="Take a request back",
    description=(
        "Watching off for the scope, running downloads that lie wholly in it stopped at their client (never "
        "blocklisted), and with `delete_files` the files moved into the recycle bin. A download being filed away "
        "cannot be stopped: then 409 `download_finished` and nothing changed. A title or version a program brought and "
        "that is empty afterwards goes."
    ),
    responses=v1_error_responses(
        *_TITLE_ERRORS, *_SCOPE_ERRORS, (409, "download_finished"), (409, "download_importing")
    ),
)
async def withdraw(kind: str, ref: str, payload: WithdrawIn, key: RequestKey) -> WithdrawOut:
    if kind == "artist":
        if payload.versions or payload.series is not None:
            raise error("scope_not_for_kind", "An artist has no versions, seasons or episodes.", 422, kind="artist")
        return WithdrawOut.model_validate(await music_writing.withdraw_artist(ref, payload.delete_files, _caller(key)))
    found = await writing.withdraw(
        kind, ref, payload.versions, _scope(payload.series), payload.delete_files, _caller(key)
    )
    return WithdrawOut.model_validate(found)


class MonitoringIn(BaseModel):
    monitored: bool = Field(
        description="False freezes: nothing is searched or upgraded, files stay. True thaws. An artist frozen is "
        "Lidarr's unmonitored artist: none of its albums is searched on its own, their switches stay."
    )
    versions: VersionIds = None
    series: SeriesScopeIn | None = None
    artist: ArtistMonitoringIn | None = None


class MonitoredVersionOut(BaseModel):
    version_id: str | None
    changed: bool


class MonitoringOut(BaseModel):
    versions: list[MonitoredVersionOut]
    title: TitleOut


@router.put(
    "/titles/{kind}/{ref}/monitoring",
    response_model=MonitoringOut,
    summary="Freeze or thaw",
    description=(
        "Only the switches: no download stops, no file moves. Freezing a whole series also keeps seasons TMDB names "
        "later out; thawing it takes them in again."
    ),
    responses=v1_error_responses(*_TITLE_ERRORS, *_SCOPE_ERRORS),
)
def set_monitoring(kind: str, ref: str, payload: MonitoringIn, key: RequestKey) -> MonitoringOut:
    if payload.artist is not None and kind != "artist":
        raise error("scope_not_for_kind", f"A {kind} has no rule for new albums.", 422, kind=kind)
    if kind == "artist":
        if payload.versions or payload.series is not None:
            raise error("scope_not_for_kind", "An artist has no versions, seasons or episodes.", 422, kind="artist")
        new_albums = payload.artist.new_albums if payload.artist is not None else None
        return MonitoringOut.model_validate(
            music_writing.set_monitoring(ref, payload.monitored, new_albums, _caller(key))
        )
    found = writing.set_monitoring(
        kind, ref, payload.monitored, payload.versions, _scope(payload.series), _caller(key)
    )
    return MonitoringOut.model_validate(found)


class DeleteFilesIn(BaseModel):
    versions: VersionIds = None
    series: SeriesScopeIn | None = None
    album: AlbumScopeIn | None = Field(default=None, description="Only for an album: single tracks.")


class DeletedVersionOut(BaseModel):
    version_id: str | None
    files_recycled: int
    still_monitored: bool = Field(
        description="As in Radarr and Sonarr, watching stays: a watched version is searched again. Take it back "
        "to stop that."
    )


class DeleteFilesOut(BaseModel):
    versions: list[DeletedVersionOut]


@router.post(
    "/titles/{kind}/{ref}/delete-files",
    response_model=DeleteFilesOut,
    summary="Move files into the recycle bin",
    description=(
        "The files of the scope go into the recycle bin with their subtitles; they come back with "
        "POST /api/v1/recycle-bin/{entry_id}/restore until the bin's time is up. A file of a series that holds several "
        "episodes goes whole when one of them is named."
    ),
    responses=v1_error_responses(*_TITLE_ERRORS, *_SCOPE_ERRORS, (409, "download_importing")),
)
def delete_files(kind: str, ref: str, payload: DeleteFilesIn, key: RequestKey) -> DeleteFilesOut:
    tracks = payload.album.tracks if payload.album is not None else None
    found = writing.delete_files(kind, ref, payload.versions, _scope(payload.series), _caller(key), tracks)
    return DeleteFilesOut.model_validate(found)


def _running_of(kind: str, ref: str) -> tuple[int, list[tuple[int, str, int | None]]]:
    with SessionLocal() as db:
        title = writing.title_of(db, kind, ref)
        own = set(
            db.scalars(
                select(Version.version_definition_id).where(
                    Version.title_id == title.id, Version.source_id.is_(None)
                )
            )
        )
        running = [
            (download.id, download.state, download.version_definition_id)
            for download in download_store.pending_downloads(db, title.id)
            if download.version_definition_id in own
        ]
        return title.id, running


@router.delete(
    "/titles/{kind}/{ref}",
    status_code=204,
    response_model=None,
    summary="Remove a title from the library",
    description=(
        "As the button in nexcrate: nexcrate's own versions go, and the title when none is left. Its running "
        "downloads are stopped at their client first. With `delete_files` the files go into the recycle bin before; "
        "⚠️ a title that is gone cannot take them back, so add it again first. Versions a Radarr or Sonarr connection "
        "feeds stay, and the answer is 409 `title_has_source_versions`."
    ),
    responses=v1_error_responses(*_TITLE_ERRORS, (409, "download_finished"), (409, "title_has_source_versions")),
)
async def remove_title(
    kind: str,
    ref: str,
    key: RequestKey,
    delete_files: Annotated[bool, Query(description="Move the files into the recycle bin first.")] = False,
) -> None:
    if kind == "artist":
        await music_writing.remove_artist(ref, delete_files, _caller(key))
        return
    title_id, running = await asyncio.to_thread(_running_of, kind, ref)
    await writing.cancel_downloads(running)
    if delete_files:
        await asyncio.to_thread(recycle_bin.delete, title_id, recycle_bin.Scope(), _caller(key).actor)
    if kind == "album":
        # An album stays in its artist's catalogue; its version goes.
        await asyncio.to_thread(music_writing.remove_album_version, title_id)
        return
    await asyncio.to_thread(library_router._remove_title, title_id)


class SearchOut(BaseModel):
    search: str = Field(
        description="queued, running (a search of it runs already), nothing_wanted, or not_possible (an anime series "
        "while `capabilities.anime` is false)."
    )


@router.post(
    "/titles/{kind}/{ref}/search",
    status_code=202,
    response_model=SearchOut,
    summary="Ask for a search",
    description=(
        "The title goes into the order of the automatic, right after the replacements, even with its switch off, and "
        "within the indexers' budget: a hundred asks at once are searched one after the other."
    ),
    responses=v1_error_responses(*_TITLE_ERRORS),
)
def ask_search(kind: str, ref: str, key: RequestKey) -> SearchOut:
    if kind == "artist":
        return SearchOut(search=music_writing.wish_search(ref))
    return SearchOut(search=writing.wish_search(kind, ref))


# --- The recycle bin ---------------------------------------------------------------------------------------------- #


class RecycleEntryOut(BaseModel):
    entry_id: int
    kind: str
    ref: str | None = Field(
        description="`tmdb:<number>` of the title, also when it left the library; `mbid:<release group>` of an album "
        "while it is there."
    )
    name: str | None
    year: int | None
    version_id: str | None
    season: int | None = Field(description="Of an episode file; null for a movie.")
    episodes: list[int] = Field(description="The episodes the file held, TMDB's numbers.")
    track: str | None = Field(default=None, description="An album's file: `mbid:<track>` it held, when known.")
    file_name: str = Field(description="The file's name, never its folder.")
    size_bytes: int
    deleted_at: datetime
    deleted_by: str = Field(description="owner, or key: a program; `deleted_by_name` is then its key's name.")
    deleted_by_name: str | None
    present: bool = Field(description="False when the file is gone or its disk cannot be seen right now.")
    in_library: bool = Field(description="False when the title or its version left the library: it cannot come back.")


class RecycleBinOut(BaseModel):
    items: list[RecycleEntryOut] = Field(description="Newest first.")


def _entry_out(item: dict[str, Any], present_versions: set[tuple[int, int]], albums: dict[int, str],
               tracks: dict[int, str]) -> RecycleEntryOut:  # fmt: skip
    if item["kind"] == "album":
        mbid = albums.get(item["title_id"] or 0)
        ref = f"mbid:{mbid}" if mbid else None
    else:
        ref = f"tmdb:{item['tmdb_id']}" if item["tmdb_id"] else None
    track = tracks.get(item["track_id"] or 0) if item.get("track_id") else None
    return RecycleEntryOut(
        entry_id=item["id"],
        kind=item["kind"],
        ref=ref,
        track=f"mbid:{track}" if track else None,
        name=item["title"] or None,
        year=item["year"],
        version_id=item["version_public_id"],
        season=item["season"],
        episodes=item["episodes"],
        file_name=item["file_name"],
        size_bytes=item["size"],
        deleted_at=item["deleted_at"],
        deleted_by=item["deleted_by"],
        deleted_by_name=item["deleted_by_name"],
        present=item["present"],
        in_library=(item["title_id"], item["version_definition_id"]) in present_versions,
    )


@router.get(
    "/recycle-bin",
    response_model=RecycleBinOut,
    summary="Read the recycle bin",
    description="What was deleted, by whom, and whether it can come back: movies, series and albums, one entry per "
    "file.",
    responses=v1_error_responses((422, "kind_unsupported")),
)
def read_bin(db: DbSession, kind: Annotated[str | None, Query(max_length=16)] = None) -> RecycleBinOut:
    if kind is not None and kind not in ("movie", "series", "album"):
        raise error("kind_unsupported", f"This nexcrate does not answer for the kind {kind}.", 422, kind=kind)
    v1_versions.ensure_public_ids(db)
    listed = [item for item in recycle_bin.listed(db, kind) if item["kind"] in ("movie", "series", "album")]
    title_ids = [item["title_id"] for item in listed if item["title_id"] is not None]
    albums = dict(
        db.execute(select(Title.id, Title.mbid).where(Title.id.in_(title_ids), Title.kind == "album")).tuples().all()
    )
    track_ids = {item["track_id"] for item in listed if item.get("track_id")}
    tracks = (
        dict(
            db.execute(select(ReleaseTrack.id, ReleaseTrack.mbid).where(ReleaseTrack.id.in_(track_ids))).tuples().all()
        )
        if track_ids
        else {}
    )
    present = set(
        db.execute(
            select(Version.title_id, Version.version_definition_id).where(Version.title_id.in_(title_ids))
        ).tuples()
    )
    return RecycleBinOut(items=[_entry_out(item, present, albums, tracks) for item in listed])


class RestoredOut(BaseModel):
    title: TitleOut


@router.post(
    "/recycle-bin/{entry_id}/restore",
    response_model=RestoredOut,
    summary="Take a file back out of the recycle bin",
    description=(
        "The file goes back where it was and counts again. Refused when something lies there "
        "(`recycle_target_taken`), when the version or episode has another file by now (`recycle_slot_taken`), when "
        "the title or version left the library (`recycle_title_gone`) or when the file is gone (`recycle_file_gone`); "
        "the file then stays in the bin."
    ),
    responses=v1_error_responses(
        (404, "not_found"),
        (409, "recycle_target_taken"),
        (409, "recycle_slot_taken"),
        (409, "recycle_title_gone"),
        (409, "recycle_file_gone"),
        (409, "version_fed_by_source"),
    ),
)
def restore(entry_id: int, key: RequestKey) -> RestoredOut:
    answer = recycle_bin.restore(entry_id, _caller(key).actor)
    with SessionLocal() as db:
        found = titles.detail(db, answer["title_id"])
    return RestoredOut(title=TitleOut.model_validate(found))
