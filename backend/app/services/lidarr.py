"""A read-only Lidarr client (M1.6, decisions 44 and 45, section "Lidarr lesen").

Only GET, only these addresses: ``/api/v1/system/status``, ``/api/v1/rootfolder``, ``/api/v1/qualityprofile``,
``/api/v1/metadataprofile``, ``/api/v1/artist``, ``/api/v1/album?artistId=``, ``/api/v1/track?artistId=``,
``/api/v1/trackfile?artistId=``, ``/api/v1/trackfile?unmapped=true``, ``/api/v1/queue``, ``/api/v1/tag``
, and for fetching them
into nexcrate ``/api/v1/indexer`` and ``/api/v1/downloadclient`` (read like Radarr's; Lidarr masks keys and
passwords the same way, and keeps its category in ``musicCategory``).

What this shares with ``services/radarr.py`` and ``services/sonarr.py``: the address rules, the key only in the
``X-Api-Key`` header, "missing, not null", a 200 proves nothing without ``application/json`` and the right
``appName``, the queue is paged and a page past the end repeats page 1.

What a throwaway Lidarr 3.1.0.4875 answered (recorded 18.09.2026, ``tests/fixtures/lidarr``):

* An artist carries its MusicBrainz id (``foreignArtistId``), a display name (``artistName``) and, separately, a
  sort name; ``monitorNewItems`` is a word (``all``, ``none``), not a number.
* An album (``/api/v1/album?artistId=``) carries its releases nested, each with a country and a label as lists. The
  album itself has no ``path``; only the artist does. No release in the recording carried a ``releaseDate`` of its
  own, only the album did (an ISO instant, ``1994-09-26T00:00:00Z``); the date is kept as ``YYYY-MM-DD``.
* A track's ``duration`` is an integer number of milliseconds, not seconds; ``trackNumber`` is a string. Like
  Sonarr's ``episodeFileId``, Lidarr writes 0 for ``trackFileId`` on a track without a file; two tracks can share
  one ``trackFileId`` (a two-part song stored as one file).
* An unmapped file (``trackfile?unmapped=true``) carries ``artistId`` and ``albumId`` as 0, not missing: a track
  file nexcrate could not place anywhere.
* Only Lidarr 2 and later is read.

Log lines carry ids and counts, never titles, names or paths.
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass, field
from typing import Any, Self

import httpx

from ..meldungen import meldung
from . import http_log
from .radarr import (
    COLON_REPLACEMENTS,
    RadarrDownloadClient,
    RadarrError,
    RadarrIndexer,
    RootFolder,
    _bool,
    _dict,
    _int,
    _ints,
    _list,
    _number,
    _texts,
    _time,
    normalize_base_url,
    parse_download_client,
    parse_indexer,
    parse_root_folder,
    parse_tags,
)
from .schreibweisen import nfc_or_none

logger = logging.getLogger("nexcrate.lidarr")

APP_NAME = "Lidarr"
MIN_MAJOR = 2
STATUS_PATH = "/api/v1/system/status"
ROOT_FOLDER_PATH = "/api/v1/rootfolder"
QUALITY_PROFILE_PATH = "/api/v1/qualityprofile"
METADATA_PROFILE_PATH = "/api/v1/metadataprofile"
ARTIST_PATH = "/api/v1/artist"
ALBUM_PATH = "/api/v1/album"
TRACK_PATH = "/api/v1/track"
TRACK_FILE_PATH = "/api/v1/trackfile"
QUEUE_PATH = "/api/v1/queue"
INDEXER_PATH = "/api/v1/indexer"
DOWNLOAD_CLIENT_PATH = "/api/v1/downloadclient"
TAG_PATH = "/api/v1/tag"
AUTO_TAGGING_PATH = "/api/v1/autotagging"
DELAY_PROFILE_PATH = "/api/v1/delayprofile"
NAMING_PATH = "/api/v1/config/naming"
QUEUE_PAGE_SIZE = 100
#: Hard cap: 100 pages of 100 are 10,000 queue items.
QUEUE_MAX_PAGES = 100
#: An artist with a large catalogue (hundreds of release groups) takes a while on a small NAS.
TIMEOUT = httpx.Timeout(120.0, connect=10.0)


class LidarrError(RadarrError):
    """An answer of Lidarr that nexcrate cannot use. A ``RadarrError`` so the import handles all three sources alike."""


def unreachable(url: str) -> LidarrError:
    return LidarrError(
        meldung("lidarr_unreachable", f"Lidarr cannot be reached at {url}. Are address and port right?", url=url), 502
    )


def timed_out() -> LidarrError:
    return LidarrError(meldung("lidarr_timeout", "Lidarr did not answer in time."), 504)


def key_rejected() -> LidarrError:
    return LidarrError(
        meldung(
            "lidarr_key_rejected",
            "Lidarr did not accept the API key. Is Radarr, Sonarr or another app answering at this address? That "
            "looks exactly the same.",
        ),
        502,
    )


def not_lidarr() -> LidarrError:
    return LidarrError(meldung("lidarr_not_lidarr", "Something answers at this address, but it is not Lidarr."), 502)


def too_old(version: str) -> LidarrError:
    return LidarrError(
        meldung("lidarr_too_old", f"nexcrate needs Lidarr 2 or later; this is {version}.", version=version), 502
    )


def http_error(status: int) -> LidarrError:
    return LidarrError(meldung("lidarr_http_error", f"Lidarr reports an error (HTTP {status}).", status=status), 502)


#: For the OpenAPI ``responses`` of every route that calls Lidarr.
ERRORS = (
    (502, "lidarr_unreachable"),
    (502, "lidarr_key_rejected"),
    (502, "lidarr_not_lidarr"),
    (502, "lidarr_too_old"),
    (502, "lidarr_http_error"),
    (504, "lidarr_timeout"),
)


def major_of(version: str) -> int | None:
    head = version.strip().split(".", 1)[0]
    return int(head) if head.isdigit() else None


# --- Parsing ------------------------------------------------------------------------------------------------------ #


def _date(value: Any) -> str | None:
    """The date of Lidarr's ``releaseDate`` (an ISO instant): only ``YYYY-MM-DD``, or None."""
    text = nfc_or_none(value)
    if not text or len(text) < 10 or text[4] != "-" or text[7] != "-":
        return None
    head = text[:10]
    return head if head[:4].isdigit() and head[5:7].isdigit() and head[8:].isdigit() else None


@dataclass(frozen=True)
class SystemStatus:
    app_name: str
    version: str


@dataclass(frozen=True)
class ProfileRecord:
    """A quality profile or a metadata profile: nexcrate reads only the name, never the rules inside."""

    id: int
    name: str | None


@dataclass(frozen=True)
class ArtistStatistics:
    album_count: int
    track_file_count: int
    #: With the two above, what a scheduled import compares to tell whether anything changed for the artist
    #: (measured on a real Lidarr, 18.09.2026: count and size follow the files for 12 of 12 artists).
    total_track_count: int = 0
    size_on_disk: int = 0


@dataclass(frozen=True)
class ArtistRecord:
    id: int
    mbid: str | None
    name: str
    sort_name: str | None
    disambiguation: str | None
    artist_type: str | None
    monitored: bool
    monitor_new_items: str | None
    path: str | None
    root_folder_path: str | None
    quality_profile_id: int | None
    metadata_profile_id: int | None
    ended: bool
    statistics: ArtistStatistics
    #: Lidarr's tag ids; their names come from ``/api/v1/tag``.
    tags: tuple[int, ...] = ()


@dataclass(frozen=True)
class AlbumStatistics:
    track_count: int
    track_file_count: int
    total_track_count: int


@dataclass(frozen=True)
class ReleaseRecord:
    id: int
    mbid: str | None
    title: str
    status: str | None
    country: list[str]
    #: ``YYYY-MM-DD``; not carried by a release of the throwaway Lidarr 3.1.0, only by its album.
    date: str | None
    format: str | None
    media_count: int | None
    track_count: int | None
    monitored: bool
    disambiguation: str | None
    label: list[str]


@dataclass(frozen=True)
class AlbumRecord:
    id: int
    mbid: str | None
    artist_id: int
    title: str
    album_type: str | None
    secondary_types: list[str]
    #: ``YYYY-MM-DD``.
    release_date: str | None
    monitored: bool
    any_release_ok: bool
    path: str | None
    statistics: AlbumStatistics
    releases: list[ReleaseRecord] = field(default_factory=list)


@dataclass(frozen=True)
class TrackRecord:
    id: int
    album_id: int
    artist_id: int
    mbid: str | None
    recording_mbid: str | None
    title: str
    track_number: str | None
    absolute_track_number: int | None
    medium_number: int | None
    duration_ms: int | None
    has_file: bool
    track_file_id: int | None


@dataclass(frozen=True)
class MediaInfo:
    audio_channels: int | None
    audio_bit_rate: str | None
    audio_codec: str | None
    audio_bits: str | None
    audio_sample_rate: str | None


@dataclass(frozen=True)
class TrackFileRecord:
    id: int
    artist_id: int
    album_id: int
    path: str | None
    size: int
    date_added: Any = None
    quality: str | None = None
    quality_weight: int | None = None
    media_info: MediaInfo | None = None


@dataclass(frozen=True)
class QueueRecord:
    id: int | None
    artist_id: int | None
    album_id: int | None
    title: str | None
    status: str | None
    size: float | None
    sizeleft: float | None
    download_id: str | None
    protocol: str | None
    tracked_download_state: str | None


def parse_profile(value: Any) -> ProfileRecord | None:
    data = _dict(value)
    profile_id = _int(data.get("id"))
    return ProfileRecord(id=profile_id, name=nfc_or_none(data.get("name"))) if profile_id is not None else None


def parse_artist(value: Any) -> ArtistRecord | None:
    """An artist from ``/api/v1/artist``, or None without a Lidarr id."""
    data = _dict(value)
    artist_id = _int(data.get("id"))
    if artist_id is None:
        return None
    stats = _dict(data.get("statistics"))
    return ArtistRecord(
        id=artist_id,
        mbid=nfc_or_none(data.get("foreignArtistId")) or None,
        name=(nfc_or_none(data.get("artistName")) or "").strip() or f"Lidarr {artist_id}",
        sort_name=nfc_or_none(data.get("sortName")) or None,
        disambiguation=(nfc_or_none(data.get("disambiguation")) or "").strip() or None,
        artist_type=nfc_or_none(data.get("artistType")) or None,
        monitored=_bool(data.get("monitored")) is True,
        monitor_new_items=(nfc_or_none(data.get("monitorNewItems")) or "").strip() or None,
        path=nfc_or_none(data.get("path")) or None,
        root_folder_path=nfc_or_none(data.get("rootFolderPath")) or None,
        quality_profile_id=_int(data.get("qualityProfileId")),
        metadata_profile_id=_int(data.get("metadataProfileId")),
        ended=_bool(data.get("ended")) is True,
        tags=tuple(_ints(data.get("tags"))),
        statistics=ArtistStatistics(
            album_count=_int(stats.get("albumCount")) or 0,
            track_file_count=_int(stats.get("trackFileCount")) or 0,
            total_track_count=_int(stats.get("totalTrackCount")) or 0,
            size_on_disk=_int(stats.get("sizeOnDisk")) or 0,
        ),
    )


def parse_release(value: Any) -> ReleaseRecord | None:
    data = _dict(value)
    release_id = _int(data.get("id"))
    if release_id is None:
        return None
    return ReleaseRecord(
        id=release_id,
        mbid=nfc_or_none(data.get("foreignReleaseId")) or None,
        title=(nfc_or_none(data.get("title")) or "").strip(),
        status=nfc_or_none(data.get("status")),
        country=_texts(data.get("country")),
        date=_date(data.get("releaseDate")),
        format=nfc_or_none(data.get("format")),
        media_count=_int(data.get("mediumCount")),
        track_count=_int(data.get("trackCount")),
        monitored=_bool(data.get("monitored")) is True,
        disambiguation=(nfc_or_none(data.get("disambiguation")) or "").strip() or None,
        label=_texts(data.get("label")),
    )


def parse_album(value: Any) -> AlbumRecord | None:
    """An album from ``/api/v1/album``, or None without a Lidarr id or an artist id."""
    data = _dict(value)
    album_id = _int(data.get("id"))
    artist_id = _int(data.get("artistId"))
    if album_id is None or artist_id is None:
        return None
    stats = _dict(data.get("statistics"))
    releases = [item for item in map(parse_release, _list(data.get("releases"))) if item is not None]
    return AlbumRecord(
        id=album_id,
        mbid=nfc_or_none(data.get("foreignAlbumId")) or None,
        artist_id=artist_id,
        title=(nfc_or_none(data.get("title")) or "").strip() or f"Lidarr {album_id}",
        album_type=nfc_or_none(data.get("albumType")),
        secondary_types=_texts(data.get("secondaryTypes")),
        release_date=_date(data.get("releaseDate")),
        monitored=_bool(data.get("monitored")) is True,
        any_release_ok=_bool(data.get("anyReleaseOk")) is True,
        path=nfc_or_none(data.get("path")) or None,
        statistics=AlbumStatistics(
            track_count=_int(stats.get("trackCount")) or 0,
            track_file_count=_int(stats.get("trackFileCount")) or 0,
            total_track_count=_int(stats.get("totalTrackCount")) or 0,
        ),
        releases=releases,
    )


def parse_track(value: Any) -> TrackRecord | None:
    data = _dict(value)
    track_id = _int(data.get("id"))
    album_id = _int(data.get("albumId"))
    artist_id = _int(data.get("artistId"))
    if track_id is None or album_id is None or artist_id is None:
        return None
    file_id = _int(data.get("trackFileId"))
    has_file = _bool(data.get("hasFile"))
    return TrackRecord(
        id=track_id,
        album_id=album_id,
        artist_id=artist_id,
        mbid=nfc_or_none(data.get("foreignTrackId")) or None,
        recording_mbid=nfc_or_none(data.get("foreignRecordingId")) or None,
        title=(nfc_or_none(data.get("title")) or "").strip(),
        track_number=(nfc_or_none(data.get("trackNumber")) or "").strip() or None,
        absolute_track_number=_int(data.get("absoluteTrackNumber")),
        medium_number=_int(data.get("mediumNumber")),
        duration_ms=_int(data.get("duration")),
        has_file=has_file is True,
        # Lidarr writes 0 for a track without a file, like Sonarr's episodeFileId.
        track_file_id=file_id if file_id and file_id > 0 and has_file is not False else None,
    )


def parse_media_info(value: Any) -> MediaInfo | None:
    data = _dict(value)
    if not data:
        return None
    channels = data.get("audioChannels")
    return MediaInfo(
        audio_channels=channels if isinstance(channels, int) and not isinstance(channels, bool) else None,
        audio_bit_rate=nfc_or_none(data.get("audioBitRate")),
        audio_codec=nfc_or_none(data.get("audioCodec")),
        audio_bits=nfc_or_none(data.get("audioBits")),
        audio_sample_rate=nfc_or_none(data.get("audioSampleRate")),
    )


def parse_track_file(value: Any) -> TrackFileRecord | None:
    """A track file from ``/api/v1/trackfile``, or None without a Lidarr id.

    ⚠️ An unmapped file carries ``artistId`` and ``albumId`` as 0, not missing: they are kept as 0, not turned into
    None, so a caller can tell "unmapped" from "the field was missing".
    """
    data = _dict(value)
    file_id = _int(data.get("id"))
    if file_id is None:
        return None
    quality = _dict(_dict(data.get("quality")).get("quality"))
    return TrackFileRecord(
        id=file_id,
        artist_id=_int(data.get("artistId")) or 0,
        album_id=_int(data.get("albumId")) or 0,
        path=nfc_or_none(data.get("path")) or None,
        size=max(0, _int(data.get("size")) or 0),
        date_added=_time(data.get("dateAdded")),
        quality=nfc_or_none(quality.get("name")),
        quality_weight=_int(data.get("qualityWeight")),
        media_info=parse_media_info(data.get("mediaInfo")),
    )


def parse_queue_item(value: Any) -> QueueRecord | None:
    if not isinstance(value, dict):
        return None
    return QueueRecord(
        id=_int(value.get("id")),
        artist_id=_int(value.get("artistId")),
        album_id=_int(value.get("albumId")),
        title=nfc_or_none(value.get("title")),
        status=nfc_or_none(value.get("status")),
        size=_number(value.get("size")),
        sizeleft=_number(value.get("sizeleft")),
        download_id=nfc_or_none(value.get("downloadId")),
        protocol=nfc_or_none(value.get("protocol")),
        tracked_download_state=nfc_or_none(value.get("trackedDownloadState")),
    )


# --- The client --------------------------------------------------------------------------------------------------- #


def parse_lidarr_indexer(value: Any) -> RadarrIndexer | None:
    """An indexer of Lidarr: Radarr's shape, its categories are music categories."""
    indexer = parse_indexer(value)
    if indexer is None:
        return None
    if indexer.name == f"Radarr indexer {indexer.id}":
        return dataclasses.replace(indexer, name=f"Lidarr indexer {indexer.id}")
    return indexer


def parse_lidarr_download_client(value: Any) -> RadarrDownloadClient | None:
    """A download client of Lidarr: Radarr's shape, the category from ``musicCategory``."""
    client = parse_download_client(value)
    if client is None:
        return None
    category: str | None = None
    for field_value in map(_dict, _list(_dict(value).get("fields"))):
        if field_value.get("name") == "musicCategory" and "value" in field_value:
            category = (nfc_or_none(field_value["value"]) or "").strip() or None
    name = client.name
    if name == f"Radarr download client {client.id}":
        name = f"Lidarr download client {client.id}"
    return dataclasses.replace(client, category=category, name=name)


@dataclass(frozen=True)
class MusicNamingConfig:
    """Lidarr's naming: the four patterns nexcrate knows, whether it renames at all and its colon replacement."""

    artist_folder: str
    album_folder: str
    track_file: str
    multi_disc_file: str
    rename_tracks: bool
    colon_replacement: str | None
    replace_illegal_characters: bool = True


def parse_naming(value: Any) -> MusicNamingConfig:
    """``/api/v1/config/naming`` of Lidarr; a missing pattern is an empty text."""
    data = _dict(value)
    colon = data.get("colonReplacementFormat")
    return MusicNamingConfig(
        artist_folder=nfc_or_none(data.get("artistFolderFormat")) or "",
        album_folder=nfc_or_none(data.get("albumFolderFormat")) or "",
        track_file=nfc_or_none(data.get("standardTrackFormat")) or "",
        multi_disc_file=nfc_or_none(data.get("multiDiscTrackFormat")) or "",
        rename_tracks=_bool(data.get("renameTracks")) is True,
        colon_replacement=COLON_REPLACEMENTS.get(colon)
        if isinstance(colon, int) and not isinstance(colon, bool)
        else (nfc_or_none(colon) if isinstance(colon, str) else None),
        replace_illegal_characters=_bool(data.get("replaceIllegalCharacters")) is not False,
    )


class LidarrClient:
    """Use as ``async with LidarrClient(url, key) as lidarr: ...``."""

    def __init__(self, base_url: str, api_key: str, *, timeout: httpx.Timeout | float = TIMEOUT) -> None:
        self.base_url = normalize_base_url(base_url)
        self._api_key = api_key
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> Self:
        # ⚠️ The key goes into a header of the client, never into params or a URL.
        self._http = http_log.client(
            "lidarr",
            base_url=self.base_url,
            timeout=self._timeout,
            headers={"X-Api-Key": self._api_key},
            follow_redirects=False,
            # Its answers carry names and paths: never in the log, not even in trace mode (decision 44).
            log_bodies=False,
        )
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError("LidarrClient is used outside of 'async with'")
        return self._http

    async def _json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if not self._api_key.isascii() or not self._api_key.isprintable():
            raise key_rejected()
        try:
            response = await http_log.send(self.http, "GET", path, params=params)
        except httpx.TimeoutException as exc:
            raise timed_out() from exc
        except httpx.RequestError as exc:
            raise unreachable(self.base_url) from exc
        status = response.status_code
        if status in (401, 403):
            raise key_rejected()
        if 300 <= status < 400 or status == 404:
            raise not_lidarr()
        if not 200 <= status < 300:
            raise http_error(status)
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise not_lidarr()
        try:
            return response.json()
        except ValueError as exc:
            raise not_lidarr() from exc

    async def _list(self, path: str, params: dict[str, Any] | None = None) -> list[Any]:
        data = await self._json(path, params)
        if not isinstance(data, list):
            raise not_lidarr()
        return data

    async def system_status(self) -> SystemStatus:
        """Proves the app is Lidarr 2 or later."""
        data = await self._json(STATUS_PATH)
        if not isinstance(data, dict) or data.get("appName") != APP_NAME:
            raise not_lidarr()
        version = nfc_or_none(data.get("version")) or ""
        major = major_of(version)
        if major is None or major < MIN_MAJOR:
            raise too_old(version[:32] or "?")
        return SystemStatus(app_name=APP_NAME, version=version)

    async def root_folders(self) -> list[RootFolder]:
        return [folder for folder in map(parse_root_folder, await self._list(ROOT_FOLDER_PATH)) if folder]

    async def quality_profiles(self) -> list[ProfileRecord]:
        return [profile for profile in map(parse_profile, await self._list(QUALITY_PROFILE_PATH)) if profile]

    async def metadata_profiles(self) -> list[ProfileRecord]:
        return [profile for profile in map(parse_profile, await self._list(METADATA_PROFILE_PATH)) if profile]

    async def tags(self) -> dict[int, str]:
        return parse_tags(await self._list(TAG_PATH))

    async def artists(self) -> list[ArtistRecord]:
        return [item for item in map(parse_artist, await self._list(ARTIST_PATH)) if item is not None]

    async def albums(self, artist_id: int) -> list[AlbumRecord]:
        raw = await self._list(ALBUM_PATH, {"artistId": artist_id})
        return [item for item in map(parse_album, raw) if item is not None and item.artist_id == artist_id]

    async def tracks(self, artist_id: int) -> list[TrackRecord]:
        raw = await self._list(TRACK_PATH, {"artistId": artist_id})
        return [item for item in map(parse_track, raw) if item is not None and item.artist_id == artist_id]

    async def track_files(self, artist_id: int) -> list[TrackFileRecord]:
        raw = await self._list(TRACK_FILE_PATH, {"artistId": artist_id})
        return [item for item in map(parse_track_file, raw) if item is not None and item.artist_id == artist_id]

    async def unmapped_files(self) -> list[TrackFileRecord]:
        raw = await self._list(TRACK_FILE_PATH, {"unmapped": "true"})
        return [item for item in map(parse_track_file, raw) if item is not None]

    async def naming(self) -> MusicNamingConfig:
        data = await self._json(NAMING_PATH)
        if not isinstance(data, dict):
            raise not_lidarr()
        return parse_naming(data)

    async def delay_profiles(self) -> list[Any]:
        """The delay profiles as the app lists them (measured 21.09.2026: the same fields in Radarr 6.3.0, Sonarr
        4.0.19 and Lidarr 3.1.0). ``services/delay.from_arr`` reads them."""
        return await self._list(DELAY_PROFILE_PATH)

    async def auto_tagging(self) -> tuple[list[Any], list[Any]]:
        """The auto tagging rules and their schema, as the app lists them. The schema names the
        languages behind the numbers of an original language condition (measured 22.09.2026: Radarr 6.3.0, Sonarr
        4.0.19, Lidarr 3.1.0)."""
        return await self._list(AUTO_TAGGING_PATH), await self._list(AUTO_TAGGING_PATH + "/schema")

    async def indexers(self) -> list[RadarrIndexer]:
        return [item for item in map(parse_lidarr_indexer, await self._list(INDEXER_PATH)) if item]

    async def download_clients(self) -> list[RadarrDownloadClient]:
        return [item for item in map(parse_lidarr_download_client, await self._list(DOWNLOAD_CLIENT_PATH)) if item]

    async def queue(self) -> list[QueueRecord]:
        """Every queue record. ⚠️ A page past the end repeats page 1; the loop stops by ``totalRecords``."""
        items: list[QueueRecord] = []
        seen: set[int] = set()
        page = 1
        while True:
            params = {"page": page, "pageSize": QUEUE_PAGE_SIZE, "includeArtist": "true", "includeAlbum": "true"}
            data = await self._json(QUEUE_PATH, params)
            if not isinstance(data, dict):
                raise not_lidarr()
            answered = _int(data.get("page"))
            if page > 1 and answered is not None and answered != page:
                break
            for item in map(parse_queue_item, _list(data.get("records"))):
                if item is None or (item.id is not None and item.id in seen):
                    continue
                if item.id is not None:
                    seen.add(item.id)
                items.append(item)
            total = _int(data.get("totalRecords")) or 0
            page_size = _int(data.get("pageSize")) or QUEUE_PAGE_SIZE
            if page_size <= 0 or page * page_size >= total:
                break
            if page >= QUEUE_MAX_PAGES:
                logger.warning("The Lidarr queue has more than %d pages; the rest is left out", QUEUE_MAX_PAGES)
                break
            page += 1
        return items
