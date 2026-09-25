"""What other programs may change through ``/api/v1``, stage V2.

Requesting and requesting more are one call: a title that is not there is added from TMDB, versions it lacks are
added, and seasons and episodes are switched on. Taking back switches off, stops running downloads and, when asked,
moves files into the recycle bin; a title or version a program brought and that is empty afterwards goes (the owner's
answer 4). Freezing only switches. A search a program asks for is a wish the automatic fulfils in its order, even with
its switch off (answer 3).

Every change here goes through the same functions the interface uses: ``library.add_owner_version``,
``watching.set_season`` and ``apply_rule``, ``download_store.follow_version``, the recycle bin. Nothing writes columns
the read side renders differently.

⚠️ Log lines carry ids, counts and codes, never a title, the program's ``origin`` or the key's name.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from ...db import SessionLocal
from ...meldungen import error
from ...models import (
    DownloadEpisode,
    Episode,
    EpisodeFile,
    EpisodeVersion,
    HistoryEntry,
    ReleaseTrack,
    Season,
    SeasonVersion,
    Title,
    TrackFile,
    Version,
    VersionDefinition,
    utcnow,
)
from .. import companions, images, library, recycle_bin, tmdb
from ..automatic import clock as automatic_clock
from ..automatic import planning as automatic_planning
from ..downloads import actions as download_actions
from ..downloads import store as download_store
from ..music import musicbrainz as mb
from ..music import store as music_store
from ..search import jobs as search_jobs
from ..series import anime, folder_read, tmdb_series, watching
from ..series import store as series_store
from . import KINDS, refs, titles
from . import versions as v1_versions

logger = logging.getLogger("nexcrate.api_v1")

ORIGIN_MAX_LENGTH = 100
#: Versions one request may name; more is no request but a mistake.
VERSIONS_MAX = 20
EPISODES_MAX = 500
#: The states in which a version still wants something the automatic can search for; an album that misses tracks
#: is searched again too.
WANTING_STATES = ("wanted", "upgrade", "incomplete")
#: The kinds with one switch per version and no scope: a movie, and an album.
SINGLE = ("movie", "album")
#: The reference a title nexcrate does not have yet is added by, per kind.
ANCHOR = {"movie": "tmdb", "series": "tmdb", "album": "mbid"}
TRACKS_MAX = 500


@dataclass(frozen=True)
class SeriesScope:
    """The scope under ``series`` (rule 4). ``seasons`` None means every season but the specials, unless episodes are
    named: then it is those episodes alone (Nexview's round of 25.09.2026). ``future_seasons`` None means: only the
    whole series brings later seasons (the owner's answer of 25.09.2026)."""

    seasons: tuple[int, ...] | None = None
    future_seasons: bool | None = None
    episodes: tuple[tuple[int, int], ...] = ()

    @property
    def whole(self) -> bool:
        return self.seasons is None and not self.episodes

    @property
    def later_seasons(self) -> bool:
        return self.whole if self.future_seasons is None else self.future_seasons


@dataclass(frozen=True)
class Caller:
    """The key a request came with: its name goes into the history and next to ``origin``."""

    key_id: int
    key_name: str

    @property
    def actor(self) -> recycle_bin.Actor:
        return recycle_bin.Actor.key(self.key_name)


@dataclass
class VersionOutcome:
    version_id: str | None
    outcome: str = "unchanged"
    monitoring_off: bool = False
    downloads_cancelled: int = 0
    files_recycled: int = 0
    version_removed: bool = False
    still_monitored: bool = False


@dataclass
class Plan:
    """What a take-back will do, found before anything changes."""

    title_id: int
    definition_ids: list[int]
    running: list[tuple[int, str, int | None]] = field(default_factory=list)


# --- Checks shared by every address ------------------------------------------------------------------------------ #


def clean_origin(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    if len(text) > ORIGIN_MAX_LENGTH or not text.isprintable():
        raise error("invalid_input", "The input is not valid.", 422, fields=["origin"])
    return text


def _kind(kind: str) -> None:
    if kind not in KINDS:
        raise error("kind_unsupported", f"This nexcrate does not answer for the kind {kind}.", 422, kind=kind)


def parse_ref(kind: str, raw: str) -> refs.Ref:
    try:
        return refs.parse(kind, raw)
    except refs.RefError as problem:
        if problem.code == "ref_source_unknown":
            raise error(
                "ref_source_unknown",
                f"A {kind} cannot be asked by this source.",
                422,
                kind=kind,
                sources=list(refs.SOURCES[kind]),
            ) from None
        raise error("ref_invalid", "A reference looks like tmdb:603 or mbid:<id>.", 422) from None


def find_title(db: OrmSession, kind: str, raw: str) -> Title | None:
    """The title ``kind`` and ``ref`` name, or None. Several by one reference is ``409 ref_ambiguous``."""
    _kind(kind)
    if kind == "artist":
        raise error("kind_unsupported", "An artist is no title; this address takes titles.", 422, kind=kind)
    found = titles.find(db, kind, parse_ref(kind, raw))
    if len(found) > 1:
        raise error("ref_ambiguous", "Several titles carry this reference; ask by tmdb.", 409, kind=kind, ref=raw)
    return db.get(Title, found[0]) if found else None


def title_of(db: OrmSession, kind: str, raw: str) -> Title:
    title = find_title(db, kind, raw)
    if title is None:
        raise error("title_not_found", "nexcrate does not have this title.", 404, kind=kind, ref=raw)
    return title


def definitions_of(db: OrmSession, kind: str, public_ids: list[str]) -> list[VersionDefinition]:
    """The version definitions behind fixed ids, in the order given, each once."""
    v1_versions.ensure_public_ids(db)
    rows = {
        row.public_id: row
        for row in db.scalars(select(VersionDefinition).where(VersionDefinition.public_id.in_(public_ids)))
    }
    chosen: list[VersionDefinition] = []
    for public_id in dict.fromkeys(public_ids):
        row = rows.get(public_id)
        if row is None:
            raise error("version_unknown", "nexcrate has no version with this id.", 422, version_id=public_id)
        if row.kind != kind:
            raise error(
                "version_kind_mismatch", f"This version is not one for a {kind}.", 422, version_id=public_id, kind=kind
            )
        chosen.append(row)
    return chosen


def definition_ids_of(db: OrmSession, kind: str, public_ids: list[str] | None) -> tuple[int, ...] | None:
    if public_ids is None:
        return None
    if not public_ids:
        raise error("invalid_input", "The input is not valid.", 422, fields=["versions"])
    return tuple(row.id for row in definitions_of(db, kind, public_ids))


def check_scope(kind: str, series: SeriesScope | None, tracks: list[str] | None = None) -> None:
    if series is not None and kind != "series":
        raise error("scope_not_for_kind", f"A {kind} has no seasons or episodes.", 422, kind=kind)
    if tracks is not None and kind != "album":
        raise error("scope_not_for_kind", f"A {kind} has no tracks.", 422, kind=kind)


def track_ids_of(db: OrmSession, title: Title, raw: list[str]) -> tuple[int, ...]:
    """The tracks of an album by ``mbid:<track>``, among the releases nexcrate knows of it. An unknown one is
    ``422 track_not_found``."""
    wanted: dict[str, str] = {}
    for item in raw:
        try:
            ref = refs.parse("album", item)
        except refs.RefError:
            raise error("ref_invalid", "A track looks like mbid:<id>.", 422) from None
        wanted.setdefault(ref.value, item)
    from ...models import Release

    found = dict(
        db.execute(
            select(ReleaseTrack.mbid, ReleaseTrack.id)
            .join(Release, Release.id == ReleaseTrack.release_id)
            .where(Release.title_id == title.id, ReleaseTrack.mbid.in_(list(wanted)))
        )
        .tuples()
        .all()
    )
    for mbid, item in wanted.items():
        if mbid not in found:
            raise error("track_not_found", "The album has no such track.", 422, track=item)
    return tuple(found[mbid] for mbid in wanted)


def _seasons_by_number(db: OrmSession, title: Title) -> dict[int, Season]:
    return {
        season.number: season
        for season in db.scalars(select(Season).where(Season.title_id == title.id).order_by(Season.number, Season.id))
    }


def _episode_ids(db: OrmSession, title: Title, pairs: tuple[tuple[int, int], ...]) -> dict[tuple[int, int], int]:
    found: dict[tuple[int, int], int] = {}
    if not pairs:
        return found
    wanted = set(pairs)
    for episode_id, season, number in db.execute(
        select(Episode.id, Episode.season_number, Episode.episode_number).where(
            Episode.title_id == title.id, Episode.tmdb_gone_at.is_(None)
        )
    ).tuples():
        if (season, number) in wanted and (season, number) not in found:
            found[(season, number)] = episode_id
    for season, number in pairs:
        if (season, number) not in found:
            raise error(
                "episode_not_found", "The series has no such episode.", 422, season=season, episode=number
            )
    return found


def _checked_seasons(seasons: dict[int, Season], scope: SeriesScope) -> list[int]:
    """The season numbers a scope names; every season but the specials when it names neither seasons nor episodes."""
    if scope.seasons is None:
        return [] if scope.episodes else [number for number in seasons if number != 0]
    for number in scope.seasons:
        if number not in seasons:
            raise error("season_not_found", "The series has no such season.", 422, season=number)
    return list(dict.fromkeys(scope.seasons))


def _history_entry(
    version: Version, label: str, event: str, caller: Caller, origin: str | None, moment: datetime
) -> HistoryEntry:
    """``detail`` is the key's name, and after a line break the program's origin."""
    detail = caller.key_name if origin is None else f"{caller.key_name}\n{origin}"
    return HistoryEntry(
        title_id=version.title_id,
        version_id=version.id,
        version_definition_id=version.version_definition_id,
        version_label=label[:64],
        event=event,
        at=moment,
        detail=detail[:1024],
        data={"by": caller.key_name, "origin": origin},
    )


# --- Switching seasons and episodes ------------------------------------------------------------------------------- #


def _season_fully_watched(db: OrmSession, version: Version, season: Season) -> bool:
    row = db.get(SeasonVersion, (season.id, version.id))
    if row is None or not row.watched:
        return False
    off = db.scalar(
        select(EpisodeVersion.episode_id)
        .join(Episode, Episode.id == EpisodeVersion.episode_id)
        .where(
            EpisodeVersion.version_id == version.id,
            EpisodeVersion.watched.is_(False),
            Episode.season_id == season.id,
            Episode.tmdb_gone_at.is_(None),
        )
        .limit(1)
    )
    return off is None


def _season_watches_any(db: OrmSession, version: Version, season: Season) -> bool:
    row = db.get(SeasonVersion, (season.id, version.id))
    if row is not None and row.watched:
        return True
    return (
        db.scalar(
            select(EpisodeVersion.episode_id)
            .join(Episode, Episode.id == EpisodeVersion.episode_id)
            .where(
                EpisodeVersion.version_id == version.id,
                EpisodeVersion.watched.is_(True),
                Episode.season_id == season.id,
            )
            .limit(1)
        )
        is not None
    )


def switch_on(db: OrmSession, title: Title, version: Version, scope: SeriesScope, on: str, *, initial: bool) -> bool:
    """Switch on what a scope names. ``initial`` for a version the request just made: its rule is set already, and every
    season is then brought to what the scope says, off as well as on. Otherwise only ever on. Returns whether anything
    changed."""
    seasons = _seasons_by_number(db, title)
    named = set(_checked_seasons(seasons, scope))
    episodes = _episode_ids(db, title, scope.episodes)
    changed = False
    for number, season in seasons.items():
        should = number in named
        if initial:
            row = db.get(SeasonVersion, (season.id, version.id))
            current = bool(row and row.watched)
            if row is not None and (current != should or (should and not _season_fully_watched(db, version, season))):
                watching.set_season(db, version, season.id, should, on)
                changed = True
        elif should and not _season_fully_watched(db, version, season):
            watching.set_season(db, version, season.id, True, on)
            changed = True
    for episode_id in episodes.values():
        row = db.get(EpisodeVersion, (episode_id, version.id))
        if row is not None and not row.watched:
            watching.set_episode(db, version, episode_id, True, on)
            changed = True
    if not initial and scope.later_seasons and version.watch_rule == "none":
        # Seasons TMDB names later come by themselves again; the switches of today stay as they are.
        version.watch_rule, version.watch_from_season = "all", None
        changed = True
    return changed


def switch_off(db: OrmSession, title: Title, version: Version, scope: SeriesScope, on: str) -> bool:
    """Switch off what a scope names; the whole series also takes the rule ``none``, so no later season comes."""
    if scope.whole:
        change = watching.apply_rule(db, version, "none", None, on, write=False)
        if change.watched == 0 and version.watch_rule == "none":
            return False
        watching.apply_rule(db, version, "none", None, on, write=True)
        return True
    seasons = _seasons_by_number(db, title)
    named = _checked_seasons(seasons, scope) if scope.seasons is not None else []
    episodes = _episode_ids(db, title, scope.episodes)
    changed = False
    for number in named:
        season = seasons[number]
        if _season_watches_any(db, version, season):
            watching.set_season(db, version, season.id, False, on)
            changed = True
    for episode_id in episodes.values():
        row = db.get(EpisodeVersion, (episode_id, version.id))
        if row is not None and row.watched:
            watching.set_episode(db, version, episode_id, False, on)
            changed = True
    return changed


def _series_watches(db: OrmSession, version: Version) -> bool:
    return (
        db.scalar(
            select(EpisodeVersion.episode_id)
            .where(EpisodeVersion.version_id == version.id, EpisodeVersion.watched.is_(True))
            .limit(1)
        )
        is not None
    )


def _series_has_files(db: OrmSession, version: Version) -> bool:
    return db.scalar(select(EpisodeFile.id).where(EpisodeFile.version_id == version.id).limit(1)) is not None


def _empty(db: OrmSession, title: Title, version: Version) -> bool:
    """Watches nothing and has no file."""
    if title.kind == "movie":
        return not version.monitored and not version.has_file
    if title.kind == "album":
        has_files = db.scalar(select(TrackFile.id).where(TrackFile.version_id == version.id).limit(1))
        return not version.monitored and has_files is None
    return not _series_watches(db, version) and not _series_has_files(db, version)


# --- Requesting ------------------------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RequestIn:
    kind: str
    ref: str
    versions: list[str]
    series: SeriesScope | None
    search_now: bool
    origin: str | None


def _check_request(payload: RequestIn) -> tuple[Title | None, list[VersionDefinition]]:
    """Everything that needs no TMDB: kind, reference, versions, scope. Returns the title when it is there."""
    _kind(payload.kind)
    check_scope(payload.kind, payload.series)
    if not payload.versions and payload.kind != "album":
        raise error("invalid_input", "The input is not valid.", 422, fields=["versions"])
    with SessionLocal() as db:
        title = find_title(db, payload.kind, payload.ref)
        if payload.kind == "album" and not payload.versions:
            # Music has one version (decision 10); naming it is not needed.
            only = music_store.definition(db)
            if only is None:
                raise error("version_not_available", "nexcrate has no music version yet.", 409, kind="album")
            v1_versions.ensure_public_ids(db)
            definitions = [only]
        else:
            definitions = definitions_of(db, payload.kind, payload.versions)
        if title is None and not payload.ref.startswith(f"{ANCHOR[payload.kind]}:"):
            raise error(
                "ref_not_addable",
                "A title nexcrate does not have yet is added by its TMDB number.",
                422,
                kind=payload.kind,
                ref=payload.ref,
            )
        if title is not None:
            db.expunge(title)
        return title, definitions


def _wanting(db: OrmSession, title_id: int) -> bool:
    return (
        db.scalar(
            select(Version.id)
            .where(
                Version.title_id == title_id,
                Version.source_id.is_(None),
                Version.monitored.is_(True),
                Version.state.in_(WANTING_STATES),
            )
            .limit(1)
        )
        is not None
    )


def _search_under_way(title: Title) -> str | None:
    """``running`` or ``queued`` when a search of the title runs or waits already, as ``wish_search`` says it."""
    if search_jobs.running_search(title.id) is not None:
        return "running"
    return "queued" if title.search_wish_at is not None else None


def _add_version(db: OrmSession, title: Title, definition: VersionDefinition, payload: RequestIn, caller: Caller,
                 moment: datetime, on: str, reading: list[int]) -> Version:  # fmt: skip
    if title.kind == "album":
        version = music_store.add_version(db, title, definition, moment)
    else:
        version = library.add_owner_version(db, title, definition, moment)
    version.origin = payload.origin
    version.origin_key = caller.key_name[:ORIGIN_MAX_LENGTH]
    if title.kind == "series":
        scope = payload.series or SeriesScope()
        version.watch_rule = "all" if scope.later_seasons else "none"
        watching.sync_rows(db, version, on)
        switch_on(db, title, version, scope, on, initial=True)
        watching.recount(db, version, on)
        if folder_read.claim(db, version, moment):
            reading.append(version.id)
    return version


def _apply_request(title_id: int, definitions: list[int], payload: RequestIn, caller: Caller, created: bool,
                   moment: datetime) -> tuple[list[VersionOutcome], str, list[dict[str, Any]], list[int]]:  # fmt: skip
    """Add what the title lacks and switch on what the request names, in one transaction."""
    on = watching.today()
    reading: list[int] = []
    outcomes: list[VersionOutcome] = []
    with SessionLocal() as db:
        names = v1_versions.public_ids(db)
        title = db.get(Title, title_id)
        if title is None:
            raise error(
                "title_not_found", "nexcrate does not have this title.", 404, kind=payload.kind, ref=payload.ref
            )
        current = {
            version.version_definition_id: version
            for version in db.scalars(select(Version).where(Version.title_id == title.id))
        }
        rows = {
            row.id: row for row in db.scalars(select(VersionDefinition).where(VersionDefinition.id.in_(definitions)))
        }
        for definition_id in definitions:
            definition = rows[definition_id]
            outcome = VersionOutcome(names.get(definition_id))
            version = current.get(definition_id)
            if version is None:
                version = _add_version(db, title, definition, payload, caller, moment, on, reading)
                outcome.outcome = "added"
            elif version.source_id is not None:
                raise recycle_bin.fed_by_source()
            elif created and title.kind == "album" and version.origin_key is None:
                # Adding an album makes its version with it, as the interface does; this request brought it.
                version.origin = payload.origin
                version.origin_key = caller.key_name[:ORIGIN_MAX_LENGTH]
                outcome.outcome = "added"
            elif title.kind in SINGLE:
                if not version.monitored:
                    version.monitored = True
                    version.updated_at = moment
                    db.flush()
                    download_store.follow_version(db, title.id, definition_id, moment)
                    outcome.outcome = "extended"
            elif switch_on(db, title, version, payload.series or SeriesScope(), on, initial=False):
                watching.recount(db, version, on)
                version.updated_at = moment
                outcome.outcome = "extended"
            if outcome.outcome != "unchanged":
                db.add(_history_entry(version, definition.label, "requested", caller, payload.origin, moment))
            outcomes.append(outcome)
        if created:
            title.origin = payload.origin
            title.origin_key = caller.key_name[:ORIGIN_MAX_LENGTH]
        db.flush()
        automatic_planning.replan(db, [title.id], automatic_clock.now())
        search = "not_asked"
        anime_waits = anime.not_searched(title.kind, title.series_type)
        if payload.search_now:
            if anime_waits:
                # Taken and watched, but not searched: no wish that is dropped at once.
                search = "not_possible"
            elif _wanting(db, title.id):
                title.search_wish_at = moment
                search = "queued"
            else:
                # Nothing more to want, but a search may already be on its way (Nexview's round of 25.09.2026).
                search = _search_under_way(title) or "nothing_wanted"
        if any(outcome.outcome != "unchanged" for outcome in outcomes) or created:
            title.updated_at = moment
        db.commit()
        if search == "queued":
            from ..automatic import wishes as automatic_wishes

            # At once, not with the automatic's order (the owner's answer of 22.09.2026).
            automatic_wishes.kick()
        notes = _notes(db, [rows[definition_id] for definition_id in definitions])
        if anime_waits:
            notes.append({"code": "anime_not_supported", "params": {}})
    return outcomes, search, notes, reading


def _notes(db: OrmSession, definitions: list[VersionDefinition]) -> list[dict[str, Any]]:
    """Honest hints: a version that is not ready does nothing by itself (V1's reasons)."""
    notes: list[dict[str, Any]] = []
    ready = {item["version_id"]: item for item in v1_versions.listing(db, None)}
    for definition in definitions:
        item = ready.get(definition.public_id)
        if item is not None and not item["ready"]:
            notes.append(
                {
                    "code": "version_not_ready",
                    "params": {"version_id": definition.public_id, "reasons": item["reasons"]},
                }
            )
    return notes


def _create_movie(data: tmdb.MovieData) -> tuple[int, bool]:
    """The movie without versions yet; they come with the request. An existing one wins a race."""
    with SessionLocal() as db:
        existing = db.scalar(select(Title.id).where(Title.kind == "movie", Title.tmdb_id == data.tmdb_id))
        if existing is not None:
            return existing, False
        try:
            title = library.add_title(db, data, [], utcnow())
            db.commit()
        except IntegrityError:
            db.rollback()
            found = db.scalar(select(Title.id).where(Title.kind == "movie", Title.tmdb_id == data.tmdb_id))
            if found is None:
                raise
            return found, False
        return title.id, True


def _create_series(data: tmdb_series.SeriesData) -> tuple[int, bool]:
    with SessionLocal() as db:
        existing = db.scalar(select(Title.id).where(Title.kind == "series", Title.tmdb_id == data.tmdb_id))
        if existing is not None:
            return existing, False
        moment, on = utcnow(), watching.today()
        try:
            title = series_store.new_title(data, moment, None)
            db.add(title)
            db.flush()
            series_store.apply_series(db, title, data, moment, on)
            db.commit()
        except IntegrityError:
            db.rollback()
            found = db.scalar(select(Title.id).where(Title.kind == "series", Title.tmdb_id == data.tmdb_id))
            if found is None:
                raise
            return found, False
        return title.id, True


async def _create_album(mbid: str) -> tuple[int, bool]:
    """An album from MusicBrainz, made as "Add album" in the interface makes it: with its version, and with the
    artists of its credit that the library lacks, whose new albums are not watched (decision 34)."""
    from ...routers import music as music_router

    try:
        group = await mb.lookup_release_group(mbid, mb.OWNER)
        artists: list[mb.ArtistData] = []
        for part in group.credit:
            credited = str(part.get("mbid") or "")
            if credited and credited != music_store.VARIOUS_ARTISTS_MBID and mb.valid_mbid(credited):
                artists.append(await mb.lookup_artist(credited, mb.OWNER))
    except mb.MusicBrainzError as exc:
        logger.info("A request for an album failed at MusicBrainz: %s", exc.code)
        raise exc.http() from exc
    return await asyncio.to_thread(music_router._create_album, group, artists)


async def _fetch_and_create(kind: str, value: str) -> tuple[int, bool]:
    if kind == "album":
        return await _create_album(value)
    tmdb_id = int(value)
    token = await asyncio.to_thread(tmdb.require_token)
    locale = await asyncio.to_thread(tmdb.account_locale)
    try:
        if kind == "movie":
            movie = await tmdb.fetch_movie(token, tmdb_id, locale)
            return await asyncio.to_thread(_create_movie, movie)
        series = await tmdb_series.fetch_series(token, tmdb_id, locale)
    except tmdb.TmdbError as exc:
        logger.info("A request for TMDB %s %d failed at TMDB: %s", kind, tmdb_id, exc.code)
        raise exc.http() from exc
    return await asyncio.to_thread(_create_series, series)


def _drop_if_empty(title_id: int) -> None:
    """A title the request created and left without any version (a failed step) does not stay."""
    with SessionLocal() as db:
        title = db.get(Title, title_id)
        if title is not None and db.scalar(select(Version.id).where(Version.title_id == title_id).limit(1)) is None:
            db.delete(title)
            db.commit()


async def request(payload: RequestIn, caller: Caller) -> dict[str, Any]:
    """Add or request more (N17, N18, N19). Idempotent over kind, reference and version."""
    title, definitions = await asyncio.to_thread(_check_request, payload)
    created = False
    if title is None:
        title_id, created = await _fetch_and_create(payload.kind, parse_ref(payload.kind, payload.ref).value)
    else:
        title_id = title.id
    wanted = [definition.id for definition in definitions]
    try:
        try:
            outcomes, search, notes, reading = await asyncio.to_thread(
                _apply_request, title_id, wanted, payload, caller, created, utcnow()
            )
        except IntegrityError:
            # The same request ran at the same moment and added the version first: once more, it then finds it.
            outcomes, search, notes, reading = await asyncio.to_thread(
                _apply_request, title_id, wanted, payload, caller, created, utcnow()
            )
    except BaseException:
        if created:
            await asyncio.to_thread(_drop_if_empty, title_id)
        raise
    if reading:
        folder_read.enqueue(reading)
    logger.info(
        "Title %d requested through key %d: %s, search %s",
        title_id,
        caller.key_id,
        ",".join(outcome.outcome for outcome in outcomes),
        search,
    )
    return {
        "created": created,
        "versions": [{"version_id": outcome.version_id, "outcome": outcome.outcome} for outcome in outcomes],
        "search": search,
        "notes": notes,
        "title": await asyncio.to_thread(_detail, title_id),
    }


def _detail(title_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        found = titles.detail(db, title_id)
    if found is None:
        raise error("title_not_found", "nexcrate does not have this title.", 404)
    return found


# --- Taking back ------------------------------------------------------------------------------------------------ #


def _scope_episode_ids(db: OrmSession, title: Title, scope: SeriesScope) -> set[int] | None:
    """The episodes a series scope covers; None for the whole series."""
    if scope.whole:
        return None
    seasons = _seasons_by_number(db, title)
    named = set(_checked_seasons(seasons, scope)) if scope.seasons is not None else set()
    ids = set(_episode_ids(db, title, scope.episodes).values())
    if named:
        ids |= set(
            db.scalars(select(Episode.id).where(Episode.title_id == title.id, Episode.season_number.in_(named)))
        )
    return ids


def _plan_withdraw(kind: str, ref: str, public_ids: list[str] | None, scope: SeriesScope | None) -> Plan:
    check_scope(kind, scope)
    with SessionLocal() as db:
        title = title_of(db, kind, ref)
        definition_ids = definition_ids_of(db, kind, public_ids)
        versions = recycle_bin.own_versions(db, title, definition_ids)
        chosen = {version.version_definition_id for version in versions}
        covered = _scope_episode_ids(db, title, scope or SeriesScope())
        running: list[tuple[int, str, int | None]] = []
        for download in download_store.pending_downloads(db, title.id):
            if download.version_definition_id not in chosen:
                continue
            if covered is not None:
                episodes = set(
                    db.scalars(select(DownloadEpisode.episode_id).where(DownloadEpisode.download_id == download.id))
                )
                if not episodes or not episodes <= covered:
                    continue
            running.append((download.id, download.state, download.version_definition_id))
        return Plan(title.id, sorted(chosen), running)


async def cancel_downloads(running: list[tuple[int, str, int | None]]) -> dict[int, int]:
    """Stop each download at its client, without the blocklist. Per version definition how many stopped.

    ⚠️ A download being filed away cannot be stopped: that is refused before any client is asked, and nothing changes.
    """
    if any(state == "importing" for _download_id, state, _definition in running):
        raise download_actions.download_finished().http()
    stopped: dict[int, int] = {}
    for download_id, _state, definition_id in running:
        try:
            await download_actions.remove(download_id, remove_from_client=True, blocklist=False)
        except download_actions.ActionError as exc:
            if exc.detail["code"] == "download_not_found":
                continue
            raise exc.http() from exc
        if definition_id is not None:
            stopped[definition_id] = stopped.get(definition_id, 0) + 1
    return stopped


def _remove_versions(db: OrmSession, title: Title, versions: list[Version]) -> list[companions.Removal]:
    removals = companions.plan_removal(db, versions) if companions.enabled(db) else []
    for version in versions:
        db.delete(version)
    db.flush()
    return removals


def _apply_withdraw(plan: Plan, scope: SeriesScope | None, delete_files: bool, caller: Caller,
                    stopped: dict[int, int]) -> dict[str, Any]:  # fmt: skip
    on, moment = watching.today(), utcnow()
    with SessionLocal() as db:
        title = db.get(Title, plan.title_id)
        if title is None:
            raise error("title_not_found", "nexcrate does not have this title.", 404)
        names = v1_versions.public_ids(db)
        versions = recycle_bin.own_versions(db, title, tuple(plan.definition_ids))
        labels = {row.id: row.label for row in db.scalars(select(VersionDefinition))}
        series_scope = scope or SeriesScope()
        outcomes: dict[int, VersionOutcome] = {}
        for version in versions:
            outcome = VersionOutcome(names.get(version.version_definition_id))
            outcome.downloads_cancelled = stopped.get(version.version_definition_id, 0)
            if title.kind in SINGLE:
                if version.monitored:
                    version.monitored = False
                    version.updated_at = moment
                    db.flush()
                    download_store.follow_version(db, title.id, version.version_definition_id, moment)
                    outcome.monitoring_off = True
            elif switch_off(db, title, version, series_scope, on):
                watching.recount(db, version, on)
                version.updated_at = moment
                outcome.monitoring_off = True
            outcomes[version.version_definition_id] = outcome
        result = recycle_bin.Result()
        if delete_files:
            bin_scope = recycle_bin.Scope(
                definition_ids=tuple(plan.definition_ids),
                seasons=series_scope.seasons,
                episodes=series_scope.episodes,
            )
            result = recycle_bin.delete_in(db, title, bin_scope, caller.actor, moment)
            for done in result.versions:
                outcomes[done.definition_id].files_recycled = done.files
        try:
            for version in versions:
                outcome = outcomes[version.version_definition_id]
                if outcome.monitoring_off or outcome.downloads_cancelled or outcome.files_recycled:
                    db.add(
                        _history_entry(version, labels.get(version.version_definition_id, ""), "withdrawn", caller,
                                       None, moment)
                    )  # fmt: skip
            db.flush()
            # Answer 4: what a program brought and what is empty now goes. The key's name marks it; ``origin`` is the
            # program's choice and may be missing.
            going = [version for version in versions if version.origin_key is not None and _empty(db, title, version)]
            for version in going:
                outcomes[version.version_definition_id].version_removed = True
            removals = _remove_versions(db, title, going)
            left = list(db.scalars(select(Version).where(Version.title_id == title.id)))
            title_removed = False
            # An album stays as a part of its artist's catalogue; only its version goes.
            if title.kind != "album" and title.origin_key is not None and all(
                version.source_id is None and _empty(db, title, version) for version in left
            ):
                removals += _remove_versions(db, title, left)
                for version in left:
                    if version.version_definition_id in outcomes:
                        outcomes[version.version_definition_id].version_removed = True
                db.delete(title)
                title_removed = True
            else:
                title.updated_at = moment
                automatic_planning.replan(db, [title.id], automatic_clock.now())
            db.commit()
        except BaseException:
            db.rollback()
            recycle_bin.undo(result.moves)
            raise
    recycle_bin.tell_media_servers(result.folders)
    if removals:
        companions.remove(removals)
    if title_removed:
        images.forget([plan.title_id])
    logger.info(
        "Title %d taken back through key %d: %d versions, %d downloads stopped, %d files recycled, title removed %s",
        plan.title_id,
        caller.key_id,
        len(versions),
        sum(stopped.values()),
        result.files,
        title_removed,
    )
    return {
        "title_removed": title_removed,
        "versions": [_outcome_out(outcome) for outcome in outcomes.values()],
    }


def _outcome_out(outcome: VersionOutcome) -> dict[str, Any]:
    return {
        "version_id": outcome.version_id,
        "monitoring_off": outcome.monitoring_off,
        "downloads_cancelled": outcome.downloads_cancelled,
        "files_recycled": outcome.files_recycled,
        "version_removed": outcome.version_removed,
    }


async def withdraw(kind: str, ref: str, public_ids: list[str] | None, scope: SeriesScope | None, delete_files: bool,
                   caller: Caller) -> dict[str, Any]:  # fmt: skip
    """Take a request back (N20): watching off, running downloads stopped, files into the bin when asked."""
    plan = await asyncio.to_thread(_plan_withdraw, kind, ref, public_ids, scope)
    stopped = await cancel_downloads(plan.running)
    return await asyncio.to_thread(_apply_withdraw, plan, scope, delete_files, caller, stopped)


# --- Freezing ------------------------------------------------------------------------------------------------- #


def set_monitoring(kind: str, ref: str, monitored: bool, public_ids: list[str] | None, scope: SeriesScope | None,
                   caller: Caller) -> dict[str, Any]:  # fmt: skip
    """Freeze or thaw (N43): only the switches; no download stops, no file moves. Thawing a whole series takes the rule
    ``all``; thawing seasons or episodes switches just those on."""
    check_scope(kind, scope)
    on, moment = watching.today(), utcnow()
    with SessionLocal() as db:
        title = title_of(db, kind, ref)
        versions = recycle_bin.own_versions(db, title, definition_ids_of(db, kind, public_ids))
        names = v1_versions.public_ids(db)
        series_scope = scope or SeriesScope()
        changed: list[dict[str, Any]] = []
        for version in versions:
            moved = False
            if title.kind in SINGLE:
                if version.monitored != monitored:
                    version.monitored = monitored
                    db.flush()
                    download_store.follow_version(db, title.id, version.version_definition_id, moment)
                    moved = True
            elif not monitored:
                moved = switch_off(db, title, version, series_scope, on)
            elif series_scope.whole:
                change = watching.apply_rule(db, version, "all", None, on, write=False)
                if change.added or version.watch_rule != "all":
                    watching.apply_rule(db, version, "all", None, on, write=True)
                    moved = True
            else:
                moved = switch_on(db, title, version, SeriesScope(series_scope.seasons, False, series_scope.episodes),
                                  on, initial=False)  # fmt: skip
            if moved:
                if title.kind == "series":
                    watching.recount(db, version, on)
                version.updated_at = moment
            changed.append({"version_id": names.get(version.version_definition_id), "changed": moved})
        if any(item["changed"] for item in changed):
            title.updated_at = moment
            automatic_planning.replan(db, [title.id], automatic_clock.now())
        db.commit()
        title_id = title.id
    logger.info("Title %d %s through key %d", title_id, "thawed" if monitored else "frozen", caller.key_id)
    return {"versions": changed, "title": _detail(title_id)}


# --- Deleting files, searching ------------------------------------------------------------------------------------ #


def delete_files(kind: str, ref: str, public_ids: list[str] | None, scope: SeriesScope | None,
                 caller: Caller, tracks: list[str] | None = None) -> dict[str, Any]:  # fmt: skip
    """Files into the bin (N22). Watching stays, as in Radarr, Sonarr and Lidarr: ``still_monitored`` says so. An
    album's files go all, or those of the ``tracks`` named."""
    check_scope(kind, scope, tracks)
    with SessionLocal() as db:
        title = title_of(db, kind, ref)
        definition_ids = definition_ids_of(db, kind, public_ids)
        track_ids = track_ids_of(db, title, tracks) if tracks else ()
        title_id = title.id
    series_scope = scope or SeriesScope()
    bin_scope = recycle_bin.Scope(
        definition_ids=definition_ids, seasons=series_scope.seasons, episodes=series_scope.episodes,
        track_ids=track_ids,
    )  # fmt: skip
    result = recycle_bin.delete(title_id, bin_scope, caller.actor)
    with SessionLocal() as db:
        names = v1_versions.public_ids(db)
        watching_now = {
            version.version_definition_id: (
                version.monitored if kind in SINGLE else _series_watches(db, version)
            )
            for version in db.scalars(select(Version).where(Version.title_id == title_id))
        }
    return {
        "versions": [
            {
                "version_id": names.get(done.definition_id),
                "files_recycled": done.files,
                "still_monitored": bool(watching_now.get(done.definition_id)),
            }
            for done in result.versions
        ]
    }


def wish_search(kind: str, ref: str) -> str:
    """A search wish (N23): ``queued``, ``running`` or ``nothing_wanted``. Since 22.09.2026 it searches at once, with
    the automatic off too, at most five wishes at a time (``automatic.wishes``)."""
    from ..automatic import wishes as automatic_wishes

    with SessionLocal() as db:
        title = title_of(db, kind, ref)
        if search_jobs.running_search(title.id) is not None:
            return "running"
        if anime.not_searched(title.kind, title.series_type):
            return "not_possible"
        if not _wanting(db, title.id):
            return "nothing_wanted"
        title.search_wish_at = utcnow()
        db.commit()
    automatic_wishes.kick()
    return "queued"
