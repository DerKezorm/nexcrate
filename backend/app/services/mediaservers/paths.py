"""A folder as the media server sees it, and the library it lies in.

nexcrate sees ``/media/movies/...``, the server perhaps ``/volume1/media/movies/...``. The owner's pairs
``{local, remote}`` translate; without a pair that fits, the path is taken as it is.

⚠️ Nothing is guessed here. A path that lies in no location of any library is told as that (``None``); the caller
decides what to do with it, and it never becomes a path the server was not given by the owner or by itself.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any


def _windows(path: str) -> bool:
    """A path in Windows' shape: a drive letter, a UNC share or backslashes and no slash."""
    return (len(path) >= 2 and path[1] == ":" and path[0].isalpha()) or path.startswith("\\\\") or (
        "\\" in path and "/" not in path
    )


def _parts(path: str) -> list[str]:
    return [part for part in path.replace("\\", "/").split("/") if part]


def _comparable(path: str) -> list[str]:
    parts = _parts(path)
    return [part.casefold() for part in parts] if _windows(path) else parts


def _starts_with(path: Sequence[str], prefix: Sequence[str]) -> bool:
    return len(prefix) <= len(path) and list(path[: len(prefix)]) == list(prefix)


def to_remote(local_path: str, mappings: Iterable[dict[str, Any]]) -> str:
    """``local_path`` as the server sees it: the pair with the longest fitting ``local`` decides, whole folder names
    only (``/media`` does not fit ``/media2``). Without a fitting pair the path comes back unchanged."""
    path = _parts(local_path)
    best: tuple[int, str] | None = None
    for mapping in mappings:
        local, remote = str(mapping.get("local") or ""), str(mapping.get("remote") or "")
        prefix = _parts(local)
        if not prefix or not remote or not _starts_with(path, prefix):
            continue
        if best is None or len(prefix) > best[0]:
            best = (len(prefix), remote)
    if best is None:
        return local_path.replace("\\", "/")
    length, remote = best
    separator = "\\" if _windows(remote) else "/"
    rest = path[length:]
    base = remote.rstrip("/\\")
    if not base:
        # The remote side is the root itself.
        return separator + separator.join(rest)
    return separator.join([base, *rest])


@dataclass(frozen=True)
class Library:
    """A library as stored on the server's row."""

    id: str
    name: str
    kind: str | None
    locations: tuple[str, ...]
    refresh: bool


def stored_libraries(raw: Iterable[dict[str, Any]] | None) -> list[Library]:
    found = []
    for entry in raw or []:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        locations = entry.get("locations")
        found.append(
            Library(
                id=str(entry["id"]),
                name=str(entry.get("name") or ""),
                kind=entry.get("kind") if entry.get("kind") in ("movie", "series", "music") else None,
                locations=tuple(str(item) for item in locations) if isinstance(locations, list) else (),
                refresh=bool(entry.get("refresh")),
            )
        )
    return found


def library_of(remote_path: str, libraries: Iterable[Library]) -> Library | None:
    """The library one of whose locations holds ``remote_path``; the deepest location wins. None: it lies in none."""
    path = _comparable(remote_path)
    best: tuple[int, Library] | None = None
    for library in libraries:
        for location in library.locations:
            prefix = _comparable(location)
            if not prefix or not _starts_with(path, prefix):
                continue
            if best is None or len(prefix) > best[0]:
                best = (len(prefix), library)
    return best[1] if best is not None else None
