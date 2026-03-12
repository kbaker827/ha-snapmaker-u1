"""Binary sensor platform for the Snapmaker U1 integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
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

    async_add_entities(
        SnapmakerBinarySensor(coordinator, description)
        for description in PRINTER_BINARY_SENSORS
        if description.exists_fn(coordinator)
    )


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
