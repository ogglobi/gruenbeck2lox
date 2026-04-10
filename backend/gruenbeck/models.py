"""Pydantic models for Grünbeck device data."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, field_validator

# Loxone epoch: seconds since 2009-01-01 00:00:00 UTC
_LOXONE_EPOCH = datetime(2009, 1, 1, tzinfo=timezone.utc)


def _to_loxone_ts(iso_str: str | None) -> int | None:
    """Convert an ISO-8601 string to Loxone epoch seconds (since 2009-01-01 UTC).

    Returns None if parsing fails or input is None.
    """
    if iso_str is None:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0, int((dt - _LOXONE_EPOCH).total_seconds()))
    except (ValueError, OverflowError):
        return None


class DeviceValues(BaseModel):
    """Normalised real-time values polled from a Grünbeck device."""

    # Core real-time
    current_flow: float | None = Field(None, description="Current flow rate in m³/h (stored raw; converted to l/min on push)")
    residual_capacity: float | None = Field(None, description="Remaining capacity in liters")
    residual_capacity_m3: float | None = Field(None, description="Remaining capacity in m³")
    residual_capacity_pct: float | None = Field(None, description="Remaining capacity in %")
    total_capacity: float | None = Field(None, description="Total capacity in liters")
    salt_quantity: float | None = Field(None, description="Current salt quantity in kg")
    water_hardness_in: float | None = Field(None, description="Input water hardness in °dH")
    water_hardness_out: float | None = Field(None, description="Output water hardness in °dH")
    regeneration_status: int | None = Field(None, description="0 = normal, 1 = regenerating")
    last_regeneration: str | None = Field(None, description="ISO-8601 timestamp of last regeneration")
    next_regeneration: str | None = Field(None, description="ISO-8601 timestamp of next regeneration")
    error_code: int | None = Field(None, description="Current error code; 0 = no error")
    # Extended metrics (SD-series cloud API)
    water_today: float | None = Field(None, description="Soft water today in litres (computed from daily delta)")
    water_month: float | None = Field(None, description="Soft water this month in litres")
    water_year: float | None = Field(None, description="Soft water this year in litres")
    salt_today: float | None = Field(None, description="Salt used today in grams (computed from daily delta)")
    salt_month: float | None = Field(None, description="Salt used this month in grams")
    salt_year: float | None = Field(None, description="Salt used this year in grams")
    salt_range: int | None = Field(None, description="Estimated days of salt remaining")
    maintenance_days: int | None = Field(None, description="Days until next maintenance")
    mode: str | None = Field(None, description="Operating mode (Comfort, Auto, etc.)")
    has_error: bool = Field(False, description="True if active (unresolved) error exists")
    last_error_msg: str | None = Field(None, description="Latest unresolved error message")

    @field_validator("last_regeneration", "next_regeneration", "mode", "last_error_msg", mode="before")
    @classmethod
    def _coerce_to_str(cls, v: Any) -> str | None:
        """Accept integers / floats from the cloud API for string fields."""
        return str(v) if v is not None else None

    def to_push_dict(self) -> dict[str, Any]:
        """Return a flat {key: value} dict for Loxone push mapping."""
        return {
            # Convert m³/h → l/min for Loxone (1 m³/h = 1000/60 l/min ≈ 16.667)
            "currentFlow":          round(self.current_flow * 1000 / 60, 3) if self.current_flow is not None else None,
            "residualCapacity":     self.residual_capacity,
            "residualCapacityM3":   self.residual_capacity_m3,
            "residualCapacityPct":  self.residual_capacity_pct,
            "totalCapacity":        self.total_capacity,
            "salt_quantity":        self.salt_quantity,
            "water_hardness_in":    self.water_hardness_in,
            "water_hardness_out":   self.water_hardness_out,
            "regeneration_status":  self.regeneration_status,
            "last_regeneration":    self.last_regeneration,
            "next_regeneration":    self.next_regeneration,
            "error_code":           self.error_code,
            "waterToday":           self.water_today,
            "waterMonth":           self.water_month,
            "waterYear":            self.water_year,
            "saltToday":            self.salt_today,
            "saltMonth":            self.salt_month,
            "saltYear":             self.salt_year,
            "saltRange":            self.salt_range,
            "maintenanceDays":      self.maintenance_days,
            "mode":                 self.mode,
            "hasError":             1 if self.has_error else 0,
            "lastErrorMsg":         self.last_error_msg,
        }

    def diff(self, other: "DeviceValues") -> dict[str, Any]:
        """Return keys whose values differ between self and other."""
        changed: dict[str, Any] = {}
        own = self.to_push_dict()
        other_dict = other.to_push_dict()
        for key, value in own.items():
            if value != other_dict.get(key):
                changed[key] = value
        return changed


class DeviceInfo(BaseModel):
    """Static information about a Grünbeck device."""

    serial_number: str | None = None
    model: str | None = None
    firmware_version: str | None = None
    mac_address: str | None = None
    hostname: str | None = None
