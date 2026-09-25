"""Transmission through its RPC (``<url>/transmission/rpc``), user and password by HTTP Basic (25.09.2026).

Measured on Transmission 4.1.3, RPC 19, in the download bench, and
read in Radarr's source:

* **Handshake**: without the right user or password 401, before anything else. With them, the first request answers
  409 with ``X-Transmission-Session-Id``; the same request again with that header goes through. The session id is kept
  for the client's life. A 403 is Transmission's address whitelist (``rpc-whitelist``) refusing nexcrate.
* Every answer is ``{"result": "success", "arguments": …}``; anything else in ``result`` (an unknown method answers
  ``no method name``) is a refusal. ``session-get`` tells ``version`` and ``rpc-version``.
* **Category**: like Radarr, a folder below Transmission's ``download-dir`` named after the category
  (``/data/torrents/nexcrate``), and from RPC 17 (Transmission 4.0) also the label. A torrent is nexcrate's when it
  carries the label or lies in that folder. Nothing to create in Transmission.
* **Handing over**: ``torrent-add`` with ``metainfo`` (the file in base64) or ``filename`` (a magnet), ``download-dir``
  and ``paused: false``. The answer names the torrent under ``torrent-added``, or ``torrent-duplicate`` when it is there
  already. The id is the info hash in lower case; ``torrent-get`` and the other calls take hashes as ids, in either
  case. Something missing goes to the top of Transmission's queue (``queue-move-top``, as Radarr's "First").
* **States** (``status``): 0 stopped, 1 and 2 checking, 3 waiting to download, 4 downloading, 5 waiting to seed, 6
  seeding. Done is ``leftUntilDone`` 0 with status 0, 5 or 6: seeding reports ``isFinished`` false until a seed limit
  stops it, so it is not read. A stopped torrent that is not done is paused. ``error`` 3 is a local error (a folder it
  cannot write, a file gone), the problem client_error; 1 and 2 are tracker messages and change nothing. A torrent
  without a size (a magnet still fetching its metadata) is queued, as in Radarr. ``eta`` -1 and -2 are unknown. The
  finished path is ``downloadDir`` plus ``name``.
* **Removing**: ``torrent-remove`` with ``delete-local-data``. An unknown id is answered with success.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any
from urllib.parse import urlsplit

from ..schreibweisen import nfc
from .base import (
    HAND_OVER_TIMEOUT,
    Answer,
    CategoryJob,
    ClientBase,
    ClientError,
    DuplicateTorrent,
    HandOverUnsure,
    Job,
    address_refused,
    auth_failed,
    number,
    percent,
    refused,
    sent_but_unanswered,
    text,
    whole,
    wrong_kind,
)

logger = logging.getLogger("nexcrate.downloaders")

SESSION_HEADER = "X-Transmission-Session-Id"
#: Labels came with RPC 17, Transmission 4.0.
LABELS_SINCE_RPC = 17
STOPPED, CHECK_WAIT, CHECKING, DOWNLOAD_WAIT, DOWNLOADING, SEED_WAIT, SEEDING = range(7)
QUEUED_STATUSES = frozenset({CHECK_WAIT, CHECKING, DOWNLOAD_WAIT})
DONE_STATUSES = frozenset({STOPPED, SEED_WAIT, SEEDING})
LOCAL_ERROR = 3
FIELDS = (
    "hashString", "name", "status", "percentDone", "leftUntilDone", "eta", "sizeWhenDone", "totalSize", "downloadDir",
    "labels", "error",
)  # fmt: skip
_VERSION = re.compile(r"^(\d+(?:\.\d+){0,3})")


def _join(folder: str, name: str) -> str:
    """A path in the client's own spelling: a Windows folder with backslashes, every other one with slashes."""
    separator = "\\" if "\\" in folder and "/" not in folder else "/"
    return folder.rstrip("/\\") + separator + name


def rpc_url(base: str) -> str:
    """Transmission's RPC address from what the owner entered: the bare address, the web interface's
    ``…/transmission`` (Radarr's URL base) or the RPC address itself."""
    trimmed = base.rstrip("/")
    path = urlsplit(trimmed).path
    if path.endswith("/rpc"):
        return trimmed
    if path in ("", "/"):
        return trimmed + "/transmission/rpc"
    return trimmed + "/rpc"


class Transmission(ClientBase):
    kind = "transmission"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._session_id = ""
        self._session: dict[str, Any] | None = None

    async def _post(self, body: str, **extra: Any) -> Answer:
        headers = {"Content-Type": "application/json"}
        if self._session_id:
            headers[SESSION_HEADER] = self._session_id
        auth = (self.target.username, self.target.secret) if self.target.username or self.target.secret else None
        return await self.call("POST", rpc_url(self.target.url), content=body, headers=headers, auth=auth, **extra)

    async def _rpc(self, method: str, arguments: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
        body = json.dumps({"method": method, "arguments": arguments or {}})
        answer = await self._post(body, **extra)
        if answer.status == 409 and answer.headers.get(SESSION_HEADER):
            self._session_id = answer.headers[SESSION_HEADER][:128]
            answer = await self._post(body, **extra)
        if answer.status == 401:
            raise auth_failed("wrong_password")
        if answer.status == 403:
            raise address_refused()
        if answer.status != 200:
            raise wrong_kind()
        try:
            data = json.loads(answer.body)
        except ValueError as exc:
            raise wrong_kind() from exc
        if not isinstance(data, dict) or "result" not in data:
            raise wrong_kind()
        if data["result"] != "success":
            raise refused()
        arguments = data.get("arguments")
        return arguments if isinstance(arguments, dict) else {}

    async def _session_values(self) -> dict[str, Any]:
        if self._session is None:
            self._session = await self._rpc("session-get")
        return self._session

    async def _labels(self) -> bool:
        return (whole((await self._session_values()).get("rpc-version")) or 0) >= LABELS_SINCE_RPC

    # --- The operations ------------------------------------------------------------------------- #

    async def version(self) -> str:
        found = _VERSION.match(text((await self._session_values()).get("version"), 64))
        if found is None:
            raise wrong_kind()
        return found.group(1)

    async def categories(self) -> list[str]:
        return [self.target.category]

    async def create_category(self) -> None:
        """Nothing to create: the folder comes with the first torrent, the label with it."""

    async def category_folder(self) -> str | None:
        folder = text((await self._session_values()).get("download-dir"))
        if not folder.startswith("/") and not re.match(r"^[A-Za-z]:[\\/]", folder):
            return None
        return _join(folder, self.target.category)

    async def add_torrent(
        self, *, content: bytes | None, magnet: str | None, file_name: str, info_hash: str, urgent: bool | None = None
    ) -> str:
        arguments: dict[str, Any] = {"paused": False}
        folder = await self.category_folder()
        if folder is not None:
            arguments["download-dir"] = folder
        if await self._labels():
            arguments["labels"] = [self.target.category]
        if content is not None:
            arguments["metainfo"] = base64.b64encode(content).decode()
        elif magnet:
            arguments["filename"] = magnet
        else:
            raise refused()
        try:
            answer = await self._rpc("torrent-add", arguments, timeout=HAND_OVER_TIMEOUT)
        except ClientError as exc:
            if sent_but_unanswered(exc) and info_hash:
                raise HandOverUnsure(self.target.url, info_hash.lower()) from exc
            raise
        duplicate = answer.get("torrent-duplicate")
        if isinstance(duplicate, dict):
            found = text(duplicate.get("hashString"), 64).lower() or info_hash.lower()
            raise DuplicateTorrent(found, category_matches=await self._ours_by_hash(found))
        added = answer.get("torrent-added")
        found = text(added.get("hashString"), 64).lower() if isinstance(added, dict) else ""
        if not found:
            raise refused()
        if urgent:
            try:
                await self._rpc("queue-move-top", {"ids": [found]})
            except ClientError as exc:
                logger.info("Transmission did not move torrent %s to the top: %s", found[:8], exc.code)
        return found

    async def _torrents(self, ids: list[str] | None = None) -> list[dict[str, Any]]:
        arguments: dict[str, Any] = {"fields": list(FIELDS)}
        if ids is not None:
            arguments["ids"] = ids
        torrents = (await self._rpc("torrent-get", arguments)).get("torrents")
        if not isinstance(torrents, list):
            raise wrong_kind()
        return [item for item in torrents if isinstance(item, dict)]

    async def _is_ours(self, item: dict[str, Any]) -> bool:
        labels = item.get("labels")
        if isinstance(labels, list) and self.target.category in labels:
            return True
        folder = await self.category_folder()
        return folder is not None and text(item.get("downloadDir")).rstrip("/\\") == folder

    async def _ours_by_hash(self, info_hash: str) -> bool:
        return any([await self._is_ours(item) for item in await self._torrents([info_hash])])

    async def jobs(self, download_ids: list[str]) -> dict[str, Job]:
        wanted = sorted({download_id.lower() for download_id in download_ids if download_id})
        if not wanted:
            return {}
        found: dict[str, Job] = {}
        for item in await self._torrents(wanted):
            job = self._job(item) if await self._is_ours(item) else None
            if job is not None and job.download_id in wanted:
                found[job.download_id] = job
        return found

    async def category_jobs(self) -> list[CategoryJob]:
        found: list[CategoryJob] = []
        for item in await self._torrents():
            job = self._job(item) if await self._is_ours(item) else None
            if job is not None:
                found.append(CategoryJob(name=nfc(text(item.get("name"), 1024)), job=job))
        return found

    @staticmethod
    def _job(item: dict[str, Any]) -> Job | None:
        download_id = text(item.get("hashString"), 64).lower()
        status = whole(item.get("status"))
        if not download_id or status is None:
            return None
        done = number(item.get("percentDone"))
        left = whole(item.get("leftUntilDone"))
        eta = whole(item.get("eta"))
        size = whole(item.get("sizeWhenDone")) or whole(item.get("totalSize"))
        common: dict[str, Any] = {
            "download_id": download_id,
            "client_state": f"status {status}, error {whole(item.get('error')) or 0}",
            "size_bytes": size if size is not None and size > 0 else None,
            "progress": percent(done * 100 if done is not None else None),
        }
        if whole(item.get("error")) == LOCAL_ERROR:
            return Job(state="problem", problem="client_error", **common)
        if size is None or size <= 0:
            # A magnet without its metadata yet: nothing to measure (Radarr: queued).
            return Job(state="queued", **common)
        if left == 0 and status in DONE_STATUSES:
            folder, name = text(item.get("downloadDir")), text(item.get("name"), 1024)
            if not folder or not name:
                return Job(state="problem", problem="path_not_found", remaining_seconds=0, **common)
            return Job(state="completed", remaining_seconds=0, path=_join(folder, name), **common)
        remaining = eta if eta is not None and eta >= 0 else None
        if status in QUEUED_STATUSES:
            return Job(state="queued", remaining_seconds=remaining, **common)
        if status == STOPPED:
            return Job(state="paused", **common)
        return Job(state="downloading", remaining_seconds=remaining, **common)

    async def remove(self, download_id: str, *, delete_files: bool) -> None:
        await self._rpc("torrent-remove", {"ids": [download_id.lower()], "delete-local-data": delete_files})
