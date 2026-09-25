"""Naming of imported movies and filed episodes: patterns with Radarr's and Sonarr's tokens, the umlaut setting.

The defaults are TRaSH's recommendations for Radarr and Sonarr. Patterns are checked on save and in the preview;
existing files are never renamed. Since the takeover plan (T2) a movie version may have both patterns of its own;
without them it uses the default patterns. Since S4 (S4.2) series have five patterns, a
multi-episode style, and per series version its own patterns (each one alone) and the numbering of the names. The
umlaut rule is one for all.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as OrmSession

from ..deps import DbSession
from ..meldungen import error, error_responses
from ..models import VersionDefinition
from ..services import naming, naming_music, naming_series
from ..services.schreibweisen import nfc

logger = logging.getLogger("nexcrate.naming")

router = APIRouter(prefix="/api/naming", tags=["naming"])


class NamingDefaults(BaseModel):
    movie_folder: str
    movie_file: str


class NamingVersion(BaseModel):
    version_id: int = Field(description="The movie version definition.")
    label: str = Field(examples=["4K"])
    own: bool = Field(description="Whether the version has patterns of its own; false means the default patterns.")
    movie_folder: str = Field(description="The movie folder pattern this version uses: its own, or the default one.")
    movie_file: str = Field(description="The movie file pattern this version uses: its own, or the default one.")
    source_id: int | None = Field(
        description="A Radarr connection that feeds this version and is not taken over, to take its naming from; "
        "null otherwise."
    )


class SeriesPatterns(BaseModel):
    series_folder: str = Field(max_length=naming.PATTERN_MAX_LENGTH, examples=[naming_series.DEFAULTS["series_folder"]])
    season_folder: str = Field(max_length=naming.PATTERN_MAX_LENGTH, examples=[naming_series.DEFAULTS["season_folder"]])
    specials_folder: str = Field(
        max_length=naming.PATTERN_MAX_LENGTH, examples=[naming_series.DEFAULTS["specials_folder"]]
    )
    episode_file: str = Field(max_length=naming.PATTERN_MAX_LENGTH, examples=[naming_series.DEFAULTS["episode_file"]])
    daily_file: str = Field(max_length=naming.PATTERN_MAX_LENGTH, examples=[naming_series.DEFAULTS["daily_file"]])
    anime_file: str = Field(
        default=naming_series.DEFAULTS["anime_file"],
        max_length=naming.PATTERN_MAX_LENGTH,
        description="The file of an anime series; the only pattern that takes {absolute}. Left out: the default.",
        examples=[naming_series.DEFAULTS["anime_file"]],
    )


class SeriesOwnPatterns(BaseModel):
    series_folder: str | None = Field(default=None, max_length=naming.PATTERN_MAX_LENGTH)
    season_folder: str | None = Field(default=None, max_length=naming.PATTERN_MAX_LENGTH)
    specials_folder: str | None = Field(default=None, max_length=naming.PATTERN_MAX_LENGTH)
    episode_file: str | None = Field(default=None, max_length=naming.PATTERN_MAX_LENGTH)
    daily_file: str | None = Field(default=None, max_length=naming.PATTERN_MAX_LENGTH)
    anime_file: str | None = Field(default=None, max_length=naming.PATTERN_MAX_LENGTH)


MultiEpisodeStyle = Literal["extend", "duplicate", "repeat", "scene", "range", "prefixed_range"]
EpisodeNumbering = Literal["tmdb", "tvdb"]


class SeriesNamingVersion(BaseModel):
    version_id: int = Field(description="The series version definition.")
    label: str = Field(examples=["Full-HD"])
    own: SeriesOwnPatterns = Field(description="The version's own patterns; null where it uses the default.")
    patterns: SeriesPatterns = Field(description="The patterns this version files with.")
    episode_numbering: EpisodeNumbering = Field(
        description="The numbers in the names: tmdb (the default; Plex and Jellyfin count so) or tvdb (Emby). A file "
        "whose episodes lack a TVDB number is named after TMDB."
    )
    source_id: int | None = Field(
        default=None,
        description="A Sonarr connection of this version to take its naming from; null when there is none. A "
        "taken-over connection counts: only its settings are read.",
    )


class SeriesNamingOut(SeriesPatterns):
    multi_episode_style: MultiEpisodeStyle = Field(
        description="How a file of several episodes is named, as Sonarr's styles: S01E01-02-03, S01E01.S01E02, "
        "S01E01E02E03, S01E01-E02-E03, S01E01-03 or S01E01-E03 (prefixed_range, the default)."
    )
    defaults: SeriesPatterns = Field(description="TRaSH's recommendation for Sonarr, the series folder without an id.")
    tokens: list[str] = Field(description="The tokens nexcrate fills for series, in Sonarr's names.")
    folder_additions: dict[str, str] = Field(
        description="Ids for the series folder by media server, measured with Plex, Jellyfin and Emby. Each fits its "
        "own server only."
    )
    versions: list[SeriesNamingVersion] = Field(description="One entry per series version, in the order of their ids.")


class MusicPatterns(BaseModel):
    artist_folder: str = Field(max_length=naming.PATTERN_MAX_LENGTH)
    album_folder: str = Field(max_length=naming.PATTERN_MAX_LENGTH)
    track_file: str = Field(max_length=naming.PATTERN_MAX_LENGTH, description="An album with one medium.")
    multi_disc_file: str = Field(max_length=naming.PATTERN_MAX_LENGTH, description="An album with several media.")


class MusicNamingOut(MusicPatterns):
    defaults: MusicPatterns = Field(description="Flat album folders, the medium in the name only with several.")
    tokens: list[str] = Field(description="The tokens nexcrate fills for music, in Lidarr's names.")
    source_id: int | None = Field(
        default=None,
        description="A Lidarr connection to take the naming from; null when there is none. A taken-over connection "
        "counts: only its settings are read.",
    )


class MusicPreviewIn(MusicPatterns):
    umlauts: Literal["keep", "replace"] = "keep"


class MusicPreviewOut(MusicPatterns):
    """The patterns filled for a made-up album: its second medium's third track."""


class NamingOut(BaseModel):
    movie_folder: str
    movie_file: str
    umlauts: str = Field(description="keep or replace.")
    defaults: NamingDefaults = Field(description="TRaSH's recommendation for Radarr.")
    tokens: list[str] = Field(description="The tokens nexcrate fills, in Radarr's names.")
    versions: list[NamingVersion] = Field(description="One entry per movie version, in the order of their ids.")
    series: SeriesNamingOut = Field(description="The naming of series (S4).")
    music: MusicNamingOut = Field(description="The naming of music (decision 14).")


class SeriesNamingIn(SeriesPatterns):
    multi_episode_style: MultiEpisodeStyle = "prefixed_range"


class NamingIn(BaseModel):
    movie_folder: str = Field(max_length=naming.PATTERN_MAX_LENGTH)
    movie_file: str = Field(max_length=naming.PATTERN_MAX_LENGTH)
    umlauts: Literal["keep", "replace"]
    series: SeriesNamingIn | None = Field(
        default=None, description="The default patterns of series and the style; left out, they stay as they are."
    )
    music: MusicPatterns | None = Field(
        default=None, description="The patterns of music; left out, they stay as they are."
    )


class SeriesVersionNamingIn(SeriesOwnPatterns):
    episode_numbering: EpisodeNumbering = "tmdb"


class SeriesPreviewIn(SeriesNamingIn):
    umlauts: Literal["keep", "replace"] = "keep"
    episode_numbering: EpisodeNumbering = "tmdb"


class SeriesPreviewExample(BaseModel):
    key: str = Field(
        description="episode, double, tba, daily, special, special_numbering (TMDB S00E09, TVDB S00E03) or "
        "special_placeholder (a German placeholder name, named after the English one)."
    )
    season_folder: str
    file: str


class SeriesPreviewOut(BaseModel):
    series_folder: str
    examples: list[SeriesPreviewExample]


class NamingVersionIn(BaseModel):
    movie_folder: str = Field(max_length=naming.PATTERN_MAX_LENGTH, description="The movie folder pattern.")
    movie_file: str = Field(max_length=naming.PATTERN_MAX_LENGTH, description="The movie file pattern.")


class PreviewOut(BaseModel):
    folder: str = Field(description="The folder name for a made-up movie.")
    file: str = Field(description="The file name for a made-up release of it, with extension.")


def _series_out(db: OrmSession) -> SeriesNamingOut:
    current = naming_series.load(db)
    return SeriesNamingOut(
        **{which: current.pattern(which) for which in naming_series.PATTERNS},
        multi_episode_style=current.style,  # type: ignore[arg-type]
        defaults=SeriesPatterns(**naming_series.DEFAULTS),
        tokens=list(naming_series.TOKENS),
        folder_additions=dict(naming_series.FOLDER_ADDITIONS),
        versions=[SeriesNamingVersion.model_validate(entry) for entry in naming_series.version_entries(db)],
    )


def _music_out(db: OrmSession) -> MusicNamingOut:
    current = naming_music.load(db)
    # One music version, so one Lidarr at most; the first one answers.
    sources = naming.setting_sources(db, "lidarr")
    return MusicNamingOut(
        **{which: current.pattern(which) for which in naming_music.PATTERNS},
        defaults=MusicPatterns(**naming_music.DEFAULTS),
        tokens=list(naming_music.TOKENS),
        source_id=next(iter(sources.values()), None),
    )


def _checked_music(patterns: dict[str, str]) -> dict[str, str]:
    checked: dict[str, str] = {}
    for which in naming_music.PATTERNS:
        text = nfc(patterns[which])
        try:
            naming_music.check(text, which)
        except naming.NamingError as exc:
            raise exc.http() from exc
        checked[which] = text
    return checked


def _out(db: OrmSession, current: naming.Naming) -> NamingOut:
    return NamingOut(
        movie_folder=current.movie_folder,
        movie_file=current.movie_file,
        umlauts=current.umlauts,
        defaults=NamingDefaults(movie_folder=naming.DEFAULT_MOVIE_FOLDER, movie_file=naming.DEFAULT_MOVIE_FILE),
        tokens=list(naming.TOKENS),
        versions=[NamingVersion.model_validate(entry) for entry in naming.version_entries(db)],
        series=_series_out(db),
        music=_music_out(db),
    )


def _checked_series(patterns: dict[str, str | None]) -> dict[str, str | None]:
    """Each given pattern in NFC and checked; None and the empty text stay None."""
    checked: dict[str, str | None] = {}
    for which in naming_series.PATTERNS:
        value = patterns.get(which)
        if value is None or not value.strip():
            checked[which] = None
            continue
        text = nfc(value)
        try:
            naming_series.check(text, which)
        except naming.NamingError as exc:
            raise exc.http() from exc
        checked[which] = text
    return checked


def _checked_patterns(movie_folder: str, movie_file: str) -> tuple[str, str]:
    folder, file = nfc(movie_folder), nfc(movie_file)
    try:
        naming.check(folder, "movie_folder")
        naming.check(file, "movie_file")
    except naming.NamingError as exc:
        raise exc.http() from exc
    return folder, file


def _checked(payload: NamingIn) -> naming.Naming:
    folder, file = _checked_patterns(payload.movie_folder, payload.movie_file)
    return naming.Naming(movie_folder=folder, movie_file=file, umlauts=payload.umlauts)


def _movie_version(db: OrmSession, version_id: int) -> VersionDefinition:
    row = db.get(VersionDefinition, version_id)
    if row is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    if row.kind != "movie":
        raise error("version_kind_mismatch", "This version belongs to another type. Naming is for movie versions.", 422)
    return row


def _entry(db: OrmSession, row: VersionDefinition) -> NamingVersion:
    entry = naming.version_entry(row, naming.load(db), naming.setting_sources(db, "radarr").get(row.id))
    return NamingVersion.model_validate(entry)


@router.get(
    "",
    response_model=NamingOut,
    summary="Read the naming",
    description=(
        "The default folder and file patterns, the umlaut setting, TRaSH's defaults, the known tokens, and per movie "
        "version the patterns it uses."
    ),
)
def read_naming(db: DbSession) -> NamingOut:
    return _out(db, naming.load(db))


@router.put(
    "",
    response_model=NamingOut,
    summary="Change the naming",
    description=(
        "Checks both patterns: no unknown token; a folder pattern needs a title token and no token from the file; a "
        "file pattern needs a title token and {Release Year}, or {Original Title}. Applies to the next import of every "
        "version without patterns of its own."
    ),
    responses=error_responses(*naming.ERRORS),
)
def update_naming(payload: NamingIn, db: DbSession) -> NamingOut:
    wanted = _checked(payload)
    if payload.series is not None:
        given = payload.series.model_dump()
        checked = _checked_series({which: given[which] for which in naming_series.PATTERNS})
        missing = [which for which, value in checked.items() if value is None]
        if missing:
            raise error("naming_pattern_empty", "A naming pattern must not be empty.", 422, pattern=missing[0])
        patterns = {which: str(value) for which, value in checked.items()}
        naming_series.save(db, patterns, payload.series.multi_episode_style)
    if payload.music is not None:
        naming_music.save(db, _checked_music(payload.music.model_dump()))
    naming.save(db, wanted)
    db.commit()
    logger.info("Naming changed (umlauts %s)", wanted.umlauts)
    return _out(db, naming.load(db))


@router.put(
    "/versions/{version_id}",
    response_model=NamingVersion,
    summary="Give a movie version a naming of its own",
    description=(
        "Stores both patterns for this version, checked as `PUT /api/naming` checks them. The next import of this "
        "version names its folder and file with them; the umlaut setting stays the one of `PUT /api/naming`. Existing "
        "files are never renamed."
    ),
    responses=error_responses((404, "not_found"), (422, "version_kind_mismatch"), *naming.ERRORS),
)
def update_version_naming(version_id: int, payload: NamingVersionIn, db: DbSession) -> NamingVersion:
    row = _movie_version(db, version_id)
    folder, file = _checked_patterns(payload.movie_folder, payload.movie_file)
    naming.set_version(row, folder, file)
    db.commit()
    logger.info("Version definition %d has a naming of its own", row.id)
    return _entry(db, row)


@router.delete(
    "/versions/{version_id}",
    response_model=NamingVersion,
    summary="Give a movie version the default naming again",
    description=(
        "Removes the version's own patterns; it uses the default patterns again and the answer has `own` false."
    ),
    responses=error_responses((404, "not_found"), (422, "version_kind_mismatch")),
)
def reset_version_naming(version_id: int, db: DbSession) -> NamingVersion:
    row = _movie_version(db, version_id)
    naming.set_version(row, None, None)
    db.commit()
    logger.info("Version definition %d uses the default naming again", row.id)
    return _entry(db, row)


@router.post(
    "/preview",
    response_model=PreviewOut,
    summary="Preview the naming",
    description="Fills the given patterns for a made-up movie and release, with the same checks as saving.",
    responses=error_responses(*naming.ERRORS),
)
def preview_naming(payload: NamingIn) -> PreviewOut:
    return PreviewOut.model_validate(naming.preview(_checked(payload)))


def _series_version(db: OrmSession, version_id: int) -> VersionDefinition:
    row = db.get(VersionDefinition, version_id)
    if row is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    if row.kind != "series":
        raise error(
            "version_kind_mismatch", "This version belongs to another type. This naming is for series versions.", 422
        )
    return row


@router.put(
    "/series/versions/{version_id}",
    response_model=SeriesNamingVersion,
    summary="Give a series version a naming of its own",
    description=(
        "Each pattern given is the version's own, checked as the default patterns; a pattern left out or empty is the "
        "default one. `episode_numbering` decides the numbers in the names. Existing files keep their names until they "
        "are renamed with `POST /api/rename/run`; until then season and series folders keep the name they got when "
        "the first file was filed."
    ),
    responses=error_responses((404, "not_found"), (422, "version_kind_mismatch"), *naming.ERRORS),
)
def update_series_version_naming(version_id: int, payload: SeriesVersionNamingIn, db: DbSession) -> SeriesNamingVersion:
    row = _series_version(db, version_id)
    given = payload.model_dump()
    checked = _checked_series({which: given[which] for which in naming_series.PATTERNS})
    naming_series.set_version(row, checked, payload.episode_numbering)
    db.commit()
    logger.info("Series version definition %d has its naming set (numbering %s)", row.id, payload.episode_numbering)
    return SeriesNamingVersion.model_validate(
        naming_series.version_entry(row, naming_series.load(db), naming.setting_sources(db, "sonarr").get(row.id))
    )


@router.delete(
    "/series/versions/{version_id}",
    response_model=SeriesNamingVersion,
    summary="Give a series version the default naming again",
    description="Removes the version's own patterns and sets its numbering back to tmdb.",
    responses=error_responses((404, "not_found"), (422, "version_kind_mismatch")),
)
def reset_series_version_naming(version_id: int, db: DbSession) -> SeriesNamingVersion:
    row = _series_version(db, version_id)
    naming_series.set_version(row, {}, None)
    db.commit()
    logger.info("Series version definition %d uses the default naming again", row.id)
    return SeriesNamingVersion.model_validate(
        naming_series.version_entry(row, naming_series.load(db), naming.setting_sources(db, "sonarr").get(row.id))
    )


@router.post(
    "/series/preview",
    response_model=SeriesPreviewOut,
    summary="Preview the naming of series",
    description=(
        "Fills the given patterns for made-up episodes: one, a double episode, one without a title (TBA), a daily "
        "show, a special, a special TMDB and TVDB number differently, and a special with a placeholder name. The "
        "same checks as saving."
    ),
    responses=error_responses(*naming.ERRORS),
)
def preview_series_naming(payload: SeriesPreviewIn) -> SeriesPreviewOut:
    given = payload.model_dump()
    checked = _checked_series({which: given[which] for which in naming_series.PATTERNS})
    missing = [which for which, value in checked.items() if value is None]
    if missing:
        raise error("naming_pattern_empty", "A naming pattern must not be empty.", 422, pattern=missing[0])
    current = naming_series.SeriesNaming(
        **{which: str(value) for which, value in checked.items()},
        style=payload.multi_episode_style,
        umlauts=payload.umlauts,
        numbering=payload.episode_numbering,
    )
    return SeriesPreviewOut.model_validate(naming_series.preview(current))


@router.post(
    "/music/preview",
    response_model=MusicPreviewOut,
    summary="Preview the naming of music",
    description="Fills the given patterns for a made-up album with umlauts. The same checks as saving.",
    responses=error_responses(*naming.ERRORS),
)
def preview_music_naming(payload: MusicPreviewIn) -> MusicPreviewOut:
    checked = _checked_music(payload.model_dump())
    current = naming_music.MusicNaming(**checked, umlauts=payload.umlauts)
    return MusicPreviewOut.model_validate(naming_music.preview(current))
