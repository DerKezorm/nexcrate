"""Entry point: the FastAPI app, its routers, the background jobs and the built frontend."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.dependencies.models import Dependant
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import Match

from . import __version__
from . import db as database
from .config import get_settings
from .db import init_db
from .deps import require_api_key, require_session
from .meldungen import (
    ERROR_CODES_PREFIX,
    ApiError,
    ErrorResponse,
    describe_codes,
    error_body,
    meldung,
)
from .middleware import (
    SAFE_METHODS,
    CsrfMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
    UrlBaseMiddleware,
    content_security_policy,
    internal_error_response,
    is_api_path,
)
from .routers import api_keys as api_keys_router
from .routers import arr_profiles as arr_profiles_router
from .routers import auth, docs, health, imports, library, setup, sources, versions
from .routers import auto_tags as auto_tags_router
from .routers import automatic as automatic_router
from .routers import backups as backups_router
from .routers import blocklist as blocklist_router
from .routers import companions as companions_router
from .routers import disk as disk_router
from .routers import download_clients as download_clients_router
from .routers import downloads as downloads_router
from .routers import expert as expert_router
from .routers import folders as folders_router
from .routers import foreign_jobs as foreign_jobs_router
from .routers import images as images_router
from .routers import indexers as indexers_router
from .routers import logs as logs_router
from .routers import media_servers as media_servers_router
from .routers import music as music_router
from .routers import music_profile as music_profile_router
from .routers import music_search as music_search_router
from .routers import naming as naming_router
from .routers import notifications as notifications_router
from .routers import numbering as numbering_router
from .routers import open as open_router
from .routers import pairings as pairings_router
from .routers import profiles as profiles_router
from .routers import ratings as ratings_router
from .routers import recycle as recycle_router
from .routers import release_calendar as calendar_router
from .routers import releases as releases_router
from .routers import rename as rename_router
from .routers import searches as searches_router
from .routers import subtitles as subtitles_router
from .routers import system_settings as system_settings_router
from .routers import tags as tags_router
from .routers import takeover as takeover_router
from .routers import tmdb as tmdb_router
from .routers import trash as trash_router
from .routers import v1 as v1_router
from .routers import v1_back as v1_back_router
from .routers import v1_pairing as v1_pairing_router
from .routers import v1_round as v1_round_router
from .routers import v1_write as v1_write_router
from .routers import waiting as waiting_router
from .routers import webhooks as webhooks_router
from .routers import whats_new as whats_new_router
from .services import (
    api_keys,
    auto_tags,
    backups,
    importer,
    jobs,
    judging,
    logs,
    profile_upkeep,
    ratings,
    sessions,
    tmdb,
    trash,
    updates,
    webhooks,
)
from .services.api_v1 import changes as v1_changes
from .services.api_v1 import events as v1_events
from .services.automatic import budget as automatic_budget
from .services.automatic import clock as automatic_clock
from .services.automatic import rss as automatic_rss
from .services.automatic import scheduler as automatic_scheduler
from .services.automatic import waiting as automatic_waiting
from .services.automatic import wishes as automatic_wishes
from .services.downloads import importing as download_importing
from .services.downloads import retention as download_retention
from .services.downloads import store as download_store
from .services.downloads import tracking as download_tracking
from .services.music import loading as music_loading
from .services.notify import outbox as notify_outbox
from .services.profiles import expert as profile_expert
from .services.rename import apply as rename_apply
from .services.series import folder_read as series_folder_read
from .services.series import halves as series_halves
from .services.series import watching as series_watching

logger = logging.getLogger("nexcrate")

#: Every router of the app, in one list.
#:
#: ⚠️ An allowlist, not a denylist. A router needs a session unless its entry names the
#: reason why it is public. ``tests/test_protected_routes.py`` walks the route table and
#: fails for every /api route that answers without a session and is not a documented
#: public path. A new router goes at the end with ``None``.
ROUTERS: list[tuple[APIRouter, str | None]] = [
    (health.router, "Liveness check for Docker and reverse proxies; carries no data."),
    (setup.router, "The first start has no account yet; the route closes for good once it exists."),
    (auth.public_router, "Logging in cannot require being logged in."),
    (docs.router, "The API description; carries no data."),
    (auth.router, None),
    (logs_router.router, None),
    (versions.router, None),
    (sources.router, None),
    (imports.router, None),
    (library.router, None),
    (images_router.router, None),
    (tmdb_router.router, None),
    (indexers_router.router, None),
    (profiles_router.router, None),
    (releases_router.router, None),
    (trash_router.router, None),
    (searches_router.router, None),
    (download_clients_router.router, None),
    (downloads_router.router, None),
    (folders_router.router, None),
    (naming_router.router, None),
    (blocklist_router.router, None),
    (recycle_router.router, None),
    (takeover_router.router, None),
    (automatic_router.router, None),
    (subtitles_router.router, None),
    (disk_router.router, None),
    (companions_router.router, None),
    (numbering_router.router, None),
    (music_router.router, None),
    (music_profile_router.router, None),
    (music_search_router.router, None),
    (rename_router.router, None),
    (expert_router.router, None),
    (arr_profiles_router.router, None),
    (calendar_router.router, None),
    (
        calendar_router.public_router,
        (
            "The calendar subscription: it carries a key of its own in the address and answers only while "
            "the owner has it turned on."
        ),
    ),
    (media_servers_router.router, None),
    (waiting_router.router, None),
    (api_keys_router.router, None),
    (
        v1_router.router,
        (
            "The contract for other programs: it opens with a key in the header Authorization, never with the "
            "session, and every route needs the scope read."
        ),
    ),
    (
        v1_write_router.router,
        (
            "The contract for other programs, stage V2: a key in the header Authorization, never the session; "
            "every route needs the scope read, and all but reading the bin the scope request."
        ),
    ),
    (
        v1_back_router.router,
        (
            "The contract for other programs, stage V3: a key in the header Authorization, never the session; "
            "every route needs the scope read, and every route that acts on a download the scope operate."
        ),
    ),
    (
        v1_round_router.router,
        (
            "The contract for other programs, stage V4: a key in the header Authorization, never the session; "
            "every route needs the scope read, asking before a request the scope request."
        ),
    ),
    (
        v1_pairing_router.router,
        (
            "A program without a key asks for one here; it learns nothing but whether the owner confirmed, and only "
            "with the secret of its own request."
        ),
    ),
    (pairings_router.router, None),
    (ratings_router.router, None),
    (system_settings_router.router, None),
    (webhooks_router.router, None),
    (open_router.router, None),
    (tags_router.router, None),
    (auto_tags_router.router, None),
    (foreign_jobs_router.router, None),
    (backups_router.router, None),
    (whats_new_router.router, None),
    (notifications_router.router, None),
]

# Background jobs, started in the lifespan. The first one takes an expired deep log mode back.
jobs.register("log_mode_expiry", 30, logs.enforce_expiry)
jobs.register("session_cleanup", 3600, sessions.purge_expired)
jobs.register(backups.JOB_NAME, backups.INTERVAL_SECONDS, backups.run_job)
jobs.register(auto_tags.JOB_NAME, auto_tags.INTERVAL_SECONDS, auto_tags.run_job)
jobs.register(importer.JOB_NAME, importer.INTERVAL_SECONDS, importer.import_all_sources)
jobs.register(tmdb.JOB_NAME, tmdb.REFRESH_INTERVAL_SECONDS, tmdb.refresh_job)
jobs.register(trash.CHECK_JOB, trash.CHECK_INTERVAL_SECONDS, trash.check_job)
# ⚠️ ``ready`` holds it back while the start judges every stored file again; the reason is in the tracking module.
jobs.register(
    download_tracking.JOB_NAME,
    download_tracking.INTERVAL_SECONDS,
    download_tracking.run_job,
    ready=download_tracking.ready,
)
jobs.register(
    download_importing.JOB_RECYCLE, download_importing.RECYCLE_INTERVAL_SECONDS, download_importing.clean_recycle
)
# how long the Usenet clients' news servers keep articles, read again when they changed or hourly.
jobs.register(download_retention.JOB_NAME, download_retention.INTERVAL_SECONDS, download_retention.run_job)
# After a takeover: TMDB's data for the taken-over titles, 50 titles and then a minute's pause.
jobs.register(tmdb.FILL_JOB, tmdb.FILL_INTERVAL_SECONDS, tmdb.fill_job)
# The automatic's four rounds come as often as its clock runs fast; only a test bench sets that (clock.py).
_automatic_every = automatic_clock.interval
# Step 3c: the plan per title every minute; with the switch on also the planned searches and their loads.
jobs.register(
    automatic_scheduler.JOB_NAME, _automatic_every(automatic_scheduler.INTERVAL_SECONDS), automatic_scheduler.run_job
)
# Step 3c: RSS looks every minute for indexers whose sync is due; with the switch off it returns at once.
jobs.register(automatic_rss.JOB_NAME, _automatic_every(automatic_rss.INTERVAL_SECONDS), automatic_rss.run_job)
# Programs' search wishes search at once, at most five at a time, whatever the automatic's switches say (22.09.2026).
jobs.register(automatic_wishes.JOB_NAME, _automatic_every(automatic_wishes.INTERVAL_SECONDS), automatic_wishes.run_job)
# Delay rules: releases that waited out the delay of their version are judged again and loaded, every minute.
jobs.register(
    automatic_waiting.JOB_NAME, _automatic_every(automatic_waiting.INTERVAL_SECONDS), automatic_waiting.run_job
)
# Music M1: artists are loaded from MusicBrainz in the background, up to a minute per run.
jobs.register(music_loading.JOB_NAME, music_loading.INTERVAL_SECONDS, music_loading.run_job)
# ⚠️ The four /api/v1 and webhook jobs below write every few seconds and wait for the start's judging (``ready``), like
# the tracking above: on 22.09.2026 the event feed failed with "database is locked" while it ran.
# /api/v1: when a key was last used is written once a minute, never by the request that used it.
jobs.register(api_keys.JOB_NAME, api_keys.INTERVAL_SECONDS, api_keys.flush_used, ready=judging.idle)
# /api/v1: the change numbers of the library, from comparing what the list says about every title.
jobs.register(v1_changes.JOB_NAME, v1_changes.INTERVAL_SECONDS, v1_changes.run_job, ready=judging.idle)
# /api/v1: the event feed. Downloads are compared every few seconds, versions, health and takeovers every minute; what
# the history records becomes an event in its own transaction, through the hook on the insert of a history line.
jobs.register(v1_events.JOB_NAME, v1_events.INTERVAL_SECONDS, v1_events.run_job, ready=judging.idle)
v1_events.install()
# V4: IMDb's daily file, the check for a newer version, and the webhooks. The first two ask
# outside only when switched on and due; the webhooks only for targets the owner made.
jobs.register(ratings.JOB_NAME, ratings.INTERVAL_SECONDS, ratings.run_job)
jobs.register(updates.JOB_NAME, updates.INTERVAL_SECONDS, updates.run_job)
jobs.register(webhooks.JOB_NAME, webhooks.INTERVAL_SECONDS, webhooks.run_job, ready=judging.idle)
# Notifications to the owner's services read the same feed.
jobs.register(notify_outbox.JOB_NAME, notify_outbox.INTERVAL_SECONDS, notify_outbox.run_job, ready=judging.idle)

OPENAPI_TAGS = [
    {"name": "system", "description": "Whether the server runs."},
    {"name": "setup", "description": "The first start: create the one account."},
    {"name": "auth", "description": "Login, logout, the account and its password."},
    {"name": "logs", "description": "The log viewer: read, download, clear, switch the mode."},
    {"name": "versions", "description": "Version definitions: named slots per media kind, such as HD and 4K."},
    {
        "name": "sources",
        "description": "The Radarr instances nexcrate reads, never writes to, and taking them over with their files.",
    },
    {"name": "imports", "description": "What the imports of the sources did."},
    {"name": "library", "description": "Every title over every source, with its versions."},
    {"name": "images", "description": "Posters, loaded through the source or from TMDB and cached."},
    {"name": "tmdb", "description": "The TMDB token and the search for movies to add."},
    {"name": "indexers", "description": "Newznab and Torznab indexers: connect, test, a test search, search settings."},
    {"name": "profiles", "description": "One profile per version: the wizard's questions, preview, save, YAML files."},
    {"name": "releases", "description": "The release checker: a release name against the profile of every version."},
    {"name": "trash", "description": "The TRaSH Guides state the profiles are built with, its check and update."},
    {
        "name": "searches",
        "description": "Searching the indexers for a movie, what nexcrate would take, and what can be loaded.",
    },
    {"name": "download-clients", "description": "SABnzbd and qBittorrent: connect, test, fetch from Radarr."},
    {"name": "downloads", "description": "Loading a release, following it, filing it away; retry, mappings, removing."},
    {"name": "folders", "description": "The folders nexcrate sees, for choosing a version's default folder."},
    {"name": "naming", "description": "Folder and file names of imported movies, with Radarr's tokens."},
    {"name": "blocklist", "description": "Releases that failed or were refused, per title."},
    {"name": "waiting", "description": "Releases that wait out the delay rule of their version before they load."},
    {"name": "api-keys", "description": "The keys other programs sign in with at /api/v1: make, name, revoke."},
    {
        "name": "v1",
        "description": "The contract for other programs such as Nexview: stable over nexcrate versions, opened by a "
        "key, errors flat. Stage V1: reading.",
    },
    {"name": "recycle", "description": "How long old files stay in the recycle folders next to the movies."},
    {
        "name": "automatic",
        "description": "Searching and loading by itself: the switch, and searching one title automatically now.",
    },
    {"name": "subtitles", "description": "Whether an import places the download's subtitle files next to the movie."},
    {
        "name": "disk",
        "description": "Folders on disk: scanning, proposals, assigning by hand and restoring from release.nex.",
    },
    {
        "name": "companions",
        "description": "release.nex next to every movie nexcrate owns: the switch, the check and the backfill.",
    },
    {
        "name": "calendar",
        "description": "What comes out when: cinema, digital and disc dates, episodes airing, albums, and the "
        "subscription a calendar app reads.",
    },
]

API_DESCRIPTION = """The API of nexcrate. The interface uses exactly this API.

**Authentication.** `POST /api/setup` on the first start, or `POST /api/auth/login`, sets the
session cookie `nexcrate_session` (HttpOnly, SameSite=Strict, path `/api`). Every other route
under `/api` needs it, except the health check and this documentation.

**Requests that change something** (everything but GET, HEAD and OPTIONS) must send the header
`X-Requested-With: nexcrate`, otherwise the answer is 403 `csrf_header_missing`. This page sends
it for you.

**Errors** have one shape: `{"detail": {"code": "...", "message": "..."}}`, with extra values
next to `code`. The `code` is stable, `message` is an English fallback. Every answer carries the
header `X-Request-Id`; a 500 also carries `request_id` in the body. Quote it when reporting a
problem, it finds the matching log lines.

**Other programs** use `/api/v1` and nothing else: it stays as it is while the routes above follow
the interface. It opens with a key from Settings, API keys, sent as `Authorization: Bearer <key>`,
never with the session cookie, and it needs no `X-Requested-With`. Its errors are flat:
`{"code": "...", "message": "...", "params": {...}}`. A program may send its own `X-Request-Id`
(up to 36 letters, digits and hyphens); nexcrate writes it on every log line of that request.
"""

_CODE_IN_DESCRIPTION = re.compile(r"`([a-z0-9_]+)`")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    logs.setup()
    # A backup laid out by /api/backups/restore is swapped in before anything opens the database (plan-sicherung).
    try:
        restored = backups.apply_pending()
    except Exception:
        restored = False
        logger.exception("Restoring the laid out backup failed; the database stays as it was")
    init_db()
    if restored:
        # The sessions in the restored database are of then; everyone logs in again.
        backups.end_sessions()
    # Before anything else writes: the old TMDB cache once, then the empty pages back (24.09.2026).
    try:
        database.compact_if_worth()
    except Exception:
        logger.exception("Compacting the database failed; it stays as it is")
    # One connection stays open while the app runs, so no close is ever the last one (see db.hold_open).
    database.hold_open()
    # Only now: the stored log mode lives in the database, which does not exist before init_db.
    logs.apply_stored_mode()
    # Profiles saved before a question of the wizard changed get their normalized answers (step 2c), and profiles
    # built with an older version of the rules are built again.
    try:
        profile_upkeep.rebuild_normalized()
    except Exception:
        logger.exception("Rebuilding the stored profiles failed; they stay as they are")
    # An import that was running when the app stopped never finishes; say so instead of "running" forever.
    importer.mark_interrupted()
    # The same for filing away a download: it waits for the next round of the tracking.
    download_importing.mark_interrupted()
    # A title stopped in the middle of a rename gets its files back; its database never changed.
    try:
        rename_apply.recover()
    except Exception:
        logger.exception("Taking back a stopped rename failed; its files may lie under their new names")
    # Series versions are counted again once after the counting rules changed (decision 46).
    try:
        counted = series_watching.recount_all_once()
        if counted:
            logger.info("%d series versions counted again", counted)
    except Exception:
        logger.exception("Counting the series versions again failed; they keep their counts")
    # nexcrate's own files are judged again with the rules in use, after the profile upkeep, in a thread of its own:
    # neither the start nor the health check waits for it (finding 15).
    try:
        judging.start()
    except Exception:
        logger.exception("Judging the stored files again could not start; they keep their judgement")
    # The formats and sizes of the stored profiles move into their own tables, once (expert mode).
    try:
        with database.SessionLocal() as db:
            moment = download_store.now()
            counts = profile_expert.split_out(db, moment)
            # And what no profile brought gets the default of Radarr or Sonarr, so the page shows their numbers.
            counts["sizes"] += profile_expert.seed_sizes(db, moment)
            # The preferred size came later than the rows: untouched defaults get theirs, and the rules learn it.
            if profile_expert.seed_preferred(db, moment):
                counts["sizes"] += sum(profile_expert.recompile(db, kind, moment) for kind in profile_expert.KINDS)
                db.commit()
            if any(counts.values()):
                db.commit()
                logger.info(
                    "After the start: %d custom formats, %d quality sizes and %d profiles taken apart",
                    counts["formats"],
                    counts["sizes"],
                    counts["profiles"],
                )
    except Exception:
        logger.exception("Taking the profiles apart failed; they keep everything in one piece")
    # A failure the replacement took care of waits for nobody either (the owner's findings of 22.09.2026).
    try:
        settled = download_store.settle_old_failures(download_store.now())
        if settled:
            logger.info("After the start: %d failed downloads left the problems, nexcrate takes care of them", settled)
    except Exception:
        logger.exception("Settling the failed downloads from before failed; they keep waiting in the problems")
    # Episodes a removed or failed series download kept holding show as loading no more (the owner's finding of
    # 24.09.2026).
    try:
        released = download_store.release_held_episodes(download_store.now())
        if released:
            logger.info("After the start: %d episodes no longer shown as loading or as a problem", released)
    except Exception:
        logger.exception("Looking at the episodes shown as loading failed; they keep their queue state")
    # A failure a later import made moot waits for nobody (the owner's finding of 20.09.2026).
    try:
        closed = download_store.close_moot_failures(download_store.now())
        if closed:
            logger.info("After the start: %d failed downloads closed, an import after them succeeded", closed)
    except Exception:
        logger.exception("Closing the moot failed downloads failed; they keep waiting in the problems")
    # Releases the indexer's grab limit held back are searched again now, not a month later (finding 6 of 22.09.2026).
    try:
        repaired = automatic_scheduler.repair_grab_limits(automatic_clock.now())
        if repaired:
            logger.info("After the start: %d titles held back by a grab limit are searched again", repaired)
    except Exception:
        logger.exception("Planning the titles held back by a grab limit again failed; they keep their plan")
    # Second halves of double episodes that lie in a series folder without an episode.
    try:
        linked = series_halves.link_all()
        if linked:
            logger.info("After the start: %d files linked as the half of a double episode", linked)
    except Exception:
        logger.exception("Linking the halves of double episodes failed; the files stay as they are")
    # Series folders not read yet are read before their versions want anything (decision 23).
    try:
        series_folder_read.resume()
    except Exception:
        logger.exception("Reading the series folders not read yet could not start; they want nothing until read")
    # Creates data/secret.key on the very first start when NEXCRATE_SECRET_KEY is not set.
    get_settings().resolved_secret_key()
    # The escalation keeps its pauses short in the first 15 minutes; the indexers' buckets start empty (step 3c).
    automatic_budget.mark_started(automatic_clock.now())
    runner = jobs.start()
    logger.info("nexcrate %s started", __version__)
    if automatic_clock.SPEED != 1.0:
        logger.warning(
            "The automatic's clock runs %g times as fast (%s); this is for a test bench only",
            automatic_clock.SPEED,
            automatic_clock.SPEED_ENV,
        )
    try:
        yield
    finally:
        await runner.stop()
        await tmdb.close()
        database.release()


# --- Error answers ------------------------------------------------------------ #

_LOCATIONS = {"body", "query", "path", "header", "cookie"}


def _field_names(errors: Any) -> list[str]:
    """Names of the fields that failed, never their values.

    ⚠️ FastAPI's own answer echoes the input, and the input can be a password.
    """
    names: list[str] = []
    for item in errors:
        location = list(item.get("loc", ()))
        if location and location[0] in _LOCATIONS:
            location = location[1:]
        name = "body" if item.get("type") == "json_invalid" else ".".join(str(part) for part in location) or "body"
        if name not in names:
            names.append(name)
    return names


async def validation_error(request: Request, exc: Exception) -> JSONResponse:
    errors = exc.errors() if isinstance(exc, RequestValidationError) else []
    detail = meldung("invalid_input", "The input is not valid.", fields=_field_names(errors))
    return JSONResponse(status_code=422, content=error_body(request.url.path, detail))


def _standard_detail(status: int) -> dict[str, Any]:
    if status == 404:
        return meldung("not_found", "This does not exist, or not any more.")
    if status == 405:
        return meldung("method_not_allowed", "This address does not accept this kind of request.")
    return meldung("request_failed", f"The request was refused (HTTP {status}).", status=status)


def _only_the_frontend_knows(request: Request) -> bool:
    """True when no route but the frontend's catch-all matches this path. That route takes GET for every path, so a
    POST to an address that does not exist would read as 405 instead of 404 once the frontend is mounted."""
    routes = [route for route in request.app.router.routes if getattr(route, "name", None) != FRONTEND_ROUTE]
    return all(route.matches(request.scope)[0] == Match.NONE for route in routes)


async def http_error(request: Request, exc: Exception) -> JSONResponse:
    status = exc.status_code if isinstance(exc, StarletteHTTPException) else 500
    detail = exc.detail if isinstance(exc, StarletteHTTPException) else None
    if status == 405 and is_api_path(request.url.path) and _only_the_frontend_knows(request):
        status, detail, exc = 404, None, None
    if not (isinstance(detail, dict) and "code" in detail):
        detail = _standard_detail(status)
    headers = getattr(exc, "headers", None)
    return JSONResponse(error_body(request.url.path, detail), status_code=status, headers=headers)


async def unexpected_error(request: Request, _exc: Exception) -> JSONResponse:
    """Last resort. Normally the request middleware answers unhandled errors itself."""
    request_id = request.scope.get("state", {}).get("request_id", "-")
    response = internal_error_response(request_id, request.url.path)
    response.headers["X-Request-Id"] = request_id
    return response


# --- OpenAPI ------------------------------------------------------------------- #


def api_routes(target: FastAPI) -> list[RouteContext]:
    """Every API route with its effective path, methods, dependencies and responses.

    ⚠️ Since FastAPI 0.141 included routers are no longer flattened into ``app.routes``.
    Walking ``app.routes`` for ``APIRoute`` finds only routes added to the app itself, and a
    guard built on that would check almost nothing while staying green.
    """
    return [context for context in iter_route_contexts(target.routes) if isinstance(context.original_route, APIRoute)]


def depends_on(dependant: Dependant, target: Any) -> bool:
    return any(sub.call is target or depends_on(sub, target) for sub in dependant.dependencies)


def _has_input(dependant: Dependant) -> bool:
    if (
        dependant.path_params
        or dependant.query_params
        or dependant.header_params
        or dependant.cookie_params
        or dependant.body_params
    ):
        return True
    return any(_has_input(sub) for sub in dependant.dependencies)


def _add_error_code(operation: dict[str, Any], status: int, code: str, model: str = "ErrorResponse") -> None:
    responses = operation.setdefault("responses", {})
    existing = responses.get(str(status), {})
    description = existing.get("description", "")
    codes = _CODE_IN_DESCRIPTION.findall(description) if description.startswith(ERROR_CODES_PREFIX) else []
    if code not in codes:
        codes.append(code)
    responses[str(status)] = {
        "description": describe_codes(codes),
        "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{model}"}}},
    }


def build_openapi(app: FastAPI) -> dict[str, Any]:
    """The OpenAPI document, with the errors every route shares added to each operation.

    Common to all: 500 ``internal_error``. Requests that change something: 403
    ``csrf_header_missing``. Routes with a session: 401 ``not_logged_in``. Routes with input:
    422 ``invalid_input``, in place of FastAPI's validation error, which is never sent.
    """
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        tags=OPENAPI_TAGS,
    )
    base = get_settings().url_base
    if base:
        # Under a sub path "Try it out" in /api/docs sends to it (decision 2).
        schema["servers"] = [{"url": base}]
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    error_schema = ErrorResponse.model_json_schema(ref_template="#/components/schemas/{model}")
    for name, definition in error_schema.pop("$defs", {}).items():
        components.setdefault(name, definition)
    components.setdefault("ErrorResponse", error_schema)
    components.setdefault("ApiError", ApiError.model_json_schema(ref_template="#/components/schemas/{model}"))

    for route in api_routes(app):
        if not route.include_in_schema:
            continue
        operations = schema.get("paths", {}).get(route.path_format, {})
        protected = depends_on(route.dependant, require_session)
        keyed = depends_on(route.dependant, require_api_key)
        has_input = _has_input(route.dependant)
        for method in route.methods:
            operation = operations.get(method.lower())
            if operation is None:
                continue
            common = [(500, "internal_error")]
            if method not in SAFE_METHODS and not keyed:
                common.append((403, "csrf_header_missing"))
            if protected:
                common.append((401, "not_logged_in"))
            if keyed:
                common.extend([(401, "api_key_missing"), (401, "api_key_invalid"), (403, "scope_missing")])
            if has_input:
                common.append((422, "invalid_input"))
            for status, code in common:
                _add_error_code(operation, status, code, "ApiError" if keyed else "ErrorResponse")

    for name in ("HTTPValidationError", "ValidationError"):
        definition = components.pop(name, None)
        if definition is not None and f"#/components/schemas/{name}" in json.dumps(schema):
            components[name] = definition
    app.openapi_schema = schema
    return schema


# --- Frontend ------------------------------------------------------------------- #

#: The name of the route that answers every GET the API does not know.
FRONTEND_ROUTE = "frontend"


def page_with_base(html: str, base: str) -> bytes:
    """The page with ``<base href>`` and the sub path in ``<meta name="nexcrate-base">``.

    The build refers to its files relatively (``./assets/…``); the base makes them resolve from ``/titel/5`` as well as
    from ``/nexcrate/titel/5``. ``base`` is checked by ``config.normalize_url_base`` and holds no quote.
    """
    head = f'<head>\n    <base href="{base}/" />\n    <meta name="nexcrate-base" content="{base}" />'
    return html.replace("<head>", head, 1).encode("utf-8")


def mount_frontend(app: FastAPI, directory: Path, base: str = "") -> Path | None:
    """Serve the built frontend when the directory has an index.html. Returns the index path.

    Every path outside /api that is not a file gets index.html, so the router in the browser
    handles it. ⚠️ A path is resolved and must stay inside the directory: nexmail once served
    every file of its container through ``..``.
    """
    index = directory / "index.html"
    if not index.is_file():
        return None
    root = directory.resolve()
    index_resolved = index.resolve()
    page = page_with_base(index_resolved.read_text(encoding="utf-8"), base)
    page_tag = '"' + hashlib.sha256(page).hexdigest()[:32] + '"'

    @app.get("/{path:path}", include_in_schema=False, response_model=None, name=FRONTEND_ROUTE)
    def frontend(path: str, request: Request) -> FileResponse | JSONResponse | Response:
        if path == "api" or path.startswith("api/"):
            detail = meldung("not_found", "This does not exist, or not any more.")
            return JSONResponse(status_code=404, content=error_body(f"/{path}", detail))
        if path:
            candidate = (root / path).resolve()
            if candidate.is_file() and candidate.is_relative_to(root) and candidate != index_resolved:
                # Files under assets/ carry a content hash in their name and never change.
                headers = {"Cache-Control": "public, max-age=31536000, immutable"} if path.startswith("assets/") else {}
                return FileResponse(candidate, headers=headers)
        # The browser asks again before using the page; with the ETag that is a 304. Without it a
        # browser kept the old page after an update, pointing at files that no longer exist.
        headers = {"Cache-Control": "no-cache", "ETag": page_tag}
        if request.headers.get("if-none-match") == page_tag:
            return Response(status_code=304, headers=headers)
        return Response(content=page, media_type="text/html; charset=utf-8", headers=headers)

    return index_resolved


# --- App ---------------------------------------------------------------------------- #


def create_app(frontend_dir: Path | None = None) -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="nexcrate",
        version=__version__,
        description=API_DESCRIPTION,
        lifespan=lifespan,
        openapi_url="/api/openapi.json",
        docs_url=None,
        redoc_url=None,
    )
    app.add_exception_handler(StarletteHTTPException, http_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(Exception, unexpected_error)

    for router, public_reason in ROUTERS:
        app.include_router(router, dependencies=[] if public_reason else [Depends(require_session)])

    index = mount_frontend(app, frontend_dir if frontend_dir is not None else settings.frontend_dir, settings.url_base)
    app.openapi = lambda: build_openapi(app)  # type: ignore[method-assign]

    # The last one added is the outermost. Security headers wrap everything, the request id wraps
    # the CSRF check, so a refused request has an id and a log line too.
    app.add_middleware(CsrfMiddleware)
    app.add_middleware(RequestContextMiddleware)
    # Under a sub path every request loses it here, before the request id and the CSRF check look at the path.
    app.add_middleware(UrlBaseMiddleware, base=settings.url_base)
    app.add_middleware(SecurityHeadersMiddleware, csp=content_security_policy(index))
    return app


app = create_app()
