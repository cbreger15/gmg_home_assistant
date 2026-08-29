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


_const = _load("custom_components.gmg.const", "const.py")
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


def test_combine_temp_matches_hand_computation():
    # Spot checks independent of the fixtures above -- e.g. a value that
    # genuinely needs the high byte (can't fit in one byte alone), proving
    # the combination isn't a no-op that happens to pass on values under 256.
    assert Grill._combine_temp(low=94, high=1) == 350  # (1 << 8) + 94
    assert Grill._combine_temp(low=0, high=0) == 0
    assert Grill._combine_temp(low=255, high=0) == 255
    assert Grill._combine_temp(low=0, high=1) == 256  # exactly where a single byte overflows


def test_set_temp_command_bytes():
    g = Grill("10.0.0.1", "TEST")
    calls = []
    g.send = lambda msg, timeout=1: calls.append(msg) or b""
    g.set_temp(350)
    assert calls == [b"UT350!"]


def test_set_temp_probe_command_bytes():
    g = Grill("10.0.0.1", "TEST")
    calls = []
    g.send = lambda msg, timeout=1: calls.append(msg) or b""
    g.set_temp_probe(165, probe_number=1)
    g.set_temp_probe(165, probe_number=2)
    assert calls == [b"UF165!", b"Uf165!"]


def test_power_commands_match_reference_project():
    g = Grill("10.0.0.1", "TEST")
    calls = []
    g.send = lambda msg, timeout=1: calls.append(msg) or b""
    g.power_on()
    g.power_on_cool()
    g.power_off()
    assert calls == [b"UK001!", b"UK002!", b"UK004!"]


def test_set_temp_rejects_out_of_range():
    g = Grill("10.0.0.1", "TEST")
    for bad in (100, 501):
        try:
            g.set_temp(bad)
            raised = False
        except ValueError:
            raised = True
        assert raised, f"set_temp({bad}) should reject a value outside 150-500"


def test_firmware_command_and_parsing():
    g = Grill("10.0.0.1", "TEST")
    # Exact response format is unconfirmed (see gmg.py's firmware() docstring) --
    # this only tests what firmware() actually does: decode and strip
    # whitespace, not a specific real value.
    g.send = lambda msg, timeout=1: b"  1.2.3  " if msg == b"UN!" else None
    assert g.firmware() == "1.2.3"

    g.send = lambda msg, timeout=1: None
    assert g.firmware() is None, "a failed firmware fetch must return None, not raise"


def test_is_probe_connected_boundaries():
    is_probe_connected = _const.is_probe_connected
    assert is_probe_connected(None) is None
    assert is_probe_connected(32) is True  # MIN_TEMP_F_PROBE
    assert is_probe_connected(257) is True  # MAX_TEMP_F_PROBE
    assert is_probe_connected(31) is False
    assert is_probe_connected(258) is False
    assert is_probe_connected(601) is False  # the real disconnected sentinel


if __name__ == "__main__":
    tests = [
        test_power_off,
        test_power_on_cold_smoke,
        test_short_response_raises_instead_of_hanging_or_crashing,
        test_combine_temp_matches_hand_computation,
        test_set_temp_command_bytes,
        test_set_temp_probe_command_bytes,
        test_power_commands_match_reference_project,
        test_set_temp_rejects_out_of_range,
        test_firmware_command_and_parsing,
        test_is_probe_connected_boundaries,
    ]
    for t in tests:
        t()
        print(f"{t.__name__}: PASS")
    print(f"\nAll {len(tests)} tests passed.")
