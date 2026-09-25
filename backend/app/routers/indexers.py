"""Indexers: Newznab and Torznab endpoints. Connect, test, a test search, fetch from Radarr.

⚠️ The API key is stored encrypted and never leaves the backend again, not even partly. Answers
carry ``has_api_key`` instead, and never a download link.

Saving runs the test (``t=caps`` and one feed request with ``limit=1``). An empty feed in the chosen
categories answers 409 ``indexer_categories_empty`` unless the body confirms it with
``confirm_empty``: nexcrate checks, and the owner decides. Radarr refuses outright.

The search settings of step 2c (``priority``, ``minimum_seeders``, ``multi_languages``, ``remove_year``) change
what a search does, not the connection: changing only them sends nothing to the indexer. They are checked before
any request. ``automatic_search`` is taken over from Radarr for step 3 and not shown yet.

The kind of a saved indexer never changes: a change with another kind answers 422 ``indexer_kind_locked``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from .. import crypto
from ..db import SessionLocal
from ..deps import DbSession
from ..meldungen import error, error_responses
from ..models import media, utcnow
from ..services import indexers, tags
from ..services.lidarr import ERRORS as LIDARR_ERRORS
from ..services.lidarr import LidarrClient
from ..services.radarr import RadarrClient, RadarrError, RadarrIndexer, SourceUrlInvalid
from ..services.releases import languages as lang
from ..services.schreibweisen import nfc
from ..services.search import album as album_search
from ..services.search import options as search_options
from ..services.sonarr import SonarrClient
from .sources import RADARR_ERRORS

logger = logging.getLogger("nexcrate.indexers")

router = APIRouter(prefix="/api/indexers", tags=["indexers"])

NAME_MAX_LENGTH = 100
KEY_MAX_LENGTH = 256
CATEGORIES_MAX = 200
CATEGORY_MAX_ID = 999_999
#: Radarr's range and default.
PRIORITY_MIN, PRIORITY_MAX, PRIORITY_DEFAULT = 1, 50, 25

#: B3: what the switch on an indexer means, for all three shapes.
_ANIME_STANDARD_TEXT = (
    "An anime series is asked for in the standard form (`S01E01`) too, not only by the number it counts through. "
    "Sonarr calls this Anime Standard Format Search and has it off; nexcrate has always asked both, so it stays "
    "on and this turns it off to save requests."
)
_ANIME_EMPTY_TEXT = (
    "Save although one request in the chosen anime categories found nothing (B4). Chosen "
    "anime categories are asked once when they change or the address or key changes; the default is never asked."
)
SEEDERS_MAX = 1000
#: Radarr's default for torrents.
SEEDERS_DEFAULT = 1
MULTI_LANGUAGES_MAX = 100
DAILY_LIMIT_MIN, DAILY_LIMIT_MAX = 1, 100_000
Kind = Literal["newznab", "torznab"]


class CapsSubcat(BaseModel):
    id: int
    name: str


class CapsCategory(BaseModel):
    id: int
    name: str
    subcats: list[CapsSubcat]


class Caps(BaseModel):
    movie_search: bool = Field(description="Whether the indexer offers t=movie.")
    movie_params: list[str] = Field(examples=[["q", "imdbid", "tmdbid"]])
    tv_search: bool = Field(default=False, description="Whether the indexer offers t=tvsearch.")
    tv_params: list[str] = Field(default_factory=list, examples=[["q", "season", "ep", "tvdbid", "tmdbid"]])
    search_params: list[str] = Field(examples=[["q"]])
    music_search: bool = Field(default=False, description="Whether the indexer offers t=music.")
    music_params: list[str] = Field(default_factory=list, examples=[["q", "artist", "album"]])
    search_engine: str = Field(description="raw or sphinx.")
    limit_max: int | None
    limit_default: int | None
    categories: list[CapsCategory]


class FromSource(BaseModel):
    source_id: int
    name: str


class IndexerUsage(BaseModel):
    api_current: int | None = Field(description="Requests used in the last 24 hours, as the indexer counts them.")
    api_max: int | None = Field(description="Requests allowed in 24 hours.")
    grab_current: int | None = Field(description="Grabs used in the last 24 hours.")
    grab_max: int | None = Field(description="Grabs allowed in 24 hours.")
    api_next_at: datetime | None = Field(description="UTC. When the request counter goes down.")
    grab_next_at: datetime | None = Field(description="UTC. When the grab counter goes down.")
    seen_at: datetime = Field(description="UTC. When the indexer sent these values.")


class IndexerRss(BaseModel):
    last_at: datetime | None = Field(description="UTC. The last RSS sync, also one that failed.")
    newest_at: datetime | None = Field(description="UTC. The publish date of the newest release of the last sync.")
    gap_at: datetime | None = Field(description="UTC. When a sync could not reach back to the sync before it.")


class Indexer(BaseModel):
    id: int
    name: str
    tags: list[str] = Field(default_factory=list, description="Asked only for titles sharing one; none: for all.")
    kind: str = Field(description="newznab or torznab.")
    url: str = Field(description="The endpoint with its path.", examples=["https://indexer.example.com/api"])
    has_api_key: bool = Field(description="Whether a key is stored. The key itself is never returned.")
    categories: list[int]
    series_categories: list[int] = Field(
        description="The categories a series search asks in. Empty: the indexer is skipped for series."
    )
    music_categories: list[int] = Field(
        description="The categories an album search asks in: the chosen ones, else the default from the caps (3000 "
        "to 3999 without 3020 and 3030, else 3000, 3010 and 3040). Empty: the indexer is skipped for albums."
    )
    music_categories_chosen: bool = Field(
        description="False while the music categories follow the default from the caps."
    )
    anime_categories: list[int] = Field(
        default_factory=list,
        description="The categories an anime series is asked in as well: the chosen ones, else the default from the "
        "caps (5070 and its subcategories; 5070 without caps; none when the caps lack 5070).",
    )
    anime_categories_chosen: bool = Field(
        default=False, description="False while the anime categories follow the default from the caps."
    )
    anime_standard_format_search: bool = Field(default=True, description=_ANIME_STANDARD_TEXT)
    enabled: bool
    priority: int = Field(
        description=f"{PRIORITY_MIN} to {PRIORITY_MAX}, default {PRIORITY_DEFAULT}. Of two releases that rank the "
        "same otherwise, the one of the indexer with the lower number wins."
    )
    minimum_seeders: int | None = Field(
        description="Torznab only: a torrent with fewer known seeders does not fit. Null for Newznab."
    )
    multi_languages: list[str] = Field(
        description="ISO 639-1 codes a release of this indexer with the MULTi token carries.", examples=[["fr", "en"]]
    )
    remove_year: bool = Field(description="Search by title without the year.")
    caps: Caps | None = Field(
        description="Null when the indexer's caps were missing or broken; the defaults apply then and the "
        "interface shows a hint."
    )
    caps_checked_at: datetime | None = Field(description="UTC. Caps are fetched again after 7 days.")
    paused_until: datetime | None = Field(description="UTC. Set while a request limit pauses the indexer.")
    last_error_code: str | None = Field(description="The code of the last failed request, null after a success.")
    from_source: FromSource | None = Field(description="The Radarr connection it was fetched from, if any.")
    daily_limit: int | None = Field(
        description=f"Requests a day you allow, {DAILY_LIMIT_MIN} to {DAILY_LIMIT_MAX}; null when unknown. The "
        "indexer's own `newznab:apilimits` win when it sends them."
    )
    usage: IndexerUsage | None = Field(description="The indexer's own `newznab:apilimits`, null until it sent them.")
    escalation_level: int = Field(description="0 to 9: how far failures paused automatic work, as in Radarr.")
    automatic_paused_until: datetime | None = Field(
        description="UTC. Set while failures pause automatic work; the owner's own searches are not held back."
    )
    rss: IndexerRss


_PRIORITY_TEXT = f"{PRIORITY_MIN} to {PRIORITY_MAX}; left out: {PRIORITY_DEFAULT} for a new indexer, else unchanged."
_SEEDERS_TEXT = (
    f"Torznab only, 0 to {SEEDERS_MAX}; left out: {SEEDERS_DEFAULT} for a new Torznab indexer, else unchanged. "
    "Left out or null for Newznab."
)
_LANGUAGES_TEXT = "ISO 639-1 codes the release checker knows; left out: none for a new indexer, else unchanged."
_DAILY_LIMIT_TEXT = (
    f"Requests a day, {DAILY_LIMIT_MIN} to {DAILY_LIMIT_MAX}, or null for unknown; left out: null for a new indexer, "
    "else unchanged."
)


class IndexerIn(BaseModel):
    tags: list[str] | None = Field(
        default=None,
        max_length=tags.PER_ITEM_MAX,
        description="With tags the indexer is asked only for titles sharing one.",
    )
    name: str = Field(max_length=400, description=f"1 to {NAME_MAX_LENGTH} characters.")
    kind: Kind
    url: str = Field(
        max_length=2048, description="The endpoint with its path, http or https, without key or user info."
    )
    api_key: str = Field(default="", max_length=KEY_MAX_LENGTH, description="May be empty.")
    categories: list[int] = Field(default_factory=list, max_length=CATEGORIES_MAX, description="At least one.")
    series_categories: list[int] | None = Field(
        default=None,
        max_length=CATEGORIES_MAX,
        description="Left out: the caps categories from 5000 to 5999 without anime, else 5030, 5040 and 5045. "
        "Empty: no series search at this indexer.",
    )
    music_categories: list[int] | None = Field(
        default=None,
        max_length=CATEGORIES_MAX,
        description="Left out: the default from the caps, following them. Empty: no album search at this indexer.",
    )
    anime_categories: list[int] | None = Field(
        default=None,
        max_length=CATEGORIES_MAX,
        description="Left out: the default from the caps, following them. Empty: anime only in the series categories.",
    )
    anime_standard_format_search: bool = Field(default=True, description=_ANIME_STANDARD_TEXT)
    enabled: bool = True
    confirm_empty: bool = Field(
        default=False, description="Save although the test found nothing in the chosen categories."
    )
    confirm_anime_empty: bool = Field(default=False, description=_ANIME_EMPTY_TEXT)
    priority: int | None = Field(default=None, strict=True, description=_PRIORITY_TEXT)
    minimum_seeders: int | None = Field(default=None, strict=True, description=_SEEDERS_TEXT)
    multi_languages: list[str] | None = Field(default=None, max_length=MULTI_LANGUAGES_MAX, description=_LANGUAGES_TEXT)
    remove_year: bool | None = Field(default=None, strict=True, description="Left out: false for a new indexer.")
    daily_limit: int | None = Field(default=None, strict=True, description=_DAILY_LIMIT_TEXT)


class IndexerPatch(BaseModel):
    tags: list[str] | None = Field(default=None, max_length=tags.PER_ITEM_MAX, description="Every tag, by name.")
    name: str | None = Field(default=None, max_length=400)
    kind: Kind | None = Field(
        default=None,
        description="Cannot change: left out or the stored kind. Another kind answers 422 `indexer_kind_locked`.",
    )
    url: str | None = Field(default=None, max_length=2048)
    api_key: str | None = Field(default=None, max_length=KEY_MAX_LENGTH, description="Empty keeps the stored key.")
    categories: list[int] | None = Field(default=None, max_length=CATEGORIES_MAX)
    series_categories: list[int] | None = Field(
        default=None,
        max_length=CATEGORIES_MAX,
        description="Left out: unchanged. Empty: no series search at this indexer. Not tested on saving.",
    )
    music_categories: list[int] | None = Field(
        default=None,
        max_length=CATEGORIES_MAX,
        description="Left out: unchanged. Empty: no album search at this indexer. Not tested on saving.",
    )
    music_categories_default: bool = Field(
        default=False, description="True: follow the default from the caps again; music_categories is ignored."
    )
    anime_categories: list[int] | None = Field(
        default=None,
        max_length=CATEGORIES_MAX,
        description="Left out: unchanged. Empty: anime only in the series categories. Not tested on saving.",
    )
    anime_categories_default: bool = Field(
        default=False, description="True: follow the default from the caps again; anime_categories is ignored."
    )
    anime_standard_format_search: bool | None = Field(
        default=None, description="Left out: unchanged. " + _ANIME_STANDARD_TEXT
    )
    enabled: bool | None = None
    confirm_empty: bool = Field(default=False, description="Save although the test found nothing.")
    confirm_anime_empty: bool = Field(default=False, description=_ANIME_EMPTY_TEXT)
    priority: int | None = Field(default=None, strict=True, description=_PRIORITY_TEXT)
    minimum_seeders: int | None = Field(default=None, strict=True, description=_SEEDERS_TEXT)
    multi_languages: list[str] | None = Field(default=None, max_length=MULTI_LANGUAGES_MAX, description=_LANGUAGES_TEXT)
    remove_year: bool | None = Field(default=None, strict=True, description="Left out: unchanged.")
    daily_limit: int | None = Field(default=None, strict=True, description=_DAILY_LIMIT_TEXT)


class IndexerTestIn(BaseModel):
    kind: Kind | None = Field(
        default=None, description="Needed without `indexer_id`; with it, overrides the saved kind."
    )
    url: str | None = Field(
        default=None, max_length=2048, description="Needed without `indexer_id`; with it, overrides the saved address."
    )
    api_key: str | None = Field(
        default=None, max_length=KEY_MAX_LENGTH, description="Empty uses the stored key of `indexer_id`."
    )
    indexer_id: int | None = Field(
        default=None, description="Test a saved indexer; the fields given along override its stored values."
    )
    categories: list[int] | None = Field(
        default=None,
        max_length=CATEGORIES_MAX,
        description="Overrides the saved categories. Empty uses the saved ones, or the default ones.",
    )
    series_categories: list[int] | None = Field(
        default=None,
        max_length=CATEGORIES_MAX,
        description="The series categories to probe with a second request. Left out or empty: series are not probed.",
    )
    music_categories: list[int] | None = Field(
        default=None,
        max_length=CATEGORIES_MAX,
        description="The music categories to probe with one more request. Left out or empty: music is not probed.",
    )


class IndexerTestOut(BaseModel):
    caps: Caps | None = Field(description="Null when the caps were missing or broken; the defaults apply then.")
    feed_items: int = Field(description="0 or 1: whether the feed request found anything in the categories.")
    default_categories: list[int] = Field(
        description="The caps categories from 2000 to 2999, else the standard movie categories."
    )
    series_feed_items: int | None = Field(
        default=None,
        description="0 or 1: whether a `t=tvsearch` (or `t=search`) in the series categories found anything; null "
        "when series were not probed.",
    )
    default_series_categories: list[int] = Field(
        default_factory=list,
        description="The caps categories from 5000 to 5999 without anime, else 5030, 5040 and 5045.",
    )
    default_music_categories: list[int] = Field(
        default_factory=list,
        description="The caps categories from 3000 to 3999 without 3020 and 3030, else 3000, 3010 and 3040.",
    )
    music_feed_items: int | None = Field(
        default=None,
        description="0 or 1: whether a `t=music` (or `t=search`) in the music categories found anything; null when "
        "music was not probed.",
    )
    default_anime_categories: list[int] = Field(
        default_factory=list,
        description="5070 and its subcategories as the caps list them; 5070 without caps; none when they lack it.",
    )
    url: str = Field(description="The endpoint that was tested. An address without a path gets `/api`.")


class SearchIn(BaseModel):
    q: str = Field(max_length=200, description="Search text.")


class Release(BaseModel):
    title: str
    size_bytes: int | None
    published_at: datetime | None = Field(description="UTC. Usenet date for Newznab, else the publication date.")
    categories: list[int]
    seeders: int | None = Field(description="Torznab only.")
    peers: int | None = Field(description="Torznab only.")
    grabs: int | None


class SearchOut(BaseModel):
    total: int = Field(description="What the indexer reports in total; the results hold at most 50.")
    took_ms: int
    results: list[Release]


class FromSourceIn(BaseModel):
    source_id: int
    radarr_indexer_id: int
    api_key: str = Field(
        default="", max_length=KEY_MAX_LENGTH, description="Radarr never returns the key; type it here. May be empty."
    )
    confirm_empty: bool = Field(default=False, description="Save although the test found nothing.")


# --- Checks -------------------------------------------------------------------------- #


def _clean_name(raw: str) -> str:
    name = nfc(raw).strip()
    if not name or len(name) > NAME_MAX_LENGTH:
        raise error("invalid_input", "The input is not valid.", 422, fields=["name"])
    return name


def _clean_url(raw: str | None, *, add_api_path: bool = True) -> str:
    try:
        return indexers.normalize_url(raw, add_api_path=add_api_path)
    except indexers.IndexerUrlInvalid as exc:
        raise error(
            "indexer_url_invalid",
            "This address does not work. It has to start with http:// or https:// and must contain neither a user "
            "name and password nor the key.",
            422,
        ) from exc


def _clean_key(raw: str | None) -> str:
    key = (raw or "").strip()
    if key and (not key.isascii() or any(character.isspace() or not character.isprintable() for character in key)):
        raise error("invalid_input", "The input is not valid.", 422, fields=["api_key"])
    return key


def _clean_categories(values: list[int] | None, *, required: bool, field: str = "categories") -> list[int]:
    cleaned: list[int] = []
    for value in values or []:
        if not 1 <= value <= CATEGORY_MAX_ID:
            raise error("invalid_input", "The input is not valid.", 422, fields=[field])
        if value not in cleaned:
            cleaned.append(value)
    if required and not cleaned:
        raise error("indexer_no_categories", "Choose at least one category.", 422)
    return cleaned


def _clean_languages(values: list[str] | None) -> list[str] | None:
    """Distinct codes the engine knows, lower case; None when one of them is not such a code."""
    if values is None:
        return None
    codes: list[str] = []
    for value in values:
        code = value.strip().lower()
        if code not in lang.ISO_CODES:
            return None
        if code not in codes:
            codes.append(code)
    return codes


def _clean_settings(payload: IndexerIn | IndexerPatch, kind: str, stored: _Stored | None) -> dict[str, Any]:
    """The search settings to store. For a new indexer the defaults fill in; a saved one keeps what is left out.

    The minimum seeders follow the kind: a Torznab indexer always has a number, a Newznab indexer never.
    """
    given = payload.model_fields_set
    failed: list[str] = []
    values: dict[str, Any] = {}

    if "priority" in given:
        if payload.priority is None or not PRIORITY_MIN <= payload.priority <= PRIORITY_MAX:
            failed.append("priority")
        else:
            values["priority"] = payload.priority
    elif stored is None:
        values["priority"] = PRIORITY_DEFAULT

    if "remove_year" in given:
        if payload.remove_year is None:
            failed.append("remove_year")
        else:
            values["remove_year"] = payload.remove_year
    elif stored is None:
        values["remove_year"] = False

    if "multi_languages" in given:
        codes = _clean_languages(payload.multi_languages)
        if codes is None:
            failed.append("multi_languages")
        else:
            values["multi_languages"] = codes
    elif stored is None:
        values["multi_languages"] = []

    if "daily_limit" in given:
        limit = payload.daily_limit
        if limit is not None and not DAILY_LIMIT_MIN <= limit <= DAILY_LIMIT_MAX:
            failed.append("daily_limit")
        else:
            values["daily_limit"] = limit
    elif stored is None:
        values["daily_limit"] = None

    if kind == "torznab":
        if "minimum_seeders" in given:
            if payload.minimum_seeders is None or not 0 <= payload.minimum_seeders <= SEEDERS_MAX:
                failed.append("minimum_seeders")
            else:
                values["minimum_seeders"] = payload.minimum_seeders
        elif stored is None or stored.kind != "torznab" or stored.minimum_seeders is None:
            values["minimum_seeders"] = SEEDERS_DEFAULT
    elif "minimum_seeders" in given and payload.minimum_seeders is not None:
        failed.append("minimum_seeders")
    elif stored is None or stored.minimum_seeders is not None:
        values["minimum_seeders"] = None

    if failed:
        raise error("invalid_input", "The input is not valid.", 422, fields=failed)
    return values


def _settings_from_radarr(found: RadarrIndexer, kind: str) -> dict[str, Any]:
    """Radarr's search settings as nexcrate stores them.

    A priority outside Radarr's range gets the default. MULTi languages are kept by their ISO code; Radarr's Original
    (-2) and numbers without a code are left out with a log line.
    """
    priority = found.priority
    if priority is None or not PRIORITY_MIN <= priority <= PRIORITY_MAX:
        priority = PRIORITY_DEFAULT
    minimum = None
    if kind == "torznab":
        seeders = found.minimum_seeders
        minimum = seeders if seeders is not None and 0 <= seeders <= SEEDERS_MAX else SEEDERS_DEFAULT
    codes: list[str] = []
    for language_id in found.multi_languages:
        code = lang.iso_of_id(language_id)
        if code is None:
            if language_id == lang.ORIGINAL:
                logger.info(
                    "Radarr indexer %d: the MULTi language Original is left out, nexcrate keeps languages by code",
                    found.id,
                )
            else:
                logger.info(
                    "Radarr indexer %d: the MULTi language %d has no code and is left out", found.id, language_id
                )
            continue
        if code not in codes:
            codes.append(code)
    return {
        "priority": priority,
        "minimum_seeders": minimum,
        "multi_languages": codes,
        "remove_year": found.remove_year,
        "automatic_search": found.automatic_search,
    }


def _key_missing() -> HTTPException:
    return error(
        "indexer_key_missing", "The stored API key of this indexer cannot be read. Please enter it again.", 422
    )


def _categories_empty() -> HTTPException:
    return error(
        "indexer_categories_empty",
        "This indexer returns nothing in the chosen categories right now. Are the categories right? You can save "
        "anyway.",
        409,
    )


# --- Storage ------------------------------------------------------------------------ #


@dataclass(frozen=True)
class _Stored:
    id: int
    kind: str
    url: str
    api_key: str
    categories: list[int]
    caps: dict[str, Any] | None
    caps_checked_at: datetime | None
    paused_until: datetime | None
    minimum_seeders: int | None = None
    series_categories: list[int] | None = None
    #: None: the default from the caps; a list: the owner's choice.
    anime_categories: list[int] | None = None


def _load(indexer_id: int) -> _Stored | None:
    with SessionLocal() as db:
        row = db.get(media.Indexer, indexer_id)
        if row is None:
            return None
        return _Stored(
            id=row.id,
            kind=row.kind,
            url=row.url,
            api_key=row.api_key,
            categories=list(row.categories or []),
            caps=row.caps,
            caps_checked_at=row.caps_checked_at,
            paused_until=row.paused_until,
            minimum_seeders=row.minimum_seeders,
            series_categories=list(row.series_categories or []),
            anime_categories=list(row.anime_categories) if row.anime_categories is not None else None,
        )


def _caps_out(value: dict[str, Any] | None) -> Caps | None:
    if not value:
        return None
    try:
        return Caps.model_validate(value)
    except ValidationError:
        return None


def _tag(indexer_id: int, labels: list[str]) -> None:
    with SessionLocal() as db:
        tags.set_indexer(db, indexer_id, labels)
        db.commit()


def indexer_out(db: Any, row: media.Indexer) -> Indexer:
    from_source = None
    if row.source_id is not None:
        name = db.scalar(select(media.Source.name).where(media.Source.id == row.source_id))
        if name is not None:
            from_source = FromSource(source_id=row.source_id, name=name)
    moment = indexers.now()
    paused = row.paused_until if row.paused_until is not None and row.paused_until > moment else None
    automatic_paused = row.automatic_paused_until
    if automatic_paused is not None and automatic_paused <= moment:
        automatic_paused = None
    usage = None
    if row.limits_seen_at is not None:
        usage = IndexerUsage(
            api_current=row.api_current,
            api_max=row.api_max,
            grab_current=row.grab_current,
            grab_max=row.grab_max,
            api_next_at=row.api_next_at,
            grab_next_at=row.grab_next_at,
            seen_at=row.limits_seen_at,
        )
    return Indexer(
        tags=tags.of_indexers(db, [row.id]).get(row.id, []),
        id=row.id,
        name=row.name,
        kind=row.kind,
        url=row.url,
        has_api_key=bool(row.api_key),
        categories=list(row.categories or []),
        series_categories=list(row.series_categories or []),
        music_categories=(
            list(row.music_categories)
            if row.music_categories is not None
            else album_search.default_music_categories(row.caps)
        ),
        music_categories_chosen=row.music_categories is not None,
        anime_categories=(
            list(row.anime_categories)
            if row.anime_categories is not None
            else indexers.default_anime_categories(row.caps)
        ),
        anime_categories_chosen=row.anime_categories is not None,
        anime_standard_format_search=bool(row.anime_standard_format_search),
        enabled=row.enabled,
        priority=row.priority,
        minimum_seeders=row.minimum_seeders if row.kind == "torznab" else None,
        multi_languages=list(row.multi_languages or []),
        remove_year=bool(row.remove_year),
        caps=_caps_out(row.caps),
        caps_checked_at=row.caps_checked_at,
        paused_until=paused,
        last_error_code=row.last_error_code,
        from_source=from_source,
        daily_limit=row.daily_limit,
        usage=usage,
        escalation_level=row.escalation_level or 0,
        automatic_paused_until=automatic_paused,
        rss=IndexerRss(last_at=row.rss_last_at, newest_at=row.rss_newest_at, gap_at=row.rss_gap_at),
    )


def _out(indexer_id: int) -> Indexer:
    with SessionLocal() as db:
        row = db.get(media.Indexer, indexer_id)
        if row is None:
            raise error("not_found", "This does not exist, or not any more.", 404)
        return indexer_out(db, row)


def _insert(
    *,
    name: str,
    kind: str,
    url: str,
    key: str,
    categories: list[int],
    enabled: bool,
    caps: dict[str, Any] | None,
    settings: dict[str, Any],
    series_categories: list[int] | None = None,
    source_id: int | None = None,
    radarr_indexer_id: int | None = None,
    music_categories: list[int] | None = None,
    anime_categories: list[int] | None = None,
    anime_standard_format_search: bool = True,
) -> int:
    moment = utcnow()
    with SessionLocal() as db:
        row = media.Indexer(
            name=name,
            kind=kind,
            url=url,
            api_key=crypto.encrypt(key) if key else "",
            categories=categories,
            series_categories=(
                series_categories if series_categories is not None else indexers.default_series_categories(caps)
            ),
            music_categories=music_categories,
            anime_categories=anime_categories,
            anime_standard_format_search=anime_standard_format_search,
            enabled=enabled,
            caps=caps,
            # The clock the 7-day check reads.
            caps_checked_at=indexers.now(),
            source_id=source_id,
            radarr_indexer_id=radarr_indexer_id,
            created_at=moment,
            updated_at=moment,
            **settings,
        )
        db.add(row)
        db.commit()
        logger.info("Indexer %d created (%s)", row.id, kind)
        return row.id


def _record(
    indexer_id: int, *, failure: indexers.IndexerError | None, caps: Any = ..., own_settings: bool = True
) -> None:
    """After a request to a saved indexer: the pause of a limit, the error code, fresh caps."""
    with SessionLocal() as db:
        row = db.get(media.Indexer, indexer_id)
        if row is None:
            return
        if failure is not None and failure.paused_until is not None and own_settings:
            row.paused_until = failure.paused_until
        if own_settings:
            row.last_error_code = failure.code if failure is not None else None
        if caps is not ... and own_settings:
            row.caps = caps
            row.caps_checked_at = indexers.now()
        db.commit()


def _decrypted_key(stored: _Stored) -> str:
    if not stored.api_key:
        return ""
    key = crypto.decrypt(stored.api_key)
    if not key:
        raise _key_missing()
    return key


def _anime_empty() -> HTTPException:
    return error(
        "indexer_anime_categories_empty",
        "In the chosen anime categories this indexer delivers nothing right now. Save anyway to keep them.",
        409,
    )


async def _check_anime(target: indexers.Target, categories: list[int], caps: dict[str, Any] | None) -> None:
    """One real request in the anime categories the owner chose; nothing in them is 409 unless he confirms."""
    try:
        found = await indexers.probe_anime(target, categories, caps)
    except indexers.IndexerError as exc:
        logger.info("Indexer anime probe failed: %s", exc.code)
        raise exc.http() from exc
    if found == 0:
        raise _anime_empty()


async def _test(target: indexers.Target, categories: list[int] | None) -> indexers.TestResult:
    try:
        return await indexers.run_test(target, categories)
    except indexers.IndexerError as exc:
        logger.info("Indexer test failed: %s", exc.code)
        raise exc.http() from exc


# --- Routes ---------------------------------------------------------------------------- #


@router.get(
    "",
    response_model=list[Indexer],
    summary="List the indexers",
    description="Every indexer with its caps, search settings and state. The API key is never part of the answer.",
)
def list_indexers(db: DbSession) -> list[Indexer]:
    return [indexer_out(db, row) for row in db.scalars(select(media.Indexer).order_by(media.Indexer.id))]


class IndexerOptions(BaseModel):
    prefer_indexer_flags: bool = Field(
        strict=True,
        description=(
            "Radarr's \"Prefer Indexer Flags\": between movie releases of the same quality, the same custom format "
            "score and the same indexer priority, the one with the better indexer flags comes first (freeleech, double "
            "upload, internal and the two PTP flags count 2, halfleech 1). Off until switched on, as in Radarr. Series "
            "are not concerned: Sonarr has no such setting."
        ),
    )


@router.get(
    "/options",
    response_model=IndexerOptions,
    summary="Read the options that hold for every indexer",
    description="What the owner set about the order of releases, apart from the single indexers.",
)
def read_options(db: DbSession) -> IndexerOptions:
    return IndexerOptions(prefer_indexer_flags=search_options.load_prefer_indexer_flags(db))


@router.put(
    "/options",
    response_model=IndexerOptions,
    summary="Set the options that hold for every indexer",
    description="Holds from the next search on; a search that is already kept keeps its order.",
)
def update_options(payload: IndexerOptions, db: DbSession) -> IndexerOptions:
    search_options.save_prefer_indexer_flags(db, payload.prefer_indexer_flags)
    db.commit()
    logger.info("Preferring indexer flags switched %s", "on" if payload.prefer_indexer_flags else "off")
    return read_options(db)


@router.post(
    "/test",
    response_model=IndexerTestOut,
    summary="Test an indexer",
    description=(
        "Send `kind`, `url` and `api_key`, or `indexer_id` of a saved indexer, whose stored values the given ones "
        "override. Asks `t=caps`, then one `t=movie` (or `t=search` when the caps offer no movie search) with "
        "`limit=1` in the given categories, or the saved ones, or the default ones. An empty feed is no error: "
        "`feed_items` is 0. Nothing is stored except fresh caps and a pause of a saved indexer."
    ),
    responses=error_responses(
        (404, "not_found"), (422, "indexer_url_invalid"), (422, "indexer_key_missing"), *indexers.ERRORS
    ),
)
async def check_indexer(payload: IndexerTestIn) -> IndexerTestOut:
    stored: _Stored | None = None
    own_settings = False
    if payload.indexer_id is not None:
        stored = await asyncio.to_thread(_load, payload.indexer_id)
        if stored is None:
            raise error("not_found", "This does not exist, or not any more.", 404)
        kind = payload.kind or stored.kind
        url = _clean_url(payload.url) if (payload.url or "").strip() else stored.url
        key_given = _clean_key(payload.api_key)
        key = key_given or await asyncio.to_thread(_decrypted_key, stored)
        categories = _clean_categories(payload.categories, required=False) or stored.categories
        series_categories = (
            _clean_categories(payload.series_categories, required=False, field="series_categories")
            if payload.series_categories is not None
            else None
        )
        own_settings = kind == stored.kind and url == stored.url and not key_given
        paused_until = stored.paused_until if url == stored.url and not key_given else None
    else:
        missing = [name for name, value in (("kind", payload.kind), ("url", (payload.url or "").strip())) if not value]
        if missing:
            raise error("invalid_input", "The input is not valid.", 422, fields=missing)
        kind = payload.kind or "newznab"
        url = _clean_url(payload.url)
        key = _clean_key(payload.api_key)
        categories = _clean_categories(payload.categories, required=False)
        series_categories = (
            _clean_categories(payload.series_categories, required=False, field="series_categories")
            if payload.series_categories is not None
            else None
        )
        paused_until = None
    target = indexers.Target(url=url, kind=kind, api_key=key, paused_until=paused_until)
    music_categories = _clean_categories(payload.music_categories, required=False, field="music_categories")
    try:
        # Series are probed only when the dialog sends its series categories (S3); an empty list means off.
        result = await indexers.run_test(
            target,
            categories or None,
            series_categories,
            probe_series=series_categories is not None,
            music_categories=music_categories or None,
        )
    except indexers.IndexerError as exc:
        logger.info("Indexer test failed: %s", exc.code)
        if stored is not None:
            await asyncio.to_thread(_record, stored.id, failure=exc, own_settings=own_settings)
        raise exc.http() from exc
    if stored is not None:
        await asyncio.to_thread(_record, stored.id, failure=None, caps=result.caps, own_settings=own_settings)
    return IndexerTestOut(
        caps=_caps_out(result.caps),
        feed_items=result.feed_items,
        default_categories=indexers.default_categories(result.caps),
        series_feed_items=result.series_feed_items,
        default_series_categories=indexers.default_series_categories(result.caps),
        default_music_categories=album_search.default_music_categories(result.caps),
        music_feed_items=result.music_feed_items,
        default_anime_categories=indexers.default_anime_categories(result.caps),
        url=url,
    )


@router.post(
    "",
    status_code=201,
    response_model=Indexer,
    summary="Add an indexer",
    description=(
        "Tests the indexer first (caps and one feed request in the chosen categories) and stores it with its "
        "API key encrypted. Broken caps are no obstacle: the defaults apply. When the feed request finds "
        "nothing, the answer is 409 `indexer_categories_empty` unless `confirm_empty` is true. The search settings "
        "are checked before any request and get Radarr's defaults when left out."
    ),
    responses=error_responses(
        (422, "indexer_url_invalid"),
        (422, "indexer_no_categories"),
        (409, "indexer_categories_empty"),
        (409, "indexer_anime_categories_empty"),
        *indexers.ERRORS,
    ),
)
async def create_indexer(payload: IndexerIn) -> Indexer:
    name = _clean_name(payload.name)
    url = _clean_url(payload.url)
    key = _clean_key(payload.api_key)
    categories = _clean_categories(payload.categories, required=True)
    series_categories = (
        _clean_categories(payload.series_categories, required=False, field="series_categories")
        if payload.series_categories is not None
        else None
    )
    music_categories = (
        _clean_categories(payload.music_categories, required=False, field="music_categories")
        if payload.music_categories is not None
        else None
    )
    anime_categories = (
        _clean_categories(payload.anime_categories, required=False, field="anime_categories")
        if payload.anime_categories is not None
        else None
    )
    settings = _clean_settings(payload, payload.kind, None)
    target = indexers.Target(url=url, kind=payload.kind, api_key=key)
    result = await _test(target, categories)
    if result.feed_items == 0 and not payload.confirm_empty:
        raise _categories_empty()
    if anime_categories and not payload.confirm_anime_empty:
        await _check_anime(target, anime_categories, result.caps)
    indexer_id = await asyncio.to_thread(
        _insert,
        name=name,
        kind=payload.kind,
        url=url,
        key=key,
        categories=categories,
        enabled=payload.enabled,
        caps=result.caps,
        settings=settings,
        series_categories=series_categories,
        music_categories=music_categories,
        anime_categories=anime_categories,
        anime_standard_format_search=payload.anime_standard_format_search,
    )
    if payload.tags:
        await asyncio.to_thread(_tag, indexer_id, payload.tags)
    return await asyncio.to_thread(_out, indexer_id)


def _apply_patch(indexer_id: int, changes: dict[str, Any], key: str | None, reset_state: bool) -> None:
    with SessionLocal() as db:
        row = db.get(media.Indexer, indexer_id)
        if row is None:
            raise error("not_found", "This does not exist, or not any more.", 404)
        for name, value in changes.items():
            setattr(row, name, value)
        if key:
            row.api_key = crypto.encrypt(key)
        if reset_state:
            row.paused_until = None
            row.last_error_code = None
        row.updated_at = utcnow()
        db.commit()
        logger.info("Indexer %d changed", indexer_id)


@router.patch(
    "/{indexer_id}",
    response_model=Indexer,
    summary="Change an indexer",
    description=(
        "Changes any of the given fields. The kind of a saved indexer cannot change; another kind is refused before "
        "anything is tested or stored. An empty or missing `api_key` keeps the stored key. A new address, "
        "key or new categories are tested before anything is stored; an empty feed answers 409 "
        "`indexer_categories_empty` unless `confirm_empty` is true. The search settings (`priority`, "
        "`minimum_seeders` for Torznab, `multi_languages`, `remove_year`) and `daily_limit` (1 to 100000, or null) "
        "are checked first and send nothing to the indexer; a Newznab indexer has no minimum seeders."
    ),
    responses=error_responses(
        (404, "not_found"),
        (422, "indexer_kind_locked"),
        (422, "indexer_url_invalid"),
        (422, "indexer_no_categories"),
        (422, "indexer_key_missing"),
        (409, "indexer_categories_empty"),
        (409, "indexer_anime_categories_empty"),
        *indexers.ERRORS,
    ),
)
async def update_indexer(indexer_id: int, payload: IndexerPatch) -> Indexer:
    stored = await asyncio.to_thread(_load, indexer_id)
    if stored is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    if payload.kind is not None and payload.kind != stored.kind:
        raise error(
            "indexer_kind_locked",
            "The kind of a saved indexer cannot change. Add a new indexer instead.",
            422,
            fields=["kind"],
        )
    changes: dict[str, Any] = {}
    if payload.name is not None:
        changes["name"] = _clean_name(payload.name)
    if payload.enabled is not None:
        changes["enabled"] = payload.enabled
    kind = stored.kind
    url = _clean_url(payload.url) if payload.url is not None else stored.url
    new_key = _clean_key(payload.api_key)
    categories = (
        _clean_categories(payload.categories, required=True) if payload.categories is not None else stored.categories
    )
    changes.update(_clean_settings(payload, kind, stored))
    if payload.series_categories is not None:
        changes["series_categories"] = _clean_categories(
            payload.series_categories, required=False, field="series_categories"
        )
    if payload.music_categories_default:
        changes["music_categories"] = None
    elif payload.music_categories is not None:
        changes["music_categories"] = _clean_categories(
            payload.music_categories, required=False, field="music_categories"
        )
    if payload.anime_standard_format_search is not None:
        changes["anime_standard_format_search"] = payload.anime_standard_format_search
    if payload.anime_categories_default:
        changes["anime_categories"] = None
    elif payload.anime_categories is not None:
        changes["anime_categories"] = _clean_categories(
            payload.anime_categories, required=False, field="anime_categories"
        )
    connection_changed = url != stored.url or bool(new_key)
    caps = stored.caps
    target: indexers.Target | None = None
    if connection_changed or categories != stored.categories:
        key = new_key or await asyncio.to_thread(_decrypted_key, stored)
        paused_until = stored.paused_until if url == stored.url and not new_key else None
        target = indexers.Target(url=url, kind=kind, api_key=key, paused_until=paused_until)
        result = await _test(target, categories)
        if result.feed_items == 0 and not payload.confirm_empty:
            raise _categories_empty()
        caps = result.caps
        changes.update(
            {
                "url": url,
                "categories": categories,
                "caps": result.caps,
                "caps_checked_at": indexers.now(),
            }
        )
    # Anime categories chosen anew, or the same ones at a new address, are asked once (B4).
    anime = changes.get("anime_categories", stored.anime_categories)
    anime_changed = "anime_categories" in changes and anime != stored.anime_categories
    if anime and (anime_changed or connection_changed) and not payload.confirm_anime_empty:
        if target is None:
            key = await asyncio.to_thread(_decrypted_key, stored)
            target = indexers.Target(url=url, kind=kind, api_key=key, paused_until=stored.paused_until)
        await _check_anime(target, anime, caps)
    await asyncio.to_thread(_apply_patch, indexer_id, changes, new_key or None, connection_changed)
    if payload.tags is not None:
        await asyncio.to_thread(_tag, indexer_id, payload.tags)
    return await asyncio.to_thread(_out, indexer_id)


@router.delete(
    "/{indexer_id}",
    status_code=204,
    response_model=None,
    summary="Delete an indexer",
    description="Removes the indexer and its stored key. The indexer itself is not contacted.",
    responses=error_responses((404, "not_found")),
)
def delete_indexer(indexer_id: int, db: DbSession) -> None:
    row = db.get(media.Indexer, indexer_id)
    if row is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    db.delete(row)
    db.commit()
    logger.info("Indexer %d deleted", indexer_id)


@router.post(
    "/{indexer_id}/search",
    response_model=SearchOut,
    summary="Run a test search",
    description=(
        "One `t=search` with `q` in the indexer's categories, at most 50 releases, no paging. Zero results are no "
        "error. Caps older than 7 days are fetched again first. The answer never carries download links."
    ),
    responses=error_responses((404, "not_found"), (422, "indexer_key_missing"), *indexers.ERRORS),
)
async def search_indexer(indexer_id: int, payload: SearchIn) -> SearchOut:
    query = nfc(payload.q).strip()
    if not query:
        raise error("invalid_input", "The input is not valid.", 422, fields=["q"])
    stored = await asyncio.to_thread(_load, indexer_id)
    if stored is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    key = await asyncio.to_thread(_decrypted_key, stored)
    target = indexers.Target(url=stored.url, kind=stored.kind, api_key=key, paused_until=stored.paused_until)
    stale = stored.caps_checked_at is None or stored.caps_checked_at < indexers.now() - indexers.CAPS_MAX_AGE
    caps = stored.caps
    try:
        async with indexers.IndexerClient(target) as client:
            if stale:
                caps = await client.caps()
            feed = await client.search(query, stored.categories, caps)
            took_ms = client.took_ms
    except indexers.IndexerError as exc:
        logger.info("Test search of indexer %d failed: %s", indexer_id, exc.code)
        await asyncio.to_thread(_record, indexer_id, failure=exc)
        raise exc.http() from exc
    if stale:
        await asyncio.to_thread(_record, indexer_id, failure=None, caps=caps)
    else:
        await asyncio.to_thread(_record, indexer_id, failure=None)
    logger.info("Test search of indexer %d: %d releases in %dms", indexer_id, len(feed.releases), took_ms)
    return SearchOut(
        total=feed.total,
        took_ms=took_ms,
        results=[
            Release(
                title=release.title,
                size_bytes=release.size_bytes,
                published_at=release.published_at,
                categories=release.categories,
                seeders=release.seeders,
                peers=release.peers,
                grabs=release.grabs,
            )
            for release in feed.releases
        ],
    )


@dataclass(frozen=True)
class _SourceRow:
    url: str
    api_key: str
    app: str = "radarr"


def _source(source_id: int) -> _SourceRow | None:
    with SessionLocal() as db:
        row = db.get(media.Source, source_id)
        return _SourceRow(url=row.url, api_key=row.api_key, app=row.app) if row is not None else None


def _add_series_categories(indexer_id: int, categories: list[int], anime: list[int] | None = None) -> None:
    """Sonarr's categories join the series categories, its anime categories the anime ones (A3);
    while those follow the caps, the caps' default is the base."""
    with SessionLocal() as db:
        row = db.get(media.Indexer, indexer_id)
        if row is None:
            return
        merged = list(row.series_categories or [])
        merged.extend(category for category in categories if category not in merged)
        row.series_categories = merged[:CATEGORIES_MAX]
        if anime:
            base = row.anime_categories
            joined = list(base) if base is not None else indexers.default_anime_categories(row.caps)
            joined.extend(category for category in anime if category not in joined)
            row.anime_categories = joined[:CATEGORIES_MAX]
        row.updated_at = utcnow()
        db.commit()


def _add_music_categories(indexer_id: int, categories: list[int]) -> None:
    """Lidarr's categories join the music categories; while those follow the caps, the caps' default is the base."""
    with SessionLocal() as db:
        row = db.get(media.Indexer, indexer_id)
        if row is None:
            return
        base = row.music_categories
        merged = list(base) if base is not None else album_search.default_music_categories(row.caps)
        merged.extend(category for category in categories if category not in merged)
        row.music_categories = merged[:CATEGORIES_MAX]
        row.updated_at = utcnow()
        db.commit()


def _indexer_with_url(url: str) -> int | None:
    with SessionLocal() as db:
        return db.scalar(select(media.Indexer.id).where(media.Indexer.url == url).limit(1))


async def _tag_names(client: Any) -> dict[int, str]:
    """The app's tag names; an app that does not answer for them gives none, the indexer comes without tags."""
    try:
        return await client.tags()
    except RadarrError:
        return {}


@router.post(
    "/from-source",
    status_code=201,
    response_model=Indexer,
    summary="Fetch an indexer from Radarr or Sonarr",
    description=(
        "Reads one Newznab or Torznab indexer of a Radarr source (name, address, categories, enabled, priority, "
        "minimum seeders, MULTi languages, remove year, whether RSS or automatic search is on), tests it with the key "
        "you type, since Radarr never returns it, and stores it. MULTi languages are kept by ISO code; Radarr's "
        "Original is left out. An empty feed answers 409 `indexer_categories_empty` unless `confirm_empty` is true. "
        "An indexer with the same address is refused. From Sonarr (decision 14) its categories "
        "become the series categories, the movie categories are the indexer's defaults, and an indexer with the same "
        "address gets Sonarr's categories added to its series categories instead of a refusal. From Lidarr its "
        "categories without 3020 and 3030 become the music categories and the feed is probed there (`t=music`, or "
        "`t=search`); an indexer "
        "with the same address gets them added to its music categories. A taken-over connection is read for this "
        "all the same: its key stays stored, and only the indexer list is asked."
    ),
    responses=error_responses(
        (404, "not_found"),
        (409, "indexer_exists"),
        (409, "indexer_categories_empty"),
        (409, "source_app_unsupported"),
        (422, "indexer_unsupported"),
        (422, "indexer_url_invalid"),
        (422, "source_key_missing"),
        (422, "source_url_invalid"),
        *RADARR_ERRORS,
        *LIDARR_ERRORS,
        *indexers.ERRORS,
    ),
)
async def create_from_source(payload: FromSourceIn) -> Indexer:
    key = _clean_key(payload.api_key)
    source = await asyncio.to_thread(_source, payload.source_id)
    if source is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    if source.app not in ("radarr", "sonarr", "lidarr"):
        raise error("source_app_unsupported", "This works for Radarr, Sonarr and Lidarr connections only.", 409)
    radarr_key = await asyncio.to_thread(crypto.decrypt, source.api_key)
    if not radarr_key:
        raise error(
            "source_key_missing", "The stored API key of this source cannot be read. Please enter it again.", 422
        )
    try:
        # The app's tag names too: an indexer's tags come along.
        if source.app == "sonarr":
            async with SonarrClient(source.url, radarr_key) as sonarr:
                listed = await sonarr.indexers()
                names = await _tag_names(sonarr)
        elif source.app == "lidarr":
            async with LidarrClient(source.url, radarr_key) as lidarr:
                listed = await lidarr.indexers()
                names = await _tag_names(lidarr)
        else:
            async with RadarrClient(source.url, radarr_key) as radarr:
                listed = await radarr.indexers()
                names = await _tag_names(radarr)
    except RadarrError as exc:
        raise exc.http() from exc
    except SourceUrlInvalid as exc:
        raise error(
            "source_url_invalid",
            "This address does not work. It has to start with http:// or https:// and must not contain "
            "a user name or password.",
            422,
        ) from exc
    found = next((item for item in listed if item.id == payload.radarr_indexer_id), None)
    if found is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    labels = [names[tag_id] for tag_id in found.tags if tag_id in names]
    kind = indexers.kind_of_implementation(found.implementation)
    if kind is None:
        raise error("indexer_unsupported", "This indexer is neither Newznab nor Torznab.", 422)
    # Radarr's endpoint already carries its path as Radarr builds it; an empty apiPath there means the root.
    url = _clean_url(found.endpoint, add_api_path=False)
    existing = await asyncio.to_thread(_indexer_with_url, url)
    categories = [category for category in found.categories if 1 <= category <= CATEGORY_MAX_ID]
    if source.app == "lidarr":
        # Music videos and audiobooks stay out, as in the default from the caps (the owner's answer, 19.09.2026).
        categories = [category for category in categories if category not in album_search.LEFT_OUT_CATEGORIES]
    categories = categories[:CATEGORIES_MAX]
    # Sonarr's anime categories, as Sonarr keeps them per indexer (empty unless the user or Prowlarr filled them).
    anime = [category for category in found.anime_categories if 1 <= category <= CATEGORY_MAX_ID][:CATEGORIES_MAX]
    if existing is not None and source.app == "sonarr" and (categories or anime):
        await asyncio.to_thread(_add_series_categories, existing, categories, anime)
        logger.info("Indexer %d got the series categories of source %d", existing, payload.source_id)
        return await asyncio.to_thread(_out, existing)
    if existing is not None and source.app == "lidarr" and categories:
        await asyncio.to_thread(_add_music_categories, existing, categories)
        logger.info("Indexer %d got the music categories of source %d", existing, payload.source_id)
        return await asyncio.to_thread(_out, existing)
    if existing is not None:
        raise error("indexer_exists", "An indexer with this address exists already.", 409, indexer_id=existing)
    if source.app == "lidarr":
        try:
            result = await indexers.run_test(
                indexers.Target(url=url, kind=kind, api_key=key), None, music_categories=categories or None
            )
        except indexers.IndexerError as exc:
            logger.info("Indexer test failed: %s", exc.code)
            raise exc.http() from exc
        # Lidarr's categories are music categories: the feed that counts is the one there.
        found_items = result.music_feed_items if result.music_feed_items is not None else result.feed_items
        if found_items == 0 and not payload.confirm_empty:
            raise _categories_empty()
        indexer_id = await asyncio.to_thread(
            _insert,
            name=found.name[:NAME_MAX_LENGTH],
            kind=kind,
            url=url,
            key=key,
            categories=result.categories,
            music_categories=categories or None,
            enabled=found.enabled,
            caps=result.caps,
            settings=_settings_from_radarr(found, kind),
            source_id=payload.source_id,
            radarr_indexer_id=found.id,
        )
        logger.info("Indexer %d fetched from Lidarr source %d", indexer_id, payload.source_id)
        await asyncio.to_thread(_tag, indexer_id, labels)
        return await asyncio.to_thread(_out, indexer_id)
    if source.app == "sonarr":
        try:
            result = await indexers.run_test(
                indexers.Target(url=url, kind=kind, api_key=key), None, categories or None, probe_series=True
            )
        except indexers.IndexerError as exc:
            logger.info("Indexer test failed: %s", exc.code)
            raise exc.http() from exc
        if result.feed_items == 0 and not payload.confirm_empty:
            raise _categories_empty()
        indexer_id = await asyncio.to_thread(
            _insert,
            name=found.name[:NAME_MAX_LENGTH],
            kind=kind,
            url=url,
            key=key,
            categories=result.categories,
            series_categories=categories or None,
            anime_categories=anime or None,
            enabled=found.enabled,
            caps=result.caps,
            settings=_settings_from_radarr(found, kind),
            source_id=payload.source_id,
            radarr_indexer_id=found.id,
        )
        logger.info("Indexer %d fetched from Sonarr source %d", indexer_id, payload.source_id)
        await asyncio.to_thread(_tag, indexer_id, labels)
        return await asyncio.to_thread(_out, indexer_id)
    result = await _test(indexers.Target(url=url, kind=kind, api_key=key), categories or None)
    if result.feed_items == 0 and not payload.confirm_empty:
        raise _categories_empty()
    indexer_id = await asyncio.to_thread(
        _insert,
        name=found.name[:NAME_MAX_LENGTH],
        kind=kind,
        url=url,
        key=key,
        categories=result.categories,
        enabled=found.enabled,
        caps=result.caps,
        settings=_settings_from_radarr(found, kind),
        source_id=payload.source_id,
        radarr_indexer_id=found.id,
    )
    logger.info("Indexer %d fetched from source %d", indexer_id, payload.source_id)
    await asyncio.to_thread(_tag, indexer_id, labels)
    return await asyncio.to_thread(_out, indexer_id)
