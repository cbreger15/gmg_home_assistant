"""The Green Mountain Grill integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_IP, CONF_SERIAL_NUMBER, DOMAIN
from .coordinator import GmgDataUpdateCoordinator
from .gmg import Grill

PLATFORMS = ["climate", "sensor", "binary_sensor", "number"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Green Mountain Grill from a config entry."""
    grill = Grill(entry.data[CONF_IP], entry.data[CONF_SERIAL_NUMBER])

    coordinator = GmgDataUpdateCoordinator(hass, grill)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded
