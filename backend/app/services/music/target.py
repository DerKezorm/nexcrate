"""The target release of an album (decision 27, E1): deluxe yes, box no.

A pure function over the releases of an album, without database or network, with its reasons as codes and values,
so the interface can say in one sentence why this release and not that one. Lidarr takes the release with the most
tracks and lands in the box (planungsgrundlage h: OK Computer's 91-track box on 6 media, The Wall's 120-track box).

1. Candidates: official releases whose media are all audio, with a track list.
2. The media brake: at most one medium more than the candidate with the fewest media.
3. The most tracks win.
4. Ties: digital before CD before vinyl before anything else, then the country order of the settings, then the
   earliest date, then the id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Formats that carry no audio for the player: a release with one of them is never a target (decision 27, step 1).
#: Lidarr's list (``NonAudioMedia``, b) plus what the bench showed (B1: Download Card, slotMusic).
NON_AUDIO = frozenset(
    {
        "DVD", "DVD-Video", "DVD-Audio", "Blu-ray", "Blu-ray-R", "HD-DVD", "VHS", "VCD", "SVCD", "Betamax",
        "LaserDisc", "UMD", "Videotape", "Video", "DualDisc", "Download Card", "slotMusic", "USB Flash Drive",
        "Data CD", "CD-ROM", "Data DVD", "Playbutton", "SACD (hybrid)",
    }
)  # fmt: skip
#: Decision 27, step 4: the format order for a tie. Anything else comes last.
FORMAT_RANKS = (
    ("Digital Media", 0),
    ("CD", 1),
    ("Vinyl", 2),
)
#: Decision 28: the country order per interface language while the definition has none of its own.
DEFAULT_COUNTRIES = {
    "de": ("DE", "XE", "XW"),
    "en": ("GB", "US", "XE", "XW"),
}


@dataclass
class Candidate:
    """What the rule needs of a release: the id is whatever the caller uses to find it again."""

    id: Any
    status: str | None
    date: str | None
    country: str | None
    formats: list[str]
    track_count: int
    tracks_loaded: bool = True
    mbid: str = ""
    #: Tracks that are no music (``is_filler``); the rule counts the rest.
    filler_count: int = 0

    @property
    def media_count(self) -> int:
        return len(self.formats)

    @property
    def music_count(self) -> int:
        return max(self.track_count - self.filler_count, 0)


@dataclass
class Choice:
    """The chosen candidate and the reasons, as codes with values; ``None`` in ``chosen`` means no candidate fit."""

    chosen: Candidate | None
    #: ``most_tracks``, ``filler_ignored`` (a release with more tracks lost, because the surplus is gaps),
    #: ``media_brake`` (with the ids and media counts left out), ``format``, ``country``,
    #: ``earliest``, ``only_candidate``, ``no_candidate`` (with why: ``no_official``, ``no_audio``, ``no_tracks``).
    reasons: list[dict[str, Any]] = field(default_factory=list)

    @property
    def codes(self) -> list[str]:
        return [str(reason["code"]) for reason in self.reasons]


def is_audio_only(formats: list[str]) -> bool:
    """Whether every medium is audio. An unknown format counts as audio: MusicBrainz adds formats, and a wrong
    "no" would hide a real release; a wrong "yes" only makes a data disc a candidate."""
    return all(_format_name(item) not in NON_AUDIO for item in formats)


def _format_name(item: str | None) -> str:
    return (item or "").strip()


def format_rank(formats: list[str]) -> int:
    """The rank of a release's formats for a tie: the worst medium counts."""
    ranks = []
    for item in formats:
        name = _format_name(item)
        rank = 3
        for prefix, value in FORMAT_RANKS:
            if name == prefix or name.endswith(prefix) or prefix in name:
                rank = value
                break
        ranks.append(rank)
    return max(ranks) if ranks else 3


def country_rank(country: str | None, countries: tuple[str, ...] | list[str]) -> int:
    """The position in the country order; a country not listed comes after every listed one."""
    if country and country in countries:
        return list(countries).index(country)
    return len(countries)


def date_key(date: str | None) -> str:
    """A date as MusicBrainz gives it, ``YYYY``, ``YYYY-MM`` or ``YYYY-MM-DD``; unknown sorts last."""
    text = (date or "").strip()
    if not text:
        return "9999-99-99"
    year, month, day = (text.split("-") + ["00", "00"])[:3]
    return f"{year:>04}-{month:>02}-{day:>02}"


def countries_for(language: str | None, own: list[str] | None) -> tuple[str, ...]:
    """The country order of a definition, or the default of the interface language (decision 28)."""
    if own:
        return tuple(own)
    return DEFAULT_COUNTRIES.get((language or "en").lower()[:2], DEFAULT_COUNTRIES["en"])


#: Counts up with every change of the rule: the loading job runs the rule again for every album, once
#: (``loading.apply_rule_change``). 2: gaps are no tracks. 3: the track counts an import had set to 0 are repaired.
RULE_VERSION = "3"

#: MusicBrainz's names for a track that is never music, whatever its length.
_NEVER_MUSIC = ("[silence]", "[data track]")
#: A track with a descriptive name in brackets ("[untitled]", "[unknown]") counts as a gap below this length; a
#: longer one is a hidden song.
FILLER_BELOW_MS = 30_000


def is_filler(name: str | None, length_ms: int | None) -> bool:
    """Whether a track is a gap and no music.

    ⚠️ Found on the owner's library (18.09.2026): a pressing of 2002 has 59 tracks, 29 of them "[untitled]" and five
    seconds long, next to pressings with 30 and 31 songs. "Most tracks" took the 59.
    """
    text = (name or "").strip().lower()
    if text in _NEVER_MUSIC:
        return True
    if not (text.startswith("[") and text.endswith("]")):
        return False
    return length_ms is not None and length_ms < FILLER_BELOW_MS


def choose(releases: list[Candidate], countries: tuple[str, ...] | list[str]) -> Choice:
    """The target release of an album by decision 27, with the reasons."""
    official = [release for release in releases if (release.status or "official") == "official"]
    if not official:
        return Choice(None, [{"code": "no_candidate", "why": "no_official"}])
    audio = [release for release in official if is_audio_only(release.formats)]
    if not audio:
        return Choice(None, [{"code": "no_candidate", "why": "no_audio"}])
    candidates = [release for release in audio if release.tracks_loaded and release.track_count > 0]
    if not candidates:
        return Choice(None, [{"code": "no_candidate", "why": "no_tracks"}])
    reasons: list[dict[str, Any]] = []
    fewest = min(release.media_count for release in candidates)
    kept = [release for release in candidates if release.media_count <= fewest + 1]
    boxes = [release for release in candidates if release.media_count > fewest + 1]
    if boxes:
        largest = max(boxes, key=lambda release: (release.media_count, release.track_count))
        reasons.append(
            {
                "code": "media_brake",
                "allowed": fewest + 1,
                "left_out": len(boxes),
                "largest_media": largest.media_count,
                "largest_tracks": largest.track_count,
                "largest_id": largest.id,
            }
        )
    if len(candidates) == 1:
        reasons.append({"code": "only_candidate"})
        return Choice(candidates[0], reasons)
    # Music, not tracks: a pressing with a gap after every song has no more to hear (``is_filler``).
    most = max(release.music_count for release in kept)
    leaders = [release for release in kept if release.music_count == most]
    reasons.append({"code": "most_tracks", "tracks": most, "media": min(r.media_count for r in leaders)})
    longest = max(kept, key=lambda release: release.track_count)
    if longest.track_count > most and longest not in leaders:
        reasons.append({"code": "filler_ignored", "tracks": longest.track_count, "filler": longest.filler_count})
    if len(leaders) > 1:
        best_format = min(format_rank(release.formats) for release in leaders)
        by_format = [release for release in leaders if format_rank(release.formats) == best_format]
        if len(by_format) < len(leaders):
            reasons.append({"code": "format", "formats": by_format[0].formats})
        leaders = by_format
    if len(leaders) > 1:
        best_country = min(country_rank(release.country, countries) for release in leaders)
        by_country = [release for release in leaders if country_rank(release.country, countries) == best_country]
        if len(by_country) < len(leaders) and best_country < len(countries):
            reasons.append({"code": "country", "country": list(countries)[best_country]})
        leaders = by_country
    if len(leaders) > 1:
        earliest = min(date_key(release.date) for release in leaders)
        by_date = [release for release in leaders if date_key(release.date) == earliest]
        if len(by_date) < len(leaders):
            reasons.append({"code": "earliest", "date": by_date[0].date})
        leaders = by_date
    leaders.sort(key=lambda release: (release.mbid, str(release.id)))
    return Choice(leaders[0], reasons)
