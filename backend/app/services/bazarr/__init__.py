"""Bazarr reads nexcrate as if it were Radarr and Sonarr (Issue #8).

Bazarr only knows Radarr and Sonarr as sources of movies and series. nexcrate answers it under two prefixes of its own,
``/bazarr/radarr`` and ``/bazarr/sonarr``, which the owner enters in Bazarr as the base URL of each. Two prefixes and
not one: Bazarr's live connection (SignalR) looks the same for both, and only the address tells nexcrate whether a
connection wants movies or series. A movie event sent to a connection that serves a real Radarr would make Bazarr
change one of that Radarr's movies.

* ``settings``: the switch, off until switched on.
* ``ids``: Bazarr's numbers for nexcrate's versions, episodes and files.
* ``library``: movies, series, episodes and files in the shape Bazarr reads from Radarr and Sonarr.
* ``live``: the SignalR connections and the job that tells Bazarr what changed.
* ``rescan``: what happens when Bazarr says it wrote or deleted a subtitle.

**One entry per version.** Radarr has one file per movie, nexcrate one per version. Every version of nexcrate's own
(no source feeds it) is a movie or a series of its own in Bazarr; the version's name goes along as a tag, so Bazarr can
give each version a language profile of its own. The title stays as it is: Bazarr's providers match by title.

**Subtitles Bazarr writes** are recorded in ``extra_files`` when Bazarr says so (``rescan``). From then on they go
where the video goes: renamed with it, into the recycle folder with it on an upgrade or a removal.

⚠️ Bazarr sends the key in the address (``?apikey=``, ``?access_token=``), as it does to Radarr. The log masks both,
and the settings page asks for a key of its own with nothing but ``read``.
"""
