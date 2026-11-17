"""A command the printer rejects must fail in the UI, not just in the log.

These handlers used to catch every error, log it and return, so Home Assistant
reported success (and automations carried on) even though nothing happened.
They now raise HomeAssistantError, which the UI shows as a failed action.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("homeassistant")

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.snapmaker_u1.button import (
    SnapmakerButton,
    SnapmakerButtonEntityDescription,
)
from custom_components.snapmaker_u1.number import (
    SnapmakerNumberEntity,
    SnapmakerNumberEntityDescription,
)
from custom_components.snapmaker_u1.select import ActiveToolSelect
from custom_components.snapmaker_u1.switch import (
    ChamberFilterSwitch,
    TimelapseSwitch,
    WorkLightSwitch,
)


@pytest.fixture
def coordinator():
    coordinator = MagicMock()
    coordinator.entry.data = {"host": "192.0.2.10"}
    coordinator.client = MagicMock()
    return coordinator


async def _async_failing(*_args, **_kwargs):
    raise RuntimeError("printer said no")


async def _async_bad_value(*_args, **_kwargs):
    raise ValueError("target below min_temp")


# --- button -----------------------------------------------------------------


def _button(coordinator, press_fn):
    description = SnapmakerButtonEntityDescription(key="pause", press_fn=press_fn)
    return SnapmakerButton(coordinator, description)


async def test_button_press_failure_raises(coordinator):
    button = _button(coordinator, _async_failing)

    with pytest.raises(HomeAssistantError, match="printer said no"):
        await button.async_press()


async def test_button_press_value_error_stays_validation_error(coordinator):
    button = _button(coordinator, _async_bad_value)

    with pytest.raises(ServiceValidationError):
        await button.async_press()


async def test_button_press_without_client_raises(coordinator):
    coordinator.client = None
    button = _button(coordinator, _async_failing)

    with pytest.raises(HomeAssistantError):
        await button.async_press()


async def test_button_press_success_calls_client(coordinator):
    press_fn = AsyncMock()
    button = _button(coordinator, press_fn)

    await button.async_press()

    press_fn.assert_awaited_once_with(coordinator.client)


# --- number -----------------------------------------------------------------


def _number(coordinator, set_fn):
    description = SnapmakerNumberEntityDescription(key="bed_target", set_fn=set_fn)
    return SnapmakerNumberEntity(coordinator, description)


async def test_number_set_failure_raises(coordinator):
    number = _number(coordinator, _async_failing)

    with pytest.raises(HomeAssistantError, match="printer said no"):
        await number.async_set_native_value(60)


async def test_number_value_error_stays_validation_error(coordinator):
    number = _number(coordinator, _async_bad_value)

    with pytest.raises(ServiceValidationError):
        await number.async_set_native_value(5)


async def test_number_set_without_client_raises(coordinator):
    coordinator.client = None
    number = _number(coordinator, AsyncMock())

    with pytest.raises(HomeAssistantError):
        await number.async_set_native_value(60)


# --- switch -----------------------------------------------------------------


async def test_work_light_failure_raises_and_keeps_state(coordinator):
    coordinator.client.set_work_light = AsyncMock(side_effect=RuntimeError("printer said no"))
    coordinator.data.work_light_on = False
    switch = WorkLightSwitch(coordinator)

    with pytest.raises(HomeAssistantError, match="printer said no"):
        await switch.async_turn_on()

    assert switch.is_on is False


async def test_work_light_without_client_raises(coordinator):
    coordinator.client = None
    switch = WorkLightSwitch(coordinator)

    with pytest.raises(HomeAssistantError):
        await switch.async_turn_on()


async def test_chamber_filter_failure_raises(coordinator):
    coordinator.client.set_chamber_filter = AsyncMock(side_effect=RuntimeError("printer said no"))
    switch = ChamberFilterSwitch(coordinator, "output_pin dragonbreath_filter")

    with pytest.raises(HomeAssistantError, match="printer said no"):
        await switch.async_turn_on()


async def test_timelapse_failure_raises(coordinator):
    coordinator.client.set_timelapse_enabled = AsyncMock(
        side_effect=RuntimeError("printer said no")
    )
    switch = TimelapseSwitch(coordinator)

    with pytest.raises(HomeAssistantError, match="printer said no"):
        await switch.async_turn_off()


# --- select -----------------------------------------------------------------


async def test_active_tool_failure_raises(coordinator):
    coordinator.client.set_active_tool = AsyncMock(side_effect=RuntimeError("printer said no"))
    select = ActiveToolSelect(coordinator, extruder_count=4)

    with pytest.raises(HomeAssistantError, match="printer said no"):
        await select.async_select_option("T2")


async def test_active_tool_without_client_raises(coordinator):
    coordinator.client = None
    select = ActiveToolSelect(coordinator, extruder_count=4)

    with pytest.raises(HomeAssistantError):
        await select.async_select_option("T2")
