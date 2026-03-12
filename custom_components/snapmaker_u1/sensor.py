"""Sensor platform for the Snapmaker U1 integration."""
from __future__ import annotations

import copy
import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import SnapmakerDataUpdateCoordinator
from .definitions import (
    EXTRUDER_SENSORS,
    PRINTER_SENSORS,
    SnapmakerSensorEntityDescription,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Snapmaker U1 sensors from a config entry."""
    coordinator: SnapmakerDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = []

    # Standard printer-level sensors
    for description in PRINTER_SENSORS:
        if description.exists_fn(coordinator):
            entities.append(SnapmakerSensor(coordinator, description))

    # Extruder sensors – one set per detected extruder
    extruder_count = coordinator.data.extruder_count if coordinator.data else 0
    # Always create at least one extruder set (U1 always has at least one nozzle)
    extruder_count = max(extruder_count, 1)

    for i in range(extruder_count):
        extruder_key = "extruder" if i == 0 else f"extruder{i}"
        label = f"T{i}"  # T0, T1, T2, T3

        for desc in EXTRUDER_SENSORS:
            # Deep-copy so each extruder gets its own description instance
            extruder_desc = copy.copy(desc)
            extruder_desc = SnapmakerSensorEntityDescription(
                key=f"{extruder_key}_{desc.key}",
                translation_key=desc.translation_key,
                device_class=desc.device_class,
                native_unit_of_measurement=desc.native_unit_of_measurement,
                state_class=desc.state_class,
                icon=desc.icon,
                value_fn=_make_extruder_value_fn(extruder_key, desc.key),
                extra_attributes_fn=_make_extruder_attrs_fn(extruder_key)
                if desc.key == "temperature"
                else (lambda self: {}),
            )
            entities.append(
                SnapmakerExtruderSensor(coordinator, extruder_desc, extruder_key, label)
            )

    async_add_entities(entities)


def _make_extruder_value_fn(extruder_key: str, field: str):
    """Return a value_fn that reads the correct extruder field."""

    def value_fn(self: SnapmakerExtruderSensor):
        ed = self.coordinator.data.extruders.get(extruder_key)
        if ed is None:
            return None
        return getattr(ed, field, None)

    return value_fn


def _make_extruder_attrs_fn(extruder_key: str):
    """Return an extra_attributes_fn for an extruder temperature sensor."""

    def attrs_fn(self: SnapmakerExtruderSensor) -> dict[str, Any]:
        ed = self.coordinator.data.extruders.get(extruder_key)
        if ed is None:
            return {}
        return {
            "target": ed.target,
            "power": round(ed.power, 2),
            "can_extrude": ed.can_extrude,
        }

    return attrs_fn


# ---------------------------------------------------------------------------
# Entity classes
# ---------------------------------------------------------------------------


class SnapmakerBaseEntity(CoordinatorEntity[SnapmakerDataUpdateCoordinator]):
    """Base entity for all Snapmaker U1 entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SnapmakerDataUpdateCoordinator,
    ) -> None:
        super().__init__(coordinator)

    @property
    def device_info(self) -> DeviceInfo:
        host = self.coordinator.entry.data["host"]
        return DeviceInfo(
            identifiers={(DOMAIN, host)},
            name=self.coordinator.printer_name,
            manufacturer=MANUFACTURER,
            model=MODEL,
            sw_version=self.coordinator.data.firmware_version
            if self.coordinator.data
            else None,
            configuration_url=f"http://{host}",
        )


class SnapmakerSensor(SnapmakerBaseEntity, SensorEntity):
    """A sensor entity for a standard printer-level value."""

    entity_description: SnapmakerSensorEntityDescription

    def __init__(
        self,
        coordinator: SnapmakerDataUpdateCoordinator,
        description: SnapmakerSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        host = coordinator.entry.data["host"]
        self._attr_unique_id = f"{host}_{description.key}"

    @property
    def native_value(self):
        if self.entity_description.value_fn is None:
            return None
        try:
            return self.entity_description.value_fn(self)
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
        try:
            return self.entity_description.extra_attributes_fn(self)
        except Exception:
            return {}


class SnapmakerExtruderSensor(SnapmakerSensor):
    """Sensor entity for a specific extruder (nozzle)."""

    def __init__(
        self,
        coordinator: SnapmakerDataUpdateCoordinator,
        description: SnapmakerSensorEntityDescription,
        extruder_key: str,
        label: str,
    ) -> None:
        super().__init__(coordinator, description)
        self._extruder_key = extruder_key
        self._label = label
        host = coordinator.entry.data["host"]
        self._attr_unique_id = f"{host}_{extruder_key}_{description.key}"
        # Override name to include T0/T1/… prefix
        self._attr_name = f"{label} {description.translation_key.replace('_', ' ').title()}"
