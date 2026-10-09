"""A replacement the download client did not take because it did not answer (09.10.2026).

A failed download's replacement found its release, and the hand-over failed with ``client_unreachable``: SABnzbd had
not answered for minutes. Until then the version waited for its next planned search, a day or more.

* ``note``: the automatic loading of a search with the origin ``replacement`` notes the release, the version and the
  episodes when ``loading.grab`` answers ``client_unreachable``, with what the release's indexer answered.
* ``run``, from the automatic's job every minute (with the switches off too, as the replacement itself): ``RETRY_AFTER``
  later that answer is judged again without a request, as a waiting release is (``waiting.load_now``), and the same
  release is handed over through ``loading.grab``, which checks everything again (blocklist, episodes downloading, a
  download of the version). Nothing is searched again; the indexer is asked for the release file only. At most
  ``TRIES`` more hand-overs; then the version waits for its plan as before.
* In memory only: after a restart the version waits for its plan, as before.

Log lines carry ids and codes, never names.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..downloads import loading
from ..search import jobs as search_jobs
from ..search import runner

logger = logging.getLogger("nexcrate.automatic")

RETRY_AFTER = timedelta(minutes=3)
#: Hand-overs after the first one.
TRIES = 5
#: The code that makes a hand-over worth another try.
UNREACHABLE = "client_unreachable"


@dataclass
class Pending:
    title_id: int
    #: movie, series or album.
    kind: str
    release_key: str
    version_id: int
    #: What the release's indexer answered: the release with its link stays in memory only, as in a kept search.
    states: list[runner.IndexerState] = field(repr=False)
    #: The episodes of a series release and their codes; empty for a movie or an album.
    episode_ids: tuple[int, ...]
    only: frozenset[str] | None
    due_at: datetime
    tries: int = 0


_lock = threading.Lock()
_pending: dict[tuple[int, str, int], Pending] = {}


def reset() -> None:
    with _lock:
        _pending.clear()


def pending() -> list[Pending]:
    with _lock:
        return list(_pending.values())


def _kind(search: search_jobs.Search) -> str:
    return "album" if search.album_scope is not None else "series" if search.scope is not None else "movie"


def note(
    search: search_jobs.Search,
    release_key: str,
    version_id: int,
    code: str,
    now: datetime,
    *,
    only: Collection[str] | None = None,
    episode_ids: Collection[int] = (),
) -> None:
    """After a failed grab of an automatic load: a replacement the client did not answer is handed over again."""
    if code != UNREACHABLE or search.origin != "replacement":
        return
    states = [state for state in search.indexers if release_key in state.releases]
    if not states:
        return
    key = (search.title_id, release_key, version_id)
    with _lock:
        if key in _pending:
            return
        _pending[key] = Pending(
            search.title_id,
            _kind(search),
            release_key,
            version_id,
            states,
            tuple(sorted(episode_ids)),
            frozenset(only) if only is not None else None,
            now + RETRY_AFTER,
        )
    logger.info(
        "Automatic search %s: the client did not answer the replacement of version %d; handing it over again in %d "
        "minutes",
        search.search_id,
        version_id,
        int(RETRY_AFTER.total_seconds() // 60),
    )


def _kept(entry: Pending) -> search_jobs.Search | None:
    """The release's answer judged again now, as a finished search without a request."""
    if entry.kind == "series":
        return search_jobs.keep_found_series(
            entry.title_id, origin="replacement", states=entry.states, episode_ids=entry.episode_ids
        )
    if entry.kind == "album":
        return search_jobs.keep_found_album(entry.title_id, origin="replacement", states=entry.states)
    return search_jobs.keep_found(entry.title_id, origin="replacement", states=entry.states)


def _grab(entry: Pending) -> tuple[int, int] | None:
    """Hand the release over again. Returns the download and the release's indexer; None when the release is no longer
    there to load. Raises ``loading.LoadError``."""
    search = _kept(entry)
    if search is None:
        return None
    try:
        found = search_jobs.find_release(search.search_id, entry.release_key)
        download_id = asyncio.run(
            loading.grab(search.search_id, entry.release_key, entry.version_id, [], only=entry.only)
        )
    except (search_jobs.SearchGone, search_jobs.ReleaseMissing):
        return None
    finally:
        search_jobs.drop(search.search_id)
    return download_id, found.indexer_id


def run(now: datetime) -> int:
    """Hand over what is due again. Returns how many downloads it started."""
    from . import scheduler

    with _lock:
        due = [entry for entry in _pending.values() if entry.due_at <= now]
    started = 0
    for entry in due:
        key = (entry.title_id, entry.release_key, entry.version_id)
        try:
            handed = _grab(entry)
        except loading.LoadError as exc:
            entry.tries += 1
            again = exc.code == UNREACHABLE and entry.tries < TRIES
            with _lock:
                if again:
                    entry.due_at = now + RETRY_AFTER
                else:
                    _pending.pop(key, None)
            logger.info(
                "Title %d: handing the replacement of version %d over again failed (%s)%s",
                entry.title_id,
                entry.version_id,
                exc.code,
                "; trying again later" if again else "; the version waits for its plan",
            )
            continue
        with _lock:
            _pending.pop(key, None)
        if handed is None:
            logger.info(
                "Title %d: the replacement of version %d is gone; nothing handed over", entry.title_id, entry.version_id
            )
            continue
        download_id, indexer_id = handed
        scheduler._count_grab(indexer_id)
        started += 1
        logger.info(
            "Title %d: the replacement of version %d is handed over again, download %d",
            entry.title_id,
            entry.version_id,
            download_id,
        )
    return started
