"""The stored judgement of nexcrate's own files follows the rules.

See the design notes, "Changes after the owner's live test", finding 15.

* **Episode files** of a series version of nexcrate's own are judged again as well (``episode_files``, A6 of
  the design notes): a changed profile reaches the files that were filed before it.
* **One rule:** ``releases.cutoff_not_met`` judges a version no source feeds that has a file. The file is named by the
  release it came with (``release_title``), else by the file name of ``relative_path``, with the quality stored for it,
  the way the search names a current file (``current_file``). Without movie rules for its version definition the verdict
  is false. Since decision 20 (15.09.2026) the rule counts the score too, as in Radarr: a file at the cutoff quality
  whose score lies below the profile's upgrade-until can still be upgraded. ``judgement`` gives the same verdict with
  its reason, the file's score and the target, for the title page, and writes nothing.
* **Never judged here:** a version a source feeds keeps the source's verdict; a version without a file is left alone;
  an album follows the music profile (``music/album_quality``).
* **The state** follows ``store.follow_version`` when a verdict changes: a running download, or a problem that needs the
  owner, keeps the state it gives.
* **When:** once after every start, after the profile upkeep, in a thread of its own (``start``), so neither the start
  nor the health check waits for it; for the versions of one definition after its profile is saved or removed
  (``after_profile_change``); when filing away (``judge``) and at the takeover (``verdict``).
* Every chunk is one transaction that takes SQLite's write lock before it reads, so a file filed away meanwhile is never
  judged by the name of the file before it.
* Log lines carry ids and counts, never titles, names or paths.
"""

from __future__ import annotations

import contextvars
import logging
import secrets
import threading
import time
from collections.abc import Collection
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session as OrmSession

from ..db import SessionLocal
from ..models import EpisodeFile, EpisodeVersion, Profile, Title, Version, VersionDefinition, utcnow
from . import logs, releases
from .downloads import store
from .profiles import store as profile_store
from .series.parts import as_whole

logger = logging.getLogger("nexcrate.judging")

#: Versions per transaction: the write lock is held for one chunk at a time. ⚠️ It was 500 until 22.09.2026: on the
#: owner's instance a chunk then held the lock for about seven seconds (3,600 versions in 59.6 s), and a request that
#: wanted to write waited out its 30 seconds and failed with "database is locked".
CHUNK = 100
#: After each chunk the pass lets go for this long. SQLite does not queue writers: whoever asks first after a commit
#: gets the lock, and without the pause that is always this loop again, while the others sleep in their busy handler.
YIELD_SECONDS = 0.25
#: Replaced in the tests, which would otherwise wait a quarter second per chunk.
sleep = time.sleep

_lock = threading.Lock()
_threads: list[threading.Thread] = []


def usable_rules(profile: Profile | None) -> dict[str, Any] | None:
    """The rules of a profile when this judgement can use them, else None.

    Movies only: a series version is judged file by file (``series_decision.judge_episode_file``) while its
    episodes are imported, not by the one file a movie version has.
    """
    if profile is None or not isinstance(profile.rules, dict) or profile.rules.get("kind") != "movie":
        return None
    return profile.rules


def current_file(version: Version) -> releases.CurrentFile:
    """The version's file as every judgement names it: by its release name, else by the file name of its path, with
    its stored quality and languages."""
    return releases.CurrentFile(
        name=version.release_title or version.relative_path,
        quality=version.quality,
        size_bytes=version.size,
        languages=releases.stored_languages(version.languages),
    )


def verdict(rules: dict[str, Any] | None, version: Version, original_language: str | None) -> bool:
    """Whether these rules would still upgrade the version's file. False without rules or without a file."""
    if rules is None or not version.has_file:
        return False
    return releases.cutoff_not_met(rules, current_file(version), original_language)


def judgement(
    rules: dict[str, Any] | None, version: Version, original_language: str | None
) -> releases.FileJudgement | None:
    """``verdict`` with reason, score and target, for the title page. None without rules or without a file."""
    if rules is None or not version.has_file:
        return None
    return releases.judge_file(rules, current_file(version), original_language)


def judge(db: OrmSession, version: Version) -> bool:
    """``verdict`` with the rules of the version's definition and its title's original language, read from ``db``."""
    profile = profile_store.of_version(db, version.version_definition_id)
    language = db.scalar(select(Title.original_language).where(Title.id == version.title_id))
    return verdict(usable_rules(profile), version, language)


def rejudge(definition_ids: Collection[int] | None = None) -> tuple[int, int]:
    """Judge again every version no source feeds that has a file, of these version definitions or of all of them.

    Where a verdict changed, ``cutoff_not_met`` is written and the state follows. Returns how many versions were judged
    and how many of them changed.
    """
    wanted = None if definition_ids is None else sorted(set(definition_ids))
    if wanted == []:
        return 0, 0
    judged = changed = 0
    after = 0
    while True:
        moment = utcnow()
        with SessionLocal() as db:
            # A write first, though it matches no row: it takes SQLite's write lock before anything is read.
            db.execute(
                update(Version).where(Version.id < 1).values(cutoff_not_met=Version.cutoff_not_met),
                execution_options={"synchronize_session": False},
            )
            statement = (
                select(Version)
                .where(
                    Version.source_id.is_(None),
                    Version.has_file.is_(True),
                    Version.id > after,
                    # An album follows the music profile (``music/album_quality``), whoever feeds it: judged here it
                    # would lose its verdict, there are no movie rules for it.
                    Version.version_definition_id.not_in(
                        select(VersionDefinition.id).where(VersionDefinition.kind == "album")
                    ),
                )
                .order_by(Version.id)
                .limit(CHUNK)
            )
            if wanted is not None:
                statement = statement.where(Version.version_definition_id.in_(wanted))
            rows = list(db.scalars(statement))
            if not rows:
                db.rollback()
                return judged, changed
            after = rows[-1].id
            definitions = sorted({row.version_definition_id for row in rows})
            rules = {
                version_id: usable_rules(profile)
                for version_id, profile in profile_store.by_versions(db, definitions).items()
            }
            title_ids = sorted({row.title_id for row in rows})
            languages = dict(
                db.execute(select(Title.id, Title.original_language).where(Title.id.in_(title_ids))).tuples().all()
            )
            for row in rows:
                found = verdict(rules.get(row.version_definition_id), row, languages.get(row.title_id))
                judged += 1
                if found == bool(row.cutoff_not_met):
                    continue
                row.cutoff_not_met = found
                row.updated_at = moment
                # From a blocking download, else from the file: ``upgrade`` or ``available``.
                store.follow_version(db, row.title_id, row.version_definition_id, moment)
                changed += 1
            db.commit()
        sleep(YIELD_SECONDS)


def episode_files(definition_ids: Collection[int] | None = None) -> tuple[int, int]:
    """Judge every episode file of a series version of nexcrate's own again, of these version definitions or of all
    of them (A6). Returns how many files were judged and how many changed their verdict.

    Until now an episode file kept the verdict of its filing: a changed profile, and the anime rules of A4, reached
    only the files filed after it. A version a source feeds keeps the source's verdict, as the import writes it.
    """
    wanted = None if definition_ids is None else sorted(set(definition_ids))
    if wanted == []:
        return 0, 0
    judged = changed = 0
    after = 0
    while True:
        moment = utcnow()
        with SessionLocal() as db:
            db.execute(
                update(EpisodeFile).where(EpisodeFile.id < 1).values(cutoff_not_met=EpisodeFile.cutoff_not_met),
                execution_options={"synchronize_session": False},
            )
            statement = (
                select(EpisodeFile, Version.version_definition_id, Version.title_id)
                .join(Version, Version.id == EpisodeFile.version_id)
                .where(Version.source_id.is_(None), EpisodeFile.id > after)
                .order_by(EpisodeFile.id)
                .limit(CHUNK)
            )
            if wanted is not None:
                statement = statement.where(Version.version_definition_id.in_(wanted))
            rows = list(db.execute(statement).all())
            if not rows:
                db.rollback()
                return judged, changed
            after = rows[-1][0].id
            definitions = sorted({row[1] for row in rows})
            title_ids = sorted({row[2] for row in rows})
            profiles = profile_store.by_versions(db, definitions)
            titles = {
                row.id: (row.original_language, row.series_type)
                for row in db.execute(
                    select(Title.id, Title.original_language, Title.series_type).where(Title.id.in_(title_ids))
                ).all()
            }
            covered = _episode_counts(db, [row[0].id for row in rows])
            for file_row, definition_id, title_id in rows:
                language, series_type = titles.get(title_id, (None, None))
                rules = profile_store.series_rules(profiles.get(definition_id), series_type)
                judged += 1
                if rules is None or rules.get("kind") != "series":
                    continue
                current = _episode_file(file_row, covered.get(file_row.id, 1))
                verdict = releases.judge_episode_file(rules, current, language)
                found = verdict is not None and verdict.reason is not None
                if found == bool(file_row.cutoff_not_met):
                    continue
                file_row.cutoff_not_met = found
                file_row.updated_at = moment
                changed += 1
            db.commit()
        sleep(YIELD_SECONDS)


def _episode_counts(db: OrmSession, file_ids: list[int]) -> dict[int, int]:
    """How many episodes each file covers; a file without a link counts as one."""
    counted: dict[int, int] = {}
    for file_id in db.scalars(
        select(EpisodeVersion.episode_file_id).where(EpisodeVersion.episode_file_id.in_(file_ids))
    ):
        counted[int(file_id)] = counted.get(int(file_id), 0) + 1
    return counted


def _episode_file(row: EpisodeFile, episode_count: int) -> releases.CurrentEpisodeFile:
    return releases.CurrentEpisodeFile(
        release_title=row.release_title,
        name=row.relative_path,
        quality=row.quality,
        release_type=row.release_type,
        size_bytes=as_whole(row.size, row.part),
        episode_count=max(1, episode_count),
        file_id=row.id,
        languages=releases.stored_languages(row.languages),
    )


def after_profile_change(definition_id: int) -> None:
    """The versions of one definition after its profile was saved or removed.

    A failure is only logged: the profile is saved already, and the next start judges again.
    """
    try:
        judged, changed = rejudge([definition_id])
    except Exception:
        logger.exception("Judging the versions of version %d again failed; the next start judges again", definition_id)
        return
    logger.info("Version %d: %d versions judged again, %d changed", definition_id, judged, changed)
    try:
        files, files_changed = episode_files([definition_id])
    except Exception:
        logger.exception(
            "Judging the episode files of version %d again failed; the next start judges again", definition_id
        )
        return
    if files:
        logger.info("Version %d: %d episode files judged again, %d changed", definition_id, files, files_changed)


# --- After the start --------------------------------------------------------------------------------------------- #


def start() -> None:
    """Judge every own file again once, in a thread of its own: the start and the health check do not wait for it."""
    thread = threading.Thread(
        target=contextvars.copy_context().run, args=(_after_start,), name="judging-after-start", daemon=True
    )
    with _lock:
        _threads[:] = [existing for existing in _threads if existing.is_alive()]
        _threads.append(thread)
    try:
        thread.start()
    except BaseException:
        with _lock:
            _threads.remove(thread)
        raise


def _after_start() -> None:
    token = logs.bind_request(secrets.token_hex(4))
    started = time.perf_counter()
    try:
        try:
            judged, changed = rejudge()
            logger.info(
                "After the start: %d versions judged again in %dms, %d changed",
                judged,
                (time.perf_counter() - started) * 1000,
                changed,
            )
        except Exception:
            logger.exception("Judging the stored files again after the start failed; they keep their judgement")
        try:
            started = time.perf_counter()
            judged, changed = episode_files()
            if judged:
                logger.info(
                    "After the start: %d episode files judged again in %dms, %d changed",
                    judged,
                    (time.perf_counter() - started) * 1000,
                    changed,
                )
        except Exception:
            logger.exception("Judging the episode files again after the start failed; they keep their judgement")
        try:
            # Albums follow the music profile (decision 22). Imported here: music builds on
            # this module's neighbours, not the other way round.
            from .music import album_quality

            started = time.perf_counter()
            judged, changed = album_quality.judge_all()
            if judged:
                logger.info(
                    "After the start: %d albums judged in %dms, %d changed",
                    judged,
                    (time.perf_counter() - started) * 1000,
                    changed,
                )
        except Exception:
            logger.exception("Judging the albums after the start failed; they keep their state")
    finally:
        logs.unbind_request(token)


def idle() -> bool:
    """The ``ready`` check of a job that writes: not while the pass of ``start`` runs."""
    return not busy()


def busy() -> bool:
    """Whether the pass of ``start`` is still running. A job that writes waits for it (see ``services/jobs.py``)."""
    with _lock:
        _threads[:] = [thread for thread in _threads if thread.is_alive()]
        return bool(_threads)


def wait_idle(timeout: float = 30.0) -> bool:
    """Wait for the pass started with ``start``. True when none is left running."""
    deadline = time.monotonic() + timeout
    with _lock:
        threads = list(_threads)
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    with _lock:
        _threads[:] = [thread for thread in _threads if thread.is_alive()]
        return not _threads
