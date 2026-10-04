"""Bazarr's side: nexcrate answering as Radarr below ``/bazarr/radarr`` and as Sonarr below ``/bazarr/sonarr``.

Only what Bazarr 1.6 asks is answered (read from its source, ``radarr/``, ``sonarr/``, ``app/signalr_client.py``,
``app/ui.py``); everything else below ``/bazarr`` is 404 as JSON, never the interface's page. Not part of nexcrate's own
API: the routes stay out of the OpenAPI document, and their shapes follow Radarr's and Sonarr's, not nexcrate's.

**The key** comes the way Bazarr sends it: ``?apikey=``, ``?access_token=`` (SignalR) or ``X-Api-Key``; a header
``Authorization: Bearer`` works too. It needs the scope ``read``. Then the switch: off, every address answers 404
``bazarr_off``. ``POST …/command`` only reads the disk and records subtitle files, so ``read`` is enough for it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from .. import __version__
from .. import db as database
from ..config import get_settings
from ..deps import DbSession
from ..meldungen import error, error_body, meldung
from ..models import ApiKey
from ..services import api_keys, images
from ..services.bazarr import library, live, rescan
from ..services.bazarr import settings as bazarr_settings

logger = logging.getLogger("nexcrate.bazarr")

router = APIRouter(include_in_schema=False)
settings_router = APIRouter(prefix="/api/bazarr", tags=["bazarr"])

PREFIX = "/bazarr"


# --- The key and the switch ----------------------------------------------------------------------------------- #


def _offered_key(query: dict[str, str], headers: dict[str, str]) -> str | None:
    for name in ("apikey", "access_token"):
        if query.get(name):
            return query[name]
    if headers.get("x-api-key"):
        return headers["x-api-key"]
    bearer = headers.get("authorization", "")
    if bearer[:7].casefold() == "bearer ":
        return bearer[7:].strip()
    return None


def check_key(db: Any, offered: str | None) -> ApiKey:
    """The key, or 401 ``api_key_missing``/``api_key_invalid``, 403 ``scope_missing``, 404 ``bazarr_off``."""
    if not offered:
        raise error("api_key_missing", "This address needs a key: ?apikey= or the header X-Api-Key.", 401)
    row = api_keys.find(db, offered)
    if row is None:
        raise error("api_key_invalid", "This key is unknown or was revoked.", 401)
    if "read" not in (row.scopes or []):
        raise error("scope_missing", "This key lacks the scope read.", 403, scope="read")
    if not bazarr_settings.load_enabled(db):
        raise error("bazarr_off", "The connection for Bazarr is off in nexcrate (Settings, System, Bazarr).", 404)
    api_keys.mark_used(row.id)
    return row


def _part(part: str) -> str:
    if part not in live.PARTS:
        raise error("not_found", "This does not exist, or not any more.", 404)
    return part


def bazarr_key(request: Request, db: DbSession, part: str) -> ApiKey:
    _part(part)
    row = check_key(db, _offered_key(dict(request.query_params), {k.lower(): v for k, v in request.headers.items()}))
    live.hub.seen(part)
    return row


Key = Annotated[ApiKey, Depends(bazarr_key)]


def radarr_only(part: str) -> None:
    if part != "radarr":
        raise error("not_found", "This does not exist, or not any more.", 404)


def sonarr_only(part: str) -> None:
    if part != "sonarr":
        raise error("not_found", "This does not exist, or not any more.", 404)


Movies = Depends(radarr_only)
Series = Depends(sonarr_only)


# --- Both parts ------------------------------------------------------------------------------------------------- #


@router.get(PREFIX + "/{part}/api/v3/system/status")
def system_status(part: str, _key: Key) -> dict[str, Any]:
    return {
        "appName": "nexcrate",
        "instanceName": "nexcrate",
        "version": library.COMPAT_VERSION,
        "nexcrateVersion": __version__,
        "urlBase": get_settings().url_base + PREFIX + "/" + part,
        "isDocker": True,
    }


@router.get(PREFIX + "/{part}/api/system/status")
def legacy_status(part: str) -> PlainTextResponse:
    """Bazarr asks the address of Radarr and Sonarr below 3 first and moves on to v3 only when the answer is no JSON.

    ⚠️ Not JSON: Bazarr raises its ``JSONDecodeError`` without arguments when a JSON answer lacks ``version``, which
    fails, takes the version for "unknown", and Sonarr's episodes then stop at a comparison with None (measured against
    Bazarr 1.6.2 on 04.10.2026). Radarr 5 and Sonarr 4 answer here with their page, not with JSON.
    """
    return PlainTextResponse("Not Found", status_code=404)


@router.get(PREFIX + "/{part}/api/v3/tag")
def tags(part: str, _key: Key, db: DbSession) -> list[dict[str, Any]]:
    return library.tags(db, live.KIND_OF_PART[part])


@router.get(PREFIX + "/{part}/api/v3/rootfolder")
def root_folders(part: str, _key: Key, db: DbSession) -> list[dict[str, Any]]:
    return library.root_folders(db, live.KIND_OF_PART[part])


@router.get(PREFIX + "/{part}/api/v3/history")
def history(part: str, _key: Key) -> dict[str, Any]:
    # Bazarr reads it for two providers only (avistaz, cinemaz), for the info address of a usenet release.
    return {"page": 1, "pageSize": 0, "totalRecords": 0, "records": []}


@router.get(PREFIX + "/{part}/api/v3/filesystem")
def filesystem(part: str, _key: Key) -> dict[str, Any]:
    # Bazarr's folder picker for path mappings. nexcrate shows no folders to a program; the paths are typed in.
    return {"parent": None, "directories": [], "files": []}


@router.post(PREFIX + "/{part}/api/v3/command")
async def command(part: str, _key: Key, payload: Annotated[dict[str, Any], Body()]) -> JSONResponse:
    """``RescanMovie`` with ``movieId`` and ``RescanSeries`` with ``seriesId``: Bazarr wrote or deleted a subtitle."""
    name = str(payload.get("name") or "")
    wanted = {"radarr": ("RescanMovie", "movieId", "movie"), "sonarr": ("RescanSeries", "seriesId", "series")}[part]
    number = payload.get(wanted[1])
    if name == wanted[0] and isinstance(number, int) and not isinstance(number, bool):
        await asyncio.to_thread(_rescan_one, number, wanted[2])
    return JSONResponse({"id": 1, "name": name, "status": "completed"}, status_code=201)


def _rescan_one(version_id: int, kind: str) -> None:
    with database.SessionLocal() as db:
        rescan.version(db, version_id, kind)


@router.get(PREFIX + "/{part}/api/v3/MediaCover/{version_id}/{file_name}")
async def media_cover(part: str, version_id: int, file_name: str, _key: Key) -> Response:
    kind = live.KIND_OF_PART[part]
    title_id = await asyncio.to_thread(_title_of, version_id, kind)
    found = await images.poster(title_id) if title_id is not None else None
    if found is None:
        raise error("poster_missing", "There is no image for this title.", 404)
    path, media_type, cache_control = found
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": cache_control})


def _title_of(version_id: int, kind: str) -> int | None:
    from ..models import Title, Version

    with database.SessionLocal() as db:
        version = db.get(Version, version_id)
        if version is None or version.source_id is not None:
            return None
        title = db.get(Title, version.title_id)
        return title.id if title is not None and title.kind == kind else None


# --- Radarr ----------------------------------------------------------------------------------------------------- #


@router.get(PREFIX + "/{part}/api/v3/qualityprofile", dependencies=[Movies])
def quality_profiles(part: str, _key: Key, db: DbSession) -> list[dict[str, Any]]:
    return [{"id": tag["id"], "name": tag["label"]} for tag in library.tags(db, "movie") if tag["id"] < 1_000_000]


@router.get(PREFIX + "/{part}/api/v3/movie", dependencies=[Movies])
def movies(part: str, _key: Key, db: DbSession) -> list[dict[str, Any]]:
    return library.movies(db)


@router.get(PREFIX + "/{part}/api/v3/movie/{version_id}", dependencies=[Movies])
def movie(part: str, version_id: int, _key: Key, db: DbSession) -> dict[str, Any]:
    found = library.movie(db, version_id)
    if found is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    return found


# --- Sonarr ----------------------------------------------------------------------------------------------------- #


@router.get(PREFIX + "/{part}/api/v3/languageprofile", dependencies=[Series])
def language_profiles(part: str, _key: Key) -> list[dict[str, Any]]:
    return []


@router.get(PREFIX + "/{part}/api/v3/series", dependencies=[Series])
@router.get(PREFIX + "/{part}/api/v3/series/", dependencies=[Series])
def all_series(part: str, _key: Key, db: DbSession) -> list[dict[str, Any]]:
    return library.all_series(db)


@router.get(PREFIX + "/{part}/api/v3/series/{version_id}", dependencies=[Series])
def one_series(part: str, version_id: int, _key: Key, db: DbSession) -> dict[str, Any]:
    found = library.one_series(db, version_id)
    if found is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    return found


@router.get(PREFIX + "/{part}/api/v3/episode", dependencies=[Series])
def episodes(
    part: str, _key: Key, db: DbSession, series_id: Annotated[int, Query(alias="seriesId")]
) -> list[dict[str, Any]]:
    return library.episodes(db, series_id)


@router.get(PREFIX + "/{part}/api/v3/episode/{number}", dependencies=[Series])
def episode(part: str, number: int, _key: Key, db: DbSession) -> dict[str, Any]:
    found = library.episode(db, number)
    if found is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    return found


@router.get(PREFIX + "/{part}/api/v3/episodeFile", dependencies=[Series])
@router.get(PREFIX + "/{part}/api/v3/episodefile", dependencies=[Series])
def episode_files(
    part: str, _key: Key, db: DbSession, series_id: Annotated[int, Query(alias="seriesId")]
) -> list[dict[str, Any]]:
    return library.episode_files(db, series_id)


@router.get(PREFIX + "/{part}/api/v3/episodeFile/{number}", dependencies=[Series])
@router.get(PREFIX + "/{part}/api/v3/episodefile/{number}", dependencies=[Series])
def episode_file(part: str, number: int, _key: Key, db: DbSession) -> dict[str, Any]:
    found = library.episode_file(db, number)
    if found is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    return found


# --- SignalR ---------------------------------------------------------------------------------------------------- #


@router.post(PREFIX + "/{part}/signalr/messages/negotiate")
def negotiate(part: str, key: Key) -> dict[str, Any]:
    return live.hub.negotiate(part, key.id)


def _socket_key(websocket: WebSocket) -> int | None:
    offered = _offered_key(dict(websocket.query_params), {k.lower(): v for k, v in websocket.headers.items()})
    try:
        with database.SessionLocal() as db:
            return check_key(db, offered).id
    except HTTPException:
        return None


@router.websocket(PREFIX + "/{part}/signalr/messages")
async def messages(websocket: WebSocket, part: str) -> None:
    if part not in live.PARTS:
        await websocket.close(code=1008)
        return
    key_id = await asyncio.to_thread(_socket_key, websocket)
    connection = live.hub.claim(websocket.query_params.get("id"), part, key_id) if key_id is not None else None
    if connection is None:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), live.HANDSHAKE_SECONDS)
    except (TimeoutError, WebSocketDisconnect):
        await _close(websocket)
        return
    if not live.handshake_ok(raw):
        await websocket.send_text(live.HANDSHAKE_REFUSED)
        await _close(websocket)
        return
    await live.hub.join(connection)
    live.hub.seen(part)
    try:
        await websocket.send_text(live.HANDSHAKE_ANSWER)
        reader = asyncio.create_task(_read(websocket))
        writer = asyncio.create_task(_write(websocket, connection))
        _done, pending = await asyncio.wait({reader, writer}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    except WebSocketDisconnect:
        pass
    finally:
        live.hub.leave(connection)
        await _close(websocket)


async def _read(websocket: WebSocket) -> None:
    """Pings and the close message come in; nothing else Bazarr sends matters."""
    try:
        while True:
            for record in live.records(await websocket.receive_text()):
                if record.get("type") == 7:
                    return
    except (WebSocketDisconnect, RuntimeError):
        return


async def _write(websocket: WebSocket, connection: live.Connection) -> None:
    while True:
        try:
            item = await asyncio.wait_for(connection.outbox.get(), live.PING_SECONDS)
        except TimeoutError:
            item = live.PING
        if item is None:
            return
        await websocket.send_text(item)


async def _close(websocket: WebSocket) -> None:
    # Closed already by the other side, or never accepted: nothing left to close.
    with contextlib.suppress(RuntimeError, OSError):
        await websocket.close()


# --- Everything else below /bazarr ----------------------------------------------------------------------------- #


@router.api_route(PREFIX, methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"])
@router.api_route(PREFIX + "/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"])
def nothing_here(request: Request) -> JSONResponse:
    """Not the interface's page: Bazarr would read it as an answer and stumble over the HTML."""
    detail = meldung("not_found", "This does not exist, or not any more.")
    return JSONResponse(status_code=404, content=error_body(request.url.path, detail))


# --- The settings page ------------------------------------------------------------------------------------------ #


class PartStatus(BaseModel):
    live: bool = Field(description="Whether Bazarr holds a live connection (SignalR) for this part.")
    since: datetime | None = Field(description="Since when; null without one.")
    last_request: datetime | None = Field(description="When Bazarr last asked anything of this part since the start.")


class BazarrOut(BaseModel):
    enabled: bool = Field(description="Whether nexcrate answers Bazarr below /bazarr.")
    url_base: str = Field(description="The sub path nexcrate started with; Bazarr's base URL starts with it.")
    radarr: PartStatus
    sonarr: PartStatus


class BazarrIn(BaseModel):
    enabled: bool


def _out(enabled: bool) -> BazarrOut:
    status = live.hub.status()
    return BazarrOut(
        enabled=enabled,
        url_base=get_settings().url_base,
        radarr=PartStatus(**status["radarr"]),
        sonarr=PartStatus(**status["sonarr"]),
    )


@settings_router.get(
    "",
    response_model=BazarrOut,
    summary="The connection for Bazarr",
    description="Whether nexcrate answers Bazarr as Radarr and Sonarr, and whether Bazarr is connected.",
)
def read_bazarr(db: DbSession) -> BazarrOut:
    return _out(bazarr_settings.load_enabled(db))


@settings_router.put(
    "",
    response_model=BazarrOut,
    summary="Turn the connection for Bazarr on or off",
    description=(
        "On, nexcrate answers Bazarr below /bazarr/radarr and /bazarr/sonarr, and records the subtitle files Bazarr "
        "writes so they move with their video. Switching it on looks once for subtitles already next to the files. "
        "Off closes every live connection."
    ),
)
async def change_bazarr(payload: BazarrIn) -> BazarrOut:
    was = await asyncio.to_thread(_save, payload.enabled)
    if not payload.enabled:
        # On the loop: the connections' queues belong to it.
        live.hub.close_all()
    elif not was:
        threading.Thread(target=_rescan_everything, name="bazarr-rescan", daemon=True).start()
    return _out(payload.enabled)


def _save(enabled: bool) -> bool:
    """Store the switch; returns what it was."""
    with database.SessionLocal() as db:
        was = bazarr_settings.load_enabled(db)
        bazarr_settings.save_enabled(db, enabled)
        db.commit()
    return was


def _rescan_everything() -> None:
    try:
        rescan.everything()
    except Exception:
        logger.exception("The first Bazarr rescan failed")
