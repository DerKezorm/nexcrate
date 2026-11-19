"""Artists of the same name: two MusicBrainz artists, one name.

Found on the owner's instance (19.11.2026): the German rapper Luciano lay in ``Luciano/``; a program asked for the
Jamaican reggae singer Luciano, and his 29 albums were filed into the same folder. Jellyfin and Plex showed one artist
with foreign albums. The folder is the truth, one album artist to a folder:

* The artist that files first keeps the plain folder; every further artist of that name gets the folder with an
  addition in brackets, ``Luciano (Jamaican roots reggae singer)``: MusicBrainz's disambiguation, else the start of its
  id, else its number. Taken means another artist keeps the folder (``artists.folder``) or has albums with files in it.
* The album artist tag carries the same addition, so a player that groups by tags shows two artists, not one.
* Adding an artist names the artists of that name in the library: the dialog warns, ``/api/v1`` answers with the note
  ``namesake_in_library``. Nothing is refused; two artists of one name are no mistake.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ...models import Artist, Title, Version
from .. import naming_music, schreibweisen


def _key(name: str) -> str:
    return schreibweisen.nfc(name).casefold()


def additions(artist: Artist) -> list[str]:
    """What goes in brackets, in this order: the disambiguation, the start of the MusicBrainz id, the number."""
    found: list[str] = []
    for value in (
        schreibweisen.nfc(artist.disambiguation or "").strip(),
        (artist.mbid or "")[:8],
        str(artist.id) if artist.id is not None else "",
    ):
        if value and value not in found:
            found.append(value)
    return found


def folder_name(naming: naming_music.MusicNaming, artist: Artist, addition: str | None = None) -> str:
    facts = naming_music.AlbumFacts(artist_name=artist.name or "", album_title="", artist_mbid=artist.mbid)
    return naming_music.artist_folder_name(naming, facts, addition)


def taken_by_others(db: OrmSession, artist_id: int | None, folder: str) -> bool:
    """Whether another artist keeps this artist folder or has albums with files in it."""
    key = _key(folder)
    for other_id, other_folder in db.execute(select(Artist.id, Artist.folder).where(Artist.folder.is_not(None))):
        if other_id != artist_id and other_folder and _key(other_folder) == key:
            return True
    escaped = folder.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    query = (
        select(Version.relative_path)
        .join(Title, Title.id == Version.title_id)
        .where(
            Title.kind == "album",
            Version.has_file.is_(True),
            Version.relative_path.like(f"{escaped}/%", escape="\\"),
        )
    )
    if artist_id is not None:
        query = query.where((Title.artist_id != artist_id) | Title.artist_id.is_(None))
    for relative in db.scalars(query):
        first = (relative or "").replace("\\", "/").split("/")[0]
        if _key(first) == key:
            return True
    return False


def fresh_folder(db: OrmSession, naming: naming_music.MusicNaming, artist: Artist) -> str:
    """The artist folder for an artist that has none yet: the plain one, else the first free one with an addition."""
    plain = folder_name(naming, artist)
    if not taken_by_others(db, artist.id, plain):
        return plain
    for addition in additions(artist):
        candidate = folder_name(naming, artist, addition)
        if not taken_by_others(db, artist.id, candidate):
            return candidate
    return plain


def addition_in_use(db: OrmSession, naming: naming_music.MusicNaming, artist: Artist) -> str | None:
    """The addition the artist's folder carries (the stored one, else the one it would get), or None."""
    folder = artist.folder or fresh_folder(db, naming, artist)
    key = _key(folder)
    if key == _key(folder_name(naming, artist)):
        return None
    for addition in additions(artist):
        if key == _key(folder_name(naming, artist, addition)):
            return addition
    return None


def in_library(db: OrmSession, name: str, mbid: str | None) -> list[Artist]:
    """The artists of the library with this name and another MusicBrainz id, oldest first."""
    if not (name or "").strip():
        return []
    found = []
    for artist in db.scalars(select(Artist).where(Artist.is_various.is_(False)).order_by(Artist.id)):
        if artist.mbid == mbid or (mbid and mbid in (artist.mbid_old or [])):
            continue
        if schreibweisen.folds_alike(artist.name, name):
            found.append(artist)
    return found


def as_items(artists: list[Artist]) -> list[dict[str, object]]:
    return [
        {"id": artist.id, "mbid": artist.mbid, "name": artist.name, "disambiguation": artist.disambiguation or None}
        for artist in artists
    ]
