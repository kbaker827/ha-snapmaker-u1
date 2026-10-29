"""Config flow for the Snapmaker U1 integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_API_KEY,
    CONF_ENTRY_TYPE,
    CONF_HOST,
    CONF_PORT,
    CONF_PRINTER_ENTRY_ID,
    CONF_SCAN_INTERVAL,
    CONF_TEMPERATURE_UNIT,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TEMPERATURE_UNIT,
    DOMAIN,
    ENTRY_TYPE_POPKNOMI,
    ENTRY_TYPE_POPSTATUS,
    TEMP_UNIT_CELSIUS,
    TEMP_UNIT_FAHRENHEIT,
    TEMP_UNIT_SYSTEM,
    is_accessory_entry,
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


# BIQU accessories that can be added: entry type -> display name
_ACCESSORY_NAMES = {ENTRY_TYPE_POPSTATUS: "PopStatus", ENTRY_TYPE_POPKNOMI: "PopKnomi"}


def _accessory_unique_id(kind: str, host: str) -> str:
    return f"{kind}_{host.strip().lower()}"


class SnapmakerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Snapmaker U1."""

    VERSION = 1

    @staticmethod
    @config_entries.callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SnapmakerOptionsFlow:
        """Return the options flow handler."""
        return SnapmakerOptionsFlow(config_entry)

    @classmethod
    @callback
    def async_supports_options_flow(cls, config_entry: ConfigEntry) -> bool:
        """Options (poll interval, units) are for printers only."""
        return not is_accessory_entry(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Ask what to add: a printer or one of BIQU's accessories."""
        return self.async_show_menu(
            step_id="user", menu_options=["printer", "popstatus", "popknomi"]
        )

    async def async_step_popstatus(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Add a BIQU PopStatus light bar."""
        return await self._async_step_accessory(ENTRY_TYPE_POPSTATUS, user_input)

    async def async_step_popknomi(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Add a BIQU PopKnomi status display."""
        return await self._async_step_accessory(ENTRY_TYPE_POPKNOMI, user_input)

    async def _async_step_accessory(
        self, kind: str, user_input: dict[str, Any] | None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            await self.async_set_unique_id(_accessory_unique_id(kind, host))
            self._abort_if_unique_id_configured()
            # Both devices use the same WebSocket and push their settings on connect.
            if await PopStatusClient.test_connection(
                host, async_get_clientsession(self.hass)
            ):
                return self.async_create_entry(
                    title=f"{_ACCESSORY_NAMES[kind]} ({host})",
                    data={
                        CONF_ENTRY_TYPE: kind,
                        CONF_HOST: host,
                        CONF_PRINTER_ENTRY_ID: user_input.get(CONF_PRINTER_ENTRY_ID, ""),
                    },
                )
            errors["base"] = "accessory_cannot_connect"
        return self.async_show_form(
            step_id=kind,
            data_schema=self._popstatus_schema(user_input or {}),
            errors=errors,
        )

    def _popstatus_schema(self, current: dict[str, Any]) -> vol.Schema:
        """Address, plus which printer it belongs to (for the dashboard card)."""
        printers = [
            e
            for e in self.hass.config_entries.async_entries(DOMAIN)
            if not is_accessory_entry(e)
        ]
        fields: dict[Any, Any] = {
            vol.Required(CONF_HOST, default=current.get(CONF_HOST, "")): str,
        }
        if printers:
            fields[
                vol.Optional(
                    CONF_PRINTER_ENTRY_ID,
                    default=current.get(CONF_PRINTER_ENTRY_ID) or printers[0].entry_id,
                )
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=[{"value": e.entry_id, "label": e.title} for e in printers],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
        return vol.Schema(fields)

    async def async_step_printer(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Add a printer – enter its IP / port / API key."""
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
            step_id="printer",
            data_schema=_STEP_USER_SCHEMA,
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle reconfiguration of an existing entry."""
        errors: dict[str, str] = {}
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is not None and is_accessory_entry(entry):
            return await self.async_step_reconfigure_accessory(user_input)

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


    async def async_step_reconfigure_accessory(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Change a PopStatus / PopKnomi's address or printer."""
        errors: dict[str, str] = {}
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        kind = entry.data[CONF_ENTRY_TYPE]
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            unique_id = _accessory_unique_id(kind, host)
            # The add step uses the address to spot duplicates, so keep the ID
            # in step with it and don't move onto another entry's address.
            if any(
                e.unique_id == unique_id and e.entry_id != entry.entry_id
                for e in self.hass.config_entries.async_entries(DOMAIN)
            ):
                return self.async_abort(reason="already_configured")
            if await PopStatusClient.test_connection(
                host, async_get_clientsession(self.hass)
            ):
                self.hass.config_entries.async_update_entry(entry, unique_id=unique_id)
                return self.async_update_reload_and_abort(
                    entry,
                    title=f"{_ACCESSORY_NAMES[kind]} ({host})",
                    data_updates={
                        CONF_HOST: host,
                        CONF_PRINTER_ENTRY_ID: user_input.get(CONF_PRINTER_ENTRY_ID, ""),
                    },
                )
            errors["base"] = "accessory_cannot_connect"
        return self.async_show_form(
            step_id="reconfigure_accessory",
            data_schema=self._popstatus_schema(user_input or dict(entry.data)),
            errors=errors,
            description_placeholders={"device": _ACCESSORY_NAMES[kind]},
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
        if user_input is not None:
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
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
