"""What a cook looks like in Home Assistant: fire, cooldown, climate, finish time.

The stages are real replies from a 15-16 Sep 2026 cook (tests/grill_wire.py).
Needs pytest-homeassistant-custom-component (requirements.test.txt).
"""

from datetime import datetime, timedelta

import pytest

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.gmg import gmg
from custom_components.gmg.const import MAX_STATUS_RETRIES
from tests.grill_wire import (
    COLD_SMOKE_36,
    COOK_COOLDOWN,
    COOK_OFF,
    COOK_RUNNING,
    COOK_STARTUP,
    NoNetwork,
    WireGrill,
    with_probe1,
)
from tests.ha_setup import PREFIX, call, poll, set_up

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

FIRE_ACTIVE = f"binary_sensor.{PREFIX}_fire_active"
COOLDOWN_FAN = f"binary_sensor.{PREFIX}_cooldown_fan"
GRILL = f"climate.{PREFIX}"
FINISH = f"sensor.{PREFIX}_probe_1_estimated_finish_time"


def _with_byte(packet: bytes, index: int, value: int) -> bytes:
    changed = bytearray(packet)
    changed[index] = value
    return bytes(changed)


@pytest.fixture(autouse=True)
def no_real_grill(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any send that gets past WireGrill fails the test instead of leaving the machine."""
    monkeypatch.setattr(gmg, "socket", NoNetwork())


@pytest.mark.parametrize(
    ("packet", "fire_active", "cooldown_fan", "mode", "action"),
    [
        (COOK_OFF, STATE_OFF, STATE_OFF, "off", "off"),
        (COOK_STARTUP, STATE_ON, STATE_OFF, "heat", "preheating"),
        (COOK_RUNNING, STATE_ON, STATE_OFF, "heat", "heating"),
        (COOK_COOLDOWN, STATE_OFF, STATE_ON, "off", "fan"),
        (COLD_SMOKE_36, STATE_OFF, STATE_OFF, "fan_only", "fan"),
        # Never seen on a real grill, but possible on the wire:
        (_with_byte(COOK_STARTUP, 32, 1), STATE_OFF, STATE_OFF, "heat", "idle"),  # on, no fire
        (_with_byte(COOK_OFF, 30, 7), STATE_OFF, STATE_OFF, "off", None),  # power state unknown
    ],
    ids=["off", "startup", "running", "cooldown", "cold_smoke", "on_without_fire", "unknown_power"],
)
async def test_each_stage_of_a_cook(
    hass: HomeAssistant, packet: bytes, fire_active: str, cooldown_fan: str, mode: str, action: str
) -> None:
    await set_up(hass, WireGrill(packet))

    assert hass.states.get(FIRE_ACTIVE).state == fire_active
    assert hass.states.get(COOLDOWN_FAN).state == cooldown_fan
    grill = hass.states.get(GRILL)
    assert grill.state == mode
    assert grill.attributes.get("hvac_action") == action


async def test_fire_and_cooldown_stay_off_the_homekit_bridge(hass: HomeAssistant) -> None:
    """The bridge publishes a binary sensor it has no type for as an occupancy
    sensor; a diagnostic entity is never published."""
    await set_up(hass, WireGrill(COOK_OFF))
    registry = er.async_get(hass)

    assert registry.async_get(FIRE_ACTIVE).entity_category == EntityCategory.DIAGNOSTIC
    assert registry.async_get(COOLDOWN_FAN).entity_category == EntityCategory.DIAGNOSTIC


async def test_turning_the_grill_off_sends_the_power_off_command(hass: HomeAssistant) -> None:
    # UK004! is the grill's own power-off: it starts the fan cooldown (COOK_COOLDOWN).
    wire = WireGrill(COOK_RUNNING)
    await set_up(hass, wire)

    await call(hass, "climate", "turn_off", GRILL)

    assert b"UK004!" in wire.sent()


def _finish(hass: HomeAssistant) -> datetime:
    return datetime.fromisoformat(hass.states.get(FINISH).state)


async def test_the_finish_time_follows_probe_1(hass: HomeAssistant, freezer) -> None:
    freezer.move_to("2026-09-16 18:00:00+00:00")
    wire = WireGrill(with_probe1(COOK_RUNNING, 150, 165))
    entry = await set_up(hass, wire)
    assert hass.states.get(FINISH).state == STATE_UNKNOWN  # one reading is no trend

    async def reading(temperature: int, target: int = 165, packet: bytes = COOK_RUNNING) -> None:
        freezer.tick(timedelta(seconds=30))
        wire.packet = bytearray(with_probe1(packet, temperature, target))
        await poll(hass, entry)

    # 1F a minute, in whole degrees, for 10 minutes: the line reaches 160F at
    # 18:10, so 165F is due at 18:15.
    for i in range(1, 21):
        await reading(150 + i // 2)
    assert abs(_finish(hass) - datetime.fromisoformat("2026-09-16 18:15:00+00:00")) <= timedelta(minutes=1)
    assert hass.states.get(FINISH).attributes["rate_f_per_hour"] == pytest.approx(60, abs=3)

    # A stall: flat for 20 minutes, until the rise has left the window...
    for _ in range(40):
        await reading(160)
    assert hass.states.get(FINISH).state == STATE_UNKNOWN
    # ...then creeping up a degree. One step is no trend, wherever it sits
    # in the window.
    for i in range(41):
        await reading(161)
        assert hass.states.get(FINISH).state == STATE_UNKNOWN, f"{i + 1} polls after the step"

    # Nothing to estimate without a probe, a target, or a fire.
    await reading(601)  # the jack reads 601 with nothing plugged in
    assert hass.states.get(FINISH).state == STATE_UNAVAILABLE
    await reading(161, target=0)
    assert hass.states.get(FINISH).state == STATE_UNAVAILABLE
    await reading(161, packet=COOK_COOLDOWN)
    assert hass.states.get(FINISH).state == STATE_UNAVAILABLE


async def test_the_finish_time_works_in_cold_smoke(hass: HomeAssistant, freezer) -> None:
    freezer.move_to("2026-09-16 18:00:00+00:00")
    wire = WireGrill(with_probe1(COLD_SMOKE_36, 40, 50))
    entry = await set_up(hass, wire)

    for i in range(1, 21):
        freezer.tick(timedelta(seconds=30))
        wire.packet = bytearray(with_probe1(COLD_SMOKE_36, 40 + i // 4, 50))  # 30F an hour
        await poll(hass, entry)

    # 45F now, the line a little behind: 50F is due in a little over 10 minutes.
    finish = _finish(hass) - datetime.fromisoformat("2026-09-16 18:10:00+00:00")
    assert timedelta(minutes=10) <= finish <= timedelta(minutes=13)


async def test_an_unplugged_probe_starts_the_trend_again(hass: HomeAssistant, freezer) -> None:
    """Readings from before the probe came out say nothing about the piece it
    goes back into -- even when the two would line up into a steady rise."""
    freezer.move_to("2026-09-16 18:00:00+00:00")
    wire = WireGrill(with_probe1(COOK_RUNNING, 150, 200))
    entry = await set_up(hass, wire)

    async def reading(temperature: int) -> None:
        freezer.tick(timedelta(seconds=30))
        wire.packet = bytearray(with_probe1(COOK_RUNNING, temperature, 200))
        await poll(hass, entry)

    for i in range(1, 21):
        await reading(150 + i)
    assert hass.states.get(FINISH).state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)

    await reading(601)
    for temperature in (172, 173, 174):  # back in, in a hotter piece
        await reading(temperature)
    assert hass.states.get(FINISH).state == STATE_UNKNOWN  # three readings are no trend


async def test_a_failed_poll_adds_no_reading(hass: HomeAssistant, freezer) -> None:
    """A failed poll still wakes the entities, holding the last good reading.
    That reading is a poll old by then, and must not join the trend."""
    freezer.move_to("2026-09-16 18:00:00+00:00")
    wire = WireGrill(with_probe1(COOK_RUNNING, 150, 200))
    entry = await set_up(hass, wire)

    async def reading(temperature: int | None) -> None:
        freezer.tick(timedelta(seconds=30))
        if temperature is None:
            wire.script = [None] * MAX_STATUS_RETRIES  # every reply lost
        else:
            wire.packet = bytearray(with_probe1(COOK_RUNNING, temperature, 200))
        await poll(hass, entry)

    for i in range(1, 21):
        await reading(150 + i)  # 1F every 30 s: exactly 120F an hour
    await reading(None)
    assert hass.states.get(FINISH).state == STATE_UNAVAILABLE
    await reading(172)  # back, and still on the same line

    assert hass.states.get(FINISH).attributes["rate_f_per_hour"] == 120.0
