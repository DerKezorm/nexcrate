"""Newznab and Torznab indexers: caps, the connection test, a test search. XML only, read with defusedxml.

What the specs, Radarr's source and the test bench taught (13.09.2026, see the design notes):

* ⚠️ **The key travels as the query parameter ``apikey``**, the one exception to the header rule:
  the Newznab and Torznab specs and Jackett accept nothing else. ``http_log`` masks it in every log
  line, and this client never lets a response body into the log: download links in a feed carry
  keys and passkeys in shapes no pattern recognises. Answers of the API never carry download links.
* **The error document** ``<error code description/>`` comes with HTTP 200 by the spec; Prowlarr and
  Jackett send it with 400, 401, 410 or 429. It is read at any status.
* **XML only**, parsed without DTDs and entities. Answers over 10 MB are refused.
* **Pacing** as in Radarr: at most one request per indexer every 2 seconds. After a request limit
  the indexer pauses until Retry-After, or one hour.
* **Caps** may be missing or broken; the defaults apply then (``search`` with q, ``movie`` with q and
  imdbid, page size 100). Default categories: the caps categories from 2000 to 2999, else Radarr's.
* **The test** asks ``t=caps``, then one ``t=movie`` (``t=search`` when caps offer no movie search)
  with ``extended=1&limit=1`` in the chosen categories. Many indexers answer caps without a key; the
  feed request needs it, and it shows whether the categories deliver anything.
* A Torznab feed saved as Newznab, or the reverse, is refused (``indexer_wrong_kind``).
* **Per release** (step 2c) a feed keeps a key of the guid (a hash: a guid is often a link), the ``tmdbid`` and
  ``imdb`` attributes as numbers, and the indexer flags as Radarr's bits. Torznab: download factor 0 freeleech, 0.25
  freeleech 75, 0.5 halfleech, 0.75 freeleech 25; upload factor 2 double upload; the tags internal and scene.
  Newznab: ``prematch`` or ``haspretime`` scene, ``nuked``. Each kind reads only its own.
* ⚠️ **The download link** (step 3): the enclosure, else ``link``, else Torznab's ``magneturl``, kept on the release
  in memory only, for loading it. It carries the key or a passkey, so it has no repr, never reaches an answer and
  never a log line. Torznab's ``infohash`` is kept for the blocklist.

Log lines carry no key, no URL query and no titles.
"""

from __future__ import annotations

import asyncio
import email.utils
import hashlib
import io
import logging
import math
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Self
from xml.etree.ElementTree import Element, ParseError

import defusedxml
import defusedxml.ElementTree as SafeXml
import httpx
from fastapi import HTTPException

from ..meldungen import meldung
from ..models import utcnow
from . import http_log
from .radarr import SourceUrlInvalid, normalize_base_url
from .schreibweisen import nfc

logger = logging.getLogger("nexcrate.indexers")

KINDS = ("newznab", "torznab")
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
MAX_BYTES = 10 * 1024 * 1024
PACE_SECONDS = 2.0
DEFAULT_PAUSE_SECONDS = 3600
MAX_PAUSE_SECONDS = 24 * 3600
CAPS_MAX_AGE = timedelta(days=7)
SEARCH_LIMIT = 50
DEFAULT_CATEGORIES = (2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060)
#: Sonarr's default series categories plus UHD, which Sonarr leaves out (decision 5).
DEFAULT_SERIES_CATEGORIES = (5030, 5040, 5045)
#: Newznab's anime category. It stays out of the series default; an anime series asks it as well
#: (``default_anime_categories``, the design notes, A3).
ANIME_CATEGORY = 5070
DEFAULT_CAPS: dict[str, Any] = {
    "movie_search": True,
    "movie_params": ["q", "imdbid"],
    # Sonarr's defaults when caps are missing or broken: practically a text search (decision 3).
    "tv_search": True,
    "tv_params": ["q", "rid", "season", "ep"],
    "search_params": ["q"],
    # Lidarr's default: without caps it searches music as plain text (decision 5).
    "music_search": False,
    "music_params": [],
    "search_engine": "sphinx",
    "limit_max": 100,
    "limit_default": 100,
    "categories": [],
}
NEWZNAB_NS = "http://www.newznab.com/dtd/2010/feeds/attributes"
TORZNAB_NS = "http://torznab.com/schemas/2015/feed"
LIMIT_TEXT = "request limit reached"
#: Caps failures that end a test: an indexer that cannot be asked cannot be saved.
_FATAL_FOR_CAPS = frozenset({"indexer_key_rejected", "indexer_limit_reached", "indexer_unreachable", "indexer_timeout"})
_MAX_CATEGORIES = 1000
_TRAILING_ZONE = re.compile(r"\s+[A-Za-z]{2,5}$")


# --- Time, replaceable in tests ------------------------------------------------------ #


def clock() -> float:
    return time.monotonic()


async def sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def now() -> datetime:
    return utcnow()


# --- Errors ------------------------------------------------------------------------------ #


class IndexerError(Exception):
    """An answer nexcrate cannot use, with the error the API sends for it."""

    def __init__(self, detail: dict[str, Any], status: int, *, paused_until: datetime | None = None) -> None:
        super().__init__(detail["code"])
        self.detail = detail
        self.status = status
        #: Set for a request limit: until when the indexer pauses.
        self.paused_until = paused_until

    @property
    def code(self) -> str:
        return str(self.detail["code"])

    def http(self) -> HTTPException:
        headers = {"Retry-After": str(self.detail["retry_after"])} if "retry_after" in self.detail else None
        return HTTPException(status_code=self.status, detail=self.detail, headers=headers)


class IndexerUrlInvalid(ValueError):
    """Not an http or https URL, or with user info, a query or a fragment."""


def key_rejected() -> IndexerError:
    return IndexerError(meldung("indexer_key_rejected", "The indexer did not accept the API key."), 502)


def limit_reached(retry_after: int, paused_until: datetime | None = None) -> IndexerError:
    return IndexerError(
        meldung(
            "indexer_limit_reached",
            f"The request limit of the indexer is reached. nexcrate asks again in {retry_after} seconds.",
            retry_after=retry_after,
        ),
        502,
        paused_until=paused_until,
    )


def indexer_error(indexer_code: int | str) -> IndexerError:
    return IndexerError(
        meldung("indexer_error", f"The indexer reports error {indexer_code}.", indexer_code=indexer_code), 502
    )


def not_newznab() -> IndexerError:
    return IndexerError(
        meldung("indexer_not_newznab", "Something answers at this address, but not as a Newznab or Torznab indexer."),
        502,
    )


def wrong_kind() -> IndexerError:
    return IndexerError(
        meldung(
            "indexer_wrong_kind",
            "The answer does not fit the chosen kind: Newznab is for Usenet, Torznab for torrents.",
        ),
        502,
    )


def unreachable(url: str) -> IndexerError:
    return IndexerError(
        meldung("indexer_unreachable", f"The indexer cannot be reached at {url}. Are address and port right?", url=url),
        502,
    )


def timed_out() -> IndexerError:
    return IndexerError(meldung("indexer_timeout", "The indexer did not answer in time."), 504)


def http_error(status: int) -> IndexerError:
    return IndexerError(
        meldung("indexer_http_error", f"The indexer reports an error (HTTP {status}).", status=status), 502
    )


def too_large() -> IndexerError:
    return IndexerError(
        meldung("indexer_answer_too_large", "The answer of the indexer is larger than 10 MB and was refused."), 502
    )


#: For the OpenAPI ``responses`` of every route that asks an indexer.
ERRORS = (
    (502, "indexer_key_rejected"),
    (502, "indexer_limit_reached"),
    (502, "indexer_error"),
    (502, "indexer_not_newznab"),
    (502, "indexer_wrong_kind"),
    (502, "indexer_unreachable"),
    (502, "indexer_http_error"),
    (502, "indexer_answer_too_large"),
    (504, "indexer_timeout"),
)


# --- Addresses and kinds ----------------------------------------------------------------- #


def normalize_url(raw: str | None, *, add_api_path: bool = True) -> str:
    """http or https, no user info, no query, trailing slashes removed, the path as given.

    An address the owner typed without any path gets ``/api``. Radarr keeps URL and API path apart
    (default ``/api``); an owner who copied the URL from there reached the indexer's web site and
    got ``indexer_not_newznab`` (found in the owner's test on 13.09.2026). An endpoint read from
    Radarr already carries its path as Radarr builds it; there an explicitly empty ``apiPath``
    means the root, so ``add_api_path=False`` keeps it.
    """
    try:
        url = normalize_base_url(raw)
    except SourceUrlInvalid as exc:
        raise IndexerUrlInvalid(str(exc)) from exc
    if add_api_path and httpx.URL(url).path in ("", "/"):
        url = f"{url}/api"
    return url


def kind_of_implementation(implementation: str | None) -> str | None:
    name = (implementation or "").strip().lower()
    return name if name in KINDS else None


# --- Pacing and pauses ----------------------------------------------------------------- #

_state_lock = threading.Lock()
#: Endpoint -> monotonic time of the next allowed request.
_next_request: dict[str, float] = {}
#: Endpoint and key -> end of a pause after a request limit.
_paused: dict[str, datetime] = {}


def reset_state() -> None:
    with _state_lock:
        _next_request.clear()
        _paused.clear()


def _pause_key(url: str, api_key: str) -> str:
    # A limit belongs to an account, and the key names the account. The hash keeps the key out of memory dumps.
    return f"{url}#{hashlib.sha256(api_key.encode('utf-8')).hexdigest()[:16]}"


async def _pace(url: str) -> None:
    while True:
        with _state_lock:
            moment = clock()
            ready = _next_request.get(url, 0.0)
            if moment >= ready:
                _next_request[url] = moment + PACE_SECONDS
                return
            wait = ready - moment
        await sleep(wait)


async def pace(key: str) -> None:
    """At most one request for ``key`` every ``PACE_SECONDS``. Loading a release paces its fetches per indexer."""
    await _pace(key)


def retry_after_seconds(value: str | None) -> int:
    """Seconds from a Retry-After header (seconds or an HTTP date), else one hour."""
    text = (value or "").strip()
    seconds: int | None = None
    if text.isdigit():
        seconds = int(text)
    elif text:
        try:
            moment = email.utils.parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            moment = None
        if moment is not None:
            moment = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment
            seconds = math.ceil((moment - now()).total_seconds())
    if seconds is None or seconds <= 0:
        return DEFAULT_PAUSE_SECONDS
    return min(seconds, MAX_PAUSE_SECONDS)


@dataclass(frozen=True)
class Target:
    url: str
    kind: str
    api_key: str = ""
    #: A stored pause of a saved indexer.
    paused_until: datetime | None = None


def paused_seconds(target: Target) -> int:
    """How long the indexer still pauses, 0 when it may be asked."""
    with _state_lock:
        remembered = _paused.get(_pause_key(target.url, target.api_key))
    moments = [moment for moment in (remembered, target.paused_until) if moment is not None]
    if not moments:
        return 0
    remaining = max((moment - now()).total_seconds() for moment in moments)
    return math.ceil(remaining) if remaining > 0 else 0


# --- XML ------------------------------------------------------------------------------ #


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _namespace(tag: object) -> str:
    if isinstance(tag, str) and tag.startswith("{"):
        return _normalized_namespace(tag[1:].split("}", 1)[0])
    return ""


def _normalized_namespace(uri: str) -> str:
    return uri.strip().rstrip("/").lower()


def _children(element: Element, name: str) -> list[Element]:
    return [child for child in element if _local(child.tag) == name]


def _child(element: Element, name: str) -> Element | None:
    return next((child for child in element if _local(child.tag) == name), None)


def _child_text(element: Element, name: str) -> str:
    child = _child(element, name)
    return nfc(child.text or "").strip() if child is not None else ""


def _int_text(value: str | None) -> int | None:
    text = (value or "").strip()
    if not re.fullmatch(r"-?\d{1,19}", text):
        return None
    return int(text)


def parse_xml(body: bytes, content_type: str = "") -> tuple[Element | None, set[str]]:
    """The root and the declared namespaces, or None for anything that is not safe, well-formed XML.

    ⚠️ No DTD, no entities, no external references: defusedxml refuses them.
    """
    if "html" in content_type:
        return None, set()
    if not body.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<"):
        return None, set()
    namespaces: set[str] = set()
    try:
        iterator = SafeXml.iterparse(
            io.BytesIO(body), events=("start-ns",), forbid_dtd=True, forbid_entities=True, forbid_external=True
        )
        for _event, value in iterator:
            namespaces.add(_normalized_namespace(value[1]))
        root = iterator.root
    except (ParseError, defusedxml.DefusedXmlException, LookupError, ValueError) as exc:
        logger.info("Indexer answer is not usable XML: %s", type(exc).__name__)
        return None, set()
    if root is None or _local(root.tag).lower() == "html":
        return None, set()
    return root, namespaces


def _params(element: Element | None, default: list[str]) -> list[str]:
    if element is None or element.get("supportedParams") is None:
        return list(default)
    return [part.strip().lower() for part in (element.get("supportedParams") or "").split(",") if part.strip()]


def parse_caps(root: Element) -> dict[str, Any] | None:
    """The caps as the API shows them, or None when the document is no caps document."""
    if _local(root.tag) != "caps":
        return None
    searching = _child(root, "searching")
    search = _child(searching, "search") if searching is not None else None
    movie = _child(searching, "movie-search") if searching is not None else None
    tv = _child(searching, "tv-search") if searching is not None else None
    # Torznab names it "music-search" or "audio-search" (Jackett writes both).
    music = _child(searching, "music-search") if searching is not None else None
    if music is None and searching is not None:
        music = _child(searching, "audio-search")
    limits = _child(root, "limits")
    categories: list[dict[str, Any]] = []
    holder = _child(root, "categories")
    for category in _children(holder, "category") if holder is not None else []:
        category_id = _int_text(category.get("id"))
        if category_id is None or len(categories) >= _MAX_CATEGORIES:
            continue
        subcats = [
            {"id": sub_id, "name": nfc(sub.get("name") or "").strip()}
            for sub in _children(category, "subcat")[:_MAX_CATEGORIES]
            if (sub_id := _int_text(sub.get("id"))) is not None
        ]
        categories.append({"id": category_id, "name": nfc(category.get("name") or "").strip(), "subcats": subcats})
    limit_max = _int_text(limits.get("max")) if limits is not None else None
    limit_default = _int_text(limits.get("default")) if limits is not None else None
    return {
        "movie_search": movie is not None and (movie.get("available") or "").strip().lower() == "yes",
        "movie_params": _params(movie, []) if movie is not None else [],
        "tv_search": tv is not None and (tv.get("available") or "").strip().lower() == "yes",
        "tv_params": _params(tv, []) if tv is not None else [],
        "search_params": _params(search, ["q"]),
        "music_search": music is not None and (music.get("available") or "").strip().lower() == "yes",
        "music_params": _params(music, []) if music is not None else [],
        "search_engine": ((search.get("searchEngine") if search is not None else None) or "sphinx").strip().lower(),
        "limit_max": limit_max if limit_max is not None and limit_max > 0 else None,
        "limit_default": limit_default if limit_default is not None and limit_default > 0 else None,
        "categories": categories,
    }


def default_categories(caps: dict[str, Any] | None) -> list[int]:
    """The caps categories from 2000 to 2999, else Radarr's movie categories."""
    found: list[int] = []
    for category in (caps or {}).get("categories") or []:
        for category_id in [category["id"], *(sub["id"] for sub in category.get("subcats") or [])]:
            if 2000 <= category_id <= 2999 and category_id not in found:
                found.append(category_id)
    return sorted(found) if found else list(DEFAULT_CATEGORIES)


def default_series_categories(caps: dict[str, Any] | None) -> list[int]:
    """The caps categories from 5000 to 5999 without anime and its subcategories, else ``DEFAULT_SERIES_CATEGORIES``.

    ⚠️ Migration 10 keeps a copy of this rule (``migrations.series_categories_from_caps``); a test holds both equal.
    """
    found: set[int] = set()
    for category in (caps or {}).get("categories") or []:
        if category["id"] == ANIME_CATEGORY:
            continue
        for category_id in [category["id"], *(sub["id"] for sub in category.get("subcats") or [])]:
            if 5000 <= category_id <= 5999 and category_id != ANIME_CATEGORY:
                found.add(category_id)
    return sorted(found) if found else list(DEFAULT_SERIES_CATEGORIES)


def default_anime_categories(caps: dict[str, Any] | None) -> list[int]:
    """5070 and its subcategories as the caps list them; 5070 alone without caps; none when the caps list categories
    but not 5070 (the owner's answer to fork 2 of the design notes: by itself, changeable per indexer).

    Sonarr leaves its ``animeCategories`` empty and asks no anime category until the user or Prowlarr fills them
    (measured at the bench, 22.09.2026).
    """
    listed = (caps or {}).get("categories") or []
    if not listed:
        return [ANIME_CATEGORY]
    for category in listed:
        if category["id"] == ANIME_CATEGORY:
            subcats = sorted({sub["id"] for sub in category.get("subcats") or []} - {ANIME_CATEGORY})
            return [ANIME_CATEGORY, *subcats]
    return []


def caps_know_series(caps: dict[str, Any] | None) -> bool:
    """Whether stored caps were read by a version that knows ``tv-search``; older ones are fetched again."""
    return caps is None or "tv_search" in caps


def caps_know_music(caps: dict[str, Any] | None) -> bool:
    """Whether stored caps were read by a version that knows ``music-search``; older ones are fetched again once."""
    return caps is None or "music_search" in caps


#: Radarr's indexer flag bits, which TRaSH's IndexerFlagSpecification compares. Only the ones a feed can set.
FLAG_FREELEECH = 1
FLAG_HALFLEECH = 2
FLAG_DOUBLE_UPLOAD = 4
FLAG_INTERNAL = 32
FLAG_SCENE = 128
FLAG_FREELEECH_75 = 256
FLAG_FREELEECH_25 = 512
FLAG_NUKED = 2048
#: The flags by the names the API shows, lowest bit first.
FLAGS: dict[str, int] = {
    "freeleech": FLAG_FREELEECH,
    "halfleech": FLAG_HALFLEECH,
    "double_upload": FLAG_DOUBLE_UPLOAD,
    "internal": FLAG_INTERNAL,
    "scene": FLAG_SCENE,
    "freeleech_75": FLAG_FREELEECH_75,
    "freeleech_25": FLAG_FREELEECH_25,
    "nuked": FLAG_NUKED,
}
#: Torznab's download volume factor to the flag it means.
_DOWNLOAD_FACTORS = {0.0: FLAG_FREELEECH, 0.25: FLAG_FREELEECH_75, 0.5: FLAG_HALFLEECH, 0.75: FLAG_FREELEECH_25}
_IMDB_NUMBER = re.compile(r"(?:tt)?(\d{1,12})", re.IGNORECASE)


#: ⚠️ Sonarr numbers the same flags differently (measured on Sonarr 4.0.19, ``customformat/schema``): its 32 is
#: Freeleech 75 and its 128 is Nuked, which are Internal and Scene above. A format built for series compares these.
SONARR_FLAGS: dict[str, int] = {
    "freeleech": 1,
    "halfleech": 2,
    "double_upload": 4,
    "internal": 8,
    "scene": 16,
    "freeleech_75": 32,
    "freeleech_25": 64,
    "nuked": 128,
}


def flag_names(bits: int) -> list[str]:
    return [name for name, bit in FLAGS.items() if bits & bit]


def as_sonarr(bits: int) -> int:
    """The flags of a release in Sonarr's numbers, for the formats of a series. A release itself keeps Radarr's."""
    flags = 0
    for name in flag_names(bits):
        flags |= SONARR_FLAGS[name]
    return flags


@dataclass(frozen=True)
class Release:
    title: str
    size_bytes: int | None
    published_at: datetime | None
    categories: list[int]
    seeders: int | None
    peers: int | None
    grabs: int | None
    #: A key of the item's guid: a hash, because a guid is often a link and no link is kept. None without a guid.
    guid: str | None = None
    tmdb_id: int | None = None
    #: The IMDb number as an integer, without ``tt`` and leading zeros.
    imdb_id: int | None = None
    #: Series numbers a feed may carry (``tvdbid``, ``tvmazeid``, ``rageid``), for matching a release to a series.
    tvdb_id: int | None = None
    tvmaze_id: int | None = None
    rage_id: int | None = None
    #: Radarr's flag bits, see ``FLAGS``.
    indexer_flags: int = 0
    #: ⚠️ The download link as the feed gives it. It carries the key or a passkey: in memory only, never in a repr,
    #: an answer or a log line.
    link: str | None = field(default=None, repr=False, compare=False)
    #: Torznab's ``infohash`` attribute in lower case; None without one.
    info_hash: str | None = None


@dataclass(frozen=True)
class ApiLimits:
    """``newznab:apilimits`` of a feed (Torznab spec 1.3, revision 1.4): requests and grabs used and allowed in 24
    hours, and the UTC time each counter goes down. Every value may be missing."""

    api_current: int | None = None
    api_max: int | None = None
    grab_current: int | None = None
    grab_max: int | None = None
    api_next_at: datetime | None = None
    grab_next_at: datetime | None = None


@dataclass(frozen=True)
class Feed:
    #: The kind the feed looks like, None when it cannot be told.
    kind: str | None
    #: ``<item>`` elements, with or without a usable title.
    items: int
    total: int
    releases: list[Release]
    #: ``newznab:apilimits`` of the channel; None when the feed has none (step 3c).
    limits: ApiLimits | None = None


def parse_date(value: str | None) -> datetime | None:
    """An RFC 2822 date (``pubDate``, ``usenetdate``) as UTC. A trailing zone name is dropped first."""
    text = (value or "").strip()
    if not text:
        return None
    for candidate in (text, _TRAILING_ZONE.sub("", text)):
        try:
            parsed = email.utils.parsedate_to_datetime(candidate)
        except (TypeError, ValueError, IndexError):
            continue
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _attributes(item: Element) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for child in item:
        if _local(child.tag) == "attr" and child.get("name"):
            found.setdefault((child.get("name") or "").strip().lower(), []).append((child.get("value") or "").strip())
    return found


def _first_int(values: list[str] | None) -> int | None:
    for value in values or []:
        number = _int_text(value)
        if number is not None:
            return number
    return None


def _release(item: Element, kind: str) -> Release | None:
    title = _child_text(item, "title")
    if not title:
        return None
    attributes = _attributes(item)
    size = _first_int(attributes.get("size"))
    if size is None:
        enclosure = _child(item, "enclosure")
        length = _int_text(enclosure.get("length")) if enclosure is not None else None
        size = length if length is not None and length > 0 else _int_text(_child_text(item, "size"))
    categories: list[int] = []
    for value in attributes.get("category", []):
        number = _int_text(value)
        if number is not None and number not in categories:
            categories.append(number)
    published = None
    if kind == "newznab":
        published = parse_date((attributes.get("usenetdate") or [""])[0])
    published = published or parse_date(_child_text(item, "pubDate"))
    seeders = _first_int(attributes.get("seeders"))
    leechers = _first_int(attributes.get("leechers"))
    peers = _first_int(attributes.get("peers"))
    if peers is None and seeders is not None and leechers is not None:
        peers = seeders + leechers
    guid = _child_text(item, "guid")
    return Release(
        title=title,
        size_bytes=size if size is not None and size >= 0 else None,
        published_at=published,
        categories=categories,
        seeders=seeders,
        peers=peers,
        grabs=_first_int(attributes.get("grabs")),
        guid=hashlib.sha256(guid.encode("utf-8")).hexdigest()[:24] if guid else None,
        tmdb_id=_positive(_first_int(attributes.get("tmdbid"))),
        imdb_id=_imdb_number(attributes.get("imdb")),
        tvdb_id=_positive(_first_int(attributes.get("tvdbid"))),
        tvmaze_id=_positive(_first_int(attributes.get("tvmazeid"))),
        rage_id=_positive(_first_int(attributes.get("rageid"))),
        indexer_flags=_flags(attributes, kind),
        link=_download_link(item, attributes, kind),
        info_hash=_info_hash(attributes) if kind == "torznab" else None,
    )


_LINK_SCHEMES = ("http://", "https://", "magnet:")
_INFO_HASH = re.compile(r"[0-9a-fA-F]{40}")


def _download_link(item: Element, attributes: dict[str, list[str]], kind: str) -> str | None:
    """The enclosure's address, else ``link``, else Torznab's ``magneturl``; only http, https and magnet links."""
    enclosure = _child(item, "enclosure")
    candidates = [enclosure.get("url") if enclosure is not None else None, _child_text(item, "link")]
    if kind == "torznab":
        candidates.extend(attributes.get("magneturl") or [])
    for candidate in candidates:
        text = (candidate or "").strip()
        if text.lower().startswith(_LINK_SCHEMES) and not any(character.isspace() for character in text):
            return text
    return None


def _info_hash(attributes: dict[str, list[str]]) -> str | None:
    for value in attributes.get("infohash") or []:
        if _INFO_HASH.fullmatch(value.strip()):
            return value.strip().lower()
    return None


def _positive(value: int | None) -> int | None:
    return value if value is not None and value > 0 else None


def _imdb_number(values: list[str] | None) -> int | None:
    """The number of an ``imdb`` attribute: digits, a leading ``tt`` tolerated. None for 0 and anything else."""
    for value in values or []:
        found = _IMDB_NUMBER.fullmatch(value.strip())
        if found is not None:
            return _positive(int(found.group(1)))
    return None


def _factor(values: list[str] | None) -> float | None:
    for value in values or []:
        try:
            number = float(value.strip())
        except ValueError:
            continue
        if math.isfinite(number):
            return number
    return None


def _flags(attributes: dict[str, list[str]], kind: str) -> int:
    """Radarr's flags from the attributes its parser reads for this kind; the other kind's attributes count nothing."""
    flags = 0
    if kind == "torznab":
        download = _factor(attributes.get("downloadvolumefactor"))
        if download is not None:
            flags |= _DOWNLOAD_FACTORS.get(download, 0)
        if _factor(attributes.get("uploadvolumefactor")) == 2.0:
            flags |= FLAG_DOUBLE_UPLOAD
        tags = {value.casefold() for value in attributes.get("tag", [])}
        if "internal" in tags:
            flags |= FLAG_INTERNAL
        if "scene" in tags:
            flags |= FLAG_SCENE
    elif kind == "newznab":
        if (_first_int(attributes.get("prematch")) or 0) > 0 or (_first_int(attributes.get("haspretime")) or 0) > 0:
            flags |= FLAG_SCENE
        if (_first_int(attributes.get("nuked")) or 0) > 0:
            flags |= FLAG_NUKED
    return flags


def detect_kind(root: Element, namespaces: set[str]) -> str | None:
    """Torznab or Newznab, from the enclosure types first, then from the namespaces in use."""
    types = [
        (enclosure.get("type") or "").lower()
        for enclosure in root.iter()
        if _local(enclosure.tag) == "enclosure"
    ]
    torrent = any("bittorrent" in value for value in types)
    nzb = any("x-nzb" in value for value in types)
    if torrent != nzb:
        return "torznab" if torrent else "newznab"
    used = namespaces | {_namespace(element.tag) for element in root.iter()}
    torznab, newznab = TORZNAB_NS in used, NEWZNAB_NS in used
    if torznab != newznab:
        return "torznab" if torznab else "newznab"
    return None


def parse_feed(root: Element, namespaces: set[str], kind: str) -> Feed:
    if _local(root.tag) != "rss":
        raise not_newznab()
    channel = _child(root, "channel")
    if channel is None:
        raise not_newznab()
    items = _children(channel, "item")
    releases = [release for release in (_release(item, kind) for item in items) if release is not None]
    total: int | None = None
    response = _child(channel, "response")
    if response is not None:
        total = _int_text(response.get("total"))
    return Feed(
        kind=detect_kind(root, namespaces),
        items=len(items),
        total=total if total is not None and total >= 0 else len(releases),
        releases=releases,
        limits=api_limits(channel) or api_limits(root),
    )


def api_limits(holder: Element) -> ApiLimits | None:
    """The ``apilimits`` element directly below ``holder``, attribute names in any case; None without a usable one.

    The spec writes ``apiCurrent``, ``apiMax``, ``grabCurrent``, ``grabMax``, ``apiNextAvailable`` and
    ``grabNextAvailable``; the Newznab docs write them in lower case. A negative or unreadable number counts as missing.
    """
    element = next((child for child in holder if _local(child.tag).lower() == "apilimits"), None)
    if element is None:
        return None
    values = {_local(name).lower(): (value or "").strip() for name, value in element.attrib.items()}

    def number(name: str) -> int | None:
        found = _int_text(values.get(name))
        return found if found is not None and found >= 0 else None

    limits = ApiLimits(
        api_current=number("apicurrent"),
        api_max=number("apimax"),
        grab_current=number("grabcurrent"),
        grab_max=number("grabmax"),
        api_next_at=parse_date(values.get("apinextavailable")),
        grab_next_at=parse_date(values.get("grabnextavailable")),
    )
    return None if limits == ApiLimits() else limits


# --- The client ------------------------------------------------------------------------ #


async def _read_limited(response: httpx.Response) -> bytes:
    declared = response.headers.get("content-length", "").strip()
    if declared.isdigit() and int(declared) > MAX_BYTES:
        raise too_large()
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > MAX_BYTES:
            raise too_large()
        chunks.append(chunk)
    return b"".join(chunks)


class IndexerClient:
    """Use as ``async with IndexerClient(target) as indexer: ...``."""

    def __init__(self, target: Target, *, timeout: httpx.Timeout | float = TIMEOUT) -> None:
        self.target = target
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None
        #: Milliseconds of the last request, without the pacing wait.
        self.took_ms = 0

    async def __aenter__(self) -> Self:
        # ⚠️ log_bodies=False: a feed carries download links with keys.
        self._http = http_log.client("indexer", timeout=self._timeout, follow_redirects=False, log_bodies=False)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError("IndexerClient is used outside of 'async with'")
        return self._http

    async def _request(self, params: dict[str, str | int]) -> tuple[Element, set[str]]:
        waiting = paused_seconds(self.target)
        if waiting:
            raise limit_reached(waiting, self.target.paused_until)
        await _pace(self.target.url)
        query: dict[str, str | int] = dict(params)
        if self.target.api_key:
            query["apikey"] = self.target.api_key
        started = time.perf_counter()
        try:
            async with self.http.stream(
                "GET",
                self.target.url,
                params=query,
                headers={"Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.1"},
            ) as response:
                body = await _read_limited(response)
                status_code, headers = response.status_code, response.headers
        except httpx.TimeoutException as exc:
            http_log.unreachable("indexer", "GET", self.target.url, exc)
            raise timed_out() from exc
        except httpx.TransportError as exc:
            http_log.unreachable("indexer", "GET", self.target.url, exc)
            raise unreachable(self.target.url) from exc
        except httpx.RequestError as exc:
            raise unreachable(self.target.url) from exc
        finally:
            self.took_ms = int((time.perf_counter() - started) * 1000)
        return self._interpret(status_code, headers, body)

    def _limit(self, headers: httpx.Headers) -> IndexerError:
        seconds = retry_after_seconds(headers.get("retry-after"))
        until = now() + timedelta(seconds=seconds)
        with _state_lock:
            _paused[_pause_key(self.target.url, self.target.api_key)] = until
        logger.warning("Indexer request limit reached, no requests for %d seconds", seconds)
        return limit_reached(seconds, until)

    def _interpret(self, status_code: int, headers: httpx.Headers, body: bytes) -> tuple[Element, set[str]]:
        content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        root, namespaces = parse_xml(body, content_type)
        if root is not None and _local(root.tag) == "error":
            raw_code = (root.get("code") or "").strip()
            code = _int_text(raw_code)
            description = (root.get("description") or "").casefold()
            # The description is never logged: some indexers repeat the key in it.
            logger.info("Indexer answered error %s with HTTP %d", code if code is not None else "?", status_code)
            if code is not None and 100 <= code <= 199:
                raise key_rejected()
            if LIMIT_TEXT in description or status_code == 429:
                raise self._limit(headers)
            if status_code in (401, 403):
                raise key_rejected()
            raise indexer_error(code if code is not None else (re.sub(r"[^A-Za-z0-9_.-]", "", raw_code)[:16] or "?"))
        if status_code in (401, 403):
            raise key_rejected()
        if status_code == 429:
            raise self._limit(headers)
        if root is None:
            if 200 <= status_code < 300 or status_code == 404:
                raise not_newznab()
            raise http_error(status_code)
        if not 200 <= status_code < 300:
            raise http_error(status_code)
        return root, namespaces

    async def caps(self) -> dict[str, Any] | None:
        """The caps, or None when missing or broken. Key, limit and connection failures are raised."""
        try:
            root, _namespaces = await self._request({"t": "caps"})
        except IndexerError as exc:
            if exc.code in _FATAL_FOR_CAPS:
                raise
            logger.info("Indexer caps not usable (%s), the defaults apply", exc.code)
            return None
        caps = parse_caps(root)
        if caps is None:
            logger.info("Indexer caps not usable (no caps document), the defaults apply")
        return caps

    async def _feed(self, params: dict[str, str | int]) -> Feed:
        root, namespaces = await self._request(params)
        feed = parse_feed(root, namespaces, self.target.kind)
        if feed.kind is not None and feed.kind != self.target.kind:
            raise wrong_kind()
        return feed

    async def probe(self, categories: list[int], caps: dict[str, Any] | None) -> Feed:
        """One feed request without a query: ``t=movie``, or ``t=search`` without movie search in the caps."""
        function = "movie" if (caps or DEFAULT_CAPS).get("movie_search") else "search"
        params: dict[str, str | int] = {"t": function}
        if categories:
            params["cat"] = ",".join(str(category) for category in categories)
        params.update({"extended": 1, "limit": 1})
        return await self._feed(params)

    async def probe_series(self, categories: list[int], caps: dict[str, Any] | None) -> Feed:
        """One feed request without a query in the series categories: ``t=tvsearch``, or ``t=search`` without it."""
        function = "tvsearch" if (caps or DEFAULT_CAPS).get("tv_search") else "search"
        params: dict[str, str | int] = {"t": function}
        if categories:
            params["cat"] = ",".join(str(category) for category in categories)
        params.update({"extended": 1, "limit": 1})
        return await self._feed(params)

    async def probe_music(self, categories: list[int], caps: dict[str, Any] | None) -> Feed:
        """One feed request without a query in the music categories: ``t=music``, or ``t=search`` without it."""
        function = "music" if (caps or DEFAULT_CAPS).get("music_search") else "search"
        params: dict[str, str | int] = {"t": function}
        if categories:
            params["cat"] = ",".join(str(category) for category in categories)
        params.update({"extended": 1, "limit": 1})
        return await self._feed(params)

    async def feed(self, params: dict[str, str | int]) -> Feed:
        """One feed request with exactly these parameters; the key is added here. The search of step 2c pages."""
        return await self._feed(params)

    async def search(self, query: str, categories: list[int], caps: dict[str, Any] | None) -> Feed:
        """One text search, at most ``SEARCH_LIMIT`` releases, no paging."""
        limit = SEARCH_LIMIT
        limit_max = (caps or {}).get("limit_max")
        if isinstance(limit_max, int) and limit_max > 0:
            limit = min(limit, limit_max)
        params: dict[str, str | int] = {"t": "search"}
        if categories:
            params["cat"] = ",".join(str(category) for category in categories)
        params.update({"extended": 1, "limit": limit, "q": query})
        return await self._feed(params)


@dataclass(frozen=True)
class TestResult:
    caps: dict[str, Any] | None
    categories: list[int]
    #: 0 or 1: whether the probe in the chosen categories delivered anything.
    feed_items: int
    #: The series categories probed, and 0 or 1 like ``feed_items``; null when series were not probed (an empty list).
    series_categories: list[int] | None = None
    series_feed_items: int | None = None
    #: 0 or 1 like ``feed_items`` for the music categories; null when music was not probed.
    music_feed_items: int | None = None


async def probe_anime(target: Target, categories: list[int], caps: dict[str, Any] | None) -> int:
    """0 or 1: whether one request in the anime categories delivers anything (B4).

    As Sonarr asks for an anime series: ``t=tvsearch`` in the anime categories, ``t=search`` without series search in
    the caps. An indexer that answers the request with an error of its own found nothing; a key, limit or connection
    failure is raised, as for every test.
    """
    async with IndexerClient(target) as indexer:
        try:
            return min(1, (await indexer.probe_series(list(categories), caps)).items)
        except IndexerError as exc:
            if exc.code in _FATAL_FOR_CAPS:
                raise
            logger.info("Indexer anime probe not usable (%s)", exc.code)
            return 0


async def run_test(
    target: Target,
    categories: list[int] | None,
    series_categories: list[int] | None = None,
    *,
    probe_series: bool = False,
    music_categories: list[int] | None = None,
) -> TestResult:
    """Caps, then the probe in the given categories, or in the default categories without any.

    With ``probe_series`` (S3) a second probe asks in ``series_categories``, or in the caps' series defaults when
    they are None. An empty list means series are off for this indexer, and nothing is asked.

    With ``music_categories`` (an indexer fetched from Lidarr) a third probe asks there, ``t=music`` or ``t=search``.
    """
    async with IndexerClient(target) as indexer:
        caps = await indexer.caps()
        chosen = list(categories) if categories else default_categories(caps)
        feed = await indexer.probe(chosen, caps)
        series_items: int | None = None
        if probe_series and series_categories is None:
            series_categories = default_series_categories(caps)
        if probe_series and series_categories:
            try:
                series_items = min(1, (await indexer.probe_series(list(series_categories), caps)).items)
            except IndexerError as exc:
                if exc.code in _FATAL_FOR_CAPS:
                    raise
                # An indexer without series search may answer the probe with an error of its own: nothing found.
                logger.info("Indexer series probe not usable (%s)", exc.code)
                series_items = 0
        music_items: int | None = None
        if music_categories:
            try:
                music_items = min(1, (await indexer.probe_music(list(music_categories), caps)).items)
            except IndexerError as exc:
                if exc.code in _FATAL_FOR_CAPS:
                    raise
                logger.info("Indexer music probe not usable (%s)", exc.code)
                music_items = 0
    return TestResult(
        caps=caps,
        categories=chosen,
        feed_items=min(1, feed.items),
        series_categories=list(series_categories) if series_categories is not None else None,
        series_feed_items=series_items,
        music_feed_items=music_items,
    )
