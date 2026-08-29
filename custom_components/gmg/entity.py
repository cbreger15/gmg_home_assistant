"""Base entity for the Green Mountain Grill integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GmgDataUpdateCoordinator


class GmgEntity(CoordinatorEntity[GmgDataUpdateCoordinator]):
    """Common device grouping for every entity belonging to one grill."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: GmgDataUpdateCoordinator) -> None:
        super().__init__(coordinator)

        serial = coordinator.grill.serial_number
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            name=f"Green Mountain Grill {serial}",
            manufacturer="Green Mountain Grill",
            configuration_url=None,
        )
