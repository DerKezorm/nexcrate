"""What a search knows about the title, its versions and the indexers, without the database."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .. import delay as delay_rules
from .. import schreibweisen
from ..releases import CurrentFile

_IMDB = re.compile(r"(?:tt)?(\d{1,12})", re.IGNORECASE)


def imdb_number(value: str | int | None) -> int | None:
    """The number of an IMDb id such as ``tt0000002``, without leading zeros; None without a usable one."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    found = _IMDB.fullmatch(value.strip())
    number = int(found.group(1)) if found is not None else 0
    return number if number > 0 else None


@dataclass(frozen=True)
class TitleInfo:
    title_id: int
    title: str
    original_title: str | None = None
    year: int | None = None
    tmdb_id: int | None = None
    #: The IMDb number without ``tt`` and leading zeros.
    imdb_id: int | None = None
    #: ISO 639-1.
    original_language: str | None = None
    runtime_min: int | None = None
    #: The spelling keys of the title, the original title and every alternative title.
    keys: frozenset[str] = frozenset()


def title_info(
    *,
    title_id: int,
    title: str,
    original_title: str | None = None,
    year: int | None = None,
    tmdb_id: int | None = None,
    imdb_id: str | int | None = None,
    original_language: str | None = None,
    runtime_min: int | None = None,
    alternative_titles: Iterable[str | None] = (),
    stored_keys: Iterable[str | None] = (),
) -> TitleInfo:
    """A title with its keys: those of its texts, and stored ``|key|key|`` strings (TMDB keeps only keys)."""
    keys: set[str] = set()
    for text in (title, original_title, *alternative_titles):
        keys.update(schreibweisen.keys(text))
    for stored in stored_keys:
        keys.update(key for key in (stored or "").split(schreibweisen.SEPARATOR) if key)
    return TitleInfo(
        title_id=title_id,
        title=title,
        original_title=original_title,
        year=year,
        tmdb_id=tmdb_id if tmdb_id is not None and tmdb_id > 0 else None,
        imdb_id=imdb_number(imdb_id),
        original_language=original_language,
        runtime_min=runtime_min,
        keys=frozenset(keys),
    )


@dataclass(frozen=True)
class IndexerInfo:
    indexer_id: int
    name: str
    #: newznab or torznab.
    kind: str
    url: str
    #: ⚠️ Decrypted and in memory only while the indexer is asked; never in a repr, an answer or a log line.
    api_key: str = field(default="", repr=False)
    categories: tuple[int, ...] = ()
    #: The categories of a series search (S3); empty means the indexer is skipped for series.
    series_categories: tuple[int, ...] = ()
    #: The categories of an album search (M3); None means the default from the caps, empty skips the indexer.
    music_categories: tuple[int, ...] | None = None
    #: The categories an anime series also asks (A3); None means the default from the caps.
    anime_categories: tuple[int, ...] | None = None
    #: B3: whether an anime series is asked in the standard form too, not only by its number counted through.
    anime_standard_format_search: bool = True
    caps: dict[str, Any] | None = None
    #: Caps older than 7 days are fetched again first.
    caps_stale: bool = False
    paused_until: datetime | None = None
    priority: int = 25
    minimum_seeders: int | None = None
    #: How many days the Usenet clients' news servers keep articles; None for no limit or a torrent indexer.
    retention_days: int | None = None
    multi_languages: tuple[str, ...] = ()
    remove_year: bool = False
    #: Set when the indexer cannot be asked at all (its stored key cannot be read): it fails with this code.
    error_code: str | None = None

    @property
    def protocol(self) -> str:
        return "torrent" if self.kind == "torznab" else "usenet"


@dataclass(frozen=True)
class VersionInfo:
    #: The version definition, as everywhere in the API.
    version_id: int
    label: str
    has_profile: bool = False
    #: The profile's rules; None without a profile of a kind the engine knows.
    rules: dict[str, Any] | None = None
    #: The version's file; None without one.
    current_file: CurrentFile | None = None
    #: The delay rule of the version: a protocol switched off does not fit, the preferred one wins a tie.
    delay: delay_rules.Rule = delay_rules.DEFAULT
