"""DataUpdateCoordinator for the Green Mountain Grill integration.

Every entity for a given grill (the grill itself, both probes, fire/warn
state) shares one of these instead of independently polling the grill on
its own schedule. That fixes two problems at once: three redundant UDP
round-trips per poll cycle become one, and the blocking socket I/O in
gmg.Grill.status() runs in the executor instead of on the event loop.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import ATTR_CONFIG, DEFAULT_SCAN_INTERVAL, DOMAIN
from .gmg import ConfigField, Grill, GmgCommunicationError

_LOGGER = logging.getLogger(__name__)


class GmgDataUpdateCoordinator(DataUpdateCoordinator):
    """Fetches grill status once per interval and shares it across every entity."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, grill: Grill) -> None:
        self.grill = grill
        self.firmware_version: str | None = None
        # Polls and config writes take turns. A poll that read the grill
        # before a write and finished after it would put the old settings
        # back on screen.
        self._grill_turn = asyncio.Lock()
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{grill.serial_number}",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )

    async def _async_update_data(self) -> dict:
        async with self._grill_turn:
            try:
                data = await self.hass.async_add_executor_job(self.grill.status)
            except GmgCommunicationError as err:
                raise UpdateFailed(str(err)) from err

        # A cut or merged reply still has good status fields but no config
        # block (see const.STATUS_PACKET_BYTES). The settings did not change
        # because one reply was cut, so keep showing the last whole packet's
        # block rather than dropping the Grill Config entities to
        # unavailable. Display only: a write never uses this copy -- it reads
        # the grill afresh (gmg.Grill.write_config_field).
        if data[ATTR_CONFIG] is None and self.data is not None:
            data[ATTR_CONFIG] = self.data.get(ATTR_CONFIG)
        return data

    async def async_write_config(self, field: ConfigField, value: int) -> None:
        """Change one Grill Config setting, and show what the grill reports after.

        Raises HomeAssistantError -- a failed toggle or automation step --
        when the setting could not be written or did not take. The failure
        is logged as well: a change made from the dashboard otherwise leaves
        only a passing notification, and the error holds the block to
        restore from.
        """
        async with self._grill_turn:
            try:
                status = await self.hass.async_add_executor_job(
                    self.grill.write_config_field, field, value
                )
            except (GmgCommunicationError, ValueError) as err:
                failure = err
            else:
                self.async_set_updated_data(status)
                return

        _LOGGER.error("Could not change %s on the grill: %s", field.name, failure)
        # Outside the turn: the refresh takes it too.
        await self.async_request_refresh()
        raise HomeAssistantError(
            f"Could not change {field.name} on the grill: {failure}"
        ) from failure
