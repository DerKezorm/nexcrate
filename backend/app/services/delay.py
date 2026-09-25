"""The delay rule of a version: which protocol wins a tie, which one is off, and how long the automatic waits for a
better release before it takes one.

The rules are Radarr's, Sonarr's and Lidarr's, read in their source on 21.09.2026 (branch develop, in words, no
code): ``DelaySpecification``, ``ProtocolSpecification``, ``DownloadDecisionComparer.CompareProtocol`` and
``PendingReleaseService``. There a rule hangs on tags; here it hangs on the version, as the profile and the folder do.

**Whether a release waits,** in the apps' order; the first line that applies decides:

1. A search the owner started himself never waits (``UserInvokedSearch``). Here: every load the owner clicks, and
   "search automatically now".
2. The delay of the release's protocol is 0: it loads.
3. *The apps let a better revision of the file on disk through. nexcrate has no revision step (repacks are custom
   formats), so there is no such line here.*
4. ``bypass_highest_quality``: its quality is the best the profile allows **and** it has the preferred protocol.
5. ``bypass_score``: its custom format score reaches ``minimum_score`` **and** it has the preferred protocol.
6. A release of the same title and version (for a series: one that shares an episode) is already waiting, and the
   oldest of them is older than the delay: it loads. So once the first release has waited, the best one known wins.
7. It is younger than the delay: it waits. Else it loads.

⚠️ The clock starts when the release was published at the indexer, never when nexcrate first saw it. A release
without a date counts from the moment it was first seen (nexcrate's own rule; the apps always have a date).

A protocol that is switched off is no waiting: the release does not fit (``protocol_disabled``), as in the apps.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

PROTOCOLS = ("usenet", "torrent")
#: 30 days. The apps know no upper limit; a longer wait is a release nobody is waiting for.
MAX_MINUTES = 43_200
SCORE_LIMIT = 1_000_000


class RuleInvalid(ValueError):
    """The fields of a rule that cannot be stored."""

    def __init__(self, fields: list[str]) -> None:
        super().__init__(", ".join(fields))
        self.fields = fields


@dataclass(frozen=True)
class Rule:
    """The defaults are the apps' (measured on Radarr 6.3.0, Sonarr 4.0.19 and Lidarr 3.1.0, ``GET delayprofile``)."""

    enable_usenet: bool = True
    enable_torrent: bool = True
    preferred_protocol: str = "usenet"
    usenet_minutes: int = 0
    torrent_minutes: int = 0
    bypass_highest_quality: bool = True
    bypass_score: bool = False
    minimum_score: int = 0

    def enabled(self, protocol: str) -> bool:
        return self.enable_torrent if protocol == "torrent" else self.enable_usenet

    def minutes(self, protocol: str) -> int:
        return self.torrent_minutes if protocol == "torrent" else self.usenet_minutes

    def prefers(self, protocol: str) -> bool:
        return protocol == self.preferred_protocol

    @property
    def waits_at_all(self) -> bool:
        return bool(self.usenet_minutes or self.torrent_minutes)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT = Rule()


def _whole(value: Any, low: int, high: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        return None
    return value


def validate(given: dict[str, Any]) -> Rule:
    """A rule from what the API or an import hands in. Raises ``RuleInvalid`` with every field that does not work.

    Both protocols off is refused, as the apps refuse it: such a version could never load anything.
    """
    bad: list[str] = []
    values: dict[str, Any] = {}
    for name in ("enable_usenet", "enable_torrent", "bypass_highest_quality", "bypass_score"):
        value = given.get(name, getattr(DEFAULT, name))
        if not isinstance(value, bool):
            bad.append(name)
        values[name] = value
    protocol = given.get("preferred_protocol", DEFAULT.preferred_protocol)
    if protocol not in PROTOCOLS:
        bad.append("preferred_protocol")
    values["preferred_protocol"] = protocol
    for name in ("usenet_minutes", "torrent_minutes"):
        number = _whole(given.get(name, 0), 0, MAX_MINUTES)
        if number is None:
            bad.append(name)
        values[name] = number
    score = _whole(given.get("minimum_score", 0), -SCORE_LIMIT, SCORE_LIMIT)
    if score is None:
        bad.append("minimum_score")
    values["minimum_score"] = score
    if not bad and not values["enable_usenet"] and not values["enable_torrent"]:
        bad.extend(["enable_usenet", "enable_torrent"])
    if bad:
        raise RuleInvalid(bad)
    return Rule(**values)


def from_stored(value: Any) -> Rule:
    """The rule of a version as stored; nothing stored, or something unreadable, is the default. Never raises."""
    if not isinstance(value, dict):
        return DEFAULT
    try:
        return validate(value)
    except RuleInvalid:
        return DEFAULT


def to_stored(rule: Rule, tagged: Sequence[TaggedRule] = ()) -> dict[str, Any] | None:
    """Null for the default without tagged rules, so a version nobody touched follows a later change of the
    defaults. The tagged rules go under ``tagged`` in their order."""
    if rule == DEFAULT and not tagged:
        return None
    stored = rule.as_dict()
    if tagged:
        stored["tagged"] = [{"tag_ids": list(item.tag_ids), **item.rule.as_dict()} for item in tagged]
    return stored


# --- Rules for titles with tags ------------------------------------------------------------- #

#: How many tagged rules one version may have.
TAGGED_MAX = 50


@dataclass(frozen=True)
class TaggedRule:
    """A rule that holds for titles carrying one of these tags, before the version's own. Tags by their id: a
    renamed tag keeps its rules, a deleted one drops out."""

    tag_ids: tuple[int, ...]
    rule: Rule


def tagged_of(value: Any) -> list[TaggedRule]:
    """The tagged rules as stored, in their order; an unreadable entry is left out. Never raises."""
    if not isinstance(value, dict) or not isinstance(value.get("tagged"), list):
        return []
    found: list[TaggedRule] = []
    for entry in value["tagged"]:
        if not isinstance(entry, dict):
            continue
        ids = entry.get("tag_ids")
        whole = isinstance(ids, list) and all(isinstance(item, int) and not isinstance(item, bool) for item in ids)
        if not whole or not ids:
            continue
        try:
            found.append(TaggedRule(tuple(ids), validate(entry)))
        except RuleInvalid:
            continue
    return found


def for_title(value: Any, title_tag_ids: set[int]) -> Rule:
    """The rule for a title, as the apps choose a delay profile: the first tagged rule sharing a tag wins, else the
    version's own (which stands last, like the apps' profile without tags)."""
    if title_tag_ids:
        for item in tagged_of(value):
            if title_tag_ids.intersection(item.tag_ids):
                return item.rule
    return from_stored(value)


# --- Reading the delay profiles of Radarr, Sonarr or Lidarr --------------------------------------------------------- #


@dataclass(frozen=True)
class FromArr:
    #: The profile without tags, as a rule; None when the app lists none.
    rule: Rule | None
    #: How many profiles were bound to tags there; since T2 they come over as ``tagged_rules``.
    tagged: int
    #: Minutes above ``MAX_MINUTES`` were cut down to it.
    shortened: bool = False
    #: The profiles with tags, in the app's order: the app's tag ids and the rule.
    tagged_rules: tuple[tuple[tuple[int, ...], Rule], ...] = ()


def from_arr(listed: Any) -> FromArr:
    """The rule a version can take from an app's ``delayprofile`` list. There a profile hangs on tags and the one
    without tags holds for everything else; here a version binds, so only that one can come over. What the app sends
    is never trusted: a field of another shape counts as its default."""
    profiles = [entry for entry in listed if isinstance(entry, dict)] if isinstance(listed, list) else []
    untagged = [entry for entry in profiles if not entry.get("tags")]
    tagged = len(profiles) - len(untagged)
    shortened = False
    tagged_rules: list[tuple[tuple[int, ...], Rule]] = []
    # Since T2 the profiles with tags come over too, in the app's order (the lowest ``order`` wins there).
    for entry in sorted((item for item in profiles if item.get("tags")), key=_order):
        ids = tuple(item for item in entry.get("tags") if isinstance(item, int) and not isinstance(item, bool))
        rule, cut = _rule_of_arr(entry)
        shortened = shortened or cut
        if ids and rule is not None:
            tagged_rules.append((ids, rule))
    if not untagged:
        return FromArr(rule=None, tagged=tagged, shortened=shortened, tagged_rules=tuple(tagged_rules))
    rule, cut = _rule_of_arr(untagged[0])
    return FromArr(rule=rule, tagged=tagged, shortened=shortened or cut, tagged_rules=tuple(tagged_rules))


def _order(entry: dict[str, Any]) -> int:
    value = entry.get("order")
    return value if isinstance(value, int) and not isinstance(value, bool) else 2**31


def _rule_of_arr(raw: dict[str, Any]) -> tuple[Rule | None, bool]:
    """One profile of an app as a rule, and whether minutes were cut down; None when it cannot be a rule."""
    shortened = False

    def flag(name: str, default: bool) -> bool:
        value = raw.get(name)
        return value if isinstance(value, bool) else default

    def minutes(name: str) -> int:
        nonlocal shortened
        value = raw.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return 0
        if value > MAX_MINUTES:
            shortened = True
            return MAX_MINUTES
        return value

    protocol = str(raw.get("preferredProtocol") or "").lower()
    score = raw.get("minimumCustomFormatScore")
    values = {
        "enable_usenet": flag("enableUsenet", True),
        "enable_torrent": flag("enableTorrent", True),
        "preferred_protocol": protocol if protocol in PROTOCOLS else DEFAULT.preferred_protocol,
        "usenet_minutes": minutes("usenetDelay"),
        "torrent_minutes": minutes("torrentDelay"),
        "bypass_highest_quality": flag("bypassIfHighestQuality", DEFAULT.bypass_highest_quality),
        "bypass_score": flag("bypassIfAboveCustomFormatScore", False),
        "minimum_score": _whole(score, -SCORE_LIMIT, SCORE_LIMIT) or 0,
    }
    try:
        return validate(values), shortened
    except RuleInvalid:
        # Both protocols off cannot be a rule here; the app itself refuses it too.
        return None, shortened


# --- Waiting -------------------------------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Facts:
    """What the decision reads of one release for one version."""

    protocol: str
    published_at: datetime | None
    #: When nexcrate first saw it; the clock of a release without a date.
    first_seen_at: datetime
    #: Its quality is the best one the version's profile allows.
    highest_quality: bool
    score: int
    #: The clock start of the oldest release already waiting for the same title and version (for a series: sharing an
    #: episode); None when none waits.
    oldest_waiting_since: datetime | None = None


def clock_start(published_at: datetime | None, first_seen_at: datetime) -> datetime:
    return published_at if published_at is not None else first_seen_at


def waits_until(rule: Rule, facts: Facts, now: datetime) -> datetime | None:
    """None when the automatic may load the release now, else the moment from which it may."""
    minutes = rule.minutes(facts.protocol)
    if minutes <= 0:
        return None
    preferred = rule.prefers(facts.protocol)
    if rule.bypass_highest_quality and facts.highest_quality and preferred:
        return None
    if rule.bypass_score and facts.score >= rule.minimum_score and preferred:
        return None
    wait = timedelta(minutes=minutes)
    if facts.oldest_waiting_since is not None and now - facts.oldest_waiting_since > wait:
        return None
    due = clock_start(facts.published_at, facts.first_seen_at) + wait
    return due if now < due else None


def protocol_rejection(rule: Rule, protocol: str) -> dict[str, Any] | None:
    """The rejection of a release whose protocol the version has switched off."""
    if rule.enabled(protocol):
        return None
    return {"code": "protocol_disabled", "protocol": protocol}


def protocol_step(rule: Rule | None, protocol: str) -> int:
    """Step 3 of the order: 0 for the preferred protocol, 1 for the other; the lower comes first."""
    return 0 if rule is None or rule.prefers(protocol) else 1
