"""Folders nexcrate sees: its mount points and the folders below them, for choosing a version's folder (step 3).

Nobody types a path: the interface browses with this route. A path outside the mount points, a link that leads out of
them, and nexcrate's own data directory are not visible.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from ..meldungen import error, error_responses
from ..services import folders

logger = logging.getLogger("nexcrate.folders")

router = APIRouter(prefix="/api/folders", tags=["folders"])


class Mount(BaseModel):
    path: str = Field(examples=["/media"])
    free_bytes: int
    total_bytes: int


class MountList(BaseModel):
    mounts: list[Mount] = Field(description="Empty when nexcrate sees no media folder: mount one into the container.")


class FolderEntry(BaseModel):
    name: str
    path: str


class FolderListing(BaseModel):
    path: str = Field(examples=["/media/movies"])
    parent: str | None = Field(description="Null at a mount point.")
    folders: list[FolderEntry] = Field(description="Sorted by name, without hidden folders and links.")
    free_bytes: int
    total_bytes: int


@router.get(
    "",
    response_model=MountList | FolderListing,
    summary="List visible folders",
    description=(
        "Without `path`: the mount points nexcrate sees, with free and total space. With `path`: the subfolders of "
        "that folder and the space of its file system."
    ),
    responses=error_responses((404, "folder_not_visible")),
)
def read_folders(
    path: Annotated[str | None, Query(max_length=4096, description="A visible folder.")] = None,
) -> MountList | FolderListing:
    if path is None or not path.strip():
        return MountList(mounts=[Mount.model_validate(mount) for mount in folders.mounts()])
    try:
        return FolderListing.model_validate(folders.listing(path))
    except folders.NotVisible as exc:
        raise error(
            "folder_not_visible", "nexcrate does not see this folder. Only mounted folders can be chosen.", 404
        ) from exc
