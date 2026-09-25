"""Unclear files of a series version of nexcrate's own: proposals and assigning by hand (Ü3,
decisions 15 to 18).

An **unclear file** is a file of the version that holds no episode and that the owner did not leave out. It comes from
a takeover (Sonarr's episode without a TMDB partner) or from reading a folder (a name without a single clear episode).

**A proposal** looks only at episodes of the version without a file, strongest step first, and counts only when it is
the single candidate of its step:

1. the same air date and the same title (spelling keys);
2. the same title;
3. the same air date, one day either way;
4. the same numbers in TVDB's or the scene numbering (TheXEM).

What a file says comes from Sonarr's episode with the file's numbers (its title and date) or, for a file read from disk,
from its name (its numbers, and the title an episode name spells in it). Without a proposal the owner gets the episodes
without a file around that place: the file's season, else the next season with such episodes, at most 12.

Assigning links the file to the chosen episodes (each must be of the title and without a file), judges it by the
version's rules, keeps its season folder and writes ``release.nex``. "No episode" marks the file left out; it stays on
disk and no longer counts as unclear.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ...models import (
    Episode,
    EpisodeFile,
    EpisodeNumber,
    EpisodeVersion,
    SeasonFolder,
    SourceEpisode,
    Title,
    Version,
    utcnow,
)
from .. import releases, schreibweisen
from ..profiles import store as profile_store
from ..releases import series_decision
from . import folder_read, watching
from .parts import as_whole

logger = logging.getLogger("nexcrate.series.unclear")

NEARBY_MAX = 12
#: A title shorter than this proves nothing inside a file name ("Pilot" does, "Go" does not).
TITLE_MIN = 4
#: The steps that "take every proposal" takes: title and date, or title alone.
SAFE_STEPS = (1, 2)
#: ⚠️ Only evidence of a source's episode (its own title) is taken unasked. A title found *inside* a file name is a
#: proposal the owner confirms: an episode named like the series would swallow every unclear file.
SAFE_WHOLE_ONLY = True


class NotAssignable(Exception):
    """An episode is not of the title, gone from TMDB, or has a file already."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Free:
    id: int
    season: int
    episode: int
    name: str
    air_date: str | None
    keys: frozenset[str]


def _key(text: str | None) -> str:
    found = schreibweisen.keys(text)
    return found[0] if found else ""


def _names(text: str | None) -> frozenset[str]:
    return frozenset(key for key in schreibweisen.keys(text) if len(key) >= TITLE_MIN)


def unclear_files(db: OrmSession, version: Version, *, left_out: bool = False) -> list[EpisodeFile]:
    linked = select(EpisodeVersion.episode_file_id).where(
        EpisodeVersion.version_id == version.id, EpisodeVersion.episode_file_id.is_not(None)
    )
    return list(
        db.scalars(
            select(EpisodeFile)
            .where(
                EpisodeFile.version_id == version.id,
                EpisodeFile.id.not_in(linked),
                EpisodeFile.left_out.is_(left_out),
                # A second half names its episode itself.
                EpisodeFile.part_of_episode_id.is_(None),
            )
            .order_by(EpisodeFile.relative_path)
        )
    )


def free_episodes(db: OrmSession, version: Version) -> list[Free]:
    rows = db.execute(
        select(
            Episode.id, Episode.season_number, Episode.episode_number, Episode.name, Episode.name_en, Episode.air_date
        )
        .join(EpisodeVersion, EpisodeVersion.episode_id == Episode.id)
        .where(
            EpisodeVersion.version_id == version.id,
            EpisodeVersion.episode_file_id.is_(None),
            Episode.tmdb_gone_at.is_(None),
        )
        .order_by(Episode.season_number, Episode.episode_number)
    ).tuples()
    return [
        Free(episode_id, season, number, name or "", air_date, _names(name) | _names(name_en))
        for episode_id, season, number, name, name_en, air_date in rows
    ]


@dataclass(frozen=True)
class Evidence:
    season: int | None
    number: int | None
    name: str | None
    air_date: str | None
    #: The name is a Sonarr episode title, compared whole; otherwise a file name holding a title somewhere.
    whole: bool = False


def evidence_of(row: EpisodeFile, sources: dict[tuple[int, int], SourceEpisode]) -> list[Evidence]:
    """What a file says, one entry per episode number it names; one entry without numbers otherwise."""
    numbers = row.source_numbers or {}
    read = row.read_as or {}
    season = numbers.get("season") if isinstance(numbers.get("season"), int) else read.get("season")
    episodes = [value for value in numbers.get("episodes") or [] if isinstance(value, int)]
    if not episodes:
        episodes = [value for value in read.get("episodes") or [] if isinstance(value, int)]
    stem = PurePosixPath(row.relative_path).stem
    if not episodes:
        return [Evidence(season if isinstance(season, int) else None, None, stem, None)]
    found: list[Evidence] = []
    for number in episodes:
        source = sources.get((season, number)) if isinstance(season, int) else None
        if source is not None:
            found.append(Evidence(season, number, source.name or stem, source.air_date, whole=bool(source.name)))
        else:
            found.append(Evidence(season if isinstance(season, int) else None, number, stem, None))
    return found


def _near(day: str, other: str | None) -> bool:
    if other is None:
        return False
    try:
        return abs((date.fromisoformat(day) - date.fromisoformat(other)).days) <= 1
    except ValueError:
        return False


def _title_fits(name: str | None, free: Free, whole_name: bool) -> bool:
    """A Sonarr episode's title is compared whole; a file name holds the title somewhere inside."""
    if not name or not free.keys:
        return False
    if whole_name:
        return bool(_names(name) & free.keys)
    text = f" {_key(name)} "
    return any(f" {key} " in text for key in free.keys)


def _numbered(db: OrmSession, title_id: int) -> dict[tuple[int, int], set[int]]:
    found: dict[tuple[int, int], set[int]] = defaultdict(set)
    for number in db.scalars(
        select(EpisodeNumber).where(EpisodeNumber.title_id == title_id, EpisodeNumber.scheme.in_(("tvdb", "scene")))
    ):
        if number.season is None or number.episode is None:
            continue
        last = number.episode_end if number.episode_end is not None else number.episode
        for value in range(number.episode, max(number.episode, last) + 1):
            found[(number.season, value)].add(number.episode_id)
    return found


def propose(evidence: Evidence, free: list[Free], numbered: dict[tuple[int, int], set[int]]) -> tuple[int, int] | None:
    """The one episode a piece of evidence names and its step, or None."""
    titled = [item for item in free if _title_fits(evidence.name, item, evidence.whole)]
    if evidence.air_date:
        both = [item for item in titled if _near(evidence.air_date, item.air_date)]
        exact = [item for item in both if item.air_date == evidence.air_date]
        if len(exact) == 1:
            return exact[0].id, 1
        if len(both) == 1:
            return both[0].id, 1
    if len(titled) == 1:
        return titled[0].id, 2
    if evidence.air_date:
        dated = [item for item in free if _near(evidence.air_date, item.air_date)]
        exact = [item for item in dated if item.air_date == evidence.air_date]
        if len(exact) == 1:
            return exact[0].id, 3
        if len(dated) == 1:
            return dated[0].id, 3
    if evidence.season is not None and evidence.number is not None:
        ids = numbered.get((evidence.season, evidence.number), set())
        candidates = [item for item in free if item.id in ids]
        if len(candidates) == 1:
            return candidates[0].id, 4
    return None


def nearby(evidence: list[Evidence], free: list[Free]) -> list[Free]:
    seasons = sorted({item.season for item in evidence if item.season is not None})
    if seasons:
        same = [item for item in free if item.season == seasons[0]]
        if same:
            return same[:NEARBY_MAX]
        later = [item for item in free if item.season > seasons[0]]
        if later:
            first = later[0].season
            return [item for item in later if item.season == first][:NEARBY_MAX]
    return free[:NEARBY_MAX]


def _shown(evidence: list[Evidence], free: list[Free], proposal: dict[str, Any] | None, own: bool) -> list[Free]:
    """The episodes the page offers: those around the file's place, and always those of its proposal."""
    if not own:
        return []
    found = list(nearby(evidence, free))
    for episode_id in (proposal or {}).get("episode_ids", []):
        if not any(item.id == episode_id for item in found):
            extra = next((item for item in free if item.id == episode_id), None)
            if extra is not None:
                found.append(extra)
    return found


def describe(db: OrmSession, version: Version) -> list[dict[str, Any]]:
    """Every unclear file of an own version with its proposal and the episodes around it; files left out follow with
    ``left_out`` true. A version a source feeds gets no proposals: Sonarr decides there."""
    own = version.source_id is None
    files = unclear_files(db, version) + (unclear_files(db, version, left_out=True) if own else [])
    if not files:
        return []
    free = free_episodes(db, version) if own else []
    sources = {
        (row.season, row.episode): row
        for row in db.scalars(select(SourceEpisode).where(SourceEpisode.version_id == version.id))
    }
    numbered = _numbered(db, version.title_id) if own else {}
    described = []
    for row in files:
        evidence = evidence_of(row, sources)
        source = next(
            (
                sources[(item.season, item.number)]
                for item in evidence
                if item.season is not None and item.number is not None and (item.season, item.number) in sources
            ),
            None,
        )
        proposal = None
        if own and not row.left_out and free:
            picked = [propose(item, free, numbered) for item in evidence]
            if picked and all(item is not None for item in picked):
                ids = list(dict.fromkeys(item[0] for item in picked if item is not None))
                if len(ids) == len(picked):
                    proposal = {
                        "episode_ids": ids,
                        "step": max(item[1] for item in picked if item is not None),
                        "safe": all(item.whole for item in evidence),
                    }
        described.append(
            {
                "file": row,
                "left_out": bool(row.left_out),
                "source_episode": {
                    "season": source.season,
                    "episode": source.episode,
                    "name": source.name or None,
                    "air_date": source.air_date,
                }
                if source is not None
                else None,
                "proposal": proposal,
                "occupied": _occupied(db, version, row) if own and not row.left_out else [],
                "nearby": [
                    {
                        "id": item.id,
                        "season": item.season,
                        "episode": item.episode,
                        "name": item.name or None,
                        "air_date": item.air_date,
                    }
                    for item in _shown(evidence, free, proposal, own and not row.left_out)
                ],
            }
        )
    return described


# --- Changes ------------------------------------------------------------------------------------------------------- #


def assign(
    db: OrmSession, version: Version, file_id: int, episode_ids: list[int], *, replace: bool = False
) -> set[int]:
    """Link one file of the version to these episodes. Returns the seasons whose ``release.nex`` wants writing.

    Raises ``LookupError`` for a file that is not the version's, ``NotAssignable`` with ``episode_has_file`` or
    ``episode_not_found``. The caller commits.

    ``replace``: an episode that has another file gives it up, and that file becomes unclear (18.09.2026: a takeover
    left second files of episodes that already had one, and nothing could say "this one, not the other"). Nothing
    on disk changes.
    """
    row = db.get(EpisodeFile, file_id)
    if row is None or row.version_id != version.id:
        raise LookupError(file_id)
    wanted = list(dict.fromkeys(episode_ids))
    if not wanted:
        raise NotAssignable("episode_not_found")
    links = {
        link.episode_id: link
        for link in db.scalars(
            select(EpisodeVersion).where(EpisodeVersion.version_id == version.id, EpisodeVersion.episode_id.in_(wanted))
        )
    }
    episodes = {
        episode.id: episode
        for episode in db.scalars(
            select(Episode).where(
                Episode.id.in_(wanted), Episode.title_id == version.title_id, Episode.tmdb_gone_at.is_(None)
            )
        )
    }
    if set(episodes) != set(wanted) or set(links) != set(wanted):
        raise NotAssignable("episode_not_found")
    others = {links[episode_id].episode_file_id for episode_id in wanted} - {None, row.id}
    if others and not replace:
        raise NotAssignable("episode_has_file")
    for episode_id in wanted:
        if links[episode_id].episode_file_id in others:
            links[episode_id].episode_file_id = None
    # A file that held other episodes before gives them up: the owner says what it is. ⚠️ Not the chosen ones: the
    # bulk update bypasses the session, and setting the same value on them again would write nothing (18.09.2026).
    db.query(EpisodeVersion).filter(
        EpisodeVersion.version_id == version.id,
        EpisodeVersion.episode_file_id == row.id,
        EpisodeVersion.episode_id.not_in(wanted),
    ).update({"episode_file_id": None}, synchronize_session=False)
    for episode_id in wanted:
        links[episode_id].episode_file_id = row.id
    row.left_out = False
    row.release_type = "multiEpisode" if len(wanted) > 1 else "singleEpisode"
    row.updated_at = utcnow()
    title = db.get(Title, version.title_id)
    profile = profile_store.of_version(db, version.version_definition_id)
    rules = profile_store.series_rules(profile, title.series_type if title is not None else None)
    if rules is not None and rules.get("kind") == "series":
        judged = series_decision.judge_episode_file(
            rules,
            releases.CurrentEpisodeFile(
                release_title=row.release_title,
                name=row.relative_path,
                quality=row.quality,
                release_type=row.release_type,
                size_bytes=as_whole(row.size, row.part),
                episode_count=len(wanted),
                file_id=row.id,
                languages=releases.stored_languages(row.languages),
            ),
            title.original_language if title is not None else None,
        )
        row.cutoff_not_met = judged is not None and judged.reason is not None
    seasons = {episodes[episode_id].season_number for episode_id in wanted}
    parts = row.relative_path.split("/")
    if len(seasons) == 1 and len(parts) == 2:
        (number,) = seasons
        named = folder_read.season_of_folder(parts[0])
        # ⚠️ A file in "Season 02" that holds an episode of season 1 must not make that the folder of season 1.
        if db.get(SeasonFolder, (version.id, number)) is None and named in (None, number):
            db.add(SeasonFolder(version_id=version.id, season_number=number, name=parts[0]))
    db.flush()
    watching.recount(db, version, watching.today())
    return seasons if len(parts) == 2 else set()


def release(db: OrmSession, version: Version, file_id: int) -> set[int]:
    """Take a linked file of the version off its episodes: it becomes unclear and waits under "Nicht zugeordnet" for
    the owner (18.09.2026: a file linked to the wrong episode could not be moved at all). Returns the seasons whose
    ``release.nex`` wants writing. Nothing on disk changes. Raises ``LookupError``."""
    row = db.get(EpisodeFile, file_id)
    if row is None or row.version_id != version.id:
        raise LookupError(file_id)
    held = list(
        db.scalars(
            select(EpisodeVersion).where(
                EpisodeVersion.version_id == version.id, EpisodeVersion.episode_file_id == row.id
            )
        )
    )
    seasons = {
        season
        for season in db.scalars(
            select(Episode.season_number).where(Episode.id.in_([link.episode_id for link in held]))
        )
    }
    for link in held:
        link.episode_file_id = None
    row.left_out = False
    row.updated_at = utcnow()
    db.flush()
    watching.recount(db, version, watching.today())
    return seasons if len(row.relative_path.split("/")) == 2 else set()


def all_episodes(db: OrmSession, version: Version) -> list[dict[str, Any]]:
    """Every episode of the version with the file it has, for "Andere Folge" when the episodes around are not enough."""
    rows = db.execute(
        select(
            Episode.id,
            Episode.season_number,
            Episode.episode_number,
            Episode.name,
            Episode.air_date,
            EpisodeFile.id,
            EpisodeFile.relative_path,
        )
        .join(EpisodeVersion, EpisodeVersion.episode_id == Episode.id)
        .outerjoin(EpisodeFile, EpisodeFile.id == EpisodeVersion.episode_file_id)
        .where(EpisodeVersion.version_id == version.id, Episode.tmdb_gone_at.is_(None))
        .order_by(Episode.season_number, Episode.episode_number)
    ).tuples()
    return [
        {
            "id": episode_id,
            "season": season,
            "episode": number,
            "name": name or None,
            "air_date": air_date,
            "file": {"id": file_id, "relative_path": path} if file_id is not None else None,
        }
        for episode_id, season, number, name, air_date, file_id, path in rows
    ]


def _occupied(db: OrmSession, version: Version, row: EpisodeFile) -> list[dict[str, Any]]:
    """For a file read as episodes that have a file already (``episode_has_file``): those episodes and their file."""
    read_as = row.read_as if isinstance(row.read_as, dict) else None
    if read_as is None or read_as.get("reason") != "episode_has_file":
        return []
    season = read_as.get("season")
    numbers = [value for value in read_as.get("episodes") or [] if isinstance(value, int)]
    numbering = read_as.get("numbering") or "tmdb"
    if not isinstance(season, int) or not numbers:
        return []
    if numbering == "tmdb":
        ids = list(
            db.scalars(
                select(Episode.id).where(
                    Episode.title_id == version.title_id,
                    Episode.season_number == season,
                    Episode.episode_number.in_(numbers),
                    Episode.tmdb_gone_at.is_(None),
                )
            )
        )
    else:
        ids = list(
            db.scalars(
                select(EpisodeNumber.episode_id).where(
                    EpisodeNumber.title_id == version.title_id,
                    EpisodeNumber.scheme == numbering,
                    EpisodeNumber.season == season,
                    EpisodeNumber.episode.in_(numbers),
                )
            )
        )
    if len(ids) != len(numbers):
        return []
    rows = db.execute(
        select(Episode.id, Episode.season_number, Episode.episode_number, Episode.name, Episode.air_date, EpisodeFile)
        .join(EpisodeVersion, EpisodeVersion.episode_id == Episode.id)
        .join(EpisodeFile, EpisodeFile.id == EpisodeVersion.episode_file_id)
        .where(EpisodeVersion.version_id == version.id, Episode.id.in_(ids), EpisodeFile.id != row.id)
        .order_by(Episode.season_number, Episode.episode_number)
    ).tuples()
    return [
        {
            "id": episode_id,
            "season": number_season,
            "episode": number,
            "name": name or None,
            "air_date": air_date,
            "file": {
                "id": other.id,
                "relative_path": other.relative_path,
                "size_bytes": other.size,
                "quality": other.quality,
            },
        }
        for episode_id, number_season, number, name, air_date, other in rows
    ]


def leave_out(db: OrmSession, version: Version, file_id: int, left_out: bool) -> None:
    """Mark a file of the version as belonging to no episode, or unclear again. Raises ``LookupError``."""
    row = db.get(EpisodeFile, file_id)
    if row is None or row.version_id != version.id:
        raise LookupError(file_id)
    linked = db.scalar(
        select(EpisodeVersion.episode_id).where(
            EpisodeVersion.version_id == version.id, EpisodeVersion.episode_file_id == row.id
        )
    )
    if linked is not None and left_out:
        raise NotAssignable("file_has_episodes")
    row.left_out = left_out
    row.updated_at = utcnow()


def take_proposals(db: OrmSession, version: Version, *, from_names: bool = False) -> tuple[int, set[int]]:
    """Every proposal of the safe steps, one after another (an earlier one may take a later one's episode). Returns how
    many files were assigned and the seasons to write.

    ``from_names`` takes a title found inside a file name too, which the owner confirmed first (18.09.2026: a library
    named by absolute numbers had 57 right proposals by title, and the button took none of them). One episode is
    still taken once: an earlier file takes it out of the later proposals."""
    taken = 0
    seasons: set[int] = set()
    while True:
        described = [
            item
            for item in describe(db, version)
            if not item["left_out"]
            and item["proposal"] is not None
            and item["proposal"]["step"] in SAFE_STEPS
            and (item["proposal"].get("safe") or from_names or not SAFE_WHOLE_ONLY)
        ]
        if not described:
            return taken, seasons
        item = described[0]
        seasons |= assign(db, version, item["file"].id, item["proposal"]["episode_ids"])
        taken += 1
