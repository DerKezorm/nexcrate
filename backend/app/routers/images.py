"""Posters, loaded through the source and cached."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Query, Response
from fastapi.responses import FileResponse

from ..meldungen import error, error_responses
from ..services import images, tmdb

router = APIRouter(prefix="/api/images", tags=["images"])


@router.get(
    "/{title_id}/poster",
    response_class=FileResponse,
    response_model=None,
    summary="Load the poster of a title",
    description=(
        "A title that follows a source gets the poster from that source, cached under data/images; the answer "
        "may be kept for a year. A title that no source feeds gets its TMDB poster (w500) while a TMDB token "
        "is stored, kept at most 30 days in the browser. `v` is the cache key from `poster_url`. An album gets its "
        "cover from the Cover Art Archive; an album without one answers 204."
    ),
    responses={
        200: {
            "description": "The image.",
            "content": {
                "image/jpeg": {"schema": {"type": "string", "format": "binary"}},
                "image/png": {"schema": {"type": "string", "format": "binary"}},
                "image/webp": {"schema": {"type": "string", "format": "binary"}},
            },
        },
        **error_responses((404, "poster_missing")),
    },
)
async def read_poster(
    title_id: int,
    v: Annotated[str | None, Query(max_length=80, description="Cache key from poster_url.")] = None,
) -> Response:
    found = await images.poster(title_id)
    if found is None:
        if await asyncio.to_thread(images.is_album, title_id):
            # Whether an album has a cover is only known after asking the Cover Art Archive: no cover is a normal
            # answer there, and an empty one keeps the browser's console quiet. The interface shows its placeholder.
            return Response(status_code=204, headers={"Cache-Control": "private, max-age=86400"})
        raise error("poster_missing", "There is no image for this title.", 404)
    path, media_type = found
    from_tmdb = path.resolve().is_relative_to(tmdb.poster_dir().resolve())
    cache_control = images.TMDB_CACHE_CONTROL if from_tmdb else images.CACHE_CONTROL
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": cache_control})
