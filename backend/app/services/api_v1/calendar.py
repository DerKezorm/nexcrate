"""The calendar through ``/api/v1`` (N34): the same entries the calendar page shows, in the six
rules of the contract.

A movie comes with its cinema, digital and disc dates (``date_kind``: ``theatrical``, ``digital``, ``physical``, as the
anchor of the automatic names them), an episode with the day it airs (``air``) and its season and episode under
``series`` in TMDB's numbers. Every entry carries the state of each version: of the movie, or of that one episode.
TMDB gives no time of day for an episode, so nexcrate has none to pass on.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ...models import Episode, EpisodeFile, EpisodeVersion, Version
from .. import release_calendar
from ..series import detail as series_detail
from ..series import watching
from . import TITLE_KINDS, titles
from . import versions as v1_versions

#: The kinds of entry of the calendar service and the ``kind`` of the title they belong to.
_ENTRY_KINDS = {"movie": "movie", "series": "episode", "album": "album"}


def _episode_versions(
    db: OrmSession, episode_ids: Iterable[int], public: dict[int, str]
) -> dict[int, list[dict[str, Any]]]:
    """Per episode the state of every version, as ``/titles/series/{ref}/seasons/{season}`` gives it."""
    ids = list(episode_ids)
    found: dict[int, list[dict[str, Any]]] = defaultdict(list)
    if not ids:
        return found
    on = watching.today()
    episodes = {row.id: row for row in db.scalars(select(Episode).where(Episode.id.in_(ids)))}
    rows = list(db.scalars(select(EpisodeVersion).where(EpisodeVersion.episode_id.in_(ids))))
    definitions = dict(
        db.execute(
            select(Version.id, Version.version_definition_id).where(Version.id.in_({row.version_id for row in rows}))
        )
        .tuples()
        .all()
    ) if rows else {}
    file_ids = {row.episode_file_id for row in rows if row.episode_file_id is not None}
    files = (
        {row.id: row for row in db.scalars(select(EpisodeFile).where(EpisodeFile.id.in_(file_ids)))} if file_ids else {}
    )
    for row in sorted(rows, key=lambda item: definitions.get(item.version_id, 0)):
        episode = episodes.get(row.episode_id)
        if episode is None:
            continue
        file = files.get(row.episode_file_id) if row.episode_file_id is not None else None
        found[row.episode_id].append(
            {
                "version_id": public.get(definitions.get(row.version_id, 0)),
                "state": series_detail._episode_state(
                    row, episode.air_date, on, bool(file is not None and file.cutoff_not_met)
                ),
                "monitored": bool(row.watched),
            }
        )
    return found


def _episode_ids(db: OrmSession, entries: list[dict[str, Any]]) -> dict[tuple[int, int, int], int]:
    wanted = {(entry["title_id"], entry["season"], entry["episode"]) for entry in entries if entry["kind"] == "episode"}
    if not wanted:
        return {}
    title_ids = {item[0] for item in wanted}
    return {
        (title_id, season, number): episode_id
        for episode_id, title_id, season, number in db.execute(
            select(Episode.id, Episode.title_id, Episode.season_number, Episode.episode_number).where(
                Episode.title_id.in_(title_ids), Episode.tmdb_gone_at.is_(None)
            )
        ).tuples()
        if (title_id, season, number) in wanted
    }


def entries(
    db: OrmSession,
    span: release_calendar.Span,
    *,
    kind: str | None,
    region: str,
    only_monitored: bool = False,
    only_missing: bool = False,
) -> tuple[list[dict[str, Any]], bool]:
    """The entries of the span in the contract's shape, and whether the span held more than one answer carries."""
    wanted = tuple(_ENTRY_KINDS[item] for item in ((kind,) if kind else TITLE_KINDS))
    found, cut = release_calendar.entries(
        db, span, kinds=wanted, region=region, only_monitored=only_monitored, only_missing=only_missing
    )
    rendered = titles.items(db, {entry["title_id"] for entry in found})
    public = v1_versions.public_ids(db)
    episode_ids = _episode_ids(db, found)
    per_episode = _episode_versions(db, episode_ids.values(), public)
    items = []
    for entry in found:
        title = rendered.get(entry["title_id"])
        if title is None:
            continue
        item: dict[str, Any] = {
            "kind": title["kind"],
            "ref": title["ref"],
            "name": title["name"],
            "year": title["year"],
            "date": entry["day"],
            "date_kind": entry["occasion"],
            "country": entry.get("country"),
            "monitored": bool(entry["monitored"]),
        }
        if title["kind"] == "series":
            episode_id = episode_ids.get((entry["title_id"], entry["season"], entry["episode"]))
            item["versions"] = per_episode.get(episode_id, []) if episode_id is not None else []
            item["series"] = {
                "season": entry["season"],
                "episode": entry["episode"],
                "name": entry.get("episode_title"),
            }
        else:
            item["versions"] = [
                {"version_id": version["version_id"], "state": version["state"], "monitored": version["monitored"]}
                for version in title["versions"]
            ]
        if title["kind"] == "album":
            item["album"] = {"artists": title["album"]["artists"], "type": title["album"]["type"]}
        items.append(item)
    return items, cut
