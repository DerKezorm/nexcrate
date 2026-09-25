"""Stored profiles after a change of the questions or of the rules: rebuilt once on start.

Step 2b retired a question and hid another one where it has no effect ("Changes after the
owner's test"). A profile saved before keeps its old answers and the rules built from them. On 14.09.2026 the rules
changed as well (``take_now`` at 2160p goes down to 1080p only, and the rules carry ``target_resolution``), which the
builder marks with a higher ``rules_version``; stored rules without the field count as version 1.

On start every stored profile is normalized again. Where the answers change, or the stored rules are of a lower
version than the builder of their kind writes, and the profile was built with the TRaSH state in use, it is built
again and saved with the normalized answers, one INFO line per profile.

A profile of another commit is left alone: it is outdated anyway, and saving it again is the owner's step. A profile
whose answers cannot be normalized or built stays as it is, with a warning. ⚠️ A profile in expert mode is never
rebuilt here: what the owner set by hand would be gone.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from ..db import SessionLocal
from ..models import Profile, utcnow
from . import profiles, trash
from .profiles import music as music_profile
from .profiles import store as profile_store

logger = logging.getLogger("nexcrate.profiles")


def _rebuild_music(row: Profile) -> int | None:
    """The music profile with its normalized answers and the rules of today; None when nothing changed."""
    try:
        answers = music_profile.normalized(row.answers)
    except profiles.AnswersInvalid:
        logger.warning("The stored profile %s cannot be rebuilt (AnswersInvalid) and stays as it is", row.name)
        return None
    if answers == row.answers and not music_profile.rules_behind(row.rules):
        return None
    row.answers = answers
    row.rules = music_profile.build(answers)
    row.updated_at = utcnow()
    return music_profile.RULES_VERSION


def rebuild_normalized() -> list[int]:
    """Rebuild the profiles of the current TRaSH commit whose answers normalize differently or whose rules are of an
    older version. Returns the ids of the versions that judge by them.

    The TRaSH state is loaded only when there is a profile at all.
    """
    rebuilt: list[tuple[int, str, bool, int]] = []
    with SessionLocal() as db:
        rows = list(db.scalars(select(Profile).order_by(Profile.kind, Profile.name)))
        if not rows:
            return []
        for row in rows:
            if row.mode == "expert":
                # The owner keeps this one by hand; nothing rebuilds it from answers.
                continue
            if row.kind == music_profile.KIND:
                # Built without the TRaSH Guides: only its own questions and rules count.
                rules_version = _rebuild_music(row)
                if rules_version is not None:
                    rebuilt.append((row.id, row.name, False, rules_version))
                continue
            # Every kind has the TRaSH state of its own app; both carry the same commit.
            snapshot = trash.current(row.kind) if row.kind in trash.FILE_NAMES else trash.current()
            if row.trash_commit != snapshot.commit:
                continue
            try:
                answers_changed = profiles.normalized(row.kind, row.answers) != row.answers
                if not answers_changed and not profiles.rules_behind(row.kind, row.rules):
                    continue
                built = profiles.build(row.kind, row.answers, snapshot)
            except (profiles.AnswersInvalid, profiles.KindUnsupported, profiles.ProfileBuildError) as exc:
                logger.warning(
                    "The stored profile %s cannot be rebuilt (%s) and stays as it is",
                    row.name,
                    type(exc).__name__,
                )
                continue
            row.answers = built.answers
            row.rules = built.rules
            row.trash_commit = str(built.rules["trash_commit"])
            row.updated_at = utcnow()
            rebuilt.append((row.id, row.name, answers_changed, profiles.rules_version_of(built.rules)))
        if rebuilt:
            db.commit()
        # A profile may serve several versions since migration 20; every one of them judges by the new rules.
        versions = sorted(
            {
                definition.id
                for profile_id, _name, _answers_changed, _rules_version in rebuilt
                for definition in profile_store.versions_of(db, profile_id)
            }
        )
    for _profile_id, name, answers_changed, rules_version in rebuilt:
        if answers_changed:
            logger.info("Profile %s rebuilt with the normalized answers", name)
        else:
            logger.info("Profile %s rebuilt with rules version %d", name, rules_version)
    return versions
