"""Discover: twenty titles per list that the library lacks, for owners without nexview in front.

Movies and series come from TMDB's ``/discover`` (``tmdb_lists``), albums from ListenBrainz (``listenbrainz``). What
the library holds and what the owner marked "not interested" (``hidden``) is left out; a list fetches further pages
until twenty remain, at most ``tmdb_lists.MAX_PAGES``.
"""
