"""Button platform for the Snapmaker U1 integration."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Coroutine, Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SnapmakerDataUpdateCoordinator
from .definitions import chamber_label
from .pysnapmaker.const import DRAGONBREATH_OBJECT, PANDA_BREATH_OBJECT
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


# Official Top Cover: press after fitting a new filter
TOP_COVER_FILTER_RESET = SnapmakerButtonEntityDescription(
    key="top_cover_reset_filter",
    translation_key="top_cover_reset_filter",
    icon="mdi:air-filter",
    press_fn=lambda client: client.reset_top_cover_filter(),
    available_fn=lambda coordinator: bool(
        coordinator.data.is_ready
        and coordinator.data.top_cover
        and coordinator.data.top_cover.detected
    ),
)


def _chamber_device_ready(coordinator, key: str) -> bool:
    device = coordinator.data.chamber_devices.get(key)
    return bool(coordinator.data.is_ready and device and device.connected)


def chamber_device_buttons(key: str) -> list[SnapmakerButtonEntityDescription]:
    """Panda Breath drying start/stop; DragonBreath fault reset."""
    label = chamber_label(key)
    if key == PANDA_BREATH_OBJECT:
        return [
            SnapmakerButtonEntityDescription(
                key=f"heater_{key}_start_drying",
                name=f"{label} Start Drying",
                icon="mdi:tumble-dryer",
                # Uses the Drying Temperature / Drying Time numbers
                press_fn=lambda client: client.start_filament_drying(
                    client.drying_temperature, client.drying_hours
                ),
                available_fn=lambda coordinator: _chamber_device_ready(coordinator, key)
                and not coordinator.data.chamber_devices[key].drying,
            ),
            SnapmakerButtonEntityDescription(
                key=f"heater_{key}_stop_drying",
                name=f"{label} Stop Drying",
                icon="mdi:tumble-dryer-off",
                press_fn=lambda client: client.stop_filament_drying(),
                available_fn=lambda coordinator: _chamber_device_ready(coordinator, key)
                and bool(coordinator.data.chamber_devices[key].drying),
            ),
        ]
    if key == DRAGONBREATH_OBJECT:
        return [
            SnapmakerButtonEntityDescription(
                key=f"heater_{key}_reset_fault",
                name=f"{label} Reset Fault",
                icon="mdi:alert-octagon-outline",
                press_fn=lambda client: client.reset_dragonbreath_fault(),
                available_fn=lambda coordinator: _chamber_device_ready(coordinator, key),
            )
        ]
    return []


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Snapmaker U1 buttons from a config entry."""
    coordinator: SnapmakerDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    descriptions = list(BUTTON_DESCRIPTIONS)
    if coordinator.client and coordinator.client.has_top_cover:
        descriptions.append(TOP_COVER_FILTER_RESET)
    for key in coordinator.client.chamber_device_keys if coordinator.client else []:
        descriptions.extend(chamber_device_buttons(key))
    async_add_entities(
        SnapmakerButton(coordinator, description) for description in descriptions
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
        if not super().available:
            return False
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
        except ValueError as exc:
            raise ServiceValidationError(str(exc)) from exc
        except Exception as exc:
            _LOGGER.error(
                "Error pressing button %s: %s", self.entity_description.key, exc
            )
