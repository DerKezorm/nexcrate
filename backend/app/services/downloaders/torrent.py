"""Torrent files and magnet links: just enough to know a torrent's info hash and that its paths stay in its folder.

* Bencoding is read by hand and strictly: integers without leading zeros, byte strings with their length, nesting at
  most ``MAX_DEPTH`` deep. Nothing is evaluated. The fetch lets at most 10 MB in.
* The info hash is the SHA-1 of the ``info`` dictionary exactly as it stands in the file, in lower-case hex; a torrent
  of BitTorrent v2 only has the SHA-256 cut to 40 characters, as qBittorrent shows it. A magnet link's ``btih`` comes
  in hex (40) or base32 (32).
* ⚠️ A path in a torrent is a list of names. An empty name, ``.``, ``..``, a slash, a backslash, a NUL or a drive make
  the torrent invalid: no path from a torrent may leave its folder.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl

MAX_DEPTH = 64
MAX_FILES = 100_000
_INTEGER = re.compile(rb"-?(?:0|[1-9][0-9]*)")
_HEX_HASH = re.compile(r"[0-9a-fA-F]{40}")
_BASE32_HASH = re.compile(r"[A-Za-z2-7]{32}")
_DRIVE = re.compile(r"^[A-Za-z]:")


class TorrentInvalid(ValueError):
    """Not a torrent nexcrate accepts. The message is safe for a log line."""


def _string(data: bytes, index: int) -> tuple[bytes, int]:
    colon = data.find(b":", index, index + 21)
    if colon < 0:
        raise TorrentInvalid("a string without its length")
    digits = data[index:colon]
    if not digits.isdigit() or (len(digits) > 1 and digits.startswith(b"0")):
        raise TorrentInvalid("a string with a broken length")
    start = colon + 1
    end = start + int(digits)
    if end > len(data):
        raise TorrentInvalid("the file ends inside a string")
    return data[start:end], end


def _decode(data: bytes, index: int, depth: int, spans: dict[bytes, tuple[int, int]] | None) -> tuple[Any, int]:
    if depth > MAX_DEPTH:
        raise TorrentInvalid("nested too deeply")
    if index >= len(data):
        raise TorrentInvalid("the file ends early")
    lead = data[index : index + 1]
    if lead == b"i":
        end = data.find(b"e", index)
        digits = data[index + 1 : end] if end > 0 else b""
        if end < 0 or not _INTEGER.fullmatch(digits):
            raise TorrentInvalid("a broken integer")
        return int(digits), end + 1
    if lead == b"l":
        items: list[Any] = []
        index += 1
        while data[index : index + 1] != b"e":
            value, index = _decode(data, index, depth + 1, None)
            items.append(value)
        return items, index + 1
    if lead == b"d":
        result: dict[bytes, Any] = {}
        index += 1
        while data[index : index + 1] != b"e":
            if index >= len(data):
                raise TorrentInvalid("the file ends early")
            key, index = _string(data, index)
            start = index
            value, index = _decode(data, index, depth + 1, None)
            if spans is not None:
                spans[key] = (start, index)
            result[key] = value
        return result, index + 1
    if lead.isdigit():
        return _string(data, index)
    raise TorrentInvalid("not bencoded")


def _name(raw: Any) -> str:
    if not isinstance(raw, bytes):
        raise TorrentInvalid("a name that is not a string")
    name = raw.decode("utf-8", "replace")
    if name in ("", ".", "..") or any(character in name for character in "/\\\x00") or _DRIVE.match(name):
        raise TorrentInvalid("a name that could leave the torrent's folder")
    return name


@dataclass(frozen=True)
class TorrentFile:
    #: Lower-case hex.
    info_hash: str
    name: str
    #: Every file as a relative path with ``/``, each part checked.
    paths: tuple[str, ...]
    size_bytes: int


def _v2_paths(tree: Any, prefix: tuple[str, ...], found: list[tuple[str, int]], depth: int = 0) -> None:
    if depth > MAX_DEPTH or not isinstance(tree, dict):
        raise TorrentInvalid("a broken file tree")
    for key, value in tree.items():
        if key == b"":
            length = value.get(b"length") if isinstance(value, dict) else None
            if not isinstance(length, int) or length < 0:
                raise TorrentInvalid("a file without its length")
            found.append(("/".join(prefix), length))
            continue
        _v2_paths(value, (*prefix, _name(key)), found, depth + 1)
        if len(found) > MAX_FILES:
            raise TorrentInvalid("too many files")


def read_torrent(data: bytes) -> TorrentFile:
    """The info hash, the name and the file paths of a torrent file. Raises ``TorrentInvalid``."""
    if not data.startswith(b"d"):
        raise TorrentInvalid("not a bencoded dictionary")
    spans: dict[bytes, tuple[int, int]] = {}
    root, end = _decode(data, 0, 0, spans)
    if data[end:].strip():
        raise TorrentInvalid("something follows the torrent")
    info = root.get(b"info") if isinstance(root, dict) else None
    if not isinstance(info, dict) or b"info" not in spans:
        raise TorrentInvalid("no info dictionary")
    raw = data[spans[b"info"][0] : spans[b"info"][1]]
    if b"pieces" in info:
        info_hash = hashlib.sha1(raw, usedforsecurity=False).hexdigest()
    elif info.get(b"meta version") == 2:
        info_hash = hashlib.sha256(raw).hexdigest()[:40]
    else:
        raise TorrentInvalid("no pieces")
    name = _name(info.get(b"name.utf-8") or info.get(b"name"))
    found: list[tuple[str, int]] = []
    if isinstance(info.get(b"files"), list):
        for entry in info[b"files"][: MAX_FILES + 1]:
            parts = entry.get(b"path.utf-8") or entry.get(b"path") if isinstance(entry, dict) else None
            length = entry.get(b"length") if isinstance(entry, dict) else None
            if not isinstance(parts, list) or not parts or not isinstance(length, int) or length < 0:
                raise TorrentInvalid("a file without a path or a length")
            found.append((f"{name}/" + "/".join(_name(part) for part in parts), length))
        if len(found) > MAX_FILES:
            raise TorrentInvalid("too many files")
    elif isinstance(info.get(b"file tree"), dict):
        _v2_paths(info[b"file tree"], (name,), found)
    elif isinstance(info.get(b"length"), int) and info[b"length"] >= 0:
        found.append((name, info[b"length"]))
    else:
        raise TorrentInvalid("no files")
    return TorrentFile(
        info_hash=info_hash,
        name=name,
        paths=tuple(path for path, _length in found),
        size_bytes=sum(length for _path, length in found),
    )


def magnet_hash(uri: str) -> str | None:
    """The info hash of a magnet link in lower-case hex, or None without a usable ``xt``."""
    if uri[:8].lower() != "magnet:?":
        return None
    for name, value in parse_qsl(uri[8:], keep_blank_values=False):
        if name.lower().split(".", 1)[0] != "xt":
            continue
        lowered = value.lower()
        if lowered.startswith("urn:btih:"):
            digest = value[len("urn:btih:") :]
            if _HEX_HASH.fullmatch(digest):
                return digest.lower()
            if _BASE32_HASH.fullmatch(digest):
                try:
                    return base64.b32decode(digest.upper()).hex()
                except (binascii.Error, ValueError):
                    return None
        if lowered.startswith("urn:btmh:1220") and re.fullmatch(r"[0-9a-f]{64}", lowered[len("urn:btmh:1220") :]):
            return lowered[len("urn:btmh:1220") :][:40]
    return None
