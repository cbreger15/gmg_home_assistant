"""Sensor entities for the Green Mountain Grill integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .analytics import ProbeTrend
from .const import (
    ATTR_FIRE_STATE,
    ATTR_FIRE_STATE_PCT,
    ATTR_ON,
    ATTR_PROBE1_SET_TEMP,
    ATTR_PROBE1_TEMP,
    ATTR_PROBE2_TEMP,
    DOMAIN,
    FIRE_STATE_NAMES,
    POWER_STATE_COLD_SMOKE,
    POWER_STATE_ON,
    is_probe_connected,
)
from .coordinator import GmgDataUpdateCoordinator
from .entity import GmgConfigEntity, GmgEntity


@dataclass(frozen=True, kw_only=True)
class GmgSensorDescription(SensorEntityDescription):
    value_key: str = ""


PROBE_SENSORS: tuple[GmgSensorDescription, ...] = (
    GmgSensorDescription(
        key="probe1_temperature",
        translation_key="probe_temperature",
        name="Probe 1 Temperature",
        value_key=ATTR_PROBE1_TEMP,
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    GmgSensorDescription(
        key="probe2_temperature",
        translation_key="probe_temperature",
        name="Probe 2 Temperature",
        value_key=ATTR_PROBE2_TEMP,
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
)

DIAGNOSTIC_SENSORS: tuple[GmgSensorDescription, ...] = (
    GmgSensorDescription(
        key="fire_state",
        name="Fire State",
        value_key=ATTR_FIRE_STATE,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:fire",
    ),
    GmgSensorDescription(
        key="fire_state_percentage",
        name="Fire State Percentage",
        value_key=ATTR_FIRE_STATE_PCT,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:fire",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: GmgDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = [
        GmgProbeTemperatureSensor(coordinator, description) for description in PROBE_SENSORS
    ]
    for description in DIAGNOSTIC_SENSORS:
        if description.key == "fire_state":
            entities.append(GmgFireStateSensor(coordinator, description))
        else:
            entities.append(GmgDiagnosticSensor(coordinator, description))
    entities.append(GmgRawStatusSensor(coordinator))
    entities.append(GmgConfigBlockSensor(coordinator))
    entities.append(GmgProbeFinishTimeSensor(coordinator))

    async_add_entities(entities)


class GmgDiagnosticSensor(GmgEntity, SensorEntity):
    """A raw grill health value (fire state, fire percentage)."""

    entity_description: GmgSensorDescription

    def __init__(
        self, coordinator: GmgDataUpdateCoordinator, description: GmgSensorDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.grill.serial_number}_{description.key}"

    @property
    def native_value(self):
        return self.coordinator.data.get(self.entity_description.value_key)


class GmgFireStateSensor(GmgDiagnosticSensor):
    """Fire state, as a friendly name where one is known.

    "off", "startup", "running", "cooldown" and "cold_smoke" are confirmed
    on real grills (see const.py). "default" (0) and "fail" (5) are carried
    over from an independent reverse-engineering project's own enum and have
    never been seen -- if one shows up, treat it as "probably right, worth
    double-checking" rather than certain. The raw numeric code is always
    available as an attribute regardless.
    """

    @property
    def native_value(self):
        value = self.coordinator.data.get(self.entity_description.value_key)
        if value is None:
            return None
        return FIRE_STATE_NAMES.get(value, f"unknown_{value}")

    @property
    def extra_state_attributes(self) -> dict:
        return {"raw_code": self.coordinator.data.get(self.entity_description.value_key)}


class GmgProbeTemperatureSensor(GmgDiagnosticSensor):
    """A food probe's current temperature.

    Reads as unavailable rather than a plausible-looking number when the
    probe isn't actually plugged in -- see const.py's is_probe_connected
    for the range-check heuristic, kept in one place instead of scattered
    across entities as it was in the original climate-entity version.
    """

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        value = self.coordinator.data.get(self.entity_description.value_key)
        return bool(is_probe_connected(value))


class GmgRawStatusSensor(GmgEntity, SensorEntity):
    """Every byte of the last status response, indexed by position.

    Only a subset of this payload is decoded anywhere in this project's
    history (see gmg.py). This entity exists to make finding more of it
    safe: watch which index changes when you do something specific to the
    grill (open the lid, run low on pellets, hit an error), then promote
    that index to a named field in const.py once you've confirmed it
    across more than one observation. Disabled by default -- it's a
    reverse-engineering tool, not something to leave polling and logging
    state changes on every poll cycle for day-to-day use.
    """

    _attr_name = "Raw Status"
    _attr_icon = "mdi:code-braces"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: GmgDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.grill.serial_number}_raw_status"

    @property
    def native_value(self) -> int:
        """Byte count of the last response -- itself a useful signal if it ever changes."""
        return len(self.coordinator.data.get("_raw_bytes", []))

    @property
    def extra_state_attributes(self) -> dict:
        raw: list[int] = self.coordinator.data.get("_raw_bytes", [])
        return {
            "raw_bytes": raw,
            "raw_hex": bytes(raw).hex() if raw else None,
            **{f"byte_{i}": value for i, value in enumerate(raw)},
        }


# Config Block attributes for status bytes 10-15, in order: each calibration
# box as the app shows it, and (with "_raw") as the grill stores it.
CALIBRATION_ATTRIBUTES = (
    "grill_adjustment_150f",
    "grill_adjustment_500f",
    "probe_1_adjustment_32f",
    "probe_1_adjustment_212f",
    "probe_2_adjustment_32f",
    "probe_2_adjustment_212f",
)


class GmgConfigBlockSensor(GmgConfigEntity, SensorEntity):
    """The Grill Config block (status bytes 8-15) as it last arrived whole.

    The instrument for decoding the rest of it: change one setting in the GMG
    app, and whichever byte moves is that setting. The six calibration boxes
    are attributes, as the app shows them and (with "_raw") as the grill
    stores them -- see gmg.GrillConfig.
    """

    _attr_name = "Config Block"
    _attr_icon = "mdi:barcode"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _decodes_the_block = False  # raw bytes are worth seeing on any API version

    def __init__(self, coordinator: GmgDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "config_block")

    @property
    def native_value(self) -> str | None:
        config = self.config
        return None if config is None else str(config)

    @property
    def extra_state_attributes(self) -> dict | None:
        config = self.config
        if config is None:
            return None
        shown = config.grill_adjustment + config.probe1_adjustment + config.probe2_adjustment
        raw = config.grill_adjustment_raw + config.probe1_adjustment_raw + config.probe2_adjustment_raw
        return {
            "api_version": config.api_version,
            **dict(zip(CALIBRATION_ATTRIBUTES, shown)),
            **{f"{name}_raw": value for name, value in zip(CALIBRATION_ATTRIBUTES, raw)},
        }


class GmgProbeFinishTimeSensor(GmgEntity, SensorEntity):
    """When probe 1 will reach its target at its current rate of rise.

    A straight line through the last 20 minutes of readings (see
    analytics.ProbeTrend). Unavailable when there is nothing to estimate --
    the grill is not on or in cold smoke, the probe is unplugged, or no
    target is set -- and unknown when there is no honest estimate: too few
    readings, a stall, a fall or a rise too slow to measure, the target
    already reached, or a finish more than a day away. Unplugging the probe,
    or the grill leaving on or cold smoke, starts the trend again.
    """

    _attr_name = "Probe 1 Estimated Finish Time"
    _attr_icon = "mdi:timer-sand"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: GmgDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.grill.serial_number}_probe_1_estimated_finish_time"
        self._trend = ProbeTrend()

    def _probe(self) -> int | None:
        """Probe 1's reading while the grill is cooking and the probe is in, else None."""
        data = self.coordinator.data
        if data.get(ATTR_ON) not in (POWER_STATE_ON, POWER_STATE_COLD_SMOKE):
            return None
        temperature = data.get(ATTR_PROBE1_TEMP)
        return temperature if is_probe_connected(temperature) else None

    def _record(self) -> None:
        if not self.coordinator.last_update_success:
            # A failed poll is announced too, with the last good data: a
            # reading from an earlier poll, not a new one.
            return
        temperature = self._probe()
        if temperature is None:
            self._trend.clear()
        else:
            self._trend.add(dt_util.utcnow(), temperature)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._record()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._record()
        super()._handle_coordinator_update()

    @property
    def available(self) -> bool:
        return (
            super().available
            and self._probe() is not None
            and bool(self.coordinator.data.get(ATTR_PROBE1_SET_TEMP))
        )

    @property
    def native_value(self) -> datetime | None:
        finish = self._trend.finish_time(self.coordinator.data[ATTR_PROBE1_SET_TEMP])
        if finish is None:
            return None
        # To the minute: a new second every poll is noise, not information.
        return (finish + timedelta(seconds=30)).replace(second=0, microsecond=0)

    @property
    def extra_state_attributes(self) -> dict:
        rate = self._trend.rate_per_hour()
        return {"rate_f_per_hour": None if rate is None else round(rate, 1)}
