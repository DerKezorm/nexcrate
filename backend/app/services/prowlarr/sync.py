"""The sync of a Prowlarr connection: each Prowlarr indexer becomes an indexer of nexcrate's own, kept up to date.

What an indexer gets is what Prowlarr's own app sync gives Radarr, Sonarr and Lidarr (read in Prowlarr 2.6.5's source,
``Radarr.cs``, ``Sonarr.cs``, ``Lidarr.cs``, ``ApplicationService.cs``; ``homelab/nexcrate-pruefstand/prowlarr``):

* **Which:** without tags on the connection every Prowlarr indexer, with tags those sharing one (Prowlarr's tags never
  become nexcrate's tags). A new one only while switched on in Prowlarr. One without a fitting category for movies,
  series, anime and music alike is left out and counted. One Prowlarr holds in a failure back-off (``indexerstatus``)
  is neither added, changed nor removed in this sync.
* **Fields:** switched on when Prowlarr has it on and its app profile allows any search; automatic search when it is on
  and the profile allows RSS or the automatic search; Prowlarr's priority (1 to 50, else 25); for torrents the minimum
  seeders ``appMinimumSeeders`` or the profile's; the seed goals; the categories as the meeting of Radarr's, Sonarr's
  (5070 apart, for anime) and Lidarr's default lists (music without music videos and audiobooks, as nexcrate's own
  default) with the indexer's category tree; the address ``{connection}/{id}/api`` and Prowlarr's key; Torznab for
  ``torrent``, Newznab for ``usenet``. A query limit becomes the daily limit (an hourly one times 24).
* **Levels:** ``full`` writes those fields at every sync and removes an indexer that no longer qualifies;
  ``add_remove`` takes them when the indexer is added and leaves them to the owner afterwards. Both remove what
  Prowlarr no longer has, and both keep address and key with the connection: a key renewed in Prowlarr reaches every
  indexer once the owner enters it at the connection. (Prowlarr's "Add and Remove Only" leaves the old key in the
  apps; here it would only break the indexers.)
* **Taking over:** an indexer nexcrate has already (entered by hand or fetched from Radarr) joins the connection when
  its address is the same, or when its path is ``/{id}/api`` with Prowlarr's key at another host name. It keeps what
  only nexcrate has: tags, MULTi languages, searching without the year, the anime standard form.

Downloads keep the indexer's name when it goes; waiting releases of a removed indexer go with it.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
import secrets
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import select

from ... import crypto
from ...db import SessionLocal
from ...meldungen import meldung
from ...models import Indexer, ProwlarrConnection, utcnow
from .. import indexers as indexer_client
from .. import logs
from ..search import album as album_search
from .client import DEFAULT_PROFILE, AppProfile, ProwlarrClient, ProwlarrError, ProwlarrIndexer

logger = logging.getLogger("nexcrate.prowlarr")

#: The default sync categories of Prowlarr's apps (``RadarrSettings``, ``SonarrSettings``, ``LidarrSettings``).
RADARR_CATEGORIES = (2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060, 2070, 2080, 2090)
SONARR_CATEGORIES = (5000, 5010, 5020, 5030, 5040, 5045, 5050, 5090)
ANIME_CATEGORIES = (5070,)
LIDARR_CATEGORIES = tuple(
    category for category in (3000, 3010, 3030, 3040, 3050, 3060) if category not in album_search.LEFT_OUT_CATEGORIES
)
PRIORITY_MIN, PRIORITY_MAX, PRIORITY_DEFAULT = 1, 50, 25
SEEDERS_MAX = 1000
DAILY_LIMIT_MAX = 100_000
SEED_RATIO_MAX = 1000.0
#: Minutes: Radarr's field takes any; nexcrate keeps a year.
SEED_TIME_MAX = 525_600
#: The fields Prowlarr decides at the level ``full``; an owner changes them in Prowlarr. Address and key follow the
#: connection at both levels.
OWNED_FIELDS = (
    "name",
    "enabled",
    "automatic_search",
    "priority",
    "minimum_seeders",
    "categories",
    "series_categories",
    "anime_categories",
    "music_categories",
    "seed_ratio",
    "seed_time",
    "pack_seed_time",
    "prowlarr_limit",
)
CONNECTION_FIELDS = ("url", "api_key")
CATEGORY_FIELDS = ("categories", "series_categories", "anime_categories", "music_categories")
#: The error a search or a test leaves when Prowlarr answers 410. Prowlarr switching the indexer on again ends it.
SWITCHED_OFF_CODE = "indexer_switched_off"
#: The counts a sync keeps on the connection.
COUNTS = (
    "total",
    "added",
    "updated",
    "removed",
    "adopted",
    "skipped_categories",
    "skipped_disabled",
    "skipped_tags",
    "skipped_unsupported",
    "kept_blocked",
)


@dataclass(frozen=True)
class Wanted:
    """What one Prowlarr indexer makes of nexcrate's indexer."""

    prowlarr_indexer_id: int
    #: Whether Prowlarr has the indexer switched on: only then a missing one is added.
    enable: bool
    name: str
    kind: str
    enabled: bool
    automatic_search: bool
    priority: int
    minimum_seeders: int | None
    categories: list[int]
    series_categories: list[int]
    anime_categories: list[int]
    music_categories: list[int]
    seed_ratio: float | None
    seed_time: int | None
    pack_seed_time: int | None
    prowlarr_limit: int | None
    #: False for a switched off indexer whose category tree Prowlarr does not report (measured on 2.6.5: an indexer
    #: switched off answers ``capabilities.categories`` empty). Its categories then stay as they are.
    categories_known: bool = True

    def owned(self) -> dict[str, Any]:
        owned = {name: getattr(self, name) for name in OWNED_FIELDS}
        if not self.categories_known:
            for name in CATEGORY_FIELDS:
                owned.pop(name)
        return owned


@dataclass
class Plan:
    """What Prowlarr holds, read into nexcrate's terms."""

    wanted: dict[int, Wanted] = field(default_factory=dict)
    #: Every indexer id Prowlarr has.
    present: set[int] = field(default_factory=set)
    #: In a failure back-off: left as they are.
    blocked: set[int] = field(default_factory=set)
    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(COUNTS, 0))


def endpoint(base_url: str, prowlarr_indexer_id: int) -> str:
    return f"{base_url.rstrip('/')}/{prowlarr_indexer_id}/api"


#: Prowlarr's per-indexer endpoint as its apps get it: ``{base}/{id}/`` plus ``/api`` (``AppIndexerRegex``).
_ENDPOINT_PATH = re.compile(r"/(\d{1,6})(?:/api)?/?$")
#: The name Prowlarr gives an indexer in Radarr, Sonarr and Lidarr.
_APP_NAME_SUFFIX = "(prowlarr)"


def looks_like_prowlarr(url: str, name: str = "") -> bool:
    """An indexer an app got from Prowlarr: its path is ``/{id}/api``, or its name ends in "(Prowlarr)"."""
    return bool(_ENDPOINT_PATH.search(_path_of(url))) or name.strip().casefold().endswith(_APP_NAME_SUFFIX)


def indexer_of(url: str, base_url: str) -> int | None:
    """The Prowlarr indexer id when ``url`` is an endpoint of the Prowlarr at ``base_url``, else None."""
    try:
        one, base = httpx.URL(url), httpx.URL(base_url)
    except (httpx.InvalidURL, TypeError, ValueError):
        return None
    if (one.scheme, (one.host or "").lower(), one.port) != (base.scheme, (base.host or "").lower(), base.port):
        return None
    prefix = base.path.rstrip("/")
    path = one.path.rstrip("/")
    if not path.startswith(prefix + "/"):
        return None
    found = _ENDPOINT_PATH.fullmatch(path[len(prefix) :])
    return int(found.group(1)) if found else None


def _meeting(defaults: Iterable[int], tree: frozenset[int]) -> list[int]:
    return [category for category in defaults if category in tree]


def _seed_ratio(value: float | None) -> float | None:
    if value is None or value < 0 or value > SEED_RATIO_MAX:
        return None
    return round(value, 3)


def _minutes(value: int | None) -> int | None:
    if value is None or value < 0 or value > SEED_TIME_MAX:
        return None
    return value


def _daily(indexer: ProwlarrIndexer) -> int | None:
    if indexer.query_limit is None or indexer.query_limit <= 0:
        return None
    per_day = indexer.query_limit * 24 if indexer.limits_unit == 1 else indexer.query_limit
    return min(per_day, DAILY_LIMIT_MAX)


def wanted_of(indexer: ProwlarrIndexer, profile: AppProfile) -> Wanted:
    torrent = indexer.protocol == "torrent"
    priority = indexer.priority
    if priority is None or not PRIORITY_MIN <= priority <= PRIORITY_MAX:
        priority = PRIORITY_DEFAULT
    seeders = None
    if torrent:
        seeders = indexer.app_minimum_seeders if indexer.app_minimum_seeders is not None else profile.minimum_seeders
        seeders = min(max(seeders, 0), SEEDERS_MAX)
    any_search = profile.enable_rss or profile.enable_automatic_search or profile.enable_interactive_search
    return Wanted(
        prowlarr_indexer_id=indexer.id,
        enable=indexer.enable,
        name=indexer.name[:100],
        kind="torznab" if torrent else "newznab",
        enabled=indexer.enable and any_search,
        automatic_search=indexer.enable and (profile.enable_rss or profile.enable_automatic_search),
        priority=priority,
        minimum_seeders=seeders,
        categories=_meeting(RADARR_CATEGORIES, indexer.categories),
        series_categories=_meeting(SONARR_CATEGORIES, indexer.categories),
        anime_categories=_meeting(ANIME_CATEGORIES, indexer.categories),
        music_categories=_meeting(LIDARR_CATEGORIES, indexer.categories),
        seed_ratio=_seed_ratio(indexer.seed_ratio) if torrent else None,
        seed_time=_minutes(indexer.seed_time) if torrent else None,
        pack_seed_time=_minutes(indexer.pack_seed_time) if torrent else None,
        prowlarr_limit=_daily(indexer),
    )


def plan(
    listed: list[ProwlarrIndexer], profiles: dict[int, AppProfile], tags: Iterable[int], blocked: set[int]
) -> Plan:
    """Prowlarr's indexers in nexcrate's terms; pure, no database."""
    wanted_tags = set(tags)
    result = Plan(blocked=set(blocked))
    for indexer in listed:
        result.present.add(indexer.id)
        result.counts["total"] += 1
        if indexer.id in blocked:
            result.counts["kept_blocked"] += 1
            continue
        if indexer.protocol not in ("torrent", "usenet"):
            result.counts["skipped_unsupported"] += 1
            continue
        if wanted_tags and not wanted_tags.intersection(indexer.tags):
            result.counts["skipped_tags"] += 1
            continue
        profile = profiles.get(indexer.app_profile_id or -1, DEFAULT_PROFILE)
        wanted = wanted_of(indexer, profile)
        if not indexer.enable and not indexer.categories:
            # ⚠️ Prowlarr reports no category tree for an indexer it has switched off (measured 02.10.2026): read as
            # "no fitting category", a full sync removed it. It is switched off in nexcrate instead, as Prowlarr's own
            # apps keep it; a missing one is not added.
            result.wanted[indexer.id] = dataclasses.replace(wanted, categories_known=False)
            continue
        if not (wanted.categories or wanted.series_categories or wanted.anime_categories or wanted.music_categories):
            result.counts["skipped_categories"] += 1
            continue
        result.wanted[indexer.id] = wanted
    return result


# --- Writing ---------------------------------------------------------------------------------------------------- #


def _same_endpoint(first: str, second: str) -> bool:
    try:
        one, two = httpx.URL(first), httpx.URL(second)
    except (httpx.InvalidURL, TypeError, ValueError):
        return False
    return (
        one.scheme == two.scheme
        and (one.host or "").lower() == (two.host or "").lower()
        and one.port == two.port
        and one.path.rstrip("/") == two.path.rstrip("/")
    )


def _path_of(url: str) -> str:
    try:
        return httpx.URL(url).path.rstrip("/")
    except (httpx.InvalidURL, TypeError, ValueError):
        return ""


def _set(row: Indexer, values: dict[str, Any]) -> bool:
    changed = False
    for name, value in values.items():
        if getattr(row, name) != value:
            setattr(row, name, value)
            changed = True
    return changed


def _owned_values(wanted: Wanted, row: Indexer | None) -> dict[str, Any]:
    values = wanted.owned()
    values["kind"] = wanted.kind
    # The daily limit stands in for Prowlarr's query limit; without one it goes back to the owner's (null when it was
    # Prowlarr's).
    if wanted.prowlarr_limit is not None:
        values["daily_limit"] = wanted.prowlarr_limit
    elif row is not None and row.prowlarr_limit is not None:
        values["daily_limit"] = None
    return values


def _connection_values(row: Indexer, url: str, key: str) -> dict[str, Any]:
    """Address and key of the connection; a change of either ends a pause and the last error, as a changed indexer
    does when saved."""
    values: dict[str, Any] = {}
    if row.url != url:
        values["url"] = url
    stored = crypto.decrypt(row.api_key) if row.api_key else ""
    if stored != key:
        values["api_key"] = crypto.encrypt(key) if key else ""
    if values:
        values["paused_until"] = None
        values["last_error_code"] = None
    return values


def _switched_on_values(row: Indexer, wanted: Wanted) -> dict[str, Any]:
    """An indexer Prowlarr has switched on again no longer carries the 410 of its time off. At both levels: the
    error was Prowlarr's. Any other error stays until the next search or test."""
    if wanted.enable and row.last_error_code == SWITCHED_OFF_CODE:
        return {"last_error_code": None}
    return {}


@dataclass(frozen=True)
class Outcome:
    counts: dict[str, int]


def apply(connection_id: int, result: Plan, *, base_url: str, key: str, level: str, moment: datetime) -> Outcome:
    """Write the plan into nexcrate's indexers of the connection. One transaction."""
    counts = dict(result.counts)
    with SessionLocal() as db:
        mapped = {
            row.prowlarr_indexer_id: row
            for row in db.scalars(select(Indexer).where(Indexer.prowlarr_id == connection_id))
            if row.prowlarr_indexer_id is not None
        }
        free = list(db.scalars(select(Indexer).where(Indexer.prowlarr_id.is_(None)).order_by(Indexer.id)))
        for prowlarr_id, row in list(mapped.items()):
            gone = prowlarr_id not in result.present
            unqualified = prowlarr_id not in result.wanted and prowlarr_id not in result.blocked
            if gone or (level == "full" and unqualified):
                logger.info(
                    "Prowlarr connection %d: indexer %d goes (%s)",
                    connection_id,
                    row.id,
                    "gone from Prowlarr" if gone else "no longer taken",
                )
                db.delete(row)
                del mapped[prowlarr_id]
                counts["removed"] += 1
        for prowlarr_id, wanted in result.wanted.items():
            url = endpoint(base_url, prowlarr_id)
            row = mapped.get(prowlarr_id)
            if row is not None:
                changed = _set(row, _connection_values(row, url, key))
                changed = _set(row, _switched_on_values(row, wanted)) or changed
                if level == "full":
                    changed = _set(row, _owned_values(wanted, row)) or changed
                if changed:
                    row.updated_at = moment
                    counts["updated"] += 1
                continue
            match = _adoptable(free, url, prowlarr_id, key)
            if match is not None:
                free.remove(match)
                match.prowlarr_id = connection_id
                match.prowlarr_indexer_id = prowlarr_id
                _set(match, _connection_values(match, url, key))
                _set(match, _owned_values(wanted, match))
                match.updated_at = moment
                counts["adopted"] += 1
                logger.info("Prowlarr connection %d: indexer %d joins it", connection_id, match.id)
                continue
            if not wanted.enable:
                counts["skipped_disabled"] += 1
                continue
            values = _owned_values(wanted, None)
            row = Indexer(
                url=url,
                api_key=crypto.encrypt(key) if key else "",
                prowlarr_id=connection_id,
                prowlarr_indexer_id=prowlarr_id,
                multi_languages=[],
                remove_year=False,
                anime_standard_format_search=True,
                caps=None,
                # Prowlarr tested the indexer; the first search fetches its caps.
                caps_checked_at=None,
                created_at=moment,
                updated_at=moment,
                **values,
            )
            db.add(row)
            counts["added"] += 1
        db.flush()
        for row in db.scalars(select(Indexer).where(Indexer.prowlarr_id == connection_id)):
            if row.prowlarr_indexer_id in result.blocked:
                _set(row, _connection_values(row, endpoint(base_url, row.prowlarr_indexer_id), key))
        db.commit()
    return Outcome(counts=counts)


def _adoptable(free: list[Indexer], url: str, prowlarr_id: int, key: str) -> Indexer | None:
    for row in free:
        if _same_endpoint(row.url, url):
            return row
    suffix = f"/{prowlarr_id}/api"
    for row in free:
        if not _path_of(row.url).endswith(suffix) or not row.api_key:
            continue
        if key and crypto.decrypt(row.api_key) == key:
            return row
    return None


# --- One sync ---------------------------------------------------------------------------------------------------- #

_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(connection_id: int) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(connection_id, threading.Lock())


class SyncBusy(Exception):
    """A sync of this connection runs already."""


@dataclass(frozen=True)
class _Connection:
    id: int
    url: str
    #: ⚠️ Decrypted, in memory only while Prowlarr is asked.
    key: str = field(repr=False)
    level: str = "full"
    tags: tuple[int, ...] = ()


def _load(connection_id: int) -> _Connection | None:
    with SessionLocal() as db:
        row = db.get(ProwlarrConnection, connection_id)
        if row is None:
            return None
        key = crypto.decrypt(row.api_key) if row.api_key else ""
        tags = tuple(int(entry["id"]) for entry in row.tags or [] if isinstance(entry, dict) and "id" in entry)
        return _Connection(id=row.id, url=row.url, key=key, level=row.sync_level, tags=tags)


def _record(
    connection_id: int,
    *,
    version: str | None,
    counts: dict[str, int] | None,
    error_code: str | None,
    labels: dict[int, str] | None = None,
) -> None:
    with SessionLocal() as db:
        row = db.get(ProwlarrConnection, connection_id)
        if row is None:
            return
        row.last_sync_at = utcnow()
        row.last_error_code = error_code
        if version is not None:
            row.version = version
        if counts is not None:
            row.last_counts = counts
        if labels:
            # A tag renamed in Prowlarr keeps its id: only the label follows.
            row.tags = [
                {"id": entry["id"], "label": labels.get(int(entry["id"]), entry.get("label"))}
                for entry in row.tags or []
                if isinstance(entry, dict) and "id" in entry
            ]
        db.commit()


def tag_missing(missing: list[int]) -> ProwlarrError:
    return ProwlarrError(
        meldung(
            "prowlarr_tag_missing",
            "Prowlarr has no tag with this id (any more). Choose the tags again.",
            tags=missing,
        ),
        422,
    )


def key_missing() -> ProwlarrError:
    return ProwlarrError(
        meldung("prowlarr_key_missing", "The stored API key of this Prowlarr cannot be read. Please enter it again."),
        422,
    )


async def sync(connection_id: int) -> dict[str, int]:
    """Read Prowlarr and write the connection's indexers. Returns the counts.

    Raises ``ProwlarrError`` (recorded on the connection as well), ``SyncBusy``, or ``LookupError`` when the connection
    is gone.
    """
    lock = _lock(connection_id)
    if not lock.acquire(blocking=False):
        raise SyncBusy
    try:
        connection = await asyncio.to_thread(_load, connection_id)
        if connection is None:
            raise LookupError(connection_id)
        if not connection.key:
            failure = key_missing()
            await asyncio.to_thread(_record, connection_id, version=None, counts=None, error_code=failure.code)
            raise failure
        try:
            async with ProwlarrClient(connection.url, connection.key) as prowlarr:
                status = await prowlarr.system_status()
                listed = await prowlarr.indexers()
                profiles = await prowlarr.app_profiles()
                blocked = await prowlarr.blocked(indexer_client.now())
                labels = await prowlarr.tags() if connection.tags else {}
            # ⚠️ A tag gone from Prowlarr would match no indexer, and a full sync would remove them all: nothing changes
            # until the owner chooses the tags again.
            missing = [tag for tag in connection.tags if tag not in labels]
            if missing:
                raise tag_missing(missing)
        except ProwlarrError as exc:
            logger.info("Prowlarr connection %d: the sync failed: %s", connection_id, exc.code)
            await asyncio.to_thread(_record, connection_id, version=None, counts=None, error_code=exc.code)
            raise
        result = plan(listed, profiles, connection.tags, blocked)
        outcome = await asyncio.to_thread(
            apply,
            connection_id,
            result,
            base_url=connection.url,
            key=connection.key,
            level=connection.level,
            moment=utcnow(),
        )
        counts = outcome.counts
        await asyncio.to_thread(
            _record, connection_id, version=status.version, counts=counts, error_code=None, labels=labels
        )
        left_out = sum(counts[name] for name in COUNTS if name.startswith("skipped_"))
        logger.info(
            "Prowlarr connection %d synced: %d in Prowlarr, %d added, %d changed, %d removed, %d joined, %d left out, "
            "%d held back by Prowlarr",
            connection_id,
            counts["total"],
            counts["added"],
            counts["updated"],
            counts["removed"],
            counts["adopted"],
            left_out,
            counts["kept_blocked"],
        )
        return counts
    finally:
        lock.release()


# --- The job ---------------------------------------------------------------------------------------------------- #

JOB_NAME = "prowlarr_sync"
#: A request to Prowlarr costs the trackers nothing; Prowlarr's own apps sync every six hours.
INTERVAL_SECONDS = 900


def _enabled_ids() -> list[int]:
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(ProwlarrConnection.id).where(ProwlarrConnection.enabled.is_(True)).order_by(ProwlarrConnection.id)
            )
        )


async def run_job() -> None:
    """Every enabled connection once; a failure is kept on its connection and the next one goes on."""
    for connection_id in await asyncio.to_thread(_enabled_ids):
        token = logs.bind_request(secrets.token_hex(4))
        try:
            await sync(connection_id)
        except (ProwlarrError, SyncBusy, LookupError):
            continue
        finally:
            logs.unbind_request(token)
