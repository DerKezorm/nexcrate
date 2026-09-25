"""Loading a release on request, and what the search answer says about loading.

A version can load when, in this order: no source feeds it, its definition has a profile, its definition has a folder,
an enabled client exists for the release's protocol, and no download blocks it. The search answer names the first code
that fails, per version (without the client) and per release and version.

``grab`` is ``POST /api/downloads``:

1. The search is kept and done (404 ``search_expired``), the release belongs to the movie (404 ``release_not_found``).
2. The version can load (409 with the code).
3. A release that does not fit, or is on the blocklist, needs the owner's confirmation (409).
4. The file is fetched from the indexer (``fetch``); a torrent on the blocklist by its info hash needs confirmation too.
5. The enabled clients of the protocol, lowest priority number first, then lowest id: the category is checked (created
   when missing) and the file handed over. A client that fails gets its error code and the next one is asked; when all
   fail, the first client's error is the answer. ⚠️ A client that got the request and did not answer in time may have
   taken the release (the owner's finding 1 of 22.09.2026): the download is recorded all the same, marked
   ``handed_unsure_at``, and no other client is asked; ``foreign`` finds the job by its name or ends the download as
   ``not_taken``.
6. The download is recorded as ``queued`` with a history entry ``grabbed``; tracking asks the client 5 seconds later.

⚠️ The link and the indexer key stay in memory; the client never sees them, the answer and the log never do.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Collection, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select

from ... import crypto
from ...db import SessionLocal
from ...meldungen import meldung
from ...models import (
    BlocklistEntry,
    Download,
    DownloadClient,
    DownloadEpisode,
    Episode,
    EpisodeFile,
    EpisodeVersion,
    Indexer,
    Title,
    Version,
    VersionDefinition,
)
from ...models.downloads import ORIGINS, PROTOCOL_OF_KIND
from .. import downloaders, naming, trash
from .. import tags as tag_store
from ..profiles import store as profile_store
from ..search import jobs as search_jobs
from ..search import report as search_report
from . import fetch, store, tracking

logger = logging.getLogger("nexcrate.downloads")

LOAD_CODES = (
    "version_fed_by_source",
    "version_no_profile",
    "version_no_folder",
    "no_client_for_protocol",
    "no_client_for_tags",
    "download_active",
    "episodes_downloading",
)
CONFIRMABLE = ("not_fitting", "blocklisted", "no_gain")
_LOAD_MESSAGES = {
    "version_fed_by_source": (
        "This version comes from a Radarr, Sonarr or Lidarr connection; nexcrate does not load for it."
    ),
    "version_no_profile": "This version has no profile yet.",
    "version_no_folder": "This version has no default folder yet.",
    "no_client_for_protocol": "No enabled download client loads this kind of release.",
    "no_client_for_tags": "No download client was found without tags or with a tag of this title.",
    "download_active": "A download is already running for this version.",
    "episodes_downloading": "A running download of this version holds episodes of this release.",
}


class LoadError(Exception):
    def __init__(self, detail: dict[str, Any], status: int) -> None:
        super().__init__(detail["code"])
        self.detail = detail
        self.status = status

    @property
    def code(self) -> str:
        return str(self.detail["code"])

    def http(self) -> HTTPException:
        return HTTPException(status_code=self.status, detail=self.detail)


def load_error(code: str) -> LoadError:
    return LoadError(meldung(code, _LOAD_MESSAGES[code]), 409)


# --- What a title's versions can do ------------------------------------------------------------- #


@dataclass(frozen=True)
class VersionFacts:
    definition_id: int
    label: str
    fed: bool
    has_profile: bool
    folder: str | None
    active: bool
    #: The profile's rules, for the formats TRaSH marks for renaming.
    rules: dict[str, Any] | None = field(default=None, repr=False)


@dataclass(frozen=True)
class TitleFacts:
    title_id: int
    versions: dict[int, VersionFacts]
    protocols: frozenset[str]
    blocklist: list[BlocklistEntry]
    kind: str = "movie"
    #: Series: version definition id to the episode ids a blocking download of it holds (decision 5).
    downloading: dict[int, frozenset[int]] = field(default_factory=dict)


def title_facts(db: Any, title_id: int) -> TitleFacts:
    rows = (
        db.execute(
            select(Version, VersionDefinition)
            .join(VersionDefinition, VersionDefinition.id == Version.version_definition_id)
            .where(Version.title_id == title_id)
        )
        .tuples()
        .all()
    )
    ids = [definition.id for _version, definition in rows]
    title = db.get(Title, title_id)
    kind = title.kind if title is not None else "movie"
    profiles = profile_store.by_versions(db, ids)
    versions: dict[int, VersionFacts] = {}
    for version, definition in rows:
        profile = profiles.get(definition.id)
        rules = profile.rules if profile is not None and isinstance(profile.rules, dict) else None
        versions[definition.id] = VersionFacts(
            definition_id=definition.id,
            label=definition.label,
            fed=version.source_id is not None,
            has_profile=rules is not None and rules.get("kind") == kind,
            folder=definition.folder,
            active=kind in ("movie", "album") and store.blocking_download(db, title_id, definition.id) is not None,
            rules=rules,
        )
    kinds = set(db.scalars(select(DownloadClient.kind).where(DownloadClient.enabled.is_(True))))
    downloading: dict[int, frozenset[int]] = {}
    if kind == "series":
        for definition_id in versions:
            downloading[definition_id] = frozenset(store.downloading_episodes(db, title_id, definition_id))
    return TitleFacts(
        title_id=title_id,
        versions=versions,
        protocols=frozenset(PROTOCOL_OF_KIND[kind] for kind in kinds if kind in PROTOCOL_OF_KIND),
        blocklist=store.blocklist(db, title_id),
        kind=kind,
        downloading=downloading,
    )


def version_block(facts: TitleFacts, definition_id: int) -> str | None:
    """The first code that does not depend on the release; None when the version could load."""
    version = facts.versions.get(definition_id)
    if version is None:
        return None
    if version.fed:
        return "version_fed_by_source"
    if not version.has_profile:
        return "version_no_profile"
    if not version.folder:
        return "version_no_folder"
    return "download_active" if version.active else None


def release_block(facts: TitleFacts, definition_id: int, protocol: str) -> str | None:
    """The first load code for this release and version, in the plan's order."""
    code = version_block(facts, definition_id)
    if code in ("version_fed_by_source", "version_no_profile", "version_no_folder"):
        return code
    if protocol not in facts.protocols:
        return "no_client_for_protocol"
    return code


def load_episodes(result: dict[str, Any] | None, confirm: Collection[str] = ()) -> dict[int, str]:
    """The episodes a series release loads for a version, with their action (decision 2): what it fills or replaces;
    with ``no_gain`` confirmed and nothing to fill or replace, every watched episode it holds (decision 4)."""
    rows = [row for row in (result or {}).get("episodes") or [] if isinstance(row.get("episode_id"), int)]
    chosen = {int(row["episode_id"]): str(row["state"]) for row in rows if row.get("state") in ("fills", "replaces")}
    if chosen or "no_gain" not in confirm:
        return chosen
    return {int(row["episode_id"]): "confirmed" for row in rows if row.get("state") != "not_watched"}


def series_release_block(
    facts: TitleFacts, definition_id: int, protocol: str, result: dict[str, Any] | None
) -> str | None:
    """The first load code of a series release for a version: as for movies, then its episodes in a running download."""
    code = release_block(facts, definition_id, protocol)
    if code is not None:
        return code
    held = facts.downloading.get(definition_id, frozenset())
    wanted = load_episodes(result, ("no_gain",))
    return "episodes_downloading" if held & set(wanted) else None


def decorate_series(body: dict[str, Any], info_hashes: dict[str, str]) -> None:
    """A series search's answer (S4.1): the series parts under their own names, ``can_load`` and ``load_block`` per
    release and version, ``blocklisted``, and per version ``load_block`` and ``takes_can_load``."""
    with SessionLocal() as db:
        facts = title_facts(db, int(body["title_id"]))
    loadable: dict[tuple[str, int], bool] = {}
    for release in body.get("releases") or []:
        release["parsed_series"] = release.pop("parsed", None)
        release["blocklisted"] = store.is_blocked(
            facts.blocklist,
            protocol=release["protocol"],
            release_title=release["title"],
            info_hash=info_hashes.get(release["release_key"]),
        )
        for placed in release.get("versions") or []:
            result = placed.pop("result", None)
            placed["series_result"] = result
            known = placed["version_id"] in facts.versions and release.get("belongs")
            code = series_release_block(facts, placed["version_id"], release["protocol"], result) if known else None
            if known and code is None and result is None:
                code = "version_no_profile"
            placed["can_load"] = bool(known) and code is None
            placed["load_block"] = code
            loadable[(release["release_key"], placed["version_id"])] = placed["can_load"]
    for entry in body.get("versions") or []:
        entry["load_block"] = version_block(facts, entry["version_id"])
        takes = entry.get("takes") or []
        entry["takes_can_load"] = bool(takes) and all(
            loadable.get((take["release_key"], entry["version_id"]), False) for take in takes
        )


def decorate(body: dict[str, Any], info_hashes: dict[str, str]) -> None:
    """Adds ``load_block`` to the versions, ``blocklisted`` to the releases, ``can_load`` and ``load_block`` to each
    release's versions. A version the title no longer has cannot load and names no code. Blocked releases leave the
    ranks and decisions (``report.leave_out_blocked``); loading one still works after the confirmation."""
    with SessionLocal() as db:
        facts = title_facts(db, int(body["title_id"]))
    for entry in body.get("versions") or []:
        entry["load_block"] = version_block(facts, entry["version_id"])
    for release in body.get("releases") or []:
        release["blocklisted"] = store.is_blocked(
            facts.blocklist,
            protocol=release["protocol"],
            release_title=release["title"],
            info_hash=info_hashes.get(release["release_key"]),
        )
        for placed in release.get("versions") or []:
            known = placed["version_id"] in facts.versions
            code = release_block(facts, placed["version_id"], release["protocol"]) if known else None
            placed["can_load"] = known and code is None
            placed["load_block"] = code
    blocked = {release["release_key"] for release in body.get("releases") or [] if release["blocklisted"]}
    search_report.leave_out_blocked(body.get("versions") or [], body.get("releases") or [], blocked)


def decorate_album(body: dict[str, Any], info_hashes: dict[str, str]) -> None:
    """An album search's answer (decision 1): ``load_block`` on the music version, and per
    release of this album ``blocklisted``, ``can_load`` and ``load_block``. A release of another album never loads."""
    with SessionLocal() as db:
        facts = title_facts(db, int(body["title_id"]))
    decision = (body.get("versions") or [None])[0]
    version_id = decision.get("version_id") if isinstance(decision, dict) else None
    if isinstance(decision, dict):
        decision["load_block"] = version_block(facts, version_id) if version_id in facts.versions else None
    for release in body.get("releases") or []:
        if release.get("verdict") is None:
            continue
        release["blocklisted"] = store.is_blocked(
            facts.blocklist,
            protocol=release["protocol"],
            release_title=release["title"],
            info_hash=info_hashes.get(release["release_key"]),
        )
        known = version_id in facts.versions
        code = release_block(facts, version_id, release["protocol"]) if known else None
        release["can_load"] = known and code is None
        release["load_block"] = code


# --- Loading --------------------------------------------------------------------------------------- #

_busy_lock = threading.Lock()
_busy: set[tuple[int, int]] = set()


@contextmanager
def _claim(title_id: int, definition_id: int) -> Iterator[None]:
    """One load at a time per version: a second request while one runs is refused as ``download_active``."""
    key = (title_id, definition_id)
    with _busy_lock:
        if key in _busy:
            raise load_error("download_active")
        _busy.add(key)
    try:
        yield
    finally:
        with _busy_lock:
            _busy.discard(key)


@dataclass(frozen=True)
class ClientChoice:
    id: int
    kind: str
    url: str
    username: str
    #: ⚠️ Decrypted, for this load only.
    secret: str = field(repr=False)
    category: str
    login_blocked: bool = False

    def target(self) -> downloaders.Target:
        return downloaders.Target(
            kind=self.kind,
            url=self.url,
            username=self.username,
            secret=self.secret,
            category=self.category,
            login_blocked=self.login_blocked,
        )


@dataclass(frozen=True)
class Prepared:
    facts: TitleFacts
    clients: list[ClientChoice]
    #: The matched formats TRaSH marks for renaming.
    rename_formats: list[str]
    #: True when clients for the protocol exist but none may load for this title's tags.
    by_tags: bool = False


def _rename_formats(rules: dict[str, Any] | None, matched: list[dict[str, Any]], kind: str = "movie") -> list[str]:
    if not rules:
        return []
    by_name = {
        str(entry.get("name")): entry.get("trash_id") for entry in rules.get("formats") or [] if isinstance(entry, dict)
    }
    known = trash.current(kind).formats_by_id
    names: list[str] = []
    for entry in matched:
        name = str(entry.get("name") or "")
        trash_id = by_name.get(name)
        if trash_id and known.get(trash_id, {}).get("includeCustomFormatWhenRenaming") and name not in names:
            names.append(name)
    return names


def _prepare(found: search_jobs.FoundRelease, definition_id: int, matched: list[dict[str, Any]]) -> Prepared:
    kinds = [kind for kind, protocol in PROTOCOL_OF_KIND.items() if protocol == found.protocol]
    trash_kind = "series" if found.scope is not None else "movie"
    with SessionLocal() as db:
        facts = title_facts(db, found.title_id)
        candidates = list(
            db.scalars(
                select(DownloadClient)
                .where(DownloadClient.enabled.is_(True), DownloadClient.kind.in_(kinds))
                .order_by(DownloadClient.priority, DownloadClient.id)
            )
        )
        # Tags, as in the apps: clients sharing a tag with the title when there are any,
        # else the ones without a tag; none of either loads nothing.
        allowed = set(tag_store.clients_for(db, found.title_id, [row.id for row in candidates]))
        rows = [row for row in candidates if row.id in allowed]
        by_tags = bool(candidates) and not rows
        clients = [
            ClientChoice(
                id=row.id,
                kind=row.kind,
                url=row.url,
                username=row.username or "",
                secret=crypto.decrypt(row.secret) if row.secret else "",
                category=row.category,
                login_blocked=store.login_blocked(row),
            )
            for row in rows
        ]
    version = facts.versions.get(definition_id)
    rename_formats = _rename_formats(version.rules if version else None, matched, trash_kind)
    return Prepared(facts=facts, clients=clients, rename_formats=rename_formats, by_tags=by_tags)


def _follows(client_id: int, download_id: str) -> bool:
    with SessionLocal() as db:
        return store.following(db, client_id, download_id)


@dataclass(frozen=True)
class HandedOver:
    #: The client's id of the job; empty when the client did not answer and nexcrate cannot know it (SABnzbd).
    download_id: str
    choice: ClientChoice
    #: The client did not answer in time: it may or may not have taken the release.
    unsure: bool = False


async def _hand_over(
    prepared: Prepared, found: search_jobs.FoundRelease, fetched: fetch.Fetched, urgent: bool | None = None
) -> HandedOver:
    """``urgent``: the release brings something missing (True) or is an upgrade (False); the client queues it so."""
    stem = naming.clean_file_name(found.release["title"])
    failures: list[downloaders.ClientError] = []
    for choice in prepared.clients:
        try:
            async with downloaders.open_client(choice.target()) as client:
                await downloaders.ensure_category(client, create=True)
                await client.check_category()
                if found.protocol == "usenet":
                    if fetched.content is None:
                        raise downloaders.refused()
                    download_id = await client.add_nzb(fetched.content, f"{stem}.nzb", urgent=urgent)
                else:
                    download_id = await client.add_torrent(
                        content=fetched.content,
                        magnet=fetched.magnet,
                        file_name=f"{stem}.torrent",
                        info_hash=fetched.info_hash or "",
                        urgent=urgent,
                    )
        except downloaders.DuplicateTorrent as exc:
            # qBittorrent has the torrent already. In nexcrate's category and followed by no download, nexcrate follows
            # it from now on; otherwise it is refused as a duplicate.
            if exc.category_matches and not await asyncio.to_thread(_follows, choice.id, exc.info_hash):
                logger.info("Download client %d has the torrent already; nexcrate follows it", choice.id)
                return HandedOver(exc.info_hash, choice)
            failures.append(exc)
            continue
        except downloaders.HandOverUnsure as exc:
            # No other client is asked: this one may have it, and a second one would load the release twice.
            logger.warning(
                "Download client %d did not answer the hand-over in time; nexcrate looks whether it took the release",
                choice.id,
            )
            await asyncio.to_thread(store.record_client_error, choice.id, None)
            return HandedOver(exc.download_id, choice, unsure=True)
        except downloaders.ClientError as exc:
            failures.append(exc)
            logger.info("Download client %d refused or failed to take a release: %s", choice.id, exc.code)
            await asyncio.to_thread(store.record_client_error, choice.id, exc.code)
            continue
        await asyncio.to_thread(store.record_client_error, choice.id, None)
        return HandedOver(download_id, choice)
    if not failures:
        raise load_error("no_client_for_tags" if prepared.by_tags else "no_client_for_protocol")
    raise LoadError(failures[0].detail, failures[0].status)


#: How often recording a download that the client already took is tried while the database is busy, and the pause
#: before each new try. A client that took the release must never go without its download (23.09.2026: three jobs in
#: SABnzbd without a download, shown as foreign, and two of them loaded a second time).
RECORD_TRIES = 6
RECORD_PAUSE_SECONDS = 5.0


def _record_patiently(*args: Any) -> int:
    """``_record`` again and again while the database is busy: the release is in the client already."""
    from ... import db as database

    for attempt in range(1, RECORD_TRIES + 1):
        try:
            return _record(*args)
        except Exception as exc:
            if not database.database_locked(exc) or attempt == RECORD_TRIES:
                raise
            logger.warning(
                "Recording a download the client took found the database busy (try %d of %d); nexcrate tries again",
                attempt,
                RECORD_TRIES,
            )
            time.sleep(RECORD_PAUSE_SECONDS)
    raise AssertionError("unreachable")


def _record(
    found: search_jobs.FoundRelease,
    definition_id: int,
    result: dict[str, Any],
    prepared: Prepared,
    choice: ClientChoice,
    download_id: str,
    confirm: list[str],
    episodes: dict[int, str] | None = None,
    unsure: bool = False,
    upgrade: bool = False,
) -> int:
    moment = store.now()
    parsed = found.release.get("parsed") or {}
    with SessionLocal() as db:
        if episodes is None and store.blocking_download(db, found.title_id, definition_id) is not None:
            # The claim keeps a second load out; a download here would be a bug, not a reason to lose this one.
            logger.warning("Title %d: a second download for version %d is recorded", found.title_id, definition_id)
        version = store.version_of(db, found.title_id, definition_id)
        label = prepared.facts.versions[definition_id].label if definition_id in prepared.facts.versions else ""
        row = Download(
            title_id=found.title_id,
            version_id=version.id if version is not None else None,
            version_definition_id=definition_id,
            version_label=label,
            client_id=choice.id,
            protocol=found.protocol,
            client_download_id=download_id,
            release_title=found.release["title"][:1024],
            indexer_id=found.indexer_id if db.get(Indexer, found.indexer_id) is not None else None,
            indexer_name=found.indexer_name[:100],
            release_key=found.release["release_key"],
            size_bytes=found.release.get("size_bytes"),
            quality=(parsed.get("quality") or None),
            score=result.get("score"),
            below_target=bool(result.get("below_target")),
            languages=list(parsed.get("languages") or []),
            formats=prepared.rename_formats,
            confirmed=[value for value in CONFIRMABLE if value in confirm],
            origin=found.origin if found.origin in ORIGINS else "manual",
            state="queued",
            progress=0.0,
            grabbed_at=moment,
            updated_at=moment,
            handed_unsure_at=moment if unsure else None,
        )
        if episodes is not None:
            _series_fields(db, row, found, episodes)
        db.add(row)
        db.flush()
        if episodes is not None:
            db.add_all(
                DownloadEpisode(download_id=row.id, episode_id=episode_id, action=action, state="expected")
                for episode_id, action in sorted(episodes.items())
            )
            db.flush()
        # Marks what the daily limit of upgrades counts (upgrade_guard.used).
        store.add_history(db, row, "grabbed", row.quality, moment, {"upgrade": upgrade, "origin": row.origin})
        store.follow(db, row, moment)
        # Late: the automatic imports this module. Whatever waited out a delay for this version is done with.
        from ..automatic import waiting

        waiting.forget_loaded(db, found.title_id, definition_id, list(episodes) if episodes is not None else None)
        db.commit()
        logger.info(
            "Title %d: a release was handed to download client %d as download %d", found.title_id, choice.id, row.id
        )
        return row.id


def _series_fields(db: Any, row: Download, found: search_jobs.FoundRelease, episodes: dict[int, str]) -> None:
    """Scope, season and the numbering the release matched through (S4.0)."""
    seasons = set(db.scalars(select(Episode.season_number).where(Episode.id.in_(list(episodes)))))
    row.scope = "episode" if len(episodes) == 1 else ("season" if len(seasons) == 1 else "series")
    row.season = next(iter(seasons)) if len(seasons) == 1 else None
    via = (found.release.get("match") or {}).get("via")
    row.match_via = str(via)[:16] if via else None
    parsed = found.release.get("parsed") or {}
    row.quality = parsed.get("quality") or None


def _title_exists(title_id: int) -> bool:
    with SessionLocal() as db:
        return db.get(Title, title_id) is not None


async def grab(
    search_id: str,
    release_key: str,
    definition_id: int,
    confirm: list[str],
    only: frozenset[str] | None = None,
) -> int:
    """Load a release of a kept search for a version. Returns the download's id. Raises ``LoadError``.

    ``only``: the codes of the episodes a series release is loaded for, as a set took it; its other episodes stay with
    the release of the set that covers them.
    """
    try:
        found = search_jobs.find_release(search_id, release_key)
    except search_jobs.SearchGone as exc:
        raise LoadError(
            meldung("search_expired", "This search is not kept any more. Please search again."), 404
        ) from exc
    except search_jobs.ReleaseMissing as exc:
        raise LoadError(meldung("release_not_found", "This search has no such release of the title."), 404) from exc
    if found.album:
        return await _grab_album(found, definition_id, confirm)
    placed = next(
        (entry for entry in found.release.get("versions") or [] if entry["version_id"] == definition_id), None
    )
    if placed is None or not await asyncio.to_thread(_title_exists, found.title_id):
        raise LoadError(meldung("invalid_input", "The input is not valid.", fields=["version_id"]), 422)
    result = placed.get("result") or {}
    series = found.scope is not None
    with _claim(found.title_id, definition_id):
        prepared = await asyncio.to_thread(_prepare, found, definition_id, list(result.get("matched") or []))
        if definition_id not in prepared.facts.versions:
            raise LoadError(meldung("invalid_input", "The input is not valid.", fields=["version_id"]), 422)
        code = release_block(prepared.facts, definition_id, found.protocol)
        if code is not None:
            raise load_error(code)
        episodes: dict[int, str] | None = None
        if series:
            if not placed.get("result"):
                raise load_error("version_no_profile")
            episodes = load_episodes(result, confirm)
            if only is not None:
                codes_of = {row.get("episode_id"): row.get("code") for row in result.get("episodes") or []}
                episodes = {item: action for item, action in episodes.items() if codes_of.get(item) in only}
            if not episodes:
                codes = "release_no_gain" if load_episodes(result, ("no_gain",)) else "release_no_episodes"
                raise LoadError(meldung(codes, _SERIES_MESSAGES[codes]), 409)
            held = prepared.facts.downloading.get(definition_id, frozenset()) & set(episodes)
            if held:
                raise LoadError(
                    meldung(
                        "episodes_downloading",
                        _LOAD_MESSAGES["episodes_downloading"],
                        episodes=", ".join(_episode_codes(result, held)),
                    ),
                    409,
                )
        if not result.get("accepted") and "not_fitting" not in confirm:
            raise LoadError(meldung("release_not_fitting", "This release does not fit the version's profile."), 409)
        upgrade = await _check_upgrade(found.title_id, definition_id, episodes, found.origin)
        blocked = store.is_blocked(
            prepared.facts.blocklist,
            protocol=found.protocol,
            release_title=found.release["title"],
            info_hash=found.info_hash,
        )
        if blocked and "blocklisted" not in confirm:
            raise _blocklisted()
        try:
            fetched = await fetch.fetch(found.link or "", found.protocol, f"release:{found.indexer_id}")
        except fetch.FetchError as exc:
            logger.info(
                "Title %d: the release file of indexer %d could not be used: %s",
                found.title_id,
                found.indexer_id,
                exc.code,
            )
            if exc.retry_after:
                await asyncio.to_thread(store.pause_indexer, found.indexer_id, exc.retry_after)
            raise LoadError(exc.detail, exc.status) from exc
        if "blocklisted" not in confirm and store.is_blocked_hash(prepared.facts.blocklist, fetched.info_hash):
            raise _blocklisted()
        handed = await _hand_over(prepared, found, fetched, urgent=not upgrade)
        row_id = await asyncio.to_thread(
            _record_patiently,
            found,
            definition_id,
            result,
            prepared,
            handed.choice,
            handed.download_id,
            confirm,
            episodes,
            handed.unsure,
            upgrade,
        )
    _after_hand_over(handed)
    return row_id


def is_upgrade(db: Any, title_id: int, definition_id: int, episodes: dict[int, str] | None) -> bool:
    """A movie or album version that has a file, or a series release that fills no episode."""
    if episodes is not None:
        return not any(action == "fills" for action in episodes.values())
    version = store.version_of(db, title_id, definition_id)
    return bool(version is not None and version.has_file)


def _upgrade_check(
    title_id: int, definition_id: int, episodes: dict[int, str] | None, origin: str
) -> tuple[bool, str | None]:
    """Whether the load is an upgrade, and for an automatic load the code that holds it back
    (``automatic/upgrade_guard.py``): ``upgrade_limit`` or ``upgrades_paused``."""
    from ..automatic import upgrade_guard

    with SessionLocal() as db:
        upgrade = is_upgrade(db, title_id, definition_id, episodes)
        if not upgrade or origin not in upgrade_guard.AUTOMATIC:
            return upgrade, None
        guard = upgrade_guard.load(db, store.now())
        if guard.limit_reached:
            return True, "upgrade_limit"
        if guard.paused_since is None:
            return True, None
        if episodes is None:
            version = store.version_of(db, title_id, definition_id)
            arrived = upgrade_guard.arrivals(db, [version.id]).get(version.id) if version is not None else None
            return True, ("upgrades_paused" if guard.blocks(arrived) else None)
        version = store.version_of(db, title_id, definition_id)
        replaced = [episode_id for episode_id, action in episodes.items() if action == "replaces"]
        added: dict[int, Any] = {}
        if version is not None and replaced:
            added = {
                episode_id: moment
                for episode_id, moment in db.execute(
                    select(EpisodeVersion.episode_id, EpisodeFile.added_at)
                    .join(EpisodeFile, EpisodeFile.id == EpisodeVersion.episode_file_id)
                    .where(EpisodeVersion.version_id == version.id, EpisodeVersion.episode_id.in_(replaced))
                ).tuples()
            }
        # A release that also brings an episode that arrived after the pause may upgrade it.
        held = all(guard.blocks(added.get(episode_id)) for episode_id in replaced)
        return True, ("upgrades_paused" if held else None)


_UPGRADE_MESSAGES = {
    "upgrades_paused": "Upgrades of what was there when they were paused wait until the pause ends.",
    "upgrade_limit": "Today's number of automatic upgrades is reached; the next one waits.",
}


async def _check_upgrade(title_id: int, definition_id: int, episodes: dict[int, str] | None, origin: str) -> bool:
    """Whether the load is an upgrade. Raises ``LoadError`` 409 when an automatic upgrade is held back."""
    upgrade, code = await asyncio.to_thread(_upgrade_check, title_id, definition_id, episodes, origin)
    if code is not None:
        logger.info("Title %d: an automatic upgrade is held back (%s)", title_id, code)
        raise LoadError(meldung(code, _UPGRADE_MESSAGES[code]), 409)
    return upgrade


def _after_hand_over(handed: HandedOver) -> None:
    tracking.soon()
    if handed.unsure:
        from . import foreign

        foreign.soon()


async def _grab_album(found: search_jobs.FoundRelease, definition_id: int, confirm: list[str]) -> int:
    """Load a release of an album search for the music version (decisions 1 to 4).

    As for a movie: the version must be able to load, a release the profile refuses or one on the blocklist needs the
    owner's confirmation. The verdict of the search stands in for the version's result.
    """
    verdict = found.release.get("verdict") or {}
    if not await asyncio.to_thread(_title_exists, found.title_id):
        raise LoadError(meldung("invalid_input", "The input is not valid.", fields=["version_id"]), 422)
    with _claim(found.title_id, definition_id):
        prepared = await asyncio.to_thread(_prepare, found, definition_id, [])
        if definition_id not in prepared.facts.versions:
            raise LoadError(meldung("invalid_input", "The input is not valid.", fields=["version_id"]), 422)
        code = release_block(prepared.facts, definition_id, found.protocol)
        if code is not None:
            raise load_error(code)
        if not verdict.get("accepted") and "not_fitting" not in confirm:
            raise LoadError(meldung("release_not_fitting", "This release does not fit the version's profile."), 409)
        upgrade = await _check_upgrade(found.title_id, definition_id, None, found.origin)
        blocked = store.is_blocked(
            prepared.facts.blocklist,
            protocol=found.protocol,
            release_title=found.release["title"],
            info_hash=found.info_hash,
        )
        if blocked and "blocklisted" not in confirm:
            raise _blocklisted()
        try:
            fetched = await fetch.fetch(found.link or "", found.protocol, f"release:{found.indexer_id}")
        except fetch.FetchError as exc:
            logger.info(
                "Album %d: the release file of indexer %d could not be used: %s",
                found.title_id,
                found.indexer_id,
                exc.code,
            )
            if exc.retry_after:
                await asyncio.to_thread(store.pause_indexer, found.indexer_id, exc.retry_after)
            raise LoadError(exc.detail, exc.status) from exc
        if "blocklisted" not in confirm and store.is_blocked_hash(prepared.facts.blocklist, fetched.info_hash):
            raise _blocklisted()
        handed = await _hand_over(prepared, found, fetched, urgent=not upgrade)
        result = {"accepted": bool(verdict.get("accepted")), "below_target": bool(verdict.get("for_now"))}
        row_id = await asyncio.to_thread(
            _record_patiently,
            found,
            definition_id,
            result,
            prepared,
            handed.choice,
            handed.download_id,
            confirm,
            None,
            handed.unsure,
            upgrade,
        )
    await asyncio.to_thread(_after_album_grab, row_id, found)
    _after_hand_over(handed)
    return row_id


def _after_album_grab(download_id: int, found: search_jobs.FoundRelease) -> None:
    """The album download's scope and step, and its album's tracks moved to the front of the loading job
    (decision 4): filing waits for them, not the other way round."""
    from ..music import loading as music_loading

    with SessionLocal() as db:
        row = db.get(Download, download_id)
        if row is None:
            return
        row.scope = store.ALBUM_SCOPE
        row.quality = found.release.get("step") or row.quality
        db.commit()
    music_loading.hurry_album(found.title_id)


_SERIES_MESSAGES = {
    "release_no_gain": "This release neither fills nor replaces an episode of the version.",
    "release_no_episodes": "This release holds no episode the version watches.",
}


def _episode_codes(result: dict[str, Any], episode_ids: Collection[int]) -> list[str]:
    return sorted(str(row["code"]) for row in result.get("episodes") or [] if row.get("episode_id") in set(episode_ids))


@dataclass
class TakeResult:
    release_key: str
    download_id: int | None = None
    error: dict[str, Any] | None = None


async def grab_takes(search_id: str, definition_id: int, confirm: list[str]) -> list[TakeResult]:
    """Load every release a series search would take for a version, one after the other (decision 3).

    A release that fails leaves the others; each result names its download or its error. Each release is loaded for
    the episodes the set took it for, so a pack does not claim an episode a single release of the set brings.
    """
    try:
        takes = search_jobs.series_takes(search_id, definition_id)
    except search_jobs.SearchGone as exc:
        raise LoadError(
            meldung("search_expired", "This search is not kept any more. Please search again."), 404
        ) from exc
    if not takes:
        raise LoadError(meldung("nothing_to_take", "The search would take nothing for this version."), 409)
    results: list[TakeResult] = []
    for key, codes in takes:
        try:
            download_id = await grab(search_id, key, definition_id, confirm, only=codes)
        except LoadError as exc:
            results.append(TakeResult(release_key=key, error=dict(exc.detail)))
            logger.info("A release of a set for version %d was not loaded: %s", definition_id, exc.code)
            continue
        results.append(TakeResult(release_key=key, download_id=download_id))
    return results


def _blocklisted() -> LoadError:
    return LoadError(meldung("release_blocklisted", "This release is on the blocklist of the title."), 409)


ERRORS = (
    (404, "search_expired"),
    (404, "release_not_found"),
    (409, "version_fed_by_source"),
    (409, "version_no_profile"),
    (409, "version_no_folder"),
    (409, "no_client_for_protocol"),
    (409, "download_active"),
    (409, "episodes_downloading"),
    (409, "release_not_fitting"),
    (409, "release_blocklisted"),
    (409, "release_no_gain"),
    (409, "release_no_episodes"),
    *fetch.ERRORS,
    (502, "client_refused"),
    *downloaders.ERRORS,
)
