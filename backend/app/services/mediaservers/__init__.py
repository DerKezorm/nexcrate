"""Media servers: Plex, Jellyfin and Emby behind one shape.

``check(target)`` reads who answers and its libraries; ``notify`` tells the servers about changed folders after
filing and renaming. ``plextv`` is the sign-in at plex.tv and the only module that talks to plex.tv.
"""

from __future__ import annotations

from .base import ERRORS, KINDS, Checked, Listed, ServerError, Target
from .jellyfin import JellyfinClient
from .plex import PlexClient


async def check(target: Target) -> Checked:
    """Who answers at the target, and its libraries. Changes nothing on the server."""
    if target.kind == "plex":
        async with PlexClient(target.url, target.token) as plex:
            return await plex.check()
    if target.kind in ("jellyfin", "emby"):
        async with JellyfinClient(target.kind, target.url, target.token) as server:
            return await server.check()
    raise ValueError(f"no media server of the kind {target.kind!r}")


__all__ = ["ERRORS", "KINDS", "Checked", "JellyfinClient", "Listed", "PlexClient", "ServerError", "Target", "check"]
