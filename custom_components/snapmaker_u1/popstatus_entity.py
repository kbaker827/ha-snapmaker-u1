"""Shared base for BIQU PopStatus entities."""
from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SnapmakerDataUpdateCoordinator
from .pysnapmaker.popstatus import PopStatusClient


class PopStatusEntity(CoordinatorEntity[SnapmakerDataUpdateCoordinator]):
    """An entity of the PopStatus light bar, shown as its own device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SnapmakerDataUpdateCoordinator, key: str) -> None:
        super().__init__(coordinator)
        host = coordinator.entry.data["host"]
        self._attr_unique_id = f"{host}_popstatus_{key}"
        self._attr_translation_key = f"popstatus_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{host}_popstatus")},
            name="PopStatus",
            manufacturer="BIQU",
            model="PopStatus",
            via_device=(DOMAIN, host),
            configuration_url=f"http://{self.popstatus.host}",
        )

    @property
    def popstatus(self) -> PopStatusClient:
        return self.coordinator.popstatus

    @property
    def available(self) -> bool:
        # Independent of the printer: the light bar has its own connection.
        return self.popstatus.data.connected
