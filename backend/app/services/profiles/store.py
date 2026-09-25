"""The one way from a version to its profile.

Since migration 20 a profile stands for itself and carries a name; a version points at one of them
(``VersionDefinition.profile_id``), and several versions may point at the same. Nothing outside this module
looks a profile up by a version, so the link can be changed in one place.

``of_version`` answers for one version, ``by_versions`` for many at once: it keeps the shape every caller had
before, a dictionary from the version's id to its profile, so a listing still costs one query.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ...models import Profile, VersionDefinition
from . import series as series_profile


def of_version(db: OrmSession, version_id: int) -> Profile | None:
    """The profile this version judges by, or None while it has none."""
    return db.scalar(
        select(Profile).join(VersionDefinition, VersionDefinition.profile_id == Profile.id).where(
            VersionDefinition.id == version_id
        )
    )


def by_versions(db: OrmSession, version_ids: Iterable[int]) -> dict[int, Profile]:
    """Version id to its profile, for the versions that have one."""
    wanted = list(version_ids)
    if not wanted:
        return {}
    rows = db.execute(
        select(VersionDefinition.id, Profile)
        .join(Profile, Profile.id == VersionDefinition.profile_id)
        .where(VersionDefinition.id.in_(wanted))
    ).tuples()
    return {int(version_id): profile for version_id, profile in rows}


def rules_by_versions(db: OrmSession, version_ids: Iterable[int]) -> dict[int, dict[str, Any]]:
    """Version id to the rules of its profile, for callers that judge and never write."""
    return {version_id: profile.rules for version_id, profile in by_versions(db, version_ids).items()}


def assign(db: OrmSession, version_id: int, profile: Profile | None) -> None:
    """Let this version judge by that profile, or by none."""
    definition = db.get(VersionDefinition, version_id)
    if definition is not None:
        definition.profile_id = None if profile is None else profile.id


def versions_of(db: OrmSession, profile_id: int) -> list[VersionDefinition]:
    """The versions that point at this profile, in the order of their kind and name."""
    return list(
        db.scalars(
            select(VersionDefinition)
            .where(VersionDefinition.profile_id == profile_id)
            .order_by(VersionDefinition.kind, VersionDefinition.label)
        )
    )


def free_name(db: OrmSession, kind: str, wanted: str) -> str:
    """``wanted``, or ``wanted 2`` and so on while a profile of this kind already has the name."""
    base = (wanted.strip() or "Profil")[:200]
    taken = {row for row in db.scalars(select(Profile.name).where(Profile.kind == kind))}
    if base not in taken:
        return base
    number = 2
    while f"{base} {number}"[:200] in taken:
        number += 1
    return f"{base} {number}"[:200]


def series_rules(profile: Profile | None, series_type: str | None) -> dict[str, Any] | None:
    """The rules a version judges a series of this type by: its profile's anime rules for an anime series
    (A4), else its rules; None without a profile."""
    rules = profile.rules if profile is not None and isinstance(profile.rules, dict) else None
    return series_profile.rules_for(rules, series_type)
