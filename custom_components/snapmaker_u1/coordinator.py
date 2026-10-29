"""Data coordinator for the Snapmaker U1 integration."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_API_KEY,
    CONF_HOST,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .pysnapmaker.client import SnapmakerClient
from .pysnapmaker.const import PRINT_STATE_EVENTS
from .pysnapmaker.models import SnapmakerPrinterData

_LOGGER = logging.getLogger(__name__)

# Fallback polling interval used when the WebSocket connection drops
_FALLBACK_POLL_INTERVAL = timedelta(seconds=30)


class SnapmakerDataUpdateCoordinator(DataUpdateCoordinator[SnapmakerPrinterData]):
    """Manages fetching Snapmaker U1 state, preferring WebSocket push updates."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        scan_interval = timedelta(
            seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=scan_interval,
        )
        self.entry = entry
        self._client: SnapmakerClient | None = None
        self._last_print_state: str | None = None

    # ------------------------------------------------------------------
    # Setup / teardown
    # ------------------------------------------------------------------

    async def async_setup(self) -> bool:
        """Create the client, test the connection, and start the WebSocket."""
        host = self.entry.data[CONF_HOST]
        port = self.entry.data.get(CONF_PORT, DEFAULT_PORT)
        api_key = self.entry.data.get(CONF_API_KEY) or None

        self._client = SnapmakerClient(host=host, port=port, api_key=api_key)

        # Register push-update callback
        self._client.register_callback(self._async_push_update)
        self._client.register_objects_changed_callback(self._schedule_reload)

        # Fetch initial data; returns False if unreachable
        if not await self._client.async_init():
            _LOGGER.warning(
                "Cannot connect to Snapmaker U1 at %s:%d; will retry", host, port
            )
            return False

        # Seed coordinator with initial data so entities get values immediately
        self.async_set_updated_data(self._client.data)

        # Start WebSocket (reconnects automatically in the background)
        await self._client.async_start()
        return True

    def _schedule_reload(self) -> None:
        """Reload the entry so entities match the printer's objects."""
        schedule = getattr(self.hass.config_entries, "async_schedule_reload", None)
        if schedule is not None:
            schedule(self.entry.entry_id)
        else:  # Home Assistant < 2024.2
            self.hass.async_create_task(
                self.hass.config_entries.async_reload(self.entry.entry_id)
            )

    async def async_shutdown(self) -> None:
        """Clean up client resources."""
        if self._client:
            self._client.remove_callback(self._async_push_update)
            await self._client.async_stop()

    # ------------------------------------------------------------------
    # Push / poll updates
    # ------------------------------------------------------------------

    async def _async_push_update(self, data: SnapmakerPrinterData) -> None:
        """Called by the WebSocket client when new data arrives."""
        await self._fire_print_state_events(data)
        self.async_set_updated_data(data)

    async def _async_update_data(self) -> SnapmakerPrinterData:
        """Fallback: poll via HTTP when WebSocket is not connected."""
        if self._client is None:
            raise UpdateFailed("Client not initialised")
        if not self._client.connected:
            try:
                await self._client.fetch_state()
            except Exception as exc:
                raise UpdateFailed(f"HTTP poll failed: {exc}") from exc
        return self._client.data

    # ------------------------------------------------------------------
    # HA event bus
    # ------------------------------------------------------------------

    async def _fire_print_state_events(self, data: SnapmakerPrinterData) -> None:
        """Fire HA bus events when the print state changes."""
        new_state = data.print_stats.state
        if new_state == self._last_print_state:
            return
        self._last_print_state = new_state

        event_name = PRINT_STATE_EVENTS.get(new_state)
        if event_name is None:
            return

        event_data: dict[str, Any] = {
            "entry_id": self.entry.entry_id,
            "state": new_state,
            "filename": data.print_stats.filename,
        }

        # Attach device_id so automations can filter by device
        dev_reg = dr.async_get(self.hass)
        host = self.entry.data[CONF_HOST]
        device = dev_reg.async_get_device(identifiers={(DOMAIN, host)})
        if device:
            event_data["device_id"] = device.id

        self.hass.bus.async_fire(event_name, event_data)
        _LOGGER.debug("Fired event %s (state=%s)", event_name, new_state)

    # ------------------------------------------------------------------
    # Convenience accessors for entities
    # ------------------------------------------------------------------

    @property
    def client(self) -> SnapmakerClient | None:
        return self._client

    @property
    def printer_name(self) -> str:
        if self._client:
            return self._client.data.printer_name
        return "Snapmaker U1"
