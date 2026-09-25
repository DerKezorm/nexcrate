"""What a message says, in the mailbox's language.

A mailbox has no reader nexcrate knows, so its language is set on it, not taken from the interface. The owner's words:
"Alles was oben steht, aber pro Kanal auswählbar": twelve events in four groups, each switched and levelled per mailbox.
"""

from __future__ import annotations

from typing import Any

from ._problems import FAILED, PROBLEMS

#: The events a mailbox can switch on, in groups, in the order of the interface.
GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("loading", ("download_started", "download_imported", "download_upgraded", "download_failed")),
    ("attention", ("problem_opened",)),
    ("library", ("title_added", "title_removed", "file_deleted", "season_complete", "request_made")),
    ("operation", ("health_changed", "update_available")),
)
EVENT_KEYS: tuple[str, ...] = tuple(key for _group, keys in GROUPS for key in keys)

TITLES: dict[str, dict[str, str]] = {
    "de": {
        "download_started": "Download gestartet",
        "download_imported": "Neu in der Bibliothek",
        "download_upgraded": "Verbessert",
        "download_failed": "Download fehlgeschlagen",
        "problem_opened": "Ein Download braucht dich",
        "title_added": "Titel hinzugefügt",
        "title_removed": "Titel entfernt",
        "file_deleted": "Datei gelöscht",
        "season_complete": "Staffel vollständig",
        "request_made": "Neue Anfrage",
        "health_problem": "Etwas stimmt nicht",
        "health_resolved": "Wieder in Ordnung",
        "update_available": "Neue nexcrate-Version",
        "test": "nexcrate: Code {code}",
    },
    "en": {
        "download_started": "Download started",
        "download_imported": "New in the library",
        "download_upgraded": "Upgraded",
        "download_failed": "Download failed",
        "problem_opened": "A download needs you",
        "title_added": "Title added",
        "title_removed": "Title removed",
        "file_deleted": "File deleted",
        "season_complete": "Season complete",
        "request_made": "New request",
        "health_problem": "Something is wrong",
        "health_resolved": "Fine again",
        "update_available": "New nexcrate version",
        "test": "nexcrate: code {code}",
    },
}

#: When more than ``BURST`` messages of one kind come in one round (a takeover adds thousands of titles), one summary.
SUMMARIES: dict[str, dict[str, str]] = {
    "de": {
        "download_started": "{count} Downloads gestartet",
        "download_imported": "{count} Downloads abgelegt",
        "download_upgraded": "{count} Verbesserungen abgelegt",
        "download_failed": "{count} Downloads fehlgeschlagen",
        "problem_opened": "{count} Downloads brauchen dich",
        "title_added": "{count} Titel hinzugefügt",
        "title_removed": "{count} Titel entfernt",
        "file_deleted": "{count} Dateien gelöscht",
        "season_complete": "{count} Staffeln vollständig",
        "request_made": "{count} neue Anfragen",
    },
    "en": {
        "download_started": "{count} downloads started",
        "download_imported": "{count} downloads filed",
        "download_upgraded": "{count} upgrades filed",
        "download_failed": "{count} downloads failed",
        "problem_opened": "{count} downloads need you",
        "title_added": "{count} titles added",
        "title_removed": "{count} titles removed",
        "file_deleted": "{count} files deleted",
        "season_complete": "{count} seasons complete",
        "request_made": "{count} new requests",
    },
}

WORDS: dict[str, dict[str, str]] = {
    "de": {
        "version": "Fassung",
        "quality": "Qualität",
        "release": "Release",
        "season": "Staffel",
        "episodes": "Folgen",
        "replaced": "Ersetzt",
        "from": "Angefragt von",
        "more": "und {count} weitere",
        "filed": "{filed} Folgen abgelegt",
        "missing": "{missing} fehlen noch",
        "current": "Installiert ist {current}.",
        "test_body": "Wenn du das liest, kommen Meldungen von nexcrate hier an. Tippe den Code in nexcrate ein.",
        "movie": "Filme",
        "series": "Serien",
        "album": "Musik",
    },
    "en": {
        "version": "Version",
        "quality": "Quality",
        "release": "Release",
        "season": "Season",
        "episodes": "Episodes",
        "replaced": "Replaced",
        "from": "Requested by",
        "more": "and {count} more",
        "filed": "{filed} episodes filed",
        "missing": "{missing} still missing",
        "current": "{current} is installed.",
        "test_body": "If you read this, messages from nexcrate arrive here. Type the code into nexcrate.",
        "movie": "movies",
        "series": "series",
        "album": "music",
    },
}

#: The health findings of ``/api/v1/health``, in both languages; ``{name}`` and ``{kind}`` from their params.
HEALTH: dict[str, dict[str, str]] = {
    "de": {
        "indexer_none": "Kein Indexer ist verbunden und eingeschaltet.",
        "indexer_failing": "Der Indexer {name} ist beim letzten Mal gescheitert.",
        "download_client_none": "Kein Download-Programm ist verbunden und eingeschaltet.",
        "download_client_failing": "Das Download-Programm {name} ist beim letzten Mal gescheitert.",
        "tmdb_token_missing": "Es ist kein TMDB-Token gespeichert; Titel lassen sich nicht hinzufügen.",
        "automatic_off": "Die Automatik für {kind} ist aus; nichts lädt von selbst.",
        "folder_missing": "Der Ordner der Fassung {name} fehlt.",
        "folder_not_writable": "In den Ordner der Fassung {name} lässt sich nicht schreiben.",
        "disk_full": "Die Platte hinter der Fassung {name} ist voll.",
        "version_not_ready": "Die Fassung {name} ist nicht bereit.",
    },
    "en": {
        "indexer_none": "No indexer is connected and switched on.",
        "indexer_failing": "The indexer {name} failed last time.",
        "download_client_none": "No download client is connected and switched on.",
        "download_client_failing": "The download client {name} failed last time.",
        "tmdb_token_missing": "No TMDB token is stored; titles cannot be added.",
        "automatic_off": "The automatic for {kind} is off; nothing loads by itself.",
        "folder_missing": "The folder of the version {name} is missing.",
        "folder_not_writable": "The folder of the version {name} cannot be written to.",
        "disk_full": "The disk behind the version {name} is full.",
        "version_not_ready": "The version {name} is not ready.",
    },
}

BURST = 5


def language(value: str | None) -> str:
    return value if value in TITLES else "de"


def title(key: str, lang: str, **values: Any) -> str:
    return TITLES[language(lang)][key].format(**values)


def summary(key: str, lang: str, count: int) -> str:
    return SUMMARIES[language(lang)][key].format(count=count)


def word(name: str, lang: str, **values: Any) -> str:
    return WORDS[language(lang)][name].format(**values)


def problem(code: str | None, lang: str) -> str:
    texts = PROBLEMS[language(lang)]
    return texts.get(code or "", texts["unknown"])


def failed(reason: str | None, lang: str) -> str | None:
    return FAILED[language(lang)].get(reason or "")


def health(finding: dict[str, Any], lang: str) -> str:
    """A finding in the mailbox's language; an unknown code keeps the English sentence of the API."""
    params = finding.get("params") if isinstance(finding.get("params"), dict) else {}
    text = HEALTH[language(lang)].get(str(finding.get("code")))
    if text is None:
        return str(finding.get("message") or finding.get("code") or "")
    kind = str(params.get("kind") or "")
    return text.format(name=params.get("name") or "", kind=WORDS[language(lang)].get(kind, kind))


def episodes(pairs: list[dict[str, Any]], lang: str) -> str | None:
    """"S02E01-E04" for a run in one season, else the codes, at most eight and "and N more"."""
    codes = [
        (int(item["season"]), int(item["episode"]))
        for item in pairs
        if isinstance(item, dict) and isinstance(item.get("season"), int) and isinstance(item.get("episode"), int)
    ]
    if not codes:
        return None
    seasons = {season for season, _number in codes}
    numbers = sorted(number for _season, number in codes)
    if len(seasons) == 1 and len(codes) > 1 and numbers == list(range(numbers[0], numbers[-1] + 1)):
        season = next(iter(seasons))
        return f"S{season:02d}E{numbers[0]:02d}-E{numbers[-1]:02d}"
    named = [f"S{season:02d}E{number:02d}" for season, number in sorted(codes)]
    if len(named) > 8:
        return ", ".join(named[:8]) + " " + word("more", lang, count=len(named) - 8)
    return ", ".join(named)
