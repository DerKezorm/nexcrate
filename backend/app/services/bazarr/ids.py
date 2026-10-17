"""Bazarr's numbers for nexcrate's rows.

* **A movie or a series** is a version: its number is the version's id.
* **An episode** is an episode in one version. The same episode in two versions are two episodes for Bazarr, so the
  number holds both: ``version id * EPISODE_FACTOR + episode id``. It stays below 2^53, so Bazarr's interface reads it
  without rounding, for version ids up to about 90 million.
* **An episode for Lingarr** must fit in 32 bits: ``episode id * 1024 + definition id`` (``lingarr_episode_id``).
  Episodes past about two million, and definitions from 1024 on, have no number there and stay out of Lingarr's list.
* **A file** must get a new number whenever the file is a new one, even under the same name: Bazarr only looks for the
  subtitles of a file again when the number or the path changed. The number is the row's id with a check value of the
  file's path, size and origin in the low 16 bits: two rows never share one, and an upgrade under the same name gets a
  new one.
"""

from __future__ import annotations

import hashlib

EPISODE_FACTOR = 100_000_000
FILE_FACTOR = 1 << 16
#: Lingarr keeps an episode's number in a 32-bit int, too small for ``episode_id``. Its numbers hold the episode and
#: the version's definition instead (a title has one version per definition): ``episode id * 1024 + definition id``.
LINGARR_FACTOR = 1 << 10
LINGARR_LIMIT = (1 << 31) - 1


def episode_id(version_id: int, episode_row_id: int) -> int:
    if not 0 < episode_row_id < EPISODE_FACTOR:
        raise ValueError("episode id out of range")
    return version_id * EPISODE_FACTOR + episode_row_id


def split_episode_id(number: int) -> tuple[int, int] | None:
    """``(version id, episode id)``, or None for a number that cannot be one."""
    if number <= 0:
        return None
    version_id, episode_row_id = divmod(number, EPISODE_FACTOR)
    if version_id <= 0 or episode_row_id <= 0:
        return None
    return version_id, episode_row_id


def file_id(row_id: int, relative_path: str, size: int, origin: str | None) -> int:
    check = hashlib.sha256(f"{relative_path}\x00{size}\x00{origin or ''}".encode()).digest()
    return row_id * FILE_FACTOR + int.from_bytes(check[:2], "big")


def row_of_file_id(number: int) -> int:
    return number // FILE_FACTOR


def lingarr_episode_id(episode_row_id: int, definition_id: int) -> int | None:
    """Lingarr's number of an episode in the version of that definition; None when it does not fit in 32 bits."""
    if episode_row_id <= 0 or not 0 < definition_id < LINGARR_FACTOR:
        return None
    number = episode_row_id * LINGARR_FACTOR + definition_id
    return number if number <= LINGARR_LIMIT else None


def split_lingarr_episode_id(number: int) -> tuple[int, int] | None:
    """``(episode id, definition id)``, or None for a number that cannot be one."""
    if not 0 < number <= LINGARR_LIMIT:
        return None
    episode_row_id, definition_id = divmod(number, LINGARR_FACTOR)
    if episode_row_id <= 0 or definition_id <= 0:
        return None
    return episode_row_id, definition_id
