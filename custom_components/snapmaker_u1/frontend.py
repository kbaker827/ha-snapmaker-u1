"""Bundled Lovelace card for the Snapmaker U1 integration.

Serves ``www/snapmaker-u1-card.js`` from the integration and registers it as a
dashboard resource, so the ``custom:snapmaker-u1-card`` card works without
adding the resource by hand. A websocket command tells the card which entity
plays which role, so it never has to guess from entity IDs the user may have
renamed.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components import frontend, websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .const import CONF_ENTRY_TYPE, CONF_PRINTER_ENTRY_ID, DOMAIN, is_accessory_entry

_LOGGER = logging.getLogger(__name__)

CARD_FILENAME = "snapmaker-u1-card.js"
CARD_URL_BASE = f"/{DOMAIN}_static"
CARD_URL = f"{CARD_URL_BASE}/{CARD_FILENAME}"
WS_TYPE_ENTITIES = f"{DOMAIN}/entities"
DATA_CARD_URL = f"{DOMAIN}_card_url"


async def async_register_frontend(hass: HomeAssistant) -> None:
    """Serve the card and add it to every dashboard (call once)."""
    www = Path(__file__).parent / "www"
    manifest = json.loads(
        await hass.async_add_executor_job(
            (Path(__file__).parent / "manifest.json").read_text
        )
    )
    version = manifest.get("version", "0")

    try:
        from homeassistant.components.http import StaticPathConfig

        await hass.http.async_register_static_paths(
            [StaticPathConfig(CARD_URL_BASE, str(www), False)]
        )
    except ImportError:  # Home Assistant < 2024.6
        hass.http.register_static_path(CARD_URL_BASE, str(www), False)

    websocket_api.async_register_command(hass, ws_entities)

    # Version query string so browsers pick up a new card after an update.
    url = f"{CARD_URL}?v={version}"
    hass.data[DATA_CARD_URL] = url
    if not await _async_ensure_lovelace_resource(hass, url):
        # YAML-mode dashboards: resources can't be edited from here, so load
        # the card as an extra frontend module instead.
        frontend.add_extra_js_url(hass, url)


def _lovelace_resources(hass: HomeAssistant) -> Any | None:
    """The storage-mode Lovelace resource collection, or None (YAML mode)."""
    data = hass.data.get("lovelace")
    if data is None:
        return None
    resources = getattr(data, "resources", None)
    if resources is None and isinstance(data, dict):  # Home Assistant < 2025.2
        resources = data.get("resources")
    if resources is None or not hasattr(resources, "async_create_item"):
        return None
    return resources


def _is_card_resource(item: dict) -> bool:
    return str(item.get("url", "")).split("?", 1)[0] == CARD_URL


async def _async_ensure_lovelace_resource(hass: HomeAssistant, url: str) -> bool:
    """Add or update the card's dashboard resource. True if handled.

    Loading the card as a dashboard resource is what reliably works on current
    Home Assistant (issue #29); an "extra module" can end up evaluated before
    the frontend's element registry is ready. Existing entries for the card
    (including one a user added by hand) are updated to the current version
    instead of duplicated.
    """
    resources = _lovelace_resources(hass)
    if resources is None:
        return False
    try:
        await resources.async_get_info()  # loads the collection from storage
        ours = [item for item in resources.async_items() if _is_card_resource(item)]
        if not ours:
            await resources.async_create_item({"res_type": "module", "url": url})
            _LOGGER.info("Added the Snapmaker U1 card as a dashboard resource")
            return True
        first, *duplicates = ours
        if first.get("url") != url or first.get("type") != "module":
            await resources.async_update_item(
                first["id"], {"res_type": "module", "url": url}
            )
        for item in duplicates:
            await resources.async_delete_item(item["id"])
        return True
    except Exception:  # noqa: BLE001 - fall back to the extra-module route
        _LOGGER.exception("Could not register the Snapmaker U1 card resource")
        return False


async def async_ensure_card_resource(hass: HomeAssistant) -> None:
    """Make sure the card resource exists (e.g. a printer was re-added).

    Removing the last printer deletes the resource; adding one again without
    restarting doesn't re-run async_setup, so entry setup restores it here.
    """
    url = hass.data.get(DATA_CARD_URL)
    if url:
        await _async_ensure_lovelace_resource(hass, url)


async def async_remove_lovelace_resource(hass: HomeAssistant) -> None:
    """Remove the card's dashboard resource (integration removed)."""
    resources = _lovelace_resources(hass)
    if resources is None:
        return
    try:
        await resources.async_get_info()
        for item in [i for i in resources.async_items() if _is_card_resource(i)]:
            await resources.async_delete_item(item["id"])
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Could not remove the Snapmaker U1 card resource", exc_info=True)


@websocket_api.websocket_command({vol.Required("type"): WS_TYPE_ENTITIES})
@callback
def ws_entities(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Return every Snapmaker U1 printer with its entities keyed by role.

    Roles are ``<domain>.<description key>``, e.g. ``sensor.print_progress``,
    ``sensor.extruder1_temperature`` or ``number.bed_target``.
    """
    registry = er.async_get(hass)
    entries = hass.config_entries.async_entries(DOMAIN)
    printers: list[dict[str, Any]] = []
    for entry in entries:
        if is_accessory_entry(entry):
            continue
        entities: dict[str, str] = {}

        def add_roles(source_entry_id: str, prefix: str, role_prefix: str = "") -> None:
            for reg_entry in er.async_entries_for_config_entry(registry, source_entry_id):
                if reg_entry.disabled_by or not reg_entry.unique_id.startswith(prefix):
                    continue
                key = reg_entry.unique_id[len(prefix):]
                entities[f"{reg_entry.domain}.{role_prefix}{key}"] = reg_entry.entity_id

        add_roles(entry.entry_id, f"{entry.data.get('host', '')}_")
        # BIQU accessories linked to this printer, with roles such as
        # "light.popstatus_light" or "select.popknomi_screen_off".
        for pop in entries:
            if is_accessory_entry(pop) and pop.data.get(CONF_PRINTER_ENTRY_ID) == entry.entry_id:
                kind = pop.data[CONF_ENTRY_TYPE]
                add_roles(pop.entry_id, f"{kind}_{pop.entry_id}_", f"{kind}_")
        coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        printers.append(
            {
                "entry_id": entry.entry_id,
                "title": (
                    coordinator.printer_name if coordinator is not None else entry.title
                ),
                "entities": entities,
            }
        )
    connection.send_result(msg["id"], {"printers": printers})
