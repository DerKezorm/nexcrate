"""Loading artists from MusicBrainz in the background (M1.2).

A job every 5 seconds, working up to 60 seconds per run, in fixed steps per artist with the state stored
(decision 19): ``queued``, ``groups`` (the release groups are browsed, page by page), ``releases`` (the releases and
tracks of the albums that need them, album by album), ``ready``, or ``failed`` with the code. A restart goes on at
the stored step; every page and every album is committed on its own. A browse interrupted by the budget starts
again at page one: the pages come from the cache for a day, so nothing is asked twice.

Order (decision 20): what the owner just added (priority 0), an album page waiting for its releases (1), an import
(2), a due refresh (3); within a priority the oldest first. Refreshes follow decision 21 and are marked due by
``groups_due_at`` and ``releases_due_at``.

"MusicBrainz is busy" says nothing about the artist. An album whose full page stays busy is asked at once in smaller
pages (``load_releases_of``); when those stay busy too, the album remembers it itself (``releases_state = "failed"``,
next try after ``RETRY_AFTER``), the artist goes on with its next album, and the job stands back for
``BUSY_PAUSE_SECONDS``. A busy browse costs the same pause.

Everything here runs in a worker thread with a short event loop of its own (``musicbrainz.run_sync``), on the
background lane of the gate; the owner's requests overtake it.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session as OrmSession

from ...db import SessionLocal, get_setting, set_setting
from ...models import Artist, Download, Release, ReleaseTrack, Title, Version, utcnow
from . import musicbrainz as mb
from . import store, target

logger = logging.getLogger("nexcrate.music")

JOB_NAME = "music_load"
#: ⚠️ The pause between two runs. It was 30 seconds: a third of the time nothing was loaded, half an hour on the
#: owner's 314 artists. A run with nothing to do costs four small queries.
INTERVAL_SECONDS = 5
#: How long one run works before it hands back, so that a shutdown never waits long for the thread.
BUDGET_SECONDS = 60.0
#: After MusicBrainz stayed busy through every retry of a request the job asks nothing for this long.
BUSY_PAUSE_SECONDS = 60.0
#: The pages an album is asked in after its full page (``mb.PAGE``) stayed busy.
SMALL_PAGE = 25
#: How often in a row the browse of one artist may end in "busy" before the artist is marked failed.
BUSY_STRIKES = 3
#: After a failure the artist waits this long before the next try (M1.2, step 5).
RETRY_AFTER = timedelta(hours=12)

PRIORITY_OWNER = 0
PRIORITY_PAGE = 1
PRIORITY_IMPORT = 2
PRIORITY_REFRESH = 3
UNFINISHED = ("queued", "groups", "releases")
#: The version of the target rule the stored targets were made with.
SETTING_RULE = "music_target_rule"
#: Counts up when the browse changes what it lists: every artist is browsed again, once, after the waiting ones
#: (``apply_browse_change``). 2: only what MusicBrainz's own artist page shows.
BROWSE_VERSION = "2"
SETTING_BROWSE = "music_browse_version"
RULE_CHUNK = 200

#: For tests: the clock of the budget.
clock = time.monotonic

#: The clock value until which the job asks nothing (``BUSY_PAUSE_SECONDS``).
_paused_until = 0.0
#: Artists (ids) whose browse ended in "busy", with how often in a row.
_busy_strikes: dict[int, int] = {}


def reset_state() -> None:
    """For tests: no pause, nothing remembered."""
    global _paused_until
    _paused_until = 0.0
    _busy_strikes.clear()


def paused() -> bool:
    return clock() < _paused_until


def _pause() -> None:
    global _paused_until
    _paused_until = clock() + BUSY_PAUSE_SECONDS


def now() -> datetime:
    return utcnow()


# --- Choosing work ------------------------------------------------------------------- #


def next_artist(db: OrmSession, moment: datetime) -> Artist | None:
    """The artist to work on: an unfinished one by priority and age, else a due refresh (decision 20)."""
    # Various Artists is never browsed (decision 35), but its samplers need their releases like any album: it waits
    # in the step ``releases`` only.
    pending = db.scalar(
        select(Artist)
        .where(
            Artist.load_state.in_(UNFINISHED),
            or_(Artist.is_various.is_(False), Artist.load_state == "releases"),
        )
        .order_by(Artist.load_priority, Artist.added, Artist.id)
        .limit(1)
    )
    if pending is not None:
        return pending
    due = db.scalar(
        select(Artist)
        .where(
            Artist.is_various.is_(False),
            Artist.load_state.in_(("ready", "failed")),
            Artist.groups_due_at.is_not(None),
            Artist.groups_due_at <= moment,
        )
        .order_by(Artist.groups_due_at, Artist.id)
        .limit(1)
    )
    if due is not None:
        queue(db, due, PRIORITY_REFRESH)
        db.flush()
    return due


def queue(db: OrmSession, artist: Artist, priority: int) -> None:
    """Put an artist (back) into the queue: for "refresh now" (decision 39), a waiting page, a due refresh."""
    if artist.load_state not in UNFINISHED:
        # Various Artists has no catalogue to browse: only the releases of its samplers are loaded.
        artist.load_state = "releases" if artist.is_various else "queued"
        artist.load_done = 0
        artist.load_total = None
    artist.load_error = None
    artist.load_priority = min(artist.load_priority, priority) if artist.load_state in UNFINISHED else priority
    artist.updated_at = now()


def albums_needing_releases(db: OrmSession, artist_id: int, moment: datetime) -> list[Title]:
    """Albums whose releases are missing or due (decision 6): watched or with files, never a gone one."""
    rows = db.execute(
        select(Title, Version)
        .outerjoin(Version, Version.title_id == Title.id)
        .where(Title.kind == "album", Title.artist_id == artist_id, Title.mb_gone_at.is_(None))
        .order_by(Title.first_release_date.desc().nulls_last(), Title.id)
    ).tuples()
    loading = set(
        db.scalars(
            select(Download.title_id).where(
                Download.scope == "album",
                Download.state.in_(("queued", "downloading", "paused", "completed", "importing", "problem")),
                Download.title_id.in_(select(Title.id).where(Title.artist_id == artist_id)),
            )
        )
    )
    wanted: list[Title] = []
    for title, version in rows:
        # An album with a running download needs its tracks to be filed (decision 4).
        needs = title.id in loading or (version is not None and (version.monitored or version.has_file))
        # An album that did not load waits for its next try (``releases_due_at``): every run would ask again else.
        missing = title.releases_state in (None, "none", "heads") or (
            title.releases_state == "failed" and title.releases_due_at is None
        )
        due = title.releases_due_at is not None and title.releases_due_at <= moment
        if needs and (missing or due):
            wanted.append(title)
    return wanted


def hurry_album(title_id: int) -> None:
    """An album was loaded before its tracks are known (decision 4): its artist moves to the
    front of the queue, so the tracks come before the download is filed. Never raises."""
    try:
        with SessionLocal() as db:
            title = db.get(Title, title_id)
            if title is None or title.releases_state == "tracks" or title.artist_id is None:
                return
            artist = db.get(Artist, title.artist_id)
            if artist is None:
                return
            title.releases_due_at = now()
            queue(db, artist, PRIORITY_OWNER)
            db.commit()
    except OperationalError:
        logger.info("Album %d: its tracks could not be moved to the front of the queue now", title_id)
        return
    logger.info("Album %d is loaded before its tracks are known; its artist moves to the front", title_id)


# --- Steps ------------------------------------------------------------------- #


def _fail(db: OrmSession, artist: Artist, code: str, moment: datetime) -> None:
    artist.load_state = "failed"
    artist.load_error = code
    artist.groups_due_at = moment + RETRY_AFTER
    artist.updated_at = moment
    db.commit()
    logger.warning("Loading artist %d (%s) failed: %s", artist.id, artist.name, code)


def load_groups(db: OrmSession, artist: Artist, *, budget_end: float, lane: str = mb.BACKGROUND) -> bool:
    """Browse the release groups page by page; True when the step is complete.

    The first load of an artist the owner added happens in the add dialog with its choice (decision 33); here a
    first load only happens for an artist without stored groups, and watches studio albums when ``monitor_new``
    is ``all``.
    """
    definition = store.definition(db)
    moment = now()
    if artist.is_various:
        # Never browsed (decision 35), whatever queued it: straight to the releases of its samplers.
        artist.load_state = "releases"
        db.commit()
        return True
    artist.load_state = "groups"
    artist.load_done = 0
    artist.load_total = None
    artist.load_error = None
    db.commit()
    first_load = artist.groups_refreshed_at is None
    # An imported artist keeps Lidarr's watching (decision 47); the owner's own gets studio albums when it wants new
    # ones and never had its groups stored (the add dialog stores them itself, decision 33).
    watch = "studio" if artist.monitor_new == "all" and artist.added_by == "owner" else "none"
    seen: set[str] = set()
    offset = 0
    # Once per artist, not per page: the library's artists for the credits of joint albums.
    known = store.library_artists(db)
    while True:
        page = mb.run_sync(mb.browse_release_groups(artist.mbid, offset, lane))
        moment = now()
        for group in page.items:
            store.apply_release_group(
                db,
                artist,
                group,
                moment=moment,
                first_load=first_load,
                watch=watch,
                definition=definition,
                artists_by_mbid=known,
            )
            seen.add(group.mbid)
        artist.groups_total = page.total
        artist.load_done += 1
        artist.load_total = (page.total + mb.PAGE - 1) // mb.PAGE if page.total else 1
        artist.updated_at = moment
        db.commit()
        if page.next_offset is None:
            break
        offset = page.next_offset
        if clock() > budget_end:
            return False
    gone = 0
    dropped = 0
    for title in store.unlisted_groups(db, artist, seen):
        if store.album_version(db, title.id) is None:
            # The browse leaves out what has no official release (``browse_release_groups``). A catalogue entry
            # nobody chose goes without a question, whatever became of it: a merged one is listed under its new id.
            db.delete(title)
            dropped += 1
            continue
        # Decision 22: a lookup tells a merge (301 to another id) from a real 404. Same id and still this artist's:
        # it has no official release, so the browse hides it, and it stays as it is for the files the owner has.
        # Same id, another artist: no longer credited here; it stays with the mark.
        # ⚠️ What the albums before this one changed is written first: a merge flushes, and the lookup below asks
        # MusicBrainz for a second or, when it is busy, a minute. Until 22.09.2026 that held the write lock.
        db.commit()
        try:
            found = mb.run_sync(mb.lookup_release_group(title.mbid or "", lane))
        except mb.MusicBrainzError as exc:
            if exc.code != "musicbrainz_not_found":
                raise
            found = None
        if found is not None and found.mbid != title.mbid:
            store.adopt_merged_group(db, title, found.mbid, moment)
            seen.add(found.mbid)
            continue
        if found is not None and any(credit.get("mbid") == artist.mbid for credit in found.credit):
            continue
        store.mark_group_gone(db, title, moment, definition)
        gone += 1
    if gone or dropped:
        logger.info(
            "Artist %d (%s): %d albums no longer listed by MusicBrainz, %d catalogue entries without an official "
            "release dropped",
            artist.id,
            artist.name,
            gone,
            dropped,
        )
    artist.groups_refreshed_at = moment
    artist.groups_due_at = store.groups_due(artist, newest_album(db, artist.id), moment)
    artist.load_state = "releases"
    artist.load_done = 0
    artist.load_total = None
    artist.updated_at = moment
    db.commit()
    logger.info("Artist %d (%s): %d release groups loaded", artist.id, artist.name, len(seen))
    return True


def newest_album(db: OrmSession, artist_id: int) -> str | None:
    return db.scalar(
        select(Title.first_release_date)
        .where(Title.kind == "album", Title.artist_id == artist_id, Title.first_release_date.is_not(None))
        .order_by(Title.first_release_date.desc())
        .limit(1)
    )


def _browse_releases(title: Title, lane: str, limit: int, waits: tuple[float, ...] | None) -> list[mb.ReleaseData]:
    releases: list[mb.ReleaseData] = []
    offset = 0
    while True:
        page = mb.run_sync(
            mb.browse_releases(title.mbid or "", offset, tracks=True, lane=lane, limit=limit, waits=waits)
        )
        releases.extend(page.items)
        if page.next_offset is None:
            return releases
        offset = page.next_offset


def load_releases_of(db: OrmSession, title: Title, *, lane: str = mb.BACKGROUND) -> dict[str, int]:
    """Every official release of one album with its tracks, then the target rule (decisions 6, 29).

    ⚠️ A full page that stays busy is asked again at once in pages of ``SMALL_PAGE`` (``mb.FULL_PAGE_WAITS``): on the
    owner's instance that cost a minute of retries and the whole artist each time, and the small pages came at once.
    Only when they stay busy as well does ``musicbrainz_busy`` leave here.
    """
    language = store.account_language(db)
    try:
        releases = _browse_releases(title, lane, mb.PAGE, mb.FULL_PAGE_WAITS if lane == mb.BACKGROUND else None)
    except mb.MusicBrainzError as exc:
        if exc.code != "musicbrainz_busy":
            raise
        logger.info("MusicBrainz stayed busy for a full page of album %d, asking in pages of %d", title.id, SMALL_PAGE)
        releases = _browse_releases(title, lane, SMALL_PAGE, None)
    moment = now()
    counts = store.apply_releases(db, title, releases, moment=moment, complete=True)
    version = store.album_version(db, title.id)
    title.releases_due_at = store.releases_due(
        title.first_release_date, bool(version is not None and version.has_file), moment
    )
    store.refresh_targets_of_title(db, title, moment=moment, language=language)
    title.updated_at = moment
    db.commit()
    return counts


def _load_album(db: OrmSession, artist: Artist, title: Title) -> bool:
    """The releases of one album; False when MusicBrainz stayed busy for it and the job has to stand back.

    ⚠️ On the owner's instance single ``/release`` requests stayed at 503 for a minute, six times in two hours, and
    every time the whole artist was marked failed for 12 hours. Now the album remembers it and the artist goes on
    without it.
    """
    try:
        load_releases_of(db, title)
    except mb.MusicBrainzError as exc:
        if exc.code != "musicbrainz_busy":
            raise
        db.rollback()
        _pause()
        moment = now()
        title.releases_state = "failed"
        title.releases_due_at = moment + RETRY_AFTER
        # The artist comes back for it: nothing else looks at the albums of a finished artist.
        artist.groups_due_at = min(artist.groups_due_at or moment + RETRY_AFTER, moment + RETRY_AFTER)
        db.commit()
        logger.warning(
            "Loading the releases of album %d (%s) failed: musicbrainz_busy; artist %d goes on without it",
            title.id,
            title.title,
            artist.id,
        )
        return False
    return True


def load_releases(db: OrmSession, artist: Artist, *, budget_end: float) -> bool:
    """The releases of every album that needs them, album by album; True when the step is complete."""
    moment = now()
    albums = albums_needing_releases(db, artist.id, moment)
    artist.load_total = len(albums)
    artist.load_done = 0
    db.commit()
    for title in albums:
        if not _load_album(db, artist, title):
            # MusicBrainz stayed busy: the job stands back, and the next run goes on with this artist.
            return False
        artist.load_done += 1
        artist.updated_at = now()
        db.commit()
        if clock() > budget_end:
            return False
    artist.load_state = "ready"
    artist.load_priority = PRIORITY_REFRESH
    artist.load_done = 0
    artist.load_total = None
    artist.updated_at = now()
    db.commit()
    logger.info("Artist %d (%s) is ready: %d albums with releases", artist.id, artist.name, len(albums))
    return True


def work(db: OrmSession, artist: Artist, *, budget_end: float) -> None:
    """One artist through its steps until done or out of budget."""
    try:
        if artist.load_state in ("queued", "groups"):
            if not load_groups(db, artist, budget_end=budget_end):
                return
            _busy_strikes.pop(artist.id, None)
        if artist.load_state == "releases":
            load_releases(db, artist, budget_end=budget_end)
    except mb.MusicBrainzError as exc:
        db.rollback()
        if exc.code == "musicbrainz_disabled":
            logger.info("MusicBrainz is switched off; artist %d (%s) waits", artist.id, artist.name)
            return
        if exc.code == "musicbrainz_busy":
            # Only the browse gets here (``_load_album`` keeps an album's): busy says nothing about the artist.
            _pause()
            strikes = _busy_strikes.get(artist.id, 0) + 1
            if strikes < BUSY_STRIKES:
                _busy_strikes[artist.id] = strikes
                logger.info(
                    "MusicBrainz stayed busy while browsing artist %d (%s); the job goes on after a pause",
                    artist.id,
                    artist.name,
                )
                return
        _busy_strikes.pop(artist.id, None)
        _fail(db, artist, exc.code, now())
    except OperationalError as exc:
        db.rollback()
        if not mb.database_busy(exc):
            logger.exception("Loading artist %d (%s) failed unexpectedly", artist.id, artist.name)
            _fail(db, artist, "music_load_failed", now())
            return
        # ⚠️ Another writer held the database for longer than SQLite waits (an import of a large library). Nothing
        # is wrong with the artist: it stays in its step, and the next run goes on. Marking it failed cost 12 hours.
        logger.info("The database was busy while loading artist %d (%s); the next run goes on", artist.id, artist.name)
    except Exception:
        # Anything else would leave the artist in its step and the job retrying it every 30 seconds without a word.
        db.rollback()
        logger.exception("Loading artist %d (%s) failed unexpectedly", artist.id, artist.name)
        _fail(db, artist, "music_load_failed", now())


def run_job() -> None:
    """The job: artists in order until the budget is spent. Nothing to do costs one query."""
    try:
        _run()
    except OperationalError as exc:
        if not mb.database_busy(exc):
            raise
        # Between two artists (choosing the next, marking one failed): as little a failure as inside ``work``.
        logger.info("The database was busy; the loading job goes on with its next run")


def apply_rule_change(db: OrmSession) -> int:
    """After an upgrade that changed the rule (``target.RULE_VERSION``): the rule runs again for every album with
    loaded releases, once. A target the owner chose or files hold stays (``refresh_target``)."""
    if get_setting(db, SETTING_RULE, "1") == target.RULE_VERSION:
        return 0
    moment = now()
    language = store.account_language(db)
    # ⚠️ Until 18.09.2026 every Lidarr import wrote the track count of its heads (0) over what MusicBrainz had
    # loaded (``store.apply_releases``). The stored tracks say what is true.
    counted = select(func.count(ReleaseTrack.id)).where(ReleaseTrack.release_id == Release.id).scalar_subquery()
    repaired = db.execute(
        update(Release).where(Release.tracks_loaded.is_(True), Release.track_count == 0).values(track_count=counted)
    ).rowcount
    db.commit()
    if repaired:
        logger.info("Track counts of %d loaded releases repaired from their stored tracks", repaired)
    done = 0
    for title in db.scalars(select(Title).where(Title.kind == "album", Title.releases_state == "tracks")):
        store.refresh_targets_of_title(db, title, moment=moment, language=language)
        done += 1
        if done % RULE_CHUNK == 0:
            # Short writes: an import may be running (see ``work``).
            db.commit()
    set_setting(db, SETTING_RULE, target.RULE_VERSION)
    db.commit()
    logger.info("The target rule changed (now %s): ran again for %d albums", target.RULE_VERSION, done)
    return done


def apply_browse_change(db: OrmSession) -> int:
    """After an upgrade that changed what the browse lists (``BROWSE_VERSION``): every artist whose groups are stored
    is due at once. An ended artist would otherwise keep its bootleg collections for 30 days."""
    if get_setting(db, SETTING_BROWSE, "1") == BROWSE_VERSION:
        return 0
    due = db.execute(
        update(Artist)
        .where(Artist.is_various.is_(False), Artist.groups_refreshed_at.is_not(None))
        .values(groups_due_at=now())
    ).rowcount
    set_setting(db, SETTING_BROWSE, BROWSE_VERSION)
    db.commit()
    logger.info("The browse changed (now %s): %d artists are browsed again", BROWSE_VERSION, due)
    return due


def _run() -> None:
    if paused():
        return
    budget_end = clock() + BUDGET_SECONDS
    with SessionLocal() as db:
        apply_rule_change(db)
        apply_browse_change(db)
        while clock() <= budget_end:
            artist = next_artist(db, now())
            if artist is None:
                db.commit()
                return
            db.commit()
            work(db, artist, budget_end=budget_end)
            db.expire_all()
            if artist.load_state in UNFINISHED or paused():
                # Out of budget in the middle of this artist, or MusicBrainz stayed busy: the next run goes on.
                return


# --- Status ------------------------------------------------------------------- #


def status(db: OrmSession) -> dict[str, Any]:
    """For the interface: the queue, the artist being loaded, and the counters of the gate (M1.2)."""
    queued = list(
        db.scalars(
            select(Artist)
            .where(Artist.load_state.in_(UNFINISHED))
            .order_by(Artist.load_priority, Artist.added, Artist.id)
        )
    )
    current = next((artist for artist in queued if artist.load_state in ("groups", "releases")), None)
    failed = list(db.scalars(select(Artist).where(Artist.load_state == "failed").order_by(Artist.updated_at.desc())))
    return {
        "enabled": mb.enabled(db),
        "queued": len(queued),
        "current": (
            {
                "artist_id": current.id,
                "name": current.name,
                "step": current.load_state,
                "done": current.load_done,
                "total": current.load_total,
            }
            if current is not None
            else None
        ),
        "failed": [{"artist_id": row.id, "name": row.name, "code": row.load_error} for row in failed[:10]],
        "failed_albums": int(
            db.scalar(select(func.count(Title.id)).where(Title.kind == "album", Title.releases_state == "failed")) or 0
        ),
        **mb.counters(),
    }
