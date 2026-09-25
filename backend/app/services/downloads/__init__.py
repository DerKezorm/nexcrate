"""Downloads (step 3): loading a release, following it in its client, filing it away.

* ``loading``: which versions can load, the search answer's additions, and ``POST /api/downloads``.
* ``fetch``: the NZB or torrent from the indexer.
* ``tracking``: the background job that asks the clients.
* ``importing``: a finished download into its version's folder, in a thread of its own.
* ``files``: file operations without the database.
* ``unpacking``: a download that holds archives, unpacked next to its destination; ``unpack_worker`` is its child.
* ``store``: the answer shape, a version's state, history and the blocklist.
* ``actions``: what the owner does with a download: list, retry, confirm a mapping, remove.
"""
