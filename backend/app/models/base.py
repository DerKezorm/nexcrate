"""Declarative base, a UTC timestamp type and ``utcnow``."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator

#: The faster clock of a test bench (``services/automatic/clock.py``): every timestamp of nexcrate runs this many times
#: as fast from the start of the process. ⚠️ Here and not only in the automatic, so a download's failure and the
#: automatic's last search stand on one clock: with two clocks the plan saw the search after the failure and never
#: searched the replacement (3c bench, 25.09.2026).
BENCH_CLOCK_ENV = "NEXCRATE_BENCH_CLOCK_SPEED"
BENCH_SPEED_MAX = 1000.0


def bench_speed_from(raw: str | None) -> float:
    try:
        value = float(raw or "")
    except ValueError:
        return 1.0
    if not value > 1.0:
        return 1.0
    return min(value, BENCH_SPEED_MAX)


BENCH_SPEED = bench_speed_from(os.environ.get(BENCH_CLOCK_ENV))
_BENCH_START = datetime.now(UTC)


def _real_now() -> datetime:
    return datetime.now(UTC)


def utcnow() -> datetime:
    real = _real_now()
    if BENCH_SPEED == 1.0:
        return real
    return _BENCH_START + (real - _BENCH_START) * BENCH_SPEED


class UTCDateTime(TypeDecorator[datetime]):
    """A point in time, stored as naive UTC and read back as aware UTC.

    SQLite has no time zones. Without this type a value comes back naive, and
    comparing it with ``utcnow()`` raises. A naive value on the way in is refused:
    it is a bug, and guessing its zone would hide it.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("A naive datetime cannot be stored; use an aware UTC datetime.")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    #: Every ``Mapped[datetime]`` is stored as UTC without further ado.
    type_annotation_map: ClassVar[dict[Any, Any]] = {datetime: UTCDateTime}
