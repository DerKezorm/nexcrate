"""What the music profile says about a release name (part M2.4, decisions 17 to 19).

``evaluate(parsed, rules)`` answers with the step the name says, whether the profile takes it, the reasons where it
does not, notes on the order, and a rank. A pure function: no network, no database.

* **Refused, whatever the answers** (decision 14): several albums in one release, an album as one file with a cue
  sheet, an audiobook. Then the step: unknown, never taken (mid and low lossy), or not the target while "take what is
  there first" is off.
* **The order** (decision 17): the role of the step (the target before what is only taken for now), then lossless
  before lossy where both have the same role, then 24 bit by the answer, then the source by the answer, then the
  revision (a REPACK before the first try). Lower ranks first; ``rank`` is a list so it sorts as it stands.
* **Only noted, never refused here** (decision 18): tribute, karaoke. M3 refuses them when the album asked for is none.
  Editions and media counts are not judged at all (decision 19): M3 compares them with the album's target release.
"""

from __future__ import annotations

from typing import Any

from . import music_qualities as mq
from .music_parser import ParsedAlbum

KIND = "album"
#: What rules do with a quality step: the target, taken for now (and replaced later), not the target while "take
#: what is there first" is off, or never taken.
TARGET = "target"
FOR_NOW = "for_now"
WAITS = "waits"
NEVER = "never"
ROLES = (TARGET, FOR_NOW, WAITS, NEVER)

#: Rips of an analogue carrier: crackle, rumble and hiss belong to the carrier, and they come last where the answer
#: to "source" is not "any".
ANALOGUE = ("Vinyl", "Tape", "Radio")


def usable(rules: Any) -> bool:
    """Whether these are music rules this code can read."""
    return (
        isinstance(rules, dict)
        and rules.get("kind") == KIND
        and isinstance(rules.get("steps"), dict)
        and rules.get("target_floor") in mq.STEPS
    )


def upgrade_possible(step: str, rules: Any) -> bool:
    """Whether files of this step lie below the target of the rules (decision 22). ``unknown`` says nothing, and
    neither do rules this code cannot read."""
    if not usable(rules) or step not in mq.STEPS:
        return False
    return mq.rank(step) > mq.rank(rules["target_floor"])


def parsed_dict(parsed: ParsedAlbum) -> dict[str, Any]:
    return {
        "release_title": parsed.release_title,
        "artist": parsed.artist,
        "album": parsed.album,
        "unsure": parsed.unsure,
        "various_artists": parsed.various_artists,
        "year": parsed.year,
        "edition_year": parsed.edition_year if parsed.edition_year != parsed.year else None,
        "format": parsed.format,
        "format_assumed": parsed.format_assumed,
        "bitrate": parsed.bitrate,
        "bit_depth": parsed.bit_depth,
        "sample_rate_khz": parsed.sample_rate_khz,
        "source": parsed.source,
        "media_count": parsed.media_count,
        "disc_part": parsed.disc_part,
        "editions": list(parsed.editions),
        "kinds": list(parsed.kinds),
        "several_albums": parsed.several_albums,
        "country": parsed.country,
        "catalogue_number": parsed.catalogue_number,
        "group": parsed.group,
        "suffix": parsed.suffix,
        "revision": {
            "version": parsed.revision.version,
            "real": parsed.revision.real,
            "repack": parsed.revision.repack,
        },
        "warnings": list(parsed.warnings),
        "shape": parsed.shape,
    }


def _hires_rank(step: str, answer: str) -> int:
    if answer == "prefer":
        return 0 if step == mq.LOSSLESS_24 else 1
    if answer == "avoid":
        return 1 if step == mq.LOSSLESS_24 else 0
    return 0


def _source_rank(source: str | None, answer: str) -> int:
    if answer == "avoid_vinyl":
        return 1 if source in ANALOGUE else 0
    if answer == "prefer_cd":
        return 0 if source in ("CD", "SACD") else 2 if source in ANALOGUE else 1
    return 0


def evaluate(parsed: ParsedAlbum, rules: dict[str, Any]) -> dict[str, Any]:
    """The verdict of music rules on one parsed name. Raises ``ValueError`` for rules that are none of music."""
    if not usable(rules):
        raise ValueError(f"no music rules: kind {rules.get('kind')!r}" if isinstance(rules, dict) else "no music rules")
    step = mq.step_of(parsed)
    role = rules["steps"].get(step) if step in mq.STEPS else None
    hires = str(rules.get("hires") or "any")
    source = str(rules.get("source") or "any")

    rejections: list[dict[str, Any]] = []
    if parsed.several_albums:
        rejections.append({"code": "several_albums"})
    if "cue" in parsed.warnings:
        rejections.append({"code": "cue_single_file"})
    if "audiobook" in parsed.warnings:
        rejections.append({"code": "audiobook"})
    if step == mq.UNKNOWN:
        rejections.append({"code": "unknown_quality"})
    elif role == WAITS:
        rejections.append({"code": "not_the_target", "step": step})
    elif role not in (TARGET, FOR_NOW):
        rejections.append({"code": "step_never_taken", "step": step})

    notes: list[dict[str, Any]] = []
    if role == FOR_NOW:
        # Lossless under "lossy is enough" is better than the target, not below it; it is replaced all the same.
        better = mq.rank(step) < mq.rank(str(rules["target_floor"]))
        notes.append({"code": "above_target" if better else "below_target", "step": step})
    if mq.bitrate_unknown(parsed):
        notes.append({"code": "mp3_assumed"})
    if step == mq.LOSSLESS_24 and hires in ("prefer", "avoid"):
        notes.append({"code": "hires_first" if hires == "prefer" else "hires_last"})
    if source != "any" and parsed.source in ANALOGUE:
        notes.append({"code": "analogue_last", "source": parsed.source})
    if source == "prefer_cd" and parsed.source in ("CD", "SACD"):
        notes.append({"code": "cd_first"})
    if parsed.revision.version > 1:
        notes.append({"code": "repeat_first"})
    for warning in ("tribute", "karaoke"):
        if warning in parsed.warnings:
            notes.append({"code": warning})

    lossless = step in mq.LOSSLESS_STEPS
    rank = [
        0 if role == TARGET else 1,
        0 if lossless else 1,
        _hires_rank(step, hires) if lossless else 0,
        _source_rank(parsed.source, source),
        -parsed.revision.version,
        -parsed.revision.real,
    ]
    return {
        "step": step,
        "accepted": not rejections,
        "for_now": not rejections and role == FOR_NOW,
        "rejections": rejections,
        "notes": notes,
        "rank": rank,
    }


def order(results: list[dict[str, Any]]) -> list[int | None]:
    """The place of every accepted result among the accepted ones, 1 for the best; None for a refused one. Equal ranks
    share a place."""
    ranks = sorted({tuple(result["rank"]) for result in results if result["accepted"]})
    return [ranks.index(tuple(result["rank"])) + 1 if result["accepted"] else None for result in results]
