"""Constants for the Green Mountain Grill integration."""

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

# Raw value the grill reports for a probe with nothing plugged in. Not a
# documented protocol flag -- the controller has no separate "connected" bit,
# this sentinel is the only signal available (see gmg.py for detail).
PROBE_DISCONNECTED_TEMP_F = 89

MIN_TEMP_F = 150
MAX_TEMP_F = 500
MIN_TEMP_F_PROBE = 32
MAX_TEMP_F_PROBE = 257

MAX_STATUS_RETRIES = 5
