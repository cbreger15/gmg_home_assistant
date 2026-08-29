"""Verify Grill._parse_status against real captured payloads.

These two byte sequences are the exact test fixtures from
github.com/brandenco/green-mountain-grill (MIT licensed), an independent
reverse-engineering of this same protocol. Reusing them here means this
isn't "trust the math" -- it's "this implementation produces the same
answer as an independently-authored one, against the same real captured
data, and both agree with each other."

Pure-Python, no Home Assistant test harness required -- run directly:
    python -m pytest tests/test_gmg_parsing.py -v
or without pytest installed:
    python tests/test_gmg_parsing.py
"""

import importlib.util
import sys
import types
from pathlib import Path

# Load gmg.py and const.py directly by file path rather than via the real
# `custom_components.gmg` package -- that package's __init__.py imports
# homeassistant, which this test deliberately does not require. gmg.py's
# actual protocol logic (what's under test here) has no such dependency.
# gmg.py does `from .const import ...` (a relative import), which needs a
# real parent package in sys.modules to resolve -- so a minimal namespace
# package is constructed by hand instead of executing the real __init__.py.
_GMG_DIR = Path(__file__).parent.parent / "custom_components" / "gmg"

sys.modules.setdefault("custom_components", types.ModuleType("custom_components"))
_pkg = types.ModuleType("custom_components.gmg")
_pkg.__path__ = [str(_GMG_DIR)]
sys.modules["custom_components.gmg"] = _pkg


def _load(fullname: str, filename: str):
    spec = importlib.util.spec_from_file_location(fullname, _GMG_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[fullname] = module
    spec.loader.exec_module(module)
    return module


_load("custom_components.gmg.const", "const.py")
_gmg = _load("custom_components.gmg.gmg", "gmg.py")

Grill = _gmg.Grill
GmgCommunicationError = _gmg.GmgCommunicationError

POWER_OFF = bytes(
    [
        0x55, 0x52, 0x66, 0x0, 0x59, 0x2, 0x96, 0x0, 0x5, 0xB, 0x14, 0x32, 0x19, 0x19, 0x19, 0x19,
        0x59, 0x2, 0x0, 0x0, 0xFF, 0xFF, 0xFF, 0xFF, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x1,
        0x0, 0x0, 0x3,
    ]
)

POWER_ON_COLD_SMOKE = bytes(
    [
        0x55, 0x52, 0x66, 0x0, 0x59, 0x2, 0x1E, 0x0, 0x5, 0xB, 0x14, 0x32, 0x19, 0x19, 0x19, 0x19,
        0x59, 0x2, 0xFA, 0x0, 0xFF, 0xFF, 0xFF, 0xFF, 0x0, 0x0, 0x0, 0x0, 0x96, 0x0, 0x3, 0x0,
        0xC6, 0x0, 0x0, 0x3,
    ]
)


def test_power_off():
    state = Grill._parse_status(POWER_OFF)
    assert state["temp"] == 102
    assert state["grill_set_temp"] == 150
    assert state["probe1_temp"] == 601  # disconnected sentinel
    assert state["probe1_set_temp"] == 0
    assert state["probe2_temp"] == 601  # disconnected sentinel
    assert state["probe2_set_temp"] == 0
    assert state["on"] == 0  # PowerStateOff
    assert state["fireState"] == 1  # FireStateOff
    assert state["warnState"] == 0


def test_power_on_cold_smoke():
    state = Grill._parse_status(POWER_ON_COLD_SMOKE)
    assert state["temp"] == 102
    assert state["grill_set_temp"] == 30
    assert state["probe1_temp"] == 601  # disconnected sentinel
    assert state["probe1_set_temp"] == 150
    assert state["probe2_temp"] == 601  # disconnected sentinel
    assert state["probe2_set_temp"] == 250
    assert state["on"] == 3  # PowerStateColdSmoke -- NOT 2, the original's assumption
    assert state["fireState"] == 198  # FireStateColdSmoke
    assert state["warnState"] == 0


def test_short_response_raises_instead_of_hanging_or_crashing():
    try:
        Grill._parse_status(b"UR")
        raised = False
    except GmgCommunicationError:
        raised = True
    assert raised, "a too-short response must raise, not crash on a raw IndexError or return partial state"


if __name__ == "__main__":
    test_power_off()
    print("test_power_off: PASS")
    test_power_on_cold_smoke()
    print("test_power_on_cold_smoke: PASS")
    test_short_response_raises_instead_of_hanging_or_crashing()
    print("test_short_response_raises_instead_of_hanging_or_crashing: PASS")
    print("\nAll assertions passed against real captured payloads.")
