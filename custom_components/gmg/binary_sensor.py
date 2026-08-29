"""Binary sensor entities for the Green Mountain Grill integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ATTR_PROBE1_TEMP, ATTR_PROBE2_TEMP, ATTR_WARN_STATE, DOMAIN, is_probe_connected
from .coordinator import GmgDataUpdateCoordinator
from .entity import GmgEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: GmgDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        [
            GmgProbeConnectedSensor(coordinator, probe_number=1, value_key=ATTR_PROBE1_TEMP),
            GmgProbeConnectedSensor(coordinator, probe_number=2, value_key=ATTR_PROBE2_TEMP),
            GmgWarningSensor(coordinator),
        ]
    )


class GmgProbeConnectedSensor(GmgEntity, BinarySensorEntity):
    """Whether a food probe is actually plugged in.

    The grill's status payload has no dedicated "connected" flag. A probe
    with nothing plugged in reports a temperature reading outside its own
    physical range (confirmed against two independent real captured
    payloads -- see const.py's is_probe_connected) -- that range check is
    the actual signal, not a specific magic value. This heuristic used to
    be buried inside a fake climate entity's hvac_mode property; it lives
    in exactly one place now.
    """

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(
        self, coordinator: GmgDataUpdateCoordinator, probe_number: int, value_key: str
    ) -> None:
        super().__init__(coordinator)
        self._value_key = value_key
        self._attr_name = f"Probe {probe_number} Connected"
        self._attr_unique_id = f"{coordinator.grill.serial_number}_probe_{probe_number}_connected"

    @property
    def is_on(self) -> bool | None:
        return is_probe_connected(self.coordinator.data.get(self._value_key))


class GmgWarningSensor(GmgEntity, BinarySensorEntity):
    """Whether the grill is reporting a warning state.

    warnState is a 4-byte combined value (see gmg.py) -- reading it as a
    single byte, as the original implementation did, could silently miss
    a real warning encoded in any of the other 3 bytes.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_name = "Warning"

    def __init__(self, coordinator: GmgDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.grill.serial_number}_warning"

    @property
    def is_on(self) -> bool | None:
        value = self.coordinator.data.get(ATTR_WARN_STATE)
        if value is None:
            return None
        return value != 0
