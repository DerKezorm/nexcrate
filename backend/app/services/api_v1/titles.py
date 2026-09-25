"""Titles as ``/api/v1`` shows them ("Die Formen").

One shape everywhere: ``kind``, ``ref``, ``refs``, the versions with their state, and under the key named like the
kind what only that kind has. A value nexcrate does not know is null, and the field is there all the same. Seasons and
episodes count as TMDB counts them, whatever the files on disk are numbered by (N15).

Nothing here says where a file lies: no path of the disk leaves through ``/api/v1``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session as OrmSession

from ...models import (
    AlbumArtist,
    Artist,
    Download,
    Episode,
    EpisodeFile,
    EpisodeVersion,
    HistoryEntry,
    Release,
    ReleaseMedium,
    ReleaseTrack,
    Season,
    SeasonVersion,
    Title,
    TrackFile,
    Version,
)
from .. import tags as tag_store
from ..music import kinds as music_kinds
from ..series import anime, watching
from ..series import detail as series_detail
from . import KINDS, TITLE_KINDS, refs
from . import versions as v1_versions

#: The columns a listed title is made of. ⚠️ The change marker hashes exactly what these render: a column that shows
#: in the list belongs here, or the marker misses its changes.
TITLE_COLUMNS = (
    Title.id,
    Title.kind,
    Title.tmdb_id,
    Title.imdb_id,
    Title.tvdb_id,
    Title.title,
    Title.year,
    Title.tmdb_poster_path,
    Title.series_status,
    Title.next_air_date,
    Title.series_type,
    Title.origin,
    Title.mbid,
    Title.primary_type,
    Title.secondary_types,
    Title.first_release_date,
    Title.artist_credit,
    Title.artist_id,
)
#: The Cover Art Archive's front picture of a release group: nexcrate hands out no pictures itself, as with TMDB.
COVER_URL = "https://coverartarchive.org/release-group/{mbid}/front-500"
#: Where a title list names titles that are not titles: an artist stands under the negative of its row number.
#: The same trick as the preview's ``-tmdb_id``; ``title_changes.title_id`` is no foreign key for that reason too.
LOOKUP_MAX = 100


def _counts(stored: object) -> dict[str, int] | None:
    """Watched regular episodes: with a file, aired, and expected altogether. None for a movie version."""
    found = series_detail.counts_from(stored)
    if found is None:
        return None
    return {"have": found["have"], "aired": found["aired_watched"], "expected": found["watched"]}


def _iso(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def _oldest(moments: Iterable[datetime | None]) -> datetime | None:
    """The oldest of the dates; None when there is none, or when one of them is unknown."""
    known: list[datetime] = []
    for moment in moments:
        if moment is None:
            return None
        known.append(moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC))
    return min(known) if known else None


#: The events that say when a movie's current file arrived, by where the file came from (``Version.file_ref``); the
#: same choice as ``release.nex`` makes (``companions._came_from_of``). A takeover or a restore from the bin is no
#: arrival: their dates would make an old file look new.
ARRIVAL_EVENTS = {"disk": ("found_on_disk", "restored", "imported"), "other": ("imported",)}


def _movie_arrivals(db: OrmSession, rows: list[tuple[int, int, str | None]]) -> dict[int, datetime | None]:
    """When the current file of each movie version arrived: a download's import, the day Radarr says it added the file,
    or the day nexcrate found it on the disk. None when nexcrate does not know; never a guessed date."""
    if not rows:
        return {}
    download_ids = {
        int(ref.split(":", 1)[1])
        for _version_id, _title_id, ref in rows
        if ref and ref.startswith("nexcrate:") and ref.split(":", 1)[1].isdigit()
    }
    imported = (
        dict(db.execute(select(Download.id, Download.imported_at).where(Download.id.in_(download_ids))).tuples().all())
        if download_ids
        else {}
    )
    latest: dict[tuple[int, str], datetime] = {}
    for version_id, event, at in db.execute(
        select(HistoryEntry.version_id, HistoryEntry.event, func.max(HistoryEntry.at))
        .where(
            HistoryEntry.title_id.in_({title_id for _version_id, title_id, _ref in rows}),
            HistoryEntry.version_id.in_([version_id for version_id, _title_id, _ref in rows]),
            HistoryEntry.event.in_(sorted({event for events in ARRIVAL_EVENTS.values() for event in events})),
        )
        .group_by(HistoryEntry.version_id, HistoryEntry.event)
    ).tuples():
        latest[(version_id, event)] = at
    found: dict[int, datetime | None] = {}
    for version_id, _title_id, ref in rows:
        ref = ref or ""
        if ref.startswith("nexcrate:") and ref.split(":", 1)[1].isdigit() and imported.get(int(ref.split(":", 1)[1])):
            found[version_id] = imported[int(ref.split(":", 1)[1])]
            continue
        events = ARRIVAL_EVENTS["disk" if ref.startswith("disk:") else "other"]
        dates = [latest[(version_id, event)] for event in events if (version_id, event) in latest]
        found[version_id] = max(dates) if dates else None
    return found


def _versions_of(
    db: OrmSession,
    title_ids: Sequence[int],
    public: dict[int, str],
    series_oldest: dict[int, datetime | None] | None = None,
) -> dict[int, list[dict[str, Any]]]:
    found: dict[int, list[dict[str, Any]]] = defaultdict(list)
    if not title_ids:
        return found
    rows = list(
        db.execute(
            select(
                Version.id,
                Title.kind,
                Version.file_ref,
                Version.title_id,
                Version.version_definition_id,
                Version.state,
                Version.monitored,
                Version.has_file,
                Version.quality,
                Version.size,
                Version.episode_counts,
                Version.origin,
                Version.track_counts,
                Version.target_release_id,
            )
            .join(Title, Title.id == Version.title_id)
            .where(Version.title_id.in_(title_ids))
            .order_by(Version.title_id, Version.version_definition_id)
        )
    )
    arrivals = _movie_arrivals(
        db, [(row.id, row.title_id, row.file_ref) for row in rows if row.kind == "movie" and row.has_file]
    )
    for row in rows:
        counts = _counts(row.episode_counts)
        has_size = bool(row.has_file) or (counts is not None and bool(row.size))
        item: dict[str, Any] = {
            "version_id": public.get(row.version_definition_id),
            "state": row.state,
            "monitored": bool(row.monitored),
            # Null without a file: a stored 0 would claim an empty one.
            "size_bytes": int(row.size) if has_size else None,
            "quality": row.quality if row.has_file else None,
            "origin": row.origin,
            # Since when the file takes space; for a series its oldest file, for an album not known yet.
            "imported_at": _iso(
                arrivals.get(row.id)
                if row.kind == "movie"
                else (series_oldest or {}).get(row.id)
                if row.kind == "series"
                else None
            ),
        }
        if counts is not None:
            item["series"] = {"counts": counts}
        tracks = _tracks(row.track_counts)
        if tracks is not None:
            item["album"] = {"tracks": tracks}
        found[row.title_id].append(item)
    return found


def _item(row: Any, versions: list[dict[str, Any]]) -> dict[str, Any]:
    item: dict[str, Any] = {
        "kind": row.kind,
        "ref": refs.primary(row),
        "refs": refs.all_of(row),
        "name": row.title or None,
        "year": row.year,
        # TMDB's path as TMDB gives it; nexcrate hands out no pictures through this interface.
        "poster_path": f"/{row.tmdb_poster_path}" if row.tmdb_poster_path else None,
        "origin": row.origin,
        "monitored": any(version["monitored"] for version in versions),
        "versions": versions,
    }
    if row.kind == "series":
        item["series"] = {
            "type": anime.series_type(row.series_type),
            "status": row.series_status,
            "next_air_date": row.next_air_date,
        }
    return item


def _tracks(stored: object) -> dict[str, int] | None:
    """``{have, total}`` of an album version's target, None before its tracks are counted."""
    if not isinstance(stored, dict) or not isinstance(stored.get("wanted"), int):
        return None
    present = stored.get("present")
    return {"have": present if isinstance(present, int) else 0, "total": stored["wanted"]}


def _album_blocks(db: OrmSession, rows: list[Any]) -> dict[int, dict[str, Any]]:
    """The ``album`` block of each album row: artists, types, date, cover, target release and tracks."""
    ids = [row.id for row in rows]
    if not ids:
        return {}
    credited: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for title_id, mbid, name in db.execute(
        select(AlbumArtist.title_id, Artist.mbid, Artist.name)
        .join(Artist, Artist.id == AlbumArtist.artist_id)
        .where(AlbumArtist.title_id.in_(ids))
        .order_by(AlbumArtist.title_id, AlbumArtist.position)
    ).tuples():
        credited[title_id].append({"ref": f"mbid:{mbid}", "name": name})
    versions = {
        title_id: (target_id, counts)
        for title_id, target_id, counts in db.execute(
            select(Version.title_id, Version.target_release_id, Version.track_counts)
            .where(Version.title_id.in_(ids))
            .order_by(Version.title_id, Version.id.desc())
        ).tuples()
    }
    target_ids = {target for target, _counts in versions.values() if target is not None}
    releases = (
        {row.id: row for row in db.scalars(select(Release).where(Release.id.in_(target_ids)))} if target_ids else {}
    )
    track_totals = (
        dict(
            db.execute(
                select(ReleaseMedium.release_id, func.sum(ReleaseMedium.track_count))
                .where(ReleaseMedium.release_id.in_(target_ids))
                .group_by(ReleaseMedium.release_id)
            )
            .tuples()
            .all()
        )
        if target_ids
        else {}
    )
    found: dict[int, dict[str, Any]] = {}
    for row in rows:
        artists = credited.get(row.id) or [
            {
                "ref": f"mbid:{part['mbid']}" if part.get("mbid") else None,
                "name": part.get("artist_name") or part.get("name"),
            }
            for part in row.artist_credit or []
            if isinstance(part, dict)
        ]
        target_id, counts = versions.get(row.id, (None, None))
        release = releases.get(target_id) if target_id is not None else None
        found[row.id] = {
            "artists": artists,
            "type": row.primary_type,
            "secondary_types": list(row.secondary_types or []),
            "group": music_kinds.group_of(row.primary_type, row.secondary_types),
            "first_release_date": row.first_release_date,
            "cover_url": COVER_URL.format(mbid=row.mbid) if row.mbid else None,
            "target_release": (
                {
                    "ref": f"mbid:{release.mbid}",
                    "name": release.name or None,
                    "country": release.country,
                    "date": release.date,
                    "formats": list(release.formats or []),
                    "tracks": int(track_totals.get(release.id) or 0) or None,
                }
                if release is not None
                else None
            ),
            "tracks": _tracks(counts),
        }
    return found


def _artists(db: OrmSession, artist_ids: Sequence[int]) -> dict[int, dict[str, Any]]:
    """Artists as a list of titles carries them, by the negative of their row number."""
    if not artist_ids:
        return {}
    rows = list(db.scalars(select(Artist).where(Artist.id.in_(artist_ids), Artist.is_various.is_(False))))
    counted: dict[int, dict[str, int]] = defaultdict(lambda: {"total": 0, "watched": 0, "available": 0, "missing": 0})
    for artist_id, monitored, has_file, state in db.execute(
        select(Title.artist_id, Version.monitored, Version.has_file, Version.state)
        .outerjoin(Version, Version.title_id == Title.id)
        .where(Title.kind == "album", Title.artist_id.in_(artist_ids), Title.mb_gone_at.is_(None))
    ).tuples():
        counts = counted[artist_id]
        counts["total"] += 1
        if monitored:
            counts["watched"] += 1
            if not has_file:
                counts["missing"] += 1
        if has_file and state in ("available", "upgrade"):
            counts["available"] += 1
    labels = tag_store.of_artists(db, [artist.id for artist in rows])
    found: dict[int, dict[str, Any]] = {}
    for artist in rows:
        refs_found = [refs.of_artist(artist)]
        found[-artist.id] = {
            "kind": "artist",
            "ref": refs_found[0],
            "refs": refs_found,
            "name": artist.name or None,
            "year": artist.begin_year,
            "poster_path": None,
            "origin": artist.origin,
            "monitored": artist.frozen_at is None,
            "tags": labels.get(artist.id, []),
            "versions": [],
            "artist": {
                "sort_name": artist.sort_name or None,
                "type": artist.artist_type,
                "country": artist.country,
                "disambiguation": artist.disambiguation,
                "new_albums": artist.monitor_new,
                "album_types": list(artist.album_types or music_kinds.DEFAULT_TYPES),
                "albums": dict(counted[artist.id]),
                "loading": artist.load_state not in ("ready", "failed"),
            },
        }
    return found


#: A title ``/api/v1`` can name: a movie or series with a TMDB number, an album with a release group.
NAMEABLE = or_(
    and_(Title.kind.in_(("movie", "series")), Title.tmdb_id.is_not(None)),
    and_(Title.kind == "album", Title.mbid.is_not(None)),
)


def items(db: OrmSession, title_ids: Iterable[int]) -> dict[int, dict[str, Any]]:
    """Listed titles by row number, for the kinds ``/api/v1`` answers for; an artist under the negative of its row
    number. A title nexcrate has no reference for is left out."""
    wanted = list(title_ids)
    if not wanted:
        return {}
    title_ids_wanted = [item for item in wanted if item > 0]
    found = _artists(db, [-item for item in wanted if item < 0])
    if not title_ids_wanted:
        return found
    public = v1_versions.public_ids(db)
    rows = list(
        db.execute(select(*TITLE_COLUMNS).where(Title.id.in_(title_ids_wanted), Title.kind.in_(TITLE_KINDS), NAMEABLE))
    )
    # A series lists its seasons everywhere, so the change marker moves when only a season changed (Nexview's wish of
    # 24.09.2026): the marker hashes exactly what is listed.
    seasons, series_oldest = _seasons_many(db, [row.id for row in rows if row.kind == "series"], public)
    versions = _versions_of(db, title_ids_wanted, public, series_oldest)
    albums = _album_blocks(db, [row for row in rows if row.kind == "album"])
    # Tags: an album shows those of its artist, as Lidarr binds at the artist.
    labels = tag_store.of_titles(db, [row.id for row in rows if row.kind != "album"])
    labels.update(tag_store.album_tags(db, [row for row in rows if row.kind == "album"]))
    for row in rows:
        item = _item(row, versions.get(row.id, []))
        item["tags"] = labels.get(row.id, [])
        if row.kind == "series":
            item["series"]["seasons"] = seasons.get(row.id, [])
        if row.kind == "album":
            item["poster_path"] = None
            item["album"] = albums[row.id]
        found[row.id] = item
    return found


def _by_mbid(db: OrmSession, kind: str, values: set[str]) -> dict[str, int]:
    """MusicBrainz ids to row numbers (an artist's negative), also by an id MusicBrainz merged away."""
    model = Artist if kind == "artist" else Title
    sign = -1 if kind == "artist" else 1
    conditions = [model.mbid.in_(values)]
    if kind == "album":
        conditions.append(Title.kind == "album")
    else:
        conditions.append(Artist.is_various.is_(False))
    rows = db.execute(select(model.id, model.mbid).where(*conditions)).tuples()
    found = {mbid: sign * row_id for row_id, mbid in rows}
    left = values - set(found)
    if left:
        merged = select(model.id, model.mbid_old).where(model.mbid_old.is_not(None))
        if kind == "album":
            merged = merged.where(Title.kind == "album")
        for row_id, old in db.execute(merged).tuples():
            for mbid in old or []:
                if mbid in left:
                    found.setdefault(mbid, sign * row_id)
    return found


def find(db: OrmSession, kind: str, ref: refs.Ref) -> list[int]:
    """The row numbers of the titles a reference names, an artist's negative. More than one only by ``tvdb`` or
    ``imdb``."""
    if ref.source == "mbid":
        hit = _by_mbid(db, kind, {ref.value}).get(ref.value)
        return [hit] if hit is not None else []
    if ref.source == "tmdb":
        condition = Title.tmdb_id == int(ref.value)
    elif ref.source == "tvdb":
        condition = Title.tvdb_id == int(ref.value)
    else:
        condition = Title.imdb_id == ref.value
    return list(db.scalars(select(Title.id).where(Title.kind == kind, condition).order_by(Title.id)))


def lookup(db: OrmSession, asked: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Many titles in one call, answered in the order asked (N12).

    An entry nexcrate cannot answer carries ``error`` and breaks nothing else: ``kind_unsupported``, ``ref_invalid``,
    ``ref_source_unknown`` or ``ref_ambiguous``.
    """
    parsed: list[tuple[str, str, refs.Ref | None, str | None]] = []
    for kind, raw in asked:
        if kind not in KINDS:
            parsed.append((kind, raw, None, "kind_unsupported"))
            continue
        try:
            parsed.append((kind, raw, refs.parse(kind, raw), None))
        except refs.RefError as problem:
            parsed.append((kind, raw, None, problem.code))

    columns = {"tmdb": Title.tmdb_id, "tvdb": Title.tvdb_id, "imdb": Title.imdb_id}
    wanted: dict[tuple[str, str], set[Any]] = defaultdict(set)
    for kind, _raw, ref, _error in parsed:
        if ref is not None:
            wanted[(kind, ref.source)].add(ref.value if ref.source in ("imdb", "mbid") else int(ref.value))
    matches: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    for (kind, source), values in wanted.items():
        if source == "mbid":
            for mbid, row_id in _by_mbid(db, kind, values).items():
                matches[(kind, source, mbid)].append(row_id)
            continue
        column = columns[source]
        for title_id, value in db.execute(
            select(Title.id, column).where(Title.kind == kind, column.in_(values)).order_by(Title.id)
        ).tuples():
            matches[(kind, source, str(value))].append(title_id)

    rendered = items(db, {title_id for ids in matches.values() for title_id in ids})
    answers = []
    for kind, raw, ref, problem in parsed:
        title = None
        title_id = None
        if ref is not None:
            ids = matches.get((kind, ref.source, ref.value), [])
            if len(ids) > 1:
                problem = "ref_ambiguous"
            elif ids:
                title = rendered.get(ids[0])
                title_id = ids[0] if title is not None else None
        # ``title_id`` is for the callers inside nexcrate; the answer's model leaves it out.
        known = title is not None
        answers.append(
            {"kind": kind, "ref": raw, "known": known, "title": title, "error": problem, "title_id": title_id}
        )
    return answers


# --- One title, with what only its kind has --------------------------------------------------- #


def series_state(
    expected: int,
    aired: int,
    have: int,
    upgrade: int,
    problem: bool,
    downloading: bool,
    files: int | None = None,
) -> str:
    """The state of a season in one version, by the rule a whole series version gets its state by
    (``watching.state_from``): what lies there decides; ``upgrade`` counts every upgradable file here. Without
    ``files`` (an older caller) a season with files is not known and the watched counts decide."""
    if problem:
        return "problem"
    if downloading:
        return "downloading"
    if expected > 0 and have < aired:
        return "wanted"
    if not files:
        return "wanted" if expected > 0 else "unmonitored"
    return "upgrade" if upgrade > 0 else "available"


def _seasons_many(
    db: OrmSession, title_ids: Sequence[int], public: dict[int, str]
) -> tuple[dict[int, list[dict[str, Any]]], dict[int, datetime | None]]:
    """The seasons of many series with every version's numbers, size and ``imported_at``, in a few queries for all of
    them; and per series version the oldest date of its files.

    Size and date count each file once: one file that holds two episodes, and both halves of a double episode TMDB
    lists as one. A season with a file of unknown date has no date: a guessed one would make
    an old season look new (Nexview's wish of 24.09.2026, as it reads Sonarr's oldest ``dateAdded``).
    """
    found: dict[int, list[dict[str, Any]]] = {}
    oldest_of_version: dict[int, datetime | None] = {}
    if not title_ids:
        return found, oldest_of_version
    on = watching.today()
    versions_of: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for version_id, title_id, definition_id in db.execute(
        select(Version.id, Version.title_id, Version.version_definition_id)
        .where(Version.title_id.in_(title_ids))
        .order_by(Version.title_id, Version.version_definition_id)
    ).tuples():
        versions_of[title_id].append((version_id, definition_id))
    version_ids = [version_id for rows in versions_of.values() for version_id, _definition in rows]
    seasons = list(
        db.scalars(
            select(Season).where(Season.title_id.in_(title_ids)).order_by(Season.title_id, Season.number, Season.id)
        )
    )
    totals: dict[int, dict[str, int]] = defaultdict(lambda: {"episodes": 0, "aired": 0})
    for season_id, air_date in db.execute(
        select(Episode.season_id, Episode.air_date).where(
            Episode.title_id.in_(title_ids), Episode.tmdb_gone_at.is_(None)
        )
    ).tuples():
        totals[season_id]["episodes"] += 1
        if watching.aired(air_date, on):
            totals[season_id]["aired"] += 1
    switches = (
        {
            (row.season_id, row.version_id): bool(row.watched)
            for row in db.scalars(select(SeasonVersion).where(SeasonVersion.version_id.in_(version_ids)))
        }
        if version_ids
        else {}
    )
    counted: dict[tuple[int, int], dict[str, Any]] = defaultdict(
        lambda: {
            "expected": 0,
            "aired": 0,
            "have": 0,
            "upgrade": 0,
            "upgradable_files": 0,
            "problem": False,
            "downloading": False,
            "files": {},
        }
    )
    rows = db.execute(
        select(
            Episode.season_id,
            Episode.air_date,
            EpisodeVersion.version_id,
            EpisodeVersion.watched,
            EpisodeVersion.queue_state,
            EpisodeFile.id,
            EpisodeFile.size,
            EpisodeFile.cutoff_not_met,
            EpisodeFile.added_at,
        )
        .join(EpisodeVersion, EpisodeVersion.episode_id == Episode.id)
        .join(EpisodeFile, EpisodeFile.id == EpisodeVersion.episode_file_id, isouter=True)
        .where(Episode.title_id.in_(title_ids), Episode.tmdb_gone_at.is_(None))
    ).tuples()
    for season_id, air_date, version_id, watched, queue_state, file_id, size, cutoff_not_met, added_at in rows:
        entry = counted[(season_id, version_id)]
        entry["problem"] = entry["problem"] or queue_state == "problem"
        entry["downloading"] = entry["downloading"] or queue_state == "downloading"
        if file_id is not None:
            # By file: one file that holds two episodes counts once.
            entry["files"][file_id] = (int(size or 0), added_at)
            if cutoff_not_met:
                entry["upgradable_files"] += 1
        if not watched:
            continue
        entry["expected"] += 1
        if watching.aired(air_date, on):
            entry["aired"] += 1
            if file_id is not None:
                entry["have"] += 1
                if cutoff_not_met:
                    entry["upgrade"] += 1
    # The second halves of double episodes name their episode themselves.
    for season_id, version_id, file_id, size, added_at in db.execute(
        select(Episode.season_id, EpisodeFile.version_id, EpisodeFile.id, EpisodeFile.size, EpisodeFile.added_at)
        .join(Episode, Episode.id == EpisodeFile.part_of_episode_id)
        .where(EpisodeFile.part == 2, Episode.title_id.in_(title_ids), Episode.tmdb_gone_at.is_(None))
    ).tuples():
        counted[(season_id, version_id)]["files"][file_id] = (int(size or 0), added_at)

    files_of_version: dict[int, dict[int, datetime | None]] = defaultdict(dict)
    by_title: dict[int, list[Season]] = defaultdict(list)
    for season in seasons:
        by_title[season.title_id].append(season)
    for title_id in title_ids:
        result = []
        for season in by_title.get(title_id, []):
            season_versions = []
            for version_id, definition_id in versions_of.get(title_id, []):
                entry = counted[(season.id, version_id)]
                switch = switches.get((season.id, version_id))
                for file_id, (_size, added_at) in entry["files"].items():
                    files_of_version[version_id][file_id] = added_at
                season_versions.append(
                    {
                        "version_id": public.get(definition_id),
                        "state": series_state(
                            entry["expected"],
                            entry["aired"],
                            entry["have"],
                            entry["upgradable_files"],
                            entry["problem"],
                            entry["downloading"],
                            len(entry["files"]),
                        ),
                        "monitored": switch if switch is not None else entry["expected"] > 0,
                        "counts": {"have": entry["have"], "aired": entry["aired"], "expected": entry["expected"]},
                        "size_bytes": sum(size for size, _added in entry["files"].values()) if entry["files"] else None,
                        "imported_at": _iso(_oldest(added for _size, added in entry["files"].values())),
                    }
                )
            result.append(
                {
                    "season": season.number,
                    "name": season.name or None,
                    "air_date": season.air_date,
                    "episodes": totals[season.id]["episodes"],
                    "aired": totals[season.id]["aired"],
                    "versions": season_versions,
                }
            )
        found[title_id] = result
    for version_id in version_ids:
        oldest_of_version[version_id] = _oldest(files_of_version.get(version_id, {}).values())
    return found, oldest_of_version


def detail(db: OrmSession, title_id: int) -> dict[str, Any] | None:
    """The listed title (a series with its seasons, as everywhere), for an album its media, for an artist its
    catalogue."""
    item = items(db, [title_id]).get(title_id)
    if item is None:
        return None
    if item["kind"] == "album":
        item["album"]["media"] = album_media(db, title_id)
    elif item["kind"] == "artist":
        item["artist"]["catalogue"] = _catalogue(db, -title_id)
    return item


def album_media(db: OrmSession, title_id: int) -> list[dict[str, Any]]:
    """The media of the album's target release with their tracks in MusicBrainz's numbers, and per track its file.

    Empty while no target is chosen or its tracks are not loaded. A track's ``ref`` is MusicBrainz's track id, the one
    ``delete-files`` and assigning by hand name tracks by.
    """
    version = db.scalar(select(Version).where(Version.title_id == title_id).order_by(Version.id))
    if version is None or version.target_release_id is None:
        return []
    media = list(
        db.scalars(
            select(ReleaseMedium)
            .where(ReleaseMedium.release_id == version.target_release_id)
            .order_by(ReleaseMedium.position, ReleaseMedium.id)
        )
    )
    tracks: dict[int, list[ReleaseTrack]] = defaultdict(list)
    for track in db.scalars(
        select(ReleaseTrack)
        .where(ReleaseTrack.release_id == version.target_release_id)
        .order_by(ReleaseTrack.position, ReleaseTrack.id)
    ):
        tracks[track.medium_id].append(track)
    files: dict[int, TrackFile] = {}
    for row in db.scalars(select(TrackFile).where(TrackFile.version_id == version.id).order_by(TrackFile.id)):
        if row.track_id is not None:
            files.setdefault(row.track_id, row)
    return [
        {
            "position": medium.position,
            "format": medium.format,
            "tracks": [
                {
                    "ref": f"mbid:{track.mbid}" if track.mbid else None,
                    "number": track.number,
                    "position": track.position,
                    "name": track.name or None,
                    "length_ms": track.length_ms,
                    "file": (
                        {"quality": files[track.id].quality, "size_bytes": files[track.id].size}
                        if track.id in files
                        else None
                    ),
                }
                for track in tracks.get(medium.id, [])
            ],
        }
        for medium in media
    ]


def _catalogue(db: OrmSession, artist_id: int) -> list[dict[str, Any]]:
    """Every album of the artist, as a list names it, oldest first."""
    ids = list(
        db.scalars(
            select(Title.id)
            .where(Title.kind == "album", Title.artist_id == artist_id, Title.mb_gone_at.is_(None))
            .order_by(Title.first_release_date.nulls_last(), Title.id)
        )
    )
    rendered = items(db, ids)
    return [rendered[title_id] for title_id in ids if title_id in rendered]


def season_detail(db: OrmSession, title_id: int, number: int) -> dict[str, Any] | None:
    """The episodes of one season in TMDB's numbers, each with every version's state and file. None without it."""
    season = db.scalar(select(Season).where(Season.title_id == title_id, Season.number == number).order_by(Season.id))
    if season is None:
        return None
    on = watching.today()
    public = v1_versions.public_ids(db)
    versions = list(
        db.scalars(select(Version).where(Version.title_id == title_id).order_by(Version.version_definition_id))
    )
    definition_of = {version.id: version.version_definition_id for version in versions}
    episodes = list(
        db.scalars(
            select(Episode)
            .where(Episode.season_id == season.id, Episode.tmdb_gone_at.is_(None))
            .order_by(Episode.episode_number, Episode.id)
        )
    )
    ids = [episode.id for episode in episodes]
    links: dict[int, dict[int, EpisodeVersion]] = defaultdict(dict)
    file_ids: set[int] = set()
    if ids:
        for row in db.scalars(select(EpisodeVersion).where(EpisodeVersion.episode_id.in_(ids))):
            links[row.episode_id][row.version_id] = row
            if row.episode_file_id is not None:
                file_ids.add(row.episode_file_id)
    files = (
        {row.id: row for row in db.scalars(select(EpisodeFile).where(EpisodeFile.id.in_(file_ids)))} if file_ids else {}
    )
    # The second half of a double episode TMDB lists as one names its episode itself;
    # one query for the whole season.
    second_parts: dict[tuple[int, int], list[EpisodeFile]] = defaultdict(list)
    if ids and versions:
        for part in db.scalars(
            select(EpisodeFile)
            .where(
                EpisodeFile.part == 2,
                EpisodeFile.part_of_episode_id.in_(ids),
                EpisodeFile.version_id.in_([version.id for version in versions]),
            )
            .order_by(EpisodeFile.id)
        ):
            second_parts[(int(part.part_of_episode_id or 0), part.version_id)].append(part)
    items_out = []
    for episode in episodes:
        episode_versions = []
        for version in versions:
            row = links[episode.id].get(version.id)
            if row is None:
                continue
            file = files.get(row.episode_file_id) if row.episode_file_id is not None else None
            episode_versions.append(
                {
                    "version_id": public.get(definition_of[version.id]),
                    "state": series_detail._episode_state(
                        row, episode.air_date, on, bool(file is not None and file.cutoff_not_met)
                    ),
                    "monitored": bool(row.watched),
                    "size_bytes": int(file.size) if file is not None else None,
                    "quality": file.quality if file is not None else None,
                    "files": [
                        {"file_id": str(item.id), "size_bytes": int(item.size) if item.size is not None else None}
                        for item in ([file] if file is not None else []) + second_parts[(episode.id, version.id)]
                    ],
                }
            )
        items_out.append(
            {
                "episode": episode.episode_number,
                "name": episode.name or None,
                "air_date": episode.air_date,
                "aired": watching.aired(episode.air_date, on),
                "versions": episode_versions,
            }
        )
    return {"season": season.number, "name": season.name or None, "episodes": items_out}
