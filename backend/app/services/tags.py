"""Tags on movies, series and artists.

One place for everything the interface, the imports and ``/api/v1`` do with tags: names are cleaned here, links are set
here, and the tags of a Radarr, Sonarr or Lidarr connection are mirrored here. Tags have no effect on searching or
loading yet (that is T2).

⚠️ Log lines carry ids and counts, never a tag's name: a name may say something about the library.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import ColumnElement, delete, func, or_, select, update
from sqlalchemy.orm import Session as OrmSession

from ..meldungen import error
from ..models import ArtistTag, DownloadClientTag, Indexer, IndexerTag, Tag, Title, TitleTag
from ..models.tags import LABEL_MAX_LENGTH
from .schreibweisen import nfc

logger = logging.getLogger("nexcrate.tags")

#: How many tags one title or artist may carry, and one request may name.
PER_ITEM_MAX = 50


def clean(raw: str) -> str | None:
    """The name as stored: trimmed, inner spaces as one, in lower case as Radarr, Sonarr and Lidarr keep it. None for
    an empty name; a name that is too long or not printable is ``invalid_input``."""
    text = " ".join(nfc(str(raw)).split()).lower()
    if not text:
        return None
    if len(text) > LABEL_MAX_LENGTH or not text.isprintable():
        raise error("invalid_input", "The input is not valid.", 422, fields=["tags"])
    return text


def clean_or_none(raw: str) -> str | None:
    """``clean``, but a name nexcrate cannot keep is None instead of an error."""
    return _lenient(raw)


def _lenient(raw: str) -> str | None:
    """An app's name as ``clean`` makes it; one nexcrate cannot keep is left out instead of failing an import."""
    try:
        return clean(raw)
    except Exception:  # noqa: BLE001
        return None


def cleaned(labels: Iterable[str]) -> list[str]:
    found = list(dict.fromkeys(label for label in (clean(item) for item in labels) if label))
    if len(found) > PER_ITEM_MAX:
        raise error("invalid_input", "The input is not valid.", 422, fields=["tags"])
    return found


def ensure(db: OrmSession, labels: Iterable[str]) -> dict[str, int]:
    """Name to id, made when missing. ``labels`` must be cleaned."""
    wanted = list(dict.fromkeys(labels))
    if not wanted:
        return {}
    found = dict(db.execute(select(Tag.label, Tag.id).where(Tag.label.in_(wanted))).tuples().all())
    for label in wanted:
        if label not in found:
            row = Tag(label=label)
            db.add(row)
            db.flush()
            found[label] = row.id
    return found


def _names(db: OrmSession, rows: Iterable[tuple[int, int]]) -> dict[int, list[str]]:
    pairs = list(rows)
    labels = dict(
        db.execute(select(Tag.id, Tag.label).where(Tag.id.in_({tag_id for _owner, tag_id in pairs}))).tuples().all()
    ) if pairs else {}  # fmt: skip
    found: dict[int, list[str]] = defaultdict(list)
    for owner, tag_id in pairs:
        if tag_id in labels:
            found[owner].append(labels[tag_id])
    return {owner: sorted(names) for owner, names in found.items()}


def of_titles(db: OrmSession, title_ids: Iterable[int]) -> dict[int, list[str]]:
    """Title id to its tags, sorted. A title without tags is missing from the answer."""
    ids = list(set(title_ids))
    if not ids:
        return {}
    return _names(db, db.execute(select(TitleTag.title_id, TitleTag.tag_id).where(TitleTag.title_id.in_(ids))).tuples())


def of_artists(db: OrmSession, artist_ids: Iterable[int]) -> dict[int, list[str]]:
    ids = list(set(artist_ids))
    if not ids:
        return {}
    return _names(
        db, db.execute(select(ArtistTag.artist_id, ArtistTag.tag_id).where(ArtistTag.artist_id.in_(ids))).tuples()
    )


def _set(db: OrmSession, model: Any, column: Any, owner_id: int, labels: list[str]) -> bool:
    """Make the owner's tags of one title or artist exactly ``labels``. A tag of a connection that is named stays and
    becomes the owner's; one that is not named goes, and the next import of that connection brings it back."""
    ids = ensure(db, labels)
    rows = {row.tag_id: row for row in db.scalars(select(model).where(column == owner_id))}
    changed = False
    for tag_id, row in rows.items():
        if tag_id not in ids.values():
            db.delete(row)
            changed = True
        elif row.source_id is not None:
            row.source_id = None
            changed = True
    for tag_id in ids.values():
        if tag_id not in rows:
            db.add(model(**{column.key: owner_id, "tag_id": tag_id, "source_id": None}))
            changed = True
    db.flush()
    return changed


def set_title(db: OrmSession, title_id: int, labels: Iterable[str]) -> bool:
    return _set(db, TitleTag, TitleTag.title_id, title_id, cleaned(labels))


def set_artist(db: OrmSession, artist_id: int, labels: Iterable[str]) -> bool:
    return _set(db, ArtistTag, ArtistTag.artist_id, artist_id, cleaned(labels))


def change_many(db: OrmSession, *, title_ids: Iterable[int] = (), artist_ids: Iterable[int] = (),
                add: Iterable[str] = (), remove: Iterable[str] = ()) -> int:  # fmt: skip
    """Add and remove tags on many titles or artists at once. Returns how many links changed."""
    adding = ensure(db, cleaned(add))
    removing = cleaned(remove)
    changed = 0
    for model, column, ids in ((TitleTag, TitleTag.title_id, list(title_ids)), (ArtistTag, ArtistTag.artist_id,
                                                                                  list(artist_ids))):  # fmt: skip
        if not ids:
            continue
        if removing:
            gone = select(Tag.id).where(Tag.label.in_(removing))
            result = db.execute(delete(model).where(column.in_(ids), model.tag_id.in_(gone)))
            changed += int(result.rowcount or 0)
        for tag_id in adding.values():
            present = set(db.scalars(select(column).where(column.in_(ids), model.tag_id == tag_id)))
            for owner_id in ids:
                if owner_id not in present:
                    db.add(model(**{column.key: owner_id, "tag_id": tag_id, "source_id": None}))
                    changed += 1
    db.flush()
    return changed


def sync_titles(db: OrmSession, source_id: int, wanted: Mapping[int, Iterable[str]]) -> int:
    """Mirror a connection's tags onto its titles: ``wanted`` is title id to the app's names. What the app no longer
    gives goes, the owner's tags stay. Returns how many links changed."""
    return _sync(db, TitleTag, TitleTag.title_id, source_id, wanted)


def sync_artists(db: OrmSession, source_id: int, wanted: Mapping[int, Iterable[str]]) -> int:
    """The same for Lidarr's artists; Lidarr's artist list comes whole in every run."""
    return _sync(db, ArtistTag, ArtistTag.artist_id, source_id, wanted)


def _sync(db: OrmSession, model: Any, column: Any, source_id: int, wanted: Mapping[int, Iterable[str]]) -> int:
    targets = {owner_id: [label for label in (_lenient(item) for item in labels) if label]
               for owner_id, labels in wanted.items()}  # fmt: skip
    ids = ensure(db, {label for labels in targets.values() for label in labels})
    # Every link of this connection: what the app dropped goes.
    rows = db.scalars(select(model).where(model.source_id == source_id))
    current = {(getattr(row, column.key), row.tag_id): row for row in rows}
    changed = 0
    desired = {(owner_id, ids[label]) for owner_id, labels in targets.items() for label in labels}
    for key, row in current.items():
        if key not in desired:
            db.delete(row)
            changed += 1
    # A link the owner has already (or another connection) stays as it is: one tag once per title.
    taken = set(
        db.execute(select(column, model.tag_id).where(column.in_(list(targets)))).tuples()
    ) if targets else set()  # fmt: skip
    for key in desired:
        if key not in taken:
            db.add(model(**{column.key: key[0], "tag_id": key[1], "source_id": source_id}))
            changed += 1
    db.flush()
    return changed


def release(db: OrmSession, source_id: int) -> int:
    """A takeover: the connection's tags become the owner's. Returns how many links it had."""
    moved = 0
    for model in (TitleTag, ArtistTag):
        result = db.execute(update(model).where(model.source_id == source_id).values(source_id=None))
        moved += int(result.rowcount or 0)
    return moved


def listing(db: OrmSession) -> list[dict[str, Any]]:
    """Every tag with how many movies, series and artists carry it, by name."""
    counts: dict[int, dict[str, int]] = defaultdict(lambda: {"movie": 0, "series": 0, "artist": 0})
    for tag_id, kind, count in db.execute(
        select(TitleTag.tag_id, Title.kind, func.count())
        .join(Title, Title.id == TitleTag.title_id)
        .group_by(TitleTag.tag_id, Title.kind)
    ).tuples():
        if kind in ("movie", "series"):
            counts[tag_id][kind] = int(count)
    for tag_id, count in db.execute(select(ArtistTag.tag_id, func.count()).group_by(ArtistTag.tag_id)).tuples():
        counts[tag_id]["artist"] = int(count)
    return [
        {"id": row.id, "label": row.label, **counts[row.id]}
        for row in db.scalars(select(Tag).order_by(Tag.label))
    ]


def rename(db: OrmSession, tag_id: int, raw: str) -> Tag:
    """A new name; when another tag has it already, the two become one."""
    row = db.get(Tag, tag_id)
    if row is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    label = clean(raw)
    if label is None:
        raise error("invalid_input", "The input is not valid.", 422, fields=["label"])
    other = db.scalar(select(Tag).where(Tag.label == label, Tag.id != tag_id))
    if other is None:
        row.label = label
        db.flush()
        return row
    for model, column in ((TitleTag, TitleTag.title_id), (ArtistTag, ArtistTag.artist_id)):
        both = set(db.scalars(select(column).where(model.tag_id == other.id)))
        if both:
            db.execute(delete(model).where(model.tag_id == tag_id, column.in_(both)))
        db.execute(update(model).where(model.tag_id == tag_id).values(tag_id=other.id))
    db.delete(row)
    db.flush()
    logger.info("Tag %d merged into tag %d", tag_id, other.id)
    return other


def remove(db: OrmSession, tag_id: int) -> None:
    row = db.get(Tag, tag_id)
    if row is None:
        raise error("not_found", "This does not exist, or not any more.", 404)
    db.execute(delete(TitleTag).where(TitleTag.tag_id == tag_id))
    db.execute(delete(ArtistTag).where(ArtistTag.tag_id == tag_id))
    db.delete(row)
    db.flush()


def title_ids_with(label: str) -> Any:
    """A select of the title ids carrying a tag, for the library's filter."""
    return select(TitleTag.title_id).join(Tag, Tag.id == TitleTag.tag_id).where(Tag.label == label)


def artist_ids_with(label: str) -> Any:
    return select(ArtistTag.artist_id).join(Tag, Tag.id == ArtistTag.tag_id).where(Tag.label == label)


def album_tags(db: OrmSession, titles: Iterable[Any]) -> dict[int, list[str]]:
    """An album shows the tags of the artist it belongs to: album title id to names."""
    owners = {row.id: row.artist_id for row in titles if getattr(row, "artist_id", None) is not None}
    by_artist = of_artists(db, owners.values())
    return {title_id: by_artist.get(artist_id, []) for title_id, artist_id in owners.items()}


# --- T2: what tags decide -------------------------------------------------------------------- #


def title_tag_ids(db: OrmSession, title_id: int | None) -> set[int]:
    """The tag ids a title counts with: its own, or for an album its artist's (as Lidarr binds at the artist). None
    (a title nexcrate does not have) has none."""
    if title_id is None or title_id <= 0:
        return set()
    row = db.execute(select(Title.kind, Title.artist_id).where(Title.id == title_id)).first()
    if row is None:
        return set()
    if row.kind == "album":
        if row.artist_id is None:
            return set()
        return set(db.scalars(select(ArtistTag.tag_id).where(ArtistTag.artist_id == row.artist_id)))
    return set(db.scalars(select(TitleTag.tag_id).where(TitleTag.title_id == title_id)))


def indexer_condition(tag_ids: set[int]) -> ColumnElement[bool]:
    """The indexers a title with these tags may ask, as in the apps: every one without a tag, and those sharing one."""
    tagged = select(IndexerTag.indexer_id)
    if not tag_ids:
        return Indexer.id.not_in(tagged)
    sharing = select(IndexerTag.indexer_id).where(IndexerTag.tag_id.in_(sorted(tag_ids)))
    return or_(Indexer.id.not_in(tagged), Indexer.id.in_(sharing))


def indexer_allows(db: OrmSession, indexer_id: int, title_ids: Iterable[int]) -> set[int]:
    """Of these titles, those this indexer may deliver for (RSS: the apps reject the rest in their decision)."""
    wanted = set(title_ids)
    mine = set(db.scalars(select(IndexerTag.tag_id).where(IndexerTag.indexer_id == indexer_id)))
    if not mine:
        return wanted
    return {title_id for title_id in wanted if title_tag_ids(db, title_id) & mine}


def clients_for(db: OrmSession, title_id: int | None, client_ids: list[int]) -> list[int]:
    """The download clients that may load for a title, in the given order, as in the apps: those sharing a tag when
    there is one, else those without a tag. Empty means none may."""
    if not client_ids:
        return []
    by_client: dict[int, set[int]] = defaultdict(set)
    for client_id, tag_id in db.execute(
        select(DownloadClientTag.client_id, DownloadClientTag.tag_id).where(DownloadClientTag.client_id.in_(client_ids))
    ).tuples():
        by_client[client_id].add(tag_id)
    wanted = title_tag_ids(db, title_id)
    sharing = [client_id for client_id in client_ids if by_client[client_id] & wanted]
    if sharing:
        return sharing
    return [client_id for client_id in client_ids if not by_client[client_id]]


def of_indexers(db: OrmSession, indexer_ids: Iterable[int]) -> dict[int, list[str]]:
    ids = list(set(indexer_ids))
    if not ids:
        return {}
    rows = db.execute(select(IndexerTag.indexer_id, IndexerTag.tag_id).where(IndexerTag.indexer_id.in_(ids))).tuples()
    return _names(db, rows)


def of_clients(db: OrmSession, client_ids: Iterable[int]) -> dict[int, list[str]]:
    ids = list(set(client_ids))
    if not ids:
        return {}
    query = select(DownloadClientTag.client_id, DownloadClientTag.tag_id).where(DownloadClientTag.client_id.in_(ids))
    return _names(db, db.execute(query).tuples())


def _set_plain(db: OrmSession, model: Any, column: Any, owner_id: int, labels: Iterable[str]) -> None:
    ids = set(ensure(db, cleaned(labels)).values())
    rows = {row.tag_id: row for row in db.scalars(select(model).where(column == owner_id))}
    for tag_id, row in rows.items():
        if tag_id not in ids:
            db.delete(row)
    for tag_id in ids - set(rows):
        db.add(model(**{column.key: owner_id, "tag_id": tag_id}))
    db.flush()


def set_indexer(db: OrmSession, indexer_id: int, labels: Iterable[str]) -> None:
    _set_plain(db, IndexerTag, IndexerTag.indexer_id, indexer_id, labels)


def set_client(db: OrmSession, client_id: int, labels: Iterable[str]) -> None:
    _set_plain(db, DownloadClientTag, DownloadClientTag.client_id, client_id, labels)


def names_of_ids(db: OrmSession, tag_ids: Iterable[int]) -> list[str]:
    ids = list(set(tag_ids))
    if not ids:
        return []
    return sorted(db.scalars(select(Tag.label).where(Tag.id.in_(ids))))
