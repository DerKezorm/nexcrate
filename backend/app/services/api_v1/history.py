"""The history of a title through ``/api/v1`` (N29): loaded, filed, replaced with the old and
the new quality and size, failed, deleted, restored, requested and taken back.

It reads the history the title page shows. What a line tells beyond its kind comes from ``history.data``, which the
writers fill since V3; a line from before has its kind and its time, and the rest is null (rule 5).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ...models import HistoryEntry, Title
from . import KINDS
from . import versions as v1_versions

PAGE_DEFAULT = 100
PAGE_MAX = 500

#: nexcrate's events and what the contract calls them; everything else is ``other`` with the event's own name.
TYPES = {
    "grabbed": "grabbed",
    "imported": "imported",
    "episodes_filed": "imported",
    "album_filed": "imported",
    "failed": "failed",
    "files_deleted": "deleted",
    "file_restored": "restored",
    "requested": "requested",
    "withdrawn": "withdrawn",
    "renamed": "renamed",
    "rename_undone": "renamed",
    "added": "added",
    "taken_over": "taken_over",
    "operated": "operated",
}


def _sum_of(values: list[Any]) -> int | None:
    if not values or any(not isinstance(value, int) for value in values):
        return None
    return sum(values)


def _one_of(values: list[Any]) -> Any:
    """The value when all agree; null when they differ or none is known."""
    known = {value for value in values if value is not None}
    return next(iter(known)) if len(known) == 1 and None not in values else None


def _replaced(files: list[dict[str, Any]]) -> dict[str, Any] | None:
    old = [item for entry in files for item in entry.get("replaced") or []]
    if not old:
        return None
    return {
        "quality": _one_of([item.get("quality") for item in old]),
        "size_bytes": _sum_of([item.get("size_bytes") for item in old]),
    }


def item(entry: HistoryEntry, kind: str, public: dict[int, str]) -> dict[str, Any]:
    data = entry.data if isinstance(entry.data, dict) else {}
    found: dict[str, Any] = {
        "type": TYPES.get(entry.event, "other"),
        "event": entry.event,
        "at": entry.at,
        "version_id": public.get(entry.version_definition_id) if entry.version_definition_id else None,
        "by": data.get("by"),
        "origin": data.get("origin"),
        "download_id": data.get("download_id"),
        "quality": data.get("quality"),
        "size_bytes": data.get("size_bytes"),
        "replaced": data.get("replaced"),
        "count": data.get("count"),
        "code": data.get("code"),
        "detail": data.get("detail"),
        "action": data.get("action"),
    }
    if entry.event == "grabbed" and found["quality"] is None and entry.detail:
        # From before V3 the line's detail is the quality.
        found["quality"] = entry.detail
    files = data.get("files")
    if isinstance(files, list):
        found["quality"] = _one_of([file.get("quality") for file in files])
        found["size_bytes"] = _sum_of([file.get("size_bytes") for file in files])
        found["replaced"] = _replaced(files)
    if kind == "series":
        block: dict[str, Any] = {"episodes": data.get("episodes")}
        if isinstance(files, list):
            block["files"] = files
        found["series"] = block
    if kind == "album" and isinstance(data.get("tracks"), list):
        found["album"] = {"tracks": data["tracks"]}
    return found


def of_title(db: OrmSession, title_id: int, before: int | None, limit: int) -> dict[str, Any] | None:
    """Newest first, in pages: ``next_before`` is the entry to ask before next, null at the end."""
    title = db.get(Title, title_id)
    if title is None or title.kind not in KINDS:
        return None
    size = max(1, min(limit, PAGE_MAX))
    statement = select(HistoryEntry).where(HistoryEntry.title_id == title_id)
    if before is not None:
        statement = statement.where(HistoryEntry.id < before)
    rows = list(db.scalars(statement.order_by(HistoryEntry.id.desc()).limit(size + 1)))
    more = len(rows) > size
    rows = rows[:size]
    public = v1_versions.public_ids(db)
    return {
        "items": [{"entry_id": row.id, **item(row, title.kind, public)} for row in rows],
        "next_before": rows[-1].id if more and rows else None,
    }
