"""Constants for the Green Mountain Grill integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "gmg"

CONF_SERIAL_NUMBER = "serial_number"
CONF_IP = "ip"

DEFAULT_SCAN_INTERVAL = 30  # seconds

# Keys in the dict returned by grill.status().
ATTR_ON = "on"
ATTR_GRILL_TEMP = "temp"
ATTR_GRILL_SET_TEMP = "grill_set_temp"
ATTR_PROBE1_TEMP = "probe1_temp"
ATTR_PROBE1_SET_TEMP = "probe1_set_temp"
ATTR_PROBE2_TEMP = "probe2_temp"
ATTR_PROBE2_SET_TEMP = "probe2_set_temp"
ATTR_FIRE_STATE = "fireState"
ATTR_FIRE_STATE_PCT = "fireStatePercentage"
ATTR_WARN_STATE = "warnState"
ATTR_WARNINGS = "warnings"  # warnState decoded by decode_warnings
ATTR_CONFIG = "config"  # gmg.GrillConfig, or None -- see STATUS_PACKET_BYTES

# Power state values (raw byte 30). Cross-checked against
# github.com/brandenco/green-mountain-grill's own reverse-engineering and
# its test fixtures -- ON=1 and OFF=0 match this project's own prior
# testing; COLD_SMOKE=3 is independently confirmed against a real captured
# payload (its "power on cold smoke" test case). FAN=2 is confirmed by five
# real cooks (6-16 Sep 2026): every power-off went to 2 for about 16 minutes
# before 0 -- the "fan mode" GMG's manual says powering down starts.
POWER_STATE_OFF = 0
POWER_STATE_ON = 1
POWER_STATE_FAN = 2  # confirmed: the fan cooldown after power-off
POWER_STATE_COLD_SMOKE = 3  # confirmed: matches this project's own UK002! command

# Fire state values (raw byte 32). Same source as above. OFF (1) and
# COLD_SMOKE (198) are confirmed against real captured payloads, and STARTUP,
# RUNNING and COOLDOWN by five real cooks (6-16 Sep 2026): off -> startup
# (ambient to ~150F) -> running, with startup again whenever the grill fell
# back below temperature -> cooldown after power-off -> off. "default" and
# "fail" are carried over from the other project's enum, never seen here.
FIRE_STATE_OFF = 1
FIRE_STATE_STARTUP = 2
FIRE_STATE_RUNNING = 3
FIRE_STATE_COOLDOWN = 4
FIRE_STATE_NAMES = {
    0: "default",
    FIRE_STATE_OFF: "off",  # confirmed
    FIRE_STATE_STARTUP: "startup",  # confirmed
    FIRE_STATE_RUNNING: "running",  # confirmed
    FIRE_STATE_COOLDOWN: "cooldown",  # confirmed
    5: "fail",
    198: "cold_smoke",  # confirmed
}

# The status reply carries no auger, fan or igniter bits: across those five
# cooks, every byte outside the decoded fields stayed constant. These two are
# what the reply does say about the fire.


def is_fire_active(fire_state: int | None) -> bool | None:
    """Whether pellets are burning: the fire is starting up or running."""
    if fire_state is None:
        return None
    return fire_state in (FIRE_STATE_STARTUP, FIRE_STATE_RUNNING)


def is_cooldown(power_state: int | None, fire_state: int | None) -> bool | None:
    """Whether the grill is in its post-shutdown fan cooldown."""
    if power_state is None and fire_state is None:
        return None
    return power_state == POWER_STATE_FAN or fire_state == FIRE_STATE_COOLDOWN

# warnState (status bytes 24-27), read as one flag per bit.
#
# The only real non-zero reading anyone has recorded is low pellets = 128
# (facultymatt/gmg-js's 2020 capture). The other seven names take the order of
# brandenco/green-mountain-grill's WarnCode enum, one bit each, with low pellets
# eighth -- which is how it lands on 128. That part is inference: the
# Aenima4six2 emulator reads the same eight names as ONE code in steps of 16
# (16, 32, 48 ... 128), and the two readings agree only on 128. Flags were
# chosen so that two warnings at once cannot read as neither; treat every name
# but low_pellet as unconfirmed until a real grill shows one.
WARN_FLAGS = {
    1 << 0: "fan_overload",
    1 << 1: "auger_overload",
    1 << 2: "ignitor_overload",
    1 << 3: "low_battery",
    1 << 4: "fan_disconnect",
    1 << 5: "auger_disconnect",
    1 << 6: "ignitor_disconnect",
    1 << 7: "low_pellet",
}


def decode_warnings(code: int) -> list[str]:
    """Every warning set in a warnState value, lowest bit first.

    A set bit without a name is reported as unknown_bit_N rather than dropped.
    """
    return [
        WARN_FLAGS.get(1 << bit, f"unknown_bit_{bit}")
        for bit in range(code.bit_length())
        if code >> bit & 1
    ]


MIN_TEMP_F = 150
MAX_TEMP_F = 500
MIN_TEMP_F_PROBE = 32
MAX_TEMP_F_PROBE = 257

MAX_STATUS_RETRIES = 5

# The finish-time estimate (analytics.ProbeTrend) fits a straight line through
# the last ETA_WINDOW of a probe's readings. It needs ETA_MIN_SAMPLES readings
# spanning ETA_MIN_SPAN. A rise slower than ETA_MIN_RATE is a stall, and a
# finish further off than ETA_MAX_AHEAD is not worth showing.
ETA_WINDOW = timedelta(minutes=20)
ETA_MIN_SPAN = timedelta(minutes=5)
ETA_MIN_SAMPLES = 5
ETA_MIN_RATE = 2.0  # F per hour -- the same line the stall automation draws
ETA_MAX_AHEAD = timedelta(hours=24)

# Grill Config writes (gmg.Grill.write_config_field). The block layout is
# confirmed on API version 6 only (status byte 8); any other grill is left
# alone rather than risk scrambling its probe calibration.
CONFIG_WRITE_API_VERSIONS = frozenset({6})
# Before a write, status polls to find two whole replies whose blocks agree.
CONFIG_READ_ATTEMPTS = 10
# After a write the block is read back CONFIG_CONFIRM_POLLS times: first after
# CONFIG_CONFIRM_FIRST_DELAY, then every CONFIG_CONFIRM_INTERVAL -- about 30 s
# in all. A grill that answers takes a few seconds; one that has stopped
# answering costs its 1 s timeouts on top, so a failed write can take about a
# minute to report.
CONFIG_CONFIRM_POLLS = 15
CONFIG_CONFIRM_FIRST_DELAY = 0.5  # seconds
CONFIG_CONFIRM_INTERVAL = 2  # seconds
# Between whole replies the Grill Config entities show the last whole block,
# for at most this long; after that they go unavailable rather than show a
# setting nobody has seen recently. (Status fields never age: a poll that
# fails takes every entity unavailable straight away.)
CONFIG_MAX_AGE = timedelta(seconds=90)

# The shortest status payload _parse_status can read. It indexes values[33]
# (fireStatePercentage), so anything under 34 bytes cannot be parsed at all.
#
# This is not hypothetical: a real grill on a real network returns short
# payloads intermittently -- 18, 22, 28, 29 and 31 bytes were all observed on
# one install over 25 hours, 112 times. The datagram is atomic (UDP), the
# receive buffer is 1024, and the parser is correct; the grill really does
# send them. Treat a short response the same way as no response and retry.
MIN_STATUS_BYTES = 34

# Every status reply starts with these two bytes. A reply that does not has
# lost its head in transit, and every field after it sits at the wrong offset.
#
# Also not hypothetical: on the same install, 54 of 1,768 polls in 48 hours
# (Sep 2026) came back with their first 1-18 bytes missing. Read at the usual
# offsets they reported 601F, 2822F and 38402F, fire states like 85, and a
# grill that was "on" while it sat cold: 12 of the 21 off-to-on changes Home
# Assistant recorded in 10 days were one bad reply, not a cook. A reply that
# lost its tail instead keeps every field where it belongs, so the prefix is
# the check that matters, not the length.
STATUS_PREFIX = b"UR"

# A whole status reply, as a Jim Bowie on firmware 2.3 ("NJB APIv6") sends it.
# Bytes 41-51 carry the model string ("JB02SUF02.3"). Pieces of a reply and two
# replies run together both arrive as well, and a misaligned byte 9 can read as
# Pizza Mode ON -- so the Grill Config block is only ever read from a reply of
# exactly this length. Older grills send 36 bytes; they get status, not config.
STATUS_PACKET_BYTES = 52

# Climate Setting (status byte 9, bits 4-2), in the GMG app's slider order.
# All five were read back from a real grill on 16 Sep 2026.
CLIMATE_SETTINGS = ("Icy", "Cold", "Average", "Warm", "Hot")

# A probe jack with nothing plugged in reports a combined value of 601 --
# confirmed against two independent real captured payloads (both probes,
# both power states, both showing exactly 601; see
# github.com/brandenco/green-mountain-grill's test fixtures, hand-verified
# against this project's own combining math in gmg.py). 601 is outside the
# probe's real physical range (32-257F), so "outside the physical range"
# is what's actually checked below, rather than hardcoding 601 as a magic
# number -- and the sentinel is NOT always exactly 601: the grill applies the
# app's probe calibration to it too, as a straight line through the 32F and
# 212F boxes extended out to 601. Seen on 16 Sep 2026: 584 / 593 and 608 / 628
# for the empty jacks. Even +/-25 in opposite boxes only moves it to roughly
# 468-734, far outside the range below, so the range check holds either way
# (anything implausible reads as disconnected).


def is_probe_connected(value: int | None) -> bool | None:
    """Whether a probe temperature reading indicates a probe is actually plugged in."""
    if value is None:
        return None
    return MIN_TEMP_F_PROBE <= value <= MAX_TEMP_F_PROBE
