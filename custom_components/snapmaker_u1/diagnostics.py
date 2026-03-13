"""Diagnostics support for the Snapmaker U1 integration.

Accessible via Settings → Devices & Services → Snapmaker U1 → Download diagnostics.
Sensitive values (API key) are automatically redacted.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_API_KEY, DOMAIN
from .coordinator import SnapmakerDataUpdateCoordinator

_REDACT = {CONF_API_KEY}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: SnapmakerDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data

    config_info = async_redact_data(dict(entry.data), _REDACT)

    extruder_info: dict[str, Any] = {}
    if data:
        for key, ext in data.extruders.items():
            extruder_info[key] = {
                "temperature": ext.temperature,
                "target": ext.target,
                "power": round(ext.power, 3),
                "can_extrude": ext.can_extrude,
            }

    return {
        "config_entry": config_info,
        "printer": {
            "name": data.printer_name if data else "unknown",
            "firmware_version": data.firmware_version if data else "unknown",
            "klipper_state": data.klipper_state if data else "unknown",
            "klipper_message": data.klipper_message if data else "",
            "extruder_count": data.extruder_count if data else 0,
        },
        "connection": {
            "websocket_connected": (
                coordinator.client.connected
                if coordinator.client
                else False
            ),
        },
        "print_job": {
            "state": data.print_stats.state if data else "unknown",
            "filename": data.print_stats.filename if data else "",
            "progress_pct": data.print_progress_pct if data else 0,
            "current_layer": data.print_stats.current_layer if data else 0,
            "total_layers": data.print_stats.total_layer if data else 0,
            "print_duration_s": int(data.print_stats.print_duration) if data else 0,
            "filament_used_mm": round(data.print_stats.filament_used, 1) if data else 0,
        },
        "temperatures": {
            "bed": {
                "current": data.heater_bed.temperature if data else 0,
                "target": data.heater_bed.target if data else 0,
                "power": round(data.heater_bed.power, 3) if data else 0,
            },
            "extruders": extruder_info,
        },
        "toolhead": {
            "position": data.toolhead.position if data else [0, 0, 0, 0],
            "homed_axes": data.toolhead.homed_axes if data else "",
        },
        "fan": {
            "speed_pct": data.fan.speed if data else 0,
        },
    }
