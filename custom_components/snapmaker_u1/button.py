"""Button platform for the Snapmaker U1 integration."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Coroutine, Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SnapmakerDataUpdateCoordinator
from .pysnapmaker.client import SnapmakerClient
from .sensor import SnapmakerBaseEntity

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class SnapmakerButtonEntityDescription(ButtonEntityDescription):
    """Button description with an async press handler."""

    press_fn: Callable[[SnapmakerClient], Coroutine[Any, Any, None]] | None = None
    # Return False from available_fn to grey the button out
    available_fn: Callable | None = None


BUTTON_DESCRIPTIONS: list[SnapmakerButtonEntityDescription] = [
    SnapmakerButtonEntityDescription(
        key="pause_print",
        translation_key="pause_print",
        icon="mdi:pause-circle",
        press_fn=lambda client: client.pause_print(),
        available_fn=lambda coordinator: coordinator.data.is_printing,
    ),
    SnapmakerButtonEntityDescription(
        key="resume_print",
        translation_key="resume_print",
        icon="mdi:play-circle",
        press_fn=lambda client: client.resume_print(),
        available_fn=lambda coordinator: coordinator.data.is_paused,
    ),
    SnapmakerButtonEntityDescription(
        key="cancel_print",
        translation_key="cancel_print",
        icon="mdi:cancel",
        press_fn=lambda client: client.cancel_print(),
        available_fn=lambda coordinator: coordinator.data.is_printing
        or coordinator.data.is_paused,
    ),
    SnapmakerButtonEntityDescription(
        key="emergency_stop",
        translation_key="emergency_stop",
        icon="mdi:stop-circle",
        press_fn=lambda client: client.emergency_stop(),
    ),
    SnapmakerButtonEntityDescription(
        key="home_all",
        translation_key="home_all",
        icon="mdi:home",
        press_fn=lambda client: client.home_axes(),
        available_fn=lambda coordinator: coordinator.data.is_ready,
    ),
    SnapmakerButtonEntityDescription(
        key="home_x",
        translation_key="home_x",
        icon="mdi:axis-x-arrow",
        press_fn=lambda client: client.home_axes("X"),
        available_fn=lambda coordinator: coordinator.data.is_ready,
    ),
    SnapmakerButtonEntityDescription(
        key="home_y",
        translation_key="home_y",
        icon="mdi:axis-y-arrow",
        press_fn=lambda client: client.home_axes("Y"),
        available_fn=lambda coordinator: coordinator.data.is_ready,
    ),
    SnapmakerButtonEntityDescription(
        key="home_z",
        translation_key="home_z",
        icon="mdi:axis-z-arrow",
        press_fn=lambda client: client.home_axes("Z"),
        available_fn=lambda coordinator: coordinator.data.is_ready,
    ),
    SnapmakerButtonEntityDescription(
        key="restart_klipper",
        translation_key="restart_klipper",
        icon="mdi:restart",
        press_fn=lambda client: client.restart_klipper(),
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Snapmaker U1 buttons from a config entry."""
    coordinator: SnapmakerDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        SnapmakerButton(coordinator, description)
        for description in BUTTON_DESCRIPTIONS
    )


class SnapmakerButton(SnapmakerBaseEntity, ButtonEntity):
    """A button that sends a command to the Snapmaker U1."""

    entity_description: SnapmakerButtonEntityDescription

    def __init__(
        self,
        coordinator: SnapmakerDataUpdateCoordinator,
        description: SnapmakerButtonEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        host = coordinator.entry.data["host"]
        self._attr_unique_id = f"{host}_{description.key}"

    @property
    def available(self) -> bool:
        fn = self.entity_description.available_fn
        if fn is None:
            return True
        try:
            return bool(fn(self.coordinator))
        except Exception:
            return True

    async def async_press(self) -> None:
        """Handle button press."""
        if self.entity_description.press_fn is None:
            return
        client = self.coordinator.client
        if client is None:
            _LOGGER.warning("Cannot press button – client not available")
            return
        try:
            await self.entity_description.press_fn(client)
        except Exception as exc:
            _LOGGER.error(
                "Error pressing button %s: %s", self.entity_description.key, exc
            )
