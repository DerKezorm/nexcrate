"""A read-only Radarr client.

Only GET, only these addresses: ``/api/v3/system/status``, ``/api/v3/movie``,
``/api/v3/qualityprofile``, ``/api/v3/customformat``, ``/api/v3/qualitydefinition``, ``/api/v3/rootfolder``,
``/api/v3/queue``, ``/api/v3/indexer``,
``/api/v3/downloadclient``, ``/api/v3/config/naming`` and the local cover images. Built on the clients of
``http_log``, so every call gets its log line.

The naming and the media management (measured on Radarr 6.3.0, 14.09.2026): ``renameMovies``,
``replaceIllegalCharacters``, ``colonReplacementFormat`` (a word such as ``smart``), ``standardMovieFormat`` and
``movieFolderFormat``; ``recycleBin``, ``recycleBinCleanupDays``, ``importExtraFiles``, ``copyUsingHardlinks``,
``fileDate`` and ``setPermissionsLinux``. A movie file carries ``sceneName`` only when Radarr kept one (3 of 6 files)
and ``originalFilePath`` for most (5 of 6), a name or a path relative to the download; both are left out, not null.
A movie carries ``minimumAvailability`` in camel case (``released``).

Indexers and download clients (measured on Radarr 6.3.0, 13.09.2026): a field is
``{name, value, privacy, ...}``; ``apiKey`` and ``password`` come back as ``********``,
⚠️ ``userName`` in clear text. Only the fields in ``INDEXER_FIELDS`` and ``DOWNLOAD_CLIENT_FIELDS``
are ever read. A field that is not set has no ``value``.

What Radarr 6.3.0 taught (measured 13.09.2026, see the design notes):

* ⚠️ **The key travels only in the ``X-Api-Key`` header**, never in a URL. Radarr also accepts
  ``?apikey=``, and the query even wins over the header. A URL ends up in logs, proxies and
  browser histories; a header does not.
* **The base URL** loses trailing slashes: ``//api/...`` answers the web UI with 200. Only
  http and https. User info in the URL is refused.
* **A 200 proves nothing.** Everything outside ``/api/``, a wrong URL base included, answers
  200 with the HTML of the web UI. ``system/status`` counts only with ``application/json`` and
  ``appName == "Radarr"``. Sonarr answers the same shape with ``appName: "Sonarr"``. A 404 or a
  redirect (the login page of a proxy) is not Radarr either.
* **401 has an empty body** and means a wrong key, a missing key, or a Sonarr at that address.
* **Missing, not null.** Radarr leaves out every property that would be null. Every optional
  field is parsed as "may be missing", ``null`` counts the same, unknown fields are ignored.
* **The queue is paged.** A page past the end is not empty, it repeats page 1.
* **Covers** have a relative ``url`` at the source and a TMDB ``remoteUrl``. Only the local one
  is ever loaded.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Self

import httpx
from fastapi import HTTPException

from ..meldungen import meldung
from . import http_log
from .releases.languages import iso_of_radarr
from .schreibweisen import nfc_or_none

logger = logging.getLogger("nexcrate.radarr")

APP_NAME = "Radarr"
STATUS_PATH = "/api/v3/system/status"
MOVIE_PATH = "/api/v3/movie"
PROFILE_PATH = "/api/v3/qualityprofile"
CUSTOM_FORMAT_PATH = "/api/v3/customformat"
QUALITY_DEFINITION_PATH = "/api/v3/qualitydefinition"
ROOT_FOLDER_PATH = "/api/v3/rootfolder"
QUEUE_PATH = "/api/v3/queue"
INDEXER_PATH = "/api/v3/indexer"
DOWNLOAD_CLIENT_PATH = "/api/v3/downloadclient"
DELAY_PROFILE_PATH = "/api/v3/delayprofile"
TAG_PATH = "/api/v3/tag"
AUTO_TAGGING_PATH = "/api/v3/autotagging"
NAMING_PATH = "/api/v3/config/naming"
MEDIA_MANAGEMENT_PATH = "/api/v3/config/mediamanagement"
#: Radarr's ``ColonReplacementFormat`` by number, for a version that sends the number instead of the word.
COLON_REPLACEMENTS = {0: "delete", 1: "dash", 2: "spaceDash", 3: "spaceDashSpace", 4: "smart"}
#: Radarr's default when an indexer has no ``apiPath`` field value.
DEFAULT_API_PATH = "/api"
QUEUE_PAGE_SIZE = 250
#: Hard cap: 40 pages of 250 are 10,000 queue items.
QUEUE_MAX_PAGES = 40
#: The movie list of a large library takes a while on a small NAS.
TIMEOUT = httpx.Timeout(60.0, connect=10.0)
URL_MAX_LENGTH = 1024
IMAGE_MAX_BYTES = 10 * 1024 * 1024
#: Image types that are cached and served. SVG is not among them: it can carry script.
IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/avif": ".avif",
    "image/bmp": ".bmp",
}

# A relative cover address as Radarr gives it: /MediaCover/10/poster.jpg?lastWrite=639...
_RELATIVE_IMAGE = re.compile(r"^/(?!/)[A-Za-z0-9._~%/-]*(?:\?[A-Za-z0-9._~%=&-]*)?$")
_FRACTION = re.compile(r"\.(\d{6})\d+")


# --- Errors ------------------------------------------------------------------ #


class RadarrError(Exception):
    """An answer of Radarr that nexcrate cannot use, with the error the API sends for it."""

    def __init__(self, detail: dict[str, Any], status: int) -> None:
        super().__init__(detail["code"])
        self.detail = detail
        self.status = status

    @property
    def code(self) -> str:
        return str(self.detail["code"])

    def http(self) -> HTTPException:
        return HTTPException(status_code=self.status, detail=self.detail)


class SourceUrlInvalid(ValueError):
    """The address of a source is not an http or https URL without user info."""


def unreachable(url: str) -> RadarrError:
    return RadarrError(
        meldung("radarr_unreachable", f"Radarr cannot be reached at {url}. Are address and port right?", url=url),
        502,
    )


def timed_out() -> RadarrError:
    return RadarrError(meldung("radarr_timeout", "Radarr did not answer in time."), 504)


def key_rejected() -> RadarrError:
    return RadarrError(
        meldung(
            "radarr_key_rejected",
            "Radarr did not accept the API key. Is Sonarr or another app answering at this address? "
            "That looks exactly the same.",
        ),
        502,
    )


def not_radarr() -> RadarrError:
    return RadarrError(meldung("radarr_not_radarr", "Something answers at this address, but it is not Radarr."), 502)


def http_error(status: int) -> RadarrError:
    return RadarrError(
        meldung("radarr_http_error", f"Radarr reports an error (HTTP {status}).", status=status),
        502,
    )


# --- Addresses --------------------------------------------------------------- #


def normalize_base_url(raw: str | None) -> str:
    """``scheme://host[:port][/path]`` without trailing slashes. Raises ``SourceUrlInvalid``."""
    text = (raw or "").strip()
    if not text or len(text) > URL_MAX_LENGTH or any(character.isspace() or ord(character) < 32 for character in text):
        raise SourceUrlInvalid("empty, too long or with spaces")
    try:
        url = httpx.URL(text)
    except (httpx.InvalidURL, TypeError, ValueError) as exc:
        raise SourceUrlInvalid("not a URL") from exc
    if url.scheme not in ("http", "https") or not url.host:
        raise SourceUrlInvalid("not http or https with a host")
    if url.userinfo:
        raise SourceUrlInvalid("user info in the URL")
    if url.query or url.fragment:
        raise SourceUrlInvalid("query or fragment in the URL")
    return _origin(url) + url.raw_path.decode("ascii").rstrip("/")


def _origin(url: httpx.URL) -> str:
    host = f"[{url.host}]" if ":" in url.host else url.host
    port = f":{url.port}" if url.port else ""
    return f"{url.scheme}://{host}{port}"


def image_url(base_url: str, relative: str | None) -> str | None:
    """The absolute address of a relative cover address at the source, or None.

    With a URL base the relative address probably includes it already (not measured): if it
    starts with the path of the base URL, it is joined to the origin, otherwise to the base.
    Absolute addresses are refused: nexcrate never loads a cover from anywhere else.
    """
    if not relative or not _RELATIVE_IMAGE.match(relative) or "/.." in relative:
        return None
    base = httpx.URL(base_url)
    base_path = base.raw_path.decode("ascii").rstrip("/")
    if base_path and (relative == base_path or relative.startswith(base_path + "/")):
        return _origin(base) + relative
    return _origin(base) + base_path + relative


def last_write(relative: str | None) -> str | None:
    """The ``lastWrite`` value of a cover address, reduced to letters and digits."""
    if not relative or "?" not in relative:
        return None
    for pair in relative.split("?", 1)[1].split("&"):
        name, _separator, value = pair.partition("=")
        if name == "lastWrite":
            cleaned = re.sub(r"[^A-Za-z0-9]", "", value)[:40]
            return cleaned or None
    return None


# --- Tolerant parsing ---------------------------------------------------------- #


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(_FRACTION.sub(r".\1", value.replace("Z", "+00:00")))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


@dataclass(frozen=True)
class SystemStatus:
    app_name: str
    version: str


@dataclass(frozen=True)
class QualityProfile:
    id: int
    name: str | None
    #: Name of the quality or group the profile's cutoff points at.
    cutoff_name: str | None
    #: The qualities the cutoff stands for: one for a quality, every member for a group. A group's own name is
    #: whatever the owner typed ("Merged QPs" on a real instance) and says nothing to the interface.
    cutoff_qualities: tuple[str, ...] = ()


@dataclass(frozen=True)
class RootFolder:
    id: int | None
    path: str


@dataclass(frozen=True)
class MovieFile:
    id: int | None
    quality: str | None
    cutoff_not_met: bool
    size: int
    languages: list[str]
    release_group: str | None
    relative_path: str | None
    date_added: datetime | None
    #: ``sceneName``: the release name Radarr kept for the file; missing for many files.
    scene_name: str | None = None
    #: The last name of ``originalFilePath``, the file as it lay in the download.
    original_file_name: str | None = None
    #: ``mediaInfo`` as Radarr answers it: its own flat record, only the fields of ``MEDIA_FIELDS``.
    media_info: dict[str, Any] | None = None

    @property
    def release_name(self) -> str | None:
        """The scene name, otherwise the file name of the original path; None when Radarr kept neither."""
        return self.scene_name or self.original_file_name or None


#: The short media info fields kept of a file, as Radarr and Sonarr name them. Their record is flat, not the tracks
#: of the media tool; ``media.from_arr`` brings it into nexcrate's shape.
MEDIA_FIELDS = (
    "audioChannels",
    "audioCodec",
    "audioLanguages",
    "subtitles",
    "videoBitDepth",
    "videoCodec",
    "videoDynamicRangeType",
    "resolution",
    "runTime",
)


@dataclass(frozen=True)
class NamingConfig:
    """Radarr's naming: both patterns, whether it renames at all, its colon replacement and illegal characters."""

    movie_folder: str
    movie_file: str
    rename_movies: bool
    colon_replacement: str | None
    replace_illegal_characters: bool = True


@dataclass(frozen=True)
class MediaManagement:
    """The part of Radarr's media management a takeover mentions."""

    recycle_bin: str | None
    recycle_bin_cleanup_days: int | None
    import_extra_files: bool
    copy_using_hardlinks: bool
    file_date: str | None
    set_permissions_linux: bool


@dataclass(frozen=True)
class Movie:
    id: int
    tmdb_id: int
    imdb_id: str | None
    title: str
    original_title: str | None
    year: int | None
    runtime: int | None
    genres: list[str]
    overview: str | None
    monitored: bool
    has_file: bool
    movie_file_id: int | None
    quality_profile_id: int | None
    root_folder_path: str | None
    path: str | None
    added: datetime | None
    poster_url: str | None
    alternate_titles: list[str] = field(default_factory=list)
    movie_file: MovieFile | None = None
    #: ISO 639-1 of ``originalLanguage`` (``{id, name}``, measured on Radarr 6.3.0); None when missing or unknown.
    original_language: str | None = None
    #: ``minimumAvailability`` as Radarr writes it: ``tba``, ``announced``, ``inCinemas``, ``released``.
    minimum_availability: str | None = None
    #: Radarr's tag ids; their names come from ``/api/v3/tag``.
    tags: list[int] = field(default_factory=list)
    #: ``studio`` and ``keywords`` as Radarr keeps them, for auto tags; None when absent.
    studio: str | None = None
    keywords: list[str] | None = None


@dataclass(frozen=True)
class QueueItem:
    id: int | None
    movie_id: int | None
    status: str | None
    tracked_download_status: str | None
    tracked_download_state: str | None
    size: float | None
    sizeleft: float | None


def _texts(values: Any) -> list[str]:
    result: list[str] = []
    for value in _list(values):
        text = nfc_or_none(value)
        if text and text not in result:
            result.append(text)
    return result


def parse_movie_file(value: Any) -> MovieFile | None:
    if not isinstance(value, dict):
        return None
    quality = _dict(_dict(value.get("quality")).get("quality"))
    languages = [nfc_or_none(_dict(item).get("name")) for item in _list(value.get("languages"))]
    original = (nfc_or_none(value.get("originalFilePath")) or "").replace("\\", "/").rstrip("/")
    media = _dict(value.get("mediaInfo"))
    kept = {
        name: media[name]
        for name in MEDIA_FIELDS
        if name in media and isinstance(media[name], str | int | float) and not isinstance(media[name], bool)
    }
    return MovieFile(
        id=_int(value.get("id")),
        quality=nfc_or_none(quality.get("name")),
        cutoff_not_met=_bool(value.get("qualityCutoffNotMet")) is True,
        size=max(0, _int(value.get("size")) or 0),
        languages=[language for language in languages if language],
        release_group=nfc_or_none(value.get("releaseGroup")) or None,
        relative_path=nfc_or_none(value.get("relativePath")) or None,
        date_added=_time(value.get("dateAdded")),
        scene_name=(nfc_or_none(value.get("sceneName")) or "").strip() or None,
        original_file_name=original.rsplit("/", 1)[-1].strip() or None,
        media_info=kept or None,
    )


def parse_naming(value: Any) -> NamingConfig:
    """``/api/v3/config/naming``; a missing pattern is an empty text."""
    data = _dict(value)
    colon = data.get("colonReplacementFormat")
    colon_name = COLON_REPLACEMENTS.get(colon) if isinstance(colon, int) and not isinstance(colon, bool) else None
    return NamingConfig(
        movie_folder=nfc_or_none(data.get("movieFolderFormat")) or "",
        movie_file=nfc_or_none(data.get("standardMovieFormat")) or "",
        rename_movies=_bool(data.get("renameMovies")) is True,
        colon_replacement=colon_name or (nfc_or_none(colon) if isinstance(colon, str) else None) or None,
        # Radarr's default is true; a missing field means the default.
        replace_illegal_characters=_bool(data.get("replaceIllegalCharacters")) is not False,
    )


def parse_media_management(value: Any) -> MediaManagement:
    """``/api/v3/config/mediamanagement``; a missing field counts as Radarr's default."""
    data = _dict(value)
    days = _int(data.get("recycleBinCleanupDays"))
    return MediaManagement(
        recycle_bin=(nfc_or_none(data.get("recycleBin")) or "").strip() or None,
        recycle_bin_cleanup_days=days if days is not None and days >= 0 else None,
        import_extra_files=_bool(data.get("importExtraFiles")) is True,
        copy_using_hardlinks=_bool(data.get("copyUsingHardlinks")) is not False,
        file_date=(nfc_or_none(data.get("fileDate")) or "").strip() or None,
        set_permissions_linux=_bool(data.get("setPermissionsLinux")) is True,
    )


def parse_movie(value: Any) -> Movie | None:
    """A movie from ``/api/v3/movie``, or None without a Radarr id or a TMDB id."""
    data = _dict(value)
    movie_id = _int(data.get("id"))
    tmdb_id = _int(data.get("tmdbId"))
    if movie_id is None or tmdb_id is None or tmdb_id <= 0:
        return None
    movie_file = parse_movie_file(data.get("movieFile"))
    has_file = _bool(data.get("hasFile"))
    poster = next(
        (
            nfc_or_none(image.get("url"))
            for image in map(_dict, _list(data.get("images")))
            if image.get("coverType") == "poster" and isinstance(image.get("url"), str)
        ),
        None,
    )
    if poster is not None and (not _RELATIVE_IMAGE.match(poster) or "/.." in poster):
        # Only a relative address at the source is ever loaded; a TMDB address is not a poster here.
        poster = None
    alternates = _texts([_dict(item).get("title") for item in _list(data.get("alternateTitles"))])
    title = nfc_or_none(data.get("title")) or nfc_or_none(data.get("originalTitle")) or f"TMDB {tmdb_id}"
    return Movie(
        id=movie_id,
        tmdb_id=tmdb_id,
        imdb_id=nfc_or_none(data.get("imdbId")) or None,
        title=title,
        original_title=nfc_or_none(data.get("originalTitle")) or None,
        year=_int(data.get("year")) or None,
        runtime=_int(data.get("runtime")) or None,
        genres=_texts(data.get("genres")),
        overview=nfc_or_none(data.get("overview")) or None,
        studio=nfc_or_none(data.get("studio")) or None,
        keywords=_texts(data.get("keywords")) if isinstance(data.get("keywords"), list) else None,
        monitored=_bool(data.get("monitored")) is True,
        has_file=has_file if has_file is not None else movie_file is not None,
        movie_file_id=_int(data.get("movieFileId")) or None,
        quality_profile_id=_int(data.get("qualityProfileId")),
        root_folder_path=nfc_or_none(data.get("rootFolderPath")) or None,
        path=nfc_or_none(data.get("path")) or None,
        added=_time(data.get("added")),
        poster_url=poster,
        alternate_titles=alternates,
        movie_file=movie_file,
        original_language=iso_of_radarr(data.get("originalLanguage")),
        minimum_availability=(nfc_or_none(data.get("minimumAvailability")) or "").strip() or None,
        tags=_ints(data.get("tags")),
    )


def _cutoff_name(items: list[Any], cutoff: int) -> str | None:
    for item in map(_dict, items):
        quality = _dict(item.get("quality"))
        if quality and _int(quality.get("id")) == cutoff:
            return nfc_or_none(quality.get("name"))
        if not quality and _int(item.get("id")) == cutoff:
            return nfc_or_none(item.get("name"))
        found = _cutoff_name(_list(item.get("items")), cutoff)
        if found:
            return found
    return None


def _cutoff_qualities(items: list[Any], cutoff: int) -> tuple[str, ...]:
    for item in map(_dict, items):
        quality = _dict(item.get("quality"))
        if quality and _int(quality.get("id")) == cutoff:
            name = nfc_or_none(quality.get("name"))
            return (name,) if name else ()
        if not quality and _int(item.get("id")) == cutoff:
            names = [nfc_or_none(_dict(member).get("quality", {}).get("name")) for member in _list(item.get("items"))]
            return tuple(name for name in names if name)
        found = _cutoff_qualities(_list(item.get("items")), cutoff)
        if found:
            return found
    return ()


def parse_quality_profile(value: Any) -> QualityProfile | None:
    data = _dict(value)
    profile_id = _int(data.get("id"))
    if profile_id is None:
        return None
    cutoff = _int(data.get("cutoff"))
    return QualityProfile(
        id=profile_id,
        name=nfc_or_none(data.get("name")),
        cutoff_name=_cutoff_name(_list(data.get("items")), cutoff) if cutoff is not None else None,
        cutoff_qualities=_cutoff_qualities(_list(data.get("items")), cutoff) if cutoff is not None else (),
    )


def parse_root_folder(value: Any) -> RootFolder | None:
    data = _dict(value)
    path = nfc_or_none(data.get("path"))
    return RootFolder(id=_int(data.get("id")), path=path) if path else None


@dataclass(frozen=True)
class RadarrIndexer:
    id: int
    name: str
    implementation: str | None
    protocol: str | None
    enable_rss: bool
    enable_automatic_search: bool
    enable_interactive_search: bool
    priority: int | None
    tags: list[int]
    base_url: str | None
    api_path: str | None
    categories: list[int]
    #: Torznab's ``minimumSeeders``; None when Radarr sends no value.
    minimum_seeders: int | None = None
    #: Radarr's language numbers of ``multiLanguages``; -2 stands for the movie's original language.
    multi_languages: tuple[int, ...] = ()
    remove_year: bool = False
    #: Sonarr's ``animeCategories`` (A3); Radarr and Lidarr have none.
    anime_categories: list[int] = field(default_factory=list)

    @property
    def enabled(self) -> bool:
        return self.enable_rss or self.enable_automatic_search or self.enable_interactive_search

    @property
    def automatic_search(self) -> bool:
        """RSS or automatic search: Radarr may use the indexer without anybody asking."""
        return self.enable_rss or self.enable_automatic_search

    @property
    def endpoint(self) -> str | None:
        """``baseUrl`` plus ``apiPath``, as Radarr builds its requests; None without a base URL."""
        if not self.base_url:
            return None
        path = self.api_path if self.api_path is not None else DEFAULT_API_PATH
        return self.base_url.rstrip("/") + path.rstrip("/")


def parse_tags(items: list[Any]) -> dict[int, str]:
    """``/api/v3/tag`` (and Lidarr's ``/api/v1/tag``): ``[{id, label}]`` to id and name; the same in all three apps."""
    found: dict[int, str] = {}
    for item in items:
        data = _dict(item)
        tag_id = _int(data.get("id"))
        label = nfc_or_none(data.get("label"))
        if tag_id is not None and label:
            found[tag_id] = label
    return found


def _ints(values: Any) -> list[int]:
    result: list[int] = []
    for value in _list(values):
        number = _int(value)
        if number is not None and number not in result:
            result.append(number)
    return result


#: The only fields of an indexer nexcrate reads. The key comes back masked, user names in clear text: neither is read.
INDEXER_FIELDS = (
    "baseUrl",
    "apiPath",
    "categories",
    "animeCategories",
    "minimumSeeders",
    "multiLanguages",
    "removeYear",
)


def parse_indexer(value: Any) -> RadarrIndexer | None:
    """An indexer from ``/api/v3/indexer``, or None without an id. Reads no field but those in ``INDEXER_FIELDS``."""
    data = _dict(value)
    indexer_id = _int(data.get("id"))
    if indexer_id is None:
        return None
    fields: dict[str, Any] = {}
    for field_value in map(_dict, _list(data.get("fields"))):
        name = field_value.get("name")
        if name in INDEXER_FIELDS and "value" in field_value:
            fields[name] = field_value["value"]
    base_url = nfc_or_none(fields.get("baseUrl"))
    api_path = nfc_or_none(fields.get("apiPath"))
    return RadarrIndexer(
        id=indexer_id,
        name=(nfc_or_none(data.get("name")) or "").strip() or f"Radarr indexer {indexer_id}",
        implementation=nfc_or_none(data.get("implementation")),
        protocol=nfc_or_none(data.get("protocol")),
        enable_rss=_bool(data.get("enableRss")) is True,
        enable_automatic_search=_bool(data.get("enableAutomaticSearch")) is True,
        enable_interactive_search=_bool(data.get("enableInteractiveSearch")) is True,
        priority=_int(data.get("priority")),
        tags=_ints(data.get("tags")),
        base_url=base_url.strip() if base_url else None,
        api_path=api_path.strip() if api_path is not None else None,
        categories=_ints(fields.get("categories")),
        minimum_seeders=_int(fields.get("minimumSeeders")),
        multi_languages=tuple(_ints(fields.get("multiLanguages"))),
        remove_year=_bool(fields.get("removeYear")) is True,
        anime_categories=_ints(fields.get("animeCategories")),
    )


@dataclass(frozen=True)
class RadarrDownloadClient:
    id: int
    name: str
    implementation: str | None
    protocol: str | None
    enabled: bool
    priority: int | None
    host: str | None
    port: int | None
    use_ssl: bool
    url_base: str | None
    #: ⚠️ Radarr sends it in clear text; read for qBittorrent only.
    username: str | None
    #: Radarr's own category, shown as information and never taken over.
    category: str | None
    #: The app's tag ids; their names come from ``/tag``.
    tags: tuple[int, ...] = ()

    @property
    def url(self) -> str | None:
        """``http(s)://host:port/urlBase`` as Radarr builds it; None without a host."""
        host = (self.host or "").strip()
        if not host or any(character.isspace() or character in "/?#@" for character in host):
            return None
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = f":{self.port}" if self.port else ""
        base = (self.url_base or "").strip().strip("/")
        return f"{'https' if self.use_ssl else 'http'}://{host}{port}" + (f"/{base}" if base else "")


#: The only fields of a download client nexcrate reads. ``apiKey`` and ``password`` come masked and are never read.
DOWNLOAD_CLIENT_FIELDS = ("host", "port", "useSsl", "urlBase", "username", "movieCategory")


def parse_download_client(value: Any) -> RadarrDownloadClient | None:
    """A download client from ``/api/v3/downloadclient``, or None without an id."""
    data = _dict(value)
    client_id = _int(data.get("id"))
    if client_id is None:
        return None
    fields: dict[str, Any] = {}
    for field_value in map(_dict, _list(data.get("fields"))):
        name = field_value.get("name")
        if name in DOWNLOAD_CLIENT_FIELDS and "value" in field_value:
            fields[name] = field_value["value"]
    port = _int(fields.get("port"))
    if port is None and isinstance(fields.get("port"), str) and fields["port"].strip().isdigit():
        port = int(fields["port"].strip())
    return RadarrDownloadClient(
        id=client_id,
        name=(nfc_or_none(data.get("name")) or "").strip() or f"Radarr download client {client_id}",
        implementation=nfc_or_none(data.get("implementation")),
        protocol=nfc_or_none(data.get("protocol")),
        enabled=_bool(data.get("enable")) is True,
        priority=_int(data.get("priority")),
        host=nfc_or_none(fields.get("host")),
        port=port if port is not None and 0 < port < 65536 else None,
        use_ssl=_bool(fields.get("useSsl")) is True,
        url_base=nfc_or_none(fields.get("urlBase")),
        username=(nfc_or_none(fields.get("username")) or "").strip() or None,
        category=(nfc_or_none(fields.get("movieCategory")) or "").strip() or None,
        tags=tuple(_ints(data.get("tags"))),
    )


def parse_queue_item(value: Any) -> QueueItem | None:
    if not isinstance(value, dict):
        return None
    return QueueItem(
        id=_int(value.get("id")),
        movie_id=_int(value.get("movieId")),
        status=nfc_or_none(value.get("status")),
        tracked_download_status=nfc_or_none(value.get("trackedDownloadStatus")),
        tracked_download_state=nfc_or_none(value.get("trackedDownloadState")),
        size=_number(value.get("size")),
        sizeleft=_number(value.get("sizeleft")),
    )


# --- The client ------------------------------------------------------------------ #


@dataclass(frozen=True)
class Image:
    content: bytes
    content_type: str


class ImageUnavailable(Exception):
    """The source has no usable image at that address. The message is safe for a log line."""


class RadarrClient:
    """Use as ``async with RadarrClient(url, key) as radarr: ...``."""

    def __init__(self, base_url: str, api_key: str, *, timeout: httpx.Timeout | float = TIMEOUT) -> None:
        self.base_url = normalize_base_url(base_url)
        self._api_key = api_key
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> Self:
        # ⚠️ The key goes into a header of the client, never into params or a URL.
        self._http = http_log.client(
            "radarr",
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
            raise RuntimeError("RadarrClient is used outside of 'async with'")
        return self._http

    def _check_key(self) -> None:
        # A header cannot carry anything but ASCII; httpx would raise deep inside.
        if not self._api_key.isascii() or not self._api_key.isprintable():
            raise key_rejected()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        self._check_key()
        try:
            return await http_log.send(self.http, "GET", path, params=params)
        except httpx.TimeoutException as exc:
            raise timed_out() from exc
        except httpx.RequestError as exc:
            # The normalized base URL cannot carry user info: normalize_base_url refuses it.
            raise unreachable(self.base_url) from exc

    async def _json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = await self._get(path, params)
        status = response.status_code
        if status == 401:
            raise key_rejected()
        if 300 <= status < 400 or status == 404:
            raise not_radarr()
        if not 200 <= status < 300:
            raise http_error(status)
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise not_radarr()
        try:
            return response.json()
        except ValueError as exc:
            raise not_radarr() from exc

    async def system_status(self) -> SystemStatus:
        data = await self._json(STATUS_PATH)
        if not isinstance(data, dict) or data.get("appName") != APP_NAME:
            raise not_radarr()
        return SystemStatus(app_name=APP_NAME, version=nfc_or_none(data.get("version")) or "")

    async def _list(self, path: str) -> list[Any]:
        data = await self._json(path)
        if not isinstance(data, list):
            raise not_radarr()
        return data

    async def movie_count(self) -> int:
        return sum(1 for item in await self._list(MOVIE_PATH) if isinstance(item, dict))

    async def movies(self) -> tuple[list[Movie], int]:
        """The movies, and how many entries were skipped for lack of an id."""
        raw = await self._list(MOVIE_PATH)
        parsed = [parse_movie(item) for item in raw]
        movies = [movie for movie in parsed if movie is not None]
        return movies, len(raw) - len(movies)

    async def indexers(self) -> list[RadarrIndexer]:
        return [indexer for indexer in map(parse_indexer, await self._list(INDEXER_PATH)) if indexer]

    async def download_clients(self) -> list[RadarrDownloadClient]:
        return [client for client in map(parse_download_client, await self._list(DOWNLOAD_CLIENT_PATH)) if client]

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

    async def tags(self) -> dict[int, str]:
        """Tag id to name, as Radarr keeps them."""
        return parse_tags(await self._list(TAG_PATH))

    async def root_folders(self) -> list[RootFolder]:
        return [folder for folder in map(parse_root_folder, await self._list(ROOT_FOLDER_PATH)) if folder]

    async def naming(self) -> NamingConfig:
        data = await self._json(NAMING_PATH)
        if not isinstance(data, dict):
            raise not_radarr()
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
            raise not_radarr()
        return parse_media_management(data)

    async def queue(self) -> list[QueueItem]:
        """Every queue item, unknown movies included.

        ⚠️ A page past the end repeats page 1 instead of being empty. The loop stops when
        ``page * pageSize >= totalRecords``, and never goes beyond ``QUEUE_MAX_PAGES``.
        """
        items: list[QueueItem] = []
        seen: set[int] = set()
        page = 1
        while True:
            params = {"includeUnknownMovieItems": "true", "pageSize": QUEUE_PAGE_SIZE, "page": page}
            data = await self._json(QUEUE_PATH, params)
            if not isinstance(data, dict):
                raise not_radarr()
            answered_page = _int(data.get("page"))
            if page > 1 and answered_page is not None and answered_page != page:
                # The page past the end: Radarr repeats page 1.
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
                logger.warning("The queue has more than %d pages; the rest is left out", QUEUE_MAX_PAGES)
                break
            page += 1
        return items

    async def image(self, relative: str | None) -> Image:
        """A cover from the source itself, at most ``IMAGE_MAX_BYTES``, only an image type."""
        target = image_url(self.base_url, relative)
        if target is None:
            raise ImageUnavailable("the cover address is not a relative address at the source")
        self._check_key()
        try:
            async with self.http.stream("GET", target) as response:
                if response.status_code != 200:
                    raise ImageUnavailable(f"the source answered HTTP {response.status_code}")
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type not in IMAGE_TYPES:
                    raise ImageUnavailable("the answer is not a supported image type")
                declared = _int_header(response.headers.get("content-length"))
                if declared is not None and declared > IMAGE_MAX_BYTES:
                    raise ImageUnavailable("the image is larger than the limit")
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > IMAGE_MAX_BYTES:
                        raise ImageUnavailable("the image is larger than the limit")
                    chunks.append(chunk)
        except httpx.TransportError as exc:
            http_log.unreachable("radarr", "GET", target, exc)
            raise ImageUnavailable(f"the source could not be reached: {type(exc).__name__}") from exc
        except httpx.RequestError as exc:
            raise ImageUnavailable(f"the request failed: {type(exc).__name__}") from exc
        if not chunks:
            raise ImageUnavailable("the image is empty")
        return Image(content=b"".join(chunks), content_type=content_type)


def _int_header(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None
