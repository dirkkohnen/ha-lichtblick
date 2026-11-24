"""Config flow for the LichtBlick integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .api import (
    Contract,
    LichtblickAuthError,
    LichtblickClient,
    LichtblickConnectionError,
    LichtblickRateLimitError,
)
from .const import CONF_CONTRACT_ID, DOMAIN, LOGGER

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.PASSWORD, autocomplete="current-password"
            )
        ),
    }
)

STEP_REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.PASSWORD, autocomplete="current-password"
            )
        ),
    }
)


class LichtblickConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for LichtBlick."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._credentials: dict[str, str] = {}
        self._contracts: dict[int, Contract] = {}

    async def _async_validate(
        self, email: str, password: str
    ) -> tuple[list[Contract], dict[str, str]]:
        """Log in and fetch the contracts; return them and any form errors."""
        client = LichtblickClient(async_get_clientsession(self.hass), email, password)
        try:
            await client.async_login()
            contracts = await client.async_get_contracts()
        except LichtblickAuthError:
            return [], {"base": "invalid_auth"}
        except LichtblickRateLimitError:
            return [], {"base": "rate_limited"}
        except LichtblickConnectionError:
            return [], {"base": "cannot_connect"}
        except Exception:  # noqa: BLE001 - unexpected errors must not crash the flow
            LOGGER.exception("Unexpected error during LichtBlick login")
            return [], {"base": "unknown"}
        if not contracts:
            return [], {"base": "no_contracts"}
        return contracts, {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the LichtBlick app credentials."""
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            contracts, errors = await self._async_validate(
                email, user_input[CONF_PASSWORD]
            )
            if not errors:
                self._credentials = {
                    CONF_EMAIL: email,
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                }
                self._contracts = {c.contract_id: c for c in contracts}
                if len(contracts) == 1:
                    return await self._async_create(contracts[0])
                return await self.async_step_contract()

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, user_input
            ),
            errors=errors,
        )

    async def async_step_contract(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick one of several power contracts."""
        if user_input is not None:
            return await self._async_create(
                self._contracts[int(user_input[CONF_CONTRACT_ID])]
            )

        options = [
            SelectOptionDict(value=str(contract.contract_id), label=contract.title)
            for contract in self._contracts.values()
        ]
        return self.async_show_form(
            step_id="contract",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CONTRACT_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.LIST
                        )
                    )
                }
            ),
        )

    async def _async_create(self, contract: Contract) -> ConfigFlowResult:
        await self.async_set_unique_id(str(contract.contract_id))
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=contract.title,
            data={**self._credentials, CONF_CONTRACT_ID: contract.contract_id},
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication after the credentials were rejected."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the new password."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            contracts, errors = await self._async_validate(
                entry.data[CONF_EMAIL], user_input[CONF_PASSWORD]
            )
            if not errors:
                if entry.data[CONF_CONTRACT_ID] not in {
                    c.contract_id for c in contracts
                }:
                    return self.async_abort(reason="contract_not_found")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            description_placeholders={"email": entry.data[CONF_EMAIL]},
            errors=errors,
        )
