"""Sensor entities for the Green Mountain Grill integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ATTR_FIRE_STATE,
    ATTR_FIRE_STATE_PCT,
    ATTR_PROBE1_TEMP,
    ATTR_PROBE2_TEMP,
    DOMAIN,
    FIRE_STATE_NAMES,
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

    Only "off" (1) and "cold_smoke" (198) are independently confirmed
    against real captured payloads (see const.py). The others are carried
    over from an independent reverse-engineering project's own enum but
    unconfirmed here -- if one of those shows up, it's worth treating as
    "probably right, worth double-checking" rather than certain. The raw
    numeric code is always available as an attribute regardless.
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


class GmgConfigBlockSensor(GmgConfigEntity, SensorEntity):
    """The Grill Config block (status bytes 8-15) as it last arrived whole.

    The instrument for decoding the rest of it: change one setting in the GMG
    app, and whichever byte moves is that setting. The six calibration bytes
    are attributes, raw -- which byte is which box, and where each box's zero
    sits, is inferred rather than confirmed (see gmg.GrillConfig), and a
    guessed zero point would turn a readout into a wrong one.
    """

    _attr_name = "Config Block"
    _attr_icon = "mdi:barcode"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

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
        grill_150f, grill_500f = config.grill_adjustment_raw
        probe1_32f, probe1_212f = config.probe1_adjustment_raw
        probe2_32f, probe2_212f = config.probe2_adjustment_raw
        return {
            "api_version": config.api_version,
            "grill_adjustment_150f_raw": grill_150f,
            "grill_adjustment_500f_raw": grill_500f,
            "probe_1_adjustment_32f_raw": probe1_32f,
            "probe_1_adjustment_212f_raw": probe1_212f,
            "probe_2_adjustment_32f_raw": probe2_32f,
            "probe_2_adjustment_212f_raw": probe2_212f,
        }
