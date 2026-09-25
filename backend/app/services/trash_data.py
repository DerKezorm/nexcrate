"""TRaSH Guides data: a snapshot built from GitHub's tarball, written and read back.

A pure module: no database, no settings, no network. ``tools/trash_snapshot.py`` builds the bundled
snapshot with it, and the update in ``services/trash.py`` builds a fetched state with it, so both have
exactly the same shape.

A snapshot is one app folder of TRaSH's data (MIT licence) at one commit: ``docs/json/radarr`` for movies,
``docs/json/sonarr`` for series. Both have the same shape and are built from the same tarball:

* ``custom_formats``, ``quality_profiles``, ``cf_groups``, ``quality_sizes``: every file as TRaSH wrote
  it, keyed by its file name without ``.json``. Profiles and groups point at formats by ``trash_id``; the
  loader indexes them, the file does not repeat them.
* ``commit``: the full commit id, from the ``comment`` in the tarball's pax header (git archive writes it).
* ``date``: the commit time, which git archive writes as the time of every member, in UTC.
* ``license`` and ``copyright``: TRaSH's licence text and its copyright line. MIT asks to keep the notice
  with a copy.

⚠️ The output is deterministic: sorted keys, indent 1, ``\\n`` line ends, UTF-8, written as bytes. On
Windows ``Path.write_text`` turns every ``\\n`` into CRLF, and a second run would differ from a first run
made elsewhere.

⚠️ Only the needed members are read. Everything else in the tarball (about 25 MB, mostly images) is
skipped without being extracted, and nothing is ever written to disk from it.
"""

from __future__ import annotations

import io
import json
import re
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: The version of the snapshot file itself.
FORMAT = 1
SOURCE_URL = "https://github.com/TRaSH-Guides/Guides"
#: The folder of TRaSH's data per kind of title.
DATA_PATHS = {"movie": ("docs", "json", "radarr"), "series": ("docs", "json", "sonarr")}
#: The movie folder, which the daily check still asks about.
DATA_PATH = DATA_PATHS["movie"]
#: TRaSH's folder name to the key in the snapshot.
SECTIONS = {
    "cf": "custom_formats",
    "quality-profiles": "quality_profiles",
    "cf-groups": "cf_groups",
    "quality-size": "quality_sizes",
}
#: One JSON file of TRaSH is a few KB; the largest custom format is far below this.
MEMBER_MAX_BYTES = 2 * 1024 * 1024
LICENSE_MAX_BYTES = 64 * 1024
COMMIT = re.compile(r"^[0-9a-f]{40}$")
_FILE_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*\.json$")


class SnapshotInvalid(ValueError):
    """The tarball or file is not a usable TRaSH state. The message is safe for a log line."""


def _date(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _copyright(license_text: str) -> str:
    for line in license_text.splitlines():
        if line.strip().lower().startswith("copyright"):
            return line.strip()
    raise SnapshotInvalid("the licence has no copyright line")


def _json(raw: bytes, member: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SnapshotInvalid(f"{member} is not JSON") from exc


def build_snapshot(tarball: bytes, *, kind: str = "movie", expected_commit: str | None = None) -> dict[str, Any]:
    """The snapshot of one kind inside a tarball of TRaSH's repository. Raises ``SnapshotInvalid``.

    Only the folder of ``kind`` is read; an unknown kind is a programming error (``KeyError``).
    """
    data_path = DATA_PATHS[kind]
    sections: dict[str, dict[str, Any]] = {key: {} for key in SECTIONS.values()}
    license_text: str | None = None
    moment: float | None = None
    try:
        with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as archive:
            for member in archive:
                parts = member.name.split("/")
                if len(parts) < 2 or any(part in ("", ".", "..") for part in parts):
                    continue
                inner = parts[1:]
                if inner == ["LICENSE"] and member.isfile():
                    if member.size > LICENSE_MAX_BYTES:
                        raise SnapshotInvalid("the licence file is too large")
                    handle = archive.extractfile(member)
                    license_text = handle.read().decode("utf-8") if handle is not None else None
                    moment = float(member.mtime)
                    continue
                if (
                    len(inner) != 5
                    or tuple(inner[:3]) != data_path
                    or inner[3] not in SECTIONS
                    or not member.isfile()
                    or not _FILE_NAME.match(inner[4])
                ):
                    continue
                if member.size > MEMBER_MAX_BYTES:
                    raise SnapshotInvalid(f"{member.name} is too large")
                handle = archive.extractfile(member)
                if handle is None:
                    continue
                sections[SECTIONS[inner[3]]][inner[4][: -len(".json")]] = _json(handle.read(), member.name)
                if moment is None:
                    moment = float(member.mtime)
            commit = str(archive.pax_headers.get("comment", "")).strip().lower()
    except (tarfile.TarError, OSError, EOFError, UnicodeDecodeError) as exc:
        raise SnapshotInvalid("not a readable gzip tarball") from exc

    if not COMMIT.match(commit):
        raise SnapshotInvalid("the tarball names no commit")
    if expected_commit is not None and commit != expected_commit.lower():
        raise SnapshotInvalid("the tarball is of another commit")
    if license_text is None or moment is None:
        raise SnapshotInvalid("the tarball has no licence")
    snapshot: dict[str, Any] = {
        "format": FORMAT,
        "source": SOURCE_URL,
        "path": "/".join(data_path),
        "commit": commit,
        "date": _date(moment),
        "license": license_text.replace("\r\n", "\n"),
        "copyright": _copyright(license_text),
        **sections,
    }
    validate(snapshot)
    return snapshot


def validate(snapshot: Any) -> None:
    """The checks every snapshot passes, bundled or fetched. Raises ``SnapshotInvalid``."""
    if not isinstance(snapshot, dict) or snapshot.get("format") != FORMAT:
        raise SnapshotInvalid("not a snapshot of this format")
    for key in ("commit", "date", "license", "copyright"):
        if not isinstance(snapshot.get(key), str) or not snapshot[key]:
            raise SnapshotInvalid(f"the snapshot has no {key}")
    if not COMMIT.match(snapshot["commit"]):
        raise SnapshotInvalid("the snapshot names no commit")
    for key in SECTIONS.values():
        section = snapshot.get(key)
        if not isinstance(section, dict) or not section:
            raise SnapshotInvalid(f"the snapshot has no {key}")
        if not all(isinstance(value, dict) for value in section.values()):
            raise SnapshotInvalid(f"{key} holds something that is not an object")
    for name, custom_format in snapshot["custom_formats"].items():
        if (
            not isinstance(custom_format.get("trash_id"), str)
            or not isinstance(custom_format.get("name"), str)
            or not isinstance(custom_format.get("specifications"), list)
        ):
            raise SnapshotInvalid(f"custom format {name} is incomplete")


def snapshot_bytes(snapshot: dict[str, Any]) -> bytes:
    """The file content: sorted keys, indent 1, one ``\\n`` at the end, UTF-8."""
    return (json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode("utf-8")


def read_snapshot(path: Path) -> dict[str, Any]:
    """A snapshot file. Raises ``SnapshotInvalid`` for anything that is not one, ``OSError`` when unreadable."""
    snapshot = _json(path.read_bytes(), path.name)
    validate(snapshot)
    return snapshot


class TrashSnapshot:
    """A loaded snapshot with the lookups the profile builder needs. Treat it as read only."""

    def __init__(self, data: dict[str, Any], origin: str = "bundled") -> None:
        validate(data)
        self.data = data
        #: ``bundled`` or ``fetched``.
        self.origin = origin
        self.commit: str = data["commit"]
        self.date: str = data["date"]
        self.license: str = data["license"]
        self.copyright: str = data["copyright"]
        self.formats_by_id: dict[str, dict[str, Any]] = {
            custom_format["trash_id"]: custom_format for custom_format in data["custom_formats"].values()
        }
        self.format_ids_by_file: dict[str, str] = {
            name: custom_format["trash_id"] for name, custom_format in data["custom_formats"].items()
        }

    def profile(self, name: str) -> dict[str, Any] | None:
        return self.data["quality_profiles"].get(name)

    def group(self, name: str) -> dict[str, Any] | None:
        return self.data["cf_groups"].get(name)

    def sizes(self, name: str) -> dict[str, Any] | None:
        return self.data["quality_sizes"].get(name)
