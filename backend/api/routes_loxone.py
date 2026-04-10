"""CRUD routes for Loxone Miniserver configuration and UDP subscriptions."""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any
from xml.sax.saxutils import escape as xml_escape

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response as FastAPIResponse
from pydantic import BaseModel, Field

from backend.db.database import Database
from backend.loxone.push import push_udp_packet
from .deps import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/loxone", tags=["loxone"])


# ── Field metadata for XML template ──────────────────────────────────────────

# (label for Title, Loxone unit string — <v> is replaced by the value in Loxone Config)
_FIELD_META: dict[str, tuple[str, str]] = {
    "residualCapacity":    ("Restkapazität (l)",       "<v> Liter"),
    "residualCapacityM3":  ("Restkapazität (m³)",      "<v.3> m³"),
    "residualCapacityPct": ("Restkapazität",            "<v> %"),
    "totalCapacity":       ("Gesamtkapazität (l)",     "<v> Liter"),
    "waterToday":          ("Wasser heute (l)",        "<v> Liter"),
    "waterMonth":          ("Wasser Monat (l)",        "<v> Liter"),
    "waterYear":           ("Wasser Jahr (l)",         "<v> Liter"),
    "saltToday":           ("Salz heute (g)",          "<v.3> g"),
    "saltMonth":           ("Salz Monat (kg)",          "<v.3> kg"),
    "saltYear":            ("Salz Jahr (kg)",          "<v.3> kg"),
    "saltRange":           ("Salzreichweite (Tage)",    "<v> Tage"),
    "water_hardness_in":   ("Eingangshärte (°dH)",     "<v> °dH"),
    "water_hardness_out":  ("Ausgangshärte (°dH)",     "<v> °dH"),
    "next_regeneration":   ("Nächste Regeneration",    "<v.u>"),
    "last_regeneration":   ("Letzte Regeneration",     "<v.u>"),
    "maintenanceDays":     ("Tage bis Wartung (Tage)", "<v> Tage"),
    "hasError":            ("Fehler aktiv (0/1)",      "<v> "),
    "currentFlow":         ("Durchfluss (l/Min)",       "<v.1> l/Min"),
    "error_code":          ("Fehlercode",              "<v.1>"),
}


# ── Request / Response models ─────────────────────────────────────────────────

class LoxoneServerIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    host: str = Field(..., min_length=1, max_length=253)
    port: int = Field(7777, ge=1, le=65535)
    push_on_change: bool = True
    push_interval_sec: int = Field(300, ge=10, le=86400)


class LoxoneServerOut(BaseModel):
    id: int
    name: str
    host: str
    port: int
    push_on_change: bool
    push_interval_sec: int
    created_at: str


class SubscriptionIn(BaseModel):
    device_id: int
    fields: list[str] = Field(default_factory=list)


class SubscriptionOut(BaseModel):
    id: int
    server_id: int
    device_id: int
    fields: list[str]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row_to_server(row) -> LoxoneServerOut:
    r = dict(row)
    return LoxoneServerOut(
        id=r["id"],
        name=r["name"],
        host=r["host"],
        port=r["port"],
        push_on_change=bool(r.get("push_on_change", 1)),
        push_interval_sec=r.get("push_interval_sec", 300),
        created_at=r["created_at"],
    )


def _row_to_subscription(row) -> SubscriptionOut:
    return SubscriptionOut(
        id=row["id"],
        server_id=row["server_id"],
        device_id=row["device_id"],
        fields=json.loads(row["fields_json"]),
    )


# ── Server Routes ──────────────────────────────────────────────────────────────

@router.get("", response_model=list[LoxoneServerOut])
async def list_servers(db: Annotated[Database, Depends(get_db)]):
    rows = await db.fetchall("SELECT * FROM loxone_servers ORDER BY id")
    return [_row_to_server(r) for r in rows]


@router.post("", response_model=LoxoneServerOut, status_code=201)
async def create_server(body: LoxoneServerIn, db: Annotated[Database, Depends(get_db)]):
    cursor = await db.execute(
        "INSERT INTO loxone_servers "
        "(name, host, port, user, password_enc, push_mode, push_on_change, push_interval_sec) "
        "VALUES (?, ?, ?, '', '', 'udp', ?, ?)",
        (body.name, body.host, body.port, 1 if body.push_on_change else 0, body.push_interval_sec),
    )
    row = await db.fetchone("SELECT * FROM loxone_servers WHERE id = ?", (cursor.lastrowid,))
    return _row_to_server(row)


@router.get("/{server_id}", response_model=LoxoneServerOut)
async def get_server(server_id: int, db: Annotated[Database, Depends(get_db)]):
    row = await db.fetchone("SELECT * FROM loxone_servers WHERE id = ?", (server_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Loxone server not found")
    return _row_to_server(row)


@router.put("/{server_id}", response_model=LoxoneServerOut)
async def update_server(
    server_id: int, body: LoxoneServerIn, db: Annotated[Database, Depends(get_db)]
):
    row = await db.fetchone("SELECT id FROM loxone_servers WHERE id = ?", (server_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Loxone server not found")
    await db.execute(
        "UPDATE loxone_servers SET name=?, host=?, port=?, push_on_change=?, push_interval_sec=? "
        "WHERE id=?",
        (body.name, body.host, body.port, 1 if body.push_on_change else 0, body.push_interval_sec, server_id),
    )
    row = await db.fetchone("SELECT * FROM loxone_servers WHERE id = ?", (server_id,))
    return _row_to_server(row)


@router.delete("/{server_id}", status_code=204)
async def delete_server(server_id: int, db: Annotated[Database, Depends(get_db)]):
    row = await db.fetchone("SELECT id FROM loxone_servers WHERE id = ?", (server_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Loxone server not found")
    await db.execute("DELETE FROM loxone_servers WHERE id = ?", (server_id,))


@router.post("/{server_id}/test")
async def test_server(
    server_id: int, db: Annotated[Database, Depends(get_db)]
) -> dict[str, Any]:
    """Send a test UDP ping packet to the Miniserver."""
    row = await db.fetchone("SELECT * FROM loxone_servers WHERE id = ?", (server_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Loxone server not found")
    result = await push_udp_packet(
        host=row["host"], port=row["port"], message="gruenbeck2lox_ping=1\n"
    )
    return {"success": result.success, "error": result.error}


# ── Subscription Routes ───────────────────────────────────────────────────────

@router.get("/{server_id}/subscriptions", response_model=list[SubscriptionOut])
async def list_subscriptions(server_id: int, db: Annotated[Database, Depends(get_db)]):
    rows = await db.fetchall(
        "SELECT * FROM udp_subscriptions WHERE server_id = ? ORDER BY id", (server_id,)
    )
    return [_row_to_subscription(r) for r in rows]


@router.post("/{server_id}/subscriptions", response_model=SubscriptionOut, status_code=201)
async def create_subscription(
    server_id: int, body: SubscriptionIn, db: Annotated[Database, Depends(get_db)]
):
    srv = await db.fetchone("SELECT id FROM loxone_servers WHERE id = ?", (server_id,))
    if not srv:
        raise HTTPException(status_code=404, detail="Loxone server not found")
    dev = await db.fetchone("SELECT id FROM devices WHERE id = ?", (body.device_id,))
    if not dev:
        raise HTTPException(status_code=404, detail="Device not found")
    try:
        cursor = await db.execute(
            "INSERT INTO udp_subscriptions (server_id, device_id, fields_json) VALUES (?, ?, ?)",
            (server_id, body.device_id, json.dumps(body.fields)),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=409, detail="Subscription for this device already exists"
        ) from exc
    row = await db.fetchone("SELECT * FROM udp_subscriptions WHERE id = ?", (cursor.lastrowid,))
    return _row_to_subscription(row)


@router.put("/{server_id}/subscriptions/{sub_id}", response_model=SubscriptionOut)
async def update_subscription(
    server_id: int, sub_id: int, body: SubscriptionIn, db: Annotated[Database, Depends(get_db)]
):
    row = await db.fetchone(
        "SELECT id FROM udp_subscriptions WHERE id = ? AND server_id = ?", (sub_id, server_id)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Subscription not found")
    await db.execute(
        "UPDATE udp_subscriptions SET fields_json=? WHERE id=?",
        (json.dumps(body.fields), sub_id),
    )
    row = await db.fetchone("SELECT * FROM udp_subscriptions WHERE id = ?", (sub_id,))
    return _row_to_subscription(row)


@router.delete("/{server_id}/subscriptions/{sub_id}", status_code=204)
async def delete_subscription(
    server_id: int, sub_id: int, db: Annotated[Database, Depends(get_db)]
):
    row = await db.fetchone(
        "SELECT id FROM udp_subscriptions WHERE id = ? AND server_id = ?", (sub_id, server_id)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Subscription not found")
    await db.execute("DELETE FROM udp_subscriptions WHERE id = ?", (sub_id,))


@router.get("/{server_id}/subscriptions/{sub_id}/template.xml")
async def download_subscription_xml(
    server_id: int, sub_id: int, db: Annotated[Database, Depends(get_db)]
) -> FastAPIResponse:
    """Generate a Loxone Config UDP template XML for manual configuration."""
    srv = await db.fetchone("SELECT * FROM loxone_servers WHERE id = ?", (server_id,))
    if not srv:
        raise HTTPException(status_code=404, detail="Loxone server not found")
    sub_row = await db.fetchone(
        "SELECT sub.*, dev.name as device_name "
        "FROM udp_subscriptions sub JOIN devices dev ON sub.device_id = dev.id "
        "WHERE sub.id = ? AND sub.server_id = ?",
        (sub_id, server_id),
    )
    if not sub_row:
        raise HTTPException(status_code=404, detail="Subscription not found")

    fields: list[str] = json.loads(sub_row["fields_json"])
    device_name: str = sub_row["device_name"]
    udp_port: int = srv["port"]

    cmds = ""
    for field in fields:
        if field not in _FIELD_META:
            continue  # skip fields no longer supported
        label, unit = _FIELD_META[field]
        title = xml_escape(label)
        cmds += (
            f'\t<VirtualInUdpCmd Title="{title}" Comment="" Address=""'
            f' Check="{xml_escape(field)}=\\v"'
            f' Signed="false" Analog="true"'
            f' SourceValLow="0" DestValLow="0" SourceValHigh="0" DestValHigh="0"'
            f' DefVal="0" MinVal="0" MaxVal="0" Unit="{xml_escape(unit)}" HintText=""/>\n'
        )

    xml_content = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<VirtualInUdp Title="{xml_escape(device_name)}" Comment=""'
        f' Address="{xml_escape(srv["host"])}" Port="{udp_port}">\n'
        f'\t<Info templateType="1" minVersion="16011106"/>\n'
        f'{cmds}</VirtualInUdp>\n'
    )
    filename = f"loxone_udp_{device_name.replace(' ', '_')}.xml"
    return FastAPIResponse(
        content=xml_content,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

