"""Select platform for the Snapmaker U1 integration.

Provides:
  - PrintFileSelect  – choose a G-code file to print (starts print on select)
  - ActiveToolSelect – switch the active extruder (only on multi-extruder printers)
  - TopCoverModeSelect – official Top Cover purifier mode (cooling / keep warm)
  - PopStatusLightModeSelect – BIQU PopStatus Simple / Advanced light mode
"""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, is_popstatus_entry
from .coordinator import SnapmakerDataUpdateCoordinator
from .definitions import TOP_COVER_MODES
from .popstatus_entity import PopStatusEntity, get_popstatus
from .pysnapmaker.popstatus import POPSTATUS_LIGHT_MODES, PopStatusClient
from .sensor import SnapmakerBaseEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Snapmaker U1 select entities from a config entry."""
    if is_popstatus_entry(entry):
        async_add_entities(
            [PopStatusLightModeSelect(hass, entry, get_popstatus(hass, entry))]
        )
        return

    coordinator: SnapmakerDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SelectEntity] = [PrintFileSelect(coordinator)]

    # Add active tool selector only for multi-extruder setups
    extruder_count = coordinator.data.extruder_count if coordinator.data else 0
    if extruder_count > 1:
        entities.append(ActiveToolSelect(coordinator, extruder_count))

    if coordinator.client and coordinator.client.has_top_cover:
        entities.append(TopCoverModeSelect(coordinator))

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
        """Return the currently printing filename if it is in the options list."""
        if not self.coordinator.data:
            return None
        fname = self.coordinator.data.print_stats.filename
        if fname and fname in self.options:
            return fname
        return None

    @property
    def available(self) -> bool:
        """Available when printer is ready and file list is populated."""
        if not super().available or not self.coordinator.data:
            return False
        return self.coordinator.data.is_ready and bool(
            self.coordinator.data.available_files
        )

    async def async_select_option(self, option: str) -> None:
        """Start printing the selected file."""
        client = self.coordinator.client
        if client is None:
            raise HomeAssistantError("Snapmaker U1 is not connected")
        data = self.coordinator.data
        if data and (data.is_printing or data.is_paused):
            raise ServiceValidationError(
                "A print is already running. Cancel or finish it before starting "
                f"{option}."
            )
        try:
            await client.start_print(option)
        except Exception as exc:
            raise HomeAssistantError(f"Could not start {option}: {exc}") from exc
        _LOGGER.info("Started print: %s", option)


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
        """Return active tool from printer data, falling back to optimistic state."""
        if self.coordinator.data:
            extruder = self.coordinator.data.toolhead.active_extruder
            # Moonraker names: "extruder" -> T0, "extruder1" -> T1, etc.
            if extruder == "extruder":
                return "T0"
            try:
                idx = int(extruder.replace("extruder", ""))
                tool = f"T{idx}"
                if tool in self._options:
                    return tool
            except ValueError:
                pass
        return self._current_tool

    @property
    def available(self) -> bool:
        return bool(
            super().available and self.coordinator.data and self.coordinator.data.is_ready
        )

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


class TopCoverModeSelect(SnapmakerBaseEntity, SelectEntity):
    """Purifier mode of the official Top Cover."""

    _attr_has_entity_name = True
    _attr_translation_key = "top_cover_mode"
    _attr_icon = "mdi:air-purifier"
    _attr_options = list(TOP_COVER_MODES.values())

    def __init__(self, coordinator: SnapmakerDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        host = coordinator.entry.data["host"]
        self._attr_unique_id = f"{host}_top_cover_mode_select"

    @property
    def current_option(self) -> str | None:
        cover = self.coordinator.data.top_cover if self.coordinator.data else None
        return TOP_COVER_MODES.get(cover.mode) if cover else None

    @property
    def available(self) -> bool:
        data = self.coordinator.data
        return bool(
            super().available
            and data
            and data.is_ready
            and data.top_cover
            and data.top_cover.detected
        )

    async def async_select_option(self, option: str) -> None:
        mode = next((m for m, name in TOP_COVER_MODES.items() if name == option), None)
        client = self.coordinator.client
        if mode is None or client is None:
            raise ServiceValidationError(f"Cannot set Top Cover mode {option}")
        try:
            await client.set_top_cover_mode(mode)
        except Exception as exc:
            raise HomeAssistantError(f"Could not set Top Cover mode: {exc}") from exc


class PopStatusLightModeSelect(PopStatusEntity, SelectEntity):
    """PopStatus light mode: Simple (one style) or Advanced (per printer state)."""

    _attr_icon = "mdi:led-strip-variant"
    _attr_options = list(POPSTATUS_LIGHT_MODES.values())

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: PopStatusClient
    ) -> None:
        super().__init__(hass, entry, client, "light_mode")

    @property
    def current_option(self) -> str | None:
        return POPSTATUS_LIGHT_MODES.get(self.popstatus.data.light_mode)

    async def async_select_option(self, option: str) -> None:
        mode = next((m for m, name in POPSTATUS_LIGHT_MODES.items() if name == option), None)
        if mode is None:
            raise ServiceValidationError(f"Unknown PopStatus light mode {option}")
        try:
            await self.popstatus.set_light_mode(mode)
        except ConnectionError as exc:
            raise HomeAssistantError(str(exc)) from exc
