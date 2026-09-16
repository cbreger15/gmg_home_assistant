"""The Grill Config controls, through Home Assistant.

The grill here is a WireGrill (tests/grill_wire.py) behind the real
gmg.Grill, so a switch or select drives the real read-change-write-confirm
sequence. Needs pytest-homeassistant-custom-component (requirements.test.txt).
"""

import asyncio
from datetime import timedelta
import threading

import pytest

from homeassistant.const import (
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    EntityCategory,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.gmg import gmg
from custom_components.gmg.const import CONFIG_READ_ATTEMPTS, DOMAIN
from tests.grill_wire import (
    APP_CALIBRATED_ALL,
    LIVE,
    MERGED_104,
    STATUS,
    TAIL_CUT_51,
    NoNetwork,
    WireGrill,
)
from tests.ha_setup import PREFIX, call as _call, poll as _poll, set_up as _set_up

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture(autouse=True)
def no_real_grill(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any send that gets past WireGrill fails the test instead of leaving the machine."""
    monkeypatch.setattr(gmg, "socket", NoNetwork())


PIZZA = f"switch.{PREFIX}_pizza_mode"
AUTO_REVERT = f"switch.{PREFIX}_auto_revert_wifi"
LOCK = f"switch.{PREFIX}_lock_temp_display"
CLIMATE_SETTING = f"select.{PREFIX}_climate_setting"
BLOCK = f"sensor.{PREFIX}_config_block"
WARNING = f"binary_sensor.{PREFIX}_warning"
GRILL = f"climate.{PREFIX}"


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


async def test_the_calibration_boxes_show_as_the_app_does(hass: HomeAssistant) -> None:
    await _set_up(hass, WireGrill(APP_CALIBRATED_ALL))  # the app showed -2/+5, -8/-3, -4/+6

    block = hass.states.get(BLOCK)
    assert block.state == "06 09 12 37 11 16 15 1f"
    assert {
        name: block.attributes[name]
        for name in (
            "grill_adjustment_150f",
            "grill_adjustment_500f",
            "probe_1_adjustment_32f",
            "probe_1_adjustment_212f",
            "probe_2_adjustment_32f",
            "probe_2_adjustment_212f",
        )
    } == {
        "grill_adjustment_150f": -2,
        "grill_adjustment_500f": 5,
        "probe_1_adjustment_32f": -8,
        "probe_1_adjustment_212f": -3,
        "probe_2_adjustment_32f": -4,
        "probe_2_adjustment_212f": 6,
    }
    assert block.attributes["grill_adjustment_500f_raw"] == 55


async def test_only_pizza_mode_is_a_user_facing_control(hass: HomeAssistant) -> None:
    """The HomeKit bridge skips an entity with an entity_category unless it is
    included by entity ID, so this decides what reaches Apple Home by
    default: Pizza Mode, and nothing else."""
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
    wire.script = ([None, TAIL_CUT_51, MERGED_104] * CONFIG_READ_ATTEMPTS)[:CONFIG_READ_ATTEMPTS]

    with pytest.raises(HomeAssistantError, match="pizza_mode.*Nothing was sent"):
        await _call(hass, "switch", "turn_on", PIZZA)

    assert wire.writes() == []


async def test_a_failed_write_shows_what_the_grill_now_reports(hass: HomeAssistant) -> None:
    # The grill takes the frame but lands Auto-Revert WiFi on as well.
    wire = WireGrill(LIVE["09"], apply=lambda block: block[:1] + bytes([block[1] | 0x02]) + block[2:])
    await _set_up(hass, wire)

    with pytest.raises(HomeAssistantError, match="pizza_mode.*something other than"):
        await _call(hass, "switch", "turn_on", PIZZA)
    await hass.async_block_till_done()

    assert hass.states.get(BLOCK).state == "06 2b 14 32 19 19 19 19"
    assert hass.states.get(AUTO_REVERT).state == STATE_ON


async def test_polls_wait_while_a_write_is_in_progress(hass: HomeAssistant) -> None:
    """A poll that read the grill before the write, and finished after it,
    would put the old block back on screen."""
    wire = WireGrill(LIVE["09"])
    entry = await _set_up(hass, wire)
    coordinator = hass.data[DOMAIN][entry.entry_id]

    frame_sent, release = threading.Event(), threading.Event()
    wire_send = coordinator.grill.send

    def send_holding_the_frame(message: bytes, timeout: float = 1):
        reply = wire_send(message, timeout)
        if message[:2] == b"UC":
            frame_sent.set()
            assert release.wait(5)
        return reply

    coordinator.grill.send = send_holding_the_frame
    write = hass.async_create_task(_call(hass, "switch", "turn_on", PIZZA))
    assert await hass.async_add_executor_job(frame_sent.wait, 5)

    polls = wire.sent().count(STATUS)
    refresh = hass.async_create_task(coordinator.async_refresh())
    await asyncio.sleep(0.2)
    assert wire.sent().count(STATUS) == polls, "a poll ran while the write was in progress"

    release.set()
    await write
    await refresh
    await hass.async_block_till_done()

    assert hass.states.get(PIZZA).state == STATE_ON
    assert hass.states.get(BLOCK).state == "06 29 14 32 19 19 19 19"


async def test_an_unverified_api_version_shows_the_block_but_offers_no_controls(
    hass: HomeAssistant,
) -> None:
    """Byte 9's layout is only confirmed on API version 6; elsewhere the
    switches would show guesses and every toggle would be refused."""
    packet = bytearray(LIVE["09"])
    packet[8] = 5
    await _set_up(hass, WireGrill(bytes(packet)))

    for entity_id in (PIZZA, AUTO_REVERT, LOCK, CLIMATE_SETTING):
        assert hass.states.get(entity_id).state == STATE_UNAVAILABLE, entity_id
    block = hass.states.get(BLOCK)
    assert block.state == "05 09 14 32 19 19 19 19"
    assert block.attributes["api_version"] == 5


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


async def test_settings_go_unavailable_by_90_seconds_without_a_whole_packet(
    hass: HomeAssistant, freezer
) -> None:
    """The last whole block stands in for two cut replies, not for ever:
    automations must never act on a setting nobody has seen for 90 s. The
    cut-off sits between polls, so a poll a few seconds early or late
    doesn't change the outcome."""
    freezer.move_to("2026-09-16 18:00:00+00:00")
    wire = WireGrill(LIVE["09"])
    entry = await _set_up(hass, wire)

    async def cut_reply(after_seconds: int) -> None:
        freezer.tick(timedelta(seconds=after_seconds))
        wire.script = [TAIL_CUT_51]
        await _poll(hass, entry)

    await cut_reply(30)
    assert hass.states.get(PIZZA).state == STATE_OFF, "30 s after the whole packet"
    await cut_reply(35)
    assert hass.states.get(PIZZA).state == STATE_OFF, "65 s: a late second poll"

    await cut_reply(20)  # 85 s: an early third poll
    for entity_id in (PIZZA, AUTO_REVERT, LOCK, CLIMATE_SETTING, BLOCK):
        assert hass.states.get(entity_id).state == STATE_UNAVAILABLE, entity_id
    assert hass.states.get(GRILL).state == "off", "the status fields are still fresh"

    freezer.tick(timedelta(seconds=30))
    await _poll(hass, entry)  # a whole packet again
    assert hass.states.get(PIZZA).state == STATE_OFF


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
