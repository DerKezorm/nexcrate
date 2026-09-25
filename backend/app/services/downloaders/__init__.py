"""Download clients: SABnzbd, NZBGet, qBittorrent, Transmission and Deluge behind one shape.

``open_client(target)`` gives the client of a kind; ``check`` is the test that saving runs: the kind (a version call),
the credentials and the category (created and read back when asked), and whether the category is usable.
"""

from __future__ import annotations

from .base import (
    DEFAULT_CATEGORY,
    ERRORS,
    KINDS,
    PROTOCOLS,
    WITH_USERNAME,
    CategoryJob,
    Checked,
    ClientBase,
    ClientError,
    DuplicateTorrent,
    HandOverUnsure,
    Job,
    Target,
    auth_failed,
    category_failed,
    category_unusable,
    ensure_category,
    file_invalid,
    host_refused,
    is_usenet,
    longest_retention,
    refused,
    unreachable,
    wrong_kind,
)
from .deluge import Deluge
from .nzbget import Nzbget
from .qbittorrent import Qbittorrent
from .sabnzbd import Sabnzbd
from .transmission import Transmission


def open_client(target: Target) -> ClientBase:
    """The client of the target's kind; use it as ``async with``."""
    if target.kind == "sabnzbd":
        return Sabnzbd(target)
    if target.kind == "qbittorrent":
        return Qbittorrent(target)
    if target.kind == "nzbget":
        return Nzbget(target)
    if target.kind == "transmission":
        return Transmission(target)
    if target.kind == "deluge":
        return Deluge(target)
    raise ValueError(f"no download client of the kind {target.kind!r}")


async def check(target: Target, *, create_category: bool) -> Checked:
    """The test of a client. Without ``create_category`` nothing is changed in the client."""
    async with open_client(target) as client:
        version = await client.version()
        existed = await ensure_category(client, create=create_category)
        if existed or create_category:
            await client.check_category()
    return Checked(version=version, category_exists=existed)


__all__ = [
    "DEFAULT_CATEGORY",
    "ERRORS",
    "KINDS",
    "PROTOCOLS",
    "WITH_USERNAME",
    "CategoryJob",
    "Checked",
    "ClientBase",
    "ClientError",
    "Deluge",
    "DuplicateTorrent",
    "HandOverUnsure",
    "Job",
    "Nzbget",
    "Qbittorrent",
    "Sabnzbd",
    "Target",
    "Transmission",
    "auth_failed",
    "category_failed",
    "category_unusable",
    "check",
    "ensure_category",
    "file_invalid",
    "host_refused",
    "is_usenet",
    "longest_retention",
    "open_client",
    "refused",
    "unreachable",
    "wrong_kind",
]
