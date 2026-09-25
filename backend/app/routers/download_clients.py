"""Download clients: SABnzbd and qBittorrent. Connect, test, fetch from Radarr (step 3).

⚠️ SABnzbd's API key and qBittorrent's password are stored encrypted and never leave the backend again, not even
partly. Answers carry ``has_secret``; qBittorrent's user name is returned.

Saving runs the test: reachable, the right kind, credentials accepted, the category there (created when missing and
read back) and usable. The category is nexcrate's own: taking over Radarr's would hand every download to that Radarr.
A client with downloads that are not finished cannot be deleted. The kind of a saved client never changes: another
program is another client.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from .. import crypto
from ..db import SessionLocal
from ..deps import DbSession
from ..meldungen import error, error_responses
from ..models import Download, DownloadClient, Source, utcnow
from ..models.downloads import PROTOCOL_OF_KIND
from ..services import downloaders, tags
from ..services.downloads import retention, store
from ..services.lidarr import ERRORS as LIDARR_ERRORS
from ..services.lidarr import LidarrClient
from ..services.radarr import RadarrClient, RadarrError, SourceUrlInvalid, normalize_base_url
from ..services.schreibweisen import nfc
from ..services.sonarr import SonarrClient
from .sources import RADARR_ERRORS

logger = logging.getLogger("nexcrate.downloads")

router = APIRouter(prefix="/api/download-clients", tags=["download-clients"])

NAME_MAX_LENGTH = 100
SECRET_MAX_LENGTH = 256
USERNAME_MAX_LENGTH = 256
PRIORITY_MIN, PRIORITY_MAX, PRIORITY_DEFAULT = 1, 50, 1
#: SABnzbd stores category names in lower case, so nexcrate takes only those.
CATEGORY_PATTERN = re.compile(r"[a-z0-9._-]{1,64}")
RADARR_KINDS = {kind: kind for kind in ("sabnzbd", "qbittorrent", "nzbget", "transmission", "deluge")}
Kind = Literal["sabnzbd", "qbittorrent", "nzbget", "transmission", "deluge"]


class PathMapping(BaseModel):
    remote: str = Field(description="The path as the client names it.", examples=["/downloads/complete"])
    local: str = Field(description="The same place as nexcrate sees it.", examples=["/media/downloads/complete"])


class FromSource(BaseModel):
    source_id: int
    name: str


class DownloadClientOut(BaseModel):
    id: int
    name: str
    kind: str = Field(description="sabnzbd, nzbget, qbittorrent, transmission or deluge.")
    protocol: str = Field(description="usenet or torrent.")
    url: str = Field(examples=["http://sabnzbd.example.com:8080"])
    username: str | None = Field(description="The user name of qBittorrent, NZBGet or Transmission; null otherwise.")
    has_secret: bool = Field(description="Whether an API key or password is stored. It is never returned.")
    category: str = Field(description="nexcrate's own category in the client.", examples=["nexcrate"])
    priority: int = Field(
        description=f"{PRIORITY_MIN} to {PRIORITY_MAX}: the lowest number of a protocol is asked first."
    )
    enabled: bool
    path_mappings: list[PathMapping] = Field(description="Confirmed pairs of the client's paths and nexcrate's.")
    last_error_code: str | None = Field(description="The code of the last failed call, null after a success.")
    from_source: FromSource | None = Field(description="The Radarr connection it was fetched from, if any.")
    active_downloads: int = Field(description="Downloads of this client that are not finished.")
    tags: list[str] = Field(
        default_factory=list,
        description="With tags it loads only titles sharing one, and those only through clients with a fitting tag "
        ".",
    )
    retention: Literal["days", "unlimited", "unknown"] | None = Field(
        default=None,
        description="Usenet clients: how long their news servers keep articles, as read from the client: "
        "`days` (see retention_days), `unlimited`, or `unknown` when it could not be read. Null for torrent clients "
        "and before the first reading. Releases older than the longest retention of all Usenet clients do not fit.",
    )
    retention_days: int | None = Field(default=None, description="The days when retention is `days`.")


class FromRadarrIn(BaseModel):
    source_id: int
    radarr_id: int


_SECRET_TEXT = "SABnzbd's API key (not the NZB key) or qBittorrent's password. Never returned."
_CATEGORY_TEXT = "1 to 64 lower-case letters, digits, dots, underscores or hyphens; left out: nexcrate."
_PRIORITY_TEXT = f"{PRIORITY_MIN} to {PRIORITY_MAX}; left out: {PRIORITY_DEFAULT} for a new client, else unchanged."


class ClientIn(BaseModel):
    tags: list[str] | None = Field(default=None, max_length=tags.PER_ITEM_MAX, description="By name.")
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(max_length=400, description=f"1 to {NAME_MAX_LENGTH} characters.")
    kind: Kind
    url: str = Field(max_length=2048, description="http or https with an optional path, without user info or query.")
    username: str | None = Field(default=None, max_length=USERNAME_MAX_LENGTH, description="qBittorrent only.")
    secret: str | None = Field(default=None, max_length=SECRET_MAX_LENGTH, description=_SECRET_TEXT)
    category: str | None = Field(default=None, max_length=200, description=_CATEGORY_TEXT)
    priority: int | None = Field(default=None, strict=True, description=_PRIORITY_TEXT)
    enabled: bool = True
    from_: FromRadarrIn | None = Field(
        default=None, alias="from", description="The Radarr source and its client this one was fetched from."
    )


class ClientPatch(BaseModel):
    tags: list[str] | None = Field(default=None, max_length=tags.PER_ITEM_MAX, description="Every tag, by name.")
    model_config = ConfigDict(populate_by_name=True)

    name: str | None = Field(default=None, max_length=400)
    kind: Kind | None = Field(
        default=None,
        description="Cannot change: left out or the stored kind. Another kind answers 422 `client_kind_locked`.",
    )
    url: str | None = Field(default=None, max_length=2048)
    username: str | None = Field(default=None, max_length=USERNAME_MAX_LENGTH, description="Empty removes it.")
    secret: str | None = Field(default=None, max_length=SECRET_MAX_LENGTH, description="Empty keeps the stored one.")
    category: str | None = Field(default=None, max_length=200)
    priority: int | None = Field(default=None, strict=True, description=_PRIORITY_TEXT)
    enabled: bool | None = None
    path_mappings: list[PathMapping] | None = Field(
        default=None, max_length=100, description="May only shrink: a mapping is added by confirming a proposal."
    )
    from_: FromRadarrIn | None = Field(default=None, alias="from")


class ClientTestIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int | None = Field(default=None, description="Test a saved client; the fields given along override its values.")
    name: str | None = Field(default=None, max_length=400, description="Not needed for the test.")
    kind: Kind | None = Field(default=None, description="Needed without `id`.")
    url: str | None = Field(default=None, max_length=2048, description="Needed without `id`.")
    username: str | None = Field(default=None, max_length=USERNAME_MAX_LENGTH)
    secret: str | None = Field(
        default=None, max_length=SECRET_MAX_LENGTH, description="Empty uses the stored one of `id`."
    )
    category: str | None = Field(default=None, max_length=200)
    priority: int | None = Field(default=None, strict=True)
    enabled: bool | None = None
    from_: FromRadarrIn | None = Field(default=None, alias="from")


class ClientTestOut(BaseModel):
    version: str = Field(examples=["4.5.1"])
    category_exists: bool = Field(description="The test creates nothing: whether the category is there already.")


class RadarrClientOut(BaseModel):
    radarr_id: int
    name: str
    kind: str = Field(description="sabnzbd, nzbget, qbittorrent, transmission or deluge.")
    url: str
    username: str | None = Field(description="qBittorrent's user name as Radarr has it; null for SABnzbd.")
    radarr_category: str | None = Field(description="Radarr's category, as information. nexcrate keeps its own.")
    enabled: bool
    priority: int
    already_added: bool = Field(description="A client with this address and kind, or fetched from this entry, exists.")
    tags: list[str] = Field(default_factory=list, description="Its tags in the app, by name: send them along.")


# --- Checks ---------------------------------------------------------------------------------------- #


def _invalid(field_name: str) -> HTTPException:
    return error(
        "client_invalid",
        "The name, address or category of the download client is not valid.",
        422,
        fields=[field_name],
    )


def _not_found() -> HTTPException:
    return error("client_not_found", "This download client does not exist, or not any more.", 404)


def _clean_name(raw: str) -> str:
    name = nfc(raw).strip()
    if not name or len(name) > NAME_MAX_LENGTH:
        raise _invalid("name")
    return name


def _clean_url(raw: str) -> str:
    try:
        return normalize_base_url(raw)
    except SourceUrlInvalid as exc:
        raise _invalid("url") from exc


def _clean_category(raw: str | None, kind: str) -> str:
    text = (raw or "").strip() or downloaders.DEFAULT_CATEGORY
    if not CATEGORY_PATTERN.fullmatch(text):
        raise _invalid("category")
    return text


def _control(text: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in text)


def _clean_secret(raw: str | None, kind: str) -> str:
    text = raw or ""
    if kind == "sabnzbd":
        text = text.strip()
    if _control(text):
        raise error("invalid_input", "The input is not valid.", 422, fields=["secret"])
    return text


def _clean_username(raw: str | None, kind: str) -> str | None:
    if kind not in downloaders.WITH_USERNAME:
        return None
    text = nfc(raw or "").strip()
    if _control(text):
        raise error("invalid_input", "The input is not valid.", 422, fields=["username"])
    return text or None


def _clean_priority(value: int | None) -> int:
    if value is None or not PRIORITY_MIN <= value <= PRIORITY_MAX:
        raise error("invalid_input", "The input is not valid.", 422, fields=["priority"])
    return value


async def _test(target: downloaders.Target, *, create: bool, record: int | None = None) -> downloaders.Checked:
    """The test. With ``record`` the result becomes that client's last error code: testing a qBittorrent that refused
    its password lifts the stop on logging in, or keeps it."""
    try:
        checked = await downloaders.check(target, create_category=create)
    except downloaders.ClientError as exc:
        logger.info("Download client test failed: %s", exc.code)
        if record is not None:
            await asyncio.to_thread(store.record_client_error, record, exc.code)
        raise exc.http() from exc
    if record is not None:
        await asyncio.to_thread(store.record_client_error, record, None)
    return checked


# --- Storage ------------------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Stored:
    id: int
    kind: str
    url: str
    username: str | None
    secret: str
    category: str
    path_mappings: list[dict[str, str]]


def _active_count(db: Any, client_id: int) -> int:
    return int(
        db.scalar(
            select(func.count(Download.id)).where(
                Download.client_id == client_id, Download.state.in_(store.UNFINISHED_STATES)
            )
        )
        or 0
    )


def _load(client_id: int) -> _Stored | None:
    with SessionLocal() as db:
        row = db.get(DownloadClient, client_id)
        if row is None:
            return None
        return _Stored(
            id=row.id,
            kind=row.kind,
            url=row.url,
            username=row.username,
            secret=row.secret,
            category=row.category,
            path_mappings=[dict(mapping) for mapping in (row.path_mappings or [])],
        )


def client_out(db: Any, row: DownloadClient) -> DownloadClientOut:
    from_source = None
    if row.source_id is not None:
        name = db.scalar(select(Source.name).where(Source.id == row.source_id))
        if name is not None:
            from_source = FromSource(source_id=row.source_id, name=name)
    return DownloadClientOut(
        id=row.id,
        name=row.name,
        kind=row.kind,
        protocol=PROTOCOL_OF_KIND.get(row.kind, "usenet"),
        url=row.url,
        username=row.username if row.kind in downloaders.WITH_USERNAME else None,
        has_secret=bool(row.secret),
        category=row.category,
        priority=row.priority,
        enabled=row.enabled,
        path_mappings=[PathMapping.model_validate(mapping) for mapping in (row.path_mappings or [])],
        last_error_code=row.last_error_code,
        from_source=from_source,
        active_downloads=_active_count(db, row.id),
        tags=tags.of_clients(db, [row.id]).get(row.id, []),
        **_retention_of(db, row),
    )


def _retention_of(db: Any, row: DownloadClient) -> dict[str, Any]:
    if not downloaders.is_usenet(row.kind):
        return {}
    known = retention.read(db).clients
    if row.id not in known:
        return {}
    value = known[row.id]
    if isinstance(value, int) and not isinstance(value, bool):
        return {"retention": "days", "retention_days": value}
    return {"retention": "unknown" if value == retention.UNKNOWN else "unlimited"}


def _read(client_id: int) -> DownloadClientOut:
    with SessionLocal() as db:
        row = db.get(DownloadClient, client_id)
        if row is None:
            raise _not_found()
        return client_out(db, row)


def _source_exists(source_id: int) -> bool:
    with SessionLocal() as db:
        return db.get(Source, source_id) is not None


def _insert(values: dict[str, Any], secret: str) -> int:
    moment = utcnow()
    with SessionLocal() as db:
        row = DownloadClient(
            **values, secret=crypto.encrypt(secret) if secret else "", created_at=moment, updated_at=moment
        )
        db.add(row)
        db.commit()
        logger.info("Download client %d created (%s)", row.id, row.kind)
        return row.id


def _decrypted(stored: _Stored) -> str:
    return crypto.decrypt(stored.secret) if stored.secret else ""


# --- Routes ---------------------------------------------------------------------------------------------- #


@router.get(
    "",
    response_model=list[DownloadClientOut],
    summary="List the download clients",
    description="Every download client with its category, mappings, state and unfinished downloads. No secret.",
)
def list_clients(db: DbSession) -> list[DownloadClientOut]:
    return [client_out(db, row) for row in db.scalars(select(DownloadClient).order_by(DownloadClient.id))]


@router.post(
    "",
    status_code=201,
    response_model=DownloadClientOut,
    summary="Add a download client",
    description=(
        "Tests the client (reachable, the right kind, credentials, the category created when missing, read back and "
        "usable) and stores it with its secret encrypted. `from` records the Radarr entry it came from, as "
        "information; nexcrate's own category is used all the same."
    ),
    responses=error_responses((422, "client_invalid"), (404, "not_found"), *downloaders.ERRORS),
)
async def create_client(payload: ClientIn) -> DownloadClientOut:
    name = _clean_name(payload.name)
    url = _clean_url(payload.url)
    category = _clean_category(payload.category, payload.kind)
    secret = _clean_secret(payload.secret, payload.kind)
    username = _clean_username(payload.username, payload.kind)
    priority = _clean_priority(payload.priority) if "priority" in payload.model_fields_set else PRIORITY_DEFAULT
    if payload.from_ is not None and not await asyncio.to_thread(_source_exists, payload.from_.source_id):
        raise error("not_found", "This does not exist, or not any more.", 404)
    target = downloaders.Target(kind=payload.kind, url=url, username=username or "", secret=secret, category=category)
    await _test(target, create=True)
    values = {
        "name": name,
        "kind": payload.kind,
        "url": url,
        "username": username,
        "category": category,
        "priority": priority,
        "enabled": payload.enabled,
        "path_mappings": [],
        "source_id": payload.from_.source_id if payload.from_ is not None else None,
        "radarr_client_id": payload.from_.radarr_id if payload.from_ is not None else None,
    }
    client_id = await asyncio.to_thread(_insert, values, secret)
    if payload.tags:
        await asyncio.to_thread(_tag, client_id, payload.tags)
    return await asyncio.to_thread(_read, client_id)


def _tag(client_id: int, labels: list[str]) -> None:
    with SessionLocal() as db:
        tags.set_client(db, client_id, labels)
        db.commit()


@router.post(
    "/test",
    response_model=ClientTestOut,
    summary="Test a download client",
    description=(
        "Send `kind` and `url` with the credentials, or `id` of a saved client, whose stored values the given ones "
        "override; an empty `secret` then uses the stored one. Checks reachability, the kind, the credentials and "
        "whether the category exists, and when it does, whether it is usable. Creates nothing."
    ),
    responses=error_responses((404, "client_not_found"), (422, "client_invalid"), *downloaders.ERRORS),
)
async def check_client(payload: ClientTestIn) -> ClientTestOut:
    record: int | None = None
    if payload.id is not None:
        stored = await asyncio.to_thread(_load, payload.id)
        if stored is None:
            raise _not_found()
        kind = payload.kind or stored.kind
        url = _clean_url(payload.url) if (payload.url or "").strip() else stored.url
        username = _clean_username(payload.username, kind) if payload.username is not None else stored.username
        given_secret = _clean_secret(payload.secret, kind)
        secret = given_secret or await asyncio.to_thread(_decrypted, stored)
        category = _clean_category(payload.category or stored.category, kind)
        own = (kind, url, username, category) == (stored.kind, stored.url, stored.username, stored.category)
        # Only a test of the stored settings says something about the stored client.
        record = stored.id if own and not given_secret else None
    else:
        missing = [name for name, value in (("kind", payload.kind), ("url", (payload.url or "").strip())) if not value]
        if missing or payload.kind is None:
            raise error("invalid_input", "The input is not valid.", 422, fields=missing or ["kind"])
        kind = payload.kind
        url = _clean_url(payload.url or "")
        username = _clean_username(payload.username, kind)
        secret = _clean_secret(payload.secret, kind)
        category = _clean_category(payload.category, kind)
    target = downloaders.Target(kind=kind, url=url, username=username or "", secret=secret, category=category)
    checked = await _test(target, create=False, record=record)
    return ClientTestOut(version=checked.version, category_exists=checked.category_exists)


def _apply(client_id: int, changes: dict[str, Any], secret: str | None, tested: bool) -> None:
    with SessionLocal() as db:
        row = db.get(DownloadClient, client_id)
        if row is None:
            raise _not_found()
        for name, value in changes.items():
            setattr(row, name, value)
        if secret:
            row.secret = crypto.encrypt(secret)
        if tested:
            row.last_error_code = None
        row.updated_at = utcnow()
        db.commit()
        logger.info("Download client %d changed", client_id)


@router.patch(
    "/{client_id}",
    response_model=DownloadClientOut,
    summary="Change a download client",
    description=(
        "Changes the given fields. An empty or missing `secret` keeps the stored one. `path_mappings` may only shrink. "
        "A new address, user name, secret or category is tested first, and the category created when missing. "
        "The kind of a saved client cannot change; another kind is refused before anything is tested or stored."
    ),
    responses=error_responses(
        (404, "client_not_found"),
        (422, "client_kind_locked"),
        (422, "client_invalid"),
        (404, "not_found"),
        *downloaders.ERRORS,
    ),
)
async def update_client(client_id: int, payload: ClientPatch) -> DownloadClientOut:
    stored = await asyncio.to_thread(_load, client_id)
    if stored is None:
        raise _not_found()
    if payload.kind is not None and payload.kind != stored.kind:
        # Another program is another client: its downloads, category and mappings belong to the stored kind.
        raise error(
            "client_kind_locked",
            "The kind of a saved download client cannot change. Add a new download client instead.",
            422,
            fields=["kind"],
        )
    given = payload.model_fields_set
    kind = stored.kind
    changes: dict[str, Any] = {}
    if payload.name is not None:
        changes["name"] = _clean_name(payload.name)
    if payload.enabled is not None:
        changes["enabled"] = payload.enabled
    if "priority" in given:
        changes["priority"] = _clean_priority(payload.priority)
    url = _clean_url(payload.url) if payload.url is not None else stored.url
    category = _clean_category(payload.category if payload.category is not None else stored.category, kind)
    if kind not in downloaders.WITH_USERNAME:
        username = None
    elif "username" in given:
        username = _clean_username(payload.username, kind)
    else:
        username = stored.username
    new_secret = _clean_secret(payload.secret, kind)
    if payload.path_mappings is not None:
        wanted = [mapping.model_dump() for mapping in payload.path_mappings]
        if any(mapping not in stored.path_mappings for mapping in wanted):
            raise error("invalid_input", "The input is not valid.", 422, fields=["path_mappings"])
        changes["path_mappings"] = wanted
    if payload.from_ is not None:
        if not await asyncio.to_thread(_source_exists, payload.from_.source_id):
            raise error("not_found", "This does not exist, or not any more.", 404)
        changes["source_id"], changes["radarr_client_id"] = payload.from_.source_id, payload.from_.radarr_id
    connection_changed = (
        url != stored.url or username != stored.username or bool(new_secret) or category != stored.category
    )
    if connection_changed:
        secret = new_secret or await asyncio.to_thread(_decrypted, stored)
        target = downloaders.Target(kind=kind, url=url, username=username or "", secret=secret, category=category)
        await _test(target, create=True)
        changes.update({"url": url, "username": username, "category": category})
    await asyncio.to_thread(_apply, client_id, changes, new_secret or None, connection_changed)
    if payload.tags is not None:
        await asyncio.to_thread(_tag, client_id, payload.tags)
    return await asyncio.to_thread(_read, client_id)


@router.delete(
    "/{client_id}",
    status_code=204,
    response_model=None,
    summary="Delete a download client",
    description=(
        "Removes the client and its stored secret. Refused while it has downloads that are not finished. The client "
        "itself is not contacted; its category stays there."
    ),
    responses=error_responses((404, "client_not_found"), (409, "client_in_use")),
)
def delete_client(client_id: int, db: DbSession) -> None:
    row = db.get(DownloadClient, client_id)
    if row is None:
        raise _not_found()
    if _active_count(db, client_id):
        raise error("client_in_use", "This download client still has downloads that are not finished.", 409)
    db.delete(row)
    db.commit()
    logger.info("Download client %d deleted", client_id)


@dataclass(frozen=True)
class _SourceRow:
    url: str
    api_key: str
    taken_over: bool = False
    app: str = "radarr"


def _source(source_id: int) -> _SourceRow | None:
    with SessionLocal() as db:
        row = db.get(Source, source_id)
        if row is None:
            return None
        return _SourceRow(url=row.url, api_key=row.api_key, taken_over=row.taken_over_at is not None, app=row.app)


def _existing() -> list[tuple[str, str, int | None, int | None]]:
    with SessionLocal() as db:
        return [
            (row.kind, row.url, row.source_id, row.radarr_client_id)
            for row in db.scalars(select(DownloadClient).order_by(DownloadClient.id))
        ]


async def _tag_names(client: Any) -> dict[int, str]:
    try:
        return await client.tags()
    except RadarrError:
        return {}


@router.get(
    "/from-radarr",
    response_model=list[RadarrClientOut],
    summary="List the download clients of a Radarr, Sonarr or Lidarr source",
    description=(
        "Reads the SABnzbd and qBittorrent entries of a Radarr source: address, user name of qBittorrent, Radarr's "
        "category, enabled and priority. Radarr never returns secrets; type them when adding. Other kinds are left "
        "out. A Sonarr source answers the same with Sonarr's ids and its TV category in `radarr_id` and "
        "`radarr_category`, a Lidarr source with its music category. A taken-over connection answers too: its key "
        "stays stored, and only its settings are read."
    ),
    responses=error_responses(
        (404, "not_found"),
        (409, "source_app_unsupported"),
        (422, "source_key_missing"),
        (422, "source_url_invalid"),
        *RADARR_ERRORS,
        *LIDARR_ERRORS,
    ),
)
async def list_from_radarr(source_id: Annotated[int, Query(description="The Radarr source.")]) -> list[RadarrClientOut]:
    source = await asyncio.to_thread(_source, source_id)
    if source is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    if source.app not in ("radarr", "sonarr", "lidarr"):
        raise error("source_app_unsupported", "This works for Radarr, Sonarr and Lidarr connections only.", 409)
    key = await asyncio.to_thread(crypto.decrypt, source.api_key)
    if not key:
        raise error(
            "source_key_missing", "The stored API key of this source cannot be read. Please enter it again.", 422
        )
    try:
        if source.app == "sonarr":
            async with SonarrClient(source.url, key) as sonarr:
                listed = await sonarr.download_clients()
                names = await _tag_names(sonarr)
        elif source.app == "lidarr":
            async with LidarrClient(source.url, key) as lidarr:
                listed = await lidarr.download_clients()
                names = await _tag_names(lidarr)
        else:
            async with RadarrClient(source.url, key) as radarr:
                listed = await radarr.download_clients()
                names = await _tag_names(radarr)
    except RadarrError as exc:
        raise exc.http() from exc
    except SourceUrlInvalid as exc:
        raise error(
            "source_url_invalid",
            "This address does not work. It has to start with http:// or https:// and must not contain "
            "a user name or password.",
            422,
        ) from exc
    existing = await asyncio.to_thread(_existing)
    result: list[RadarrClientOut] = []
    for entry in listed:
        kind = RADARR_KINDS.get((entry.implementation or "").strip().lower())
        if kind is None or entry.url is None:
            continue
        try:
            url = normalize_base_url(entry.url)
        except SourceUrlInvalid:
            continue
        priority = entry.priority if entry.priority is not None else PRIORITY_DEFAULT
        priority = priority if PRIORITY_MIN <= priority <= PRIORITY_MAX else PRIORITY_DEFAULT
        already = any(
            (stored_kind == kind and stored_url == url) or (stored_source == source_id and stored_radarr == entry.id)
            for stored_kind, stored_url, stored_source, stored_radarr in existing
        )
        result.append(
            RadarrClientOut(
                radarr_id=entry.id,
                name=entry.name[:NAME_MAX_LENGTH],
                kind=kind,
                url=url,
                username=entry.username if kind in downloaders.WITH_USERNAME else None,
                radarr_category=entry.category,
                enabled=entry.enabled,
                priority=priority,
                already_added=already,
                tags=[label for label in (tags.clean_or_none(names.get(tag_id, "")) for tag_id in entry.tags) if label],
            )
        )
    logger.info("Source %d lists %d download clients nexcrate can take", source_id, len(result))
    return result
