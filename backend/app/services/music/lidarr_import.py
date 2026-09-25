"""Reading a Lidarr connection (M1.6, decisions 44 to 53): artists, albums, releases, the
tracks of the release Lidarr watches, the files, the files it could not map, and its queue. Nothing is written to
Lidarr, and not one request goes to MusicBrainz during the run (decision 46): the catalogue behind Lidarr's albums
and the tracks of the other releases follow in the background at the priority of an import (decision 20).

Measured on the bench (plan, B3): ``track?artistId=`` carries only the tracks of the watched release; a track has
``foreignTrackId`` and ``foreignRecordingId`` and no release id; one file can hold several tracks (two tracks with
one ``trackFileId``); an unmapped file comes with artist and album 0.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session as OrmSession

from ... import crypto
from ...db import SessionLocal, get_setting, set_setting
from ...models import (
    Artist,
    ImportRun,
    Release,
    ReleaseTrack,
    Setting,
    Source,
    SourceUnmappedFile,
    Title,
    TrackFile,
    Version,
    utcnow,
)
from .. import importer, tags
from ..lidarr import (
    AlbumRecord,
    ArtistRecord,
    LidarrClient,
    LidarrError,
    QueueRecord,
    TrackFileRecord,
    TrackRecord,
)
from ..radarr import RadarrError, SourceUrlInvalid
from . import album_quality, loading, store
from .musicbrainz import ArtistData, MediumData, ReleaseData, ReleaseGroupData, TrackData

logger = logging.getLogger("nexcrate.music")

PROGRESS_EVERY = 5
#: The longest the import holds the write lock before it lets others in (SQLite waits 5 s for a lock).
WRITE_SLICE_SECONDS = 0.5
#: Patched by tests.
clock = time.monotonic
UNMAPPED_LISTED = 50
#: Per connection: what the last full look at every artist saw (Lidarr's numbers for it, the Lidarr ids of its
#: albums, what it added to the counts of the run), and when every artist was last read in full.
SETTING_SEEN = "lidarr_seen_{source_id}"
SETTING_FULL_READ = "lidarr_full_read_{source_id}"
#: A scheduled run reads every artist again after this long, whatever Lidarr's numbers say.
FULL_READ_EVERY = timedelta(hours=24)
#: Lidarr's ``monitorNewItems``: ``all`` watches new albums, ``none`` and ``new`` do not (decision 47).
MONITOR_NEW = {"all": "all"}
#: A release Lidarr lists in this many countries is a worldwide one (see ``musicbrainz.WORLDWIDE_EVENTS``).
WORLDWIDE_COUNTRIES = 10
#: Lidarr names the country of a release; MusicBrainz and the target rule use the ISO code (decision 28).
COUNTRY_CODES = {
    "united states": "US",
    "united kingdom": "GB",
    "germany": "DE",
    "austria": "AT",
    "switzerland": "CH",
    "europe": "XE",
    "[worldwide]": "XW",
    "worldwide": "XW",
    "japan": "JP",
    "france": "FR",
    "netherlands": "NL",
    "canada": "CA",
    "australia": "AU",
    "italy": "IT",
    "spain": "ES",
    "sweden": "SE",
    "denmark": "DK",
    "norway": "NO",
    "finland": "FI",
    "poland": "PL",
    "brazil": "BR",
    "argentina": "AR",
    "mexico": "MX",
    "russia": "RU",
    "belgium": "BE",
    "ireland": "IE",
    "new zealand": "NZ",
    "south africa": "ZA",
    "south korea": "KR",
    "china": "CN",
    "taiwan": "TW",
    "portugal": "PT",
    "greece": "GR",
    "czechia": "CZ",
    "hungary": "HU",
    "turkey": "TR",
    "israel": "IL",
    "india": "IN",
    "[unknown country]": None,
}


@dataclass
class ArtistRead:
    """What Lidarr says about one artist: read, written and let go before the next one is asked for."""

    albums: list[AlbumRecord]
    tracks: list[TrackRecord]
    files: list[TrackFileRecord]


@dataclass
class Tally:
    artists: int = 0
    artists_new: int = 0
    albums: int = 0
    albums_new: int = 0
    releases: int = 0
    tracks: int = 0
    files: int = 0
    versions_total: int = 0
    versions_removed: int = 0
    unmapped_paths: list[str] = field(default_factory=list)
    unmapped_files: int = 0
    #: Artists a scheduled run did not read again, because Lidarr's numbers for them are what they were.
    unchanged: int = 0

    def counts(self) -> list[int]:
        return [self.artists, self.albums, self.releases, self.tracks, self.files, self.versions_total]

    def add(self, counts: list[int]) -> None:
        """What an artist counted when it was last read in full, so the numbers of a run stay those of the library."""
        if len(counts) != 6:
            return
        self.artists += counts[0]
        self.albums += counts[1]
        self.releases += counts[2]
        self.tracks += counts[3]
        self.files += counts[4]
        self.versions_total += counts[5]

    def details(self) -> dict[str, Any]:
        return {
            "unchanged": self.unchanged,
            "artists": self.artists,
            "albums": self.albums,
            "releases": self.releases,
            "tracks": self.tracks,
            "files": self.files,
            "unmapped_files": self.unmapped_files,
            "unmapped_paths": self.unmapped_paths[:UNMAPPED_LISTED],
        }


def _progress(run_id: int, phase: str, done: int, total: int) -> None:
    with SessionLocal() as db:
        db.execute(
            update(ImportRun)
            .where(ImportRun.id == run_id, ImportRun.status == "running")
            .values(details={"phase": phase, "done": done, "total": total})
        )
        db.commit()


def _mark(record: ArtistRecord) -> list[Any]:
    """What a scheduled run compares: when none of this moved, Lidarr has nothing new for the artist."""
    numbers = record.statistics
    return [
        record.mbid,
        record.name,
        record.monitored,
        record.monitor_new_items,
        record.path,
        record.quality_profile_id,
        record.metadata_profile_id,
        numbers.album_count,
        numbers.track_file_count,
        numbers.total_track_count,
        numbers.size_on_disk,
    ]


def _seen_before(db: OrmSession, source_id: int) -> dict[str, Any]:
    raw = get_setting(db, SETTING_SEEN.format(source_id=source_id), "")
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def full_read_due(db: OrmSession, source_id: int, moment: datetime) -> bool:
    """Whether a scheduled run reads every artist again (``FULL_READ_EVERY``): Lidarr's numbers do not show an album
    switched to unwatched, another chosen release or a renamed file (measured 18.09.2026 on the bench's Lidarr)."""
    raw = get_setting(db, SETTING_FULL_READ.format(source_id=source_id), "")
    try:
        last = datetime.fromisoformat(raw) if raw else None
    except ValueError:
        last = None
    return last is None or moment - last >= FULL_READ_EVERY


async def _import(url: str, api_key: str, source_id: int, run_id: int, light: bool) -> Tally | None:
    """Read and write artist by artist. ⚠️ Until 18.09.2026 everything was read first and written afterwards: with
    4,000 artists the process held 1.2 GB (measured on the bench in real size). Now one artist is in memory at a time.

    ``light``: a scheduled run. An artist whose numbers in Lidarr's list are what the last full look at it saw is not
    asked for again; on a real Lidarr the list took 0.7 s and the three requests per artist 0.7 s each.
    """
    tally = Tally()
    moment = utcnow()
    seen_albums: set[int] = set()
    async with LidarrClient(url, api_key) as lidarr:
        await lidarr.system_status()
        quality_profiles = await lidarr.quality_profiles()
        artists = await lidarr.artists()
        try:
            tag_names: dict[int, str] | None = await lidarr.tags()
        except RadarrError:
            tag_names = None
        with SessionLocal() as db:
            source = db.get(Source, source_id)
            if source is None:
                return None
            language = store.account_language(db)
            definition = store.ensure_definition(db, language)
            if definition.id != source.version_id:
                # The connection feeds the one music definition (decision 11).
                source.version_id = definition.id
            before = _seen_before(db, source_id) if light else {}
            seen: dict[str, Any] = {}
            profiles = {profile.id: profile.name for profile in quality_profiles}
            # ⚠️ Once per run, kept up as artists come: read per artist, 4,000 artists made 16 million rows of it.
            library = store.library_artists(db)
            total = len(artists)
            for index, record in enumerate(artists, start=1):
                known = before.get(str(record.id))
                mark = _mark(record)
                if isinstance(known, dict) and known.get("mark") == mark and (record.mbid or "") in library:
                    # Decision 52 still needs its albums: what Lidarr no longer lists loses its version.
                    seen_albums.update(known.get("albums") or [])
                    tally.add(known.get("counts") or [])
                    tally.unchanged += 1
                    seen[str(record.id)] = known
                else:
                    albums = await lidarr.albums(record.id)
                    read = ArtistRead(albums, await lidarr.tracks(record.id), await lidarr.track_files(record.id))
                    # Known to Lidarr, with or without an id: decision 52 removes only what Lidarr no longer lists.
                    seen_albums.update(album.id for album in read.albums)
                    counted = tally.counts()
                    if record.mbid:
                        _write_artist(db, source, definition, record, read, profiles, tally, library, moment, language)
                    seen[str(record.id)] = {
                        "mark": mark,
                        "albums": [album.id for album in read.albums],
                        "counts": [now - was for now, was in zip(tally.counts(), counted, strict=True)],
                    }
                    db.commit()
                if index % PROGRESS_EVERY == 0 or index == total:
                    _progress(run_id, "artists", index, total)
            unmapped = await lidarr.unmapped_files()
            queue = await lidarr.queue()
            if tag_names is not None:
                # The artist list comes whole in every run, so its tags are mirrored whole too.
                ids = {mbid: known.id for mbid, known in store.library_artists(db).items()}
                tags.sync_artists(
                    db,
                    source_id,
                    {
                        ids[record.mbid]: [tag_names[tag_id] for tag_id in record.tags if tag_id in tag_names]
                        for record in artists
                        if record.mbid and record.mbid in ids
                    },
                )
            _write_unmapped(db, source, unmapped, tally, moment)
            _write_queue(db, source, queue)
            set_setting(db, SETTING_SEEN.format(source_id=source_id), json.dumps(seen, separators=(",", ":")))
            if not light:
                set_setting(db, SETTING_FULL_READ.format(source_id=source_id), moment.isoformat())
            _finish(db, source, run_id, seen_albums, tally, moment)
            db.commit()
    return tally


def run(source_id: int, run_id: int, *, scheduled: bool = False) -> None:
    """Run a begun import of a Lidarr connection to its end. The caller releases the source. ``scheduled``: the
    hourly run reads only what changed, and everything once a day; the owner's own import reads everything."""
    started = time.perf_counter()
    with SessionLocal() as db:
        source = db.get(Source, source_id)
        if source is None:
            logger.info("Import of source %d stopped, the source was deleted", source_id)
            return
        url, stored_key = source.url, source.api_key
    logger.info("Import of Lidarr source %d started", source_id)
    api_key = crypto.decrypt(stored_key)
    if not api_key:
        logger.warning("Import of source %d failed, the stored API key cannot be read", source_id)
        importer._finish_failed(run_id, "source_key_missing")
        return
    with SessionLocal() as db:
        light = scheduled and not full_read_due(db, source_id, utcnow())
    try:
        tally = asyncio.run(_import(url, api_key, source_id, run_id, light))
    except LidarrError as exc:
        logger.warning("Import of source %d failed: %s", source_id, exc.code)
        importer._finish_failed(run_id, exc.code, {k: v for k, v in exc.detail.items() if k not in ("code", "message")})
        return
    except SourceUrlInvalid:
        logger.warning("Import of source %d failed, the stored address is not valid", source_id)
        importer._finish_failed(run_id, "source_url_invalid")
        return
    if tally is None:
        logger.info("Import of source %d stopped, the source was deleted", source_id)
        return
    logger.info(
        "Import of Lidarr source %d done in %.1f s (%s): %d artists, %d of them unchanged and not read again, "
        "%d albums, %d files, %d unmapped",
        source_id,
        time.perf_counter() - started,
        "only what changed" if light else "everything",
        tally.artists,
        tally.unchanged,
        tally.albums,
        tally.files,
        tally.unmapped_files,
    )


# --- Writing ------------------------------------------------------------------------------ #


def _artist_data(record: ArtistRecord) -> ArtistData:
    return ArtistData(
        mbid=record.mbid or "",
        name=record.name,
        sort_name=record.sort_name or record.name,
        disambiguation=record.disambiguation,
        artist_type=record.artist_type,
        country=None,
        begin_year=None,
        end_year=None,
        ended=record.ended,
        aliases=[],
    )


def _group_data(album: AlbumRecord, artist: ArtistRecord) -> ReleaseGroupData:
    return ReleaseGroupData(
        mbid=album.mbid or "",
        title=album.title,
        primary_type=album.album_type,
        secondary_types=list(album.secondary_types),
        first_release_date=album.release_date,
        disambiguation=None,
        credit=[{"mbid": artist.mbid or "", "name": artist.name, "artist_name": artist.name, "join": ""}],
    )


def _formats_of(format_text: str | None, media_count: int | None) -> list[str]:
    """Lidarr prints ``2xCD`` or ``CD + Digital Media``; nexcrate keeps one format per medium."""
    text = (format_text or "").strip()
    if not text:
        return ["?"] * max(1, media_count or 1)
    formats: list[str] = []
    for part in text.split("+"):
        item = part.strip()
        count = 1
        if "x" in item and item.split("x", 1)[0].strip().isdigit():
            count = int(item.split("x", 1)[0].strip())
            item = item.split("x", 1)[1].strip()
        formats.extend([item or "?"] * max(1, count))
    if media_count and len(formats) < media_count:
        formats.extend([formats[-1] if formats else "?"] * (media_count - len(formats)))
    return formats


def _release_data(album: AlbumRecord, release: Any, tracks: list[TrackRecord], artist: ArtistRecord) -> ReleaseData:
    """One of Lidarr's releases as MusicBrainz would give it, with the tracks when this is the watched release."""
    formats = _formats_of(release.format, release.media_count)
    media: list[MediumData] = []
    by_medium: dict[int, list[TrackRecord]] = defaultdict(list)
    for track in tracks:
        by_medium[track.medium_number or 1].append(track)
    if tracks:
        for position in range(1, max(max(by_medium), len(formats)) + 1):
            items = sorted(by_medium.get(position, []), key=lambda t: (t.absolute_track_number or 0, t.id))
            media.append(
                MediumData(
                    position=position,
                    format=formats[position - 1] if position - 1 < len(formats) else None,
                    name=None,
                    track_count=len(items),
                    tracks=[
                        TrackData(
                            mbid=track.mbid or "",
                            recording_mbid=track.recording_mbid,
                            position=index + 1,
                            number=track.track_number,
                            name=track.title,
                            length_ms=track.duration_ms,
                            credit=[],
                        )
                        for index, track in enumerate(items)
                    ],
                )
            )
    else:
        media = [
            MediumData(position=index + 1, format=item, name=None, track_count=0, tracks=[])
            for index, item in enumerate(formats)
        ]
    return ReleaseData(
        mbid=release.mbid or "",
        title=release.title or album.title,
        status=(release.status or "official").lower(),
        date=release.date or album.release_date,
        country=_country_of(release.country),
        disambiguation=release.disambiguation,
        barcode=None,
        labels=[{"name": name, "catalog_number": None} for name in release.label],
        packaging=None,
        credit=[{"mbid": artist.mbid or "", "name": artist.name, "artist_name": artist.name, "join": ""}],
        media=media,
    )


def _country_of(countries: list[str]) -> str | None:
    """Lidarr lists every country of a release; ten or more mean a worldwide one (``XW``), as for MusicBrainz's
    release events. Otherwise the first, as the ISO code."""
    if len(countries) >= WORLDWIDE_COUNTRIES:
        return "XW"
    return country_code(countries[0]) if countries else None


def country_code(name: str | None) -> str | None:
    """Lidarr's country name as the ISO code MusicBrainz uses; an unknown name stays as it is, a two-letter code too."""
    text = (name or "").strip()
    if not text:
        return None
    if len(text) == 2 and text.isupper():
        return text
    if text.lower() in COUNTRY_CODES:
        return COUNTRY_CODES[text.lower()]
    return text


def _relative(path: str | None, root: str | None) -> str:
    text = (path or "").replace("\\", "/")
    base = (root or "").replace("\\", "/").rstrip("/")
    if base and text.startswith(base + "/"):
        return text[len(base) + 1 :]
    return text.lstrip("/")


def _write_artist(
    db: OrmSession,
    source: Source,
    definition: Any,
    record: ArtistRecord,
    read: ArtistRead,
    profiles: dict[int, str | None],
    tally: Tally,
    library: dict[str, store.KnownArtist],
    moment: datetime,
    language: str,
) -> None:
    artist, new = store.upsert_artist(
        db,
        _artist_data(record),
        moment=moment,
        language=language,
        added_by="import",
        monitor_new=MONITOR_NEW.get(record.monitor_new_items or "", "none"),
        priority=loading.PRIORITY_IMPORT,
    )
    if new:
        tally.artists_new += 1
    tally.artists += 1
    written = clock()
    library[artist.mbid] = store.KnownArtist(artist.id)
    known = library
    tracks_by_album: dict[int, list[TrackRecord]] = defaultdict(list)
    for track in read.tracks:
        tracks_by_album[track.album_id].append(track)
    files_by_album: dict[int, list[TrackFileRecord]] = defaultdict(list)
    for file in read.files:
        files_by_album[file.album_id].append(file)
    # Once per artist, not per album and not per run: a profile saved while a long import runs still counts.
    rules = album_quality.rules_of(db)
    for album in read.albums:
        if not album.mbid:
            continue
        title, title_new = store.apply_release_group(
            db,
            artist,
            _group_data(album, record),
            moment=moment,
            first_load=True,
            watch="none",
            definition=definition,
            artists_by_mbid=known,
        )
        tally.albums += 1
        if title_new:
            tally.albums_new += 1
        _write_album(
            db,
            source,
            definition,
            artist,
            record,
            album,
            title,
            tracks_by_album.get(album.id, []),
            files_by_album.get(album.id, []),
            profiles,
            tally,
            moment,
            language,
            rules,
        )
        if clock() - written >= WRITE_SLICE_SECONDS:
            # ⚠️ Never one write per artist: an artist with hundreds of albums held the write lock for longer than
            # SQLite waits (5 s), and the loading job died with "database is locked" (found on the owner's import).
            # A write per album is safe as well, but costs a third of the run on a slow disk.
            db.commit()
            written = clock()
    needs = artist.groups_refreshed_at is None or bool(loading.albums_needing_releases(db, artist.id, moment))
    if needs and artist.load_state not in loading.UNFINISHED:
        # The catalogue behind Lidarr's albums comes from MusicBrainz later, after the owner's own artists.
        loading.queue(db, artist, loading.PRIORITY_IMPORT)
    db.flush()


def _write_album(
    db: OrmSession,
    source: Source,
    definition: Any,
    artist: Artist,
    record: ArtistRecord,
    album: AlbumRecord,
    title: Title,
    tracks: list[TrackRecord],
    files: list[TrackFileRecord],
    profiles: dict[int, str | None],
    tally: Tally,
    moment: datetime,
    language: str,
    rules: dict[str, Any] | None = None,
) -> None:
    watched = next((release for release in album.releases if release.monitored), None)
    releases = [
        _release_data(album, release, tracks if watched is not None and release.id == watched.id else [], record)
        for release in album.releases
        if release.mbid
    ]
    keep = {watched.mbid} if watched is not None and watched.mbid else set()
    counts = store.apply_releases(db, title, releases, moment=moment, complete=False, keep=keep)
    tally.releases += counts["releases"]
    # What Lidarr lists, not what this run wrote: once MusicBrainz has loaded an album the import writes none of
    # its tracks again, and the number in the result shrank from run to run (found on the bench in real size).
    tally.tracks += sum(1 for track in tracks if track.mbid) if keep else 0
    monitored = bool(album.monitored and record.monitored)
    has_files = bool(files)
    version = store.album_version(db, title.id)
    if version is None and (monitored or has_files):
        version = store.add_version(
            db, title, definition, moment, monitored=monitored, added_by="import", source_id=source.id, event="imported"
        )
    if version is None:
        return
    if version.source_id is None and version.added_by == "owner":
        # The owner's own album, now also in Lidarr: the connection feeds it from here on (as for movies).
        version.source_id = source.id
    if version.source_id != source.id:
        return
    tally.versions_total += 1
    version.lidarr_album_id = album.id
    version.monitored = monitored
    version.source_album_path = record.path
    version.root_folder = record.root_folder_path
    version.profile_name = profiles.get(record.quality_profile_id or -1)
    _write_files(db, version, files, tracks, record, tally, moment)
    # The step of the album is the worst of its files; below the profile's target it shows as "upgrade" (M2).
    album_quality.apply(version, album_quality.step_of_files(file.quality for file in files), rules)
    watched_row = None
    if watched is not None and watched.mbid:
        watched_row = db.scalar(select(Release).where(Release.title_id == title.id, Release.mbid == watched.mbid))
    if has_files and watched_row is not None:
        # Decision 48: with files, Lidarr's watched release is target and actual release.
        version.target_release_id = watched_row.id
        version.actual_release_id = watched_row.id
        version.target_set_by = "source"
        version.target_reason = {"codes": [{"code": "source"}]}
        version.target_suggestion_id = None
        store._count_tracks(db, version)
    else:
        version.actual_release_id = None
        if version.target_set_by == "source":
            version.target_set_by = None
        store.refresh_target(db, title, version, moment=moment, language=language)
    version.state = store.state_of(version)
    version.updated_at = moment
    db.flush()


def _write_files(
    db: OrmSession,
    version: Version,
    files: list[TrackFileRecord],
    tracks: list[TrackRecord],
    record: ArtistRecord,
    tally: Tally,
    moment: datetime,
) -> None:
    """Decision 49: one row per Lidarr file; a file with several tracks keeps their ids and no single track."""
    by_file: dict[int, list[TrackRecord]] = defaultdict(list)
    for track in tracks:
        if track.track_file_id:
            by_file[track.track_file_id].append(track)
    track_ids = dict(
        db.execute(
            select(ReleaseTrack.mbid, ReleaseTrack.id).where(
                ReleaseTrack.mbid.in_([t.mbid for t in tracks if t.mbid] or [""])
            )
        )
        .tuples()
        .all()
    )
    existing = {
        row.relative_path: row for row in db.scalars(select(TrackFile).where(TrackFile.version_id == version.id))
    }
    seen: set[str] = set()
    for file in files:
        relative = _relative(file.path, record.root_folder_path)
        if not relative:
            continue
        row = existing.get(relative)
        if row is None:
            row = TrackFile(version_id=version.id, relative_path=relative, added_at=moment)
            db.add(row)
            existing[relative] = row
        owners = by_file.get(file.id, [])
        row.track_id = track_ids.get(owners[0].mbid or "") if len(owners) == 1 else None
        row.source_track_ids = [track.id for track in owners] if len(owners) > 1 else None
        covered = [track_ids[track.mbid] for track in owners if track.mbid and track.mbid in track_ids]
        row.track_ids = covered if len(owners) > 1 and covered else None
        row.size = file.size
        row.quality = file.quality
        info = file.media_info
        row.audio = (
            {
                "codec": info.audio_codec,
                "bits": info.audio_bits,
                "sample_rate": info.audio_sample_rate,
                "channels": info.audio_channels,
                "bitrate": info.audio_bit_rate,
            }
            if info is not None
            else None
        )
        row.source_file_id = file.id
        row.updated_at = moment
        seen.add(relative)
        tally.files += 1
    for relative, row in existing.items():
        if relative not in seen and row.source_file_id is not None:
            db.delete(row)
    db.flush()
    version.size = sum(file.size for file in files)
    version.has_file = bool(files)
    version.release_group = None


def _write_unmapped(
    db: OrmSession, source: Source, unmapped: list[TrackFileRecord], tally: Tally, moment: datetime
) -> None:
    """Decision 50: the files Lidarr could not map, replaced on every run."""
    db.execute(delete(SourceUnmappedFile).where(SourceUnmappedFile.source_id == source.id))
    for file in unmapped:
        if not file.path:
            continue
        db.add(
            SourceUnmappedFile(
                source_id=source.id, path=file.path, size=file.size, quality=file.quality, seen_at=moment
            )
        )
        tally.unmapped_files += 1
        if len(tally.unmapped_paths) < UNMAPPED_LISTED:
            tally.unmapped_paths.append(file.path)
    db.flush()


def _write_queue(db: OrmSession, source: Source, queue: list[QueueRecord]) -> None:
    """Decision 51: an album in Lidarr's queue shows as downloading with its progress; nothing is touched."""
    by_album: dict[int, QueueRecord] = {}
    for item in queue:
        if item.album_id is not None:
            by_album.setdefault(item.album_id, item)
    for version in db.scalars(
        select(Version).where(Version.source_id == source.id, Version.lidarr_album_id.is_not(None))
    ):
        item = by_album.get(version.lidarr_album_id or -1)
        if item is None:
            if version.state == "downloading":
                version.state = store.state_of(version)
                version.progress = None
            continue
        version.state = "downloading"
        size = item.size or 0.0
        left = item.sizeleft or 0.0
        version.progress = round(max(0.0, min(100.0, (size - left) / size * 100.0)), 1) if size > 0 else None
    db.flush()


def _finish(db: OrmSession, source: Source, run_id: int, seen_albums: set[int], tally: Tally, moment: datetime) -> None:
    """Decision 52: a version this connection fed for an album Lidarr no longer knows goes; the title stays; an
    album the owner added keeps its version, without the connection."""
    for version in list(db.scalars(select(Version).where(Version.source_id == source.id))):
        if version.lidarr_album_id in seen_albums:
            continue
        if version.added_by == "owner":
            version.source_id = None
            version.lidarr_album_id = None
            version.source_album_path = None
            version.updated_at = moment
        else:
            db.delete(version)
            tally.versions_removed += 1
    db.flush()
    run = db.get(ImportRun, run_id)
    if run is None:
        return
    run.status = "done"
    run.finished_at = utcnow()
    run.titles_new = tally.albums_new
    run.titles_updated = tally.albums - tally.albums_new
    run.versions_total = tally.versions_total
    run.versions_removed = tally.versions_removed
    run.error_code = None
    run.details = tally.details()


def release_music_source(db: OrmSession, source_id: int, moment: datetime) -> None:
    """A Lidarr connection is deleted: the owner's versions it fed become nexcrate's own; the importer removes the
    rest. The unmapped files go with the connection."""
    for version in list(db.scalars(select(Version).where(Version.source_id == source_id, Version.added_by == "owner"))):
        version.source_id = None
        version.lidarr_album_id = None
        version.source_album_path = None
        version.updated_at = moment
    db.execute(delete(SourceUnmappedFile).where(SourceUnmappedFile.source_id == source_id))
    # What the scheduled runs remembered of this connection goes with it: a new one may get the same number.
    keys = [SETTING_SEEN.format(source_id=source_id), SETTING_FULL_READ.format(source_id=source_id)]
    db.execute(delete(Setting).where(Setting.key.in_(keys)))
