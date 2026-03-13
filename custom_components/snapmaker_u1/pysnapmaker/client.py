"""Async Moonraker API client for the Snapmaker U1."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Callable, Optional

import aiohttp

from .const import (
    CAMERA_SNAPSHOT_PATH,
    CAMERA_STREAM_PATH,
    ENDPOINT_EMERGENCY_STOP,
    ENDPOINT_FILES_LIST,
    ENDPOINT_GCODE_SCRIPT,
    ENDPOINT_PRINT_CANCEL,
    ENDPOINT_PRINT_PAUSE,
    ENDPOINT_PRINT_RESUME,
    ENDPOINT_PRINT_START,
    ENDPOINT_PRINTER_INFO,
    ENDPOINT_PRINTER_OBJECTS_LIST,
    ENDPOINT_PRINTER_OBJECTS_QUERY,
    ENDPOINT_PRINTER_RESTART,
    ENDPOINT_SERVER_INFO,
    KLIPPER_READY,
    KLIPPER_SHUTDOWN,
    PRINTER_OBJECTS,
    WS_ENDPOINT,
    WS_METHOD_SUBSCRIBE,
    WS_NOTIFY_HISTORY_CHANGED,
    WS_NOTIFY_KLIPPY_DISCONNECTED,
    WS_NOTIFY_KLIPPY_READY,
    WS_NOTIFY_KLIPPY_SHUTDOWN,
    WS_NOTIFY_STATUS_UPDATE,
)
from .models import (
    ExtruderData,
    FanData,
    FilamentSensor,
    GcodeMove,
    HeaterBedData,
    IdleTimeout,
    PrintStats,
    SnapmakerPrinterData,
    Toolhead,
    VirtualSdCard,
)

_LOGGER = logging.getLogger(__name__)

# Regex patterns to identify dynamic Moonraker printer objects
_RE_FILAMENT_SENSOR = re.compile(r"^filament_switch_sensor\s+\S+")
_RE_TEMP_SENSOR = re.compile(r"^temperature_sensor\s+\S+")


class SnapmakerClient:
    """Async client for the Snapmaker U1 Moonraker API.

    Connects via WebSocket for push updates and falls back to HTTP polling
    when the WebSocket is unavailable.
    """

    def __init__(
        self,
        host: str,
        port: int = 80,
        api_key: Optional[str] = None,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.api_key = api_key

        self._session = session
        self._own_session = session is None

        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._request_id = 0
        self._pending_requests: dict[int, asyncio.Future] = {}

        self._data = SnapmakerPrinterData()
        self._connected = False
        self._callbacks: list[Callable] = []

        # Dynamically discovered object keys
        self._filament_sensor_keys: list[str] = []
        self._temp_sensor_keys: list[str] = []

        # Reconnection backoff
        self._reconnect_delay = 5
        self._max_reconnect_delay = 60
        self._should_reconnect = True

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def base_url(self) -> str:
        if self.port in (80, 443):
            scheme = "https" if self.port == 443 else "http"
            return f"{scheme}://{self.host}"
        return f"http://{self.host}:{self.port}"

    @property
    def ws_url(self) -> str:
        if self.port in (80, 443):
            scheme = "wss" if self.port == 443 else "ws"
            return f"{scheme}://{self.host}{WS_ENDPOINT}"
        return f"ws://{self.host}:{self.port}{WS_ENDPOINT}"

    @property
    def camera_stream_url(self) -> str:
        return f"{self.base_url}{CAMERA_STREAM_PATH}"

    @property
    def camera_snapshot_url(self) -> str:
        return f"{self.base_url}{CAMERA_SNAPSHOT_PATH}"

    @property
    def headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.api_key:
            headers["X-Api-Key"] = self.api_key
        return headers

    @property
    def data(self) -> SnapmakerPrinterData:
        return self._data

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def filament_sensor_keys(self) -> list[str]:
        """Names of discovered filament switch sensor objects."""
        return list(self._filament_sensor_keys)

    @property
    def temp_sensor_keys(self) -> list[str]:
        """Names of discovered extra temperature sensor objects."""
        return list(self._temp_sensor_keys)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def register_callback(self, callback: Callable) -> None:
        self._callbacks.append(callback)

    def remove_callback(self, callback: Callable) -> None:
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    async def async_init(self) -> bool:
        """Initialize session, test connection, and fetch initial state."""
        if self._own_session:
            self._session = aiohttp.ClientSession()
        try:
            await self._fetch_printer_info()
            await self._discover_dynamic_objects()
            await self._fetch_printer_state()
            await self.fetch_file_list()
            self._connected = True
            return True
        except Exception as exc:
            _LOGGER.error("Failed to initialise Snapmaker U1 client: %s", exc)
            return False

    async def async_start(self) -> None:
        """Start the WebSocket connection loop."""
        self._should_reconnect = True
        self._ws_task = asyncio.create_task(self._ws_loop())

    async def async_stop(self) -> None:
        """Stop all connections and clean up."""
        self._should_reconnect = False
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        if self._own_session and self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------
    # Dynamic object discovery
    # ------------------------------------------------------------------

    async def _discover_dynamic_objects(self) -> None:
        """Query /printer/objects/list and find filament/temp sensor objects."""
        try:
            data = await self._get(ENDPOINT_PRINTER_OBJECTS_LIST)
            objects: list[str] = data.get("result", {}).get("objects", [])
            self._filament_sensor_keys = [
                o for o in objects if _RE_FILAMENT_SENSOR.match(o)
            ]
            self._temp_sensor_keys = [
                o for o in objects if _RE_TEMP_SENSOR.match(o)
            ]
            _LOGGER.debug(
                "Discovered filament sensors: %s; extra temp sensors: %s",
                self._filament_sensor_keys,
                self._temp_sensor_keys,
            )
        except Exception as exc:
            _LOGGER.debug("Could not discover dynamic printer objects: %s", exc)

    def _all_objects(self) -> list[str]:
        """Return the full list of objects to subscribe/query."""
        return PRINTER_OBJECTS + self._filament_sensor_keys + self._temp_sensor_keys

    # ------------------------------------------------------------------
    # WebSocket connection management
    # ------------------------------------------------------------------

    async def _ws_loop(self) -> None:
        """Main WebSocket reconnection loop."""
        delay = self._reconnect_delay
        while self._should_reconnect:
            try:
                await self._ws_connect()
                delay = self._reconnect_delay  # Reset on clean run
            except asyncio.CancelledError:
                break
            except Exception as exc:
                _LOGGER.warning(
                    "WebSocket connection lost (%s). Reconnecting in %ds.", exc, delay
                )
                self._connected = False
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._max_reconnect_delay)

    async def _ws_connect(self) -> None:
        """Open and maintain a single WebSocket connection."""
        _LOGGER.debug("Connecting to Snapmaker U1 WebSocket: %s", self.ws_url)
        async with self._session.ws_connect(
            self.ws_url,
            headers=self.headers,
            heartbeat=20,
            timeout=aiohttp.ClientWSTimeout(ws_close=10),
        ) as ws:
            self._ws = ws
            self._connected = True
            _LOGGER.info("Snapmaker U1 WebSocket connected (%s)", self.host)

            await self._subscribe_objects()

            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        await self._handle_ws_message(json.loads(msg.data))
                    except Exception as exc:
                        _LOGGER.debug("Error handling WS message: %s", exc)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    _LOGGER.warning("WebSocket error: %s", ws.exception())
                    break
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSED,
                ):
                    break

        self._ws = None
        self._connected = False

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _subscribe_objects(self) -> None:
        """Send printer.objects.subscribe via WebSocket."""
        objects_param = {obj: None for obj in self._all_objects()}
        payload = {
            "jsonrpc": "2.0",
            "method": WS_METHOD_SUBSCRIBE,
            "params": {"objects": objects_param},
            "id": self._next_id(),
        }
        await self._ws.send_str(json.dumps(payload))

    # ------------------------------------------------------------------
    # Message handling
    # ------------------------------------------------------------------

    async def _handle_ws_message(self, msg: dict[str, Any]) -> None:
        """Dispatch an incoming WebSocket message."""
        if "result" in msg:
            # Response to a previous request (e.g., subscription ack)
            result = msg["result"]
            if isinstance(result, dict) and "status" in result:
                await self._process_status(result["status"])
            req_id = msg.get("id")
            if req_id and req_id in self._pending_requests:
                self._pending_requests.pop(req_id).set_result(result)
            return

        method = msg.get("method", "")
        params = msg.get("params", [])

        if method == WS_NOTIFY_STATUS_UPDATE and params:
            await self._process_status(params[0])

        elif method == WS_NOTIFY_KLIPPY_READY:
            self._data.klipper_state = KLIPPER_READY
            self._data.klipper_message = "Klipper ready"
            await self._notify_callbacks()

        elif method in (WS_NOTIFY_KLIPPY_SHUTDOWN, WS_NOTIFY_KLIPPY_DISCONNECTED):
            self._data.klipper_state = KLIPPER_SHUTDOWN
            self._data.klipper_message = "Klipper disconnected"
            await self._notify_callbacks()

        elif method == WS_NOTIFY_HISTORY_CHANGED:
            # A print just finished; refresh state and file list
            await self._fetch_printer_state()
            await self.fetch_file_list()

    async def _process_status(self, status: dict[str, Any]) -> None:
        """Parse a Moonraker status dict and update the data model."""
        changed = False

        if "webhooks" in status:
            wh = status["webhooks"]
            if "state" in wh:
                self._data.klipper_state = wh["state"]
            if "state_message" in wh:
                self._data.klipper_message = wh["state_message"]
            changed = True

        if "print_stats" in status:
            ps = status["print_stats"]
            if "state" in ps:
                self._data.print_stats.state = ps["state"]
            if "filename" in ps:
                self._data.print_stats.filename = ps.get("filename") or ""
            if "total_duration" in ps:
                self._data.print_stats.total_duration = ps["total_duration"]
            if "print_duration" in ps:
                self._data.print_stats.print_duration = ps["print_duration"]
            if "filament_used" in ps:
                self._data.print_stats.filament_used = ps["filament_used"]
            if "info" in ps:
                info = ps["info"] or {}
                self._data.print_stats.current_layer = info.get("current_layer") or 0
                self._data.print_stats.total_layer = info.get("total_layer") or 0
            changed = True

        if "virtual_sdcard" in status:
            vsd = status["virtual_sdcard"]
            if "progress" in vsd:
                self._data.virtual_sdcard.progress = vsd["progress"]
            if "is_active" in vsd:
                self._data.virtual_sdcard.is_active = vsd["is_active"]
            if "file_position" in vsd:
                self._data.virtual_sdcard.file_position = vsd["file_position"]
            if "file_size" in vsd:
                self._data.virtual_sdcard.file_size = vsd["file_size"]
            changed = True

        # Handle up to 4 extruders (extruder, extruder1, extruder2, extruder3)
        for i in range(4):
            key = "extruder" if i == 0 else f"extruder{i}"
            if key in status:
                ext = status[key]
                if key not in self._data.extruders:
                    self._data.extruders[key] = ExtruderData()
                    if i + 1 > self._data.extruder_count:
                        self._data.extruder_count = i + 1
                ed = self._data.extruders[key]
                if "temperature" in ext:
                    ed.temperature = round(ext["temperature"], 1)
                if "target" in ext:
                    ed.target = round(ext["target"], 1)
                if "power" in ext:
                    ed.power = ext["power"]
                if "can_extrude" in ext:
                    ed.can_extrude = ext["can_extrude"]
                changed = True

        if "heater_bed" in status:
            bed = status["heater_bed"]
            if "temperature" in bed:
                self._data.heater_bed.temperature = round(bed["temperature"], 1)
            if "target" in bed:
                self._data.heater_bed.target = round(bed["target"], 1)
            if "power" in bed:
                self._data.heater_bed.power = bed["power"]
            changed = True

        if "toolhead" in status:
            th = status["toolhead"]
            if "position" in th:
                self._data.toolhead.position = th["position"]
            if "homed_axes" in th:
                self._data.toolhead.homed_axes = th["homed_axes"]
            if "max_velocity" in th:
                self._data.toolhead.max_velocity = th["max_velocity"]
            if "max_accel" in th:
                self._data.toolhead.max_accel = th["max_accel"]
            changed = True

        if "gcode_move" in status:
            gm = status["gcode_move"]
            if "speed_factor" in gm:
                self._data.gcode_move.speed_factor = gm["speed_factor"]
            if "extrude_factor" in gm:
                self._data.gcode_move.extrude_factor = gm["extrude_factor"]
            changed = True

        if "fan" in status:
            fan = status["fan"]
            if "speed" in fan:
                self._data.fan.speed = round(fan["speed"] * 100, 1)
            changed = True

        if "display_status" in status:
            ds = status["display_status"]
            if "message" in ds:
                self._data.display_message = ds.get("message") or ""
            changed = True

        if "idle_timeout" in status:
            it = status["idle_timeout"]
            if "state" in it:
                self._data.idle_timeout.state = it["state"]
            if "printing_time" in it:
                self._data.idle_timeout.printing_time = it["printing_time"]
            changed = True

        # Dynamic filament switch sensors
        for key in self._filament_sensor_keys:
            if key in status:
                fs = status[key]
                if key not in self._data.filament_sensors:
                    self._data.filament_sensors[key] = FilamentSensor()
                sensor = self._data.filament_sensors[key]
                if "enabled" in fs:
                    sensor.enabled = fs["enabled"]
                if "filament_detected" in fs:
                    sensor.filament_detected = fs["filament_detected"]
                changed = True

        # Dynamic temperature sensors (chamber, MCU, etc.)
        for key in self._temp_sensor_keys:
            if key in status:
                ts = status[key]
                if "temperature" in ts:
                    self._data.chamber_sensors[key] = round(ts["temperature"], 1)
                changed = True

        if changed:
            await self._notify_callbacks()

    async def _notify_callbacks(self) -> None:
        for cb in list(self._callbacks):
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(self._data)
                else:
                    cb(self._data)
            except Exception as exc:
                _LOGGER.debug("Error in data callback: %s", exc)

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    async def _get(self, endpoint: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}{endpoint}"
        async with self._session.get(
            url,
            headers=self.headers,
            params=params,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _post(self, endpoint: str, data: dict | None = None) -> dict:
        url = f"{self.base_url}{endpoint}"
        async with self._session.post(
            url,
            headers=self.headers,
            json=data or {},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            resp.raise_for_status()
            if resp.content_length == 0 or resp.status == 204:
                return {}
            return await resp.json()

    # ------------------------------------------------------------------
    # Data fetching
    # ------------------------------------------------------------------

    async def _fetch_printer_info(self) -> None:
        """Fetch static printer identity from /printer/info."""
        try:
            data = await self._get(ENDPOINT_PRINTER_INFO)
            result = data.get("result", {})
            self._data.firmware_version = result.get("software_version", "")
            hostname = result.get("hostname", "")
            if hostname:
                self._data.printer_name = hostname
        except Exception as exc:
            _LOGGER.debug("Could not fetch printer info: %s", exc)

    async def _fetch_printer_state(self) -> None:
        """Fetch current printer state via HTTP (used for init and fallback)."""
        all_obj = self._all_objects()
        data = await self._get(
            ENDPOINT_PRINTER_OBJECTS_QUERY, params=dict.fromkeys(all_obj, "")
        )
        status = data.get("result", {}).get("status", {})
        await self._process_status(status)

    async def fetch_state(self) -> None:
        """Public wrapper – refresh printer state via HTTP."""
        await self._fetch_printer_state()

    async def fetch_file_list(self) -> None:
        """Fetch available G-code files and update the data model."""
        try:
            data = await self._get(ENDPOINT_FILES_LIST)
            files = data.get("result", [])
            self._data.available_files = sorted(
                f["filename"]
                for f in files
                if isinstance(f, dict) and "filename" in f
            )
            _LOGGER.debug("Fetched %d G-code files", len(self._data.available_files))
        except Exception as exc:
            _LOGGER.debug("Could not fetch file list: %s", exc)

    # ------------------------------------------------------------------
    # Printer control commands
    # ------------------------------------------------------------------

    async def pause_print(self) -> None:
        """Pause the active print."""
        await self._post(ENDPOINT_PRINT_PAUSE)

    async def resume_print(self) -> None:
        """Resume a paused print."""
        await self._post(ENDPOINT_PRINT_RESUME)

    async def cancel_print(self) -> None:
        """Cancel the active print."""
        await self._post(ENDPOINT_PRINT_CANCEL)

    async def emergency_stop(self) -> None:
        """Trigger an emergency stop (halts all motion immediately)."""
        await self._post(ENDPOINT_EMERGENCY_STOP)

    async def restart_klipper(self) -> None:
        """Restart the Klipper firmware process."""
        await self._post(ENDPOINT_PRINTER_RESTART)

    async def execute_gcode(self, script: str) -> None:
        """Send a G-code command or macro."""
        await self._post(ENDPOINT_GCODE_SCRIPT, {"script": script})

    async def home_axes(self, axes: str = "") -> None:
        """Home one or more axes. Pass 'X', 'Y', 'Z' or leave empty for all."""
        gcode = f"G28 {axes}".strip()
        await self.execute_gcode(gcode)

    async def set_bed_temperature(self, temp: float) -> None:
        """Set the heated bed target temperature."""
        await self.execute_gcode(f"M140 S{temp}")

    async def set_nozzle_temperature(self, temp: float, index: int = 0) -> None:
        """Set the nozzle target temperature (index 0–3 for T0–T3)."""
        if not (0 <= index <= 3):
            raise ValueError(f"Extruder index must be 0–3, got {index}")
        await self.execute_gcode(f"T{index}\nM104 S{temp}")

    async def set_fan_speed(self, speed_pct: int) -> None:
        """Set part-cooling fan speed (0–100 %)."""
        pwm = int(speed_pct / 100 * 255)
        await self.execute_gcode(f"M106 S{pwm}")

    async def set_speed_factor(self, speed_pct: int) -> None:
        """Set print speed override via M220 (10–200 %)."""
        await self.execute_gcode(f"M220 S{speed_pct}")

    async def set_flow_rate(self, flow_pct: int) -> None:
        """Set extrusion flow-rate override via M221 (50–150 %)."""
        await self.execute_gcode(f"M221 S{flow_pct}")

    async def set_work_light(self, on: bool) -> None:
        """Toggle the work/chamber light via M355."""
        await self.execute_gcode(f"M355 S{'1' if on else '0'}")

    async def set_active_tool(self, tool_index: int) -> None:
        """Switch the active extruder tool (T0–T3)."""
        if not (0 <= tool_index <= 3):
            raise ValueError(f"Tool index must be 0–3, got {tool_index}")
        await self.execute_gcode(f"T{tool_index}")

    async def start_print(self, filename: str) -> None:
        """Start printing the given G-code file from the virtual SD card."""
        await self._post(ENDPOINT_PRINT_START, {"filename": filename})

    async def list_files(self) -> list[dict]:
        """Return a list of G-code file metadata dicts from the printer."""
        data = await self._get(ENDPOINT_FILES_LIST)
        return data.get("result", [])

    # ------------------------------------------------------------------
    # Class-level helpers
    # ------------------------------------------------------------------

    @classmethod
    async def test_connection(
        cls, host: str, port: int = 80, api_key: str | None = None
    ) -> bool:
        """Return True if the Moonraker API is reachable."""
        scheme = "https" if port == 443 else "http"
        if port in (80, 443):
            base = f"{scheme}://{host}"
        else:
            base = f"http://{host}:{port}"
        headers: dict[str, str] = {}
        if api_key:
            headers["X-Api-Key"] = api_key
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{base}{ENDPOINT_SERVER_INFO}",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as resp:
                    return resp.status == 200
        except Exception:
            return False
