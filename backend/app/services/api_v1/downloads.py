"""Downloads through ``/api/v1``, stage V3: the queue, problems with what may be done about
them, and the owner's actions for a program with the scope ``operate``.

**One entry per download, one state** (N26): a season pack is one entry with its episodes, a trouble lies in
``problem``, never in three fields. The episodes stand under ``series`` in TMDB's numbers (rules 3 and 6).

**Every problem carries its actions and what an automatic may do alone** (N27). The table follows Nexview's own for
Radarr and Sonarr (``download_gruende.py``), so a program that handled a stuck download by itself before still may.
An action a problem does not list is refused (``action_not_allowed``): the table is contract, not advice.

Everything here calls what the interface calls: ``downloads.actions`` and ``downloads.assigning``. Log lines carry ids
and codes, never a title or a key's name.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ...db import SessionLocal
from ...meldungen import error
from ...models import Download, DownloadClient, Episode, HistoryEntry, Release, ReleaseTrack, Title, Version, utcnow
from ...models.downloads import ACTIVE_STATES, CLIENT_STATES
from ..downloads import actions, album_assigning, assigning
from ..downloads import store as download_store
from . import KINDS, events, refs
from . import versions as v1_versions

logger = logging.getLogger("nexcrate.api_v1")

#: What a program may ask for, as the addresses are named.
ACTIONS = ("retry", "remove", "remove_and_search", "clear", "search", "confirm_mapping", "finish", "assign")

#: English fallback texts per problem code; a program translates by code.
MESSAGES = {
    "download_failed": "The download client gave up on the download. Its release is blocked.",
    "path_not_found": "nexcrate cannot see the folder the download client reports.",
    "packed": "The download holds archives nexcrate cannot unpack safely.",
    "no_video": "The download holds no video.",
    "no_audio": "The download holds no audio file.",
    "no_space": "There is not enough free space to file the download away.",
    "gone_from_client": "The download client no longer knows the download.",
    "client_error": "The download client reports an error for the download.",
    "import_failed": "Filing the download away failed.",
    "file_truncated": "A video of the download is cut off; it was not filed and replaced nothing.",
    "dangerous_file": "The download holds a dangerous file; it was refused and its release blocked.",
    "encrypted": "The download is protected by a password; it was refused and its release blocked.",
    "client_unreachable": "The download client cannot be reached; nexcrate keeps asking.",
    "stalled": "The download makes no progress.",
    "files_unassigned": "Files of the download could not be filed safely and wait for an assignment.",
    "other_series_suspected": "The files are named like another series.",
    "several_videos": "The download holds several similar videos; one has to be chosen.",
    "import_stalled": "Filing the download away made no progress for 30 minutes.",
    "too_many_files": "The download holds too many files to be read.",
    "multi_part": "The movie comes in parts; it was refused and its release blocked.",
    "album_single_file": "The album comes as one file with a cue sheet; it was refused and its release blocked.",
    "album_not_better": "The album's files are not better than those already there.",
    "album_tracks_missing": "Tracks of the album are missing from the download.",
}

_SEARCH_AND_CLEAR = (("search", "clear"), ("search",))
_REPLACE = (("remove_and_search", "remove"), ("remove_and_search",))
_REFUSED = (("remove", "search"), ("remove", "search"))


@dataclass(frozen=True)
class Choice:
    actions: tuple[str, ...]
    automatic: tuple[str, ...]


def choice(code: str | None, state: str, values: dict[str, Any], scope: str | None) -> Choice:
    """What may be done about a download: with a problem by its code, without one what the interface offers."""
    retry = ("retry",) if state in ("problem", "completed") else ()
    series_or_album = scope is not None
    if code is None:
        found: tuple[str, ...] = ()
        if state in (*CLIENT_STATES, "completed"):
            found = ("remove", "remove_and_search")
        if state == "completed":
            found = ("retry", *found)
        return Choice(found, ())
    table: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
        "download_failed": _SEARCH_AND_CLEAR,
        "packed": _REPLACE,
        "no_video": _REPLACE,
        "file_truncated": _REPLACE,
        "no_audio": _REPLACE,
        "dangerous_file": _REFUSED,
        "encrypted": _REFUSED,
        "multi_part": _REFUSED,
        "album_single_file": _REFUSED,
        "gone_from_client": (("remove", "remove_and_search"), ("remove",)),
        "client_error": _REPLACE,
        "stalled": _REPLACE,
        "import_stalled": ((*retry, "remove"), ("retry",) if retry else ()),
        "album_not_better": (("assign", "finish", "remove"), ("remove",)),
        "client_unreachable": ((), ()),
        "no_space": ((*retry, "remove"), ()),
        "too_many_files": (("remove", "remove_and_search"), ()),
        "import_failed": ((*retry, *(("finish",) if series_or_album else ()), "remove"), ()),
        "album_tracks_missing": ((*retry, "finish", "remove"), ()),
        "files_unassigned": (("assign", "finish", "remove"), ()),
        "other_series_suspected": (("assign", "remove_and_search", "remove"), ()),
        "several_videos": (("assign", "remove"), ()),
    }
    if code == "path_not_found":
        if isinstance(values.get("proposal"), dict):
            return Choice(("confirm_mapping", "remove"), ())
        return Choice((*retry, "remove"), ())
    found_actions, automatic = table.get(code, ((*retry, "remove"), ()))
    return Choice(found_actions, automatic)


# --- Reading ------------------------------------------------------------------------------------------------------ #


def _problem(row: Download, client_code: str | None) -> dict[str, Any] | None:
    found = download_store.problem_of(row, client_code)
    if found is None:
        return None
    picked = choice(found["code"], row.state, found["values"], row.scope)
    return {
        "code": found["code"],
        "needs_owner": found["needs_owner"],
        "message": MESSAGES.get(found["code"], "The download needs a look."),
        "params": found["values"],
        "actions": list(picked.actions),
        "automatic": list(picked.automatic),
    }


def _remaining_bytes(row: Download) -> int | None:
    if row.size_bytes is None or row.progress is None:
        return None
    return max(0, round(row.size_bytes * (100.0 - min(100.0, max(0.0, row.progress))) / 100.0))


def render(db: OrmSession, rows: list[Download]) -> list[dict[str, Any]]:
    """Downloads of the kinds ``/api/v1`` answers for, as the queue lists them."""
    title_ids = {row.title_id for row in rows}
    titles = {row.id: row for row in db.scalars(select(Title).where(Title.id.in_(title_ids)))} if title_ids else {}
    client_codes = {client.id: client.last_error_code for client in db.scalars(select(DownloadClient))}
    public = v1_versions.public_ids(db)
    origins = {
        (title_id, definition_id): origin
        for title_id, definition_id, origin in db.execute(
            select(Version.title_id, Version.version_definition_id, Version.origin).where(
                Version.title_id.in_(title_ids)
            )
        ).tuples()
    } if title_ids else {}
    pairs_of = defaultdict(list)
    series_ids = [row.id for row in rows if row.scope in download_store.SERIES_SCOPES]
    if series_ids:
        pairs_of.update(events._episodes_of(db, series_ids))
    items = []
    for row in rows:
        title = titles.get(row.title_id)
        if title is None or title.kind not in KINDS:
            continue
        item: dict[str, Any] = {
            "download_id": row.id,
            "title": {
                "kind": title.kind,
                "ref": refs.primary(title),
                "name": title.title or None,
                "origin": title.origin,
            },
            "version_id": public.get(row.version_definition_id) if row.version_definition_id else None,
            "origin": origins.get((row.title_id, row.version_definition_id)),
            "started_by": row.origin or "manual",
            "state": row.state,
            "progress": row.progress,
            "size_bytes": row.size_bytes,
            "remaining_bytes": _remaining_bytes(row) if row.state in ACTIVE_STATES else None,
            "remaining_seconds": row.remaining_seconds if row.state in ACTIVE_STATES else None,
            "quality": row.quality,
            "release": row.release_title or None,
            "protocol": row.protocol,
            "grabbed_at": row.grabbed_at,
            "problem": _problem(row, client_codes.get(row.client_id or 0)),
        }
        if title.kind == "series":
            item["series"] = events.series_block(row.season, pairs_of.get(row.id, []))
        items.append(item)
    return items


@dataclass(frozen=True)
class AlbumAssign:
    """Assigning an album download: the release by ``mbid:``, and per file its key, its track by ``mbid:`` (None: not
    filed), and whether it is filed without a track."""

    release: str | None
    files: list[tuple[int, str | None, bool]]


def _unfinished(db: OrmSession) -> list[Download]:
    return list(
        db.scalars(
            select(Download)
            .where(Download.state.in_(download_store.UNFINISHED_STATES) | download_store.waiting_for_owner())
            .order_by(Download.grabbed_at.desc(), Download.id.desc())
        )
    )


def queue(db: OrmSession, kind: str | None) -> list[dict[str, Any]]:
    """Every download that is not finished, newest first: running, stuck, and failed ones waiting for the owner. A
    failure the replacement or the schedule took over is history, as in the interface (the owner's rule of
    22.09.2026)."""
    items = render(db, _unfinished(db))
    return [item for item in items if kind is None or item["title"]["kind"] == kind]


def problems(db: OrmSession, kind: str | None) -> list[dict[str, Any]]:
    """The downloads of the queue with a problem or a hint, those that need the owner first."""
    found = [item for item in queue(db, kind) if item["problem"] is not None]
    return sorted(found, key=lambda item: not item["problem"]["needs_owner"])


def one(db: OrmSession, download_id: int) -> dict[str, Any]:
    row = db.get(Download, download_id)
    items = render(db, [row]) if row is not None else []
    if not items:
        raise _not_found(download_id)
    return items[0]


def _not_found(download_id: int) -> Exception:
    return error("download_not_found", "This download does not exist, or not any more.", 404, download_id=download_id)


# --- Acting ------------------------------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Standing:
    title_id: int
    state: str
    code: str | None
    choice: Choice


def standing(download_id: int) -> Standing:
    """Where a download stands and what may be done; 404 for one of a kind ``/api/v1`` does not answer for."""
    with SessionLocal() as db:
        row = db.get(Download, download_id)
        title = db.get(Title, row.title_id) if row is not None else None
        if row is None or title is None or title.kind not in KINDS:
            raise _not_found(download_id)
        client = db.get(DownloadClient, row.client_id) if row.client_id is not None else None
        found = download_store.problem_of(row, client.last_error_code if client is not None else None)
        code = found["code"] if found is not None else None
        values = found["values"] if found is not None else {}
        return Standing(row.title_id, row.state, code, choice(code, row.state, values, row.scope))


def allowed(download_id: int, action: str) -> Standing:
    found = standing(download_id)
    if action not in found.choice.actions:
        raise error(
            "action_not_allowed",
            "This action is not allowed for the download as it stands.",
            409,
            action=action,
            problem=found.code,
            allowed=list(found.choice.actions),
        )
    return found


def _history(title_id: int, download_id: int, action: str, key_name: str) -> None:
    """``operated`` in the title's history: which action a program took, and through which key."""
    with SessionLocal() as db:
        row = db.get(Download, download_id)
        version = (
            download_store.version_of(db, row.title_id, row.version_definition_id) if row is not None else None
        )
        db.add(
            HistoryEntry(
                title_id=title_id,
                version_id=version.id if version is not None else None,
                version_definition_id=row.version_definition_id if row is not None else None,
                version_label=(row.version_label if row is not None else "")[:64],
                event="operated",
                at=utcnow(),
                detail=f"{action} {key_name}"[:1024],
                data={"action": action, "by": key_name, "download_id": download_id},
            )
        )
        db.commit()


def _wish(title_id: int, moment: datetime) -> None:
    with SessionLocal() as db:
        title = db.get(Title, title_id)
        if title is not None:
            title.search_wish_at = moment
            db.commit()
    from ..automatic import wishes as automatic_wishes

    automatic_wishes.kick()


def _raise(exc: actions.ActionError) -> Exception:
    return exc.http()


async def act(download_id: int, action: str, key_name: str) -> None:
    """``retry``, ``remove``, ``remove_and_search``, ``clear``, ``search``, ``confirm_mapping`` or ``finish``."""
    found = await asyncio.to_thread(allowed, download_id, action)
    try:
        if action == "retry":
            await asyncio.to_thread(actions.retry, download_id)
        elif action in ("remove", "remove_and_search"):
            await actions.remove(
                download_id,
                remove_from_client=found.code != "gone_from_client" and found.state not in ("failed", "importing"),
                blocklist=action == "remove_and_search",
            )
            if action == "remove_and_search":
                await asyncio.to_thread(_wish, found.title_id, utcnow())
        elif action == "clear":
            await asyncio.to_thread(actions.clear, download_id)
        elif action == "search":
            if found.state == "failed":
                await asyncio.to_thread(actions.clear, download_id)
            await asyncio.to_thread(_wish, found.title_id, utcnow())
        elif action == "confirm_mapping":
            await asyncio.to_thread(actions.confirm_mapping, download_id)
        elif action == "finish":
            await asyncio.to_thread(assigning.finish, download_id)
    except actions.ActionError as exc:
        raise _raise(exc) from exc
    await asyncio.to_thread(_history, found.title_id, download_id, action, key_name)
    logger.info("Download %d: %s through the key interface", download_id, action)


# --- Assigning by hand (N42) --------------------------------------------------------------------------------------- #


def _is_album(download_id: int) -> bool:
    with SessionLocal() as db:
        row = db.get(Download, download_id)
        return row is not None and row.scope == "album"


def _album_files(download_id: int) -> dict[str, Any]:
    """An album download: its files, the releases of the album and the tracks of the shown one, by MusicBrainz ids."""
    try:
        found = album_assigning.files_of(download_id)
    except actions.ActionError as exc:
        raise _raise(exc) from exc
    with SessionLocal() as db:
        releases = {
            row_id: mbid
            for row_id, mbid in db.execute(
                select(Release.id, Release.mbid).where(Release.id.in_([item["id"] for item in found["releases"]]))
            ).tuples()
        } if found["releases"] else {}
        track_ids = {item["id"] for item in found["tracks"]} | {
            item["track_id"] for item in found["files"] if item["track_id"] is not None
        }
        tracks = dict(
            db.execute(select(ReleaseTrack.id, ReleaseTrack.mbid).where(ReleaseTrack.id.in_(track_ids))).tuples().all()
        ) if track_ids else {}
        entry = one(db, download_id)

    def ref(mbid: str | None) -> str | None:
        return f"mbid:{mbid}" if mbid else None

    return {
        "download": entry,
        "files": [
            {
                "key": item["key"],
                "name": item["path"],
                "size_bytes": item["size_bytes"],
                "duration_seconds": round(item["duration_ms"] / 1000) if item["duration_ms"] else None,
                "reading": None,
                "decision": item["decision"],
                "album": {
                    "track": ref(tracks.get(item["track_id"])) if item["track_id"] is not None else None,
                    "placed": item["placed"],
                    "other_album": item["other_album"] is not None,
                },
            }
            for item in found["files"]
        ],
        "album": {
            "release": ref(releases.get(found["release_id"])) if found["release_id"] else None,
            "target_release": (
                ref(releases.get(found["target_release_id"])) if found["target_release_id"] else None
            ),
            "release_fixed": found["release_fixed"],
            "releases": [
                {
                    "ref": ref(releases.get(item["id"])),
                    "name": item["name"] or None,
                    "date": item["date"],
                    "country": item["country"],
                    "formats": item["formats"],
                    "tracks": item["track_count"],
                }
                for item in found["releases"]
            ],
            "tracks": [
                {
                    "ref": ref(tracks.get(item["id"])),
                    "medium": item["medium"],
                    "position": item["position"],
                    "number": item["number"],
                    "name": item["name"] or None,
                    "length_ms": item["length_ms"],
                    "held": item["held"] is not None,
                }
                for item in found["tracks"]
            ],
        },
    }


def files(download_id: int) -> dict[str, Any]:
    """The files of a download with what nexcrate read and decided; for a series every episode in TMDB's numbers,
    for an album the tracks of the release by MusicBrainz ids."""
    standing(download_id)
    if _is_album(download_id):
        return _album_files(download_id)
    try:
        found = assigning.files_of(download_id)
    except actions.ActionError as exc:
        raise _raise(exc) from exc
    with SessionLocal() as db:
        numbers = {
            episode_id: (season, number)
            for episode_id, season, number in db.execute(
                select(Episode.id, Episode.season_number, Episode.episode_number).where(
                    Episode.id.in_([item["id"] for item in found["episodes"]])
                )
            ).tuples()
        } if found["episodes"] else {}
        entry = one(db, download_id)
    series = found["kind"] == "series"

    def pairs(episodes: list[dict[str, Any]]) -> list[dict[str, int]]:
        return [
            {"season": numbers[item["id"]][0], "episode": numbers[item["id"]][1]}
            for item in episodes
            if item["id"] in numbers
        ]

    files_out = []
    for item in found["files"]:
        file_out: dict[str, Any] = {
            "key": item["key"],
            "name": item["path"],
            "size_bytes": item["size_bytes"],
            "duration_seconds": item["duration_seconds"],
            "reading": item["reading"],
            "decision": item["decision"],
        }
        if series:
            file_out["series"] = {"episodes": pairs(item["episodes"])}
        files_out.append(file_out)
    answer: dict[str, Any] = {"download": entry, "files": files_out}
    if series:
        answer["series"] = {
            "episodes": [
                {
                    "season": numbers[item["id"]][0],
                    "episode": numbers[item["id"]][1],
                    "name": item["name"] or None,
                    "in_download": item["in_download"],
                    "state": item["state"],
                    "monitored": item["watched"],
                    "current_file": (
                        {"quality": item["current_file"]["quality"], "size_bytes": item["current_file"]["size_bytes"]}
                        if item["current_file"] is not None
                        else None
                    ),
                }
                for item in found["episodes"]
                if item["id"] in numbers
            ]
        }
    return answer


def _episode_ids(title_id: int, wanted: list[list[tuple[int, int]]]) -> list[list[int]]:
    """TMDB's numbers to nexcrate's episodes; an unknown pair is ``episode_not_found``."""
    with SessionLocal() as db:
        ids = {
            (season, number): episode_id
            for episode_id, season, number in db.execute(
                select(Episode.id, Episode.season_number, Episode.episode_number).where(
                    Episode.title_id == title_id, Episode.tmdb_gone_at.is_(None)
                )
            ).tuples()
        }
    result = []
    for pairs in wanted:
        chosen = []
        for pair in pairs:
            if pair not in ids:
                raise error(
                    "episode_not_found", "The series has no such episode.", 422, season=pair[0], episode=pair[1]
                )
            chosen.append(ids[pair])
        result.append(chosen)
    return result


def _album_ids(title_id: int, release: str | None, tracks: list[str]) -> tuple[int | None, dict[str, int]]:
    """MusicBrainz ids of a release and of tracks to nexcrate's rows, among the album's releases."""
    with SessionLocal() as db:
        release_id = None
        if release is not None:
            release_id = db.scalar(
                select(Release.id).where(Release.title_id == title_id, Release.mbid == release.partition(":")[2])
            )
            if release_id is None:
                raise error("release_not_of_album", "The release does not belong to this album.", 422)
        wanted = {item.partition(":")[2] for item in tracks}
        found = {
            f"mbid:{mbid}": track_id
            for track_id, mbid in db.execute(
                select(ReleaseTrack.id, ReleaseTrack.mbid)
                .join(Release, Release.id == ReleaseTrack.release_id)
                .where(Release.title_id == title_id, ReleaseTrack.mbid.in_(wanted))
            ).tuples()
        } if wanted else {}
    for item in tracks:
        if item not in found:
            raise error("track_not_found", "The album has no such track.", 422, track=item)
    return release_id, found


async def assign(
    download_id: int,
    chosen: list[tuple[int, list[tuple[int, int]] | None]],
    confirm: list[str],
    key_name: str,
    album: AlbumAssign | None = None,
) -> None:
    """Series: each file with its episodes (none: not filed). Movie: exactly one file, the video to file. Album:
    each file with its track of the release, filed without one, or not filed."""
    found = await asyncio.to_thread(allowed, download_id, "assign")
    if await asyncio.to_thread(_is_album, download_id):
        if album is None or any(pairs is not None for _key, pairs in chosen):
            raise error("scope_not_for_kind", "A file of an album names its track under album.", 422)
        wanted = [track for _key, track, _loose in album.files if track is not None]
        release_id, ids = await asyncio.to_thread(_album_ids, found.title_id, album.release, wanted)
        picked = [(key, ids[track] if track is not None else None, loose) for key, track, loose in album.files]
        try:
            await asyncio.to_thread(album_assigning.assign, download_id, release_id, picked, confirm)
        except actions.ActionError as exc:
            raise _raise(exc) from exc
        await asyncio.to_thread(_history, found.title_id, download_id, "assign", key_name)
        logger.info("Download %d: album files assigned through the key interface", download_id)
        return
    if album is not None:
        raise error("scope_not_for_kind", "Only an album download takes tracks.", 422)
    with SessionLocal() as db:
        row = db.get(Download, download_id)
        is_series = row is not None and row.scope in download_store.SERIES_SCOPES
    try:
        if is_series:
            if any(pairs is None for _key, pairs in chosen):
                raise error("scope_not_for_kind", "A file of a series names its episodes under series.", 422)
            ids = await asyncio.to_thread(_episode_ids, found.title_id, [list(pairs or []) for _key, pairs in chosen])
            files_with_ids = [(key, ids[index]) for index, (key, _pairs) in enumerate(chosen)]
            await asyncio.to_thread(assigning.assign, download_id, files_with_ids, confirm)
        else:
            if len(chosen) != 1 or chosen[0][1] is not None:
                raise error(
                    "invalid_input", "A movie download takes exactly one file, without series.", 422, fields=["files"]
                )
            await asyncio.to_thread(assigning.choose, download_id, chosen[0][0])
    except actions.ActionError as exc:
        raise _raise(exc) from exc
    await asyncio.to_thread(_history, found.title_id, download_id, "assign", key_name)
    logger.info("Download %d: files assigned through the key interface", download_id)
