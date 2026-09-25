"""SABnzbd through its API (``<url>/api``), JSON only.

Measured on SABnzbd 5.1.3 (14.09.2026) and read in Radarr's source:

* ``mode=version`` answers without a key and tells the kind. Every other call needs the full API key: without a key or
  with a wrong one SABnzbd answers 403 with a plain text, and the NZB key gets 403 on queue, history and categories. A
  403 ``Access denied - Hostname verification failed`` means nexcrate's host name is not on SABnzbd's whitelist
  (``client_host_refused``). For any other 403, ``mode=auth`` tells whether the given key is the NZB key.
* Categories: names are stored in lower case. A missing one is created with ``set_config`` (``keyword`` and ``dir`` the
  name, ``pp`` 3, ``script`` None, ``priority`` -100) and must then be in ``get_cats``. A category folder ending in
  ``*`` switches job folders off, and sorting renames and moves files: both make it unusable.
* An NZB goes up as a file (``mode=addfile``, field ``name``) named like the cleaned release title, with ``cat`` and
  nothing else. The id is the first ``nzo_ids`` entry; ``status false`` or no id is a refusal. ⚠️ A job with an unknown
  category is accepted silently and put into ``*``: its ``cat`` is read back at once, and a job in ``*`` is removed
  again.
* Queue: Queued, Grabbing and Propagating are queued; a paused item, or a paused queue for an item that is not forced,
  is paused; every other status is downloading. Numbers are texts.
* History (filtered by ``category`` and ``nzo_ids``): Completed with a ``storage`` is completed, with an empty one still
  downloading; Failed is failed, except the unpack message about a full disk, which is the problem no_space; every
  post-processing status is downloading. A job name starting with ``ENCRYPTED /`` failed as encrypted.
* ``storage`` is the file of a job that left one file, the job folder of one that left several. It is walked up to the
  parent folder named like the job.
* Removing: a queued job with ``del_files=1``; a finished job from the history with ``archive=0`` (and ``del_files=1``
  for a job that was not imported, which removes a failed job's incomplete folder). A history delete never removes a
  completed job's files: that is nexcrate's job after the import.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from ..schreibweisen import nfc
from .base import (
    HAND_OVER_TIMEOUT,
    Answer,
    CategoryJob,
    ClientBase,
    ClientError,
    HandOverUnsure,
    Job,
    auth_failed,
    category_failed,
    category_unusable,
    host_refused,
    longest_retention,
    number,
    percent,
    refused,
    sent_but_unanswered,
    text,
    unreachable,
    whole,
    wrong_kind,
)

logger = logging.getLogger("nexcrate.downloaders")

API_PATH = "/api"
MIB = 1024 * 1024
#: The history is filtered by nexcrate's job ids; this only caps a broken answer.
HISTORY_LIMIT = 500
QUEUED_STATUSES = frozenset({"queued", "grabbing", "propagating"})
NO_SPACE_MESSAGE = "unpacking failed, write error or disk is full?"
ENCRYPTED_PREFIX = "ENCRYPTED /"
HOST_REFUSED_TEXT = "hostname verification failed"
_TIME_LEFT = re.compile(r"^(?:(\d+):)?(\d+):(\d{1,2}):(\d{1,2})$")
_VERSION = re.compile(r"^\d+\.\d+")


class _Rejected(Exception):
    """SABnzbd answered with an error that is not about the key. Its text is never logged or answered."""


class _KeyRefused(Exception):
    """SABnzbd refused the key."""


def seconds_left(value: Any) -> int | None:
    """``0:10:32`` or ``1:02:03:04`` (days first) as seconds."""
    found = _TIME_LEFT.match(text(value, 32))
    if found is None:
        return None
    days, hours, minutes, seconds = (int(part) if part else 0 for part in found.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def job_path(storage: str, name: str) -> str:
    """The finished path: ``storage``, or the nearest folder above it named like the job."""
    if not storage:
        return storage
    separator = "\\" if "\\" in storage and "/" not in storage else "/"
    parts = storage.rstrip("/\\").split(separator)
    wanted = nfc(name)
    for end in range(len(parts), 1, -1):
        if parts[end - 1] and nfc(parts[end - 1]) == wanted:
            return separator.join(parts[:end])
    return storage


#: SABnzbd's ``fail_message`` as a code, by the start of its sentence (SABnzbd 4 and 5). The sentence itself is
#: never kept, logged or answered; the interface says it in the owner's language.
_FAIL_CODES = (
    ("repair failed", "repair_failed"),
    ("not enough repair", "repair_failed"),
    ("aborted, cannot be completed", "incomplete"),
    ("download might fail", "incomplete"),
    ("download failed - not on your server", "not_on_server"),
    ("unpacking failed, archive requires a password", "password"),
    ("aborted, encryption detected", "encrypted"),
    ("aborted, unwanted extension", "unwanted_extension"),
    ("duplicate", "duplicate"),
    ("unpacking failed", "unpack_failed"),
    ("post-processing was aborted", "aborted"),
    ("aborted", "aborted"),
)


def fail_code(message: str) -> str | None:
    """A code for SABnzbd's ``fail_message``; ``other`` for one nexcrate does not know, None for none."""
    lowered = message.strip().casefold()
    if not lowered:
        return None
    return next((code for start, code in _FAIL_CODES if lowered.startswith(start)), "other")


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    return isinstance(value, str) and value.strip().lower() in ("1", "true", "yes", "on")


def _names(values: Any) -> set[str]:
    return {text(name, 64).casefold() for name in values or [] if isinstance(name, str)}


#: SABnzbd's priorities of a job: 1 high, -1 low (0 normal, 2 force, -100 the category's).
PRIORITY_HIGH = "1"
PRIORITY_LOW = "-1"


class Sabnzbd(ClientBase):
    kind = "sabnzbd"

    def _read(self, answer: Answer) -> Any:
        body = answer.text
        lowered = body.casefold()
        if answer.status in (401, 403):
            if HOST_REFUSED_TEXT in lowered:
                raise host_refused()
            raise _KeyRefused
        if answer.status == 404 or 300 <= answer.status < 400:
            raise wrong_kind()
        if answer.status >= 500:
            raise unreachable(self.target.url)
        if lowered.startswith("error"):
            if "api key" in lowered:
                raise _KeyRefused
            raise _Rejected
        try:
            data = json.loads(body)
        except ValueError as exc:
            raise wrong_kind() from exc
        if isinstance(data, dict) and data.get("status") is False:
            if "api key" in text(data.get("error"), 500).casefold():
                raise _KeyRefused
            raise _Rejected
        if not 200 <= answer.status < 300:
            raise wrong_kind()
        return data

    async def _key_reason(self) -> str:
        """``nzb_key`` when SABnzbd calls the given key its NZB key, else ``wrong_key``."""
        if not self.target.secret:
            return "wrong_key"
        try:
            answer = await self.call(
                "GET", self.address(API_PATH), params={"mode": "auth", "key": self.target.secret, "output": "json"}
            )
            data = json.loads(answer.body)
        except ValueError:
            return "wrong_key"
        return "nzb_key" if isinstance(data, dict) and data.get("auth") == "nzbkey" else "wrong_key"

    async def _call(
        self, mode: str, *, method: str = "GET", files: Any = None, key: bool = True, timeout: Any = None, **params: Any
    ) -> Any:
        query: dict[str, Any] = {"mode": mode, **params, "output": "json"}
        if key and self.target.secret:
            query["apikey"] = self.target.secret
        extra: dict[str, Any] = {"files": files} if files is not None else {}
        if timeout is not None:
            extra["timeout"] = timeout
        answer = await self.call(method, self.address(API_PATH), params=query, **extra)
        try:
            return self._read(answer)
        except _KeyRefused:
            raise auth_failed(await self._key_reason()) from None

    async def _config(self, section: str) -> Any:
        try:
            data = await self._call("get_config", section=section)
        except _Rejected:
            return None
        config = data.get("config") if isinstance(data, dict) else None
        return config.get(section) if isinstance(config, dict) else None

    # --- The operations ------------------------------------------------------------------------- #

    async def version(self) -> str:
        try:
            data = await self._call("version", key=False)
        except _Rejected as exc:
            raise wrong_kind() from exc
        version = text(data.get("version"), 40) if isinstance(data, dict) else ""
        if not _VERSION.match(version):
            raise wrong_kind()
        return version

    async def categories(self) -> list[str]:
        try:
            data = await self._call("get_cats")
        except _Rejected as exc:
            raise wrong_kind() from exc
        names = data.get("categories") if isinstance(data, dict) else data
        if not isinstance(names, list):
            raise wrong_kind()
        return [nfc(name) for name in names if isinstance(name, str)]

    def has_category(self, names: list[str]) -> bool:
        wanted = self.target.category.casefold()
        return any(name.casefold() == wanted for name in names)

    async def create_category(self) -> None:
        name = self.target.category
        try:
            await self._call(
                "set_config", section="categories", keyword=name, dir=name, pp="3", script="None", priority="-100"
            )
        except _Rejected as exc:
            raise category_failed(name) from exc

    def _category_entry(self, entries: Any) -> dict[str, Any] | None:
        wanted = self.target.category.casefold()
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict) and text(entry.get("name"), 64).casefold() == wanted:
                return entry
        return None

    async def check_category(self) -> None:
        entry = self._category_entry(await self._config("categories"))
        if entry is not None and text(entry.get("dir")).endswith("*"):
            raise category_unusable("no_job_folders")
        wanted = self.target.category.casefold()
        # SABnzbd 4.1 and later: sorters with the categories they apply to. The shape is not measured; unknown shapes
        # count as no sorting.
        sorters = await self._config("sorters")
        for sorter in sorters if isinstance(sorters, list) else []:
            if isinstance(sorter, dict) and _truthy(sorter.get("is_active")):
                names = _names(sorter.get("sort_cats"))
                if wanted in names or "*" in names:
                    raise category_unusable("sorting")
        misc = await self._config("misc")
        if isinstance(misc, dict):
            for kind in ("movie", "tv", "date"):
                names = _names(misc.get(f"{kind}_categories"))
                if _truthy(misc.get(f"enable_{kind}_sorting")) and (wanted in names or "*" in names):
                    raise category_unusable("sorting")

    async def _job_category(self, download_id: str) -> str | None:
        """The category SABnzbd gave a job, from the queue or the history; None when the job is not found."""
        for mode, field_name in (("queue", "cat"), ("history", "category")):
            try:
                data = await self._call(mode, nzo_ids=download_id, start=0, limit=HISTORY_LIMIT)
            except _Rejected:
                continue
            part = data.get(mode) if isinstance(data, dict) else None
            slots = part.get("slots") if isinstance(part, dict) else None
            for slot in slots if isinstance(slots, list) else []:
                if isinstance(slot, dict) and text(slot.get("nzo_id"), 128) == download_id:
                    return text(slot.get(field_name), 64)
        return None

    async def add_nzb(self, content: bytes, file_name: str, urgent: bool | None = None) -> str:
        category = self.target.category
        # SABnzbd sorts its queue by priority: what is missing goes before upgrades already waiting (24.09.2026).
        priority = {} if urgent is None else {"priority": PRIORITY_HIGH if urgent else PRIORITY_LOW}
        try:
            data = await self._call(
                "addfile",
                method="POST",
                files={"name": (file_name, content, "application/x-nzb")},
                timeout=HAND_OVER_TIMEOUT,
                cat=category,
                **priority,
            )
        except _Rejected as exc:
            raise refused() from exc
        except ClientError as exc:
            # ⚠️ Sent, but no answer in time: SABnzbd may have taken it. The tracking looks for the job by its name.
            if sent_but_unanswered(exc):
                raise HandOverUnsure(self.target.url) from exc
            raise
        ids = data.get("nzo_ids") if isinstance(data, dict) else None
        first = ids[0] if isinstance(ids, list) and ids else None
        if not isinstance(first, str) or not first.strip() or len(first) > 128:
            raise refused()
        download_id = first.strip()
        # ⚠️ An unknown category is accepted silently and the job put into *.
        given = await self._job_category(download_id)
        if given is not None and given.casefold() != category.casefold():
            logger.warning("SABnzbd put a new job outside nexcrate's category; the job is removed again")
            await self.remove(download_id, delete_files=True)
            raise category_failed(category)
        return download_id

    async def jobs(self, download_ids: list[str]) -> dict[str, Job]:
        wanted = {download_id for download_id in download_ids if download_id}
        if not wanted:
            return {}
        ids = ",".join(sorted(wanted))
        try:
            queue = await self._call("queue", start=0, limit=0, category=self.target.category, nzo_ids=ids)
            history = await self._call(
                "history", start=0, limit=HISTORY_LIMIT, category=self.target.category, nzo_ids=ids
            )
        except _Rejected as exc:
            raise wrong_kind() from exc
        found: dict[str, Job] = {}
        queue_part = queue.get("queue") if isinstance(queue, dict) else None
        queue_part = queue_part if isinstance(queue_part, dict) else {}
        queue_paused = _truthy(queue_part.get("paused"))
        for slot in queue_part.get("slots") or []:
            job = self._queue_job(slot, queue_paused) if isinstance(slot, dict) else None
            if job is not None and job.download_id in wanted:
                found[job.download_id] = job
        history_part = history.get("history") if isinstance(history, dict) else None
        history_part = history_part if isinstance(history_part, dict) else {}
        for slot in history_part.get("slots") or []:
            job = self._history_job(slot) if isinstance(slot, dict) else None
            if job is not None and job.download_id in wanted:
                found[job.download_id] = job
        return found

    async def category_jobs(self) -> list[CategoryJob]:
        category = self.target.category
        try:
            queue = await self._call("queue", start=0, limit=0, category=category)
            history = await self._call("history", start=0, limit=HISTORY_LIMIT, category=category)
        except _Rejected as exc:
            raise wrong_kind() from exc
        found: list[CategoryJob] = []
        queue_part = queue.get("queue") if isinstance(queue, dict) else None
        queue_part = queue_part if isinstance(queue_part, dict) else {}
        queue_paused = _truthy(queue_part.get("paused"))
        wanted = category.casefold()
        for slot in queue_part.get("slots") or []:
            if not isinstance(slot, dict) or text(slot.get("cat"), 64).casefold() != wanted:
                continue
            job = self._queue_job(slot, queue_paused)
            if job is not None:
                found.append(CategoryJob(name=nfc(text(slot.get("filename"), 1024)), job=job))
        history_part = history.get("history") if isinstance(history, dict) else None
        history_part = history_part if isinstance(history_part, dict) else {}
        for slot in history_part.get("slots") or []:
            if not isinstance(slot, dict) or text(slot.get("category"), 64).casefold() != wanted:
                continue
            job = self._history_job(slot)
            if job is not None:
                found.append(CategoryJob(name=nfc(text(slot.get("name"), 1024)), job=job))
        return found

    @staticmethod
    def _queue_job(slot: dict[str, Any], queue_paused: bool) -> Job | None:
        download_id = text(slot.get("nzo_id"), 128)
        status = text(slot.get("status"), 64)
        lowered = status.casefold()
        if not download_id or lowered == "deleted":
            return None
        size_mb, left_mb = number(slot.get("mb")), number(slot.get("mbleft"))
        size = int(size_mb * MIB) if size_mb is not None else None
        common: dict[str, Any] = {"download_id": download_id, "client_state": status, "size_bytes": size}
        if text(slot.get("filename"), 1024).startswith(ENCRYPTED_PREFIX):
            return Job(state="failed", reason="encrypted", **common)
        progress = number(slot.get("percentage"))
        if progress is None and size_mb and left_mb is not None:
            progress = (size_mb - left_mb) / size_mb * 100
        common["progress"] = percent(progress)
        forced = text(slot.get("priority"), 32).casefold() in ("force", "2")
        if lowered == "paused" or (queue_paused and not forced):
            return Job(state="paused", **common)
        state = "queued" if lowered in QUEUED_STATUSES else "downloading"
        return Job(state=state, remaining_seconds=seconds_left(slot.get("timeleft")), **common)

    @staticmethod
    def _history_job(slot: dict[str, Any]) -> Job | None:
        download_id = text(slot.get("nzo_id"), 128)
        if not download_id:
            return None
        status = text(slot.get("status"), 64)
        lowered = status.casefold()
        name = text(slot.get("name"), 1024)
        common: dict[str, Any] = {
            "download_id": download_id,
            "client_state": status,
            "size_bytes": whole(slot.get("bytes")),
        }
        if name.startswith(ENCRYPTED_PREFIX):
            return Job(state="failed", reason="encrypted", **common)
        if lowered == "completed":
            storage = text(slot.get("storage"))
            if not storage:
                # ⚠️ Measured: at the first Completed the storage can still be empty for a second.
                return Job(state="downloading", progress=100.0, **common)
            return Job(state="completed", progress=100.0, remaining_seconds=0, path=job_path(storage, name), **common)
        if lowered == "failed":
            message = text(slot.get("fail_message"), 500)
            if message.casefold().startswith(NO_SPACE_MESSAGE):
                return Job(state="problem", problem="no_space", **common)
            leftovers = tuple(path for path in (text(slot.get("storage")), text(slot.get("path"))) if path)
            return Job(state="failed", reason="client_failed", detail=fail_code(message), leftovers=leftovers, **common)
        # Verifying, repairing, extracting, moving, running a script: still working on it.
        return Job(state="downloading", progress=100.0, **common)

    async def category_folder(self) -> str | None:
        """``complete_dir`` joined with the category's ``dir`` (or its name), as SABnzbd names it. None when either is
        missing or ``complete_dir`` is relative (to SABnzbd's own folder, which nexcrate does not know)."""
        misc = await self._config("misc")
        complete = text(misc.get("complete_dir")) if isinstance(misc, dict) else ""
        entry = self._category_entry(await self._config("categories"))
        folder = text(entry.get("dir")) if entry is not None else ""
        folder = folder or self.target.category
        if folder.startswith("/"):
            return folder.rstrip("/")
        if not complete.startswith("/"):
            return None
        return complete.rstrip("/") + "/" + folder.strip("/")

    async def usenet_retention(self) -> int | None:
        """``retention`` of every server with ``enable``, in days, 0 for unlimited (measured on SABnzbd 5.1.3). The
        section also holds the servers' passwords: only these two fields are read, nothing of it is logged."""
        servers = await self._config("servers")
        listed = servers if isinstance(servers, list) else []
        active = [item for item in listed if isinstance(item, dict) and whole(item.get("enable"))]
        return longest_retention([whole(item.get("retention")) for item in active])

    async def remove(self, download_id: str, *, delete_files: bool) -> None:
        del_files = "1" if delete_files else "0"
        for mode, extra in (("queue", {}), ("history", {"archive": "0"})):
            try:
                await self._call(mode, name="delete", value=download_id, del_files=del_files, **extra)
            except _Rejected:
                logger.info("SABnzbd did not remove a job from its %s; it may be gone already", mode)

    async def remove_imported(self, download_id: str) -> None:
        try:
            await self._call("history", name="delete", value=download_id, archive="0")
        except _Rejected:
            logger.info("SABnzbd did not remove an imported job from its history")
