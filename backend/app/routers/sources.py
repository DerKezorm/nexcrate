"""Sources: the Radarr instances nexcrate reads, never writes to.

⚠️ The API key is stored encrypted and never leaves the backend again, not even partly.
Answers carry ``has_api_key`` instead.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from .. import crypto
from ..db import SessionLocal
from ..deps import DbSession
from ..meldungen import error, error_responses
from ..models import media, utcnow
from ..services import images, importer, indexers, takeover
from ..services.lidarr import ERRORS as LIDARR_ERRORS
from ..services.lidarr import LidarrClient
from ..services.music import lidarr_import
from ..services.radarr import RadarrClient, RadarrError, SourceUrlInvalid, normalize_base_url
from ..services.schreibweisen import nfc
from ..services.search import album as album_search
from ..services.series import folder_read as series_folder_read
from ..services.series import sonarr_import
from ..services.sonarr import ERRORS as SONARR_ERRORS
from ..services.sonarr import SonarrClient
from .imports import ImportRun, run_out

logger = logging.getLogger("nexcrate.sources")

router = APIRouter(prefix="/api/sources", tags=["sources"])

NAME_MAX_LENGTH = 100
KEY_MAX_LENGTH = 256

RADARR_ERRORS = (
    (502, "radarr_unreachable"),
    (502, "radarr_key_rejected"),
    (502, "radarr_not_radarr"),
    (502, "radarr_http_error"),
    (504, "radarr_timeout"),
)


class Source(BaseModel):
    id: int
    name: str = Field(examples=["Radarr 4K"])
    app: str = Field(
        description="radarr (feeds a movie version), sonarr (a series version) or lidarr (the music version).",
        examples=["radarr"],
    )
    url: str = Field(description="Base address, without trailing slash.", examples=["http://radarr.example.com:7878"])
    has_api_key: bool = Field(
        description="Whether a key is stored. The key itself is never returned. A taken-over source keeps its key for "
        "undoing the takeover; a source taken over before 14.09.2026 has none."
    )
    version_id: int = Field(description="The version definition this source feeds.")
    last_import: ImportRun | None = Field(description="The newest import run, if any.")
    taken_over_at: datetime | None = Field(
        default=None,
        description="UTC. When the source was taken over; nexcrate does not read it until the takeover is undone. Null "
        "while nexcrate reads it.",
    )


class SourceIn(BaseModel):
    name: str = Field(max_length=400, description=f"1 to {NAME_MAX_LENGTH} characters.")
    app: str = Field(default="radarr", max_length=32, description="radarr, sonarr or lidarr.")
    url: str = Field(max_length=2048, description="http or https, without user name and password.")
    api_key: str = Field(max_length=KEY_MAX_LENGTH, description="The API key of the Radarr or Sonarr instance.")
    version_id: int = Field(
        description="A version definition of the app's kind (movie for Radarr, series for Sonarr) that no other "
        "source feeds."
    )


class SourcePatch(BaseModel):
    name: str | None = Field(default=None, max_length=400)
    app: str | None = Field(default=None, max_length=32)
    url: str | None = Field(default=None, max_length=2048)
    api_key: str | None = Field(default=None, max_length=KEY_MAX_LENGTH, description="Empty keeps the stored key.")
    version_id: int | None = None


class SourceTestIn(BaseModel):
    url: str | None = Field(default=None, max_length=2048, description="Overrides the stored address.")
    api_key: str | None = Field(
        default=None, max_length=KEY_MAX_LENGTH, description="Empty uses the stored key of `source_id`."
    )
    source_id: int | None = Field(default=None, description="Test a saved source.")
    app: str | None = Field(
        default=None,
        max_length=32,
        description="radarr, sonarr or lidarr for an unsaved connection; a saved one uses its own.",
    )


class SourceTestOut(BaseModel):
    app: str = Field(examples=["radarr"])
    app_version: str = Field(examples=["6.3.0.10514"])
    movie_count: int = Field(description="Radarr: its movies. 0 for Sonarr and Lidarr.")
    series_count: int | None = Field(default=None, description="Sonarr: its series. Null for the others.")
    artist_count: int | None = Field(default=None, description="Lidarr: its artists. Null for the others.")


class RadarrIndexer(BaseModel):
    radarr_indexer_id: int
    name: str
    kind: str = Field(description="newznab or torznab.")
    url: str = Field(
        description="Radarr's baseUrl plus apiPath, normalised.", examples=["https://indexer.example.com/api"]
    )
    categories: list[int]
    enabled: bool = Field(description="Whether RSS, automatic or interactive search is switched on in Radarr.")
    tags: list[int] = Field(
        description="Radarr's tag ids, as information only. Radarr skips a tagged indexer for movies without the "
        "tag; nexcrate ignores tags for now."
    )
    already_added: bool = Field(description="Whether an indexer with the same address exists in nexcrate.")


# --- Checks -------------------------------------------------------------------- #


def _clean_name(raw: str) -> str:
    name = nfc(raw).strip()
    if not name or len(name) > NAME_MAX_LENGTH:
        raise error("invalid_input", "The input is not valid.", 422, fields=["name"])
    return name


def _check_app(raw: str) -> str:
    app = raw.strip().lower()
    if app not in media.APPS:
        raise error("app_unsupported", "This app is not supported yet.", 422)
    return app


def _clean_url(raw: str) -> str:
    try:
        return normalize_base_url(raw)
    except SourceUrlInvalid as exc:
        raise error(
            "source_url_invalid",
            "This address does not work. It has to start with http:// or https:// and must not contain "
            "a user name or password.",
            422,
        ) from exc


def _clean_key(raw: str) -> str:
    key = raw.strip()
    if not key or not key.isascii() or any(character.isspace() or not character.isprintable() for character in key):
        raise error("invalid_input", "The input is not valid.", 422, fields=["api_key"])
    return key


def _definition_for(
    db: OrmSession, version_id: int, own_source_id: int | None, app: str = "radarr"
) -> media.VersionDefinition:
    definition = db.get(media.VersionDefinition, version_id)
    if definition is None:
        raise error("invalid_input", "The input is not valid.", 422, fields=["version_id"])
    if definition.kind != media.APP_KINDS.get(app, "movie"):
        raise error(
            "version_kind_mismatch",
            "This version belongs to another type. Radarr needs a movie version, Sonarr a series version, Lidarr the "
            "music version.",
            422,
        )
    taken = db.scalar(
        select(media.Source.id).where(media.Source.version_id == version_id, media.Source.id != (own_source_id or 0))
    )
    if taken is not None:
        raise error("version_taken", "This version already gets its data from another source.", 409)
    return definition


def source_out(db: OrmSession, row: media.Source) -> Source:
    last = db.scalar(
        select(media.ImportRun).where(media.ImportRun.source_id == row.id).order_by(media.ImportRun.id.desc()).limit(1)
    )
    return Source(
        id=row.id,
        name=row.name,
        app=row.app,
        url=row.url,
        has_api_key=bool(row.api_key),
        version_id=row.version_id,
        last_import=run_out(last) if last is not None else None,
        taken_over_at=row.taken_over_at,
    )


def _delete_taken_over(db: OrmSession, row: media.Source) -> None:
    """A taken-over source: only the record goes, with its alternate titles and import runs.

    ⚠️ No ``release_source`` and no orphan cleanup: its versions are nexcrate's own now, and so are their titles.
    """
    source_id = row.id
    options = {"synchronize_session": False}
    posters = list(db.scalars(select(media.Title.id).where(media.Title.poster_source_id == source_id)))
    db.execute(
        delete(media.AlternateTitle).where(media.AlternateTitle.source_id == source_id), execution_options=options
    )
    db.execute(
        update(media.Title).where(media.Title.meta_source_id == source_id).values(meta_source_id=None),
        execution_options=options,
    )
    db.execute(
        update(media.Title).where(media.Title.poster_source_id == source_id).values(**importer.poster_gone()),
        execution_options=options,
    )
    db.execute(
        update(media.Indexer)
        .where(media.Indexer.source_id == source_id)
        .values(source_id=None, radarr_indexer_id=None),
        execution_options=options,
    )
    db.execute(delete(media.ImportRun).where(media.ImportRun.source_id == source_id), execution_options=options)
    db.delete(row)
    db.commit()
    # The cached copies of its posters go; TMDB's posters take their place.
    images.forget(posters)
    logger.info("Taken-over source %d deleted; %d posters give way to TMDB's", source_id, len(posters))


# --- Routes ------------------------------------------------------------------------ #


@router.get(
    "",
    response_model=list[Source],
    summary="List the sources",
    description="Every Radarr source with its newest import run. The API key is never part of the answer.",
)
def list_sources(db: DbSession) -> list[Source]:
    return [source_out(db, row) for row in db.scalars(select(media.Source).order_by(media.Source.id))]


@router.post(
    "",
    status_code=201,
    response_model=Source,
    summary="Add a Radarr source",
    description=(
        "Stores the source with its API key encrypted. Radarr is not contacted; use the connection test "
        "first. One version definition can be fed by one source only."
    ),
    responses=error_responses(
        (409, "version_taken"),
        (422, "source_url_invalid"),
        (422, "app_unsupported"),
        (422, "version_kind_mismatch"),
    ),
)
def create_source(payload: SourceIn, db: DbSession) -> Source:
    name = _clean_name(payload.name)
    app = _check_app(payload.app)
    url = _clean_url(payload.url)
    key = _clean_key(payload.api_key)
    definition = _definition_for(db, payload.version_id, None, app)
    now = utcnow()
    row = media.Source(
        name=name,
        app=app,
        url=url,
        api_key=crypto.encrypt(key),
        version_id=definition.id,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise error("version_taken", "This version already gets its data from another source.", 409) from exc
    logger.info("Source %d created for version definition %d", row.id, row.version_id)
    return source_out(db, row)


@router.patch(
    "/{source_id}",
    response_model=Source,
    summary="Change a source",
    description=(
        "Changes any of the given fields. An empty or missing `api_key` keeps the stored key. A new "
        "`version_id` moves the versions already imported from this source to that definition. Versions you added "
        "yourself stay in their definition, no longer fed; your versions in the new definition are taken over. A "
        "taken-over source answers 409 `source_taken_over`; while a check or takeover of it runs, 409 "
        "`takeover_running`."
    ),
    responses=error_responses(
        (404, "not_found"),
        (409, "version_taken"),
        (409, "source_taken_over"),
        (409, "takeover_running"),
        (409, "source_app_locked"),
        (409, "source_version_locked"),
        (422, "source_url_invalid"),
        (422, "app_unsupported"),
        (422, "version_kind_mismatch"),
    ),
)
def update_source(source_id: int, payload: SourcePatch, db: DbSession) -> Source:
    row = db.get(media.Source, source_id)
    if row is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    if row.taken_over_at is not None:
        raise error("source_taken_over", "This connection was taken over. nexcrate no longer reads it.", 409)
    if takeover.is_running(source_id):
        raise error("takeover_running", "A check or takeover of this connection is already running.", 409)
    if payload.name is not None:
        row.name = _clean_name(payload.name)
    if payload.app is not None and _check_app(payload.app) != row.app:
        raise error("source_app_locked", "The app of a connection cannot change. Delete it and add a new one.", 409)
    if payload.url is not None:
        row.url = _clean_url(payload.url)
    if payload.api_key is not None and payload.api_key.strip():
        row.api_key = crypto.encrypt(_clean_key(payload.api_key))
    if payload.version_id is not None and payload.version_id != row.version_id:
        definition = _definition_for(db, payload.version_id, row.id, row.app)
        if row.app == "sonarr" and importer.count_versions(db, row.id):
            # Moving series versions with their seasons and episodes is not built.
            raise error(
                "source_version_locked",
                "This Sonarr connection already feeds its version. Delete it and add it again to feed another one.",
                409,
            )
        importer.move_source(db, row, definition, utcnow())
        row.version_id = definition.id
    row.updated_at = utcnow()
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise error("version_taken", "This version already gets its data from another source.", 409) from exc
    logger.info("Source %d changed", row.id)
    return source_out(db, row)


@router.delete(
    "/{source_id}",
    status_code=204,
    response_model=None,
    summary="Delete a source",
    description=(
        "Removes the source, the versions imported from it and every title left without a version. Versions you "
        "added yourself that it fed stay, wanted again. Indexers fetched from it stay. Radarr itself is never "
        "touched. Refused while an import, a check or a takeover of this source runs. Of a taken-over source only the "
        "record goes, with its alternate titles and import runs: versions and titles stay, and its posters give way "
        "to TMDB's."
    ),
    responses=error_responses((404, "not_found"), (409, "import_running")),
)
def delete_source(source_id: int, db: DbSession) -> None:
    row = db.get(media.Source, source_id)
    if row is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    if importer.is_running(source_id):
        raise error("import_running", "An import is already running for this source.", 409)
    if row.taken_over_at is not None:
        _delete_taken_over(db, row)
        return
    detached_series: list[int] = []
    if row.app == "sonarr":
        # The owner's series versions it fed stay with their rule; their files belonged to Sonarr. They become
        # nexcrate's own, so their folders are read before they want anything (23).
        detached_series = sonarr_import.release_series_source(db, source_id, utcnow())
    if row.app == "lidarr":
        # The owner's album versions it fed become nexcrate's own; the unmapped files go.
        lidarr_import.release_music_source(db, source_id, utcnow())
    removed, detached = importer.release_source(db, source_id, utcnow())
    options = {"synchronize_session": False}
    db.execute(
        delete(media.AlternateTitle).where(media.AlternateTitle.source_id == source_id), execution_options=options
    )
    db.execute(
        update(media.Title).where(media.Title.meta_source_id == source_id).values(meta_source_id=None),
        execution_options=options,
    )
    db.execute(
        update(media.Title).where(media.Title.poster_source_id == source_id).values(**importer.poster_gone()),
        execution_options=options,
    )
    db.execute(
        update(media.Indexer)
        .where(media.Indexer.source_id == source_id)
        .values(source_id=None, radarr_indexer_id=None),
        execution_options=options,
    )
    db.execute(delete(media.ImportRun).where(media.ImportRun.source_id == source_id), execution_options=options)
    db.delete(row)
    db.flush()
    removed_titles = importer.remove_orphans(db)
    db.commit()
    if detached_series:
        series_folder_read.enqueue(detached_series)
    images.forget(removed_titles)
    logger.info(
        "Source %d deleted with %d versions, %d own versions kept, %d titles removed",
        source_id,
        removed,
        detached,
        len(removed_titles),
    )


@dataclass(frozen=True)
class _Stored:
    url: str
    api_key: str
    taken_over: bool = False
    app: str = "radarr"


def _stored(source_id: int) -> _Stored | None:
    with SessionLocal() as db:
        row = db.get(media.Source, source_id)
        if row is None:
            return None
        return _Stored(url=row.url, api_key=row.api_key, taken_over=row.taken_over_at is not None, app=row.app)


@router.post(
    "/test",
    response_model=SourceTestOut,
    summary="Test the connection to Radarr, Sonarr or Lidarr",
    description=(
        "Send `url`, `api_key` and `app`, or `source_id` of a saved source. With `source_id` a given `url` "
        "overrides the stored one, and an empty `api_key` uses the stored key. Checks that Radarr answers "
        "as Radarr and counts its movies, that Sonarr 4 or later answers and counts its series, or that Lidarr 2 or "
        "later answers and counts its artists. Nothing is stored. A taken-over `source_id` answers 409."
    ),
    responses=error_responses(
        (404, "not_found"),
        (409, "source_taken_over"),
        (422, "source_url_invalid"),
        (422, "source_key_missing"),
        (422, "app_unsupported"),
        *RADARR_ERRORS,
        *SONARR_ERRORS,
        *LIDARR_ERRORS,
    ),
)
async def check_connection(payload: SourceTestIn) -> SourceTestOut:
    url_given = (payload.url or "").strip()
    key_given = (payload.api_key or "").strip()
    app = _check_app(payload.app or "radarr")
    if payload.source_id is not None:
        stored = await asyncio.to_thread(_stored, payload.source_id)
        if stored is None:
            raise error("not_found", "This does not exist, or not any more.", 404)
        if stored.taken_over:
            raise error("source_taken_over", "This connection was taken over. nexcrate no longer reads it.", 409)
        app = stored.app
        url_given = url_given or stored.url
        if not key_given:
            key_given = await asyncio.to_thread(crypto.decrypt, stored.api_key)
            if not key_given:
                raise error(
                    "source_key_missing",
                    "The stored API key of this source cannot be read. Please enter it again.",
                    422,
                )
    missing = [name for name, value in (("url", url_given), ("api_key", key_given)) if not value]
    if missing:
        raise error("invalid_input", "The input is not valid.", 422, fields=missing)
    url = _clean_url(url_given)
    key = _clean_key(key_given)
    if app == "sonarr":
        try:
            async with SonarrClient(url, key) as sonarr:
                sonarr_status = await sonarr.system_status()
                series_count = await sonarr.series_count()
        except RadarrError as exc:
            logger.info("Connection test failed: %s", exc.code)
            raise exc.http() from exc
        logger.info("Connection test succeeded: Sonarr %s with %d series", sonarr_status.version, series_count)
        return SourceTestOut(app="sonarr", app_version=sonarr_status.version, movie_count=0, series_count=series_count)
    if app == "lidarr":
        try:
            async with LidarrClient(url, key) as lidarr:
                lidarr_status = await lidarr.system_status()
                artist_count = len(await lidarr.artists())
        except RadarrError as exc:
            logger.info("Connection test failed: %s", exc.code)
            raise exc.http() from exc
        logger.info("Connection test succeeded: Lidarr %s with %d artists", lidarr_status.version, artist_count)
        return SourceTestOut(app="lidarr", app_version=lidarr_status.version, movie_count=0, artist_count=artist_count)
    try:
        async with RadarrClient(url, key) as radarr:
            status = await radarr.system_status()
            movie_count = await radarr.movie_count()
    except RadarrError as exc:
        logger.info("Connection test failed: %s", exc.code)
        raise exc.http() from exc
    logger.info("Connection test succeeded: Radarr %s with %d movies", status.version, movie_count)
    return SourceTestOut(app="radarr", app_version=status.version, movie_count=movie_count)


def _listed_categories(app: str, categories: list[int]) -> list[int]:
    """Lidarr's categories as nexcrate takes them: without music videos and audiobooks (3020, 3030)."""
    if app != "lidarr":
        return categories
    return [category for category in categories if category not in album_search.LEFT_OUT_CATEGORIES]


def _indexer_urls() -> set[str]:
    with SessionLocal() as db:
        return set(db.scalars(select(media.Indexer.url)))


@router.get(
    "/{source_id}/indexers",
    response_model=list[RadarrIndexer],
    summary="List the indexers of a Radarr, Sonarr or Lidarr source",
    description=(
        "Reads Radarr's Newznab and Torznab indexers, to fetch them into nexcrate with "
        "`POST /api/indexers/from-source`. Radarr never returns the API key, so it is not part of the answer; other "
        "kinds of indexer and entries without a usable address are left out. Nothing is stored. A Sonarr source "
        "answers its indexers the same way, with its TV categories as `categories`, a Lidarr source with its music "
        "categories without 3020 and 3030. A taken-over connection answers too: its key stays stored, and only its "
        "settings are read."
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
async def list_source_indexers(source_id: int) -> list[RadarrIndexer]:
    stored = await asyncio.to_thread(_stored, source_id)
    if stored is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    if stored.app not in ("radarr", "sonarr", "lidarr"):
        raise error("source_app_unsupported", "This works for Radarr, Sonarr and Lidarr connections only.", 409)
    key = await asyncio.to_thread(crypto.decrypt, stored.api_key)
    if not key:
        raise error(
            "source_key_missing", "The stored API key of this source cannot be read. Please enter it again.", 422
        )
    try:
        if stored.app == "sonarr":
            async with SonarrClient(stored.url, key) as sonarr:
                listed = await sonarr.indexers()
        elif stored.app == "lidarr":
            async with LidarrClient(stored.url, key) as lidarr:
                listed = await lidarr.indexers()
        else:
            async with RadarrClient(stored.url, key) as radarr:
                listed = await radarr.indexers()
    except RadarrError as exc:
        logger.info("Reading the indexers of source %d failed: %s", source_id, exc.code)
        raise exc.http() from exc
    except SourceUrlInvalid as exc:
        raise error(
            "source_url_invalid",
            "This address does not work. It has to start with http:// or https:// and must not contain "
            "a user name or password.",
            422,
        ) from exc
    known = await asyncio.to_thread(_indexer_urls)
    result: list[RadarrIndexer] = []
    for item in listed:
        kind = indexers.kind_of_implementation(item.implementation)
        try:
            url = indexers.normalize_url(item.endpoint, add_api_path=False)
        except indexers.IndexerUrlInvalid:
            continue
        if kind is None:
            continue
        result.append(
            RadarrIndexer(
                radarr_indexer_id=item.id,
                name=item.name[:NAME_MAX_LENGTH],
                kind=kind,
                url=url,
                categories=_listed_categories(stored.app, item.categories),
                enabled=item.enabled,
                tags=item.tags,
                already_added=url in known,
            )
        )
    logger.info("Source %d lists %d indexers, %d of them Newznab or Torznab", source_id, len(listed), len(result))
    return result


@router.post(
    "/{source_id}/import",
    status_code=202,
    response_model=ImportRun,
    summary="Import a source now",
    description=(
        "Starts an import in the background and answers at once with the running import. Poll "
        "`GET /api/imports/{run_id}`. Every source is also imported every 15 minutes. A Sonarr connection needs the "
        "TMDB token; without it the run fails with `tmdb_not_configured`."
    ),
    responses=error_responses((404, "not_found"), (409, "import_running"), (409, "source_taken_over")),
)
def import_source(source_id: int) -> ImportRun:
    try:
        run = importer.start(source_id)
    except importer.SourceMissing as exc:
        raise error("not_found", "This does not exist, or not any more.", 404) from exc
    except importer.ImportRunning as exc:
        raise error("import_running", "An import is already running for this source.", 409) from exc
    except importer.SourceTakenOver as exc:
        raise error("source_taken_over", "This connection was taken over. nexcrate no longer reads it.", 409) from exc
    return run_out(run)
