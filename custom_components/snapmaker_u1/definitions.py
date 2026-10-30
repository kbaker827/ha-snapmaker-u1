"""Declarative entity descriptions for the Snapmaker U1 integration."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
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
from homeassistant.util import dt as dt_util

from .coordinator import SnapmakerDataUpdateCoordinator
from .pysnapmaker.const import TOP_COVER_FILTER_LIFE_HOURS


# ---------------------------------------------------------------------------
# Sensor descriptions
# ---------------------------------------------------------------------------


def _print_eta(self):
    """Estimated finish time, to the minute.

    It's recalculated on every update (several a second while printing), so
    keep the previous value unless the estimate moved by a minute or more.
    """
    remaining = self.coordinator.data.time_remaining
    if remaining is None:
        self._eta_estimate = self._eta = None
        return None
    estimate = dt_util.utcnow() + timedelta(seconds=remaining)
    previous = getattr(self, "_eta_estimate", None)
    # Compare unrounded estimates: rounded ones flip between two minutes
    # when the estimate sits on a minute boundary.
    if previous is not None and abs(estimate - previous) < timedelta(minutes=1):
        return self._eta
    self._eta_estimate = estimate
    self._eta = estimate.replace(second=0, microsecond=0)
    return self._eta


@dataclass(frozen=True, kw_only=True)
class SnapmakerSensorEntityDescription(SensorEntityDescription):
    """Extends SensorEntityDescription with lambdas used by the entity classes."""

    value_fn: Callable | None = None
    available_fn: Callable = field(default=lambda self: True)
    # Called with the coordinator; return False to skip creating this entity
    exists_fn: Callable = field(default=lambda coordinator: True)
    extra_attributes_fn: Callable = field(default=lambda self: {})
    # Write the state at most this often (seconds; 0 = on every update). For
    # values that change with every push, so they don't flood the recorder.
    min_update_interval: float = 0


# ---------------------------------------------------------------------------
# Binary sensor descriptions
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class SnapmakerBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Extends BinarySensorEntityDescription with lambdas."""

    value_fn: Callable | None = None
    available_fn: Callable = field(default=lambda self: True)
    exists_fn: Callable = field(default=lambda coordinator: True)
    extra_attributes_fn: Callable = field(default=lambda self: {})


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
        min_update_interval=10,
    ),
    SnapmakerSensorEntityDescription(
        key="total_duration",
        translation_key="total_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:timer-outline",
        value_fn=lambda self: int(
            self.coordinator.data.print_stats.total_duration
        ),
        available_fn=lambda self: self.coordinator.data.is_printing
        or self.coordinator.data.is_paused,
        min_update_interval=10,
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
        min_update_interval=10,
    ),
    SnapmakerSensorEntityDescription(
        key="print_eta",
        translation_key="print_eta",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-end",
        value_fn=lambda self: _print_eta(self),
        available_fn=lambda self: self.coordinator.data.is_printing
        and self.coordinator.data.time_remaining is not None,
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
        # Changes with every move while printing; off unless you need it.
        entity_registry_enabled_default=False,
        min_update_interval=10,
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
    SnapmakerSensorEntityDescription(
        key="speed_factor",
        translation_key="speed_factor",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:speedometer",
        value_fn=lambda self: self.coordinator.data.speed_factor_pct,
    ),
    SnapmakerSensorEntityDescription(
        key="flow_rate",
        translation_key="flow_rate",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:water-percent",
        value_fn=lambda self: self.coordinator.data.flow_rate_pct,
    ),
]


# ---------------------------------------------------------------------------
# Chamber / extra temperature sensor template (instantiated per discovered sensor)
# ---------------------------------------------------------------------------

CHAMBER_SENSOR_TEMPLATE = SnapmakerSensorEntityDescription(
    key="chamber_temperature",
    translation_key="chamber_temperature",
    device_class=SensorDeviceClass.TEMPERATURE,
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    state_class=SensorStateClass.MEASUREMENT,
    icon="mdi:thermometer",
)


# ---------------------------------------------------------------------------
# Chamber heaters (Panda Breath / DragonBreath via U1 Extended Firmware)
# ---------------------------------------------------------------------------

_CHAMBER_LABELS = {"dragonbreath": "DragonBreath", "panda_breath": "Panda Breath"}


def chamber_object_name(object_key: str) -> str:
    """'heater_generic panda_breath' -> 'panda_breath'."""
    return object_key.split(" ", 1)[-1]


def chamber_label(object_key: str) -> str:
    """Friendly label for a chamber heater or filter object.

    'heater_generic panda_breath' -> 'Panda Breath',
    'output_pin dragonbreath_filter' -> 'DragonBreath Filter'.
    """
    name = chamber_object_name(object_key).lower()
    for raw, label in _CHAMBER_LABELS.items():
        if name.startswith(raw):
            rest = name[len(raw):].strip("_").replace("_", " ").title()
            return f"{label} {rest}".strip()
    return name.replace("_", " ").title()


# ---------------------------------------------------------------------------
# Official Top Cover (air purifier); created only while the cover is plugged in
# ---------------------------------------------------------------------------

# Purifier mode number -> select option
TOP_COVER_MODES: dict[int, str] = {0: "off", 1: "cooling", 2: "preheat", 3: "keep_warm"}


def _top_cover_ready(self) -> bool:
    data = self.coordinator.data
    return bool(data.is_ready and data.top_cover and data.top_cover.detected)


def _filter_hours(self) -> float:
    return round(self.coordinator.data.top_cover.filter_seconds / 3600, 1)


TOP_COVER_SENSORS: list[SnapmakerSensorEntityDescription] = [
    SnapmakerSensorEntityDescription(
        key="top_cover_filter_used",
        translation_key="top_cover_filter_used",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:air-filter",
        value_fn=_filter_hours,
        available_fn=_top_cover_ready,
    ),
    SnapmakerSensorEntityDescription(
        key="top_cover_filter_life",
        translation_key="top_cover_filter_life",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:air-filter",
        value_fn=lambda self: max(
            0.0, round(100 - _filter_hours(self) / TOP_COVER_FILTER_LIFE_HOURS * 100, 1)
        ),
        available_fn=_top_cover_ready,
        extra_attributes_fn=lambda self: {"filter_life_hours": TOP_COVER_FILTER_LIFE_HOURS},
    ),
    SnapmakerSensorEntityDescription(
        key="top_cover_circulation_rpm",
        translation_key="top_cover_circulation_rpm",
        native_unit_of_measurement="rpm",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:fan",
        value_fn=lambda self: (
            None
            if self.coordinator.data.top_cover.circulation_rpm is None
            else round(self.coordinator.data.top_cover.circulation_rpm)
        ),
        available_fn=_top_cover_ready,
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
