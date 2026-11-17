"""After an album download: the target follows a release the album's files fit completely (17.11.2026).

Found on the owner's library: of 15 new albums, 6 had every file in place, but the target the rule had chosen was
another release (more tracks, another pressing). nexcrate showed tracks as missing and would have searched on.

* **Fits completely**: every track of a release that is music (``target.is_filler``) is a song the album holds, by
  recording or by its unique name (``same_song``), whichever release the files were filed on.
* **Nothing lost**: the release has at least as many tracks of music as the target, or every song of the target is on
  it as well. Then a target the rule chose goes there by itself, as ``rule`` with the reason ``files``.
* Otherwise, and always for the owner's own choice or a source's, the owner is asked: the download waits as
  ``album_fits_other_release`` with the release and what the target would lose.
* Only releases the rule would take at all: official, audio only, with tracks, the album's own version (no live,
  instrumental or remix release, as since 1.8.0). Among several that fit, the one with the most music, then the
  rule's order; the media brake does not apply, as the files lie there already.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ...models import Release, ReleaseTrack, Title, TrackFile, Version, VersionDefinition
from . import same_song, store, target

logger = logging.getLogger("nexcrate.music")

#: Who chose a target the rule may move by itself.
MOVABLE = ("rule", None)
PROBLEM = "album_fits_other_release"


@dataclass(frozen=True)
class Fit:
    release_id: int
    #: Tracks of music of the release that fits, and of the target.
    tracks: int
    target_tracks: int
    #: Tracks of music of the target whose song the release lacks.
    lost: int
    #: The target moves by itself; otherwise the owner is asked.
    automatic: bool

    def values(self, release: Release | None) -> dict[str, Any]:
        """The problem's values: what the card says."""
        return {
            "release_id": self.release_id,
            "name": release.name if release is not None else None,
            "date": release.date if release is not None else None,
            "country": release.country if release is not None else None,
            "tracks": self.tracks,
            "target_tracks": self.target_tracks,
            "lost": self.lost,
        }


def _held(db: OrmSession, version: Version) -> set[int]:
    held: set[int] = set()
    for item in db.scalars(select(TrackFile).where(TrackFile.version_id == version.id)):
        held.update(item.track_ids or ([item.track_id] if item.track_id else []))
    return held


def fit_of(db: OrmSession, title: Title, version: Version, language: str) -> Fit | None:
    """The release the album's files fit completely while its target is not complete; None when there is none."""
    current = version.target_release_id
    if current is None:
        return None
    candidates = {item.id: item for item in store.candidates_of(db, title.id)}
    if current not in candidates:
        return None
    tracks: dict[int, list[ReleaseTrack]] = defaultdict(list)
    for track in db.scalars(select(ReleaseTrack).where(ReleaseTrack.release_id.in_(list(candidates)))):
        tracks[track.release_id].append(track)
    keys = same_song.keys_by_track(tracks.values())
    held = same_song.union(keys, _held(db, version))

    def music(release_id: int) -> list[ReleaseTrack]:
        return [track for track in tracks[release_id] if not target.is_filler(track.name, track.length_ms)]

    def complete(release_id: int) -> bool:
        listed = music(release_id)
        return bool(listed) and all(keys.get(track.id, frozenset()) & held for track in listed)

    if complete(current):
        return None
    fitting = [
        item
        for item in candidates.values()
        if item.id != current
        and (item.status or "official") == "official"
        and target.is_audio_only(item.formats)
        and item.tracks_loaded
        and not item.other_versions
        and complete(item.id)
    ]
    if not fitting:
        return None
    # The most music first, without the rule's media brake: the files lie there already, no box has to be loaded.
    most = max(len(music(item.id)) for item in fitting)
    fitting = [item for item in fitting if len(music(item.id)) == most]
    definition = db.get(VersionDefinition, version.version_definition_id)
    chosen = target.choose(fitting, store.countries_of(db, definition, language)).chosen
    if chosen is None:
        return None
    theirs = music(chosen.id)
    songs = same_song.union(keys, (track.id for track in theirs))
    ours = music(current)
    lost = sum(1 for track in ours if not keys.get(track.id, frozenset()) & songs)
    nothing_lost = len(theirs) >= len(ours) or lost == 0
    return Fit(chosen.id, len(theirs), len(ours), lost, nothing_lost and version.target_set_by in MOVABLE)


def follow(db: OrmSession, title: Title, version: Version, fit: Fit, *, moment: Any) -> None:
    """The target goes to the release the files fit, as the rule's choice with the reason ``files``."""
    definition = db.get(VersionDefinition, version.version_definition_id)
    old = db.get(Release, version.target_release_id) if version.target_release_id is not None else None
    new = db.get(Release, fit.release_id)
    version.target_release_id = fit.release_id
    version.target_set_by = "rule"
    version.target_reason = {"codes": [{"code": "files"}]}
    version.target_suggestion_id = None
    store._history(
        db,
        title.id,
        version.id,
        definition,
        "target_changed",
        moment,
        f"{store._release_text(old)} > {store._release_text(new)}",
    )
    store._count_tracks(db, version)


# --- Once for the albums there are (17.11.2026) --------------------------------------------------------------------- #

#: The modes of the run; ``targets-`` before them in the version repair's request file (``version_repair.run_job``).
MODES = ("preview", "apply")


def survey(db: OrmSession, language: str) -> list[dict[str, Any]]:
    """Every album with files whose target the rule chose and whose files fit another release completely: what the
    run would do. The owner's own targets and a source's stay out. Writes nothing."""
    found: list[dict[str, Any]] = []
    rows = db.execute(
        select(Title, Version)
        .join(Version, Version.title_id == Title.id)
        .where(
            Title.kind == "album",
            Version.has_file.is_(True),
            Version.target_release_id.is_not(None),
            Version.source_id.is_(None),
        )
        .order_by(Title.id)
    ).all()
    for title, version in rows:
        if version.target_set_by not in MOVABLE:
            continue
        fit = fit_of(db, title, version, language)
        if fit is None:
            continue
        old = db.get(Release, version.target_release_id)
        new = db.get(Release, fit.release_id)
        found.append(
            {
                "title_id": title.id,
                "version_id": version.id,
                "album": title.title,
                "from": {"id": old.id, "name": old.name, "date": old.date, "country": old.country} if old else None,
                "to": {"id": new.id, "name": new.name, "date": new.date, "country": new.country} if new else None,
                "tracks": fit.tracks,
                "target_tracks": fit.target_tracks,
                "lost": fit.lost,
                "nothing_lost": fit.automatic,
            }
        )
    return found


def run(mode: str) -> Path:
    """``preview`` looks, ``apply`` moves every target ``survey`` names to the release the files fit, writes the album
    folder's release.nex again and lets files of several releases follow the new target. The report lies beside the
    log. Returns its path."""
    import json

    from ...db import SessionLocal
    from ...models import Artist, utcnow
    from .. import companions_album, logs, naming_music
    from ..rename import music as rename_music
    from . import album_read, align, tag_writing

    if mode not in MODES:
        raise ValueError(mode)
    moment = utcnow()
    with SessionLocal() as db:
        language = store.account_language(db)
        findings = survey(db, language)
        naming = naming_music.load(db)
        write_tags = tag_writing.write_enabled(db)
    moved: list[int] = []
    failed: list[dict[str, Any]] = []
    if mode == "apply":
        for finding in findings:
            try:
                with SessionLocal() as db:
                    title = db.get(Title, finding["title_id"])
                    version = db.get(Version, finding["version_id"])
                    fit = fit_of(db, title, version, language) if title and version else None
                    if title is None or version is None or fit is None or version.target_set_by not in MOVABLE:
                        continue
                    follow(db, title, version, fit, moment=moment)
                    db.commit()
                    folder = album_read.album_folder(version)
                    artist = db.get(Artist, title.artist_id) if title.artist_id is not None else None
                    if folder is not None and artist is not None:
                        facts = rename_music.album_facts(artist, title)
                        align.align(db, version, folder, naming, facts, write_tags=write_tags)
                        db.commit()
                moved.append(finding["version_id"])
                companions_album.write_version(finding["version_id"])
            except Exception as exc:  # one album never stops the run
                logger.exception("Target follow: album %d could not be changed", finding["title_id"])
                failed.append({"title_id": finding["title_id"], "error": type(exc).__name__})
    data = {
        "mode": mode,
        "at": moment.isoformat(),
        "albums": len(findings),
        "nothing_lost": sum(1 for item in findings if item["nothing_lost"]),
        "moved": len(moved),
        "failed": failed,
        "findings": findings,
    }
    target = logs.log_dir() / f"music-target-follow-{mode}-{moment:%Y%m%d-%H%M%S}.json"
    target.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    logger.info(
        "Target follow %s: %d albums, %d without loss, %d moved, %d failed, report %s",
        mode,
        len(findings),
        data["nothing_lost"],
        len(moved),
        len(failed),
        target.name,
    )
    return target
