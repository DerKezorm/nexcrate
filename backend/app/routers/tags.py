"""Tags: the list with rename and delete, the tags of one title or artist, and many at once."""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from ..deps import DbSession
from ..meldungen import error, error_responses
from ..models import Artist, Title, utcnow
from ..services import library, tags
from ..services.schreibweisen import query_keys

logger = logging.getLogger("nexcrate.tags")

router = APIRouter(tags=["tags"])

Labels = list[str]


class TagOut(BaseModel):
    id: int
    label: str
    movie: int = Field(description="Movies carrying it.")
    series: int
    artist: int


class TagsOut(BaseModel):
    items: list[TagOut] = Field(description="By name.")


class TagRename(BaseModel):
    label: str = Field(max_length=200)


class TagsIn(BaseModel):
    tags: Labels = Field(max_length=tags.PER_ITEM_MAX, description="Every tag it shall carry, by name.")


class TagsSet(BaseModel):
    tags: Labels


class TagsManyIn(BaseModel):
    kind: Literal["movie", "series", "artist"] = "movie"
    add: Labels = Field(default_factory=list, max_length=tags.PER_ITEM_MAX)
    remove: Labels = Field(default_factory=list, max_length=tags.PER_ITEM_MAX)
    ids: list[int] | None = Field(
        default=None, max_length=5000, description="The titles or artists the owner marked; given, the view is ignored."
    )
    state: str | None = Field(default=None, description="The state filter of the view (not for artists).")
    q: str | None = Field(default=None, max_length=200, description="The search text of the view.")
    tag: str | None = Field(default=None, max_length=64, description="The tag filter of the view.")


class TagsManyOut(BaseModel):
    changed: int = Field(description="Links that were added or removed.")


@router.get("/api/tags", response_model=TagsOut, summary="List the tags", description="Every tag and what carries it.")
def list_tags(db: DbSession) -> TagsOut:
    return TagsOut(items=[TagOut.model_validate(item) for item in tags.listing(db)])


@router.put(
    "/api/tags/{tag_id}",
    response_model=TagOut,
    summary="Rename a tag",
    description="A name another tag has already merges the two, as in Radarr and Sonarr.",
    responses=error_responses((404, "not_found"), (422, "invalid_input")),
)
def rename_tag(db: DbSession, tag_id: int, payload: TagRename) -> TagOut:
    row = tags.rename(db, tag_id, payload.label)
    db.commit()
    found = next(item for item in tags.listing(db) if item["id"] == row.id)
    return TagOut.model_validate(found)


@router.delete(
    "/api/tags/{tag_id}",
    status_code=204,
    response_model=None,
    summary="Delete a tag",
    description="Takes it off every title and artist. A Radarr, Sonarr or Lidarr connection brings its own back.",
    responses=error_responses((404, "not_found")),
)
def delete_tag(db: DbSession, tag_id: int) -> None:
    tags.remove(db, tag_id)
    db.commit()


@router.put(
    "/api/library/{title_id}/tags",
    response_model=TagsSet,
    summary="Set the tags of a movie or series",
    description=(
        "Exactly these tags afterwards. A tag of a connection that is left out comes back with its next import; one "
        "that is named becomes the owner's."
    ),
    responses=error_responses((404, "not_found"), (422, "invalid_input")),
)
def set_title_tags(db: DbSession, title_id: int, payload: TagsIn) -> TagsSet:
    title = db.get(Title, title_id)
    if title is None or title.kind not in ("movie", "series"):
        raise error("not_found", "This does not exist, or not any more.", 404)
    if tags.set_title(db, title.id, payload.tags):
        title.updated_at = utcnow()
    db.commit()
    return TagsSet(tags=tags.of_titles(db, [title.id]).get(title.id, []))


@router.put(
    "/api/music/artists/{artist_id}/tags",
    response_model=TagsSet,
    summary="Set the tags of an artist",
    description="Its albums show them. A tag of Lidarr that is left out comes back with its next import.",
    responses=error_responses((404, "not_found"), (422, "invalid_input")),
)
def set_artist_tags(db: DbSession, artist_id: int, payload: TagsIn) -> TagsSet:
    artist = db.get(Artist, artist_id)
    if artist is None or artist.is_various:
        raise error("not_found", "This does not exist, or not any more.", 404)
    if tags.set_artist(db, artist.id, payload.tags):
        artist.updated_at = utcnow()
    db.commit()
    return TagsSet(tags=tags.of_artists(db, [artist.id]).get(artist.id, []))


@router.post(
    "/api/tags/change",
    response_model=TagsManyOut,
    summary="Add and remove tags on many at once",
    description=(
        "The titles or artists marked, or every one of the view (`kind`, `state`, `q`, `tag` as in the library)."
    ),
    responses=error_responses((422, "invalid_input")),
)
def change_many(db: DbSession, payload: TagsManyIn) -> TagsManyOut:
    if not payload.add and not payload.remove:
        raise error("invalid_input", "The input is not valid.", 422, fields=["add"])
    if payload.kind == "artist":
        if payload.ids is not None:
            ids = list(db.scalars(select(Artist.id).where(Artist.id.in_(payload.ids), Artist.is_various.is_(False))))
        else:
            conditions = [Artist.is_various.is_(False)]
            forms = query_keys(payload.q)
            if forms:
                conditions.append(or_(*(Artist.search_keys.contains(form, autoescape=True) for form in forms)))
            label = tags.clean(payload.tag) if payload.tag else None
            if label is not None:
                conditions.append(Artist.id.in_(tags.artist_ids_with(label)))
            ids = list(db.scalars(select(Artist.id).where(*conditions)))
        changed = tags.change_many(db, artist_ids=ids, add=payload.add, remove=payload.remove)
    else:
        if payload.ids is not None:
            ids = list(db.scalars(select(Title.id).where(Title.id.in_(payload.ids), Title.kind == payload.kind)))
        else:
            conditions = library.title_conditions(payload.kind, payload.state, payload.q, payload.tag)
            ids = list(db.scalars(select(Title.id).where(*conditions)))
        changed = tags.change_many(db, title_ids=ids, add=payload.add, remove=payload.remove)
    db.commit()
    logger.info("Tags changed on %d %s: %d links", len(ids), payload.kind, changed)
    return TagsManyOut(changed=changed)
