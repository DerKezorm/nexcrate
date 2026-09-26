"""Putting a file back whose title or version left the library: they are added again first (the owner's decision of
26.09.2026).

A program that takes back what it brought, the interface's "remove", removing several titles at once: each may leave a
title's files in the recycle bin while the title itself goes. Restoring such a file adds the title again from TMDB (an
album from MusicBrainz, found by the release group the entry kept), and the version the file belonged to, then the
file goes back as always. When the title was added again in between, the file goes into that one.

* The version comes back **unwatched**: the entry does not know what was watched, and a restore never starts a search.
  A series version watches no season (``watch_rule`` none); its folder is read afterwards as for any new version.
* The program's ``origin`` and its key's name come back with title and version, so the program knows them as its own
  again and may take them back once more (the entry keeps them since 26.09.2026; an older entry brings none).
* A series' episodes and an album's tracks have new rows then: the file finds them by TMDB's numbers and by the track's
  MusicBrainz id. An episode file whose episodes are not found comes back without, as an unclear file. So does a track
  file whose track is not found, and that is always so for an album added again: its releases load afterwards, in the
  background. It is listed under the album's unclear files and assigned there; reading the folder again does not
  place it, a path the version knows stays as it is.

Refused, with nothing added: the version no longer exists (``recycle_version_gone``), the file is gone
(``recycle_file_gone``), the entry does not know the title's reference or TMDB and MusicBrainz no longer know the title
(``recycle_title_gone``), and TMDB or MusicBrainz cannot answer (their own codes). Everything that needs no network is
checked before either is asked.

⚠️ Log lines carry ids, never a title, a path or a name.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ..db import SessionLocal
from ..meldungen import error
from ..models import Episode, RecycleEntry, Release, ReleaseTrack, Title, Version, VersionDefinition
from . import library

logger = logging.getLogger("nexcrate.recycle_bin")

#: The key of ``file_facts`` that holds what the entry knows of its title and version beyond its columns.
LIBRARY = "library"


@dataclass(frozen=True)
class Missing:
    """A title a restore has to add first: its kind, and its TMDB number or an album's release group."""

    kind: str
    ref: str


@dataclass(frozen=True)
class Fetched:
    """What TMDB or MusicBrainz answered for a ``Missing`` title."""

    kind: str
    ref: str
    data: Any


# --- What the entry keeps ------------------------------------------------------------------------------------------- #


def facts(title: Title, version: Version, **more: Any) -> dict[str, Any]:
    """What a restore needs to add the title and version again, kept when a file goes into the bin."""
    kept: dict[str, Any] = {
        "origin": title.origin,
        "origin_key": title.origin_key,
        "version_origin": version.origin,
        "version_origin_key": version.origin_key,
    }
    if title.kind == "series":
        kept["series_type"] = title.series_type
    if title.kind == "album":
        kept["mbid"] = title.mbid
    kept.update(more)
    return kept


def kept(entry: RecycleEntry) -> dict[str, Any]:
    found = (entry.file_facts or {}).get(LIBRARY)
    return found if isinstance(found, dict) else {}


def ref_of(entry: RecycleEntry) -> str | None:
    """The TMDB number, or an album's release group; None when the entry does not know it."""
    if entry.kind == "album":
        mbid = kept(entry).get("mbid")
        return str(mbid) if mbid else None
    if entry.kind in ("movie", "series") and entry.tmdb_id:
        return str(entry.tmdb_id)
    return None


# --- Finding ------------------------------------------------------------------------------------------------------ #


def find_title(db: OrmSession, entry: RecycleEntry) -> Title | None:
    """The entry's title, or the title of the same reference added again since."""
    if entry.title_id is not None:
        title = db.get(Title, entry.title_id)
        if title is not None:
            return title
    ref = ref_of(entry)
    if ref is None:
        return None
    if entry.kind == "album":
        from .music import store as music_store

        return music_store.find_album(db, ref)
    return db.scalar(select(Title).where(Title.kind == entry.kind, Title.tmdb_id == int(ref)))


def find_version(db: OrmSession, entry: RecycleEntry, title: Title) -> Version | None:
    if entry.version_definition_id is None:
        return None
    return db.scalar(
        select(Version).where(
            Version.title_id == title.id, Version.version_definition_id == entry.version_definition_id
        )
    )


def definition_of(db: OrmSession, entry: RecycleEntry) -> VersionDefinition:
    """The version the file belonged to, or 409 ``recycle_version_gone``."""
    definition = (
        db.get(VersionDefinition, entry.version_definition_id) if entry.version_definition_id is not None else None
    )
    if definition is None or definition.kind != entry.kind:
        raise error("recycle_version_gone", "The version the file belonged to no longer exists.", 409)
    return definition


def can_add(db: OrmSession, entry: RecycleEntry, title_there: bool) -> bool:
    """Whether a restore could add the version, and the title unless it is there, as far as the entry tells without
    the network."""
    definition = (
        db.get(VersionDefinition, entry.version_definition_id) if entry.version_definition_id is not None else None
    )
    if definition is None or definition.kind != entry.kind:
        return False
    return title_there or ref_of(entry) is not None


def missing(entry_id: int) -> Missing | None:
    """The title a restore has to fetch first, or None when it is there (or the entry is not: the restore says so).

    Refuses before anything is fetched when the version no longer exists, the file is gone, something lies where it
    was, or the entry does not know the title's reference.
    """
    from . import recycle_bin

    with SessionLocal() as db:
        entry = db.get(RecycleEntry, entry_id)
        if entry is None or find_title(db, entry) is not None:
            return None
        definition_of(db, entry)
        located = recycle_bin.bin_file(entry)
        if located is None:
            raise recycle_bin.file_gone()
        recycle_bin.target_free(located[0], entry.relative_path)
        ref = ref_of(entry)
        if ref is None:
            raise recycle_bin.title_gone()
        return Missing(entry.kind, ref)


#: What TMDB and MusicBrainz answer for a number they do not know (any more).
_UNKNOWN_THERE = ("not_found", "musicbrainz_not_found")


async def fetch(wanted: Missing) -> Fetched:
    """The title's data from TMDB or MusicBrainz; their error, as the API answers it, when they cannot give it. A
    title neither knows any more cannot be added again: ``recycle_title_gone``, not a 404 that reads as "no entry"."""
    from . import recycle_bin
    from .api_v1 import writing

    try:
        data = await writing.fetch(wanted.kind, wanted.ref)
    except HTTPException as exc:
        if isinstance(exc.detail, dict) and exc.detail.get("code") in _UNKNOWN_THERE:
            raise recycle_bin.title_gone() from exc
        raise
    return Fetched(wanted.kind, wanted.ref, data)


# --- Adding ------------------------------------------------------------------------------------------------------- #


def add_title(db: OrmSession, entry: RecycleEntry, fetched: Fetched, definition: VersionDefinition,
              moment: datetime) -> Title:  # fmt: skip
    """The title from what was fetched, with the origin the entry kept. The caller commits."""
    from .series import store as series_store
    from .series import watching

    known = kept(entry)
    if fetched.kind == "movie":
        title = library.add_title(db, fetched.data, [], moment)
    elif fetched.kind == "series":
        title = series_store.new_title(fetched.data, moment, known.get("series_type"))
        db.add(title)
        db.flush()
        series_store.apply_series(db, title, fetched.data, moment, watching.today())
    else:
        from ..routers import music as music_router

        group, artists = fetched.data
        title = music_router.new_album(db, group, artists, moment, definition)
    title.origin = known.get("origin")
    title.origin_key = known.get("origin_key")
    db.flush()
    logger.info("Title %d added again for recycle entry %d", title.id, entry.id)
    return title


def _folder_of(entry: RecycleEntry) -> str | None:
    """The series or album folder below the root: the entry's path without the file's own path below the folder."""
    inner = str((entry.file_facts or {}).get("relative_path") or "").strip("/")
    outer = entry.relative_path.replace("\\", "/").strip("/")
    if not inner or not outer.endswith(f"/{inner}"):
        return None
    folder = outer[: -len(inner) - 1].strip("/")
    return folder or None


def add_version(db: OrmSession, entry: RecycleEntry, title: Title, definition: VersionDefinition,
                moment: datetime) -> tuple[Version, bool]:  # fmt: skip
    """The version the file belonged to, unwatched, in the root folder the file came from, with the origin the entry
    kept. Returns it and whether its series folder is to be read after the commit. The caller commits."""
    from .music import store as music_store
    from .series import folder_read, watching

    known = kept(entry)
    read = False
    if title.kind == "album":
        version = music_store.add_version(db, title, definition, moment, monitored=False)
    else:
        version = library.add_owner_version(db, title, definition, moment)
    version.root_folder = entry.root_folder
    version.origin = known.get("version_origin")
    version.origin_key = known.get("version_origin_key")
    if title.kind == "movie":
        version.monitored, version.state = False, "unmonitored"
    elif title.kind == "series":
        folder = _folder_of(entry)
        # One folder below the root, as every series version of nexcrate's own has it.
        version.relative_path = folder if folder and "/" not in folder else None
        version.watch_rule = "none"
        on = watching.today()
        watching.sync_rows(db, version, on)
        watching.recount(db, version, on)
        read = folder_read.claim(db, version, moment)
    else:
        version.relative_path = _folder_of(entry)
    db.flush()
    logger.info("Version %d added again for recycle entry %d", version.id, entry.id)
    return version, read


# --- Rows that are new after adding again --------------------------------------------------------------------------- #


def episode_ids(db: OrmSession, entry: RecycleEntry, title: Title, stored: list[int]) -> tuple[list[int], bool]:
    """The episodes of the file in this title, and whether they were found again by TMDB's numbers.

    The stored rows while they are all the title's; otherwise the title was added again and its episodes have new
    rows, found by the season and episode numbers the entry kept.
    """
    if not stored:
        return [], False
    own = set(db.scalars(select(Episode.id).where(Episode.title_id == title.id, Episode.id.in_(stored))))
    if own == set(stored):
        return stored, False
    if entry.season is None or not entry.episodes:
        return [], True
    found = db.scalars(
        select(Episode.id)
        .where(
            Episode.title_id == title.id,
            Episode.season_number == entry.season,
            Episode.episode_number.in_(list(entry.episodes)),
        )
        .order_by(Episode.episode_number)
    )
    return list(found), True


def track_ids(db: OrmSession, entry: RecycleEntry, title: Title, track_id: Any,
              more: Any) -> tuple[int | None, list[int] | None]:  # fmt: skip
    """The file's track and tracks in this album: the stored rows while they are the album's, otherwise found again by
    the tracks' MusicBrainz ids the entry kept; None where neither finds one."""
    rows = dict(
        db.execute(
            select(ReleaseTrack.id, ReleaseTrack.mbid)
            .join(Release, Release.id == ReleaseTrack.release_id)
            .where(Release.title_id == title.id)
        )
        .tuples()
        .all()
    )
    stored = [value for value in (more or []) if isinstance(value, int)]
    if (track_id is None or track_id in rows) and all(value in rows for value in stored):
        return track_id, list(more) if more is not None else None
    by_mbid = {mbid: row_id for row_id, mbid in rows.items() if mbid}
    known = kept(entry)
    track = by_mbid.get(str(known.get("track") or ""))
    tracks = [by_mbid[mbid] for mbid in known.get("tracks") or [] if mbid in by_mbid]
    return track, tracks or None
