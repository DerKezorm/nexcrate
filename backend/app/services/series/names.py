"""Placeholder names of episodes (decision 49).

TMDB never gives an empty name in a language without a translation; it gives the word for episode and a number
("Folge 9", "Episode 9"). Such a name is no name: the English name is shown where one is stored, and a file name
falls back to it, else to ``TBA``.

Another language adds its word to ``WORDS``.
"""

from __future__ import annotations

import re

#: The word for episode by ISO 639-1, as TMDB writes its placeholders. English is always checked too.
WORDS: dict[str, tuple[str, ...]] = {
    "de": ("Folge",),
    "en": ("Episode",),
}
_FALLBACK = "en"


def _pattern(words: tuple[str, ...]) -> re.Pattern[str]:
    alternatives = "|".join(re.escape(word) for word in words)
    return re.compile(rf"(?:{alternatives})\s*\d{{1,5}}", re.IGNORECASE)


_PATTERNS = {language: _pattern(words) for language, words in WORDS.items()}


def language_of(code: str | None) -> str:
    """ISO 639-1 of an account language or a locale such as ``de-DE``."""
    return (code or "").strip().lower().split("-", 1)[0].split("_", 1)[0] or _FALLBACK


def is_placeholder(name: str | None, language: str | None = None) -> bool:
    """Whether a name is only the word for episode, in the language or in English, and a number."""
    text = " ".join((name or "").split())
    if not text:
        return False
    for code in {language_of(language), _FALLBACK}:
        pattern = _PATTERNS.get(code)
        if pattern is not None and pattern.fullmatch(text):
            return True
    return False


def usable(name: str | None, language: str | None = None) -> str | None:
    """The name when it is a real one; None for an empty name or a placeholder."""
    text = (name or "").strip()
    return text if text and not is_placeholder(text, language) else None


def display_name(name: str | None, name_en: str | None, language: str | None = None) -> str:
    """The name to show: a real name, else a real English name, else the placeholder as it is."""
    return usable(name, language) or usable(name_en, _FALLBACK) or (name or "")


def file_title(name: str | None, name_en: str | None, language: str | None = None) -> str | None:
    """The title in a file name: a real name, else a real English name, else None, which becomes ``TBA``."""
    return usable(name, language) or usable(name_en, _FALLBACK)


def account_language(db: object) -> str:
    """The account's language as ISO 639-1."""
    from ...models import ACCOUNT_ID, Account

    account = db.get(Account, ACCOUNT_ID)  # type: ignore[attr-defined]
    return language_of(account.language if account is not None else None)
