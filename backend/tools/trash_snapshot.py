"""Build the bundled TRaSH Guides snapshots, ``backend/app/data/trash-radarr.json`` and ``trash-sonarr.json``.

Run from ``backend/``::

    .venv/Scripts/python tools/trash_snapshot.py
    .venv/Scripts/python tools/trash_snapshot.py --commit <full commit id>
    .venv/Scripts/python tools/trash_snapshot.py --tarball <downloaded tarball>

⚠️ **The pin is the Radarr snapshot itself.** Without ``--commit`` the tool fetches the commit
``trash-radarr.json`` names, so a second run gives the same bytes. ``--commit`` moves the pin to another
commit of TRaSH's repository; both snapshots then name that one. The commit id is written in one place only.

⚠️ **Both files come from one tarball, or neither is written.** Movies and series always carry the same
commit: both snapshots are built first, and only then are both files written.

⚠️ **Never edited by hand.** What nexcrate makes of the data belongs in ``services/profiles/``; each
snapshot stays an unchanged copy of TRaSH's ``docs/json/radarr`` or ``docs/json/sonarr``, so it is always
clear which state is inside.

One request to codeload.github.com, public data. The tarball stays in memory; only the needed files are
read from it (``services/trash_data.py``).
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import httpx

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services import trash_data  # noqa: E402 - needs the path above

DATA_DIR = BACKEND_DIR / "app" / "data"
#: The file per kind of title. The movie file holds the pin.
TARGETS = {"movie": DATA_DIR / "trash-radarr.json", "series": DATA_DIR / "trash-sonarr.json"}
TARBALL_URL = "https://codeload.github.com/TRaSH-Guides/Guides/tar.gz/{commit}"
#: The tarball is about 25 MB; anything far larger is not what we expect.
TARBALL_MAX_BYTES = 150 * 1024 * 1024


def _pinned_commit() -> str:
    pin = TARGETS["movie"]
    if not pin.is_file():
        raise SystemExit(f"{pin.name} does not exist yet: name the commit with --commit.")
    return str(trash_data.read_snapshot(pin)["commit"])


def _download(commit: str) -> bytes:
    url = TARBALL_URL.format(commit=commit)
    chunks: list[bytes] = []
    size = 0
    with httpx.stream("GET", url, timeout=httpx.Timeout(180.0, connect=15.0), follow_redirects=True) as response:
        if response.status_code != 200:
            raise SystemExit(f"GitHub answered HTTP {response.status_code} for {url}")
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > TARBALL_MAX_BYTES:
                raise SystemExit("The tarball is larger than expected; stopped.")
            chunks.append(chunk)
    return b"".join(chunks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--commit", help="full commit id of TRaSH-Guides/Guides; default: the commit of the snapshot")
    parser.add_argument("--tarball", type=Path, help="read this tarball instead of downloading it")
    args = parser.parse_args(argv)

    commit = (args.commit or _pinned_commit()).strip().lower()
    if not trash_data.COMMIT.match(commit):
        raise SystemExit("--commit needs the full 40 character commit id.")
    raw = args.tarball.read_bytes() if args.tarball else _download(commit)
    # Build every snapshot before writing any, so a tarball that fails for one kind changes nothing.
    snapshots = {kind: trash_data.build_snapshot(raw, kind=kind, expected_commit=commit) for kind in TARGETS}
    for kind, target in TARGETS.items():
        snapshot = snapshots[kind]
        content = trash_data.snapshot_bytes(snapshot)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Bytes, not text: write_text would write CRLF on Windows.
        target.write_bytes(content)
        print(
            f"{target.name}: commit {snapshot['commit'][:12]} of {snapshot['date']}, "
            f"{len(snapshot['custom_formats'])} custom formats, {len(snapshot['quality_profiles'])} profiles, "
            f"{len(snapshot['cf_groups'])} groups, {len(snapshot['quality_sizes'])} size sets, "
            f"{len(content)} bytes, sha256 {hashlib.sha256(content).hexdigest()}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
