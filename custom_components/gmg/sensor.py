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
    PROBE_DISCONNECTED_TEMP_F,
)
from .coordinator import GmgDataUpdateCoordinator
from .entity import GmgEntity


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
    entities += [
        GmgDiagnosticSensor(coordinator, description) for description in DIAGNOSTIC_SENSORS
    ]

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


class GmgProbeTemperatureSensor(GmgDiagnosticSensor):
    """A food probe's current temperature.

    Reads as unavailable rather than a plausible-looking number when the
    probe isn't actually plugged in -- see binary_sensor.py for the same
    "disconnected" heuristic, kept in one place instead of scattered
    across entities as it was in the original climate-entity version.
    """

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        value = self.coordinator.data.get(self.entity_description.value_key)
        return value is not None and value != PROBE_DISCONNECTED_TEMP_F
