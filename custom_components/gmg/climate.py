"""Climate entity for the Green Mountain Grill itself.

Only the grill is modeled as a climate entity now -- the two food probes
moved to sensor.py/number.py/binary_sensor.py, since a probe pretending to
be a thermostat with HVACMode.HEAT/OFF standing in for "connected" was
never a good fit for HomeKit or any other consumer expecting real climate
semantics.
"""

from __future__ import annotations

import logging

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ATTR_FIRE_STATE,
    ATTR_GRILL_SET_TEMP,
    ATTR_GRILL_TEMP,
    ATTR_ON,
    DOMAIN,
    FIRE_STATE_RUNNING,
    FIRE_STATE_STARTUP,
    POWER_STATE_COLD_SMOKE,
    POWER_STATE_OFF,
    POWER_STATE_ON,
    is_cooldown,
)
from .coordinator import GmgDataUpdateCoordinator
from .entity import GmgEntity

_LOGGER = logging.getLogger(__name__)

# The grill will not accept a new setpoint until it has actually reached
# this temperature -- straight from the GMG manual, not a guess.
MIN_TEMP_TO_ALLOW_SETPOINT_CHANGE = 150


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: GmgDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([GmgGrillClimate(coordinator)])


class GmgGrillClimate(GmgEntity, ClimateEntity):
    """Representation of a Green Mountain Grill smoker."""

    _attr_name = None  # device name is enough, per has_entity_name
    _attr_icon = "mdi:grill"
    _attr_temperature_unit = UnitOfTemperature.FAHRENHEIT
    _attr_target_temperature_step = 1
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.FAN_ONLY, HVACMode.OFF]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _enable_turn_on_off_backwards_compatibility = False

    def __init__(self, coordinator: GmgDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = coordinator.grill.serial_number
        self._attr_max_temp = coordinator.grill.MAX_TEMP_F
        self._attr_min_temp = coordinator.grill.MIN_TEMP_F

    @property
    def hvac_mode(self) -> HVACMode:
        # Corrected 2026-08-28: the original implementation mapped state==2
        # to FAN_ONLY/cold-smoke. Cross-checked against an independent
        # reverse-engineering of this same protocol (with real captured
        # payloads, not just a guess): cold smoke is confirmed as state==3.
        # State==2 is the fan cooldown after power-off (confirmed by five
        # real cooks): the grill is shutting down, so the mode is OFF and
        # hvac_action says FAN.
        state = self.coordinator.data[ATTR_ON]
        if state == POWER_STATE_ON:
            return HVACMode.HEAT
        if state == POWER_STATE_COLD_SMOKE:
            return HVACMode.FAN_ONLY
        return HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction | None:
        """What the grill is doing, from the power (byte 30) and fire (byte 32) states.

        Fire state STARTUP has also come back mid-cook after the grill dropped
        below temperature, so PREHEATING can reappear during a cook.
        """
        power = self.coordinator.data.get(ATTR_ON)
        fire = self.coordinator.data.get(ATTR_FIRE_STATE)
        if is_cooldown(power, fire) or power == POWER_STATE_COLD_SMOKE:
            return HVACAction.FAN
        if power == POWER_STATE_ON:
            if fire == FIRE_STATE_STARTUP:
                return HVACAction.PREHEATING
            if fire == FIRE_STATE_RUNNING:
                return HVACAction.HEATING
            return HVACAction.IDLE
        if power == POWER_STATE_OFF:
            return HVACAction.OFF
        return None

    @property
    def current_temperature(self) -> int | None:
        return self.coordinator.data.get(ATTR_GRILL_TEMP)

    @property
    def target_temperature(self) -> int | None:
        return self.coordinator.data.get(ATTR_GRILL_SET_TEMP)

    async def async_set_temperature(self, **kwargs) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return

        if temperature == self.coordinator.data.get(ATTR_GRILL_SET_TEMP):
            return

        if self.coordinator.data[ATTR_ON] == 0:
            _LOGGER.warning("Grill is not on, cannot set temperature")
            return

        current_temp = self.coordinator.data.get(ATTR_GRILL_TEMP, 0)
        if current_temp < MIN_TEMP_TO_ALLOW_SETPOINT_CHANGE:
            _LOGGER.warning(
                "Grill has not reached %s degrees F yet (currently %s), cannot change setpoint",
                MIN_TEMP_TO_ALLOW_SETPOINT_CHANGE,
                current_temp,
            )
            return

        await self.hass.async_add_executor_job(
            self.coordinator.grill.set_temp, int(temperature)
        )
        await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        grill = self.coordinator.grill

        if hvac_mode == HVACMode.HEAT:
            await self.hass.async_add_executor_job(grill.power_on)
        elif hvac_mode == HVACMode.OFF:
            await self.hass.async_add_executor_job(grill.power_off)
        elif hvac_mode == HVACMode.FAN_ONLY:
            await self.hass.async_add_executor_job(grill.power_on_cool)
        else:
            _LOGGER.error("Unsupported hvac mode: %s", hvac_mode)
            return

        await self.coordinator.async_request_refresh()

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)
