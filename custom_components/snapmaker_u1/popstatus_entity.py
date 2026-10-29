"""Shared base for BIQU accessory entities (PopStatus, PopKnomi).

Each accessory is its own config entry (see ``config_flow``), so it keeps
working when the printer is off. Its entities follow the device's own
WebSocket connection.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, Entity

from .const import (
    CONF_ENTRY_TYPE,
    CONF_HOST,
    CONF_PRINTER_ENTRY_ID,
    DATA_POPSTATUS,
    DOMAIN,
    ENTRY_TYPE_POPKNOMI,
)
from .pysnapmaker.popstatus import PopStatusClient

_MODELS = {ENTRY_TYPE_POPKNOMI: "PopKnomi"}


def popstatus_unique_id_prefix(entry: ConfigEntry) -> str:
    """Prefix of an accessory entity's unique ID, e.g. "popknomi_<entry id>_".

    The frontend maps these to card roles such as ``light.popstatus_light``.
    """
    return f"{entry.data[CONF_ENTRY_TYPE]}_{entry.entry_id}_"


def get_popstatus(hass: HomeAssistant, entry: ConfigEntry) -> PopStatusClient:
    return hass.data[DATA_POPSTATUS][entry.entry_id]


class PopStatusEntity(Entity):
    """An entity of a BIQU accessory, shown as its own device."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: PopStatusClient, key: str
    ) -> None:
        self.popstatus = client
        kind = entry.data[CONF_ENTRY_TYPE]  # "popstatus" or "popknomi"
        model = _MODELS.get(kind, "PopStatus")
        self._attr_unique_id = f"{popstatus_unique_id_prefix(entry)}{key}"
        self._attr_translation_key = f"{kind}_{key}"
        info = DeviceInfo(
            identifiers={(DOMAIN, f"{kind}_{entry.entry_id}")},
            name=model,
            manufacturer="BIQU",
            model=model,
            configuration_url=f"http://{client.host}",
        )
        printer = hass.config_entries.async_get_entry(
            entry.data.get(CONF_PRINTER_ENTRY_ID) or ""
        )
        if printer is not None and printer.data.get(CONF_HOST):
            info["via_device"] = (DOMAIN, printer.data[CONF_HOST])
        self._attr_device_info = info

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.popstatus.register_callback(self.async_write_ha_state))

    @property
    def available(self) -> bool:
        return self.popstatus.data.connected
