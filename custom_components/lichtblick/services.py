"""Actions for the LichtBlick integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .const import ATTR_CONFIG_ENTRY, ATTR_DAY, DOMAIN, SERVICE_GET_PRICES
from .coordinator import LichtblickConfigEntry

DAY_OPTIONS = ("today", "tomorrow", "all")

GET_PRICES_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY): cv.string,
        vol.Optional(ATTR_DAY, default="all"): vol.In(DAY_OPTIONS),
    }
)


def _get_entry(hass: HomeAssistant, entry_id: str) -> LichtblickConfigEntry:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_config_entry",
            translation_placeholders={"config_entry": entry_id},
        )
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="config_entry_not_loaded",
            translation_placeholders={"config_entry": entry.title},
        )
    return entry


async def _async_get_prices(call: ServiceCall) -> ServiceResponse:
    entry = _get_entry(call.hass, call.data[ATTR_CONFIG_ENTRY])
    data = entry.runtime_data.data
    day = call.data[ATTR_DAY]
    if day == "today":
        slots = data.today.slots
    elif day == "tomorrow":
        slots = data.tomorrow.slots
    else:
        slots = data.slots
    return {
        "prices": [
            {
                "start": slot.start.isoformat(),
                "end": slot.end.isoformat(),
                "price": slot.price_eur,
            }
            for slot in slots
        ]
    }


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the LichtBlick actions."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_PRICES,
        _async_get_prices,
        schema=GET_PRICES_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
