"""Tests for the LichtBlick API client (HTTP mocked)."""

from __future__ import annotations

from datetime import date

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.lichtblick.api import (
    B2C_TOKEN_URL,
    GRAPH_URL,
    LichtblickAuthError,
    LichtblickClient,
    LichtblickConnectionError,
    LichtblickRateLimitError,
)

from .conftest import TODAY, build_curve

TOKEN = {"access_token": "abc", "refresh_token": "r1", "expires_in": 3600}


def _client(hass: HomeAssistant) -> LichtblickClient:
    return LichtblickClient(async_get_clientsession(hass), "u@example.com", "pw")


async def test_contracts_and_prices(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Login, contract discovery and price parsing work end to end."""
    aioclient_mock.post(B2C_TOKEN_URL, json=TOKEN)
    aioclient_mock.post(
        GRAPH_URL,
        json={
            "data": {
                "b2cCustomer": {
                    "appSelectableContracts": {
                        "contracts": [
                            {"b2cContractId": 1, "category": "GAS"},
                            {
                                "b2cContractId": 2,
                                "category": "POWER",
                                "productName": "ÖkoStrom Dynamic",
                                "address": {"city": "Hamburg"},
                            },
                        ]
                    }
                }
            }
        },
    )
    client = _client(hass)
    contracts = await client.async_get_contracts()
    assert [c.contract_id for c in contracts] == [2]
    assert contracts[0].title == "ÖkoStrom Dynamic (Hamburg)"

    aioclient_mock.clear_requests()
    aioclient_mock.post(
        GRAPH_URL,
        json={"data": {"today": build_curve(TODAY), "tomorrow": None}},
    )
    prices = await client.async_get_prices(2, today=TODAY)
    assert len(prices.today.slots) == 96
    assert prices.today.slots[0].start.isoformat() == "2026-11-22T23:00:00+00:00"
    assert prices.today.slots[-1].end.isoformat() == "2026-11-23T23:00:00+00:00"
    assert prices.today.cheapest_window is not None
    # Placeholder range (year 0001) is dropped.
    assert prices.today.most_expensive_window is None
    assert not prices.tomorrow.available
    assert prices.tomorrow.day == date(2026, 11, 24)

    # Token is cached: no second login.
    assert not any(
        str(call[1]).startswith(B2C_TOKEN_URL[:30])
        for call in aioclient_mock.mock_calls
    )
    variables = aioclient_mock.mock_calls[0][2]["variables"]
    assert variables["startToday"] == "2026-11-23T00:00:00+01:00"
    assert variables["endTomorrow"] == "2026-11-25T00:00:00+01:00"


async def test_invalid_credentials(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """B2C errors map to an auth error."""
    aioclient_mock.post(B2C_TOKEN_URL, status=400, json={"error": "invalid_grant"})
    with pytest.raises(LichtblickAuthError):
        await _client(hass).async_login()


async def test_rate_limited(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """HTTP 429 maps to a rate limit error."""
    aioclient_mock.post(B2C_TOKEN_URL, status=429, text="slow down")
    with pytest.raises(LichtblickRateLimitError):
        await _client(hass).async_login()


async def test_graphql_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """GraphQL errors without data raise a connection error."""
    aioclient_mock.post(B2C_TOKEN_URL, json=TOKEN)
    aioclient_mock.post(GRAPH_URL, json={"errors": [{"message": "boom"}]})
    with pytest.raises(LichtblickConnectionError):
        await _client(hass).async_get_prices(2, today=TODAY)


async def test_unauthenticated_token_is_renewed_once(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A rejected token is renewed once, then an auth error is raised."""
    aioclient_mock.post(B2C_TOKEN_URL, json=TOKEN)
    aioclient_mock.post(GRAPH_URL, status=401, json={"errors": []})
    with pytest.raises(LichtblickAuthError):
        await _client(hass).async_get_prices(2, today=TODAY)
    graph_calls = [c for c in aioclient_mock.mock_calls if str(c[1]) == GRAPH_URL]
    assert len(graph_calls) == 2
