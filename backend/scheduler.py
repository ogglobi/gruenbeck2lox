"""Background polling scheduler and Loxone push orchestration."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from cryptography.fernet import Fernet

from backend.db.database import Database
from backend.gruenbeck import make_client
from backend.gruenbeck.cloud_api import SDCloudApi
from backend.gruenbeck.models import DeviceValues
from backend.loxone.push import push_udp_packet

logger = logging.getLogger(__name__)


class DevicePoller:
    """Polls a single Grünbeck device and pushes changes to Loxone."""

    def __init__(self, device: dict[str, Any], db: Database, fernet: Fernet) -> None:
        self._device = device
        self._db = db
        self._fernet = fernet
        self._task: asyncio.Task | None = None
        self._cloud_client: SDCloudApi | None = None
        self._prev_values: DeviceValues = DeviceValues()
        self._server_push_ts: dict[int, float] = {}
        self._ws_push_ts: float = 0.0  # last time a WS-triggered push was sent
        self._device_is_online: int = 1  # 1 = online, 0 = offline (for is_online field)
        # WS-based flow accumulator – integrates mflow1 (m³/h) to derive waterToday.
        # Resets at UTC midnight. Persisted to SQLite so container restarts keep the value.
        self._flow_last_ts: float = 0.0
        self._flow_last_m3h: float = 0.0
        self._flow_today_m3: float = 0.0
        self._flow_day: str = ""  # ISO date (UTC) of current accumulator day
        self._flow_loaded: bool = False  # True after DB restore attempted
        self._flow_was_flowing: bool = False  # True while currentFlow > 0

    @property
    def device_id(self) -> int:
        return self._device["id"]

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name=f"poller-{self.device_id}")

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()

    async def _get_or_connect_cloud(self) -> SDCloudApi:
        """Return existing connected cloud client or create and connect a new one.

        Starts the WebSocket listener on first connect so real-time push values
        (e.g. mrescapa1 for residual capacity) populate _ws_values continuously.
        """
        if self._cloud_client is not None:
            return self._cloud_client
        device = self._device
        password = self._fernet.decrypt(device["cloud_password_enc"].encode()).decode()
        client = SDCloudApi(email=device["cloud_email"], password=password)
        await client.connect()
        await client.start_websocket()
        client.ws_callback = self._on_ws_update
        self._cloud_client = client
        logger.info(
            "Cloud client connected for device %s, WebSocket started", device["id"]
        )
        return client

    # Minimum interval between WS-triggered currentFlow pushes (seconds).
    _WS_PUSH_MIN_INTERVAL: float = 1.0

    async def _on_ws_update(self, ws_data: dict[str, Any], is_null_cap_frame: bool = False) -> None:
        """Called for every live WebSocket frame from the Grünbeck cloud.

        Sends a single-field ``currentFlow=X.XXX`` UDP packet to Loxone when
        water is actually flowing, throttled to once per _WS_PUSH_MIN_INTERVAL.

        All other values (capacity, salt, waterToday, dates …) are sent by the
        regular 30s poll so there is no risk of null-frame corruption.
        """
        from backend.gruenbeck.cloud_api import _CLOUD_FIELD_MAP

        if is_null_cap_frame:
            return  # discard spurious zero-reset frames sent by cloud after enter/refresh

        now = asyncio.get_event_loop().time()

        # ── Extract flow rate ─────────────────────────────────────────────────
        raw_flow_m3h: float | None = None
        for ws_key, ws_val in ws_data.items():
            if _CLOUD_FIELD_MAP.get(ws_key) == "current_flow" and ws_val is not None:
                try:
                    raw_flow_m3h = float(ws_val)
                except (TypeError, ValueError):
                    pass
                break

        # ── Flow accumulator for waterToday ───────────────────────────────────
        # Integrates mflow1 (m³/h) via trapezoidal rule; resets at UTC midnight.
        today_utc = datetime.now(timezone.utc).date().isoformat()
        if self._flow_day != today_utc:
            self._flow_today_m3 = 0.0
            self._flow_day = today_utc
            self._flow_last_ts = now
            self._flow_last_m3h = raw_flow_m3h if raw_flow_m3h is not None else 0.0
        elif raw_flow_m3h is not None and self._flow_last_ts > 0:
            dt_hours = (now - self._flow_last_ts) / 3600.0
            self._flow_today_m3 += ((self._flow_last_m3h + raw_flow_m3h) / 2.0) * dt_hours
            self._flow_last_ts = now
            self._flow_last_m3h = raw_flow_m3h
        elif raw_flow_m3h is not None:
            self._flow_last_ts = now
            self._flow_last_m3h = raw_flow_m3h

        # ── Push currentFlow only when water is flowing ───────────────────────
        if not raw_flow_m3h or raw_flow_m3h <= 0:
            # Send a single currentFlow=0 packet on the falling edge (flow just stopped)
            if self._flow_was_flowing:
                self._flow_was_flowing = False
                self._ws_push_ts = now
                logger.debug("WS flow stopped device %s: sending currentFlow=0", self.device_id)
                await self._push_flow_only(0.0)
            return  # idle – regular 30s poll handles further updates

        if now - self._ws_push_ts < self._WS_PUSH_MIN_INTERVAL:
            return  # throttle

        self._flow_was_flowing = True
        self._ws_push_ts = now
        flow_lmin = round(raw_flow_m3h * 1000 / 60, 1)
        logger.debug("WS flow push device %s: currentFlow=%s l/min", self.device_id, flow_lmin)
        await self._push_flow_only(flow_lmin)

    async def _push_flow_only(self, flow_lmin: float) -> None:
        """Send a minimal ``currentFlow=X.XXX`` UDP packet to all subscribed Loxone servers
        that have currentFlow in their field list."""
        import json as _json

        subs = await self._db.fetchall(
            "SELECT sub.fields_json, s.host, s.port "
            "FROM udp_subscriptions sub "
            "JOIN loxone_servers s ON sub.server_id = s.id "
            "WHERE sub.device_id = ?",
            (self.device_id,),
        )
        for sub in subs:
            fields: list[str] = _json.loads(sub["fields_json"])
            if "currentFlow" not in fields:
                continue
            message = f"currentFlow={flow_lmin}\n"
            await push_udp_packet(host=sub["host"], port=sub["port"], message=message)

    async def _run(self) -> None:
        device = self._device
        logger.info(
            "Poller started: device %s (%s, type=%s)", device["id"], device["name"], device["type"]
        )
        try:
            while True:
                try:
                    await self._poll_once()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception(
                        "Unexpected error in poller for device %s: %s", device["id"], exc
                    )
                await asyncio.sleep(device["poll_interval"])
        finally:
            if self._cloud_client is not None:
                await self._cloud_client.close()
                self._cloud_client = None
                logger.debug("Cloud client closed for device %s", device["id"])

    async def _poll_once(self) -> None:
        device = self._device
        raw_source = None

        # Restore accumulator from DB on first run (survives container restart).
        if not self._flow_loaded:
            self._flow_loaded = True
            row = await self._db.fetchone(
                "SELECT acc_date, water_m3 FROM water_today_accumulator WHERE device_id = ?",
                (self.device_id,),
            )
            if row is not None:
                today_utc = datetime.now(timezone.utc).date().isoformat()
                r = dict(row)
                if r["acc_date"] == today_utc:
                    self._flow_today_m3 = float(r["water_m3"])
                    self._flow_day = today_utc
                    self._flow_last_ts = asyncio.get_event_loop().time()
                    logger.info(
                        "Device %s: restored waterToday accumulator %.3f m³ from DB",
                        self.device_id, self._flow_today_m3,
                    )
        try:
            if device.get("cloud_password_enc") and device["type"] == "sd":
                # SD device via myGruenbeck cloud – persistent client with live WebSocket so
                # _ws_values (incl. mrescapa1 = real-time remaining capacity) are populated.
                try:
                    cloud_client = await self._get_or_connect_cloud()
                    values = await cloud_client.get_realtime()
                    raw_source = cloud_client
                    self._device_is_online = 1
                except Exception as exc:
                    logger.warning(
                        "Cloud poll failed for device %s: %s – resetting client for reconnect",
                        device["id"],
                        exc,
                    )
                    if self._cloud_client is not None:
                        try:
                            await self._cloud_client.close()
                        except Exception:
                            pass
                        self._cloud_client = None
                    # Mark device as offline and push this status
                    self._device_is_online = 0
                    offline_values = DeviceValues(is_online=0)
                    await self._push_to_loxone(offline_values, {"isOnline"})
                    return
            else:
                # Local HTTP client (SC series or SD with local access)
                local_client = make_client(device["type"], device["host"], device["port"])
                async with local_client:
                    values = await local_client.get_realtime()
                raw_source = local_client
            # Mark device as online
            self._device_is_online = 1
        except Exception as exc:
            logger.error("Device %s poll failed: %s", device["id"], exc)
            # Mark device as offline and push this status
            self._device_is_online = 0
            offline_values = DeviceValues(is_online=0)
            await self._push_to_loxone(offline_values, {"isOnline"})
            return

        # Compute saltToday from persistent monthly baseline.
        # waterToday comes from the WS flow accumulator (more accurate, avoids
        # cloud monthly-counter jump artifacts).
        values = await self._compute_daily_today(values)
        # Set online status from poll result
        values.is_online = self._device_is_online
        today_utc = datetime.now(timezone.utc).date().isoformat()
        if self._flow_day == today_utc:
            # WS accumulator is running for today – use it for waterToday.
            values.water_today = round(self._flow_today_m3 * 1000, 1)
        # If accumulator has not started yet (container just restarted, no flow seen)
        # leave values.water_today from _compute_daily_today as-is.

        # Persist values to DB
        await self._save_values(values)
        # Persist waterToday accumulator so it survives container restarts.
        await self._db.execute(
            "INSERT INTO water_today_accumulator (device_id, acc_date, water_m3, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(device_id) DO UPDATE SET "
            "  acc_date=excluded.acc_date, water_m3=excluded.water_m3, updated_at=excluded.updated_at",
            (self.device_id, today_utc,
             self._flow_today_m3 if self._flow_day == today_utc else 0.0,
             datetime.now(timezone.utc).isoformat()),
        )
        # Save full raw values for detail view
        if raw_source is not None and hasattr(raw_source, "_latest_values") and raw_source._latest_values:
            await self._save_raw_cache(raw_source._latest_values)

        changed = values.diff(self._prev_values)
        await self._push_to_loxone(values, set(changed.keys()))
        self._prev_values = values

    async def _compute_daily_today(self, values: DeviceValues) -> DeviceValues:
        """Compute waterToday / saltToday from the daily baseline stored in the DB.

        The baseline is the monthly counter value at the start of the current calendar
        day (UTC). Whenever the date changes (or on first run) the current counters are
        persisted as the new baseline and the delta resets to zero.
        Monthly resets (counter < baseline) are handled by resetting the baseline.
        """
        today = datetime.now(timezone.utc).date().isoformat()
        row = await self._db.fetchone(
            "SELECT baseline_date, water_month_start, salt_month_start "
            "FROM daily_baseline WHERE device_id = ?",
            (self.device_id,),
        )
        water_now = values.water_month
        salt_now = values.salt_month

        if row is None or dict(row)["baseline_date"] != today:
            # New day or first run – snapshot current monthly counters as baseline
            await self._db.execute(
                "INSERT INTO daily_baseline "
                "  (device_id, baseline_date, water_month_start, salt_month_start) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(device_id) DO UPDATE SET "
                "  baseline_date       = excluded.baseline_date, "
                "  water_month_start   = excluded.water_month_start, "
                "  salt_month_start    = excluded.salt_month_start",
                (self.device_id, today, water_now, salt_now),
            )
            values.water_today = 0.0 if water_now is not None else None
            values.salt_today = 0.0 if salt_now is not None else None
        else:
            r = dict(row)
            s_start = r["salt_month_start"]
            # waterToday is supplied by the WS accumulator in _poll_once; skip it here.
            values.salt_today = (
                max(0.0, salt_now - s_start)
                if salt_now is not None and s_start is not None
                else None
            )
        return values

    async def _save_values(self, values: DeviceValues) -> None:
        """Upsert current values into the device_values cache table."""
        now = datetime.now(timezone.utc).isoformat()
        for key, raw_val in values.to_push_dict().items():
            if raw_val is None:
                continue
            await self._db.execute(
                "INSERT INTO device_values (device_id, key, value, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(device_id, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (self.device_id, key, str(raw_val), now),
            )

    async def _save_raw_cache(self, raw: dict[str, Any]) -> None:
        """Upsert full raw cloud values into device_raw_cache for the detail view."""
        import json as _json

        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT INTO device_raw_cache (device_id, raw_json, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(device_id) DO UPDATE SET raw_json=excluded.raw_json, updated_at=excluded.updated_at",
            (self.device_id, _json.dumps(raw, default=str), now),
        )

    async def _push_to_loxone(self, values: DeviceValues, changed_keys: set[str]) -> None:
        """Push UDP packets to all Loxone servers subscribed to this device."""
        import json as _json

        subs = await self._db.fetchall(
            "SELECT sub.id as sub_id, sub.fields_json, "
            "       s.id as server_id, s.host, s.port, s.push_on_change, s.push_interval_sec "
            "FROM udp_subscriptions sub "
            "JOIN loxone_servers s ON sub.server_id = s.id "
            "WHERE sub.device_id = ?",
            (self.device_id,),
        )
        if not subs:
            return

        push_dict = values.to_push_dict()
        now = asyncio.get_event_loop().time()

        for sub in subs:
            server_id = sub["server_id"]
            fields: list[str] = _json.loads(sub["fields_json"])
            push_on_change = bool(sub["push_on_change"])
            interval = sub["push_interval_sec"]

            last_push = self._server_push_ts.get(server_id, 0)
            heartbeat_due = (now - last_push) >= interval
            has_change = bool(set(fields) & changed_keys)

            if push_on_change and not has_change and not heartbeat_due:
                continue
            if not push_on_change and not heartbeat_due:
                continue

            lines = []
            for k in fields:
                v = push_dict.get(k)
                if v is None:
                    continue
                if k in ("next_regeneration", "last_regeneration"):
                    from backend.gruenbeck.models import _to_loxone_ts
                    from datetime import date, timedelta
                    raw_v = str(v)
                    # Try full ISO datetime first; if only HH:MM prepend today's date
                    v = _to_loxone_ts(raw_v)
                    if v is None and len(raw_v) == 5 and raw_v[2] == ":":
                        today = date.today()
                        v = _to_loxone_ts(f"{today}T{raw_v}:00")
                        # If the result represents a future time, it actually happened yesterday
                        if v is not None:
                            from datetime import datetime, timezone
                            _epoch = datetime(2009, 1, 1, tzinfo=timezone.utc)
                            _now_ts = int((datetime.now(timezone.utc) - _epoch).total_seconds())
                            if v > _now_ts:
                                v = _to_loxone_ts(f"{today - timedelta(days=1)}T{raw_v}:00")
                    if v is None:
                        continue
                elif k in ("saltYear", "saltMonth", "saltToday") and v is not None:
                    v = round(float(v) / 1000, 3)
                lines.append(f"{k}={v}")
            if not lines:
                continue

            message = "\n".join(lines) + "\n"
            result = await push_udp_packet(host=sub["host"], port=sub["port"], message=message)
            self._server_push_ts[server_id] = now

            status = "ok" if result.success else "error"
            summary = ",".join(fields[:3]) + ("…" if len(fields) > 3 else "")
            await self._db.execute(
                "INSERT INTO push_log "
                "(loxone_server_id, device_id, gruenbeck_key, loxone_input, value, status, error_message) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (server_id, self.device_id, summary, f"udp:{sub['port']}",
                 f"{len(lines)} Felder", status, result.error),
            )



class Scheduler:
    """Manages DevicePoller instances for all active devices."""

    def __init__(self, db: Database, fernet: Fernet) -> None:
        self._db = db
        self._fernet = fernet
        self._pollers: dict[int, DevicePoller] = {}

    async def start_all(self) -> None:
        """Load all enabled devices from DB and start their pollers."""
        rows = await self._db.fetchall(
            "SELECT * FROM devices WHERE enabled = 1"
        )
        for row in rows:
            device = dict(row)
            poller = DevicePoller(device, self._db, self._fernet)
            poller.start()
            self._pollers[device["id"]] = poller
        logger.info("Scheduler started %d poller(s)", len(self._pollers))

    async def stop_all(self) -> None:
        """Cancel all running pollers."""
        for poller in self._pollers.values():
            poller.stop()
        self._pollers.clear()
        logger.info("Scheduler stopped all pollers")

    async def add_device(self, device: dict[str, Any]) -> None:
        """Start a poller for a newly added device."""
        if not device.get("enabled"):
            return
        device_id = device["id"]
        if device_id in self._pollers:
            self._pollers[device_id].stop()
        poller = DevicePoller(device, self._db, self._fernet)
        poller.start()
        self._pollers[device_id] = poller

    async def remove_device(self, device_id: int) -> None:
        """Stop and remove the poller for a device."""
        poller = self._pollers.pop(device_id, None)
        if poller:
            poller.stop()

    async def restart_device(self, device_id: int) -> None:
        """Reload device config from DB and restart its poller."""
        await self.remove_device(device_id)
        row = await self._db.fetchone("SELECT * FROM devices WHERE id = ?", (device_id,))
        if row and row["enabled"]:
            await self.add_device(dict(row))
