"""Declarative entity descriptions for the Snapmaker U1 integration."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntityDescription,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfLength,
    UnitOfTemperature,
    UnitOfTime,
)

from .coordinator import SnapmakerDataUpdateCoordinator


# ---------------------------------------------------------------------------
# Sensor descriptions
# ---------------------------------------------------------------------------


@dataclass(frozen=False)
class SnapmakerSensorEntityDescription(SensorEntityDescription):
    """Extends SensorEntityDescription with lambdas used by the entity classes."""

    value_fn: Callable | None = None
    available_fn: Callable = field(default=lambda self: True)
    # Called with the coordinator; return False to skip creating this entity
    exists_fn: Callable = field(default=lambda coordinator: True)
    extra_attributes_fn: Callable = field(default=lambda self: {})


# ---------------------------------------------------------------------------
# Binary sensor descriptions
# ---------------------------------------------------------------------------


@dataclass(frozen=False)
class SnapmakerBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Extends BinarySensorEntityDescription with lambdas."""

    value_fn: Callable | None = None
    available_fn: Callable = field(default=lambda self: True)
    exists_fn: Callable = field(default=lambda coordinator: True)


# ---------------------------------------------------------------------------
# Printer sensors
# ---------------------------------------------------------------------------

PRINTER_SENSORS: list[SnapmakerSensorEntityDescription] = [
    SnapmakerSensorEntityDescription(
        key="print_state",
        translation_key="print_state",
        icon="mdi:printer-3d",
        value_fn=lambda self: self.coordinator.data.print_stats.state,
    ),
    SnapmakerSensorEntityDescription(
        key="print_progress",
        translation_key="print_progress",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:progress-check",
        value_fn=lambda self: self.coordinator.data.print_progress_pct,
        available_fn=lambda self: self.coordinator.data.is_printing
        or self.coordinator.data.is_paused,
    ),
    SnapmakerSensorEntityDescription(
        key="current_layer",
        translation_key="current_layer",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:layers",
        value_fn=lambda self: self.coordinator.data.print_stats.current_layer,
        available_fn=lambda self: self.coordinator.data.is_printing
        or self.coordinator.data.is_paused,
    ),
    SnapmakerSensorEntityDescription(
        key="total_layers",
        translation_key="total_layers",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:layers-triple",
        value_fn=lambda self: self.coordinator.data.print_stats.total_layer,
        available_fn=lambda self: self.coordinator.data.is_printing
        or self.coordinator.data.is_paused,
    ),
    SnapmakerSensorEntityDescription(
        key="print_duration",
        translation_key="print_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:timer",
        value_fn=lambda self: int(
            self.coordinator.data.print_stats.print_duration
        ),
        available_fn=lambda self: self.coordinator.data.is_printing
        or self.coordinator.data.is_paused,
    ),
    SnapmakerSensorEntityDescription(
        key="time_remaining",
        translation_key="time_remaining",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:timer-sand",
        value_fn=lambda self: self.coordinator.data.time_remaining,
        available_fn=lambda self: self.coordinator.data.is_printing,
    ),
    SnapmakerSensorEntityDescription(
        key="filament_used",
        translation_key="filament_used",
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:tape-measure",
        value_fn=lambda self: round(
            self.coordinator.data.print_stats.filament_used, 1
        ),
        available_fn=lambda self: self.coordinator.data.is_printing
        or self.coordinator.data.is_paused,
    ),
    SnapmakerSensorEntityDescription(
        key="filename",
        translation_key="filename",
        icon="mdi:file-code",
        value_fn=lambda self: self.coordinator.data.print_stats.filename or None,
        available_fn=lambda self: bool(
            self.coordinator.data.print_stats.filename
        ),
    ),
    SnapmakerSensorEntityDescription(
        key="bed_temperature",
        translation_key="bed_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:radiator",
        value_fn=lambda self: self.coordinator.data.heater_bed.temperature,
        extra_attributes_fn=lambda self: {
            "target": self.coordinator.data.heater_bed.target,
            "power": round(self.coordinator.data.heater_bed.power, 2),
        },
    ),
    SnapmakerSensorEntityDescription(
        key="bed_target",
        translation_key="bed_target",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:radiator",
        value_fn=lambda self: self.coordinator.data.heater_bed.target,
    ),
    SnapmakerSensorEntityDescription(
        key="fan_speed",
        translation_key="fan_speed",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:fan",
        value_fn=lambda self: self.coordinator.data.fan.speed,
    ),
    SnapmakerSensorEntityDescription(
        key="klipper_state",
        translation_key="klipper_state",
        icon="mdi:chip",
        value_fn=lambda self: self.coordinator.data.klipper_state,
        extra_attributes_fn=lambda self: {
            "message": self.coordinator.data.klipper_message
        },
    ),
    SnapmakerSensorEntityDescription(
        key="toolhead_position",
        translation_key="toolhead_position",
        icon="mdi:crosshairs-gps",
        value_fn=lambda self: (
            f"X{self.coordinator.data.toolhead.position[0]:.2f} "
            f"Y{self.coordinator.data.toolhead.position[1]:.2f} "
            f"Z{self.coordinator.data.toolhead.position[2]:.2f}"
        ),
        extra_attributes_fn=lambda self: {
            "x": round(self.coordinator.data.toolhead.position[0], 2),
            "y": round(self.coordinator.data.toolhead.position[1], 2),
            "z": round(self.coordinator.data.toolhead.position[2], 2),
            "homed_axes": self.coordinator.data.toolhead.homed_axes,
        },
    ),
]


# ---------------------------------------------------------------------------
# Extruder sensors (instantiated per detected extruder in sensor.py)
# ---------------------------------------------------------------------------

EXTRUDER_SENSORS: list[SnapmakerSensorEntityDescription] = [
    SnapmakerSensorEntityDescription(
        key="temperature",
        translation_key="nozzle_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer",
        # value_fn and extra_attributes_fn are set dynamically in sensor.py
    ),
    SnapmakerSensorEntityDescription(
        key="target",
        translation_key="nozzle_target",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer-lines",
    ),
]


# ---------------------------------------------------------------------------
# Binary sensors
# ---------------------------------------------------------------------------

PRINTER_BINARY_SENSORS: list[SnapmakerBinarySensorEntityDescription] = [
    SnapmakerBinarySensorEntityDescription(
        key="printer_ready",
        translation_key="printer_ready",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        icon="mdi:printer-3d-nozzle",
        value_fn=lambda self: self.coordinator.data.is_ready,
    ),
    SnapmakerBinarySensorEntityDescription(
        key="is_printing",
        translation_key="is_printing",
        icon="mdi:printer-3d-nozzle-alert",
        value_fn=lambda self: self.coordinator.data.is_printing,
    ),
    SnapmakerBinarySensorEntityDescription(
        key="is_paused",
        translation_key="is_paused",
        icon="mdi:pause-circle",
        value_fn=lambda self: self.coordinator.data.is_paused,
    ),
    SnapmakerBinarySensorEntityDescription(
        key="has_error",
        translation_key="has_error",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda self: self.coordinator.data.has_error,
        extra_attributes_fn=lambda self: {
            "klipper_message": self.coordinator.data.klipper_message
        },
    ),
]
