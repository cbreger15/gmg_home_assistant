"""Base entity for the Green Mountain Grill integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTR_CONFIG, CONFIG_WRITE_API_VERSIONS, DOMAIN
from .coordinator import GmgDataUpdateCoordinator
from .gmg import GrillConfig


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
            sw_version=coordinator.firmware_version,
        )


class GmgConfigEntity(GmgEntity):
    """An entity for the GMG app's Grill Config screen (status bytes 8-15).

    Unavailable until the grill has sent one whole status packet -- the only
    kind the block is read from (see const.STATUS_PACKET_BYTES). A control
    also needs a grill on a config API version whose layout is confirmed:
    elsewhere it would show a guess and every change would be refused.
    """

    # False for entities that show the raw block rather than decode it.
    _decodes_the_block = True

    def __init__(self, coordinator: GmgDataUpdateCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.grill.serial_number}_{key}"

    @property
    def config(self) -> GrillConfig | None:
        return self.coordinator.data.get(ATTR_CONFIG)

    @property
    def available(self) -> bool:
        config = self.config
        if not super().available or config is None:
            return False
        return not self._decodes_the_block or config.api_version in CONFIG_WRITE_API_VERSIONS
