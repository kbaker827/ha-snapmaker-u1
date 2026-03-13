"""Binary sensor platform for the Snapmaker U1 integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SnapmakerDataUpdateCoordinator
from .definitions import (
    PRINTER_BINARY_SENSORS,
    SnapmakerBinarySensorEntityDescription,
)
from .sensor import SnapmakerBaseEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Snapmaker U1 binary sensors from a config entry."""
    coordinator: SnapmakerDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[BinarySensorEntity] = [
        SnapmakerBinarySensor(coordinator, description)
        for description in PRINTER_BINARY_SENSORS
        if description.exists_fn(coordinator)
    ]

    # Add dynamic filament runout sensors for each discovered sensor
    if coordinator.client:
        for key in coordinator.client.filament_sensor_keys:
            # Friendly name: strip "filament_switch_sensor " prefix
            sensor_name = key.replace("filament_switch_sensor ", "").replace("_", " ").title()
            desc = SnapmakerBinarySensorEntityDescription(
                key=f"filament_{key}",
                name=f"{sensor_name} Filament",
                device_class=BinarySensorDeviceClass.PROBLEM,
                icon="mdi:alert-circle",
                value_fn=_make_filament_value_fn(key),
                extra_attributes_fn=_make_filament_attrs_fn(key),
            )
            entities.append(SnapmakerFilamentBinarySensor(coordinator, desc, key))

    async_add_entities(entities)


def _make_filament_value_fn(sensor_key: str):
    """Return a value_fn that is True (problem) when filament is NOT detected."""
    def fn(self: SnapmakerBinarySensor) -> bool | None:
        sensor = self.coordinator.data.filament_sensors.get(sensor_key)
        if sensor is None:
            return None
        # Problem = True means filament runout (no filament detected)
        return not sensor.filament_detected

    return fn


def _make_filament_attrs_fn(sensor_key: str):
    """Return extra_attributes_fn for a filament sensor."""
    def fn(self: SnapmakerBinarySensor) -> dict[str, Any]:
        sensor = self.coordinator.data.filament_sensors.get(sensor_key)
        if sensor is None:
            return {}
        return {
            "enabled": sensor.enabled,
            "filament_detected": sensor.filament_detected,
        }

    return fn


class SnapmakerBinarySensor(SnapmakerBaseEntity, BinarySensorEntity):
    """Binary sensor entity for a Snapmaker U1 printer state."""

    entity_description: SnapmakerBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: SnapmakerDataUpdateCoordinator,
        description: SnapmakerBinarySensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        host = coordinator.entry.data["host"]
        self._attr_unique_id = f"{host}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        if self.entity_description.value_fn is None:
            return None
        try:
            return bool(self.entity_description.value_fn(self))
        except Exception:
            return None

    @property
    def available(self) -> bool:
        try:
            return self.entity_description.available_fn(self)
        except Exception:
            return True

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        fn = getattr(self.entity_description, "extra_attributes_fn", None)
        if fn is None:
            return {}
        try:
            return fn(self)
        except Exception:
            return {}


class SnapmakerFilamentBinarySensor(SnapmakerBinarySensor):
    """Binary sensor for a dynamically discovered filament runout sensor."""

    def __init__(
        self,
        coordinator: SnapmakerDataUpdateCoordinator,
        description: SnapmakerBinarySensorEntityDescription,
        sensor_key: str,
    ) -> None:
        super().__init__(coordinator, description)
        host = coordinator.entry.data["host"]
        self._attr_unique_id = f"{host}_filament_{sensor_key}"
        self._attr_has_entity_name = False  # Use full name set in description
