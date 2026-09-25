"""NZBGet through its JSON-RPC API (``<url>/jsonrpc``), user and password by HTTP Basic (25.09.2026).

Measured on NZBGet 26.3 in the download bench:

* ``version`` answers a text such as ``26.3``; without the right user or password NZBGet answers 401.
* **Categories** live in NZBGet's configuration (``CategoryN.Name``). A job with a category NZBGet does not know is
  still taken and filed below ``DestDir/<category>`` (``AppendCategoryDir`` on, the default), so nexcrate needs no
  entry there and creates none: writing NZBGet's configuration would mean a restart of NZBGet, and ⚠️ ``saveconfig``
  takes the whole list: saved with a single option, every other one was gone (bench, 25.09.2026). A configured
  category with its own ``DestDir`` files there.
* **Retention** (``usenet_retention``): ``ServerN.Retention`` in days of every server with ``ServerN.Active``, 0 for
  unlimited; read only.
* **Handing over**: ``append(name, base64, category, priority, AddToTop, AddPaused, DupeKey, DupeScore, DupeMode,
  PPParameters)`` answers the job's number, above 0. ⚠️ ``DupeMode`` is ``FORCE``: with ``SCORE`` or ``ALL`` NZBGet
  dropped a release it had seen before, even one only hidden in its history, silently as ``DELETED/COPY`` without
  loading it. Which release is a duplicate nexcrate decides itself. Priorities: 50 for something missing, -50 for an
  upgrade (NZBGet's high and low), 0 otherwise.
* **Queue** (``listgroups``): ``QUEUED`` is queued, ``PAUSED`` paused, and a queue NZBGet holds as a whole
  (``status.DownloadPaused``) makes every queued or downloading job paused; post-processing states
  (``PP_QUEUED`` to ``PP_FINISHED``) are still downloading. Sizes in bytes from ``FileSizeLo/Hi``.
* **History** (``history``): the text before the slash decides. ``SUCCESS`` is completed at ``FinalDir`` or
  ``DestDir``; ``FAILURE`` failed; ``WARNING/SPACE`` is the problem no_space, ``WARNING/PASSWORD`` failed for the
  password, other warnings failed; ``DELETED`` failed (``COPY``/``DUPE``/``GOOD`` as duplicate). A failed job's
  folder is often gone already (health failures remove it), otherwise it is the leftover.
* **Removing**: a queued job with ``GroupFinalDelete`` (files too) or ``GroupDelete``; a finished one with
  ``HistoryFinalDelete`` (gone for good) or, after an import, ``HistoryDelete`` (only hidden, NZBGet forgets it after
  its ``KeepHistory`` days; the files were moved by the import).
"""

from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any

import httpx

from ..schreibweisen import nfc
from .base import (
    HAND_OVER_TIMEOUT,
    CategoryJob,
    ClientBase,
    ClientError,
    HandOverUnsure,
    Job,
    auth_failed,
    longest_retention,
    percent,
    refused,
    sent_but_unanswered,
    text,
    whole,
    wrong_kind,
)

logger = logging.getLogger("nexcrate.downloaders")

RPC_PATH = "/jsonrpc"
_VERSION = re.compile(r"^\d+(\.\d+){0,3}(-[\w.]+)?$")
PRIORITY_HIGH = 50
PRIORITY_LOW = -50
QUEUED_STATUSES = frozenset({"QUEUED"})
PAUSED_STATUSES = frozenset({"PAUSED"})
#: The text after ``FAILURE/`` or ``DELETED/`` as nexcrate's code (``models.downloads.FAILED_DETAILS``).
_FAIL_CODES = {
    "PAR": "repair_failed",
    "UNPACK": "unpack_failed",
    "HEALTH": "incomplete",
    "MOVE": "other",
    "BAD": "other",
    "SCRIPT": "other",
    "MANUAL": "aborted",
    "COPY": "duplicate",
    "DUPE": "duplicate",
    "GOOD": "duplicate",
    "PASSWORD": "password",
    "DAMAGED": "repair_failed",
    "REPAIRABLE": "repair_failed",
}


def _size(item: dict[str, Any], low: str, high: str) -> int | None:
    lo, hi = whole(item.get(low)), whole(item.get(high))
    if lo is None:
        return None
    return (hi or 0) * (1 << 32) + lo


class Nzbget(ClientBase):
    kind = "nzbget"

    async def _rpc(self, method: str, *params: Any, timeout: httpx.Timeout | None = None) -> Any:
        token = base64.b64encode(f"{self.target.username}:{self.target.secret}".encode()).decode()
        extra: dict[str, Any] = {"timeout": timeout} if timeout is not None else {}
        answer = await self.call(
            "POST",
            self.address(RPC_PATH),
            content=json.dumps({"method": method, "params": list(params), "id": 1}),
            headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
            **extra,
        )
        if answer.status in (401, 403):
            raise auth_failed("wrong_password")
        if answer.status != 200:
            raise wrong_kind()
        try:
            data = json.loads(answer.body)
        except ValueError as exc:
            raise wrong_kind() from exc
        if not isinstance(data, dict) or "result" not in data:
            raise wrong_kind()
        if data.get("error"):
            raise refused()
        return data["result"]

    async def _config(self) -> dict[str, str]:
        entries = await self._rpc("config")
        if not isinstance(entries, list):
            raise wrong_kind()
        return {
            text(entry.get("Name"), 128): text(entry.get("Value"))
            for entry in entries
            if isinstance(entry, dict) and isinstance(entry.get("Name"), str)
        }

    async def version(self) -> str:
        version = text(await self._rpc("version"), 40)
        if not _VERSION.match(version):
            raise wrong_kind()
        return version

    async def categories(self) -> list[str]:
        config = await self._config()
        return [nfc(value) for name, value in config.items() if re.fullmatch(r"Category\d+\.Name", name) and value]

    def has_category(self, names: list[str]) -> bool:
        # Measured: NZBGet takes any category and files it below DestDir/<category>.
        return True

    async def create_category(self) -> None:
        """Nothing to create: see ``has_category``."""

    async def add_nzb(self, content: bytes, file_name: str, urgent: bool | None = None) -> str:
        priority = 0 if urgent is None else (PRIORITY_HIGH if urgent else PRIORITY_LOW)
        encoded = base64.b64encode(content).decode()
        try:
            number = await self._rpc(
                "append", file_name, encoded, self.target.category, priority, False, False, "", 0, "FORCE", [],
                timeout=HAND_OVER_TIMEOUT,
            )  # fmt: skip
        except ClientError as exc:
            if sent_but_unanswered(exc):
                raise HandOverUnsure(self.target.url) from exc
            raise
        found = whole(number)
        if found is None or found <= 0:
            raise refused()
        return str(found)

    def _queue_job(self, item: dict[str, Any], held: bool) -> Job | None:
        number = whole(item.get("NZBID"))
        if number is None:
            return None
        status = text(item.get("Status"), 64).upper()
        size = _size(item, "FileSizeLo", "FileSizeHi")
        left = _size(item, "RemainingSizeLo", "RemainingSizeHi")
        progress = percent((size - left) / size * 100) if size and left is not None else None
        common: dict[str, Any] = {
            "download_id": str(number), "client_state": status, "size_bytes": size, "progress": progress,
        }  # fmt: skip
        if status in PAUSED_STATUSES or (held and status in QUEUED_STATUSES | {"DOWNLOADING"}):
            return Job(state="paused", **common)
        if status in QUEUED_STATUSES:
            return Job(state="queued", **common)
        return Job(state="downloading", **common)

    @staticmethod
    def _history_job(item: dict[str, Any]) -> Job | None:
        number = whole(item.get("NZBID"))
        if number is None or text(item.get("Kind"), 16).upper() not in ("", "NZB"):
            return None
        status = text(item.get("Status"), 64).upper()
        head, _slash, tail = status.partition("/")
        common: dict[str, Any] = {
            "download_id": str(number), "client_state": status, "size_bytes": _size(item, "FileSizeLo", "FileSizeHi"),
        }  # fmt: skip
        folder = text(item.get("FinalDir")) or text(item.get("DestDir"))
        if head == "SUCCESS":
            return Job(state="completed", progress=100.0, remaining_seconds=0, path=folder or None, **common)
        if head == "WARNING" and tail == "SPACE":
            return Job(state="problem", problem="no_space", **common)
        if head in ("FAILURE", "WARNING", "DELETED"):
            return Job(
                state="failed", reason="client_failed", detail=_FAIL_CODES.get(tail, "other"),
                leftovers=(folder,) if folder else (), **common,
            )  # fmt: skip
        return Job(state="downloading", progress=100.0, **common)

    async def _both(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
        groups = await self._rpc("listgroups", 0)
        history = await self._rpc("history", False)
        status = await self._rpc("status")
        if not isinstance(groups, list) or not isinstance(history, list):
            raise wrong_kind()
        held = isinstance(status, dict) and bool(status.get("DownloadPaused"))
        return (
            [item for item in groups if isinstance(item, dict)],
            [item for item in history if isinstance(item, dict)],
            held,
        )

    def _ours(self, item: dict[str, Any]) -> bool:
        return text(item.get("Category"), 64).casefold() == self.target.category.casefold()

    async def jobs(self, download_ids: list[str]) -> dict[str, Job]:
        wanted = {download_id for download_id in download_ids if download_id}
        if not wanted:
            return {}
        groups, history, held = await self._both()
        found: dict[str, Job] = {}
        for item in groups:
            job = self._queue_job(item, held) if self._ours(item) else None
            if job is not None and job.download_id in wanted:
                found[job.download_id] = job
        for item in history:
            job = self._history_job(item) if self._ours(item) else None
            if job is not None and job.download_id in wanted:
                found[job.download_id] = job
        return found

    async def category_jobs(self) -> list[CategoryJob]:
        groups, history, held = await self._both()
        found: list[CategoryJob] = []
        for item in groups:
            job = self._queue_job(item, held) if self._ours(item) else None
            if job is not None:
                found.append(CategoryJob(name=nfc(text(item.get("NZBName"), 1024)), job=job))
        for item in history:
            job = self._history_job(item) if self._ours(item) else None
            if job is not None:
                found.append(CategoryJob(name=nfc(text(item.get("Name"), 1024)), job=job))
        return found

    async def category_folder(self) -> str | None:
        config = await self._config()
        wanted = self.target.category.casefold()
        for name, value in config.items():
            match = re.fullmatch(r"(Category\d+)\.Name", name)
            if match and value.casefold() == wanted:
                own = config.get(f"{match.group(1)}.DestDir", "")
                if own.startswith("/"):
                    return own.rstrip("/")
        destination = config.get("DestDir", "")
        if not destination.startswith("/"):
            return None
        if config.get("AppendCategoryDir", "yes").lower() == "no":
            return destination.rstrip("/")
        return destination.rstrip("/") + "/" + self.target.category

    async def usenet_retention(self) -> int | None:
        """``ServerN.Retention`` of every server with ``ServerN.Active`` set to yes, in days, 0 for unlimited."""
        config = await self._config()
        values: list[int | None] = []
        for name, value in config.items():
            match = re.fullmatch(r"(Server\d+)\.Active", name)
            if match and value.casefold() == "yes":
                values.append(whole(config.get(f"{match.group(1)}.Retention")))
        return longest_retention(values)

    async def remove(self, download_id: str, *, delete_files: bool) -> None:
        number = whole(download_id)
        if number is None:
            return
        for command in (
            "GroupFinalDelete" if delete_files else "GroupDelete",
            "HistoryFinalDelete" if delete_files else "HistoryDelete",
        ):
            try:
                await self._rpc("editqueue", command, "", [number])
            except ClientError as exc:
                if exc.code != "client_refused":
                    raise
                logger.info("NZBGet did not %s a job; it may be gone already", command)

    async def remove_imported(self, download_id: str) -> None:
        number = whole(download_id)
        if number is None:
            return
        try:
            await self._rpc("editqueue", "HistoryDelete", "", [number])
        except ClientError as exc:
            if exc.code != "client_refused":
                raise
            logger.info("NZBGet did not hide an imported job in its history")
