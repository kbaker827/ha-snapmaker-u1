"""Select platform for the Snapmaker U1 integration.

Provides:
  - PrintFileSelect  – choose a G-code file to print (starts print on select)
  - ActiveToolSelect – switch the active extruder (only on multi-extruder printers)
"""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
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
    """Set up Snapmaker U1 select entities from a config entry."""
    coordinator: SnapmakerDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SelectEntity] = [PrintFileSelect(coordinator)]

    # Add active tool selector only for multi-extruder setups
    extruder_count = coordinator.data.extruder_count if coordinator.data else 0
    if extruder_count > 1:
        entities.append(ActiveToolSelect(coordinator, extruder_count))

    async_add_entities(entities)


class PrintFileSelect(SnapmakerBaseEntity, SelectEntity):
    """Select entity to choose and start a G-code file for printing."""

    _attr_has_entity_name = True
    _attr_translation_key = "print_file"
    _attr_icon = "mdi:file-document"

    def __init__(self, coordinator: SnapmakerDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        host = coordinator.entry.data["host"]
        self._attr_unique_id = f"{host}_print_file"

    @property
    def options(self) -> list[str]:
        """Return the list of available G-code files."""
        files = self.coordinator.data.available_files if self.coordinator.data else []
        return files if files else []

    @property
    def current_option(self) -> str | None:
        """Return the currently printing filename, or None."""
        if self.coordinator.data:
            return self.coordinator.data.print_stats.filename or None
        return None

    @property
    def available(self) -> bool:
        """Available when printer is ready and file list is populated."""
        if not self.coordinator.data:
            return False
        return self.coordinator.data.is_ready and bool(
            self.coordinator.data.available_files
        )

    async def async_select_option(self, option: str) -> None:
        """Start printing the selected file."""
        client = self.coordinator.client
        if client is None:
            _LOGGER.warning("Cannot start print – client not available")
            return
        try:
            await client.start_print(option)
            _LOGGER.info("Started print: %s", option)
        except Exception as exc:
            _LOGGER.error("Error starting print %s: %s", option, exc)


class ActiveToolSelect(SnapmakerBaseEntity, SelectEntity):
    """Select entity to switch the active extruder tool."""

    _attr_has_entity_name = True
    _attr_translation_key = "active_tool"
    _attr_icon = "mdi:printer-3d-nozzle"

    def __init__(
        self,
        coordinator: SnapmakerDataUpdateCoordinator,
        extruder_count: int,
    ) -> None:
        super().__init__(coordinator)
        host = coordinator.entry.data["host"]
        self._attr_unique_id = f"{host}_active_tool"
        self._options = [f"T{i}" for i in range(extruder_count)]
        self._current_tool: str = "T0"

    @property
    def options(self) -> list[str]:
        return self._options

    @property
    def current_option(self) -> str:
        return self._current_tool

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data and self.coordinator.data.is_ready)

    async def async_select_option(self, option: str) -> None:
        """Switch to the selected tool."""
        client = self.coordinator.client
        if client is None:
            _LOGGER.warning("Cannot switch tool – client not available")
            return
        try:
            index = int(option[1:])  # Strip the "T" prefix
            await client.set_active_tool(index)
            self._current_tool = option
            self.async_write_ha_state()
        except Exception as exc:
            _LOGGER.error("Error switching to tool %s: %s", option, exc)
