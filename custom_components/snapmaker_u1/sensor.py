"""Sensor platform for the Snapmaker U1 integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL, U1_EXTRUDER_COUNT
from .coordinator import SnapmakerDataUpdateCoordinator
from .definitions import (
    CHAMBER_SENSOR_TEMPLATE,
    EXTRUDER_SENSORS,
    PRINTER_SENSORS,
    SnapmakerSensorEntityDescription,
    chamber_label,
    chamber_object_name,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Snapmaker U1 sensors from a config entry."""
    coordinator: SnapmakerDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.async_add_entities_on_discovery(
        async_add_entities, lambda: _build_entities(coordinator)
    )


def _build_entities(coordinator: SnapmakerDataUpdateCoordinator) -> list[SensorEntity]:
    """Build fixed U1 sensors and any capabilities discovered since startup."""
    entities: list[SensorEntity] = []

    # Standard printer-level sensors
    for description in PRINTER_SENSORS:
        if description.exists_fn(coordinator):
            entities.append(SnapmakerSensor(coordinator, description))

    # Extruder sensors – one set per detected extruder
    extruder_count = coordinator.data.extruder_count if coordinator.data else 0
    # The U1's four nozzles exist even before its first successful connection.
    extruder_count = max(extruder_count, U1_EXTRUDER_COUNT)

    for i in range(extruder_count):
        extruder_key = "extruder" if i == 0 else f"extruder{i}"
        label = f"T{i}"  # T0, T1, T2, T3

        for desc in EXTRUDER_SENSORS:
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

        # Loaded filament (type/colour from RFID or the touchscreen)
        if coordinator.client and coordinator.client.has_filament_config:
            entities.append(
                SnapmakerSensor(
                    coordinator,
                    SnapmakerSensorEntityDescription(
                        key=f"{extruder_key}_filament",
                        name=f"{label} Filament",
                        icon="mdi:palette",
                        value_fn=_make_filament_value_fn(i),
                        extra_attributes_fn=_make_filament_attrs_fn(i),
                    ),
                )
            )

    # Chamber / extra temperature sensors (e.g. chamber thermistor, MCU temp)
    temp_keys = coordinator.client.temp_sensor_keys if coordinator.client else []
    for sensor_key in temp_keys:
        # "temperature_sensor chamber" -> label "Chamber"
        label = (
            sensor_key.split(" ", 1)[1].replace("_", " ").title()
            if " " in sensor_key
            else sensor_key
        )
        desc = SnapmakerSensorEntityDescription(
            key=f"chamber_{sensor_key.replace(' ', '_')}",
            translation_key=CHAMBER_SENSOR_TEMPLATE.translation_key,
            device_class=CHAMBER_SENSOR_TEMPLATE.device_class,
            native_unit_of_measurement=CHAMBER_SENSOR_TEMPLATE.native_unit_of_measurement,
            state_class=CHAMBER_SENSOR_TEMPLATE.state_class,
            icon=CHAMBER_SENSOR_TEMPLATE.icon,
            value_fn=_make_chamber_value_fn(sensor_key),
        )
        entities.append(SnapmakerChamberSensor(coordinator, desc, sensor_key, label))

    # Chamber heaters (Panda Breath / DragonBreath on U1 Extended Firmware)
    heater_keys = coordinator.client.chamber_heater_keys if coordinator.client else []
    for heater_key in heater_keys:
        entities.append(
            SnapmakerSensor(
                coordinator,
                SnapmakerSensorEntityDescription(
                    key=f"heater_{chamber_object_name(heater_key)}_temperature",
                    name=f"{chamber_label(heater_key)} Temperature",
                    device_class=CHAMBER_SENSOR_TEMPLATE.device_class,
                    native_unit_of_measurement=CHAMBER_SENSOR_TEMPLATE.native_unit_of_measurement,
                    state_class=CHAMBER_SENSOR_TEMPLATE.state_class,
                    icon="mdi:heat-wave",
                    value_fn=_make_chamber_heater_value_fn(heater_key),
                    extra_attributes_fn=_make_chamber_heater_attrs_fn(heater_key),
                ),
            )
        )

    return entities


def _make_filament_value_fn(index: int):
    """State: 'PLA Basic', or 'Empty' when nothing is loaded."""

    def value_fn(self: SnapmakerSensor) -> str | None:
        slot = self.coordinator.data.filament_slots.get(index)
        if slot is None:
            return None
        if not slot.loaded:
            return "Empty"
        return slot.name

    return value_fn


def _make_filament_attrs_fn(index: int):
    def attrs_fn(self: SnapmakerSensor) -> dict[str, Any]:
        slot = self.coordinator.data.filament_slots.get(index)
        if slot is None:
            return {}
        # An untouched slot reports white; only trust the colour once the
        # material is known.
        color = slot.color if slot.material else None
        return {
            "color": color,
            "alpha": slot.alpha if color else None,
            "material": slot.material,
            "sub_type": slot.sub_type,
            "vendor": slot.vendor,
            "loaded": slot.loaded,
            "official": slot.official,
        }

    return attrs_fn


def _make_chamber_heater_value_fn(heater_key: str):
    def value_fn(self: SnapmakerSensor) -> float | None:
        heater = self.coordinator.data.chamber_heaters.get(heater_key)
        return heater.temperature if heater else None

    return value_fn


def _make_chamber_heater_attrs_fn(heater_key: str):
    def attrs_fn(self: SnapmakerSensor) -> dict[str, Any]:
        heater = self.coordinator.data.chamber_heaters.get(heater_key)
        if heater is None:
            return {}
        return {
            "target": heater.target,
            "power": round(heater.power, 2),
            "min_temp": heater.min_temp,
            "max_temp": heater.max_temp,
        }

    return attrs_fn


def _make_chamber_value_fn(sensor_key: str):
    """Return a value_fn that reads the correct chamber sensor temperature."""

    def value_fn(self: SnapmakerChamberSensor) -> float | None:
        return self.coordinator.data.chamber_sensors.get(sensor_key)

    return value_fn


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
        if not super().available:
            return False

        try:
            return self.entity_description.available_fn(self)
        except Exception:
            return False

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


class SnapmakerChamberSensor(SnapmakerSensor):
    """Sensor for a dynamically discovered temperature sensor (e.g. chamber, MCU)."""

    def __init__(
        self,
        coordinator: SnapmakerDataUpdateCoordinator,
        description: SnapmakerSensorEntityDescription,
        sensor_key: str,
        label: str,
    ) -> None:
        super().__init__(coordinator, description)
        host = coordinator.entry.data["host"]
        self._attr_unique_id = f"{host}_chamber_{sensor_key.replace(' ', '_')}"
        self._attr_name = label
