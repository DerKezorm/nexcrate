"""A read-only Sonarr client (S1.7).

Only GET, only these addresses: ``/api/v3/system/status``, ``/api/v3/series``, ``/api/v3/episode?seriesId=``,
``/api/v3/episodefile?seriesId=``, ``/api/v3/queue``, ``/api/v3/rootfolder``, ``/api/v3/qualityprofile``,
``/api/v3/customformat``, ``/api/v3/qualitydefinition``, ``/api/v3/tag``, and for the
takeover ``/api/v3/config/naming``, ``/api/v3/config/mediamanagement``, ``/api/v3/indexer`` and
``/api/v3/downloadclient``, of which only the fields the Radarr client reads are read (``tvCategory`` for Sonarr).
⚠️ Never ``/api/v3/config/host``: it answers Sonarr's own API key in plain text.

What it shares with the Radarr client (``services/radarr.py``): the address rules, the key only in the ``X-Api-Key``
header, "missing, not null", a 200 proves nothing without ``application/json`` and the right ``appName``, the queue is
paged and a page past the end repeats page 1.

What Sonarr 4 answers (research of 15.09.2026, the facts of the plan):

* ``episode`` and ``episodefile`` answer 400 without ``seriesId``; a double episode is several episodes with one
  ``episodeFileId``.
* An episode carries TVDB's numbers, the scene numbers and ``unverifiedSceneNumbering``; a file ``relativePath`` to the
  series folder, ``sceneName`` (empty after a disk scan), ``releaseType`` and a short ``mediaInfo``.
* Only Sonarr 4 and later is read: version 3 is out of support.

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
    MEDIA_FIELDS,
    MediaManagement,
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
    parse_media_management,
    parse_root_folder,
    parse_tags,
)
from .schreibweisen import nfc_or_none

logger = logging.getLogger("nexcrate.sonarr")

APP_NAME = "Sonarr"
MIN_MAJOR = 4
STATUS_PATH = "/api/v3/system/status"
SERIES_PATH = "/api/v3/series"
EPISODE_PATH = "/api/v3/episode"
EPISODE_FILE_PATH = "/api/v3/episodefile"
QUEUE_PATH = "/api/v3/queue"
ROOT_FOLDER_PATH = "/api/v3/rootfolder"
PROFILE_PATH = "/api/v3/qualityprofile"
CUSTOM_FORMAT_PATH = "/api/v3/customformat"
QUALITY_DEFINITION_PATH = "/api/v3/qualitydefinition"
RELEASE_PROFILE_PATH = "/api/v3/releaseprofile"
TAG_PATH = "/api/v3/tag"
AUTO_TAGGING_PATH = "/api/v3/autotagging"
NAMING_PATH = "/api/v3/config/naming"
MEDIA_MANAGEMENT_PATH = "/api/v3/config/mediamanagement"
INDEXER_PATH = "/api/v3/indexer"
DOWNLOAD_CLIENT_PATH = "/api/v3/downloadclient"
DELAY_PROFILE_PATH = "/api/v3/delayprofile"
#: Sonarr's ``MultiEpisodeStyle`` by number (measured on Sonarr 4.0.19, 17.09.2026).
MULTI_EPISODE_STYLES = {
    0: "extend",
    1: "duplicate",
    2: "repeat",
    3: "scene",
    4: "range",
    5: "prefixed_range",
}
#: Sonarr's ``ColonReplacementFormat`` adds 5, custom, to Radarr's numbers.
SONARR_COLON_REPLACEMENTS = {**COLON_REPLACEMENTS, 5: "custom"}
QUEUE_PAGE_SIZE = 250
QUEUE_MAX_PAGES = 40
#: A daily show's episode list takes a while on a small NAS.
TIMEOUT = httpx.Timeout(120.0, connect=10.0)
class SonarrError(RadarrError):
    """An answer of Sonarr that nexcrate cannot use. A ``RadarrError`` so the import handles both alike."""


def unreachable(url: str) -> SonarrError:
    return SonarrError(
        meldung("sonarr_unreachable", f"Sonarr cannot be reached at {url}. Are address and port right?", url=url), 502
    )


def timed_out() -> SonarrError:
    return SonarrError(meldung("sonarr_timeout", "Sonarr did not answer in time."), 504)


def key_rejected() -> SonarrError:
    return SonarrError(
        meldung(
            "sonarr_key_rejected",
            "Sonarr did not accept the API key. Is Radarr or another app answering at this address? That looks exactly "
            "the same.",
        ),
        502,
    )


def not_sonarr() -> SonarrError:
    return SonarrError(meldung("sonarr_not_sonarr", "Something answers at this address, but it is not Sonarr."), 502)


def too_old(version: str) -> SonarrError:
    return SonarrError(
        meldung("sonarr_too_old", f"nexcrate needs Sonarr 4 or later; this is {version}.", version=version), 502
    )


def http_error(status: int) -> SonarrError:
    return SonarrError(meldung("sonarr_http_error", f"Sonarr reports an error (HTTP {status}).", status=status), 502)


#: For the OpenAPI ``responses`` of every route that calls Sonarr.
ERRORS = (
    (502, "sonarr_unreachable"),
    (502, "sonarr_key_rejected"),
    (502, "sonarr_not_sonarr"),
    (502, "sonarr_too_old"),
    (502, "sonarr_http_error"),
    (504, "sonarr_timeout"),
)


def major_of(version: str) -> int | None:
    head = version.strip().split(".", 1)[0]
    return int(head) if head.isdigit() else None


# --- Parsing ------------------------------------------------------------------------------------------------------ #


def _date(value: Any) -> str | None:
    """Sonarr's ``airDate`` (``YYYY-MM-DD``), or None."""
    text = nfc_or_none(value)
    if not text or len(text) != 10 or text[4] != "-" or text[7] != "-":
        return None
    return text if text[:4].isdigit() and text[5:7].isdigit() and text[8:].isdigit() else None


@dataclass(frozen=True)
class SystemStatus:
    app_name: str
    version: str


@dataclass(frozen=True)
class SeasonItem:
    number: int
    monitored: bool


@dataclass(frozen=True)
class SeriesItem:
    id: int
    tvdb_id: int | None
    tmdb_id: int | None
    imdb_id: str | None
    title: str
    year: int | None
    series_type: str
    path: str | None
    root_folder_path: str | None
    quality_profile_id: int | None
    monitored: bool
    monitor_new_items: str | None
    season_folder: bool
    use_scene_numbering: bool
    alternate_titles: list[str] = field(default_factory=list)
    seasons: list[SeasonItem] = field(default_factory=list)
    #: Sonarr's tag ids; their names come from ``/api/v3/tag``.
    tags: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class EpisodeItem:
    id: int
    series_id: int
    tvdb_id: int | None
    season: int
    episode: int
    absolute: int | None
    scene_season: int | None
    scene_episode: int | None
    scene_absolute: int | None
    unverified_scene: bool
    air_date: str | None
    title: str
    monitored: bool
    episode_file_id: int | None


@dataclass(frozen=True)
class EpisodeFileItem:
    id: int
    series_id: int
    season: int | None
    relative_path: str | None
    size: int
    quality: str | None
    languages: list[str]
    release_group: str | None
    scene_name: str | None
    release_type: str | None
    media_info: dict[str, Any] | None
    #: Sonarr's own verdict whether the file may still be upgraded, computed with the series' profile on each call.
    cutoff_not_met: bool | None
    date_added: Any = None


@dataclass(frozen=True)
class QueueItem:
    """A queue record of one episode. Attribute names as ``radarr.QueueItem``, so the import's state rules fit both."""

    id: int | None
    series_id: int | None
    episode_id: int | None
    status: str | None
    tracked_download_status: str | None
    tracked_download_state: str | None
    size: float | None
    sizeleft: float | None


@dataclass(frozen=True)
class QualityProfile:
    id: int
    name: str | None


def parse_series(value: Any) -> SeriesItem | None:
    """A series from ``/api/v3/series``, or None without a Sonarr id."""
    data = _dict(value)
    series_id = _int(data.get("id"))
    if series_id is None:
        return None
    tvdb_id = _int(data.get("tvdbId"))
    tmdb_id = _int(data.get("tmdbId"))
    kind = (nfc_or_none(data.get("seriesType")) or "standard").strip().lower()
    seasons: list[SeasonItem] = []
    for season in map(_dict, _list(data.get("seasons"))):
        number = _int(season.get("seasonNumber"))
        if number is not None and number >= 0:
            seasons.append(SeasonItem(number=number, monitored=_bool(season.get("monitored")) is True))
    return SeriesItem(
        id=series_id,
        tvdb_id=tvdb_id if tvdb_id is not None and tvdb_id > 0 else None,
        tmdb_id=tmdb_id if tmdb_id is not None and tmdb_id > 0 else None,
        imdb_id=(nfc_or_none(data.get("imdbId")) or "").strip().lower() or None,
        title=(nfc_or_none(data.get("title")) or "").strip() or f"Sonarr {series_id}",
        year=_int(data.get("year")) or None,
        series_type=kind if kind in ("standard", "daily", "anime") else "standard",
        path=nfc_or_none(data.get("path")) or None,
        root_folder_path=nfc_or_none(data.get("rootFolderPath")) or None,
        quality_profile_id=_int(data.get("qualityProfileId")),
        monitored=_bool(data.get("monitored")) is True,
        monitor_new_items=(nfc_or_none(data.get("monitorNewItems")) or "").strip() or None,
        season_folder=_bool(data.get("seasonFolder")) is not False,
        use_scene_numbering=_bool(data.get("useSceneNumbering")) is True,
        alternate_titles=_texts([_dict(item).get("title") for item in _list(data.get("alternateTitles"))]),
        seasons=seasons,
        tags=_ints(data.get("tags")),
    )


def parse_episode(value: Any) -> EpisodeItem | None:
    data = _dict(value)
    episode_id = _int(data.get("id"))
    series_id = _int(data.get("seriesId"))
    season = _int(data.get("seasonNumber"))
    number = _int(data.get("episodeNumber"))
    if episode_id is None or series_id is None or season is None or season < 0 or number is None:
        return None
    file_id = _int(data.get("episodeFileId"))
    has_file = _bool(data.get("hasFile"))
    tvdb_id = _int(data.get("tvdbId"))
    return EpisodeItem(
        id=episode_id,
        series_id=series_id,
        tvdb_id=tvdb_id if tvdb_id is not None and tvdb_id > 0 else None,
        season=season,
        episode=number,
        absolute=_int(data.get("absoluteEpisodeNumber")),
        scene_season=_int(data.get("sceneSeasonNumber")),
        scene_episode=_int(data.get("sceneEpisodeNumber")),
        scene_absolute=_int(data.get("sceneAbsoluteEpisodeNumber")),
        unverified_scene=_bool(data.get("unverifiedSceneNumbering")) is True,
        air_date=_date(data.get("airDate")),
        title=(nfc_or_none(data.get("title")) or "").strip(),
        monitored=_bool(data.get("monitored")) is True,
        # Sonarr writes 0 for an episode without a file.
        episode_file_id=file_id if file_id and file_id > 0 and has_file is not False else None,
    )


def parse_episode_file(value: Any) -> EpisodeFileItem | None:
    data = _dict(value)
    file_id = _int(data.get("id"))
    series_id = _int(data.get("seriesId"))
    if file_id is None or series_id is None:
        return None
    quality = _dict(_dict(data.get("quality")).get("quality"))
    languages = [nfc_or_none(_dict(item).get("name")) for item in _list(data.get("languages"))]
    media = _dict(data.get("mediaInfo"))
    kept = {
        name: media[name]
        for name in MEDIA_FIELDS
        if name in media and isinstance(media[name], str | int | float) and not isinstance(media[name], bool)
    }
    relative = (nfc_or_none(data.get("relativePath")) or "").replace("\\", "/").strip("/")
    return EpisodeFileItem(
        id=file_id,
        series_id=series_id,
        season=_int(data.get("seasonNumber")),
        relative_path=relative or None,
        size=max(0, _int(data.get("size")) or 0),
        quality=nfc_or_none(quality.get("name")),
        languages=[language for language in languages if language],
        release_group=(nfc_or_none(data.get("releaseGroup")) or "").strip() or None,
        scene_name=(nfc_or_none(data.get("sceneName")) or "").strip() or None,
        release_type=(nfc_or_none(data.get("releaseType")) or "").strip()[:32] or None,
        media_info=kept or None,
        cutoff_not_met=data.get("qualityCutoffNotMet") if isinstance(data.get("qualityCutoffNotMet"), bool) else None,
        date_added=_time(data.get("dateAdded")),
    )


def parse_queue_item(value: Any) -> QueueItem | None:
    if not isinstance(value, dict):
        return None
    return QueueItem(
        id=_int(value.get("id")),
        series_id=_int(value.get("seriesId")),
        episode_id=_int(value.get("episodeId")),
        status=nfc_or_none(value.get("status")),
        tracked_download_status=nfc_or_none(value.get("trackedDownloadStatus")),
        tracked_download_state=nfc_or_none(value.get("trackedDownloadState")),
        size=_number(value.get("size")),
        sizeleft=_number(value.get("sizeleft")),
    )


def parse_quality_profile(value: Any) -> QualityProfile | None:
    data = _dict(value)
    profile_id = _int(data.get("id"))
    return QualityProfile(id=profile_id, name=nfc_or_none(data.get("name"))) if profile_id is not None else None


@dataclass(frozen=True)
class SeriesNamingConfig:
    """Sonarr's naming: the patterns, whether it renames, the multi episode style and the colon replacement."""

    series_folder: str
    season_folder: str
    specials_folder: str
    episode_file: str
    daily_file: str
    anime_file: str
    rename_episodes: bool
    #: ``extend``, ``duplicate``, ``repeat``, ``scene``, ``range``, ``prefixed_range``; None when unknown.
    multi_episode_style: str | None
    colon_replacement: str | None
    replace_illegal_characters: bool = True


def parse_naming(value: Any) -> SeriesNamingConfig:
    """``/api/v3/config/naming`` of Sonarr; a missing pattern is an empty text."""
    data = _dict(value)
    colon = data.get("colonReplacementFormat")
    style = data.get("multiEpisodeStyle")
    return SeriesNamingConfig(
        series_folder=nfc_or_none(data.get("seriesFolderFormat")) or "",
        season_folder=nfc_or_none(data.get("seasonFolderFormat")) or "",
        specials_folder=nfc_or_none(data.get("specialsFolderFormat")) or "",
        episode_file=nfc_or_none(data.get("standardEpisodeFormat")) or "",
        daily_file=nfc_or_none(data.get("dailyEpisodeFormat")) or "",
        anime_file=nfc_or_none(data.get("animeEpisodeFormat")) or "",
        rename_episodes=_bool(data.get("renameEpisodes")) is True,
        multi_episode_style=MULTI_EPISODE_STYLES.get(style)
        if isinstance(style, int) and not isinstance(style, bool)
        else None,
        colon_replacement=SONARR_COLON_REPLACEMENTS.get(colon)
        if isinstance(colon, int) and not isinstance(colon, bool)
        else (nfc_or_none(colon) if isinstance(colon, str) else None),
        replace_illegal_characters=_bool(data.get("replaceIllegalCharacters")) is not False,
    )


def parse_sonarr_download_client(value: Any) -> RadarrDownloadClient | None:
    """A download client of Sonarr: Radarr's shape, the category from ``tvCategory``."""
    client = parse_download_client(value)
    if client is None:
        return None
    category: str | None = None
    for field_value in map(_dict, _list(_dict(value).get("fields"))):
        if field_value.get("name") == "tvCategory" and "value" in field_value:
            category = (nfc_or_none(field_value["value"]) or "").strip() or None
    name = client.name
    if name == f"Radarr download client {client.id}":
        name = f"Sonarr download client {client.id}"
    return dataclasses.replace(client, category=category, name=name)


def parse_sonarr_indexer(value: Any) -> RadarrIndexer | None:
    indexer = parse_indexer(value)
    if indexer is None:
        return None
    if indexer.name == f"Radarr indexer {indexer.id}":
        return dataclasses.replace(indexer, name=f"Sonarr indexer {indexer.id}")
    return indexer


# --- The client --------------------------------------------------------------------------------------------------- #


class SonarrClient:
    """Use as ``async with SonarrClient(url, key) as sonarr: ...``."""

    def __init__(self, base_url: str, api_key: str, *, timeout: httpx.Timeout | float = TIMEOUT) -> None:
        self.base_url = normalize_base_url(base_url)
        self._api_key = api_key
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> Self:
        # ⚠️ The key goes into a header of the client, never into params or a URL.
        self._http = http_log.client(
            "sonarr",
            base_url=self.base_url,
            timeout=self._timeout,
            headers={"X-Api-Key": self._api_key},
            follow_redirects=False,
        )
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError("SonarrClient is used outside of 'async with'")
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
        if status == 401:
            raise key_rejected()
        if 300 <= status < 400 or status == 404:
            raise not_sonarr()
        if not 200 <= status < 300:
            raise http_error(status)
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise not_sonarr()
        try:
            return response.json()
        except ValueError as exc:
            raise not_sonarr() from exc

    async def _list(self, path: str, params: dict[str, Any] | None = None) -> list[Any]:
        data = await self._json(path, params)
        if not isinstance(data, list):
            raise not_sonarr()
        return data

    async def system_status(self) -> SystemStatus:
        """Proves the app is Sonarr 4 or later."""
        data = await self._json(STATUS_PATH)
        if not isinstance(data, dict) or data.get("appName") != APP_NAME:
            raise not_sonarr()
        version = nfc_or_none(data.get("version")) or ""
        major = major_of(version)
        if major is None or major < MIN_MAJOR:
            raise too_old(version[:32] or "?")
        return SystemStatus(app_name=APP_NAME, version=version)

    async def tags(self) -> dict[int, str]:
        return parse_tags(await self._list(TAG_PATH))

    async def series(self) -> tuple[list[SeriesItem], int]:
        """Every series, and how many entries were skipped for lack of an id."""
        raw = await self._list(SERIES_PATH)
        parsed = [parse_series(item) for item in raw]
        found = [item for item in parsed if item is not None]
        return found, len(raw) - len(found)

    async def series_count(self) -> int:
        return sum(1 for item in await self._list(SERIES_PATH) if isinstance(item, dict))

    async def episodes(self, series_id: int) -> list[EpisodeItem]:
        raw = await self._list(EPISODE_PATH, {"seriesId": series_id})
        return [item for item in map(parse_episode, raw) if item is not None and item.series_id == series_id]

    async def episode_files(self, series_id: int) -> list[EpisodeFileItem]:
        raw = await self._list(EPISODE_FILE_PATH, {"seriesId": series_id})
        return [item for item in map(parse_episode_file, raw) if item is not None and item.series_id == series_id]

    async def root_folders(self) -> list[RootFolder]:
        return [folder for folder in map(parse_root_folder, await self._list(ROOT_FOLDER_PATH)) if folder]

    async def quality_profiles(self) -> list[QualityProfile]:
        return [profile for profile in map(parse_quality_profile, await self._list(PROFILE_PATH)) if profile]

    async def quality_setup(self) -> tuple[list[Any], list[Any], list[Any]]:
        """The quality setup as it comes: profiles, custom formats and quality definitions, unparsed.

        For taking a setup over (E5). Everything is read; what can be rebuilt here is
        decided in ``services/profiles/arr_import.py``, not in the client.
        """
        return (
            await self._list(PROFILE_PATH),
            await self._list(CUSTOM_FORMAT_PATH),
            await self._list(QUALITY_DEFINITION_PATH),
        )

    async def release_profiles(self) -> list[Any]:
        """The release profiles as they come (must contain, must not contain). Radarr 5 has none."""
        return await self._list(RELEASE_PROFILE_PATH)

    async def naming(self) -> SeriesNamingConfig:
        data = await self._json(NAMING_PATH)
        if not isinstance(data, dict):
            raise not_sonarr()
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

    async def propers_and_repacks(self) -> str | None:
        """Propers and repacks there: ``preferAndUpgrade`` (the default), ``doNotUpgrade`` or ``doNotPrefer``."""
        data = await self._json(MEDIA_MANAGEMENT_PATH)
        value = data.get("downloadPropersAndRepacks") if isinstance(data, dict) else None
        return str(value) if value else None

    async def media_management(self) -> MediaManagement:
        data = await self._json(MEDIA_MANAGEMENT_PATH)
        if not isinstance(data, dict):
            raise not_sonarr()
        return parse_media_management(data)

    async def indexers(self) -> list[RadarrIndexer]:
        return [item for item in map(parse_sonarr_indexer, await self._list(INDEXER_PATH)) if item]

    async def download_clients(self) -> list[RadarrDownloadClient]:
        return [item for item in map(parse_sonarr_download_client, await self._list(DOWNLOAD_CLIENT_PATH)) if item]

    async def queue(self) -> list[QueueItem]:
        """Every queue record. ⚠️ A page past the end repeats page 1; the loop stops by ``totalRecords``."""
        items: list[QueueItem] = []
        seen: set[int] = set()
        page = 1
        while True:
            params = {
                "includeUnknownSeriesItems": "true",
                "includeSeries": "false",
                "includeEpisode": "false",
                "pageSize": QUEUE_PAGE_SIZE,
                "page": page,
            }
            data = await self._json(QUEUE_PATH, params)
            if not isinstance(data, dict):
                raise not_sonarr()
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
                logger.warning("The Sonarr queue has more than %d pages; the rest is left out", QUEUE_MAX_PAGES)
                break
            page += 1
        return items
