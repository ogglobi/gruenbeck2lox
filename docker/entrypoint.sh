#!/bin/sh
set -e

PUID=${PUID:-1001}
PGID=${PGID:-1001}
UMASK=${UMASK:-022}

echo "Starting gruenbeck2lox with PUID=${PUID}, PGID=${PGID}, UMASK=${UMASK}"

# Only adjust IDs if running as root
if [ "$(id -u)" = "0" ]; then
    # Adjust group ID if different
    if [ "$(id -g appgroup)" != "$PGID" ]; then
        echo "Adjusting group ID from $(id -g appgroup) to $PGID"
        if ! groupmod -o -g "$PGID" appgroup 2>/dev/null; then
            echo "Warning: Failed to change group ID, continuing with default"
        fi
    fi

    # Adjust user ID if different
    if [ "$(id -u appuser)" != "$PUID" ]; then
        echo "Adjusting user ID from $(id -u appuser) to $PUID"
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
