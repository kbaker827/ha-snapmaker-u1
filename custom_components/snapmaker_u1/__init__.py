"""Snapmaker U1 Home Assistant integration."""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .coordinator import SnapmakerDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CAMERA,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
]

# Service names
SERVICE_EXECUTE_GCODE = "execute_gcode"
SERVICE_SET_BED_TEMPERATURE = "set_bed_temperature"
SERVICE_SET_NOZZLE_TEMPERATURE = "set_nozzle_temperature"
SERVICE_START_PRINT = "start_print"

# Service schemas
EXECUTE_GCODE_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): cv.string,
        vol.Required("script"): cv.string,
    }
)
SET_BED_TEMPERATURE_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): cv.string,
        vol.Required("temperature"): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=120)
        ),
    }
)
SET_NOZZLE_TEMPERATURE_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): cv.string,
        vol.Required("temperature"): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=300)
        ),
        vol.Optional("extruder_index", default=0): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=3)
        ),
    }
)
START_PRINT_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): cv.string,
        vol.Required("filename"): cv.string,
    }
)


def _get_client(hass: HomeAssistant, call: ServiceCall):
    """Resolve the SnapmakerClient for a service call."""
    entry_id = call.data["config_entry_id"]
    coordinator: SnapmakerDataUpdateCoordinator | None = (
        hass.data.get(DOMAIN, {}).get(entry_id)
    )
    if coordinator is None or coordinator.client is None:
        raise ServiceValidationError(
            f"No Snapmaker U1 entry found for config_entry_id '{entry_id}'"
        )
    return coordinator.client


def _register_services(hass: HomeAssistant) -> None:
    """Register integration-level services (called once on first entry setup)."""

    async def handle_execute_gcode(call: ServiceCall) -> None:
        await _get_client(hass, call).execute_gcode(call.data["script"])

    async def handle_set_bed_temperature(call: ServiceCall) -> None:
        await _get_client(hass, call).set_bed_temperature(call.data["temperature"])

    async def handle_set_nozzle_temperature(call: ServiceCall) -> None:
        await _get_client(hass, call).set_nozzle_temperature(
            call.data["temperature"],
            call.data.get("extruder_index", 0),
        )

    async def handle_start_print(call: ServiceCall) -> None:
        await _get_client(hass, call).start_print(call.data["filename"])

    hass.services.async_register(
        DOMAIN, SERVICE_EXECUTE_GCODE, handle_execute_gcode, EXECUTE_GCODE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_BED_TEMPERATURE,
        handle_set_bed_temperature,
        SET_BED_TEMPERATURE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_NOZZLE_TEMPERATURE,
        handle_set_nozzle_temperature,
        SET_NOZZLE_TEMPERATURE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_START_PRINT, handle_start_print, START_PRINT_SCHEMA
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Snapmaker U1 from a config entry."""
    coordinator = SnapmakerDataUpdateCoordinator(hass, entry)

    if not await coordinator.async_setup():
        return False

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Register services only once (first loaded entry)
    if not hass.services.has_service(DOMAIN, SERVICE_EXECUTE_GCODE):
        _register_services(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload the entry if options change (e.g. scan interval)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update by reloading the entry."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator: SnapmakerDataUpdateCoordinator = hass.data[DOMAIN].get(
        entry.entry_id
    )
    if coordinator:
        await coordinator.async_shutdown()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    # Unregister services when the last entry is removed
    if not hass.data.get(DOMAIN):
        for svc in (
            SERVICE_EXECUTE_GCODE,
            SERVICE_SET_BED_TEMPERATURE,
            SERVICE_SET_NOZZLE_TEMPERATURE,
            SERVICE_START_PRINT,
        ):
            hass.services.async_remove(DOMAIN, svc)

    return unload_ok
