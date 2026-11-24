"""Tests for setup, sensors, actions and diagnostics of LichtBlick."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.lichtblick.api import (
    LichtblickAuthError,
    LichtblickConnectionError,
)
from custom_components.lichtblick.const import DOMAIN
from custom_components.lichtblick.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import BERLIN, build_prices

NOW = datetime(2026, 11, 23, 13, 5, tzinfo=BERLIN)


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _state(hass: HomeAssistant, key: str, *, enable: bool = False) -> str:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"4711_{key}")
    assert entity_id, key
    state = hass.states.get(entity_id)
    assert state, entity_id
    return state.state


@pytest.mark.freeze_time(NOW)
async def test_sensors(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """All sensors report the expected values."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    await _setup(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED

    assert float(_state(hass, "current_price")) == pytest.approx(0.10)
    assert float(_state(hass, "next_price")) == pytest.approx(0.2025)
    assert float(_state(hass, "today_min_price")) == pytest.approx(0.10)
    assert float(_state(hass, "today_max_price")) == pytest.approx(0.35)
    assert float(_state(hass, "tomorrow_min_price")) == pytest.approx(0.20)
    assert float(_state(hass, "tomorrow_max_price")) == pytest.approx(0.45)

    expected_avg = sum(s.price_ct for s in build_prices().today.slots) / 96 / 100
    assert float(_state(hass, "today_average_price")) == pytest.approx(expected_avg)

    assert _state(hass, "today_min_price_time") == "2026-11-23T12:00:00+00:00"
    assert _state(hass, "today_max_price_time") == "2026-11-23T17:00:00+00:00"
    assert _state(hass, "price_slots") == "192"

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, "4711_price_slots")
    attributes = hass.states.get(entity_id).attributes
    assert len(attributes["prices_today"]) == 96
    assert attributes["prices_today"][52] == {
        "start": "2026-11-23T12:00:00+00:00",
        "end": "2026-11-23T12:15:00+00:00",
        "price": 0.1,
    }

    # Disabled by default.
    window = registry.async_get_entity_id(
        "sensor", DOMAIN, "4711_today_cheapest_window_start"
    )
    assert (
        registry.async_get(window).disabled_by is er.RegistryEntryDisabler.INTEGRATION
    )


@pytest.mark.freeze_time(NOW)
async def test_slot_change_updates_state_without_polling(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """At every quarter hour the current price moves on without an API call."""
    await _setup(hass, mock_config_entry)
    calls = mock_client.async_get_prices.call_count

    freezer.move_to(datetime(2026, 11, 23, 13, 15, tzinfo=BERLIN))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert float(_state(hass, "current_price")) == pytest.approx(0.2025)
    assert mock_client.async_get_prices.call_count == calls


@pytest.mark.freeze_time(NOW)
async def test_tomorrow_unavailable(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Tomorrow sensors are unavailable until prices are published."""
    mock_client.async_get_prices.return_value = build_prices(with_tomorrow=False)
    await _setup(hass, mock_config_entry)

    assert _state(hass, "tomorrow_average_price") == STATE_UNAVAILABLE
    assert float(_state(hass, "current_price")) == pytest.approx(0.10)
    # Prices are expected after 13:00, so polling is faster.
    assert mock_config_entry.runtime_data.update_interval.total_seconds() == 900


async def test_setup_retry_on_connection_error(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Connection problems lead to a setup retry."""
    mock_client.async_get_prices.side_effect = LichtblickConnectionError
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_auth_error_starts_reauth(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Rejected credentials start a reauth flow."""
    mock_client.async_get_prices.side_effect = LichtblickAuthError
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == SOURCE_REAUTH


@pytest.mark.freeze_time(NOW)
async def test_get_prices_action(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """The get_prices action returns the price list."""
    await _setup(hass, mock_config_entry)

    response = await hass.services.async_call(
        DOMAIN,
        "get_prices",
        {"config_entry": mock_config_entry.entry_id, "day": "tomorrow"},
        blocking=True,
        return_response=True,
    )
    assert len(response["prices"]) == 96
    assert response["prices"][0]["start"] == "2026-11-23T23:00:00+00:00"

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "get_prices",
            {"config_entry": "does-not-exist"},
            blocking=True,
            return_response=True,
        )


@pytest.mark.freeze_time(NOW)
async def test_unload_and_diagnostics(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Diagnostics are redacted and the entry unloads cleanly."""
    await _setup(hass, mock_config_entry)

    diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    assert diagnostics["entry"]["data"]["password"] == "**REDACTED**"
    assert diagnostics["entry"]["data"]["email"] == "**REDACTED**"
    assert len(diagnostics["today"]["slots"]) == 96

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED
