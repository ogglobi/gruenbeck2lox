#!/bin/sh
set -e

PUID=${PUID:-1001}
PGID=${PGID:-1001}
UMASK=${UMASK:-022}

echo "Starting gruenbeck2lox with PUID=${PUID}, PGID=${PGID}, UMASK=${UMASK}"

# Adjust group ID
if [ "$(id -g appgroup)" != "$PGID" ]; then
    groupmod -o -g "$PGID" appgroup
fi

# Adjust user ID
if [ "$(id -u appuser)" != "$PUID" ]; then
    usermod -o -u "$PUID" appuser
fi

# Fix ownership of data directory
chown -R appuser:appgroup /app/data

# Apply umask
umask "$UMASK"

# Drop privileges and run
exec gosu appuser "$@"
