"""Media servers: Plex, Jellyfin and Emby. Connect, check, choose the libraries, map paths.

⚠️ Plex's token and the API key of Jellyfin or Emby are stored encrypted and never leave the backend again, not even
partly. Answers carry ``has_token``.

⚠️ plex.tv is contacted by three ``/plex/pin`` routes only, the ones behind the button "Sign in at plex.tv": create
the PIN, ask for its state, list the account's servers. A token typed by hand causes no contact with plex.tv at all.
Choosing a listed server asks that server only, at its own addresses. The token a PIN brought stays in the backend;
saving names the PIN with ``plex_pin_id``.

Saving runs the check: reachable, the right kind, the token accepted, the libraries read. The kind of a saved server
never changes: another program is another server. Nothing is ever changed on the server by a check.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import crypto
from ..db import SessionLocal
from ..deps import DbSession
from ..meldungen import error, error_responses
from ..models import MediaServer, VersionDefinition, utcnow
from ..services import mediaservers
from ..services.mediaservers import base as server_base
from ..services.mediaservers import plextv, suggest
from ..services.radarr import SourceUrlInvalid, normalize_base_url
from ..services.schreibweisen import nfc

logger = logging.getLogger("nexcrate.mediaservers")

router = APIRouter(prefix="/api/media-servers", tags=["media-servers"])

NAME_MAX_LENGTH = 100
TOKEN_MAX_LENGTH = 512
PATH_MAX_LENGTH = 1024
MAPPINGS_MAX = 50
Kind = Literal["plex", "jellyfin", "emby"]


class PathMapping(BaseModel):
    local: str = Field(description="The folder as nexcrate sees it.", examples=["/media"])
    remote: str = Field(description="The same folder as the media server sees it.", examples=["/volume1/media"])


class LibraryOut(BaseModel):
    id: str = Field(description="Plex's section key, or the item id of Jellyfin and Emby.")
    name: str
    kind: str | None = Field(description="movie, series, music, or null for anything else.")
    locations: list[str] = Field(description="The folders of the library as the server names them.")
    refresh: bool = Field(description="Whether nexcrate tells the server about changes in this library.")


class MediaServerOut(BaseModel):
    id: int
    name: str
    kind: str = Field(description="plex, jellyfin or emby.")
    url: str = Field(examples=["http://plex.example.com:32400"])
    has_token: bool = Field(description="Whether a token or API key is stored. It is never returned.")
    enabled: bool
    server_name: str | None = Field(description="What the server calls itself, as read at the last check.")
    version: str | None
    libraries: list[LibraryOut]
    path_mappings: list[PathMapping]
    last_error_code: str | None = Field(
        description=(
            "The code of the last failed check or notification; `mediaserver_path_unmatched` when the last folder lay "
            "in no library and whole libraries were read again instead; null when fine."
        )
    )
    last_checked_at: datetime | None
    last_notify_at: datetime | None = Field(description="When the server was last told about a change.")
    last_notify_result: str | None = Field(description="folder, library or failed.")


class LibrarySwitch(BaseModel):
    id: str = Field(max_length=64)
    refresh: bool


_TOKEN_TEXT = "Plex's token, or the API key of Jellyfin or Emby. Never returned."
_PIN_TEXT = "Plex only: the id of a claimed PIN from `POST /api/media-servers/plex/pin`; its token is taken."


class ServerIn(BaseModel):
    name: str = Field(max_length=400, description=f"1 to {NAME_MAX_LENGTH} characters.")
    kind: Kind
    url: str = Field(max_length=2048, description="http or https with an optional path, without user info or query.")
    token: str | None = Field(default=None, max_length=TOKEN_MAX_LENGTH, description=_TOKEN_TEXT)
    plex_pin_id: int | None = Field(default=None, description=_PIN_TEXT)
    enabled: bool = True
    path_mappings: list[PathMapping] = Field(default_factory=list, max_length=MAPPINGS_MAX)


class ServerPatch(BaseModel):
    name: str | None = Field(default=None, max_length=400)
    kind: Kind | None = Field(
        default=None,
        description="Cannot change: left out or the stored kind. Another kind answers 422 `mediaserver_kind_locked`.",
    )
    url: str | None = Field(default=None, max_length=2048)
    token: str | None = Field(default=None, max_length=TOKEN_MAX_LENGTH, description="Empty keeps the stored one.")
    plex_pin_id: int | None = Field(default=None, description=_PIN_TEXT)
    enabled: bool | None = None
    path_mappings: list[PathMapping] | None = Field(default=None, max_length=MAPPINGS_MAX)
    libraries: list[LibrarySwitch] | None = Field(
        default=None, max_length=200, description="The switches of libraries the server listed; others are ignored."
    )


class ServerTestIn(BaseModel):
    id: int | None = Field(default=None, description="Test a saved server; the fields given along override its values.")
    kind: Kind | None = Field(default=None, description="Needed without `id`.")
    url: str | None = Field(default=None, max_length=2048, description="Needed without `id`.")
    token: str | None = Field(
        default=None, max_length=TOKEN_MAX_LENGTH, description="Empty uses the stored one of `id`."
    )
    plex_pin_id: int | None = Field(default=None, description=_PIN_TEXT)


class SuggestedMapping(PathMapping):
    reason: Literal["same_ending", "only_one"] = Field(
        description="same_ending: a version's folder and a library folder end in the same folder names. only_one: "
        "exactly one version folder and one library folder of the kind."
    )


class ServerTestOut(BaseModel):
    server_name: str
    version: str
    libraries: list[LibraryOut] = Field(description="What the server lists; `refresh` is what saving would set.")
    suggested_mappings: list[SuggestedMapping] = Field(
        default_factory=list,
        description="Path pairs the dialog can offer, worked out from the folders the server lists and the folders of "
        "nexcrate's versions. ⚠️ An offer for the owner to confirm: nothing is stored, and no pair nobody saved is "
        "ever used.",
    )
    mappings_needed: bool = Field(
        default=True,
        description="False: every version folder already lies inside a library folder as it is; no pair is needed.",
    )


class PlexServerOut(BaseModel):
    machine_id: str
    name: str
    addresses: list[str] = Field(description="Where plex.tv says the server answers, the local addresses first.")


class PlexServersOut(BaseModel):
    servers: list[PlexServerOut] = Field(description="The servers the signed-in account owns.")
    shared_hidden: int = Field(description="Servers that were only shared with the account; never offered.")


class PlexChosenOut(ServerTestOut):
    name: str = Field(description="The server's name at plex.tv, for the name field.")
    url: str = Field(description="The first of its addresses that answered; save the server with it.")


class PlexPinOut(BaseModel):
    pin_id: int
    auth_url: str = Field(description="The page at plex.tv the owner opens to confirm.")
    expires_in: int = Field(description="Seconds until the PIN is no longer asked for.")


class PlexPinState(BaseModel):
    state: Literal["waiting", "claimed", "expired"] = Field(
        description="claimed: the token waits in the backend; save the server with `plex_pin_id`."
    )


# --- Checks ---------------------------------------------------------------------------------------- #


def _invalid(field_name: str) -> HTTPException:
    return error(
        "mediaserver_invalid", "The name, address or a path of the media server is not valid.", 422, fields=[field_name]
    )


def _not_found() -> HTTPException:
    return error("mediaserver_not_found", "This media server does not exist, or not any more.", 404)


def _control(text: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in text)


def _clean_name(raw: str) -> str:
    name = nfc(raw).strip()
    if not name or len(name) > NAME_MAX_LENGTH or _control(name):
        raise _invalid("name")
    return name


def _clean_url(raw: str) -> str:
    try:
        return normalize_base_url(raw)
    except SourceUrlInvalid as exc:
        raise _invalid("url") from exc


def _clean_token(raw: str | None) -> str:
    text = (raw or "").strip()
    if _control(text):
        raise error("invalid_input", "The input is not valid.", 422, fields=["token"])
    return text


def _clean_mappings(raw: list[PathMapping]) -> list[dict[str, str]]:
    cleaned: list[dict[str, str]] = []
    for mapping in raw:
        local, remote = nfc(mapping.local).strip(), nfc(mapping.remote).strip()
        if (
            not local
            or not remote
            or max(len(local), len(remote)) > PATH_MAX_LENGTH
            or _control(local)
            or _control(remote)
        ):
            raise _invalid("path_mappings")
        pair = {"local": local, "remote": remote}
        if pair not in cleaned:
            cleaned.append(pair)
    return cleaned


def _pin_token(pin_id: int) -> str:
    token = plextv.token_of(pin_id)
    if not token:
        raise error(
            "plextv_pin_unclaimed",
            "This sign-in at plex.tv is not confirmed, or it has expired. Please start it again.",
            409,
        )
    return token


def _merged(listed: tuple[mediaservers.Listed, ...], stored: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The libraries as the server lists them now, each with the owner's switch. A new library of movies, series or
    music starts switched on, anything else switched off."""
    switches = {str(entry.get("id")): bool(entry.get("refresh")) for entry in stored if isinstance(entry, dict)}
    return [
        {
            "id": library.id,
            "name": library.name,
            "kind": library.kind,
            "locations": list(library.locations),
            "refresh": switches.get(library.id, library.kind is not None),
        }
        for library in listed
    ]


def _version_folders() -> dict[str, list[str]]:
    with SessionLocal() as db:
        rows = db.execute(select(VersionDefinition.kind, VersionDefinition.folder).order_by(VersionDefinition.id))
        found: dict[str, list[str]] = {}
        for kind, folder in rows.tuples():
            if folder:
                found.setdefault(kind, []).append(folder)
        return found


async def _tested(checked: mediaservers.Checked, stored_libraries: list[dict[str, Any]]) -> dict[str, Any]:
    """What a check answers: who it is, the libraries with their switches, and the path pairs to offer."""
    offered = suggest.suggest(await asyncio.to_thread(_version_folders), checked.libraries)
    return {
        "server_name": checked.server_name,
        "version": checked.version,
        "libraries": [LibraryOut.model_validate(entry) for entry in _merged(checked.libraries, stored_libraries)],
        "suggested_mappings": [
            SuggestedMapping(local=pair.local, remote=pair.remote, reason=pair.reason) for pair in offered.pairs
        ],
        "mappings_needed": not offered.nothing_needed,
    }


async def _check(target: mediaservers.Target, record: int | None = None) -> mediaservers.Checked:
    """The check. With ``record`` the result becomes that server's last error code."""
    try:
        checked = await mediaservers.check(target)
    except mediaservers.ServerError as exc:
        logger.info("Media server check failed: %s", exc.code)
        if record is not None:
            await asyncio.to_thread(_record_error, record, exc.code)
        raise exc.http() from exc
    return checked


# --- Storage ------------------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Stored:
    id: int
    kind: str
    url: str
    token: str = field(repr=False)
    libraries: list[dict[str, Any]]


def _load(server_id: int) -> _Stored | None:
    with SessionLocal() as db:
        row = db.get(MediaServer, server_id)
        if row is None:
            return None
        return _Stored(
            id=row.id,
            kind=row.kind,
            url=row.url,
            token=row.token,
            libraries=[dict(entry) for entry in (row.libraries or [])],
        )


def _decrypted(stored: _Stored) -> str:
    return crypto.decrypt(stored.token) if stored.token else ""


def server_out(row: MediaServer) -> MediaServerOut:
    return MediaServerOut(
        id=row.id,
        name=row.name,
        kind=row.kind,
        url=row.url,
        has_token=bool(row.token),
        enabled=row.enabled,
        server_name=row.server_name,
        version=row.version,
        libraries=[LibraryOut.model_validate(_library(entry)) for entry in (row.libraries or [])],
        path_mappings=[PathMapping.model_validate(mapping) for mapping in (row.path_mappings or [])],
        last_error_code=row.last_error_code,
        last_checked_at=row.last_checked_at,
        last_notify_at=row.last_notify_at,
        last_notify_result=row.last_notify_result,
    )


def _library(entry: dict[str, Any]) -> dict[str, Any]:
    locations = entry.get("locations")
    return {
        "id": str(entry.get("id") or ""),
        "name": str(entry.get("name") or ""),
        "kind": entry.get("kind") if entry.get("kind") in ("movie", "series", "music") else None,
        "locations": [str(item) for item in locations] if isinstance(locations, list) else [],
        "refresh": bool(entry.get("refresh")),
    }


def _read(server_id: int) -> MediaServerOut:
    with SessionLocal() as db:
        row = db.get(MediaServer, server_id)
        if row is None:
            raise _not_found()
        return server_out(row)


def _record_error(server_id: int, code: str) -> None:
    with SessionLocal() as db:
        row = db.get(MediaServer, server_id)
        if row is not None:
            row.last_error_code = code
            row.last_checked_at = utcnow()
            db.commit()


def _insert(values: dict[str, Any], token: str) -> int:
    moment = utcnow()
    with SessionLocal() as db:
        row = MediaServer(
            **values,
            token=crypto.encrypt(token) if token else "",
            last_checked_at=moment,
            created_at=moment,
            updated_at=moment,
        )
        db.add(row)
        db.commit()
        logger.info("Media server %d created (%s)", row.id, row.kind)
        return row.id


def _apply(server_id: int, changes: dict[str, Any], token: str | None, checked: bool) -> None:
    moment = utcnow()
    with SessionLocal() as db:
        row = db.get(MediaServer, server_id)
        if row is None:
            raise _not_found()
        for name, value in changes.items():
            setattr(row, name, value)
        if token:
            row.token = crypto.encrypt(token)
        if checked:
            row.last_error_code = None
            row.last_checked_at = moment
        row.updated_at = moment
        db.commit()
        logger.info("Media server %d changed", server_id)


def _checked_values(checked: mediaservers.Checked, stored: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "server_name": checked.server_name[:200] or None,
        "version": checked.version[:64] or None,
        "libraries": _merged(checked.libraries, stored),
    }


# --- Routes ---------------------------------------------------------------------------------------------- #


@router.get(
    "",
    response_model=list[MediaServerOut],
    summary="List the media servers",
    description=(
        "Every media server with its libraries and their switches, its path mappings, and when and how it was last "
        "told about a change. No token."
    ),
)
def list_servers(db: DbSession) -> list[MediaServerOut]:
    return [server_out(row) for row in db.scalars(select(MediaServer).order_by(MediaServer.id))]


@router.post(
    "",
    status_code=201,
    response_model=MediaServerOut,
    summary="Add a media server",
    description=(
        "Checks the server (reachable, the right kind, the token accepted), reads its libraries and stores it with "
        "its token encrypted. Libraries of movies, series and music start switched on. For Plex, `plex_pin_id` takes "
        "the token a confirmed sign-in at plex.tv brought instead of `token`; plex.tv is not contacted by this route."
    ),
    responses=error_responses(
        (422, "mediaserver_invalid"), (409, "plextv_pin_unclaimed"), (422, "invalid_input"), *mediaservers.ERRORS
    ),
)
async def create_server(payload: ServerIn) -> MediaServerOut:
    name = _clean_name(payload.name)
    url = _clean_url(payload.url)
    mappings = _clean_mappings(payload.path_mappings)
    token = _clean_token(payload.token)
    if payload.kind == "plex" and payload.plex_pin_id is not None and not token:
        token = _pin_token(payload.plex_pin_id)
    checked = await _check(mediaservers.Target(kind=payload.kind, url=url, token=token))
    values = {
        "name": name,
        "kind": payload.kind,
        "url": url,
        "enabled": payload.enabled,
        "path_mappings": mappings,
        **_checked_values(checked, []),
    }
    server_id = await asyncio.to_thread(_insert, values, token)
    if payload.plex_pin_id is not None:
        plextv.forget(payload.plex_pin_id)
    return await asyncio.to_thread(_read, server_id)


@router.post(
    "/test",
    response_model=ServerTestOut,
    summary="Check a media server without saving",
    description=(
        "Send `kind` and `url` with the token, or `id` of a saved server, whose stored values the given ones override; "
        "an empty `token` then uses the stored one. Reads who answers and its libraries. Changes nothing, neither on "
        "the server nor in nexcrate."
    ),
    responses=error_responses(
        (404, "mediaserver_not_found"),
        (422, "mediaserver_invalid"),
        (409, "plextv_pin_unclaimed"),
        (422, "invalid_input"),
        *mediaservers.ERRORS,
    ),
)
async def probe_server(payload: ServerTestIn) -> ServerTestOut:
    stored: _Stored | None = None
    if payload.id is not None:
        stored = await asyncio.to_thread(_load, payload.id)
        if stored is None:
            raise _not_found()
    kind = payload.kind or (stored.kind if stored is not None else None)
    raw_url = (payload.url or "").strip()
    if kind is None or (not raw_url and stored is None):
        missing = [name for name, value in (("kind", kind), ("url", raw_url)) if not value]
        raise error("invalid_input", "The input is not valid.", 422, fields=missing)
    url = _clean_url(raw_url) if raw_url else (stored.url if stored is not None else "")
    token = _clean_token(payload.token)
    if not token and kind == "plex" and payload.plex_pin_id is not None:
        token = _pin_token(payload.plex_pin_id)
    if not token and stored is not None:
        token = await asyncio.to_thread(_decrypted, stored)
    checked = await _check(mediaservers.Target(kind=kind, url=url, token=token))
    return ServerTestOut(**await _tested(checked, stored.libraries if stored is not None else []))


@router.post(
    "/{server_id}/check",
    response_model=MediaServerOut,
    summary="Check a saved media server and read its libraries again",
    description=(
        "Checks the stored connection and stores what the server lists now: its name, version and libraries. The "
        "switch of a library that is still there stays; a new library of movies, series or music starts switched on. "
        "A failure becomes the server's `last_error_code`. Nothing is changed on the server."
    ),
    responses=error_responses((404, "mediaserver_not_found"), *mediaservers.ERRORS),
)
async def check_server(server_id: int) -> MediaServerOut:
    stored = await asyncio.to_thread(_load, server_id)
    if stored is None:
        raise _not_found()
    token = await asyncio.to_thread(_decrypted, stored)
    checked = await _check(mediaservers.Target(kind=stored.kind, url=stored.url, token=token), record=stored.id)
    await asyncio.to_thread(_apply, server_id, _checked_values(checked, stored.libraries), None, True)
    return await asyncio.to_thread(_read, server_id)


@router.patch(
    "/{server_id}",
    response_model=MediaServerOut,
    summary="Change a media server",
    description=(
        "Changes the given fields. An empty or missing `token` keeps the stored one. A new address or token is "
        "checked first and the libraries are read again. `libraries` sets the switches, `path_mappings` replaces the "
        "pairs. The kind of a saved server cannot change."
    ),
    responses=error_responses(
        (404, "mediaserver_not_found"),
        (422, "mediaserver_kind_locked"),
        (422, "mediaserver_invalid"),
        (409, "plextv_pin_unclaimed"),
        (422, "invalid_input"),
        *mediaservers.ERRORS,
    ),
)
async def update_server(server_id: int, payload: ServerPatch) -> MediaServerOut:
    stored = await asyncio.to_thread(_load, server_id)
    if stored is None:
        raise _not_found()
    if payload.kind is not None and payload.kind != stored.kind:
        raise error(
            "mediaserver_kind_locked",
            "The kind of a saved media server cannot change. Add a new media server instead.",
            422,
            fields=["kind"],
        )
    changes: dict[str, Any] = {}
    if payload.name is not None:
        changes["name"] = _clean_name(payload.name)
    if payload.enabled is not None:
        changes["enabled"] = payload.enabled
    if payload.path_mappings is not None:
        changes["path_mappings"] = _clean_mappings(payload.path_mappings)
    url = _clean_url(payload.url) if payload.url is not None else stored.url
    new_token = _clean_token(payload.token)
    if not new_token and stored.kind == "plex" and payload.plex_pin_id is not None:
        new_token = _pin_token(payload.plex_pin_id)
    libraries = stored.libraries
    connection_changed = url != stored.url or bool(new_token)
    if connection_changed:
        token = new_token or await asyncio.to_thread(_decrypted, stored)
        checked = await _check(mediaservers.Target(kind=stored.kind, url=url, token=token))
        changes.update({"url": url, **_checked_values(checked, stored.libraries)})
        libraries = changes["libraries"]
    if payload.libraries is not None:
        wanted = {switch.id: switch.refresh for switch in payload.libraries}
        changes["libraries"] = [
            {**entry, "refresh": wanted.get(str(entry.get("id")), bool(entry.get("refresh")))} for entry in libraries
        ]
    await asyncio.to_thread(_apply, server_id, changes, new_token or None, connection_changed)
    if payload.plex_pin_id is not None:
        plextv.forget(payload.plex_pin_id)
    return await asyncio.to_thread(_read, server_id)


@router.delete(
    "/{server_id}",
    status_code=204,
    response_model=None,
    summary="Delete a media server",
    description="Removes the server and its stored token. The server itself is not contacted.",
    responses=error_responses((404, "mediaserver_not_found")),
)
def delete_server(server_id: int, db: DbSession) -> None:
    row = db.get(MediaServer, server_id)
    if row is None:
        raise _not_found()
    db.delete(row)
    db.commit()
    logger.info("Media server %d deleted", server_id)


@router.post(
    "/plex/pin",
    status_code=201,
    response_model=PlexPinOut,
    summary="Start signing in at plex.tv",
    description=(
        "Creates a PIN at plex.tv and answers the page the owner opens there to confirm. ⚠️ This route, the one that "
        "asks for the PIN's state and the one that lists the account's servers are the only ones that contact "
        "plex.tv, and only when called: a token typed by hand causes no contact with plex.tv at all."
    ),
    responses=error_responses(*plextv.ERRORS),
)
async def create_plex_pin() -> PlexPinOut:
    try:
        pin = await plextv.create_pin()
    except mediaservers.ServerError as exc:
        raise exc.http() from exc
    return PlexPinOut(pin_id=pin.id, auth_url=pin.auth_url, expires_in=pin.expires_in)


@router.get(
    "/plex/pin/{pin_id}",
    response_model=PlexPinState,
    summary="Ask whether the sign-in at plex.tv is confirmed",
    description=(
        "Asks plex.tv about a PIN this installation created and still waits for; any other id answers `expired` "
        "without a call. Once confirmed, the token stays in the backend and is never part of an answer: save the "
        "server with `plex_pin_id`."
    ),
    responses=error_responses(*plextv.ERRORS),
)
async def read_plex_pin(pin_id: int) -> PlexPinState:
    try:
        return PlexPinState(state=await plextv.check_pin(pin_id))
    except mediaservers.ServerError as exc:
        raise exc.http() from exc


@router.get(
    "/plex/pin/{pin_id}/servers",
    response_model=PlexServersOut,
    summary="List the Plex servers of the signed-in account",
    description=(
        "After a confirmed sign-in: asks plex.tv for the servers of the account and answers the ones it owns, with the "
        "addresses plex.tv knows for each, the local ones first. Servers that were only shared with the account are "
        "counted, never offered. ⚠️ Contacts plex.tv. No token is part of the answer."
    ),
    responses=error_responses((409, "plextv_pin_unclaimed"), *plextv.ERRORS),
)
async def list_plex_servers(pin_id: int) -> PlexServersOut:
    try:
        listed = await plextv.list_servers(pin_id)
    except mediaservers.ServerError as exc:
        raise exc.http() from exc
    if listed is None:
        _pin_token(pin_id)
        raise error("plextv_pin_unclaimed", "This sign-in at plex.tv is not confirmed, or it has expired.", 409)
    owned, shared = listed
    return PlexServersOut(
        servers=[
            PlexServerOut(machine_id=server.machine_id, name=server.name, addresses=list(server.addresses))
            for server in owned
        ],
        shared_hidden=shared,
    )


@router.post(
    "/plex/pin/{pin_id}/servers/{machine_id}",
    response_model=PlexChosenOut,
    summary="Choose one of the listed Plex servers",
    description=(
        "Tries the addresses plex.tv listed for the server, the local ones first, and answers the first one at which "
        "the server itself answers with its libraries, as `POST /api/media-servers/test` would. Saving with "
        "`plex_pin_id` then takes this server's token. plex.tv is not contacted by this route; the server is asked at "
        "its own addresses only. Nothing is stored."
    ),
    responses=error_responses((404, "plextv_server_unknown"), (409, "plextv_pin_unclaimed"), *mediaservers.ERRORS),
)
async def choose_plex_server(pin_id: int, machine_id: str) -> PlexChosenOut:
    _pin_token(pin_id)
    server = plextv.server_of(pin_id, machine_id)
    if server is None:
        raise error(
            "plextv_server_unknown", "This server is not among the servers plex.tv listed for the sign-in.", 404
        )
    failure: mediaservers.ServerError | None = None
    for address in server.addresses:
        try:
            url = normalize_base_url(address)
            checked = await mediaservers.check(mediaservers.Target(kind="plex", url=url, token=server.token))
        except SourceUrlInvalid:
            continue
        except mediaservers.ServerError as exc:
            # The first failure that is not "nobody answers" says most: a server that refuses the token, for one.
            if failure is None or failure.code == "mediaserver_unreachable":
                failure = exc
            continue
        plextv.choose(pin_id, machine_id)
        logger.info("A Plex server of the sign-in answered at one of its %d addresses", len(server.addresses))
        return PlexChosenOut(name=server.name, url=url, **await _tested(checked, []))
    if failure is not None:
        raise failure.http() from failure
    raise server_base.unreachable(server.name).http()
