"""Radarr's order of the releases that fit one version, and what nexcrate would take.

Checked against Radarr's ``DecisionEngine/DownloadDecisionComparer.cs`` at commit a96bf7c (the commit of the step 2b
research). Radarr is GPL-3.0: these are its rules in words, no code. Radarr compares two releases step by step; the
first step that tells them apart decides, a tie goes on to the next step.

1. **Quality:** the place of the parsed quality in the profile's order, the higher place first; members of one group
   share a place. Radarr then compares the revision, unless propers and repacks are set to "do not prefer". TRaSH
   advises that setting and step 2b leaves revisions to the repack formats, so nexcrate has no revision step.
2. **Custom format score:** the higher score first.
3. **Protocol:** the protocol the version's delay rule prefers first (``services/delay.py``; in Radarr the delay
   profile of the movie). From the factory that is Usenet, as in Radarr.
4. **Indexer priority:** the lower number first (Radarr compares the number reversed). Always applied.
5. **Indexer flags, only with the setting "prefer indexer flags"** (``options.py``), off by default as in Radarr:
   the higher flag score first. Radarr's ``ScoreFlags`` (read at the develop branch on 20.09.2026) gives 2 points
   each for freeleech, double upload, internal, PTP golden and PTP approved and 1 for halfleech; every other flag,
   scene, the partial freeleeches and nuked among them, gives nothing. With the setting off every release carries
   0 and the step decides nothing. Sonarr has neither the setting nor the step, so series never get a score.
6. **Torrents, only when both are torrents:** the seeders as a rounded order of magnitude (the base 10 logarithm,
   rounded; no or unknown seeders count as 0), the higher first; then the peers in the same way.
7. **Usenet, only when both are Usenet:** the age in buckets, the newer first. Under one hour; up to 24 hours; up to
   7 whole days; beyond that the rounded base 10 logarithm of the whole days, negated. A release without a date
   comes last here (nexcrate's own rule: Radarr always has a date).
8. **Size:** the size nearest to the quality's preferred size (its MB per minute times the runtime), the distance
   rounded to steps of 200 MiB; where the quality has no preferred size or the runtime is unknown, the size itself in
   the same steps, the larger first. Radarr computes this number per release and compares the numbers, so a release
   with a preferred size (never above 0) comes after one without; both apps do. An unknown size counts as 0.

A torrent and a Usenet release skip steps 6 and 7. What is still equal keeps the order in which the releases were
found, as Radarr's sort is stable. Python's ``round`` rounds halves to even, like .NET's ``Math.Round``.

**Would take:** the first release in this order that is the version's first file, or better than its file by the
engine's upgrade check. When none qualifies and the version has a file, it keeps that file. Otherwise nothing fits,
and the rejection codes of the evaluated releases are counted (each code once per release), most common first.

**Blocklist (step 3):** a release on the title's blocklist is never taken, as in Radarr's decision engine. When it
would have been taken, the version does not keep its file either: nothing fits, and ``blocklisted`` counts like a
rejection code.
"""

from __future__ import annotations

import functools
import math
from collections import Counter
from collections.abc import Collection, Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

SIZE_STEP_BYTES = 200 * 1024 * 1024
#: The age bucket of a Usenet release without a date: after every other one.
UNKNOWN_AGE_BUCKET = -1_000_000
#: The code under which ``nothing_fits`` counts releases on the title's blocklist.
BLOCKLISTED = "blocklisted"


@dataclass(frozen=True)
class Candidate:
    """A release that fits one version, with what the order compares."""

    key: str
    #: The place in which it was found; what is left equal keeps this order.
    order: int
    #: usenet or torrent.
    protocol: str
    priority: int
    seeders: int | None
    peers: int | None
    age_hours: float | None
    size_bytes: int | None
    #: The place of its quality in the profile's order.
    quality_position: int
    score: int
    #: The engine's upgrade check against the version's file; None without a file.
    better: bool | None = None
    #: What a release of its quality and runtime should weigh; None without a preferred size or a known runtime.
    preferred_bytes: int | None = None
    #: ``flag_score`` of its indexer flags while the owner prefers them, else 0.
    flag_score: int = 0
    #: 0 for the protocol the version's delay rule prefers, 1 for the other.
    protocol_step: int = 0


#: Radarr's flag bits and what each is worth in step 5 (``IndexerFlags``, measured against Radarr 5 on 20.09.2026:
#: freeleech 1, halfleech 2, double upload 4, PTP golden 8, PTP approved 16, internal 32).
FLAG_POINTS: dict[int, int] = {1: 2, 2: 1, 4: 2, 8: 2, 16: 2, 32: 2}


def flag_score(flags: int) -> int:
    """What step 5 compares: the points of every flag the release carries, added up."""
    return sum(points for bit, points in FLAG_POINTS.items() if flags & bit)


def _higher_first(first: float, second: float) -> int:
    return (second > first) - (second < first)


def magnitude(count: int | None) -> int:
    """Seeders or peers as a rounded order of magnitude; none or unknown count as 0."""
    return round(math.log10(count)) if count is not None and count > 0 else 0


def age_bucket(age_hours: float | None) -> int:
    if age_hours is None:
        return UNKNOWN_AGE_BUCKET
    if age_hours < 1:
        return 1000
    if age_hours <= 24:
        return 100
    days = int(age_hours // 24)
    if days <= 7:
        return 10
    return -round(math.log10(days))


def size_step(size_bytes: int | None, preferred_bytes: int | None = None) -> int:
    """What step 8 compares, the higher first: the distance to the preferred size, negated, else the size itself."""
    if preferred_bytes is not None:
        return -abs(round(((size_bytes or 0) - preferred_bytes) / SIZE_STEP_BYTES))
    return round((size_bytes or 0) / SIZE_STEP_BYTES)


def _steps(first: Candidate, second: Candidate) -> Iterator[int]:
    yield _higher_first(first.quality_position, second.quality_position)
    yield _higher_first(first.score, second.score)
    yield _higher_first(-first.protocol_step, -second.protocol_step)
    yield _higher_first(-first.priority, -second.priority)
    yield _higher_first(first.flag_score, second.flag_score)
    if first.protocol == "torrent" and second.protocol == "torrent":
        yield _higher_first(magnitude(first.seeders), magnitude(second.seeders))
        yield _higher_first(magnitude(first.peers), magnitude(second.peers))
    if first.protocol == "usenet" and second.protocol == "usenet":
        yield _higher_first(age_bucket(first.age_hours), age_bucket(second.age_hours))
    yield _higher_first(
        size_step(first.size_bytes, first.preferred_bytes), size_step(second.size_bytes, second.preferred_bytes)
    )


def compare(first: Candidate, second: Candidate) -> int:
    """Negative when ``first`` comes before ``second``, positive when after."""
    for result in _steps(first, second):
        if result:
            return result
    return (first.order > second.order) - (first.order < second.order)


def rank(candidates: Iterable[Candidate]) -> list[Candidate]:
    return sorted(candidates, key=functools.cmp_to_key(compare))


@dataclass(frozen=True)
class Decision:
    would_take: str | None
    keeps_current: bool
    nothing_fits: list[dict[str, Any]]


class Takeable(Protocol):
    """What the decision reads of a fitting release: a ``Candidate``, or a release of an answer already built."""

    @property
    def key(self) -> str: ...

    @property
    def better(self) -> bool | None: ...


def decide(
    ranked: Sequence[Takeable],
    has_file: bool,
    rejected: Iterable[Iterable[str]],
    blocked: Collection[str] = frozenset(),
) -> Decision:
    """What the version would take, given its fitting releases in order and the codes of the ones that do not fit.

    ``blocked`` holds the keys of releases on the title's blocklist; ``rejected`` then carries ``blocklisted`` for each
    of them, fitting or not.
    """
    wanted = [candidate for candidate in ranked if not has_file or candidate.better]
    for candidate in wanted:
        # ⚠️ Radarr rejects a blocked release; nexcrate never recommends one either.
        if candidate.key not in blocked:
            return Decision(would_take=candidate.key, keeps_current=False, nothing_fits=[])
    if has_file and not wanted:
        return Decision(would_take=None, keeps_current=True, nothing_fits=[])
    counts: Counter[str] = Counter()
    for codes in rejected:
        counts.update(set(codes))
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return Decision(
        would_take=None, keeps_current=False, nothing_fits=[{"code": code, "count": count} for code, count in ordered]
    )


def retention_rejection(protocol: str, age_hours: float | None, retention_days: int | None) -> dict[str, Any] | None:
    """Radarr's rule (``RetentionSpecification``): a Usenet release older than the news servers keep articles does not
    fit, its download would fail. Unknown age or no limit passes (``downloads.retention``)."""
    if protocol != "usenet" or age_hours is None or retention_days is None or age_hours / 24 <= retention_days:
        return None
    return {"code": "older_than_retention", "age_days": int(age_hours // 24), "retention_days": retention_days}


def seeders_rejection(protocol: str, seeders: int | None, minimum: int | None) -> dict[str, Any] | None:
    """Radarr's rule: a torrent with known seeders below the indexer's minimum does not fit. Unknown seeders pass."""
    if protocol != "torrent" or seeders is None or minimum is None or seeders >= minimum:
        return None
    return {"code": "not_enough_seeders", "seeders": seeders, "minimum": minimum}
