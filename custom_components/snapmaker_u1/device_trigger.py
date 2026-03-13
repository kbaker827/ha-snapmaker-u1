"""Device automation triggers for the Snapmaker U1 integration.

Registers five triggers so users can create HA automations based on
print-state transitions without having to listen to raw event bus events.

Trigger types (appear in the Automations UI under the device):
  - print_started
  - print_complete
  - print_failed
  - print_paused
  - print_cancelled
"""
from __future__ import annotations

import voluptuous as vol
from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_PLATFORM, CONF_TYPE
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo

from .const import DOMAIN
from .pysnapmaker.const import (
    EVENT_PRINT_CANCELLED,
    EVENT_PRINT_COMPLETE,
    EVENT_PRINT_FAILED,
    EVENT_PRINT_PAUSED,
    EVENT_PRINT_STARTED,
)

# Map trigger type → HA event name
_TRIGGER_EVENT_MAP: dict[str, str] = {
    "print_started": EVENT_PRINT_STARTED,
    "print_complete": EVENT_PRINT_COMPLETE,
    "print_failed": EVENT_PRINT_FAILED,
    "print_paused": EVENT_PRINT_PAUSED,
    "print_cancelled": EVENT_PRINT_CANCELLED,
}

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(_TRIGGER_EVENT_MAP),
    }
)


async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> list[dict]:
    """Return a list of triggers for the given device."""
    return [
        {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device_id,
            CONF_TYPE: trigger_type,
        }
        for trigger_type in _TRIGGER_EVENT_MAP
    ]


async def async_attach_trigger(
    hass: HomeAssistant,
    config: dict,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Attach a trigger for a given event type."""
    trigger_type = config[CONF_TYPE]
    event_name = _TRIGGER_EVENT_MAP[trigger_type]
    device_id = config[CONF_DEVICE_ID]

    event_config = event_trigger.TRIGGER_SCHEMA(
        {
            event_trigger.CONF_PLATFORM: "event",
            event_trigger.CONF_EVENT_TYPE: event_name,
            event_trigger.CONF_EVENT_DATA: {"device_id": device_id},
        }
    )
    return await event_trigger.async_attach_trigger(
        hass, event_config, action, trigger_info, platform_type="device"
    )
