"""Renaming an artist's albums (decisions 23 to 28, answers G2 and G3).

The unit is the artist: its folder is renamed only when all its albums are in the run (``album_id`` None) and no other
artist's album lies in it. Each album gets its own folder named by the music naming; albums that would share a name
keep it in the order album, EP, single, broadcast, other, and the others get their type, then their disambiguation,
then the start of their id as an addition (``naming_music.free_album_folder``). A folder nexcrate did not make keeps
its name, and an album folder avoids it the same way.

An album that has its folder to itself moves it as a whole: every file of a track takes the name filing builds (flat,
``1-01 …`` for several media, so medium folders dissolve), unclear files keep their place in the folder, anything else
moves along. An album that shares its folder with other albums (Lidarr filed them together, or straight into the
artist folder) takes its own files out: its tracks, their ``.lrc``, and images from a folder whose audio is all its
own. The rest stays. With the artist folder renamed, whatever else lies in it follows with its place kept.

Tags are never written here (the owner's answer in M6).
"""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ...models import Artist, Release, ReleaseMedium, ReleaseTrack, Title, TrackFile, Version
from .. import folders, naming_music
from ..downloads import files
from ..music import paths, tag_writing
from .plan import AUDIO_ENDINGS, IMAGE_ENDINGS, Move, TitlePlan, files_below, finish, holds_media, parts_of, posix, put


def album_facts(artist: Artist, title: Title) -> naming_music.AlbumFacts:
    year = None
    if title.first_release_date and title.first_release_date[:4].isdigit():
        year = int(title.first_release_date[:4])
    return naming_music.AlbumFacts(
        artist_name=artist.name,
        album_title=title.title or "",
        year=year or title.year,
        artist_mbid=artist.mbid,
        album_mbid=title.mbid,
        album_type=title.primary_type,
        disambiguation=title.release_group_disambiguation,
    )


def _own_albums(db: OrmSession, artist_id: int) -> list[tuple[Title, Version]]:
    rows = db.execute(
        select(Title, Version)
        .join(Version, Version.title_id == Title.id)
        .where(
            Title.kind == "album",
            Title.artist_id == artist_id,
            Version.source_id.is_(None),
            Version.has_file.is_(True),
            Version.relative_path.is_not(None),
        )
        .order_by(Title.sort_key, Title.id)
    )
    return [(title, version) for title, version in rows.tuples()]


def _rank(title: Title) -> tuple[int, int, int]:
    kind = (title.primary_type or "").casefold()
    year = (
        int(title.first_release_date[:4])
        if title.first_release_date and title.first_release_date[:4].isdigit()
        else 9999
    )
    return naming_music.TYPE_RANK.get(kind, 5), year, title.id


def album_names(
    naming: naming_music.MusicNaming,
    albums: list[tuple[Title, naming_music.AlbumFacts]],
    taken: set[str],
) -> dict[int, str | None]:
    """The folder name of each album: the plain name for the first of a name in rank order, an addition for the others
    and for a name ``taken`` already holds. None when no addition helps."""
    names: dict[int, str | None] = {}
    used = set(taken)
    for title, facts in sorted(albums, key=lambda item: _rank(item[0])):
        name = naming_music.free_album_folder(naming, facts, used, title.id)
        names[title.id] = name
        if name is not None:
            used.add(name.casefold())
    return names


def plan_artist(db: OrmSession, artist_id: int, album_id: int | None = None) -> TitlePlan | None:
    artist = db.get(Artist, artist_id)
    if artist is None:
        return None
    albums = _own_albums(db, artist_id)
    if album_id is not None:
        chosen = [(title, version) for title, version in albums if title.id == album_id]
    else:
        chosen = albums
    if not chosen:
        return None
    first = chosen[0][0]
    plan = TitlePlan(
        kind="music",
        title_id=first.id,
        name=artist.name if album_id is None else (first.title or ""),
        title_ids=[title.id for title, _version in chosen],
        artist_id=artist.id,
    )
    naming = naming_music.load(db)
    located: dict[int, tuple[Path, Path]] = {}
    for title, version in albums:
        try:
            root, _mount = folders.visible(version.root_folder)
        except folders.NotVisible:
            if any(title.id == item.id for item, _ in chosen):
                return plan.skipped("folder_missing")
            continue
        album_dir = root.joinpath(*parts_of(version.relative_path))
        if not files.inside(album_dir, root) or files.is_link(album_dir):
            return plan.skipped("link")
        located[version.id] = (root, album_dir)
    if not located:
        return plan.skipped("folder_missing")
    roots = {root for root, _dir in located.values()}
    if len(roots) != 1:
        return plan.skipped("several_roots")
    root = next(iter(roots))
    plan.roots.append(root)

    # The artist folder: the stored one, else the first part most album folders share.
    firsts = Counter(
        parts_of(version.relative_path)[0] for _title, version in albums if parts_of(version.relative_path)
    )
    current_artist = artist.folder or (firsts.most_common(1)[0][0] if firsts else None)
    if not current_artist:
        return plan.skipped("folder_missing")
    artist_dir = root / current_artist
    whole = album_id is None
    exclusive = not _others_in_artist_folder(db, artist_id, root, current_artist)
    new_artist = current_artist
    if whole and exclusive:
        new_artist = naming_music.artist_folder_name(naming, album_facts(artist, first))
    elif whole:
        plan.note("artist_folder_shared")
    new_artist_dir = root / new_artist
    if new_artist != current_artist:
        plan.folders.append((current_artist, new_artist))
    put(plan.before, "artists", artist.id, folder=artist.folder)
    put(plan.after, "artists", artist.id, folder=new_artist if new_artist != current_artist else artist.folder)

    # The folder names: every album of the artist competes; albums not in the run keep theirs, as do foreign folders.
    current_dirs = {located[version.id][1] for _title, version in albums if version.id in located}
    taken: set[str] = set()
    if new_artist_dir.is_dir():
        for entry in new_artist_dir.iterdir():
            # A folder with music in it keeps its name; one with nothing but rests of an older rip takes the album in.
            if entry.is_dir() and entry not in current_dirs and holds_media(entry):
                taken.add(entry.name.casefold())
    chosen_ids = {title.id for title, _version in chosen}
    for title, version in albums:
        if title.id not in chosen_ids and version.id in located:
            album_dir = located[version.id][1]
            if album_dir.parent == artist_dir:
                taken.add(album_dir.name.casefold())
    names = album_names(naming, [(title, album_facts(artist, title)) for title, _version in chosen], taken)

    moved: set[Path] = set()
    # Images of a shared folder that stay, counted once however many albums leave the folder.
    stays: set[Path] = set()
    for title, version in chosen:
        if version.id not in located:
            continue
        album_dir = located[version.id][1]
        name = names.get(title.id)
        if name is None:
            return plan.skipped("target_taken", name=album_facts(artist, title).album_title)
        new_album_dir = new_artist_dir / name
        # Straight in the artist folder or the root, or together with other albums: only its own files come out.
        shared = bool(paths.sharing(db, version)) or album_dir in (artist_dir, root)
        if shared and album_dir != new_album_dir:
            plan.note("folder_split", title_id=title.id)
        put(plan.before, "versions", version.id, relative_path=version.relative_path)
        put(plan.after, "versions", version.id, relative_path=posix(new_album_dir, root))
        plan.companions.append(("album", version.id))
        if album_dir != new_album_dir:
            plan.folders.append((posix(album_dir, root), posix(new_album_dir, root)))
        count = _album_moves(
            db, plan, naming, artist, title, version, (album_dir, new_album_dir), shared, (moved, stays)
        )
        if plan.skip is not None:
            return plan
        plan.albums.append((title.id, name, count))

    plan.left = len(stays - moved)
    if whole and exclusive and new_artist_dir != artist_dir:
        for path in files_below(artist_dir, moved):
            plan.moves.append(Move(path, new_artist_dir / path.relative_to(artist_dir), "other"))
    return plan


def _album_moves(
    db: OrmSession,
    plan: TitlePlan,
    naming: naming_music.MusicNaming,
    artist: Artist,
    title: Title,
    version: Version,
    folders_of: tuple[Path, Path],
    shared: bool,
    sets: tuple[set[Path], set[Path]],
) -> int:
    """The moves of one album; returns how many of its files are tracks. Leaves the plan skipped on a conflict."""
    album_dir, new_album_dir = folders_of
    moved, stays = sets
    facts = album_facts(artist, title)
    rows = list(db.scalars(select(TrackFile).where(TrackFile.version_id == version.id).order_by(TrackFile.id)))
    track_ids = {row.track_id for row in rows if row.track_id is not None}
    tracks = {track.id: track for track in db.scalars(select(ReleaseTrack).where(ReleaseTrack.id.in_(track_ids)))}
    media = {
        medium.id: medium
        for medium in db.scalars(
            select(ReleaseMedium).where(ReleaseMedium.id.in_({track.medium_id for track in tracks.values()}))
        )
    }
    releases = {
        release.id: release
        for release in db.scalars(
            select(Release).where(Release.id.in_({track.release_id for track in tracks.values()}))
        )
    }
    own: set[Path] = set()
    count = 0
    for row in rows:
        old = paths.file_in(album_dir, row.relative_path)
        if old is None:
            continue
        if old in moved:
            plan.skipped("file_twice")
            return 0
        own.add(old)
        if not os.path.lexists(old):
            plan.note("file_missing", name=old.name)
            continue
        if files.is_link(old):
            plan.skipped("link")
            return 0
        track = tracks.get(row.track_id) if row.track_id is not None and not row.unclear else None
        if track is None:
            # Unclear or without a track: it keeps its name and its place in the album folder.
            target = new_album_dir / old.relative_to(album_dir)
        else:
            medium = media.get(track.medium_id)
            release = releases.get(track.release_id)
            track_facts = naming_music.TrackFacts(
                medium=medium.position if medium is not None else 1,
                position=track.position,
                title=track.name or "",
                artist_name=tag_writing.credit_text(track.artist_credit or title.artist_credit),
                medium_format=medium.format if medium is not None else None,
                quality=row.quality,
                original_filename=old.stem,
            )
            several = release is not None and (release.media_count or 1) > 1
            name = naming_music.file_name(naming, facts, track_facts, old.suffix, several_media=several)
            target = new_album_dir / name
            count += 1
            lyrics = old.with_suffix(".lrc")
            if lyrics != old and os.path.lexists(lyrics) and lyrics not in moved:
                plan.moves.append(Move(lyrics, target.with_suffix(".lrc"), "extra"))
                moved.add(lyrics)
                own.add(lyrics)
        plan.moves.append(Move(old, target, "file" if track is not None else "other"))
        moved.add(old)
        put(plan.before, "track_files", row.id, relative_path=row.relative_path)
        put(plan.after, "track_files", row.id, relative_path=posix(target, new_album_dir))
    if not shared:
        for path in files_below(album_dir, moved | own):
            plan.moves.append(Move(path, new_album_dir / path.relative_to(album_dir), "other"))
            moved.add(path)
        return count
    # A shared folder: images go along from a folder whose audio is all this album's.
    for folder in {path.parent for path in own}:
        try:
            entries = list(folder.iterdir())
        except OSError:
            continue
        audio = {entry for entry in entries if entry.suffix.casefold() in AUDIO_ENDINGS}
        if not audio or not audio <= own:
            stays.update(
                entry
                for entry in entries
                if entry.is_file() and entry not in own and entry.suffix.casefold() in IMAGE_ENDINGS
            )
            continue
        for entry in entries:
            if entry.is_file() and entry.suffix.casefold() in IMAGE_ENDINGS and entry not in moved:
                target = new_album_dir / entry.name
                if any(move.new == target for move in plan.moves):
                    stays.add(entry)
                    continue
                plan.moves.append(Move(entry, target, "other"))
                moved.add(entry)
    return count


def _others_in_artist_folder(db: OrmSession, artist_id: int, root: Path, folder: str) -> bool:
    """Whether an album of another artist lies in this artist's folder."""
    rows = db.execute(
        select(Version.root_folder, Version.relative_path)
        .join(Title, Title.id == Version.title_id)
        .where(
            Title.kind == "album",
            Title.artist_id != artist_id,
            (Version.relative_path == folder) | Version.relative_path.like(folder + "/%"),
        )
    )
    for root_folder, _relative in rows:
        try:
            other, _mount = folders.visible(root_folder)
        except folders.NotVisible:
            continue
        if other == root:
            return True
    return False


def plan(db: OrmSession, artist_id: int, planned_elsewhere: set[Path] | None = None) -> TitlePlan | None:
    found = plan_artist(db, artist_id)
    return finish(found, planned_elsewhere) if found is not None else None


def plan_album(db: OrmSession, title_id: int) -> TitlePlan | None:
    title = db.get(Title, title_id)
    if title is None or title.kind != "album" or title.artist_id is None:
        return None
    found = plan_artist(db, title.artist_id, album_id=title.id)
    return finish(found) if found is not None else None


def artist_ids(db: OrmSession) -> list[int]:
    """Artists with an album file of nexcrate's own, by name."""
    return list(
        db.scalars(
            select(Artist.id)
            .where(
                Artist.id.in_(
                    select(Title.artist_id)
                    .join(Version, Version.title_id == Title.id)
                    .where(Title.kind == "album", Version.source_id.is_(None), Version.has_file.is_(True))
                )
            )
            .order_by(Artist.sort_name, Artist.name, Artist.id)
        )
    )
