"""One way of writing each genre: the genre tag of filed music, translated onto a short list.

Downloads bring the genre of their source: Deezer writes ``Rap/Hip Hop``, Qobuz ``Pop, Rock, Alternatif et Indé`` in
the language of the account, Apple ``Hip-Hop/Rap``, taggers the long tail of MusicBrainz or Spotify. A player shows each
spelling as a genre of its own. With the switch on, nexcrate writes one of 23 genres instead.

* **The table** (``app/data/genres.json``): the genres with their names in English and German, about 3,500 spellings
  of Deezer (by genre number, every language), Apple, MusicBrainz, Qobuz and the beets genre tree (MIT, (c) Adrian
  Sampson) onto them, Qobuz's umbrella categories, and rules by keyword for a spelling the table does not know.
* **An album** gets at most two genres, in the order of the tag. A part that says only a country or a language
  (``Europe``, ``German Music``) falls away. With no genre left, the album keeps the genre it had in nexcrate, else it
  takes the one most of the artist's other albums have; with none, the tag stays as it is.
* **Regional rule** (off from the start): hip-hop by an artist from Germany, Austria or Switzerland (MusicBrainz's
  country of the artist) becomes ``Deutschrap``.
* **Own choices** win over the table; a spelling nothing knows is counted under ``unmapped`` for the owner to place.
* Only with writing tags on, and never for a file of a version a connection feeds (``tag_writing``).
"""

from __future__ import annotations

import collections
import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ...db import SessionLocal, get_setting, set_setting
from ...models import Artist, Title

logger = logging.getLogger("nexcrate.music")

TABLE = Path(__file__).resolve().parents[2] / "data" / "genres.json"

SETTING_ON = "music_genres"
SETTING_NAMES = "music_genre_names"
SETTING_REGIONAL = "music_genre_regional"
SETTING_CUSTOM = "music_genre_custom"
SETTING_UNMAPPED = "music_genre_unmapped"

NAME_SETS = ("en", "de")
#: What a custom choice may say besides a genre: the spelling falls away.
DROP = "drop"
#: Spellings kept under ``unmapped``; the rarest go first.
UNMAPPED_MAX = 200
CUSTOM_MAX = 500
SPELLING_MAX = 100

_LIST_SPLIT = re.compile(r"\s*[;,|\\]\s*")
_COMPARE_SPLIT = re.compile(r"\s*[;,|\\/]\s*")


@dataclass(frozen=True)
class Table:
    keys: tuple[str, ...]
    names: dict[str, dict[str, str]]
    spellings: dict[str, str | None]
    rules: tuple[tuple[re.Pattern[str], str | None], ...]
    umbrellas: tuple[tuple[str, ...], ...]
    limit: int
    regional: dict[str, tuple[frozenset[str], str]]


@lru_cache(maxsize=1)
def table() -> Table:
    data = json.loads(TABLE.read_text(encoding="utf-8"))
    keys = tuple(item["key"] for item in data["targets"])
    names = {item["key"]: dict(item["names"]) for item in data["targets"]}
    spellings: dict[str, str | None] = {str(k): v for k, v in data["map"].items()}
    # A genre's own names always lead to it: writing twice changes nothing.
    for key, by_set in names.items():
        for name in by_set.values():
            spellings[name.casefold()] = key
    rules = tuple((re.compile(item["pattern"]), item["target"]) for item in data["rules"])
    regional = {
        key: (frozenset(str(c).upper() for c in rule["countries"]), rule["target"])
        for key, rule in (data.get("regional") or {}).items()
    }
    return Table(
        keys=keys,
        names=names,
        spellings=spellings,
        rules=rules,
        umbrellas=tuple(tuple(u) for u in data["umbrellas"]),
        limit=int(data.get("limit", 2)),
        regional=regional,
    )


# --- Settings --------------------------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Settings:
    enabled: bool
    names: str
    regional: bool
    #: Spelling (casefold) to a genre key or ``DROP``.
    custom: dict[str, str] = field(default_factory=dict)


def _json(db: OrmSession, key: str) -> dict[str, Any]:
    try:
        value = json.loads(get_setting(db, key, "{}") or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def settings(db: OrmSession) -> Settings:
    names = get_setting(db, SETTING_NAMES, "en")
    keys = set(table().keys)
    custom = {str(k): str(v) for k, v in _json(db, SETTING_CUSTOM).items() if v in keys or v == DROP}
    return Settings(
        enabled=get_setting(db, SETTING_ON, "0") == "1",
        names=names if names in NAME_SETS else "en",
        regional=get_setting(db, SETTING_REGIONAL, "0") == "1",
        custom=custom,
    )


def unmapped(db: OrmSession) -> list[tuple[str, int]]:
    """Spellings no table, rule or own choice knew, most often seen first."""
    counted = {str(k): int(v) for k, v in _json(db, SETTING_UNMAPPED).items() if isinstance(v, int)}
    custom = settings(db).custom
    return sorted(
        ((name, count) for name, count in counted.items() if name.casefold() not in custom),
        key=lambda item: (-item[1], item[0].casefold()),
    )


def save(
    db: OrmSession,
    *,
    enabled: bool | None = None,
    names: str | None = None,
    regional: bool | None = None,
    custom: dict[str, str | None] | None = None,
) -> None:
    """Stage changed settings; the caller commits. ``custom`` merges; a spelling with ``None`` leaves the choices."""
    if enabled is not None:
        set_setting(db, SETTING_ON, "1" if enabled else "0")
    if names is not None:
        set_setting(db, SETTING_NAMES, names)
    if regional is not None:
        set_setting(db, SETTING_REGIONAL, "1" if regional else "0")
    if custom is not None:
        stored = {str(k): str(v) for k, v in _json(db, SETTING_CUSTOM).items()}
        for spelling, target in custom.items():
            key = spelling.strip().casefold()
            if not key:
                continue
            if target is None:
                stored.pop(key, None)
            else:
                stored[key] = target
        set_setting(db, SETTING_CUSTOM, json.dumps(dict(list(stored.items())[-CUSTOM_MAX:]), ensure_ascii=False))


# --- Translating ------------------------------------------------------------------------------------------------ #


def _items(raw: Iterable[str], spellings: dict[str, str | None], custom: dict[str, str]) -> list[str]:
    """The parts of the tag values: a list splits at ``; , | \\``, a part the table does not know whole at ``/``."""
    items: list[str] = []
    for value in raw:
        for item in _LIST_SPLIT.split(value or ""):
            item = item.strip()
            if not item:
                continue
            key = item.casefold()
            if key in custom or key in spellings or "/" not in item:
                items.append(item)
            else:
                items.extend(part.strip() for part in item.split("/") if part.strip())
    return items


def _by_rule(item: str, rules: tuple[tuple[re.Pattern[str], str | None], ...]) -> tuple[bool, str | None]:
    key = item.casefold()
    for pattern, target in rules:
        if pattern.search(key):
            return True, target
    return False, None


def translate(raw: Iterable[str], custom: dict[str, str] | None = None) -> tuple[list[str], list[str]]:
    """Genre keys for the values of a genre tag, at most ``limit``, and the parts nothing knew."""
    t = table()
    custom = custom or {}
    items = _items(raw, t.spellings, custom)
    keys = [item.casefold() for item in items]
    for umbrella in t.umbrellas:
        if tuple(keys[: len(umbrella)]) == umbrella and len(keys) > len(umbrella):
            items, keys = items[len(umbrella) :], keys[len(umbrella) :]
            break
    found: list[str] = []
    unknown: list[str] = []
    for item, key in zip(items, keys, strict=True):
        if key in custom:
            target: str | None = None if custom[key] == DROP else custom[key]
        elif key in t.spellings:
            target = t.spellings[key]
        else:
            known, target = _by_rule(item, t.rules)
            if not known:
                if item[:SPELLING_MAX] not in unknown:
                    unknown.append(item[:SPELLING_MAX])
                continue
        if target and target not in found:
            found.append(target)
    for key, (_countries, regional) in t.regional.items():
        if regional in found and key in found:
            found.remove(key)
    return found[: t.limit], unknown


def umbrella_alone(raw: Iterable[str], custom: dict[str, str] | None = None) -> tuple[str, ...]:
    """The genres of an umbrella category that stands alone, as Qobuz's "Pop, Rock": it says only "pop or rock".
    Empty when the tag says more (a part that falls away does not count)."""
    t = table()
    custom = custom or {}
    keys = [
        key
        for key in (item.casefold() for item in _items(raw, t.spellings, custom))
        if custom.get(key) != DROP and not (key not in custom and key in t.spellings and t.spellings[key] is None)
    ]
    for umbrella in t.umbrellas:
        if tuple(keys) == umbrella:
            return tuple(dict.fromkeys(target for part in umbrella if (target := t.spellings.get(part))))
    return ()


def names_of(keys: Iterable[str], name_set: str) -> list[str]:
    t = table()
    return [t.names[key].get(name_set) or t.names[key]["en"] for key in keys if key in t.names]


def comparable(values: list[str]) -> list[str]:
    """For comparing a file's genre with the wanted one: ID3 2.3 joins several values with ``/``."""
    parts: list[str] = []
    for value in values:
        parts.extend(part.strip() for part in _COMPARE_SPLIT.split(value or "") if part.strip())
    return parts


# --- An album --------------------------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Decision:
    """The genres an album gets: ``names`` to write, ``came_from`` ``files``, ``album`` or ``artist``."""

    keys: list[str]
    names: list[str]
    came_from: str
    unknown: list[str] = field(default_factory=list)


def raw_of(tag_dicts: Iterable[dict[str, list[str]]]) -> list[str]:
    """The genre values of the files, each once, in the order they come."""
    seen: list[str] = []
    for tags in tag_dicts:
        for value in tags.get("genre") or []:
            if value and value not in seen:
                seen.append(value)
    return seen


def _regional(db: OrmSession, title: Title, keys: list[str], on: bool) -> list[str]:
    """With the rule off, a regional genre is its general one (``Deutschrap`` is ``Hip-Hop``); with it on, the
    general one of an artist from one of the countries becomes the regional one."""
    t = table()
    if not on:
        general = {target: key for key, (_countries, target) in t.regional.items()}
        return list(dict.fromkeys(general.get(key, key) for key in keys))
    country = ""
    if title.artist_id is not None:
        country = (db.scalar(select(Artist.country).where(Artist.id == title.artist_id)) or "").upper()
    result = []
    for key in keys:
        rule = t.regional.get(key)
        result.append(rule[1] if rule is not None and country in rule[0] else key)
    return list(dict.fromkeys(result))


def _of_album(title: Title) -> list[str]:
    keys, _unknown = translate(title.genres or [])
    return keys


def _artist_counts(db: OrmSession, title: Title, *, first_only: bool) -> collections.Counter[str]:
    """How many of the artist's other albums carry each genre (only their first, or all of theirs)."""
    counted: collections.Counter[str] = collections.Counter()
    if title.artist_id is None:
        return counted
    for genres in db.scalars(
        select(Title.genres).where(Title.artist_id == title.artist_id, Title.kind == "album", Title.id != title.id)
    ):
        keys, _unknown = translate(genres or [])
        counted.update(keys[:1] if first_only else keys)
    return counted


def _of_artist(db: OrmSession, title: Title) -> list[str]:
    counted = _artist_counts(db, title, first_only=True)
    return [counted.most_common(1)[0][0]] if counted else []


def _within(db: OrmSession, title: Title, candidates: tuple[str, ...]) -> str | None:
    """One of an umbrella's genres: the one the album had, else the one most of the artist's other albums carry.
    None when nothing tells them apart; the album then keeps both."""
    own = [key for key in _of_album(title) if key in candidates]
    if len(own) == 1:
        return own[0]
    counted = _artist_counts(db, title, first_only=False)
    ranked = sorted(((counted[key], key) for key in candidates), reverse=True)
    if ranked and ranked[0][0] > 0 and (len(ranked) == 1 or ranked[0][0] > ranked[1][0]):
        return ranked[0][1]
    return None


def decide(db: OrmSession, title: Title, raw: list[str], *, album_first: bool = False) -> Decision | None:
    """The genres of an album from the genre values of its files. None: the switch is off, or nothing is known; the
    tag then stays as it is. ``album_first``: the album keeps files of its own, its genre goes before theirs."""
    current = settings(db)
    if not current.enabled:
        return None
    keys, unknown = translate(raw, current.custom)
    came_from = "files"
    umbrella = umbrella_alone(raw, current.custom)
    if umbrella and (picked := _within(db, title, umbrella)) is not None:
        # "Pop, Rock" alone says "pop or rock": the artist's albums say which.
        keys, came_from = [picked], "artist"
    if album_first and _of_album(title):
        keys, came_from = _of_album(title), "album"
    if not keys:
        keys, came_from = _of_album(title), "album"
    if not keys:
        keys, came_from = _of_artist(db, title), "artist"
    if not keys:
        return Decision([], [], "", unknown) if unknown else None
    keys = _regional(db, title, keys, current.regional)
    return Decision(keys, names_of(keys, current.names), came_from, unknown)


def remember(title_id: int, decision: Decision | None) -> None:
    """After writing: the album keeps its genres (for the next file and the artist's other albums), and spellings
    nothing knew are counted for the owner. Never raises."""
    if decision is None:
        return
    try:
        with SessionLocal() as db:
            title = db.get(Title, title_id)
            if title is not None and decision.names and list(title.genres or []) != decision.names:
                title.genres = list(decision.names)
            if decision.unknown:
                counted = {str(k): int(v) for k, v in _json(db, SETTING_UNMAPPED).items() if isinstance(v, int)}
                for name in decision.unknown:
                    counted[name] = counted.get(name, 0) + 1
                kept = dict(sorted(counted.items(), key=lambda item: -item[1])[:UNMAPPED_MAX])
                set_setting(db, SETTING_UNMAPPED, json.dumps(kept, ensure_ascii=False))
                logger.info("Album %d: %d genre spellings nexcrate does not know", title_id, len(decision.unknown))
            db.commit()
    except Exception:  # noqa: BLE001 - remembering never undoes a filing
        logger.warning("Album %d: its genres could not be remembered", title_id)
