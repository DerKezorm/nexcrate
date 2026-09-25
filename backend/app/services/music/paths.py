"""Where a file of an album lies (decision 6).

``track_files.relative_path`` is relative to the album folder: a bare name for what nexcrate filed, and for files taken
over from Lidarr possibly with a medium folder in front (``CD 01/…``). Every reader joins it here, so a name with a
folder never loses the folder, and nothing outside the album folder is ever reached.
"""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import Version
from ..downloads import files

#: A medium folder below an album folder, as Lidarr and most taggers name a disc: ``CD 01``, ``Disc 2``, ``CD2``,
#: ``Digital Media 01``, ``Vinyl 1``, ``SACD 01``, ``CD-R 02``: the formats MusicBrainz names media by.
MEDIUM_FOLDER = re.compile(
    r"^(?:.*\s)?(?:cd|cd-r|sacd|hdcd|shm-cd|dvd|dvd-audio|dvd-video|blu-ray|disc|disk|medium|digital media|vinyl"
    r"|cassette)\s*\d{1,3}$",
    re.IGNORECASE,
)


def parts_of(relative: str | None) -> list[str]:
    return [part for part in (relative or "").replace("\\", "/").split("/") if part and part not in (".", "..")]


def file_in(folder: Path, relative: str | None) -> Path | None:
    """The file's path inside the album folder, or None for an empty path or one that would leave the folder."""
    parts = parts_of(relative)
    if not parts:
        return None
    path = folder.joinpath(*parts)
    return path if files.strictly_inside(path, folder) else None


def sharing(db: Session, version: Version) -> list[int]:
    """Other own versions with the same album folder. Lidarr can file every album of an artist flat into the artist
    folder; taken over, those albums share one folder. Reading it then cannot tell whose a new file is, and one
    ``release.nex`` cannot describe two albums."""
    if not version.root_folder or not version.relative_path:
        return []
    return list(
        db.scalars(
            select(Version.id).where(
                Version.id != version.id,
                Version.source_id.is_(None),
                Version.root_folder == version.root_folder,
                Version.relative_path == version.relative_path,
            )
        )
    )


def display(relative: str | None) -> str:
    """The path as written in a companion file and on the album page: forward slashes, medium folder kept."""
    return "/".join(parts_of(relative))
