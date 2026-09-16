"""Home Assistant test helpers: the integration set up against a WireGrill.

Only the Home Assistant test modules import this (it needs
pytest-homeassistant-custom-component); the protocol tests don't.
"""

from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from custom_components.gmg.const import CONF_IP, CONF_SERIAL_NUMBER, DOMAIN
from custom_components.gmg.gmg import Grill
from tests.grill_wire import TEST_IP, WireGrill

SERIAL = "GMG12272191"
PREFIX = "green_mountain_grill_gmg12272191"  # entity ids follow the device name


async def set_up(hass: HomeAssistant, wire: WireGrill) -> MockConfigEntry:
    """Set the integration up with every Grill talking to `wire`."""
    hass.config.units = US_CUSTOMARY_SYSTEM  # temperatures read back in F, as sent
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SERIAL,
        data={CONF_IP: TEST_IP, CONF_SERIAL_NUMBER: SERIAL},
    )
    entry.add_to_hass(hass)

    def grill_on_the_wire(ip: str, serial: str) -> Grill:
        grill = Grill(ip, serial)
        grill.send = wire.send
        grill._sleep = wire.sleep
        return grill

    with patch("custom_components.gmg.Grill", side_effect=grill_on_the_wire):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def poll(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """One regular status poll, as the coordinator's timer would run it."""
    await hass.data[DOMAIN][entry.entry_id].async_refresh()
    await hass.async_block_till_done()


async def call(hass: HomeAssistant, domain: str, service: str, entity_id: str, **data) -> None:
    await hass.services.async_call(
        domain, service, {ATTR_ENTITY_ID: entity_id, **data}, blocking=True
    )
