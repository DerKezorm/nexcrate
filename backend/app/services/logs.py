"""Logging, modelled on Nexview.

Lines go to ``data/logs/nexcrate.log`` and to stderr. The file rotates at a size
limit and keeps three old files; files older than 14 days are removed. Times are
UTC and say so (``2026-09-13T10:15:30Z``): a container rarely runs in the time
zone of its operator.

Four modes, switchable at runtime
---------------------------------

``quiet``
    Warnings and errors only.
``normal``
    The default: state changes, warnings, errors.
``detailed``
    Plus the way there: every request, every outgoing HTTP call.
``trace``
    Plus the raw output of third-party libraries (HTTP, SQL) and response bodies of
    outgoing calls, truncated. Produces many lines.

``detailed`` and ``trace`` switch themselves off. The file is a ring buffer: a
forgotten ``trace`` overwrites within a day exactly the lines somebody wanted to
keep, and nobody remembers to switch it back. They are set with a duration, and
the app falls back to ``normal`` afterwards, also across a restart.

``NEXCRATE_LOG_LEVEL`` fixes the mode. It is the way out when the app does not
start at all and there is no interface to change it.

⚠️ Nothing secret belongs in a line
-----------------------------------

A log is passed on: attached to a bug report, pasted into a forum. Request headers
are never logged, query parameters with secret-looking names are masked where the
URL is written. ``RedactingFilter`` sits on every handler as the last fence: it
masks secret-looking key=value and JSON pairs, bearer tokens, bcrypt hashes, 32 hex
character keys (the shape of Radarr keys) and long base64url runs (our session
tokens have 43 characters). Writing a secret into a message stays a bug.
"""

from __future__ import annotations

import logging
import re
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ..config import get_settings

logger = logging.getLogger("nexcrate.logs")

APP_LOGGER = "nexcrate"
LOG_FILE_NAME = "nexcrate.log"
BACKUP_COUNT = 3
RETENTION_DAYS = 14

# Debugging fills the file fast. In the deep modes it gets more room, otherwise the
# interesting beginning has already rolled away.
MAX_BYTES_NORMAL = 5 * 1024 * 1024
MAX_BYTES_DEEP = 25 * 1024 * 1024

#: Longest message the log viewer returns for one entry, traceback included.
MESSAGE_LIMIT = 20_000

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s [%(ctx)s] | %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

#: Third-party loggers that would otherwise write a line for every single call.
NOISY_LOGGERS = (
    "httpx",
    "httpcore",
    "hpack",
    "urllib3",
    "multipart",
    "python_multipart",
    "sqlalchemy.engine",
    "sqlalchemy.pool",
    "uvicorn.access",
    "watchfiles",
    "asyncio",
)

MODES: dict[str, dict[str, int]] = {
    #            own messages           everything else        third-party libraries
    "quiet": {"app": logging.WARNING, "root": logging.WARNING, "libs": logging.WARNING},
    "normal": {"app": logging.INFO, "root": logging.INFO, "libs": logging.WARNING},
    "detailed": {"app": logging.DEBUG, "root": logging.INFO, "libs": logging.WARNING},
    "trace": {"app": logging.DEBUG, "root": logging.DEBUG, "libs": logging.DEBUG},
}

#: Modes that stay until they are changed.
PERMANENT_MODES = ("quiet", "normal")
#: Modes that switch themselves off after a duration.
DEEP_MODES = ("detailed", "trace")
DEFAULT_MODE = "normal"
#: Durations in minutes for the deep modes.
DEEP_MINUTES = (30, 120, 480)
#: Every value the API accepts for ``minutes``; 0 belongs to the permanent modes.
ALLOWED_MINUTES = (*DEEP_MINUTES, 0)

SETTING_MODE = "log_mode"
SETTING_UNTIL = "log_mode_until"

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

LINE_PATTERN = re.compile(
    r"^(?P<time>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) +"
    r"(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL) +"
    r"(?P<logger>\S+) "
    r"\[(?P<ctx>[^\]]*)\] "
    r"\| (?P<message>.*)$"
)

_ENV_ALIASES = {"warning": "quiet", "warn": "quiet", "error": "quiet", "info": "normal", "debug": "detailed"}


# --- What must never reach a file ------------------------------------------ #

_WORD = (
    r"(?:api[_\-]?key|key|token|password|passwd|passphrase|pwd|secret|auth|authorization"
    r"|cookie|session|credential|signature)"
)
# The secret word may carry a plural s and one more segment after _ or - (password_hash, session-id).
# It may not run on into letters: "KeyError" and "author" are not secrets.
_SUFFIX = r"s?(?:[_\-][A-Za-z0-9]{1,20})?"
_BARE_NAME = r"(?<![A-Za-z0-9_\-])[A-Za-z0-9_\-]{0,40}?" + _WORD + _SUFFIX

_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    # bcrypt hashes.
    (re.compile(r"\$2[abxy]?\$\d{2}\$[./A-Za-z0-9]{53}"), "***"),
    # Our encrypted credentials.
    (re.compile(r"\benc:[A-Za-z0-9_\-=]+"), "enc:***"),
    # Credentials inside a URL: scheme://user:secret@host
    (re.compile(r"(://[^/\s:@]{1,200}:)[^@\s/]{1,200}@"), r"\1***@"),
    # Authorization schemes, before key=value, so "Authorization: Bearer x" keeps its structure.
    (re.compile(r"\b(Bearer)\s+[A-Za-z0-9._~+/\-]+=*", re.IGNORECASE), r"\1 ***"),
    (re.compile(r"\b(Basic)\s+[A-Za-z0-9+/]{8,}={0,2}", re.IGNORECASE), r"\1 ***"),
    # JSON and Python dict pairs with a quoted value: "apiKey": "value", 'password': 'value'
    (
        re.compile(r'("[^"\\\s]{0,40}?' + _WORD + _SUFFIX + r'"\s*:\s*")(?:[^"\\]|\\.)*(")', re.IGNORECASE),
        r"\1***\2",
    ),
    (
        re.compile(r"('[^'\\\s]{0,40}?" + _WORD + _SUFFIX + r"'\s*:\s*')(?:[^'\\]|\\.)*(')", re.IGNORECASE),
        r"\1***\2",
    ),
    # JSON pairs with an unquoted value: "apiKey": 12345
    (
        re.compile(r'("[^"\\\s]{0,40}?' + _WORD + _SUFFIX + r'"\s*:\s*)(?![\s"\'{\[*])[^\s,}\]]+', re.IGNORECASE),
        r"\1***",
    ),
    # key="value" and key='value'
    (re.compile("(" + _BARE_NAME + r"\s*[=:]\s*)([\"'])[^\"'\n]{0,500}?\2", re.IGNORECASE), r"\1\2***\2"),
    # key=value and key: value
    (re.compile("(" + _BARE_NAME + r"\s*[=:]\s*)(?![\s\"'*])[^\s&,;\"'<>]+", re.IGNORECASE), r"\1***"),
    # Radarr-style API keys: exactly 32 hex characters.
    (re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{32}(?![0-9A-Fa-f])"), "***"),
    # Long base64url runs: our session tokens have 43 characters, Fernet tokens more.
    (re.compile(r"(?<![A-Za-z0-9_\-])[A-Za-z0-9_\-]{40,}(?![A-Za-z0-9_\-])"), "***"),
)


def redact(text: str) -> str:
    """Mask everything in a line that looks like a secret."""
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    """The redaction on a handler, applied to message, traceback and stack.

    ⚠️ The args are emptied when the message changed. Otherwise the handler formats
    again from the original, and the redaction does nothing.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a broken message must not stop the line
            return True
        clean = redact(message)
        if clean != message:
            record.msg = clean
            record.args = ()
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


def ensure_redaction(handler: logging.Handler) -> None:
    if not any(isinstance(existing, RedactingFilter) for existing in handler.filters):
        handler.addFilter(RedactingFilter())


#: Set while nexcrate calls an address whose path or query is secret, such as a release's download link.
_quiet_libraries: ContextVar[bool] = ContextVar("nexcrate_quiet_libraries", default=False)
#: Libraries that write whole addresses in the trace mode, response headers with a redirect's target included.
QUIET_LIBRARY_LOGGERS = ("httpx", "httpcore")


@contextmanager
def quiet_libraries() -> Iterator[None]:
    """Drop the lines of httpx and httpcore while the block runs, in this context only.

    ⚠️ In the trace mode httpx writes ``HTTP Request: GET <whole address>``. The redaction masks ``apikey=...``, but
    not a passkey in a path (``/download/<passkey>/``). nexcrate's own line of the call (``http_log`` with
    ``origin_only``) stays and says what happened.
    """
    token = _quiet_libraries.set(True)
    try:
        yield
    finally:
        _quiet_libraries.reset(token)


class QuietLibrariesFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not _quiet_libraries.get():
            return True
        return not any(record.name == name or record.name.startswith(f"{name}.") for name in QUIET_LIBRARY_LOGGERS)


# --- Context of a request --------------------------------------------------- #
#
# ⚠️ The ContextVar holds a mutable dict, not a string. FastAPI runs sync endpoints and
# dependencies in a thread pool, which gets a copy of the context: a value set there
# would be gone after the call. The dict is the same object in every copy.

_context: ContextVar[dict[str, str] | None] = ContextVar("nexcrate_log_context", default=None)


def bind_request(request_id: str) -> Token[dict[str, str] | None]:
    return _context.set({"rid": request_id})


def unbind_request(token: Token[dict[str, str] | None]) -> None:
    _context.reset(token)


def set_session_ref(reference: str) -> None:
    """Add the short session reference to the lines of the running request."""
    data = _context.get()
    if data is not None:
        data["session"] = reference


def set_key_ref(key_id: int) -> None:
    """Add the number of the API key to the lines of the running request. Never the key itself."""
    data = _context.get()
    if data is not None:
        data["key"] = str(key_id)


def set_caller_ref(reference: str) -> None:
    """Add the request id the calling program sent, so one action can be followed through both logs."""
    data = _context.get()
    if data is not None:
        data["caller"] = reference


def current_request_id() -> str | None:
    data = _context.get()
    return data.get("rid") if data else None


class ContextFilter(logging.Filter):
    """Request id and session reference on every line: ``[ab12cd34 s:ef56ab78]``. A request of ``/api/v1`` carries the
    number of its key and the caller's own request id instead: ``[ab12cd34 k:3 c:9f8e7d6c]``."""

    def filter(self, record: logging.LogRecord) -> bool:
        data = _context.get() or {}
        parts = [data["rid"]] if data.get("rid") else []
        if data.get("session"):
            parts.append(f"s:{data['session']}")
        if data.get("key"):
            parts.append(f"k:{data['key']}")
        if data.get("caller"):
            parts.append(f"c:{data['caller']}")
        record.ctx = " ".join(parts) if parts else "-"
        return True


class _NoDuplicateAsgiTraceback(logging.Filter):
    """uvicorn's second traceback says nothing our own line does not say better.

    ⚠️ Compared stripped: uvicorn writes the message with a trailing newline. Nexmail
    compared without stripping, and the filter never matched.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return record.getMessage().strip() != "Exception in ASGI application"


# --- Files ------------------------------------------------------------------ #


def log_dir() -> Path:
    directory = get_settings().data_dir / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def log_file() -> Path:
    return log_dir() / LOG_FILE_NAME


def rotated_files() -> list[Path]:
    """The kept old files, newest first (``nexcrate.log.1`` is the newest)."""

    def number(path: Path) -> int:
        suffix = path.name.rsplit(".", 1)[-1]
        return int(suffix) if suffix.isdigit() else 0

    return sorted((path for path in log_dir().glob(f"{LOG_FILE_NAME}.*") if path.is_file()), key=number)


def files_for_download() -> list[Path]:
    """Oldest first, so the lines read from top to bottom."""
    flush()
    return [path for path in [*reversed(rotated_files()), log_file()] if path.is_file()]


def purge_old() -> None:
    limit = time.time() - RETENTION_DAYS * 86400
    for path in log_dir().glob(f"{LOG_FILE_NAME}.*"):
        try:
            if path.stat().st_mtime < limit:
                path.unlink(missing_ok=True)
        except OSError:
            continue


# --- Setting up and switching ----------------------------------------------- #

_MARK = "_nexcrate"
_file_handler: RotatingFileHandler | None = None
_console_handler: logging.StreamHandler | None = None  # type: ignore[type-arg]
_mode = DEFAULT_MODE


def _formatter() -> logging.Formatter:
    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)
    formatter.converter = time.gmtime
    return formatter


def env_mode() -> str | None:
    """The mode from ``NEXCRATE_LOG_LEVEL``, if set and understood."""
    raw = (get_settings().log_level or "").strip().lower()
    if not raw:
        return None
    if raw in MODES:
        return raw
    return _ENV_ALIASES.get(raw)


def setup(console: bool = True) -> None:
    """Set up logging, once at start.

    The stored mode is not read here: on the very first start there is no database yet.
    ``apply_stored_mode`` follows once it exists. The command line tools pass
    ``console=False``: their own output is enough in a terminal.
    """
    global _file_handler, _console_handler

    root = logging.getLogger()
    uvicorn_error = logging.getLogger("uvicorn.error")
    for target in (root, uvicorn_error):
        for handler in list(target.handlers):
            if getattr(handler, _MARK, False):
                target.removeHandler(handler)
                handler.close()

    formatter = _formatter()

    file_handler = RotatingFileHandler(
        log_file(), maxBytes=MAX_BYTES_NORMAL, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    console_handler = logging.StreamHandler(sys.stderr) if console else None
    for handler in (file_handler, console_handler):
        if handler is None:
            continue
        handler.setFormatter(formatter)
        handler.addFilter(ContextFilter())
        handler.addFilter(QuietLibrariesFilter())
        handler.addFilter(RedactingFilter())
        setattr(handler, _MARK, True)
        root.addHandler(handler)
    _file_handler = file_handler
    _console_handler = console_handler

    # uvicorn sets its loggers to propagate=False. Without the next line no start error and
    # no crash of the server itself reaches the file the operator can download.
    uvicorn_error.addHandler(file_handler)
    if not any(isinstance(existing, _NoDuplicateAsgiTraceback) for existing in uvicorn_error.filters):
        uvicorn_error.addFilter(_NoDuplicateAsgiTraceback())
    # uvicorn brings handlers of its own when started from the command line. They get the
    # redaction too: docker logs is passed on just like the file.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        for handler in logging.getLogger(name).handlers:
            ensure_redaction(handler)

    apply_mode(env_mode() or DEFAULT_MODE)
    raw = (get_settings().log_level or "").strip()
    if raw and env_mode() is None:
        logger.warning(
            "NEXCRATE_LOG_LEVEL %r is not understood and ignored. Use quiet, normal, detailed or trace.", raw
        )
    purge_old()


def apply_mode(mode: str) -> None:
    """Make a mode effective, without a restart. A restart often destroys what was to be examined."""
    global _mode
    if mode not in MODES:
        mode = DEFAULT_MODE
    levels = MODES[mode]
    _mode = mode
    logging.getLogger().setLevel(levels["root"])
    logging.getLogger(APP_LOGGER).setLevel(levels["app"])
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(levels["libs"])
    if _file_handler is not None:
        _file_handler.maxBytes = MAX_BYTES_DEEP if mode in DEEP_MODES else MAX_BYTES_NORMAL
    if _console_handler is not None:
        # Never finer than INFO: the container output stays readable during debugging,
        # the details are in the file.
        _console_handler.setLevel(max(levels["app"], logging.INFO))


def current_mode() -> str:
    return _mode


def is_valid_mode(mode: str, minutes: int) -> bool:
    """Permanent modes take 0 minutes, deep modes one of the allowed durations."""
    if mode in PERMANENT_MODES:
        return minutes == 0
    if mode in DEEP_MODES:
        return minutes in DEEP_MINUTES
    return False


@dataclass(frozen=True)
class ModeState:
    mode: str
    until: datetime | None
    fixed_by_env: bool


def apply_stored_mode() -> None:
    """Take over the stored mode. Called after ``init_db``."""
    fixed = env_mode()
    if fixed:
        apply_mode(fixed)
        logger.info("Log mode %s, fixed by NEXCRATE_LOG_LEVEL", fixed)
        return
    mode, until = _stored()
    if mode in DEEP_MODES and (until is None or until <= _now()):
        # The time ran out while the app was not running.
        _store(DEFAULT_MODE, None)
        mode, until = DEFAULT_MODE, None
    apply_mode(mode)
    logger.info("Log mode %s until %s", mode, _iso(until) or "-")


def set_mode(mode: str, minutes: int = 0) -> ModeState:
    """Switch and store the mode. Raises ``ValueError`` for an invalid combination."""
    if not is_valid_mode(mode, minutes):
        raise ValueError(f"invalid log mode {mode!r} with {minutes} minutes")
    until = _now() + timedelta(minutes=minutes) if mode in DEEP_MODES else None
    previous = _mode
    _store(mode, until)
    louder = MODES[mode]["app"] <= MODES[previous]["app"]
    if not louder:
        # Written before switching, or a switch to quiet would not be in the file.
        logger.info("Log mode changed from %s to %s", previous, mode)
    apply_mode(mode)
    if louder:
        logger.info("Log mode changed from %s to %s until %s", previous, mode, _iso(until) or "-")
    return state()


def state() -> ModeState:
    """The mode in effect, with an expired deep mode already taken back."""
    fixed = env_mode()
    if fixed:
        return ModeState(mode=fixed, until=None, fixed_by_env=True)
    enforce_expiry()
    _mode_stored, until = _stored()
    return ModeState(mode=_mode, until=until if _mode in DEEP_MODES else None, fixed_by_env=False)


def enforce_expiry() -> bool:
    """Take an expired deep mode back to ``normal``. The background job calls this every 30 seconds."""
    if env_mode():
        return False
    mode, until = _stored()
    if mode not in DEEP_MODES:
        if _mode in DEEP_MODES:
            # The store says permanent, memory says deep: the store wins.
            apply_mode(mode)
        return False
    if until is not None and until > _now():
        return False
    _store(DEFAULT_MODE, None)
    apply_mode(DEFAULT_MODE)
    logger.info("Log mode %s expired, back to %s", mode, DEFAULT_MODE)
    return True


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime | None) -> str | None:
    return moment.astimezone(UTC).strftime(DATE_FORMAT) if moment else None


def _stored() -> tuple[str, datetime | None]:
    """Stored mode and end time. Falls back to the default when the database cannot be read:
    logging must never keep the app from starting."""
    try:
        from ..db import SessionLocal, get_setting

        with SessionLocal() as db:
            mode = get_setting(db, SETTING_MODE).strip()
            raw_until = get_setting(db, SETTING_UNTIL).strip()
    except Exception:  # noqa: BLE001 - without a readable setting the default applies, whatever the reason
        return DEFAULT_MODE, None
    if mode not in MODES:
        mode = DEFAULT_MODE
    until: datetime | None = None
    if raw_until:
        try:
            until = datetime.fromisoformat(raw_until)
        except ValueError:
            until = None
        else:
            if until.tzinfo is None:
                until = until.replace(tzinfo=UTC)
    return mode, until


def _store(mode: str, until: datetime | None) -> None:
    from ..db import SessionLocal, set_setting

    with SessionLocal() as db:
        set_setting(db, SETTING_MODE, mode)
        set_setting(db, SETTING_UNTIL, _iso(until) or "")
        db.commit()


# --- Reading and clearing ---------------------------------------------------- #


@dataclass(frozen=True)
class LogLine:
    time: str
    level: str
    logger: str
    message: str
    request_id: str | None


def flush() -> None:
    for handler in logging.getLogger().handlers:
        if getattr(handler, _MARK, False):
            handler.flush()


def parse_line(raw: str) -> LogLine | None:
    """One line of the file as ``{time, level, logger, message, request_id}``; None for a continuation."""
    match = LINE_PATTERN.match(raw.rstrip("\r\n"))
    if match is None:
        return None
    request_id: str | None = None
    for part in match.group("ctx").split():
        if part != "-" and not part.startswith("s:"):
            request_id = part
    return LogLine(
        time=match.group("time"),
        level=match.group("level"),
        logger=match.group("logger"),
        message=match.group("message"),
        request_id=request_id,
    )


def read(limit: int = 200, level: str | None = None, search: str | None = None) -> list[LogLine]:
    """The newest lines, newest first.

    ``level`` means "this level and higher". An equality check would be the classic trap:
    whoever picks WARNING looks for the errors and would not see them.

    Only the current file is read; for everything the download is there. A traceback stays
    with the line it belongs to.
    """
    path = log_file()
    if not path.is_file():
        return []
    flush()
    threshold = LEVELS.index(level) if level in LEVELS else 0
    needle = search.casefold() if search else None

    entries: list[LogLine] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            parsed = parse_line(raw)
            if parsed is not None:
                entries.append(parsed)
            elif entries and len(entries[-1].message) < MESSAGE_LIMIT:
                last = entries[-1]
                message = (last.message + "\n" + raw.rstrip("\r\n"))[:MESSAGE_LIMIT]
                entries[-1] = LogLine(last.time, last.level, last.logger, message, last.request_id)

    chosen = [
        entry
        for entry in entries
        if LEVELS.index(entry.level) >= threshold and (needle is None or _matches(entry, needle))
    ]
    chosen.reverse()
    return chosen[:limit]


def _matches(entry: LogLine, needle: str) -> bool:
    # The request id belongs in the search: the error message in the interface shows it,
    # and searching for it returns exactly the lines of that one request.
    return any(needle in (value or "").casefold() for value in (entry.message, entry.request_id, entry.logger))


def clear() -> None:
    """Empty the current file and remove the rotated ones, without losing the running handler."""
    handler = _file_handler
    if handler is not None and handler.stream is not None:
        handler.acquire()
        try:
            handler.flush()
            handler.stream.seek(0)
            handler.stream.truncate(0)
        finally:
            handler.release()
    elif log_file().is_file():
        log_file().write_text("", encoding="utf-8")
    for path in rotated_files():
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove the old log file %s", path.name)
