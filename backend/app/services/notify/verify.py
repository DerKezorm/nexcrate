"""Did the test message reach somebody? A four-digit code in the test message, typed back (taken over from Nexview).

A 200 of a push service means "taken", not "arrived": a wrong topic or a muted app answer 200 too. Whoever can type
the code has seen the message, and only then a mailbox is saved. Kept in memory: it lives minutes, belongs to one
screen and has no business in the database; a restart costs another click on "Test". nexcrate has one account, so one
pending code per service.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

VALID_SECONDS = 600
MAX_TRIES = 5


@dataclass
class _Pending:
    code: str
    print_: tuple[str, ...]
    made: float
    tries: int = 0
    confirmed: bool = False


_pending: dict[str, _Pending] = {}


def _expired(entry: _Pending) -> bool:
    return time.monotonic() - entry.made > VALID_SECONDS


def _sweep() -> None:
    for key in [key for key, entry in _pending.items() if _expired(entry)]:
        _pending.pop(key, None)


def start(kind: str, print_: tuple[str, ...], *, confirmed: bool = False) -> str:
    """A new code for this service; replaces an older one. ``confirmed`` for a service without a code (e-mail): the
    accepted test counts as the proof."""
    _sweep()
    code = f"{secrets.randbelow(10000):04d}"
    _pending[kind] = _Pending(code=code, print_=print_, made=time.monotonic(), confirmed=confirmed)
    return code


def confirm(kind: str, typed: str) -> str | None:
    """Check the typed code. None when right, else the error code."""
    _sweep()
    entry = _pending.get(kind)
    if entry is None:
        return "notify_code_missing"
    entry.tries += 1
    if entry.tries > MAX_TRIES:
        _pending.pop(kind, None)
        return "notify_code_too_many"
    if not secrets.compare_digest(entry.code, typed.strip()):
        return "notify_code_wrong"
    entry.confirmed = True
    return None


def confirmed_for(kind: str, print_: tuple[str, ...]) -> bool:
    """Was exactly this connection confirmed? Otherwise one address could be tested and another saved."""
    _sweep()
    entry = _pending.get(kind)
    return bool(entry and entry.confirmed and entry.print_ == print_)


def forget(kind: str) -> None:
    _pending.pop(kind, None)


def reset() -> None:
    _pending.clear()
