"""Data update coordinator for the LichtBlick integration."""

from __future__ import annotations

from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    TARIFF_TZ,
    LichtblickAuthError,
    LichtblickClient,
    LichtblickError,
    PriceData,
)
from .const import (
    CONF_CONTRACT_ID,
    DOMAIN,
    LOGGER,
    TOMORROW_PRICES_EXPECTED_HOUR,
    UPDATE_INTERVAL,
    UPDATE_INTERVAL_WAITING,
)

type LichtblickConfigEntry = ConfigEntry[LichtblickCoordinator]


class LichtblickCoordinator(DataUpdateCoordinator[PriceData]):
    """Fetch LichtBlick prices and update entities on every 15-minute slot."""

    config_entry: LichtblickConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: LichtblickConfigEntry,
        client: LichtblickClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client
        self.contract_id: int = config_entry.data[CONF_CONTRACT_ID]

    async def _async_setup(self) -> None:
        """Re-evaluate entity states at every quarter hour without polling."""
        self.config_entry.async_on_unload(
            async_track_time_change(
                self.hass, self._async_slot_changed, minute=(0, 15, 30, 45), second=0
            )
        )

    @callback
    def _async_slot_changed(self, now: datetime) -> None:
        """Handle the start of a new price slot."""
        if self.data is None:
            return
        if self.data.today.day != now.astimezone(TARIFF_TZ).date():
            # A new day has started: roll the data over right away.
            self.hass.async_create_task(self.async_request_refresh())
        self.async_update_listeners()

    async def _async_update_data(self) -> PriceData:
        """Fetch the latest prices."""
        try:
            data = await self.client.async_get_prices(self.contract_id)
        except LichtblickAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except LichtblickError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        now = dt_util.now(TARIFF_TZ)
        if not data.tomorrow.available and now.hour >= TOMORROW_PRICES_EXPECTED_HOUR:
            self.update_interval = UPDATE_INTERVAL_WAITING
        else:
            self.update_interval = UPDATE_INTERVAL
        return data
