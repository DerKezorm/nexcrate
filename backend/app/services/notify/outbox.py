"""From the event feed to the mailboxes.

The same way as the webhooks for programs (``services/webhooks.py``): every mailbox keeps its place in the feed of
``/api/v1`` (``cursor``), so no event is sent twice and none skipped, and a new mailbox starts at the newest event, so
no backlog lands on it. Writing a message at every place something happens was turned down for the feed already: the
first place somebody forgets makes it silently incomplete.

A message is rendered when it is taken over, in the mailbox's language, and sent by the same job: tried again after 1,
5, 30 and 120 minutes, then given up and shown on the tile. When more than ``texts.BURST`` messages of one kind come in
one round (a takeover adds thousands of titles), the mailbox gets one summary instead.

A new nexcrate version comes from the update check, not from the feed: announced once per version.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session as OrmSession

from ... import db as database
from ...models import ApiEvent, HistoryEntry, NotificationMessage, NotificationTarget, Title, VersionDefinition, utcnow
from .. import updates
from ..api_v1 import events as feed
from . import ChannelError, Notice, build, send, texts
from .targets import is_leaf, values

logger = logging.getLogger("nexcrate.notify")

JOB_NAME = "notifications"
INTERVAL_SECONDS = 10
#: The waits before the second to fifth try; after the fifth, a message is given up.
BACKOFF = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30), timedelta(minutes=120))
KEEP = 50
TAKE_MAX = 500
SEND_MAX = 50
#: The last version announced, so a version is announced once.
SETTING_UPDATE_ANNOUNCED = "notify_update_announced"
POSTER_SIZE = "w342"

_FROM_FEED = {
    "download.started": "download_started",
    "download.failed": "download_failed",
    "title.added": "title_added",
    "title.removed": "title_removed",
    "file.deleted": "file_deleted",
    "series.season_complete": "season_complete",
    "request.made": "request_made",
    "health.changed": "health_changed",
}


# --- Which mailboxes ----------------------------------------------------------------------------------------------- #


def active(db: OrmSession, target: NotificationTarget) -> bool:
    """Switched on, and its instance too: a switched-off instance silences its topics."""
    if not target.enabled:
        return False
    if target.parent_id is None:
        return True
    parent = db.get(NotificationTarget, target.parent_id)
    return parent is not None and parent.enabled


def mailboxes(db: OrmSession) -> list[NotificationTarget]:
    """The confirmed mailboxes that are on."""
    found = []
    for target in db.scalars(select(NotificationTarget).where(NotificationTarget.verified.is_(True))):
        if is_leaf(target) and active(db, target):
            found.append(target)
    return found


def level_of(target: NotificationTarget, key: str) -> str | None:
    from .base import LEVELS

    level = (target.events or {}).get(key)
    return level if level in LEVELS else None


# --- Events to messages -------------------------------------------------------------------------------------------- #


def _upgraded(db: OrmSession, row: ApiEvent) -> bool:
    """Did this import replace a file? A movie says it in the event; a series in its history line, per file."""
    params = row.params or {}
    if params.get("replaced"):
        return True
    if row.kind != "series" or row.download_id is None or row.title_id is None:
        return False
    for data in db.scalars(
        select(HistoryEntry.data)
        .where(HistoryEntry.title_id == row.title_id, HistoryEntry.event == "episodes_filed")
        .order_by(HistoryEntry.id.desc())
        .limit(20)
    ):
        if isinstance(data, dict) and data.get("download_id") == row.download_id:
            return any(item.get("replaced") for item in data.get("files") or [] if isinstance(item, dict))
    return False


def key_of(db: OrmSession, row: ApiEvent) -> str | None:
    """The event key a feed event belongs to; None for one no mailbox can switch on."""
    if row.type == "download.imported":
        return "download_upgraded" if _upgraded(db, row) else "download_imported"
    if row.type == "problem.opened":
        return "problem_opened" if (row.params or {}).get("needs_owner") else None
    return _FROM_FEED.get(row.type)


class _Lookup:
    """Titles, versions and names asked once per round."""

    def __init__(self, db: OrmSession) -> None:
        self.db = db
        self._titles: dict[int, Title | None] = {}
        self._labels: dict[int, str | None] = {}

    def title(self, title_id: int | None) -> Title | None:
        if title_id is None:
            return None
        if title_id not in self._titles:
            self._titles[title_id] = self.db.get(Title, title_id)
        return self._titles[title_id]

    def label(self, definition_id: int | None) -> str | None:
        if definition_id is None:
            return None
        if definition_id not in self._labels:
            definition = self.db.get(VersionDefinition, definition_id)
            self._labels[definition_id] = definition.label if definition is not None else None
        return self._labels[definition_id]

    def name_of(self, row: ApiEvent) -> str:
        """The title's name; for a removed title the name an earlier event of the same reference carried."""
        if row.name:
            return row.name
        if row.ref:
            earlier = self.db.scalar(
                select(ApiEvent.name)
                .where(ApiEvent.ref == row.ref, ApiEvent.kind == row.kind, ApiEvent.name.is_not(None))
                .order_by(ApiEvent.seq.desc())
                .limit(1)
            )
            if earlier:
                return str(earlier)
            return row.ref
        return "?"


def _poster(title: Title | None) -> str | None:
    from ..tmdb import IMAGE_BASE

    if title is None or not title.tmdb_poster_path:
        return None
    return f"{IMAGE_BASE}/{POSTER_SIZE}/{title.tmdb_poster_path.lstrip('/')}"


def _head(lookup: _Lookup, row: ApiEvent, lang: str) -> str:
    title = lookup.title(row.title_id)
    name = lookup.name_of(row)
    if title is not None and title.year and title.kind != "album":
        name = f"{name} ({title.year})"
    series = (row.block or {}).get("series") if isinstance(row.block, dict) else None
    season = series.get("season") if isinstance(series, dict) else None
    if isinstance(season, int):
        name += f" · {texts.word('season', lang)} {season}"
    return f"**{name}**"


def _replaced(params: dict[str, Any]) -> str | None:
    replaced = params.get("replaced")
    if isinstance(replaced, dict) and replaced.get("quality"):
        return str(replaced["quality"])
    return None


def _body(lookup: _Lookup, row: ApiEvent, key: str, lang: str) -> list[str]:
    params = dict(row.params or {})
    lines = [_head(lookup, row, lang)]
    series = (row.block or {}).get("series") if isinstance(row.block, dict) else None
    # A whole season stands in the head already; single episodes are named.
    if isinstance(series, dict) and not isinstance(series.get("season"), int):
        codes = texts.episodes(series.get("episodes") or [], lang)
        if codes:
            lines.append(codes)
    label = lookup.label(row.version_definition_id)
    if label and key not in ("title_added", "title_removed"):
        lines.append(f"{texts.word('version', lang)}: {label}")
    if key in ("download_started", "download_imported", "download_upgraded", "download_failed", "problem_opened"):
        if params.get("quality"):
            lines.append(f"{texts.word('quality', lang)}: {params['quality']}")
        if key == "download_upgraded" and _replaced(params):
            lines.append(f"{texts.word('replaced', lang)}: {_replaced(params)}")
        if isinstance(params.get("filed"), int) and params.get("filed"):
            counts = texts.word("filed", lang, filed=params["filed"])
            if params.get("missing"):
                counts += ", " + texts.word("missing", lang, missing=params["missing"])
            lines.append(counts)
    if key == "download_failed":
        reason = texts.failed(params.get("reason"), lang)
        if reason:
            lines.append(reason)
    if key == "problem_opened":
        lines.append(texts.problem(params.get("code"), lang))
    if key == "request_made" and params.get("by"):
        lines.append(f"{texts.word('from', lang)}: {params['by']}")
    if key in ("download_started", "download_failed", "problem_opened") and params.get("release"):
        lines.append(f"{texts.word('release', lang)}: {params['release']}")
    return lines


def _finding_key(finding: dict[str, Any]) -> tuple[str, str]:
    params = finding.get("params") if isinstance(finding.get("params"), dict) else {}
    return str(finding.get("code")), str(params.get("version_id") or params.get("name") or params.get("kind") or "")


def _health(db: OrmSession, row: ApiEvent, lang: str) -> tuple[str, list[str]] | None:
    """What came and what went, against the health event before this one."""
    now = [item for item in (row.params or {}).get("items") or [] if isinstance(item, dict)]
    before_params = db.scalar(
        select(ApiEvent.params)
        .where(ApiEvent.type == "health.changed", ApiEvent.seq < row.seq)
        .order_by(ApiEvent.seq.desc())
        .limit(1)
    )
    before = [item for item in (before_params or {}).get("items") or [] if isinstance(item, dict)]
    known = {_finding_key(item) for item in before}
    still = {_finding_key(item) for item in now}
    came = [item for item in now if _finding_key(item) not in known]
    went = [item for item in before if _finding_key(item) not in still]
    if not came and not went:
        return None
    lines = [texts.health(item, lang) for item in came]
    if went:
        prefix = texts.title("health_resolved", lang)
        lines += [f"{prefix}: {texts.health(item, lang)}" for item in went]
    return texts.title("health_problem" if came else "health_resolved", lang), lines


def render(db: OrmSession, lookup: _Lookup, row: ApiEvent, key: str, lang: str) -> tuple[str, str, str | None] | None:
    """Title, body and poster of one event's message; None when there is nothing to say."""
    if key == "health_changed":
        found = _health(db, row, lang)
        if found is None:
            return None
        return found[0], "\n".join(found[1]), None
    return texts.title(key, lang), "\n".join(_body(lookup, row, key, lang)), _poster(lookup.title(row.title_id))


def _message(target: NotificationTarget, key: str, level: str, title: str, body: str, poster: str | None,
             seq: int | None) -> NotificationMessage:  # fmt: skip
    now = utcnow()
    return NotificationMessage(
        target_id=target.id,
        event_seq=seq,
        event_key=key,
        level=level,
        title=title[:512],
        body=body[:8000],
        poster_url=poster,
        state="pending",
        next_at=now,
        created_at=now,
    )


def take_events(db: OrmSession) -> int:
    """New events of the feed into messages, per mailbox from its cursor on. Returns how many; commits."""
    made = 0
    targets = mailboxes(db)
    if not targets:
        return 0
    lookup = _Lookup(db)
    keys: dict[int, str | None] = {}
    for target in targets:
        rows = list(
            db.scalars(select(ApiEvent).where(ApiEvent.seq > target.cursor).order_by(ApiEvent.seq).limit(TAKE_MAX))
        )
        if not rows:
            continue
        lang = texts.language(target.language)
        wanted: dict[str, list[ApiEvent]] = defaultdict(list)
        for row in rows:
            if row.seq not in keys:
                keys[row.seq] = key_of(db, row)
            key = keys[row.seq]
            if key is not None and level_of(target, key) is not None:
                wanted[key].append(row)
        for key, found in wanted.items():
            level = level_of(target, key) or "normal"
            if key != "health_changed" and len(found) > texts.BURST:
                names = [lookup.name_of(row) for row in found]
                shown = ", ".join(names[: texts.BURST])
                rest = len(names) - texts.BURST
                body = shown + (" " + texts.word("more", lang, count=rest) if rest > 0 else "")
                db.add(_message(target, key, level, texts.summary(key, lang, len(found)), body, None, None))
                made += 1
                continue
            for row in found:
                rendered = render(db, lookup, row, key, lang)
                if rendered is None:
                    continue
                db.add(_message(target, key, level, rendered[0], rendered[1], rendered[2], row.seq))
                made += 1
        target.cursor = rows[-1].seq
    db.commit()
    return made


def announce_update(db: OrmSession) -> int:
    """A newer nexcrate, once per version, to the mailboxes that want it. Commits when something was made."""
    status = updates.status(db)
    latest = status.get("latest")
    if not status.get("available") or not latest:
        return 0
    if database.get_setting(db, SETTING_UPDATE_ANNOUNCED, "") == latest:
        return 0
    made = 0
    for target in mailboxes(db):
        level = level_of(target, "update_available")
        if level is None:
            continue
        lang = texts.language(target.language)
        body = f"**{latest}**\n" + texts.word("current", lang, current=status.get("current") or "")
        db.add(_message(target, "update_available", level, texts.title("update_available", lang), body, None, None))
        made += 1
    database.set_setting(db, SETTING_UPDATE_ANNOUNCED, str(latest))
    db.commit()
    return made


# --- Sending ------------------------------------------------------------------------------------------------------- #


def notice_of(message: NotificationMessage) -> Notice:
    return Notice(
        title=message.title,
        body=message.body,
        level=message.level,
        poster_url=message.poster_url,
        event=message.event_key,
    )


def test_notice(language: str, code: str) -> Notice:
    lang = texts.language(language)
    return Notice(
        title=texts.title("test", lang, code=code), body=texts.word("test_body", lang), code=code, level="normal"
    )


async def send_one(message_id: int) -> NotificationMessage | None:
    """Send one message now and note the outcome."""
    with database.SessionLocal() as db:
        message = db.get(NotificationMessage, message_id)
        target = db.get(NotificationTarget, message.target_id) if message is not None else None
        if message is None or target is None:
            return None
        kind = target.channel
        config = build(kind, values(db, target))
        notice = notice_of(message)
        target_id = target.id
    problem: ChannelError | None = None
    try:
        await send(kind, config, notice)
    except ChannelError as exc:
        problem = exc
    now = utcnow()
    with database.SessionLocal() as db:
        message = db.get(NotificationMessage, message_id)
        if message is None:
            return None
        message.attempts += 1
        if problem is None:
            message.state, message.next_at, message.done_at = "delivered", None, now
            message.error_code, message.error_params = None, None
        else:
            message.error_code, message.error_params = problem.code, dict(problem.params)
            if message.attempts > len(BACKOFF):
                message.state, message.next_at, message.done_at = "failed", None, now
                logger.warning("Notification %d to target %d given up (%s)", message.id, target_id, problem.code)
            else:
                message.next_at = now + BACKOFF[message.attempts - 1]
                logger.info("Notification %d to target %d failed (%s); tried again later", message.id, target_id,
                            problem.code)  # fmt: skip
        db.commit()
        db.refresh(message)
        db.expunge(message)
        return message


def _prune(db: OrmSession) -> None:
    for target_id in db.scalars(select(NotificationTarget.id)):
        keep = list(
            db.scalars(
                select(NotificationMessage.id)
                .where(NotificationMessage.target_id == target_id)
                .order_by(NotificationMessage.id.desc())
                .limit(KEEP)
            )
        )
        if keep:
            db.execute(
                delete(NotificationMessage).where(
                    NotificationMessage.target_id == target_id,
                    NotificationMessage.id < min(keep),
                    NotificationMessage.state != "pending",
                )
            )
    db.commit()


def due(db: OrmSession) -> list[int]:
    found = []
    for message_id, target_id in db.execute(
        select(NotificationMessage.id, NotificationMessage.target_id)
        .where(NotificationMessage.state == "pending", NotificationMessage.next_at <= utcnow())
        .order_by(NotificationMessage.id)
        .limit(SEND_MAX)
    ).tuples():
        target = db.get(NotificationTarget, target_id)
        if target is not None and active(db, target):
            found.append(message_id)
    return found


async def run_job() -> None:
    with database.SessionLocal() as db:
        take_events(db)
        announce_update(db)
        waiting = due(db)
    for message_id in waiting:
        await send_one(message_id)
    if waiting:
        with database.SessionLocal() as db:
            _prune(db)


def start_at_newest(db: OrmSession, target: NotificationTarget) -> None:
    """A mailbox that just got confirmed starts at the newest event: nothing old lands on it."""
    target.cursor = feed.latest(db)

