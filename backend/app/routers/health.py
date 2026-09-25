"""Liveness check."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .. import __version__

router = APIRouter(tags=["system"])

#: When this process started. After a restore the interface waits until it changes.
STARTED = datetime.now(UTC).isoformat(timespec="seconds")


class Health(BaseModel):
    status: str = Field(description="Always ok when the server answers.", examples=["ok"])
    version: str = Field(description="Version of the running nexcrate.", examples=["0.1.0"])
    started: str = Field(description="When the running process started, ISO 8601.", examples=["2026-09-24T08:00:00Z"])


@router.get(
    "/api/health",
    response_model=Health,
    summary="Check that the server is running",
    description="Answers without a login and without touching the database. Used by the Docker healthcheck.",
)
def health() -> Health:
    return Health(status="ok", version=__version__, started=STARTED)
