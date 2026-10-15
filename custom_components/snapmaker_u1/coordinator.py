"""Data coordinator for the Snapmaker U1 integration."""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_API_KEY,
    CONF_HOST,
    CONF_PORT,
    DEFAULT_PORT,
    DOMAIN,
)
from .pysnapmaker.client import SnapmakerClient
from .pysnapmaker.const import PRINT_STATE_EVENTS
from .pysnapmaker.models import SnapmakerPrinterData

_LOGGER = logging.getLogger(__name__)


class SnapmakerDataUpdateCoordinator(DataUpdateCoordinator[SnapmakerPrinterData]):
    """Receive push updates; the client alone handles reconnection."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
        )
        self.entry = entry
        self._client: SnapmakerClient | None = None
        self._last_print_state: str | None = None

    # ------------------------------------------------------------------
    # Setup / teardown
    # ------------------------------------------------------------------

    async def async_setup(self) -> bool:
        """Load immediately, even while the printer is switched off."""
        host = self.entry.data[CONF_HOST]
        port = self.entry.data.get(CONF_PORT, DEFAULT_PORT)
        api_key = self.entry.data.get(CONF_API_KEY) or None

        self._client = SnapmakerClient(host=host, port=port, api_key=api_key)

        # Register push-update callback
        self._client.register_callback(self._async_push_update)

        # Typed defaults let platforms create entities without network access.
        # Do not publish them as a successful measurement.
        self.data = self._client.data
        device = dr.async_get(self.hass).async_get_device_by_identifier(
            (DOMAIN, host), self.entry.entry_id
        )
        if device and device.name:
            self.data.printer_name = device.name
        if device and device.sw_version:
            self.data.firmware_version = device.sw_version
        self.last_update_success = False

        # Start WebSocket (reconnects automatically in the background)
        await self._client.async_start()
        return True

    async def async_shutdown(self) -> None:
        """Clean up client resources."""
        if self._client:
            self._client.remove_callback(self._async_push_update)
            await self._client.async_stop()
        await super().async_shutdown()

    # ------------------------------------------------------------------
    # Push updates
    # ------------------------------------------------------------------

    async def _async_push_update(self, data: SnapmakerPrinterData) -> None:
        """Called by the WebSocket client when new data arrives."""
        if self._client is None or not self._client.connected:
            # A powered-off printer is expected. Notify entities without the
            # error-level logging of async_set_update_error().
            was_available = self.last_update_success
            self.last_update_success = False
            self.last_exception = UpdateFailed("Printer is offline")
            if was_available:
                self.async_update_listeners()
            return
        device_registry = dr.async_get(self.hass)
        device = device_registry.async_get_device_by_identifier(
            (DOMAIN, self.entry.data[CONF_HOST]), self.entry.entry_id
        )
        if device and (
            device.name != data.printer_name
            or device.sw_version != data.firmware_version
        ):
            device_registry.async_update_device(
                device.id, name=data.printer_name, sw_version=data.firmware_version
            )
        await self._fire_print_state_events(data)
        self.async_set_updated_data(data)

    async def _async_update_data(self) -> SnapmakerPrinterData:
        """Return the current push snapshot without another network request."""
        if self._client is None or not self._client.connected:
            raise UpdateFailed("Printer is offline")
        return self._client.data

    @callback
    def async_add_entities_on_discovery(
        self,
        async_add_entities: AddEntitiesCallback,
        factory: Callable[[], Iterable[Entity]],
    ) -> None:
        """Create known entities now and add newly discovered ones later."""
        seen: set[str | None] = set()
        previous_signature: tuple | None = None

        @callback
        def add_discovered_entities() -> None:
            nonlocal previous_signature
            if self._client is None:
                return
            signature = self._client.discovery_signature
            if signature == previous_signature:
                return
            previous_signature = signature
            entities = []
            for entity in factory():
                if entity.unique_id not in seen:
                    seen.add(entity.unique_id)
                    entities.append(entity)
            if entities:
                async_add_entities(entities)

        self.entry.async_on_unload(self.async_add_listener(add_discovered_entities))
        add_discovered_entities()

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
        device = dev_reg.async_get_device_by_identifier(
            (DOMAIN, host), self.entry.entry_id
        )
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
