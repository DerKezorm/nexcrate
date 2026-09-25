"""Searching an album: what to ask the indexers, which release is the album, and what nexcrate would take
(M3.2 to M3.5).

The movie search runs the indexers; this module gives it the album's queries and judges the answers. ``load`` reads
the database once; everything else is pure.

**Queries** per indexer, at most ``MAX_QUERIES`` (decisions 5 to 9):

1. ``t=music`` with ``artist`` and ``album``, only when the caps offer music search with both (private trackers
   through Prowlarr or Jackett). A sampler asks with the album alone.
2. Free text, only when step 1 was not possible or found no release of the album: ``t=search`` with "artist album"
   in the spellings of ``schreibweisen`` (as written, ae, a), each once. A sampler asks with the album alone; an
   album named like its artist adds the year, else every release of the artist would come. When the name of the
   target release differs from the album's, one query with it. Only when the caps list ``q`` for the plain search.
3. On request (``aliases``), when nothing fitted: the same with up to two further names of the artist.

Categories: the indexer's music categories; an indexer without any is skipped and says so.

**Judging** (decisions 10 to 17): every release is matched with ``music.hit_matching`` against all albums of the
artist. The releases of this album are judged with the music profile (``releases.music_decision``), after the
category of the indexer filled a gap in the name (``category_filled``). The size is checked against the length of
the target release when its tracks are loaded. "Would take" is the best accepted release that is better than the
album's files, in this order: the profile's rank, a name that fits the target release, the indexer's priority,
seeders or age, and the size.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from ...models import Artist, Release, ReleaseTrack, Title
from .. import delay as delay_rules
from .. import indexers, schreibweisen
from .. import tags as tag_store
from ..music import album_quality, hit_matching, store
from ..releases import music_decision as md
from ..releases import music_qualities as mq
from ..releases.music_parser import ParsedAlbum, parse_album
from . import plan, ranking
from .model import IndexerInfo

#: Queries per indexer and album search (decision 9).
MAX_QUERIES = 4
#: Further names of the artist asked on request (decision 8).
MAX_ALIASES = 2
SEARCH_SECONDS = 180.0
SKIPPED_CODE = "indexer_no_music_categories"
#: The music categories when an indexer lists none in its caps (decision 4).
DEFAULT_MUSIC_CATEGORIES = (3000, 3010, 3040)
#: Never asked: video and audiobooks.
LEFT_OUT_CATEGORIES = (3020, 3030)
LOSSLESS_CATEGORY = 3040
MP3_CATEGORY = 3010
#: Below this bitrate over the whole length a release cannot be the whole album (decision 16).
FLOOR_KBIT = 96
#: The bitrate below which a step looks too small to be what the name says: a hint only.
STEP_FLOOR_KBIT = {mq.LOSSLESS_24: 900, mq.LOSSLESS_16: 400, mq.LOSSY_HIGH: 180, mq.LOSSY_MID: 110}
#: More than this over the whole length is no album of audio alone.
CEILING_KBIT = 12_000


def default_music_categories(caps: dict[str, Any] | None) -> list[int]:
    """The caps categories from 3000 to 3999 without video and audiobooks, else ``DEFAULT_MUSIC_CATEGORIES``."""
    found: set[int] = set()
    for category in (caps or {}).get("categories") or []:
        if category["id"] in LEFT_OUT_CATEGORIES:
            continue
        for category_id in [category["id"], *(sub["id"] for sub in category.get("subcats") or [])]:
            if 3000 <= category_id <= 3999 and category_id not in LEFT_OUT_CATEGORIES:
                found.add(category_id)
    return sorted(found) if found else list(DEFAULT_MUSIC_CATEGORIES)


def music_categories(info: IndexerInfo) -> tuple[int, ...]:
    """The categories an album search asks: the stored ones, else the default from the caps."""
    if info.music_categories is not None:
        return info.music_categories
    return tuple(default_music_categories(info.caps))


# --- What the search knows ------------------------------------------------------------------------------------------ #


@dataclass(frozen=True)
class TargetInfo:
    name: str
    track_count: int
    media_count: int
    #: The length of every track together; None while a track has no length or the tracks are not loaded.
    length_ms: int | None
    year: int | None


@dataclass(frozen=True)
class AlbumSearch:
    title_id: int
    title: str
    year: int | None
    artist: hit_matching.ArtistRef
    #: The name the artist is asked with; the first further names for ``aliases``.
    artist_name: str
    further_names: tuple[str, ...]
    #: Every album of the artist, the one searched included.
    albums: tuple[hit_matching.AlbumRef, ...]
    target: TargetInfo | None
    #: The music version: its definition id, label and profile.
    version_id: int | None
    label: str
    has_profile: bool
    rules: dict[str, Any] | None
    #: The step of the album's files; None without files.
    current_step: str | None
    has_file: bool
    #: Asking with the further names too (decision 8).
    aliases: bool = False
    search_seconds: float = SEARCH_SECONDS
    scope: dict[str, Any] = field(default_factory=dict)
    #: The delay rule of the music version: a protocol switched off does not fit, the preferred one wins a tie.
    delay: delay_rules.Rule = delay_rules.DEFAULT


def _year(date: str | None) -> int | None:
    return int(date[:4]) if date and len(date) >= 4 and date[:4].isdigit() else None


def _target(db: OrmSession, release_id: int | None) -> TargetInfo | None:
    release = db.get(Release, release_id) if release_id is not None else None
    if release is None:
        return None
    length: int | None = None
    if release.tracks_loaded:
        count, total, known = db.execute(
            select(
                func.count(ReleaseTrack.id), func.sum(ReleaseTrack.length_ms), func.count(ReleaseTrack.length_ms)
            ).where(ReleaseTrack.release_id == release.id)
        ).one()
        if count and known == count and total:
            length = int(total)
    return TargetInfo(
        name=release.name,
        track_count=release.track_count,
        media_count=release.media_count,
        length_ms=length,
        year=_year(release.date),
    )


def _names(artist: Artist) -> list[str]:
    names = [artist.name]
    for alias in artist.aliases or []:
        name = alias.get("name") if isinstance(alias, dict) else None
        if isinstance(name, str) and name.strip() and name not in names:
            names.append(name)
    return names


def load(db: OrmSession, title_id: int, *, aliases: bool = False) -> AlbumSearch | None:
    """The album with its artist, the artist's albums, the target release and the music version; None when the title
    is gone, no album, or has no artist."""
    title = db.get(Title, title_id)
    if title is None or title.kind != "album" or title.artist_id is None:
        return None
    artist = db.get(Artist, title.artist_id)
    if artist is None:
        return None
    rows = db.execute(
        select(Title.id, Title.title, Title.year, Title.primary_type, Title.secondary_types).where(
            Title.kind == "album", Title.artist_id == artist.id
        )
    ).all()
    albums = tuple(
        hit_matching.AlbumRef(
            title_id=row.id,
            title=row.title,
            year=row.year,
            primary_type=row.primary_type,
            secondary_types=tuple(row.secondary_types or ()),
        )
        for row in rows
    )
    names = _names(artist)
    version = store.album_version(db, title.id)
    definition = store.definition(db)
    rules = album_quality.rules_of(db)
    step = version.quality if version is not None and version.quality in mq.STEPS else None
    return AlbumSearch(
        title_id=title.id,
        title=title.title,
        year=title.year,
        artist=hit_matching.ArtistRef(names=tuple(names), various=bool(artist.is_various)),
        artist_name=artist.name,
        further_names=tuple(names[1 : 1 + MAX_ALIASES]),
        albums=albums,
        target=_target(db, version.target_release_id if version is not None else None),
        version_id=definition.id if definition is not None else None,
        label=definition.label if definition is not None else "",
        has_profile=rules is not None,
        rules=rules,
        current_step=step,
        has_file=bool(version is not None and version.has_file),
        aliases=aliases,
        scope={"kind": "album", "aliases": aliases},
        delay=delay_rules.for_title(
            definition.delay if definition is not None else None, tag_store.title_tag_ids(db, title_id)
        ),
    )


# --- Queries -------------------------------------------------------------------------------------------------------- #


def music_query(caps: dict[str, Any] | None, album: AlbumSearch) -> plan.Query | None:
    """``t=music`` with artist and album, when the caps offer music search with both (decision 5)."""
    used = plan.caps_in_use(caps)
    params = set(used.get("music_params") or [])
    if not used.get("music_search") or "album" not in params or (not album.artist.various and "artist" not in params):
        return None
    values: dict[str, str | int] = {"t": "music", "album": album.title}
    if not album.artist.various:
        values["artist"] = album.artist_name
    text = album.title if album.artist.various else f"{album.artist_name} {album.title}"
    return plan.Query("music", text, values)


def _texts(album: AlbumSearch, artist_names: list[str]) -> list[str]:
    if album.artist.various:
        texts = [album.title]
        if album.target is not None and not schreibweisen.share_a_key(album.target.name, album.title):
            texts.append(album.target.name)
        return texts
    texts = []
    for name in artist_names:
        if schreibweisen.share_a_key(name, album.title):
            # Named like the artist: the year narrows it (decision 7).
            texts.append(f"{name} {album.year}" if album.year else name)
        else:
            texts.append(f"{name} {album.title}")
    if album.target is not None and not schreibweisen.share_a_key(album.target.name, album.title):
        texts.append(f"{artist_names[0]} {album.target.name}")
    return texts


def text_queries(caps: dict[str, Any] | None, album: AlbumSearch, room: int = MAX_QUERIES) -> list[plan.Query]:
    """The free-text queries in order, without repeats, at most ``room``; empty without ``q`` in the caps."""
    if "q" not in (plan.caps_in_use(caps).get("search_params") or []):
        return []
    names = list(album.further_names) if album.aliases else [album.artist_name]
    queries: list[plan.Query] = []
    for text in _texts(album, names):
        for spelling in schreibweisen.query_spellings(text):
            if len(queries) >= room:
                return queries
            if all(existing.text != spelling for existing in queries):
                queries.append(plan.Query("title", spelling, {"t": "search", "q": spelling}))
    return queries


def fits_any(album: AlbumSearch, releases: list[indexers.Release]) -> bool:
    """Whether one of the releases is this album: the structured query found it, no free text follows."""
    return any(
        hit_matching.match(parse_album(release.title), album.artist, album.albums, album.title_id).kind
        == hit_matching.THIS
        for release in releases
    )


# --- The category fills a gap --------------------------------------------------------------------------------------- #

#: Decision 15 with the threshold of M3.0: the category fills the format a name does not give.
USE_CATEGORY = True


def category_filled(parsed: ParsedAlbum, categories: list[int]) -> tuple[ParsedAlbum, str | None]:
    """The parsed name with the format its indexer category gives, when the name gives none, and the note to show:
    ``format_from_category`` when filled, ``category_differs`` when the category says otherwise than the name."""
    lossless = LOSSLESS_CATEGORY in categories
    lossy = MP3_CATEGORY in categories
    if lossless == lossy:
        return parsed, None
    # A scene name without a format is only assumed MP3; one with a bitrate says lossy itself.
    if parsed.format is None or (parsed.format_assumed and parsed.bitrate is None):
        if not USE_CATEGORY:
            return parsed, None
        if lossless:
            return dataclasses.replace(parsed, format="FLAC", format_assumed=True), "format_from_category"
        if parsed.format is None:
            return dataclasses.replace(parsed, format="MP3", format_assumed=True), "format_from_category"
        return parsed, None
    named_lossless = parsed.format in ("FLAC", "ALAC", "WAV", "APE", "WavPack", "DSD")
    return parsed, ("category_differs" if named_lossless != lossless else None)


# --- Judging -------------------------------------------------------------------------------------------------------- #


def _size_notes(
    size: int | None, step: str, target: TargetInfo | None
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """A rejection when the release is too small to be the whole album, and hints for sizes that do not fit."""
    if not size or target is None or not target.length_ms:
        return None, []
    kbit = size * 8 / 1000 / (target.length_ms / 1000)
    minutes = round(target.length_ms / 60000)
    if kbit < FLOOR_KBIT:
        floor = int(FLOOR_KBIT * 1000 / 8 * target.length_ms / 1000)
        return {"code": "too_small_for_album", "minutes": minutes, "minimum_bytes": floor}, []
    notes: list[dict[str, Any]] = []
    if step in STEP_FLOOR_KBIT and kbit < STEP_FLOOR_KBIT[step]:
        notes.append({"code": "small_for_step", "step": step, "kbit": round(kbit)})
    if kbit > CEILING_KBIT:
        notes.append({"code": "large_for_album", "minutes": minutes})
    return None, notes


def _edition_notes(parsed: ParsedAlbum, target: TargetInfo | None) -> list[dict[str, Any]]:
    """A name that says another edition than the target release: a hint, never a rejection (decision 14)."""
    if target is None:
        return []
    notes: list[dict[str, Any]] = []
    other = [edition for edition in parsed.editions if edition in ("deluxe", "expanded", "box", "complete", "bonus")]
    other_media = bool(parsed.media_count and target.media_count and parsed.media_count != target.media_count)
    named = {word for key in schreibweisen.keys(target.name) for word in key.split(" ")}
    if other_media or (other and not set(other) & named):
        notes.append({"code": "other_edition", "editions": other, "media": parsed.media_count})
    return notes


def _age_hours(published: datetime | None, moment: datetime) -> float | None:
    return max(0.0, (moment - published).total_seconds() / 3600) if published is not None else None


def _order_key(entry: dict[str, Any]) -> tuple[Any, ...]:
    verdict = entry["verdict"]
    torrent = entry["protocol"] == "torrent"
    return (
        tuple(verdict["rank"]),
        1 if any(note["code"] == "other_edition" for note in verdict["notes"]) else 0,
        # The protocol the music version's delay rule prefers, where Lidarr's comparer has it: after the quality.
        entry.get("protocol_step", 0),
        entry["priority"],
        -ranking.magnitude(entry["seeders"]) if torrent else 0,
        -ranking.age_bucket(entry["age_hours"]) if not torrent else 0,
        -ranking.size_step(entry["size_bytes"]),
        entry["order"],
    )


def evaluate(
    album: AlbumSearch, states: list[Any], moment: datetime
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Per release its album and verdict, and per version what nexcrate would take.

    Returns the version decisions (one: the music version) and the releases: this album first in the order of the
    decision, then other albums of the artist, then the rest, each group in the order found.
    """
    found: list[dict[str, Any]] = []
    order = 0
    for state in states:
        info = state.info
        for key, release in state.releases.items():
            parsed = parse_album(release.title)
            hit = hit_matching.match(parsed, album.artist, album.albums, album.title_id)
            parsed, category_note = category_filled(parsed, list(release.categories or []))
            step = mq.step_of(parsed)
            entry: dict[str, Any] = {
                "release_key": key,
                "indexer_id": info.indexer_id,
                "indexer_name": info.name,
                "protocol": info.protocol,
                "title": release.title,
                "size_bytes": release.size_bytes,
                "published_at": release.published_at,
                "age_hours": _age_hours(release.published_at, moment),
                "seeders": release.seeders,
                "peers": release.peers,
                "grabs": release.grabs,
                "categories": list(release.categories or []),
                "priority": info.priority,
                "protocol_step": delay_rules.protocol_step(album.delay, info.protocol),
                "order": order,
                "match": {
                    "kind": hit.kind,
                    "title_id": hit.album.title_id if hit.album is not None else None,
                    "album": hit.album.title if hit.album is not None else None,
                    "year": hit.album.year if hit.album is not None else None,
                    "reason": hit.reason,
                    "read_artist": hit.read_artist,
                    "read_album": hit.read_album,
                    "kind_differs": hit.kind_differs,
                },
                "step": step if step in mq.STEPS else None,
                "format": parsed.format,
                "bit_depth": parsed.bit_depth,
                "source": parsed.source,
                "verdict": None,
                "place": None,
            }
            order += 1
            if hit.kind == hit_matching.THIS:
                entry["verdict"] = _verdict(album, parsed, release, info, step, category_note, entry["age_hours"])
            found.append(entry)
    ours = [entry for entry in found if entry["verdict"] is not None]
    accepted = sorted((entry for entry in ours if entry["verdict"]["accepted"]), key=_order_key)
    for place, entry in enumerate(accepted, start=1):
        entry["place"] = place
    decided = [_decide(album, accepted, [entry for entry in ours if not entry["verdict"]["accepted"]])]
    refused = [entry for entry in ours if not entry["verdict"]["accepted"]]
    others = [
        entry for entry in found if entry["verdict"] is None and entry["match"]["kind"] == hit_matching.OTHER_ALBUM
    ]
    rest = [entry for entry in found if entry["verdict"] is None and entry["match"]["kind"] != hit_matching.OTHER_ALBUM]
    for entry in found:
        entry.pop("order", None)
        entry.pop("protocol_step", None)
    return decided, [*accepted, *refused, *others, *rest]


def _verdict(
    album: AlbumSearch,
    parsed: ParsedAlbum,
    release: indexers.Release,
    info: IndexerInfo,
    step: str,
    category_note: str | None,
    age_hours: float | None = None,
) -> dict[str, Any]:
    notes: list[dict[str, Any]] = []
    if category_note is not None:
        notes.append({"code": category_note})
    rejections: list[dict[str, Any]] = []
    seeders = ranking.seeders_rejection(info.protocol, release.seeders, info.minimum_seeders)
    if seeders is not None:
        rejections.append(seeders)
    too_old = ranking.retention_rejection(info.protocol, age_hours, info.retention_days)
    if too_old is not None:
        rejections.append(too_old)
    switched_off = delay_rules.protocol_rejection(album.delay, info.protocol)
    if switched_off is not None:
        rejections.append(switched_off)
    too_small, size_notes = _size_notes(release.size_bytes, step, album.target)
    if too_small is not None:
        rejections.append(too_small)
    notes.extend(size_notes)
    notes.extend(_edition_notes(parsed, album.target))
    if album.rules is None:
        # Without a profile only what nexcrate never takes (decision 3): no step is judged.
        fixed = [{"code": "cue_single_file"}] if "cue" in parsed.warnings else []
        if "audiobook" in parsed.warnings:
            fixed.append({"code": "audiobook"})
        judged = {"for_now": False, "rejections": fixed, "notes": [], "rank": [0]}
    else:
        judged = md.evaluate(parsed, album.rules)
    rejections = [*judged["rejections"], *rejections]
    return {
        "judged": album.rules is not None,
        "accepted": not rejections,
        "for_now": bool(judged["for_now"]) and not rejections,
        "rejections": rejections,
        "notes": [*judged["notes"], *notes],
        "rank": list(judged["rank"]),
    }


def _better(step: str | None, current: str | None) -> bool:
    return step in mq.STEPS and (current is None or mq.rank(step) < mq.rank(current))


def _decide(album: AlbumSearch, accepted: list[dict[str, Any]], refused: list[dict[str, Any]]) -> dict[str, Any]:
    """What the music version would take: the first accepted release better than the album's files."""
    decision: dict[str, Any] = {
        "version_id": album.version_id,
        "label": album.label,
        "has_profile": album.has_profile,
        "would_take": None,
        "keeps_current": False,
        "nothing_fits": [],
    }
    if not album.has_profile:
        return decision
    for entry in accepted:
        if not album.has_file or (
            _better(entry["step"], album.current_step) and md.upgrade_possible(album.current_step or "", album.rules)
        ):
            decision["would_take"] = entry["release_key"]
            return decision
    if album.has_file:
        decision["keeps_current"] = True
        return decision
    decision["nothing_fits"] = ranking.decide(
        [], False, [[item["code"] for item in entry["verdict"]["rejections"]] for entry in refused]
    ).nothing_fits
    return decision
