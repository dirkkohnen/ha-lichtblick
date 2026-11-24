"""Diagnostics support for the LichtBlick integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .const import CONF_CONTRACT_ID
from .coordinator import LichtblickConfigEntry

TO_REDACT = {CONF_EMAIL, CONF_PASSWORD, CONF_CONTRACT_ID}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: LichtblickConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data
    return {
        "entry": {
            "title": "**REDACTED**",
            "data": async_redact_data(dict(entry.data), TO_REDACT),
        },
        "last_update_success": coordinator.last_update_success,
        "update_interval": str(coordinator.update_interval),
        "today": asdict(data.today) if data else None,
        "tomorrow": asdict(data.tomorrow) if data else None,
    }
