"""Bazarr's live connection: SignalR as Radarr and Sonarr speak it, and the job that says what changed.

Bazarr keeps a SignalR connection open to Radarr and to Sonarr (``/signalr/messages``) and learns through it within
seconds that a movie or an episode changed. Without one it logs a reconnect error every three minutes and only learns
of a new file at its hourly sync. nexcrate speaks the small part of the protocol Bazarr's client uses:

* ``POST …/signalr/messages/negotiate`` hands out a connection id, valid for ``NEGOTIATION_SECONDS``;
* the WebSocket at ``…/signalr/messages?id=…`` takes the handshake ``{"protocol":"json","version":1}`` and answers
  ``{}``; every record ends with the character 0x1E;
* what changed goes out as ``{"type":1,"target":"receiveMessage","arguments":[{"name":…,"body":{…}}]}``, a ping
  ``{"type":6}`` every ``PING_SECONDS`` when nothing else did.

**What changed** is found the way the change marker of ``/api/v1`` finds it, not by every place that changes a file
remembering to say so: while a connection is open, a look every ``INTERVAL_SECONDS`` renders a small print of what
Bazarr sees (each movie version's file, each series version's episodes) and compares it with the last one. The first
look of a part is taken when its first connection opens; Bazarr syncs everything itself at that moment.

* A movie version that is new, has another file, or lost it: ``movie`` ``updated``; one that is gone: ``deleted``.
* A series version that is new: ``series`` ``updated`` (Bazarr adds the series) and then the same with
  ``episodesChanged`` (Bazarr reads its episodes). One whose episodes or files changed: only the second. Gone:
  ``deleted``.

⚠️ Bazarr drops an event equal to the one before it. Every event carries ``nexcrateChange``, a running number, so the
second change of the same movie in a row is not lost.

⚠️ Turning the switch off or revoking the key closes the connection at the next look.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select

from ... import db as database
from ...models import ApiKey, EpisodeFile, EpisodeVersion, Title, Version, utcnow
from . import ids, library, settings

logger = logging.getLogger("nexcrate.bazarr")

PARTS = ("radarr", "sonarr")
KIND_OF_PART = {"radarr": "movie", "sonarr": "series"}
RS = "\x1e"
INTERVAL_SECONDS = 15.0
PING_SECONDS = 15.0
NEGOTIATION_SECONDS = 60.0
HANDSHAKE_SECONDS = 15.0
PENDING_MAX = 100

#: A movie: (title, year, file number or None, path or None).
MoviePrint = dict[int, tuple[str, int, int | None, str | None]]


@dataclass
class SeriesPrint:
    #: A series: (title, year, folder).
    series: dict[int, tuple[str, int, str]] = field(default_factory=dict)
    #: A series: a hash of its episodes, their files and whether they are watched.
    episodes: dict[int, str] = field(default_factory=dict)


@dataclass
class Connection:
    id: str
    part: str
    key_id: int
    opened: datetime
    #: What to send; ``None`` closes the connection.
    outbox: asyncio.Queue[str | None] = field(default_factory=asyncio.Queue)


# --- The prints ------------------------------------------------------------------------------------------------- #


def movie_print(db: Any) -> MoviePrint:
    rows = db.execute(
        select(
            Version.id,
            Version.has_file,
            Version.root_folder,
            Version.relative_path,
            Version.size,
            Version.file_ref,
            Title.title,
            Title.year,
        )
        .join(Title, Title.id == Version.title_id)
        .where(Title.kind == "movie", Version.source_id.is_(None))
    ).tuples()
    found: MoviePrint = {}
    for version_id, has_file, root, relative, size, ref, name, year in rows:
        with_file = bool(has_file and relative and root)
        found[version_id] = (
            name or "",
            year or 0,
            ids.file_id(version_id, relative or "", size or 0, ref) if with_file else None,
            library.join(root, relative) if with_file else None,
        )
    return found


def series_print(db: Any) -> SeriesPrint:
    found = SeriesPrint()
    for version, title in db.execute(
        select(Version, Title)
        .join(Title, Title.id == Version.title_id)
        .where(Title.kind == "series", Version.source_id.is_(None))
    ).tuples():
        folder = library.series_folder(version)
        if folder is not None:
            found.series[version.id] = (title.title or "", title.year or 0, folder)
    lines: dict[int, list[str]] = defaultdict(list)
    for version_id, episode_id, watched, file_row, relative, size, ref in db.execute(
        select(
            EpisodeVersion.version_id,
            EpisodeVersion.episode_id,
            EpisodeVersion.watched,
            EpisodeFile.id,
            EpisodeFile.relative_path,
            EpisodeFile.size,
            EpisodeFile.file_ref,
        )
        .outerjoin(
            EpisodeFile,
            (EpisodeFile.id == EpisodeVersion.episode_file_id) & (EpisodeFile.version_id == EpisodeVersion.version_id),
        )
        .where(EpisodeVersion.version_id.in_(select(Version.id).where(Version.source_id.is_(None))))
    ).tuples():
        if version_id in found.series:
            lines[version_id].append(f"{episode_id}|{int(bool(watched))}|{file_row}|{relative}|{size}|{ref}")
    for version_id in found.series:
        digest = hashlib.sha256("\n".join(sorted(lines.get(version_id, []))).encode()).hexdigest()
        found.episodes[version_id] = digest
    return found


def take_print(part: str) -> MoviePrint | SeriesPrint:
    with database.SessionLocal() as db:
        return movie_print(db) if part == "radarr" else series_print(db)


# --- The messages ----------------------------------------------------------------------------------------------- #


def message(name: str, resource: dict[str, Any], action: str) -> str:
    body = {"name": name, "body": {"resource": resource, "action": action}}
    return json.dumps({"type": 1, "target": "receiveMessage", "arguments": [body]}, separators=(",", ":")) + RS


class Counter:
    def __init__(self) -> None:
        self.value = 0

    def next(self) -> int:
        self.value += 1
        return self.value


def movie_changes(before: MoviePrint, after: MoviePrint, counter: Counter) -> list[str]:
    out = []
    for version_id in sorted(set(before) | set(after)):
        old, new = before.get(version_id), after.get(version_id)
        if old == new:
            continue
        shown = new if new is not None else old
        assert shown is not None
        resource = {"id": version_id, "title": shown[0], "year": shown[1], "nexcrateChange": counter.next()}
        out.append(message("movie", resource, "deleted" if new is None else "updated"))
    return out


def series_changes(before: SeriesPrint, after: SeriesPrint, counter: Counter) -> list[str]:
    out = []

    def resource(version_id: int, shown: tuple[str, int, str], **more: Any) -> dict[str, Any]:
        return {"id": version_id, "title": shown[0], "year": shown[1], "nexcrateChange": counter.next(), **more}

    for version_id in sorted(set(before.series) | set(after.series)):
        old, new = before.series.get(version_id), after.series.get(version_id)
        shown = new if new is not None else old
        assert shown is not None
        if new is None:
            out.append(message("series", resource(version_id, shown), "deleted"))
            continue
        if old != new:
            out.append(message("series", resource(version_id, shown), "updated"))
        if old != new or before.episodes.get(version_id) != after.episodes.get(version_id):
            out.append(message("series", resource(version_id, shown, episodesChanged=True), "updated"))
    return out


# --- The hub ---------------------------------------------------------------------------------------------------- #


def _look(parts: set[str], key_ids: set[int]) -> tuple[bool, set[int], dict[str, MoviePrint | SeriesPrint]]:
    """In a thread: the switch, the keys still valid, and the print of every part with a connection."""
    with database.SessionLocal() as db:
        enabled = settings.load_enabled(db)
        valid = {
            key_id
            for key_id, scopes in db.execute(select(ApiKey.id, ApiKey.scopes).where(ApiKey.id.in_(key_ids))).tuples()
            if "read" in (scopes or [])
        }
        prints: dict[str, MoviePrint | SeriesPrint] = {}
        if enabled:
            for part in parts:
                prints[part] = movie_print(db) if part == "radarr" else series_print(db)
    return enabled, valid, prints


class Hub:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.pending: dict[str, tuple[str, int, float]] = {}
        self.connections: dict[str, Connection] = {}
        self.prints: dict[str, MoviePrint | SeriesPrint] = {}
        self.counter = Counter()
        self.task: asyncio.Task[None] | None = None
        #: When Bazarr last asked anything of each part, for the settings page.
        self.last_request: dict[str, datetime] = {}

    # -- Negotiation -- #

    def negotiate(self, part: str, key_id: int) -> dict[str, Any]:
        now = time.monotonic()
        for token, (_part, _key, made) in list(self.pending.items()):
            if now - made > NEGOTIATION_SECONDS:
                del self.pending[token]
        while len(self.pending) >= PENDING_MAX:
            del self.pending[next(iter(self.pending))]
        token = secrets.token_urlsafe(24)
        self.pending[token] = (part, key_id, now)
        return {
            "negotiateVersion": 0,
            "connectionId": token,
            "availableTransports": [{"transport": "WebSockets", "transferFormats": ["Text"]}],
        }

    def claim(self, token: str | None, part: str, key_id: int) -> Connection | None:
        """The connection a negotiated id stands for, once; None when it is unknown, old, or another's."""
        found = self.pending.pop(token or "", None)
        if found is None:
            return None
        wanted_part, wanted_key, made = found
        if wanted_part != part or wanted_key != key_id or time.monotonic() - made > NEGOTIATION_SECONDS:
            return None
        return Connection(id=token or "", part=part, key_id=key_id, opened=utcnow())

    # -- Joining and leaving -- #

    async def join(self, connection: Connection) -> None:
        if not any(other.part == connection.part for other in self.connections.values()):
            self.prints[connection.part] = await asyncio.to_thread(take_print, connection.part)
        self.connections[connection.id] = connection
        logger.info("Bazarr connected live as %s (key %d)", connection.part, connection.key_id)
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._watch())

    def leave(self, connection: Connection) -> None:
        if self.connections.pop(connection.id, None) is None:
            return
        logger.info("Bazarr's live connection as %s closed", connection.part)
        if not any(other.part == connection.part for other in self.connections.values()):
            self.prints.pop(connection.part, None)

    def close_all(self) -> None:
        for connection in list(self.connections.values()):
            connection.outbox.put_nowait(None)

    # -- The look -- #

    async def _watch(self) -> None:
        while self.connections:
            await asyncio.sleep(INTERVAL_SECONDS)
            try:
                await self.tick()
            except Exception:
                logger.exception("The look for Bazarr failed")

    async def tick(self) -> int:
        """One look. Returns how many messages went out."""
        if not self.connections:
            return 0
        parts = {connection.part for connection in self.connections.values()}
        keys = {connection.key_id for connection in self.connections.values()}
        enabled, valid, prints = await asyncio.to_thread(_look, parts, keys)
        if not enabled:
            self.close_all()
            return 0
        for connection in list(self.connections.values()):
            if connection.key_id not in valid:
                connection.outbox.put_nowait(None)
        sent = 0
        for part, after in prints.items():
            before = self.prints.get(part)
            # A part whose connections all left while the look ran has no print to compare with.
            if before is None or not any(c.part == part for c in self.connections.values()):
                continue
            if isinstance(before, SeriesPrint) and isinstance(after, SeriesPrint):
                messages = series_changes(before, after, self.counter)
            elif isinstance(before, dict) and isinstance(after, dict):
                messages = movie_changes(before, after, self.counter)
            else:
                messages = []
            self.prints[part] = after
            for connection in self.connections.values():
                if connection.part == part and connection.key_id in valid:
                    for item in messages:
                        connection.outbox.put_nowait(item)
                        sent += 1
            if messages:
                logger.info("Told Bazarr about %d change(s) as %s", len(messages), part)
        return sent

    # -- For the settings page -- #

    def seen(self, part: str) -> None:
        self.last_request[part] = utcnow()

    def status(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for part in PARTS:
            live = [c for c in self.connections.values() if c.part == part]
            out[part] = {
                "live": bool(live),
                "since": min(c.opened for c in live) if live else None,
                "last_request": self.last_request.get(part),
            }
        return out


hub = Hub()


# --- The protocol ------------------------------------------------------------------------------------------------ #


def records(raw: str) -> list[dict[str, Any]]:
    """The JSON records of a frame; a record that is no object is skipped."""
    out = []
    for part in raw.split(RS):
        if not part.strip():
            continue
        try:
            value = json.loads(part)
        except ValueError:
            continue
        if isinstance(value, dict):
            out.append(value)
    return out


def handshake_ok(raw: str) -> bool:
    found = records(raw)
    return bool(found) and found[0].get("protocol") == "json" and found[0].get("version") == 1


PING = json.dumps({"type": 6}) + RS
HANDSHAKE_ANSWER = "{}" + RS
HANDSHAKE_REFUSED = json.dumps({"error": "nexcrate speaks the JSON protocol, version 1, only."}) + RS
