"""What the owner does with downloads: the list, retry, confirming a proposed path mapping, removing (step 3).

* **Views:** ``active`` is queued to importing, ``problems`` every download with a problem or a hint (``stalled`` and an
  unreachable client included), ``history`` imported, failed and removed. Newest first: active and problems by the
  time of loading, history by the last change.
* **Retry** is for ``problem`` and ``completed``. A problem the client reported before the download finished goes back
  to the client (tracking asks again soon); any other goes back to the import.
* **Confirming a mapping** stores the proposal of a ``path_not_found`` problem on the client, once, and imports again.
  ⚠️ The local side must still be a visible folder.
* **Removing** a download that is not finished: with ``remove_from_client`` the client drops the job with its files
  (SABnzbd from queue and history, qBittorrent with ``deleteFiles``), then the download is ``removed``. An imported,
  failed, removed or importing download cannot be removed from the client (``download_finished``). Removing never
  touches imported files.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select

from ... import crypto
from ...db import SessionLocal
from ...meldungen import meldung
from ...models import Download, DownloadClient
from ...models.downloads import ACTIVE_STATES, FINISHED_STATES
from .. import downloaders, folders
from . import files, importing, store, tracking

logger = logging.getLogger("nexcrate.downloads")

VIEWS = ("active", "problems", "history")


class ActionError(Exception):
    def __init__(self, detail: dict[str, Any], status: int) -> None:
        super().__init__(detail["code"])
        self.detail = detail
        self.status = status

    def http(self) -> HTTPException:
        return HTTPException(status_code=self.status, detail=self.detail)


def not_found() -> ActionError:
    return ActionError(meldung("download_not_found", "This download does not exist, or not any more."), 404)


def download_finished() -> ActionError:
    return ActionError(
        meldung(
            "download_finished",
            "This download is filed away already or right now and cannot be removed from the download client.",
        ),
        409,
    )


def listing(view: str, page: int, per_page: int) -> dict[str, Any]:
    offset = (page - 1) * per_page
    with SessionLocal() as db:
        # A failed download that nobody took off counts here too: it waits for the owner (finding of 20.09.2026).
        unfinished = list(
            db.scalars(
                select(Download)
                .where(Download.state.in_(store.UNFINISHED_STATES) | store.waiting_for_owner())
                .order_by(Download.grabbed_at.desc(), Download.id.desc())
            )
        )
        client_codes = {row.id: row.last_error_code for row in db.scalars(select(DownloadClient))}
        problems = [
            (row, problem)
            for row in unfinished
            if (problem := store.problem_of(row, client_codes.get(row.client_id or 0))) is not None
        ]
        counts = {
            "active": sum(1 for row in unfinished if row.state in ACTIVE_STATES),
            "needs_owner": sum(1 for _row, problem in problems if problem["needs_owner"]),
            "hints": sum(1 for _row, problem in problems if not problem["needs_owner"]),
        }
        if view == "active":
            selected = [row for row in unfinished if row.state in ACTIVE_STATES]
            total, items = len(selected), selected[offset : offset + per_page]
        elif view == "problems":
            selected = [row for row, _problem in problems]
            total, items = len(selected), selected[offset : offset + per_page]
        else:
            condition = Download.state.in_(FINISHED_STATES)
            total = int(db.scalar(select(func.count(Download.id)).where(condition)) or 0)
            items = list(
                db.scalars(
                    select(Download)
                    .where(condition)
                    .order_by(Download.updated_at.desc(), Download.id.desc())
                    .offset(offset)
                    .limit(per_page)
                )
            )
        return {"items": store.outs(db, items), "total": total, "page": page, "per_page": per_page, "counts": counts}


def read(download_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        row = db.get(Download, download_id)
        if row is None:
            raise not_found()
        return store.outs(db, [row])[0]


def clear(download_id: int) -> dict[str, Any]:
    """Take a failed download off the problems (the owner's finding of 20.09.2026). It stays in the history, and
    its release stays blocked."""
    moment = store.now()
    with SessionLocal() as db:
        row = db.get(Download, download_id)
        if row is None:
            raise not_found()
        if row.state != "failed":
            raise ActionError(
                meldung("download_not_failed", "Only a failed download can be taken off the problems."), 409
            )
        if row.cleared_at is None:
            row.cleared_at = moment
            row.updated_at = moment
            db.commit()
            logger.info("Download %d is taken off the problems", download_id)
        return store.outs(db, [row])[0]


def retry(download_id: int) -> dict[str, Any]:
    moment = store.now()
    with SessionLocal() as db:
        row = db.get(Download, download_id)
        if row is None:
            raise not_found()
        if row.state not in ("problem", "completed"):
            raise ActionError(
                meldung(
                    "download_not_retryable",
                    "Only a download with a problem or one waiting to be filed away can be tried again.",
                ),
                409,
            )
        back_to_client = row.state == "problem" and row.completed_at is None
        row.state = "downloading" if back_to_client else "completed"
        row.problem_code, row.problem_values, row.missing_count = None, None, 0
        row.updated_at = moment
        store.follow(db, row, moment)
        db.commit()
    logger.info("Download %d is tried again", download_id)
    if back_to_client:
        tracking.soon()
    else:
        importing.request(download_id)
    return read(download_id)


def confirm_mapping(download_id: int) -> dict[str, Any]:
    moment = store.now()
    refused = ActionError(
        meldung("mapping_not_proposed", "There is no proposed path for this download to confirm."), 409
    )
    with SessionLocal() as db:
        row = db.get(Download, download_id)
        if row is None:
            raise not_found()
        values = row.problem_values or {}
        proposal = values.get("proposal") if row.state == "problem" and row.problem_code == "path_not_found" else None
        client = db.get(DownloadClient, row.client_id) if row.client_id is not None else None
        if not isinstance(proposal, dict) or client is None:
            raise refused
        remote, local = str(proposal.get("remote") or ""), str(proposal.get("local") or "")
        # ⚠️ The local side is checked again: the proposal is days old, and the folder may be gone or a link now.
        if files.remote_parts(remote) is None or folders.visible_path(local) is None:
            raise refused
        entry = {"remote": remote, "local": local}
        mappings = [dict(mapping) for mapping in (client.path_mappings or [])]
        if entry not in mappings:
            # A new list: a JSON column changed in place is not written.
            client.path_mappings = [*mappings, entry]
            client.updated_at = moment
        row.state, row.problem_code, row.problem_values = "completed", None, None
        row.updated_at = moment
        store.follow(db, row, moment)
        db.commit()
        client_id = client.id
    logger.info("Download %d: a path mapping of download client %d is confirmed", download_id, client_id)
    importing.request(download_id)
    return read(download_id)


@dataclass(frozen=True)
class _Removal:
    state: str
    client_download_id: str
    #: ⚠️ With the decrypted secret; None when the client is gone.
    target: downloaders.Target | None = field(repr=False)


def _removal(download_id: int) -> _Removal:
    with SessionLocal() as db:
        row = db.get(Download, download_id)
        if row is None:
            raise not_found()
        client = db.get(DownloadClient, row.client_id) if row.client_id is not None else None
        target = None
        if client is not None:
            target = downloaders.Target(
                kind=client.kind,
                url=client.url,
                username=client.username or "",
                secret=crypto.decrypt(client.secret) if client.secret else "",
                category=client.category,
                login_blocked=store.login_blocked(client),
            )
        return _Removal(state=row.state, client_download_id=row.client_download_id, target=target)


def _mark_removed(download_id: int, *, blocklist: bool) -> None:
    moment = store.now()
    with SessionLocal() as db:
        row = db.get(Download, download_id)
        if row is None:
            return
        if row.state not in FINISHED_STATES and row.state != "importing":
            row.state, row.problem_code, row.problem_values = "removed", None, None
            row.updated_at = moment
        if blocklist:
            store.block(db, row, "removed_by_owner", moment)
        store.follow(db, row, moment)
        db.commit()


async def remove(download_id: int, *, remove_from_client: bool, blocklist: bool) -> None:
    removal = await asyncio.to_thread(_removal, download_id)
    finished = removal.state in FINISHED_STATES or removal.state == "importing"
    if finished and remove_from_client:
        raise download_finished()
    if remove_from_client and removal.target is not None and removal.client_download_id:
        try:
            async with downloaders.open_client(removal.target) as client:
                await client.remove(removal.client_download_id, delete_files=True)
        except downloaders.ClientError as exc:
            logger.info("Download %d could not be removed from its client: %s", download_id, exc.code)
            raise ActionError(exc.detail, exc.status) from exc
    await asyncio.to_thread(_mark_removed, download_id, blocklist=blocklist)
    logger.info("Download %d removed by the owner", download_id)
