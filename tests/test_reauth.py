"""Rejected API key -> Home Assistant asks for a new one (reauth).

Previously a wrong or rotated key looked like "printer unreachable", so setup
was retried forever with no hint that the key was the problem.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("homeassistant")

from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

import custom_components.snapmaker_u1 as integration
from custom_components.snapmaker_u1.config_flow import SnapmakerConfigFlow
from custom_components.snapmaker_u1.const import CONF_API_KEY, CONF_HOST, CONF_PORT
from custom_components.snapmaker_u1.coordinator import SnapmakerDataUpdateCoordinator
from custom_components.snapmaker_u1.pysnapmaker import SnapmakerAuthError
from custom_components.snapmaker_u1.pysnapmaker.client import (
    CONNECTION_CANNOT_CONNECT,
    CONNECTION_INVALID_AUTH,
    CONNECTION_OK,
)

CHECK = "custom_components.snapmaker_u1.config_flow.SnapmakerClient.async_check_connection"
ENTRY_DATA = {CONF_HOST: "192.0.2.10", CONF_PORT: 80, CONF_API_KEY: "old-key"}


@pytest.fixture
def entry():
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.data = dict(ENTRY_DATA)
    entry.update_listeners = []  # setup failed on the old key
    return entry


@pytest.fixture
def flow(entry):
    flow = SnapmakerConfigFlow()
    flow.hass = MagicMock()
    flow.hass.config_entries.async_get_entry.return_value = entry
    flow.context = {"source": "reauth", "entry_id": entry.entry_id}
    flow.handler = "snapmaker_u1"
    flow.flow_id = "flow1"
    return flow


async def test_reauth_shows_api_key_form(flow):
    result = await flow.async_step_reauth(ENTRY_DATA)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"]["host"] == "192.0.2.10"


async def test_reauth_with_valid_key_updates_entry_and_reloads(flow, entry):
    config_entries = flow.hass.config_entries
    with patch(CHECK, AsyncMock(return_value=CONNECTION_OK)) as check:
        result = await flow.async_step_reauth_confirm({CONF_API_KEY: "  new-key  "})

    check.assert_awaited_once_with("192.0.2.10", 80, "new-key")
    config_entries.async_update_entry.assert_called_once_with(
        entry, data={**ENTRY_DATA, CONF_API_KEY: "new-key"}
    )
    config_entries.async_schedule_reload.assert_called_once_with("entry1")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"


async def test_reauth_reloads_on_home_assistant_without_schedule_reload(flow):
    # async_schedule_reload arrived in Home Assistant 2024.2; 2024.1 is supported.
    del flow.hass.config_entries.async_schedule_reload
    with patch(CHECK, AsyncMock(return_value=CONNECTION_OK)):
        result = await flow.async_step_reauth_confirm({CONF_API_KEY: "new-key"})

    flow.hass.config_entries.async_reload.assert_called_once_with("entry1")
    flow.hass.async_create_task.assert_called_once()
    assert result["reason"] == "reauth_successful"


async def test_reauth_on_loaded_entry_leaves_reload_to_update_listener(flow, entry):
    entry.update_listeners = [object()]
    with patch(CHECK, AsyncMock(return_value=CONNECTION_OK)):
        result = await flow.async_step_reauth_confirm({CONF_API_KEY: "new-key"})

    flow.hass.config_entries.async_update_entry.assert_called_once()
    flow.hass.config_entries.async_schedule_reload.assert_not_called()
    assert result["reason"] == "reauth_successful"


async def test_reauth_with_blank_key_clears_it(flow, entry):
    with patch(CHECK, AsyncMock(return_value=CONNECTION_OK)) as check:
        await flow.async_step_reauth_confirm({CONF_API_KEY: ""})

    check.assert_awaited_once_with("192.0.2.10", 80, None)
    data = flow.hass.config_entries.async_update_entry.call_args.kwargs["data"]
    assert data[CONF_API_KEY] is None


@pytest.mark.parametrize(
    ("status", "error"),
    [(CONNECTION_INVALID_AUTH, "invalid_auth"), (CONNECTION_CANNOT_CONNECT, "cannot_connect")],
)
async def test_reauth_failure_shows_error(flow, entry, status, error):
    with patch(CHECK, AsyncMock(return_value=status)):
        result = await flow.async_step_reauth_confirm({CONF_API_KEY: "bad"})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
    flow.hass.config_entries.async_update_entry.assert_not_called()


async def test_add_printer_reports_invalid_auth(flow):
    flow.context = {"source": "user"}
    with patch(CHECK, AsyncMock(return_value=CONNECTION_INVALID_AUTH)):
        result = await flow.async_step_printer(
            {CONF_HOST: "192.0.2.10", CONF_PORT: 80, CONF_API_KEY: "bad"}
        )

    assert result["errors"] == {"base": "invalid_auth"}


async def test_setup_entry_raises_auth_failed_on_rejected_key(entry):
    hass = MagicMock()
    entry.data = dict(ENTRY_DATA)
    coordinator = MagicMock()
    coordinator.async_setup = AsyncMock(side_effect=SnapmakerAuthError("401"))
    with patch.object(integration, "SnapmakerDataUpdateCoordinator", return_value=coordinator):
        with pytest.raises(ConfigEntryAuthFailed):
            await integration.async_setup_entry(hass, entry)


async def test_poll_raises_auth_failed_when_key_is_rejected_later():
    coordinator = MagicMock(spec=SnapmakerDataUpdateCoordinator)
    coordinator._client = MagicMock(connected=False)
    coordinator._client.fetch_state = AsyncMock(side_effect=SnapmakerAuthError("401"))

    with pytest.raises(ConfigEntryAuthFailed):
        await SnapmakerDataUpdateCoordinator._async_update_data(coordinator)


async def test_poll_other_errors_still_update_failed():
    coordinator = MagicMock(spec=SnapmakerDataUpdateCoordinator)
    coordinator._client = MagicMock(connected=False)
    coordinator._client.fetch_state = AsyncMock(side_effect=OSError("down"))

    with pytest.raises(UpdateFailed):
        await SnapmakerDataUpdateCoordinator._async_update_data(coordinator)
