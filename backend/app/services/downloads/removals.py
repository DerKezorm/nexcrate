"""Failed downloads whose job the client did not remove (09.10.2026).

When SABnzbd fails a download, ``tracking`` takes its job out of SABnzbd with its files. On 08.10.2026 SABnzbd did not
answer for minutes just then: the removal failed with ``client_unreachable``, was never tried again, and the job with
its files stayed.

* Kept in the setting ``failed_removals`` as JSON, ``{"<download id>": "<first failure>"}``: no column, so no schema
  change.
* Every tracking round tries again, for each client that answers, after its own downloads. A job the client removed,
  or no longer has, leaves the list.
* A download that is no longer failed, is gone, or whose client is gone leaves the list too.
* After ``NOTE_AFTER`` the history card says so (``stuck``): the owner may remove the job in the client.

Log lines carry ids and codes only.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ...db import get_setting, set_setting
from ...models import Download, DownloadClient

SETTING = "failed_removals"
NOTE_AFTER = timedelta(days=1)


def pending(db: OrmSession) -> dict[int, datetime]:
    """Download id to when its removal first failed."""
    try:
        raw = json.loads(get_setting(db, SETTING, "") or "{}")
    except ValueError:
        raw = {}
    found: dict[int, datetime] = {}
    for key, value in (raw.items() if isinstance(raw, dict) else ()):
        try:
            since = datetime.fromisoformat(str(value))
            found[int(key)] = since if since.tzinfo is not None else since.replace(tzinfo=UTC)
        except ValueError:
            continue
    return found


def _store(db: OrmSession, entries: dict[int, datetime]) -> None:
    text = json.dumps({str(key): value.isoformat() for key, value in sorted(entries.items())}, separators=(",", ":"))
    set_setting(db, SETTING, text)


def remember(db: OrmSession, download_ids: Iterable[int], moment: datetime) -> list[int]:
    """Stage the failed removals; the first failure's time stays. Returns the ids that are new. The caller commits."""
    entries = pending(db)
    new = [download_id for download_id in download_ids if download_id not in entries]
    if not new:
        return []
    for download_id in new:
        entries[download_id] = moment
    _store(db, entries)
    return new


def forget(db: OrmSession, download_ids: Iterable[int]) -> None:
    """Stage that these jobs are out of the client. The caller commits."""
    entries = pending(db)
    gone = [download_id for download_id in download_ids if download_id in entries]
    if gone:
        _store(db, {key: value for key, value in entries.items() if key not in gone})


def stuck(db: OrmSession, moment: datetime) -> set[int]:
    """The downloads whose job the client has not removed for ``NOTE_AFTER``."""
    return {key for key, since in pending(db).items() if moment - since >= NOTE_AFTER}


def by_client(db: OrmSession) -> dict[int, list[tuple[int, str]]]:
    """The removals to try again, per client: download id and the client's id of the job. Drops entries whose download
    is no longer failed, is gone, or whose client is gone; the caller commits."""
    entries = pending(db)
    if not entries:
        return {}
    rows = list(db.scalars(select(Download).where(Download.id.in_(list(entries)))))
    clients = set(db.scalars(select(DownloadClient.id)))
    found: dict[int, list[tuple[int, str]]] = defaultdict(list)
    kept: set[int] = set()
    for row in rows:
        if row.state != "failed" or row.client_id not in clients or not row.client_download_id:
            continue
        kept.add(row.id)
        found[int(row.client_id)].append((row.id, row.client_download_id))
    if kept != set(entries):
        _store(db, {key: value for key, value in entries.items() if key in kept})
    return dict(found)
