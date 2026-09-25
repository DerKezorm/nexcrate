"""Spellings: umlauts, accents and "&" in one place.

Every place that compares, searches, sorts or names text goes through this module.

Why: release names and metadata in the wild use every form of a word. "Schöne" turns up as
"Schoene", "Schone" and even "Schne". Radarr, Sonarr and Lidarr each handle only some of those
forms and lose titles in the gap (measured on 12.09.2026: 57 % of albums with an umlaut had no
file, against 20 % without).

The rules
---------

1. **NFC everywhere.** Text from Radarr, from disk and from the user is stored and compared in
   Unicode NFC. macOS and some shares deliver NFD, where "ü" is two characters.
2. **Keys, not one spelling.** ``keys(text)`` returns comparison forms, all case folded, with
   punctuation, dots and underscores as spaces:

   (a) umlauts as ae, oe, ue and ß as ss,
   (b) umlauts and accents as the base letter,
   (c) umlauts dropped.

   Other letters: é→e, ø→o and oe, æ→ae, å→a and aa, ł→l, đ→d, þ→th. "&" also as "and" and
   "und". Two texts match when they share a key.
3. **Sorting** uses form (a) without a leading article (der, die, das, the, a, an), so "Ärger"
   sorts under A.

⚠️ The library search looks for the forms (a) and (b) of the query inside the keys of a title,
not for form (c). Form (c) of "schöne" is "schne", which is part of "Schnee": searching for one
word would find the other. A query typed without the umlaut ("schne") still finds "Schöne"
through the title's form (c).

**Indexer queries** (``query_spellings``, step 2c) ask for the forms a release name carries:
the text as written (umlauts, accents and ß kept), (a) and (b), each once. Never (c), for the
same reason. Matching a found release to a title still uses every key, (c) included.

Apostrophes are dropped, not turned into a space: "Don't" and "Dont" are the same word in
release names.

**File names** (step 3, ``replaced``): with the umlaut setting "replace", a name is written in form
(a) with its case kept and nothing else changed: "Ä" becomes "Ae", a word in capitals like "ÄRZTE"
becomes "AERZTE", other accents lose their mark. Punctuation and spaces stay as they are.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable

ARTICLES = frozenset({"der", "die", "das", "the", "a", "an"})
#: Separates the keys in a stored search text. Never part of a key: punctuation becomes a space.
SEPARATOR = "|"

_APOSTROPHES = frozenset("'‘’ʼ`´")

# Letters without a decomposition in Unicode. Everything with one (é, ñ, å) also loses its
# accent through NFD; the tables name only what NFD cannot do, and ä, ö, ü, å, whose forms differ.
_FORM_A = {
    "ä": "ae",
    "ö": "oe",
    "ü": "ue",
    "ß": "ss",
    "ø": "oe",
    "æ": "ae",
    "å": "aa",
    "œ": "oe",
    "ł": "l",
    "đ": "d",
    "ð": "d",
    "þ": "th",
    "ı": "i",
}
_FORM_B = {**_FORM_A, "ä": "a", "ö": "o", "ü": "u", "ø": "o", "å": "a"}
_FORM_C = {**_FORM_B, "ä": "", "ö": "", "ü": ""}

_AMPERSAND_WORDS = ("and", "und")


def nfc(text: str) -> str:
    """The text in Unicode NFC."""
    return unicodedata.normalize("NFC", text)


def nfc_or_none(value: object) -> str | None:
    """A string in NFC, or None for anything that is not a string."""
    return nfc(value) if isinstance(value, str) else None


def _folded(text: str) -> str:
    # Case folding can leave a decomposed sequence behind ("İ" becomes "i" plus a dot).
    return nfc(nfc(text).casefold())


def _form(folded: str, table: dict[str, str]) -> str:
    mapped = "".join(table.get(character, character) for character in folded)
    parts: list[str] = []
    for character in unicodedata.normalize("NFD", mapped):
        category = unicodedata.category(character)
        if category == "Mn" or character in _APOSTROPHES:
            continue
        # Punctuation (dots, underscores, dashes), symbols, separators and controls become spaces.
        parts.append(" " if category[0] in "PSZC" else character)
    return nfc(" ".join("".join(parts).split()))


def _variants(folded: str) -> list[str]:
    if "&" not in folded:
        return [folded]
    return [folded, *(folded.replace("&", f" {word} ") for word in _AMPERSAND_WORDS)]


def _collect(folded: str, tables: tuple[dict[str, str], ...]) -> list[str]:
    result: list[str] = []
    for variant in _variants(folded):
        for table in tables:
            key = _form(variant, table)
            if key and key not in result:
                result.append(key)
    return result


def keys(text: str | None) -> list[str]:
    """Every comparison form of a text: (a), (b) and (c), each with "&" as is, "and" and "und"."""
    if not text:
        return []
    return _collect(_folded(text), (_FORM_A, _FORM_B, _FORM_C))


def query_keys(query: str | None) -> list[str]:
    """The forms of a search query: (a) and (b). See the module note on why not (c)."""
    if not query or not query.strip():
        return []
    return _collect(_folded(query), (_FORM_A, _FORM_B))


def _written(text: str, *, casefold: bool = False) -> str:
    """The text as written, for a query: NFC and lower case, umlauts, accents and ß kept, punctuation as spaces.

    ``str.lower`` by default and not case folding, which would turn ß into ss and so into form (a).
    """
    parts: list[str] = []
    for character in nfc(nfc(text).casefold() if casefold else nfc(text).lower()):
        if character in _APOSTROPHES:
            continue
        parts.append(" " if unicodedata.category(character)[0] in "PSZC" else character)
    return nfc(" ".join("".join(parts).split()))


def query_spellings(text: str | None) -> list[str]:
    """The forms of a text an indexer is asked with, distinct and in this order: as written, (a), (b).

    Never (c), see the module note. "&" becomes a space in every form, which finds "und", "and" and "&" alike.
    """
    if not text or not text.strip():
        return []
    folded = _folded(text)
    result: list[str] = []
    for form in (_written(text), _form(folded, _FORM_A), _form(folded, _FORM_B)):
        if form and form not in result:
            result.append(form)
    return result


def folds_alike(first: str | None, second: str | None) -> bool:
    """Whether two texts are one text for a query once case is folded (ß as ss) and punctuation is a space.

    Umlauts and accents still count: "Café" and "Cafe" do not fold alike, "Straße" and "STRASSE" do.
    """
    return _written(first or "", casefold=True) == _written(second or "", casefold=True)


def _upper_context(text: str, index: int) -> bool:
    """Whether the capital letter at ``index`` stands in a word written in capitals.

    The next letter is a capital, or the previous one is and no lower-case letter follows: "ÄRZTE", "MÄDCHEN", "ÜBER",
    and "GROẞ" at the end of a word. A lone capital at the start of a word ("Äpfel", "Ä") is not.
    """
    following = text[index + 1] if index + 1 < len(text) else ""
    previous = text[index - 1] if index > 0 else ""
    if following.isalpha() and following.isupper():
        return True
    return previous.isalpha() and previous.isupper() and not (following.isalpha() and following.islower())


def replaced(text: str | None) -> str:
    """The text for a file name with umlauts replaced: form (a) with the case kept, in NFC.

    ä as ae, ß as ss, the other letters of form (a) as there, every other accent as its base letter. Capitals follow
    the word: "Ä" as "Ae", "ÄRZTE" as "AERZTE". Everything that is not a letter stays.
    """
    if not text:
        return ""
    source = nfc(text)
    parts: list[str] = []
    for index, character in enumerate(source):
        lower = character.lower()
        if len(lower) == 1 and lower in _FORM_A:
            replacement = _FORM_A[lower]
            if character != lower:
                replacement = replacement.upper() if _upper_context(source, index) else replacement.capitalize()
            parts.append(replacement)
            continue
        decomposed = unicodedata.normalize("NFD", character)
        parts.append("".join(mark for mark in decomposed if unicodedata.category(mark) != "Mn") or character)
    return nfc("".join(parts))


def share_a_key(first: str | None, second: str | None) -> bool:
    """Whether two texts match: they have at least one key in common."""
    return bool(set(keys(first)) & set(keys(second)))


def sort_key(text: str | None) -> str:
    """Form (a) without a leading article: "Die Schöne Straße" sorts as "schoene strasse"."""
    if not text:
        return ""
    words = _form(_folded(text), _FORM_A).split(" ")
    if len(words) > 1 and words[0] in ARTICLES:
        words = words[1:]
    return " ".join(words) or _folded(text)


def search_text(texts: Iterable[str | None]) -> str:
    """The keys of several texts in one stored string: ``|key|key|``. Empty without keys."""
    collected: list[str] = []
    for text in texts:
        for key in keys(text):
            if key not in collected:
                collected.append(key)
    return SEPARATOR + SEPARATOR.join(collected) + SEPARATOR if collected else ""


def matches(query: str | None, texts: Iterable[str | None]) -> bool:
    """Whether a search query finds one of the texts. The library does the same in SQL."""
    stored = search_text(texts)
    return any(key in stored for key in query_keys(query))
