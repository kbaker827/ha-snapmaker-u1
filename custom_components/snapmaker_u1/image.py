"""Image platform: preview thumbnail of the file being printed."""
from __future__ import annotations

import asyncio
import logging

import aiohttp
from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import SnapmakerDataUpdateCoordinator
from .sensor import SnapmakerBaseEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SnapmakerDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SnapmakerPrintPreview(hass, coordinator)])


class SnapmakerPrintPreview(SnapmakerBaseEntity, ImageEntity):
    """Thumbnail embedded by the slicer in the current (or last) G-code file.

    The image is fetched from Moonraker when first requested and cached until
    the file name changes. With no file, there is no image.
    """

    _attr_translation_key = "print_preview"
    _attr_content_type = "image/png"

    def __init__(
        self, hass: HomeAssistant, coordinator: SnapmakerDataUpdateCoordinator
    ) -> None:
        SnapmakerBaseEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, hass)
        self._attr_unique_id = f"{coordinator.entry.data['host']}_print_preview"
        self._filename: str | None = None
        self._image: bytes | None = None
        self._track_filename()

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        return {"filename": self._filename}

    @callback
    def _handle_coordinator_update(self) -> None:
        self._track_filename()
        super()._handle_coordinator_update()

    def _track_filename(self) -> None:
        """Drop the cached image when a different file is loaded."""
        data = self.coordinator.data
        filename = (data.print_stats.filename if data else "") or None
        if filename == self._filename and self._attr_image_last_updated:
            return
        self._filename = filename
        self._image = None
        self._attr_image_last_updated = dt_util.utcnow()

    async def async_image(self) -> bytes | None:
        filename = self._filename
        client = self.coordinator.client
        if self._image is not None or not filename or client is None:
            return self._image
        try:
            result = await client.fetch_thumbnail(filename)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            _LOGGER.debug("No preview for %s: %s", filename, exc)
            return None
        if result is None or filename != self._filename:
            return None
        self._image, self._attr_content_type = result
        return self._image
