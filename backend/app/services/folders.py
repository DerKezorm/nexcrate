"""The folders nexcrate sees: the mount points of its container and the folders below them (step 3).

* Mount points come from ``/proc/self/mountinfo``. Left out: the container's root file system, nexcrate's own data
  directory and every mount inside it, system paths such as ``/proc``, ``/etc`` or ``/app``, pseudo file systems, and
  mounts that are no directory (Docker binds single files such as ``/etc/hosts``).
* ⚠️ A path from a request is resolved first, links included, and must then lie inside one of those mount points and
  neither inside nor around the data directory. A link inside a media folder that points at ``/etc`` is not visible.
* Listing leaves hidden folders (a leading dot), links and junctions out.
* Outside Linux there is no ``/proc``: nexcrate sees no folder there.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ..config import get_settings
from ..meldungen import error
from ..models import VersionDefinition
from . import schreibweisen
from .schreibweisen import nfc

logger = logging.getLogger("nexcrate.folders")

MOUNTINFO = Path("/proc/self/mountinfo")
#: Paths of the container itself; a mount there is never a media folder.
SYSTEM_PATHS = (
    "/proc",
    "/sys",
    "/dev",
    "/etc",
    "/usr",
    "/app",
    "/bin",
    "/sbin",
    "/lib",
    "/lib32",
    "/lib64",
    "/libx32",
    "/boot",
    "/run",
    "/var",
    "/root",
    "/tmp",
)
PSEUDO_TYPES = frozenset(
    {
        "proc",
        "sysfs",
        "cgroup",
        "cgroup2",
        "devpts",
        "devtmpfs",
        "mqueue",
        "securityfs",
        "debugfs",
        "tracefs",
        "pstore",
        "bpf",
        "autofs",
        "binfmt_misc",
        "configfs",
        "fusectl",
        "hugetlbfs",
        "rpc_pipefs",
        "nsfs",
        "efivarfs",
    }
)
WRITE_TEST_PREFIX = ".nexcrate-write-test-"
#: At most this many folders in one listing.
LIST_MAX = 5000
_ESCAPE = re.compile(r"\\([0-7]{3})")


class NotVisible(Exception):
    """The path is not a folder nexcrate sees."""


def _unescape(value: str) -> str:
    return _ESCAPE.sub(lambda match: chr(int(match.group(1), 8)), value)


def parse_mountinfo(text: str) -> list[str]:
    """The mount points of a mountinfo text without root, system paths and pseudo file systems, in their order."""
    points: list[str] = []
    for line in text.splitlines():
        fields = line.split(" ")
        if len(fields) < 10 or "-" not in fields[6:]:
            continue
        separator = fields.index("-", 6)
        fs_type = fields[separator + 1] if separator + 1 < len(fields) else ""
        mount_point = _unescape(fields[4])
        if fs_type in PSEUDO_TYPES or not mount_point.startswith("/"):
            continue
        posix = PurePosixPath(mount_point)
        if posix == PurePosixPath("/") or ".." in posix.parts:
            continue
        if any(posix == PurePosixPath(system) or posix.is_relative_to(system) for system in SYSTEM_PATHS):
            continue
        if str(posix) not in points:
            points.append(str(posix))
    return points


def resolved(path: str | Path) -> Path | None:
    """The path with every link resolved, or None when it does not exist or cannot be read."""
    try:
        return Path(os.path.realpath(path, strict=True))
    except (OSError, ValueError):
        return None


def data_dir() -> Path | None:
    return resolved(get_settings().data_dir)


def _touches_data(path: Path, data: Path | None) -> bool:
    return data is not None and (path == data or path.is_relative_to(data) or data.is_relative_to(path))


def mount_points() -> list[Path]:
    """Every visible mount point, resolved, in the order of mountinfo."""
    try:
        raw = MOUNTINFO.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    data = data_dir()
    found: list[Path] = []
    for point in parse_mountinfo(raw):
        path = resolved(point)
        if path is None or not path.is_dir():
            continue
        if data is not None and (path == data or path.is_relative_to(data)):
            continue
        if path not in found:
            found.append(path)
    return found


@dataclass(frozen=True)
class Space:
    free_bytes: int
    total_bytes: int


def space(path: Path) -> Space:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return Space(0, 0)
    return Space(int(usage.free), int(usage.total))


def mounts() -> list[dict[str, Any]]:
    result = []
    for point in mount_points():
        usage = space(point)
        result.append({"path": str(point), "free_bytes": usage.free_bytes, "total_bytes": usage.total_bytes})
    return result


def containing_mount(path: Path) -> Path | None:
    """The innermost visible mount point a resolved path lies in, or None."""
    candidates = [point for point in mount_points() if path == point or path.is_relative_to(point)]
    return max(candidates, key=lambda point: len(point.parts)) if candidates else None


def visible(raw: str | Path | None) -> tuple[Path, Path]:
    """The resolved folder and the mount point it lies in. Raises ``NotVisible``.

    ⚠️ Resolved before the check: a link that leads out of every mount point is not visible.
    """
    value = str(raw) if raw is not None else ""
    if not value.strip() or "\x00" in value or len(value) > 4096:
        raise NotVisible
    candidate = Path(value)
    if not candidate.is_absolute():
        raise NotVisible
    path = resolved(candidate)
    if path is None or not path.is_dir():
        raise NotVisible
    mount = containing_mount(path)
    if mount is None or _touches_data(path, data_dir()):
        raise NotVisible
    return path, mount


def visible_path(raw: str | Path) -> Path | None:
    """A resolved file or folder inside a visible mount point, or None. For the files a download client reports."""
    path = resolved(raw)
    if path is None or containing_mount(path) is None or _touches_data(path, data_dir()):
        return None
    return path


def is_link(path: Path) -> bool:
    try:
        return path.is_symlink() or path.is_junction()
    except OSError:
        return True


def listing(raw: str) -> dict[str, Any]:
    """The subfolders of a visible folder with the space of its file system. Raises ``NotVisible``."""
    path, mount = visible(raw)
    names: list[str] = []
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.name.startswith("."):
                    continue
                try:
                    if entry.is_symlink() or entry.is_junction() or not entry.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                names.append(entry.name)
    except OSError:
        logger.info("A visible folder could not be listed")
    # Form (a) of the name, so "Ärger" sorts under A.
    names.sort(key=lambda name: ((schreibweisen.keys(name) or [""])[0], name))
    usage = space(path)
    return {
        "path": str(path),
        "parent": None if path == mount else str(path.parent),
        "folders": [{"name": nfc(name), "path": str(path / name)} for name in names[:LIST_MAX]],
        "free_bytes": usage.free_bytes,
        "total_bytes": usage.total_bytes,
    }


def is_writable(path: Path) -> bool:
    """Whether a file can be created in the folder: tried with a hidden file that is removed at once."""
    try:
        descriptor, name = tempfile.mkstemp(prefix=WRITE_TEST_PREFIX, dir=path)
    except OSError:
        return False
    os.close(descriptor)
    try:
        os.unlink(name)
    except OSError:
        logger.warning("The write test file in a version folder could not be removed")
    return True


def overlap(first: Path, second: Path) -> bool:
    """The same folder, or one inside the other."""
    return first == second or first.is_relative_to(second) or second.is_relative_to(first)


def _other_version_using(db: OrmSession, definition_id: int, path: Path) -> VersionDefinition | None:
    others = db.scalars(
        select(VersionDefinition).where(VersionDefinition.id != definition_id, VersionDefinition.folder.is_not(None))
    )
    for other in others:
        other_path = resolved(other.folder or "")
        if other_path is not None and overlap(path, other_path):
            return other
    return None


def check_version_folder(db: OrmSession, definition: VersionDefinition, raw: str) -> str:
    """The resolved folder a movie version may use, or the HTTP error of ``PUT /api/versions/{id}/folder``.

    Visible, writable (a hidden test file is created and removed), and neither another version's folder nor inside or
    around it.
    """
    try:
        path, _mount = visible(raw)
    except NotVisible as exc:
        raise error(
            "folder_not_visible", "nexcrate does not see this folder. Only mounted folders can be chosen.", 404
        ) from exc
    if not is_writable(path):
        raise error("folder_not_writable", "nexcrate cannot write into this folder.", 422)
    other = _other_version_using(db, definition.id, path)
    if other is not None:
        raise error(
            "folder_in_use",
            "This folder is already the default folder of another version, or lies inside or around it.",
            409,
            label=other.label,
        )
    return str(path)


def may_propose(db: OrmSession, definition_id: int, raw: str | Path) -> str | None:
    """A folder a version could use, judged without writing: visible, writable by the rights, used by no other version.

    For proposals only; choosing the folder runs ``check_version_folder`` with its write test.
    """
    try:
        path, _mount = visible(raw)
    except NotVisible:
        return None
    if not os.access(path, os.W_OK) or _other_version_using(db, definition_id, path) is not None:
        return None
    return str(path)
