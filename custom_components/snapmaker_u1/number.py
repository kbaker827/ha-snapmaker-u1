"""Number platform for the Snapmaker U1 integration.

Provides interactive setpoint controls for bed temperature, nozzle temperatures,
and part-cooling fan speed directly from the Home Assistant dashboard.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature, PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SnapmakerDataUpdateCoordinator
from .pysnapmaker.client import SnapmakerClient
from .sensor import SnapmakerBaseEntity

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class SnapmakerNumberEntityDescription(NumberEntityDescription):
    """Number entity description with value/set callbacks."""

    value_fn: Callable | None = None
    set_fn: Callable[[SnapmakerClient, float], Coroutine[Any, Any, None]] | None = None
    available_fn: Callable = field(default=lambda coordinator: True)


# ---------------------------------------------------------------------------
# Static number entities (not per-extruder)
# ---------------------------------------------------------------------------

STATIC_NUMBER_DESCRIPTIONS: list[SnapmakerNumberEntityDescription] = [
    SnapmakerNumberEntityDescription(
        key="bed_target",
        translation_key="bed_target",
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=0,
        native_max_value=120,
        native_step=1,
        mode=NumberMode.BOX,
        icon="mdi:radiator",
        value_fn=lambda coordinator: coordinator.data.heater_bed.target,
        set_fn=lambda client, v: client.set_bed_temperature(v),
    ),
    SnapmakerNumberEntityDescription(
        key="fan_speed",
        translation_key="fan_speed_setpoint",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        mode=NumberMode.SLIDER,
        icon="mdi:fan",
        value_fn=lambda coordinator: coordinator.data.fan.speed,
        set_fn=lambda client, v: client.set_fan_speed(int(v)),
        available_fn=lambda coordinator: coordinator.data.is_ready,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Snapmaker U1 number entities from a config entry."""
    coordinator: SnapmakerDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[NumberEntity] = []

    # Static entities (bed temp, fan speed)
    for desc in STATIC_NUMBER_DESCRIPTIONS:
        entities.append(SnapmakerNumberEntity(coordinator, desc))

    # Per-extruder nozzle temperature setpoints
    extruder_count = max(
        coordinator.data.extruder_count if coordinator.data else 0, 1
    )
    for i in range(extruder_count):
        extruder_key = "extruder" if i == 0 else f"extruder{i}"
        label = f"T{i}"
        desc = SnapmakerNumberEntityDescription(
            key=f"{extruder_key}_target",
            translation_key="nozzle_target_setpoint",
            device_class=NumberDeviceClass.TEMPERATURE,
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
            native_min_value=0,
            native_max_value=300,
            native_step=1,
            mode=NumberMode.BOX,
            icon="mdi:thermometer-lines",
            value_fn=_make_nozzle_value_fn(extruder_key),
            set_fn=_make_nozzle_set_fn(i),
        )
        entities.append(
            SnapmakerExtruderNumberEntity(coordinator, desc, extruder_key, label)
        )

    async_add_entities(entities)


def _make_nozzle_value_fn(extruder_key: str) -> Callable:
    def fn(coordinator: SnapmakerDataUpdateCoordinator) -> float | None:
        ed = coordinator.data.extruders.get(extruder_key)
        return ed.target if ed is not None else None

    return fn


def _make_nozzle_set_fn(index: int) -> Callable:
    async def fn(client: SnapmakerClient, value: float) -> None:
        await client.set_nozzle_temperature(value, index)

    return fn


# ---------------------------------------------------------------------------
# Entity classes
# ---------------------------------------------------------------------------


class SnapmakerNumberEntity(SnapmakerBaseEntity, NumberEntity):
    """A number entity for controlling a Snapmaker U1 setpoint."""

    entity_description: SnapmakerNumberEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SnapmakerDataUpdateCoordinator,
        description: SnapmakerNumberEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        host = coordinator.entry.data["host"]
        self._attr_unique_id = f"{host}_{description.key}"

    @property
    def native_value(self) -> float | None:
        if self.entity_description.value_fn is None:
            return None
        try:
            return self.entity_description.value_fn(self.coordinator)
        except Exception:
            return None

    @property
    def available(self) -> bool:
        try:
            return self.entity_description.available_fn(self.coordinator)
        except Exception:
            return True

    async def async_set_native_value(self, value: float) -> None:
        """Handle the user changing the value in HA."""
        if self.entity_description.set_fn is None:
            return
        client = self.coordinator.client
        if client is None:
            _LOGGER.warning("Cannot set value – client not available")
            return
        try:
            await self.entity_description.set_fn(client, value)
        except Exception as exc:
            _LOGGER.error(
                "Error setting %s to %s: %s", self.entity_description.key, value, exc
            )


class SnapmakerExtruderNumberEntity(SnapmakerNumberEntity):
    """Number entity for a specific extruder nozzle temperature setpoint."""

    def __init__(
        self,
        coordinator: SnapmakerDataUpdateCoordinator,
        description: SnapmakerNumberEntityDescription,
        extruder_key: str,
        label: str,
    ) -> None:
        super().__init__(coordinator, description)
        host = coordinator.entry.data["host"]
        self._attr_unique_id = f"{host}_{extruder_key}_target_setpoint"
        self._attr_name = f"{label} Target Temperature"
