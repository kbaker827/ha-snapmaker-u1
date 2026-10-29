"""Light platform: the BIQU PopStatus light bar (optional)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SnapmakerDataUpdateCoordinator
from .popstatus_entity import PopStatusEntity
from .pysnapmaker.popstatus import POPSTATUS_LIGHT_MODES


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SnapmakerDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.popstatus is not None:
        async_add_entities([PopStatusLight(coordinator)])


class PopStatusLight(PopStatusEntity, LightEntity):
    """On/off and brightness of the PopStatus light bar.

    Brightness is the Simple mode brightness; in Advanced mode each printer
    state has its own effect and brightness, set in the PopStatus web UI.
    """

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, coordinator: SnapmakerDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "light")

    @property
    def is_on(self) -> bool | None:
        return self.popstatus.data.light_on

    @property
    def brightness(self) -> int | None:
        pct = self.popstatus.data.brightness
        return None if pct is None else round(pct * 255 / 100)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.popstatus.data
        return {
            "light_mode": POPSTATUS_LIGHT_MODES.get(data.light_mode),
            "printer_connected": data.printer_linked,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        try:
            if not self.popstatus.data.light_on:
                await self.popstatus.set_light(True)
            if ATTR_BRIGHTNESS in kwargs:
                await self.popstatus.set_brightness(
                    max(1, round(kwargs[ATTR_BRIGHTNESS] * 100 / 255))
                )
        except ConnectionError as exc:
            raise HomeAssistantError(str(exc)) from exc

    async def async_turn_off(self, **kwargs: Any) -> None:
        try:
            await self.popstatus.set_light(False)
        except ConnectionError as exc:
            raise HomeAssistantError(str(exc)) from exc
