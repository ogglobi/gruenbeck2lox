"""CRUD routes for Grünbeck devices."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend.db.database import Database
from backend.gruenbeck import make_client
from backend.gruenbeck.cloud_api import SDCloudApi
from .deps import get_db, get_crypto

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/devices", tags=["devices"])


# ── Request / Response models ─────────────────────────────────────────────────

class DeviceIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    type: str = Field(..., pattern="^(sc|sd)$")
    # For SC devices: local IP/hostname.  For SD devices: leave empty.
    host: str = Field("", max_length=253)
    port: int = Field(80, ge=1, le=65535)
    poll_interval: int = Field(30, ge=5, le=3600)
    enabled: bool = True
    # myGruenbeck cloud credentials (SD-series only)
    cloud_email: str | None = None
    cloud_password: str | None = None   # write-only; never returned


class DeviceOut(BaseModel):
    id: int
    name: str
    type: str
    host: str
    port: int
    poll_interval: int
    enabled: bool
    has_cloud_credentials: bool
    cloud_email: str | None
    created_at: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row_to_device(row) -> DeviceOut:
    return DeviceOut(
        id=row["id"],
        name=row["name"],
        type=row["type"],
        host=row["host"] or "",
        port=row["port"],
        poll_interval=row["poll_interval"],
        enabled=bool(row["enabled"]),
        has_cloud_credentials=bool(row["cloud_password_enc"]),
        cloud_email=row["cloud_email"],
        created_at=row["created_at"],
    )


def _encrypt(fernet: Fernet, plaintext: str) -> str:
    return fernet.encrypt(plaintext.encode()).decode()


@router.get("", response_model=list[DeviceOut])
async def list_devices(db: Annotated[Database, Depends(get_db)]):
    """Return all configured Grünbeck devices."""
    rows = await db.fetchall("SELECT * FROM devices ORDER BY id")
    return [_row_to_device(r) for r in rows]


@router.post("", response_model=DeviceOut, status_code=201)
async def create_device(
    body: DeviceIn,
    db: Annotated[Database, Depends(get_db)],
    fernet: Annotated[Fernet, Depends(get_crypto)],
    request: Request,
):
    """Add a new Grünbeck device."""
    enc_pw = _encrypt(fernet, body.cloud_password) if body.cloud_password else None
    cursor = await db.execute(
        "INSERT INTO devices (name, type, host, port, poll_interval, enabled, cloud_email, cloud_password_enc) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (body.name, body.type, body.host, body.port, body.poll_interval,
         int(body.enabled), body.cloud_email, enc_pw),
    )
    new_id = cursor.lastrowid
    row = await db.fetchone("SELECT * FROM devices WHERE id = ?", (new_id,))

    # Notify scheduler about the new device
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler:
        await scheduler.add_device(dict(row))

    return _row_to_device(row)


@router.get("/{device_id}", response_model=DeviceOut)
async def get_device(device_id: int, db: Annotated[Database, Depends(get_db)]):
    """Return a single device by ID."""
    row = await db.fetchone("SELECT * FROM devices WHERE id = ?", (device_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Device not found")
    return _row_to_device(row)


@router.put("/{device_id}", response_model=DeviceOut)
async def update_device(
    device_id: int,
    body: DeviceIn,
    db: Annotated[Database, Depends(get_db)],
    fernet: Annotated[Fernet, Depends(get_crypto)],
    request: Request,
):
    """Update an existing device."""
    existing = await db.fetchone("SELECT * FROM devices WHERE id = ?", (device_id,))
    if not existing:
        raise HTTPException(status_code=404, detail="Device not found")

    # Keep existing cloud password if none provided
    if body.cloud_password:
        enc_pw = _encrypt(fernet, body.cloud_password)
    else:
        enc_pw = existing["cloud_password_enc"]

    await db.execute(
        "UPDATE devices SET name=?, type=?, host=?, port=?, poll_interval=?, enabled=?, "
        "cloud_email=?, cloud_password_enc=? WHERE id=?",
        (body.name, body.type, body.host, body.port, body.poll_interval,
         int(body.enabled), body.cloud_email, enc_pw, device_id),
    )

    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler:
        await scheduler.restart_device(device_id)

    row = await db.fetchone("SELECT * FROM devices WHERE id = ?", (device_id,))
    return _row_to_device(row)


@router.delete("/{device_id}", status_code=204)
async def delete_device(device_id: int, db: Annotated[Database, Depends(get_db)], request: Request):
    """Delete a device and all its mappings/value cache."""
    row = await db.fetchone("SELECT id FROM devices WHERE id = ?", (device_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Device not found")

    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler:
        await scheduler.remove_device(device_id)

    await db.execute("DELETE FROM devices WHERE id = ?", (device_id,))


@router.post("/{device_id}/test")
async def test_device(
    device_id: int,
    db: Annotated[Database, Depends(get_db)],
    fernet: Annotated[Fernet, Depends(get_crypto)],
) -> dict[str, Any]:
    """Test connectivity to the Grünbeck device (local or cloud)."""
    row = await db.fetchone("SELECT * FROM devices WHERE id = ?", (device_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Device not found")

    if row["cloud_password_enc"] and row["type"] == "sd":
        try:
            password = fernet.decrypt(row["cloud_password_enc"].encode()).decode()
            cloud_client = SDCloudApi(email=row["cloud_email"], password=password)
            await cloud_client.connect()
            await cloud_client.close()
            return {"reachable": True, "mode": "cloud", "email": row["cloud_email"]}
        except Exception as exc:
            return {"reachable": False, "mode": "cloud", "error": str(exc)}

    client = make_client(row["type"], row["host"], row["port"])
    reachable = await client.test_connection()
    return {"reachable": reachable, "mode": "local", "host": row["host"], "port": row["port"]}
