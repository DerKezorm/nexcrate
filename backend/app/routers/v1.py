"""``/api/v1``: the contract for Nexview, nexbeat and other programs. This module holds the reading routes of stage
V1; how far the contract has come, ``GET /api/v1/system`` says (``STAGE`` in ``services/api_v1``).

Apart from the routes of the interface on purpose: those change with the pages, this stays. Six rules hold for every
address here.

1. Every title carries ``kind``. A program skips a kind it does not know and ignores a field it does not know.
2. A title is named by ``kind`` plus ``ref``, a text of source and number: ``movie`` and ``tmdb:603``. ``ref`` alone
   is not enough, TMDB counts movies and series apart.
3. What only one kind has stands under a key named like the kind (``series``), never on the top level.
4. The scope of a request hangs on the kind and lies under the same key (from V2).
5. A value nexcrate does not know is null, and its field is there all the same.
6. Counting follows the source of the ``ref``: with ``tmdb:`` seasons and episodes are TMDB's.

It opens with a key (``Authorization: Bearer``), never with the session cookie, and errors are flat:
``{"code", "message", "params"}``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, SerializerFunctionWrapHandler, model_serializer

from .. import __version__
from ..deps import DbSession, require_api_key, require_scope
from ..meldungen import error, v1_error_responses
from ..models import ApiKey
from ..models.media import STATES
from ..services import api_v1, companions, ratings, updates, web
from ..services.api_v1 import changes, refs, titles
from ..services.api_v1 import events as v1_events
from ..services.api_v1 import health as v1_health
from ..services.api_v1 import versions as v1_versions
from ..services.series import anime

logger = logging.getLogger("nexcrate.api_v1")

router = APIRouter(prefix="/api/v1", tags=["v1"], dependencies=[Depends(require_scope("read"))])

#: The keys named like a kind (rule 3). One that does not apply is left out instead of being null. ``movie`` since V4:
#: the dates of a movie in a preview; ``album`` and ``artist`` since V5.
KIND_BLOCKS = ("series", "movie", "album", "artist")

#: What each state means, in the order of precedence: the first that applies to a version is its state.
STATE_MEANINGS = {
    "problem": "A download is stuck or failed and waits for the owner.",
    "downloading": "A download for it is running.",
    "incomplete": "Files are there, but tracks of the album are missing. Albums only.",
    "upgrade": "A file is there, and the profile would still take a better one.",
    "available": "A file is there and good enough.",
    "wanted": "Watched and without a file: nexcrate looks for it.",
    "unmonitored": "Without a file, and nexcrate leaves it alone.",
}


class _KindBlocks(BaseModel):
    """Leaves out the block of another kind; every other field stays, null or not."""

    @model_serializer(mode="wrap")
    def _without_foreign_blocks(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        for key in KIND_BLOCKS:
            if key in data and data[key] is None:
                del data[key]
        return data


# --- System ------------------------------------------------------------------------------------ #


class Contract(BaseModel):
    major: int = Field(description="As in the address. Changes only with a break.", examples=[1])
    stage: str = Field(
        description="How far this nexcrate carries the contract: V1 is reading, V2 requesting and taking back, V3 "
        "the queue, problems, reasons, history and events, V4 the calendar, ratings, previews and pairing, V5 "
        "music: albums and artists in every address.",
        examples=["V5"],
    )


class KindAbilities(BaseModel):
    read: bool
    request: bool = Field(description="Adding, watching, searching, taking back, deleting through the bin.")
    operate: bool = Field(description="Resolving problems of downloads and assigning their files by hand.")
    ratings: bool = Field(
        description="Ratings of IMDb (POST /api/v1/ratings); false while switched off, and always for music."
    )


class Capabilities(BaseModel):
    movies: bool
    series: bool
    music: bool = Field(description="Albums and artists, by MusicBrainz ids (`mbid:`).")
    calendar: bool
    events: bool = Field(description="GET /api/v1/events.")
    stream: bool = Field(description="GET /api/v1/events/stream, Server-Sent Events.")
    event_types: list[str] = Field(description="Every event type this nexcrate writes; skip one you do not know.")
    kinds: dict[str, KindAbilities] = Field(description="Per kind what works. A kind that is missing works not at all.")
    wishes_search_at_once: bool = Field(
        description="True since 22.09.2026: a request with `search_now` and `POST .../search` search at once, for "
        "movies, series and albums, also with nexcrate's automatic of that kind off (at most five at a time, the "
        "indexers' limits hold). Missing on an older nexcrate: the old way, the wish waited for the automatic's order.",
    )
    anime: bool = Field(
        description="Whether nexcrate searches, loads and files anime (a series with `series.type` anime). While "
        "false an anime series is requested and watched like any other, but not searched: a request answers "
        "`search: not_possible` with the note `anime_not_supported`, and `why` says `anime_not_supported`. Read a "
        "missing field as false.",
    )


class UpdateOut(BaseModel):
    current: str
    latest: str | None = Field(description="The newest release on GitHub; null while unknown or not asked.")
    available: bool | None = Field(description="Null while the newest release is unknown.")
    checked_at: datetime | None


class SystemOut(BaseModel):
    app: str = Field(examples=["nexcrate"])
    version: str = Field(description="The running nexcrate.", examples=["0.1.0"])
    contract: Contract
    installation_id: str = Field(
        description="Fixed for this installation, whatever address it answers at. Tells a moved nexcrate from another."
    )
    scopes: list[str] = Field(description="What the key of this request may do.")
    capabilities: Capabilities
    web_url: str | None = Field(
        description="The address the owner gave for reaching nexcrate, sub path included; null without one.",
        examples=["https://example.com/nexcrate"],
    )
    links: dict[str, str] = Field(
        description="The jumps into nexcrate's pages, to put behind `web_url` (or the address you reach nexcrate "
        "at): title, version, profile, download, problems, recycle_bin, calendar. They stay whatever the pages "
        "are called.",
    )
    update: UpdateOut


class StateOut(BaseModel):
    state: str
    meaning: str


class StatesOut(BaseModel):
    items: list[StateOut] = Field(description="In the order of precedence: the first that applies wins.")


@router.get(
    "/system",
    response_model=SystemOut,
    summary="What this nexcrate is and can do",
    description=(
        "The version, the stage of the contract, what works per kind, and the fixed id of the installation. Ask this "
        "first: a program that needs more than this nexcrate carries can say so instead of failing."
    ),
)
def system(db: DbSession, key: Annotated[ApiKey, Depends(require_api_key)]) -> SystemOut:
    with_ratings = ratings.imdb_enabled(db)
    kinds = {
        kind: KindAbilities(
            read=True, request=True, operate=kind != "artist", ratings=with_ratings and kind not in api_v1.MUSIC_KINDS
        )
        for kind in api_v1.KINDS
    }
    update = updates.status(db)
    return SystemOut(
        app="nexcrate",
        version=__version__,
        contract=Contract(major=api_v1.CONTRACT, stage=api_v1.STAGE),
        installation_id=companions.installation_id(db),
        scopes=list(key.scopes or []),
        capabilities=Capabilities(
            movies=True,
            series=True,
            music=True,
            calendar=True,
            events=True,
            stream=True,
            event_types=list(v1_events.TYPES),
            kinds=kinds,
            wishes_search_at_once=True,
            anime=anime.SEARCHED,
        ),
        web_url=web.web_url(db),
        links=dict(web.LINKS),
        update=UpdateOut(
            current=update["current"],
            latest=update["latest"],
            available=update["available"],
            checked_at=update["checked_at"],
        ),
    )


@router.get(
    "/states",
    response_model=StatesOut,
    summary="The states a version, a season or an episode can have",
    description=(
        "The list is part of the contract. A later stage may add a state; a program shows one it does not know as "
        "unknown instead of failing."
    ),
)
def states() -> StatesOut:
    return StatesOut(items=[StateOut(state=state, meaning=STATE_MEANINGS[state]) for state in STATES])


# --- Versions ---------------------------------------------------------------------------------- #


class ReasonOut(BaseModel):
    code: str = Field(
        description="no_profile, no_folder, no_indexer, no_download_client, automatic_off or fed_by_source.",
        examples=["no_folder"],
    )
    params: dict[str, Any] = Field(description="`fed_by_source` names the `app`: radarr or sonarr.")


class VersionOut(BaseModel):
    version_id: str = Field(
        description="Fixed for good: renaming the version does not change it. Rights of a program hang on it.",
        examples=["v_7c1e90ab"],
    )
    kind: str
    name: str = Field(examples=["4K"])
    order: int = Field(description="Its place among the versions of its kind, from 1.")
    tier: str | None = Field(
        description="The best resolution its profile allows, in coarse: sd, hd or uhd. Null without a profile. Another "
        "kind may bring other values.",
        examples=["uhd"],
    )
    ready: bool = Field(description="Whether nexcrate would search and load for this version by itself.")
    reasons: list[ReasonOut] = Field(description="Why not; empty when ready.")


class VersionsOut(BaseModel):
    items: list[VersionOut] = Field(description="Per kind none, one or several: assume no number.")


@router.get(
    "/versions",
    response_model=VersionsOut,
    summary="List the versions and whether they are ready",
    description=(
        "A version is a named slot per kind, such as HD and 4K for movies. A version that is not ready does nothing "
        "by itself: offer only ready ones, and tell the owner the reasons of the others."
    ),
    responses=v1_error_responses((422, "kind_unsupported")),
)
def list_versions(db: DbSession, kind: Annotated[str | None, Query(max_length=16)] = None) -> VersionsOut:
    if kind is not None and kind not in api_v1.KINDS:
        raise error("kind_unsupported", f"This nexcrate does not answer for the kind {kind}.", 422, kind=kind)
    return VersionsOut(items=[VersionOut(**item) for item in v1_versions.listing(db, kind)])


# --- Titles ------------------------------------------------------------------------------------ #


class CountsOut(BaseModel):
    have: int = Field(description="Watched episodes that aired and have a file.")
    aired: int = Field(description="Watched episodes that aired.")
    expected: int = Field(description="Watched episodes altogether, those to come included.")


class VersionSeriesBlock(BaseModel):
    counts: CountsOut


class TracksOut(BaseModel):
    have: int = Field(description="Tracks of the target release with a file.")
    total: int = Field(description="Tracks of the target release.")


class VersionAlbumBlock(BaseModel):
    tracks: TracksOut


class CreditOut(BaseModel):
    ref: str | None = Field(description="`mbid:<artist>`; null for a credit MusicBrainz names without an id.")
    name: str | None


class TargetReleaseOut(BaseModel):
    ref: str = Field(description="`mbid:<release>`: the edition nexcrate aims for; the album is asked by its group.")
    name: str | None
    country: str | None
    date: str | None
    formats: list[str]
    tracks: int | None


class TrackFileOut(BaseModel):
    quality: str | None = Field(description="The step: lossless_24, lossless, lossy_high, lossy_mid, lossy_low.")
    size_bytes: int | None


class TrackOut(BaseModel):
    ref: str | None = Field(description="`mbid:<track>`, as delete-files and assigning name a track.")
    number: str | None = Field(description="As printed on the medium, such as A1.")
    position: int
    name: str | None
    length_ms: int | None
    file: TrackFileOut | None


class MediumOut(BaseModel):
    position: int
    format: str | None
    tracks: list[TrackOut]


class AlbumBlock(BaseModel):
    artists: list[CreditOut] = Field(description="Always a list, a compilation's too.")
    type: str | None = Field(description="MusicBrainz's primary type: Album, EP, Single, Broadcast, Other.")
    secondary_types: list[str] = Field(description="Live, Compilation, Soundtrack and the like, as they come.")
    group: str = Field(
        description="The group of the artist page: studio, ep, single, live, compilation, soundtrack, remix, "
        "spoken, other."
    )
    first_release_date: str | None
    cover_url: str | None = Field(description="The Cover Art Archive's picture; nexcrate hands out none itself.")
    target_release: TargetReleaseOut | None
    tracks: TracksOut | None
    media: list[MediumOut] | None = Field(default=None, description="Only where one album is asked for.")


class ArtistAlbumsOut(BaseModel):
    total: int = Field(description="Albums of the artist nexcrate knows, watched or not.")
    watched: int
    available: int
    missing: int = Field(description="Watched and without a file.")


class ArtistBlock(BaseModel):
    sort_name: str | None
    type: str | None = Field(description="Person, Group, Orchestra and the like, as MusicBrainz says.")
    country: str | None
    disambiguation: str | None
    new_albums: str = Field(description="all: new albums of `album_types` get watched; none: not.")
    album_types: list[str] = Field(description="The groups that count, like Lidarr's metadata profile.")
    albums: ArtistAlbumsOut
    loading: bool = Field(description="True while nexcrate still loads its catalogue from MusicBrainz.")
    catalogue: list[TitleOut] | None = Field(
        default=None, description="Every album, only where one artist is asked for."
    )


class TitleVersionOut(_KindBlocks):
    version_id: str | None
    state: str = Field(description="See GET /api/v1/states.")
    monitored: bool = Field(description="False: nexcrate leaves it alone, whatever its state.")
    size_bytes: int | None = Field(description="Null without a file.")
    quality: str | None = Field(description="Of a movie's file. Null without one, and for a series: its files differ.")
    origin: str | None = Field(
        description="The `origin` of the program that added this version; null for the owner's or an import's."
    )
    imported_at: str | None = Field(
        description="Since when the file takes space, ISO 8601: for a movie its current file (a download's import, "
        "Radarr's date of adding the file, or the day nexcrate found it on disk), for a series or season its oldest "
        "file. Null when nexcrate does not know, without a file, and for albums; never a guessed date.",
    )
    series: VersionSeriesBlock | None = None
    album: VersionAlbumBlock | None = None


class SeasonVersionOut(BaseModel):
    version_id: str | None
    state: str
    monitored: bool
    counts: CountsOut
    size_bytes: int | None = Field(
        description="Every file of the season once: a file holding two episodes, and both halves of a double episode."
    )
    imported_at: str | None = Field(
        description="Since when the file takes space, ISO 8601: for a movie its current file (a download's import, "
        "Radarr's date of adding the file, or the day nexcrate found it on disk), for a series or season its oldest "
        "file. Null when nexcrate does not know, without a file, and for albums; never a guessed date.",
    )


class SeasonOut(BaseModel):
    season: int = Field(description="TMDB's number; 0 holds the specials.")
    name: str | None
    air_date: str | None
    episodes: int = Field(description="Episodes TMDB lists.")
    aired: int = Field(description="Of those, how many aired.")
    versions: list[SeasonVersionOut]


class SeriesBlock(BaseModel):
    type: str = Field(
        description="standard, daily or anime: how the series' releases are named. Show a type you do not know as "
        "standard.",
        examples=["standard"],
    )
    status: str | None = Field(description="TMDB's status as it comes.", examples=["Returning Series"])
    next_air_date: str | None
    seasons: list[SeasonOut] | None = Field(
        default=None,
        description="Every season with every version's numbers, size and date, in lists and lookups too; a change of "
        "one season moves the title's `seq`.",
    )


class TitleOut(_KindBlocks):
    kind: str = Field(examples=["series"])
    ref: str = Field(
        description="`tmdb:<number>`, for an album or artist `mbid:<id>`. Together with `kind` it names the title.",
        examples=["tmdb:1399"],
    )
    refs: list[str] = Field(
        description="Every reference nexcrate knows of it.", examples=[["tmdb:1399", "tvdb:121361", "imdb:tt0944947"]]
    )
    name: str | None
    year: int | None
    poster_path: str | None = Field(description="TMDB's path. nexcrate hands out no pictures here.")
    origin: str | None = Field(
        description="The `origin` of the program that added the title; null for the owner's or an import's.",
        examples=["nexview:request:123"],
    )
    monitored: bool = Field(
        description="Whether any of its versions is watched; for an artist, false while it is frozen."
    )
    versions: list[TitleVersionOut] = Field(description="An album has one or none, an artist none.")
    tags: list[str] = Field(
        description="Its tags by name, as in Radarr, Sonarr and Lidarr; an album shows those of its artist."
    )
    series: SeriesBlock | None = None
    album: AlbumBlock | None = None
    artist: ArtistBlock | None = None


class ChangedTitleOut(TitleOut):
    seq: int = Field(description="The number of its last change.")


class RemovedOut(BaseModel):
    kind: str
    ref: str
    seq: int


class TitlesOut(BaseModel):
    items: list[ChangedTitleOut] = Field(description="Titles that are new or changed, the oldest change first.")
    removed: list[RemovedOut] = Field(description="Titles that went away. Never part of a fetch from 0.")
    next_after: int = Field(description="Ask with this next.")
    more: bool = Field(description="True: ask again at once, there is more.")
    latest: int = Field(description="The highest number given so far.")


class LookupItemIn(BaseModel):
    kind: str = Field(max_length=16, examples=["movie"])
    ref: str = Field(max_length=64, examples=["tmdb:603"])


class LookupIn(BaseModel):
    items: list[LookupItemIn] = Field(max_length=titles.LOOKUP_MAX)


class LookupItemOut(BaseModel):
    kind: str
    ref: str = Field(description="As asked.")
    known: bool
    title: TitleOut | None = Field(description="Null for a title nexcrate does not have.")
    error: str | None = Field(
        description="Why this one entry has no answer: kind_unsupported, ref_invalid, ref_source_unknown or "
        "ref_ambiguous. The rest of the batch is answered all the same."
    )


class LookupOut(BaseModel):
    items: list[LookupItemOut] = Field(description="In the order asked.")


class EpisodeFileOut(BaseModel):
    file_id: str = Field(
        description="Opaque and stable while the file lies there. A file that holds several episodes carries the same "
        "id at each of them."
    )
    size_bytes: int | None = Field(description="Size of this one file.")


class EpisodeVersionOut(BaseModel):
    version_id: str | None
    state: str
    monitored: bool
    size_bytes: int | None = Field(description="Size of the file linked to the episode; a second part is not counted.")
    quality: str | None
    files: list[EpisodeFileOut] = Field(
        description="Every file of the episode in this version: the linked one and, for a double episode TMDB lists "
        "as one, its second part. Empty without a file, never null. To add up several episodes, count each file_id "
        "once."
    )


class EpisodeOut(BaseModel):
    episode: int = Field(description="TMDB's number, whatever the file on disk is numbered by.")
    name: str | None
    air_date: str | None
    aired: bool
    versions: list[EpisodeVersionOut]


class SeasonDetailOut(BaseModel):
    season: int
    name: str | None
    episodes: list[EpisodeOut]


def _title_id(db: DbSession, kind: str, raw: str) -> int:
    try:
        ref = refs.parse(kind, raw)
    except refs.RefError as problem:
        if problem.code == "ref_source_unknown":
            raise error(
                "ref_source_unknown",
                f"A {kind} cannot be asked by this source.",
                422,
                kind=kind,
                sources=list(refs.SOURCES[kind]),
            ) from None
        raise error("ref_invalid", "A reference looks like tmdb:603.", 422) from None
    found = titles.find(db, kind, ref)
    if not found:
        raise error("title_not_found", "nexcrate does not have this title.", 404, kind=kind, ref=raw)
    if len(found) > 1:
        raise error("ref_ambiguous", "Several titles carry this reference; ask by tmdb.", 409, kind=kind, ref=raw)
    return found[0]


@router.get(
    "/titles",
    response_model=TitlesOut,
    summary="The library, whole or what changed since a number",
    description=(
        "With `after=0` the whole library in pages; afterwards ask with the `next_after` of the last answer and get "
        "only what changed since, and what went away. A title's number moves whenever anything of what this list "
        "says about it changes, some seconds after the change. A series carries its seasons; episodes are not in the "
        "list: fetch the season when its number moved. `marker_too_old`: the number is older than 90 days of "
        "removals, start over at 0."
    ),
    responses=v1_error_responses((422, "kind_unsupported"), (410, "marker_too_old")),
)
def list_titles(
    db: DbSession,
    after: Annotated[int, Query(ge=0, description="The `next_after` of the last answer; 0 for everything.")] = 0,
    kind: Annotated[str | None, Query(max_length=16)] = None,
    limit: Annotated[int, Query(ge=1, le=changes.PAGE_MAX)] = changes.PAGE_DEFAULT,
) -> TitlesOut:
    if kind is not None and kind not in api_v1.KINDS:
        raise error("kind_unsupported", f"This nexcrate does not answer for the kind {kind}.", 422, kind=kind)
    try:
        found = changes.since(db, after, kind, limit)
    except changes.MarkerTooOld:
        raise error("marker_too_old", "This number is too old; fetch the library again from 0.", 410) from None
    return TitlesOut.model_validate(found)


@router.post(
    "/titles/lookup",
    response_model=LookupOut,
    summary="Look up many titles at once",
    description=(
        f"Up to {titles.LOOKUP_MAX} titles in one call, for a page of tiles that each need a badge. Answers in the "
        "order asked, straight from the library and without the delay of the change numbers. An entry nexcrate cannot "
        "answer carries `error`; the batch never fails for one entry."
    ),
)
def lookup_titles(payload: LookupIn, db: DbSession) -> LookupOut:
    return LookupOut.model_validate({"items": titles.lookup(db, [(item.kind, item.ref) for item in payload.items])})


@router.get(
    "/titles/{kind}/{ref}",
    response_model=TitleOut,
    summary="One title, a series with its seasons",
    description=(
        "The title as the list shows it; a series also with every season and, per version, its state, numbers and "
        "size on disk. `ref` takes every source nexcrate knows for the kind: tmdb and imdb, for a series also tvdb."
    ),
    responses=v1_error_responses(
        (422, "kind_unsupported"),
        (422, "ref_invalid"),
        (422, "ref_source_unknown"),
        (404, "title_not_found"),
        (409, "ref_ambiguous"),
    ),
)
def read_title(kind: str, ref: str, db: DbSession) -> TitleOut:
    if kind not in api_v1.KINDS:
        raise error("kind_unsupported", f"This nexcrate does not answer for the kind {kind}.", 422, kind=kind)
    found = titles.detail(db, _title_id(db, kind, ref))
    if found is None:
        raise error("title_not_found", "nexcrate does not have this title.", 404, kind=kind, ref=ref)
    return TitleOut.model_validate(found)


@router.get(
    "/titles/series/{ref}/seasons/{season}",
    response_model=SeasonDetailOut,
    summary="The episodes of one season",
    description=(
        "Every episode TMDB lists for the season, in TMDB's numbers, with every version's state, size and quality. "
        "Season 0 holds the specials."
    ),
    responses=v1_error_responses(
        (422, "ref_invalid"),
        (422, "ref_source_unknown"),
        (404, "title_not_found"),
        (404, "season_not_found"),
        (409, "ref_ambiguous"),
    ),
)
def read_season(ref: str, season: int, db: DbSession) -> SeasonDetailOut:
    found = titles.season_detail(db, _title_id(db, "series", ref), season)
    if found is None:
        raise error("season_not_found", "The series has no such season.", 404, season=season)
    return SeasonDetailOut.model_validate(found)


# --- Storage and health ------------------------------------------------------------------------ #


class StorageOut(BaseModel):
    version_id: str | None
    kind: str
    volume: str | None = Field(
        description="Versions with the same value share a disk: count its free space once. Null without a folder."
    )
    free_bytes: int | None
    total_bytes: int | None
    problem: str | None = Field(description="folder_missing or folder_not_writable; null when the folder is fine.")


class StorageListOut(BaseModel):
    items: list[StorageOut]


class FindingOut(BaseModel):
    code: str = Field(
        description="indexer_none, indexer_failing, download_client_none, download_client_failing, "
        "tmdb_token_missing, automatic_off, folder_missing, folder_not_writable, disk_full or version_not_ready.",
        examples=["automatic_off"],
    )
    level: str = Field(description="error, warning or notice.")
    message: str = Field(description="English fallback text.")
    params: dict[str, Any]


class HealthOut(BaseModel):
    items: list[FindingOut] = Field(description="The worst first; empty when nothing is wrong.")


@router.get(
    "/storage",
    response_model=StorageListOut,
    summary="Free space behind every version",
    description="Free and total space of the disk each version's folder lies on. Never a path.",
)
def read_storage(db: DbSession) -> StorageListOut:
    return StorageListOut.model_validate({"items": v1_health.storage(db)})


@router.get(
    "/health",
    response_model=HealthOut,
    summary="What is wrong with this nexcrate",
    description=(
        "Findings from what nexcrate already knows: asking sends no request anywhere and wakes no disk. Translate by "
        "`code`; `params` carry the names."
    ),
)
def read_health(db: DbSession) -> HealthOut:
    return HealthOut.model_validate({"items": v1_health.findings(db)})


ArtistBlock.model_rebuild()
