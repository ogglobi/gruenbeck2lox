"""Grünbeck SC-series local REST API client (SC18, SC23, …)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .client import GruenbeckClient
from .models import DeviceInfo, DeviceValues

logger = logging.getLogger(__name__)

# Maps every known SC JSON field name → DeviceValues field name.
# Multiple source keys may map to the same target field; the first match wins.
_FIELD_MAP: dict[str, str] = {
    # Flow
    "currentFlow": "current_flow",
    "currentFlowRate": "current_flow",
    "flow": "current_flow",
    # Capacity
    "residualCapacity": "residual_capacity",
    "remainingCapacity": "residual_capacity",
    "capacityRemaining": "residual_capacity",
    "totalCapacity": "total_capacity",
    "capacityTotal": "total_capacity",
    # Salt
    "saltRange": "salt_range",
    "salt_range": "salt_range",
    "saltReach": "salt_range",
    "saltQuantity": "salt_quantity",
    "salt_quantity": "salt_quantity",
    # Hardness
    "hardnessIn": "water_hardness_in",
    "water_hardness_in": "water_hardness_in",
    "inputHardness": "water_hardness_in",
    "hardnessOut": "water_hardness_out",
    "water_hardness_out": "water_hardness_out",
    "outputHardness": "water_hardness_out",
    # Regeneration
    "regenerationStatus": "regeneration_status",
    "regeneration_status": "regeneration_status",
    "regeneration": "regeneration_status",
    "lastRegeneration": "last_regeneration",
    "last_regeneration": "last_regeneration",
    "lastRegen": "last_regeneration",
    # Error
    "errorCode": "error_code",
    "error_code": "error_code",
    "error": "error_code",
    "faultCode": "error_code",
}

# REST endpoints tried in order; first successful JSON response is used.
_REALTIME_ENDPOINTS = ["/api/realtime", "/api/sd", "/api/measurements"]
_INFO_ENDPOINTS = ["/api/info", "/api/device", "/api/system"]


class SCApi(GruenbeckClient):
    """Client for Grünbeck SC-series devices.

    Communicates via the device's local HTTP REST API on port 80 (default).
    No cloud connection is used.
    """

    async def get_realtime(self) -> DeviceValues:
        """Fetch real-time values from the SC device's local API."""
        if self._http is None:
            raise RuntimeError("Client not open – use 'async with SCApi(...)'")

        raw: dict[str, Any] = {}
        for endpoint in _REALTIME_ENDPOINTS:
            try:
                resp = await self._http.get(endpoint)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, dict) and data:
                        raw = data
                        logger.debug("SC realtime (%s %s): %s keys", self.host, endpoint, len(raw))
                        break
                    if isinstance(data, list) and data and isinstance(data[0], dict):
                        raw = data[0]
                        break
            except (httpx.HTTPError, ValueError) as exc:
                logger.debug("SC endpoint %s/%s skipped: %s", self.host, endpoint, exc)

        if not raw:
            logger.warning("SC device %s returned no usable realtime data", self.host)

        return _parse_values(raw)

    async def get_info(self) -> DeviceInfo:
        """Fetch static device information from the SC device's local API."""
        if self._http is None:
            raise RuntimeError("Client not open – use 'async with SCApi(...)'")

        for endpoint in _INFO_ENDPOINTS:
            try:
                resp = await self._http.get(endpoint)
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        if isinstance(data, dict):
                            return _parse_info(data)
                    except ValueError:
                        pass  # HTML root page – skip
            except httpx.HTTPError as exc:
                logger.debug("SC info endpoint %s/%s skipped: %s", self.host, endpoint, exc)

        return DeviceInfo()


def _parse_values(raw: dict[str, Any]) -> DeviceValues:
    """Map raw SC JSON keys to normalised DeviceValues fields."""
    valid = set(DeviceValues.model_fields)
    kwargs: dict[str, Any] = {}

    for src_key, src_val in raw.items():
        dst_field = _FIELD_MAP.get(src_key)
        if dst_field and dst_field in valid and dst_field not in kwargs:
            kwargs[dst_field] = src_val

    # Normalise residual_capacity: some devices report a fraction [0, 1]
    rc = kwargs.get("residual_capacity")
    tc = kwargs.get("total_capacity")
    if isinstance(rc, float) and 0.0 < rc <= 1.0 and tc:
        kwargs["residual_capacity"] = round(rc * float(tc), 1)

    return DeviceValues(**kwargs)


def _parse_info(raw: dict[str, Any]) -> DeviceInfo:
    """Map raw SC JSON keys to DeviceInfo fields."""
    return DeviceInfo(
        serial_number=raw.get("serialNumber") or raw.get("serial") or raw.get("id"),
        model=raw.get("model") or raw.get("type") or raw.get("deviceType"),
        firmware_version=(
            raw.get("firmwareVersion") or raw.get("firmware") or raw.get("version")
        ),
        mac_address=raw.get("macAddress") or raw.get("mac"),
        hostname=raw.get("hostname") or raw.get("name"),
    )
