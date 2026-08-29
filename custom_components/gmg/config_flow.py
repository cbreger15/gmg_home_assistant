"""Config flow for Green Mountain Grill.

The discovery logic already existed in gmg.py -- it just was not wired
into a config flow, so setup was YAML-only with a hardcoded IP. This
connects the two.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult

from .const import CONF_IP, CONF_SERIAL_NUMBER, DOMAIN
from .gmg import Grill, discover_grills

_LOGGER = logging.getLogger(__name__)

DISCOVERY_TIMEOUT = 3


class GmgConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Green Mountain Grill."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered: list[Grill] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            selected = next(
                (g for g in self._discovered if g.serial_number == user_input[CONF_SERIAL_NUMBER]),
                None,
            )
            if selected is None:
                errors["base"] = "not_found"
            else:
                await self.async_set_unique_id(selected.serial_number)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Green Mountain Grill ({selected.serial_number})",
                    data={CONF_IP: selected.ip, CONF_SERIAL_NUMBER: selected.serial_number},
                )

        self._discovered = await self.hass.async_add_executor_job(
            discover_grills, DISCOVERY_TIMEOUT
        )

        if not self._discovered:
            return self.async_abort(reason="no_grills_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SERIAL_NUMBER): vol.In(
                        {g.serial_number: f"{g.serial_number} ({g.ip})" for g in self._discovered}
                    )
                }
            ),
            errors=errors,
        )
