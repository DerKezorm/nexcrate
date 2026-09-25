"""What "studio albums and EPs" means, and the groups of an artist page (M1.3).

MusicBrainz gives a release group a primary type (``Album``, ``EP``, ``Single``, ``Broadcast``, ``Other``, or none)
and any number of secondary types (``Live``, ``Compilation``, ``Soundtrack``, ``Remix``, ``Audiobook``, ...).
Types are stored as text, never as a fixed list: a type nexcrate does not know lands under "other" instead of
falling through silently, Lidarr's gap (planungsgrundlage b). Measured on the bench (plan, B1): Radiohead has
585 groups, 28 of them studio albums or EPs; the primary type can be missing.
"""

from __future__ import annotations

#: The groups of an artist page in their order (decision 26).
GROUPS = (
    "studio",
    "ep",
    "single",
    "live",
    "compilation",
    "soundtrack",
    "remix",
    "spoken",
    "other",
)
#: The first matching secondary type decides the group (decision 26).
_SECONDARY_GROUPS = (
    ("Live", "live"),
    ("Compilation", "compilation"),
    ("Soundtrack", "soundtrack"),
    ("Remix", "remix"),
    ("DJ-mix", "remix"),
    ("Mixtape/Street", "remix"),
    ("Audiobook", "spoken"),
    ("Audio drama", "spoken"),
    ("Spokenword", "spoken"),
    ("Interview", "spoken"),
    ("Demo", "other"),
    ("Field recording", "other"),
)


#: The groups watched when nobody says otherwise: studio albums and EPs (decision 25).
DEFAULT_TYPES = ("studio", "ep")


def is_studio_or_ep(primary_type: str | None, secondary_types: list[str] | None) -> bool:
    """Decision 25 (E4): primary type ``Album`` or ``EP`` and no secondary type at all."""
    return primary_type in ("Album", "EP") and not secondary_types


def of_types(primary_type: str | None, secondary_types: list[str] | None, types: list[str] | None) -> bool:
    """Whether an album belongs to one of these groups; None are the default ones, as ``is_studio_or_ep``."""
    if types is None:
        return is_studio_or_ep(primary_type, secondary_types)
    return group_of(primary_type, secondary_types) in types


def group_of(primary_type: str | None, secondary_types: list[str] | None) -> str:
    """The group of an artist page an album belongs to, one of ``GROUPS``."""
    secondary = secondary_types or []
    for name, group in _SECONDARY_GROUPS:
        if name in secondary:
            return group
    if secondary:
        return "other"
    if primary_type == "Album":
        return "studio"
    if primary_type == "EP":
        return "ep"
    if primary_type == "Single":
        return "single"
    return "other"
