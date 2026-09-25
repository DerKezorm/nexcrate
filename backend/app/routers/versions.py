"""Version definitions: named slots per media kind, such as HD and 4K for movies.

A version is a name with an optional profile (step 2b): ``has_profile`` and a one-line ``profile_line`` come
along, the profile itself lives under ``/api/versions/{id}/profile``. Older clients still send
``profile_name``, ``root_folder`` and ``auto_add``: the request models ignore unknown fields, so such a
request keeps working and changes nothing.

Since step 3 a movie version has a ``folder``, chosen from the folders nexcrate sees. Two versions never share a
folder, and no version folder lies inside another. Changing it moves nothing.

A version also carries its delay rule: which protocol wins a tie, whether one is off,
and how long the automatic waits for a better release. Radarr, Sonarr and Lidarr hang that on tags; here the version
binds, as it binds the profile and the folder.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from ..deps import DbSession
from ..meldungen import error, error_responses
from ..models import PendingRelease, media
from ..services import delay as delay_rules
from ..services import folder_rules, folders, profiles, tags, trash
from ..services.automatic import waiting
from ..services.profiles import store as profile_store
from ..services.schreibweisen import nfc

logger = logging.getLogger("nexcrate.versions")

router = APIRouter(prefix="/api/versions", tags=["versions"])

LABEL_MAX_LENGTH = 64
Kind = Literal["movie", "series", "album"]


class LanguageAnswer(BaseModel):
    code: str = Field(description="ISO 639-1.", examples=["de"])
    role: str = Field(description="required or preferred.", examples=["required"])


class ProfileLine(BaseModel):
    """What the Fassungen tab says about a profile in one line."""

    resolution: str = Field(examples=["2160p"])
    source: str = Field(examples=["encodes"])
    languages: list[LanguageAnswer]
    hdr: str | None = Field(description="Null where the question is not asked (1080p).", examples=["safety"])
    max_gb_per_hour: int | float | None = Field(description="Null means no upper limit.", examples=[20])
    outdated: bool = Field(description="Built with another TRaSH Guides state than the one in use.")


class DelayRule(BaseModel):
    """The delay rule of a version. Left alone it is what Radarr, Sonarr and Lidarr ship: Usenet preferred, no wait."""

    enable_usenet: bool = Field(default=True, description="Off: a Usenet release never fits this version.")
    enable_torrent: bool = Field(default=True, description="Off: a torrent never fits this version.")
    preferred_protocol: Literal["usenet", "torrent"] = Field(
        default="usenet", description="Wins between releases of the same quality and custom format score."
    )
    usenet_minutes: int = Field(
        default=0,
        ge=0,
        le=delay_rules.MAX_MINUTES,
        description="How long the automatic waits for a better release before it takes a Usenet release, counted "
        "from when the indexer published it. 0 means at once.",
    )
    torrent_minutes: int = Field(default=0, ge=0, le=delay_rules.MAX_MINUTES, description="The same for a torrent.")
    bypass_highest_quality: bool = Field(
        default=True,
        description="A release of the best quality the profile allows, in the preferred protocol, never waits.",
    )
    bypass_score: bool = Field(
        default=False,
        description="A release whose custom format score reaches `minimum_score`, in the preferred protocol, never "
        "waits.",
    )
    minimum_score: int = Field(default=0, ge=-delay_rules.SCORE_LIMIT, le=delay_rules.SCORE_LIMIT)


class TaggedDelayRule(DelayRule):
    """A rule for titles carrying one of `tags`, as a delay profile with tags in the apps."""

    tags: list[str] = Field(min_length=1, max_length=tags.PER_ITEM_MAX, description="By name.")


class DelayWithTags(DelayRule):
    tagged: list[TaggedDelayRule] = Field(
        default_factory=list,
        max_length=delay_rules.TAGGED_MAX,
        description="Rules for titles with tags, in their order: the first sharing a tag with a title holds for "
        "it; a title without one gets the version's rule, as the apps' delay profile without tags.",
    )


class Version(BaseModel):
    id: int
    kind: str = Field(description="movie, series or album.", examples=["movie"])
    label: str = Field(examples=["4K"])
    title_count: int = Field(description="How many titles have this version.")
    has_profile: bool = Field(default=False, description="Whether the version has a profile.")
    profile_line: ProfileLine | None = Field(default=None, description="Null without a profile.")
    profile_id: int | None = Field(
        default=None, description="The profile it judges by; several versions may share one."
    )
    profile: str | None = Field(default=None, description="The name of that profile.", examples=["Full-HD"])
    folder: str | None = Field(
        default=None,
        description="The default folder of this movie version: new movies go into it, movies that lie elsewhere stay "
        "there. Null until chosen.",
    )
    delay: DelayWithTags = Field(
        default_factory=DelayWithTags, description="The delay rule; the defaults while untouched."
    )
    waiting: int = Field(default=0, description="How many releases wait out the delay for this version right now.")


class FolderIn(BaseModel):
    folder: str | None = Field(
        max_length=4096, description="A folder from GET /api/folders, or null to remove the folder."
    )


class VersionIn(BaseModel):
    # Ignored, not refused: see the module note.
    model_config = ConfigDict(extra="ignore")

    kind: Kind = "movie"
    label: str = Field(max_length=200, description=f"1 to {LABEL_MAX_LENGTH} characters, unique per kind.")


class VersionPatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: Kind | None = None
    label: str | None = Field(default=None, max_length=200)


def _clean_label(raw: str) -> str | None:
    label = nfc(raw).strip()
    return label if label and len(label) <= LABEL_MAX_LENGTH else None


def _label_taken(db: OrmSession, kind: str, label: str, own_id: int | None) -> bool:
    wanted = label.casefold()
    rows = db.execute(
        select(media.VersionDefinition.id, media.VersionDefinition.label).where(media.VersionDefinition.kind == kind)
    )
    return any(row.label.casefold() == wanted and row.id != own_id for row in rows)


def _usage(db: OrmSession, version_id: int) -> tuple[int, int]:
    sources = db.scalar(select(func.count(media.Source.id)).where(media.Source.version_id == version_id)) or 0
    titles = (
        db.scalar(select(func.count(media.Version.id)).where(media.Version.version_definition_id == version_id)) or 0
    )
    return int(sources), int(titles)


#: What a version says about its profile: its number, its name and the line in words (None without answers).
ProfileFacts = tuple[int, str, dict[str, Any] | None]


def _profile_lines(db: OrmSession, ids: list[int]) -> dict[int, ProfileFacts]:
    """Per version definition that has one: the profile, and the line in words where the kind knows them."""
    if not ids:
        return {}
    facts: dict[int, ProfileFacts] = {}
    for version_id, row in profile_store.by_versions(db, ids).items():
        line = (
            profiles.line_of(row.kind, row.answers, row.trash_commit != trash.current(row.kind).commit)
            if row.kind in trash.FILE_NAMES
            else None
        )
        facts[version_id] = (row.id, row.name, line)
    return facts


def _with_profile(version: Version, lines: dict[int, ProfileFacts]) -> Version:
    found = lines.get(version.id)
    if found is not None:
        profile_id, name, line = found
        version.has_profile = True
        version.profile_id = profile_id
        version.profile = name
        version.profile_line = ProfileLine.model_validate(line) if line is not None else None
    return version


def _delay(db: OrmSession, row: media.VersionDefinition) -> DelayWithTags:
    tagged = []
    for item in delay_rules.tagged_of(row.delay):
        names = tags.names_of_ids(db, item.tag_ids)
        if names:
            # A rule whose tags were all deleted holds for nothing and is not shown.
            tagged.append({**item.rule.as_dict(), "tags": names})
    return DelayWithTags.model_validate({**delay_rules.from_stored(row.delay).as_dict(), "tagged": tagged})


def _waiting(db: OrmSession) -> dict[int, int]:
    counted = db.execute(
        select(PendingRelease.version_definition_id, func.count(PendingRelease.id)).group_by(
            PendingRelease.version_definition_id
        )
    )
    return {int(version_id): int(count) for version_id, count in counted.tuples()}


def version_out(db: OrmSession, row: media.VersionDefinition) -> Version:
    version = Version(
        id=row.id,
        kind=row.kind,
        label=row.label,
        title_count=_usage(db, row.id)[1],
        folder=row.folder,
        delay=_delay(db, row),
        waiting=_waiting(db).get(row.id, 0),
    )
    return _with_profile(version, _profile_lines(db, [row.id]))


@router.get(
    "",
    response_model=list[Version],
    summary="List the version definitions",
    description="Every version definition, optionally of one media kind, with the number of titles that have it.",
)
def list_versions(
    db: DbSession,
    kind: Annotated[Kind | None, Query(description="Only this media kind: movie, series or album.")] = None,
) -> list[Version]:
    statement = select(media.VersionDefinition).order_by(media.VersionDefinition.id)
    if kind is not None:
        statement = statement.where(media.VersionDefinition.kind == kind)
    rows = list(db.scalars(statement))
    counts = dict(
        db.execute(
            select(media.Version.version_definition_id, func.count(media.Version.id)).group_by(
                media.Version.version_definition_id
            )
        )
        .tuples()
        .all()
    )
    lines = _profile_lines(db, [row.id for row in rows])
    waits = _waiting(db)
    return [
        _with_profile(
            Version(
                id=row.id,
                kind=row.kind,
                label=row.label,
                title_count=int(counts.get(row.id, 0)),
                folder=row.folder,
                delay=_delay(db, row),
                waiting=waits.get(row.id, 0),
            ),
            lines,
        )
        for row in rows
    ]


@router.post(
    "",
    status_code=201,
    response_model=Version,
    summary="Create a version definition",
    description=(
        "A named slot such as HD or 4K. A version is only a name; quality rules come later. The label is unique "
        "per media kind, compared without regard to case. A source can then feed this version. Unknown fields "
        "in the body are ignored."
    ),
    responses=error_responses((409, "version_label_taken"), (409, "music_version_exists")),
)
def create_version(payload: VersionIn, db: DbSession) -> Version:
    label = _clean_label(payload.label)
    if label is None:
        raise error("invalid_input", "The input is not valid.", 422, fields=["label"])
    if payload.kind == "album" and db.scalar(
        select(media.VersionDefinition.id).where(media.VersionDefinition.kind == "album")
    ):
        # Music has exactly one version (decision 10).
        raise error("music_version_exists", "Music has one version; a second one cannot be added yet.", 409)
    if _label_taken(db, payload.kind, label, None):
        raise error("version_label_taken", "A version with this name already exists for this type.", 409)
    row = media.VersionDefinition(kind=payload.kind, label=label)
    db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if payload.kind == "album":
            raise error(
                "music_version_exists", "Music has one version; a second one cannot be added yet.", 409
            ) from exc
        raise error("version_label_taken", "A version with this name already exists for this type.", 409) from exc
    logger.info("Version definition %d created", row.id)
    return version_out(db, row)


@router.patch(
    "/{version_id}",
    response_model=Version,
    summary="Change a version definition",
    description=(
        "Changes the label, the kind or both; fields left out stay, unknown fields are ignored. The kind can only "
        "change while no source and no title uses the version; a profile goes with the change of kind."
    ),
    responses=error_responses((404, "not_found"), (409, "version_label_taken"), (409, "version_in_use")),
)
def update_version(version_id: int, payload: VersionPatch, db: DbSession) -> Version:
    row = db.get(media.VersionDefinition, version_id)
    if row is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    kind = payload.kind or row.kind
    if kind != row.kind:
        sources, titles = _usage(db, version_id)
        if sources or titles:
            raise error(
                "version_in_use",
                f"This version is still in use: by {sources} sources and {titles} titles.",
                409,
                sources=sources,
                titles=titles,
            )
    label = row.label
    if payload.label is not None:
        cleaned = _clean_label(payload.label)
        if cleaned is None:
            raise error("invalid_input", "The input is not valid.", 422, fields=["label"])
        label = cleaned
    if (label != row.label or kind != row.kind) and _label_taken(db, kind, label, row.id):
        raise error("version_label_taken", "A version with this name already exists for this type.", 409)
    if kind != row.kind:
        # A profile answers the questions of one kind; it cannot follow the version into another kind. It stays
        # where it is when another version still points at it.
        stored = profile_store.of_version(db, row.id)
        if stored is not None:
            profile_store.assign(db, row.id, None)
            db.flush()
            if not profile_store.versions_of(db, stored.id):
                db.delete(stored)
            logger.info("Profile of version definition %d let go with the change of kind", row.id)
    row.kind = kind
    row.label = label
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise error("version_label_taken", "A version with this name already exists for this type.", 409) from exc
    logger.info("Version definition %d changed", row.id)
    return version_out(db, row)


@router.put(
    "/{version_id}/folder",
    response_model=Version,
    summary="Set the default folder of a version",
    description=(
        "The default folder of this version, from the folders nexcrate sees; null removes it. New movies or series of "
        "the version go into it; a title that already lies in another folder, such as a taken-over one, stays there. "
        "The folder must be visible, writable, and neither the default folder of another version nor inside or around "
        "it. Changing the folder moves nothing. Since M4 the music version has one too: albums are filed below it."
    ),
    responses=error_responses(
        (404, "not_found"), (404, "folder_not_visible"), (422, "folder_not_writable"), (409, "folder_in_use")
    ),
)
def set_folder(version_id: int, payload: FolderIn, db: DbSession) -> Version:
    row = db.get(media.VersionDefinition, version_id)
    if row is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    if row.kind not in ("movie", "series", "album"):
        raise error("invalid_input", "The input is not valid.", 422, fields=["version_id"])
    if payload.folder is None or not payload.folder.strip():
        row.folder = None
    else:
        # The checks live in ``folders``: the takeover checks a chosen folder the same way.
        row.folder = folders.check_version_folder(db, row, payload.folder)
    db.commit()
    logger.info("Version definition %d: folder %s", row.id, "set" if row.folder else "removed")
    return version_out(db, row)


class FolderRule(BaseModel):
    when: Literal["genre", "certification", "tag", "series_type"] = Field(
        description="genre: TMDB's English genre name; certification: the rating in the calendar's country (movies); "
        "tag: a tag id; series_type: standard, daily or anime (series)."
    )
    values: list[Annotated[str, Field(max_length=folder_rules.VALUE_MAX_LENGTH)]] = Field(
        min_length=1, max_length=folder_rules.VALUES_MAX, description="The rule fits when the title has one of them."
    )
    folder: str = Field(
        max_length=4096, description="Where a fitting new title goes; checked like the version's folder."
    )


class FolderRulesIn(BaseModel):
    rules: list[FolderRule] = Field(default_factory=list, max_length=folder_rules.RULES_MAX)


@router.get(
    "/{version_id}/folder-rules",
    response_model=FolderRulesIn,
    summary="The library rules of a version",
    description="The rules in their order; the first that fits a new title decides its folder.",
    responses=error_responses((404, "not_found")),
)
def get_folder_rules(version_id: int, db: DbSession) -> FolderRulesIn:
    row = db.get(media.VersionDefinition, version_id)
    if row is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    return FolderRulesIn(rules=[FolderRule(**rule.as_dict()) for rule in folder_rules.load(db, row.id)])


@router.put(
    "/{version_id}/folder-rules",
    response_model=FolderRulesIn,
    summary="Set the library rules of a version",
    description=(
        "Rules \"when … then folder …\" for a movie or series version: the first that fits decides where a new title "
        "goes when its first file is filed; without one it is the default folder. A title with a file stays where it "
        "lies. Every folder is checked like the version's own folder."
    ),
    responses=error_responses(
        (404, "not_found"), (404, "folder_not_visible"), (422, "folder_not_writable"), (409, "folder_in_use"),
        (422, "folder_rules_not_for_kind"), (422, "invalid_input"),
    ),
)
def set_folder_rules(version_id: int, payload: FolderRulesIn, db: DbSession) -> FolderRulesIn:
    row = db.get(media.VersionDefinition, version_id)
    if row is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    saved = folder_rules.save(db, row, [rule.model_dump() for rule in payload.rules])
    db.commit()
    return FolderRulesIn(rules=[FolderRule(**rule.as_dict()) for rule in saved])


class Relocation(BaseModel):
    title_id: int
    name: str
    year: int | None = None
    from_: str = Field(alias="from", description="The folder the title lies in now.")
    to: str = Field(description="The folder its rules name.")
    folder: str = Field(description="The title's own folder, which moves as a whole.")
    skip: str | None = Field(
        description="Why it cannot move: folder_missing, other_disk, shared_folder, target_exists; null when it can."
    )

    model_config = ConfigDict(populate_by_name=True)


class RelocateIn(BaseModel):
    title_ids: list[int] = Field(min_length=1, max_length=10_000)


class RelocateOut(BaseModel):
    moved: list[int]
    left: list[dict[str, Any]] = Field(description="Titles that stayed, with the reason (also busy, move_failed).")


@router.get(
    "/{version_id}/relocations",
    response_model=list[Relocation],
    response_model_by_alias=True,
    summary="Which titles lie elsewhere than the library rules say",
    description="The preview of moving by the rules: every title of the version whose folder the rules would put "
    "elsewhere, with the reason when it cannot move. Nothing moves.",
    responses=error_responses((404, "not_found")),
)
def get_relocations(version_id: int, db: DbSession) -> list[Relocation]:
    row = db.get(media.VersionDefinition, version_id)
    if row is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    return [Relocation.model_validate(item) for item in folder_rules.relocations(db, row)]


@router.post(
    "/{version_id}/relocate",
    response_model=RelocateOut,
    summary="Move titles by the library rules",
    description="Moves each named title's folder as a whole into the folder its rules name, on the same disk only; a "
    "title either moves whole or stays. The history of the title says where it went.",
    responses=error_responses((404, "not_found")),
)
def relocate(version_id: int, payload: RelocateIn, db: DbSession) -> RelocateOut:
    row = db.get(media.VersionDefinition, version_id)
    if row is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    return RelocateOut.model_validate(folder_rules.relocate(db, row, payload.title_ids))


@router.put(
    "/{version_id}/delay",
    response_model=Version,
    summary="Set the delay rule of a version",
    description=(
        "Which protocol wins between releases that are otherwise equal, whether a protocol is off for this version, "
        "and how many minutes the automatic waits for a better release before it takes one, per protocol. A release "
        "that waits is listed under `GET /api/waiting`; a load the owner clicks never waits. The rule is the delay "
        "profile of Radarr, Sonarr and Lidarr without tags, bound to the version; `tagged` are the profiles with "
        "tags, in their order. Both protocols off is refused. The releases that already "
        "wait for the version get their moment again."
    ),
    responses=error_responses((404, "not_found"), (422, "invalid_input")),
)
def set_delay(version_id: int, payload: DelayWithTags, db: DbSession) -> Version:
    row = db.get(media.VersionDefinition, version_id)
    if row is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    try:
        rule = delay_rules.validate(payload.model_dump(exclude={"tagged"}))
        tagged = []
        for index, item in enumerate(payload.tagged):
            try:
                one = delay_rules.validate(item.model_dump(exclude={"tags"}))
            except delay_rules.RuleInvalid as exc:
                raise delay_rules.RuleInvalid([f"tagged.{index}.{field}" for field in exc.fields]) from exc
            ids = tags.ensure(db, tags.cleaned(item.tags))
            if not ids:
                raise delay_rules.RuleInvalid([f"tagged.{index}.tags"])
            tagged.append(delay_rules.TaggedRule(tuple(sorted(ids.values())), one))
    except delay_rules.RuleInvalid as exc:
        raise error("invalid_input", "The input is not valid.", 422, fields=exc.fields) from exc
    row.delay = delay_rules.to_stored(rule, tagged)
    db.flush()
    waiting.rule_changed(db, row.id)
    db.commit()
    logger.info("Version definition %d: delay rule %s", row.id, "set" if row.delay else "back to the defaults")
    return version_out(db, row)


@router.delete(
    "/{version_id}",
    status_code=204,
    response_model=None,
    summary="Delete a version definition",
    description="Only possible while no source feeds it and no title has it; otherwise 409 with both counts.",
    responses=error_responses((404, "not_found"), (409, "version_in_use")),
)
def delete_version(version_id: int, db: DbSession) -> None:
    row = db.get(media.VersionDefinition, version_id)
    if row is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    sources, titles = _usage(db, version_id)
    if sources or titles:
        raise error(
            "version_in_use",
            f"This version is still in use: by {sources} sources and {titles} titles.",
            409,
            sources=sources,
            titles=titles,
        )
    # ⚠️ Since migration 20 the profile no longer goes with the version by itself. One nobody else points at
    # goes here, so no nameless leftovers pile up; a shared one stays for the others.
    stored = profile_store.of_version(db, version_id)
    db.delete(row)
    db.flush()
    if stored is not None and not profile_store.versions_of(db, stored.id):
        db.delete(stored)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        sources, titles = _usage(db, version_id)
        raise error(
            "version_in_use",
            f"This version is still in use: by {sources} sources and {titles} titles.",
            409,
            sources=sources,
            titles=titles,
        ) from exc
    logger.info("Version definition %d deleted", version_id)
