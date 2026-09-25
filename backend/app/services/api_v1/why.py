"""Why a title is not (all) there yet (N28): the biggest visible gain over Radarr and Sonarr.

It says per version one reason, the weightiest first, as a code with params, so a program builds its sentence from
the code and never guesses from one: "comes out digitally on …", "next search tomorrow", "so far only releases the
profile refuses". Everything comes from what the title page shows in its box "Automatic search"
(``automatic.planning.search_plan``), the readiness of V1 and the queue; nothing is asked of an indexer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from ...models import Artist, Download, PendingRelease, Title, Version, utcnow
from ...models.downloads import ACTIVE_STATES
from ..automatic import planning
from ..downloads import store as download_store
from ..series import anime
from . import KINDS, refs
from . import versions as v1_versions

BATCH_MAX = 50


def _time(value: Any) -> Any:
    """The summary keeps times as text; a program gets them as they are, or null."""
    return value if value else None


def _version_result(entry: dict[str, Any], public: dict[int, str], releases: Any) -> dict[str, Any]:
    return {
        "version_id": public.get(entry.get("version_id")),
        "releases": releases if isinstance(releases, int) else None,
        "best": entry.get("best_title"),
        "codes": list(entry.get("codes") or []),
        "loaded": bool(entry.get("loaded")),
        "load_code": entry.get("load_code"),
    }


def _because(
    version: Version,
    ready: dict[str, Any] | None,
    download: Download | None,
    problem: dict[str, Any] | None,
    pending: tuple[datetime, str] | None,
    held: dict[str, Any] | None,
    plan: dict[str, Any],
    result: dict[str, Any] | None,
    wish: bool,
    today: str,
    frozen: bool = False,
    wish_at: datetime | None = None,
    running: bool = False,
    anime_waits: bool = False,
) -> dict[str, Any]:
    if download is not None and problem is not None and problem["needs_owner"]:
        return {"code": "problem", "params": {"code": problem["code"], "download_id": download.id}}
    if download is not None and download.state in ACTIVE_STATES:
        return {
            "code": "downloading",
            "params": {
                "download_id": download.id,
                "state": download.state,
                "progress": download.progress,
                "remaining_seconds": download.remaining_seconds,
            },
        }
    if version.state == "available":
        return {"code": "available", "params": {}}
    if anime_waits:
        # nexcrate does not search anime yet: nothing below would ever come.
        return {"code": "anime_not_supported", "params": {}}
    if version.state == "upgrade":
        return {"code": "upgrade_possible", "params": {"quality": version.quality}}
    if not version.monitored:
        return {"code": "unmonitored", "params": {}}
    if frozen and not wish:
        # The album's artist is frozen, as unmonitored in Lidarr (fork 3).
        return {"code": "artist_frozen", "params": {}}
    if ready is not None and not ready["ready"]:
        reasons = [reason["code"] for reason in ready["reasons"]]
        # A program's search wish searches with the automatic off (V2, answer 3): that reason alone holds nothing.
        if not (wish and reasons == ["automatic_off"]):
            return {"code": "version_not_ready", "params": {"reasons": ready["reasons"]}}
    if wish:
        # A program's wish searches at once (22.09.2026): ``running`` while its search runs, else it starts within
        # seconds (at most five wishes search at a time).
        return {"code": "wish_searching", "params": {"since": wish_at, "running": running}}
    if held is not None:
        return {"code": "held", "params": {"reason": held["reason"], "count": held["count"]}}
    if pending is not None:
        return {"code": "waiting_delay", "params": {"until": pending[0], "release": pending[1]}}
    anchor = plan.get("anchor") or {}
    if plan.get("reason") == "anchor" and anchor.get("date") and anchor["date"] > today:
        return {"code": "not_released", "params": {"date": anchor["date"], "kind": anchor.get("kind")}}
    if plan.get("reason") == "no_date":
        return {"code": "no_date", "params": {}}
    if result is not None:
        if result["releases"] == 0:
            return {"code": "not_found", "params": {"at": plan.get("last_at")}}
        if not result["loaded"] and (result["codes"] or result["best"] is not None):
            return {
                "code": "no_fitting_release",
                "params": {
                    "releases": result["releases"],
                    "codes": result["codes"],
                    "best": result["best"],
                    "at": plan.get("last_at"),
                },
            }
    if plan.get("reason") == "limit":
        return {"code": "search_limit", "params": {"next_at": plan.get("next_at")}}
    return {"code": "searching_soon", "params": {"next_at": plan.get("next_at")}}


def explain(db: OrmSession, title_id: int, now: datetime | None = None) -> dict[str, Any] | None:
    moment = now or utcnow()
    title = db.get(Title, title_id)
    if title is None or title.kind not in KINDS:
        return None
    plan = planning.search_plan(db, title, moment)
    public = v1_versions.public_ids(db)
    readiness = {item["version_id"]: item for item in v1_versions.listing(db, title.kind)}
    versions = list(
        db.scalars(select(Version).where(Version.title_id == title.id).order_by(Version.version_definition_id))
    )
    downloads: dict[int, Download] = {}
    for row in db.scalars(
        select(Download)
        .where(Download.title_id == title.id, Download.state.in_(download_store.UNFINISHED_STATES))
        .order_by(Download.grabbed_at.desc(), Download.id.desc())
    ):
        if row.version_definition_id is not None:
            downloads.setdefault(row.version_definition_id, row)
    pending = {
        definition_id: (due, release)
        for definition_id, due, release in db.execute(
            select(PendingRelease.version_definition_id, func.min(PendingRelease.due_at), PendingRelease.release_title)
            .where(PendingRelease.title_id == title.id)
            .group_by(PendingRelease.version_definition_id)
        ).tuples()
    }
    summary = plan.get("summary") or {}
    results = {
        entry.get("version_id"): _version_result(entry, public, summary.get("releases"))
        for entry in summary.get("versions") or []
        if isinstance(entry, dict)
    }
    held = {entry["version_id"]: entry for entry in plan.get("held") or []}
    wish = title.search_wish_at is not None
    from ..search import jobs as search_jobs

    running = wish and search_jobs.running_search(title.id) is not None
    artist = db.get(Artist, title.artist_id) if title.kind == "album" and title.artist_id is not None else None
    frozen = artist is not None and artist.frozen_at is not None
    today = moment.date().isoformat()
    items = []
    for version in versions:
        version_id = public.get(version.version_definition_id)
        download = downloads.get(version.version_definition_id)
        problem = None
        if download is not None:
            problem = download_store.problem_of(download, None)
        items.append(
            {
                "version_id": version_id,
                "state": version.state,
                "monitored": bool(version.monitored),
                "because": _because(
                    version,
                    readiness.get(version_id),
                    download,
                    problem,
                    pending.get(version.version_definition_id),
                    held.get(version.version_definition_id),
                    plan,
                    results.get(version.version_definition_id),
                    wish,
                    today,
                    frozen,
                    title.search_wish_at,
                    running,
                    anime.not_searched(title.kind, title.series_type),
                ),
                "last_search": (
                    {**results[version.version_definition_id], "at": _time(summary.get("at"))}
                    if version.version_definition_id in results
                    else None
                ),
            }
        )
    anchor = plan.get("anchor") or {}
    answer: dict[str, Any] = {
        "title": {"kind": title.kind, "ref": refs.primary(title), "name": title.title or None},
        "automatic": bool(plan.get("automatic")),
        "search_wish": wish,
        "last_search_at": plan.get("last_at"),
        "next_search_at": plan.get("next_at"),
        "next_search_reason": plan.get("reason"),
        "release": (
            {"date": anchor.get("date"), "kind": anchor.get("kind"), "country": anchor.get("country")}
            if title.kind == "movie"
            else None
        ),
        "versions": items,
    }
    if title.kind == "series":
        answer["series"] = {"seasons": [_season(item, public) for item in plan.get("seasons") or []]}
    if title.kind == "album":
        answer["album"] = {
            "first_release_date": title.first_release_date,
            "release_date": anchor.get("date"),
            "artist_frozen": frozen,
        }
    return answer


def _season(item: dict[str, Any], public: dict[int, str]) -> dict[str, Any]:
    result = item.get("result")
    last = None
    if isinstance(result, dict):
        last = {
            "at": _time(result.get("at")),
            "versions": [
                {
                    "version_id": public.get(entry.get("version_id")),
                    "loaded": int(entry.get("loaded") or 0),
                    "not_found": int(entry.get("not_found") or 0),
                    "no_fit": int(entry.get("no_fit") or 0),
                    "codes": list(entry.get("codes") or []),
                    "pack_only": entry.get("pack_only"),
                    "load_code": entry.get("load_code"),
                }
                for entry in result.get("versions") or []
                if isinstance(entry, dict)
            ],
        }
    return {
        "season": item["season"],
        "next_search_at": item.get("next_at"),
        "next_search_reason": item.get("reason"),
        "missing": item.get("missing", 0),
        "upgrades": item.get("upgrades", 0),
        "waiting": item.get("waiting", 0),
        "no_date": item.get("no_date", 0),
        "last_search_at": item.get("last_at"),
        "last_search": last,
    }
