"""Pydantic models for Loxone server and mapping configuration."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoxoneServer(BaseModel):
    """Represents a Loxone Miniserver target for HTTP/UDP push."""

    id: int
    name: str
    host: str
    port: int = 80
    user: str
    push_mode: str = "http"  # "http" | "udp"


class Mapping(BaseModel):
    """Links one Grünbeck device value key to one Loxone Virtual HTTP Input."""

    id: int
    loxone_server_id: int
    device_id: int
    gruenbeck_key: str
    loxone_input: str


class PushResult(BaseModel):
    """Outcome of a single Loxone push attempt."""

    success: bool
    status_code: int | None = None
    error: str | None = None
