#!/bin/sh
set -e

PUID=${PUID:-1001}
PGID=${PGID:-1001}
UMASK=${UMASK:-022}
SKIP_APPDATA_CHOWN=${SKIP_APPDATA_CHOWN:-1}

echo "Starting gruenbeck2lox with PUID=${PUID}, PGID=${PGID}, UMASK=${UMASK}, SKIP_APPDATA_CHOWN=${SKIP_APPDATA_CHOWN}"

# Ensure group exists
if ! getent group appgroup >/dev/null 2>&1; then
    echo "Creating group appgroup with GID $PGID"
    groupadd -g "$PGID" appgroup 2>/dev/null || true
fi

# Ensure user exists
if ! getent passwd appuser >/dev/null 2>&1; then
    echo "Creating user appuser with UID $PUID"
    useradd -u "$PUID" -g appgroup -s /bin/sh -d /app appuser 2>/dev/null || true
fi

# Only perform ID adjustments and chown when running as root
if [ "$(id -u)" = "0" ]; then
    # Adjust group ID if different
    if [ "$(getent group appgroup | cut -d: -f3)" != "$PGID" ]; then
        echo "Adjusting group ID to $PGID"
        if ! groupmod -o -g "$PGID" appgroup 2>/dev/null; then
            echo "Warning: Failed to change group ID, continuing with default"
        fi
    fi

    # Adjust user ID if different
    if [ "$(getent passwd appuser | cut -d: -f3)" != "$PUID" ]; then
        echo "Adjusting user ID to $PUID"
        if ! usermod -o -u "$PUID" appuser 2>/dev/null; then
            echo "Warning: Failed to change user ID, continuing with default"
        fi
    fi

    # ensure data dir exists
    if [ ! -d /app/data ]; then
        mkdir -p /app/data || true
    fi

    # Safe chown behavior:
    echo "SKIP_APPDATA_CHOWN=${SKIP_APPDATA_CHOWN}"
    if [ "${SKIP_APPDATA_CHOWN}" = "1" ]; then
        echo "Skipping recursive chown of /app/data (SKIP_APPDATA_CHOWN=1)"
    else
        echo "Evaluating ownership of /app/data"
        if grep -q ' /app/data ' /proc/mounts 2>/dev/null; then
            echo "/app/data appears to be a host mount; will only chown if non-owned files are found (shallow scan)"
            FIND_MAXDEPTH=2
        else
            FIND_MAXDEPTH=5
        fi

        NEED_CHOWN=$(find /app/data -mindepth 1 -maxdepth "$FIND_MAXDEPTH" \( -not -user appuser -o -not -group appgroup \) -print -quit 2>/dev/null || true)

        if [ -n "$NEED_CHOWN" ]; then
            echo "Changing ownership of files in /app/data to appuser:appgroup (this can take time)"
            if ! chown -R appuser:appgroup /app/data 2>/dev/null; then
                echo "Warning: chown failed; continuing"
            fi
        else
            echo "Ownership looks correct; skipping chown."
        fi
    fi
fi

# Apply umask
umask "$UMASK"
echo "Applied umask $UMASK"

# Drop privileges and run
if [ "$(id -u)" = "0" ]; then
    if command -v gosu >/dev/null 2>&1; then
        echo "Dropping privileges to appuser:appgroup and exec: $*"
        exec gosu appuser "$@"
    elif command -v su-exec >/dev/null 2>&1; then
        echo "Dropping privileges to appuser:appgroup and exec with su-exec: $*"
        exec su-exec appuser "$@"
    else
        echo "Warning: No privilege drop tool found, running as root"
        exec "$@"
    fi
else
    exec "$@"
fi
