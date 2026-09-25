"""A limit per key of ``/api/v1`` (N38): a faulty program cannot bring nexcrate to its knees.

A bucket per key: ``RATE`` requests a second on average, ``BURST`` at once. Nexview asks a page of fifty tiles in one
request (``titles/lookup``) and makes a few requests a minute besides, far below. The buckets live in memory: after a
start every key begins full.
"""

from __future__ import annotations

import math
import threading
import time

RATE = 20.0
BURST = 200.0

_lock = threading.Lock()
#: Per key: tokens left, and when they were counted.
_buckets: dict[int, tuple[float, float]] = {}


def reset() -> None:
    with _lock:
        _buckets.clear()


def take(key_id: int, now: float | None = None) -> int | None:
    """Take one request from the key's bucket. None when it may go, else the seconds to wait (at least 1)."""
    moment = time.monotonic() if now is None else now
    with _lock:
        tokens, since = _buckets.get(key_id, (BURST, moment))
        tokens = min(BURST, tokens + max(0.0, moment - since) * RATE)
        if tokens < 1.0:
            _buckets[key_id] = (tokens, moment)
            return max(1, math.ceil((1.0 - tokens) / RATE))
        _buckets[key_id] = (tokens - 1.0, moment)
        return None
