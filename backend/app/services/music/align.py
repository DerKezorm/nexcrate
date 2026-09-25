"""One album, one release in names and tags (the owner's answer of 19.09.2026, the design notes, "Zu bestätigen").

Completing an album can leave files of two releases side by side: ``1-01 ….flac`` of a double CD next to ``29 ….mp3``
of the anniversary edition, each tagged as its own release. When the files of an album come from more than one
release, every file whose song (``same_song``) is on the target release takes the target track's name and tags. A file
of a song the target does not have keeps its own. An album whose files all come from one release is left as filed, so
a download of another edition than the target still names its tracks as they are.

Renaming stays inside the album folder and never overwrites; tags follow the link rule of ``tags.write`` (a file with
a second link is never written, its state says so). The owner's switch for tags applies.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ...models import Release, ReleaseMedium, ReleaseTrack, Title, TrackFile, Version, utcnow
from .. import naming_music
from ..downloads import files
from . import paths, same_song, store, tag_writing

logger = logging.getLogger("nexcrate.music")


def align(
    db: OrmSession,
    version: Version,
    folder: Path,
    naming: naming_music.MusicNaming,
    album: naming_music.AlbumFacts,
    *,
    write_tags: bool,
) -> int:
    """Rename and retag the version's files to its target release when they come from more than one release. Stages
    the rows; the caller commits. Returns how many files changed."""
    if version.target_release_id is None:
        return 0
    target = db.get(Release, version.target_release_id)
    title = db.get(Title, version.title_id)
    if target is None or title is None:
        return 0
    rows = list(db.scalars(select(TrackFile).where(TrackFile.version_id == version.id)))
    single = {row.id: row.track_id for row in rows if row.track_id is not None and not (row.track_ids or [])[1:]}
    if not single:
        return 0
    held = {
        track.id: track for track in db.scalars(select(ReleaseTrack).where(ReleaseTrack.id.in_(set(single.values()))))
    }
    releases = {track.release_id for track in held.values()}
    if len(releases) < 2:
        return 0
    by_release: dict[int, list[ReleaseTrack]] = {}
    for track in db.scalars(select(ReleaseTrack).where(ReleaseTrack.release_id.in_(releases | {target.id}))):
        by_release.setdefault(track.release_id, []).append(track)
    keys = same_song.keys_by_track(by_release.values())
    target_tracks = by_release.get(target.id, [])
    media = {row.id: row for row in db.scalars(select(ReleaseMedium).where(ReleaseMedium.release_id == target.id))}
    several = (target.media_count or 1) > 1
    wanted_album = tag_writing.album_tags(db, target, title) if write_tags else None
    changed = 0
    for row in rows:
        track_id = single.get(row.id)
        track = held.get(track_id) if track_id is not None else None
        if track is None:
            continue
        mine = keys.get(track.id, frozenset())
        matches = [item for item in target_tracks if keys.get(item.id, frozenset()) & mine]
        if len(matches) != 1:
            continue
        goal = matches[0]
        medium = media.get(goal.medium_id)
        name = paths.display(row.relative_path)
        found = paths.file_in(folder, row.relative_path)
        if found is None or files.is_link(found) or not found.is_file():
            continue
        path = found
        facts = naming_music.TrackFacts(
            medium=medium.position if medium is not None else 1,
            position=goal.position,
            title=goal.name,
            artist_name=tag_writing.credit_text(goal.artist_credit or title.artist_credit),
            medium_format=medium.format if medium is not None else None,
            quality=row.quality,
            original_filename=path.stem,
        )
        wanted_name = naming_music.file_name(naming, album, facts, path.suffix, several_media=several)
        renamed = False
        if wanted_name != name:
            destination = folder / wanted_name
            if destination.parent != folder and folder in destination.parents:
                # The name carries the folder of its medium (``CD 02/03 Title.flac``).
                destination.parent.mkdir(parents=True, exist_ok=True)
            if os.path.lexists(destination):
                logger.info("Album %d: file %d keeps its name, another file has the target's", title.id, row.id)
            else:
                os.replace(path, destination)
                path, renamed = destination, True
                row.relative_path = wanted_name
        state = row.tags_state
        if wanted_album is not None:
            state = tag_writing.state_of(path, tag_writing.wanted(wanted_album, goal), None, enabled=True)
        if renamed or goal.id != row.track_id or state != row.tags_state:
            row.track_id = goal.id
            row.track_ids = None
            if wanted_album is not None:
                row.tags_state = state
                row.tags_written_at = utcnow() if state == "written" else row.tags_written_at
            changed += 1
    if changed:
        version.actual_release_id = target.id
        db.flush()
        store._count_tracks(db, version)
        logger.info("Album %d: %d files follow the target release in names and tags", title.id, changed)
    return changed
