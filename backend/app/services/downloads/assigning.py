"""What the owner does with a download nexcrate could not file by itself (S4.5, S4.6).

* ``files`` lists the videos of a series download, or the candidates of a movie download with several videos, each by
  a number (``key``), never by a path the browser could send back. With them the episodes of the download and of the
  series, so the dialog can offer every episode that has a TMDB episode (decision 52).
* ``assign`` files videos with the episodes the owner chose (decisions 35 and 36): every episode once, only episodes of
  the series; replacing a file that is not worse needs ``confirm: ["not_better"]``. A video mapped to no episode is not
  filed. The filing runs in the import's thread; the answer is the download, ``importing``.
* ``finish`` is "Rest nicht ablegen" (decision 37): the download is ``imported`` with what is filed; afterwards its
  unpack folder goes, and SABnzbd's job folder as after a filed download (decision 30). A torrent keeps its files.
* An assignment while the import's thread still ends answers ``download_busy``; an assignment that replaces only part
  of a multi-episode file answers ``assignment_covers_more`` with the episodes the file also holds (decision 26).
* ``choose`` files the video the owner chose of several (decision 39).
* ``watch`` is the guard of decision 33: a download ``completed`` or ``importing`` for 30 minutes without a change and
  without a running import becomes ``import_stalled``.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update

from ...db import SessionLocal
from ...meldungen import meldung
from ...models import (
    Download,
    DownloadEpisode,
    DownloadFile,
    Episode,
    EpisodeFile,
    EpisodeVersion,
    Release,
    Title,
)
from .. import releases
from ..profiles import store as profile_store
from ..releases import series_decision
from ..series import names
from ..series.parts import as_whole
from . import episodes, files, importing, series_import, store
from .actions import ActionError, not_found, read

logger = logging.getLogger("nexcrate.downloads")

#: Problems whose card has "Von Hand zuordnen".
ASSIGNABLE = ("files_unassigned", "other_series_suspected")
#: Problems of a series download whose card has "Rest nicht ablegen": a file that failed ends the download too.
FINISHABLE = (*ASSIGNABLE, "import_failed")
#: Decisions of videos the owner may still assign.
OPEN_DECISIONS = (
    episodes.OPEN, episodes.NOT_NEEDED, episodes.DUPLICATE, episodes.SAMPLE, episodes.EXTRA, "not_filed",
    series_import.SKIPPED,
)  # fmt: skip
STALLED_AFTER = timedelta(minutes=30)


def _code(season: int, number: int) -> str:
    return f"S{season:02d}E{number:02d}"


def _is_album(download_id: int) -> bool:
    with SessionLocal() as db:
        return db.scalar(select(Download.scope).where(Download.id == download_id)) == store.ALBUM_SCOPE


def files_of(download_id: int) -> dict[str, Any]:
    """``GET /api/downloads/{id}/files``. An album download has its own dialog (``album_assigning``)."""
    with SessionLocal() as db:
        row = db.get(Download, download_id)
        if row is None or row.scope == store.ALBUM_SCOPE:
            raise not_found()
        title = db.get(Title, row.title_id)
        version = store.version_of(db, row.title_id, row.version_definition_id)
        language = names.account_language(db)
        listed = list(
            db.scalars(select(DownloadFile).where(DownloadFile.download_id == row.id).order_by(DownloadFile.id))
        )
        series_rows: dict[int, Episode] = {}
        if row.scope is not None and title is not None:
            series_rows = {
                item.id: item
                for item in db.scalars(
                    select(Episode)
                    .where(Episode.title_id == title.id, Episode.tmdb_gone_at.is_(None))
                    .order_by(Episode.season_number, Episode.episode_number)
                )
            }
        links = {
            item.episode_id: item
            for item in db.scalars(select(DownloadEpisode).where(DownloadEpisode.download_id == row.id))
        }
        watched: dict[int, bool] = {}
        current: dict[int, dict[str, Any]] = {}
        if version is not None and series_rows:
            file_rows = {
                item.id: item for item in db.scalars(select(EpisodeFile).where(EpisodeFile.version_id == version.id))
            }
            version_links = list(db.scalars(select(EpisodeVersion).where(EpisodeVersion.version_id == version.id)))
            held: dict[int, list[str]] = defaultdict(list)
            for link in version_links:
                episode = series_rows.get(link.episode_id)
                if link.episode_file_id is not None and episode is not None:
                    held[link.episode_file_id].append(_code(episode.season_number, episode.episode_number))
            seconds = {
                int(item.part_of_episode_id): item
                for item in file_rows.values()
                if item.part == 2 and item.part_of_episode_id is not None
            }
            for link in version_links:
                watched[link.episode_id] = bool(link.watched)
                stored = file_rows.get(link.episode_file_id) if link.episode_file_id is not None else None
                if stored is not None:
                    # Every episode the file holds, so the dialog can say a multi-episode file needs all of them.
                    second = seconds.get(link.episode_id)
                    current[link.episode_id] = {
                        "quality": stored.quality,
                        "size_bytes": stored.size + (second.size if second is not None else 0),
                        "episodes": sorted(held.get(stored.id, [])),
                        "parts": [1, 2] if second is not None else ([1] if stored.part == 1 else []),
                    }

        def episode_out(episode_id: int) -> dict[str, Any]:
            item = series_rows.get(episode_id)
            if item is None:
                return {"id": episode_id, "code": "", "name": ""}
            return {
                "id": item.id,
                "code": _code(item.season_number, item.episode_number),
                "name": names.display_name(item.name, item.name_en, language),
            }

        return {
            "download_id": row.id,
            "kind": "series" if row.scope is not None else "movie",
            "files": [
                {
                    "key": item.id,
                    "path": item.path,
                    "size_bytes": item.size,
                    "duration_seconds": item.duration_seconds,
                    "reading": _reading_out(item.reading),
                    "decision": item.decision,
                    "episodes": [episode_out(episode_id) for episode_id in item.episode_ids or []],
                }
                for item in listed
            ],
            "episodes": [
                {
                    **episode_out(item.id),
                    "season": item.season_number,
                    "in_download": item.id in links,
                    "state": links[item.id].state if item.id in links else None,
                    "watched": watched.get(item.id, False),
                    "current_file": current.get(item.id),
                }
                for item in series_rows.values()
            ],
            "series": {"id": row.title_id, "title": title.title if title is not None else ""},
            "version": {"id": row.version_definition_id, "label": row.version_label},
        }


def _reading_out(reading: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(reading, dict):
        return None
    return {
        "form": reading.get("form"),
        "from": reading.get("from"),
        "season": reading.get("season"),
        "numbers": list(reading.get("numbers") or []),
        "air_date": reading.get("air_date"),
        "part": reading.get("part") if reading.get("part") in (1, 2) else None,
    }


def _not_assignable() -> ActionError:
    return ActionError(meldung("download_not_assignable", "This download has no files waiting for an assignment."), 409)


def _invalid(code: str, text: str, **values: Any) -> ActionError:
    return ActionError(meldung(code, text, **values), 422)


def _busy() -> ActionError:
    return ActionError(
        meldung("download_busy", "nexcrate is still working on this download. Please try again in a moment."), 409
    )


def assign(
    download_id: int, chosen: list[tuple[int, list[int], int | None]], confirm: list[str]
) -> dict[str, Any]:
    """``POST /api/downloads/{id}/assign``: checks, then files in the import's thread.

    A file may be one half of a double episode (``part`` 1 or 2): then it names exactly
    one episode, and the other half may name the same episode. The choice is kept with the file's reading, where the
    filing reads it."""
    moment = store.now()
    with SessionLocal() as db:
        row = db.get(Download, download_id)
        if row is None:
            raise not_found()
        if (
            row.scope is None
            or row.scope == store.ALBUM_SCOPE
            or row.state != "problem"
            or row.problem_code not in ASSIGNABLE
        ):
            raise _not_assignable()
        if importing.running(download_id):
            raise _busy()
        problem_code, problem_values = row.problem_code, row.problem_values
        rows = {item.id: item for item in db.scalars(select(DownloadFile).where(DownloadFile.download_id == row.id))}
        series_ids = set(
            db.scalars(select(Episode.id).where(Episode.title_id == row.title_id, Episode.tmdb_gone_at.is_(None)))
        )
        already = {
            item.episode_id
            for item in db.scalars(
                select(DownloadEpisode).where(DownloadEpisode.download_id == row.id, DownloadEpisode.state == "filed")
            )
        }
        # What of each episode is taken: 0 for the whole, 1 and 2 for the halves. A filed file of this download
        # counts with the half it was filed as.
        taken: dict[int, set[int]] = defaultdict(set)
        for item in rows.values():
            if item.decision == episodes.FILED:
                half = item.reading.get("part") if isinstance(item.reading, dict) else None
                for episode_id in item.episode_ids or []:
                    taken[episode_id].add(half if half in (1, 2) else 0)
        for episode_id in already:
            taken[episode_id] = taken[episode_id] or {0}
        mapping: dict[int, tuple[int, ...]] = {}
        halves: dict[int, int | None] = {}
        for key, episode_ids, part in chosen:
            file_row = rows.get(key)
            if file_row is None or file_row.decision not in OPEN_DECISIONS:
                raise _not_assignable()
            if part is not None and len(set(episode_ids)) != 1:
                raise _invalid("invalid_input", "The input is not valid.", fields=["files"])
            half = part or 0
            for episode_id in episode_ids:
                if episode_id not in series_ids:
                    raise _invalid("episode_not_in_series", "An episode does not belong to this series.")
                if taken[episode_id] and (half == 0 or taken[episode_id] & {0, half}):
                    raise _invalid("episode_twice", "An episode is chosen for more than one file.")
                taken[episode_id].add(half)
            mapping[key] = tuple(sorted(set(episode_ids)))
            halves[key] = part if episode_ids else None
        if not mapping:
            raise _invalid("invalid_input", "The input is not valid.", fields=["files"])
        _check_files(db, row, rows, mapping)
        more = _covers_more(db, row, mapping, already, halves)
        if more:
            raise ActionError(
                meldung(
                    "assignment_covers_more",
                    "A file an episode has holds more episodes. Choose all of them, or leave the file.",
                    episodes=", ".join(more),
                ),
                409,
            )
        if "not_better" not in confirm:
            worse = _not_better(db, row, rows, mapping, halves)
            if worse:
                raise ActionError(
                    meldung(
                        "assignment_not_better",
                        "A chosen file is not better than the file an episode has.",
                        episodes=", ".join(worse),
                    ),
                    409,
                )
        for key, part in halves.items():
            # The owner's word on the half travels with the file's reading, where the filing reads it.
            reading = dict(rows[key].reading) if isinstance(rows[key].reading, dict) else {}
            if part is None:
                reading.pop("part", None)
            else:
                reading["part"] = part
            rows[key].reading = reading
        links = set(db.scalars(select(DownloadEpisode.episode_id).where(DownloadEpisode.download_id == row.id)))
        for episode_ids in mapping.values():
            for episode_id in episode_ids:
                if episode_id not in links:
                    db.add(
                        DownloadEpisode(download_id=row.id, episode_id=episode_id, action="confirmed", state="expected")
                    )
        claimed = db.execute(
            update(Download)
            .where(Download.id == row.id, Download.state == "problem")
            .values(state="importing", problem_code=None, problem_values=None, updated_at=moment)
        )
        if not getattr(claimed, "rowcount", 0):
            raise _not_assignable()
        db.commit()
    filed = {key: value for key, value in mapping.items()}
    logger.info("Download %d: the owner assigned %d files", download_id, sum(1 for value in filed.values() if value))
    if not importing.request(download_id, work=lambda: series_import.run(download_id, manual=filed)):
        # The thread of the round before still ended: the problem stays as it was, the owner tries again.
        with SessionLocal() as db:
            db.execute(
                update(Download)
                .where(Download.id == download_id, Download.state == "importing")
                .values(state="problem", problem_code=problem_code, problem_values=problem_values, updated_at=moment)
            )
            db.commit()
        logger.info("Download %d: an import still ran; the assignment is refused", download_id)
        raise _busy()
    return read(download_id)


def _covers_more(
    db: Any,
    row: Download,
    mapping: dict[int, tuple[int, ...]],
    already: set[int],
    halves: dict[int, int | None] | None = None,
) -> list[str]:
    """Codes of the episodes a replaced multi-episode file also holds that neither this assignment nor the download
    files: such a file is only replaced whole (decision 26), so the assignment would file nothing.

    The same for a half of a double episode whose episode has a whole file: both halves come, or neither."""
    version = store.version_of(db, row.title_id, row.version_definition_id)
    if version is None:
        return []
    chosen = {episode_id for episode_ids in mapping.values() for episode_id in episode_ids} | already
    links = {
        link.episode_id: link.episode_file_id
        for link in db.scalars(select(EpisodeVersion).where(EpisodeVersion.version_id == version.id))
        if link.episode_file_id is not None
    }
    own = f"nexcrate:{row.id}"
    missing: set[int] = set()
    for episode_id in chosen:
        file_id = links.get(episode_id)
        stored = db.get(EpisodeFile, file_id) if file_id is not None else None
        if stored is None or stored.file_ref == own:
            continue
        missing |= {other for other, linked in links.items() if linked == file_id and other not in chosen}
    chosen_halves = {
        (episode_id, part)
        for key, part in (halves or {}).items()
        if part is not None
        for episode_id in mapping.get(key, ())
    }
    for episode_id, part in chosen_halves:
        file_id = links.get(episode_id)
        stored = db.get(EpisodeFile, file_id) if file_id is not None else None
        whole = stored is not None and stored.part is None and stored.file_ref != own
        if whole and (episode_id, 3 - part) not in chosen_halves:
            missing.add(episode_id)
    codes = []
    for episode_id in sorted(missing):
        episode = db.get(Episode, episode_id)
        if episode is not None:
            codes.append(_code(episode.season_number, episode.episode_number))
    return sorted(codes)


def _check_files(db: Any, row: Download, rows: dict[int, DownloadFile], mapping: dict[int, tuple[int, ...]]) -> None:
    """The chosen videos of the job folder are still there with their size (``download_files_changed``)."""
    try:
        with SessionLocal() as session:
            context = series_import.load(session, row.id)
        job = series_import._local_job(context) if context is not None else None
    except series_import.Problem:
        job = None
    for key, episode_ids in mapping.items():
        file_row = rows[key]
        if not episode_ids or file_row.path.startswith(series_import.UNPACKED_PREFIX) or job is None:
            continue
        base = job if job.is_dir() else job.parent
        parts = [part for part in file_row.path.split("/") if part not in ("", ".", "..")]
        path = base.joinpath(*parts) if job.is_dir() else job
        try:
            size = path.stat().st_size if files.inside(path, base) else None
        except OSError:
            size = None
        if size != file_row.size:
            raise ActionError(
                meldung("download_files_changed", "A file of this download is gone or changed. Please look again."),
                409,
            )


#: The release type the filing gives a new file of a download that is no pack, by whether it holds one episode.
SINGLE_OR_MULTI = {True: "singleEpisode", False: "multiEpisode"}


def _not_better(
    db: Any,
    row: Download,
    rows: dict[int, DownloadFile],
    mapping: dict[int, tuple[int, ...]],
    halves: dict[int, int | None] | None = None,
) -> list[str]:
    """Codes of chosen episodes whose current file the chosen video does not improve on (decision 36)."""
    profile = profile_store.of_version(db, row.version_definition_id)
    version = store.version_of(db, row.title_id, row.version_definition_id)
    title = db.get(Title, row.title_id)
    rules = profile_store.series_rules(profile, title.series_type if title is not None else None)
    if rules is None or rules.get("kind") != "series" or version is None:
        return []
    links = {
        link.episode_id: link.episode_file_id
        for link in db.scalars(select(EpisodeVersion).where(EpisodeVersion.version_id == version.id))
        if link.episode_file_id is not None
    }
    covered: dict[int, int] = defaultdict(int)
    for file_id in links.values():
        covered[file_id] += 1
    worse: list[str] = []
    season_pack = releases.parse_series(row.release_title).series.release_type == "season_pack"
    for key, episode_ids in mapping.items():
        part = (halves or {}).get(key)
        for episode_id in episode_ids:
            file_id = links.get(episode_id)
            stored = db.get(EpisodeFile, file_id) if file_id is not None else None
            if part == 2:
                # A second half is measured against the second half the episode has, if it has one.
                stored = db.scalar(
                    select(EpisodeFile).where(
                        EpisodeFile.version_id == version.id,
                        EpisodeFile.part == 2,
                        EpisodeFile.part_of_episode_id == episode_id,
                    )
                )
            if stored is None or stored.file_ref == f"nexcrate:{row.id}":
                continue
            file_row = rows[key]
            language = title.original_language if title is not None else None
            # Judged as the filing judges it: formats and languages of the release, the quality as Sonarr sums it up.
            candidate = releases.CurrentEpisodeFile(
                release_title=row.release_title,
                name=file_row.path,
                quality=series_import.file_quality(file_row.path, row.release_title, language),
                size_bytes=as_whole(file_row.size, part),
                episode_count=len(episode_ids),
                release_type="seasonPack" if season_pack else SINGLE_OR_MULTI[len(episode_ids) == 1],
                languages=releases.stored_languages(row.languages),
            )
            current = releases.CurrentEpisodeFile(
                release_title=stored.release_title,
                name=stored.relative_path,
                quality=stored.quality,
                release_type=stored.release_type,
                size_bytes=as_whole(stored.size, stored.part),
                episode_count=covered[stored.id],
                file_id=stored.id,
                languages=releases.stored_languages(stored.languages),
            )
            if not series_decision.better_file(rules, candidate, current, language):
                episode = db.get(Episode, episode_id)
                if episode is not None:
                    worse.append(_code(episode.season_number, episode.episode_number))
    return sorted(set(worse))


def finish(download_id: int) -> dict[str, Any]:
    """``POST /api/downloads/{id}/finish``: the rest is not filed; the download is ``imported``."""
    if _is_album(download_id):
        from . import album_assigning

        return album_assigning.finish(download_id)
    moment = store.now()
    with SessionLocal() as db:
        row = db.get(Download, download_id)
        if row is None:
            raise not_found()
        if row.scope is None or row.state != "problem" or row.problem_code not in FINISHABLE:
            raise _not_assignable()
        if importing.running(download_id):
            raise _busy()
        filed = 0
        not_filed: list[tuple[int, int]] = []
        for link, season, number in db.execute(
            select(DownloadEpisode, Episode.season_number, Episode.episode_number)
            .join(Episode, Episode.id == DownloadEpisode.episode_id)
            .where(DownloadEpisode.download_id == row.id)
        ).tuples():
            if link.state == "filed":
                filed += 1
            elif link.state in ("expected", "missing"):
                link.state = "not_filed"
                not_filed.append((season, number))
        for item in db.scalars(
            select(DownloadFile).where(
                DownloadFile.download_id == row.id, DownloadFile.decision.in_((episodes.OPEN, series_import.PLACING))
            )
        ):
            item.decision = "not_filed"
        skipped = db.scalar(
            select(func.count())
            .select_from(DownloadEpisode)
            .where(DownloadEpisode.download_id == row.id, DownloadEpisode.state == "skipped_not_better")
        )
        row.state, row.problem_code, row.problem_values = "imported", None, None
        row.open_count = 0
        row.imported_at = moment
        row.updated_at = moment
        detail = series_import.history_detail(filed, int(skipped or 0), []) + (
            " not_filed=" + ",".join(_code(season, number) for season, number in sorted(not_filed)) if not_filed else ""
        )
        store.add_history(db, row, "episodes_filed", detail, moment, series_import.history_data(db, row.id))
        store.follow(db, row, moment)
        db.commit()
    logger.info("Download %d: the rest is not filed (%d episodes)", download_id, len(not_filed))
    # The unpack folder and SABnzbd's job folder go in the background (decision 30): the download is finished.
    importing.request(download_id, work=lambda: series_import.after_finish(download_id))
    return read(download_id)


def choose(download_id: int, key: int) -> dict[str, Any]:
    """``POST /api/downloads/{id}/choose``: the chosen video of a movie download with several (decision 39)."""
    moment = store.now()
    with SessionLocal() as db:
        row = db.get(Download, download_id)
        if row is None:
            raise not_found()
        if row.scope is not None or row.state != "problem" or row.problem_code != "several_videos":
            raise ActionError(
                meldung("download_not_choosable", "This download has no videos waiting for a choice."), 409
            )
        candidates = list(
            db.scalars(
                select(DownloadFile).where(
                    DownloadFile.download_id == row.id, DownloadFile.decision.in_(("candidate", "chosen"))
                )
            )
        )
        if key not in {item.id for item in candidates}:
            raise ActionError(meldung("invalid_input", "The input is not valid.", fields=["key"]), 422)
        for item in candidates:
            item.decision = "chosen" if item.id == key else "candidate"
        row.state, row.problem_code, row.problem_values = "completed", None, None
        row.updated_at = moment
        store.follow(db, row, moment)
        db.commit()
    logger.info("Download %d: the owner chose one of several videos", download_id)
    importing.request(download_id)
    return read(download_id)


def _albums_waiting_for_tracks(db: Any, title_ids: list[int]) -> set[int]:
    if not title_ids:
        return set()
    loaded = set(
        db.scalars(select(Release.title_id).where(Release.title_id.in_(title_ids), Release.tracks_loaded.is_(True)))
    )
    return set(title_ids) - loaded


def watch() -> int:
    """Downloads ``completed`` or ``importing`` for 30 minutes without a change and without a running import become
    ``import_stalled`` (decision 33). Returns how many."""
    moment = store.now()
    changed = 0
    with SessionLocal() as db:
        rows = list(
            db.scalars(
                select(Download).where(
                    Download.state.in_(("completed", "importing")), Download.updated_at < moment - STALLED_AFTER
                )
            )
        )
        waiting = _albums_waiting_for_tracks(db, [row.title_id for row in rows if row.scope == store.ALBUM_SCOPE])
        for row in rows:
            if importing.running(row.id):
                continue
            if row.state == "completed" and row.title_id in waiting:
                # An album download waits for its tracks up to a day (decision 4).
                continue
            row.state, row.problem_code, row.problem_values = "problem", "import_stalled", {}
            row.completed_at = row.completed_at or moment
            row.updated_at = moment
            store.follow(db, row, moment)
            changed += 1
        if changed:
            db.commit()
    if changed:
        logger.warning("%d downloads waited 30 minutes to be filed and are problems now", changed)
    return changed
