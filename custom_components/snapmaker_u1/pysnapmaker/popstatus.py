"""Client for the BIQU PopStatus status light bar.

The PopStatus is a separate Wi-Fi device: it reads the printer's status from
Moonraker on its own and has a web UI that talks to it over a WebSocket at
``ws://<popstatus>/ws``. Messages are JSON objects keyed by a section name,
e.g. ``{"rgb_switch": {"total_switch": 1}}``. The device pushes its settings
when a client connects; this client keeps them and sends the same commands
as the web UI (PopStatus firmware 1.0.0).
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

import aiohttp

_LOGGER = logging.getLogger(__name__)

# Light modes as named in the PopStatus web UI
POPSTATUS_LIGHT_MODES = {0: "simple", 1: "advanced"}
# printer.state as reported by the PopStatus
POPSTATUS_PRINTER_CONNECTED = 3


@dataclass
class PopStatusData:
    """Last known PopStatus state (None until the device reports it)."""

    connected: bool = False
    light_on: Optional[bool] = None
    light_mode: Optional[int] = None  # 0 simple, 1 advanced
    brightness: Optional[int] = None  # simple mode brightness, 0–100
    printer_state: Optional[int] = None  # 3 = connected to the printer
    printer_host: str = ""

    @property
    def printer_linked(self) -> bool:
        return self.printer_state == POPSTATUS_PRINTER_CONNECTED


class PopStatusClient:
    """Keeps a WebSocket open to a PopStatus and mirrors its light settings."""

    def __init__(
        self,
        host: str,
        session: aiohttp.ClientSession,
        reconnect_delay: float = 5,
        max_reconnect_delay: float = 60,
    ) -> None:
        self.host = host.strip().rstrip("/")
        self._session = session
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._reconnect_delay = reconnect_delay
        self._max_reconnect_delay = max_reconnect_delay
        self._callbacks: list[Callable[[], None]] = []
        self.data = PopStatusData()

    @property
    def ws_url(self) -> str:
        host = self.host
        for prefix in ("http://", "ws://"):
            if host.startswith(prefix):
                host = host[len(prefix):]
        return f"ws://{host}/ws"

    @classmethod
    async def test_connection(
        cls, host: str, session: aiohttp.ClientSession, timeout: float = 8
    ) -> bool:
        """Return True if a PopStatus answers at ``host``."""
        url = cls(host, session).ws_url
        try:
            async with session.ws_connect(
                url, timeout=aiohttp.ClientWSTimeout(ws_close=timeout)
            ) as ws:
                # The device sends its settings straight after connecting.
                msg = await ws.receive(timeout=timeout)
                return msg.type == aiohttp.WSMsgType.TEXT
        except Exception:  # noqa: BLE001
            return False

    def register_callback(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Call ``callback`` on every change; returns a function that removes it."""
        self._callbacks.append(callback)

        def remove() -> None:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

        return remove

    def _notify(self) -> None:
        for cb in list(self._callbacks):
            try:
                cb()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("Error in PopStatus callback: %s", exc)

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        delay = self._reconnect_delay
        while self._running:
            was_up = False
            try:
                async with self._session.ws_connect(self.ws_url, heartbeat=20) as ws:
                    self._ws = ws
                    self.data.connected = was_up = True
                    _LOGGER.info("Connected to PopStatus at %s", self.host)
                    self._notify()
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                self._handle(json.loads(msg.data))
                            except (ValueError, TypeError, AttributeError) as exc:
                                _LOGGER.debug("Bad PopStatus message %r: %s", msg.data, exc)
                        elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                            break
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("PopStatus connection failed (%s): %s", self.host, exc)
            finally:
                self._ws = None
                if self.data.connected:
                    self.data.connected = False
                    self._notify()
            if not self._running:
                break
            if was_up:
                delay = self._reconnect_delay
            await asyncio.sleep(delay)
            delay = min(delay * 2, self._max_reconnect_delay)

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    def _handle(self, msg: dict[str, Any]) -> None:
        changed = False
        rgb_mode = msg.get("rgb_mode")
        if isinstance(rgb_mode, dict):
            if isinstance(rgb_mode.get("light_on_off"), bool):
                self.data.light_on = rgb_mode["light_on_off"]
                changed = True
            if isinstance(rgb_mode.get("rgb_light_mode"), int):
                self.data.light_mode = rgb_mode["rgb_light_mode"]
                changed = True
            simple = rgb_mode.get("simple_mode")
            if isinstance(simple, dict) and isinstance(simple.get("bg"), (int, float)):
                self.data.brightness = int(simple["bg"])
                changed = True
        rgb_switch = msg.get("rgb_switch")
        if isinstance(rgb_switch, dict):
            if "total_switch" in rgb_switch:
                self.data.light_on = bool(rgb_switch["total_switch"])
                changed = True
            if isinstance(rgb_switch.get("current_light_mode"), int):
                self.data.light_mode = rgb_switch["current_light_mode"]
                changed = True
        printer = msg.get("printer")
        if isinstance(printer, dict):
            if isinstance(printer.get("state"), int):
                self.data.printer_state = printer["state"]
                changed = True
            if printer.get("host"):
                self.data.printer_host = str(printer["host"])
                changed = True
        if changed:
            self._notify()

    async def _send(self, section: str, members: dict[str, Any]) -> None:
        if self._ws is None or self._ws.closed:
            raise ConnectionError(f"PopStatus at {self.host} is not connected")
        await self._ws.send_str(json.dumps({section: members}))

    async def set_light(self, on: bool) -> None:
        """Turn the light bar on or off."""
        await self._send("rgb_switch", {"total_switch": 1 if on else 0})
        # The device doesn't always echo the change; reflect it right away.
        self.data.light_on = on
        self._notify()

    async def set_brightness(self, percent: int) -> None:
        """Set the Simple mode brightness (0–100 %)."""
        percent = max(0, min(100, int(percent)))
        await self._send("rgb_mode", {"simple_mode": {"bg": percent}})
        self.data.brightness = percent
        self._notify()

    async def set_light_mode(self, mode: int) -> None:
        """Switch between Simple (0) and Advanced (1) light mode."""
        if mode not in POPSTATUS_LIGHT_MODES:
            raise ValueError(f"Unknown PopStatus light mode {mode}")
        await self._send("rgb_switch", {"current_light_mode": mode})
        self.data.light_mode = mode
        self._notify()
