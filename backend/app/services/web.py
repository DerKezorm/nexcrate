"""nexcrate's address to the outside and the fixed jumps into its pages (N7 and N21).

The owner enters the address people and programs reach nexcrate at, sub path included (``https://example.com/nexcrate``).
nexcrate itself needs it nowhere; a program builds "open in nexcrate" from it with the jumps below. The jumps are
addresses of the interface that stay, whatever its pages are called later: the page ``/open/…`` looks the target up
with the session and goes on.
"""

from __future__ import annotations

from typing import Any

from .. import db as database
from ..vendor.nexoidc import flow

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
    ``ValueError`` for anything else.

    ⚠️ The sign-in module takes this address as its public address and registers callbacks below it at the provider, so
    this accepts exactly what the module's own check accepts (``flow.clean_public_url``: scheme and host in small
    letters, at most 199 characters, no ``.`` or ``..`` in the path) and nothing it refuses."""
    value = (text or "").strip()
    if not value:
        return ""
    if len(value) > MAX_LENGTH:
        raise ValueError("too long")
    return flow.clean_public_url(value)


def web_url(db: Any) -> str | None:
    return database.get_setting(db, SETTING, "") or None
