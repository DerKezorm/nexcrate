"""Asking before a request (N25): is it there already, when does it come out, would a version be
ready for it, and is there a fitting release at all ("would take", without loading).

A title in the library answers from the library: its state, ``why`` and, when asked, a search of the owner's lane. A
movie in no library comes from TMDB (the owner's token), and its search runs without a row in the database. A series
in no library gets its dates and the readiness of the versions; its search needs its episodes, so it is not possible
before the series is added (``search_not_possible``). Nothing is ever loaded from a preview.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date
from typing import Any

from sqlalchemy import func, select

from ...db import SessionLocal
from ...meldungen import error
from ...models import Indexer, Season, Title, VersionDefinition, utcnow
from .. import calendar_feed, release_calendar, tmdb
from ..automatic import anchors
from ..downloads import loading
from ..music import musicbrainz as mb
from ..search import jobs
from ..search.model import title_info
from ..series import anime, tmdb_series
from . import KINDS, refs, titles, why
from . import versions as v1_versions

_CODE = re.compile(r"^S(\d+)E(\d+)$")


def _pairs(codes: list[str]) -> list[dict[str, int]]:
    found = []
    for code in codes:
        match = _CODE.match(code or "")
        if match:
            found.append({"season": int(match[1]), "episode": int(match[2])})
    return found


def _definitions(kind: str, wanted: list[str] | None) -> list[dict[str, Any]]:
    """The versions of the kind, or the named ones; an unknown id is ``version_unknown``."""
    with SessionLocal() as db:
        listed = v1_versions.listing(db, kind)
        ids = dict(db.execute(select(VersionDefinition.public_id, VersionDefinition.id)).tuples().all())
    by_id = {item["version_id"]: item for item in listed}
    if wanted is None:
        chosen = listed
    else:
        missing = [version_id for version_id in wanted if version_id not in by_id]
        if missing:
            raise error("version_unknown", "A version with this id does not exist.", 422, version_id=missing[0])
        chosen = [by_id[version_id] for version_id in dict.fromkeys(wanted)]
    return [{**item, "definition_id": ids.get(item["version_id"])} for item in chosen]


def _dates(release_dates: object, region: str) -> dict[str, Any]:
    dated = anchors.entries(release_dates)
    found: dict[str, Any] = {}
    for occasion, types in release_calendar.OCCASION_TYPES.items():
        chosen = release_calendar._pick(dated, types, region)
        if chosen is None:
            found[occasion] = None
            continue
        country = None if region and chosen.country == region else chosen.country
        found[occasion] = {"date": chosen.at.date().isoformat(), "country": country}
    return found


def _released(dates: dict[str, Any], today: date) -> bool | None:
    days = [entry["date"] for entry in dates.values() if entry is not None]
    if not days:
        return None
    return min(days) <= today.isoformat()


def _credit(parts: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [
        {
            "ref": f"mbid:{part['mbid']}" if part.get("mbid") else None,
            "name": part.get("artist_name") or part.get("name"),
        }
        for part in parts or []
        if isinstance(part, dict)
    ]


async def _music(kind: str, ref: refs.Ref, raw: str, search: bool,
                 versions: list[dict[str, Any]]) -> dict[str, Any]:  # fmt: skip
    """An album or artist: known or not, and what MusicBrainz says of one nexcrate lacks. An album nexcrate has
    can be searched without loading; one it lacks cannot, as a series it lacks."""

    def known() -> tuple[int | None, dict[str, Any] | None, dict[str, Any] | None]:
        with SessionLocal() as db:
            found = titles.find(db, kind, ref)
            if not found:
                return None, None, None
            explained = why.explain(db, found[0]) if kind == "album" else None
            return found[0], titles.detail(db, found[0]), explained

    title_id, detail, explained = await asyncio.to_thread(known)
    today = utcnow().date().isoformat()
    answer: dict[str, Any] = {
        "kind": kind,
        "ref": raw,
        "known": title_id is not None,
        "title": detail,
        "why": explained,
        "name": detail["name"] if detail else None,
        "year": detail["year"] if detail else None,
        "released": None,
        "versions": [
            {"version_id": item["version_id"], "ready": item["ready"], "reasons": item["reasons"]} for item in versions
        ],
        "search": None,
    }
    try:
        if kind == "album":
            if detail is not None:
                block = detail["album"]
                answer["album"] = {
                    "artists": block["artists"],
                    "type": block["type"],
                    "first_release_date": block["first_release_date"],
                }
            else:
                group = await mb.lookup_release_group(ref.value, mb.OWNER)
                year = group.first_release_date[:4] if group.first_release_date else ""
                answer.update(name=group.title or None, year=int(year) if year.isdigit() else None)
                answer["album"] = {
                    "artists": _credit(group.credit),
                    "type": group.primary_type,
                    "first_release_date": group.first_release_date,
                }
            first = answer["album"]["first_release_date"]
            answer["released"] = first <= today if first else None
        elif detail is not None:
            block = detail["artist"]
            answer["artist"] = {"type": block["type"], "country": block["country"],
                                "disambiguation": block["disambiguation"]}  # fmt: skip
        else:
            artist = await mb.lookup_artist(ref.value, mb.OWNER)
            answer.update(name=artist.name or None, year=artist.begin_year)
            answer["artist"] = {"type": artist.artist_type, "country": artist.country,
                                "disambiguation": artist.disambiguation}  # fmt: skip
    except mb.MusicBrainzError as exc:
        raise exc.http() from exc
    if search:
        answer["search"] = await _start_album(title_id if kind == "album" else None)
    return answer


async def _start_album(title_id: int | None) -> dict[str, Any]:
    def begin() -> dict[str, Any]:
        if title_id is None:
            return {"preview_id": None, "state": "not_possible", "problem": "search_not_possible"}
        with SessionLocal() as db:
            has_indexers = db.scalar(select(Indexer.id).where(Indexer.enabled.is_(True)).limit(1)) is not None
            title = db.get(Title, title_id)
            if title is None or title.artist_id is None:
                return {"preview_id": None, "state": "not_possible", "problem": "search_not_possible"}
        try:
            scope = {"kind": "album", "aliases": False}
            search_id = jobs.start(title_id, has_indexers=has_indexers, album_scope=scope)
        except jobs.SearchRunning as exc:
            search_id = exc.search_id
        except jobs.NoIndexers:
            return {"preview_id": None, "state": "not_possible", "problem": "no_indexers"}
        except jobs.SearchBusy:
            raise error("search_busy", "Too many searches are running at the same time.", 409) from None
        return {"preview_id": search_id, "state": "running", "problem": None}

    return await asyncio.to_thread(begin)


async def ask(kind: str, raw: str, wanted: list[str] | None, search: bool, season: int | None) -> dict[str, Any]:
    if kind not in KINDS:
        raise error("kind_unsupported", f"This nexcrate does not answer for the kind {kind}.", 422, kind=kind)
    try:
        ref = refs.parse(kind, raw)
    except refs.RefError as problem:
        raise error(problem.code, "A reference looks like tmdb:603.", 422, kind=kind) from None
    if season is not None and kind != "series":
        raise error("scope_not_for_kind", "A movie has no seasons.", 422, kind=kind)
    if kind == "artist" and wanted:
        raise error("scope_not_for_kind", "An artist has no versions.", 422, kind=kind)
    versions = await asyncio.to_thread(_definitions, kind, wanted) if kind != "artist" else []
    if kind in ("album", "artist"):
        return await _music(kind, ref, raw, search, versions)

    def known_title() -> tuple[int | None, dict[str, Any] | None, dict[str, Any] | None, str]:
        with SessionLocal() as db:
            found = titles.find(db, kind, ref)
            if len(found) > 1:
                raise error("ref_ambiguous", "Several titles carry this reference; ask by tmdb.", 409)
            region = calendar_feed.region(db)
            if not found:
                return None, None, None, region
            return found[0], titles.detail(db, found[0]), why.explain(db, found[0]), region

    title_id, detail, explained, region = await asyncio.to_thread(known_title)
    answer: dict[str, Any] = {
        "kind": kind,
        "ref": raw,
        "known": title_id is not None,
        "title": detail,
        "why": explained,
        "name": detail["name"] if detail else None,
        "year": detail["year"] if detail else None,
        "released": None,
        "versions": [
            {"version_id": item["version_id"], "ready": item["ready"], "reasons": item["reasons"]} for item in versions
        ],
        "search": None,
    }
    movie: tmdb.MovieData | None = None
    if title_id is not None:
        with SessionLocal() as db:
            row = db.get(Title, title_id)
            stored_dates = row.release_dates if row is not None else None
            seasons = db.scalar(
                select(func.count()).select_from(Season).where(Season.title_id == title_id, Season.number > 0)
            )
            first_air = row.first_air_date if row is not None else None
            series_facts = {
                "status": row.series_status if row is not None else None,
                "first_air_date": first_air,
                "next_air_date": row.next_air_date if row is not None else None,
                "seasons": int(seasons or 0),
            }
        if kind == "movie":
            dates = _dates(stored_dates, region)
            answer["movie"] = {"dates": dates}
            answer["released"] = _released(dates, utcnow().date())
        else:
            answer["series"] = series_facts
            answer["released"] = first_air <= utcnow().date().isoformat() if first_air else None
    else:
        if ref.source != "tmdb":
            raise error("ref_not_addable", "nexcrate learns about a title it does not have only by its TMDB number.",
                        422, kind=kind)  # fmt: skip
        tmdb_id = int(ref.value)
        token = await asyncio.to_thread(tmdb.require_token)
        locale = await asyncio.to_thread(tmdb.account_locale)
        if kind == "movie":
            movie = await tmdb.fetch_movie(token, tmdb_id, locale)
            dates = _dates(movie.release_dates, region)
            answer.update(name=movie.title or None, year=movie.year, released=_released(dates, utcnow().date()))
            answer["movie"] = {"dates": dates}
        else:
            series = await tmdb_series.fetch_series(token, tmdb_id, locale)
            answer.update(name=series.title or None, year=series.year)
            answer["series"] = {
                "status": series.status,
                "first_air_date": series.first_air_date,
                "next_air_date": series.next_air_date,
                "seasons": len([item for item in series.seasons if item.number > 0]),
            }
            answer["released"] = (
                series.first_air_date <= utcnow().date().isoformat() if series.first_air_date else None
            )
    if kind == "series" and "series" not in answer:
        answer["series"] = None
    if kind == "movie" and "movie" not in answer:
        answer["movie"] = None
    if search:
        answer["search"] = await _start(kind, title_id, movie, versions, season)
    return answer


async def _start(
    kind: str,
    title_id: int | None,
    movie: tmdb.MovieData | None,
    versions: list[dict[str, Any]],
    season: int | None,
) -> dict[str, Any]:
    def begin() -> dict[str, Any]:
        with SessionLocal() as db:
            has_indexers = db.scalar(select(Indexer.id).where(Indexer.enabled.is_(True)).limit(1)) is not None
        try:
            if title_id is not None:
                with SessionLocal() as db:
                    row = db.get(Title, title_id)
                    if row is not None and anime.not_searched(row.kind, row.series_type):
                        return {"preview_id": None, "state": "not_possible", "problem": "anime_not_supported"}
                scope = None
                if kind == "series":
                    scope = {"kind": "season", "season": season} if season is not None else {"kind": "series"}
                search_id = jobs.start(title_id, has_indexers=has_indexers, scope=scope)
            elif movie is not None:
                info = title_info(
                    title_id=-movie.tmdb_id,
                    title=movie.title,
                    original_title=movie.original_title,
                    year=movie.year,
                    tmdb_id=movie.tmdb_id,
                    imdb_id=movie.imdb_id,
                    original_language=movie.original_language,
                    runtime_min=movie.runtime,
                    alternative_titles=movie.other_titles,
                )
                wanted = [item["definition_id"] for item in versions if item["definition_id"] is not None]
                search_id = jobs.start_preview(jobs.load_preview(info, wanted))
            else:
                return {"preview_id": None, "state": "not_possible", "problem": "search_not_possible"}
        except jobs.SearchRunning as exc:
            search_id = exc.search_id
        except jobs.NoIndexers:
            return {"preview_id": None, "state": "not_possible", "problem": "no_indexers"}
        except jobs.SearchBusy:
            raise error("search_busy", "Too many searches are running at the same time.", 409) from None
        return {"preview_id": search_id, "state": "running", "problem": None}

    return await asyncio.to_thread(begin)


def result(preview_id: str) -> dict[str, Any]:
    """What a preview's search found, per version: what nexcrate would take, or why nothing."""
    found = jobs.snapshot(preview_id) if len(preview_id) <= 64 else None
    if found is None:
        raise error("preview_not_found", "This preview does not exist, or not any more.", 404)
    with SessionLocal() as db:
        public = v1_versions.public_ids(db)
    series = found.get("kind") == "series"
    if found.get("kind") == "album":
        loading.decorate_album(found, jobs.info_hashes(preview_id))
    elif found["title_id"] > 0:
        if series:
            loading.decorate_series(found, jobs.info_hashes(preview_id))
        else:
            loading.decorate(found, jobs.info_hashes(preview_id))
    releases = {release["release_key"]: release for release in found.get("releases") or []}

    def release_out(key: str | None) -> dict[str, Any] | None:
        release = releases.get(key or "")
        if release is None:
            return None
        parsed = release.get("parsed") or release.get("parsed_series") or {}
        return {
            "name": release["title"],
            # An album's release names its step instead of a quality.
            "quality": parsed.get("quality") or release.get("step"),
            "size_bytes": release.get("size_bytes"),
            "indexer": release.get("indexer"),
            "protocol": release.get("protocol"),
            "age_hours": release.get("age_hours"),
        }

    versions = []
    for entry in found.get("versions") or []:
        item: dict[str, Any] = {
            "version_id": public.get(entry["version_id"]),
            "has_profile": bool(entry.get("has_profile")),
            "keeps_current": bool(entry.get("keeps_current")),
            "codes": [
                str(code.get("code") if isinstance(code, dict) else code) for code in entry.get("nothing_fits") or []
            ],
        }
        if series:
            item["series"] = {
                "takes": [
                    {
                        "release": release_out(take["release_key"]),
                        "fills": _pairs(take.get("fills") or []),
                        "replaces": _pairs(take.get("replaces") or []),
                    }
                    for take in entry.get("takes") or []
                ],
                "not_found": _pairs(entry.get("not_found") or []),
                "no_fit": _pairs(entry.get("no_fit") or []),
            }
            item["would_take"] = None
        else:
            item["would_take"] = release_out(entry.get("would_take"))
        versions.append(item)
    return {
        "preview_id": preview_id,
        "state": found["state"],
        "releases": sum(1 for release in releases.values() if release.get("belongs")),
        "indexers": [
            {"name": state["name"], "state": state["state"], "error_code": state["error_code"]}
            for state in found.get("indexers") or []
        ],
        "versions": versions,
    }
