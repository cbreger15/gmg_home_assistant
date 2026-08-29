"""Constants for the Green Mountain Grill integration."""

from __future__ import annotations

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

# Power state values (raw byte 30). Cross-checked against
# github.com/brandenco/green-mountain-grill's own reverse-engineering and
# its test fixtures -- ON=1 and OFF=0 match this project's own prior
# testing; COLD_SMOKE=3 is independently confirmed against a real captured
# payload (its "power on cold smoke" test case). FAN=2 is in the other
# project's enum but neither project has a confirmed example of it -- do
# not assume it means the same thing power_on_cool() actually produces.
POWER_STATE_OFF = 0
POWER_STATE_ON = 1
POWER_STATE_FAN = 2  # unconfirmed by any known real payload
POWER_STATE_COLD_SMOKE = 3  # confirmed: matches this project's own UK002! command

# Fire state values (raw byte 32). Same source as above. Only OFF (1) and
# COLD_SMOKE (198) are confirmed against real captured payloads; the rest
# are carried over from the other project's enum, unconfirmed here.
FIRE_STATE_NAMES = {
    0: "default",
    1: "off",  # confirmed
    2: "startup",
    3: "running",
    4: "cooldown",
    5: "fail",
    198: "cold_smoke",  # confirmed
}

MIN_TEMP_F = 150
MAX_TEMP_F = 500
MIN_TEMP_F_PROBE = 32
MAX_TEMP_F_PROBE = 257

MAX_STATUS_RETRIES = 5

# A probe jack with nothing plugged in reports a combined value of 601 --
# confirmed against two independent real captured payloads (both probes,
# both power states, both showing exactly 601; see
# github.com/brandenco/green-mountain-grill's test fixtures, hand-verified
# against this project's own combining math in gmg.py). 601 is outside the
# probe's real physical range (32-257F), so "outside the physical range"
# is what's actually checked below, rather than hardcoding 601 as a magic
# number -- there's no evidence the sentinel is always exactly 601 versus
# some other always-out-of-range value, and a range check degrades safely
# either way (anything implausible reads as disconnected).


def is_probe_connected(value: int | None) -> bool | None:
    """Whether a probe temperature reading indicates a probe is actually plugged in."""
    if value is None:
        return None
    return MIN_TEMP_F_PROBE <= value <= MAX_TEMP_F_PROBE
