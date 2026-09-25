"""Starting an album's planned search and loading what it found (decisions 8 to 11).

The movie's way (``scheduler``) with the album search of M3: the same queue, caps and budget; an album search plans the
requests it really makes on each indexer with music categories (the structured query, else its free-text queries, at
most four; fresh caps one more). After the search, the release the music version would take is loaded through
``loading.grab`` without any confirmation; one that cannot load gives way to the next accepted release of the album
that is better than its files, at most ``scheduler.ATTEMPTS_PER_VERSION``. The summary has the movies' shape.

Log lines carry ids, counts and codes, never names, release titles or links.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from ...db import SessionLocal
from ...models import Indexer, Title
from .. import indexers
from ..downloads import loading
from ..releases import music_qualities as mq
from ..search import album as album_search
from ..search import jobs as search_jobs
from . import album_planning, budget, clock, planning, scheduler, settings, waiting, wishes

logger = logging.getLogger("nexcrate.automatic")

ALBUM_SCOPE = {"kind": "album", "aliases": False}


def _categories(row: Indexer) -> tuple[int, ...]:
    if row.music_categories is not None:
        return tuple(row.music_categories)
    return tuple(album_search.default_music_categories(row.caps))


def planned_cost(row: Indexer, album: album_search.AlbumSearch, now: datetime) -> int:
    """The requests an album search plans on one indexer: the structured query 1, else its free-text queries."""
    stale = row.caps_checked_at is None or row.caps_checked_at < now - indexers.CAPS_MAX_AGE
    if album_search.music_query(row.caps, album) is not None:
        queries = 1
    else:
        queries = len(album_search.text_queries(row.caps, album))
    return max(1, queries) + (1 if stale else 0)


def _wanted(title_id: int, now: datetime) -> planning.Plan | None:
    with SessionLocal() as db:
        fact = album_planning.load_facts(db, [title_id], now).get(title_id)
    return album_planning.title_plan(fact, now) if fact is not None else None


def start_planned(title_id: int, now: datetime, left: int = scheduler.MAX_STARTS_PER_HOUR) -> int:
    """Start a due album's planned search. Returns the starts it counts, 0 when it did not start."""
    plan = _wanted(title_id, now)
    if plan is None or not plan.wanted or plan.next_at is None or plan.next_at > now:
        return 0
    if plan.reason not in planning.SEARCHING_REASONS or left <= 0:
        return 0
    with SessionLocal() as db:
        album = album_search.load(db, title_id)
        if album is None:
            return 0
        rows = [row for row in scheduler._automatic_indexers(db) if _categories(row)]
        candidates = [(row.id, budget.Standing.of(row), planned_cost(row, album, now)) for row in rows]
    if not candidates:
        return 0
    chosen = scheduler.reserve(title_id, candidates, now)
    if chosen is None:
        return 0
    origin = "replacement" if plan.reason == "replacement" else "search"
    try:
        search_id = search_jobs.start_automatic(
            title_id,
            origin=origin,
            indexer_ids=list(chosen),
            reserved=chosen,
            after=scheduler.after_search,
            album_scope=ALBUM_SCOPE,
        )
    except search_jobs.SearchRunning, search_jobs.SearchBusy:
        scheduler.give_back(candidates, chosen, now)
        return 0
    logger.info(
        "Automatic search %s started for album %d (%s) with %d of %d indexers",
        search_id,
        title_id,
        origin,
        len(chosen),
        len(candidates),
    )
    return 1


def search_now(title_id: int, *, switch: bool = True) -> str:
    """The album's automatic search at once, past the budget (decision 11). Raises like ``scheduler.search_now``."""
    now = clock.now()
    with SessionLocal() as db:
        if switch and not settings.load_enabled(db, "album"):
            raise scheduler.AutomaticOff
    plan = _wanted(title_id, now)
    if plan is None or not plan.wanted:
        raise scheduler.NothingWanted
    running = search_jobs.running_search(title_id)
    if running is not None:
        raise search_jobs.SearchRunning(running)
    with SessionLocal() as db:
        usable = scheduler.usable_now(
            [row for row in scheduler._automatic_indexers(db) if _categories(row)], now, switch=switch
        )
    if not usable:
        raise search_jobs.NoIndexers
    search_id = search_jobs.start_automatic(
        title_id,
        origin="search",
        indexer_ids=usable,
        reserved={},
        after=scheduler.after_search,
        album_scope=ALBUM_SCOPE,
        user_invoked=True,
    )
    logger.info("Automatic search %s started at once for album %d with %d indexers", search_id, title_id, len(usable))
    return search_id


# --- After the search ----------------------------------------------------------------------------------------------- #


def candidates(body: dict[str, Any]) -> list[dict[str, Any]]:
    """The releases the music version may take, best first: the one it would take, then every other accepted release
    of this album with a place, not blocked, and (with files) of a better step than the album's."""
    decision = next(iter(body.get("versions") or []), None)
    if decision is None:
        return []
    ours = [
        release
        for release in body.get("releases") or []
        if (release.get("match") or {}).get("kind") == "this"
        and (release.get("verdict") or {}).get("accepted")
        and not release.get("blocklisted")
        and release.get("place") is not None
    ]
    ours.sort(key=lambda release: int(release["place"]))
    would = decision.get("would_take")
    if would is None:
        return []
    first = [release for release in ours if release["release_key"] == would]
    current = body.get("current_step")
    rest = [
        release
        for release in ours
        if release["release_key"] != would
        and (current is None or (release.get("step") in mq.STEPS and mq.rank(release["step"]) < mq.rank(current)))
    ]
    return first + rest


def _load(search: search_jobs.Search, body: dict[str, Any], now: datetime) -> tuple[bool, str | None]:
    decision = next(iter(body.get("versions") or []), None)
    plan = _wanted(search.title_id, now)
    if decision is None or plan is None or not plan.wanted:
        return False, None
    with SessionLocal() as db:
        # A replacement after a failure, and a program's wish, also with the switch off (22.09.2026).
        allowed = search.origin == "replacement" or wishes.wished(db, search.title_id)
        if not settings.load_enabled(db, "album") and not allowed:
            return False, "automatic_off"
    if decision.get("load_block"):
        return False, decision["load_block"]
    code: str | None = None
    attempts = 0
    ranked = candidates(body)
    for place, release in enumerate(ranked):
        if attempts >= scheduler.ATTEMPTS_PER_VERSION:
            break
        if not release.get("can_load"):
            code = release.get("load_block") or code
            continue
        verdict = release.get("verdict") or {}
        until = waiting.gate(
            search,
            release["release_key"],
            int(decision["version_id"]),
            # Music has steps, not a list of qualities: a release the profile takes for good is its best.
            highest_quality=bool(verdict.get("accepted")) and not verdict.get("for_now"),
            score=0,
            now=now,
        )
        if until is not None:
            kept = [waiting.Kept(release_key=item["release_key"], quality=item.get("step")) for item in ranked[place:]]
            waiting.keep(search, int(decision["version_id"]), kept, now)
            return False, waiting.WAITING
        standing = scheduler._standing(int(release["indexer_id"]))
        if standing is None:
            continue
        if budget.grab_stop(standing, now) is not None:
            code = scheduler.GRAB_STOPPED
            continue
        attempts += 1
        try:
            download_id = asyncio.run(
                loading.grab(search.search_id, release["release_key"], int(decision["version_id"]), [])
            )
        except loading.LoadError as exc:
            code = exc.code
            logger.info(
                "Automatic search %s: album %d could not load a release: %s", search.search_id, search.title_id, code
            )
            if code in scheduler.RELEASE_CODES:
                continue
            break
        scheduler._count_grab(int(release["indexer_id"]))
        logger.info("Automatic search %s: album %d loads download %d", search.search_id, search.title_id, download_id)
        return True, None
    return False, code


def _best(body: dict[str, Any]) -> tuple[str | None, list[str]]:
    """The best release of this album and its codes: the one it would take, else the best placed or the first judged."""
    decision = next(iter(body.get("versions") or []), {})
    ours = [release for release in body.get("releases") or [] if (release.get("match") or {}).get("kind") == "this"]
    would = decision.get("would_take")
    for release in ours:
        if would is not None and release["release_key"] == would:
            return release["title"], []
    judged = [release for release in ours if release.get("verdict") is not None]
    if not judged:
        return None, []
    judged.sort(key=lambda release: (0 if release.get("place") is not None else 1, int(release.get("place") or 0)))
    best = judged[0]
    codes = [item["code"] for item in (best["verdict"].get("rejections") or [])]
    if best.get("blocklisted"):
        codes.append("blocklisted")
    if best["verdict"].get("accepted") and decision.get("keeps_current"):
        codes.append("not_better")
    return best["title"], list(dict.fromkeys(codes))


def summary(search: search_jobs.Search, body: dict[str, Any], loaded: bool, code: str | None, now: datetime) -> dict:
    """``search_summary`` in the movies' shape; ``releases`` counts the releases of this album."""
    decision = next(iter(body.get("versions") or []), None)
    ours = [release for release in body.get("releases") or [] if (release.get("match") or {}).get("kind") == "this"]
    versions = []
    if decision is not None and decision.get("has_profile"):
        best, codes = _best(body)
        versions.append(
            {
                "version_id": decision["version_id"],
                "label": decision["label"],
                "best_title": best,
                "codes": codes[: scheduler.SUMMARY_CODES],
                "loaded": loaded,
                "load_code": code if code is not None else (None if loaded else decision.get("load_block")),
            }
        )
    return {
        "at": planning.format_time(now),
        "origin": search.origin,
        "releases": len(ours),
        "indexers": [
            {"id": state["indexer_id"], "name": state["name"], "code": state["error_code"]}
            for state in body.get("indexers") or []
        ],
        "versions": versions,
    }


def prepare(body: dict[str, Any], search_id: str, title_id: int) -> None:
    """What the load step reads beyond the answer: ``can_load`` and ``load_block`` per release, and the step of the
    album's files (``current_step``, None without files)."""
    loading.decorate_album(body, search_jobs.info_hashes(search_id))
    with SessionLocal() as db:
        facts = album_search.load(db, title_id)
        body["current_step"] = facts.current_step if facts is not None and facts.has_file else None


def load_found(search: search_jobs.Search, body: dict[str, Any], now: datetime) -> tuple[bool, str | None]:
    """The load step for a prepared answer, a planned search's or one RSS made (decision 13)."""
    return _load(search, body, now)


def after_search(search: search_jobs.Search, now: datetime) -> None:
    """After an album's automatic search, in its thread (the budget is charged already): load, summarise, plan."""
    body = search_jobs.snapshot(search.search_id)
    if body is None:
        return
    prepare(body, search.search_id, search.title_id)
    loaded, code = _load(search, body, now)
    written = summary(search, body, loaded, code, now)
    with SessionLocal() as db:
        title = db.get(Title, search.title_id)
        if title is None:
            return
        title.last_search_at = now
        # A program's wish is fulfilled with the search, as for movies and series.
        title.search_wish_at = None
        title.search_summary = scheduler.hold_for_grab_limit(
            written, code == scheduler.GRAB_STOPPED and not loaded, body, now
        )
        db.flush()
        planning.replan(db, [title.id], now)
        db.commit()
    logger.info(
        "Automatic search %s of album %d: %d releases of the album, loaded %s",
        search.search_id,
        search.title_id,
        written["releases"],
        loaded,
    )
