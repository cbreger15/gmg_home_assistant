"""Number entities for the Green Mountain Grill integration.

A food probe's target/alarm temperature is a settable numeric value, not a
climate setpoint -- this is what "probe alerts" automations (see the
Convergence build doc) trigger against directly, instead of a fake
thermostat's target_temperature.
"""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ATTR_ON,
    ATTR_PROBE1_SET_TEMP,
    ATTR_PROBE2_SET_TEMP,
    DOMAIN,
    MAX_TEMP_F_PROBE,
    MIN_TEMP_F_PROBE,
)
from .coordinator import GmgDataUpdateCoordinator
from .entity import GmgEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: GmgDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        [
            GmgProbeTargetTemperature(coordinator, probe_number=1, value_key=ATTR_PROBE1_SET_TEMP),
            GmgProbeTargetTemperature(coordinator, probe_number=2, value_key=ATTR_PROBE2_SET_TEMP),
        ]
    )


class GmgProbeTargetTemperature(GmgEntity, NumberEntity):
    """Target/alarm temperature for one food probe."""

    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_native_min_value = MIN_TEMP_F_PROBE
    _attr_native_max_value = MAX_TEMP_F_PROBE
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:thermometer-alert"

    def __init__(
        self, coordinator: GmgDataUpdateCoordinator, probe_number: int, value_key: str
    ) -> None:
        super().__init__(coordinator)
        self._probe_number = probe_number
        self._value_key = value_key
        self._attr_name = f"Probe {probe_number} Target Temperature"
        self._attr_unique_id = f"{coordinator.grill.serial_number}_probe_{probe_number}_target"

    @property
    def native_value(self) -> int | None:
        return self.coordinator.data.get(self._value_key)

    async def async_set_native_value(self, value: float) -> None:
        if self.coordinator.data.get(ATTR_ON) == 0:
            _LOGGER.warning("Grill is not on, cannot set probe target temperature")
            return

        await self.hass.async_add_executor_job(
            self.coordinator.grill.set_temp_probe, int(value), self._probe_number
        )
        await self.coordinator.async_request_refresh()
