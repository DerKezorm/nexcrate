"""Reading a subtitle file's name and naming it next to the movie (C9).

**Reading** a name without its extension. A name that starts with the chosen video's name (case ignored) is read after
it, so nothing of the release name counts. From the end:

1. **Tags:** ``forced`` and ``foreign`` mean forced; ``sdh`` and ``cc`` mean for the hard of hearing; ``default`` is
   ignored. They stand after the language (``.de.forced``, ``5_German_Forced``) or right before it (``.sdh.en``).
2. **The language**, stored as ISO 639-1:

   * a region form, a language code joined by ``-`` or ``_`` to a region of 2 letters, 3 digits or a script
     (``pt-BR``, ``pt_BR``, ``es-419``, ``zh-Hans``), keeps its language;
   * else the last word, when it has 2 or 3 letters and is an ISO 639-1 or ISO 639-2 code (``de``, ``deu``, ``ger``);
   * else a language name of up to three words at the end, in English or German (``German``, ``Deutsch``,
     ``2_English``, ``Portuguese (Brazil)``).

   A word equal to a release group (``TEL`` would be Telugu) never counts as a language. Unknown stays unknown.
3. **hi** is the tag for the hard of hearing and the code of Hindi. With another language in the name it is the tag
   (``.en.hi``, ``.hi.en``); without one it is Hindi (``.hi``, ``.hi.forced``).

**Naming:** ``<movie file name without extension>[.<n>].<ISO 639-1>[.forced][.sdh].<ext>`` in NFC, the extension in
lower case, no language code when the language is unknown.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from ..schreibweisen import nfc
from . import languages

#: Subtitle extensions, compared in lower case.
EXTENSIONS = frozenset({".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx", ".sup", ".smi"})
FORCED_TAGS = frozenset({"forced", "foreign"})
SDH_TAGS = frozenset({"sdh", "cc"})
IGNORED_TAGS = frozenset({"default"})
#: Hearing impaired, or Hindi: see the module text.
HI = "hi"
#: Scripts a region form may name instead of a region.
SCRIPTS = frozenset({"hans", "hant", "latn", "cyrl", "arab"})

_WORD = re.compile(r"[^\W_]+")


@dataclass(frozen=True)
class Reading:
    #: ISO 639-1, None when unknown.
    language: str | None = None
    forced: bool = False
    sdh: bool = False


def without_prefix(name: str, prefix: str) -> str | None:
    """``name`` after ``prefix`` when it starts with it, case ignored; else None. Both in NFC."""
    text, start = nfc(name), nfc(prefix)
    if not start or len(text) < len(start) or text[: len(start)].casefold() != start.casefold():
        return None
    return text[len(start) :]


def _is_region(word: str) -> bool:
    return (
        (len(word) == 2 and word.isascii() and word.isalpha())
        or (len(word) == 3 and word.isascii() and word.isdigit())
        or word.casefold() in SCRIPTS
    )


def _language(text: str, words: list[re.Match[str]], end: int, groups: set[str]) -> tuple[str | None, int]:
    """The language ending at ``words[end - 1]`` and how many words it takes."""
    if end == 0:
        return None, 0
    last = words[end - 1]
    if end >= 2:
        first = words[end - 2]
        joined = text[first.end() : last.start()] in ("-", "_")
        if joined and _is_region(last.group()) and first.group().casefold() not in groups:
            code = languages.by_code(first.group())
            if code is not None:
                return code, 2
    if last.group().casefold() not in groups:
        code = languages.by_code(last.group())
        if code is not None:
            return code, 1
    for count in range(min(languages.MAX_NAME_WORDS, end), 0, -1):
        chosen = [word.group() for word in words[end - count : end]]
        if any(word.casefold() in groups for word in chosen):
            continue
        code = languages.by_name(" ".join(chosen))
        if code is not None:
            return code, count
    return None, 0


def read(name: str, *, video_name: str | None = None, groups: Iterable[str | None] = ()) -> Reading:
    """Language and tags of a subtitle file's name without its extension; a word in ``groups`` is never a language."""
    text = nfc(name)
    if video_name:
        rest = without_prefix(text, video_name)
        if rest is not None:
            text = rest
    taboo = {group.casefold() for group in groups if group}
    words = list(_WORD.finditer(text))
    forced = sdh = hearing = False
    end = len(words)
    while end > 0:
        word = words[end - 1].group().casefold()
        if word in FORCED_TAGS:
            forced = True
        elif word in SDH_TAGS:
            sdh = True
        elif word == HI:
            hearing = True
        elif word not in IGNORED_TAGS:
            break
        end -= 1
    language, used = _language(text, words, end, taboo)
    if language is None:
        return Reading(HI if hearing and HI not in taboo else None, forced, sdh)
    start = end - used
    while start > 0:
        word = words[start - 1].group().casefold()
        if word in FORCED_TAGS:
            forced = True
        elif word in SDH_TAGS or word == HI:
            sdh = True
        elif word not in IGNORED_TAGS:
            break
        start -= 1
    return Reading(language, forced, sdh or hearing)


def file_name(movie_name: str, reading: Reading, extension: str, copy: int | None = None) -> str:
    """``<movie_name>[.<copy>].<language>[.forced][.sdh]<extension>``; ``extension`` with its dot."""
    parts = [movie_name]
    if copy is not None:
        parts.append(str(copy))
    if reading.language:
        parts.append(reading.language)
    if reading.forced:
        parts.append("forced")
    if reading.sdh:
        parts.append("sdh")
    return nfc(".".join(parts) + extension.lower())
