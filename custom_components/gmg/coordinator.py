"""DataUpdateCoordinator for the Green Mountain Grill integration.

Every entity for a given grill (the grill itself, both probes, fire/warn
state) shares one of these instead of independently polling the grill on
its own schedule. That fixes two problems at once: three redundant UDP
round-trips per poll cycle become one, and the blocking socket I/O in
gmg.Grill.status() runs in the executor instead of on the event loop.
"""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .gmg import Grill, GmgCommunicationError

_LOGGER = logging.getLogger(__name__)


class GmgDataUpdateCoordinator(DataUpdateCoordinator):
    """Fetches grill status once per interval and shares it across every entity."""

    def __init__(self, hass: HomeAssistant, grill: Grill) -> None:
        self.grill = grill
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{grill.serial_number}",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )

    async def _async_update_data(self) -> dict:
        try:
            return await self.hass.async_add_executor_job(self.grill.status)
        except GmgCommunicationError as err:
            raise UpdateFailed(str(err)) from err
