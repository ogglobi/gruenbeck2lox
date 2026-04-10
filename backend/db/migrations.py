"""Database schema setup (idempotent migrations)."""

from __future__ import annotations

import logging

from .database import Database

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    type            TEXT    NOT NULL CHECK(type IN ('sc', 'sd')),
    host            TEXT    NOT NULL DEFAULT '',
    port            INTEGER NOT NULL DEFAULT 80,
    poll_interval   INTEGER NOT NULL DEFAULT 30,
    enabled         INTEGER NOT NULL DEFAULT 1,
    -- myGruenbeck cloud credentials (SD-series only; stored encrypted)
    cloud_email     TEXT,
    cloud_password_enc TEXT,
    cloud_device_id TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Migration: add cloud columns if upgrading from earlier schema
CREATE INDEX IF NOT EXISTS ix_devices_enabled ON devices(enabled);

CREATE TABLE IF NOT EXISTS loxone_servers (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL,
    host              TEXT    NOT NULL,
    port              INTEGER NOT NULL DEFAULT 7777,
    user              TEXT    NOT NULL DEFAULT '',
    password_enc      TEXT    NOT NULL DEFAULT '',
    push_mode         TEXT    NOT NULL DEFAULT 'udp',
    push_on_change    INTEGER NOT NULL DEFAULT 1,
    push_interval_sec INTEGER NOT NULL DEFAULT 300,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS udp_subscriptions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id   INTEGER NOT NULL REFERENCES loxone_servers(id) ON DELETE CASCADE,
    device_id   INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    fields_json TEXT    NOT NULL DEFAULT '[]',
    UNIQUE(server_id, device_id)
);

CREATE TABLE IF NOT EXISTS mappings (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    loxone_server_id INTEGER NOT NULL REFERENCES loxone_servers(id) ON DELETE CASCADE,
    device_id        INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    gruenbeck_key    TEXT    NOT NULL,
    loxone_input     TEXT    NOT NULL,
    UNIQUE(loxone_server_id, device_id, gruenbeck_key)
);

CREATE TABLE IF NOT EXISTS device_values (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id  INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    key        TEXT    NOT NULL,
    value      TEXT,
    updated_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(device_id, key)
);

CREATE TABLE IF NOT EXISTS push_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    loxone_server_id INTEGER REFERENCES loxone_servers(id) ON DELETE SET NULL,
    device_id        INTEGER REFERENCES devices(id) ON DELETE SET NULL,
    gruenbeck_key    TEXT,
    loxone_input     TEXT,
    value            TEXT,
    status           TEXT NOT NULL,
    error_message    TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS device_raw_cache (
    device_id  INTEGER PRIMARY KEY REFERENCES devices(id) ON DELETE CASCADE,
    raw_json   TEXT    NOT NULL,
    updated_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS daily_baseline (
    device_id         INTEGER PRIMARY KEY REFERENCES devices(id) ON DELETE CASCADE,
    baseline_date     TEXT    NOT NULL,
    water_month_start REAL,
    salt_month_start  REAL
);

CREATE INDEX IF NOT EXISTS ix_push_log_created   ON push_log(created_at DESC);
CREATE INDEX IF NOT EXISTS ix_device_values_dev  ON device_values(device_id);
CREATE INDEX IF NOT EXISTS ix_mappings_server    ON mappings(loxone_server_id);
CREATE INDEX IF NOT EXISTS ix_mappings_device    ON mappings(device_id);
"""

# Idempotent ALTER TABLE migrations for columns added after initial release
_ALTER_MIGRATIONS = [
    "ALTER TABLE loxone_servers ADD COLUMN push_on_change INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE loxone_servers ADD COLUMN push_interval_sec INTEGER NOT NULL DEFAULT 300",
]


async def run_migrations(db: Database) -> None:
    """Apply all schema statements (safe to call multiple times)."""
    async with db.connection() as conn:
        await conn.executescript(_SCHEMA)
        # Run ALTER TABLE migrations; ignore errors for already-existing columns
        for stmt in _ALTER_MIGRATIONS:
            try:
                await conn.execute(stmt)
            except Exception:
                pass  # Column already exists
        await conn.commit()
    logger.info("Database migrations applied")
