"""Versions as ``/api/v1`` shows them: a fixed id, a coarse tier, and whether nexcrate would load anything for them by
itself (N9 and N10).

⚠️ A version without a profile or a folder does nothing, silently, and the automatic is off from the factory. That is
why readiness is part of the contract: a program offers only ready versions and tells its owner why another is not.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ...models import DownloadClient, Indexer, Source, VersionDefinition
from ...models.media import new_public_id
from ..automatic import settings as automatic_settings
from ..profiles import music as music_profile
from ..profiles import store as profile_store
from ..releases import music_qualities as mq
from ..search import album as album_search
from . import TITLE_KINDS

logger = logging.getLogger("nexcrate.api_v1")

#: The coarse class of a version per kind; a program must put up with a value it does not know.
TIERS = {"movie": ("sd", "hd", "uhd"), "series": ("sd", "hd", "uhd"), "album": ("lossy", "lossless")}
#: Why a version is not ready, as codes. ``fed_by_source`` carries ``app``.
REASONS = ("no_profile", "no_folder", "no_indexer", "no_download_client", "automatic_off", "fed_by_source")

_RESOLUTION = re.compile(r"(\d{3,4})p")
#: Qualities that name no resolution and still are HD.
_HD_NAMES = ("raw-hd", "br-disk")


def ensure_public_ids(db: OrmSession) -> int:
    """Give every version without a fixed id its own, and commit. Returns how many got one.

    A version made by the app has one from its first moment (the column's default). This is for the rows from before
    the column, and it runs where ``/api/v1`` names versions: a start step could fail and leave them nameless.
    """
    rows = list(db.scalars(select(VersionDefinition).where(VersionDefinition.public_id.is_(None))))
    if not rows:
        return 0
    taken = set(db.scalars(select(VersionDefinition.public_id).where(VersionDefinition.public_id.is_not(None))))
    for row in rows:
        candidate = new_public_id()
        while candidate in taken:
            candidate = new_public_id()
        taken.add(candidate)
        row.public_id = candidate
    db.commit()
    logger.info("%d versions got their id for /api/v1", len(rows))
    return len(rows)


def _quality_names(rules: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for item in rules.get("qualities") or []:
        if not isinstance(item, dict) or not item.get("allowed"):
            continue
        if "group" in item:
            names.extend(str(name) for name in item.get("items") or [])
        elif item.get("name"):
            names.append(str(item["name"]))
    return names


def tier_of(rules: dict[str, Any] | None) -> str | None:
    """The best resolution the profile allows, in coarse: ``sd``, ``hd`` or ``uhd``. None without a profile.

    For music: ``lossless`` when a lossless step is the profile's target, else ``lossy``.
    """
    if not rules:
        return None
    if rules.get("kind") == music_profile.KIND:
        steps = rules.get("steps") if isinstance(rules.get("steps"), dict) else {}
        if not steps:
            return None
        return "lossless" if any(steps.get(step) == music_profile.TARGET for step in mq.LOSSLESS_STEPS) else "lossy"
    names = _quality_names(rules)
    if not names:
        return None
    best = 0
    for name in names:
        found = _RESOLUTION.search(name)
        if found:
            best = max(best, int(found.group(1)))
        elif name.casefold() in _HD_NAMES:
            best = max(best, 1080)
    if best >= 2160:
        return "uhd"
    return "hd" if best >= 720 else "sd"


def _usable_indexers(db: OrmSession) -> dict[str, bool]:
    """Per kind whether an enabled indexer would be asked for it: one without categories for the kind is skipped."""
    usable = dict.fromkeys(TITLE_KINDS, False)
    for enabled, categories, series_categories, music_categories, caps in db.execute(
        select(Indexer.enabled, Indexer.categories, Indexer.series_categories, Indexer.music_categories, Indexer.caps)
    ).tuples():
        if not enabled:
            continue
        usable["movie"] = usable["movie"] or bool(categories)
        usable["series"] = usable["series"] or bool(series_categories)
        # Music takes the indexer's own list, or without one the music categories its caps name (Music M3).
        music = music_categories if music_categories is not None else album_search.default_music_categories(caps)
        usable["album"] = usable["album"] or bool(music)
    return usable


def listing(db: OrmSession, kind: str | None = None) -> list[dict[str, Any]]:
    """Every version of the kinds ``/api/v1`` answers for, per kind in the order they were made."""
    ensure_public_ids(db)
    wanted = (kind,) if kind else TITLE_KINDS
    rows = list(
        db.scalars(
            select(VersionDefinition)
            .where(VersionDefinition.kind.in_(wanted))
            .order_by(VersionDefinition.kind, VersionDefinition.id)
        )
    )
    # Movies, series, music: the order the kinds came in, whatever the alphabet says.
    rows.sort(key=lambda row: (TITLE_KINDS.index(row.kind), row.id))
    rules = profile_store.rules_by_versions(db, [row.id for row in rows])
    feeding = {
        version_id: app
        for version_id, app in db.execute(
            select(Source.version_id, Source.app).where(Source.taken_over_at.is_(None))
        ).tuples()
    }
    indexers = _usable_indexers(db)
    has_client = db.scalar(select(DownloadClient.id).where(DownloadClient.enabled.is_(True)).limit(1)) is not None
    automatic = automatic_settings.load_kinds(db)
    position: dict[str, int] = {}
    items = []
    for row in rows:
        position[row.kind] = position.get(row.kind, 0) + 1
        reasons: list[dict[str, Any]] = []
        if row.profile_id is None:
            reasons.append({"code": "no_profile", "params": {}})
        if not row.folder:
            reasons.append({"code": "no_folder", "params": {}})
        if not indexers.get(row.kind, False):
            reasons.append({"code": "no_indexer", "params": {}})
        if not has_client:
            reasons.append({"code": "no_download_client", "params": {}})
        if row.kind not in automatic:
            reasons.append({"code": "automatic_off", "params": {}})
        if row.id in feeding:
            reasons.append({"code": "fed_by_source", "params": {"app": feeding[row.id]}})
        items.append(
            {
                "version_id": row.public_id,
                "kind": row.kind,
                "name": row.label,
                "order": position[row.kind],
                "tier": tier_of(rules.get(row.id)),
                "ready": not reasons,
                "reasons": reasons,
            }
        )
    return items


def public_ids(db: OrmSession) -> dict[int, str]:
    """Row number to fixed id, for everything that names a version."""
    ensure_public_ids(db)
    return {
        int(row_id): public_id
        for row_id, public_id in db.execute(select(VersionDefinition.id, VersionDefinition.public_id)).tuples()
        if public_id
    }
