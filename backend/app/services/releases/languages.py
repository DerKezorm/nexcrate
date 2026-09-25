"""Radarr's language numbers and ISO 639-1 codes.

TRaSH's LanguageSpecification names languages by Radarr's numbers (1 English, 2 French, 4 German, -2 the
movie's original language). Radarr sends a movie's original language as ``{id, name}``; TMDB sends ISO 639-1
(``original_language``). nexcrate stores ISO 639-1 on a title and turns it into Radarr's number where a rule
needs one.

The numbers and English names are Radarr's (read 13.09.2026); the ISO codes are the standard ones.
"""

from __future__ import annotations

from dataclasses import dataclass

UNKNOWN = 0
ANY = -1
#: Stands for the movie's original language in a rule, and in a parsed name until it is resolved.
ORIGINAL = -2


@dataclass(frozen=True)
class Language:
    id: int
    name: str
    iso: str | None


LANGUAGES: tuple[Language, ...] = (
    Language(0, "Unknown", None),
    Language(1, "English", "en"),
    Language(2, "French", "fr"),
    Language(3, "Spanish", "es"),
    Language(4, "German", "de"),
    Language(5, "Italian", "it"),
    Language(6, "Danish", "da"),
    Language(7, "Dutch", "nl"),
    Language(8, "Japanese", "ja"),
    Language(9, "Icelandic", "is"),
    Language(10, "Chinese", "zh"),
    Language(11, "Russian", "ru"),
    Language(12, "Polish", "pl"),
    Language(13, "Vietnamese", "vi"),
    Language(14, "Swedish", "sv"),
    Language(15, "Norwegian", "no"),
    Language(16, "Finnish", "fi"),
    Language(17, "Turkish", "tr"),
    Language(18, "Portuguese", "pt"),
    Language(19, "Flemish", "nl"),
    Language(20, "Greek", "el"),
    Language(21, "Korean", "ko"),
    Language(22, "Hungarian", "hu"),
    Language(23, "Hebrew", "he"),
    Language(24, "Lithuanian", "lt"),
    Language(25, "Czech", "cs"),
    Language(26, "Hindi", "hi"),
    Language(27, "Romanian", "ro"),
    Language(28, "Thai", "th"),
    Language(29, "Bulgarian", "bg"),
    Language(30, "Portuguese (Brazil)", "pt"),
    Language(31, "Arabic", "ar"),
    Language(32, "Ukrainian", "uk"),
    Language(33, "Persian", "fa"),
    Language(34, "Bengali", "bn"),
    Language(35, "Slovak", "sk"),
    Language(36, "Latvian", "lv"),
    Language(37, "Spanish (Latino)", "es"),
    Language(38, "Catalan", "ca"),
    Language(39, "Croatian", "hr"),
    Language(40, "Serbian", "sr"),
    Language(41, "Bosnian", "bs"),
    Language(42, "Estonian", "et"),
    Language(43, "Tamil", "ta"),
    Language(44, "Indonesian", "id"),
    Language(45, "Telugu", "te"),
    Language(46, "Macedonian", "mk"),
    Language(47, "Slovenian", "sl"),
    Language(48, "Malayalam", "ml"),
    Language(49, "Kannada", "kn"),
    Language(50, "Albanian", "sq"),
    Language(51, "Afrikaans", "af"),
    Language(52, "Marathi", "mr"),
    Language(53, "Tagalog", "tl"),
    Language(54, "Urdu", "ur"),
    Language(55, "Romansh", "rm"),
    Language(56, "Mongolian", "mn"),
    Language(57, "Georgian", "ka"),
    Language(-1, "Any", None),
    Language(-2, "Original", None),
)

BY_ID = {language.id: language for language in LANGUAGES}
_BY_NAME = {language.name.casefold(): language for language in LANGUAGES}
#: ISO 639-1 to Radarr's number. The first language with a code wins (Dutch before Flemish, Portuguese before
#: Brazilian). TMDB writes Cantonese as ``cn`` and Norwegian Bokmål as ``nb``.
_BY_ISO: dict[str, int] = {}
for _language in LANGUAGES:
    if _language.iso is not None:
        _BY_ISO.setdefault(_language.iso, _language.id)
_BY_ISO.update({"cn": 10, "nb": 15, "nn": 15})
#: The ISO 639-1 codes of Radarr's languages, without TMDB's extra spellings: what an indexer's MULTi languages hold.
ISO_CODES: frozenset[str] = frozenset(language.iso for language in LANGUAGES if language.iso is not None)


def iso_of_id(language_id: int) -> str | None:
    """The ISO 639-1 code of Radarr's language number; None for Unknown, Any, Original and numbers Radarr lacks."""
    found = BY_ID.get(language_id)
    return found.iso if found is not None else None


def iso_of_radarr(value: object) -> str | None:
    """The ISO 639-1 code of Radarr's ``originalLanguage`` object (or its name), or None."""
    if isinstance(value, dict):
        name = value.get("name")
        number = value.get("id")
        found = _BY_NAME.get(name.casefold()) if isinstance(name, str) else None
        if found is None and isinstance(number, int) and not isinstance(number, bool):
            found = BY_ID.get(number)
        return found.iso if found is not None else None
    if isinstance(value, str):
        found = _BY_NAME.get(value.casefold())
        return found.iso if found is not None else None
    return None


def normalize_iso(value: object) -> str | None:
    """A two letter code as TMDB or the API gives it, lower case; None for anything else."""
    if not isinstance(value, str):
        return None
    code = value.strip().lower()
    return code if len(code) == 2 and code.isascii() and code.isalpha() else None


def radarr_id(iso: str | None) -> int:
    """Radarr's number for an ISO 639-1 code; Unknown for none or an unknown code."""
    return _BY_ISO.get(iso, UNKNOWN) if iso else UNKNOWN


def name(language_id: int) -> str:
    found = BY_ID.get(language_id)
    return found.name if found is not None else "Unknown"


def stored_names(value: object) -> tuple[str, ...]:
    """A file's stored language names as the engines take them; anything but a list of names gives none."""
    if not isinstance(value, list | tuple):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def ids_of_names(names: object, original_iso: str | None = None) -> tuple[int, ...]:
    """Radarr's numbers of stored language names ("German"), in their order without repeats; a name Radarr does not
    know is left out, and anything but a list of names gives nothing. A stored "Original" (Sonarr keeps it for some DL
    files) is the title's original language, left out while that is unknown."""
    if not isinstance(names, list | tuple):
        return ()
    original = radarr_id(original_iso)
    found: list[int] = []
    for value in names:
        language = _BY_NAME.get(value.casefold()) if isinstance(value, str) else None
        number = language.id if language is not None else None
        if number == ORIGINAL:
            number = original if original != UNKNOWN else None
        if number is not None and number not in found:
            found.append(number)
    return tuple(found)
