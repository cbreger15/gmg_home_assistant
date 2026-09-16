"""Select entities for the Green Mountain Grill integration.

The Climate Setting slider on the GMG app's Grill Config screen. Named
"Climate Setting" rather than "Climate" so it is not mistaken for the grill's
own climate entity.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CLIMATE_SETTINGS, DOMAIN
from .coordinator import GmgDataUpdateCoordinator
from .entity import GmgConfigEntity
from .gmg import CLIMATE


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: GmgDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([GmgClimateSettingSelect(coordinator)])


class GmgClimateSettingSelect(GmgConfigEntity, SelectEntity):
    """Icy, Cold, Average, Warm or Hot -- the ambient conditions the grill tunes for."""

    _attr_name = "Climate Setting"
    _attr_icon = "mdi:thermometer-lines"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(CLIMATE_SETTINGS)

    def __init__(self, coordinator: GmgDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "climate_setting")

    @property
    def current_option(self) -> str | None:
        config = self.config
        # 5-7 fit the field but have never been seen; show them as unknown.
        if config is None or config.climate >= len(CLIMATE_SETTINGS):
            return None
        return CLIMATE_SETTINGS[config.climate]

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_write_config(CLIMATE, CLIMATE_SETTINGS.index(option))
