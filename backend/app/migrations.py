"""Numbered schema migrations: what ``create_all`` and the missing-column upkeep cannot do.

SQLite cannot make a column nullable in place; the table has to be rebuilt. The schema number
lives in the settings table under ``schema_version``. ``init_db`` copies the database first
(``VACUUM INTO``), then runs every migration above the stored number, in order, then adds new
tables, columns and indexes. A fresh installation gets the newest schema from the models and the
newest number directly.

⚠️ A migration is written against the schema of its time, never against the current models. The
models go on changing; an old database must still arrive at the same place.

⚠️ A migration also checks whether it is needed. A lost number must not rebuild a table a second
time or overwrite what is already there.

⚠️ A rebuild runs with foreign keys switched off, as SQLite's documentation prescribes
(https://www.sqlite.org/lang_altertable.html, "otheralter"). With them on, ``DROP TABLE versions``
would first delete every row and set ``history.version_id`` to null for the whole history.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import Engine

logger = logging.getLogger("nexcrate.db")

SETTING_KEY = "schema_version"


@dataclass(frozen=True)
class Migration:
    number: int
    name: str
    #: Whether the database still needs it. Runs outside a transaction, reads only.
    needed: Callable[[sqlite3.Cursor], bool]
    #: The change itself, inside the transaction the runner opens.
    apply: Callable[[sqlite3.Cursor], None]
    #: Tables whose foreign keys are checked before the commit.
    checked_tables: tuple[str, ...] = ()


# --- Helpers ------------------------------------------------------------------------ #


def _columns(cursor: sqlite3.Cursor, table: str) -> dict[str, tuple[object, ...]]:
    """Column name to its ``PRAGMA table_info`` row. Empty when the table does not exist."""
    return {row[1]: tuple(row) for row in cursor.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _has_table(cursor: sqlite3.Cursor, table: str) -> bool:
    row = cursor.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
    return row is not None


# --- Migration 1: versions without a source ------------------------------------------- #

#: The table as migration 1 leaves it: the step-1 table with ``source_id`` nullable and ``added_by``.
_VERSIONS_1 = """
CREATE TABLE versions_migrating (
	id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
	title_id INTEGER NOT NULL,
	version_definition_id INTEGER NOT NULL,
	source_id INTEGER,
	added_by VARCHAR(16) DEFAULT 'import' NOT NULL,
	radarr_movie_id INTEGER,
	monitored BOOLEAN NOT NULL,
	has_file BOOLEAN NOT NULL,
	file_ref VARCHAR(64),
	quality VARCHAR(100),
	cutoff_not_met BOOLEAN NOT NULL,
	size BIGINT NOT NULL,
	languages JSON NOT NULL,
	release_group VARCHAR(200),
	relative_path VARCHAR(2048),
	profile_name VARCHAR(200),
	root_folder VARCHAR(1024),
	upgrade_to VARCHAR(200),
	state VARCHAR(16) NOT NULL,
	progress FLOAT,
	problem_code VARCHAR(64),
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	CONSTRAINT uq_versions_title_definition UNIQUE (title_id, version_definition_id),
	FOREIGN KEY(title_id) REFERENCES titles (id) ON DELETE CASCADE,
	FOREIGN KEY(version_definition_id) REFERENCES version_definitions (id) ON DELETE RESTRICT,
	FOREIGN KEY(source_id) REFERENCES sources (id) ON DELETE CASCADE
)
"""

#: The columns of step 1, copied as they are.
_VERSIONS_STEP_1_COLUMNS = (
    "id",
    "title_id",
    "version_definition_id",
    "source_id",
    "radarr_movie_id",
    "monitored",
    "has_file",
    "file_ref",
    "quality",
    "cutoff_not_met",
    "size",
    "languages",
    "release_group",
    "relative_path",
    "profile_name",
    "root_folder",
    "upgrade_to",
    "state",
    "progress",
    "problem_code",
    "created_at",
    "updated_at",
)


def _versions_need_rebuild(cursor: sqlite3.Cursor) -> bool:
    columns = _columns(cursor, "versions")
    if not columns:
        # No table at all: create_all makes it in the new shape.
        return False
    source_not_null = bool(columns.get("source_id", (None, None, None, 0))[3])
    return source_not_null or "added_by" not in columns


def _rebuild_versions(cursor: sqlite3.Cursor) -> None:
    old_columns = _columns(cursor, "versions")
    copied = [name for name in _VERSIONS_STEP_1_COLUMNS if name in old_columns]
    # The objects that go with the old table: indexes and triggers, recreated by their own SQL.
    attached = [
        row[0]
        for row in cursor.execute(
            "SELECT sql FROM sqlite_master WHERE tbl_name = 'versions' AND type IN ('index', 'trigger') "
            "AND sql IS NOT NULL ORDER BY rowid"
        ).fetchall()
    ]
    # ⚠️ Ids are never reused. The sequence can be ahead of the largest id still present; a copy
    # alone would hand out the id of a deleted version again.
    sequence_row = (
        cursor.execute("SELECT seq FROM sqlite_sequence WHERE name = 'versions'").fetchone()
        if _has_table(cursor, "sqlite_sequence")
        else None
    )

    cursor.execute(_VERSIONS_1)
    column_list = ", ".join(copied)
    added_by = "added_by" if "added_by" in old_columns else "'import'"
    # Names come from the fixed list above, never from outside.
    cursor.execute(
        f"INSERT INTO versions_migrating ({column_list}, added_by) SELECT {column_list}, {added_by} FROM versions"  # noqa: S608
    )
    cursor.execute("DROP TABLE versions")
    cursor.execute("ALTER TABLE versions_migrating RENAME TO versions")
    for statement in attached:
        cursor.execute(statement)
    if sequence_row is not None:
        cursor.execute(
            "UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = 'versions'",
            (int(sequence_row[0]),),
        )
        if cursor.rowcount == 0:
            cursor.execute("INSERT INTO sqlite_sequence (name, seq) VALUES ('versions', ?)", (int(sequence_row[0]),))


# --- Migration 2: profiles and the original language of a title ------------------------------ #

#: The table as migration 2 creates it (step 2b).
_PROFILES_2 = """
CREATE TABLE profiles (
	id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
	version_definition_id INTEGER NOT NULL,
	kind VARCHAR(16) NOT NULL,
	answers JSON NOT NULL,
	rules JSON NOT NULL,
	trash_commit VARCHAR(64) NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	FOREIGN KEY(version_definition_id) REFERENCES version_definitions (id) ON DELETE CASCADE
)
"""
_PROFILES_2_INDEX = "CREATE UNIQUE INDEX ix_profiles_version_definition_id ON profiles (version_definition_id)"


def _profiles_needed(cursor: sqlite3.Cursor) -> bool:
    if not _has_table(cursor, "profiles"):
        return True
    return _has_table(cursor, "titles") and "original_language" not in _columns(cursor, "titles")


def _add_profiles(cursor: sqlite3.Cursor) -> None:
    if not _has_table(cursor, "profiles"):
        cursor.execute(_PROFILES_2)
        cursor.execute(_PROFILES_2_INDEX)
    if _has_table(cursor, "titles") and "original_language" not in _columns(cursor, "titles"):
        # Nullable: every existing title gets it with its next import or TMDB refresh.
        cursor.execute("ALTER TABLE titles ADD COLUMN original_language VARCHAR(8)")


# --- Migration 3: the search settings of the indexers --------------------------------------------- #

#: The columns as migration 3 adds them (step 2c), with Radarr's defaults.
_INDEXER_COLUMNS_3 = (
    ("priority", "INTEGER DEFAULT 25 NOT NULL"),
    ("minimum_seeders", "INTEGER"),
    ("multi_languages", "JSON DEFAULT '[]' NOT NULL"),
    ("remove_year", "BOOLEAN DEFAULT 0 NOT NULL"),
    ("automatic_search", "BOOLEAN DEFAULT 1 NOT NULL"),
)


def _indexer_settings_needed(cursor: sqlite3.Cursor) -> bool:
    columns = _columns(cursor, "indexers")
    # No table at all: create_all makes it with the columns.
    return bool(columns) and any(name not in columns for name, _definition in _INDEXER_COLUMNS_3)


def _add_indexer_settings(cursor: sqlite3.Cursor) -> None:
    columns = _columns(cursor, "indexers")
    for name, definition in _INDEXER_COLUMNS_3:
        if name not in columns:
            # Names and definitions come from the fixed list above, never from outside.
            cursor.execute(f'ALTER TABLE indexers ADD COLUMN "{name}" {definition}')
    if "minimum_seeders" not in columns:
        # Radarr's default for torrents is one seeder; Usenet has no seeders and keeps null.
        cursor.execute("UPDATE indexers SET minimum_seeders = 1 WHERE kind = 'torznab'")


# --- Migration 4: the folder of a version definition ------------------------------------------------ #

#: The column as migration 4 adds it (step 3). The tables of step 3 (download clients, downloads, the blocklist) are new
#: and come from ``create_all``.
_FOLDER_COLUMN_4 = "VARCHAR(4096)"


def _version_folder_needed(cursor: sqlite3.Cursor) -> bool:
    columns = _columns(cursor, "version_definitions")
    # No table at all: create_all makes it with the column.
    return bool(columns) and "folder" not in columns


def _add_version_folder(cursor: sqlite3.Cursor) -> None:
    if "folder" not in _columns(cursor, "version_definitions"):
        # Nullable without a default: every existing version has no folder until the owner picks one.
        cursor.execute(f"ALTER TABLE version_definitions ADD COLUMN folder {_FOLDER_COLUMN_4}")


# --- Migration 5: taking over from Radarr, the naming of a version ------------------------------------------- #

#: The columns as migration 5 adds them (the takeover plan), all nullable without a default.
_COLUMNS_5 = (
    ("sources", "taken_over_at", "DATETIME"),
    ("versions", "source_movie_path", "VARCHAR(4096)"),
    ("versions", "release_title", "VARCHAR(1024)"),
    ("versions", "minimum_availability", "VARCHAR(32)"),
    ("version_definitions", "naming_movie_folder", "VARCHAR(1000)"),
    ("version_definitions", "naming_movie_file", "VARCHAR(1000)"),
)


def _takeover_columns_needed(cursor: sqlite3.Cursor) -> bool:
    for table, name, _definition in _COLUMNS_5:
        columns = _columns(cursor, table)
        # No table at all: create_all makes it with the column.
        if columns and name not in columns:
            return True
    return False


def _add_takeover_columns(cursor: sqlite3.Cursor) -> None:
    for table, name, definition in _COLUMNS_5:
        columns = _columns(cursor, table)
        if columns and name not in columns:
            # Names and definitions come from the fixed list above, never from outside. Null everywhere: no source is
            # taken over, the next import keeps the movie folders and release names, every version names by default.
            cursor.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}')


# --- Migration 6: searching and loading by itself ------------------------------------------------------------ #

#: The columns as migration 6 adds them ("Data model, migration 6"). Null where nothing is known
#: yet; the escalation level starts at 0 and every existing download was the owner's. The table ``extra_files`` is new
#: and comes from ``create_all``; the switches are rows in ``settings`` that nobody wrote yet, so their defaults apply.
_COLUMNS_6 = (
    ("titles", "next_search_at", "DATETIME"),
    ("titles", "last_search_at", "DATETIME"),
    ("titles", "next_search_reason", "VARCHAR(32)"),
    ("titles", "search_summary", "JSON"),
    ("versions", "replacement_times", "JSON"),
    ("indexers", "daily_limit", "INTEGER"),
    ("indexers", "api_current", "INTEGER"),
    ("indexers", "api_max", "INTEGER"),
    ("indexers", "grab_current", "INTEGER"),
    ("indexers", "grab_max", "INTEGER"),
    ("indexers", "api_next_at", "DATETIME"),
    ("indexers", "grab_next_at", "DATETIME"),
    ("indexers", "limits_seen_at", "DATETIME"),
    ("indexers", "escalation_level", "INTEGER DEFAULT 0 NOT NULL"),
    ("indexers", "initial_failure_at", "DATETIME"),
    ("indexers", "automatic_paused_until", "DATETIME"),
    ("indexers", "rss_newest_at", "DATETIME"),
    ("indexers", "rss_newest_key", "VARCHAR(64)"),
    ("indexers", "rss_last_at", "DATETIME"),
    ("indexers", "rss_gap_at", "DATETIME"),
    ("downloads", "origin", "VARCHAR(16) DEFAULT 'manual' NOT NULL"),
)


def _automatic_columns_needed(cursor: sqlite3.Cursor) -> bool:
    for table, name, _definition in _COLUMNS_6:
        columns = _columns(cursor, table)
        # No table at all: create_all makes it with the column.
        if columns and name not in columns:
            return True
    return False


def _add_automatic_columns(cursor: sqlite3.Cursor) -> None:
    for table, name, definition in _COLUMNS_6:
        columns = _columns(cursor, table)
        if columns and name not in columns:
            # Names and definitions come from the fixed list above, never from outside.
            cursor.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}')


_COLUMNS_7: tuple[tuple[str, str, str], ...] = (
    ("versions", "companion_state", "VARCHAR(32)"),
    ("versions", "companion_written_at", "DATETIME"),
    ("versions", "companion_sha256", "VARCHAR(64)"),
    ("versions", "quality_from", "VARCHAR(16)"),
    ("versions", "media_info", "JSON"),
    ("versions", "media_read_at", "DATETIME"),
)


def _disk_columns_needed(cursor: sqlite3.Cursor) -> bool:
    for table, name, _definition in _COLUMNS_7:
        columns = _columns(cursor, table)
        if columns and name not in columns:
            return True
    return False


def _add_disk_columns(cursor: sqlite3.Cursor) -> None:
    for table, name, definition in _COLUMNS_7:
        columns = _columns(cursor, table)
        if columns and name not in columns:
            # Names and definitions come from the fixed list above, never from outside.
            cursor.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}')


_COLUMNS_8: tuple[tuple[str, str, str], ...] = (
    ("titles", "series_type", "VARCHAR(16)"),
    ("titles", "tvdb_id", "INTEGER"),
    ("titles", "series_status", "VARCHAR(32)"),
    ("titles", "networks", "JSON"),
    ("titles", "first_air_date", "VARCHAR(10)"),
    ("titles", "last_air_date", "VARCHAR(10)"),
    ("titles", "next_air_date", "VARCHAR(10)"),
    ("titles", "episode_groups", "JSON"),
    ("titles", "numbering_note", "JSON"),
    ("versions", "watch_rule", "VARCHAR(16)"),
    ("versions", "watch_from_season", "INTEGER"),
    ("versions", "episode_counts", "JSON"),
    ("versions", "sonarr_series_id", "INTEGER"),
    ("versions", "source_series_path", "VARCHAR(4096)"),
    ("versions", "source_details", "JSON"),
    ("import_runs", "details", "JSON"),
)


def _series_columns_needed(cursor: sqlite3.Cursor) -> bool:
    for table, name, _definition in _COLUMNS_8:
        columns = _columns(cursor, table)
        if columns and name not in columns:
            return True
    return False


def _add_series_columns(cursor: sqlite3.Cursor) -> None:
    for table, name, definition in _COLUMNS_8:
        columns = _columns(cursor, table)
        if columns and name not in columns:
            # Names and definitions come from the fixed list above, never from outside.
            cursor.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}')


_COLUMNS_9: tuple[tuple[str, str, str], ...] = (("episode_files", "cutoff_not_met", "BOOLEAN"),)


def _episode_cutoff_needed(cursor: sqlite3.Cursor) -> bool:
    for table, name, _definition in _COLUMNS_9:
        columns = _columns(cursor, table)
        if columns and name not in columns:
            return True
    return False


def _add_episode_cutoff(cursor: sqlite3.Cursor) -> None:
    """The verdict per episode file: null means not judged, which counts as no upgrade (decision 33)."""
    for table, name, definition in _COLUMNS_9:
        columns = _columns(cursor, table)
        if columns and name not in columns:
            # Names and definitions come from the fixed list above, never from outside.
            cursor.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}')


# --- Migration 10: searching series ------------------------------------------ #

_COLUMNS_10: tuple[tuple[str, str, str], ...] = (
    ("indexers", "series_categories", "JSON NOT NULL DEFAULT '[]'"),
    ("titles", "title_en", "VARCHAR(1024)"),
    ("titles", "xem_checked_at", "DATETIME"),
    ("titles", "xem_state", "VARCHAR(32)"),
    ("titles", "episode_group_id", "VARCHAR(64)"),
    ("episodes", "name_en", "VARCHAR(1024)"),
)
#: Newznab's anime category; it and its subcategories stay out of the series default until anime follows.
_ANIME_CATEGORY = 5070
#: Sonarr's default plus UHD, for an indexer without usable caps (decision 5).
_SERIES_CATEGORIES_WITHOUT_CAPS = (5030, 5040, 5045)


def _series_search_needed(cursor: sqlite3.Cursor) -> bool:
    for table, name, _definition in _COLUMNS_10:
        columns = _columns(cursor, table)
        if columns and name not in columns:
            return True
    return False


def series_categories_from_caps(caps: object) -> list[int]:
    """Every category from 5000 to 5999 the caps list, anime and its subcategories left out; else Sonarr's default
    plus UHD. The same rule as ``indexers.default_series_categories``, kept here so the migration stands alone."""
    found: set[int] = set()
    groups = caps.get("categories") if isinstance(caps, dict) else None
    for group in groups if isinstance(groups, list) else []:
        if not isinstance(group, dict) or not isinstance(group.get("id"), int):
            continue
        anime = group["id"] == _ANIME_CATEGORY
        subcats = group.get("subcats") if isinstance(group.get("subcats"), list) else []
        for number in [group["id"], *(sub.get("id") for sub in subcats if isinstance(sub, dict))]:
            if isinstance(number, int) and 5000 <= number <= 5999 and not anime and number != _ANIME_CATEGORY:
                found.add(number)
    return sorted(found) if found else list(_SERIES_CATEGORIES_WITHOUT_CAPS)


def _add_series_search(cursor: sqlite3.Cursor) -> None:
    """The columns of S3. Every indexer gets the series categories its stored caps offer (decision 5)."""
    added_categories = False
    for table, name, definition in _COLUMNS_10:
        columns = _columns(cursor, table)
        if columns and name not in columns:
            # Names and definitions come from the fixed list above, never from outside.
            cursor.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}')
            added_categories = added_categories or name == "series_categories"
    if added_categories:
        for indexer_id, caps in cursor.execute("SELECT id, caps FROM indexers").fetchall():
            try:
                parsed = json.loads(caps) if caps else None
            except ValueError:
                parsed = None
            cursor.execute(
                "UPDATE indexers SET series_categories = ? WHERE id = ?",
                (json.dumps(series_categories_from_caps(parsed)), indexer_id),
            )


# --- Migration 11: loading and filing series (S4.0) ---------------------------------- #

_COLUMNS_11: tuple[tuple[str, str, str], ...] = (
    ("downloads", "scope", "VARCHAR(16)"),
    ("downloads", "season", "INTEGER"),
    ("downloads", "match_via", "VARCHAR(16)"),
    ("downloads", "filed_count", "INTEGER NOT NULL DEFAULT 0"),
    ("downloads", "open_count", "INTEGER NOT NULL DEFAULT 0"),
    ("downloads", "absent_count", "INTEGER NOT NULL DEFAULT 0"),
    ("episode_files", "file_ref", "VARCHAR(64)"),
    ("episode_files", "quality_from", "VARCHAR(16)"),
    ("episode_files", "named_tba", "BOOLEAN NOT NULL DEFAULT 0"),
    ("episode_files", "name_numbering", "VARCHAR(8)"),
    # A column with a reference may be added when its default is null (https://www.sqlite.org/lang_altertable.html).
    ("extra_files", "episode_file_id", "INTEGER REFERENCES episode_files (id) ON DELETE CASCADE"),
    ("version_definitions", "naming_series_folder", "VARCHAR(1000)"),
    ("version_definitions", "naming_season_folder", "VARCHAR(1000)"),
    ("version_definitions", "naming_specials_folder", "VARCHAR(1000)"),
    ("version_definitions", "naming_episode_file", "VARCHAR(1000)"),
    ("version_definitions", "naming_daily_file", "VARCHAR(1000)"),
    ("version_definitions", "episode_numbering", "VARCHAR(8)"),
)


def _series_loading_needed(cursor: sqlite3.Cursor) -> bool:
    for table, name, _definition in _COLUMNS_11:
        columns = _columns(cursor, table)
        if columns and name not in columns:
            return True
    return False


def _add_series_loading(cursor: sqlite3.Cursor) -> None:
    """The columns of S4. The tables download_episodes, download_files, season_folders and source_episodes come from
    create_all. Existing movie downloads keep every value; the new counts start at 0."""
    for table, name, definition in _COLUMNS_11:
        columns = _columns(cursor, table)
        if columns and name not in columns:
            # Names and definitions come from the fixed list above, never from outside.
            cursor.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}')


_COLUMNS_12: tuple[tuple[str, str, str], ...] = (
    ("titles", "id_search_seen", "JSON"),
    ("versions", "series_replacements", "JSON"),
    ("indexers", "rss_form", "VARCHAR(8)"),
    ("episodes", "last_search_at", "DATETIME"),
)


def _series_automatic_needed(cursor: sqlite3.Cursor) -> bool:
    for table, name, _definition in _COLUMNS_12:
        columns = _columns(cursor, table)
        if columns and name not in columns:
            return True
    return False


def _add_series_automatic(cursor: sqlite3.Cursor) -> None:
    """The columns of S5: every new value starts null. The switch for series is a setting without a row (off)."""
    for table, name, definition in _COLUMNS_12:
        columns = _columns(cursor, table)
        if columns and name not in columns:
            # Names and definitions come from the fixed list above, never from outside.
            cursor.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}')


_COLUMNS_13: tuple[tuple[str, str, str], ...] = (
    ("versions", "own_since", "DATETIME"),
    ("versions", "files_read_at", "DATETIME"),
    ("episode_files", "left_out", "BOOLEAN NOT NULL DEFAULT 0"),
    ("episode_files", "read_as", "JSON"),
    ("disk_roots", "kind", "VARCHAR(16) NOT NULL DEFAULT 'movie'"),
    ("disk_folders", "series", "JSON"),
)


def _series_takeover_needed(cursor: sqlite3.Cursor) -> bool:
    for table, name, _definition in _COLUMNS_13:
        columns = _columns(cursor, table)
        if columns and name not in columns:
            return True
    return False


def _add_series_takeover(cursor: sqlite3.Cursor) -> None:
    """The columns of S6. Series versions of nexcrate's own that exist already count as nexcrate's since they were
    created and as read: nexcrate filed their files itself (decision 23)."""
    added: list[str] = []
    for table, name, definition in _COLUMNS_13:
        columns = _columns(cursor, table)
        if columns and name not in columns:
            # Names and definitions come from the fixed list above, never from outside.
            cursor.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}')
            added.append(name)
    if "files_read_at" in added and "kind" in _columns(cursor, "version_definitions"):
        cursor.execute(
            "UPDATE versions SET own_since = created_at, files_read_at = CURRENT_TIMESTAMP "
            "WHERE source_id IS NULL AND version_definition_id IN "
            "(SELECT id FROM version_definitions WHERE kind = 'series')"
        )


# --- Migration 14: music (M1.0) ------------------------------------------------ #

#: The columns of schema 13, copied as they are. ⚠️ Names come from this list, never from outside.
_TITLES_13_COLUMNS = (
    "id", "kind", "tmdb_id", "imdb_id", "title", "original_title", "year", "runtime", "genres", "overview",
    "sort_key", "search_keys", "meta_source_id", "poster_source_id", "poster_url", "poster_key", "added",
    "updated_at", "poster_origin", "tmdb_poster_path", "tmdb_refreshed_at", "release_dates", "tmdb_search_keys",
    "original_language", "next_search_at", "last_search_at", "next_search_reason", "search_summary", "series_type",
    "tvdb_id", "series_status", "networks", "first_air_date", "last_air_date", "next_air_date", "episode_groups",
    "numbering_note", "title_en", "xem_checked_at", "xem_state", "episode_group_id", "id_search_seen",
)  # fmt: skip

#: The table as migration 14 leaves it: schema 13 with ``tmdb_id`` nullable and the album columns (decision 1).
_TITLES_14 = """
CREATE TABLE titles_migrating (
	id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
	kind VARCHAR(16) NOT NULL,
	tmdb_id INTEGER,
	imdb_id VARCHAR(32),
	title VARCHAR(1024) NOT NULL,
	original_title VARCHAR(1024),
	year INTEGER,
	runtime INTEGER,
	genres JSON NOT NULL,
	overview TEXT,
	sort_key VARCHAR(1024) NOT NULL,
	search_keys TEXT NOT NULL,
	meta_source_id INTEGER,
	poster_source_id INTEGER,
	poster_url VARCHAR(1024),
	poster_key VARCHAR(64),
	added DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	poster_origin VARCHAR(16),
	tmdb_poster_path VARCHAR(256),
	tmdb_refreshed_at DATETIME,
	release_dates JSON,
	tmdb_search_keys TEXT NOT NULL,
	original_language VARCHAR(8),
	next_search_at DATETIME,
	last_search_at DATETIME,
	next_search_reason VARCHAR(32),
	search_summary JSON,
	series_type VARCHAR(16),
	tvdb_id INTEGER,
	series_status VARCHAR(32),
	networks JSON,
	first_air_date VARCHAR(10),
	last_air_date VARCHAR(10),
	next_air_date VARCHAR(10),
	episode_groups JSON,
	numbering_note JSON,
	title_en VARCHAR(1024),
	xem_checked_at DATETIME,
	xem_state VARCHAR(32),
	episode_group_id VARCHAR(64),
	id_search_seen JSON,
	mbid VARCHAR(36),
	mbid_old JSON,
	artist_id INTEGER,
	artist_credit JSON,
	primary_type VARCHAR(32),
	secondary_types JSON,
	first_release_date VARCHAR(10),
	release_group_disambiguation VARCHAR(1024),
	releases_refreshed_at DATETIME,
	releases_due_at DATETIME,
	releases_state VARCHAR(16),
	mb_gone_at DATETIME,
	first_seen_at DATETIME,
	CONSTRAINT uq_titles_kind_tmdb_id UNIQUE (kind, tmdb_id),
	FOREIGN KEY(meta_source_id) REFERENCES sources (id) ON DELETE SET NULL,
	FOREIGN KEY(poster_source_id) REFERENCES sources (id) ON DELETE SET NULL,
	FOREIGN KEY(artist_id) REFERENCES artists (id) ON DELETE SET NULL
)
"""

#: The artists table as migration 14 creates it: ``titles`` refers to it, so it exists before the rebuild.
_ARTISTS_14 = """
CREATE TABLE artists (
	id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
	mbid VARCHAR(36) NOT NULL,
	mbid_old JSON,
	name VARCHAR(1024) NOT NULL,
	sort_name VARCHAR(1024) NOT NULL,
	disambiguation VARCHAR(1024),
	artist_type VARCHAR(32),
	country VARCHAR(8),
	begin_year INTEGER,
	end_year INTEGER,
	ended BOOLEAN NOT NULL,
	aliases JSON,
	alias_display VARCHAR(1024),
	search_keys TEXT NOT NULL,
	sort_key VARCHAR(1024) NOT NULL,
	is_various BOOLEAN NOT NULL,
	monitor_new VARCHAR(8) NOT NULL,
	groups_total INTEGER,
	load_state VARCHAR(16) NOT NULL,
	load_error VARCHAR(64),
	load_done INTEGER NOT NULL,
	load_total INTEGER,
	load_priority INTEGER NOT NULL,
	groups_refreshed_at DATETIME,
	groups_due_at DATETIME,
	mb_gone_at DATETIME,
	added_by VARCHAR(16) NOT NULL,
	added DATETIME NOT NULL,
	updated_at DATETIME NOT NULL
)
"""

#: Required columns of the rebuilt table that a database from before their upkeep lacks, with the SQL default the
#: upkeep gave them (``tmdb_search_keys`` came with step 2a).
_TITLES_14_REQUIRED = {
    "kind": "'movie'",
    "genres": "'[]'",
    "sort_key": "''",
    "search_keys": "''",
    "tmdb_search_keys": "''",
}

#: The indexes of the rebuilt table beyond those copied from the old one.
_TITLES_14_INDEXES = (
    "CREATE UNIQUE INDEX uq_titles_kind_mbid ON titles (kind, mbid) WHERE mbid IS NOT NULL",
    "CREATE INDEX ix_titles_artist_id ON titles (artist_id)",
    "CREATE INDEX ix_titles_releases_due_at ON titles (releases_due_at)",
)

_COLUMNS_14: tuple[tuple[str, str, str], ...] = (
    ("versions", "target_release_id", "INTEGER REFERENCES releases (id) ON DELETE SET NULL"),
    ("versions", "target_set_by", "VARCHAR(16)"),
    ("versions", "target_reason", "JSON"),
    ("versions", "actual_release_id", "INTEGER REFERENCES releases (id) ON DELETE SET NULL"),
    ("versions", "target_suggestion_id", "INTEGER REFERENCES releases (id) ON DELETE SET NULL"),
    ("versions", "track_counts", "JSON"),
    ("versions", "lidarr_album_id", "INTEGER"),
    ("versions", "source_album_path", "VARCHAR(4096)"),
    ("version_definitions", "music_countries", "JSON"),
)


def _titles_need_rebuild(cursor: sqlite3.Cursor) -> bool:
    columns = _columns(cursor, "titles")
    if not columns:
        # No table at all: create_all makes it in the new shape.
        return False
    return bool(columns.get("tmdb_id", (None, None, None, 0))[3]) or "mbid" not in columns


def _music_needed(cursor: sqlite3.Cursor) -> bool:
    if _titles_need_rebuild(cursor):
        return True
    for table, name, _definition in _COLUMNS_14:
        columns = _columns(cursor, table)
        if columns and name not in columns:
            return True
    return False


def _rebuild_titles(cursor: sqlite3.Cursor) -> None:
    """``tmdb_id`` becomes nullable, the album columns come along (decision 1). Pattern of ``_rebuild_versions``.

    ⚠️ Eleven tables refer to ``titles.id``; foreign keys are off while the old table goes (see the module docstring),
    and the runner checks them before the commit.
    """
    old_columns = _columns(cursor, "titles")
    copied = [name for name in _TITLES_13_COLUMNS if name in old_columns]
    attached = [
        row[0]
        for row in cursor.execute(
            "SELECT sql FROM sqlite_master WHERE tbl_name = 'titles' AND type IN ('index', 'trigger') "
            "AND sql IS NOT NULL ORDER BY rowid"
        ).fetchall()
    ]
    sequence_row = (
        cursor.execute("SELECT seq FROM sqlite_sequence WHERE name = 'titles'").fetchone()
        if _has_table(cursor, "sqlite_sequence")
        else None
    )

    if not _has_table(cursor, "artists"):
        cursor.execute(_ARTISTS_14)
        cursor.execute("CREATE UNIQUE INDEX ix_artists_mbid ON artists (mbid)")
    cursor.execute(_TITLES_14)
    # A database older than the upkeep that added a required column gets the same default the upkeep gives.
    missing = [name for name in _TITLES_14_REQUIRED if name not in old_columns]
    column_list = ", ".join(copied + missing)
    select_list = ", ".join(copied + [_TITLES_14_REQUIRED[name] for name in missing])
    # Names and defaults come from the fixed lists above, never from outside.
    cursor.execute(f"INSERT INTO titles_migrating ({column_list}) SELECT {select_list} FROM titles")  # noqa: S608
    cursor.execute("DROP TABLE titles")
    cursor.execute("ALTER TABLE titles_migrating RENAME TO titles")
    for statement in attached:
        cursor.execute(statement)
    for statement in _TITLES_14_INDEXES:
        cursor.execute(statement)
    if sequence_row is not None:
        cursor.execute("UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = 'titles'", (int(sequence_row[0]),))
        if cursor.rowcount == 0:
            cursor.execute("INSERT INTO sqlite_sequence (name, seq) VALUES ('titles', ?)", (int(sequence_row[0]),))


def _add_music(cursor: sqlite3.Cursor) -> None:
    """The rebuild of ``titles`` when it still has ``tmdb_id NOT NULL``, then the album columns of versions and
    definitions. The other music tables come from ``create_all``."""
    if _titles_need_rebuild(cursor):
        _rebuild_titles(cursor)
        # Thirteen columns in eleven tables refer to titles, some of the tables optional: the whole database is
        # checked here, where the runner's per-table check cannot name a table that may not exist.
        broken = cursor.execute("PRAGMA foreign_key_check").fetchall()
        if broken:
            raise RuntimeError(f"Migration 14 would leave {len(broken)} broken references after the rebuild of titles")
    for table, name, definition in _COLUMNS_14:
        columns = _columns(cursor, table)
        if columns and name not in columns:
            # Names and definitions come from the fixed list above, never from outside.
            cursor.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}')


# --- Migration 15: searching albums (M3.1) --------------------------------------------------- #

#: Null means "the default from the caps" (decision 4); a stored list is the owner's choice.
_COLUMNS_15 = (("indexers", "music_categories", "JSON"),)


def _music_search_needed(cursor: sqlite3.Cursor) -> bool:
    for table, name, _definition in _COLUMNS_15:
        columns = _columns(cursor, table)
        if columns and name not in columns:
            return True
    return False


def _add_music_search(cursor: sqlite3.Cursor) -> None:
    for table, name, definition in _COLUMNS_15:
        columns = _columns(cursor, table)
        if columns and name not in columns:
            # Names and definitions come from the fixed list above, never from outside.
            cursor.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}')


# --- Migration 16: loading and filing albums ------------------------------------------------ #

#: The table ``download_audio_files`` comes from ``create_all``. Tables that may not exist yet are skipped: the column
#: comes with the table then.
_COLUMNS_16 = (
    ("downloads", "release_id", "INTEGER REFERENCES releases (id) ON DELETE SET NULL"),
    ("artists", "folder", "VARCHAR(1024)"),
    ("track_files", "tags_state", "VARCHAR(16)"),
    ("track_files", "tags_written_at", "DATETIME"),
)


def _music_loading_needed(cursor: sqlite3.Cursor) -> bool:
    for table, name, _definition in _COLUMNS_16:
        columns = _columns(cursor, table)
        if columns and name not in columns:
            return True
    return False


def _add_music_loading(cursor: sqlite3.Cursor) -> None:
    for table, name, definition in _COLUMNS_16:
        columns = _columns(cursor, table)
        if columns and name not in columns:
            # Names and definitions come from the fixed list above, never from outside.
            cursor.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}')


# --- Migration 17: taking over Lidarr and reading album folders ----------------------------- #

_COLUMNS_17 = (("track_files", "unclear", "BOOLEAN DEFAULT 0 NOT NULL"),)


def _music_takeover_needed(cursor: sqlite3.Cursor) -> bool:
    for table, name, _definition in _COLUMNS_17:
        columns = _columns(cursor, table)
        if columns and name not in columns:
            return True
    return False


def _add_music_takeover(cursor: sqlite3.Cursor) -> None:
    for table, name, definition in _COLUMNS_17:
        columns = _columns(cursor, table)
        if columns and name not in columns:
            # Names and definitions come from the fixed list above, never from outside.
            cursor.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}')


# --- Migration 18: a failed download waits for the owner (the owner's finding of 20.09.2026) ------------------------ #

_COLUMNS_18 = (("downloads", "cleared_at", "DATETIME"),)


def _failed_cleared_needed(cursor: sqlite3.Cursor) -> bool:
    for table, name, _definition in _COLUMNS_18:
        columns = _columns(cursor, table)
        if columns and name not in columns:
            return True
    return False


def _add_failed_cleared(cursor: sqlite3.Cursor) -> None:
    for table, name, definition in _COLUMNS_18:
        columns = _columns(cursor, table)
        if columns and name not in columns:
            # Names and definitions come from the fixed list above, never from outside.
            cursor.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}')


# --- Migration 19: profiles by hand --------------------------------------------------- #

#: ``custom_formats`` and ``quality_sizes`` come from ``create_all``; these are the columns of ``profiles``.
_COLUMNS_19 = (
    ("profiles", "mode", "VARCHAR(16) DEFAULT 'wizard' NOT NULL"),
    ("profiles", "expert", "JSON"),
)


def _expert_needed(cursor: sqlite3.Cursor) -> bool:
    for table, name, _definition in _COLUMNS_19:
        columns = _columns(cursor, table)
        if columns and name not in columns:
            return True
    return False


def _add_expert(cursor: sqlite3.Cursor) -> None:
    for table, name, definition in _COLUMNS_19:
        columns = _columns(cursor, table)
        if columns and name not in columns:
            # Names and definitions come from the fixed list above, never from outside.
            cursor.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}')


# --- Migration 20: a profile stands for itself, with a name ---------------------------- #

#: The new ``profiles``: no version any more, a name instead, unique within its kind.
_PROFILES_20 = """CREATE TABLE profiles_migrating (
	id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
	name VARCHAR(200) NOT NULL,
	kind VARCHAR(16) NOT NULL,
	answers JSON NOT NULL,
	rules JSON NOT NULL,
	trash_commit VARCHAR(64) NOT NULL,
	mode VARCHAR(16) DEFAULT 'wizard' NOT NULL,
	expert JSON,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	CONSTRAINT uq_profiles_kind_name UNIQUE (kind, name)
)"""

_PROFILES_20_COLUMNS = ("id", "kind", "answers", "rules", "trash_commit", "mode", "expert", "created_at", "updated_at")


def _named_profiles_needed(cursor: sqlite3.Cursor) -> bool:
    return bool(_columns(cursor, "profiles")) and "name" not in _columns(cursor, "profiles")


def _profile_names(cursor: sqlite3.Cursor) -> dict[int, str]:
    """Each profile takes the name of the version it belonged to, and a number when two kinds meet in one name."""
    if _has_table(cursor, "version_definitions"):
        rows = cursor.execute(
            "SELECT p.id, p.kind, COALESCE(v.label, '') FROM profiles p "
            "LEFT JOIN version_definitions v ON v.id = p.version_definition_id ORDER BY p.id"
        ).fetchall()
    else:
        # A database so old that the versions come only with create_all; then there is nothing to name after.
        rows = [(row[0], row[1], "") for row in cursor.execute("SELECT id, kind FROM profiles ORDER BY id").fetchall()]
    names: dict[int, str] = {}
    taken: set[tuple[str, str]] = set()
    for profile_id, kind, label in rows:
        base = str(label or f"Profil {profile_id}")[:200]
        name = base
        number = 2
        while (kind, name) in taken:
            name = f"{base} {number}"[:200]
            number += 1
        taken.add((kind, name))
        names[int(profile_id)] = name
    return names


def _add_named_profiles(cursor: sqlite3.Cursor) -> None:
    """Rebuild ``profiles`` without its version, and let the version point at its profile instead.

    ⚠️ The old table had the version as a unique column with ``ON DELETE CASCADE``: removing a version took its
    profile with it. Several versions may share one profile now, so the link moves to ``version_definitions``
    with ``ON DELETE SET NULL``, and a foreign key cannot be changed in place in SQLite.
    """
    names = _profile_names(cursor)
    assignment = (
        cursor.execute("SELECT version_definition_id, id FROM profiles WHERE version_definition_id IS NOT NULL")
        .fetchall()
        if "version_definition_id" in _columns(cursor, "profiles")
        else []
    )
    sequence_row = (
        cursor.execute("SELECT seq FROM sqlite_sequence WHERE name = 'profiles'").fetchone()
        if _has_table(cursor, "sqlite_sequence")
        else None
    )

    cursor.execute(_PROFILES_20)
    column_list = ", ".join(_PROFILES_20_COLUMNS)
    marks = ", ".join("?" for _ in _PROFILES_20_COLUMNS)
    # ⚠️ Row by row with its own name, not all of them with an empty one first: two profiles of one kind would
    # meet in the empty name, and the new table holds every name of a kind only once. Found on the owner's
    # database, which has two movie profiles.
    stored = cursor.execute(f"SELECT {column_list} FROM profiles ORDER BY id").fetchall()  # noqa: S608
    for row in stored:
        # Names come from the fixed list above, never from outside.
        cursor.execute(
            f"INSERT INTO profiles_migrating ({column_list}, name) VALUES ({marks}, ?)",  # noqa: S608
            (*row, names.get(int(row[0]), f"Profil {row[0]}")),
        )
    cursor.execute("DROP TABLE profiles")
    cursor.execute("ALTER TABLE profiles_migrating RENAME TO profiles")
    if sequence_row is not None:
        cursor.execute("UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = 'profiles'", (int(sequence_row[0]),))
        if cursor.rowcount == 0:
            cursor.execute("INSERT INTO sqlite_sequence (name, seq) VALUES ('profiles', ?)", (int(sequence_row[0]),))

    if _has_table(cursor, "version_definitions") and "profile_id" not in _columns(cursor, "version_definitions"):
        cursor.execute(
            'ALTER TABLE version_definitions ADD COLUMN "profile_id" INTEGER '
            "REFERENCES profiles (id) ON DELETE SET NULL"
        )
        cursor.execute("CREATE INDEX ix_version_definitions_profile_id ON version_definitions (profile_id)")
    if _has_table(cursor, "version_definitions"):
        for definition_id, profile_id in assignment:
            cursor.execute("UPDATE version_definitions SET profile_id = ? WHERE id = ?", (profile_id, definition_id))


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        number=1,
        name="versions without a source",
        needed=_versions_need_rebuild,
        apply=_rebuild_versions,
        checked_tables=("versions", "history"),
    ),
    Migration(
        number=2,
        name="profiles and the original language",
        needed=_profiles_needed,
        apply=_add_profiles,
        checked_tables=("profiles",),
    ),
    Migration(
        number=3,
        name="indexer settings for searching",
        needed=_indexer_settings_needed,
        apply=_add_indexer_settings,
        checked_tables=("indexers",),
    ),
    Migration(
        number=4,
        name="folders of versions for downloading",
        needed=_version_folder_needed,
        apply=_add_version_folder,
        checked_tables=("version_definitions",),
    ),
    Migration(
        number=5,
        name="taking over from Radarr and naming per version",
        needed=_takeover_columns_needed,
        apply=_add_takeover_columns,
        checked_tables=("sources", "versions", "version_definitions"),
    ),
    Migration(
        number=6,
        name="searching and loading by itself",
        needed=_automatic_columns_needed,
        apply=_add_automatic_columns,
        # Added columns carry no reference. Only tables every database has: indexers and downloads may not exist yet.
        checked_tables=("titles", "versions"),
    ),
    Migration(
        number=7,
        name="the library from disk",
        needed=_disk_columns_needed,
        apply=_add_disk_columns,
        # The new tables disk_roots and disk_folders come from create_all.
        checked_tables=("versions",),
    ),
    Migration(
        number=8,
        name="series, seasons and episodes",
        needed=_series_columns_needed,
        apply=_add_series_columns,
        # The six series tables come from create_all; the index on titles.tvdb_id from the index upkeep.
        checked_tables=("titles", "versions", "import_runs"),
    ),
    Migration(
        number=9,
        name="the upgrade verdict per episode file",
        needed=_episode_cutoff_needed,
        apply=_add_episode_cutoff,
        checked_tables=("episode_files",),
    ),
    Migration(
        number=10,
        name="searching series",
        needed=_series_search_needed,
        apply=_add_series_search,
        # The tables title_aliases and xem_names come from create_all. Only titles is in every database: indexers
        # and episodes may not exist yet.
        checked_tables=("titles",),
    ),
    Migration(
        number=11,
        name="loading and filing series",
        needed=_series_loading_needed,
        apply=_add_series_loading,
        # Only version_definitions is in every database; downloads, extra_files and episode_files may not exist yet.
        checked_tables=("version_definitions",),
    ),
    Migration(
        number=12,
        name="searching and loading series by itself",
        needed=_series_automatic_needed,
        apply=_add_series_automatic,
        # Added columns carry no reference. Only titles and versions are in every database.
        checked_tables=("titles", "versions"),
    ),
    Migration(
        number=13,
        name="taking over series and reading series folders",
        needed=_series_takeover_needed,
        apply=_add_series_takeover,
        # Only versions is in every database; episode_files and the disk tables may not exist yet.
        checked_tables=("versions",),
    ),
    Migration(
        number=14,
        name="music: albums as titles, artists, releases and tracks",
        needed=_music_needed,
        apply=_add_music,
        # Tables every database has that refer to titles; the series, disk and download tables may not exist yet.
        checked_tables=("titles", "versions", "alternate_titles", "history"),
    ),
    Migration(
        number=15,
        name="searching albums: music categories per indexer",
        needed=_music_search_needed,
        apply=_add_music_search,
        checked_tables=("indexers",),
    ),
    Migration(
        number=16,
        name="loading and filing albums",
        needed=_music_loading_needed,
        apply=_add_music_loading,
        # Added columns only; downloads, artists and track_files may not exist in an old database.
        checked_tables=("versions",),
    ),
    Migration(
        number=17,
        name="taking over Lidarr and reading album folders",
        needed=_music_takeover_needed,
        apply=_add_music_takeover,
        checked_tables=("versions",),
    ),
    Migration(
        number=18,
        name="a failed download waits for the owner",
        needed=_failed_cleared_needed,
        apply=_add_failed_cleared,
        # Only versions is in every database; downloads may not exist yet, then the column comes with the table.
        checked_tables=("versions",),
    ),
    Migration(
        number=19,
        name="profiles by hand: custom formats and quality sizes of their own",
        needed=_expert_needed,
        apply=_add_expert,
        # profiles may not exist in a very old database; then the columns come with the table.
        checked_tables=("versions",),
    ),
    Migration(
        number=20,
        name="a profile stands for itself, with a name",
        needed=_named_profiles_needed,
        apply=_add_named_profiles,
        # The rebuild drops the old table; version_definitions now points at the new one.
        checked_tables=("version_definitions", "versions"),
    ),
)

#: The number a database of this version has.
LATEST = MIGRATIONS[-1].number


# --- Running ---------------------------------------------------------------------------- #


def _stored(cursor: sqlite3.Cursor) -> int:
    try:
        row = cursor.execute("SELECT value FROM settings WHERE key = ?", (SETTING_KEY,)).fetchone()
    except sqlite3.Error:
        return 0
    try:
        return int(row[0]) if row is not None else 0
    except TypeError, ValueError:
        return 0


def _store(cursor: sqlite3.Cursor, number: int) -> bool:
    columns = _columns(cursor, "settings")
    if "key" not in columns or "value" not in columns:
        return False
    cursor.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (SETTING_KEY, str(number)),
    )
    return True


class _Raw:
    """The plain sqlite3 connection under the engine, in autocommit mode, so BEGIN and COMMIT are ours."""

    def __init__(self, bind: Engine) -> None:
        self._pooled = bind.raw_connection()
        connection = self._pooled.driver_connection
        if not isinstance(connection, sqlite3.Connection):
            self._pooled.close()
            raise TypeError("migrations need the sqlite3 driver")
        self.connection = connection
        self._previous = connection.isolation_level
        connection.isolation_level = None
        self.cursor = connection.cursor()

    def close(self) -> None:
        try:
            self.cursor.close()
            self.connection.isolation_level = self._previous
        finally:
            self._pooled.close()


def stored_number(bind: Engine) -> int:
    raw = _Raw(bind)
    try:
        return _stored(raw.cursor)
    finally:
        raw.close()


def due(bind: Engine) -> list[Migration]:
    """The migrations above the stored number that this database still needs."""
    raw = _Raw(bind)
    try:
        current = _stored(raw.cursor)
        return [migration for migration in MIGRATIONS if migration.number > current and migration.needed(raw.cursor)]
    finally:
        raw.close()


def run(bind: Engine) -> list[int]:
    """Apply every due migration, each in one transaction with its number. Returns the numbers applied."""
    raw = _Raw(bind)
    cursor = raw.cursor
    applied: list[int] = []
    try:
        current = _stored(cursor)
        for migration in MIGRATIONS:
            if migration.number <= current or not migration.needed(cursor):
                continue
            cursor.execute("PRAGMA foreign_keys=OFF")
            try:
                cursor.execute("BEGIN IMMEDIATE")
                try:
                    migration.apply(cursor)
                    for table in migration.checked_tables:
                        if not _has_table(cursor, table):
                            # A table that is not there yet has no rows and so no references to break;
                            # it comes later with create_all.
                            continue
                        broken = cursor.execute(f'PRAGMA foreign_key_check("{table}")').fetchall()
                        if broken:
                            raise RuntimeError(
                                f"Migration {migration.number} would leave {len(broken)} broken references in {table}"
                            )
                    _store(cursor, migration.number)
                    cursor.execute("COMMIT")
                except BaseException:
                    cursor.execute("ROLLBACK")
                    raise
            finally:
                cursor.execute("PRAGMA foreign_keys=ON")
            logger.info("Migration %d applied: %s", migration.number, migration.name)
            applied.append(migration.number)
    finally:
        raw.close()
    return applied


def store_latest(bind: Engine) -> None:
    """Write the newest number when the stored one is lower. Nothing to do for an up-to-date database."""
    raw = _Raw(bind)
    try:
        if _stored(raw.cursor) < LATEST:
            _store(raw.cursor, LATEST)
    finally:
        raw.close()
