"""The titles a search asks for in the languages its profiles want.

A release in another language is named after the title in that language ("Le Titre 2024 FRENCH 1080p"), and many
indexers of such releases cannot search by number. The plain search with the title in the account language and the
original title never finds it. So the search asks with the title in every language a profile of the title wants too:

* **The languages** come from each version's rules: every code among the answers ``languages`` (any role), and every
  language a format with a positive score asks for (``LanguageSpecification``, neither negated nor "except"), which
  covers profiles set by hand and taken over from Radarr or Sonarr. ``original`` is the original title, already asked.
* **The titles** come from TMDB's translations of the title (``/movie/{id}/translations``, ``/tv/{id}/translations``),
  every title of the language, the one of the language's own country first ("fr-FR" before "fr-CA"). They are kept in
  the TMDB cache for ``tmdb.DETAIL_TTL``; ``refresh_title`` fetches them before a search when they are missing, and
  every loader reads them from the cache, so a search, its plan and its budget ask the same.
* A title that folds alike with one already asked is not asked again (``plan`` and ``series`` drop it).
* Without a TMDB token, a TMDB number or a wanted language nothing is asked; a failing TMDB costs the search nothing
  but these titles.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select

from ...db import SessionLocal
from ...models import Title, Version
from .. import schreibweisen, tmdb
from ..profiles import store as profile_store
from ..releases import languages as lang
from .model import TitleInfo

logger = logging.getLogger("nexcrate.search")

#: TMDB's path segment by kind of title.
TMDB_KINDS = {"movie": "movie", "series": "tv"}


def codes_of(rules: dict[str, Any] | None) -> list[str]:
    """The ISO 639-1 codes a profile's rules want, in the order they name them."""
    found: list[str] = []

    def add(code: object) -> None:
        normalized = lang.normalize_iso(code)
        if normalized is not None and normalized not in found:
            found.append(normalized)

    if not isinstance(rules, dict):
        return found
    for entry in rules.get("languages") or []:
        if isinstance(entry, dict):
            add(entry.get("code"))
    for format_ in rules.get("formats") or []:
        if not isinstance(format_, dict):
            continue
        score = format_.get("score")
        if isinstance(score, bool) or not isinstance(score, int | float) or score <= 0:
            continue
        for specification in format_.get("specifications") or []:
            if not isinstance(specification, dict) or specification.get("implementation") != "LanguageSpecification":
                continue
            fields = specification.get("fields") if isinstance(specification.get("fields"), dict) else {}
            if specification.get("negate") or fields.get("exceptLanguage") is True:
                continue
            value = fields.get("value")
            if isinstance(value, int) and not isinstance(value, bool):
                add(lang.iso_of_id(value))
    return found


def codes_of_all(rules: Iterable[dict[str, Any] | None]) -> list[str]:
    found: list[str] = []
    for one in rules:
        for code in codes_of(one):
            if code not in found:
                found.append(code)
    return found


def title_codes(db: Any, title_id: int) -> list[str]:
    """The codes the profiles of a title's versions want."""
    ids = list(db.scalars(select(Version.version_definition_id).where(Version.title_id == title_id)))
    return codes_of_all(profile_store.rules_by_versions(db, ids).values())


def _key(kind: str, tmdb_id: int) -> str:
    return tmdb.cache_key("translations", kind, tmdb_id)


def slim(data: Any) -> list[dict[str, str]]:
    """TMDB's translations as ``{language, country, title}``, only those with a title."""
    kept: list[dict[str, str]] = []
    translations = data.get("translations") if isinstance(data, dict) else None
    for item in translations if isinstance(translations, list) else []:
        if not isinstance(item, dict):
            continue
        language = lang.normalize_iso(item.get("iso_639_1"))
        texts = item.get("data") if isinstance(item.get("data"), dict) else {}
        text = texts.get("title") or texts.get("name")
        if language is None or not isinstance(text, str) or not text.strip():
            continue
        country = item.get("iso_3166_1")
        kept.append(
            {
                "language": language,
                "country": country.upper() if isinstance(country, str) else "",
                "title": text.strip(),
            }
        )
    return kept


def titles(kind: str, tmdb_id: int | None, codes: Iterable[str]) -> tuple[str, ...]:
    """The stored titles of the languages in ``codes``, in their order; empty when nothing is stored."""
    wanted = list(codes)
    if not wanted or not tmdb_id or tmdb_id <= 0 or kind not in TMDB_KINDS:
        return ()
    stored = tmdb.cache_get(_key(kind, tmdb_id))
    if not isinstance(stored, list):
        return ()
    found: list[str] = []
    for code in wanted:
        of_language = [item for item in stored if isinstance(item, dict) and item.get("language") == code]
        of_language.sort(key=lambda item: item.get("country") != code.upper())
        for item in of_language:
            text = str(item.get("title") or "")
            if text and text not in found:
                found.append(text)
    return tuple(found)


def _wanted(title_id: int) -> tuple[str, int, list[str]] | None:
    with SessionLocal() as db:
        title = db.get(Title, title_id)
        if title is None or title.kind not in TMDB_KINDS or not title.tmdb_id or title.tmdb_id <= 0:
            return None
        codes = title_codes(db, title_id)
        return (title.kind, title.tmdb_id, codes) if codes else None


async def fetch(kind: str, tmdb_id: int) -> bool:
    """TMDB's translations of a title into the cache, unless they are there. True when they are stored afterwards."""
    stored, token = await asyncio.to_thread(tmdb.token_state)
    if not stored or not token:
        return False
    try:
        await tmdb._cached(
            token,
            _key(kind, tmdb_id),
            tmdb.DETAIL_TTL,
            f"/{TMDB_KINDS[kind]}/{tmdb_id}/translations",
            {},
            title=True,
            slim=slim,
        )
    except tmdb.TmdbError as exc:
        logger.warning("TMDB translations of %s %d not usable: %s", kind, tmdb_id, exc.code)
        return False
    return True


async def refresh_title(title_id: int) -> bool:
    """Fetch TMDB's translations of a title before its search when a profile wants a language and none are stored.
    True when they are stored afterwards."""
    wanted = await asyncio.to_thread(_wanted, title_id)
    if wanted is None:
        return False
    kind, tmdb_id, _codes = wanted
    return await fetch(kind, tmdb_id)


def with_titles(info: TitleInfo, found: Iterable[str]) -> TitleInfo:
    """A movie's search title with further language titles: asked, and names that need the year."""
    added = [text for text in found if text and text not in info.language_texts]
    if not added:
        return info
    keys = set(info.keys)
    others = list(info.other_texts)
    for text in added:
        keys.update(schreibweisen.keys(text))
        if text not in info.texts and text not in others:
            others.append(text)
    return dataclasses.replace(
        info, keys=frozenset(keys), other_texts=tuple(others), language_texts=(*info.language_texts, *added)
    )


async def complete(info: TitleInfo, rules: Iterable[dict[str, Any] | None]) -> TitleInfo:
    """A movie's search title with the titles in the languages ``rules`` want, fetched first when missing. A movie
    in no library (a preview) has no title of its own to refresh by."""
    codes = codes_of_all(rules)
    if not codes or not info.tmdb_id:
        return info
    await fetch("movie", info.tmdb_id)
    found = await asyncio.to_thread(titles, "movie", info.tmdb_id, codes)
    return with_titles(info, found)
