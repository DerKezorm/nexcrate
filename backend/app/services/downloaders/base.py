"""What SABnzbd and qBittorrent have in common: the target, a job as nexcrate reads it, and the errors the API sends.

* Every call goes through the clients of ``http_log`` and has its own timeout of 10 seconds. SABnzbd's key travels as
  ``apikey`` in the query, which the log masks; qBittorrent's password only in the login body, and no request body is
  ever logged. No answer body either, not even in ``trace``.
* A client that cannot be reached, or does not answer in time, is ``client_unreachable``. Credentials it refuses are
  ``client_auth_failed`` with ``values.reason`` (``wrong_key``, ``nzb_key``, ``wrong_password``); a SABnzbd that refuses
  nexcrate's host name ``client_host_refused``, a Transmission whose whitelist refuses nexcrate's address
  ``client_address_refused``; something else at the address ``client_wrong_kind``. A client's own
  sentences never reach an answer.
* ⚠️ What a client answers is never trusted. Names, numbers and paths are read tolerantly here and checked where they
  are used (``services/downloads``). Answers over 20 MB are refused.
* The category is nexcrate's own. Saving creates it when it is missing and reads it back; loading checks it again,
  because SABnzbd puts a job with an unknown category silently into ``*``.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, ClassVar, Self

import httpx
from fastapi import HTTPException

from ...meldungen import meldung
from ...models.downloads import CLIENT_KINDS, PROTOCOL_OF_KIND
from .. import http_log

logger = logging.getLogger("nexcrate.downloaders")

#: The kinds and their protocols live with the model, one table for the whole app.
KINDS = CLIENT_KINDS
PROTOCOLS = PROTOCOL_OF_KIND
#: The kinds that log in with a user name and a password; the others take one key or password.
WITH_USERNAME = frozenset({"qbittorrent", "nzbget", "transmission"})


def is_usenet(kind: str) -> bool:
    return PROTOCOLS.get(kind) == "usenet"


def longest_retention(values: list[int | None]) -> int | None:
    """How long several news servers keep articles together: the longest, and no limit as soon as one has none (0 or
    unknown), or when there is none at all. A release is there while any server still has it."""
    if not values or any(value is None or value <= 0 for value in values):
        return None
    return max(value for value in values if value is not None)
DEFAULT_CATEGORY = "nexcrate"
#: Every call to a client has its own 10 seconds (plan, "Download clients").
TIMEOUT = httpx.Timeout(10.0)
#: Handing over a release waits longer (the owner's finding 1 of 22.09.2026): with 14 jobs unpacking the owner's
#: SABnzbd took the NZB and answered after more than 10 seconds, five times in a day. Connecting gives up after 10.
HAND_OVER_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
#: Transport errors after the request went out: the client may have taken it, and only a look into its category says.
UNSURE_ERRORS = (httpx.ReadTimeout, httpx.ReadError, httpx.RemoteProtocolError, httpx.WriteTimeout, httpx.WriteError)
MAX_ANSWER_BYTES = 20 * 1024 * 1024
#: What a job can be for tracking.
JOB_STATES = ("queued", "downloading", "paused", "completed", "failed", "problem")


# --- Errors ----------------------------------------------------------------------------- #


class ClientError(Exception):
    """An answer of a download client that nexcrate cannot use, with the error the API sends for it."""

    def __init__(self, detail: dict[str, Any], status: int = 502) -> None:
        super().__init__(detail["code"])
        self.detail = detail
        self.status = status

    @property
    def code(self) -> str:
        return str(self.detail["code"])

    def http(self) -> HTTPException:
        return HTTPException(status_code=self.status, detail=self.detail)


def unreachable(url: str) -> ClientError:
    return ClientError(
        meldung(
            "client_unreachable",
            f"The download client cannot be reached at {url}. Are address and port right?",
            url=url,
        )
    )


def auth_failed(reason: str) -> ClientError:
    return ClientError(
        meldung("client_auth_failed", "The download client did not accept the credentials.", reason=reason)
    )


def host_refused() -> ClientError:
    return ClientError(
        meldung(
            "client_host_refused",
            "SABnzbd refused the host name nexcrate used. Add it to SABnzbd's host whitelist, or use the address.",
        )
    )


def address_refused() -> ClientError:
    return ClientError(
        meldung(
            "client_address_refused",
            "The download client refuses nexcrate's address. Allow it in the client's whitelist (Transmission: "
            "rpc-whitelist), or switch the whitelist off.",
        )
    )


def wrong_kind() -> ClientError:
    return ClientError(
        meldung("client_wrong_kind", "Something answers at this address, but not the chosen download client.")
    )


def category_failed(category: str) -> ClientError:
    return ClientError(
        meldung(
            "client_category_failed",
            f"The category {category} could not be created or used in the download client.",
            category=category,
        )
    )


def category_unusable(reason: str) -> ClientError:
    return ClientError(
        meldung(
            "client_category_unusable",
            "SABnzbd handles this category in a way nexcrate cannot follow. Switch job folders on and sorting off.",
            reason=reason,
        )
    )


def refused(reason: str | None = None) -> ClientError:
    values = {"reason": reason} if reason else {}
    return ClientError(meldung("client_refused", "The download client refused the release.", **values))


def file_invalid() -> ClientError:
    return ClientError(meldung("release_file_invalid", "The indexer did not deliver a usable NZB or torrent file."))


class HandOverUnsure(ClientError):
    """The client did not answer a hand-over in time, but the request went out: it may have taken the release.
    ``download_id`` is the id when nexcrate knows it before the answer (a torrent's info hash), else empty."""

    def __init__(self, url: str, download_id: str = "") -> None:
        super().__init__(unreachable(url).detail)
        self.download_id = download_id


def sent_but_unanswered(exc: ClientError) -> bool:
    """Whether a ``client_unreachable`` came after the request went out (``UNSURE_ERRORS``)."""
    return exc.code == "client_unreachable" and isinstance(exc.__cause__, UNSURE_ERRORS)


class DuplicateTorrent(ClientError):
    """qBittorrent has this torrent already. ``category_matches``: it lies in nexcrate's category."""

    def __init__(self, info_hash: str, *, category_matches: bool) -> None:
        super().__init__(meldung("client_refused", "The download client has this torrent already.", reason="duplicate"))
        self.info_hash = info_hash
        self.category_matches = category_matches


#: For the OpenAPI ``responses`` of every route that tests a client.
ERRORS = (
    (502, "client_unreachable"),
    (502, "client_auth_failed"),
    (502, "client_host_refused"),
    (502, "client_address_refused"),
    (502, "client_wrong_kind"),
    (502, "client_category_failed"),
    (502, "client_category_unusable"),
)


# --- What goes in and comes out ------------------------------------------------------------- #


@dataclass(frozen=True)
class Target:
    #: sabnzbd, qbittorrent, nzbget, transmission or deluge.
    kind: str
    #: The base address, without user info or query.
    url: str
    #: The user name of qBittorrent, NZBGet or Transmission; empty for SABnzbd and Deluge.
    username: str = ""
    #: ⚠️ SABnzbd's API key or the password (Deluge: of its web interface), decrypted, in memory only while the
    #: client is asked.
    secret: str = field(default="", repr=False)
    category: str = DEFAULT_CATEGORY
    #: ⚠️ qBittorrent refused the password before. nexcrate does not log in again until the client is saved or tested:
    #: five failures ban nexcrate's address for an hour.
    login_blocked: bool = False


@dataclass(frozen=True)
class Job:
    """One download as a client reports it, in nexcrate's words."""

    download_id: str
    #: queued, downloading, paused, completed, failed or problem.
    state: str
    #: The client's own state, for the log only.
    client_state: str
    #: 0 to 100.
    progress: float | None = None
    remaining_seconds: int | None = None
    size_bytes: int | None = None
    #: The path the client reports for a finished job, as it reports it; None while unknown.
    path: str | None = None
    #: A problem or a hint: client_error, no_space, path_not_found, stalled.
    problem: str | None = None
    #: For failed: client_failed or encrypted.
    reason: str | None = None
    #: For failed: what the client said, as a code (``models.downloads.FAILED_DETAILS``).
    detail: str | None = None
    #: For failed: the folders the client may have left, as it reports them (SABnzbd's ``storage`` and ``path``).
    leftovers: tuple[str, ...] = ()


@dataclass(frozen=True)
class CategoryJob:
    """A job in nexcrate's category with the client's name of it (the owner's findings 1 and 2 of 22.09.2026)."""

    name: str
    job: Job


@dataclass(frozen=True)
class Checked:
    version: str
    category_exists: bool


# --- Tolerant reading --------------------------------------------------------------------- #


def number(value: Any) -> float | None:
    """A finite number from a number or a numeric text (SABnzbd sends numbers as texts), else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        result = float(value)
    elif isinstance(value, str):
        try:
            result = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return result if math.isfinite(result) else None


def whole(value: Any) -> int | None:
    found = number(value)
    return int(found) if found is not None else None


def text(value: Any, limit: int = 4096) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def percent(value: float | None) -> float | None:
    if value is None:
        return None
    return round(min(100.0, max(0.0, value)), 1)


async def read_limited(response: httpx.Response) -> bytes:
    declared = response.headers.get("content-length", "").strip()
    if declared.isdigit() and int(declared) > MAX_ANSWER_BYTES:
        raise wrong_kind()
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > MAX_ANSWER_BYTES:
            raise wrong_kind()
        chunks.append(chunk)
    return b"".join(chunks)


@dataclass(frozen=True)
class Answer:
    status: int
    headers: httpx.Headers
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace").strip()


# --- The client ---------------------------------------------------------------------------- #


class ClientBase:
    """Use as ``async with open_client(target) as client: ...``."""

    kind: ClassVar[str] = ""

    def __init__(self, target: Target, *, timeout: httpx.Timeout | float = TIMEOUT) -> None:
        self.target = target
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> Self:
        # ⚠️ log_bodies=False: SABnzbd's config answer holds its keys, qBittorrent's torrent list tracker passkeys.
        self._http = http_log.client(self.kind, timeout=self._timeout, follow_redirects=False, log_bodies=False)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError("A download client is used outside of 'async with'")
        return self._http

    def address(self, path: str) -> str:
        return self.target.url.rstrip("/") + path

    async def call(self, method: str, url: str, **kwargs: Any) -> Answer:
        """One request; no answer at all is ``client_unreachable``."""
        try:
            async with self.http.stream(method, url, **kwargs) as response:
                body = await read_limited(response)
                return Answer(response.status_code, response.headers, body)
        except httpx.TransportError as exc:
            http_log.unreachable(self.kind, method, url, exc)
            raise unreachable(self.target.url) from exc
        except httpx.RequestError as exc:
            raise unreachable(self.target.url) from exc

    # The operations every kind has. ------------------------------------------------------ #

    async def version(self) -> str:
        """The client's version; tells the kind."""
        raise NotImplementedError

    async def categories(self) -> list[str]:
        raise NotImplementedError

    def has_category(self, names: list[str]) -> bool:
        return self.target.category in names

    async def create_category(self) -> None:
        raise NotImplementedError

    async def check_category(self) -> None:
        """Raises ``client_category_unusable`` when the client would move files behind nexcrate's back."""

    async def add_nzb(self, content: bytes, file_name: str, urgent: bool | None = None) -> str:
        """``urgent``: True for a release that brings something missing, False for an upgrade, None for neither
        (the owner's wish of 24.09.2026: new titles go before upgrades in the client's queue)."""
        raise refused()

    async def add_torrent(
        self, *, content: bytes | None, magnet: str | None, file_name: str, info_hash: str, urgent: bool | None = None
    ) -> str:
        raise refused()

    async def jobs(self, download_ids: list[str]) -> dict[str, Job]:
        """The client's jobs among ``download_ids`` in nexcrate's category, by download id."""
        raise NotImplementedError

    async def remove(self, download_id: str, *, delete_files: bool) -> None:
        """Remove a job that is not imported."""
        raise NotImplementedError

    async def remove_imported(self, download_id: str) -> None:
        """After a successful import. SABnzbd forgets the job; a torrent stays and seeds."""

    async def category_jobs(self) -> list[CategoryJob]:
        """Every job in nexcrate's category, whoever put it there."""
        raise NotImplementedError

    async def category_folder(self) -> str | None:
        """The folder finished jobs of nexcrate's category go to, as the client names it; None when unknown."""
        return None

    async def usenet_retention(self) -> int | None:
        """How many days the client's active news servers keep articles (``longest_retention``); None for no limit."""
        return None


async def ensure_category(client: ClientBase, *, create: bool) -> bool:
    """Whether the category existed. With ``create`` a missing one is created and read back."""
    if client.has_category(await client.categories()):
        return True
    if not create:
        return False
    await client.create_category()
    if not client.has_category(await client.categories()):
        logger.warning("The %s category was not there after creating it", client.kind)
        raise category_failed(client.target.category)
    logger.info("Category created in %s", client.kind)
    return False
