"""Discover's album lists, from ListenBrainz (MetaBrainz, CC0 statistics, no key).

Three lists, measured on 05.10.2026:

* ``classics``: ``/1/stats/sitewide/release-groups?range=all_time``, the albums listened to most ever.
* ``trending``: the same with ``range=week``.
* ``fresh``: ``/1/explore/fresh-releases`` names every release of the last days (60 days: 9,719 albums and EPs,
  8.7 MB), but its ``listen_count`` is 0 for every one of them. So the list keeps those among the week's
  ``TOP_WEEK`` most listened release groups and sorts by the week's listens (60 days gave 59 of them).

⚠️ The statistics name a release group once per release that was listened to: the same album can come two or three
times. Entries are merged by release group, the first (most listened) wins.

ListenBrainz is a way out of the house, so it has a switch of its own (``listenbrainz_enabled``), off from the
factory: off, no request goes out and every list answers ``listenbrainz_disabled``. Answers are kept in memory for
``TTL``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session as OrmSession

from ...db import get_setting, set_setting
from ...meldungen import meldung
from .. import http_log
from ..music import musicbrainz as mb

logger = logging.getLogger("nexcrate.listenbrainz")

API_BASE = "https://api.listenbrainz.org"
SETTING_ENABLED = "listenbrainz_enabled"
LISTS = ("fresh", "trending", "classics")
WANTED = 20
TTL = 6 * 3600.0
TIMEOUT = httpx.Timeout(60.0, connect=10.0)
#: The answer for 60 days of fresh releases measured 8.7 MB.
MAX_BYTES = 32 * 1024 * 1024
FRESH_DAYS = 60
#: How many of the week's most listened release groups ``fresh`` compares against.
TOP_WEEK = 2000
#: How many release groups one statistics page holds at most (ListenBrainz's own limit).
PAGE = 500
#: How many entries of the statistics ``trending`` and ``classics`` read at most to fill up.
TOP_LIST = 1500
FRESH_TYPES = frozenset({"Album", "EP"})

ERRORS = (
    (409, "listenbrainz_disabled"),
    (502, "listenbrainz_unreachable"),
    (502, "listenbrainz_unavailable"),
    (502, "listenbrainz_bad_answer"),
    (504, "listenbrainz_timeout"),
)

clock: Callable[[], float] = time.monotonic


class ListenBrainzError(Exception):
    def __init__(self, detail: dict[str, Any], status: int) -> None:
        super().__init__(detail["code"])
        self.detail = detail
        self.status = status

    @property
    def code(self) -> str:
        return str(self.detail["code"])

    def http(self) -> HTTPException:
        return HTTPException(status_code=self.status, detail=self.detail)


def disabled_error() -> ListenBrainzError:
    return ListenBrainzError(
        meldung("listenbrainz_disabled", "ListenBrainz is switched off under Settings, Online services."), 409
    )


def _unreachable() -> ListenBrainzError:
    return ListenBrainzError(
        meldung(
            "listenbrainz_unreachable", "ListenBrainz cannot be reached. Is this server connected to the internet?"
        ),
        502,
    )


def _unavailable(status: int) -> ListenBrainzError:
    return ListenBrainzError(
        meldung(
            "listenbrainz_unavailable", f"ListenBrainz is not available at the moment (HTTP {status}).", status=status
        ),
        502,
    )


def _bad_answer() -> ListenBrainzError:
    return ListenBrainzError(
        meldung("listenbrainz_bad_answer", "ListenBrainz sent an answer nexcrate cannot read."), 502
    )


def _timed_out() -> ListenBrainzError:
    return ListenBrainzError(meldung("listenbrainz_timeout", "ListenBrainz did not answer in time."), 504)


# --- The switch ------------------------------------------------------------------------------------------------ #


def enabled(db: OrmSession) -> bool:
    return get_setting(db, SETTING_ENABLED, "0") == "1"


def save_enabled(db: OrmSession, on: bool) -> None:
    """Stage the switch; off also forgets every kept answer. The caller commits."""
    set_setting(db, SETTING_ENABLED, "1" if on else "0")
    if not on:
        forget()


# --- Entries --------------------------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Album:
    mbid: str
    title: str
    artist: str
    artist_mbids: tuple[str, ...]
    release_mbid: str | None
    release_date: str | None
    primary_type: str | None
    listens: int | None


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _mbid(value: Any) -> str | None:
    text = _text(value).lower()
    return text if mb.valid_mbid(text) else None


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _artists(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(found for found in (_mbid(item) for item in value) if found)


def _from_stats(item: dict[str, Any]) -> Album | None:
    mbid = _mbid(item.get("release_group_mbid"))
    title = _text(item.get("release_group_name"))
    if mbid is None or not title:
        return None
    return Album(
        mbid=mbid,
        title=title,
        artist=_text(item.get("artist_name")),
        artist_mbids=_artists(item.get("artist_mbids")),
        release_mbid=_mbid(item.get("caa_release_mbid")),
        release_date=None,
        primary_type=None,
        listens=_count(item.get("listen_count")),
    )


def _from_fresh(item: dict[str, Any]) -> Album | None:
    mbid = _mbid(item.get("release_group_mbid"))
    title = _text(item.get("release_name"))
    kind = _text(item.get("release_group_primary_type"))
    if mbid is None or not title or kind not in FRESH_TYPES:
        return None
    date = _text(item.get("release_date"))
    return Album(
        mbid=mbid,
        title=title,
        artist=_text(item.get("artist_credit_name")),
        artist_mbids=_artists(item.get("artist_mbids")),
        release_mbid=_mbid(item.get("caa_release_mbid")) or _mbid(item.get("release_mbid")),
        release_date=date[:10] or None,
        primary_type=kind,
        listens=None,
    )


def merged(albums: list[Album]) -> list[Album]:
    """Each release group once, at its first place."""
    seen: set[str] = set()
    found: list[Album] = []
    for album in albums:
        if album.mbid not in seen:
            seen.add(album.mbid)
            found.append(album)
    return found


def fresh_among(fresh: list[Album], week: list[Album]) -> list[Album]:
    """The fresh albums that are among the week's most listened, sorted by the week's listens."""
    by_mbid = {album.mbid: album for album in fresh}
    chosen: list[Album] = []
    for listened in merged(week):
        album = by_mbid.get(listened.mbid)
        if album is not None:
            chosen.append(
                Album(
                    mbid=album.mbid,
                    title=album.title,
                    artist=album.artist or listened.artist,
                    artist_mbids=album.artist_mbids or listened.artist_mbids,
                    release_mbid=album.release_mbid or listened.release_mbid,
                    release_date=album.release_date,
                    primary_type=album.primary_type,
                    listens=listened.listens,
                )
            )
    return chosen


# --- Requests -------------------------------------------------------------------------------------------------- #


async def _get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    client = http_log.client(
        "listenbrainz", base_url=API_BASE, timeout=TIMEOUT, follow_redirects=False, log_bodies=False
    )
    try:
        response = await http_log.send(
            client, "GET", path, params=params, headers={"User-Agent": mb.USER_AGENT, "Accept": "application/json"}
        )
    except httpx.TimeoutException as exc:
        raise _timed_out() from exc
    except httpx.HTTPError as exc:
        raise _unreachable() from exc
    finally:
        await client.aclose()
    if response.status_code == 204:
        return {}
    if not 200 <= response.status_code < 300:
        raise _unavailable(response.status_code)
    if len(response.content) > MAX_BYTES:
        raise _bad_answer()
    try:
        data = response.json()
    except ValueError as exc:
        raise _bad_answer() from exc
    if not isinstance(data, dict):
        raise _bad_answer()
    payload = data.get("payload")
    return payload if isinstance(payload, dict) else {}


async def _stats(range_: str, limit: int) -> list[Album]:
    found: list[Album] = []
    offset = 0
    while offset < limit:
        payload = await _get("/1/stats/sitewide/release-groups", {"range": range_, "count": PAGE, "offset": offset})
        items = payload.get("release_groups")
        if not isinstance(items, list) or not items:
            break
        found.extend(album for album in (_from_stats(item) for item in items if isinstance(item, dict)) if album)
        if len(items) < PAGE:
            break
        offset += PAGE
    return found


async def _fresh() -> list[Album]:
    payload = await _get("/1/explore/fresh-releases/", {"days": FRESH_DAYS, "past": "true", "future": "false"})
    items = payload.get("releases")
    if not isinstance(items, list):
        raise _bad_answer()
    return [album for album in (_from_fresh(item) for item in items if isinstance(item, dict)) if album]


async def _build(name: str) -> list[Album]:
    if name == "fresh":
        fresh = await _fresh()
        week = await _stats("week", TOP_WEEK)
        return fresh_among(fresh, week)
    return merged(await _stats("week" if name == "trending" else "all_time", TOP_LIST))


# --- The kept answers ------------------------------------------------------------------------------------------ #


_kept: dict[str, tuple[float, list[Album]]] = {}


def forget() -> None:
    _kept.clear()


async def candidates(name: str) -> list[Album]:
    """Every album of a list, best first. Raises ValueError for an unknown list, ``ListenBrainzError``."""
    if name not in LISTS:
        raise ValueError(name)
    hit = _kept.get(name)
    if hit is not None and clock() - hit[0] < TTL:
        return hit[1]
    albums = await _build(name)
    _kept[name] = (clock(), albums)
    logger.info("ListenBrainz list %s read: %d albums", name, len(albums))
    return albums


def choose(albums: list[Album], left_out: set[str]) -> list[Album]:
    """The first ``WANTED`` albums whose release group is not ``left_out``."""
    return [album for album in albums if album.mbid not in left_out][:WANTED]
