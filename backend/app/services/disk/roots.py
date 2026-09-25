"""The folders the scan walks ("roots", the design notes, L4).

The default folder of every movie version definition, every distinct ``root_folder`` of a version nexcrate owns, and the
folders the owner added on the page. Since S6 (decision 25) the same for series, as roots of
kind ``series``, and since Music M6 (decision 17) for albums, as roots of kind ``album``; a
folder of more than one kind takes the first of movie, series, album. The rows in ``disk_roots`` follow before every
scan: roots from definitions and versions come and go with those; the owner's roots stay until they are removed. Each
root knows the definitions it belongs to: the one whose default folder it is, and those whose own versions have it as
``root_folder``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session as OrmSession

from ...models import DiskFolder, DiskRoot, Version, VersionDefinition
from .. import folders

logger = logging.getLogger("nexcrate.disk")

#: The kinds of a root; a folder of more than one takes the first.
KIND_ORDER = ("movie", "series", "album")


class NotVisible(Exception):
    """The folder is not one nexcrate sees."""


class RootExists(Exception):
    """The owner adds a folder that is a root already."""


class NotRemovable(Exception):
    """The root belongs to a definition or a version; only the owner's own roots can be removed."""


@dataclass
class Derived:
    """The roots definitions and versions give, by resolved path."""

    paths: dict[str, list[int]] = field(default_factory=dict)
    kinds: dict[str, str] = field(default_factory=dict)

    def add(self, path: str, definition_id: int | None, kind: str = "movie") -> None:
        current = self.kinds.get(path)
        if current is None or KIND_ORDER.index(kind) < KIND_ORDER.index(current):
            self.kinds[path] = kind
        definitions = self.paths.setdefault(path, [])
        if definition_id is not None and definition_id not in definitions:
            definitions.append(definition_id)


def _resolved(cache: dict[str, str | None], raw: str | None) -> str | None:
    """A stored folder as its resolved path, or the stored text when it cannot be resolved (it is listed as gone)."""
    if not raw:
        return None
    if raw not in cache:
        found = folders.resolved(raw)
        cache[raw] = str(found) if found is not None and found.is_dir() else raw
    return cache[raw]


def derived(db: OrmSession) -> Derived:
    """The roots definitions and owned versions give, with the definitions each belongs to."""
    cache: dict[str, str | None] = {}
    result = Derived()
    for kind in KIND_ORDER:
        for definition in db.scalars(select(VersionDefinition).where(VersionDefinition.kind == kind)):
            path = _resolved(cache, definition.folder)
            if path is not None:
                result.add(path, definition.id, kind)
        rows = db.execute(
            select(Version.root_folder, Version.version_definition_id)
            .join(VersionDefinition, VersionDefinition.id == Version.version_definition_id)
            .where(Version.source_id.is_(None), Version.root_folder.is_not(None), VersionDefinition.kind == kind)
            .distinct()
        ).all()
        for root_folder, definition_id in rows:
            path = _resolved(cache, root_folder)
            if path is not None:
                result.add(path, definition_id, kind)
    return result


def sync(db: OrmSession) -> list[DiskRoot]:
    """Bring ``disk_roots`` in step with the definitions and versions. Stages the change; the caller commits.

    Returns every root, the owner's included, ordered by path.
    """
    wanted = derived(db)
    existing = {root.path: root for root in db.scalars(select(DiskRoot))}
    for path in wanted.paths:
        if path not in existing:
            row = DiskRoot(path=path, added_by_owner=False, kind=wanted.kinds.get(path, "movie"))
            db.add(row)
            existing[path] = row
        elif existing[path].kind != wanted.kinds.get(path, "movie"):
            # Also for a folder the owner added: a definition now claims it, and its kind decides what is scanned.
            existing[path].kind = wanted.kinds.get(path, "movie")
    gone = [root for path, root in existing.items() if not root.added_by_owner and path not in wanted.paths]
    for root in gone:
        db.delete(root)
        del existing[root.path]
    db.flush()
    if gone:
        logger.info("%d scanned folders left with their definitions or versions", len(gone))
    return sorted(existing.values(), key=lambda root: root.path)


def definitions_of(db: OrmSession, root: DiskRoot, wanted: Derived | None = None) -> list[VersionDefinition]:
    """The version definitions a root belongs to, in their order."""
    wanted = wanted if wanted is not None else derived(db)
    ids = wanted.paths.get(root.path, [])
    if not ids:
        return []
    rows = db.scalars(select(VersionDefinition).where(VersionDefinition.id.in_(ids)).order_by(VersionDefinition.id))
    return list(rows)


def add(db: OrmSession, raw: str, kind: str = "movie") -> DiskRoot:
    """The owner adds a folder of movies or series. Raises ``NotVisible`` or ``RootExists``. Stages; the caller
    commits."""
    try:
        path, _mount = folders.visible(raw)
    except folders.NotVisible as exc:
        raise NotVisible from exc
    sync(db)
    existing = db.scalar(select(DiskRoot).where(DiskRoot.path == str(path)))
    if existing is not None:
        raise RootExists
    row = DiskRoot(path=str(path), added_by_owner=True, kind=kind)
    db.add(row)
    db.flush()
    logger.info("Scanned folder %d added by the owner", row.id)
    return row


def remove(db: OrmSession, root: DiskRoot) -> None:
    """Remove a root the owner added, with its rows. Raises ``NotRemovable``. Stages; the caller commits."""
    if not root.added_by_owner or root.path in derived(db).paths:
        raise NotRemovable
    db.execute(delete(DiskFolder).where(DiskFolder.root_id == root.id))
    db.delete(root)
    db.flush()
    logger.info("Scanned folder %d removed by the owner", root.id)


def visible_path(root: DiskRoot) -> Path | None:
    """The root as a folder nexcrate sees now, or None."""
    try:
        path, _mount = folders.visible(root.path)
    except folders.NotVisible:
        return None
    return path


def counts_of(db: OrmSession, root_id: int) -> dict[str, int]:
    """The rows of a root per state, ignored rows counted under ``ignored`` only."""
    rows = db.execute(
        select(DiskFolder.state, DiskFolder.ignored, func.count(DiskFolder.id))
        .where(DiskFolder.root_id == root_id)
        .group_by(DiskFolder.state, DiskFolder.ignored)
    ).all()
    counts: dict[str, int] = {}
    for state, ignored, count in rows:
        key = "ignored" if ignored else state
        counts[key] = counts.get(key, 0) + int(count)
    return counts
