"""Folders on disk (L4 to L6): the roots, the scan, proposals, assigning, restoring.

* ``roots``: which folders are scanned and the rows in ``disk_roots``.
* ``walk``: listing a root and looking at every candidate (videos, ``release.nex``, ``.nfo``).
* ``proposals``: numbers in names, the library and TMDB as candidates for a folder nobody knows.
* ``scan``: the scan job with its phases and the matching order of decision 17.
* ``assign``: assigning by hand, many at once, a moved folder's path, ignoring, media data of a file.
* ``restore``: restoring versions from ``release.nex``.
* ``jobs``: the in-memory jobs the three kinds of work run as.
"""

from . import assign, jobs, proposals, restore, roots, scan, walk

__all__ = ["assign", "jobs", "proposals", "restore", "roots", "scan", "walk"]
