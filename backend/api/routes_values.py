"""Routes exposing current device values (Loxone polling endpoint) and logs."""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from backend.db.database import Database
from .deps import get_db

logger = logging.getLogger(__name__)
router = APIRouter(tags=["values"])


@router.get("/devices/{device_id}/values")
async def get_device_values(
    device_id: int, db: Annotated[Database, Depends(get_db)]
) -> dict[str, Any]:
    """Return the latest cached values for a device.

    This endpoint can be polled directly by the Loxone Miniserver via a
    Virtual HTTP Input configured to parse the JSON response.
    """
    device = await db.fetchone("SELECT id FROM devices WHERE id = ?", (device_id,))
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    rows = await db.fetchall(
        "SELECT key, value FROM device_values WHERE device_id = ?", (device_id,)
    )
    return {r["key"]: _coerce(r["value"]) for r in rows}


@router.get("/devices/{device_id}/raw")
async def get_device_raw(
    device_id: int, db: Annotated[Database, Depends(get_db)]
) -> dict[str, Any]:
    """Return all raw cloud values cached during the last poll, for the detail view."""
    device = await db.fetchone("SELECT id FROM devices WHERE id = ?", (device_id,))
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    row = await db.fetchone(
        "SELECT raw_json, updated_at FROM device_raw_cache WHERE device_id = ?", (device_id,)
    )
    if not row:
        raise HTTPException(
            status_code=404, detail="No raw data cached yet – wait for the first poll"
        )
    return {"updated_at": row["updated_at"], "data": json.loads(row["raw_json"])}


@router.get("/logs")
async def get_logs(
    db: Annotated[Database, Depends(get_db)],
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Return the most recent push log entries."""
    rows = await db.fetchall(
        "SELECT * FROM push_log ORDER BY created_at DESC LIMIT ?", (min(limit, 1000),)
    )
    return [dict(r) for r in rows]


def _coerce(value: str | None) -> int | float | str | None:
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        pass
    try:
        return float(value)
    except (ValueError, TypeError):
        return value
