"""Bazarr's numbers for nexcrate's rows.

* **A movie or a series** is a version: its number is the version's id.
* **An episode** is an episode in one version. The same episode in two versions are two episodes for Bazarr, so the
  number holds both: ``version id * EPISODE_FACTOR + episode id``. It stays below 2^53, so Bazarr's interface reads it
  without rounding, for version ids up to about 90 million.
* **A file** must get a new number whenever the file is a new one, even under the same name: Bazarr only looks for the
  subtitles of a file again when the number or the path changed. The number is the row's id with a check value of the
  file's path, size and origin in the low 16 bits: two rows never share one, and an upgrade under the same name gets a
  new one.
"""

from __future__ import annotations

import hashlib

EPISODE_FACTOR = 100_000_000
FILE_FACTOR = 1 << 16


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
