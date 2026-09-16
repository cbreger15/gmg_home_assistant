"""The Grill Config controls, through Home Assistant.

The grill here is a WireGrill (tests/grill_wire.py) behind the real
gmg.Grill, so a switch or select drives the real read-change-write-confirm
sequence. Needs pytest-homeassistant-custom-component (requirements.test.txt).
"""

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import (
    ATTR_ENTITY_ID,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    EntityCategory,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from custom_components.gmg.const import CONF_IP, CONF_SERIAL_NUMBER, DOMAIN
from custom_components.gmg.gmg import Grill
from tests.grill_wire import LIVE, MERGED_104, TAIL_CUT_51, WireGrill

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

SERIAL = "GMG12272191"
PIZZA = "switch.green_mountain_grill_gmg12272191_pizza_mode"
AUTO_REVERT = "switch.green_mountain_grill_gmg12272191_auto_revert_wifi"
LOCK = "switch.green_mountain_grill_gmg12272191_lock_temp_display"
CLIMATE_SETTING = "select.green_mountain_grill_gmg12272191_climate_setting"
BLOCK = "sensor.green_mountain_grill_gmg12272191_config_block"
WARNING = "binary_sensor.green_mountain_grill_gmg12272191_warning"
GRILL = "climate.green_mountain_grill_gmg12272191"


async def _set_up(hass: HomeAssistant, wire: WireGrill) -> MockConfigEntry:
    hass.config.units = US_CUSTOMARY_SYSTEM  # temperatures read back in F, as sent
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SERIAL,
        data={CONF_IP: "10.0.0.94", CONF_SERIAL_NUMBER: SERIAL},
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


async def _poll(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.data[DOMAIN][entry.entry_id].async_refresh()
    await hass.async_block_till_done()


async def _call(hass: HomeAssistant, domain: str, service: str, entity_id: str, **data) -> None:
    await hass.services.async_call(
        domain, service, {ATTR_ENTITY_ID: entity_id, **data}, blocking=True
    )


async def test_the_controls_show_the_grill_settings(hass: HomeAssistant) -> None:
    await _set_up(hass, WireGrill(LIVE["09"]))

    assert hass.states.get(PIZZA).state == STATE_OFF
    assert hass.states.get(AUTO_REVERT).state == STATE_OFF
    assert hass.states.get(LOCK).state == STATE_ON
    climate = hass.states.get(CLIMATE_SETTING)
    assert climate.state == "Average"
    assert climate.attributes["options"] == ["Icy", "Cold", "Average", "Warm", "Hot"]

    block = hass.states.get(BLOCK)
    assert block.state == "06 09 14 32 19 19 19 19"
    assert block.attributes["api_version"] == 6
    assert block.attributes["grill_adjustment_150f_raw"] == 20
    assert block.attributes["grill_adjustment_500f_raw"] == 50
    assert block.attributes["probe_1_adjustment_32f_raw"] == 25
    assert block.attributes["probe_1_adjustment_212f_raw"] == 25
    assert block.attributes["probe_2_adjustment_32f_raw"] == 25
    assert block.attributes["probe_2_adjustment_212f_raw"] == 25


async def test_only_pizza_mode_is_a_user_facing_control(hass: HomeAssistant) -> None:
    """The HomeKit bridge never publishes an entity with an entity_category, so
    this decides what reaches Apple Home: Pizza Mode, and nothing else."""
    await _set_up(hass, WireGrill(LIVE["09"]))
    registry = er.async_get(hass)

    assert registry.async_get(PIZZA).entity_category is None
    assert registry.async_get(AUTO_REVERT).entity_category == EntityCategory.CONFIG
    assert registry.async_get(LOCK).entity_category == EntityCategory.CONFIG
    assert registry.async_get(CLIMATE_SETTING).entity_category == EntityCategory.CONFIG
    assert registry.async_get(BLOCK).entity_category == EntityCategory.DIAGNOSTIC


async def test_pizza_mode_on_writes_the_live_block_and_shows_the_grill_result(
    hass: HomeAssistant,
) -> None:
    wire = WireGrill(LIVE["09"])
    await _set_up(hass, wire)

    await _call(hass, "switch", "turn_on", PIZZA)

    assert wire.writes() == [bytes.fromhex("5543062914321919191921")]
    assert hass.states.get(PIZZA).state == STATE_ON
    assert hass.states.get(BLOCK).state == "06 29 14 32 19 19 19 19"

    await _call(hass, "switch", "turn_off", PIZZA)

    assert wire.writes()[-1] == bytes.fromhex("5543060914321919191921")
    assert hass.states.get(PIZZA).state == STATE_OFF


async def test_choosing_a_climate_changes_only_the_climate_bits(hass: HomeAssistant) -> None:
    wire = WireGrill(LIVE["09"])
    await _set_up(hass, wire)

    await _call(hass, "select", "select_option", CLIMATE_SETTING, option="Hot")

    assert wire.writes() == [bytes.fromhex("5543061114321919191921")]
    assert hass.states.get(CLIMATE_SETTING).state == "Hot"
    assert hass.states.get(LOCK).state == STATE_ON
    assert hass.states.get(AUTO_REVERT).state == STATE_OFF


async def test_each_toggle_starts_from_the_block_the_last_one_left(hass: HomeAssistant) -> None:
    wire = WireGrill(LIVE["09"])
    await _set_up(hass, wire)

    await _call(hass, "switch", "turn_off", LOCK)
    await _call(hass, "switch", "turn_on", AUTO_REVERT)

    assert wire.writes() == [
        bytes.fromhex("5543060814321919191921"),  # 09 without Lock Temp Display
        bytes.fromhex("5543060a14321919191921"),  # 08 with Auto-Revert WiFi
    ]
    assert hass.states.get(LOCK).state == STATE_OFF
    assert hass.states.get(AUTO_REVERT).state == STATE_ON


async def test_a_write_that_does_not_take_is_reported(hass: HomeAssistant) -> None:
    wire = WireGrill(LIVE["09"], apply=lambda block: None)  # the grill ignores it
    await _set_up(hass, wire)

    with pytest.raises(HomeAssistantError, match="pizza_mode.*did not take"):
        await _call(hass, "switch", "turn_on", PIZZA)

    assert len(wire.writes()) == 1
    assert hass.states.get(PIZZA).state == STATE_OFF


async def test_nothing_is_written_when_the_grill_cannot_be_read(hass: HomeAssistant) -> None:
    wire = WireGrill(LIVE["09"])
    await _set_up(hass, wire)
    wire.script = [None, TAIL_CUT_51, None, MERGED_104, None]

    with pytest.raises(HomeAssistantError, match="pizza_mode.*no usable status"):
        await _call(hass, "switch", "turn_on", PIZZA)

    assert wire.writes() == []


async def test_settings_keep_their_last_value_across_cut_and_merged_replies(
    hass: HomeAssistant,
) -> None:
    # Both pieces carry an older block (0b, Auto-Revert WiFi on). Read as a
    # block, either would flip the switch.
    wire = WireGrill(LIVE["09"])
    entry = await _set_up(hass, wire)
    assert hass.states.get(GRILL).attributes["current_temperature"] == 75

    for piece in (TAIL_CUT_51, MERGED_104):
        wire.script = [piece]
        await _poll(hass, entry)

        assert hass.states.get(AUTO_REVERT).state == STATE_OFF
        assert hass.states.get(BLOCK).state == "06 09 14 32 19 19 19 19"
        # ...while the piece's status fields are still read: it says 62F.
        assert hass.states.get(GRILL).attributes["current_temperature"] == 62


async def test_settings_are_unavailable_until_a_whole_packet_arrives(hass: HomeAssistant) -> None:
    wire = WireGrill(LIVE["09"], script=[TAIL_CUT_51])
    entry = await _set_up(hass, wire)

    for entity_id in (PIZZA, AUTO_REVERT, LOCK, CLIMATE_SETTING, BLOCK):
        assert hass.states.get(entity_id).state == STATE_UNAVAILABLE, entity_id
    assert hass.states.get(GRILL).state == "off"

    await _poll(hass, entry)

    assert hass.states.get(PIZZA).state == STATE_OFF
    assert hass.states.get(CLIMATE_SETTING).state == "Average"


async def test_a_climate_the_app_never_offers_reads_as_unknown(hass: HomeAssistant) -> None:
    packet = bytearray(LIVE["09"])
    packet[9] = 0x15  # climate bits 101 = 5; Lock Temp Display still on
    await _set_up(hass, WireGrill(bytes(packet)))

    assert hass.states.get(CLIMATE_SETTING).state == STATE_UNKNOWN
    assert hass.states.get(LOCK).state == STATE_ON


async def test_warning_lists_every_flag_that_is_set(hass: HomeAssistant) -> None:
    packet = bytearray(LIVE["09"])
    packet[24] = 0x81  # fan overload + low pellets
    await _set_up(hass, WireGrill(bytes(packet)))

    warning = hass.states.get(WARNING)
    assert warning.state == STATE_ON
    assert warning.attributes["warnings"] == ["fan_overload", "low_pellet"]
    assert warning.attributes["raw_code"] == 129


async def test_no_warning_lists_nothing(hass: HomeAssistant) -> None:
    await _set_up(hass, WireGrill(LIVE["09"]))

    warning = hass.states.get(WARNING)
    assert warning.state == STATE_OFF
    assert warning.attributes["warnings"] == []


async def test_the_entry_unloads_with_the_new_platforms(hass: HomeAssistant) -> None:
    entry = await _set_up(hass, WireGrill(LIVE["09"]))

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(PIZZA).state == STATE_UNAVAILABLE
