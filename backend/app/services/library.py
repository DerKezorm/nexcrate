"""The merged library: list with filters and search, stats, the detail of one title, adding titles.

The search goes through ``schreibweisen``: the forms of the query are looked up in the stored
keys of the title, the original title, every alternate title from a source and the TMDB
alternative and translated titles, so "schone", "schoene" and "SCHÖNE" find the same movie, and a
German title is found when Radarr runs in English.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, exists, func, or_, select, union_all
from sqlalchemy.orm import Session as OrmSession

from ..models import (
    AlternateTitle,
    Artist,
    Download,
    DownloadClient,
    Episode,
    EpisodeFile,
    EpisodeVersion,
    HistoryEntry,
    PendingRelease,
    Source,
    Title,
    TrackFile,
    Version,
    VersionDefinition,
)
from . import images, judging, schreibweisen, tags, tmdb
from .automatic import clock as automatic_clock
from .automatic import planning as automatic_planning
from .downloads import store as download_store
from .music import detail as music_detail
from .profiles import store as profile_store
from .releases import qualities
from .series import detail as series_detail
from .series import watching
from .subtitles import records as subtitle_records

SORTS = ("title", "year", "added")
PAGE_SIZE_DEFAULT = 60
PAGE_SIZE_MAX = 200


def _search(query: str | None) -> ColumnElement[bool] | None:
    forms = schreibweisen.query_keys(query)
    if not forms:
        return None
    in_title = [Title.search_keys.contains(form, autoescape=True) for form in forms]
    in_title.extend(Title.tmdb_search_keys.contains(form, autoescape=True) for form in forms)
    in_alternates = exists().where(
        AlternateTitle.title_id == Title.id,
        or_(*(AlternateTitle.search_keys.contains(form, autoescape=True) for form in forms)),
    )
    return or_(*in_title, in_alternates)


#: Not a version state: series with files that belong to no episode yet (plan S6, U3). The owner assigns them on the
#: series page; the filter finds the series.
UNCLEAR = "unclear"


def _unclear_conditions() -> tuple[ColumnElement[bool], ...]:
    """A file that is not left out and holds no episode: unclear (the same rule as ``series.unclear``)."""
    linked = select(EpisodeVersion.episode_file_id).where(EpisodeVersion.episode_file_id.is_not(None))
    # The second half of a double episode names its episode itself and is no unclear file.
    return (EpisodeFile.left_out.is_(False), EpisodeFile.id.not_in(linked), EpisodeFile.part_of_episode_id.is_(None))


def _unclear_files() -> Any:
    """The versions with at least one unclear file, as a subquery: episode files of a series, and since Music M6 files
    of an album folder without a track the owner has not settled (``track_files.unclear``)."""
    return union_all(
        select(EpisodeFile.version_id).where(*_unclear_conditions()),
        select(TrackFile.version_id).where(TrackFile.unclear.is_(True)),
    )


def _unclear_titles(db: OrmSession, kind: str) -> int:
    return int(
        db.scalar(
            select(func.count(func.distinct(Version.title_id)))
            .join(Title, Title.id == Version.title_id)
            .where(Title.kind == kind, Version.id.in_(_unclear_files()))
        )
        or 0
    )


def list_titles(
    db: OrmSession,
    *,
    kind: str = "movie",
    state: str | None = None,
    query: str | None = None,
    sort: str = "title",
    page: int = 1,
    page_size: int = PAGE_SIZE_DEFAULT,
    tag: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """One page of titles and the total count. ``state`` keeps titles with a version in that state."""
    # ⚠️ The state is a list, not an EXISTS per title: for the EXISTS SQLite took the index on ``state`` and walked
    # every version in that state once per title. With 50,000 albums the page did not come within five minutes and
    # held all cores (measured on the bench in real size, 18.09.2026); as a list it takes 25 ms.
    conditions = title_conditions(kind, state, query, tag)

    total = int(db.scalar(select(func.count(Title.id)).where(*conditions)) or 0)
    if sort == "year":
        order = (Title.year.desc().nulls_last(), Title.sort_key, Title.id)
    elif sort == "added":
        order = (Title.added.desc(), Title.id.desc())
    else:
        order = (Title.sort_key, Title.year, Title.id)
    size = max(1, min(page_size, PAGE_SIZE_MAX))
    rows = list(
        db.scalars(select(Title).where(*conditions).order_by(*order).offset((max(1, page) - 1) * size).limit(size))
    )
    briefs = _briefs(db, [row.id for row in rows])
    artists = _artist_names(db, [row.artist_id for row in rows if row.artist_id]) if kind == "album" else {}
    labels = (
        tags.album_tags(db, rows) if kind == "album" else tags.of_titles(db, [row.id for row in rows])
    )
    items = [
        {
            "id": row.id,
            "kind": row.kind,
            "title": row.title,
            "year": row.year,
            "poster_url": images.poster_url(row),
            "versions": briefs.get(row.id, []),
            "artist": artists.get(row.artist_id) if row.artist_id else None,
            "tags": labels.get(row.id, []),
        }
        for row in rows
    ]
    return items, total


def title_conditions(
    kind: str, state: str | None, query: str | None, tag: str | None = None
) -> list[ColumnElement[bool]]:
    """The conditions of one view of the library, for the listing and for what acts on all of it. ``tag`` keeps
    the titles carrying it; an album carries the tags of its artist."""
    conditions: list[ColumnElement[bool]] = [Title.kind == kind]
    label = tags.clean(tag) if tag else None
    if label is not None:
        if kind == "album":
            conditions.append(Title.artist_id.in_(tags.artist_ids_with(label)))
        else:
            conditions.append(Title.id.in_(tags.title_ids_with(label)))
    if kind == "album":
        conditions.append(exists().where(Version.title_id == Title.id))
    if state == UNCLEAR:
        conditions.append(exists().where(Version.title_id == Title.id, Version.id.in_(_unclear_files())))
    elif state:
        conditions.append(Title.id.in_(select(Version.title_id).where(Version.state == state)))
    search = _search(query)
    if search is not None:
        conditions.append(search)
    return conditions


def set_monitored(
    db: OrmSession,
    *,
    kind: str,
    state: str | None,
    query: str | None,
    monitored: bool,
    moment: datetime,
    title_ids: list[int] | None = None,
    tag: str | None = None,
) -> int:
    """Watch these versions, or leave them alone (the owner's finding of 20.09.2026).

    With ``title_ids`` exactly those titles, otherwise every title of the view. Only versions of nexcrate's own:
    what a source feeds is watched there. Returns how many changed.
    """
    if title_ids is not None:
        titles = select(Title.id).where(Title.id.in_(title_ids), Title.kind == kind)
    else:
        titles = select(Title.id).where(*title_conditions(kind, state, query, tag))
    rows = list(
        db.scalars(
            select(Version).where(
                Version.title_id.in_(titles), Version.source_id.is_(None), Version.monitored.is_(not monitored)
            )
        )
    )
    for version in rows:
        version.monitored = monitored
        version.updated_at = moment
    db.flush()
    for version in rows:
        download_store.follow_version(db, version.title_id, version.version_definition_id, moment)
    return len(rows)


def series_versions_of(
    db: OrmSession, *, state: str | None, query: str | None, title_ids: list[int] | None = None,
    tag: str | None = None,
) -> list[Version]:
    """The series versions of nexcrate's own in this view, for a rule that is set for several series at once."""
    titles = (
        select(Title.id).where(Title.id.in_(title_ids), Title.kind == "series")
        if title_ids is not None
        else select(Title.id).where(*title_conditions("series", state, query, tag))
    )
    return list(db.scalars(select(Version).where(Version.title_id.in_(titles), Version.source_id.is_(None))))


def series_titles_of(
    db: OrmSession, *, state: str | None, query: str | None, title_ids: list[int] | None = None,
    tag: str | None = None,
) -> list[Title]:
    """The series of this view, for a change that is made for several of them at once (B1)."""
    conditions = (
        [Title.id.in_(title_ids), Title.kind == "series"]
        if title_ids is not None
        else title_conditions("series", state, query, tag)
    )
    return list(db.scalars(select(Title).where(*conditions).order_by(Title.id)))


def fed_series_ids(db: OrmSession, title_ids: list[int]) -> set[int]:
    """Of these series the ones a live Sonarr connection feeds.

    ⚠️ Such a series takes Sonarr's type on every run (A6), so setting the type here would be
    undone at the next run without anyone noticing. A source that was taken over feeds nothing any more.
    """
    if not title_ids:
        return set()
    return set(
        db.scalars(
            select(Version.title_id)
            .join(Source, Source.id == Version.source_id)
            .where(Version.title_id.in_(title_ids), Source.taken_over_at.is_(None), Source.app == "sonarr")
        )
    )


def _artist_names(db: OrmSession, artist_ids: list[int]) -> dict[int, str]:
    if not artist_ids:
        return {}
    return dict(db.execute(select(Artist.id, Artist.name).where(Artist.id.in_(artist_ids))).tuples().all())


def _briefs(db: OrmSession, title_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """The versions of one page of titles, in one query for the whole page.

    ``quality`` and ``size_bytes`` are null without a file: a version without one stores size 0,
    and a list showing "0 B" would claim an empty file.
    """
    if not title_ids:
        return {}
    result: dict[int, list[dict[str, Any]]] = {}
    unclear = dict(
        db.execute(
            select(EpisodeFile.version_id, func.count(EpisodeFile.id))
            .join(Version, Version.id == EpisodeFile.version_id)
            .where(Version.title_id.in_(title_ids), *_unclear_conditions())
            .group_by(EpisodeFile.version_id)
        )
        .tuples()
        .all()
    )
    for version_id, count in db.execute(
        select(TrackFile.version_id, func.count(TrackFile.id))
        .join(Version, Version.id == TrackFile.version_id)
        .where(Version.title_id.in_(title_ids), TrackFile.unclear.is_(True))
        .group_by(TrackFile.version_id)
    ).tuples():
        unclear[version_id] = unclear.get(version_id, 0) + count
    rows = db.execute(
        select(
            Version.id,
            Version.title_id,
            VersionDefinition.label,
            Version.state,
            Version.progress,
            Version.has_file,
            Version.quality,
            Version.size,
            Version.episode_counts,
            Version.monitored,
        )
        .join(VersionDefinition, VersionDefinition.id == Version.version_definition_id)
        .where(Version.title_id.in_(title_ids))
        .order_by(Version.title_id, VersionDefinition.id)
    )
    for row in rows:
        counts = series_detail.counts_from(row.episode_counts)
        result.setdefault(row.title_id, []).append(
            {
                "id": row.id,
                "label": row.label,
                "state": row.state,
                "progress": row.progress,
                "quality": row.quality if row.has_file else None,
                # A series version shows the size of its files; a movie version without a file none.
                "size_bytes": row.size if row.has_file or (counts is not None and row.size) else None,
                "counts": counts,
                "unclear_files": int(unclear.get(row.id, 0)),
                # The eye in the list (the owner's finding of 20.09.2026): a version nexcrate leaves alone.
                "monitored": bool(row.monitored),
            }
        )
    return result


def add_owner_version(db: OrmSession, title: Title, definition: VersionDefinition, moment: datetime) -> Version:
    """A version the owner wants: no source, no file, wanted. History gets ``added``."""
    version = Version(
        title_id=title.id,
        version_definition_id=definition.id,
        source_id=None,
        added_by="owner",
        monitored=True,
        has_file=False,
        state="wanted",
        created_at=moment,
        updated_at=moment,
    )
    db.add(version)
    db.flush()
    db.add(
        HistoryEntry(
            title_id=title.id,
            version_id=version.id,
            version_definition_id=definition.id,
            version_label=definition.label,
            event="added",
            at=moment,
            detail=None,
        )
    )
    return version


def add_title(db: OrmSession, data: tmdb.MovieData, definitions: list[VersionDefinition], moment: datetime) -> Title:
    """A movie the owner adds: its data from TMDB, no source, one wanted version per definition."""
    title = Title(kind="movie", tmdb_id=data.tmdb_id, added=moment, updated_at=moment)
    tmdb.apply_movie_data(title, data, moment)
    db.add(title)
    db.flush()
    for definition in definitions:
        add_owner_version(db, title, definition, moment)
    db.flush()
    return title


def in_library(db: OrmSession, tmdb_ids: list[int], kind: str = "movie") -> dict[int, tuple[int, list[int]]]:
    """Titles of a kind already in the library: TMDB id to title id and the version definition ids it has."""
    if not tmdb_ids:
        return {}
    found: dict[int, tuple[int, list[int]]] = {}
    rows = db.execute(
        select(Title.tmdb_id, Title.id, Version.version_definition_id)
        .outerjoin(Version, Version.title_id == Title.id)
        .where(Title.kind == kind, Title.tmdb_id.in_(tmdb_ids))
        .order_by(Title.id, Version.version_definition_id)
    )
    for row in rows:
        _title_id, definitions = found.setdefault(row.tmdb_id, (row.id, []))
        if row.version_definition_id is not None:
            definitions.append(row.version_definition_id)
    return found


def _state_titles(db: OrmSession) -> dict[str, dict[str, int]]:
    """Per kind and state the titles with a version in that state: the number at each chip of the state filter."""
    found: dict[str, dict[str, int]] = {kind: {} for kind in ("movie", "series", "album")}
    rows = db.execute(
        select(Title.kind, Version.state, func.count(func.distinct(Version.title_id)))
        .join(Title, Title.id == Version.title_id)
        .group_by(Title.kind, Version.state)
    ).tuples()
    for kind, state, count in rows:
        if kind in found and state:
            found[kind][state] = int(count)
    return found


def stats(db: OrmSession) -> dict[str, Any]:
    counts = dict(db.execute(select(Title.kind, func.count(Title.id)).group_by(Title.kind)).tuples().all())
    # Albums count like the library lists them: with a version (M1, decision 2); the catalogue behind them does not.
    albums = db.scalar(
        select(func.count(Title.id)).where(Title.kind == "album", exists().where(Version.title_id == Title.id))
    )
    # Eine Serie ist eine Zeile, ihre Folgen sind die Arbeit; dasselbe gilt fuer Kuenstler und Alben. Beide Zahlen
    # stehen in der Kachel (Rueckmeldung 20.09.2026).
    episodes = db.scalar(select(func.count(Episode.id)))
    artists = db.scalar(select(func.count(Artist.id)))
    return {
        "movies": int(counts.get("movie", 0)),
        "series": int(counts.get("series", 0)),
        "episodes": int(episodes or 0),
        "artists": int(artists or 0),
        "albums": int(albums or 0),
        "size_bytes": int(db.scalar(select(func.coalesce(func.sum(Version.size), 0))) or 0),
        "problems": int(db.scalar(select(func.count(Version.id)).where(Version.state == "problem")) or 0),
        "problem_titles": int(
            db.scalar(select(func.count(func.distinct(Version.title_id))).where(Version.state == "problem")) or 0
        ),
        "unclear_titles": _unclear_titles(db, "series"),
        "unclear_albums": _unclear_titles(db, "album"),
        "state_titles": _state_titles(db),
    }


_DOWNLOAD_IN_DETAIL = re.compile(r"(?:^|\s)download=(\d+)(?:\s|$)")


def _filed_download(db: OrmSession, title_id: int, entry: HistoryEntry) -> int | None:
    """The album download an ``album_filed`` entry is about: named in the detail since 19.09.2026, before that the
    album download of the title finished within a few seconds of the entry."""
    named = _DOWNLOAD_IN_DETAIL.search(entry.detail or "")
    if named is not None:
        return int(named.group(1))
    return db.scalar(
        select(Download.id)
        .where(
            Download.title_id == title_id,
            Download.scope == "album",
            Download.imported_at.between(entry.at - timedelta(seconds=5), entry.at + timedelta(seconds=5)),
        )
        .order_by(Download.id.desc())
        .limit(1)
    )


def _download_brief(db: OrmSession, title_id: int, definition_id: int) -> dict[str, Any] | None:
    """nexcrate's download of a version that is not finished and still counts: active, or a problem for the owner."""
    row = download_store.blocking_download(db, title_id, definition_id)
    if row is None:
        return None
    client = db.get(DownloadClient, row.client_id) if row.client_id is not None else None
    return {
        "id": row.id,
        "state": row.state,
        "progress": row.progress,
        "problem_code": row.problem_code,
        "client_name": client.name if client is not None else None,
    }


def _waiting(db: OrmSession, title_id: int, definition_id: int) -> dict[str, Any] | None:
    """The releases that wait out the delay of this version, as a line for its card: how many, and from when the
    first of them may load."""
    count, due = db.execute(
        select(func.count(PendingRelease.id), func.min(PendingRelease.due_at)).where(
            PendingRelease.title_id == title_id, PendingRelease.version_definition_id == definition_id
        )
    ).one()
    if not count:
        return None
    # ``due``: the first one has waited long enough; it loads with the next look of the job, so no moment is named.
    return {"count": int(count), "due_at": due, "due": due <= automatic_clock.now()}


def _last_failure(db: OrmSession, title_id: int, definition_id: int) -> dict[str, Any] | None:
    """The newest failed download of this version the owner has not taken off, as a line for its card (the owner's
    finding of 20.09.2026: a failure was only in the history). Its release is on the blocklist."""
    row = db.scalar(
        select(Download)
        .where(
            Download.title_id == title_id,
            Download.version_definition_id == definition_id,
            Download.state == "failed",
            Download.cleared_at.is_(None),
        )
        .order_by(Download.updated_at.desc(), Download.id.desc())
        .limit(1)
    )
    if row is None:
        return None
    return {"id": row.id, "at": row.updated_at, "reason": row.failed_reason or "client_failed"}


def location(version: Version, version_folder: str | None, series: bool = False) -> dict[str, str] | None:
    """Where the movie of a version lies or goes (finding 13), read from the database only.

    * ``radarr``: a version a source feeds, with Radarr's movie folder as Radarr sees it; None before an import kept it.
    * ``file``: a version of nexcrate's own with a file, with the folder holding it (``root_folder`` joined with every
      part of ``relative_path`` but the last); None when either is not stored.
    * ``target``: a version of nexcrate's own without a file, with where its next file goes: ``root_folder`` when set,
      else the version folder; None when there is neither. Filing away still falls back to the version folder when
      nexcrate cannot see or write into ``root_folder``; nothing on disk is looked at here.
    """
    if version.source_id is not None:
        if version.source_series_path:
            # A series version a Sonarr connection feeds: Sonarr's series folder.
            return {"kind": "sonarr", "path": version.source_series_path}
        return {"kind": "radarr", "path": version.source_movie_path} if version.source_movie_path else None
    if series:
        # An own series version (S4.4): its series folder once the first file lies in it.
        names = [name for name in (version.relative_path or "").replace("\\", "/").split("/") if name]
        if version.root_folder and names:
            return {"kind": "file", "path": _below(version.root_folder, names)}
        target = version.root_folder or version_folder
        return {"kind": "target", "path": target} if target else None
    if version.has_file:
        names = [name for name in (version.relative_path or "").replace("\\", "/").split("/") if name]
        if not version.root_folder or not names:
            return None
        return {"kind": "file", "path": _below(version.root_folder, names[:-1])}
    target = version.root_folder or version_folder
    return {"kind": "target", "path": target} if target else None


def companion_of(version: Version) -> dict[str, Any] | None:
    """The state of the version's ``release.nex``; None for a version a source feeds or one never looked at."""
    if version.source_id is not None or not version.companion_state:
        return None
    return {"state": version.companion_state, "written_at": version.companion_written_at}


def _below(folder: str, names: list[str]) -> str:
    """A path below a stored folder, written with that folder's separator."""
    if not names:
        return folder
    separator = "\\" if "\\" in folder and "/" not in folder else "/"
    return folder.rstrip("/\\") + separator + separator.join(names)


def target_items(upgrade_to: str | None) -> list[str]:
    """A stored target of several qualities ("Bluray-1080p, WEBDL-1080p") as items, so the interface reads it as it
    reads nexcrate's own targets; anything else is no list."""
    if not upgrade_to or ", " not in upgrade_to:
        return []
    parts = [part.strip() for part in upgrade_to.split(",")]
    return parts if parts and all(part in qualities.BY_NAME for part in parts) else []


def upgrade_facts(version: Version, rules: dict[str, Any] | None, original_language: str | None) -> dict[str, Any]:
    """Why a version's file can still be upgraded (decision 20), read from the database and the rules, nothing written.

    A version a source feeds keeps what the source stored: its ``upgrade_to``, no reason and no score. A version of
    nexcrate's own is judged by its profile as ``judging`` judges it; reason and target show only while the stored
    judgement says the file can still be upgraded, so they never contradict the state.
    """
    if version.source_id is not None:
        return {
            "upgrade_to": version.upgrade_to,
            "upgrade_to_items": target_items(version.upgrade_to),
            "upgrade_reason": None,
            "current_score": None,
            "upgrade_until": None,
        }
    found = judging.judgement(rules, version, original_language)
    if found is None:
        return {
            "upgrade_to": None,
            "upgrade_to_items": [],
            "upgrade_reason": None,
            "current_score": None,
            "upgrade_until": None,
        }
    reason = found.reason if version.cutoff_not_met else None
    return {
        "upgrade_to": found.target if reason is not None else None,
        "upgrade_to_items": list(found.target_items) if reason is not None else [],
        "upgrade_reason": reason,
        "current_score": found.score,
        "upgrade_until": found.upgrade_until,
    }


def _rules_of(db: OrmSession, definition_ids: set[int]) -> dict[int, dict[str, Any] | None]:
    if not definition_ids:
        return {}
    return {
        version_id: judging.usable_rules(profile)
        for version_id, profile in profile_store.by_versions(db, sorted(definition_ids)).items()
    }


def detail(db: OrmSession, title_id: int) -> dict[str, Any] | None:
    title = db.get(Title, title_id)
    if title is None:
        return None
    # ⚠️ The set removing the version or the title looks at, hints included; ``download`` shows only what blocks loading.
    pending = [row.version_definition_id for row in download_store.pending_downloads(db, title_id)]
    rows = (
        db.execute(
            select(Version, VersionDefinition.label, VersionDefinition.folder, Source.name)
            .join(VersionDefinition, VersionDefinition.id == Version.version_definition_id)
            .outerjoin(Source, Source.id == Version.source_id)
            .where(Version.title_id == title_id)
            .order_by(VersionDefinition.id)
        )
        .tuples()
        .all()
    )
    rules = _rules_of(db, {version.version_definition_id for version, _label, _folder, _source in rows})
    versions = [
        {
            "id": version.id,
            "version_id": version.version_definition_id,
            "label": label,
            "profile_name": version.profile_name,
            "root_folder": version.root_folder,
            "state": version.state,
            "quality": version.quality,
            **upgrade_facts(version, rules.get(version.version_definition_id), title.original_language),
            "size_bytes": version.size,
            "languages": list(version.languages or []),
            "release_group": version.release_group,
            "relative_path": version.relative_path,
            "source_name": source_name,
            "added_by": version.added_by,
            # A program that asked for the version through /api/v1: its reference and key.
            "origin": version.origin,
            "origin_key": version.origin_key,
            # Whether nexcrate watches this version (the owner's finding of 20.09.2026); for a version a source
            # feeds it is what the source says.
            "monitored": bool(version.monitored),
            "progress": version.progress,
            "problem_code": version.problem_code,
            "download": _download_brief(db, title_id, version.version_definition_id),
            # A failed download waits at the version until the owner takes it off (finding of 20.09.2026).
            "last_failure": _last_failure(db, title_id, version.version_definition_id)
            if version.version_definition_id is not None
            else None,
            # Releases the automatic keeps instead of loading them, until the delay of the version is over.
            "waiting": _waiting(db, title_id, version.version_definition_id)
            if version.version_definition_id is not None
            else None,
            "pending_downloads": pending.count(version.version_definition_id),
            "location": location(version, version_folder, series=title.kind == "series"),
            # The subtitle files nexcrate placed next to the version's file (C9).
            "subtitles": subtitle_records.listed(db, version),
            # The library from disk: release.nex, where the quality came from, media.
            "companion": companion_of(version),
            "quality_from": version.quality_from,
            "media": version.media_info,
            # Series: what the version watches and its counts; null for a movie.
            "watch": series_detail.version_watch(db, version) if title.kind == "series" else None,
            "counts": series_detail.counts_from(version.episode_counts) if title.kind == "series" else None,
            # Episodes only the feeding Sonarr knows, mostly specials (decision 47).
            "source_only_episodes": series_detail.source_only_episodes(db, version) if title.kind == "series" else [],
        }
        for version, label, version_folder, source_name in rows
    ]
    entries = list(
        db.execute(
            select(HistoryEntry, VersionDefinition.label)
            .outerjoin(VersionDefinition, VersionDefinition.id == HistoryEntry.version_definition_id)
            .where(HistoryEntry.title_id == title_id)
            .order_by(HistoryEntry.at.desc(), HistoryEntry.id.desc())
        ).tuples()
    )
    history = [
        {
            "at": entry.at,
            "event": entry.event,
            "version": label or entry.version_label,
            "detail": entry.detail,
            "download_id": _filed_download(db, title_id, entry) if entry.event == "album_filed" else None,
        }
        for entry, label in entries
    ]
    labels = tags.album_tags(db, [title]) if title.kind == "album" else tags.of_titles(db, [title.id])
    return {
        "tags": labels.get(title.id, []),
        "id": title.id,
        "kind": title.kind,
        "title": title.title,
        "year": title.year,
        "poster_url": images.poster_url(title),
        "original_title": title.original_title,
        "runtime_min": title.runtime,
        "genres": list(title.genres or []),
        "overview": title.overview,
        "tmdb_id": title.tmdb_id,
        "imdb_id": title.imdb_id,
        "versions": versions,
        "history": history,
        "search_plan": automatic_planning.search_plan(db, title, automatic_clock.now()),
        "series": (
            series_detail.series_block(db, title, [version for version, *_rest in rows], watching.today())
            if title.kind == "series"
            else None
        ),
        # Music (decision 40): the credit, the target release, its tracks, the releases.
        "album": (
            music_detail.album_block(db, title, [version for version, *_rest in rows])
            if title.kind == "album"
            else None
        ),
    }
