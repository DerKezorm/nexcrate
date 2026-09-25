"""Brake against password guessing, in memory.

Three failed attempts are free. After that the wait grows from 1 second up to
five minutes. A success resets the counter, and so does an hour without a failure.

Counted per target: the account when the given username is the account's, the
given name otherwise, and a counter of its own for the current password on a
password change. Not per address: behind a reverse proxy every visitor would
share one, and one typo would lock out the whole household.

⚠️ The counters do not survive a restart. For a brake that is enough: it makes
guessing expensive, it does not keep books.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass

FREE_ATTEMPTS = 3
MAX_DELAY_SECONDS = 300
FORGET_AFTER_SECONDS = 3600
#: Above this many counters the forgotten ones are dropped, so made-up names cannot grow memory.
PRUNE_ABOVE = 10_000

ACCOUNT_KEY = "account"
PASSWORD_CHANGE_KEY = "password-change"

#: The clock. Tests replace it.
_clock = time.monotonic


@dataclass
class _State:
    failures: int = 0
    blocked_until: float = 0.0
    last_failure: float = 0.0


_lock = threading.Lock()
_states: dict[str, _State] = {}


def device_key(device_id: str) -> str:
    """The counter of a known browser (``known_devices``): its failures brake it alone."""
    return f"device:{device_id}"


def login_key(username: str) -> str:
    return f"login:{username.strip().casefold()}"


def _current(key: str, now: float) -> _State | None:
    state = _states.get(key)
    if state is not None and now - state.last_failure > FORGET_AFTER_SECONDS:
        del _states[key]
        return None
    return state


def wait_seconds(key: str, now: float | None = None) -> int:
    now = _clock() if now is None else now
    with _lock:
        state = _current(key, now)
        return 0 if state is None else max(0, math.ceil(state.blocked_until - now))


def failed(key: str, now: float | None = None) -> None:
    now = _clock() if now is None else now
    with _lock:
        if len(_states) > PRUNE_ABOVE:
            for stale in [name for name, state in _states.items() if now - state.last_failure > FORGET_AFTER_SECONDS]:
                del _states[stale]
        state = _current(key, now) or _states.setdefault(key, _State())
        state.failures += 1
        state.last_failure = now
        if state.failures > FREE_ATTEMPTS:
            exponent = min(state.failures - FREE_ATTEMPTS - 1, 16)
            state.blocked_until = now + min(MAX_DELAY_SECONDS, 2**exponent)


def succeeded(key: str) -> None:
    with _lock:
        _states.pop(key, None)


def reset_all() -> None:
    with _lock:
        _states.clear()
