"""Switch platform for the Snapmaker U1 integration.

Provides:
  - WorkLightSwitch – toggle the printer's work/chamber light (M355 S1/S0)
"""
from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SnapmakerDataUpdateCoordinator
from .sensor import SnapmakerBaseEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Snapmaker U1 switch entities from a config entry."""
    coordinator: SnapmakerDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([WorkLightSwitch(coordinator)])


class WorkLightSwitch(SnapmakerBaseEntity, SwitchEntity):
    """Switch to toggle the printer's work/chamber light."""

    _attr_has_entity_name = True
    _attr_translation_key = "work_light"
    _attr_icon = "mdi:lightbulb"

    def __init__(self, coordinator: SnapmakerDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        host = coordinator.entry.data["host"]
        self._attr_unique_id = f"{host}_work_light"
        self._optimistic_state: bool | None = None

    @property
    def is_on(self) -> bool:
        """Return the current (or optimistic) light state."""
        if self._optimistic_state is not None:
            return self._optimistic_state
        if self.coordinator.data:
            return self.coordinator.data.work_light_on
        return False

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data and self.coordinator.data.is_ready)

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the work light on."""
        await self._set_light(True)

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the work light off."""
        await self._set_light(False)

    async def _set_light(self, on: bool) -> None:
        client = self.coordinator.client
        if client is None:
            _LOGGER.warning("Cannot toggle work light – client not available")
            return
        try:
            await client.set_work_light(on)
            # Optimistic update – Moonraker doesn't report M355 state back
            self._optimistic_state = on
            if self.coordinator.data:
                self.coordinator.data.work_light_on = on
            self.async_write_ha_state()
        except Exception as exc:
            _LOGGER.error("Error toggling work light: %s", exc)
