"""Async Moonraker API client for the Snapmaker U1."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Callable, Optional
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

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
    ENDPOINT_SPOOLMAN_SPOOL_ID,
    ENDPOINT_SPOOLMAN_STATUS,
    ENDPOINT_TIMELAPSE_SETTINGS,
    ENDPOINT_WEBCAMS_LIST,
    EXCLUDE_OBJECT,
    KLIPPER_READY,
    KLIPPER_SHUTDOWN,
    PRINTER_OBJECTS,
    PURIFIER_OBJECT,
    RTSP_PORT_INTERNAL,
    RTSP_PORT_USB,
    WS_ENDPOINT,
    WS_METHOD_SUBSCRIBE,
    WS_NOTIFY_ACTIVE_SPOOL_SET,
    WS_NOTIFY_FILELIST_CHANGED,
    WS_NOTIFY_HISTORY_CHANGED,
    WS_NOTIFY_KLIPPY_DISCONNECTED,
    WS_NOTIFY_KLIPPY_READY,
    WS_NOTIFY_KLIPPY_SHUTDOWN,
    WS_NOTIFY_SPOOLMAN_STATUS,
    WS_NOTIFY_STATUS_UPDATE,
    WS_NOTIFY_TIMELAPSE_EVENT,
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
    ExcludeObjectData,
    TopCoverData,
    Webcam,
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
        self._has_purifier = False
        # Whether the Top Cover was plugged in at setup (None until known).
        self._top_cover_at_setup: bool | None = None
        # Klipper objects that decide which entities exist, as seen at setup.
        self._entity_objects: frozenset[str] = frozenset()
        self._objects_changed_callbacks: list[Callable] = []
        self._disconnect_callbacks: list[Callable] = []
        self._ws_connected_once = False
        # Set once a WebSocket connection is up and subscribed.
        self._ws_session_established = False
        self._file_list_fetched_at: float | None = None
        self._file_list_refresh_task: asyncio.Task | None = None

        # Camera URLs – populated by webcam discovery; fall back to defaults.
        # Initialised to None here; set properly after base_url is available.
        self._camera_stream_url: str | None = None
        self._camera_snapshot_url: str | None = None
        self._webcam_info: dict[str, Any] | None = None
        self._webcams: list[Webcam] = []
        # Remote screen page (Extended Firmware), if the printer offers one
        self._screen_url: str | None = None
        # Optional Moonraker components (e.g. "spoolman", "timelapse")
        self._components: set[str] = set()
        self._has_exclude_object = False

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
    def webcams(self) -> list[Webcam]:
        """Every camera the printer lists; the first is the internal one."""
        if self._webcams:
            return list(self._webcams)
        return [
            Webcam(
                name="Webcam",
                snapshot_url=self.camera_snapshot_url,
                stream_url=self.camera_stream_url,
                primary=True,
            )
        ]

    @property
    def screen_url(self) -> str | None:
        """Remote screen page (Extended Firmware), if enabled on the printer."""
        return self._screen_url

    @property
    def has_spoolman(self) -> bool:
        return "spoolman" in self._components

    @property
    def has_timelapse(self) -> bool:
        return "timelapse" in self._components

    @property
    def has_exclude_object(self) -> bool:
        return self._has_exclude_object

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

    @property
    def has_top_cover(self) -> bool:
        """True when the official Top Cover is plugged in (seen at setup)."""
        return bool(self._top_cover_at_setup)

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
            await self._fetch_components()
            await self._discover_dynamic_objects()
            await self._discover_webcams()
            await self._fetch_printer_state()
            await self.fetch_file_list()
            await self._fetch_component_state()
            self._top_cover_at_setup = bool(
                self._data.top_cover and self._data.top_cover.detected
            )
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
            self._has_purifier = PURIFIER_OBJECT in objects
            self._has_exclude_object = EXCLUDE_OBJECT in objects
            if self._has_exclude_object and self._data.exclude_object is None:
                self._data.exclude_object = ExcludeObjectData()
            if self._has_purifier and self._data.top_cover is None:
                self._data.top_cover = TopCoverData()
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
            or o == PURIFIER_OBJECT
            or o == EXCLUDE_OBJECT
        )

    def register_disconnect_callback(self, callback: Callable) -> None:
        """Call `callback` (no arguments) when the WebSocket connection drops."""
        self._disconnect_callbacks.append(callback)

    def _on_disconnected(self) -> None:
        """Mark the WebSocket down, fail pending requests and tell listeners."""
        was_connected = self._connected
        self._ws = None
        self._connected = False
        for future in self._pending_requests.values():
            if not future.done():
                future.cancel()
        self._pending_requests.clear()
        if not was_connected or not self._should_reconnect:  # not on shutdown
            return
        for cb in list(self._disconnect_callbacks):
            try:
                cb()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("Error in disconnect callback: %s", exc)

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
        self._fire_objects_changed()

    def _fire_objects_changed(self) -> None:
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
        """Query /server/webcams/list for the printer's cameras.

        Every real camera becomes a camera entity; the first is the internal
        one. Extended U1 firmware with the remote-screen add-on registers its
        own touchscreen mirror as a webcam (``[webcam gui]`` in Moonraker
        config, typically named "gui"). That's not a camera: it's offered as
        the remote screen page instead.
        """
        try:
            data = await self._get(ENDPOINT_WEBCAMS_LIST)
            webcams: list[dict] = data.get("result", {}).get("webcams", [])
            enabled = [w for w in webcams if w.get("enabled", True)]
            if not enabled:
                _LOGGER.debug("No enabled webcams found via /server/webcams/list")
                return
            real_cameras = []
            for w in enabled:
                label = f"{w.get('name', '')} {w.get('location', '')}".lower()
                if any(token in label for token in _SCREEN_MIRROR_WEBCAM_TOKENS):
                    self._screen_url = f"{self.base_url}/screen/"
                else:
                    real_cameras.append(w)
            cameras = real_cameras or enabled
            cam = cameras[0]
            self._webcam_info = cam
            stream_url: str = cam.get("stream_url", "")
            snapshot_url: str = cam.get("snapshot_url", "")
            # Moonraker may return relative paths (e.g. "/webcam/?action=stream")
            # or full URLs, sometimes pointing at localhost on the printer.
            if stream_url:
                self._camera_stream_url = self.resolve_url(stream_url)
            if snapshot_url:
                self._camera_snapshot_url = self.resolve_url(snapshot_url)
            self._webcams = [
                Webcam(
                    name=str(w.get("name") or f"Camera {i + 1}"),
                    # Only the camera's own URLs: falling back to the default
                    # /webcam/ paths would show the internal camera instead.
                    snapshot_url=(
                        self.resolve_url(w["snapshot_url"]) if w.get("snapshot_url") else ""
                    ),
                    stream_url=(
                        self.resolve_url(w["stream_url"]) if w.get("stream_url") else ""
                    ),
                    primary=i == 0,
                )
                for i, w in enumerate(cameras)
            ]
            await self._discover_rtsp()
            _LOGGER.debug(
                "Discovered webcams: %s (remote screen: %s)",
                [(w.name, w.snapshot_url, w.stream_url, w.rtsp_url) for w in self._webcams],
                self._screen_url,
            )
        except Exception as exc:
            _LOGGER.debug("Could not discover webcams: %s", exc)

    async def _discover_rtsp(self) -> None:
        """Find the Extended Firmware's RTSP streams (off by default).

        Its camera service serves the internal camera on port 8554 and a USB
        camera (``/webcam2/``) on 8555 when RTSP is enabled in Firmware Config.
        """
        for cam in self._webcams:
            usb = "/webcam2" in f"{cam.snapshot_url} {cam.stream_url}"
            if not usb and not cam.primary:
                continue
            port = RTSP_PORT_USB if usb else RTSP_PORT_INTERNAL
            if await self._port_open(port):
                cam.rtsp_url = f"rtsp://{self.host}:{port}/stream"

    async def _port_open(self, port: int, timeout: float = 2) -> bool:
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, port), timeout
            )
        except (OSError, asyncio.TimeoutError):
            return False
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        return True

    async def _fetch_components(self) -> None:
        """Note which optional Moonraker components are loaded."""
        try:
            data = await self._get(ENDPOINT_SERVER_INFO)
            self._components = set(data.get("result", {}).get("components", []))
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Could not read Moonraker components: %s", exc)

    async def _fetch_component_state(self) -> None:
        """Spoolman active spool and timelapse settings, if available."""
        if self.has_spoolman:
            try:
                data = await self._get(ENDPOINT_SPOOLMAN_SPOOL_ID)
                self._data.active_spool_id = data.get("result", {}).get("spool_id") or None
                status = await self._get(ENDPOINT_SPOOLMAN_STATUS)
                connected = status.get("result", {}).get("spoolman_connected")
                if connected is not None:
                    self._data.spoolman_connected = bool(connected)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("Could not read the Spoolman state: %s", exc)
        if self.has_timelapse:
            try:
                data = await self._get(ENDPOINT_TIMELAPSE_SETTINGS)
                enabled = data.get("result", {}).get("enabled")
                if enabled is not None:
                    self._data.timelapse_enabled = bool(enabled)
                files = await self._get(ENDPOINT_FILES_LIST, params={"root": "timelapse"})
                videos = [
                    f for f in files.get("result", [])
                    if isinstance(f, dict)
                    and str(f.get("path") or f.get("filename") or "").endswith((".mp4", ".mkv"))
                ]
                if videos:
                    newest = max(videos, key=lambda f: f.get("modified") or 0)
                    self._data.last_timelapse = newest.get("path") or newest.get("filename")
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("Could not read the timelapse state: %s", exc)

    def timelapse_url(self, filename: str) -> str:
        """Download URL of a file in Moonraker's "timelapse" root."""
        return f"{self.base_url}/server/files/timelapse/{quote(filename)}"

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
            + ([PURIFIER_OBJECT] if self._has_purifier else [])
            + ([EXCLUDE_OBJECT] if self._has_exclude_object else [])
        )

    # ------------------------------------------------------------------
    # WebSocket connection management
    # ------------------------------------------------------------------

    async def _ws_loop(self) -> None:
        """Main WebSocket reconnection loop."""
        delay = self._reconnect_delay
        while self._should_reconnect:
            self._ws_session_established = False
            try:
                await self._ws_connect()
                reason = "closed by the printer"
            except asyncio.CancelledError:
                self._on_disconnected()
                break
            except Exception as exc:  # noqa: BLE001
                reason = str(exc) or type(exc).__name__
            self._on_disconnected()
            if not self._should_reconnect:
                break
            # Back off only while connecting keeps failing; a connection that
            # was up starts again from the shortest delay. Always wait, so a
            # printer that accepts and immediately closes the socket can't
            # cause a tight reconnect loop.
            if self._ws_session_established:
                delay = self._reconnect_delay
            _LOGGER.warning(
                "WebSocket connection lost (%s). Reconnecting in %ds.", reason, delay
            )
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                break
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
            self._ws_session_established = True
            if self._ws_connected_once:
                # Reconnected (e.g. the printer was switched off and on):
                # refresh state and pick up objects that weren't there before.
                await self._check_objects_changed()
                await self.fetch_file_list()
                await self._fetch_component_state()
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

        elif method == WS_NOTIFY_ACTIVE_SPOOL_SET:
            info = params[0] if params and isinstance(params[0], dict) else {}
            self._data.active_spool_id = info.get("spool_id") or None
            await self._notify_callbacks()

        elif method == WS_NOTIFY_SPOOLMAN_STATUS:
            info = params[0] if params and isinstance(params[0], dict) else {}
            if "spoolman_connected" in info:
                self._data.spoolman_connected = bool(info["spoolman_connected"])
                await self._notify_callbacks()

        elif method == WS_NOTIFY_TIMELAPSE_EVENT:
            info = params[0] if params and isinstance(params[0], dict) else {}
            if (
                info.get("action") == "render"
                and info.get("status") == "success"
                and info.get("filename")
            ):
                self._data.last_timelapse = str(info["filename"])
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

        if EXCLUDE_OBJECT in status and self._data.exclude_object is not None:
            eo = status[EXCLUDE_OBJECT]
            target = self._data.exclude_object
            if isinstance(eo.get("objects"), list):
                target.objects = [
                    str(o.get("name")) for o in eo["objects"] if isinstance(o, dict) and o.get("name")
                ]
            if isinstance(eo.get("excluded_objects"), list):
                target.excluded = [str(o) for o in eo["excluded_objects"]]
            if "current_object" in eo:
                target.current = eo["current_object"] or None
            changed = True

        if PURIFIER_OBJECT in status and self._data.top_cover is not None:
            self._update_top_cover(status[PURIFIER_OBJECT])
            changed = True

        if changed:
            await self._notify_callbacks()

    def _update_top_cover(self, ps: dict[str, Any]) -> None:
        """Merge a (possibly partial) purifier status update."""
        cover = self._data.top_cover
        if "power_detected" in ps:
            cover.detected = bool(ps["power_detected"])
            if (
                self._top_cover_at_setup is not None
                and cover.detected != self._top_cover_at_setup
            ):
                # Cover plugged in or removed: reload to add/remove its entities.
                _LOGGER.info(
                    "Snapmaker U1 Top Cover %s; reloading",
                    "connected" if cover.detected else "disconnected",
                )
                self._top_cover_at_setup = cover.detected
                self._fire_objects_changed()
        if "mode" in ps:
            cover.mode = int(ps["mode"] or 0)
        if "desired_chamber_temp" in ps:
            cover.desired_temp = ps["desired_chamber_temp"] or 0.0
        if "inner_fan_work_time" in ps:
            cover.filter_seconds = ps["inner_fan_work_time"] or 0.0
        if "inner_fan_rpm" in ps:
            cover.circulation_rpm = ps["inner_fan_rpm"]
        for field_name, speed_attr, delay_attr in (
            ("exhaust_fan", "exhaust_speed", "exhaust_delay"),
            ("inner_fan", "circulation_speed", "circulation_delay"),
        ):
            fan = ps.get(field_name)
            if isinstance(fan, dict):
                if "speed" in fan:
                    setattr(cover, speed_attr, fan["speed"] or 0.0)
                if "delay" in fan:
                    setattr(cover, delay_attr, int(fan["delay"] or 0))

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

        Returns True when the list changed. File and directory events are
        applied from the payload; only root_update (or an unknown action)
        schedules a rate-limited full refresh.
        """
        info = params[0] if isinstance(params, list) and params and isinstance(params[0], dict) else {}
        action = info.get("action", "")
        item = info.get("item") if isinstance(info.get("item"), dict) else {}
        source = info.get("source_item") if isinstance(info.get("source_item"), dict) else {}
        item_in_gcodes = item.get("root", "gcodes") == "gcodes"
        source_in_gcodes = bool(source) and source.get("root", "gcodes") == "gcodes"
        path = item.get("path") if isinstance(item.get("path"), str) else ""
        src = source.get("path") if isinstance(source.get("path"), str) else ""

        def is_gcode(name: str) -> bool:
            return bool(name) and name.lower().endswith(GCODE_EXTENSIONS)

        files = list(self._data.available_files)
        if action in ("move_file", "move_dir"):
            # Moves can cross roots: drop the source if it was in gcodes, add
            # the destination if it lands in gcodes.
            if not item_in_gcodes and not source_in_gcodes:
                return False
            if action == "move_file":
                if source_in_gcodes and src in files:
                    files.remove(src)
                if item_in_gcodes and is_gcode(path):
                    if path in files:
                        files.remove(path)
                    files.insert(0, path)
            else:
                if item_in_gcodes and not source_in_gcodes:
                    # A folder arrived from another root; the notice doesn't
                    # list its files, so fetch the list (rate-limited).
                    self._schedule_file_list_refresh()
                    return False
                old_prefix = src.rstrip("/") + "/"
                new_prefix = path.rstrip("/") + "/"
                moved = []
                for f in files:
                    if source_in_gcodes and f.startswith(old_prefix):
                        if item_in_gcodes:
                            moved.append(new_prefix + f[len(old_prefix):])
                        continue  # moved out of gcodes, or renamed below
                    moved.append(f)
                files = moved
        elif not item_in_gcodes:
            return False  # config files, logs, timelapses, ...
        elif action in ("create_file", "modify_file"):
            if not is_gcode(path):
                return False  # thumbnails, metadata, ...
            if path in files:
                files.remove(path)
            files.insert(0, path)  # newest first, like Moonraker's list
        elif action == "delete_file":
            if path not in files:
                return False
            files.remove(path)
        elif action == "create_dir":
            return False
        elif action == "delete_dir":
            prefix = path.rstrip("/") + "/"
            files = [f for f in files if not f.startswith(prefix)]
        else:
            # root_update or something new: refresh the full list, unless
            # this is just the echo of our own request.
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

    async def set_top_cover_mode(self, mode: int) -> None:
        """Switch the Top Cover purifier mode (same settings as Snapmaker's slicer)."""
        commands = {
            0: "SET_PURIFIER_MODE MODE=0",
            # Cooling: exhaust fan, speeds up above 42 °C (PLA/PETG/TPU)
            1: "SET_PURIFIER_MODE MODE=1 DESIRE_TEMP=42 ALARM_TEMP=48 FAN_SPEED=0.6",
            # Preheat / keep warm: circulate through the filter, no exhaust
            2: "SET_PURIFIER_MODE MODE=2 FAN_SPEED=0.6",
            3: "SET_PURIFIER_MODE MODE=3 DESIRE_TEMP=45 FAN_SPEED=0.6",
        }
        if mode not in commands:
            raise ValueError(f"Unknown Top Cover mode {mode}")
        await self.execute_gcode(commands[mode])

    async def set_top_cover_fan(self, fan: str, percent: float) -> None:
        """Set the Top Cover exhaust or circulation fan (0–100 %).

        ``fan`` is "exhaust" or "inner". The firmware normally keeps a fan
        running for its delay-off time after it is set to 0; switching it off
        from Home Assistant should be immediate, so the delay is cleared for
        that command and restored afterwards.
        """
        if fan not in ("exhaust", "inner"):
            raise ValueError(f"Unknown Top Cover fan {fan}")
        percent = max(0.0, min(100.0, percent))
        if percent > 0:
            await self.execute_gcode(
                f"SET_PURIFIER FAN={fan} SPEED={percent / 100:.2f}"
            )
            return
        cover = self._data.top_cover
        delay = 0
        if cover is not None:
            delay = cover.exhaust_delay if fan == "exhaust" else cover.circulation_delay
        script = f"SET_PURIFIER FAN={fan} SPEED=0 DELAY_OFF=0"
        if delay:
            script += f"\nSET_PURIFIER FAN={fan} DELAY_OFF={delay}"
        await self.execute_gcode(script)

    async def reset_top_cover_filter(self) -> None:
        """Reset the filter usage counter after fitting a new filter."""
        await self.execute_gcode("SET_PURIFIER FAN=inner WORK=0")

    async def exclude_object(self, name: str) -> None:
        """Stop printing one labelled object of the current print."""
        objects = self._data.exclude_object
        if objects is None or name not in objects.objects:
            raise ValueError(f"No object named {name!r} in the current print")
        if any(ch.isspace() for ch in name):
            raise ValueError(f"Object name {name!r} contains spaces")
        await self.execute_gcode(f"EXCLUDE_OBJECT NAME={name}")

    async def set_active_spool(self, spool_id: int | None) -> None:
        """Set (or with None/0 clear) Moonraker's active Spoolman spool."""
        body = {"spool_id": int(spool_id)} if spool_id else {}
        await self._post(ENDPOINT_SPOOLMAN_SPOOL_ID, body)
        self._data.active_spool_id = int(spool_id) if spool_id else None
        await self._notify_callbacks()

    async def set_timelapse_enabled(self, enabled: bool) -> None:
        """Turn the Moonraker timelapse plugin on or off."""
        data = await self._post(ENDPOINT_TIMELAPSE_SETTINGS, {"enabled": enabled})
        result = data.get("result") if isinstance(data, dict) else None
        self._data.timelapse_enabled = (
            bool(result["enabled"]) if isinstance(result, dict) and "enabled" in result else enabled
        )
        await self._notify_callbacks()

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
