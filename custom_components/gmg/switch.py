"""Switch entities for the Green Mountain Grill integration.

The on/off settings on the GMG app's Grill Config screen. Each toggle reads
the grill's current config block, changes its own bit, writes the block back
and waits for the grill to report it -- see gmg.Grill.write_config_field.

Pizza Mode is a user-facing control (no entity_category), so Home
Assistant's HomeKit bridge can publish it; the two housekeeping toggles are
config entities, which the bridge skips unless one is included by entity ID.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import GmgDataUpdateCoordinator
from .entity import GmgConfigEntity
from .gmg import AUTO_REVERT_WIFI, LOCK_TEMP_DISPLAY, PIZZA_MODE, ConfigField


@dataclass(frozen=True, kw_only=True)
class GmgConfigSwitchDescription(SwitchEntityDescription):
    field: ConfigField


CONFIG_SWITCHES: tuple[GmgConfigSwitchDescription, ...] = (
    # GMG: use with the pizza attachment, and never above 350F with it fitted.
    GmgConfigSwitchDescription(
        key="pizza_mode",
        name="Pizza Mode",
        icon="mdi:pizza",
        field=PIZZA_MODE,
    ),
    # Off, per GMG: the grill keeps trying to re-establish its last network
    # configuration until it is reset.
    GmgConfigSwitchDescription(
        key="auto_revert_wifi",
        name="Auto-Revert WiFi",
        icon="mdi:wifi-refresh",
        entity_category=EntityCategory.CONFIG,
        field=AUTO_REVERT_WIFI,
    ),
    GmgConfigSwitchDescription(
        key="lock_temp_display",
        name="Lock Temp Display",
        icon="mdi:lock-outline",
        entity_category=EntityCategory.CONFIG,
        field=LOCK_TEMP_DISPLAY,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: GmgDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        GmgConfigSwitch(coordinator, description) for description in CONFIG_SWITCHES
    )


class GmgConfigSwitch(GmgConfigEntity, SwitchEntity):
    """One on/off Grill Config setting."""

    entity_description: GmgConfigSwitchDescription

    def __init__(
        self, coordinator: GmgDataUpdateCoordinator, description: GmgConfigSwitchDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        config = self.config
        if config is None:
            return None
        return bool(config.get(self.entity_description.field))

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_write_config(self.entity_description.field, 1)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_write_config(self.entity_description.field, 0)
