"""Constants for the LichtBlick integration."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Final

DOMAIN: Final = "lichtblick"
LOGGER = logging.getLogger(__package__)

CONF_CONTRACT_ID: Final = "contract_id"

# Prices for the next day are usually published in the early afternoon.
TOMORROW_PRICES_EXPECTED_HOUR: Final = 13

# Normal polling interval, and the faster one used while tomorrow's prices
# are expected but not yet available.
UPDATE_INTERVAL: Final = timedelta(hours=1)
UPDATE_INTERVAL_WAITING: Final = timedelta(minutes=15)

ATTRIBUTION: Final = "Data provided by LichtBlick SE"
MANUFACTURER: Final = "LichtBlick SE"

SERVICE_GET_PRICES: Final = "get_prices"
ATTR_CONFIG_ENTRY: Final = "config_entry"
ATTR_DAY: Final = "day"
