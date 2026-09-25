"""Deluge through the JSON-RPC of its web interface (``<url>/json``), with the web password (25.09.2026).

Measured on Deluge 2.2.0 (libtorrent 2.0.14) in the download bench, and
read in Radarr's source:

* **Login**: ``auth.login`` with the password answers ``true`` and a cookie ``_session_id``; a wrong password answers
  ``false`` with HTTP 200. Without the cookie every call answers the error ``Not authenticated`` (code 1): nexcrate logs
  in again, once. Errors come as ``{"result": null, "error": {"message", "code"}}``; code 2 is an unknown method.
* The web interface has to be **connected to a daemon**: ``web.connected``; if not, ``web.get_hosts`` lists them
  (``[id, host, port, status]``) and ``web.connect`` takes the first, as Radarr does. ``daemon.get_version`` tells the
  version (``daemon.info`` is unknown in 2.2).
* **Category** is a label of the **Label plugin**, which Deluge ships switched off. Without it ``label.*`` is an
  unknown method. Saving the client switches it on (``core.enable_plugin``) and adds the label, as qBittorrent's
  category is created; a label that exists already answers an error, which is fine. Deluge keeps labels in lower
  case, like nexcrate's categories.
* **Handing over**: ``core.add_torrent_file`` (name, base64, options) or ``core.add_torrent_magnet``, ``add_paused``
  false, Deluge's own download folder. The answer is the info hash; a torrent that is there already is the error
  ``Torrent already in session``. Then ``label.set_torrent``; without the label nexcrate would not find it again, so a
  torrent whose label cannot be set is taken out again. Something missing goes to the top (``core.queue_top``).
* **States** (``core.get_torrents_status`` with the filter ``id`` and ``label``; ids only in lower case): Downloading,
  Seeding, Paused, Queued, Checking, Allocating, Moving, Error. ``progress`` counts 0 to 100. Done is Seeding, or Paused
  and Queued with ``is_finished``; Error is the problem client_error; a size of 0 is a magnet without its metadata,
  queued. ``eta`` 0 is unknown. The finished path is ``save_path`` plus ``name``.
* **Removing**: ``core.remove_torrent`` with ``remove_data``; an unknown hash is the error ``not in session``.
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
    CategoryJob,
    ClientBase,
    ClientError,
    DuplicateTorrent,
    HandOverUnsure,
    Job,
    auth_failed,
    category_failed,
    number,
    percent,
    refused,
    sent_but_unanswered,
    text,
    whole,
    wrong_kind,
)

logger = logging.getLogger("nexcrate.downloaders")

NOT_AUTHENTICATED = 1
UNKNOWN_METHOD = 2
LABEL_PLUGIN = "Label"
QUEUED_STATES = frozenset({"queued", "checking", "allocating", "moving"})
KEYS = ("hash", "name", "state", "progress", "eta", "total_wanted", "total_size", "save_path", "label", "is_finished")
_VERSION = re.compile(r"^(\d+(?:\.\d+){0,3})")
_HASH = re.compile(r"^[0-9a-f]{40}$")


def json_url(base: str) -> str:
    trimmed = base.rstrip("/")
    return trimmed if urlsplit(trimmed).path.endswith("/json") else trimmed + "/json"


class DelugeError(ClientError):
    """An error Deluge answered, with its code; its message is never shown and never logged whole."""

    def __init__(self, code: int | None, message: str) -> None:
        super().__init__(refused().detail)
        self.deluge_code = code
        self.deluge_message = message


class Deluge(ClientBase):
    kind = "deluge"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._logged_in = False
        self._counter = 0

    async def _raw(self, method: str, *params: Any, **extra: Any) -> Any:
        self._counter += 1
        body = json.dumps({"method": method, "params": list(params), "id": self._counter})
        answer = await self.call(
            "POST", json_url(self.target.url), content=body, headers={"Content-Type": "application/json"}, **extra
        )
        if answer.status != 200:
            raise wrong_kind()
        try:
            data = json.loads(answer.body)
        except ValueError as exc:
            raise wrong_kind() from exc
        if not isinstance(data, dict) or "result" not in data or "error" not in data:
            raise wrong_kind()
        error = data["error"]
        if isinstance(error, dict):
            raise DelugeError(whole(error.get("code")), text(error.get("message"), 400))
        return data["result"]

    async def _login(self) -> None:
        if await self._raw("auth.login", self.target.secret) is not True:
            raise auth_failed("wrong_password")
        self._logged_in = True
        if await self._raw("web.connected") is True:
            return
        hosts = await self._raw("web.get_hosts")
        if not isinstance(hosts, list) or not hosts or not isinstance(hosts[0], list) or not hosts[0]:
            raise refused("no_daemon")
        await self._raw("web.connect", text(hosts[0][0], 64))
        if await self._raw("web.connected") is not True:
            raise refused("no_daemon")

    async def _rpc(self, method: str, *params: Any, **extra: Any) -> Any:
        if not self._logged_in:
            await self._login()
        try:
            return await self._raw(method, *params, **extra)
        except DelugeError as exc:
            if exc.deluge_code != NOT_AUTHENTICATED:
                raise
        # The session ran out: log in again, once.
        self._logged_in = False
        await self._login()
        return await self._raw(method, *params, **extra)

    # --- The operations ------------------------------------------------------------------------- #

    async def version(self) -> str:
        found = _VERSION.match(text(await self._rpc("daemon.get_version"), 64))
        if found is None:
            raise wrong_kind()
        return found.group(1)

    async def categories(self) -> list[str]:
        plugins = await self._rpc("core.get_enabled_plugins")
        if not isinstance(plugins, list) or LABEL_PLUGIN not in plugins:
            return []
        labels = await self._rpc("label.get_labels")
        return [nfc(label) for label in labels if isinstance(label, str)] if isinstance(labels, list) else []

    async def create_category(self) -> None:
        try:
            await self._rpc("core.enable_plugin", LABEL_PLUGIN)
            await self._rpc("label.add", self.target.category)
        except DelugeError as exc:
            if "already exists" not in exc.deluge_message:
                raise category_failed(self.target.category) from exc

    async def add_torrent(
        self, *, content: bytes | None, magnet: str | None, file_name: str, info_hash: str, urgent: bool | None = None
    ) -> str:
        options = {"add_paused": False}
        try:
            if content is not None:
                added = await self._rpc(
                    "core.add_torrent_file", file_name, base64.b64encode(content).decode(), options,
                    timeout=HAND_OVER_TIMEOUT,
                )  # fmt: skip
            elif magnet:
                added = await self._rpc("core.add_torrent_magnet", magnet, options, timeout=HAND_OVER_TIMEOUT)
            else:
                raise refused()
        except DelugeError as exc:
            if "already in session" in exc.deluge_message and info_hash:
                found = info_hash.lower()
                ours = await self._label_of(found) == self.target.category
                raise DuplicateTorrent(found, category_matches=ours) from exc
            raise refused() from exc
        except ClientError as exc:
            if sent_but_unanswered(exc) and info_hash:
                raise HandOverUnsure(self.target.url, info_hash.lower()) from exc
            raise
        found = text(added, 64).lower()
        if not _HASH.match(found):
            raise refused()
        try:
            await self._rpc("label.set_torrent", found, self.target.category)
        except DelugeError as exc:
            # Without its label nexcrate would never find the torrent again: it goes out, the files with it.
            logger.warning("Deluge did not label torrent %s; it is removed again", found[:8])
            await self._rpc("core.remove_torrent", found, True)
            raise category_failed(self.target.category) from exc
        if urgent:
            try:
                await self._rpc("core.queue_top", [found])
            except DelugeError:
                logger.info("Deluge did not move torrent %s to the top", found[:8])
        return found

    async def _label_of(self, info_hash: str) -> str | None:
        status = await self._rpc("core.get_torrents_status", {"id": [info_hash]}, ["label"])
        entry = status.get(info_hash) if isinstance(status, dict) else None
        return text(entry.get("label"), 64) if isinstance(entry, dict) else None

    async def _torrents(self, ids: list[str] | None = None) -> dict[str, Any]:
        wanted: dict[str, Any] = {"label": self.target.category}
        if ids is not None:
            wanted["id"] = ids
        status = await self._rpc("core.get_torrents_status", wanted, list(KEYS))
        if not isinstance(status, dict):
            raise wrong_kind()
        return status

    async def jobs(self, download_ids: list[str]) -> dict[str, Job]:
        wanted = sorted({download_id.lower() for download_id in download_ids if download_id})
        if not wanted:
            return {}
        found: dict[str, Job] = {}
        for item in (await self._torrents(wanted)).values():
            job = self._job(item) if isinstance(item, dict) else None
            if job is not None and job.download_id in wanted:
                found[job.download_id] = job
        return found

    async def category_jobs(self) -> list[CategoryJob]:
        found: list[CategoryJob] = []
        for item in (await self._torrents()).values():
            job = self._job(item) if isinstance(item, dict) else None
            if job is not None:
                found.append(CategoryJob(name=nfc(text(item.get("name"), 1024)), job=job))
        return found

    @staticmethod
    def _job(item: dict[str, Any]) -> Job | None:
        download_id = text(item.get("hash"), 64).lower()
        if not download_id:
            return None
        state = text(item.get("state"), 64)
        lowered = state.casefold()
        progress = number(item.get("progress"))
        eta = whole(item.get("eta"))
        size = whole(item.get("total_wanted")) or whole(item.get("total_size"))
        finished = item.get("is_finished") is True
        common: dict[str, Any] = {
            "download_id": download_id,
            "client_state": state,
            "size_bytes": size if size is not None and size > 0 else None,
            "progress": percent(progress),
        }
        if lowered == "error":
            return Job(state="problem", problem="client_error", **common)
        if size is None or size <= 0:
            return Job(state="queued", **common)
        if lowered == "seeding" or (finished and lowered in ("paused", "queued")):
            folder, name = text(item.get("save_path")), text(item.get("name"), 1024)
            if not folder or not name:
                return Job(state="problem", problem="path_not_found", remaining_seconds=0, **common)
            separator = "\\" if "\\" in folder and "/" not in folder else "/"
            path = folder.rstrip("/\\") + separator + name
            return Job(state="completed", remaining_seconds=0, path=path, **common)
        remaining = eta if eta is not None and eta > 0 else None
        if lowered in QUEUED_STATES:
            return Job(state="queued", remaining_seconds=remaining, **common)
        if lowered == "paused":
            return Job(state="paused", **common)
        return Job(state="downloading", remaining_seconds=remaining, **common)

    async def remove(self, download_id: str, *, delete_files: bool) -> None:
        try:
            await self._rpc("core.remove_torrent", download_id.lower(), delete_files)
        except DelugeError as exc:
            if "not in session" not in exc.deluge_message:
                raise
            logger.info("Deluge did not have torrent %s any more", download_id[:8])
