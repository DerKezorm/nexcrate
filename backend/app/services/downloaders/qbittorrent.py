"""qBittorrent through its Web API v2.

Measured on qBittorrent 5.2.3, WebAPI 2.15.1 (14.09.2026) and read in Radarr's source:

* Login with user name and password (``auth/login``): 204 with a session cookie named after the WebUI port, which the
  cookie jar keeps whatever its name. A 403 later logs in again, once. A 401 is a wrong password: nexcrate stops there,
  and does not log in again (``Target.login_blocked``) until the client is saved or tested, since five failures ban the
  address for an hour. Without a user name and a password nexcrate does not log in (qBittorrent's bypass).
* ⚠️ Never an ``Origin`` or ``Referer`` header: a foreign one is 401, even with the right password. The login body
  carries the password; no request body is ever logged.
* ``app/webapiVersion`` tells the kind and the version.
* A category is created with ``torrents/createCategory`` and an empty ``savePath`` and must then be in
  ``torrents/categories``; a second create answers 409.
* A torrent goes up as a file (field ``torrents``, named like the cleaned release title) or a magnet (``urls``), with
  ``category`` and ``stopped=false`` (``paused=false`` below WebAPI 2.11.0; ``paused`` is ignored in 5.x). Never a save
  path, never ``autoTMM``: qBittorrent's own setting decides, and ``content_path`` tells nexcrate. Exactly ``Fails.`` is
  a refusal, 415 an invalid file, 409 a torrent that is there already (``DuplicateTorrent``). The download id is the
  info hash nexcrate computed, in lower case.
* States: metaDL, forcedMetaDL, queuedDL, checkingDL, checkingUP and checkingResumeData are queued (a magnet without
  metadata has ``amount_left`` 0 and is not done); stoppedDL and pausedDL paused; stalledDL downloading with the hint
  stalled; uploading, stalledUP, queuedUP, forcedUP, stoppedUP and pausedUP completed only with ``progress`` 1; error
  and missingFiles the problem client_error; everything else downloading. An ``eta`` of 8640000 is unknown.
* The finished path is ``content_path``; equal to ``save_path``, or missing, it is the problem path_not_found.
* ``torrents/delete`` needs ``deleteFiles``. Only a download that was not imported is ever removed.
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
    DuplicateTorrent,
    HandOverUnsure,
    Job,
    auth_failed,
    category_failed,
    file_invalid,
    number,
    percent,
    refused,
    sent_but_unanswered,
    text,
    whole,
    wrong_kind,
)

logger = logging.getLogger("nexcrate.downloaders")

LOGIN_OK_TEXTS = ("", "Ok.")
REFUSED_TEXT = "Fails."
#: From this WebAPI version on the start parameter is ``stopped``, below it ``paused``.
STOPPED_SINCE = (2, 11, 0)
#: qBittorrent's ETA for "unknown".
ETA_UNKNOWN = 8_640_000

QUEUED_STATES = frozenset({"metadl", "forcedmetadl", "queueddl", "checkingdl", "checkingup", "checkingresumedata"})
PAUSED_STATES = frozenset({"stoppeddl", "pauseddl"})
COMPLETED_STATES = frozenset({"uploading", "stalledup", "queuedup", "forcedup", "stoppedup", "pausedup"})
ERROR_STATES = frozenset({"error", "missingfiles"})
STALLED_STATE = "stalleddl"
_WEBAPI = re.compile(r"^\d+(?:\.\d+)+$")


def version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split(".")) if _WEBAPI.match(value) else ()


def _same_path(first: str, second: str) -> bool:
    return first.rstrip("/\\") == second.rstrip("/\\")


class Qbittorrent(ClientBase):
    kind = "qbittorrent"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._logged_in = False
        self._webapi: tuple[int, ...] | None = None

    async def _login(self) -> None:
        if self.target.login_blocked:
            raise auth_failed("wrong_password")
        if not self.target.username and not self.target.secret:
            self._logged_in = True
            return
        # ⚠️ No Origin, no Referer: qBittorrent answers a foreign one with 401 as if the password were wrong.
        answer = await self.call(
            "POST",
            self.address("/api/v2/auth/login"),
            data={"username": self.target.username, "password": self.target.secret},
        )
        if answer.status in (401, 403) or answer.text == REFUSED_TEXT:
            logger.warning("qBittorrent refused the login; no new try until the client is saved or tested")
            raise auth_failed("wrong_password")
        if answer.status in (200, 204) and answer.text in LOGIN_OK_TEXTS:
            self._logged_in = True
            return
        raise wrong_kind()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Answer:
        if not self._logged_in:
            await self._login()
        answer = await self.call(method, self.address(path), **kwargs)
        if answer.status == 403:
            # The session ran out: log in again, once.
            self._logged_in = False
            await self._login()
            answer = await self.call(method, self.address(path), **kwargs)
        if answer.status in (401, 403):
            raise auth_failed("wrong_password")
        return answer

    async def _json(self, path: str, **params: Any) -> Any:
        answer = await self._request("GET", path, params=params or None)
        if answer.status != 200:
            raise wrong_kind()
        try:
            return json.loads(answer.body)
        except ValueError as exc:
            raise wrong_kind() from exc

    async def _webapi_version(self) -> tuple[int, ...]:
        if self._webapi is None:
            await self.version()
        return self._webapi or ()

    # --- The operations ------------------------------------------------------------------------- #

    async def version(self) -> str:
        answer = await self._request("GET", "/api/v2/app/webapiVersion")
        version = answer.text
        if answer.status != 200 or len(version) > 20 or not _WEBAPI.match(version):
            raise wrong_kind()
        self._webapi = version_tuple(version)
        return version

    async def categories(self) -> list[str]:
        data = await self._json("/api/v2/torrents/categories")
        if not isinstance(data, dict):
            raise wrong_kind()
        return [nfc(name) for name in data if isinstance(name, str)]

    async def create_category(self) -> None:
        answer = await self._request(
            "POST", "/api/v2/torrents/createCategory", data={"category": self.target.category, "savePath": ""}
        )
        # 409: it exists already, which is what nexcrate wants.
        if answer.status not in (200, 409):
            raise category_failed(self.target.category)

    async def _category_of(self, info_hash: str) -> str | None:
        data = await self._json("/api/v2/torrents/info", hashes=info_hash.lower())
        for item in data if isinstance(data, list) else []:
            if isinstance(item, dict) and text(item.get("hash"), 64).lower() == info_hash.lower():
                return text(item.get("category"), 64)
        return None

    async def add_torrent(
        self, *, content: bytes | None, magnet: str | None, file_name: str, info_hash: str, urgent: bool | None = None
    ) -> str:
        start = "stopped" if await self._webapi_version() >= STOPPED_SINCE else "paused"
        data: dict[str, str] = {"category": self.target.category, start: "false"}
        files: dict[str, Any] | None = None
        if content is not None:
            files = {"torrents": (file_name, content, "application/x-bittorrent")}
        elif magnet:
            data["urls"] = magnet
        else:
            raise refused()
        try:
            answer = await self._request(
                "POST", "/api/v2/torrents/add", data=data, files=files, timeout=HAND_OVER_TIMEOUT
            )
        except ClientError as exc:
            # ⚠️ Sent, but no answer in time: qBittorrent may have taken it. Its id is the info hash nexcrate computed,
            # and the tracking looks for it (the owner's finding 1 of 22.09.2026).
            if sent_but_unanswered(exc) and info_hash:
                raise HandOverUnsure(self.target.url, info_hash.lower()) from exc
            raise
        if answer.status == 415:
            raise file_invalid()
        if answer.status == 409:
            category = await self._category_of(info_hash) if info_hash else None
            raise DuplicateTorrent(info_hash.lower(), category_matches=category == self.target.category)
        if answer.status != 200 or answer.text == REFUSED_TEXT or not info_hash:
            raise refused()
        if urgent:
            await self._to_the_top(info_hash.lower())
        return info_hash.lower()

    async def _to_the_top(self, info_hash: str) -> None:
        """A torrent that brings something missing goes to the top of qBittorrent's queue (24.09.2026); a new torrent
        starts at the bottom anyway. With queueing off qBittorrent answers 409 and has no queue: nothing to do. Never
        fails the hand-over, the torrent is in."""
        try:
            await self._request("POST", "/api/v2/torrents/topPrio", data={"hashes": info_hash})
        except ClientError as exc:
            logger.info("qBittorrent did not move torrent %s to the top: %s", info_hash[:8], exc.code)

    async def jobs(self, download_ids: list[str]) -> dict[str, Job]:
        wanted = {download_id.lower() for download_id in download_ids if download_id}
        if not wanted:
            return {}
        data = await self._json("/api/v2/torrents/info", category=self.target.category, hashes="|".join(sorted(wanted)))
        if not isinstance(data, list):
            raise wrong_kind()
        found: dict[str, Job] = {}
        for item in data:
            job = self._job(item) if isinstance(item, dict) else None
            if job is not None and job.download_id in wanted:
                found[job.download_id] = job
        return found

    async def category_jobs(self) -> list[CategoryJob]:
        data = await self._json("/api/v2/torrents/info", category=self.target.category)
        if not isinstance(data, list):
            raise wrong_kind()
        found: list[CategoryJob] = []
        for item in data:
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
        remaining = eta if eta is not None and 0 <= eta < ETA_UNKNOWN else None
        size = whole(item.get("size"))
        if size is None or size <= 0:
            size = whole(item.get("total_size"))
        common: dict[str, Any] = {
            "download_id": download_id,
            "client_state": state,
            "size_bytes": size if size is not None and size > 0 else None,
            "progress": percent(progress * 100 if progress is not None else None),
        }
        if lowered in ERROR_STATES:
            return Job(state="problem", problem="client_error", **common)
        if lowered in COMPLETED_STATES and progress is not None and progress >= 1:
            content_path, save_path = text(item.get("content_path")), text(item.get("save_path"))
            if not content_path or (save_path and _same_path(content_path, save_path)):
                return Job(state="problem", problem="path_not_found", remaining_seconds=0, **common)
            return Job(state="completed", remaining_seconds=0, path=content_path, **common)
        if lowered in QUEUED_STATES:
            return Job(state="queued", remaining_seconds=remaining, **common)
        if lowered in PAUSED_STATES:
            return Job(state="paused", **common)
        if lowered == STALLED_STATE:
            return Job(state="downloading", problem="stalled", remaining_seconds=remaining, **common)
        return Job(state="downloading", remaining_seconds=remaining, **common)

    async def remove(self, download_id: str, *, delete_files: bool) -> None:
        answer = await self._request(
            "POST",
            "/api/v2/torrents/delete",
            data={"hashes": download_id.lower(), "deleteFiles": "true" if delete_files else "false"},
        )
        if answer.status != 200:
            logger.info("qBittorrent did not remove a torrent (HTTP %d)", answer.status)
