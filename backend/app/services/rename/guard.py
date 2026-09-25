"""Filing and renaming never touch one title at the same time (decision 19).

Filing a download, renaming a TBA file and reading a folder again call ``filing(title_id)``; renaming holds
``renaming(title_ids)``. Whoever comes second waits for the next round: a download goes back to ``completed``, a
title of a rename run is skipped as ``busy``. In one process; nexcrate runs in one.
"""

from __future__ import annotations

import threading
from collections import Counter
from collections.abc import Iterable, Iterator
from contextlib import contextmanager

_lock = threading.Lock()
_renaming: set[int] = set()
_filing: Counter[int] = Counter()


class Busy(Exception):
    """The title is being renamed, or filed while a rename wants it."""


def is_renaming(title_id: int) -> bool:
    with _lock:
        return title_id in _renaming


@contextmanager
def filing(title_id: int | None) -> Iterator[None]:
    """Hold a title while something files into its folders. Raises ``Busy`` while it is renamed."""
    if title_id is None:
        yield
        return
    with _lock:
        if title_id in _renaming:
            raise Busy
        _filing[title_id] += 1
    try:
        yield
    finally:
        with _lock:
            _filing[title_id] -= 1
            if _filing[title_id] <= 0:
                del _filing[title_id]


@contextmanager
def renaming(title_ids: Iterable[int]) -> Iterator[None]:
    """Hold titles while they are renamed. Raises ``Busy`` when one of them is being filed or renamed."""
    wanted = set(title_ids)
    with _lock:
        if any(_filing[title_id] for title_id in wanted) or wanted & _renaming:
            raise Busy
        _renaming.update(wanted)
    try:
        yield
    finally:
        with _lock:
            _renaming.difference_update(wanted)


def reset() -> None:
    """For tests."""
    with _lock:
        _renaming.clear()
        _filing.clear()
