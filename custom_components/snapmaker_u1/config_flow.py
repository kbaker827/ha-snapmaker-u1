"""Config flow for the Snapmaker U1 integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_API_KEY,
    CONF_HOST,
    CONF_POPSTATUS_HOST,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_TEMPERATURE_UNIT,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TEMPERATURE_UNIT,
    DOMAIN,
    TEMP_UNIT_CELSIUS,
    TEMP_UNIT_FAHRENHEIT,
    TEMP_UNIT_SYSTEM,
)
from .pysnapmaker.client import SnapmakerClient
from .pysnapmaker.popstatus import PopStatusClient

_LOGGER = logging.getLogger(__name__)

_STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=65535)
        ),
        vol.Optional(CONF_API_KEY, default=""): str,
    }
)


class SnapmakerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Snapmaker U1."""

    VERSION = 1

    @staticmethod
    @config_entries.callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SnapmakerOptionsFlow:
        """Return the options flow handler."""
        return SnapmakerOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step – enter printer IP / port / API key."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input.get(CONF_PORT, DEFAULT_PORT)
            api_key = user_input.get(CONF_API_KEY, "").strip() or None

            # Test connectivity before saving
            ok = await SnapmakerClient.test_connection(host, port, api_key)
            if ok:
                unique_id = f"snapmaker_u1_{host}_{port}"
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"Snapmaker U1 ({host})",
                    data={
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_API_KEY: api_key,
                    },
                )
            else:
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=_STEP_USER_SCHEMA,
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle reconfiguration of an existing entry."""
        errors: dict[str, str] = {}
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input.get(CONF_PORT, DEFAULT_PORT)
            api_key = user_input.get(CONF_API_KEY, "").strip() or None

            ok = await SnapmakerClient.test_connection(host, port, api_key)
            if ok:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_API_KEY: api_key,
                    },
                )
            else:
                errors["base"] = "cannot_connect"

        # Pre-fill the form with current values
        current_data = entry.data if entry else {}
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_HOST, default=current_data.get(CONF_HOST, "")
                ): str,
                vol.Optional(
                    CONF_PORT, default=current_data.get(CONF_PORT, DEFAULT_PORT)
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
                vol.Optional(
                    CONF_API_KEY, default=current_data.get(CONF_API_KEY) or ""
                ): str,
            }
        )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            errors=errors,
        )


class SnapmakerOptionsFlow(config_entries.OptionsFlow):
    """Handle options for the Snapmaker U1 integration."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        # Newer Home Assistant provides a read-only `config_entry` property and
        # raises if it's assigned, which broke this dialog; keep our own copy.
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            popstatus = (user_input.get(CONF_POPSTATUS_HOST) or "").strip()
            user_input[CONF_POPSTATUS_HOST] = popstatus
            if popstatus and not await PopStatusClient.test_connection(
                popstatus, async_get_clientsession(self.hass)
            ):
                errors[CONF_POPSTATUS_HOST] = "popstatus_cannot_connect"
            else:
                return self.async_create_entry(title="", data=user_input)

        current_interval = self._entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=current_interval,
                ): vol.All(vol.Coerce(int), vol.Range(min=10, max=300)),
                vol.Optional(
                    CONF_TEMPERATURE_UNIT,
                    default=self._entry.options.get(
                        CONF_TEMPERATURE_UNIT, DEFAULT_TEMPERATURE_UNIT
                    ),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            TEMP_UNIT_CELSIUS,
                            TEMP_UNIT_FAHRENHEIT,
                            TEMP_UNIT_SYSTEM,
                        ],
                        translation_key=CONF_TEMPERATURE_UNIT,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_POPSTATUS_HOST,
                    description={
                        "suggested_value": (user_input or self._entry.options).get(
                            CONF_POPSTATUS_HOST, ""
                        )
                    },
                ): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
