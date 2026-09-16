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

from homeassistant.core import HomeAssistant, ServiceCall, callback
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


def _text(message: str) -> str:
    """A notification as a phone shows it: YAML folding leaves no mark."""
    return " ".join(message.split())


class Calls(dict):
    """The service calls the automations made, by service name.

    The grill obeys set_temperature and turn_off -- its climate entity
    changes as the real one does on the refresh that follows -- unless
    `deaf` is set, as if the command never reached it.
    """

    deaf = False


@pytest.fixture
async def calls(hass: HomeAssistant) -> Calls:
    """Stand-ins for every service the automations use."""
    made = Calls(
        set_temperature=[],
        turn_off=[],
        notify=async_mock_service(hass, "notify", "mobile_app_chris_iphone"),
        log=async_mock_service(hass, "logbook", "log"),
    )

    def grill_changes(mode: str | None = None, **attributes) -> None:
        state = hass.states.get(GRILL)
        hass.states.async_set(GRILL, mode or state.state, {**state.attributes, **attributes})

    @callback
    def set_temperature(call: ServiceCall) -> None:
        made["set_temperature"].append(call)
        if not made.deaf:
            grill_changes(temperature=call.data["temperature"])

    @callback
    def turn_off(call: ServiceCall) -> None:
        made["turn_off"].append(call)
        if not made.deaf:
            grill_changes(mode="off")

    hass.services.async_register("climate", "set_temperature", set_temperature)
    hass.services.async_register("climate", "turn_off", turn_off)
    return made


def _grill(
    hass: HomeAssistant, mode: str, now: float, setpoint: float = 225, action: str | None = None
) -> None:
    attributes = {"current_temperature": now, "temperature": setpoint}
    if action:
        attributes["hvac_action"] = action
    hass.states.async_set(GRILL, mode, attributes)


def _cooking(hass: HomeAssistant, probe: str = "190") -> None:
    """A cook with the helper in place: the grill at 225F, probe 1 short of 203F."""
    hass.states.async_set(RATE, "12")
    _grill(hass, "heat", 225)
    hass.states.async_set(PROBE, probe)
    hass.states.async_set(TARGET, "203")


async def _load(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "automation", {"automation": _automations()})
    await hass.async_block_till_done()


async def _let_run(hass: HomeAssistant) -> None:
    for _ in range(50):
        await asyncio.sleep(0)


async def _settle(hass: HomeAssistant) -> None:
    """Let every automation finish what it started.

    One still waiting (the hold's delay, a wait for the grill) never would
    under a frozen clock, and hass.async_block_till_done() would wait for it
    for ever -- so that fails here instead.
    """
    await _let_run(hass)
    running = [
        state.entity_id
        for state in hass.states.async_all("automation")
        if state.attributes.get("current")
    ]
    assert not running, f"still running: {running}"
    await hass.async_block_till_done()


async def _later(hass: HomeAssistant, freezer, **delta) -> None:
    """Move the clock, and let every automation finish what it started."""
    freezer.tick(timedelta(**delta))
    async_fire_time_changed(hass, dt_util.utcnow())
    await _settle(hass)


async def _meanwhile(hass: HomeAssistant, freezer, **delta) -> None:
    """Move the clock, and let the automations act -- including one that is
    itself waiting, which _later would refuse."""
    freezer.tick(timedelta(**delta))
    async_fire_time_changed(hass, dt_util.utcnow())
    await _let_run(hass)


async def test_all_five_load(hass: HomeAssistant, calls: Calls) -> None:
    await _load(hass)
    ids = {state.attributes.get("id") for state in hass.states.async_all("automation")}
    assert ids == {
        "gmg_brisket_stall",
        "gmg_probe_1_hold_then_shutdown",
        "gmg_flameout",
        "gmg_grease_fire",
        "gmg_fire_not_lighting",
    }
    assert all(state.state == "on" for state in hass.states.async_all("automation"))


# 1. Stall monitor


async def test_the_stall_monitor(hass: HomeAssistant, calls: Calls, freezer) -> None:
    _grill(hass, "heat", 225)
    hass.states.async_set(PROBE, "140")
    hass.states.async_set(RATE, "12")
    await _load(hass)

    hass.states.async_set(RATE, "1.5")  # below 2F/h, but probe 1 is under 145F
    await _later(hass, freezer, minutes=16)
    assert calls["notify"] == []

    hass.states.async_set(RATE, "8")
    hass.states.async_set(PROBE, "162")
    await _settle(hass)
    hass.states.async_set(RATE, "1.2")
    await _later(hass, freezer, minutes=14)
    assert calls["notify"] == [], "not before 15 minutes"
    await _later(hass, freezer, minutes=2)
    assert len(calls["notify"]) == 1
    assert calls["notify"][0].data["title"] == "Brisket has stalled"


async def test_one_stall_alert_despite_a_failed_poll(hass: HomeAssistant, calls: Calls, freezer) -> None:
    _cooking(hass, probe="165")
    hass.states.async_set(RATE, "8")
    await _load(hass)

    hass.states.async_set(RATE, "1")
    await _later(hass, freezer, minutes=16)
    assert len(calls["notify"]) == 1

    hass.states.async_set(RATE, "unavailable")  # a failed poll takes the helper with it
    await _later(hass, freezer, seconds=30)
    hass.states.async_set(RATE, "1")
    await _later(hass, freezer, minutes=16)
    assert len(calls["notify"]) == 1, "still the same stall"

    hass.states.async_set(RATE, "6")  # hours later, a second plateau
    await _later(hass, freezer, hours=4)
    hass.states.async_set(RATE, "1")
    await _later(hass, freezer, minutes=16)
    assert len(calls["notify"]) == 2


async def test_no_stall_alert_once_the_grill_is_off(hass: HomeAssistant, calls: Calls, freezer) -> None:
    _grill(hass, "off", 150)  # the cook is over; the probe is still in the meat
    hass.states.async_set(PROBE, "203")
    hass.states.async_set(RATE, "12")
    await _load(hass)

    hass.states.async_set(RATE, "-4")  # resting
    await _later(hass, freezer, minutes=16)

    assert calls["notify"] == []


async def test_no_stall_alert_without_a_probe(
    hass: HomeAssistant, calls: Calls, freezer, caplog: pytest.LogCaptureFixture
) -> None:
    _cooking(hass, probe="unavailable")  # nothing plugged in
    await _load(hass)

    hass.states.async_set(RATE, "0")
    await _later(hass, freezer, minutes=16)

    assert calls["notify"] == []
    assert _problems(caplog) == []


# 2. Hold, then shut down


async def test_hold_then_shut_down(hass: HomeAssistant, calls: Calls, freezer) -> None:
    _cooking(hass)
    await _load(hass)

    hass.states.async_set(PROBE, "203")
    await _meanwhile(hass, freezer)
    assert calls["set_temperature"][0].data == {"entity_id": [GRILL], "temperature": 150}
    assert calls["notify"][0].data["title"] == "Probe 1 is done - holding at 150°F"

    await _meanwhile(hass, freezer, minutes=44)
    assert calls["turn_off"] == []
    await _later(hass, freezer, minutes=2)  # the hold is over

    assert calls["turn_off"][0].data == {"entity_id": [GRILL]}
    assert len(calls["turn_off"]) == 1, "the grill read as off at once"
    assert calls["notify"][-1].data["title"] == "Grill shut down after the hold"


@pytest.mark.parametrize(
    ("mode", "setpoint", "logged", "told"),
    [
        (
            "heat",
            170,
            "the hold ended with the grill changed or unreadable; left as it is",
            "45 minutes after probe 1 finished, the grill is still heating, set to 170°F, "
            "so it was left as it is. Turn it off yourself when you're done.",
        ),
        ("off", 150, "the grill was turned off during the hold", None),
    ],
    ids=["setpoint_raised", "turned_off"],
)
async def test_a_hold_changed_by_hand_is_left_alone(
    hass: HomeAssistant, calls: Calls, freezer, mode: str, setpoint: int, logged: str, told: str | None
) -> None:
    _cooking(hass)
    await _load(hass)

    hass.states.async_set(PROBE, "204")
    await _meanwhile(hass, freezer)
    _grill(hass, mode, 180, setpoint=setpoint)  # someone stepped in during the hold
    await _meanwhile(hass, freezer, minutes=46)

    assert calls["turn_off"] == []
    assert calls["log"][-1].data["message"] == logged
    if told:
        assert _text(calls["notify"][-1].data["message"]) == told
    else:
        assert len(calls["notify"]) == 1, "only the hold's own"

    _grill(hass, "off", 170)  # the cook ends
    await _settle(hass)


async def test_one_hold_per_cook(
    hass: HomeAssistant, calls: Calls, freezer, caplog: pytest.LogCaptureFixture
) -> None:
    """A failed poll re-arms the trigger: probe 1 and its target read as
    unavailable, then above the target again."""
    _cooking(hass)
    await _load(hass)

    hass.states.async_set(PROBE, "203")
    await _meanwhile(hass, freezer)
    _grill(hass, "heat", 190, setpoint=170)  # taken over: carry on at 170F
    await _meanwhile(hass, freezer, minutes=46)

    for entity_id in (GRILL, PROBE, TARGET):  # a failed poll
        hass.states.async_set(entity_id, "unavailable")
    await _meanwhile(hass, freezer, seconds=30)
    _grill(hass, "heat", 190, setpoint=170)
    hass.states.async_set(PROBE, "206")
    hass.states.async_set(TARGET, "203")
    await _meanwhile(hass, freezer, minutes=50)

    assert len(calls["set_temperature"]) == 1, "no second hold"
    assert calls["turn_off"] == []
    assert _problems(caplog) == [], "and no 'already running' warning either"

    # The next cook gets a hold of its own.
    _grill(hass, "off", 180, setpoint=170)
    await _later(hass, freezer, hours=2)
    _grill(hass, "heat", 225)
    hass.states.async_set(PROBE, "150")
    await _later(hass, freezer, hours=3)
    hass.states.async_set(PROBE, "203")
    await _meanwhile(hass, freezer)
    assert len(calls["set_temperature"]) == 2

    _grill(hass, "off", 150)
    await _later(hass, freezer, minutes=46)


async def test_a_setpoint_the_grill_doesnt_take_is_reported(hass: HomeAssistant, calls: Calls, freezer) -> None:
    _cooking(hass)
    await _load(hass)
    calls.deaf = True  # e.g. the grill is under 150F, where the integration won't change it

    hass.states.async_set(PROBE, "203")
    await _meanwhile(hass, freezer)
    assert len(calls["set_temperature"]) == 1
    assert calls["notify"] == [], "not before the check"
    await _meanwhile(hass, freezer, seconds=91)

    told = calls["notify"][0].data
    assert told["title"] == "Probe 1 is done - the grill didn't drop to 150°F"
    assert _text(told["message"]) == (
        "Probe 1 reached 203°F, but the grill is still set to 225°F. "
        "It won't be turned off automatically."
    )
    await _meanwhile(hass, freezer, minutes=50)
    assert calls["turn_off"] == [], "no hold, so no shutdown"

    for entity_id in (PROBE, TARGET):  # a failed poll re-arms the trigger
        hass.states.async_set(entity_id, "unavailable")
    await _meanwhile(hass, freezer, seconds=30)
    hass.states.async_set(PROBE, "204")
    hass.states.async_set(TARGET, "203")
    await _meanwhile(hass, freezer, minutes=5)
    assert len(calls["set_temperature"]) == 1, "and no second try this cook"
    assert len(calls["notify"]) == 1

    _grill(hass, "off", 180)
    await _settle(hass)


async def test_a_failed_poll_at_the_end_of_the_hold_is_waited_out(
    hass: HomeAssistant, calls: Calls, freezer
) -> None:
    _cooking(hass)
    await _load(hass)

    hass.states.async_set(PROBE, "203")
    await _meanwhile(hass, freezer, minutes=44, seconds=45)
    hass.states.async_set(GRILL, "unavailable")  # just as the hold ends
    await _meanwhile(hass, freezer, seconds=30)
    assert calls["turn_off"] == []
    _grill(hass, "heat", 180, setpoint=150)  # back on the next poll
    await _later(hass, freezer, seconds=30)

    assert len(calls["turn_off"]) == 1
    assert calls["notify"][-1].data["title"] == "Grill shut down after the hold"


async def test_a_grill_unreadable_at_the_end_of_the_hold_is_reported(
    hass: HomeAssistant, calls: Calls, freezer
) -> None:
    _cooking(hass)
    await _load(hass)

    hass.states.async_set(PROBE, "203")
    await _meanwhile(hass, freezer, minutes=40)
    hass.states.async_set(GRILL, "unavailable")  # the grill drops off the network
    await _meanwhile(hass, freezer, minutes=5, seconds=30)  # the hold ends
    await _meanwhile(hass, freezer, minutes=9)
    assert calls["notify"][-1].data["title"] == "Probe 1 is done - holding at 150°F"
    await _meanwhile(hass, freezer, minutes=1, seconds=30)  # 10 minutes of waiting

    assert calls["turn_off"] == []
    left = calls["notify"][-1].data
    assert left["title"] == "Grill left on after the hold"
    assert _text(left["message"]) == (
        "45 minutes after probe 1 finished, the grill couldn't be read, "
        "so it was left as it is. Turn it off yourself when you're done."
    )

    _grill(hass, "off", 150)  # back, and turned off
    await _settle(hass)


async def test_a_hold_shutdown_the_grill_doesnt_confirm(hass: HomeAssistant, calls: Calls, freezer) -> None:
    _cooking(hass)
    await _load(hass)

    hass.states.async_set(PROBE, "203")
    await _meanwhile(hass, freezer)
    calls.deaf = True  # the setpoint landed; the turn-off won't
    await _meanwhile(hass, freezer, minutes=45, seconds=1)
    assert len(calls["turn_off"]) == 1
    await _later(hass, freezer, seconds=91)

    assert len(calls["turn_off"]) == 2
    told = calls["notify"][-1].data
    assert told["title"] == "Grill didn't confirm the shutdown"
    assert _text(told["message"]) == (
        "The hold is over and the grill has been told to turn off twice, "
        "but it still reads heat. Turn it off at the grill."
    )


async def test_no_hold_when_the_grill_is_not_cooking(hass: HomeAssistant, calls: Calls, freezer) -> None:
    _grill(hass, "off", 180)  # turned off; the probe is still in the meat
    hass.states.async_set(PROBE, "190")
    hass.states.async_set(TARGET, "203")
    await _load(hass)

    hass.states.async_set(PROBE, "203")
    await _meanwhile(hass, freezer, minutes=1)

    assert calls["set_temperature"] == []
    assert calls["notify"] == []


async def test_an_empty_jack_or_no_target_does_not_start_the_hold(
    hass: HomeAssistant, calls: Calls, freezer
) -> None:
    _cooking(hass)
    await _load(hass)

    hass.states.async_set(PROBE, "unavailable")  # how the integration shows an empty jack
    hass.states.async_set(TARGET, "0")  # and a probe with no target
    hass.states.async_set(PROBE, "250")
    await _meanwhile(hass, freezer, minutes=1)

    assert calls["set_temperature"] == []


# 3. Flameout


async def test_the_flameout_shutdown(hass: HomeAssistant, calls: Calls, freezer) -> None:
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
    assert len(calls["turn_off"]) == 1, "the grill read as off at once"
    alarm = calls["notify"][0].data
    assert alarm["title"] == "GMG ALARM: flameout"
    assert alarm["data"]["push"]["sound"]["critical"] == 1
    assert len(calls["notify"]) == 1


async def test_a_fire_that_dies_below_130_is_a_flameout(
    hass: HomeAssistant, calls: Calls, freezer
) -> None:
    _grill(hass, "heat", 125)  # the lid has been open; the fire still burns
    hass.states.async_set(FIRE, "on")
    await _load(hass)

    hass.states.async_set(FIRE, "off")
    await _later(hass, freezer, seconds=30)
    assert calls["turn_off"] == [], "not before a full minute without fire"
    await _later(hass, freezer, seconds=30)

    assert calls["turn_off"][0].data == {"entity_id": [GRILL]}


async def test_a_flameout_shutdown_the_grill_doesnt_confirm(hass: HomeAssistant, calls: Calls, freezer) -> None:
    _grill(hass, "heat", 125)
    hass.states.async_set(FIRE, "on")
    await _load(hass)
    calls.deaf = True

    hass.states.async_set(FIRE, "off")
    await _later(hass, freezer, seconds=30)
    await _meanwhile(hass, freezer, seconds=30)  # a minute without fire
    assert len(calls["turn_off"]) == 1
    assert calls["notify"][0].data["title"] == "GMG ALARM: flameout", "the alarm doesn't wait"
    await _meanwhile(hass, freezer, seconds=60)
    assert len(calls["turn_off"]) == 1, "not before 90 seconds"
    await _later(hass, freezer, seconds=31)

    assert len(calls["turn_off"]) == 2
    follow_up = calls["notify"][-1].data
    assert follow_up["title"] == "GMG ALARM: flameout - grill still on"
    assert _text(follow_up["message"]) == (
        "The grill has been told to turn off twice, but it still reads heat. "
        "Turn it off at the grill's controller."
    )
    assert follow_up["data"]["push"]["sound"]["critical"] == 1


async def test_a_one_poll_flicker_is_not_a_flameout(hass: HomeAssistant, calls: Calls, freezer) -> None:
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
    hass: HomeAssistant, calls: Calls, freezer
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


async def test_the_end_of_a_cook_is_not_a_flameout(hass: HomeAssistant, calls: Calls, freezer) -> None:
    _grill(hass, "heat", 225)
    hass.states.async_set(FIRE, "on")
    await _load(hass)

    _grill(hass, "off", 225)  # turned off: the fan cooldown starts
    hass.states.async_set(FIRE, "off")
    for temperature in (200, 160, 129, 100):
        await _later(hass, freezer, minutes=2)
        _grill(hass, "off", temperature)
    await _settle(hass)

    assert calls["turn_off"] == []
    assert calls["notify"] == []


# 4. Possible grease fire


async def test_the_grease_fire_shutdown(hass: HomeAssistant, calls: Calls, freezer) -> None:
    _grill(hass, "heat", 410, setpoint=450)
    await _load(hass)

    # Steady for five minutes: unchanged polls only move last_reported.
    for _ in range(10):
        await _later(hass, freezer, seconds=30)
        _grill(hass, "heat", 410, setpoint=450)
    await _later(hass, freezer, seconds=30)
    _grill(hass, "heat", 455, setpoint=450)  # +45F since the last poll
    await _settle(hass)

    assert calls["turn_off"][0].data == {"entity_id": [GRILL]}
    assert len(calls["turn_off"]) == 1, "the grill read as off at once"
    alert = calls["notify"][0].data
    assert alert["title"] == "CRITICAL: possible grease fire"
    assert _text(alert["message"]) == GREASE_FIRE_TEXT
    assert alert["data"]["push"]["sound"]["critical"] == 1
    assert len(calls["notify"]) == 1


async def test_a_grease_fire_shutdown_the_grill_doesnt_confirm(
    hass: HomeAssistant, calls: Calls, freezer
) -> None:
    _grill(hass, "heat", 420, setpoint=450)
    await _load(hass)
    calls.deaf = True

    await _later(hass, freezer, seconds=30)
    _grill(hass, "heat", 470, setpoint=450)
    await _meanwhile(hass, freezer)
    assert len(calls["turn_off"]) == 1
    assert _text(calls["notify"][0].data["message"]) == GREASE_FIRE_TEXT, "the alert doesn't wait"
    await _meanwhile(hass, freezer, seconds=89)
    assert len(calls["turn_off"]) == 1, "not before 90 seconds"
    await _later(hass, freezer, seconds=2)

    assert len(calls["turn_off"]) == 2
    follow_up = calls["notify"][-1].data
    assert follow_up["title"] == "CRITICAL: grease fire - shutdown NOT confirmed"
    assert _text(follow_up["message"]) == (
        "The grill has been told to turn off twice, but it still reads heat. "
        "Turn it off at the grill's controller. Keep the lid closed and don't unplug the grill."
    )
    assert follow_up["data"]["push"]["sound"]["critical"] == 1


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
    hass: HomeAssistant, calls: Calls, freezer, mode: str, before: int, after: int, seconds: int
) -> None:
    _grill(hass, mode, before, setpoint=450)
    await _load(hass)

    await _later(hass, freezer, seconds=seconds)
    _grill(hass, mode, after, setpoint=450)
    await _settle(hass)

    assert calls["turn_off"] == []


async def test_the_grill_appearing_or_dropping_out_is_not_a_grease_fire(
    hass: HomeAssistant, calls: Calls, freezer, caplog: pytest.LogCaptureFixture
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
    await _settle(hass)

    assert calls["turn_off"] == []
    assert _problems(caplog) == []


# 5. Fire not lighting


async def test_a_fire_that_wont_light_is_reported(
    hass: HomeAssistant, calls: Calls, freezer, caplog: pytest.LogCaptureFixture
) -> None:
    hass.states.async_set(RATE, "12")
    _grill(hass, "off", 70, action="off")
    await _load(hass)

    _grill(hass, "heat", 70, action="preheating")  # switched on; the fire never takes
    for temperature in (71, 72, 72, 73):
        await _later(hass, freezer, minutes=7)
        _grill(hass, "heat", temperature, action="preheating")
    await _later(hass, freezer, minutes=1)
    assert calls["notify"] == [], "not before 30 minutes"
    await _later(hass, freezer, minutes=1, seconds=1)

    alarm = calls["notify"][0].data
    assert alarm["title"] == "GMG ALARM: the fire isn't lighting"
    assert _text(alarm["message"]) == (
        "The grill has been trying to light for 30 minutes and is only at 73°F. "
        "Check the fire. If it's out, turn the grill off and let it cool, "
        "and clear the firepot before relighting."
    )
    assert alarm["data"]["push"]["sound"]["critical"] == 1
    assert calls["turn_off"] == [], "an alert only"
    assert calls["log"][-1].data["message"] == "still trying to light after 30 minutes, at 73°F"
    assert _problems(caplog) == []


async def test_a_relight_that_isnt_taking_is_reported(hass: HomeAssistant, calls: Calls, freezer) -> None:
    _grill(hass, "heat", 225, action="heating")
    await _load(hass)

    _grill(hass, "heat", 150, action="preheating")  # the fire died; the grill tries to relight
    await _later(hass, freezer, minutes=10)
    _grill(hass, "heat", 131, action="preheating")
    await _later(hass, freezer, minutes=10)
    _grill(hass, "heat", 129, action="preheating")  # below 130F from here
    await _later(hass, freezer, minutes=15)
    _grill(hass, "heat", 118, action="preheating")
    await _later(hass, freezer, minutes=14)
    assert calls["notify"] == [], "not before 30 minutes below 130F"
    await _later(hass, freezer, minutes=1, seconds=1)

    assert calls["notify"][0].data["title"] == "GMG ALARM: the fire isn't lighting"
    assert calls["turn_off"] == []


async def test_a_slow_start_is_not_reported(hass: HomeAssistant, calls: Calls, freezer) -> None:
    _grill(hass, "off", 60, action="off")
    await _load(hass)

    # Below 130F for 27 minutes: slower than any start in the log (11-20
    # minutes to running), as on a cold day.
    _grill(hass, "heat", 60, action="preheating")
    for temperature in (75, 90, 105, 118, 129):
        await _later(hass, freezer, minutes=5)
        _grill(hass, "heat", temperature, action="preheating")
    await _later(hass, freezer, minutes=2)
    _grill(hass, "heat", 140, action="preheating")
    await _later(hass, freezer, minutes=4)
    _grill(hass, "heat", 151, action="heating")
    await _later(hass, freezer, hours=1)

    assert calls["notify"] == []


async def test_a_long_return_to_startup_above_130_is_not_reported(
    hass: HomeAssistant, calls: Calls, freezer
) -> None:
    _grill(hass, "heat", 225, action="heating")
    await _load(hass)

    _grill(hass, "heat", 147, action="preheating")  # as on 10 Sep: 72 minutes at 147-150F
    await _later(hass, freezer, minutes=72)
    _grill(hass, "heat", 150, action="heating")
    await _later(hass, freezer, minutes=5)

    assert calls["notify"] == []


async def test_a_grill_heating_below_130_is_not_reported(hass: HomeAssistant, calls: Calls, freezer) -> None:
    _grill(hass, "heat", 225, action="heating")
    await _load(hass)

    _grill(hass, "heat", 125, action="heating")  # the lid is open; the fire still burns
    await _later(hass, freezer, minutes=40)

    assert calls["notify"] == []


async def test_a_failed_poll_starts_the_30_minutes_again(hass: HomeAssistant, calls: Calls, freezer) -> None:
    _grill(hass, "off", 70, action="off")
    await _load(hass)

    _grill(hass, "heat", 70, action="preheating")
    await _later(hass, freezer, minutes=20)
    hass.states.async_set(GRILL, "unavailable")
    await _later(hass, freezer, seconds=30)
    _grill(hass, "heat", 71, action="preheating")
    await _later(hass, freezer, minutes=29)
    assert calls["notify"] == [], "counted again from the failed poll"
    await _later(hass, freezer, minutes=1, seconds=1)

    assert len(calls["notify"]) == 1


# The finish-time snippet


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
