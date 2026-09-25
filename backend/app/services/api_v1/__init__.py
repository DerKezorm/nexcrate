"""What stands behind ``/api/v1``, the contract for other programs.

The shapes every address uses live here, apart from the routes of the interface: a change to a page of the interface
must never change what another program reads.
"""

#: The major number of the contract, as in the address. It changes only with a break.
CONTRACT = 1
#: What of the contract this nexcrate carries: V1 is reading, V2 requesting and taking back, V3
#: the way back: queue, problems, reasons, history, events, V4 the round things: calendar,
#: ratings, previews, pairing, webhooks, V5 music.
STAGE = "V5"
#: The kinds this nexcrate answers for.
KINDS = ("movie", "series", "album", "artist")
#: The kinds that are rows of ``titles``. An artist is a row of ``artists``; where a list of titles carries it, it
#: stands under the negative of its row number (``titles.items``).
TITLE_KINDS = ("movie", "series", "album")
#: The kinds of music: ``capabilities.music`` and the ratings, which music has none of.
MUSIC_KINDS = ("album", "artist")
