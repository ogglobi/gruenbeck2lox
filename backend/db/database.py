"""SQLite database connection management via aiosqlite."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

logger = logging.getLogger(__name__)


class Database:
    """Async SQLite database manager.

    Usage::

        db = Database(path)
        await db.connect()
        async with db.connection() as conn:
            await conn.execute(...)
        await db.close()
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        """Open the database connection and configure pragmas."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        logger.info("Database connected: %s", self._path)

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None
            logger.info("Database closed")

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[aiosqlite.Connection]:
        """Yield the open connection; raises RuntimeError if not connected."""
        if self._conn is None:
            raise RuntimeError("Database is not connected – call connect() first")
        yield self._conn

    async def execute(self, sql: str, parameters: tuple = ()) -> aiosqlite.Cursor:
        """Execute a single DML/DDL statement and commit."""
        async with self.connection() as conn:
            cursor = await conn.execute(sql, parameters)
            await conn.commit()
            return cursor

    async def fetchall(self, sql: str, parameters: tuple = ()) -> list[aiosqlite.Row]:
        """Return all rows for a SELECT query."""
        async with self.connection() as conn:
            cursor = await conn.execute(sql, parameters)
            return await cursor.fetchall()

    async def fetchone(self, sql: str, parameters: tuple = ()) -> aiosqlite.Row | None:
        """Return the first row for a SELECT query, or None."""
        async with self.connection() as conn:
            cursor = await conn.execute(sql, parameters)
            return await cursor.fetchone()
