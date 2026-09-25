"""Taking over a Radarr or Sonarr connection, and reading its naming (T1 and T2;
the design notes, Ü1 and Ü2).

The check and the takeover run as jobs in the background; ``GET /api/sources/{id}/takeover`` follows the newest one.
Radarr is only read. A taken-over connection answers 409 ``source_taken_over`` here.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .. import crypto
from ..db import SessionLocal
from ..meldungen import error, error_responses
from ..models import Source, VersionDefinition
from ..services import auto_tags_import, folders, importer, naming_series, tags, takeover
from ..services import delay as delay_rules
from ..services.lidarr import LidarrClient
from ..services.music import takeover_music
from ..services.radarr import RadarrClient, RadarrError, SourceUrlInvalid
from ..services.series import takeover_series
from ..services.sonarr import SonarrClient
from .imports import ImportRun, run_out
from .sources import KEY_MAX_LENGTH, RADARR_ERRORS, _clean_key

logger = logging.getLogger("nexcrate.takeover")

router = APIRouter(prefix="/api/sources", tags=["sources"])

PATH_MAX_LENGTH = 4096


class MappingIn(BaseModel):
    remote: str = Field(
        max_length=PATH_MAX_LENGTH,
        description="A root folder as Radarr names it, or a prefix of it. Absolute, at most once per request.",
        examples=["/data/Movies"],
    )
    local: str = Field(
        max_length=PATH_MAX_LENGTH,
        description="The same folder as nexcrate sees it, chosen from GET /api/folders.",
        examples=["/media/Movies"],
    )


class CheckIn(BaseModel):
    mappings: list[MappingIn] = Field(
        default_factory=list,
        max_length=100,
        description="Mappings the owner chose. They win over nexcrate's proposals for the root folders they cover.",
    )


class TakeoverIn(CheckIn):
    accept_missing: bool = Field(
        default=False, description="Take over although files Radarr lists are missing; those versions become wanted."
    )
    accept_queue: bool = Field(
        default=False, description="Take over although Radarr still runs downloads for these movies."
    )
    take_naming: bool = Field(default=False, description="Give the version Radarr's naming as its own naming.")
    keep_as_is: bool = Field(
        default=False,
        description="Keep what the connection brought as it is: every version with a file is left alone and every "
        "episode with a file switched off, so nothing of it is upgraded; what is missing stays wanted, and what "
        "arrives later is upgraded as usual.",
    )
    folder: str | None = Field(
        default=None,
        max_length=PATH_MAX_LENGTH,
        description="A default folder for the version, used only when it has none; checked as "
        "PUT /api/versions/{id}/folder.",
    )


class Progress(BaseModel):
    done: int = Field(description="Files looked at; in `reading_folders` and `companions` the series done.")
    total: int = Field(description="Files to look at, or series.")


class Note(BaseModel):
    """Something Radarr did that nexcrate does differently. A note never blocks."""

    code: str = Field(examples=["radarr_no_rename"])
    values: dict[str, Any] = Field(description="The values of the code.", examples=[{}])


class RootOut(BaseModel):
    remote: str = Field(description="Radarr's root folder.", examples=["/data/Movies4K"])
    local: str | None = Field(description="The folder nexcrate sees for it; null when unmapped.")
    found_by: Literal["same_path", "found", "chosen", "derived", "none"] = Field(
        description="same_path: nexcrate sees the path unchanged; found: sample files found elsewhere; chosen: the "
        "owner's mapping; derived: a root folder without files whose path leads through another root folder's mapping "
        "to an existing folder; none: no mapping."
    )
    movies: int = Field(description="Movies of this source under the root folder; for Sonarr its series.")
    files: int = Field(
        description="Of them with a file. A root folder without files never blocks; its movies without a file keep "
        "`local` as their folder when it is set."
    )
    samples: int = Field(description="Sample files tried, at most 20, spread over the root folder's files.")
    samples_matched: int = Field(
        description="Samples found as regular files of Radarr's size under `local`; for `none` the most any folder "
        "nexcrate found held."
    )


class VersionBrief(BaseModel):
    id: int = Field(description="The version definition this source fed.")
    label: str
    has_profile: bool
    folder: str | None
    folder_proposal: str | None = Field(
        description="Only without a default folder: the local folder of the root folder with the most files, when "
        "nexcrate could use it."
    )


class NamingProblem(BaseModel):
    code: str = Field(description="The first code nexcrate's naming check gives.", examples=["naming_token_unknown"])
    values: dict[str, Any] = Field(description="The values of that code.", examples=[{"token": "{MediaInfo Full}"}])


class NamingProblems(BaseModel):
    movie_folder: NamingProblem | None
    movie_file: NamingProblem | None


class SourceNaming(BaseModel):
    movie_folder: str = Field(
        description="Radarr's movie folder pattern.", examples=["{Movie CleanTitle} ({Release Year})"]
    )
    movie_file: str = Field(
        description="Radarr's movie file pattern when Radarr renames movies; otherwise {Original Title}, which renders "
        "the release name Radarr keeps."
    )
    rename_movies: bool = Field(description="Radarr's switch whether it renames movies at all.")
    colon_replacement: str | None = Field(description="Radarr's colon replacement.", examples=["smart"])
    problems: NamingProblems = Field(
        description="Per pattern the first code nexcrate's check gives, null when it passes."
    )
    can_take: bool = Field(description="Both patterns pass nexcrate's check; the notes do not matter for it.")
    notes: list[Note] = Field(
        description="radarr_no_rename, slash_in_pattern (`pattern`), colon_format (`format`), illegal_characters_kept."
    )


class SeriesNamingProblems(BaseModel):
    series_folder: NamingProblem | None
    season_folder: NamingProblem | None
    specials_folder: NamingProblem | None
    episode_file: NamingProblem | None
    daily_file: NamingProblem | None
    anime_file: NamingProblem | None = None


class SeriesSourceNaming(BaseModel):
    """Sonarr's naming as a series version would take it (decision 13)."""

    series_folder: str = Field(examples=["{Series Title}"])
    season_folder: str = Field(examples=["Season {season:00}"])
    specials_folder: str = Field(examples=["Specials"])
    episode_file: str = Field(
        description="Sonarr's standard episode pattern when Sonarr renames; otherwise {Original Title}, which renders "
        "the release name Sonarr keeps."
    )
    daily_file: str = Field(description="The same for daily shows.")
    anime_file: str = Field(default="", description="The same for anime series (Sonarr's anime episode format).")
    rename_episodes: bool
    colon_replacement: str | None = Field(examples=["smart"])
    multi_episode_style: str | None = Field(
        description="extend, duplicate, repeat, scene, range or prefixed_range. nexcrate keeps one style for all "
        "series; the note style_differs says when Sonarr's is another.",
        examples=["prefixed_range"],
    )
    problems: SeriesNamingProblems
    can_take: bool = Field(description="Every pattern passes nexcrate's check.")
    notes: list[Note] = Field(
        description="sonarr_no_rename, slash_in_pattern (`pattern`), colon_format (`format`), illegal_characters_kept, "
        "style_differs (`style`)."
    )


class MusicNamingProblems(BaseModel):
    artist_folder: NamingProblem | None
    album_folder: NamingProblem | None
    track_file: NamingProblem | None
    multi_disc_file: NamingProblem | None


class MusicSourceNaming(BaseModel):
    """Lidarr's naming as the music naming would take it (the owner's finding of 20.09.2026)."""

    artist_folder: str = Field(examples=["{Artist Name}"])
    album_folder: str = Field(examples=["{Album Title} ({Release Year})"])
    track_file: str = Field(
        description="Lidarr's standard track pattern when Lidarr renames; otherwise {Original Filename}, which "
        "renders the name the file came with."
    )
    multi_disc_file: str = Field(description="The same for an album with several media.")
    rename_tracks: bool
    colon_replacement: str | None = Field(examples=["smart"])
    problems: MusicNamingProblems
    can_take: bool = Field(description="Every pattern passes nexcrate's check.")
    notes: list[Note] = Field(
        description="lidarr_no_rename, slash_in_pattern (`pattern`), colon_format (`format`), illegal_characters_kept."
    )


class Taken(BaseModel):
    versions: int = Field(description="Versions that became nexcrate's own.")
    with_file: int = Field(description="Of them with a file.")
    missing: int = Field(description="Of them whose file was missing; they are wanted now.")
    titles: int = Field(description="Titles handed to the TMDB fill.")


class TakeoverResult(BaseModel):
    app: Literal["radarr", "sonarr", "lidarr"] = Field(default="radarr", description="The app of the source.")
    kept: dict[str, int] | None = Field(
        default=None,
        description="With `keep_as_is`, after the takeover: `movies` and `albums` left alone, `episodes` switched off.",
    )
    data_from: Literal["fresh", "stored"] = Field(description="fresh: read just now; stored: Radarr did not answer.")
    read_at: datetime | None = Field(description="UTC. When the data used was read.")
    movies: int
    with_file: int
    roots: list[RootOut]
    files_found: int = Field(description="Files found, those of another size included.")
    files_missing: int
    files_other_size: int = Field(description="Files found with another size than Radarr's; the size on disk counts.")
    missing_examples: list[str] = Field(description="Up to 20 missing files, relative to their root folder.")
    series: int | None = Field(default=None, description="Sonarr: its series. Null for Radarr.")
    albums: int | None = Field(default=None, description="Lidarr: its albums. Null otherwise.")
    album_files: int | None = Field(default=None, description="Lidarr: its track files. Null otherwise.")
    files_outside: int | None = Field(
        default=None,
        description="Lidarr: files outside their album's folder (the folder most of its files lie in). They stay on "
        "disk; their tracks count as missing until the owner moves them in and reads the folder again.",
    )
    wanted: int | None = Field(
        default=None, description="Lidarr: watched albums without files, searched by plan after the takeover."
    )
    incomplete: int | None = Field(default=None, description="Lidarr: albums lacking tracks of their target release.")
    episode_files: int | None = Field(default=None, description="Sonarr: its episode files. Null for Radarr.")
    unclear: int | None = Field(
        default=None,
        description="Sonarr: files no TMDB episode matched. After the takeover they are unclear files of the series "
        "(Ü3). Null for Radarr.",
    )
    would_upgrade: int | None = Field(
        default=None,
        description="Sonarr: found files nexcrate's rules judge below the target. Null for Radarr or without series "
        "rules.",
    )
    queue: int = Field(description="Radarr's queue items for movies of this source.")
    version: VersionBrief
    naming: SourceNaming | SeriesSourceNaming | None = Field(
        description="The naming when the app answered, null otherwise: movie patterns for Radarr, series patterns for "
        "Sonarr."
    )
    notes: list[Note] = Field(
        description="From the media management: radarr_recycle_bin (`days`), radarr_extra_files, "
        "radarr_hardlinks_off, radarr_file_date, radarr_permissions; for Sonarr the same with sonarr_. Empty when it "
        "could not be read."
    )
    blockers: list[Literal["root_unmapped", "files_missing", "queue_active"]] = Field(
        description="What a takeover refuses without acceptance."
    )
    taken: Taken | None = Field(description="After a takeover: what was taken over. Null for a check.")
    companions: dict[str, int] | None = Field(
        default=None,
        description="After a takeover: the release.nex files written in its phase `companions`, counted per state "
        "(written, current, not_writable and the other states of the companion files; `off` when the switch is off). "
        "Null for a check.",
    )


class TakeoverJob(BaseModel):
    id: int
    source_id: int
    kind: Literal["check", "takeover"]
    state: Literal["running", "done", "failed"]
    phase: Literal["reading", "mapping", "files", "saving", "reading_folders", "companions"] | None = Field(
        description="Null unless running. `reading_folders` (Sonarr): the takeover is committed and the series "
        "folders are read for files Sonarr did not know. `companions`: the takeover is committed and release.nex is "
        "written."
    )
    progress: Progress | None = Field(description="Files looked at; null before that phase.")
    started_at: datetime = Field(description="UTC.")
    finished_at: datetime | None = Field(description="UTC. Null while running.")
    error_code: str | None = Field(
        description="Why a failed job failed: takeover_needs_import, takeover_root_unmapped, takeover_files_missing, "
        "takeover_queue_active, a Radarr code, a naming code, a folder code. Null unless failed; nothing was changed."
    )
    error_values: dict[str, Any] | None = Field(description="The values of the error code. Null unless failed.")
    result: TakeoverResult | None = Field(description="Null unless done.")


_JOB_ERRORS = (
    (404, "not_found"),
    (409, "source_taken_over"),
    (409, "source_app_unsupported"),
    (409, "import_running"),
    (409, "takeover_running"),
    (422, "takeover_mapping_invalid"),
)


def _start(source_id: int, kind: str, payload: CheckIn | None) -> TakeoverJob:
    pairs = [(mapping.remote, mapping.local) for mapping in (payload.mappings if payload is not None else [])]
    folder: str | None = None
    with SessionLocal() as db:
        source = db.get(Source, source_id)
        if source is None:
            raise error("not_found", "This does not exist, or not any more.", 404)
        if source.taken_over_at is not None:
            raise error("source_taken_over", "This connection was taken over. nexcrate no longer reads it.", 409)
        if source.app not in ("radarr", "sonarr", "lidarr"):
            raise error("source_app_unsupported", "This works for Radarr, Sonarr and Lidarr connections only.", 409)
        try:
            mappings = takeover.mappings_from(pairs)
        except takeover.MappingInvalid as exc:
            raise error(
                "takeover_mapping_invalid",
                "A mapping is not valid: the remote path must be absolute and given once, the local folder one "
                "nexcrate sees.",
                422,
                remote=exc.remote,
            ) from exc
        if isinstance(payload, TakeoverIn) and payload.folder and payload.folder.strip():
            definition = db.get(VersionDefinition, source.version_id)
            if definition is not None and definition.folder is None:
                folder = folders.check_version_folder(db, definition, payload.folder)
    request = takeover.Request(
        mappings=mappings,
        accept_missing=bool(getattr(payload, "accept_missing", False)),
        accept_queue=bool(getattr(payload, "accept_queue", False)),
        take_naming=bool(getattr(payload, "take_naming", False)),
        folder=folder,
        keep_as_is=bool(getattr(payload, "keep_as_is", False)),
    )
    try:
        job = takeover.start(source_id, kind, request)
    except takeover.TakeoverRunning as exc:
        raise error("takeover_running", "A check or takeover of this connection is already running.", 409) from exc
    except importer.ImportRunning as exc:
        raise error("import_running", "An import is already running for this source.", 409) from exc
    except importer.SourceMissing as exc:
        raise error("not_found", "This does not exist, or not any more.", 404) from exc
    except importer.SourceTakenOver as exc:
        raise error("source_taken_over", "This connection was taken over. nexcrate no longer reads it.", 409) from exc
    return TakeoverJob.model_validate(job)


@router.post(
    "/{source_id}/takeover/check",
    status_code=202,
    response_model=TakeoverJob,
    summary="Check what a takeover of a Radarr source would do",
    description=(
        "Starts a check in the background and answers at once with the running job; follow it with "
        "`GET /api/sources/{source_id}/takeover`. The check reads Radarr as an import does (it is an import run), or "
        "uses the stored state when Radarr does not answer. It maps Radarr's root folders to folders nexcrate sees by "
        "finding up to 20 sample files of exactly Radarr's size, looks at every file, counts Radarr's queue and reads "
        "Radarr's naming and media management. Nothing else changes; nothing on disk is written."
    ),
    responses=error_responses(*_JOB_ERRORS),
)
def check_takeover(source_id: int, payload: CheckIn | None = None) -> TakeoverJob:
    return _start(source_id, "check", payload)


@router.post(
    "/{source_id}/takeover",
    status_code=202,
    response_model=TakeoverJob,
    summary="Take over a Radarr source and end the connection",
    description=(
        "Starts the takeover in the background and answers at once with the running job. It reads and tests as the "
        "check does, then refuses an unmapped root folder with files (`takeover_root_unmapped`), missing files without "
        "`accept_missing` (`takeover_files_missing`) and running downloads without `accept_queue` "
        "(`takeover_queue_active`). Otherwise, in one transaction, every version of the source becomes nexcrate's own "
        "with its file, the source keeps a record with `taken_over_at` and its API key, encrypted, for undoing, and "
        "the titles get TMDB's data by and by. Radarr is only read; nothing on disk changes before the transaction. "
        "Afterwards the phase `companions` writes a release.nex into the movie folder of every version with a file; "
        "a folder without write permission is counted, never a failure of the takeover."
    ),
    responses=error_responses(
        *_JOB_ERRORS, (404, "folder_not_visible"), (422, "folder_not_writable"), (409, "folder_in_use")
    ),
)
def take_over(source_id: int, payload: TakeoverIn | None = None) -> TakeoverJob:
    return _start(source_id, "takeover", payload)


class UndoIn(BaseModel):
    api_key: str | None = Field(
        default=None,
        max_length=KEY_MAX_LENGTH,
        description="Radarr's API key. Needed only when none is stored (takeovers before 14.09.2026 deleted it); a "
        "given key replaces the stored one.",
    )


@router.post(
    "/{source_id}/takeover/undo",
    status_code=202,
    response_model=ImportRun,
    summary="Undo the takeover of a Radarr source",
    description=(
        "Tests Radarr with the stored key, or with `api_key`. Then, in one transaction, the source becomes a "
        "connection nexcrate reads again (`taken_over_at` null, a given key stored) and every version the takeover "
        "took gets a history entry `takeover_undone`. Afterwards an import starts, and the answer is that running "
        "import as "
        "`POST /api/sources/{source_id}/import` gives it; the import feeds the versions from Radarr again. What "
        "nexcrate loaded since the takeover gives way to Radarr's view in the database; files on disk and the "
        "version's naming stay. The release.nex files nexcrate wrote for these versions are removed after the commit, "
        "unchanged files only. Refused while nexcrate has downloads for the version. When Radarr does not answer, "
        "nothing changes."
    ),
    responses=error_responses(
        (404, "not_found"),
        (409, "source_not_taken_over"),
        (409, "import_running"),
        (409, "takeover_running"),
        (409, "takeover_downloads_active"),
        (422, "source_key_missing"),
        (422, "source_url_invalid"),
        (422, "invalid_input"),
        *RADARR_ERRORS,
    ),
)
async def undo_takeover(source_id: int, payload: UndoIn | None = None) -> ImportRun:
    given = ((payload.api_key if payload is not None else None) or "").strip()
    key_given = _clean_key(given) if given else None
    try:
        target = await asyncio.to_thread(takeover.claim_for_undo, source_id)
    except importer.SourceMissing as exc:
        raise error("not_found", "This does not exist, or not any more.", 404) from exc
    except takeover.NotTakenOver as exc:
        raise error("source_not_taken_over", "This connection is not taken over.", 409) from exc
    except takeover.TakeoverRunning as exc:
        raise error("takeover_running", "A check or takeover of this connection is already running.", 409) from exc
    except importer.ImportRunning as exc:
        raise error("import_running", "An import is already running for this source.", 409) from exc
    # ⚠️ The claim is held from here: every way out before ``takeover.undo`` gives it up.
    try:
        key = key_given or target.stored_key
        if not key:
            raise error("source_key_missing", "No readable API key is stored for this source. Please enter it.", 422)
        if target.pending:
            raise error(
                "takeover_downloads_active",
                "nexcrate still has downloads for this version. Let them finish or remove them first.",
                409,
                count=target.pending,
            )
        try:
            if target.app == "sonarr":
                async with SonarrClient(target.url, key) as sonarr:
                    await sonarr.system_status()
            elif target.app == "lidarr":
                async with LidarrClient(target.url, key) as lidarr:
                    await lidarr.system_status()
            else:
                async with RadarrClient(target.url, key) as radarr:
                    await radarr.system_status()
        except RadarrError as exc:
            logger.info("Undoing the takeover of source %d stopped: %s", source_id, exc.code)
            raise exc.http() from exc
        except SourceUrlInvalid as exc:
            raise error(
                "source_url_invalid",
                "This address does not work. It has to start with http:// or https:// and must not contain a user "
                "name or password.",
                422,
            ) from exc
    except BaseException:
        importer.release(source_id)
        raise
    try:
        run = await asyncio.to_thread(takeover.undo, source_id, key_given)
    except importer.SourceMissing as exc:
        raise error("not_found", "This does not exist, or not any more.", 404) from exc
    except takeover.NotTakenOver as exc:
        raise error("source_not_taken_over", "This connection is not taken over.", 409) from exc
    except takeover.DownloadsActive as exc:
        raise error(
            "takeover_downloads_active",
            "nexcrate still has downloads for this version. Let them finish or remove them first.",
            409,
            count=exc.count,
        ) from exc
    return run_out(run)


@router.get(
    "/{source_id}/takeover",
    response_model=TakeoverJob | None,
    summary="Follow the check or takeover of a Radarr or Sonarr source",
    description=(
        "The newest check or takeover of this source that runs or ended less than 30 minutes ago, or null when there "
        "is none. Jobs live in memory; after a restart there is none. 404 `not_found` only for an unknown source."
    ),
    responses=error_responses((404, "not_found")),
)
def read_takeover(source_id: int) -> TakeoverJob | None:
    with SessionLocal() as db:
        if db.get(Source, source_id) is None:
            raise error("not_found", "This does not exist, or not any more.", 404)
    job = takeover.latest(source_id)
    # No job is an answer, not an error: the dialog asks on every opening (18.09.2026, a 404 in the console).
    return None if job is None else TakeoverJob.model_validate(job)


@dataclass(frozen=True)
class _Row:
    url: str
    api_key: str
    taken_over: bool
    app: str = "radarr"


def _series_style() -> str:
    with SessionLocal() as db:
        return naming_series.load(db).style


def _row(source_id: int) -> _Row | None:
    with SessionLocal() as db:
        source = db.get(Source, source_id)
        if source is None:
            return None
        return _Row(url=source.url, api_key=source.api_key, taken_over=source.taken_over_at is not None, app=source.app)


class SourceDelay(BaseModel):
    """The delay profile of a connection as a version could take it (D4)."""

    app: str = Field(description="radarr, sonarr or lidarr.")
    kind: str = Field(description="movie, series or album: the versions this rule is for.")
    rule: dict[str, Any] | None = Field(
        description="The profile without tags, in the fields of `PUT /api/versions/{version_id}/delay`; null when the "
        "app lists none that can be a rule here."
    )
    tagged: int = Field(description="Profiles bound to tags there.")
    tagged_rules: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Those profiles as rules for titles with tags, in the app's order, each with `tags` by name: "
        "the field `tagged` of `PUT /api/versions/{version_id}/delay`.",
    )
    shortened: bool = Field(description="A waiting time above 30 days was cut down to 30 days.")


@router.get(
    "/{source_id}/delay",
    response_model=SourceDelay,
    summary="Read the delay profile of a Radarr, Sonarr or Lidarr source",
    description=(
        "Reads the app's delay profiles and answers the one without tags as a delay rule: preferred protocol, a "
        "protocol switched off, minutes per protocol, the two exceptions. Nothing is stored; taking it over is "
        "`PUT /api/versions/{version_id}/delay`. A taken-over connection answers too: only its settings are read."
    ),
    responses=error_responses(
        (404, "not_found"),
        (409, "source_app_unsupported"),
        (422, "source_key_missing"),
        (422, "source_url_invalid"),
        *RADARR_ERRORS,
    ),
)
async def read_source_delay(source_id: int) -> SourceDelay:
    row = await asyncio.to_thread(_row, source_id)
    if row is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    if row.app not in ("radarr", "sonarr", "lidarr"):
        raise error("source_app_unsupported", "This works for Radarr, Sonarr and Lidarr connections only.", 409)
    key = await asyncio.to_thread(crypto.decrypt, row.api_key)
    if not key:
        raise error(
            "source_key_missing", "The stored API key of this source cannot be read. Please enter it again.", 422
        )
    try:
        if row.app == "sonarr":
            async with SonarrClient(row.url, key) as sonarr:
                listed = await sonarr.delay_profiles()
                names = await sonarr.tags() if _has_tags(listed) else {}
        elif row.app == "lidarr":
            async with LidarrClient(row.url, key) as lidarr:
                listed = await lidarr.delay_profiles()
                names = await lidarr.tags() if _has_tags(listed) else {}
        else:
            async with RadarrClient(row.url, key) as radarr:
                listed = await radarr.delay_profiles()
                names = await radarr.tags() if _has_tags(listed) else {}
    except RadarrError as exc:
        logger.info("Reading the delay profiles of source %d failed: %s", source_id, exc.code)
        raise exc.http() from exc
    except SourceUrlInvalid as exc:
        raise error(
            "source_url_invalid",
            "This address does not work. It has to start with http:// or https:// and must not contain "
            "a user name or password.",
            422,
        ) from exc
    read = delay_rules.from_arr(listed)
    kind = {"radarr": "movie", "sonarr": "series", "lidarr": "album"}[row.app]
    logger.info("Source %d: delay profiles read, %d bound to tags", source_id, read.tagged)
    return SourceDelay(
        app=row.app,
        kind=kind,
        rule=read.rule.as_dict() if read.rule is not None else None,
        tagged=read.tagged,
        shortened=read.shortened,
        tagged_rules=[
            {**rule.as_dict(), "tags": labels}
            for ids, rule in read.tagged_rules
            if (labels := [label for label in (tags.clean_or_none(names.get(tag_id, "")) for tag_id in ids) if label])
        ],
    )


def _has_tags(listed: Any) -> bool:
    return isinstance(listed, list) and any(isinstance(entry, dict) and entry.get("tags") for entry in listed)


class RuleNote(BaseModel):
    code: str = Field(
        description="Why a rule cannot be taken or what was left out: no_tags, no_conditions, profile_unknown, "
        "root_folder_unknown, language_unknown, status_unknown, series_type_unknown, tag_unknown, range_invalid, "
        "condition_empty, condition_unknown, condition_unsupported (the kind has no such condition here, as genre for "
        "music), metadata_profile (left out)."
    )
    value: str = Field(description="What the app named, for the sentence: a profile, a path, a language.")


class SourceAutoTag(BaseModel):
    name: str
    tags: list[str] = Field(description="The tags it gives, by name.")
    remove_automatically: bool
    conditions: list[dict[str, Any]] = Field(description="In the fields of `POST /api/auto-tags`.")
    importable: bool = Field(description="It can be taken: nothing unknown in it, and its name is free.")
    exists: bool = Field(description="The kind has a rule of this name already; that one stays.")
    problems: list[RuleNote]
    dropped: list[RuleNote] = Field(description="Conditions left out; the rule is taken without them.")


class SourceAutoTags(BaseModel):
    app: str
    kind: str = Field(description="movie, series or album: the kind of `POST /api/auto-tags`.")
    rules: list[SourceAutoTag]


@router.get(
    "/{source_id}/auto-tags",
    response_model=SourceAutoTags,
    summary="Read the auto tagging rules of a Radarr, Sonarr or Lidarr source",
    description=(
        "Reads the app's auto tagging rules and answers each as a rule of `POST /api/auto-tags`, with whether it can "
        "be taken and why not. Nothing is stored; taking a rule over is `POST /api/auto-tags`. A rule with a condition "
        "nexcrate cannot rebuild is not offered, Lidarr's metadata profile is left out and named. A taken-over "
        "connection answers too: only its settings are read."
    ),
    responses=error_responses(
        (404, "not_found"),
        (409, "source_app_unsupported"),
        (422, "source_key_missing"),
        (422, "source_url_invalid"),
        *RADARR_ERRORS,
    ),
)
async def read_source_auto_tags(source_id: int) -> SourceAutoTags:
    row = await asyncio.to_thread(_row, source_id)
    if row is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    if row.app not in ("radarr", "sonarr", "lidarr"):
        raise error("source_app_unsupported", "This works for Radarr, Sonarr and Lidarr connections only.", 409)
    key = await asyncio.to_thread(crypto.decrypt, row.api_key)
    if not key:
        raise error(
            "source_key_missing", "The stored API key of this source cannot be read. Please enter it again.", 422
        )
    try:
        if row.app == "sonarr":
            async with SonarrClient(row.url, key) as sonarr:
                rules, schema = await sonarr.auto_tagging()
                names, profiles = (await sonarr.tags(), await sonarr.quality_profiles()) if rules else ({}, [])
        elif row.app == "lidarr":
            async with LidarrClient(row.url, key) as lidarr:
                rules, schema = await lidarr.auto_tagging()
                names, profiles = (await lidarr.tags(), await lidarr.quality_profiles()) if rules else ({}, [])
        else:
            async with RadarrClient(row.url, key) as radarr:
                rules, schema = await radarr.auto_tagging()
                names, profiles = (await radarr.tags(), await radarr.quality_profiles()) if rules else ({}, [])
    except RadarrError as exc:
        logger.info("Reading the auto tagging of source %d failed: %s", source_id, exc.code)
        raise exc.http() from exc
    except SourceUrlInvalid as exc:
        raise error(
            "source_url_invalid",
            "This address does not work. It has to start with http:// or https:// and must not contain "
            "a user name or password.",
            422,
        ) from exc
    kind = {"radarr": "movie", "sonarr": "series", "lidarr": "album"}[row.app]

    def translate() -> list[auto_tags_import.Candidate]:
        with SessionLocal() as db:
            return auto_tags_import.from_arr(
                db, kind, rules, schema, names, auto_tags_import.names_of_profiles(profiles)
            )

    found = await asyncio.to_thread(translate)
    logger.info(
        "Source %d: %d auto tagging rules read, %d can be taken",
        source_id,
        len(found),
        sum(1 for rule in found if rule.importable),
    )
    return SourceAutoTags(
        app=row.app,
        kind=kind,
        rules=[
            SourceAutoTag(
                name=rule.name,
                tags=rule.tags,
                remove_automatically=rule.remove_automatically,
                conditions=rule.conditions,
                importable=rule.importable,
                exists=rule.exists,
                problems=[RuleNote(**note) for note in rule.problems],
                dropped=[RuleNote(**note) for note in rule.dropped],
            )
            for rule in found
        ],
    )


@router.get(
    "/{source_id}/naming",
    response_model=SourceNaming | SeriesSourceNaming | MusicSourceNaming,
    summary="Read the naming of a Radarr, Sonarr or Lidarr source",
    description=(
        "Reads Radarr's movie folder and movie file patterns, its rename switch and its colon replacement, and names "
        "per pattern the first code nexcrate's naming check gives. Nothing is stored; taking the naming over is "
        "`PUT /api/naming/versions/{version_id}`. For Sonarr the five series patterns, the rename switch, the multi "
        "episode style and the colon replacement; taking them over is `PUT /api/naming/series/versions/{version_id}`. "
        "For Lidarr the four music patterns and its rename switch; taking them over is `PUT /api/naming`. A "
        "taken-over connection answers too: its key stays stored, and only its settings are read."
    ),
    responses=error_responses(
        (404, "not_found"),
        (409, "source_app_unsupported"),
        (422, "source_key_missing"),
        (422, "source_url_invalid"),
        *RADARR_ERRORS,
    ),
)
async def read_source_naming(source_id: int) -> SourceNaming | SeriesSourceNaming | MusicSourceNaming:
    row = await asyncio.to_thread(_row, source_id)
    if row is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    if row.app not in ("radarr", "sonarr", "lidarr"):
        raise error("source_app_unsupported", "This works for Radarr, Sonarr and Lidarr connections only.", 409)
    key = await asyncio.to_thread(crypto.decrypt, row.api_key)
    if not key:
        raise error(
            "source_key_missing", "The stored API key of this source cannot be read. Please enter it again.", 422
        )
    try:
        if row.app == "sonarr":
            async with SonarrClient(row.url, key) as sonarr:
                series_config = await sonarr.naming()
            style = await asyncio.to_thread(_series_style)
            series_answer = SeriesSourceNaming.model_validate(takeover_series.source_naming(series_config, style))
            logger.info("Source %d: series naming read, can be taken: %s", source_id, series_answer.can_take)
            return series_answer
        if row.app == "lidarr":
            async with LidarrClient(row.url, key) as lidarr:
                music_config = await lidarr.naming()
            music_answer = MusicSourceNaming.model_validate(takeover_music.source_naming(music_config))
            logger.info("Source %d: music naming read, can be taken: %s", source_id, music_answer.can_take)
            return music_answer
        async with RadarrClient(row.url, key) as radarr:
            config = await radarr.naming()
    except RadarrError as exc:
        # A LidarrError is one of these: all three sources fail alike.
        logger.info("Reading the naming of source %d failed: %s", source_id, exc.code)
        raise exc.http() from exc
    except SourceUrlInvalid as exc:
        raise error(
            "source_url_invalid",
            "This address does not work. It has to start with http:// or https:// and must not contain "
            "a user name or password.",
            422,
        ) from exc
    answer = SourceNaming.model_validate(takeover.source_naming(config))
    logger.info("Source %d: naming read, can be taken: %s", source_id, answer.can_take)
    return answer
