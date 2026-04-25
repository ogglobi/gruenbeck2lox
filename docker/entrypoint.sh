#!/bin/sh
set -e

PUID=${PUID:-1001}
PGID=${PGID:-1001}
UMASK=${UMASK:-022}

echo "Starting gruenbeck2lox with PUID=${PUID}, PGID=${PGID}, UMASK=${UMASK}"

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

# Only adjust IDs if running as root
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

    # Fix ownership of data directory
    echo "Setting ownership of /app/data"
    if ! chown -R appuser:appgroup /app/data 2>/dev/null; then
        echo "Warning: Failed to change ownership of /app/data"
    fi

    # Apply umask
    umask "$UMASK"
    echo "Applied umask $UMASK"

    # Drop privileges and run
    echo "Dropping privileges to appuser:appgroup"
    if command -v gosu >/dev/null 2>&1; then
        exec gosu appuser "$@"
    elif command -v su-exec >/dev/null 2>&1; then
        exec su-exec appuser "$@"
    else
        echo "Warning: No privilege drop tool found, running as root"
        exec "$@"
    #!/bin/sh
    set -e

    PUID=${PUID:-1001}
    PGID=${PGID:-1001}
    UMASK=${UMASK:-022}
    SKIP_APPDATA_CHOWN=${SKIP_APPDATA_CHOWN:-1}

    echo "Starting gruenbeck2lox with PUID=${PUID}, PGID=${PGID}, UMASK=${UMASK}, SKIP_APPDATA_CHOWN=${SKIP_APPDATA_CHOWN}"

    # try to adjust group id if appgroup exists
    if id -g appgroup >/dev/null 2>&1; then
        CUR_GID=$(id -g appgroup)
        if [ "$CUR_GID" != "$PGID" ]; then
            echo "Adjusting group ID from $CUR_GID to $PGID"
            if ! groupmod -o -g "$PGID" appgroup 2>/dev/null; then
                echo "Warning: failed to change group ID (continuing)"
            fi
        fi
    else
        if command -v groupadd >/dev/null 2>&1; then
            groupadd -g "$PGID" appgroup 2>/dev/null || true
        fi
    fi

    # try to adjust user id if appuser exists
    if id -u appuser >/dev/null 2>&1; then
        CUR_UID=$(id -u appuser)
        if [ "$CUR_UID" != "$PUID" ]; then
            echo "Adjusting user ID from $CUR_UID to $PUID"
            if ! usermod -o -u "$PUID" appuser 2>/dev/null; then
                echo "Warning: failed to change user ID (continuing)"
            fi
        fi
    else
        if command -v useradd >/dev/null 2>&1; then
            useradd -u "$PUID" -g appgroup -M -s /sbin/nologin appuser 2>/dev/null || true
        fi
    fi

    # ensure data dir exists
    if [ ! -d /app/data ]; then
        mkdir -p /app/data || true
    fi

    # Safe chown behavior:
    # - default: SKIP_APPDATA_CHOWN=1 -> skip heavy recursive chown (safer on host mounts)
    # - set SKIP_APPDATA_CHOWN=0 to evaluate and run chown if needed
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

    # Apply umask
    umask "$UMASK"
    echo "Applied umask $UMASK"

    # Drop privileges and run
    if command -v gosu >/dev/null 2>&1; then
        echo "Dropping privileges to appuser:appgroup and exec: $*"
        exec gosu appuser "$@"
    else
        echo "gosu not found, exec as current user: $*"
        exec "$@"
    fi
