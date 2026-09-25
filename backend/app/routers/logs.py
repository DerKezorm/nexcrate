"""Read, download and clear the log, and switch the log mode."""

from __future__ import annotations

import logging
import tempfile
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import IO, Annotated

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..meldungen import error, error_responses
from ..services import logs

logger = logging.getLogger("nexcrate.logs")

router = APIRouter(prefix="/api/logs", tags=["logs"])

_CHUNK = 64 * 1024


class LogLineOut(BaseModel):
    time: str = Field(description="UTC, ISO 8601.", examples=["2026-09-13T10:15:30Z"])
    level: str = Field(examples=["INFO"])
    logger: str = Field(examples=["nexcrate.auth"])
    message: str
    request_id: str | None = Field(description="The request the line belongs to, if any.", examples=["ab12cd34"])


class LogModeOut(BaseModel):
    mode: str = Field(description="quiet, normal, detailed or trace.", examples=["normal"])
    until: datetime | None = Field(description="When a deep mode falls back to normal, UTC. Null otherwise.")
    fixed_by_env: bool = Field(description="True when NEXCRATE_LOG_LEVEL fixes the mode.")


class LogsOut(BaseModel):
    lines: list[LogLineOut]
    mode: LogModeOut


class LogModeIn(BaseModel):
    mode: str = Field(max_length=32, description="quiet, normal, detailed or trace.")
    minutes: int = Field(
        default=0,
        description="0 for quiet and normal. 30, 120 or 480 for detailed and trace, which then fall back to normal.",
    )


class ZipResponse(StreamingResponse):
    media_type = "application/zip"


def _mode_out() -> LogModeOut:
    state = logs.state()
    return LogModeOut(mode=state.mode, until=state.until, fixed_by_env=state.fixed_by_env)


@router.get(
    "",
    response_model=LogsOut,
    summary="Read the newest log lines",
    description=(
        "The newest lines of the current log file, newest first, with the log mode. "
        "`level` means this level and higher. `search` looks in message, logger and request id. "
        "A traceback stays with its line."
    ),
)
def read_logs(
    limit: Annotated[int, Query(ge=1, le=1000, description="How many lines, 1 to 1000.")] = 200,
    level: Annotated[str | None, Query(max_length=10, description="DEBUG, INFO, WARNING, ERROR or CRITICAL.")] = None,
    search: Annotated[str | None, Query(max_length=200, description="Text to look for.")] = None,
) -> LogsOut:
    chosen = (level or "").strip().upper() or None
    if chosen is not None and chosen not in logs.LEVELS:
        raise error("invalid_input", "The input is not valid.", 422, fields=["level"])
    lines = logs.read(limit=limit, level=chosen, search=(search or "").strip() or None)
    return LogsOut(
        lines=[LogLineOut(**line.__dict__) for line in lines],
        mode=_mode_out(),
    )


@router.put(
    "/mode",
    response_model=LogModeOut,
    summary="Switch the log mode",
    description=(
        "quiet and normal stay until changed and take `minutes` 0. detailed and trace need `minutes` "
        "30, 120 or 480 and fall back to normal afterwards, also across a restart. "
        "While NEXCRATE_LOG_LEVEL is set the mode cannot be changed here."
    ),
    responses=error_responses((409, "log_mode_fixed"), (422, "log_mode_invalid")),
)
def set_log_mode(payload: LogModeIn) -> LogModeOut:
    if logs.env_mode():
        raise error(
            "log_mode_fixed",
            "The log mode is fixed by NEXCRATE_LOG_LEVEL and cannot be changed here.",
            409,
        )
    if not logs.is_valid_mode(payload.mode, payload.minutes):
        raise error("log_mode_invalid", "This log mode or duration does not exist.", 422)
    logs.set_mode(payload.mode, payload.minutes)
    return _mode_out()


@router.get(
    "/download",
    response_class=ZipResponse,
    response_model=None,
    summary="Download the log files as a zip",
    description="A zip archive with the current log file and the rotated ones, unfiltered.",
    responses={
        200: {
            "description": "The zip archive.",
            "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
def download_logs() -> StreamingResponse:
    spool: IO[bytes] = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)  # noqa: SIM115 - closed by the stream
    with zipfile.ZipFile(spool, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in logs.files_for_download():
            archive.write(path, arcname=path.name)
    spool.seek(0)
    name = f"nexcrate-logs-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.zip"
    return ZipResponse(
        _chunks(spool),
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


def _chunks(spool: IO[bytes]) -> Iterator[bytes]:
    try:
        while chunk := spool.read(_CHUNK):
            yield chunk
    finally:
        spool.close()


@router.delete(
    "",
    status_code=204,
    response_model=None,
    summary="Clear the log files",
    description="Empties the current log file and removes the rotated ones.",
)
def clear_logs() -> None:
    logs.clear()
    logger.info("Log files cleared")
