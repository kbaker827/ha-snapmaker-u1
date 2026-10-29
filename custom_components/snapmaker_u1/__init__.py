"""Snapmaker U1 Home Assistant integration."""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, UnitOfTemperature
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_TEMPERATURE_UNIT,
    DEFAULT_TEMPERATURE_UNIT,
    DOMAIN,
    TEMP_UNIT_CELSIUS,
    TEMP_UNIT_FAHRENHEIT,
)
from .coordinator import SnapmakerDataUpdateCoordinator
from .frontend import (
    async_ensure_card_resource,
    async_register_frontend,
    async_remove_lovelace_resource,
)

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CAMERA,
    Platform.LIGHT,
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


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the bundled dashboard card."""
    try:
        await async_register_frontend(hass)
    except Exception:  # noqa: BLE001 - the card is optional; never block setup
        _LOGGER.exception("Could not register the Snapmaker U1 dashboard card")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Snapmaker U1 from a config entry."""
    coordinator = SnapmakerDataUpdateCoordinator(hass, entry)

    if not await coordinator.async_setup():
        # Printer off or unreachable: Home Assistant retries setup with
        # back-off, so the integration recovers on its own once it's back.
        raise ConfigEntryNotReady(
            f"Cannot connect to Snapmaker U1 at {entry.data.get('host')}"
        )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Register services only once (first loaded entry)
    if not hass.services.has_service(DOMAIN, SERVICE_EXECUTE_GCODE):
        _register_services(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _apply_temperature_unit(hass, entry)
    await async_ensure_card_resource(hass)

    # Reload the entry if options change (e.g. scan interval)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


def _apply_temperature_unit(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Show every printer temperature in the unit chosen in the options.

    Home Assistant converts temperatures to its own unit system (°F on US
    installs). Setting the unit in the entity registry options overrides that
    per entity, for sensors and number setpoints alike. "Home Assistant
    default" removes the override again.
    """
    choice = entry.options.get(CONF_TEMPERATURE_UNIT, DEFAULT_TEMPERATURE_UNIT)
    unit = {
        TEMP_UNIT_CELSIUS: UnitOfTemperature.CELSIUS,
        TEMP_UNIT_FAHRENHEIT: UnitOfTemperature.FAHRENHEIT,
    }.get(choice)
    registry = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg_entry.domain not in ("sensor", "number"):
            continue
        device_class = reg_entry.device_class or reg_entry.original_device_class
        if device_class != "temperature":
            continue
        options = dict(reg_entry.options.get(reg_entry.domain, {}))
        if unit is None:
            if "unit_of_measurement" not in options:
                continue
            options.pop("unit_of_measurement")
        elif options.get("unit_of_measurement") == unit:
            continue
        else:
            options["unit_of_measurement"] = unit
        registry.async_update_entity_options(
            reg_entry.entity_id, reg_entry.domain, options
        )


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update by reloading the entry."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Unload the entities first so none of them use the client after it stops.
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False
    coordinator: SnapmakerDataUpdateCoordinator | None = hass.data[DOMAIN].pop(
        entry.entry_id, None
    )
    if coordinator:
        await coordinator.async_shutdown()

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


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the dashboard resource when the last printer is removed."""
    others = [
        e for e in hass.config_entries.async_entries(DOMAIN) if e.entry_id != entry.entry_id
    ]
    if not others:
        await async_remove_lovelace_resource(hass)
