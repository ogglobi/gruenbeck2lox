"""myGruenbeck cloud REST + WebSocket client for softliQ SD-series devices.

This client authenticates via Azure B2C (see cloud_auth.py) and fetches
real-time device data from the myGruenbeck Azure backend.

Cloud API host: prod-eu-gruenbeck-api.azurewebsites.net

Reference: https://github.com/TA2k/ioBroker.gruenbeck
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
import websockets

from .cloud_auth import TokenSet, login, refresh_tokens
from .models import DeviceInfo, DeviceValues

logger = logging.getLogger(__name__)

_API_BASE    = "https://prod-eu-gruenbeck-api.azurewebsites.net"
_API_VERSION = "2024-05-02"
_SIGNALR_HUB = "gruenbeck"
_USER_AGENT  = "Gruenbeck/354 CFNetwork/1240.0.4 Darwin/20.6.0"
_TOKEN_REFRESH_INTERVAL = 50 * 60  # 50 minutes

# Maps cloud JSON field names → DeviceValues field names
_CLOUD_FIELD_MAP: dict[str, str] = {
    # Flow / throughput
    "currentFlow":            "current_flow",
    "currentFlowRate":        "current_flow",
    "D_A_1_1":                "current_flow",
    "mflow1":                 "current_flow",
    "mflow2":                 "current_flow",
    # Residual capacity – liters (local SC API + pcurrent cloud)
    "residualCapacity":       "residual_capacity",
    "remainingCapacity":      "residual_capacity",
    "D_A_1_2":                "residual_capacity",
    "pcurrent":               "residual_capacity",   # SD cloud: current remaining litres
    # Residual capacity – m³ (cloud SD API)
    "mrescapa1":              "residual_capacity_m3",
    "mrescapa2":              "residual_capacity_m3",
    # Residual capacity – percent
    "mresidcap1":             "residual_capacity_pct",
    "mresidcap2":             "residual_capacity_pct",
    "residualCapacityPercent":"residual_capacity_pct",
    "D_A_1_4":                "residual_capacity_pct",
    # Total capacity
    "totalCapacity":          "total_capacity",
    "softWaterQuantity":      "total_capacity",
    "D_A_1_3":                "total_capacity",
    "mcapacity":              "total_capacity",
    "pload":                  "total_capacity",      # SD cloud: exchange capacity litres
    # Salt range (days)
    "saltRange":              "salt_range",
    "saltReach":              "salt_range",
    "D_Y_1":                  "salt_range",
    "msaltrange":             "salt_range",
    # Salt quantity
    "saltQuantity":           "salt_quantity",
    "D_A_2_1":                "salt_quantity",
    "msaltusage":             "salt_quantity",
    # Water hardness in
    "hardnessIn":             "water_hardness_in",
    "rawWaterHardness":       "water_hardness_in",
    "rawWater":               "water_hardness_in",   # SD cloud base endpoint
    "D_A_2_2":                "water_hardness_in",
    "prawhard":               "water_hardness_in",
    # Water hardness out.
    # softWater (base REST) = configured output hardness setpoint = matches device display.
    # mhardsoftw (WS) is a different internal measurement and does NOT match the display.
    "softWater":              "water_hardness_out",
    "D_A_2_3":                "water_hardness_out",
    # Regeneration
    "regenerationStatus":     "regeneration_status",
    "D_D_1":                  "regeneration_status",
    "mregstatus":             "regeneration_status",
    "lastRegeneration":       "last_regeneration",
    "mendreg1":               "last_regeneration",
    "nextRegeneration":       "next_regeneration",
    "icalcreg1":              "next_regeneration",
    # Error
    "errorCode":              "error_code",
    "D_K_1":                  "error_code",
    # Maintenance & mode
    "mmaint":                 "maintenance_days",
    "maintenanceDays":        "maintenance_days",
    "pmode":                  "mode",
    "operatingMode":          "mode",
    # Error state
    "hasError":               "has_error",
    "activeErrorMsg":         "last_error_msg",
    # Water consumption
    "waterMonth":             "water_month",
    "softWaterMonth":         "water_month",
    "waterYear":              "water_year",
    "softWaterYear":          "water_year",
    # Salt consumption
    "saltMonth":              "salt_month",
    "saltYear":               "salt_year",
}


class SDCloudApi:
    """myGruenbeck cloud client for softliQ SD-series devices.

    Authenticates once, keeps tokens alive via background refresh, and
    exposes the same interface as the local GruenbeckClient.

    Usage::

        client = SDCloudApi(email="...", password="...")
        await client.connect()
        values = await client.get_realtime()
        await client.close()
    """

    def __init__(self, email: str, password: str) -> None:
        # Credentials kept only in memory, never written to disk / logs
        self._email = email
        self._password = password

        self._tokens: TokenSet | None = None
        self._device_id: str | None = None
        self._device_info: DeviceInfo = DeviceInfo()
        self._latest_values: dict[str, Any] = {}
        self._refresh_task: asyncio.Task | None = None
        self._ws_task: asyncio.Task | None = None
        self._ws_values: dict[str, Any] = {}
        # Event set whenever a non-null WS frame with capacity data arrives.
        # get_realtime() waits on this event instead of a fixed sleep so the poll
        # only proceeds once the device has actually responded via SignalR.
        self._ws_live_event: asyncio.Event = asyncio.Event()
        # Optional callback fired for every WebSocket frame (raw dict of changed fields).
        # Set by DevicePoller to enable real-time Loxone push without waiting for next poll.
        self.ws_callback: Any | None = None  # Callable[[dict[str, Any]], Awaitable[None]] | None

    # ── Public interface ──────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Authenticate and discover the first SD device in the account."""
        self._tokens = await login(self._email, self._password)
        await self._discover_device()
        self._refresh_task = asyncio.create_task(self._token_refresh_loop())
        logger.info("SDCloudApi connected, device_id=%s", self._device_id)

    async def close(self) -> None:
        """Cancel background tasks and clean up."""
        if self._refresh_task:
            self._refresh_task.cancel()
        if self._ws_task:
            self._ws_task.cancel()

    async def get_realtime(self) -> DeviceValues:
        """Fetch latest values from all relevant cloud endpoints."""
        if not self._tokens or not self._device_id:
            logger.warning("SDCloudApi not connected – returning empty values")
            return DeviceValues()

        # 1. Base device endpoint – real-time sensor values + embedded measurements
        raw = await self._get(f"/api/devices/{self._device_id}")
        if raw:
            # Parse measurement lists embedded in the base response (recent 3 days)
            for embed_key in ("water", "salt"):
                embed_list = raw.get(embed_key)
                if isinstance(embed_list, list):
                    parsed = self._parse_measurements(embed_list, embed_key)
                    self._latest_values.update(parsed)
            # Extract active (unresolved) error message BEFORE the flat filter strips lists
            errors_list = raw.get("errors", [])
            if isinstance(errors_list, list):
                active_err = next(
                    (e for e in errors_list if isinstance(e, dict) and not e.get("isResolved", True)),
                    None,
                )
                if active_err:
                    self._latest_values["activeErrorMsg"] = active_err.get("message", "")
                else:
                    self._latest_values.pop("activeErrorMsg", None)
            # Merge only flat scalar values (skip lists/dicts that confuse field map)
            flat = {k: v for k, v in raw.items() if not isinstance(v, (list, dict))}
            self._latest_values.update(flat)

        # 2. Parameters endpoint – hardness settings, mode, maintenance, etc.
        #    Loaded BEFORE /update so the live snapshot can override stale parameter
        #    values (e.g. pcurrent = remaining capacity at last regeneration).
        params = await self._get(f"/api/devices/{self._device_id}/parameters")
        if params:
            self._latest_values.update(params)

        # 3. Dedicated measurements endpoints – full history, overrides the 3-day embedded lists
        water_list = await self._get_list(f"/api/devices/{self._device_id}/measurements/water")
        if water_list:
            self._latest_values.update(self._parse_measurements(water_list, "water"))

        salt_list = await self._get_list(f"/api/devices/{self._device_id}/measurements/salt")
        if salt_list:
            self._latest_values.update(self._parse_measurements(salt_list, "salt"))

        # 4. Overlay WebSocket stream values (most up-to-date, live push from device).
        #    If the WS task is running, wait for the first live (non-null) frame triggered
        #    by the enter/refresh call above.  Timeout of 4 s covers slow cloud responses.
        if self._ws_task and not self._ws_task.done():
            self._ws_live_event.clear()
            await self._refresh_realtime()  # trigger the device to push live values
            try:
                await asyncio.wait_for(self._ws_live_event.wait(), timeout=4.0)
            except asyncio.TimeoutError:
                logger.debug("WS live event timed out – using last known WS values")
        self._latest_values.update(self._ws_values)

        return _parse_cloud_values(self._latest_values)

    async def get_info(self) -> DeviceInfo:
        return self._device_info

    async def test_connection(self) -> bool:
        """Return True if we can reach the cloud API."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{_API_BASE}/api/devices",
                    params={"api-version": _API_VERSION},
                    headers=self._auth_headers(),
                )
                return resp.status_code < 500
        except (httpx.ConnectError, httpx.TimeoutException):
            return False

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _parse_measurements(data: Any, prefix: str) -> dict[str, Any]:
        """Extract today/month/year aggregates from a measurements response.

        The API may return either a flat dict (already keyed) or a list of
        daily {date, value} records that we aggregate ourselves.
        """
        from datetime import datetime, timezone

        if isinstance(data, dict):
            return data   # Already flat – pass through for _CLOUD_FIELD_MAP handling

        if not isinstance(data, list) or not data:
            return {}

        today = datetime.now(timezone.utc).date()
        today_sum = month_sum = year_sum = 0.0

        for rec in data:
            if not isinstance(rec, dict):
                continue
            date_str = rec.get("date") or rec.get("day") or rec.get("timestamp") or ""
            val = rec.get("value") or rec.get("count") or rec.get("amount") or 0
            try:
                rec_date = datetime.fromisoformat(str(date_str)[:10]).date()
                fval = float(val)
                if rec_date == today:
                    today_sum += fval
                if rec_date.year == today.year and rec_date.month == today.month:
                    month_sum += fval
                if rec_date.year == today.year:
                    year_sum += fval
            except (ValueError, TypeError, AttributeError):
                continue

        result: dict[str, Any] = {}
        lc = prefix.lower()   # keep keys lowercase to match _CLOUD_FIELD_MAP
        # Always emit aggregates so a genuine zero is not silently dropped
        if month_sum or year_sum or today_sum:
            result[f"{lc}Today"] = round(today_sum, 4)
            result[f"{lc}Month"] = round(month_sum, 4)
            result[f"{lc}Year"]  = round(year_sum, 4)
        return result

    def _auth_headers(self) -> dict[str, str]:
        if not self._tokens:
            return {}
        return {
            "Authorization": f"Bearer {self._tokens.access_token}",
            "User-Agent": _USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "de-de",
        }

    async def _get(self, path: str, **params: str) -> dict[str, Any]:
        params.setdefault("api-version", _API_VERSION)
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{_API_BASE}{path}",
                    headers=self._auth_headers(),
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()
                return data if isinstance(data, dict) else {}
        except Exception as exc:
            logger.error("SDCloud GET %s failed: %s", path, exc)
            return {}

    async def _get_list(self, path: str, **params: str) -> list[Any]:
        """Like _get but returns a list response (measurements endpoints)."""
        params.setdefault("api-version", _API_VERSION)
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{_API_BASE}{path}",
                    headers=self._auth_headers(),
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()
                return data if isinstance(data, list) else []
        except Exception as exc:
            logger.error("SDCloud GET %s failed: %s", path, exc)
            return []

    async def _post(self, path: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{_API_BASE}{path}",
                    headers={**self._auth_headers(), "Content-Type": "application/json"},
                    params={"api-version": _API_VERSION},
                    json={},
                )
                return resp.status_code < 400
        except Exception as exc:
            logger.error("SDCloud POST %s failed: %s", path, exc)
            return False

    async def _discover_device(self) -> None:
        """Find the first softliQ device in the account and store its ID."""
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{_API_BASE}/api/devices",
                    headers=self._auth_headers(),
                    params={"api-version": _API_VERSION},
                )
                resp.raise_for_status()
                devices: list[dict] = resp.json()
                # Filter softliQ devices
                softliq = [d for d in devices if "soft" in str(d.get("id", "")).lower()]
                if not softliq:
                    softliq = devices  # Fallback: take all

                if not softliq:
                    raise RuntimeError("No devices found in myGruenbeck account")

                device = softliq[0]
                self._device_id = device.get("id") or device.get("deviceId")
                self._device_info = DeviceInfo(
                    serial_number=str(device.get("serialNumber") or self._device_id),
                    model=str(device.get("type") or device.get("model") or "SD"),
                    firmware_version=device.get("firmwareVersion"),
                )
                logger.info("Discovered SD device: %s (%s)", self._device_id, self._device_info.model)
        except Exception as exc:
            raise RuntimeError(f"SDCloud device discovery failed: {exc}") from exc

    async def _refresh_realtime(self) -> None:
        """Enter + refresh realtime mode to trigger a data snapshot update."""
        if not self._device_id:
            return
        await self._post(f"/api/devices/{self._device_id}/realtime/enter")
        await self._post(f"/api/devices/{self._device_id}/realtime/refresh")

    async def _leave_realtime(self) -> None:
        if not self._device_id:
            return
        await self._post(f"/api/devices/{self._device_id}/realtime/leave")

    async def _token_refresh_loop(self) -> None:
        """Background task: refresh access token every 50 minutes."""
        while True:
            await asyncio.sleep(_TOKEN_REFRESH_INTERVAL)
            try:
                assert self._tokens is not None
                self._tokens = await refresh_tokens(self._tokens.refresh_token)
                logger.debug("myGruenbeck access token refreshed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Token refresh failed: %s – retrying in 60s", exc)
                await asyncio.sleep(60)

    async def start_websocket(self) -> None:
        """Start background WebSocket listener for real-time push updates.

        This is optional – polling via get_realtime() works without it.
        """
        if self._ws_task and not self._ws_task.done():
            return
        self._ws_task = asyncio.create_task(self._ws_loop())

    async def _ws_loop(self) -> None:
        """Maintain a SignalR WebSocket connection and parse incoming frames."""
        while True:
            try:
                await self._ws_connect_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("WebSocket disconnected: %s – reconnecting in 5s", exc)
                await asyncio.sleep(5)

    async def _ws_connect_once(self) -> None:
        """Negotiate and connect to the SignalR hub once."""
        if not self._tokens:
            return

        # Negotiate
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{_API_BASE}/api/realtime/negotiate",
                headers={
                    **self._auth_headers(),
                    "Content-Type": "text/plain;charset=UTF-8",
                    "Origin": "file://",
                    "X-Requested-With": "XMLHttpRequest",
                },
            )
            resp.raise_for_status()
            neg = resp.json()

        ws_token = neg.get("accessToken")

        # Get SignalR connection ID
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp2 = await client.post(
                "https://prod-eu-gruenbeck-signalr.service.signalr.net/client/negotiate",
                params={"hub": _SIGNALR_HUB},
                headers={
                    "Content-Type": "text/plain;charset=UTF-8",
                    "Origin": "file://",
                    "Authorization": f"Bearer {ws_token}",
                    "User-Agent": _USER_AGENT,
                },
                json={},
            )
            resp2.raise_for_status()
            conn_data = resp2.json()

        connection_id = conn_data.get("connectionId")
        ws_url = (
            f"wss://prod-eu-gruenbeck-signalr.service.signalr.net/client/"
            f"?hub={_SIGNALR_HUB}&id={connection_id}&access_token={ws_token}"
        )

        async with websockets.connect(ws_url, user_agent_header=_USER_AGENT) as ws:
            logger.debug("SignalR WebSocket connected")
            # Init SignalR protocol
            await ws.send('{"protocol":"json","version":1}\x1e')

            async for message in ws:
                try:
                    # SignalR uses record separator 0x1e as message delimiter
                    for frame in message.split("\x1e"):
                        frame = frame.strip()
                        if not frame:
                            continue
                        data = json.loads(frame)
                        if data.get("type") == 1 and "arguments" in data:
                            for arg in data["arguments"]:
                                if isinstance(arg, dict):
                                    # Detect null-cap frames: the cloud pushes a cached-snapshot
                                    # (all capacity fields = 0) immediately after enter/refresh,
                                    # before the device responds with real live values.
                                    # A frame is a null-cap frame when at least one capacity key
                                    # is present AND all present capacity keys are zero/falsy.
                                    _CAP_KEYS = {
                                        "mrescapa1", "mrescapa2",
                                        "mresidcap1", "mresidcap2",
                                    }
                                    present_cap = {k for k in _CAP_KEYS if k in arg}
                                    is_null_cap_frame = bool(present_cap) and all(
                                        not arg.get(k) for k in present_cap
                                    )
                                    for k, v in arg.items():
                                        if is_null_cap_frame and k in _CAP_KEYS:
                                            continue  # retain existing non-zero values
                                        self._ws_values[k] = v
                                    # Only signal live data when we actually have non-zero cap values
                                    if not is_null_cap_frame and any(arg.get(k) for k in present_cap):
                                        self._ws_live_event.set()
                                    logger.debug(
                                        "WS frame: %d keys (null_cap=%s)",
                                        len(arg), is_null_cap_frame,
                                    )
                                    if self.ws_callback is not None:
                                        asyncio.create_task(self.ws_callback(arg, is_null_cap_frame))
                except (json.JSONDecodeError, KeyError):
                    pass


def _parse_cloud_values(raw: dict[str, Any]) -> DeviceValues:
    """Map cloud JSON fields to normalised DeviceValues."""
    from datetime import date, timedelta

    valid = set(DeviceValues.model_fields)
    kwargs: dict[str, Any] = {}

    for src_key, src_val in raw.items():
        if src_val is None:
            continue
        dst = _CLOUD_FIELD_MAP.get(src_key)
        if dst and dst in valid and dst not in kwargs:
            kwargs[dst] = src_val

    # Derive / reconcile residual capacity fields so all three stay consistent.
    #
    # Priority (highest first):
    #   1. mrescapa1  → residual_capacity_m3  (WebSocket, real-time)
    #   2. mresidcap1 → residual_capacity_pct (WebSocket, real-time)
    #   3. pcurrent   → residual_capacity     (parameter, stale – only fallback)
    #
    # If both m³ and % are available (from WS), derive the actual cycle capacity:
    #   total_capacity = residual_m3 / (pct / 100)
    # This is more accurate than pload (nominal capacity at reference hardness).

    # Step 1: if only liters from pcurrent, derive m³
    rc = kwargs.get("residual_capacity")
    if rc is not None and "residual_capacity_m3" not in kwargs:
        try:
            kwargs["residual_capacity_m3"] = round(float(rc) / 1000, 3)
        except (TypeError, ValueError):
            pass

    # Step 2: if real-time m³ arrived (WS), override stale pcurrent liters
    if "residual_capacity_m3" in kwargs:
        try:
            kwargs["residual_capacity"] = round(float(kwargs["residual_capacity_m3"]) * 1000, 1)
        except (TypeError, ValueError):
            pass
        rc = kwargs.get("residual_capacity")

    # Step 3: if both m³ and % are known, derive the actual cycle capacity
    pct = kwargs.get("residual_capacity_pct")
    if rc is not None and pct is not None:
        try:
            pct_f = float(pct)
            if pct_f > 0:
                kwargs["total_capacity"] = round(float(rc) / (pct_f / 100))
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    # Step 4: fallback % from liters / total if not yet set
    rc = kwargs.get("residual_capacity")
    tc = kwargs.get("total_capacity")
    if rc is not None and tc and "residual_capacity_pct" not in kwargs:
        try:
            kwargs["residual_capacity_pct"] = round(float(rc) / float(tc) * 100, 1)
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    # Compute maintenance_days from lastService + pmaintint (days) if not already mapped
    if "maintenance_days" not in kwargs:
        last_service = raw.get("lastService")
        maint_interval = raw.get("pmaintint", 365)
        if last_service:
            try:
                ls_date = date.fromisoformat(str(last_service)[:10])
                next_maint = ls_date + timedelta(days=int(maint_interval))
                kwargs["maintenance_days"] = max(0, (next_maint - date.today()).days)
            except (ValueError, TypeError):
                pass

    return DeviceValues(**kwargs)
