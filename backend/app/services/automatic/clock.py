"""The clock of automatic searching and loading.

Every part of ``services/automatic`` reads the time through ``clock.now()``, never the system time itself, so a test
replaces this one function and moves time by hand.

**The faster clock of the test bench** (decided on 15.09.2026, built 25.09.2026): ``NEXCRATE_BENCH_CLOCK_SPEED=10``
lets every timestamp of nexcrate run ten times as fast from the start of the process (``models.base.utcnow``), and the
automatic's rounds (``interval``) come ten times as often, so the budget, the RSS interval and the quiet phases of the
end-to-end run take minutes instead of hours. Only for a throwaway instance; the log says at start that the switch is
set. Without it, or with anything that is no number above 1, the clock is the real one.
"""

from __future__ import annotations

from datetime import datetime

from ...models import base as models_base
from ...models import utcnow

SPEED_ENV = models_base.BENCH_CLOCK_ENV
SPEED_MAX = models_base.BENCH_SPEED_MAX
#: No round of the automatic comes more often than this, however fast the clock runs.
INTERVAL_FLOOR_SECONDS = 1.0
speed_from = models_base.bench_speed_from
SPEED = models_base.BENCH_SPEED


def now() -> datetime:
    return utcnow()


def interval(seconds: float) -> float:
    """The real seconds between two rounds of an automatic job that runs every ``seconds`` on its own clock."""
    if SPEED == 1.0:
        return seconds
    return max(INTERVAL_FLOOR_SECONDS, seconds / SPEED)
