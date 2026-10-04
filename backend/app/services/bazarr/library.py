"""Movies, series, episodes and files in the shape Bazarr reads from Radarr v3 and Sonarr v4.

Only what Bazarr reads is filled in, and every field it reads without a fallback is always there: a missing
``alternateTitles`` or ``tvdbId`` makes Bazarr's whole sync stop at that title. Read from Bazarr 1.6's source
(``radarr/sync/parser.py``, ``sonarr/sync/parser.py``, 26.09.2026).

Only versions no source feeds are listed: a version fed by Radarr or Sonarr belongs to them, and Bazarr can ask them.
A series version needs its folder (``versions.relative_path``); one whose folder is not settled yet is left out.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ...models import Episode, EpisodeFile, EpisodeVersion, Tag, Title, TitleTag, Version, VersionDefinition
from ..releases import qualities
from ..subtitles import languages as language_table
from . import ids

#: What nexcrate tells Bazarr it is. Bazarr reads Radarr below 4 and Sonarr below 4 as legacy, and compares Sonarr's
#: version against 4.0.9.2421 to know that episodes carry their file. 5 passes every check for both.
COMPAT_VERSION = "5.0.0"
#: Tag numbers of title tags start here; below are the version definitions.
TITLE_TAG_OFFSET = 1_000_000
SERIES_TYPES = ("standard", "daily", "anime")
ENDED = ("ended", "canceled", "cancelled")
_RESOLUTION = re.compile(r"(\d{3,4})p", re.IGNORECASE)


def _english_names() -> dict[str, str]:
    names: dict[str, str] = {}
    for code, _codes, all_names in language_table.entries():
        if all_names:
            names[code] = all_names[0]
    return names


_LANGUAGE_NAMES = _english_names()


def language_name(code: str | None) -> str | None:
    return _LANGUAGE_NAMES.get(code) if code else None


def join(root: str, relative: str) -> str:
    """A path as nexcrate sees it: the root folder and the relative path, in the root's own style."""
    windows = "\\" in root and "/" not in root
    separator = "\\" if windows else "/"
    rest = relative.replace("/", "\\") if windows else relative.replace("\\", "/")
    return root.rstrip("/\\") + separator + rest.lstrip("/\\")


def tag_label(text: str) -> str:
    """A version's or a tag's name as Radarr writes tags: lower case, a dash for every run of other characters."""
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-") or "version"


def quality(name: str | None) -> dict[str, Any]:
    """Radarr's quality block. Bazarr splits the name at the dash into source and resolution."""
    label = name or "Unknown"
    known = qualities.BY_NAME.get(label)
    resolution = known.resolution if known is not None else 0
    if not resolution and (found := _RESOLUTION.search(label)):
        resolution = int(found.group(1))
    return {"quality": {"name": label, "resolution": resolution}, "revision": {"version": 1, "real": 0}}


def media_info(media: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(media, dict):
        return None
    video = media.get("video") if isinstance(media.get("video"), dict) else {}
    tracks = [track for track in media.get("audio") or [] if isinstance(track, dict)]
    out: dict[str, Any] = {}
    if video.get("codec"):
        out["videoCodec"] = video["codec"]
    if tracks and tracks[0].get("codec"):
        out["audioCodec"] = tracks[0]["codec"]
    return out or None


def _languages(names: Iterable[str] | None) -> list[dict[str, Any]]:
    return [{"id": index + 1, "name": name} for index, name in enumerate(names or []) if isinstance(name, str)]


def _original(title: Title) -> dict[str, Any] | None:
    name = language_name(title.original_language)
    return {"id": 0, "name": name} if name else None


def _images(kind: str, version_id: int) -> list[dict[str, Any]]:
    # Bazarr asks for the poster through its own address and adds the key; nexcrate answers it below the prefix.
    return [{"coverType": "poster", "url": f"/MediaCover/{version_id}/poster.jpg", "remoteUrl": None}]


# --- Tags --------------------------------------------------------------------------------------------------------- #


def tags(db: OrmSession, kind: str) -> list[dict[str, Any]]:
    """The version definitions of a kind and every title tag, as Radarr's tags."""
    rows = [
        {"id": definition_id, "label": tag_label(label)}
        for definition_id, label in db.execute(
            select(VersionDefinition.id, VersionDefinition.label)
            .where(VersionDefinition.kind == kind)
            .order_by(VersionDefinition.id)
        ).tuples()
    ]
    rows += [
        {"id": TITLE_TAG_OFFSET + tag_id, "label": tag_label(label)}
        for tag_id, label in db.execute(select(Tag.id, Tag.label).order_by(Tag.id)).tuples()
    ]
    return rows


def _title_tags(db: OrmSession, title_ids: Iterable[int]) -> dict[int, list[int]]:
    wanted = list(set(title_ids))
    found: dict[int, list[int]] = defaultdict(list)
    for start in range(0, len(wanted), 500):
        part = wanted[start : start + 500]
        for title_id, tag_id in db.execute(
            select(TitleTag.title_id, TitleTag.tag_id).where(TitleTag.title_id.in_(part))
        ).tuples():
            found[title_id].append(TITLE_TAG_OFFSET + tag_id)
    return found


# --- Movies ------------------------------------------------------------------------------------------------------- #


def _movie_query():  # type: ignore[no-untyped-def]
    return (
        select(Version, Title)
        .join(Title, Title.id == Version.title_id)
        .where(Title.kind == "movie", Version.source_id.is_(None))
        .order_by(Version.id)
    )


def movie_file_id(version: Version) -> int:
    return ids.file_id(version.id, version.relative_path or "", version.size or 0, version.file_ref)


def movie_path(version: Version) -> str | None:
    if not version.has_file or not version.relative_path or not version.root_folder:
        return None
    return join(version.root_folder, version.relative_path)


def _movie(version: Version, title: Title, title_tags: list[int]) -> dict[str, Any]:
    path = movie_path(version)
    folder = join(version.root_folder, version.relative_path.replace("\\", "/").rsplit("/", 1)[0]) if path else None
    out: dict[str, Any] = {
        "id": version.id,
        "title": title.title or "",
        "originalTitle": title.original_title or title.title or "",
        "sortTitle": (title.title or "").casefold(),
        "alternateTitles": [],
        "year": title.year or 0,
        "tmdbId": title.tmdb_id or 0,
        "imdbId": title.imdb_id or "",
        "overview": title.overview or "",
        "images": _images("movie", version.id),
        "monitored": bool(version.monitored),
        "hasFile": path is not None,
        "path": folder or (version.root_folder or ""),
        "rootFolderPath": version.root_folder or "",
        "qualityProfileId": version.version_definition_id,
        "tags": [version.version_definition_id, *title_tags],
        "originalLanguage": _original(title),
        "added": title.added.isoformat() if title.added else None,
    }
    if path is not None:
        movie_file: dict[str, Any] = {
            "id": movie_file_id(version),
            "movieId": version.id,
            "relativePath": version.relative_path.replace("\\", "/").rsplit("/", 1)[-1],
            "path": path,
            "size": version.size or 0,
            "quality": quality(version.quality),
            "languages": _languages(version.languages),
            "releaseGroup": version.release_group,
            "sceneName": version.release_title,
        }
        info = media_info(version.media_info)
        if info is not None:
            movie_file["mediaInfo"] = info
        out["movieFile"] = movie_file
        out["movieFileId"] = movie_file["id"]
    return out


def movies(db: OrmSession) -> list[dict[str, Any]]:
    rows = db.execute(_movie_query()).tuples().all()
    title_tags = _title_tags(db, (title.id for _version, title in rows))
    return [_movie(version, title, title_tags.get(title.id, [])) for version, title in rows]


def movie(db: OrmSession, version_id: int) -> dict[str, Any] | None:
    row = db.execute(_movie_query().where(Version.id == version_id)).tuples().first()
    if row is None:
        return None
    version, title = row
    return _movie(version, title, _title_tags(db, [title.id]).get(title.id, []))


# --- Series ------------------------------------------------------------------------------------------------------- #


def series_folder(version: Version) -> str | None:
    """The version's series folder as nexcrate sees it, or None while it is not settled (``folder_read``)."""
    relative = version.relative_path or ""
    if not relative or "/" in relative or "\\" in relative or not version.root_folder:
        return None
    return join(version.root_folder, relative)


def _series_query():  # type: ignore[no-untyped-def]
    return (
        select(Version, Title)
        .join(Title, Title.id == Version.title_id)
        .where(Title.kind == "series", Version.source_id.is_(None))
        .order_by(Version.id)
    )


def _series(version: Version, title: Title, folder: str, title_tags: list[int]) -> dict[str, Any]:
    series_type = (title.series_type or "standard").casefold()
    return {
        "id": version.id,
        "title": title.title or "",
        "sortTitle": (title.title or "").casefold(),
        "alternateTitles": [],
        "year": title.year or 0,
        "tvdbId": title.tvdb_id or 0,
        "tmdbId": title.tmdb_id or 0,
        "imdbId": title.imdb_id or "",
        "overview": title.overview or "",
        "images": _images("series", version.id),
        "monitored": bool(version.monitored),
        "path": folder,
        "rootFolderPath": version.root_folder or "",
        "seriesType": series_type if series_type in SERIES_TYPES else "standard",
        "ended": (title.series_status or "").casefold() in ENDED,
        "status": (title.series_status or "continuing").casefold(),
        "lastAired": title.last_air_date,
        "qualityProfileId": version.version_definition_id,
        "tags": [version.version_definition_id, *title_tags],
        "originalLanguage": _original(title),
        "added": title.added.isoformat() if title.added else None,
    }


def all_series(db: OrmSession) -> list[dict[str, Any]]:
    rows = [(v, t, folder) for v, t in db.execute(_series_query()).tuples() if (folder := series_folder(v)) is not None]
    title_tags = _title_tags(db, (title.id for _version, title, _folder in rows))
    return [_series(version, title, folder, title_tags.get(title.id, [])) for version, title, folder in rows]


def one_series(db: OrmSession, version_id: int) -> dict[str, Any] | None:
    row = db.execute(_series_query().where(Version.id == version_id)).tuples().first()
    if row is None:
        return None
    version, title = row
    folder = series_folder(version)
    if folder is None:
        return None
    return _series(version, title, folder, _title_tags(db, [title.id]).get(title.id, []))


@dataclass(frozen=True)
class SeriesVersion:
    version: Version
    title: Title
    folder: str


def series_version(db: OrmSession, version_id: int) -> SeriesVersion | None:
    row = db.execute(_series_query().where(Version.id == version_id)).tuples().first()
    if row is None:
        return None
    version, title = row
    folder = series_folder(version)
    return SeriesVersion(version, title, folder) if folder is not None else None


def episode_file_id(row: EpisodeFile) -> int:
    return ids.file_id(row.id, row.relative_path or "", row.size or 0, row.file_ref)


def _episode_file(row: EpisodeFile, series_id: int, folder: str, season: int) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": episode_file_id(row),
        "seriesId": series_id,
        "seasonNumber": season,
        "relativePath": row.relative_path,
        "path": join(folder, row.relative_path),
        "size": row.size or 0,
        "quality": quality(row.quality),
        "languages": _languages(row.languages),
        "releaseGroup": row.release_group,
        "sceneName": row.release_title,
    }
    info = media_info(row.media_info)
    if info is not None:
        out["mediaInfo"] = info
    return out


def _episode(
    owner: SeriesVersion,
    episode: Episode,
    link: EpisodeVersion,
    file_row: EpisodeFile | None,
    absolute: int | None,
) -> dict[str, Any]:
    number = ids.episode_id(owner.version.id, episode.id)
    out: dict[str, Any] = {
        "id": number,
        "seriesId": owner.version.id,
        "tvdbId": episode.tvdb_id or 0,
        "seasonNumber": episode.season_number,
        "episodeNumber": episode.episode_number,
        "title": episode.name or "",
        "airDate": episode.air_date,
        "overview": episode.overview or "",
        "monitored": bool(link.watched),
        "hasFile": file_row is not None,
        "episodeFileId": episode_file_id(file_row) if file_row is not None else 0,
        "series": {"id": owner.version.id, "title": owner.title.title or "", "year": owner.title.year or 0},
    }
    if absolute is not None:
        out["absoluteEpisodeNumber"] = absolute
    if file_row is not None:
        out["episodeFile"] = _episode_file(file_row, owner.version.id, owner.folder, episode.season_number)
    return out


def _absolute_numbers(db: OrmSession, title_id: int) -> dict[int, int]:
    from ...models import EpisodeNumber

    found: dict[int, int] = {}
    for episode_id, absolute in db.execute(
        select(EpisodeNumber.episode_id, EpisodeNumber.absolute).where(
            EpisodeNumber.title_id == title_id, EpisodeNumber.absolute.is_not(None)
        )
    ).tuples():
        found.setdefault(episode_id, absolute)
    return found


def episodes(db: OrmSession, version_id: int, *, episode_row_id: int | None = None) -> list[dict[str, Any]]:
    """The episodes of a series version (or the one named), each with its file when it has one."""
    owner = series_version(db, version_id)
    if owner is None:
        return []
    query = (
        select(Episode, EpisodeVersion, EpisodeFile)
        .join(EpisodeVersion, EpisodeVersion.episode_id == Episode.id)
        .outerjoin(
            EpisodeFile,
            (EpisodeFile.id == EpisodeVersion.episode_file_id) & (EpisodeFile.version_id == version_id),
        )
        .where(EpisodeVersion.version_id == version_id, Episode.title_id == owner.title.id)
        .order_by(Episode.season_number, Episode.episode_number, Episode.id)
    )
    if episode_row_id is not None:
        query = query.where(Episode.id == episode_row_id)
    absolute = _absolute_numbers(db, owner.title.id) if owner.title.series_type == "anime" else {}
    return [
        _episode(owner, episode, link, file_row, absolute.get(episode.id))
        for episode, link, file_row in db.execute(query).tuples()
    ]


def episode(db: OrmSession, number: int) -> dict[str, Any] | None:
    parts = ids.split_episode_id(number)
    if parts is None:
        return None
    found = episodes(db, parts[0], episode_row_id=parts[1])
    return found[0] if found else None


def episode_files(db: OrmSession, version_id: int) -> list[dict[str, Any]]:
    """The files of a series version that hold an episode, each once."""
    seen: dict[int, dict[str, Any]] = {}
    for item in episodes(db, version_id):
        file = item.get("episodeFile")
        if file is not None:
            seen.setdefault(file["id"], file)
    return list(seen.values())


def episode_file(db: OrmSession, number: int) -> dict[str, Any] | None:
    row = db.get(EpisodeFile, ids.row_of_file_id(number))
    if row is None or episode_file_id(row) != number:
        return None
    for item in episode_files(db, row.version_id):
        if item["id"] == number:
            return item
    return None


def root_folders(db: OrmSession, kind: str) -> list[dict[str, Any]]:
    """The root folders of the versions Bazarr sees, numbered by first appearance."""
    query = _movie_query() if kind == "movie" else _series_query()
    folders: list[str] = []
    for version, _title in db.execute(query).tuples():
        if version.root_folder and version.root_folder not in folders:
            folders.append(version.root_folder)
    return [{"id": index + 1, "path": path, "accessible": True, "freeSpace": 0} for index, path in enumerate(folders)]
