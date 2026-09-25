"""nexcrate's address to the outside and the fixed jumps into its pages (N7 and N21).

The owner enters the address people and programs reach nexcrate at, sub path included (``https://example.com/nexcrate``).
nexcrate itself needs it nowhere; a program builds "open in nexcrate" from it with the jumps below. The jumps are
addresses of the interface that stay, whatever its pages are called later: the page ``/open/…`` looks the target up
with the session and goes on.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from .. import db as database

SETTING = "web_url"
MAX_LENGTH = 500

#: The jumps into the interface, relative to the address to the outside.
LINKS = {
    "title": "/open/title/{kind}/{ref}",
    "version": "/open/version/{version_id}",
    "profile": "/open/profile/{version_id}",
    "download": "/open/download/{download_id}",
    "problems": "/open/problems",
    "recycle_bin": "/open/recycle-bin",
    "calendar": "/open/calendar",
}


def normalize(text: str) -> str:
    """``https://host[:port][/path]`` without a trailing slash, query or fragment; empty stays empty. Raises
    ``ValueError`` for anything else."""
    value = (text or "").strip()
    if not value:
        return ""
    if len(value) > MAX_LENGTH:
        raise ValueError("too long")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("not an address")
    if parts.query or parts.fragment or parts.username or parts.password:
        raise ValueError("not an address")
    path = parts.path.rstrip("/")
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{host}{port}{path}"


def web_url(db: Any) -> str | None:
    return database.get_setting(db, SETTING, "") or None
