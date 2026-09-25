"""Fetching a release file from its indexer: the NZB, the torrent, or the magnet link a torrent leads to (step 3).

* ⚠️ The link carries the indexer's key or a passkey, anywhere in path or query. It is used as the feed gives it, only
  here, and never logged: the HTTP client writes scheme, host and port only (``origin_only``), and no error text of a
  request reaches a log line.
* At most one fetch per indexer every 2 seconds, with the pacing of ``services/indexers.py``.
* Redirects are followed by hand, at most 5. A ``Location`` starting with ``magnet:`` makes a torrent a magnet link.
* NZB at most 20 MB, parsed without loading a DTD or entities: root ``nzb`` with at least one ``file``. Torrent at most
  10 MB, asked for with ``Accept: application/x-bittorrent``: a bencoded dictionary with ``info`` (``torrent``).
* Anything else is ``release_file_invalid``; a failed request ``release_fetch_failed``, with the HTTP status when there
  is one. A 429 hands its Retry-After to the caller, who pauses the indexer as a search does.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from xml.etree.ElementTree import ParseError

import defusedxml
import defusedxml.ElementTree as SafeXml
import httpx
from fastapi import HTTPException

from ...meldungen import meldung
from .. import http_log, indexers, logs
from ..downloaders import torrent

logger = logging.getLogger("nexcrate.downloads")

NZB_MAX_BYTES = 20 * 1024 * 1024
TORRENT_MAX_BYTES = 10 * 1024 * 1024
MAX_REDIRECTS = 5
TIMEOUT = httpx.Timeout(60.0, connect=10.0)
REDIRECT_STATUSES = (301, 302, 303, 307, 308)


class FetchError(Exception):
    def __init__(self, detail: dict[str, Any], status: int = 502, *, retry_after: int | None = None) -> None:
        super().__init__(detail["code"])
        self.detail = detail
        self.status = status
        #: Seconds of a 429's Retry-After (one hour without one); None for other failures.
        self.retry_after = retry_after

    @property
    def code(self) -> str:
        return str(self.detail["code"])

    def http(self) -> HTTPException:
        return HTTPException(status_code=self.status, detail=self.detail)


def fetch_failed(status: int | None = None, *, retry_after: int | None = None) -> FetchError:
    values = {"status": status} if status is not None else {}
    return FetchError(
        meldung("release_fetch_failed", "The release file could not be fetched from the indexer.", **values),
        retry_after=retry_after,
    )


def file_invalid() -> FetchError:
    return FetchError(meldung("release_file_invalid", "The indexer did not deliver a usable NZB or torrent file."))


ERRORS = ((502, "release_fetch_failed"), (502, "release_file_invalid"))


@dataclass(frozen=True)
class Fetched:
    #: The NZB or torrent file; None for a magnet link.
    content: bytes | None = field(default=None, repr=False)
    #: ⚠️ A magnet link can carry a passkey in its trackers: never logged.
    magnet: str | None = field(default=None, repr=False)
    #: A torrent's info hash in lower case.
    info_hash: str | None = None


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def check_nzb(content: bytes) -> None:
    """Raises ``release_file_invalid`` unless the content is an NZB with at least one file.

    ⚠️ NZB files declare a DOCTYPE, so a DTD is allowed but never loaded; entities and external references are refused.
    """
    try:
        root = SafeXml.fromstring(content, forbid_dtd=False, forbid_entities=True, forbid_external=True)
    except (ParseError, defusedxml.DefusedXmlException, ValueError) as exc:
        raise file_invalid() from exc
    if _local(root.tag) != "nzb" or not any(_local(child.tag) == "file" for child in root):
        raise file_invalid()


def _magnet(link: str) -> Fetched:
    info_hash = torrent.magnet_hash(link)
    if info_hash is None:
        raise file_invalid()
    return Fetched(magnet=link, info_hash=info_hash)


async def _read(response: httpx.Response, limit: int) -> bytes:
    declared = response.headers.get("content-length", "").strip()
    if declared.isdigit() and int(declared) > limit:
        raise file_invalid()
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > limit:
            raise file_invalid()
        chunks.append(chunk)
    return b"".join(chunks)


def _validated(body: bytes, is_torrent: bool) -> Fetched:
    if not is_torrent:
        check_nzb(body)
        return Fetched(content=body)
    try:
        read = torrent.read_torrent(body)
    except torrent.TorrentInvalid as exc:
        logger.info("A fetched torrent was refused: %s", exc)
        raise file_invalid() from exc
    return Fetched(content=body, info_hash=read.info_hash)


async def fetch(link: str, protocol: str, pace_key: str) -> Fetched:
    """The release file behind ``link`` for ``usenet`` or ``torrent``. Raises ``FetchError``."""
    is_torrent = protocol == "torrent"
    if not link:
        raise fetch_failed()
    if link.lower().startswith("magnet:"):
        if not is_torrent:
            raise file_invalid()
        return _magnet(link)
    limit = TORRENT_MAX_BYTES if is_torrent else NZB_MAX_BYTES
    accept = "application/x-bittorrent" if is_torrent else "application/x-nzb, application/xml;q=0.9, */*;q=0.1"
    await indexers.pace(pace_key)
    url = link
    # ⚠️ Without quiet_libraries httpx would write the whole link into the log in the trace mode.
    with logs.quiet_libraries():
        return await _follow(url, is_torrent=is_torrent, limit=limit, accept=accept)


async def _follow(url: str, *, is_torrent: bool, limit: int, accept: str) -> Fetched:
    async with http_log.client(
        "release", timeout=TIMEOUT, follow_redirects=False, log_bodies=False, origin_only=True
    ) as http:
        for _hop in range(MAX_REDIRECTS + 1):
            if not url.lower().startswith(("http://", "https://")):
                raise fetch_failed()
            try:
                async with http.stream("GET", url, headers={"Accept": accept}) as response:
                    status = response.status_code
                    if status in REDIRECT_STATUSES:
                        location = response.headers.get("location", "").strip()
                        if not location:
                            raise fetch_failed(status)
                        if location.lower().startswith("magnet:"):
                            if not is_torrent:
                                raise file_invalid()
                            return _magnet(location)
                        url = str(response.url.join(location))
                        continue
                    if status == 429:
                        seconds = indexers.retry_after_seconds(response.headers.get("retry-after"))
                        raise fetch_failed(status, retry_after=seconds)
                    if status != 200:
                        raise fetch_failed(status)
                    body = await _read(response, limit)
            except httpx.TransportError as exc:
                http_log.unreachable("release", "GET", url, exc, origin_only=True)
                raise fetch_failed() from exc
            except (httpx.RequestError, httpx.InvalidURL) as exc:
                raise fetch_failed() from exc
            return _validated(body, is_torrent)
    logger.info("A release link redirected more than %d times", MAX_REDIRECTS)
    raise fetch_failed()
