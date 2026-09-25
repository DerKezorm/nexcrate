"""Deciding about what the indexers found: matching, the engine per version, the ranks and the decisions.

Knows nothing about the database or HTTP. ``jobs`` hands in the title, its versions and the indexer states and gets
back the ``versions`` and ``releases`` parts of the ``Search`` answer ("API").

* Every release is read once, with the title's original language and its indexer's MULTi languages.
* A release that does not belong to the title keeps ``not_this_movie`` with the title and year read from its name,
  and is not evaluated.
* A release that belongs is evaluated for every version with a profile: the title's original language and runtime,
  the release size, its indexer flags, the version's file and the MULTi languages. A torrent with known seeders below
  its indexer's minimum also gets ``not_enough_seeders`` and does not fit, a Usenet release older than the news
  servers keep articles ``older_than_retention``.
* ``rank`` is the place in that version's order of fitting releases (``ranking``), null for a release that does not
  fit. The decision per version follows from that order.
* Releases that belong come first; each group keeps the order found: indexers by id, then the order of their answers.
* ``leave_out_blocked`` takes releases on the title's blocklist out of the ranks and decisions on every read (step 3):
  the blocklist changes while a search is kept.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .. import delay as delay_rules
from .. import indexers, releases
from . import matching, ranking
from .model import TitleInfo, VersionInfo
from .runner import IndexerState


def _age_hours(release: indexers.Release, moment: datetime) -> float | None:
    if release.published_at is None:
        return None
    return (moment - release.published_at).total_seconds() / 3600


def evaluate(
    title: TitleInfo,
    versions: list[VersionInfo],
    states: list[IndexerState],
    moment: datetime,
    prefer_indexer_flags: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The ``versions`` and ``releases`` of the answer. ``moment`` is now, for the age of the releases.

    ``prefer_indexer_flags`` is the owner's setting (``options.py``); off, step 5 of the order decides nothing.
    """
    positions = {
        version.version_id: releases.quality_positions(version.rules)
        for version in versions
        if version.rules is not None
    }
    tops = {version.version_id: releases.highest_position(version.rules) for version in versions if version.rules}
    fitting: dict[int, list[tuple[ranking.Candidate, dict[str, Any]]]] = {
        version.version_id: [] for version in versions
    }
    rejected: dict[int, list[list[str]]] = {version.version_id: [] for version in versions}
    belonging: list[dict[str, Any]] = []
    others: list[dict[str, Any]] = []
    order = 0
    for state in states:
        info = state.info
        for key, release in state.releases.items():
            order += 1
            parsed = releases.parse(release.title, title.original_language, info.multi_languages)
            belongs = matching.belongs(
                title,
                tmdb_id=release.tmdb_id,
                imdb_id=release.imdb_id,
                parsed_title=parsed.movie.title,
                parsed_year=parsed.movie.year,
            )
            age = _age_hours(release, moment)
            entry: dict[str, Any] = {
                "release_key": key,
                "title": release.title,
                "indexer_id": info.indexer_id,
                "indexer": info.name,
                "protocol": info.protocol,
                "size_bytes": release.size_bytes,
                "age_hours": round(age, 1) if age is not None else None,
                "seeders": release.seeders,
                "peers": release.peers,
                "grabs": release.grabs,
                "flags": indexers.flag_names(release.indexer_flags),
                "belongs": belongs,
                "not_this_movie": None
                if belongs
                else {"parsed_title": parsed.movie.title, "parsed_year": parsed.movie.year},
                "parsed": parsed.as_dict(),
                "versions": [],
            }
            if not belongs:
                others.append(entry)
                continue
            belonging.append(entry)
            too_few = ranking.seeders_rejection(info.protocol, release.seeders, info.minimum_seeders)
            too_old = ranking.retention_rejection(info.protocol, age, info.retention_days)
            for version in versions:
                if version.rules is None:
                    entry["versions"].append({"version_id": version.version_id, "result": None, "rank": None})
                    continue
                context = releases.ReleaseContext(
                    original_language=title.original_language,
                    runtime_min=title.runtime_min,
                    size_bytes=release.size_bytes,
                    indexer_flags=release.indexer_flags,
                    current_file=version.current_file,
                    multi_languages=info.multi_languages,
                )
                result = releases.evaluate(version.rules, release.title, context, parsed=parsed)
                result.pop("parsed", None)
                for refused in (too_few, too_old):
                    if refused is not None:
                        result["rejections"].append(dict(refused))
                        result["accepted"] = False
                switched_off = delay_rules.protocol_rejection(version.delay, info.protocol)
                if switched_off is not None:
                    result["rejections"].append(switched_off)
                    result["accepted"] = False
                position = positions[version.version_id].get(parsed.movie.quality.name, -1)
                placed: dict[str, Any] = {
                    "version_id": version.version_id,
                    "result": result,
                    "rank": None,
                    # For the delay rule: a release of the best allowed quality need not wait for a better one.
                    "highest_quality": 0 <= tops[version.version_id] <= position,
                }
                entry["versions"].append(placed)
                if not result["accepted"]:
                    rejected[version.version_id].append([rejection["code"] for rejection in result["rejections"]])
                    continue
                upgrade = result["upgrade"]
                candidate = ranking.Candidate(
                    key=key,
                    order=order,
                    protocol=info.protocol,
                    priority=info.priority,
                    seeders=release.seeders,
                    peers=release.peers,
                    age_hours=age,
                    size_bytes=release.size_bytes,
                    quality_position=position,
                    score=int(result["score"]),
                    better=bool(upgrade["better"]) if upgrade is not None else None,
                    preferred_bytes=releases.preferred_bytes(
                        version.rules, parsed.movie.quality.name, title.runtime_min
                    ),
                    flag_score=ranking.flag_score(release.indexer_flags) if prefer_indexer_flags else 0,
                    protocol_step=delay_rules.protocol_step(version.delay, info.protocol),
                )
                fitting[version.version_id].append((candidate, placed))

    decisions: list[dict[str, Any]] = []
    for version in versions:
        decision = ranking.Decision(would_take=None, keeps_current=False, nothing_fits=[])
        if version.rules is not None:
            placed_by_key = {candidate.key: placed for candidate, placed in fitting[version.version_id]}
            ranked = ranking.rank(candidate for candidate, _placed in fitting[version.version_id])
            for place, candidate in enumerate(ranked, start=1):
                placed_by_key[candidate.key]["rank"] = place
            decision = ranking.decide(ranked, version.current_file is not None, rejected[version.version_id])
        decisions.append(
            {
                "version_id": version.version_id,
                "label": version.label,
                "has_profile": version.has_profile,
                "would_take": decision.would_take,
                "keeps_current": decision.keeps_current,
                "nothing_fits": decision.nothing_fits,
            }
        )
    return decisions, belonging + others


@dataclass(frozen=True)
class _Fitting:
    """A fitting release of an answer already built, as ``ranking.decide`` reads it."""

    key: str
    better: bool | None


def _better(placed: dict[str, Any]) -> bool | None:
    upgrade = placed["result"]["upgrade"]
    return bool(upgrade["better"]) if upgrade is not None else None


def leave_out_blocked(versions: list[dict[str, Any]], found: list[dict[str, Any]], blocked: Collection[str]) -> None:
    """Takes the releases in ``blocked`` (keys on the title's blocklist) out of every version's order and decision.

    Changes the answer in place, so it must be a copy (``jobs.snapshot``). A blocked release keeps its evaluation and
    gets ``rank`` null; the fitting releases after it move up without gaps. A version that evaluated no blocked release
    stays as it is.
    """
    if not blocked:
        return
    for decision in versions:
        fitting: list[tuple[int, str, dict[str, Any]]] = []
        rejected: list[list[str]] = []
        has_file = touched = False
        for release in found:
            key = release["release_key"]
            is_blocked = key in blocked
            for placed in release["versions"]:
                result = placed["result"]
                if placed["version_id"] != decision["version_id"] or result is None:
                    continue
                # The engine's upgrade check exists exactly when the version has a file.
                has_file = has_file or result["upgrade"] is not None
                touched = touched or is_blocked
                fits = bool(result["accepted"]) and placed["rank"] is not None
                codes = [] if fits else [rejection["code"] for rejection in result["rejections"]]
                if is_blocked:
                    codes.append(ranking.BLOCKLISTED)
                if fits:
                    fitting.append((placed["rank"], key, placed))
                if codes:
                    rejected.append(codes)
        if not touched:
            continue
        fitting.sort(key=lambda item: item[0])
        place = 0
        for _rank, key, placed in fitting:
            if key in blocked:
                placed["rank"] = None
            else:
                place += 1
                placed["rank"] = place
        ranked = [_Fitting(key=key, better=_better(placed)) for _rank, key, placed in fitting]
        made = ranking.decide(ranked, has_file, rejected, blocked)
        decision.update(would_take=made.would_take, keeps_current=made.keeps_current, nothing_fits=made.nothing_fits)
