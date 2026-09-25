"""What is wrong with this nexcrate, as findings with a code (N35), and the free space behind
every version (N16).

Everything here is read from what nexcrate already knows: no request goes out, and nothing is written to a media
disk, so a program may ask every minute without waking a disk or an indexer.

No finding and no storage entry names a path of the disk.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ... import db as database
from ...models import DownloadClient, Indexer, Title, Version, VersionDefinition
from .. import folders, tmdb
from ..automatic import settings as automatic_settings
from . import TITLE_KINDS
from . import versions as v1_versions

LEVELS = ("error", "warning", "notice")
#: Below this a disk counts as full: no import of a movie fits any more.
DISK_FULL_BYTES = 1024 * 1024 * 1024


def _finding(code: str, level: str, message: str, **params: Any) -> dict[str, Any]:
    return {"code": code, "level": level, "message": message, "params": params}


def _folder_state(raw: str) -> tuple[str | None, folders.Space | None, int | None]:
    """Problem code, free space and the device of a version folder."""
    path = Path(raw)
    try:
        if not path.is_dir():
            return "folder_missing", None, None
        device = int(path.stat().st_dev)
    except OSError:
        return "folder_missing", None, None
    # ⚠️ Asked, not tried: writing a test file would wake the disk with every look.
    if not os.access(path, os.W_OK | os.X_OK):
        return "folder_not_writable", folders.space(path), device
    return None, folders.space(path), device


def storage(db: OrmSession) -> list[dict[str, Any]]:
    """Free and total space behind the folder of every version. ``volume`` groups versions that share a disk, so a
    program does not count the same free space twice."""
    v1_versions.ensure_public_ids(db)
    rows = list(
        db.scalars(select(VersionDefinition).where(VersionDefinition.kind.in_(TITLE_KINDS)).order_by(VersionDefinition.id))
    )
    volumes: dict[int, str] = {}
    result = []
    for row in rows:
        problem, space, device = _folder_state(row.folder) if row.folder else (None, None, None)
        volume = None
        if device is not None:
            volume = volumes.setdefault(device, f"volume-{len(volumes) + 1}")
        known = space is not None and space.total_bytes > 0
        result.append(
            {
                "version_id": row.public_id,
                "kind": row.kind,
                "volume": volume,
                "free_bytes": space.free_bytes if known and space is not None else None,
                "total_bytes": space.total_bytes if known and space is not None else None,
                "problem": problem,
            }
        )
    return result


def findings(db: OrmSession) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    indexers = list(db.execute(select(Indexer.name, Indexer.enabled, Indexer.last_error_code)).tuples())
    if not any(enabled for _name, enabled, _error in indexers):
        found.append(_finding("indexer_none", "error", "No indexer is connected and switched on."))
    for name, enabled, problem in indexers:
        if enabled and problem:
            found.append(
                _finding(
                    "indexer_failing", "warning", f"The indexer {name} failed last time.", name=name, error=problem
                )
            )

    clients = list(
        db.execute(select(DownloadClient.name, DownloadClient.enabled, DownloadClient.last_error_code)).tuples()
    )
    if not any(enabled for _name, enabled, _error in clients):
        found.append(_finding("download_client_none", "error", "No download client is connected and switched on."))
    for name, enabled, problem in clients:
        if enabled and problem:
            found.append(
                _finding(
                    "download_client_failing",
                    "error",
                    f"The download client {name} failed last time.",
                    name=name,
                    error=problem,
                )
            )

    if not database.get_setting(db, tmdb.SETTING_TOKEN):
        found.append(_finding("tmdb_token_missing", "error", "No TMDB token is stored; titles cannot be added."))

    automatic = automatic_settings.load_kinds(db)
    # Music without an album is no finding: nobody who has no music needs to switch it on. A music version alone
    # loads nothing either (Nexview's round of 24.09.2026).
    has_music = (
        db.scalar(select(Version.id).join(Title, Title.id == Version.title_id).where(Title.kind == "album").limit(1))
        is not None
    )
    for kind in TITLE_KINDS:
        if kind not in automatic and (kind != "album" or has_music):
            named = "music" if kind == "album" else kind
            found.append(
                _finding(
                    "automatic_off", "warning", f"The automatic for {named} is off; nothing loads by itself.", kind=kind
                )
            )

    v1_versions.ensure_public_ids(db)
    full: set[int] = set()
    for row in db.scalars(
        select(VersionDefinition).where(VersionDefinition.kind.in_(TITLE_KINDS)).order_by(VersionDefinition.id)
    ):
        if not row.folder:
            continue
        problem, space, device = _folder_state(row.folder)
        if problem is not None:
            sentence = (
                f"The folder of the version {row.label} is missing."
                if problem == "folder_missing"
                else f"The folder of the version {row.label} cannot be written to."
            )
            found.append(_finding(problem, "error", sentence, version_id=row.public_id, name=row.label))
        if space is not None and 0 < space.total_bytes and space.free_bytes < DISK_FULL_BYTES and device not in full:
            if device is not None:
                full.add(device)
            found.append(
                _finding(
                    "disk_full",
                    "error",
                    f"The disk behind the version {row.label} is full.",
                    version_id=row.public_id,
                    name=row.label,
                    free_bytes=space.free_bytes,
                )
            )

    for version in v1_versions.listing(db):
        # The automatic has its own finding above; a version that lacks nothing else is not named again.
        reasons = [reason["code"] for reason in version["reasons"] if reason["code"] != "automatic_off"]
        if reasons:
            found.append(
                _finding(
                    "version_not_ready",
                    "warning",
                    f"The version {version['name']} is not ready: {', '.join(reasons)}.",
                    version_id=version["version_id"],
                    name=version["name"],
                    reasons=reasons,
                )
            )
    order = {level: index for index, level in enumerate(LEVELS)}
    found.sort(key=lambda entry: order[entry["level"]])
    return found
