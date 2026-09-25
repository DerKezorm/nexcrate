"""Release evaluation per kind: movies and series. Music brings its own parser and engine later.

Knows nothing about the database: the router hands in the rules of a profile and what it knows about the
title and its current files. ``decision.py`` is the movie engine, ``series_decision.py`` the series one; both
share the quality order, the rank rule and the upgrade rule.
"""

from __future__ import annotations

from .decision import (
    CurrentFile,
    FileJudgement,
    Parsed,
    ReleaseContext,
    cutoff_not_met,
    evaluate,
    highest_position,
    judge_file,
    parse,
    preferred_bytes,
    quality_positions,
)
from .languages import stored_names as stored_languages
from .series_decision import (
    CurrentEpisodeFile,
    EpisodeInfo,
    ParsedRelease,
    SeriesContext,
    judge_episode_file,
    rank_series,
)
from .series_decision import cutoff_not_met as episode_cutoff_not_met
from .series_decision import evaluate as evaluate_series
from .series_decision import parse as parse_series
from .series_decision import preferred_bytes as preferred_series_bytes

KINDS = ("movie", "series")

__all__ = [
    "KINDS",
    "CurrentEpisodeFile",
    "CurrentFile",
    "EpisodeInfo",
    "FileJudgement",
    "Parsed",
    "ParsedRelease",
    "ReleaseContext",
    "SeriesContext",
    "cutoff_not_met",
    "episode_cutoff_not_met",
    "evaluate",
    "evaluate_series",
    "highest_position",
    "judge_episode_file",
    "judge_file",
    "parse",
    "parse_series",
    "preferred_bytes",
    "preferred_series_bytes",
    "quality_positions",
    "rank_series",
    "stored_languages",
]
