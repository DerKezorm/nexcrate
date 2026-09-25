"""The child process that lists or unpacks one archive (T3, "Extracting").

``unpacking`` starts it once per listing and once per extraction: stdin closed, no terminal, an argument list, a small
environment, and a timeout after which the parent kills it with every tool it started. It writes one line of JSON to
stdout and nothing else.

* ``list <kind> <first volume>`` gives ``{"ok": true, "encrypted": bool, "entries": [[name, size, kind], ...],
  "more": bool}``, ``kind`` being ``file``, ``dir``, ``link`` or ``other``; ``more`` when the archive holds more than
  ``MAX_LISTED`` entries.
* ``extract <kind> <first volume> <folder>`` gives ``{"ok": true}``.
* A failure gives ``{"ok": false, "reason": ..., "error": <exception class or null>}``, ``reason`` being ``encrypted``,
  ``incomplete``, ``unsupported``, ``unsafe`` or ``broken``.

Kinds, as measured on 14.09.2026 (session fact file ``unpacking.md``):

* ``rar``: rarfile with the tool ``unrar`` only, in the image the free ``unrar-free``. rarfile would otherwise fall back
  to bsdtar or 7z, which unpack RAR wrongly or not at all. ``needs_password()`` also catches encrypted headers, where
  extracting silently writes nothing. An old RAR4 archive packed as one solid block fails with unrar-free:
  ``unsupported`` (decision 19).
* ``7z``: 7z archives and split ``.7z.001`` and ``.zip.001`` archives, always with a dummy password: without ``-p`` 7z
  waits for one on a terminal.
* ``zip``: zipfile; flag bit 1 or strong encryption is encrypted.
* ``tar``: tarfile with the filter ``data``.

⚠️ Standalone: only the standard library and rarfile, so the parent runs it by its path with ``python -I``. Nothing goes
to stderr and no name or path into the answer but the listed entry names, which the parent checks and never logs.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tarfile
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

#: At most this many entries are listed; the parent refuses far fewer.
MAX_LISTED = 5000
SEVEN_ZIP = "7z"
#: Any password: with one 7z fails at once on an encrypted archive instead of asking on a terminal.
SEVEN_ZIP_DUMMY_PASSWORD = "-pnone"
#: The first bytes of a RAR4 archive; RAR5 has a 1 where the 0 is.
RAR4_MARKER = b"Rar!\x1a\x07\x00"
ZIP_ENCRYPTED = 0x1
ZIP_STRONG_ENCRYPTION = 0x40
ZIP_UNIX = 3
_UNIX_MODE = re.compile(r"(?:^|\s)([-dlcbps])[-rwxsStT]{9}(?:\s|$)")
_7Z_FIELD = re.compile(r"^(\S[^=]*?) =(?: (.*))?$")
_7Z_ENCRYPTED = ("wrong password", "encrypted archive", "encrypted file")


def failed(reason: str, error: BaseException | None = None) -> dict[str, Any]:
    return {"ok": False, "reason": reason, "error": type(error).__name__ if error is not None else None}


def listed(entries: list[list[Any]], *, encrypted: bool, more: bool) -> dict[str, Any]:
    return {"ok": True, "encrypted": encrypted, "entries": entries, "more": more}


# --- RAR ----------------------------------------------------------------------------------------------------- #


def _next_volume_missing(archive: Any) -> bool:
    """rarfile opens an incomplete set without complaint and only notes that the next volume could not be opened."""
    return (archive.strerror() or "").startswith("Cannot open next volume")


def _is_rar4(path: str) -> bool:
    try:
        with open(path, "rb") as handle:
            return handle.read(len(RAR4_MARKER)) == RAR4_MARKER
    except OSError:
        return False


def _only_unrar(rarfile: Any) -> None:
    """rarfile's tool is ``unrar`` or none: a missing ``unrar`` fails compressed data instead of trying bsdtar or 7z."""
    try:
        rarfile.tool_setup(unrar=True, unar=False, bsdtar=False, sevenzip=False, sevenzip2=False)
    except rarfile.RarCannotExec:

        def no_tool(*_args: object, **_kwargs: object) -> Any:
            raise rarfile.RarCannotExec("unrar is missing")

        rarfile.tool_setup = no_tool


def rar_list(path: str) -> dict[str, Any]:
    import rarfile

    try:
        archive = rarfile.RarFile(path)
    except rarfile.NeedFirstVolume as exc:
        return failed("incomplete", exc)
    except (rarfile.Error, OSError, EOFError, ValueError) as exc:
        return failed("broken", exc)
    if archive.needs_password():
        return listed([], encrypted=True, more=False)
    if _next_volume_missing(archive):
        return failed("incomplete")
    entries: list[list[Any]] = []
    for info in archive.infolist():
        if len(entries) >= MAX_LISTED:
            return listed(entries, encrypted=False, more=True)
        # A RAR5 hard link or file copy has ``file_redir`` but counts as a file in rarfile.
        if info.is_symlink() or info.file_redir is not None:
            kind = "link"
        elif info.is_dir():
            kind = "dir"
        elif info.is_file():
            kind = "file"
        else:
            kind = "other"
        entries.append([str(info.filename), int(info.file_size or 0), kind])
    return listed(entries, encrypted=False, more=False)


def rar_extract(path: str, folder: str) -> dict[str, Any]:
    import rarfile

    _only_unrar(rarfile)
    solid_rar4 = False
    try:
        with rarfile.RarFile(path) as archive:
            if archive.needs_password():
                return failed("encrypted")
            if _next_volume_missing(archive):
                return failed("incomplete")
            solid_rar4 = _is_rar4(path) and archive.is_solid()
            archive.extractall(folder)
    except rarfile.PasswordRequired as exc:
        return failed("encrypted", exc)
    except (rarfile.NeedFirstVolume, FileNotFoundError) as exc:
        return failed("incomplete", exc)
    except rarfile.BadSymLinkError as exc:
        return failed("unsafe", exc)
    except rarfile.RarCannotExec as exc:
        return failed("unsupported", exc)
    except (rarfile.Error, OSError, EOFError, ValueError) as exc:
        return failed("unsupported" if solid_rar4 else "broken", exc)
    return {"ok": True}


# --- 7z ------------------------------------------------------------------------------------------------------ #


@dataclass
class SevenZipListing:
    entries: list[list[Any]] = field(default_factory=list)
    encrypted: bool = False
    more: bool = False
    #: Lines that are no ``Key = Value`` field: 7z's messages, errors among them.
    messages: list[str] = field(default_factory=list)


def _7z_kind(block: dict[str, str]) -> str:
    if block.get("Symbolic Link") or block.get("Hard Link"):
        return "link"
    if block.get("Folder") == "+":
        return "dir"
    attributes = block.get("Attributes", "")
    mode = _UNIX_MODE.search(attributes)
    if mode is not None:
        return {"-": "file", "d": "dir", "l": "link"}.get(mode.group(1), "other")
    return "dir" if "D" in attributes.split(" ", 1)[0] else "file"


def _size(value: str | None) -> int:
    try:
        return max(0, int(value or 0))
    except ValueError:
        return 0


def parse_7z_listing(lines: Iterable[str]) -> SevenZipListing:
    """The entries of ``7z l -slt``: blocks of ``Key = Value`` lines after the line ``----------``.

    Blocks before that line describe the archive (and for a split archive the archive inside). Stops after
    ``MAX_LISTED`` entries with ``more``.
    """
    result = SevenZipListing()
    started = False
    block: dict[str, str] = {}

    def flush() -> None:
        if started and "Path" in block:
            result.encrypted = result.encrypted or block.get("Encrypted") == "+"
            result.entries.append([block["Path"], _size(block.get("Size")), _7z_kind(block)])
        block.clear()

    for raw in lines:
        line = raw.rstrip("\r\n")
        if not line.strip():
            flush()
            if len(result.entries) > MAX_LISTED:
                break
            continue
        if line.strip() == "----------":
            block.clear()
            started = True
            continue
        found = _7Z_FIELD.match(line)
        if found is None:
            result.messages.append(line)
        else:
            block[found.group(1)] = found.group(2) or ""
    flush()
    if len(result.entries) > MAX_LISTED:
        del result.entries[MAX_LISTED:]
        result.more = True
    return result


def classify_7z(messages: Iterable[str], *, split: bool) -> str:
    """Why 7z failed, from its messages. ⚠️ Only messages: a listing's ``Encrypted = -`` field is no message."""
    text = " ".join(messages).casefold()
    if any(phrase in text for phrase in _7Z_ENCRYPTED):
        return "encrypted"
    if "missing volume" in text:
        return "incomplete"
    if "unexpected end of archive" in text:
        return "incomplete" if split else "broken"
    if "unsupported method" in text:
        return "unsupported"
    if "dangerous link" in text:
        return "unsafe"
    return "broken"


def _is_split(path: str) -> bool:
    return re.search(r"\.\d{3}$", path) is not None


def seven_zip_list(path: str) -> dict[str, Any]:
    try:
        process = subprocess.Popen(
            [SEVEN_ZIP, "l", "-slt", "-bd", "-y", "-sccUTF-8", SEVEN_ZIP_DUMMY_PASSWORD, path],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        return failed("unsupported", exc)
    if process.stdout is None:
        process.kill()
        return failed("broken")
    parsed = parse_7z_listing(process.stdout)
    if parsed.more:
        process.kill()
    code = process.wait()
    if parsed.more:
        return listed(parsed.entries, encrypted=parsed.encrypted, more=True)
    if code != 0:
        return failed(classify_7z(parsed.messages, split=_is_split(path)))
    return listed(parsed.entries, encrypted=parsed.encrypted, more=False)


def seven_zip_extract(path: str, folder: str) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [SEVEN_ZIP, "x", "-y", "-bd", "-sccUTF-8", SEVEN_ZIP_DUMMY_PASSWORD, f"-o{folder}", path],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        return failed("unsupported", exc)
    if completed.returncode != 0:
        return failed(classify_7z(completed.stdout.splitlines(), split=_is_split(path)))
    return {"ok": True}


# --- zip and tar ---------------------------------------------------------------------------------------------- #


def _zip_encrypted(info: zipfile.ZipInfo) -> bool:
    return bool(info.flag_bits & (ZIP_ENCRYPTED | ZIP_STRONG_ENCRYPTION))


def _zip_kind(info: zipfile.ZipInfo) -> str:
    mode = (info.external_attr >> 16) & 0o170000 if info.create_system == ZIP_UNIX else 0
    if mode == 0o120000:
        return "link"
    if info.is_dir():
        return "dir"
    return "file" if mode in (0, 0o100000) else "other"


def zip_list(path: str) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
    except (zipfile.BadZipFile, OSError, EOFError, ValueError) as exc:
        return failed("broken", exc)
    entries: list[list[Any]] = []
    encrypted = False
    for info in infos:
        if len(entries) >= MAX_LISTED:
            return listed(entries, encrypted=encrypted, more=True)
        encrypted = encrypted or _zip_encrypted(info)
        entries.append([info.filename, int(info.file_size), _zip_kind(info)])
    return listed(entries, encrypted=encrypted, more=False)


def zip_extract(path: str, folder: str) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as archive:
            if any(_zip_encrypted(info) for info in archive.infolist()):
                return failed("encrypted")
            archive.extractall(folder)
    except RuntimeError as exc:
        # zipfile: "File ... is encrypted, password required for extraction".
        return failed("encrypted", exc)
    except NotImplementedError as exc:
        return failed("unsupported", exc)
    except (zipfile.BadZipFile, OSError, EOFError, ValueError) as exc:
        return failed("broken", exc)
    return {"ok": True}


def _tar_kind(member: tarfile.TarInfo) -> str:
    if member.issym() or member.islnk():
        return "link"
    if member.isdir():
        return "dir"
    return "file" if member.isfile() else "other"


def tar_list(path: str) -> dict[str, Any]:
    entries: list[list[Any]] = []
    try:
        with tarfile.open(path, "r:*") as archive:
            for member in archive:
                if len(entries) >= MAX_LISTED:
                    return listed(entries, encrypted=False, more=True)
                entries.append([member.name, int(member.size), _tar_kind(member)])
    except (tarfile.TarError, OSError, EOFError, ValueError) as exc:
        return failed("broken", exc)
    return listed(entries, encrypted=False, more=False)


def tar_extract(path: str, folder: str) -> dict[str, Any]:
    try:
        with tarfile.open(path, "r:*") as archive:
            archive.extractall(folder, filter="data")
    except tarfile.FilterError as exc:
        return failed("unsafe", exc)
    except (tarfile.TarError, OSError, EOFError, ValueError) as exc:
        return failed("broken", exc)
    return {"ok": True}


# --- The process ---------------------------------------------------------------------------------------------- #

LISTERS = {"rar": rar_list, "7z": seven_zip_list, "zip": zip_list, "tar": tar_list}
EXTRACTORS = {"rar": rar_extract, "7z": seven_zip_extract, "zip": zip_extract, "tar": tar_extract}


def run(arguments: list[str]) -> dict[str, Any]:
    if len(arguments) == 3 and arguments[0] == "list" and arguments[1] in LISTERS:
        return LISTERS[arguments[1]](arguments[2])
    if len(arguments) == 4 and arguments[0] == "extract" and arguments[1] in EXTRACTORS:
        return EXTRACTORS[arguments[1]](arguments[2], arguments[3])
    return failed("broken")


def main() -> int:
    try:
        result = run(sys.argv[1:])
    except Exception as exc:  # noqa: BLE001 - whatever a parser or tool raises, the archive counts as broken
        result = failed("broken", exc)
    sys.stdout.write(json.dumps(result) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
