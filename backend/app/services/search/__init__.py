"""Searching the indexers for one movie, matching the releases, evaluating them per version and deciding (step 2c).

Nothing is downloaded; the design notes is the contract. The core knows nothing about the database:

* ``model``: what a search knows about the title, its versions and the indexers.
* ``plan``: the queries per indexer, the pages and the release keys.
* ``runner``: asking the indexers, in parallel across indexers and paced within one, inside the time limit.
* ``matching``: whether a release belongs to the title.
* ``ranking``: Radarr's order of fitting releases, and what nexcrate would take.
* ``report``: every release against every version, and the parts of the answer.

``jobs`` is the thin layer around it: searches in memory, the worker thread, reading and writing the database.
"""
