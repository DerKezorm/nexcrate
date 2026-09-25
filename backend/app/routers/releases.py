"""The release checkers: typed release names against the profile of every movie or series version, or of music.

Nothing is searched and nothing is downloaded; this shows why a release would fit or not. With a title, the
title's original language and runtime count, and each version's current file for the upgrade question. The
engine (``services/releases``) knows nothing about the database; this route hands it what it needs.

Release names never reach a log line: they are as telling as titles.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from ..deps import DbSession
from ..meldungen import error, error_responses
from ..models import EpisodeFile, EpisodeVersion, Title, Version, VersionDefinition
from ..services import releases
from ..services.music import album_quality
from ..services.profiles import store as profile_store
from ..services.releases import music_decision, music_parser, music_qualities
from ..services.series import release_match, watching

logger = logging.getLogger("nexcrate.releases")

router = APIRouter(prefix="/api/releases", tags=["releases"])

NAME_MAX_LENGTH = 500


class CheckIn(BaseModel):
    name: str = Field(min_length=1, max_length=NAME_MAX_LENGTH, description="A release name, 1 to 500 characters.")
    title_id: int | None = Field(default=None, description="A movie of the library: its language, runtime and files.")
    runtime_min: int | None = Field(default=None, ge=1, le=10_000, description="Used without title_id; else unknown.")
    size_bytes: int | None = Field(
        default=None, ge=0, le=2**53, description="The release size; unknown is not checked."
    )


class RevisionOut(BaseModel):
    version: int
    real: int
    repack: bool


class ParsedOut(BaseModel):
    title: str | None
    year: int | None
    group: str | None
    source: str = Field(examples=["BLURAY"])
    resolution: int = Field(examples=[2160])
    modifier: str = Field(examples=["NONE"])
    quality: str = Field(examples=["Bluray-2160p"])
    revision: RevisionOut
    edition: str | None
    languages: list[str] = Field(description="After the movie's original language filled in.", examples=[["German"]])
    hardcoded_subs: str | None


class MatchedOut(BaseModel):
    name: str
    score: int


class RejectionOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    code: str = Field(
        description=(
            "quality_not_allowed {quality}, score_below_minimum {score, minimum}, too_small {size_bytes, "
            "minimum_bytes}, too_large {size_bytes, maximum_bytes}, language_missing {languages}, hardcoded_subs "
            "{value} or unknown_quality."
        )
    )


class UpgradeOut(BaseModel):
    current_quality: str
    current_score: int
    better: bool
    reason: str | None = Field(
        description=(
            "Null when better. worse_quality, upgrades_disabled, cutoff_met, score_not_higher, "
            "upgrade_until_reached or step_too_small."
        )
    )


class ResultOut(BaseModel):
    accepted: bool
    score: int
    matched: list[MatchedOut]
    rejections: list[RejectionOut]
    upgrade: UpgradeOut | None = Field(description="Null without a current file.")
    below_target: bool = Field(
        description=(
            "The release's resolution is known and lower than the profile's target resolution, whether it fits or "
            "not. A fitting release below the target fits for now. False for a profile built before the target "
            "was stored."
        )
    )


class CheckedVersion(BaseModel):
    version_id: int
    label: str
    has_profile: bool
    result: ResultOut | None = Field(description="Null without a profile.")


class CheckOut(BaseModel):
    parsed: ParsedOut
    versions: list[CheckedVersion]


@router.post(
    "/check",
    response_model=CheckOut,
    summary="Check a release name against every movie version",
    description=(
        "Parses the name and evaluates it with the profile of each movie version: fits or not, the score, the "
        "matched formats and the reasons as codes. With `title_id` the title's original language and runtime "
        "count, and the upgrade question is answered against each version's current file. Without it "
        "`runtime_min` counts (else 110 minutes, as Radarr assumes) and the original language is unknown."
    ),
    responses=error_responses((404, "not_found")),
)
def check_release(payload: CheckIn, db: DbSession) -> CheckOut:
    name = payload.name.strip()
    if not name:
        raise error("invalid_input", "The input is not valid.", 422, fields=["name"])
    title: Title | None = None
    if payload.title_id is not None:
        title = db.get(Title, payload.title_id)
        if title is None:
            raise error("not_found", "This does not exist, or not any more.", 404)
        if title.kind != "movie":
            raise error("invalid_input", "The input is not valid.", 422, fields=["title_id"])
    original_language = title.original_language if title is not None else None
    runtime = (title.runtime if title is not None and title.runtime else None) or payload.runtime_min
    parsed = releases.parse(name, original_language)

    definitions = list(
        db.scalars(select(VersionDefinition).where(VersionDefinition.kind == "movie").order_by(VersionDefinition.id))
    )
    ids = [definition.id for definition in definitions]
    stored = profile_store.by_versions(db, ids)
    files: dict[int, Version] = {}
    if title is not None:
        files = {
            version.version_definition_id: version
            for version in db.scalars(select(Version).where(Version.title_id == title.id))
        }

    rows: list[CheckedVersion] = []
    for definition in definitions:
        profile = stored.get(definition.id)
        result: dict[str, Any] | None = None
        if profile is not None and profile.rules.get("kind") == "movie":
            current = _version_file(files.get(definition.id))
            context = releases.ReleaseContext(
                original_language=original_language,
                runtime_min=runtime,
                size_bytes=payload.size_bytes or None,
                current_file=current,
            )
            result = releases.evaluate(profile.rules, name, context, parsed=parsed)
            result.pop("parsed", None)
        rows.append(
            CheckedVersion(
                version_id=definition.id,
                label=definition.label,
                has_profile=profile is not None,
                result=ResultOut.model_validate(result) if result is not None else None,
            )
        )
    logger.info(
        "Release checked against %d versions, %d with a profile%s",
        len(rows),
        len(stored),
        f", title {title.id}" if title is not None else "",
    )
    return CheckOut(parsed=ParsedOut.model_validate(parsed.as_dict()), versions=rows)


# --- The series checker (S2.6) ------------------------------------------- #

NAMES_MAX = 50


class CheckSeriesIn(BaseModel):
    names: list[str] = Field(
        min_length=1, max_length=NAMES_MAX, description="Release names, 1 to 50, each 1 to 500 characters."
    )
    title_id: int | None = Field(
        default=None, description="A series of the library: its episodes, runtimes, language and files."
    )
    version_ids: list[int] | None = Field(
        default=None, max_length=50, description="Only these series versions; by default every one of them."
    )
    runtime_min: int | None = Field(default=None, ge=1, le=10_000, description="Used without title_id; else unknown.")
    size_bytes: int | None = Field(
        default=None, ge=0, le=2**53, description="The release size, only with exactly one name."
    )


class ParsedSeriesOut(BaseModel):
    series_title: str | None
    year: int | None
    form: str = Field(
        description=(
            "standard, multi_episode, season_pack, daily, mini_series, multi_season, complete_series, "
            "split_episode, partial_season, season_extras, anime or none."
        )
    )
    release_type: str | None = Field(description="single_episode, multi_episode or season_pack; null when refused.")
    season: int | None
    seasons: list[int]
    episodes: list[int]
    air_date: str | None
    part: int | None
    absolute: list[int]
    refused: str | None = Field(description="The code of a refused form, null when the form is taken.")
    group: str | None
    source: str = Field(examples=["WEBDL"])
    resolution: int = Field(examples=[1080])
    quality: str = Field(examples=["WEBDL-1080p"])
    revision: RevisionOut
    languages: list[str]
    hardcoded_subs: str | None


class MatchedEpisodeOut(BaseModel):
    episode_id: int
    code: str
    name: str
    via: str


class MatchOut(BaseModel):
    via: str | None = Field(
        description="owner, scene, tvdb, group, tmdb, air_date or absolute (an anime series' number counted through); "
        "null when nothing matched."
    )
    ambiguous: bool
    other: dict[str, Any] | None = Field(description="The other reading: its scheme and the codes of its episodes.")
    missing: list[int]
    episodes: list[MatchedEpisodeOut]
    notes: list[str] = Field(
        description="unverified_scene, two_dates, not_daily, not_anime (counted through, but the series is no anime), "
        "group_counting, scene_season (a scene name of one season said where an anime number counted through lies)."
    )


class EpisodeResultOut(BaseModel):
    episode_id: int
    code: str
    state: str = Field(description="fills, replaces, keeps or not_watched.")
    upgrade: UpgradeOut | None = None


class NoteOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    code: str = Field(description="runtime_unknown or title_mismatch.")


class SeriesResultOut(BaseModel):
    accepted: bool
    score: int
    matched: list[MatchedOut]
    rejections: list[RejectionOut] = Field(
        description=(
            "Every failing check, not only the first group as Sonarr shows it. Besides the movie codes: "
            "refused_form {form, refused}, no_episode_match, season_incomplete {not_aired} and already_imported (a "
            "file of this very release is there already)."
        )
    )
    notes: list[NoteOut]
    below_target: bool
    below_target_in_group: bool = Field(
        description=(
            "The release lies below the profile's target resolution and shares a quality group with it, where only "
            "the score decides."
        )
    )
    episodes: list[EpisodeResultOut]
    would_take: bool | None = Field(description="Null without a series.")
    replaces: dict[str, int] = Field(description="How many files would be replaced, and their size.")
    runtime_min: int | None = Field(description="The summed runtime the size was checked against; null when unknown.")
    rank: int | None = Field(default=None, description="The place among the names given, 1 first.")


class CheckedSeriesVersion(BaseModel):
    version_id: int
    label: str
    has_profile: bool
    result: SeriesResultOut | None = Field(description="Null without a profile.")


class CheckedSeriesRelease(BaseModel):
    name: str
    parsed: ParsedSeriesOut
    match: MatchOut | None = Field(description="Null without a series.")
    versions: list[CheckedSeriesVersion]


class CheckSeriesOut(BaseModel):
    releases: list[CheckedSeriesRelease]


def _series_definitions(db: DbSession, wanted: list[int] | None) -> list[VersionDefinition]:
    definitions = list(
        db.scalars(select(VersionDefinition).where(VersionDefinition.kind == "series").order_by(VersionDefinition.id))
    )
    if wanted is None:
        return definitions
    known = {definition.id for definition in definitions}
    if not set(wanted) <= known:
        raise error("invalid_input", "The input is not valid.", 422, fields=["version_ids"])
    return [definition for definition in definitions if definition.id in set(wanted)]


def _title_matches(title: Title, keys: tuple[str, ...]) -> bool:
    """Whether the title in the name is one of the series' spellings; a hint only (decision 20)."""
    if not keys:
        return True
    stored = f"{title.search_keys or ''}{title.tmdb_search_keys or ''}"
    return any(f"|{key}|" in stored for key in keys)


def _version_file(version: Version | None) -> releases.CurrentFile | None:
    """A movie version's file as the checker compares it: by its path, with its stored quality and languages."""
    if version is None or not version.has_file:
        return None
    return releases.CurrentFile(
        name=version.relative_path,
        quality=version.quality,
        size_bytes=version.size,
        languages=releases.stored_languages(version.languages),
    )


def _episode_file(row: EpisodeFile | None, covered: int) -> releases.CurrentEpisodeFile | None:
    if row is None:
        return None
    return releases.CurrentEpisodeFile(
        release_title=row.release_title,
        name=row.relative_path,
        quality=row.quality,
        release_type=row.release_type,
        size_bytes=row.size,
        episode_count=max(1, covered),
        file_id=row.id,
        languages=releases.stored_languages(row.languages),
    )


def _match_out(numbering: release_match.Numbering, found: release_match.Match) -> MatchOut:
    return MatchOut(
        via=found.via,
        ambiguous=found.ambiguous,
        other=found.other,
        missing=list(found.missing),
        episodes=[
            MatchedEpisodeOut(
                episode_id=episode_id,
                code=numbering.episodes[episode_id].code,
                name=numbering.episodes[episode_id].name,
                via=found.via or "",
            )
            for episode_id in found.episode_ids
            if episode_id in numbering.episodes
        ],
        notes=list(found.notes),
    )


def _series_context(
    *,
    title: Title | None,
    numbering: release_match.Numbering | None,
    found: release_match.Match | None,
    read: releases.ParsedRelease,
    rows: dict[int, EpisodeVersion],
    files: dict[int, EpisodeFile],
    covered: dict[int, int],
    size_bytes: int | None,
    runtime_min: int | None,
    on: str,
) -> releases.SeriesContext:
    """What the engine needs of the series and of one version."""
    episodes: list[releases.EpisodeInfo] = []
    not_aired: list[str] = []
    if numbering is not None and found is not None:
        for episode_id in found.episode_ids:
            episode = numbering.episodes.get(episode_id)
            if episode is None:
                continue
            row = rows.get(episode_id)
            file_row = files.get(row.episode_file_id) if row is not None and row.episode_file_id is not None else None
            has_aired = watching.aired(episode.air_date, on)
            if not watching.out_for_pack(episode.air_date, on) and not episode.special:
                not_aired.append(episode.code)
            episodes.append(
                releases.EpisodeInfo(
                    episode_id=episode.id,
                    code=episode.code,
                    runtime_min=episode.runtime,
                    aired=has_aired,
                    watched=bool(row.watched) if row is not None else False,
                    special=episode.special,
                    file=_episode_file(file_row, covered.get(file_row.id, 1) if file_row is not None else 1),
                )
            )
    return releases.SeriesContext(
        original_language=title.original_language if title is not None else None,
        runtime_min=(title.runtime if title is not None and title.runtime else None) or runtime_min,
        size_bytes=size_bytes or None,
        episodes=tuple(episodes),
        with_series=title is not None,
        not_aired=tuple(not_aired) if read.series.release_type == "season_pack" else (),
        title_matches=_title_matches(title, read.series.title_keys) if title is not None else True,
    )


@router.post(
    "/check-series",
    response_model=CheckSeriesOut,
    summary="Check release names against every series version",
    description=(
        "Reads each name as a series release (season and episodes, a date, a season pack), maps it onto the "
        "episodes of the series when `title_id` names one, and evaluates it with the profile of each series "
        "version: fits or not, the score, which episodes it would fill or replace, and the reasons as codes. "
        "Nothing is searched and nothing is downloaded. `size_bytes` needs exactly one name."
    ),
    responses=error_responses((404, "not_found")),
)
def check_series_releases(payload: CheckSeriesIn, db: DbSession) -> CheckSeriesOut:
    names = [name.strip() for name in payload.names if name.strip()]
    if not names or any(len(name) > NAME_MAX_LENGTH for name in names):
        raise error("invalid_input", "The input is not valid.", 422, fields=["names"])
    if payload.size_bytes is not None and len(names) != 1:
        raise error("invalid_input", "The input is not valid.", 422, fields=["size_bytes"])

    title: Title | None = None
    if payload.title_id is not None:
        title = db.get(Title, payload.title_id)
        if title is None:
            raise error("not_found", "This does not exist, or not any more.", 404)
        if title.kind != "series":
            raise error("invalid_input", "The input is not valid.", 422, fields=["title_id"])

    definitions = _series_definitions(db, payload.version_ids)
    ids = [definition.id for definition in definitions]
    stored = profile_store.by_versions(db, ids)

    numbering = release_match.load(db, title) if title is not None else None
    on = watching.today()
    episode_rows: dict[int, dict[int, EpisodeVersion]] = defaultdict(dict)
    files: dict[int, EpisodeFile] = {}
    covered: dict[int, int] = defaultdict(int)
    version_of: dict[int, int] = {}
    if title is not None:
        versions = list(db.scalars(select(Version).where(Version.title_id == title.id)))
        version_of = {version.version_definition_id: version.id for version in versions}
        version_ids = [version.id for version in versions]
        if version_ids:
            for row in db.scalars(select(EpisodeVersion).where(EpisodeVersion.version_id.in_(version_ids))):
                episode_rows[row.version_id][row.episode_id] = row
                if row.episode_file_id is not None:
                    covered[row.episode_file_id] += 1
            files = {
                row.id: row for row in db.scalars(select(EpisodeFile).where(EpisodeFile.version_id.in_(version_ids)))
            }

    original_language = title.original_language if title is not None else None
    known = tuple(text for text in (title.title, title.original_title) if text) if title is not None else ()
    anime = title is not None and title.series_type == "anime"
    parsed = [releases.parse_series(name, original_language, titles=known, anime=anime) for name in names]
    # An anime series is judged by the anime rules of each version's profile (A4).
    rules_of = {
        definition_id: profile_store.series_rules(profile, title.series_type if title is not None else None)
        for definition_id, profile in stored.items()
    }
    matches = [release_match.match(numbering, read.series) if numbering is not None else None for read in parsed]

    results: dict[tuple[int, int], dict[str, Any]] = {}
    for index, (name, read) in enumerate(zip(names, parsed, strict=True)):
        for definition in definitions:
            profile = stored.get(definition.id)
            if profile is None or profile.rules.get("kind") != "series":
                continue
            context = _series_context(
                title=title,
                numbering=numbering,
                found=matches[index],
                read=read,
                rows=episode_rows.get(version_of.get(definition.id, -1), {}),
                files=files,
                covered=covered,
                size_bytes=payload.size_bytes,
                runtime_min=payload.runtime_min,
                on=on,
            )
            results[(index, definition.id)] = releases.evaluate_series(rules_of[definition.id], name, context, read)

    for definition in definitions:
        entries = [
            (index, results[(index, definition.id)]) for index in range(len(names)) if (index, definition.id) in results
        ]
        if not entries:
            continue
        releases.rank_series(
            rules_of[definition.id],
            [result for _index, result in entries],
            [parsed[index] for index, _result in entries],
            [payload.size_bytes for _entry in entries],
        )

    rows_out = [
        CheckedSeriesRelease(
            name=name,
            parsed=ParsedSeriesOut.model_validate(parsed[index].as_dict()),
            match=(
                _match_out(numbering, matches[index]) if numbering is not None and matches[index] is not None else None
            ),
            versions=[
                CheckedSeriesVersion(
                    version_id=definition.id,
                    label=definition.label,
                    has_profile=definition.id in stored,
                    result=(
                        SeriesResultOut.model_validate(results[(index, definition.id)])
                        if (index, definition.id) in results
                        else None
                    ),
                )
                for definition in definitions
            ],
        )
        for index, name in enumerate(names)
    ]
    logger.info(
        "Series releases checked: %d names against %d versions, %d with a profile%s",
        len(names),
        len(definitions),
        len(stored),
        f", title {title.id}" if title is not None else "",
    )
    return CheckSeriesOut(releases=rows_out)


# --- The music checker (M2.5) --------------------------------------------- #


class CheckAlbumIn(BaseModel):
    names: list[str] = Field(
        min_length=1, max_length=NAMES_MAX, description="Release names, 1 to 50, each 1 to 500 characters."
    )


class ParsedAlbumOut(BaseModel):
    release_title: str
    artist: str = Field(description="A proposal only: the name is split at its first divider.")
    album: str
    unsure: bool = Field(description="The head has more than one hyphen: the artist may be longer than proposed.")
    various_artists: bool
    year: int | None
    edition_year: int | None = Field(description="Only when the name says a second year: the edition, a remaster.")
    format: str | None = Field(examples=["FLAC"])
    format_assumed: bool = Field(description="A scene name without a format is an MP3 release; the name says none.")
    bitrate: str | None = Field(examples=["320"])
    bit_depth: int | None
    sample_rate_khz: float | None
    source: str | None = Field(examples=["WEB"])
    media_count: int | None
    disc_part: int | None
    editions: list[str]
    kinds: list[str]
    several_albums: bool
    country: str | None
    catalogue_number: str | None
    group: str | None
    suffix: str | None = Field(description="What a poster hung behind the group.")
    revision: RevisionOut
    warnings: list[str] = Field(description="cue, audiobook, tribute, karaoke.")
    shape: str = Field(description="scene, p2p or unknown.")


class AlbumVerdictOut(BaseModel):
    accepted: bool
    for_now: bool = Field(description="Accepted, but not the target of the profile: the file would be replaced later.")
    rejections: list[RejectionOut] = Field(
        description=(
            "Codes: several_albums, cue_single_file, audiobook, unknown_quality, not_the_target (with step), "
            "step_never_taken (with step)."
        )
    )
    notes: list[NoteOut] = Field(
        description=(
            "What orders the release, as codes: below_target, above_target (with step), mp3_assumed, hires_first, "
            "hires_last, analogue_last (with source), cd_first, repeat_first, tribute, karaoke."
        )
    )
    place: int | None = Field(description="The place among the accepted names of this request, 1 for the best.")


class CheckedAlbumRelease(BaseModel):
    name: str
    parsed: ParsedAlbumOut
    step: str = Field(
        description="lossless_24, lossless, lossy_high, lossy_mid, lossy_low or unknown.", examples=["lossless"]
    )
    verdict: AlbumVerdictOut | None = Field(description="Null without a music profile.")


class CheckAlbumOut(BaseModel):
    has_profile: bool
    releases: list[CheckedAlbumRelease]


@router.post(
    "/check-album",
    response_model=CheckAlbumOut,
    summary="Check release names against the music profile",
    description=(
        "Reads each name as a music release (artist and album as a proposal, format, bit depth, source, edition, "
        "group) and says which quality step it is and what the music profile does with it, with the reasons as "
        "codes. Nothing is searched and nothing is downloaded. Without a profile only the reading comes back."
    ),
)
def check_album_releases(payload: CheckAlbumIn, db: DbSession) -> CheckAlbumOut:
    names = [name.strip() for name in payload.names if name.strip()]
    if not names or any(len(name) > NAME_MAX_LENGTH for name in names):
        raise error("invalid_input", "The input is not valid.", 422, fields=["names"])
    rules = album_quality.rules_of(db)
    parsed = [music_parser.parse_album(name) for name in names]
    verdicts = [music_decision.evaluate(read, rules) for read in parsed] if rules is not None else []
    places = music_decision.order(verdicts)
    rows_out = [
        CheckedAlbumRelease(
            name=name,
            parsed=ParsedAlbumOut.model_validate(music_decision.parsed_dict(parsed[index])),
            step=verdicts[index]["step"] if verdicts else music_qualities.step_of(parsed[index]),
            verdict=(AlbumVerdictOut.model_validate({**verdicts[index], "place": places[index]}) if verdicts else None),
        )
        for index, name in enumerate(names)
    ]
    logger.info(
        "Music releases checked: %d names, %s a profile", len(names), "with" if rules is not None else "without"
    )
    return CheckAlbumOut(has_profile=rules is not None, releases=rows_out)
