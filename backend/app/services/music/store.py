"""Writing MusicBrainz data into the library (M1.2): one place for adding, loading,
refreshing and, later, the scan of a music folder.

* An artist is one row in ``artists``; an album one ``Title`` of kind ``album`` per release group; an album that the
  owner watches or that has files has one ``Version`` in the one music definition (decisions 1, 2, 10).
* Nothing is deleted because MusicBrainz stopped listing it: it gets ``mb_gone_at`` (decision 22). A merged id
  answers with the surviving id; the old one goes to ``mbid_old`` and still finds the row.
* New means "seen for the first time at a later load" (decision 24): the first load takes the choice of the
  add dialog, later loads the artist's ``monitor_new``.
* The target release of an album version follows ``target.choose`` (decision 27) until a file lies there or the
  owner chose one (decisions 29, 30).
"""

from __future__ import annotations

import logging
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session as OrmSession

from ...models import (
    ACCOUNT_ID,
    Account,
    AlbumArtist,
    Artist,
    HistoryEntry,
    Release,
    ReleaseMedium,
    ReleaseTrack,
    Title,
    TrackFile,
    Version,
    VersionDefinition,
    utcnow,
)
from ...models.music import VARIOUS_ARTISTS_MBID
from .. import schreibweisen
from . import kinds, same_song, target
from .musicbrainz import ArtistData, ReleaseData, ReleaseGroupData

logger = logging.getLogger("nexcrate.music")

#: The label of the one music definition when it is created (decision 10), by interface language.
DEFINITION_LABELS = {"de": "Musik", "en": "Music"}
#: The choices of the add dialog (decision 32), and since the design notes every choice Lidarr offers when adding
#: an artist: albums without files, albums with files, the first and the latest album.
WATCH_CHOICES = ("studio", "future", "none", "missing", "existing", "first", "latest")
#: The choices that are applied while the groups are made; the others after, over the whole catalogue.
WATCH_WHILE_MAKING = ("studio", "future", "none")
#: Decision 21: how often release groups and releases are asked for again, with up to 10 % jitter.
GROUPS_ACTIVE = timedelta(days=2)
GROUPS_QUIET = timedelta(days=30)
RELEASES_FRESH = timedelta(hours=12)
RELEASES_QUIET = timedelta(days=30)
RELEASES_WITH_FILES = timedelta(days=60)
FRESH_ALBUM = timedelta(days=30)
JITTER = 0.1


def now() -> datetime:
    return utcnow()


def jittered(base: timedelta) -> timedelta:
    return base * (1 + random.uniform(0, JITTER))


def account_language(db: OrmSession) -> str:
    account = db.get(Account, ACCOUNT_ID)
    return (account.language if account is not None and account.language else "en")[:2].lower()


# --- The definition and the versions ------------------------------------------------------------------- #


def ensure_definition(db: OrmSession, language: str) -> VersionDefinition:
    """The one version definition of kind ``album``, created at the first music step (decision 10)."""
    row = db.scalar(select(VersionDefinition).where(VersionDefinition.kind == "album").order_by(VersionDefinition.id))
    if row is not None:
        return row
    row = VersionDefinition(
        kind="album",
        label=DEFINITION_LABELS.get(language, DEFINITION_LABELS["en"]),
        profile_name="",
        root_folder="",
        auto_add=False,
        created_at=now(),
    )
    db.add(row)
    db.flush()
    logger.info("Music definition %d created: %s", row.id, row.label)
    return row


def definition(db: OrmSession) -> VersionDefinition | None:
    return db.scalar(select(VersionDefinition).where(VersionDefinition.kind == "album").order_by(VersionDefinition.id))


def incomplete(version: Version) -> bool:
    """An album with files that lacks tracks of its target release (the owner's answer of 19.09.2026)."""
    counts = version.track_counts or {}
    wanted = counts.get("wanted") or 0
    return bool(version.has_file) and wanted > 0 and (counts.get("present") or 0) < wanted


def state_of(version: Version) -> str:
    if version.has_file:
        if incomplete(version):
            return "incomplete"
        # Below the target of the music profile (``album_quality``): shown only, nothing is searched for yet.
        return "upgrade" if version.cutoff_not_met else "available"
    return "wanted" if version.monitored else "unmonitored"


def album_version(db: OrmSession, title_id: int) -> Version | None:
    return db.scalar(select(Version).where(Version.title_id == title_id).order_by(Version.id))


def add_version(
    db: OrmSession,
    title: Title,
    definition: VersionDefinition,
    moment: datetime,
    *,
    monitored: bool = True,
    added_by: str = "owner",
    source_id: int | None = None,
    event: str = "added",
) -> Version:
    """The one version of an album, wanted while it has no file. History gets ``added`` (``imported`` for a source)."""
    version = Version(
        title_id=title.id,
        version_definition_id=definition.id,
        source_id=source_id,
        added_by=added_by,
        monitored=monitored,
        has_file=False,
        state="wanted" if monitored else "unmonitored",
        created_at=moment,
        updated_at=moment,
    )
    db.add(version)
    db.flush()
    _history(db, title.id, version.id, definition, event, moment)
    return version


def _history(
    db: OrmSession,
    title_id: int,
    version_id: int | None,
    definition: VersionDefinition | None,
    event: str,
    moment: datetime,
    detail: str | None = None,
) -> None:
    db.add(
        HistoryEntry(
            title_id=title_id,
            version_id=version_id,
            version_definition_id=definition.id if definition is not None else None,
            version_label=definition.label if definition is not None else "",
            event=event,
            at=moment,
            detail=detail[:1024] if detail else None,
        )
    )


# --- Artists ------------------------------------------------------------------- #


def find_artist(db: OrmSession, mbid: str) -> Artist | None:
    """By the current id, or by an old one MusicBrainz merged away (decision 22)."""
    row = db.scalar(select(Artist).where(Artist.mbid == mbid))
    if row is not None:
        return row
    for candidate in db.scalars(select(Artist).where(Artist.mbid_old.is_not(None))):
        if mbid in (candidate.mbid_old or []):
            return candidate
    return None


def alias_display(data: ArtistData, language: str) -> str | None:
    """The primary alias in the language of the interface, when it differs from the name (decision 36)."""
    for alias in data.aliases:
        locale = str(alias.get("locale") or "").lower()
        if alias.get("primary") and locale[:2] == language and alias.get("name") and alias["name"] != data.name:
            return str(alias["name"])
    return None


def artist_keys(data: ArtistData) -> str:
    names = [data.name, *(str(alias.get("name") or "") for alias in data.aliases)]
    return schreibweisen.search_text(names)


def upsert_artist(
    db: OrmSession,
    data: ArtistData,
    *,
    moment: datetime,
    language: str,
    requested_mbid: str | None = None,
    added_by: str = "owner",
    monitor_new: str = "all",
    priority: int = 0,
) -> tuple[Artist, bool]:
    """The artist row for MusicBrainz's data, made or updated. Returns it and whether it is new."""
    artist = find_artist(db, data.mbid)
    if artist is None and requested_mbid and requested_mbid != data.mbid:
        artist = find_artist(db, requested_mbid)
    new = artist is None
    if artist is None:
        artist = Artist(
            mbid=data.mbid,
            name=data.name,
            added_by=added_by,
            monitor_new=monitor_new if monitor_new in ("all", "none") else "all",
            load_state="queued",
            load_priority=priority,
            added=moment,
        )
        db.add(artist)
    old_ids = list(artist.mbid_old or [])
    for old in (artist.mbid, requested_mbid):
        if old and old != data.mbid and old not in old_ids:
            old_ids.append(old)
    artist.mbid = data.mbid
    artist.mbid_old = old_ids or None
    if data.mbid == VARIOUS_ARTISTS_MBID:
        # ⚠️ However it arrives (a Lidarr import lists it like any artist): the collective artist has 290,000 release
        # groups at MusicBrainz and is never browsed (decision 35). Found on the bench, 81 pages in.
        artist.is_various = True
        artist.monitor_new = "none"
        if artist.load_state in ("queued", "groups"):
            artist.load_state = "ready"
    artist.name = data.name
    artist.sort_name = data.sort_name or data.name
    artist.disambiguation = data.disambiguation
    artist.artist_type = data.artist_type
    artist.country = data.country
    artist.begin_year = data.begin_year
    artist.end_year = data.end_year
    artist.ended = data.ended
    artist.aliases = data.aliases or None
    artist.alias_display = alias_display(data, language)
    artist.search_keys = artist_keys(data)
    artist.sort_key = schreibweisen.sort_key(artist.sort_name)
    artist.mb_gone_at = None
    artist.updated_at = moment
    db.flush()
    return artist, new


def various_artist(db: OrmSession, moment: datetime) -> Artist:
    """The collective artist for compilations (decision 35), made at the first sampler, never browsed."""
    row = db.scalar(select(Artist).where(Artist.mbid == VARIOUS_ARTISTS_MBID))
    if row is not None:
        return row
    row = Artist(
        mbid=VARIOUS_ARTISTS_MBID,
        name="Various Artists",
        sort_name="Various Artists",
        sort_key=schreibweisen.sort_key("Various Artists"),
        search_keys=schreibweisen.search_text(["Various Artists"]),
        is_various=True,
        monitor_new="none",
        load_state="ready",
        added_by="owner",
        added=moment,
        updated_at=moment,
    )
    db.add(row)
    db.flush()
    return row


def groups_due(artist: Artist, newest_album: str | None, moment: datetime) -> datetime:
    """Decision 21: every 2 days while the artist is active or its newest album is under 30 days old, else 30."""
    fresh = _date_of(newest_album)
    active = not artist.ended or (fresh is not None and moment - fresh < FRESH_ALBUM)
    return moment + jittered(GROUPS_ACTIVE if active else GROUPS_QUIET)


def releases_due(first_release_date: str | None, has_file: bool, moment: datetime) -> datetime:
    """Decision 21: an album under 30 days old every 12 hours, one with files every 60 days, else every 30."""
    released = _date_of(first_release_date)
    if released is not None and moment - released < FRESH_ALBUM:
        return moment + jittered(RELEASES_FRESH)
    if has_file:
        return moment + jittered(RELEASES_WITH_FILES)
    return moment + jittered(RELEASES_QUIET)


def _date_of(text: str | None) -> datetime | None:
    if not text:
        return None
    parts = text.split("-")
    try:
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 else 1
        day = int(parts[2]) if len(parts) > 2 else 1
        return datetime(year, month, day, tzinfo=UTC)
    except TypeError, ValueError:
        return None


# --- Albums ------------------------------------------------------------------- #


def find_album(db: OrmSession, mbid: str) -> Title | None:
    row = db.scalar(select(Title).where(Title.kind == "album", Title.mbid == mbid))
    if row is not None:
        return row
    return db.scalar(select(Title).where(Title.kind == "album", Title.mbid_old.like(f'%"{mbid}"%')))


def album_keys(group: ReleaseGroupData, artist_names: list[str]) -> str:
    return schreibweisen.search_text([group.title, *artist_names])


def _credit_names(group: ReleaseGroupData) -> list[str]:
    return [str(part.get("artist_name") or part.get("name") or "") for part in group.credit]


def apply_release_group(
    db: OrmSession,
    artist: Artist,
    group: ReleaseGroupData,
    *,
    moment: datetime,
    first_load: bool,
    watch: str,
    definition: VersionDefinition | None,
    artists_by_mbid: dict[str, KnownArtist] | None = None,
) -> tuple[Title, bool]:
    """One release group of an artist as a title: made or updated, watched by the rules, with its history.

    ``first_load`` with ``watch`` is the choice of the add dialog (decision 32); later loads follow the artist's
    ``monitor_new`` for a group seen for the first time (decision 24). ``artists_by_mbid`` holds the artists of the
    library for the credit, for a joint album (decision 3).
    """
    title = find_album(db, group.mbid)
    new = title is None
    studio = kinds.of_types(group.primary_type, group.secondary_types, artist.album_types)
    if title is None:
        # Decision 3: the first artist of the credit that is in the library owns the album, whoever loads it first.
        known = artists_by_mbid or {}
        credited = [str(part.get("mbid") or "") for part in group.credit]
        owner = next((known[mbid] for mbid in credited if mbid in known), artist)
        title = Title(
            kind="album",
            tmdb_id=None,
            mbid=group.mbid,
            title=group.title,
            genres=[],
            artist_id=owner.id,
            first_seen_at=moment,
            releases_state="none",
            added=moment,
        )
        db.add(title)
    else:
        old_types = (title.primary_type, list(title.secondary_types or []))
        new_types = (group.primary_type, list(group.secondary_types))
        if old_types != new_types and title.first_seen_at is not None:
            detail = f"{_type_text(*old_types)} > {_type_text(*new_types)}"
            _history(db, title.id, None, definition, "album_type_changed", moment, detail)
        if title.mbid != group.mbid:
            title.mbid_old = [*(title.mbid_old or []), *([title.mbid] if title.mbid else [])]
            title.mbid = group.mbid
        if title.artist_id is None:
            title.artist_id = artist.id
    title.title = group.title
    title.original_title = None
    title.primary_type = group.primary_type
    title.secondary_types = list(group.secondary_types) or None
    title.first_release_date = group.first_release_date
    title.year = _year_of(group.first_release_date)
    title.release_group_disambiguation = group.disambiguation
    title.artist_credit = group.credit or None
    title.sort_key = schreibweisen.sort_key(group.title)
    names = _credit_names(group) or [artist.name]
    title.search_keys = album_keys(group, names)
    title.mb_gone_at = None
    title.updated_at = moment
    db.flush()
    _credit_rows(db, title, artist, group, artists_by_mbid or {})
    if new and not first_load:
        _history(
            db,
            title.id,
            None,
            definition,
            "album_appeared",
            moment,
            _type_text(group.primary_type, group.secondary_types),
        )
    wants = (first_load and watch == "studio" and studio) or (
        not first_load and new and artist.monitor_new == "all" and studio
    )
    if wants and definition is not None and album_version(db, title.id) is None:
        add_version(db, title, definition, moment)
    return title, new


def _credit_rows(
    db: OrmSession, title: Title, artist: Artist, group: ReleaseGroupData, known: dict[str, KnownArtist]
) -> None:
    """Every further artist of the credit that is in the library gets an ``album_artists`` row (decision 3)."""
    wanted: dict[int, int] = {}
    for position, part in enumerate(group.credit):
        other = known.get(str(part.get("mbid") or ""))
        if other is not None and other.id != title.artist_id:
            wanted.setdefault(other.id, position)
    existing = {row.artist_id: row for row in db.scalars(select(AlbumArtist).where(AlbumArtist.title_id == title.id))}
    for artist_id, position in wanted.items():
        if artist_id in existing:
            existing[artist_id].position = position
        else:
            db.add(AlbumArtist(title_id=title.id, artist_id=artist_id, position=position))
    for artist_id, row in existing.items():
        if artist_id not in wanted and artist_id != artist.id:
            db.delete(row)


def _type_text(primary: str | None, secondary: list[str] | None) -> str:
    return "+".join([primary or "?", *(secondary or [])])


def _year_of(date: str | None) -> int | None:
    text = (date or "").strip()
    return int(text[:4]) if len(text) >= 4 and text[:4].isdigit() else None


@dataclass(frozen=True)
class KnownArtist:
    """An artist of the library as the credits need it: its row id."""

    id: int


def library_artists(db: OrmSession) -> dict[str, KnownArtist]:
    """Every artist of the library by MusicBrainz id, for the credits of joint albums. Two columns, no objects: with
    thousands of artists this is asked for often (measured on the bench in real size)."""
    return {mbid: KnownArtist(row_id) for mbid, row_id in db.execute(select(Artist.mbid, Artist.id)).tuples()}


def unlisted_groups(db: OrmSession, artist: Artist, seen: set[str]) -> list[Title]:
    """After a complete browse: the albums of the artist MusicBrainz did not list and that are not marked yet."""
    rows = db.scalars(select(Title).where(Title.kind == "album", Title.artist_id == artist.id))
    return [title for title in rows if title.mbid not in seen and title.mb_gone_at is None]


def mark_group_gone(db: OrmSession, title: Title, moment: datetime, definition: VersionDefinition | None) -> None:
    """Decision 22: the album stays, with the mark and a line in its history."""
    title.mb_gone_at = moment
    _history(db, title.id, None, definition, "album_gone", moment)


def mark_groups_gone(
    db: OrmSession, artist: Artist, seen: set[str], moment: datetime, definition: VersionDefinition | None
) -> int:
    """Every unlisted album of the artist gets ``mb_gone_at``, once; for tests and the case without a lookup."""
    titles = unlisted_groups(db, artist, seen)
    for title in titles:
        mark_group_gone(db, title, moment, definition)
    return len(titles)


def adopt_merged_group(db: OrmSession, title: Title, new_mbid: str, moment: datetime) -> Title:
    """MusicBrainz merged the album's release group into another (decision 22): the title takes the new id and
    keeps the old one in ``mbid_old``. A title the same browse made for the new id is a duplicate of minutes: its
    version moves over when the old title has none, then it goes with its history."""
    duplicate = db.scalar(select(Title).where(Title.kind == "album", Title.mbid == new_mbid, Title.id != title.id))
    if duplicate is not None:
        for column in (
            "title", "primary_type", "secondary_types", "first_release_date", "year",
            "release_group_disambiguation", "artist_credit", "sort_key", "search_keys",
        ):  # fmt: skip
            setattr(title, column, getattr(duplicate, column))
        own = album_version(db, title.id)
        for version in db.scalars(select(Version).where(Version.title_id == duplicate.id)):
            if own is None:
                version.title_id = title.id
                own = version
        db.execute(
            update(HistoryEntry).where(HistoryEntry.title_id == duplicate.id).values(title_id=title.id),
            execution_options={"synchronize_session": False},
        )
        db.flush()
        db.delete(duplicate)
        # The unique index on (kind, mbid): the duplicate must be gone before the title takes its id.
        db.flush()
    title.mbid_old = [*(title.mbid_old or []), *([title.mbid] if title.mbid else [])]
    title.mbid = new_mbid
    title.mb_gone_at = None
    title.updated_at = moment
    db.flush()
    logger.info("Album %d follows MusicBrainz's merge to %s", title.id, new_mbid)
    return title


# --- Releases and tracks ------------------------------------------------------------------- #


def apply_releases(
    db: OrmSession,
    title: Title,
    releases: list[ReleaseData],
    *,
    moment: datetime,
    complete: bool,
    keep: set[str] | None = None,
) -> dict[str, int]:
    """The official releases of an album with their media and tracks, made or updated; a release MusicBrainz no
    longer lists gets ``mb_gone_at`` when the browse was ``complete``. Tracks keep their rows: a track file points
    at one, and a rebuild would cut that link.

    ``keep`` names releases stored whatever their status: the release Lidarr watches for an album with files may be
    a bootleg or withdrawn when the album has no official one (54 of 496 on the bench), and its files belong to it.
    The target rule still never chooses it (decision 5, E1)."""
    existing = {row.mbid: row for row in db.scalars(select(Release).where(Release.title_id == title.id))}
    by_old: dict[str, Release] = {}
    for row in existing.values():
        for old in row.mbid_old or []:
            by_old[old] = row
    seen: set[str] = set()
    counts = {"releases": 0, "new": 0, "tracks": 0}
    for data in releases:
        if not data.mbid or ((data.status or "official") != "official" and data.mbid not in (keep or set())):
            continue
        row = existing.get(data.mbid) or by_old.get(data.mbid)
        # ⚠️ A source fills in what is not loaded yet and never writes over it: Lidarr's heads carry no track count,
        # no barcode and no credits, and every import set the track count of a loaded release to 0, which left the
        # rule with Lidarr's own release as the only candidate (found on the owner's import).
        filling = keep is not None and row is not None and row.tracks_loaded
        if row is None:
            row = Release(title_id=title.id, mbid=data.mbid, name=data.title)
            db.add(row)
            counts["new"] += 1
        elif row.mbid != data.mbid:
            row.mbid_old = [*(row.mbid_old or []), row.mbid]
            row.mbid = data.mbid
        seen.add(row.mbid)
        if not filling:
            row.name = data.title
            row.status = data.status
            row.date = data.date
            row.country = data.country
            row.disambiguation = data.disambiguation
            row.barcode = data.barcode
            row.labels = data.labels or None
            row.packaging = data.packaging
            row.media_count = len(data.media)
            row.track_count = data.track_count
            row.formats = data.formats
            row.audio_only = target.is_audio_only(data.formats)
        if complete:
            # Only MusicBrainz itself takes the mark back: Lidarr goes on listing a release MusicBrainz dropped, and
            # every import would otherwise send the album back to loading.
            row.mb_gone_at = None
        db.flush()
        if any(medium.tracks for medium in data.media):
            # While filling: a track Lidarr knows and nexcrate does not is added for its file; nothing else moves.
            counts["tracks"] += _apply_tracks(db, row, data, only_missing=filling)
            row.tracks_loaded = True
        counts["releases"] += 1
    if complete:
        for mbid, row in existing.items():
            if mbid not in seen and row.mb_gone_at is None:
                row.mb_gone_at = moment
    # ⚠️ From the database, not from this call's bookkeeping: the first Lidarr import stored 39 releases with the
    # tracks of one and called the album complete, so the job never loaded the rest (found on the owner's import).
    db.flush()
    stored = list(
        db.execute(
            select(Release.tracks_loaded, Release.status).where(
                Release.title_id == title.id, Release.mb_gone_at.is_(None)
            )
        ).tuples()
    )
    official = [loaded for loaded, status in stored if (status or "official") == "official"]
    if complete:
        # Everything MusicBrainz lists as official came with its tracks, even when that is nothing (an album with
        # bootlegs only): nothing more to load until the album is due again.
        title.releases_state = "tracks"
    elif title.releases_state == "failed" and not (official and all(official)):
        # The loading job remembers here that MusicBrainz stayed busy for this album, with the next try in
        # ``releases_due_at``. A source's heads must not wipe that: every hourly import would send the album back.
        pass
    elif not stored:
        title.releases_state = "none"
    else:
        title.releases_state = "tracks" if official and all(official) else "heads"
    title.releases_refreshed_at = moment
    db.flush()
    return counts


def _apply_tracks(db: OrmSession, release: Release, data: ReleaseData, *, only_missing: bool = False) -> int:
    """The media and tracks of a release, made, updated and removed; with ``only_missing`` only made."""
    media = {
        row.position: row for row in db.scalars(select(ReleaseMedium).where(ReleaseMedium.release_id == release.id))
    }
    tracks = {row.mbid: row for row in db.scalars(select(ReleaseTrack).where(ReleaseTrack.release_id == release.id))}
    seen_media: set[int] = set()
    seen_tracks: set[str] = set()
    count = 0
    for medium_data in data.media:
        medium = media.get(medium_data.position)
        new_medium = medium is None
        if medium is None:
            medium = ReleaseMedium(release_id=release.id, position=medium_data.position)
            db.add(medium)
            media[medium_data.position] = medium
        if new_medium or not only_missing:
            medium.format = medium_data.format
            medium.name = medium_data.name
            medium.track_count = medium_data.track_count
        db.flush()
        seen_media.add(medium.position)
        for track_data in medium_data.tracks:
            if not track_data.mbid:
                continue
            track = tracks.get(track_data.mbid)
            if track is None:
                track = ReleaseTrack(
                    release_id=release.id,
                    medium_id=medium.id,
                    mbid=track_data.mbid,
                    position=track_data.position,
                    name=track_data.name,
                )
                db.add(track)
                tracks[track_data.mbid] = track
            elif only_missing:
                seen_tracks.add(track_data.mbid)
                continue
            track.medium_id = medium.id
            track.recording_mbid = track_data.recording_mbid
            track.position = track_data.position
            track.number = track_data.number
            track.name = track_data.name
            track.length_ms = track_data.length_ms
            track.artist_credit = track_data.credit if track_data.credit != data.credit else None
            seen_tracks.add(track_data.mbid)
            count += 1
    if not only_missing:
        for mbid, track in tracks.items():
            if mbid not in seen_tracks:
                db.delete(track)
        for position, medium in media.items():
            if position not in seen_media:
                db.delete(medium)
    db.flush()
    return count


# --- The target release ------------------------------------------------------------------- #


def candidates_of(db: OrmSession, title_id: int) -> list[target.Candidate]:
    rows = list(db.scalars(select(Release).where(Release.title_id == title_id, Release.mb_gone_at.is_(None))))
    # Only names in brackets can be gaps (``target.is_filler``): a handful of rows, whatever the album.
    bracketed = db.execute(
        select(ReleaseTrack.release_id, ReleaseTrack.name, ReleaseTrack.length_ms).where(
            ReleaseTrack.release_id.in_([row.id for row in rows]), ReleaseTrack.name.like("[%")
        )
    ).tuples()
    filler: dict[int, int] = defaultdict(int)
    for release_id, name, length_ms in bracketed.all():
        if target.is_filler(name, length_ms):
            filler[release_id] += 1
    return [
        target.Candidate(
            id=row.id,
            status=row.status,
            date=row.date,
            country=row.country,
            formats=list(row.formats or []),
            track_count=row.track_count,
            tracks_loaded=row.tracks_loaded,
            mbid=row.mbid,
            filler_count=filler.get(row.id, 0),
        )
        for row in rows
    ]


def countries_of(db: OrmSession, definition: VersionDefinition | None, language: str) -> tuple[str, ...]:
    own = list(definition.music_countries) if definition is not None and definition.music_countries else None
    return target.countries_for(language, own)


def refresh_target(db: OrmSession, title: Title, version: Version, *, moment: datetime, language: str) -> target.Choice:
    """Run the rule for one album version (decision 29): sets the target while nothing freezes it, else a
    suggestion; the owner's choice stays (decision 30). Writes ``target_changed`` when the target moved."""
    definition_row = db.get(VersionDefinition, version.version_definition_id)
    choice = target.choose(candidates_of(db, title.id), countries_of(db, definition_row, language))
    chosen_id = choice.chosen.id if choice.chosen is not None else None
    present = db.scalar(select(func.count(TrackFile.id)).where(TrackFile.version_id == version.id)) or 0
    frozen = version.target_set_by == "owner" or (present > 0 and version.target_release_id is not None)
    if frozen:
        version.target_suggestion_id = chosen_id if chosen_id and chosen_id != version.target_release_id else None
        _count_tracks(db, version)
        version.updated_at = moment
        db.flush()
        return choice
    old_id = version.target_release_id
    version.target_release_id = chosen_id
    version.target_set_by = "rule" if chosen_id else None
    version.target_reason = {"codes": choice.reasons} if choice.reasons else None
    version.target_suggestion_id = None
    if old_id is not None and chosen_id is not None and old_id != chosen_id:
        old = db.get(Release, old_id)
        new = db.get(Release, chosen_id)
        _history(
            db,
            title.id,
            version.id,
            definition_row,
            "target_changed",
            moment,
            f"{_release_text(old)} > {_release_text(new)}",
        )
    _count_tracks(db, version)
    version.updated_at = moment
    db.flush()
    return choice


def set_owner_target(
    db: OrmSession, title: Title, version: Version, release: Release | None, *, moment: datetime, language: str
) -> None:
    """Decision 30: the owner chooses any official release, or hands the choice back to the rule."""
    definition_row = db.get(VersionDefinition, version.version_definition_id)
    old_id = version.target_release_id
    if release is None:
        if version.actual_release_id is not None:
            # Files lie there: the target goes back to the release they belong to (decision 29), and the rule's
            # choice shows as a suggestion again.
            version.target_release_id = version.actual_release_id
            version.target_set_by = "source" if version.source_id is not None else "rule"
            version.target_reason = {"codes": [{"code": "source" if version.source_id is not None else "files"}]}
        else:
            version.target_set_by = None
            version.target_release_id = None
        refresh_target(db, title, version, moment=moment, language=language)
        return
    version.target_release_id = release.id
    version.target_set_by = "owner"
    version.target_reason = {"codes": [{"code": "owner"}]}
    version.target_suggestion_id = None
    if old_id != release.id:
        old = db.get(Release, old_id) if old_id is not None else None
        _history(
            db,
            title.id,
            version.id,
            definition_row,
            "target_changed",
            moment,
            f"{_release_text(old)} > {_release_text(release)}",
        )
    _count_tracks(db, version)
    version.updated_at = moment
    db.flush()


def _release_text(release: Release | None) -> str:
    if release is None:
        return "?"
    formats = "+".join(release.formats or []) or "?"
    return f"{release.name} ({formats}, {release.track_count} tracks, {release.country or '?'}, {release.date or '?'})"


def _count_tracks(db: OrmSession, version: Version) -> None:
    """``wanted`` is the target's track count; ``present`` how many of the target's tracks the files cover as songs
    (``same_song``: the recording, or the name when it is unique in its release), so a file of another release counts
    for the target's track with the same song and a second file of one song never counts twice (finding of
    19.09.2026: 50 of 42). A file of a source without nexcrate's tracks counts once per track it names."""
    wanted = 0
    if version.target_release_id is not None:
        release = db.get(Release, version.target_release_id)
        wanted = release.track_count if release is not None else 0
    files = list(db.scalars(select(TrackFile).where(TrackFile.version_id == version.id)))
    ids: set[int] = set()
    extra = 0
    for file in files:
        covered = file.track_ids or ([file.track_id] if file.track_id is not None else [])
        if covered:
            ids.update(covered)
        elif file.source_track_ids:
            extra += len(file.source_track_ids)
        elif file.source_file_id is not None:
            extra += 1
        # A file nexcrate filed without a track (a hidden track, the design notes, decision 32) holds none.
    if version.target_release_id is not None and ids:
        release_ids = {version.target_release_id}
        release_ids.update(db.scalars(select(ReleaseTrack.release_id).where(ReleaseTrack.id.in_(ids))))
        tracks = list(db.scalars(select(ReleaseTrack).where(ReleaseTrack.release_id.in_(release_ids))))
        by_release: dict[int, list[ReleaseTrack]] = defaultdict(list)
        for track in tracks:
            by_release[track.release_id].append(track)
        keys = same_song.keys_by_track(by_release.values())
        have = same_song.union(keys, ids)
        target = by_release.get(version.target_release_id, [])
        present = sum(1 for track in target if track.id in ids or keys.get(track.id, frozenset()) & have)
        present = min(wanted, present + extra) if wanted else present + extra
    else:
        present = len(ids) + extra

    version.track_counts = {"wanted": wanted, "present": present}
    version.has_file = bool(files)
    version.state = state_of(version)


#: The public name of the count, for the recycle bin.
count_tracks = _count_tracks


def refresh_targets_of_title(db: OrmSession, title: Title, *, moment: datetime, language: str) -> None:
    for version in db.scalars(select(Version).where(Version.title_id == title.id)):
        refresh_target(db, title, version, moment=moment, language=language)


#: The state an artist row shows, most pressing first (the owner's answer of 20.09.2026).
ROW_STATES = ("problem", "downloading", "wanted", "incomplete", "upgrade")


def counts_of(db: OrmSession, artist_id: int) -> dict[str, Any]:
    """For the artist row and tile: albums with a version, how many are complete, what is missing or could be
    better, the size on disk, and the one state the row shows."""
    return counts_of_many(db, [artist_id])[artist_id]


def counts_of_many(db: OrmSession, artist_ids: list[int]) -> dict[int, dict[str, Any]]:
    """``counts_of`` for a page of artists in one query; every artist asked for has its counts, zero ones too."""
    versions: dict[int, list[tuple[Any, ...]]] = {artist_id: [] for artist_id in artist_ids}
    if artist_ids:
        for artist_id, *row in db.execute(
            select(Title.artist_id, Version.has_file, Version.track_counts, Version.state, Version.size)
            .join(Title, Title.id == Version.title_id)
            .where(Title.artist_id.in_(artist_ids), Title.kind == "album")
        ).tuples():
            versions[artist_id].append(tuple(row))
    return {artist_id: _counts(rows) for artist_id, rows in versions.items()}


def _counts(versions: list[tuple[Any, ...]]) -> dict[str, Any]:
    complete = sum(
        1
        for has_file, counts, _state, _size in versions
        if has_file and counts and counts.get("wanted") and counts.get("present", 0) >= counts.get("wanted", 0)
    )
    states = Counter(state for _has_file, _counts, state, _size in versions)
    return {
        "albums": len(versions),
        "complete": complete,
        # "wanted" counts an album without a single file, "incomplete" one that misses tracks.
        "missing": states.get("wanted", 0),
        "incomplete": states.get("incomplete", 0),
        "upgrade": states.get("upgrade", 0),
        "downloading": states.get("downloading", 0),
        "problem": states.get("problem", 0),
        "size_bytes": sum(size or 0 for _has_file, _counts, _state, size in versions),
        "state": next((state for state in ROW_STATES if states.get(state)), None),
    }


def new_albums_for(choice: str) -> str:
    """``monitor_new`` after a watch choice: none for none and for albums with files, else all."""
    return "none" if choice in ("none", "existing") else "all"


def apply_choice(
    db: OrmSession,
    artist: Artist,
    choice: str,
    *,
    moment: datetime,
    definition: VersionDefinition,
    today: str | None = None,
) -> int:
    """Watch the albums of an artist a choice names, among the groups of ``artist.album_types``. Returns how many albums
    it watched that were not watched before; it never unwatches one.

    ``studio`` is every album of the groups, ``missing`` those without a file, ``existing`` those with one, ``first``
    and ``latest`` the earliest and the newest album out by ``today``. ``future`` and ``none`` watch nothing now.
    """
    if choice not in WATCH_CHOICES or choice in ("future", "none"):
        return 0
    albums = [
        title
        for title in db.scalars(
            select(Title).where(Title.kind == "album", Title.artist_id == artist.id, Title.mb_gone_at.is_(None))
        )
        if kinds.of_types(title.primary_type, title.secondary_types, artist.album_types)
    ]
    versions = {title.id: album_version(db, title.id) for title in albums}
    if choice == "missing":
        albums = [title for title in albums if versions[title.id] is None or not versions[title.id].has_file]
    elif choice == "existing":
        albums = [title for title in albums if versions[title.id] is not None and versions[title.id].has_file]
    elif choice in ("first", "latest"):
        day = today or moment.date().isoformat()
        out = sorted(
            (title for title in albums if title.first_release_date and title.first_release_date <= day),
            key=lambda title: (title.first_release_date or "", title.id),
        )
        albums = out[:1] if choice == "first" else out[-1:]
    watched = 0
    for title in albums:
        version = versions[title.id]
        if version is None:
            add_version(db, title, definition, moment)
            watched += 1
        elif not version.monitored:
            version.monitored = True
            version.state = state_of(version)
            version.updated_at = moment
            watched += 1
    return watched
