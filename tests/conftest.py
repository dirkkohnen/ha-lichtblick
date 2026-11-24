"""Fixtures for the LichtBlick tests."""

from __future__ import annotations

from collections.abc import Generator
from datetime import date, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.lichtblick.api import Contract, PriceData, parse_day
from custom_components.lichtblick.const import CONF_CONTRACT_ID, DOMAIN

BERLIN = ZoneInfo("Europe/Berlin")
TODAY = date(2026, 11, 23)
CONTRACT = Contract(
    contract_id=4711,
    product_name="ÖkoStrom Dynamic",
    city="Hamburg",
    post_code="20095",
    street="Musterweg",
    house_number="1",
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable custom integrations in all tests."""


def build_curve(day: date, base: float = 20.0) -> dict[str, Any]:
    """Build a raw API curve with 96 quarter-hour points (UTC, like the API)."""
    start = datetime(day.year, day.month, day.day, tzinfo=BERLIN)
    points = []
    for i in range(96):
        moment = (start + timedelta(minutes=15 * i)).astimezone(ZoneInfo("UTC"))
        # Cheapest at 13:00 local, most expensive at 18:00 local.
        value = base + (i % 4) * 0.25
        if i == 52:
            value = base - 10
        if i == 72:
            value = base + 15
        points.append(
            {"time": moment.strftime("%Y-%m-%dT%H:%M:%SZ"), "valueInCents": value}
        )
    return {
        "energyPriceCurvesResponse": {
            "energyPricesInCents": points,
            "averagePriceInCents": None,
            "minAvgPriceRange": {
                "startTime": (start + timedelta(hours=12)).isoformat(),
                "endTime": (start + timedelta(hours=15)).isoformat(),
                "averagePrice": base - 2,
            },
            "maxAvgPriceRange": {
                "startTime": "0001-01-01T00:00:00Z",
                "endTime": "0001-01-01T00:00:00Z",
                "averagePrice": 0,
            },
        }
    }


def build_prices(*, with_tomorrow: bool = True) -> PriceData:
    """Return parsed price data for TODAY (and optionally tomorrow)."""
    tomorrow = TODAY + timedelta(days=1)
    return PriceData(
        today=parse_day(TODAY, build_curve(TODAY)),
        tomorrow=parse_day(
            tomorrow, build_curve(tomorrow, 30.0) if with_tomorrow else None
        ),
    )


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a LichtBlick config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=CONTRACT.title,
        unique_id=str(CONTRACT.contract_id),
        data={
            CONF_EMAIL: "user@example.com",
            CONF_PASSWORD: "secret",
            CONF_CONTRACT_ID: CONTRACT.contract_id,
        },
    )


@pytest.fixture
def mock_client() -> Generator[AsyncMock]:
    """Mock the LichtBlick client in the integration and the config flow."""
    with (
        patch(
            "custom_components.lichtblick.LichtblickClient", autospec=True
        ) as client_cls,
        patch(
            "custom_components.lichtblick.config_flow.LichtblickClient",
            new=client_cls,
        ),
    ):
        client = client_cls.return_value
        client.async_login.return_value = None
        client.async_get_contracts.return_value = [CONTRACT]
        client.async_get_prices.return_value = build_prices()
        yield client


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Prevent the integration from being set up during config flow tests."""
    with patch(
        "custom_components.lichtblick.async_setup_entry", return_value=True
    ) as mock:
        yield mock
