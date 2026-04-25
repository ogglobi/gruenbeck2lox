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
    fi
else
    echo "Not running as root, skipping user/group adjustments"
    # Apply umask anyway
    umask "$UMASK"
    exec "$@"
fi
