"""``/api/v1/about``: the page about a movie or series, for a program to show before or after requesting it.

Overview, runtime, genres, TMDB's rating, who directed or created it and the cast, in the account language, for a title
of the library and for one nexcrate does not have yet. One call to TMDB per title and language, kept seven days. The
six rules of ``v1.py`` hold; errors are flat.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, Field

from ..db import SessionLocal
from ..deps import require_scope
from ..meldungen import error, v1_error_responses
from ..services import tmdb
from ..services.api_v1 import refs, titles

router = APIRouter(prefix="/api/v1", tags=["v1"], dependencies=[Depends(require_scope("read"))])

#: How many of the cast a page shows; TMDB orders them by billing.
CAST_MAX = 20
#: The jobs that name a movie's makers; a series names its creators instead.
DIRECTING_JOBS = ("Director",)


class PersonOut(BaseModel):
    name: str
    role: str | None = Field(description="The character for the cast, the job for the crew.")
    profile_path: str | None = Field(description="TMDB's path of the person's picture.")


class AboutOut(BaseModel):
    kind: str
    ref: str = Field(description="As asked.")
    known: bool = Field(description="Whether the library has the title.")
    name: str | None
    original_name: str | None
    year: int | None
    tagline: str | None
    overview: str | None
    runtime_minutes: int | None = Field(description="A movie's runtime; for a series the usual length of an episode.")
    genres: list[str]
    rating: float | None = Field(description="TMDB's average, 0 to 10.")
    votes: int | None
    poster_path: str | None
    backdrop_path: str | None
    makers: list[PersonOut] = Field(description="Who directed a movie or created a series.")
    cast: list[PersonOut] = Field(description=f"At most {CAST_MAX}, in TMDB's order.")


def _text(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _person(item: dict[str, Any], role: Any) -> dict[str, Any] | None:
    name = _text(item.get("name"))
    if name is None:
        return None
    return {"name": name, "role": _text(role), "profile_path": _text(item.get("profile_path"))}


def _slim(kind: str, data: Any) -> dict[str, Any]:
    """Only what the page shows: the credits of a series run to thousands of entries."""
    if not isinstance(data, dict):
        return {}
    credits = data.get("aggregate_credits" if kind == "series" else "credits")
    credits = credits if isinstance(credits, dict) else {}
    cast: list[dict[str, Any]] = []
    for item in credits.get("cast") or []:
        if not isinstance(item, dict):
            continue
        if kind == "series":
            roles = item.get("roles") if isinstance(item.get("roles"), list) else []
            role = next((r.get("character") for r in roles if isinstance(r, dict) and _text(r.get("character"))), None)
        else:
            role = item.get("character")
        person = _person(item, role)
        if person is not None:
            cast.append(person)
        if len(cast) >= CAST_MAX:
            break
    if kind == "series":
        creators = [item for item in data.get("created_by") or [] if isinstance(item, dict)]
        makers = [p for p in (_person(item, "Creator") for item in creators) if p]
    else:
        makers = [
            p
            for p in (
                _person(item, item.get("job"))
                for item in credits.get("crew") or []
                if isinstance(item, dict) and item.get("job") in DIRECTING_JOBS
            )
            if p
        ]
    runtimes = data.get("episode_run_time") if kind == "series" else None
    if kind == "movie":
        runtime = data.get("runtime")
    else:
        runtime = runtimes[0] if isinstance(runtimes, list) and runtimes else None
    date = data.get("release_date" if kind == "movie" else "first_air_date")
    return {
        "name": _text(data.get("title" if kind == "movie" else "name")),
        "original_name": _text(data.get("original_title" if kind == "movie" else "original_name")),
        "year": int(date[:4]) if isinstance(date, str) and date[:4].isdigit() else None,
        "tagline": _text(data.get("tagline")),
        "overview": _text(data.get("overview")),
        "runtime_minutes": runtime if isinstance(runtime, int) and runtime > 0 else None,
        "genres": [g["name"] for g in data.get("genres") or [] if isinstance(g, dict) and _text(g.get("name"))],
        "rating": float(data["vote_average"]) if isinstance(data.get("vote_average"), (int, float)) else None,
        "votes": data["vote_count"] if isinstance(data.get("vote_count"), int) else None,
        "poster_path": _text(data.get("poster_path")),
        "backdrop_path": _text(data.get("backdrop_path")),
        "makers": makers,
        "cast": cast,
    }


def _tmdb_id(kind: str, raw: str) -> tuple[int, bool]:
    """TMDB's number of the title and whether the library has it; a title nexcrate lacks only by ``tmdb:``."""
    try:
        ref = refs.parse(kind, raw)
    except refs.RefError as problem:
        raise error(problem.code, "A reference looks like tmdb:603.", 422, kind=kind) from None
    with SessionLocal() as db:
        found = titles.lookup(db, [(kind, raw)])[0]
        known = bool(found.get("known"))
        if ref.source == "tmdb":
            return int(ref.value), known
        title = found.get("title")
        number = title and next((r for r in title.get("refs", []) if r.startswith("tmdb:")), None)
        if not number:
            raise error("ref_not_addable", "nexcrate learns about a title it does not have only by its TMDB number.",
                        422, kind=kind)  # fmt: skip
        return int(number.split(":", 1)[1]), known


@router.get(
    "/about/{kind}/{ref}",
    response_model=AboutOut,
    summary="What a movie or series is about, with its cast",
    description=(
        "Overview, tagline, runtime, genres, TMDB's rating, who directed or created it and the cast, for a title of "
        "the library and for one nexcrate does not have (then only by `tmdb:`). From TMDB with nexcrate's token, kept "
        "seven days. Since 1.1.0."
    ),
    responses=v1_error_responses(
        (422, "kind_unsupported"),
        (422, "ref_invalid"),
        (422, "ref_source_unknown"),
        (422, "ref_not_addable"),
        (404, "not_found"),
        *tmdb.ERRORS,
    ),
)
async def about(
    kind: Annotated[str, Path(max_length=16)], ref: Annotated[str, Path(max_length=64)]
) -> AboutOut:
    if kind not in ("movie", "series"):
        raise error("kind_unsupported", f"This nexcrate tells about movies and series, not a {kind}.", 422, kind=kind)
    tmdb_id, known = await asyncio.to_thread(_tmdb_id, kind, ref)
    token = await asyncio.to_thread(tmdb.require_token)
    locale = await asyncio.to_thread(tmdb.account_locale)
    path = f"/movie/{tmdb_id}" if kind == "movie" else f"/tv/{tmdb_id}"
    extra = "credits" if kind == "movie" else "aggregate_credits"
    try:
        data = await tmdb._cached(
            token,
            tmdb.cache_key("about", kind, tmdb_id, locale),
            tmdb.DETAIL_TTL,
            path,
            {"language": locale, "append_to_response": extra},
            title=True,
            slim=lambda answer: _slim(kind, answer),
        )
    except tmdb.TmdbError as exc:
        raise exc.http() from exc
    return AboutOut.model_validate({**data, "kind": kind, "ref": ref, "known": known})
