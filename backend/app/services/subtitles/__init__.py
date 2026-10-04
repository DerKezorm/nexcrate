"""Subtitle files next to the movie (C9).

* ``settings``: the switch, on until switched off.
* ``languages``: ISO 639 codes and language names in English and German.
* ``names``: reading a subtitle file's language and tags from its name, and its name next to the movie.
* ``placing``: finding the subtitles of a download, placing them next to the movie, and sending the old file's subtitles
  into the recycle folder on an upgrade. No database.
* ``records``: the table ``extra_files``: what nexcrate placed, what belongs to a version's file, the title page list.

⚠️ nexcrate only ever touches subtitle files recorded in ``extra_files``. Without the connection for Bazarr those are
the ones it placed itself, and a file placed by Radarr or the owner is never overwritten, renamed, moved or deleted.
With it switched on, every subtitle file next to a video and named after it is recorded (``bazarr.rescan``, once when
switching on and whenever Bazarr says it wrote one) and goes where the video goes.
"""
