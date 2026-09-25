"""How releases count a series: the owner's search titles and corrections, the chosen TMDB episode group, TheXEM's state
(S3.2, decisions 11 to 13).

* **Search titles** (``title_aliases``): asked for first and matched like the series' own titles. Sonarr has no local
  aliases (issue #8058).
* **Corrections** (scheme ``owner``): "in releases this episode is called S02E01", one row per episode, a range for a
  double episode. They come first when releases are matched and never make a match ambiguous. The owner replaces
  them as a whole; "count on" is the interface's work. For an anime series a correction may carry the number the
  episode is counted through by instead of, or beside, season and episode (B6). Sonarr has no
  such field; there the number only comes from TheXEM. It goes before every other source of the scheme ``absolute``.
* **Scene numbering** (``titles.use_scene_numbering``): the owner may switch the scene numbering of a series off, as
  Sonarr's ``useSceneNumbering``; the numbers stay stored and count again once it is on.
* **Episode group** (scheme ``group``, origin ``tmdb``): TMDB episode groups are free lists; one only counts once the
  owner chose it. Its seasons are the group's parts in their order, leaving out a part named like specials or with
  order 0; an episode's number is its place in the part (the rule measured at B1, 16.09.2026).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session as OrmSession

from ...models import Episode, EpisodeNumber, Title, TitleAlias
from .. import schreibweisen
from . import absolute
from .store import code as episode_code

#: At most this many search titles per series, each at most ``ALIAS_MAX_LENGTH`` characters (plan, "Sicherheit").
ALIASES_MAX = 50
ALIAS_MAX_LENGTH = 200
#: Season and episode numbers a correction may name, as the parser reads them.
NUMBER_MAX = 9999
#: A range of a correction holds at most this many episodes.
RANGE_MAX = 100
_SPECIAL_PART = re.compile(r"special|extra|bonus", re.IGNORECASE)


@dataclass(frozen=True)
class Correction:
    episode_id: int
    season: int | None
    episode: int | None
    episode_end: int | None = None
    #: The number counted through, anime series only (B6).
    absolute: int | None = None


# --- Search titles ----------------------------------------------------------------------------- #


def aliases(db: OrmSession, title_id: int) -> list[TitleAlias]:
    return list(db.scalars(select(TitleAlias).where(TitleAlias.title_id == title_id).order_by(TitleAlias.id)))


def clean_aliases(texts: list[str]) -> list[str] | None:
    """Distinct, trimmed texts with keys; None when one is empty, too long or there are too many."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in texts:
        text = " ".join(schreibweisen.nfc(raw).split())
        if not text or len(text) > ALIAS_MAX_LENGTH or not schreibweisen.keys(text):
            return None
        key = text.casefold()
        if key not in seen:
            seen.add(key)
            cleaned.append(text)
    return cleaned if len(cleaned) <= ALIASES_MAX else None


def set_aliases(db: OrmSession, title: Title, texts: list[str], moment: datetime) -> None:
    db.execute(delete(TitleAlias).where(TitleAlias.title_id == title.id))
    for text in texts:
        db.add(
            TitleAlias(title_id=title.id, text=text, search_keys=schreibweisen.search_text([text]), created_at=moment)
        )


# --- Corrections ------------------------------------------------------------------------------- #


def corrections(db: OrmSession, title_id: int) -> list[EpisodeNumber]:
    return list(
        db.scalars(
            select(EpisodeNumber)
            .where(EpisodeNumber.title_id == title_id, EpisodeNumber.scheme == "owner")
            .order_by(EpisodeNumber.season, EpisodeNumber.episode, EpisodeNumber.id)
        )
    )


def check_corrections(db: OrmSession, title_id: int, items: list[Correction]) -> list[str]:
    """The fields that are wrong: an episode of another series, numbers out of range, two for one episode, a number
    counted through for a series that is no anime, or twice."""
    wrong: list[str] = []
    known = set(db.scalars(select(Episode.id).where(Episode.title_id == title_id)))
    title = db.get(Title, title_id)
    anime = title is not None and title.series_type == "anime"
    seen: set[int] = set()
    counted: set[int] = set()
    for item in items:
        if item.episode_id not in known or item.episode_id in seen:
            wrong.append("episode_id")
        seen.add(item.episode_id)
        numbered = item.season is not None or item.episode is not None
        if numbered and not (
            item.season is not None
            and item.episode is not None
            and 0 <= item.season <= NUMBER_MAX
            and 0 <= item.episode <= NUMBER_MAX
        ):
            wrong.append("numbers")
        if not numbered and item.absolute is None:
            wrong.append("numbers")
        if item.episode_end is not None and not (
            item.episode is not None and item.episode < item.episode_end <= item.episode + RANGE_MAX
        ):
            wrong.append("episode_end")
        if item.absolute is not None:
            if not anime or not 1 <= item.absolute <= NUMBER_MAX or item.absolute in counted:
                wrong.append("absolute")
            counted.add(item.absolute)
    return sorted(set(wrong))


def set_corrections(db: OrmSession, title: Title, items: list[Correction], moment: datetime) -> None:
    db.execute(delete(EpisodeNumber).where(EpisodeNumber.title_id == title.id, EpisodeNumber.scheme == "owner"))
    for item in items:
        db.add(
            EpisodeNumber(
                episode_id=item.episode_id,
                title_id=title.id,
                scheme="owner",
                season=item.season,
                episode=item.episode,
                episode_end=item.episode_end,
                absolute=item.absolute,
                verified=True,
                origin="owner",
                updated_at=moment,
            )
        )
    # The owner's numbers counted through go first in the scheme ``absolute`` (B6).
    absolute.store(db, title, moment)


def scene_numbers(db: OrmSession, title_id: int) -> int:
    """How many episodes of the series carry a scene number: the switch only means something with some."""
    return len(
        list(
            db.scalars(
                select(EpisodeNumber.id).where(EpisodeNumber.title_id == title_id, EpisodeNumber.scheme == "scene")
            )
        )
    )


# --- Episode group ----------------------------------------------------------------------------- #


def group_places(group: dict[str, Any]) -> dict[int, tuple[int, int]]:
    """TMDB episode id to (season, episode) in a group's numbering. Pure."""
    parts = sorted(
        (part for part in group.get("groups") or [] if isinstance(part, dict)),
        key=lambda part: part.get("order") if isinstance(part.get("order"), int) else 0,
    )
    regular = [
        part
        for part in parts
        if not _SPECIAL_PART.search(str(part.get("name") or ""))
        and isinstance(part.get("order"), int)
        and part["order"] > 0
    ] or parts
    places: dict[int, tuple[int, int]] = {}
    for season, part in enumerate(regular, start=1):
        episodes = sorted(
            (item for item in part.get("episodes") or [] if isinstance(item, dict) and isinstance(item.get("id"), int)),
            key=lambda item: item.get("order") if isinstance(item.get("order"), int) else 0,
        )
        for number, item in enumerate(episodes, start=1):
            places.setdefault(item["id"], (season, number))
    return places


def store_group(
    db: OrmSession, title: Title, group_id: str | None, places: dict[int, tuple[int, int]], moment: datetime
) -> int:
    """The chosen group as scheme ``group``; None clears it. Returns how many episodes got a number."""
    db.execute(delete(EpisodeNumber).where(EpisodeNumber.title_id == title.id, EpisodeNumber.scheme == "group"))
    title.episode_group_id = group_id
    if group_id is None:
        return 0
    by_tmdb = dict(
        db.execute(
            select(Episode.tmdb_episode_id, Episode.id).where(
                Episode.title_id == title.id, Episode.tmdb_episode_id.is_not(None)
            )
        ).all()
    )
    stored = 0
    for tmdb_episode_id, (season, number) in places.items():
        episode_id = by_tmdb.get(tmdb_episode_id)
        if episode_id is None:
            continue
        db.add(
            EpisodeNumber(
                episode_id=episode_id,
                title_id=title.id,
                scheme="group",
                season=season,
                episode=number,
                verified=True,
                origin="tmdb",
                updated_at=moment,
            )
        )
        stored += 1
    return stored


# --- Reading ----------------------------------------------------------------------------------- #


def describe(db: OrmSession, title: Title) -> dict[str, Any]:
    episodes = {
        row.id: row
        for row in db.scalars(select(Episode).where(Episode.title_id == title.id, Episode.tmdb_gone_at.is_(None)))
    }
    return {
        "title_id": title.id,
        "episode_groups": list(title.episode_groups or []),
        "episode_group_id": title.episode_group_id,
        "aliases": [row.text for row in aliases(db, title.id)],
        "title_en": title.title_en,
        "corrections": [
            {
                "episode_id": row.episode_id,
                "code": episode_code(episodes[row.episode_id].season_number, episodes[row.episode_id].episode_number),
                "season": row.season,
                "episode": row.episode,
                "episode_end": row.episode_end,
                "absolute": row.absolute,
            }
            for row in corrections(db, title.id)
            if row.episode_id in episodes
            and ((row.season is not None and row.episode is not None) or row.absolute is not None)
        ],
        "use_scene_numbering": title.use_scene_numbering is not False,
        "scene_numbers": scene_numbers(db, title.id),
        "xem_state": title.xem_state,
        "xem_checked_at": title.xem_checked_at,
        "numbering_note": title.numbering_note,
    }
