"""The automations in docs/automations.md, loaded and driven in Home Assistant.

The YAML is read straight from the document, so the document cannot drift
from what was tested. Entity states are set by hand and every service the
automations call is a stand-in, so nothing here reaches a grill or a phone.
Needs pytest-homeassistant-custom-component (requirements.test.txt).
"""

import asyncio
from datetime import timedelta
import logging
from pathlib import Path
import re

import pytest
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    async_mock_service,
)
import yaml

from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

DOC = Path(__file__).parent.parent / "docs" / "automations.md"

GRILL = "climate.green_mountain_grill_gmg12272191"
PROBE = "sensor.green_mountain_grill_gmg12272191_probe_1_temperature"
TARGET = "number.green_mountain_grill_gmg12272191_probe_1_target_temperature"
FIRE = "binary_sensor.patio_gmg_smoker_fire_active"
FINISH = "sensor.patio_gmg_smoker_probe_1_estimated_finish_time"
RATE = "sensor.gmg_probe_1_rate"

GREASE_FIRE_TEXT = (
    "CRITICAL ALERT: Potential grease fire detected! Smoker has been commanded OFF "
    "to initiate safe shutdown. DO NOT open the lid, DO NOT unplug the grill. Keep "
    "lid closed to starve oxygen. Call emergency services if uncontrolled."
)


def _block(language: str) -> str:
    """The document's first code block in `language`."""
    return re.search(rf"```{language}\n(.*?)```", DOC.read_text(encoding="utf-8"), re.S).group(1)


def _automations() -> list[dict]:
    return yaml.safe_load(_block("yaml"))


def _problems(caplog: pytest.LogCaptureFixture) -> list[str]:
    """What Home Assistant logged as a warning or an error -- a template or
    condition that fails says so there, and nowhere a user would look."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING and record.name.startswith("homeassistant")
    ]


@pytest.fixture
async def calls(hass: HomeAssistant) -> dict:
    """Stand-ins for every service the automations use."""
    return {
        "set_temperature": async_mock_service(hass, "climate", "set_temperature"),
        "turn_off": async_mock_service(hass, "climate", "turn_off"),
        "notify": async_mock_service(hass, "notify", "mobile_app_chris_iphone"),
        "log": async_mock_service(hass, "logbook", "log"),
    }


def _grill(hass: HomeAssistant, mode: str, now: float, setpoint: float = 225) -> None:
    hass.states.async_set(GRILL, mode, {"current_temperature": now, "temperature": setpoint})


async def _load(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "automation", {"automation": _automations()})
    await hass.async_block_till_done()


async def _later(hass: HomeAssistant, freezer, **delta) -> None:
    freezer.tick(timedelta(**delta))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()


async def _during_the_hold(hass: HomeAssistant, freezer, **delta) -> None:
    """Move the clock, and let the automations act -- without waiting for the
    hold's 45-minute delay, as hass.async_block_till_done() would."""
    freezer.tick(timedelta(**delta))
    async_fire_time_changed(hass, dt_util.utcnow())
    for _ in range(20):
        await asyncio.sleep(0)


async def test_all_four_load(hass: HomeAssistant, calls: dict) -> None:
    await _load(hass)
    ids = {state.attributes.get("id") for state in hass.states.async_all("automation")}
    assert ids == {"gmg_brisket_stall", "gmg_probe_1_hold_then_shutdown", "gmg_flameout", "gmg_grease_fire"}
    assert all(state.state == "on" for state in hass.states.async_all("automation"))


async def test_the_stall_monitor(hass: HomeAssistant, calls: dict, freezer) -> None:
    _grill(hass, "heat", 225)
    hass.states.async_set(PROBE, "140")
    hass.states.async_set(RATE, "12")
    await _load(hass)

    hass.states.async_set(RATE, "1.5")  # below 2F/h, but probe 1 is under 145F
    await _later(hass, freezer, minutes=16)
    assert calls["notify"] == []

    hass.states.async_set(RATE, "8")
    hass.states.async_set(PROBE, "162")
    await hass.async_block_till_done()
    hass.states.async_set(RATE, "1.2")
    await _later(hass, freezer, minutes=14)
    assert calls["notify"] == [], "not before 15 minutes"
    await _later(hass, freezer, minutes=2)
    assert len(calls["notify"]) == 1
    assert calls["notify"][0].data["title"] == "Brisket has stalled"


async def test_no_stall_alert_once_the_grill_is_off(hass: HomeAssistant, calls: dict, freezer) -> None:
    _grill(hass, "off", 150)  # the cook is over; the probe is still in the meat
    hass.states.async_set(PROBE, "203")
    hass.states.async_set(RATE, "12")
    await _load(hass)

    hass.states.async_set(RATE, "-4")  # resting
    await _later(hass, freezer, minutes=16)

    assert calls["notify"] == []


async def test_no_stall_alert_without_a_probe(
    hass: HomeAssistant, calls: dict, freezer, caplog: pytest.LogCaptureFixture
) -> None:
    _grill(hass, "heat", 225)
    hass.states.async_set(PROBE, "unavailable")  # nothing plugged in
    hass.states.async_set(RATE, "12")
    await _load(hass)

    hass.states.async_set(RATE, "0")
    await _later(hass, freezer, minutes=16)

    assert calls["notify"] == []
    assert _problems(caplog) == []


async def test_hold_then_shut_down(hass: HomeAssistant, calls: dict, freezer) -> None:
    _grill(hass, "heat", 225)
    hass.states.async_set(PROBE, "190")
    hass.states.async_set(TARGET, "203")
    await _load(hass)

    hass.states.async_set(PROBE, "203")
    await _during_the_hold(hass, freezer)
    assert calls["set_temperature"][0].data == {"entity_id": [GRILL], "temperature": 150}
    assert calls["notify"][0].data["title"] == "Probe 1 is done - holding at 150°F"

    _grill(hass, "heat", 180, setpoint=150)  # what the grill would now report
    await _during_the_hold(hass, freezer, minutes=44)
    assert calls["turn_off"] == []
    await _later(hass, freezer, minutes=2)  # the hold is over
    assert calls["turn_off"][0].data == {"entity_id": [GRILL]}
    assert calls["notify"][-1].data["title"] == "Grill shut down after the hold"


@pytest.mark.parametrize(
    ("mode", "setpoint"),
    [("heat", 170), ("off", 150)],
    ids=["setpoint_raised", "turned_off"],
)
async def test_a_hold_changed_by_hand_is_left_alone(
    hass: HomeAssistant, calls: dict, freezer, mode: str, setpoint: int
) -> None:
    _grill(hass, "heat", 225)
    hass.states.async_set(PROBE, "190")
    hass.states.async_set(TARGET, "203")
    await _load(hass)

    hass.states.async_set(PROBE, "204")
    await _during_the_hold(hass, freezer)
    assert calls["set_temperature"]
    _grill(hass, mode, 180, setpoint=setpoint)  # someone stepped in during the hold
    await _during_the_hold(hass, freezer, minutes=30)
    await _later(hass, freezer, minutes=16)  # the hold is over

    assert calls["turn_off"] == []
    assert calls["log"][-1].data["message"] == "the hold was changed by hand; leaving the grill as it is"


async def test_no_hold_when_the_grill_is_not_cooking(hass: HomeAssistant, calls: dict, freezer) -> None:
    _grill(hass, "off", 180)  # turned off; the probe is still in the meat
    hass.states.async_set(PROBE, "190")
    hass.states.async_set(TARGET, "203")
    await _load(hass)

    hass.states.async_set(PROBE, "203")
    await _during_the_hold(hass, freezer, minutes=1)

    assert calls["set_temperature"] == []
    assert calls["notify"] == []


async def test_an_empty_jack_or_no_target_does_not_start_the_hold(
    hass: HomeAssistant, calls: dict, freezer
) -> None:
    _grill(hass, "heat", 225)
    hass.states.async_set(PROBE, "190")
    hass.states.async_set(TARGET, "203")
    await _load(hass)

    hass.states.async_set(PROBE, "unavailable")  # how the integration shows an empty jack
    hass.states.async_set(TARGET, "0")  # and a probe with no target
    hass.states.async_set(PROBE, "250")
    await _during_the_hold(hass, freezer, minutes=1)

    assert calls["set_temperature"] == []


async def test_the_flameout_shutdown(hass: HomeAssistant, calls: dict, freezer) -> None:
    _grill(hass, "heat", 250)
    hass.states.async_set(FIRE, "on")
    await _load(hass)

    hass.states.async_set(FIRE, "off")  # the fire is out, but the grill is still hot
    await _later(hass, freezer, minutes=2)
    assert calls["turn_off"] == []

    _grill(hass, "heat", 125)  # ...until it cools below 130F
    await _later(hass, freezer, seconds=30)
    _grill(hass, "heat", 122)
    assert calls["turn_off"] == [], "not before a full minute below 130F"
    await _later(hass, freezer, seconds=30)

    assert calls["turn_off"][0].data == {"entity_id": [GRILL]}
    alarm = calls["notify"][0].data
    assert alarm["title"] == "GMG ALARM: flameout"
    assert alarm["data"]["push"]["sound"]["critical"] == 1


async def test_a_fire_that_dies_below_130_is_a_flameout(
    hass: HomeAssistant, calls: dict, freezer
) -> None:
    _grill(hass, "heat", 125)  # the lid has been open; the fire still burns
    hass.states.async_set(FIRE, "on")
    await _load(hass)

    hass.states.async_set(FIRE, "off")
    await _later(hass, freezer, seconds=30)
    assert calls["turn_off"] == [], "not before a full minute without fire"
    await _later(hass, freezer, seconds=30)

    assert calls["turn_off"][0].data == {"entity_id": [GRILL]}


async def test_a_one_poll_flicker_is_not_a_flameout(hass: HomeAssistant, calls: dict, freezer) -> None:
    _grill(hass, "heat", 131)
    hass.states.async_set(FIRE, "on")
    await _load(hass)

    hass.states.async_set(FIRE, "off")  # one poll reads the fire as out...
    _grill(hass, "heat", 129)  # ...just as the grill dips below 130F
    await _later(hass, freezer, seconds=30)
    hass.states.async_set(FIRE, "on")
    await _later(hass, freezer, minutes=2)

    assert calls["turn_off"] == []


async def test_a_cold_start_and_an_open_lid_are_not_flameouts(
    hass: HomeAssistant, calls: dict, freezer
) -> None:
    _grill(hass, "off", 70)
    hass.states.async_set(FIRE, "off")
    await _load(hass)

    _grill(hass, "heat", 70)  # switched on: cold, and the fire is lighting
    hass.states.async_set(FIRE, "on")
    for temperature in (100, 150, 180):
        await _later(hass, freezer, seconds=30)
        _grill(hass, "heat", temperature)
    await _later(hass, freezer, seconds=30)
    _grill(hass, "heat", 125)  # the lid is open on a cold day; the fire still burns
    await _later(hass, freezer, minutes=2)

    assert calls["turn_off"] == []


async def test_the_end_of_a_cook_is_not_a_flameout(hass: HomeAssistant, calls: dict, freezer) -> None:
    _grill(hass, "heat", 225)
    hass.states.async_set(FIRE, "on")
    await _load(hass)

    _grill(hass, "off", 225)  # turned off: the fan cooldown starts
    hass.states.async_set(FIRE, "off")
    for temperature in (200, 160, 129, 100):
        await _later(hass, freezer, minutes=2)
        _grill(hass, "off", temperature)
    await hass.async_block_till_done()

    assert calls["turn_off"] == []
    assert calls["notify"] == []


async def test_the_grease_fire_shutdown(hass: HomeAssistant, calls: dict, freezer) -> None:
    _grill(hass, "heat", 410, setpoint=450)
    await _load(hass)

    # Steady for five minutes: unchanged polls only move last_reported.
    for _ in range(10):
        await _later(hass, freezer, seconds=30)
        _grill(hass, "heat", 410, setpoint=450)
    await _later(hass, freezer, seconds=30)
    _grill(hass, "heat", 455, setpoint=450)  # +45F since the last poll
    await hass.async_block_till_done()

    assert calls["turn_off"][0].data == {"entity_id": [GRILL]}
    alert = calls["notify"][0].data
    assert alert["title"] == "CRITICAL: possible grease fire"
    assert " ".join(alert["message"].split()) == GREASE_FIRE_TEXT
    assert alert["data"]["push"]["sound"]["critical"] == 1


@pytest.mark.parametrize(
    ("mode", "before", "after", "seconds"),
    [
        ("heat", 380, 430, 30),  # the grill was not above 400F yet
        ("heat", 420, 455, 30),  # only +35F
        ("heat", 420, 470, 90),  # +50F, but over 90 seconds
        ("off", 420, 470, 30),  # not cooking: the check is for a cook
    ],
    ids=["below_400", "small_jump", "slow_jump", "grill_off"],
)
async def test_what_is_not_a_grease_fire(
    hass: HomeAssistant, calls: dict, freezer, mode: str, before: int, after: int, seconds: int
) -> None:
    _grill(hass, mode, before, setpoint=450)
    await _load(hass)

    await _later(hass, freezer, seconds=seconds)
    _grill(hass, mode, after, setpoint=450)
    await hass.async_block_till_done()

    assert calls["turn_off"] == []


async def test_the_grill_appearing_or_dropping_out_is_not_a_grease_fire(
    hass: HomeAssistant, calls: dict, freezer, caplog: pytest.LogCaptureFixture
) -> None:
    """Home Assistant starting mid-cook, a failed poll and an unload each move
    the grill's temperature without two readings to compare."""
    hass.states.async_set(RATE, "12")  # the stall monitor's helper exists
    await _load(hass)

    _grill(hass, "heat", 450, setpoint=450)  # first seen, hot
    await _later(hass, freezer, seconds=30)
    hass.states.async_set(GRILL, "unavailable")  # a failed poll
    await _later(hass, freezer, seconds=30)
    _grill(hass, "heat", 500, setpoint=450)  # back
    await _later(hass, freezer, seconds=30)
    hass.states.async_remove(GRILL)  # the integration unloads
    await hass.async_block_till_done()

    assert calls["turn_off"] == []
    assert _problems(caplog) == []


@pytest.mark.parametrize(
    ("state", "shown"),
    [
        ("2026-09-16T20:15:00+00:00", "done around 20:15"),
        ("unknown", "no finish time yet"),
        ("unavailable", "no finish time yet"),
    ],
)
async def test_the_finish_time_snippet(hass: HomeAssistant, state: str, shown: str) -> None:
    await hass.config.async_set_time_zone("UTC")
    hass.states.async_set(FINISH, state)

    assert Template(_block("jinja"), hass).async_render(parse_result=False).strip() == shown
