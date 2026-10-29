"""Async Moonraker API client for the Snapmaker U1."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Callable, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

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
    ENDPOINT_WEBCAMS_LIST,
    KLIPPER_READY,
    KLIPPER_SHUTDOWN,
    PRINTER_OBJECTS,
    WS_ENDPOINT,
    WS_METHOD_SUBSCRIBE,
    WS_NOTIFY_FILELIST_CHANGED,
    WS_NOTIFY_HISTORY_CHANGED,
    WS_NOTIFY_KLIPPY_DISCONNECTED,
    WS_NOTIFY_KLIPPY_READY,
    WS_NOTIFY_KLIPPY_SHUTDOWN,
    WS_NOTIFY_STATUS_UPDATE,
)
from .models import (
    ChamberHeaterData,
    FilamentSlot,
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
# The U1 uses filament_motion_sensor e0_filament … e3_filament; plain Klipper
# setups use filament_switch_sensor. Both report enabled/filament_detected.
_RE_FILAMENT_SENSOR = re.compile(r"^filament_(switch|motion)_sensor\s+\S+")
_RE_TEMP_SENSOR = re.compile(r"^temperature_sensor\s+\S+")
_RE_EXTRUDER = re.compile(r"^extruder\d*$")
# Chamber heaters such as the BIQU Panda Breath or DragonBreath, which the U1
# Extended Firmware exposes to Klipper as a heater_generic.
_RE_CHAMBER_HEATER = re.compile(r"^heater_generic\s+\S+")
# Their filtration blower, exposed as an output_pin (e.g. dragonbreath_filter).
_RE_CHAMBER_FILTER = re.compile(r"^output_pin\s+\S*(filter|breath)\S*$", re.IGNORECASE)

# Webcam name/location substrings that identify a touchscreen mirror rather
# than the printer's actual camera (see the remote-screen add-on's
# "[webcam gui]" Moonraker config entry).
_SCREEN_MIRROR_WEBCAM_TOKENS = ("gui", "screen")

# Hostnames that only make sense from the printer itself. Moonraker webcam
# entries sometimes use these, which would point Home Assistant at itself.
# Snapmaker's per-nozzle filament info (vendor/type/colour from RFID or the
# touchscreen). Only these fields are requested; the object is large.
FILAMENT_CONFIG_OBJECT = "print_task_config"
FILAMENT_CONFIG_FIELDS = (
    "filament_vendor",
    "filament_type",
    "filament_sub_type",
    "filament_color_rgba",
    "filament_exist",
    "filament_official",
)

# File-list handling. Moonraker reports each change with its details, so the
# cached list is updated from those; a full re-fetch is only done for bulk
# changes, at most this often. Some firmware (e.g. Extended Firmware) emits
# notify_filelist_changed in response to a list request, which turned an
# unconditional re-fetch into a busy loop (issue #28).
FILE_LIST_MIN_REFRESH_INTERVAL = 60  # seconds
# Bulk notifications arriving this soon after our own fetch are its echo.
FILE_LIST_ECHO_WINDOW = 5  # seconds
GCODE_EXTENSIONS = (".gcode", ".g", ".gco", ".ufp")

_LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1", "0.0.0.0")


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
        self._chamber_heater_keys: list[str] = []
        self._chamber_filter_keys: list[str] = []
        self._has_filament_config = False
        # Klipper objects that decide which entities exist, as seen at setup.
        self._entity_objects: frozenset[str] = frozenset()
        self._objects_changed_callbacks: list[Callable] = []
        self._ws_connected_once = False
        self._file_list_fetched_at: float | None = None
        self._file_list_refresh_task: asyncio.Task | None = None

        # Camera URLs – populated by webcam discovery; fall back to defaults.
        # Initialised to None here; set properly after base_url is available.
        self._camera_stream_url: str | None = None
        self._camera_snapshot_url: str | None = None
        self._webcam_info: dict[str, Any] | None = None

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
        return self._camera_stream_url or f"{self.base_url}{CAMERA_STREAM_PATH}"

    @property
    def camera_snapshot_url(self) -> str:
        return self._camera_snapshot_url or f"{self.base_url}{CAMERA_SNAPSHOT_PATH}"

    @property
    def webcam_info(self) -> dict[str, Any] | None:
        """Raw Moonraker entry of the discovered webcam, if any."""
        return self._webcam_info

    def resolve_url(self, url: str) -> str:
        """Turn a Moonraker-provided URL into one reachable from Home Assistant.

        Handles relative paths (``/webcam/?action=snapshot``, ``webcam/...``),
        protocol-relative URLs, and absolute URLs pointing at a loopback
        address, which only resolve on the printer itself.
        """
        resolved = urljoin(f"{self.base_url}/", url)
        parts = urlsplit(resolved)
        if parts.hostname in _LOOPBACK_HOSTS:
            netloc = self.host if parts.port is None else f"{self.host}:{parts.port}"
            resolved = urlunsplit(parts._replace(netloc=netloc))
        return resolved

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

    @property
    def has_filament_config(self) -> bool:
        """True when the printer reports per-nozzle filament info."""
        return self._has_filament_config

    @property
    def chamber_heater_keys(self) -> list[str]:
        """Discovered ``heater_generic`` objects (chamber heaters)."""
        return list(self._chamber_heater_keys)

    @property
    def chamber_filter_keys(self) -> list[str]:
        """Discovered chamber filter ``output_pin`` objects."""
        return list(self._chamber_filter_keys)

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
            await self._discover_webcams()
            await self._fetch_printer_state()
            await self.fetch_file_list()
            self._connected = True
            return True
        except Exception as exc:
            _LOGGER.warning("Failed to initialise Snapmaker U1 client: %s", exc)
            # Don't leak the session; Home Assistant will retry setup later.
            if self._own_session and self._session and not self._session.closed:
                await self._session.close()
            return False

    async def async_start(self) -> None:
        """Start the WebSocket connection loop."""
        self._should_reconnect = True
        self._ws_task = asyncio.create_task(self._ws_loop())

    async def async_stop(self) -> None:
        """Stop all connections and clean up."""
        self._should_reconnect = False
        if self._file_list_refresh_task and not self._file_list_refresh_task.done():
            self._file_list_refresh_task.cancel()
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
            self._chamber_heater_keys = [
                o for o in objects if _RE_CHAMBER_HEATER.match(o)
            ]
            self._chamber_filter_keys = [
                o for o in objects if _RE_CHAMBER_FILTER.match(o)
            ]
            self._has_filament_config = FILAMENT_CONFIG_OBJECT in objects
            self._entity_objects = self._entity_objects_from(objects)
            _LOGGER.debug(
                "Discovered filament sensors: %s; extra temp sensors: %s; "
                "chamber heaters: %s; chamber filters: %s",
                self._filament_sensor_keys,
                self._temp_sensor_keys,
                self._chamber_heater_keys,
                self._chamber_filter_keys,
            )
        except Exception as exc:
            _LOGGER.debug("Could not discover dynamic printer objects: %s", exc)
        await self._fetch_chamber_heater_limits()

    @staticmethod
    def _entity_objects_from(objects: list[str]) -> frozenset[str]:
        """The subset of Klipper objects that decides which entities exist."""
        return frozenset(
            o
            for o in objects
            if _RE_EXTRUDER.match(o)
            or _RE_FILAMENT_SENSOR.match(o)
            or _RE_TEMP_SENSOR.match(o)
            or _RE_CHAMBER_HEATER.match(o)
            or _RE_CHAMBER_FILTER.match(o)
            or o == FILAMENT_CONFIG_OBJECT
        )

    def register_objects_changed_callback(self, callback: Callable) -> None:
        """Call `callback` when the printer's Klipper objects differ from setup.

        Happens when Home Assistant set up while Klipper was still starting
        (so some heaters/sensors weren't listed yet), or after the printer's
        configuration changed. The integration reloads to pick them up.
        """
        self._objects_changed_callbacks.append(callback)

    async def _check_objects_changed(self) -> None:
        try:
            data = await self._get(ENDPOINT_PRINTER_OBJECTS_LIST)
            objects: list[str] = data.get("result", {}).get("objects", [])
        except Exception as exc:
            _LOGGER.debug("Could not re-check printer objects: %s", exc)
            return
        current = self._entity_objects_from(objects)
        # An empty list means Klipper isn't ready yet; wait for the next check.
        if not current or current == self._entity_objects:
            return
        _LOGGER.info(
            "Snapmaker U1 printer objects changed (added %s, removed %s); reloading",
            sorted(current - self._entity_objects),
            sorted(self._entity_objects - current),
        )
        self._entity_objects = current
        for cb in list(self._objects_changed_callbacks):
            try:
                cb()
            except Exception as exc:
                _LOGGER.debug("Error in objects-changed callback: %s", exc)

    async def _fetch_chamber_heater_limits(self) -> None:
        """Read each chamber heater's min_temp / max_temp from the Klipper config."""
        for key in self._chamber_heater_keys:
            self._data.chamber_heaters.setdefault(key, ChamberHeaterData())
        if not self._chamber_heater_keys:
            return
        try:
            data = await self._get(
                ENDPOINT_PRINTER_OBJECTS_QUERY, params={"configfile": "settings"}
            )
            settings = (
                data.get("result", {})
                .get("status", {})
                .get("configfile", {})
                .get("settings", {})
            )
            for key in self._chamber_heater_keys:
                section = settings.get(key.lower(), {})
                heater = self._data.chamber_heaters[key]
                if section.get("max_temp"):
                    heater.max_temp = float(section["max_temp"])
                if section.get("min_temp") is not None:
                    heater.min_temp = max(float(section["min_temp"]), 0.0)
        except Exception as exc:
            _LOGGER.debug("Could not read chamber heater limits: %s", exc)

    async def _discover_webcams(self) -> None:
        """Query /server/webcams/list and use the first real camera's URLs.

        Extended U1 firmware with the remote-screen add-on registers its own
        touchscreen mirror as a webcam (``[webcam gui]`` in Moonraker config,
        typically named "gui"). That entry can be returned before the real
        camera, so it gets excluded here rather than picked as the stream.
        """
        try:
            data = await self._get(ENDPOINT_WEBCAMS_LIST)
            webcams: list[dict] = data.get("result", {}).get("webcams", [])
            enabled = [w for w in webcams if w.get("enabled", True)]
            if not enabled:
                _LOGGER.debug("No enabled webcams found via /server/webcams/list")
                return
            real_cameras = [
                w
                for w in enabled
                if not any(
                    token in f"{w.get('name', '')} {w.get('location', '')}".lower()
                    for token in _SCREEN_MIRROR_WEBCAM_TOKENS
                )
            ]
            cam = (real_cameras or enabled)[0]
            self._webcam_info = cam
            stream_url: str = cam.get("stream_url", "")
            snapshot_url: str = cam.get("snapshot_url", "")
            # Moonraker may return relative paths (e.g. "/webcam/?action=stream")
            # or full URLs, sometimes pointing at localhost on the printer.
            if stream_url:
                self._camera_stream_url = self.resolve_url(stream_url)
            if snapshot_url:
                self._camera_snapshot_url = self.resolve_url(snapshot_url)
            _LOGGER.debug(
                "Discovered webcam '%s': stream=%s snapshot=%s",
                cam.get("name", "unknown"),
                self._camera_stream_url,
                self._camera_snapshot_url,
            )
        except Exception as exc:
            _LOGGER.debug("Could not discover webcams: %s", exc)

    def _object_fields(self) -> dict[str, list[str] | None]:
        """Objects to subscribe/query, with the fields wanted (None = all)."""
        fields: dict[str, list[str] | None] = dict.fromkeys(self._all_objects())
        if self._has_filament_config:
            fields[FILAMENT_CONFIG_OBJECT] = list(FILAMENT_CONFIG_FIELDS)
        return fields

    def _all_objects(self) -> list[str]:
        """Return the full list of objects to subscribe/query."""
        return (
            PRINTER_OBJECTS
            + self._filament_sensor_keys
            + self._temp_sensor_keys
            + self._chamber_heater_keys
            + self._chamber_filter_keys
        )

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
            if self._ws_connected_once:
                # Reconnected (e.g. the printer was switched off and on):
                # refresh state and pick up objects that weren't there before.
                await self._check_objects_changed()
                await self.fetch_file_list()
            self._ws_connected_once = True

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
        objects_param = self._object_fields()
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
            # Klipper restarted: resubscribe (Moonraker drops subscriptions) and
            # check whether new heaters/sensors appeared.
            await self._subscribe_objects()
            await self._check_objects_changed()

        elif method in (WS_NOTIFY_KLIPPY_SHUTDOWN, WS_NOTIFY_KLIPPY_DISCONNECTED):
            self._data.klipper_state = KLIPPER_SHUTDOWN
            self._data.klipper_message = "Klipper disconnected"
            await self._notify_callbacks()

        elif method == WS_NOTIFY_FILELIST_CHANGED:
            # A file was uploaded, deleted or moved on the printer
            if await self._apply_file_list_change(params):
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
            if "extruder" in th:
                self._data.toolhead.active_extruder = th["extruder"]
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

        if "led cavity_led" in status:
            # Klipper reports color_data as [[R, G, B, W]]. The U1 drives the
            # cavity lamp on the WHITE channel alone, so a lit chamber reads
            # [0.0, 0.0, 0.0, 1.0] – any channel above zero counts as on.
            color_data = status["led cavity_led"].get("color_data")
            if color_data:
                self._data.work_light_on = any(
                    (value or 0) > 0 for channel in color_data for value in channel
                )
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

        if FILAMENT_CONFIG_OBJECT in status:
            self._update_filament_slots(status[FILAMENT_CONFIG_OBJECT])
            changed = True

        # Chamber heaters (Panda Breath / DragonBreath via heater_generic)
        for key in self._chamber_heater_keys:
            if key in status:
                hs = status[key]
                heater = self._data.chamber_heaters.setdefault(key, ChamberHeaterData())
                if "temperature" in hs:
                    heater.temperature = round(hs["temperature"], 1)
                if "target" in hs:
                    heater.target = round(hs["target"], 1)
                if "power" in hs:
                    heater.power = hs["power"]
                changed = True

        for key in self._chamber_filter_keys:
            if key in status and "value" in status[key]:
                self._data.chamber_filters[key] = (status[key]["value"] or 0) > 0
                changed = True

        if changed:
            await self._notify_callbacks()

    def _update_filament_slots(self, config: dict[str, Any]) -> None:
        """Merge a (possibly partial) print_task_config update into the slots."""

        def known(value: Any) -> str | None:
            # Snapmaker writes the literal "NONE" for unknown values.
            if not isinstance(value, str) or not value.strip() or value.upper() == "NONE":
                return None
            return value.strip()

        for i in range(4):
            slot = self._data.filament_slots.setdefault(i, FilamentSlot())

            def item(field_name: str):
                values = config.get(field_name)
                return values[i] if isinstance(values, list) and i < len(values) else ...

            if (v := item("filament_vendor")) is not ...:
                slot.vendor = known(v)
            if (v := item("filament_type")) is not ...:
                slot.material = known(v)
            if (v := item("filament_sub_type")) is not ...:
                slot.sub_type = known(v)
            if (v := item("filament_color_rgba")) is not ...:
                rgba = str(v or "").strip().lstrip("#")
                if re.fullmatch(r"[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?", rgba):
                    slot.color = f"#{rgba[:6].upper()}"
                    slot.alpha = int(rgba[6:8], 16) if len(rgba) == 8 else 255
                else:
                    slot.color = None
            if (v := item("filament_exist")) is not ...:
                slot.loaded = bool(v)
            if (v := item("filament_official")) is not ...:
                slot.official = bool(v)

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
        params = {
            obj: ",".join(fields) if fields else ""
            for obj, fields in self._object_fields().items()
        }
        data = await self._get(ENDPOINT_PRINTER_OBJECTS_QUERY, params=params)
        status = data.get("result", {}).get("status", {})
        await self._process_status(status)

    async def fetch_state(self) -> None:
        """Public wrapper – refresh printer state via HTTP."""
        await self._fetch_printer_state()

    async def _apply_file_list_change(self, params: Any) -> bool:
        """Update the cached file list from a notify_filelist_changed payload.

        Returns True when the list changed. Never re-fetches the whole list for
        single-file events, and only schedules a rate-limited refresh for bulk
        events (root_update, move_dir, unknown actions).
        """
        info = params[0] if isinstance(params, list) and params and isinstance(params[0], dict) else {}
        action = info.get("action", "")
        item = info.get("item") if isinstance(info.get("item"), dict) else {}
        source = info.get("source_item") if isinstance(info.get("source_item"), dict) else {}
        if item.get("root", "gcodes") != "gcodes" and source.get("root", "gcodes") != "gcodes":
            return False  # config files, logs, timelapses, ...
        path = item.get("path") if isinstance(item.get("path"), str) else ""

        def is_gcode(name: str) -> bool:
            return bool(name) and name.lower().endswith(GCODE_EXTENSIONS)

        files = list(self._data.available_files)
        if action in ("create_file", "modify_file"):
            if not is_gcode(path):
                return False  # thumbnails, metadata, ...
            if path in files:
                files.remove(path)
            files.insert(0, path)  # newest first, like Moonraker's list
        elif action == "delete_file":
            if path not in files:
                return False
            files.remove(path)
        elif action == "move_file":
            src = source.get("path") if isinstance(source.get("path"), str) else ""
            if src in files:
                files.remove(src)
            if is_gcode(path) and item.get("root", "gcodes") == "gcodes":
                if path in files:
                    files.remove(path)
                files.insert(0, path)
        elif action == "create_dir":
            return False
        elif action == "delete_dir":
            prefix = path.rstrip("/") + "/"
            files = [f for f in files if not f.startswith(prefix)]
        else:
            # root_update, move_dir or something new: refresh the full list,
            # unless this is just the echo of our own request.
            if (
                self._file_list_fetched_at is not None
                and time.monotonic() - self._file_list_fetched_at < FILE_LIST_ECHO_WINDOW
            ):
                return False
            self._schedule_file_list_refresh()
            return False

        if files == self._data.available_files:
            return False
        self._data.available_files = files
        return True

    def _schedule_file_list_refresh(self) -> None:
        """Re-fetch the file list once, no sooner than the minimum interval."""
        if self._file_list_refresh_task and not self._file_list_refresh_task.done():
            return
        delay = 0.0
        if self._file_list_fetched_at is not None:
            delay = max(
                0.0,
                self._file_list_fetched_at + FILE_LIST_MIN_REFRESH_INTERVAL - time.monotonic(),
            )

        async def refresh() -> None:
            await asyncio.sleep(delay)
            await self.fetch_file_list()
            await self._notify_callbacks()

        self._file_list_refresh_task = asyncio.create_task(refresh())

    async def fetch_file_list(self) -> None:
        """Fetch available G-code files and update the data model."""
        self._file_list_fetched_at = time.monotonic()
        try:
            data = await self._get(ENDPOINT_FILES_LIST)
            files = data.get("result", [])
            # Moonraker reports each file's name as "path" (older releases
            # used "filename") and already sorts newest first; keep that order.
            # Don't drop dot-paths: the U1 lists USB-drive files under
            # ".udisk/", and Moonraker already filters reserved directories.
            names = []
            for f in files:
                if not isinstance(f, dict):
                    continue
                name = f.get("path") or f.get("filename")
                if isinstance(name, str) and name:
                    names.append(name)
            self._data.available_files = names
            # Count the echo window from when the list arrived, not when asked.
            self._file_list_fetched_at = time.monotonic()
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
        """Toggle the cavity/work light.

        M355 is a Marlin case-light command and is a no-op on the U1's
        Klipper firmware; the light is actually the cavity_led RGBW LED,
        controlled via SET_LED.
        """
        level = "1" if on else "0"
        await self.execute_gcode(
            f"SET_LED LED=cavity_led RED={level} GREEN={level} BLUE={level} WHITE={level}"
        )

    async def set_chamber_heater_temperature(self, heater_key: str, temp: float) -> None:
        """Set a chamber heater target; 0 turns it off.

        ``heater_key`` is the Klipper object name, e.g. "heater_generic panda_breath".
        Klipper rejects non-zero targets outside min_temp..max_temp, so those
        raise ValueError here instead of being sent.
        """
        heater = self._data.chamber_heaters.get(heater_key)
        if heater is not None and temp and not heater.min_temp <= temp <= heater.max_temp:
            raise ValueError(
                f"Target must be 0 (off) or between {heater.min_temp:g} and "
                f"{heater.max_temp:g} °C"
            )
        name = heater_key.split(" ", 1)[-1]
        await self.execute_gcode(f"SET_HEATER_TEMPERATURE HEATER={name} TARGET={temp:g}")

    async def set_chamber_filter(self, pin_key: str, on: bool) -> None:
        """Turn a chamber filter output_pin on or off (e.g. DragonBreath filter)."""
        name = pin_key.split(" ", 1)[-1]
        await self.execute_gcode(f"SET_PIN PIN={name} VALUE={1 if on else 0}")

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
