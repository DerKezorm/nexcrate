#!/bin/sh
# Entrypoint of the container.
#
# nexcrate should not run as root. A data directory mounted from outside carries the rights
# of the host, and those rarely match the user in the image by chance. So the container
# fixes the rights as root for a moment and then hands over to the user "nexcrate".
# PUID and PGID say which host user owns the files.

set -e

PUID=${PUID:-1000}
PGID=${PGID:-1000}
# The image sets NEXCRATE_DATA_DIR to /data; the tests point it at a folder of their own.
DATA_DIR=${NEXCRATE_DATA_DIR:-/data}

# Everything nexcrate creates directly in its data directory: the database with the side files
# of SQLite, the key, and the folders of backups, poster copies, album covers, logs, TMDB images and
# the TRaSH state (backend/app: config.py, db.py, services/images.py, services/music/covers.py,
# services/logs.py, services/tmdb.py, services/trash.py). backend/tests/test_entrypoint.py keeps it in step.
KNOWN_ENTRIES="nexcrate.db nexcrate.db-wal nexcrate.db-shm nexcrate.db-journal secret.key backups covers images logs tmdb-images trash ratings"

# Port inside the container, 8390 by default. NEXCRATE_PORT is for host networking, where
# the port in the container is the port of the host. Docker does not expand variables in
# the JSON form of CMD, so it happens here, before the exit for unprivileged starts below.
if [ "$1" = "uvicorn" ]; then
    case " $* " in
        *" --port "*) ;;
        *) set -- "$@" --port "${NEXCRATE_PORT:-8390}" ;;
    esac
fi

# Started without root rights already ("user:" in the compose file): nothing to set up.
if [ "$(id -u)" != "0" ]; then
    exec "$@"
fi

is_known() {
    for known in $KNOWN_ENTRIES; do
        if [ "$1" = "$known" ]; then
            return 0
        fi
    done
    return 1
}

# ⚠️ Owners change only in a folder that holds nothing but nexcrate's own data. A media share
# mounted at /data, as the arr apps have it, would otherwise get the owner of every file
# changed. So the top level is looked at before anything changes. "*" leaves out names that
# start with a dot; names starting with @ or # and lost+found come from NAS systems and file
# systems (Synology's @eaDir and #recycle).
foreign=""
for entry in "$DATA_DIR"/*; do
    # Without any match the pattern itself comes back.
    if [ ! -e "$entry" ] && [ ! -L "$entry" ]; then
        continue
    fi
    name=${entry##*/}
    case "$name" in
        @* | \#* | lost+found) continue ;;
    esac
    if ! is_known "$name"; then
        foreign=$name
        break
    fi
done
if [ -n "$foreign" ]; then
    echo "nexcrate: the folder mounted at $DATA_DIR holds files nexcrate did not create, such as \"$foreign\"." >&2
    echo "" >&2
    echo "  nexcrate's data needs a folder of its own: its database, key, logs and backups." >&2
    echo "  Nothing was changed, and nothing has been started." >&2
    echo "" >&2
    echo "  Mount an empty folder at $DATA_DIR, or the folder that already holds nexcrate's data," >&2
    echo "  and start the container again. Media folders belong at another path, for example /media." >&2
    exit 1
fi

if [ "$(id -g nexcrate)" != "$PGID" ]; then
    groupmod -o -g "$PGID" nexcrate
fi
if [ "$(id -u nexcrate)" != "$PUID" ]; then
    usermod -o -u "$PUID" nexcrate
fi

mkdir -p "$DATA_DIR"

# Only when the owner is wrong. A "chown -R" on every start costs time on large directories.
if [ "$(stat -c %u "$DATA_DIR")" != "$PUID" ] || [ "$(stat -c %g "$DATA_DIR")" != "$PGID" ]; then
    echo "nexcrate: adjusting ownership of the data directory to $PUID:$PGID."
    # ⚠️ The folder itself without -R; recursively only nexcrate's own entries.
    chown "$PUID:$PGID" "$DATA_DIR"
    for known in $KNOWN_ENTRIES; do
        # ⚠️ A link is left alone: chown would change whatever it points to.
        if [ -e "$DATA_DIR/$known" ] && [ ! -L "$DATA_DIR/$known" ]; then
            chown -R "$PUID:$PGID" "$DATA_DIR/$known"
        fi
    done
fi

# ⚠️ Owner does not mean writable. On a NAS the directory can belong to the right user and
# still be closed by access lists. Then the start would end in a long Python error nobody
# reads the cause from. So this really writes, as the user who will write later.
if ! gosu nexcrate sh -c 'touch "$1/.write-test"' sh "$DATA_DIR" 2>/dev/null; then
    echo "nexcrate: the data directory is not writable." >&2
    echo "" >&2
    echo "  nexcrate runs as uid $PUID, gid $PGID and cannot write to the" >&2
    echo "  directory mounted at $DATA_DIR. Nothing has been started." >&2
    echo "" >&2
    echo "  On the host, that directory needs to belong to that user:" >&2
    echo "" >&2
    echo "      sudo chown -R $PUID:$PGID /path/to/your/data" >&2
    echo "      sudo chmod -R u+rwX /path/to/your/data" >&2
    echo "" >&2
    echo "  PUID and PGID are set in your compose file. To find your own," >&2
    echo "  run 'id' on the host and use the uid and gid it reports." >&2
    exit 1
fi
rm -f "$DATA_DIR/.write-test"

exec gosu nexcrate "$@"
