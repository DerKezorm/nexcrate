"""A read-only Prowlarr client: ``/api/v1/system/status``, ``indexer``, ``appprofile``, ``tag`` and ``indexerstatus``.

Measured on Prowlarr 2.6.5.5623 (linuxserver image, 02.10.2026, ``homelab/nexcrate-pruefstand/prowlarr``):

* ⚠️ The key travels only in the ``X-Api-Key`` header. Prowlarr also takes ``?apikey=`` and prefers it; a URL ends up in
  logs, a header does not. Prowlarr's per-indexer endpoints ``/{id}/api`` take the key as a parameter, as every
  Torznab endpoint does; that is the indexer client's business, not this one's.
* A wrong or missing key is 401 with an empty body, on the JSON API as on the endpoints. An unknown API path is 404
  without a content type. Everything outside ``/api/`` is the web interface with 200: a 200 proves nothing,
  ``system/status`` counts only as JSON with ``appName`` "Prowlarr".
* The address carries Prowlarr's URL base (``urlBase`` in the status); without it the API is not found at all.
* A field without a value has no ``value`` key; a ratio of 1.0 comes as ``1``.
* Version 1.8.6 is the oldest release with every field nexcrate reads (``limitsUnit`` came then; ``preferMagnetUrl``
  came with 1.25.4 and is not read).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Self

import httpx
from fastapi import HTTPException

from ...meldungen import meldung
from .. import http_log
from ..radarr import normalize_base_url
from ..schreibweisen import nfc_or_none

logger = logging.getLogger("nexcrate.prowlarr")

APP_NAME = "Prowlarr"
STATUS_PATH = "/api/v1/system/status"
INDEXER_PATH = "/api/v1/indexer"
APP_PROFILE_PATH = "/api/v1/appprofile"
TAG_PATH = "/api/v1/tag"
INDEXER_STATUS_PATH = "/api/v1/indexerstatus"
#: The oldest Prowlarr with every field nexcrate reads.
MINIMUM_VERSION = (1, 8, 6)
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


# --- Errors ------------------------------------------------------------------ #


class ProwlarrError(Exception):
    """An answer of Prowlarr nexcrate cannot use, with the error the API sends for it."""

    def __init__(self, detail: dict[str, Any], status: int) -> None:
        super().__init__(detail["code"])
        self.detail = detail
        self.status = status

    @property
    def code(self) -> str:
        return str(self.detail["code"])

    def http(self) -> HTTPException:
        return HTTPException(status_code=self.status, detail=self.detail)


def unreachable(url: str) -> ProwlarrError:
    return ProwlarrError(
        meldung("prowlarr_unreachable", f"Prowlarr cannot be reached at {url}. Are address and port right?", url=url),
        502,
    )


def timed_out() -> ProwlarrError:
    return ProwlarrError(meldung("prowlarr_timeout", "Prowlarr did not answer in time."), 504)


def key_rejected() -> ProwlarrError:
    return ProwlarrError(meldung("prowlarr_key_rejected", "Prowlarr did not accept the API key."), 502)


def not_prowlarr() -> ProwlarrError:
    return ProwlarrError(
        meldung(
            "prowlarr_not_prowlarr",
            "Something answers at this address, but it is not Prowlarr. With a URL base in Prowlarr, the address "
            "needs it too.",
        ),
        502,
    )


def too_old(version: str) -> ProwlarrError:
    return ProwlarrError(
        meldung(
            "prowlarr_too_old",
            f"This Prowlarr is version {version}; nexcrate needs 1.8.6 or newer.",
            version=version,
        ),
        502,
    )


def http_error(status: int) -> ProwlarrError:
    return ProwlarrError(
        meldung("prowlarr_http_error", f"Prowlarr reports an error (HTTP {status}).", status=status), 502
    )


#: For the OpenAPI ``responses`` of every route that asks Prowlarr.
ERRORS = (
    (502, "prowlarr_unreachable"),
    (502, "prowlarr_key_rejected"),
    (502, "prowlarr_not_prowlarr"),
    (502, "prowlarr_too_old"),
    (502, "prowlarr_http_error"),
    (504, "prowlarr_timeout"),
)


# --- Tolerant reading ---------------------------------------------------------- #


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


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _ints(values: Any) -> list[int]:
    found: list[int] = []
    for value in _list(values):
        number = _int(value)
        if number is not None and number not in found:
            found.append(number)
    return found


def version_tuple(value: str) -> tuple[int, ...]:
    match = _VERSION.match(value or "")
    return tuple(int(part) for part in match.groups()) if match else ()


def _moment(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


# --- What is read ---------------------------------------------------------------- #


@dataclass(frozen=True)
class Status:
    version: str
    #: Prowlarr's URL base, ``""`` or ``/prefix``.
    url_base: str


@dataclass(frozen=True)
class AppProfile:
    id: int
    name: str
    enable_rss: bool = True
    enable_automatic_search: bool = True
    enable_interactive_search: bool = True
    minimum_seeders: int = 1


#: Prowlarr creates this profile on its first start; an indexer whose profile is gone is read with it.
DEFAULT_PROFILE = AppProfile(id=0, name="Standard")


@dataclass(frozen=True)
class ProwlarrIndexer:
    id: int
    name: str
    enable: bool
    #: ``torrent``, ``usenet`` or anything Prowlarr sends (``unknown``).
    protocol: str
    priority: int | None
    app_profile_id: int | None
    tags: tuple[int, ...]
    #: Every category id of the tree, parents and subcategories alike.
    categories: frozenset[int]
    query_limit: int | None = None
    #: 0 for a day, 1 for an hour (``IndexerLimitsUnit``); missing before Prowlarr 1.8.6, a day then.
    limits_unit: int = 0
    app_minimum_seeders: int | None = None
    seed_ratio: float | None = None
    #: Minutes.
    seed_time: int | None = None
    pack_seed_time: int | None = None
    privacy: str | None = None
    #: The fields as read, for nothing but the tests.
    raw_fields: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


#: The only fields of an indexer nexcrate reads. ``apiKey`` comes back masked and is never read.
INDEXER_FIELDS = (
    "baseSettings.queryLimit",
    "baseSettings.limitsUnit",
    "torrentBaseSettings.appMinimumSeeders",
    "torrentBaseSettings.seedRatio",
    "torrentBaseSettings.seedTime",
    "torrentBaseSettings.packSeedTime",
)


def _tree(capabilities: dict[str, Any]) -> frozenset[int]:
    found: set[int] = set()
    stack = list(_list(capabilities.get("categories")))
    while stack:
        entry = _dict(stack.pop())
        number = _int(entry.get("id"))
        if number is not None:
            found.add(number)
        stack.extend(_list(entry.get("subCategories")))
    return frozenset(found)


def parse_indexer(value: Any) -> ProwlarrIndexer | None:
    data = _dict(value)
    indexer_id = _int(data.get("id"))
    if indexer_id is None:
        return None
    fields: dict[str, Any] = {}
    for item in map(_dict, _list(data.get("fields"))):
        name = item.get("name")
        if name in INDEXER_FIELDS and "value" in item:
            fields[name] = item["value"]
    protocol = (nfc_or_none(data.get("protocol")) or "").strip().lower()
    unit = _int(fields.get("baseSettings.limitsUnit"))
    return ProwlarrIndexer(
        id=indexer_id,
        name=(nfc_or_none(data.get("name")) or "").strip() or f"Prowlarr indexer {indexer_id}",
        enable=data.get("enable") is True,
        protocol=protocol,
        priority=_int(data.get("priority")),
        app_profile_id=_int(data.get("appProfileId")),
        tags=tuple(_ints(data.get("tags"))),
        categories=_tree(_dict(data.get("capabilities"))),
        query_limit=_int(fields.get("baseSettings.queryLimit")),
        limits_unit=unit if unit in (0, 1) else 0,
        app_minimum_seeders=_int(fields.get("torrentBaseSettings.appMinimumSeeders")),
        seed_ratio=_number(fields.get("torrentBaseSettings.seedRatio")),
        seed_time=_int(fields.get("torrentBaseSettings.seedTime")),
        pack_seed_time=_int(fields.get("torrentBaseSettings.packSeedTime")),
        privacy=nfc_or_none(data.get("privacy")),
        raw_fields=fields,
    )


def parse_profile(value: Any) -> AppProfile | None:
    data = _dict(value)
    profile_id = _int(data.get("id"))
    if profile_id is None:
        return None
    seeders = _int(data.get("minimumSeeders"))
    return AppProfile(
        id=profile_id,
        name=(nfc_or_none(data.get("name")) or "").strip() or f"Profile {profile_id}",
        enable_rss=data.get("enableRss") is not False,
        enable_automatic_search=data.get("enableAutomaticSearch") is not False,
        enable_interactive_search=data.get("enableInteractiveSearch") is not False,
        minimum_seeders=seeders if seeders is not None else 1,
    )


def parse_tags(value: Any) -> dict[int, str]:
    found: dict[int, str] = {}
    for item in map(_dict, _list(value)):
        tag_id = _int(item.get("id"))
        label = (nfc_or_none(item.get("label")) or "").strip()
        if tag_id is not None and label:
            found[tag_id] = label
    return found


def blocked_ids(value: Any, moment: datetime) -> set[int]:
    """Prowlarr's indexers in a failure back-off right now (``disabledTill`` in the future)."""
    found: set[int] = set()
    for item in map(_dict, _list(value)):
        indexer_id = _int(item.get("indexerId"))
        until = _moment(item.get("disabledTill"))
        if indexer_id is not None and until is not None and until > moment:
            found.add(indexer_id)
    return found


class ProwlarrClient:
    """Use as ``async with ProwlarrClient(url, key) as prowlarr: ...``."""

    def __init__(self, base_url: str, api_key: str, *, timeout: httpx.Timeout | float = TIMEOUT) -> None:
        self.base_url = normalize_base_url(base_url)
        self._api_key = api_key
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> Self:
        # ⚠️ The key goes into a header of the client, never into params or a URL.
        self._http = http_log.client(
            "prowlarr",
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
            raise RuntimeError("ProwlarrClient is used outside of 'async with'")
        return self._http

    async def _json(self, path: str) -> Any:
        # A header cannot carry anything but ASCII; httpx would raise deep inside.
        if not self._api_key.isascii() or not self._api_key.isprintable():
            raise key_rejected()
        try:
            response = await http_log.send(self.http, "GET", path)
        except httpx.TimeoutException as exc:
            raise timed_out() from exc
        except httpx.RequestError as exc:
            raise unreachable(self.base_url) from exc
        status = response.status_code
        if status == 401:
            raise key_rejected()
        if 300 <= status < 400 or status == 404:
            raise not_prowlarr()
        if not 200 <= status < 300:
            raise http_error(status)
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise not_prowlarr()
        try:
            return response.json()
        except ValueError as exc:
            raise not_prowlarr() from exc

    async def _items(self, path: str) -> list[Any]:
        data = await self._json(path)
        if not isinstance(data, list):
            raise not_prowlarr()
        return data

    async def system_status(self) -> Status:
        data = await self._json(STATUS_PATH)
        if not isinstance(data, dict) or data.get("appName") != APP_NAME:
            raise not_prowlarr()
        version = (nfc_or_none(data.get("version")) or "").strip()[:64]
        parsed = version_tuple(version)
        if not parsed or parsed < MINIMUM_VERSION:
            raise too_old(version or "?")
        return Status(version=version, url_base=(nfc_or_none(data.get("urlBase")) or "").strip())

    async def indexers(self) -> list[ProwlarrIndexer]:
        return [item for item in map(parse_indexer, await self._items(INDEXER_PATH)) if item is not None]

    async def app_profiles(self) -> dict[int, AppProfile]:
        profiles = [item for item in map(parse_profile, await self._items(APP_PROFILE_PATH)) if item is not None]
        return {profile.id: profile for profile in profiles}

    async def tags(self) -> dict[int, str]:
        return parse_tags(await self._items(TAG_PATH))

    async def blocked(self, moment: datetime) -> set[int]:
        return blocked_ids(await self._items(INDEXER_STATUS_PATH), moment)
