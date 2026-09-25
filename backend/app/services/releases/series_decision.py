"""One series release for one version: does it fit, which episodes would it fill, replace or leave alone.

Decisions 21 to 29 of the design notes. Like the movie engine this knows nothing about the database: the
router hands in the rules of a profile, what it knows about the series, and the episodes the release was mapped
to (``services/series/release_match.py``).

**Fit** (``accepted``), every failing check listed, not only the first group as Sonarr shows it:

1. the form is one nexcrate takes (``refused_form {form}``: anime numbering, split episodes, partial packs,
   bonus material, multi season packs, complete series, no numbering)
2. quality allowed (``unknown_quality``, else ``quality_not_allowed``)
3. score at least the minimum (``score_below_minimum``)
4. size within the limits of that quality (``too_small``, ``too_large``)
5. required languages present (``language_missing``)
6. no hardcoded subtitles (``hardcoded_subs``)
7. with a series: at least one episode matched (``no_episode_match``)
8. a season pack whose season has not aired in full (``season_incomplete {not_aired}``)

**Size** (decision 23): MB per minute times the summed runtime of the episodes the release covers, each episode's
own runtime or else the series' usual one. Specials are not checked, as in Sonarr. Is no runtime known at all,
nexcrate checks no size and says so (``runtime_unknown``); Sonarr refuses such a release, and the movie engine's
110 minutes would be wrong for episodes of 20 to 60 minutes.

**Per episode** (decision 27), never all or nothing as in Sonarr:

* ``fills``: watched, no file;
* ``replaces``: a file that this release improves on;
* ``keeps {reason}``: a file this release does not improve on;
* ``not_watched``: the switch is off, so nothing would be stored.

``would_take`` holds when the release fits and at least one episode would be filled or replaced. ``replaces``
counts the files and their size.

**Upgrade per episode** follows E7 exactly as movies do (``decision._upgrade``), without revisions. A stored file
is read by its release name (``release_title``), else by its file name, scored with the quality and, when stored, the
languages Sonarr or nexcrate keep for it (a MULTi name read again loses the indexer's languages, measured against
Radarr on 17.09.2026), and — this matters — with the release type of its own column, never from its file name
(decision 25): a file
out of a season pack is called ``S01E03.mkv`` and must keep its +10, or the same pack would replace it for ever.
A file with an unknown release type does not get those points, so a pack of the same quality replaces it, as in
Sonarr.

**Double episodes stay whole** (decision 26): a release is no improvement for an episode whose file holds more
episodes than the release covers (``file_covers_more``). A pack that holds all of them may replace it.

⚠️ The custom formats see the whole release title, the series name included: Sonarr does the same (decision 18,
measured 16.09.2026). Group and languages are still read with the title masked, so a series called "German
Crime" carries no language of its own.

**Below the target** (``below_target``) as for movies, plus ``below_target_in_group`` (decision 28): the release
lies below the target resolution *and* shares a quality group with it. In TRaSH's German profiles 720p and 1080p
lie in one group, where only the score decides, so a 720p Blu-ray of a top tier group replaces a 1080p WEB file.
That is what the owner decided on 16.09.2026; the interface says so in its own words.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import PurePosixPath
from typing import Any

from . import decision, formats, parser, series_parser
from . import languages as lang
from . import qualities_series as qs

MIB = 1024 * 1024
#: Sonarr's release type numbers, which TRaSH's ``ReleaseTypeSpecification`` compares.
RELEASE_TYPES = {"single_episode": 1, "multi_episode": 2, "season_pack": 3}
#: The same, as Sonarr writes them on a file.
FILE_RELEASE_TYPES = {"singleEpisode": 1, "multiEpisode": 2, "seasonPack": 3}


@dataclass(frozen=True)
class CurrentEpisodeFile:
    """A stored file of a series version, as the engine judges it."""

    #: The release name the file came from; the file name is only the fallback (decision 25).
    release_title: str | None = None
    name: str | None = None
    #: Sonarr's quality name as stored.
    quality: str | None = None
    #: ``singleEpisode``, ``multiEpisode``, ``seasonPack`` or ``unknown``; never read from the file name.
    release_type: str | None = None
    size_bytes: int | None = None
    #: How many episodes the file holds.
    episode_count: int = 1
    #: The file's id, so episodes sharing one file are counted once.
    file_id: int | None = None
    #: The languages stored with the file, by name ("German"): the formats see them instead of the name's. Empty: the
    #: name's languages. A new file about to be filed carries its download's languages, the indexer's MULTi included.
    languages: tuple[str, ...] = ()
    #: The file is part 1 of a double and part 2 is missing: a season pack fills the episode (25.09.2026).
    second_part_missing: bool = False


@dataclass(frozen=True)
class EpisodeInfo:
    episode_id: int
    code: str
    runtime_min: int | None = None
    aired: bool = True
    watched: bool = True
    special: bool = False
    file: CurrentEpisodeFile | None = None


@dataclass(frozen=True)
class SeriesContext:
    #: ISO 639-1 of the series' original language.
    original_language: str | None = None
    #: The usual runtime of the series, for episodes without one of their own.
    runtime_min: int | None = None
    size_bytes: int | None = None
    #: ⚠️ Sonarr's numbers (``indexers.SONARR_FLAGS``), which the formats of a series compare; never Radarr's.
    indexer_flags: int = 0
    multi_languages: tuple[str, ...] = ()
    #: The episodes the release was mapped to; empty without a series.
    episodes: tuple[EpisodeInfo, ...] = ()
    #: True while a series of the library was chosen, so no episode means no match.
    with_series: bool = False
    #: Codes of regular episodes of the season that have not aired yet (a pack is refused then).
    not_aired: tuple[str, ...] = ()
    #: Whether the release name's title fits the chosen series; a hint only (decision 20).
    title_matches: bool = True


@dataclass(frozen=True)
class ParsedRelease:
    """A series release name read, with its languages resolved and Sonarr's quality."""

    series: series_parser.ParsedSeries
    languages: tuple[int, ...]
    original_language: int
    quality: qs.Quality

    @property
    def release_type(self) -> int:
        return RELEASE_TYPES.get(self.series.release_type or "", 0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "series_title": self.series.series_title,
            "year": self.series.year,
            "form": self.series.form,
            "release_type": self.series.release_type,
            "season": self.series.season,
            "seasons": list(self.series.seasons),
            "episodes": list(self.series.episodes),
            "air_date": self.series.air_date.isoformat() if self.series.air_date else None,
            "part": self.series.part,
            "absolute": list(self.series.absolute),
            "refused": self.series.refused,
            "group": self.series.group,
            "source": qs.SOURCES[self.quality.source],
            "resolution": self.quality.resolution,
            "quality": self.quality.name,
            "revision": {
                "version": self.series.revision.version,
                "real": self.series.revision.real,
                "repack": self.series.revision.repack,
            },
            "languages": [lang.name(language) for language in self.languages],
            "hardcoded_subs": self.series.hardcoded_subs,
        }

    def format_input(
        self, context: SeriesContext, quality: qs.Quality | None = None, release_type: int | None = None
    ) -> formats.FormatInput:
        used = quality or self.quality
        return formats.FormatInput(
            release_title=self.series.release_title,
            group=self.series.group,
            edition=None,
            source=used.source,
            resolution=used.resolution,
            modifier=0,
            languages=self.languages,
            original_language=self.original_language,
            indexer_flags=context.indexer_flags,
            size_bytes=context.size_bytes,
            year=self.series.year,
            release_type=self.release_type if release_type is None else release_type,
        )


def parse(
    name: str,
    original_language: str | None = None,
    multi_languages: tuple[str, ...] = (),
    *,
    today: date | None = None,
    titles: tuple[str, ...] = (),
    anime: bool = False,
) -> ParsedRelease:
    """The name read for a series, its languages resolved and its quality mapped onto Sonarr's table.

    ``titles`` are the known series' titles, so a title that looks like a numbering form stays a title. ``anime``: the
    series is an anime series, whose names counted through are taken (A2).
    """
    read = series_parser.parse_series(name, today=today, titles=titles, anime=anime)
    found = read.languages
    if multi_languages and parser.has_multi_token(read.release_title):
        found = parser.with_multi_languages(found, tuple(lang.radarr_id(code) for code in multi_languages))
    return ParsedRelease(
        series=read,
        languages=parser.aggregate_languages(found, original_language, unknown_for_double=False),
        original_language=lang.radarr_id(original_language),
        quality=qs.from_movie(read.quality, read.resolution_hint),
    )


# --- A stored file ---------------------------------------------------------------------------- #


def _read_file(current: CurrentEpisodeFile, original_language: str | None) -> tuple[ParsedRelease, qs.Quality]:
    """A stored file read as the upgrade check reads it: by its release name, else by its file name, with its stored
    quality and languages."""
    text = current.release_title or PurePosixPath((current.name or "").replace("\\", "/")).name
    read = parse(text, original_language)
    stored = lang.ids_of_names(current.languages, original_language)
    if stored:
        read = replace(read, languages=stored)
    quality = qs.BY_NAME.get(current.quality or "", read.quality)
    return read, quality


def _file_release_type(current: CurrentEpisodeFile) -> int:
    return FILE_RELEASE_TYPES.get(current.release_type or "", 0)


def _file_score(rules: dict[str, Any], current: CurrentEpisodeFile, read: ParsedRelease, quality: qs.Quality) -> int:
    """A stored file's score: its own size, no indexer flags, its stored quality and release type."""
    context = SeriesContext(size_bytes=current.size_bytes)
    total, _matched = formats.score(
        rules.get("formats") or [],
        read.format_input(context, quality, release_type=_file_release_type(current)),
        str(rules.get("trash_commit") or ""),
    )
    return total


def judge_episode_file(
    rules: dict[str, Any], current: CurrentEpisodeFile, original_language: str | None = None
) -> decision.FileJudgement | None:
    """What the rules make of a stored episode file, as ``judge_file`` does for a movie file.

    None for rules of another kind. The verdict drives the state "Verbesserung möglich" of a series version.
    """
    if rules.get("kind") != "series":
        return None
    order = decision.order_of(rules, qs.WEIGHTS)
    read, quality = _read_file(current, original_language)
    score = _file_score(rules, current, read, quality)
    upgrades_allowed = bool(rules.get("upgrades_allowed"))
    cutoff = order.cutoff if upgrades_allowed else order.lowest_allowed
    rank = decision.rank_of(order, quality.name)
    reason: str | None = None
    if rank < cutoff or decision.below_target_of(rules, quality):
        reason = "quality"
    elif upgrades_allowed and cutoff >= 0 and rank == cutoff and score < int(rules.get("upgrade_until") or 0):
        reason = "score"
    target: str | None = None
    items: tuple[str, ...] = ()
    if reason is not None and upgrades_allowed and order.cutoff >= 0 and rank <= order.cutoff:
        target, items = _target(rules, order)
    return decision.FileJudgement(
        reason=reason,
        score=score,
        upgrade_until=int(rules.get("upgrade_until") or 0) if upgrades_allowed else None,
        target=target,
        target_items=items,
    )


def _target(rules: dict[str, Any], order: Any) -> tuple[str, tuple[str, ...]]:
    item = (rules.get("qualities") or [])[order.cutoff]
    label = decision.item_label_of(item)
    if "group" not in item:
        return label, ()
    members = [str(name) for name in decision.item_names_of(item)]
    wanted = rules.get("target_resolution")
    reaching: list[str] = []
    if isinstance(wanted, int) and not isinstance(wanted, bool) and wanted > 0:
        reaching = [name for name in members if name in qs.BY_NAME and qs.BY_NAME[name].resolution >= wanted]
    return label, tuple(reaching or members)


def cutoff_not_met(
    rules: dict[str, Any], current: CurrentEpisodeFile, original_language: str | None = None
) -> bool:
    """Whether these rules would still upgrade a stored episode file. False for rules of another kind."""
    judged = judge_episode_file(rules, current, original_language)
    return judged is not None and judged.reason is not None


# --- Size ----------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Runtime:
    minutes: int
    known: bool
    #: True when every episode the release covers is a special; Sonarr checks no size for those.
    specials_only: bool


def _runtime(parsed: ParsedRelease, context: SeriesContext) -> _Runtime:
    """The summed runtime of the episodes a release covers (decision 23)."""
    episodes = [episode for episode in context.episodes if not episode.special]
    if context.episodes and not episodes:
        return _Runtime(0, False, True)
    if episodes:
        total = 0
        known = False
        for episode in episodes:
            minutes = episode.runtime_min or context.runtime_min or 0
            if minutes > 0:
                known = True
            total += minutes
        return _Runtime(total, known and total > 0, False)
    if context.with_series or parsed.series.form == "season_pack" or not context.runtime_min:
        return _Runtime(0, False, False)
    count = max(1, len(parsed.series.episodes))
    return _Runtime(context.runtime_min * count, True, False)


def _size_rejection(
    rules: dict[str, Any], quality: str, context: SeriesContext, runtime: _Runtime
) -> dict[str, Any] | None:
    size = context.size_bytes
    limits = (rules.get("sizes") or {}).get(quality)
    if not size or size <= 0 or not isinstance(limits, dict) or not runtime.known:
        return None
    low, high = limits.get("min_mb_per_min"), limits.get("max_mb_per_min")
    if isinstance(low, int | float) and low > 0:
        minimum = int(low * MIB) * runtime.minutes
        if size < minimum:
            return {"code": "too_small", "size_bytes": size, "minimum_bytes": minimum}
    if isinstance(high, int | float) and high > 0:
        maximum = int(high * MIB) * runtime.minutes
        if size > maximum:
            return {"code": "too_large", "size_bytes": size, "maximum_bytes": maximum}
    return None


def preferred_bytes(rules: dict[str, Any], parsed: ParsedRelease, context: SeriesContext) -> int | None:
    """What this release should weigh by the preferred size of its quality, over the episodes it covers."""
    runtime = _runtime(parsed, context)
    return decision.preferred_bytes(rules, parsed.quality.name, runtime.minutes if runtime.known else None)


#: A file of the release is there already, so the whole release is refused.
ALREADY_IMPORTED = "already_imported"


# --- Episodes ---------------------------------------------------------------------------------- #


@dataclass
class _Replaced:
    files: set[int] = field(default_factory=set)
    size_bytes: int = 0


def _episode_states(
    rules: dict[str, Any],
    order: Any,
    parsed: ParsedRelease,
    context: SeriesContext,
    score: int,
) -> tuple[list[dict[str, Any]], _Replaced]:
    covered: dict[int, int] = {}
    for episode in context.episodes:
        if episode.file is not None and episode.file.file_id is not None:
            covered[episode.file.file_id] = covered.get(episode.file.file_id, 0) + 1
    rows: list[dict[str, Any]] = []
    replaced = _Replaced()
    for episode in context.episodes:
        row: dict[str, Any] = {"episode_id": episode.episode_id, "code": episode.code}
        if not episode.watched:
            row["state"] = "not_watched"
            rows.append(row)
            continue
        current = episode.file
        if current is None:
            row["state"] = "fills"
            rows.append(row)
            continue
        read, quality = _read_file(current, context.original_language)
        current_score = _file_score(rules, current, read, quality)
        holds = covered.get(current.file_id, 1) if current.file_id is not None else 1
        if current.episode_count > holds:
            row["state"] = "keeps"
            row["upgrade"] = {
                "current_quality": quality.name,
                "current_score": current_score,
                "better": False,
                "reason": "file_covers_more",
            }
            rows.append(row)
            continue
        upgrade = decision.upgrade_of(rules, order, parsed.quality.name, score, quality.name, current_score)
        if decision.same_release(parsed.series.release_title, current.release_title or current.name):
            # The file came from this release: loading it again gains nothing (Sonarr: already imported).
            upgrade = {**upgrade, "better": False, "reason": decision.SAME_RELEASE}
        row["upgrade"] = upgrade
        if upgrade["better"]:
            row["state"] = "replaces"
            if current.file_id is None or current.file_id not in replaced.files:
                replaced.size_bytes += current.size_bytes or 0
            replaced.files.add(current.file_id if current.file_id is not None else -len(rows) - 1)
        elif current.second_part_missing and parsed.series.release_type == "season_pack":
            # Only a pack shows the halves by their numbers; filing keeps part 1 and puts part 2 beside it.
            row["state"] = "fills"
        else:
            row["state"] = "keeps"
        rows.append(row)
    return rows, replaced


# --- All together -------------------------------------------------------------------------------- #


def _below_target_in_group(rules: dict[str, Any], quality: qs.Quality) -> bool:
    """The release lies below the target and shares a quality group with it (decision 28)."""
    if not decision.below_target_of(rules, quality):
        return False
    target = rules.get("target_resolution")
    for item in rules.get("qualities") or []:
        if not isinstance(item, dict) or "group" not in item or not item.get("allowed"):
            continue
        names = [str(name) for name in decision.item_names_of(item)]
        if quality.name not in names:
            continue
        if any(qs.BY_NAME[name].resolution == target for name in names if name in qs.BY_NAME):
            return True
    return False


def evaluate(
    rules: dict[str, Any],
    name: str,
    context: SeriesContext | None = None,
    parsed: ParsedRelease | None = None,
) -> dict[str, Any]:
    """The engine result for one series release and one version's rules."""
    if rules.get("kind") != "series":
        raise ValueError(f"no series engine for the kind {rules.get('kind')!r}")
    context = context or SeriesContext()
    parsed = parsed or parse(name, context.original_language, context.multi_languages)
    commit = str(rules.get("trash_commit") or "")
    order = decision.order_of(rules, qs.WEIGHTS)
    quality = parsed.quality
    total, matched = formats.score(rules.get("formats") or [], parsed.format_input(context), commit)

    rejections: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    if parsed.series.refused is not None:
        rejections.append({"code": "refused_form", "form": parsed.series.form, "refused": parsed.series.refused})
    if quality.name == qs.UNKNOWN_QUALITY.name:
        rejections.append({"code": "unknown_quality"})
    elif quality.name not in order.allowed:
        rejections.append({"code": "quality_not_allowed", "quality": quality.name})
    minimum = int(rules.get("min_score") or 0)
    if total < minimum:
        rejections.append({"code": "score_below_minimum", "score": total, "minimum": minimum})
    runtime = _runtime(parsed, context)
    size = _size_rejection(rules, quality.name, context, runtime)
    if size is not None:
        rejections.append(size)
    if context.size_bytes and not runtime.known and not runtime.specials_only:
        notes.append({"code": "runtime_unknown"})
    missing = decision.missing_languages_of(rules, parsed.languages, parsed.original_language)
    if missing:
        rejections.append({"code": "language_missing", "languages": missing})
    if parsed.series.hardcoded_subs:
        rejections.append({"code": "hardcoded_subs", "value": parsed.series.hardcoded_subs})
    if context.with_series and parsed.series.refused is None and not context.episodes:
        rejections.append({"code": "no_episode_match"})
    if parsed.series.release_type == "season_pack" and context.not_aired:
        rejections.append({"code": "season_incomplete", "not_aired": list(context.not_aired)})
    if not context.title_matches:
        notes.append({"code": "title_mismatch"})

    episodes, replaced = _episode_states(rules, order, parsed, context, total)
    if any((row.get("upgrade") or {}).get("reason") == decision.SAME_RELEASE for row in episodes):
        # A file of this release is there already: the release gave what it holds, loading it again brings the same
        # files (Sonarr's AlreadyImportedSpecification; the owner's finding of 24.09.2026, a pack short of episodes).
        rejections.append({"code": ALREADY_IMPORTED})
    accepted = not rejections
    would_take: bool | None = None
    if context.with_series:
        would_take = accepted and any(row["state"] in ("fills", "replaces") for row in episodes)

    return {
        "accepted": accepted,
        "score": total,
        "matched": matched,
        "rejections": rejections,
        "notes": notes,
        "below_target": decision.below_target_of(rules, quality),
        "below_target_in_group": _below_target_in_group(rules, quality),
        "episodes": episodes,
        "would_take": would_take,
        "replaces": {"files": len(replaced.files), "size_bytes": replaced.size_bytes},
        "runtime_min": runtime.minutes if runtime.known else None,
    }


# --- The order of several releases -------------------------------------------------------------- #


def rank_series(
    rules: dict[str, Any],
    results: list[dict[str, Any]],
    parsed: list[ParsedRelease],
    sizes: list[int | None] | None = None,
) -> None:
    """Sonarr's order (decision 29), written into each result as ``rank``, 1 first.

    Accepted first, then quality, score, a pack before a single release, fewer episodes, the lower first episode
    number, and the larger size. Indexer priority, seeders and age have no place in the checker; the search of a
    later stage sorts by the same function with them.
    """
    order = decision.order_of(rules, qs.WEIGHTS)

    def key(index: int) -> tuple[Any, ...]:
        result = results[index]
        read = parsed[index]
        # A name counted through (anime) has its numbers in ``absolute``.
        episodes = read.series.episodes or read.series.absolute or ()
        return (
            0 if result.get("accepted") else 1,
            -decision.rank_of(order, read.quality.name),
            -int(result.get("score") or 0),
            0 if read.series.release_type == "season_pack" else 1,
            len(episodes),
            episodes[0] if episodes else 0,
            -int((sizes[index] if sizes is not None else None) or 0),
        )

    for place, index in enumerate(sorted(range(len(results)), key=key), start=1):
        results[index]["rank"] = place


# --- Comparing two files (decisions 22 and 25) -------------------------------------------- #


def file_standing(
    rules: dict[str, Any], current: CurrentEpisodeFile, original_language: str | None = None
) -> tuple[str, int, int]:
    """A file's quality name, its rank in the rules and its score, as the upgrade check reads a stored file."""
    order = decision.order_of(rules, qs.WEIGHTS)
    read, quality = _read_file(current, original_language)
    return quality.name, decision.rank_of(order, quality.name), _file_score(rules, current, read, quality)


def better_file(
    rules: dict[str, Any],
    new: CurrentEpisodeFile,
    current: CurrentEpisodeFile,
    original_language: str | None = None,
) -> bool:
    """Whether ``new`` improves on ``current`` by the rules, as a release improves on a stored file."""
    order = decision.order_of(rules, qs.WEIGHTS)
    new_quality, _new_rank, new_score = file_standing(rules, new, original_language)
    current_quality, _current_rank, current_score = file_standing(rules, current, original_language)
    return bool(decision.upgrade_of(rules, order, new_quality, new_score, current_quality, current_score)["better"])
