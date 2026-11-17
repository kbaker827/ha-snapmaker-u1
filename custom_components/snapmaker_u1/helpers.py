"""Small Home Assistant helpers shared across the integration."""
from __future__ import annotations

from homeassistant.core import HomeAssistant


def schedule_entry_reload(hass: HomeAssistant, entry_id: str) -> None:
    """Reload a config entry without waiting for it.

    ``async_schedule_reload`` arrived in Home Assistant 2024.2; older versions
    get the reload as a task instead.
    """
    schedule = getattr(hass.config_entries, "async_schedule_reload", None)
    if schedule is not None:
        schedule(entry_id)
    else:  # Home Assistant < 2024.2
        hass.async_create_task(hass.config_entries.async_reload(entry_id))
