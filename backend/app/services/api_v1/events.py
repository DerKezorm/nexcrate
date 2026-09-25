"""The event feed of ``/api/v1`` (N30, N31, N33).

A program learns in seconds that something changed, and after an outage it reads on from its last number without a
gap. Every event carries the title by ``kind`` and ``ref``, the fixed ``version_id``, the program's ``origin`` and,
under the key named like the kind, what only the kind has (rule 3).

**Two ways in, both chosen so that no change can slip past:**

1. What the history records (requests, take-backs, files into the bin and back) becomes an event in the **same
   transaction** as its history line: a hook on the insert of a ``HistoryEntry`` writes it on the same connection.
   Every history line is written through the ORM.
2. What is changed past the ORM (downloads, ``update(Download)``) or only exists as a state (the library, readiness,
   health) is compared with a stored print by a job, like the change marker: downloads every three seconds, the
   library in the change marker's own look, versions, health and takeovers once a minute.

Writing an event at every place something happens was turned down: there are more than twenty, and the first one
somebody forgets makes the feed silently incomplete.

⚠️ Progress is no event (N33): percentages and remaining time are in the queue only. Events stay ``KEEP_DAYS``; a
number older than that is ``marker_too_old``.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Connection, delete, event, func, insert, or_, select
from sqlalchemy.orm import Session as OrmSession

from ... import db as database
from ...models import (
    ApiDownloadMark,
    ApiEvent,
    Download,
    DownloadClient,
    DownloadEpisode,
    Episode,
    EpisodeVersion,
    HistoryEntry,
    Source,
    Title,
    Version,
    utcnow,
)
from ..downloads import store as download_store
from ..series import watching
from . import KINDS, refs

logger = logging.getLogger("nexcrate.api_v1")

JOB_NAME = "api_events"
INTERVAL_SECONDS = 3
#: Versions, health and takeovers are compared every this many runs of the job (a minute).
SLOW_EVERY = 20
KEEP_DAYS = 90
PAGE_DEFAULT = 200
PAGE_MAX = 1000

#: The highest number cleared away: a program below it may have missed events.
SETTING_FLOOR = "api_event_floor"
#: The highest download id the job has seen: a higher one is a new download.
SETTING_DOWNLOAD_SEEN = "api_event_download_seen"
#: What versions, health and takeovers looked like at the last slow look (JSON).
SETTING_SNAPSHOT = "api_event_snapshot"

#: History events that become events of the feed, and what they are called there.
FROM_HISTORY = {
    "requested": "request.made",
    "withdrawn": "request.withdrawn",
    "files_deleted": "file.deleted",
    "file_restored": "file.restored",
}
#: Every type the feed can carry, for the documentation and the tests.
TYPES = (
    "title.added",
    "title.changed",
    "title.removed",
    "version.added",
    "version.removed",
    "download.started",
    "download.state",
    "download.imported",
    "download.failed",
    "download.removed",
    "problem.opened",
    "problem.closed",
    "file.deleted",
    "file.restored",
    "request.made",
    "request.withdrawn",
    "series.season_complete",
    "version_definition.added",
    "version_definition.changed",
    "version_definition.removed",
    "health.changed",
    "source.taken_over",
    "source.takeover_undone",
)
#: A download in one of these is done: its mark goes.
_DONE = ("imported", "removed")

_runs = 0


def reset() -> None:
    global _runs
    _runs = 0


# --- Writing ------------------------------------------------------------------------------------------------------ #


def _title_fields(title: Title | None) -> dict[str, Any]:
    if title is None:
        return {"title_id": None, "kind": None, "ref": None, "name": None}
    return {"title_id": title.id, "kind": title.kind, "ref": refs.primary(title), "name": title.title or None}


def record(
    db: OrmSession,
    kind_of_event: str,
    *,
    title: Title | None = None,
    version_definition_id: int | None = None,
    origin: str | None = None,
    download_id: int | None = None,
    block: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    at: datetime | None = None,
    title_fields: dict[str, Any] | None = None,
) -> ApiEvent:
    """Stage one event; the caller commits. ``title_fields`` stands in for a title that is gone."""
    row = ApiEvent(
        type=kind_of_event,
        at=at or utcnow(),
        version_definition_id=version_definition_id,
        origin=origin,
        download_id=download_id,
        block=block,
        params=params or {},
        **(title_fields if title_fields is not None else _title_fields(title)),
    )
    db.add(row)
    return row


def _version_origin(db: OrmSession, title_id: int, definition_id: int | None) -> str | None:
    if definition_id is None:
        return None
    return db.scalar(
        select(Version.origin).where(Version.title_id == title_id, Version.version_definition_id == definition_id)
    )


def _history_by(entry: HistoryEntry) -> tuple[str | None, str | None]:
    """The key's name and the program's origin of a history line: from ``data``, else from the V2 ``detail``."""
    data = entry.data or {}
    if "by" in data or "origin" in data:
        return data.get("by"), data.get("origin")
    detail = entry.detail or ""
    if entry.event in ("requested", "withdrawn"):
        name, _newline, origin = detail.partition("\n")
        return name or None, origin or None
    _count, _space, name = detail.partition(" ")
    return name or None, None


def _history_inserted(_mapper: object, connection: Connection, entry: HistoryEntry) -> None:
    """A new history line of a kind in ``FROM_HISTORY`` becomes an event on the same connection, so in the same
    transaction. Listens on the insert of a history line only: a flush of an import with thousands of new rows costs
    nothing here."""
    if entry.event not in FROM_HISTORY:
        return
    title = connection.execute(
        select(Title.id, Title.kind, Title.tmdb_id, Title.mbid, Title.title, Title.origin).where(
            Title.id == entry.title_id
        )
    ).first()
    if title is None or title.kind not in KINDS:
        return
    data = entry.data or {}
    by, origin = _history_by(entry)
    version_origin = None
    if entry.version_definition_id is not None:
        version_origin = connection.execute(
            select(Version.origin).where(
                Version.title_id == title.id, Version.version_definition_id == entry.version_definition_id
            )
        ).scalar()
    params: dict[str, Any] = {"by": by}
    if entry.event in ("files_deleted", "file_restored"):
        count = data.get("count")
        if count is None:
            head = (entry.detail or "").partition(" ")[0]
            count = int(head) if head.isdigit() else None
        params |= {"count": count, "size_bytes": data.get("size_bytes")}
    block = None
    if title.kind == "series" and data.get("episodes") is not None:
        block = {"series": {"episodes": data["episodes"]}}
    if title.kind == "album" and data.get("tracks") is not None:
        block = {"album": {"tracks": data["tracks"]}}
    connection.execute(
        insert(ApiEvent).values(
            type=FROM_HISTORY[entry.event],
            at=entry.at or utcnow(),
            title_id=title.id,
            kind=title.kind,
            ref=(
                (f"mbid:{title.mbid}" if title.mbid else None)
                if title.kind == "album"
                else (f"tmdb:{title.tmdb_id}" if title.tmdb_id is not None else None)
            ),
            name=title.title or None,
            version_definition_id=entry.version_definition_id,
            origin=origin if origin is not None else version_origin,
            block=block,
            params=params,
        )
    )


def install() -> None:
    """Listen on the insert of history lines; once."""
    if not event.contains(HistoryEntry, "after_insert", _history_inserted):
        event.listen(HistoryEntry, "after_insert", _history_inserted)


# --- The library, from the change marker's look ---------------------------------------------------------------- #


def title_removed(db: OrmSession, kind: str, ref: str, seq: int, at: datetime) -> None:
    record(
        db,
        "title.removed",
        title_fields={"title_id": None, "kind": kind, "ref": ref, "name": None},
        params={"seq": seq},
        at=at,
    )


def title_seen(
    db: OrmSession,
    item: dict[str, Any],
    title_id: int,
    seq: int,
    known_versions: list[str] | None,
    is_new: bool,
    at: datetime,
) -> None:
    """A title the change marker gave a number: added or changed, and the versions that came or went."""
    fields = {"title_id": title_id, "kind": item["kind"], "ref": item["ref"], "name": item["name"]}
    record(
        db,
        "title.added" if is_new else "title.changed",
        title_fields=fields,
        origin=item.get("origin"),
        params={"seq": seq},
        at=at,
    )
    if is_new or known_versions is None:
        return
    now_versions = {version["version_id"]: version for version in item["versions"] if version["version_id"]}
    for version_id in sorted(set(now_versions) - set(known_versions)):
        record(
            db,
            "version.added",
            title_fields=fields,
            origin=now_versions[version_id].get("origin"),
            at=at,
        ).version_id = version_id
    for version_id in sorted(set(known_versions) - set(now_versions)):
        record(db, "version.removed", title_fields=fields, at=at).version_id = version_id


def version_ids(item: dict[str, Any]) -> list[str]:
    return sorted(version["version_id"] for version in item["versions"] if version["version_id"])


# --- Downloads ------------------------------------------------------------------------------------------------------ #


def _episodes_of(db: OrmSession, download_ids: Iterable[int]) -> dict[int, list[tuple[int, int]]]:
    ids = list(download_ids)
    found: dict[int, list[tuple[int, int]]] = defaultdict(list)
    if not ids:
        return found
    for download_id, season, number in db.execute(
        select(DownloadEpisode.download_id, Episode.season_number, Episode.episode_number)
        .join(Episode, Episode.id == DownloadEpisode.episode_id)
        .where(DownloadEpisode.download_id.in_(ids))
        .order_by(Episode.season_number, Episode.episode_number)
    ).tuples():
        found[download_id].append((season, number))
    return found


def series_block(season: int | None, pairs: list[tuple[int, int]]) -> dict[str, Any]:
    seasons = {pair[0] for pair in pairs}
    return {
        "season": season if season is not None else (next(iter(seasons)) if len(seasons) == 1 else None),
        "episodes": [{"season": pair[0], "episode": pair[1]} for pair in pairs],
    }


def _replaced_of(db: OrmSession, row: Download) -> dict[str, Any] | None:
    """What the import wrote into its history line's ``data`` (``replaced``, ``filed``); None without it."""
    for data in db.scalars(
        select(HistoryEntry.data)
        .where(
            HistoryEntry.title_id == row.title_id,
            HistoryEntry.event.in_(("imported", "episodes_filed")),
            HistoryEntry.data.is_not(None),
        )
        .order_by(HistoryEntry.id.desc())
        .limit(20)
    ):
        if isinstance(data, dict) and data.get("download_id") == row.id:
            return data
    return None


def _complete_seasons(db: OrmSession, row: Download) -> list[int]:
    """Seasons of a series download in which every watched, aired episode of its version has a file now, and which
    this download filled at least one gap of."""
    version = db.scalar(
        select(Version).where(
            Version.title_id == row.title_id, Version.version_definition_id == row.version_definition_id
        )
    )
    if version is None:
        return []
    filled = {
        season
        for (season,) in db.execute(
            select(Episode.season_number)
            .join(DownloadEpisode, DownloadEpisode.episode_id == Episode.id)
            .where(
                DownloadEpisode.download_id == row.id,
                DownloadEpisode.action == "fills",
                DownloadEpisode.state == "filed",
            )
        ).tuples()
    }
    if not filled:
        return []
    on = watching.today()
    complete: list[int] = []
    for season in sorted(filled):
        rows = db.execute(
            select(Episode.air_date, EpisodeVersion.episode_file_id)
            .join(EpisodeVersion, EpisodeVersion.episode_id == Episode.id)
            .where(
                Episode.title_id == row.title_id,
                Episode.season_number == season,
                Episode.tmdb_gone_at.is_(None),
                EpisodeVersion.version_id == version.id,
                EpisodeVersion.watched.is_(True),
            )
        ).tuples()
        aired = [file_id for air_date, file_id in rows if watching.aired(air_date, on)]
        if aired and all(file_id is not None for file_id in aired):
            complete.append(season)
    return complete


def _download_event(
    db: OrmSession,
    kind_of_event: str,
    row: Download,
    title: Title,
    pairs: list[tuple[int, int]],
    params: dict[str, Any],
    at: datetime,
) -> None:
    block = {"series": series_block(row.season, pairs)} if title.kind == "series" else None
    record(
        db,
        kind_of_event,
        title=title,
        version_definition_id=row.version_definition_id,
        origin=_version_origin(db, title.id, row.version_definition_id) or title.origin,
        download_id=row.id,
        block=block,
        params=params,
        at=at,
    )


def look_downloads(db: OrmSession, at: datetime | None = None) -> int:
    """Compare downloads with their marks and write what changed. Returns how many events; commits.

    The first look ever only takes the marks: the downloads that exist then are no news.
    """
    now = at or utcnow()
    stored = database.get_setting(db, SETTING_DOWNLOAD_SEEN, "")
    seeding = stored == ""
    seen = int(stored) if stored.isdigit() else 0
    marks = {mark.download_id: mark for mark in db.scalars(select(ApiDownloadMark))}
    condition = Download.id > seen
    if marks:
        condition = or_(condition, Download.id.in_(list(marks)))
    rows = list(db.scalars(select(Download).where(condition).order_by(Download.id)))
    client_codes = {client.id: client.last_error_code for client in db.scalars(select(DownloadClient))}
    titles_by_id = {
        title.id: title
        for title in db.scalars(select(Title).where(Title.id.in_({row.title_id for row in rows})))
    } if rows else {}
    pairs_of = _episodes_of(db, [row.id for row in rows if row.scope in download_store.SERIES_SCOPES])
    written = 0
    present = set()
    for row in rows:
        present.add(row.id)
        seen = max(seen, row.id)
        problem = download_store.problem_of(row, client_codes.get(row.client_id or 0))
        code = problem["code"] if problem is not None else None
        mark = marks.get(row.id)
        finished = row.state in _DONE or (row.state == "failed" and row.cleared_at is not None)
        title = titles_by_id.get(row.title_id)
        if not seeding and title is not None and title.kind in KINDS:
            written += _download_changes(db, row, title, mark, code, problem, pairs_of.get(row.id, []), now)
        if finished:
            if mark is not None:
                db.delete(mark)
            continue
        if mark is None:
            db.add(ApiDownloadMark(download_id=row.id, state=row.state, problem=code))
        else:
            mark.state, mark.problem = row.state, code
    for download_id, mark in marks.items():
        if download_id not in present:
            db.delete(mark)
    database.set_setting(db, SETTING_DOWNLOAD_SEEN, str(seen))
    db.commit()
    return written


def _download_changes(
    db: OrmSession,
    row: Download,
    title: Title,
    mark: ApiDownloadMark | None,
    code: str | None,
    problem: dict[str, Any] | None,
    pairs: list[tuple[int, int]],
    now: datetime,
) -> int:
    written = 0
    before_state = mark.state if mark is not None else None
    before_problem = mark.problem if mark is not None else None
    base = {"quality": row.quality, "size_bytes": row.size_bytes, "release": row.release_title or None}
    if mark is None:
        _download_event(db, "download.started", row, title, pairs, base | {"started_by": row.origin}, now)
        written += 1
        before_state = "queued"
    if row.state != before_state:
        if row.state == "imported":
            data = _replaced_of(db, row) or {}
            params = base | {"replaced": data.get("replaced")}
            if row.scope in download_store.SERIES_SCOPES:
                params |= {"filed": row.filed_count or 0, "missing": row.absent_count or 0}
            _download_event(db, "download.imported", row, title, pairs, params, now)
            written += 1
            if title.kind == "series":
                for season in _complete_seasons(db, row):
                    record(
                        db,
                        "series.season_complete",
                        title=title,
                        version_definition_id=row.version_definition_id,
                        origin=_version_origin(db, title.id, row.version_definition_id) or title.origin,
                        download_id=row.id,
                        block={"series": {"season": season}},
                        at=now,
                    )
                    written += 1
        elif row.state == "failed":
            failure = {"reason": row.failed_reason, "detail": row.failed_detail}
            _download_event(db, "download.failed", row, title, pairs, base | failure, now)
            written += 1
        elif row.state == "removed":
            _download_event(db, "download.removed", row, title, pairs, base, now)
            written += 1
        else:
            _download_event(db, "download.state", row, title, pairs, {"from": before_state, "to": row.state}, now)
            written += 1
    if code != before_problem:
        if before_problem is not None:
            _download_event(db, "problem.closed", row, title, pairs, {"code": before_problem}, now)
            written += 1
        if code is not None and problem is not None:
            params = {"code": code, "needs_owner": problem["needs_owner"], "params": problem["values"]}
            _download_event(db, "problem.opened", row, title, pairs, params, now)
            written += 1
    return written


# --- Versions, health, takeovers, once a minute ------------------------------------------------------------------ #


def _plain(params: dict[str, Any]) -> list[list[Any]]:
    """The params that name something; numbers move all the time (free space) and are no change of health."""
    return sorted(
        [key, value]
        for key, value in params.items()
        if isinstance(value, str | bool) or value is None
    )


def _snapshot(db: OrmSession) -> dict[str, Any]:
    from . import health as v1_health
    from . import versions as v1_versions

    definitions = {
        item["version_id"]: {
            "kind": item["kind"],
            "name": item["name"],
            "order": item["order"],
            "tier": item["tier"],
            "ready": item["ready"],
            "reasons": sorted(reason["code"] for reason in item["reasons"]),
        }
        for item in v1_versions.listing(db)
        if item["version_id"]
    }
    health = sorted(
        [finding["code"], finding["level"], _plain(finding["params"])] for finding in v1_health.findings(db)
    )
    sources = {
        str(source_id): {"app": app, "taken_over": taken is not None}
        for source_id, app, taken in db.execute(select(Source.id, Source.app, Source.taken_over_at)).tuples()
    }
    return {"definitions": definitions, "health": health, "sources": sources}


def look_slow(db: OrmSession, at: datetime | None = None) -> int:
    """Compare versions, health and takeovers with the last look. Returns how many events; commits."""
    now = at or utcnow()
    current = _snapshot(db)
    stored = database.get_setting(db, SETTING_SNAPSHOT, "")
    try:
        before = json.loads(stored) if stored else None
    except ValueError:
        before = None
    written = 0
    if isinstance(before, dict):
        written = _slow_changes(db, before, current, now)
    database.set_setting(db, SETTING_SNAPSHOT, json.dumps(current, sort_keys=True))
    db.commit()
    return written


def _slow_changes(db: OrmSession, before: dict[str, Any], current: dict[str, Any], now: datetime) -> int:
    written = 0
    old_definitions = before.get("definitions") or {}
    for version_id, item in current["definitions"].items():
        old = old_definitions.get(version_id)
        if old == item:
            continue
        if old is None:
            kind_of_event, changed = "version_definition.added", sorted(item)
        else:
            kind_of_event = "version_definition.changed"
            changed = sorted(key for key in item if old.get(key) != item[key])
        params = {"kind": item["kind"], "changed": changed, "ready": item["ready"], "reasons": item["reasons"]}
        record(db, kind_of_event, params=params, at=now).version_id = version_id
        written += 1
    for version_id, old in old_definitions.items():
        if version_id not in current["definitions"]:
            record(db, "version_definition.removed", params={"kind": old.get("kind")}, at=now).version_id = version_id
            written += 1
    if before.get("health") != current["health"]:
        from . import health as v1_health

        record(db, "health.changed", params={"items": v1_health.findings(db)}, at=now)
        written += 1
    old_sources = before.get("sources") or {}
    for source_id, item in current["sources"].items():
        old = old_sources.get(source_id)
        if old is None or old.get("taken_over") == item["taken_over"]:
            continue
        kind_of_event = "source.taken_over" if item["taken_over"] else "source.takeover_undone"
        record(db, kind_of_event, params={"app": item["app"]}, at=now)
        written += 1
    return written


# --- Clearing away ------------------------------------------------------------------------------------------------ #


def _number(db: OrmSession, key: str) -> int:
    stored = database.get_setting(db, key, "0")
    return int(stored) if stored.isdigit() else 0


def clear_old(db: OrmSession, at: datetime | None = None) -> int:
    """Events older than ``KEEP_DAYS`` go; the floor remembers the highest number that went. Commits."""
    limit = (at or utcnow()) - timedelta(days=KEEP_DAYS)
    highest = db.scalar(select(func.max(ApiEvent.seq)).where(ApiEvent.at < limit))
    if highest is None:
        return 0
    database.set_setting(db, SETTING_FLOOR, str(max(int(highest), _number(db, SETTING_FLOOR))))
    result = db.execute(delete(ApiEvent).where(ApiEvent.seq <= highest))
    db.commit()
    return int(result.rowcount or 0)


def run_job() -> None:
    """Downloads every run; versions, health and takeovers, and clearing away, every ``SLOW_EVERY`` runs."""
    global _runs
    slow = _runs % SLOW_EVERY == 0
    _runs += 1
    with database.SessionLocal() as db:
        written = look_downloads(db)
        if slow:
            written += look_slow(db)
            clear_old(db)
    if written:
        logger.debug("The event feed got %d events", written)


# --- Reading ------------------------------------------------------------------------------------------------------ #


class MarkerTooOld(Exception):
    """The number lies below events that were cleared away."""


def latest(db: OrmSession) -> int:
    return int(db.scalar(select(func.max(ApiEvent.seq))) or 0)


def render(rows: list[ApiEvent], public: dict[int, str]) -> list[dict[str, Any]]:
    items = []
    for row in rows:
        version_id = row.version_id or (public.get(row.version_definition_id) if row.version_definition_id else None)
        title = None
        if row.kind is not None:
            title = {"kind": row.kind, "ref": row.ref, "name": row.name}
        item: dict[str, Any] = {
            "seq": row.seq,
            "type": row.type,
            "at": row.at,
            "title": title,
            "version_id": version_id,
            "origin": row.origin,
            "download_id": row.download_id,
            "params": dict(row.params or {}),
        }
        for key, value in (row.block or {}).items():
            item[key] = value
        items.append(item)
    return items


def since(db: OrmSession, after: int, limit: int) -> dict[str, Any]:
    """The events after a number, oldest first. Raises ``MarkerTooOld``."""
    from . import versions as v1_versions

    if 0 < after < _number(db, SETTING_FLOOR):
        raise MarkerTooOld
    size = max(1, min(limit, PAGE_MAX))
    rows = list(db.scalars(select(ApiEvent).where(ApiEvent.seq > after).order_by(ApiEvent.seq).limit(size + 1)))
    more = len(rows) > size
    rows = rows[:size]
    return {
        "items": render(rows, v1_versions.public_ids(db)),
        "next_after": rows[-1].seq if rows else max(after, 0),
        "more": more,
        "latest": latest(db),
    }
