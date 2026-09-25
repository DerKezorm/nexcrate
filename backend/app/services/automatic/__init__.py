"""Searching and loading by itself (step 3c): the switch, the plan per title, the budget, loading.

* ``clock``: the time every part reads, replaceable in the tests.
* ``settings``: the switch ``automatic_enabled``, off until the owner switches it on.
* ``anchors``: from when a version is searched, from TMDB's release dates and the language table.
* ``planning``: which versions want something, the intervals, the next search and its reason per title.
* ``budget``: the request budget per indexer, the stop points of ``newznab:apilimits`` and the escalation.
* ``replacement``: what a failed or filed away download changes in the plan.
* ``scheduler``: the job every minute, the load decision after a search, and "search automatically now".
* ``rss``: the newest releases of every indexer every minute when due, matched to wanting titles and loaded.

Nothing here imports ``scheduler`` at package level: the search jobs and the downloads import the light modules.
"""
