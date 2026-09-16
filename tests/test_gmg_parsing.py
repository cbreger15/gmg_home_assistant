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

try:
    # Home Assistant is installed and the repo root is importable (the full
    # test run, alongside the Home Assistant tests): use the real package.
    # Replacing it with the stand-in below would break those tests, which
    # need the real __init__.py.
    from custom_components.gmg import const as _const, gmg as _gmg
except ImportError:
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


# Real replies from a Jim Bowie (firmware 2.3, APIv6), taken from Home
# Assistant's history of the raw status sensor, 13 Sep 2026. A whole reply is
# 52 bytes; the grill also sends pieces of one.
#
# HEAD_CUT lost its first two bytes ("UR") on the way. Read at the usual
# offsets it says the grill is ON at 601F with a 2822F setpoint -- this exact
# reply put a phantom cook into Home Assistant at 21:48 UTC that day.
HEAD_CUT = bytes.fromhex(
    "4c0059029600060b14321919191959020000ffffffff00000000000000000100000300000000"
    "004a42303253554630322e33"
)
# TAIL_CUT kept its head and lost its last 16 bytes -- every status field is
# still at its proper offset.
TAIL_CUT = bytes.fromhex(
    "5552460059029600060b14321919191959020000ffffffff000000000000000001000003"
)
# MERGED is two whole replies run together.
MERGED = bytes.fromhex(
    "5552430059029600060b14321919191959020000ffffffff00000000000000000100000300000000"
    "004a42303253554630322e33"
    "5552430059029600060b14321919191959020000ffffffff00000000000000000100000300000000"
    "004a42303253554630322e33"
)


# facultymatt/gmg-js's capture with the low-pellet alarm on (its decode.txt,
# 2020, another grill on API version 1): bytes 24-27 read 80 00 00 00. The
# only real non-zero warning anyone has recorded.
LOW_PELLET_2020 = bytes.fromhex(
    "555249002d029600010114321919000000000000ffffffff800000000000000001000000"
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


def test_status_retries_a_short_response_then_succeeds():
    """The regression this fork exists to fix: a short payload must be
    retried, not raised on. Two short reads then a good one is a success."""
    g = Grill("10.0.0.1", "TEST")
    replies = [b"UR", b"URfoo", POWER_ON_COLD_SMOKE]
    calls = []

    def fake_send(message, timeout=1):
        calls.append(message)
        return replies[len(calls) - 1]

    g.send = fake_send
    state = g.status()
    assert len(calls) == 3, "both short replies should have been retried"
    assert state["on"] == 3
    assert state["fireState"] == 198


def test_status_gives_up_after_all_short_and_says_so():
    """It must still fail eventually, and the message must name the length --
    a grill returning a CONSISTENT short payload is a protocol difference,
    not a blip, and the error is the only place that would show it."""
    g = Grill("10.0.0.1", "TEST")
    calls = []

    def fake_send(message, timeout=1):
        calls.append(message)
        return b"UR" + b"x" * 20          # 22 bytes -- a real observed length

    g.send = fake_send
    try:
        g.status()
        raised = None
    except GmgCommunicationError as err:
        raised = str(err)
    assert raised is not None, "all-short must still raise"
    assert "22 bytes" in raised, f"the error should name the length, got: {raised}"
    assert len(calls) == _const.MAX_STATUS_RETRIES, (
        f"should have spent every retry, spent {len(calls)}"
    )


def test_status_retries_a_reply_that_lost_its_first_bytes():
    """The phantom-cook regression: a reply that does not start with UR is
    read at the wrong offsets, so it must be retried like a short one --
    never parsed into 'on at 601F'."""
    g = Grill("10.0.0.1", "TEST")
    replies = [HEAD_CUT, POWER_OFF]
    calls = []

    def fake_send(message, timeout=1):
        calls.append(message)
        return replies[len(calls) - 1]

    g.send = fake_send
    state = g.status()
    assert len(calls) == 2, "the headless reply should have been retried"
    assert state["on"] == 0
    assert state["temp"] == 102


def test_status_gives_up_after_all_headless_and_says_so():
    g = Grill("10.0.0.1", "TEST")
    calls = []

    def fake_send(message, timeout=1):
        calls.append(message)
        return HEAD_CUT

    g.send = fake_send
    try:
        g.status()
        raised = None
    except GmgCommunicationError as err:
        raised = str(err)
    assert raised is not None, "all-headless must raise, not parse"
    assert "50 bytes" in raised, f"the error should name the length, got: {raised}"
    assert len(calls) == _const.MAX_STATUS_RETRIES


def test_parse_status_refuses_a_reply_that_does_not_start_with_ur():
    try:
        Grill._parse_status(HEAD_CUT)
        raised = False
    except GmgCommunicationError:
        raised = True
    assert raised, "a reply without the UR prefix must never be parsed"


def test_status_still_reads_tail_cut_and_merged_replies():
    """Only the head matters for the status fields: a reply that kept its
    UR prefix has every field at its proper offset, however it ends."""
    for reply in (TAIL_CUT, MERGED):
        g = Grill("10.0.0.1", "TEST")
        calls = []
        g.send = lambda msg, timeout=1, reply=reply: calls.append(msg) or reply
        state = g.status()
        assert len(calls) == 1, f"a {len(reply)}-byte UR reply should be read, not retried"
        assert state["on"] == 0
        assert state["fireState"] == 1
        assert state["grill_set_temp"] == 150
        assert state["probe1_temp"] == 601


def test_low_pellet_warning_from_a_real_capture():
    state = Grill._parse_status(LOW_PELLET_2020)
    assert state["warnState"] == 128
    assert state["warnings"] == ["low_pellet"]
    assert Grill._parse_status(POWER_OFF)["warnings"] == []


def test_warnings_are_flags_so_simultaneous_ones_all_show():
    decode = _const.decode_warnings
    assert decode(0) == []
    assert decode(128 | 1) == ["fan_overload", "low_pellet"]
    assert decode(2 | 4 | 8) == ["auger_overload", "ignitor_overload", "low_battery"]
    assert decode(16 | 32 | 64) == ["fan_disconnect", "auger_disconnect", "ignitor_disconnect"]


def test_warning_bits_without_a_name_are_still_reported():
    assert _const.decode_warnings((1 << 8) | (1 << 31) | 128) == [
        "low_pellet",
        "unknown_bit_8",
        "unknown_bit_31",
    ]


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


def test_probe_targets_under_100_are_zero_padded():
    """Every other implementation sends UF%03d!, and the Aenima4six2 emulator
    only recognises UF(\\d{3})! -- so UF32! may never register at all."""
    g = Grill("10.0.0.1", "TEST")
    calls = []
    g.send = lambda msg, timeout=1: calls.append(msg) or b""
    g.set_temp_probe(32, probe_number=1)
    g.set_temp_probe(99, probe_number=2)
    assert calls == [b"UF032!", b"Uf099!"]


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
        test_probe_targets_under_100_are_zero_padded,
        test_power_commands_match_reference_project,
        test_set_temp_rejects_out_of_range,
        test_firmware_command_and_parsing,
        test_is_probe_connected_boundaries,
        test_status_retries_a_short_response_then_succeeds,
        test_status_gives_up_after_all_short_and_says_so,
        test_status_retries_a_reply_that_lost_its_first_bytes,
        test_status_gives_up_after_all_headless_and_says_so,
        test_parse_status_refuses_a_reply_that_does_not_start_with_ur,
        test_status_still_reads_tail_cut_and_merged_replies,
        test_low_pellet_warning_from_a_real_capture,
        test_warnings_are_flags_so_simultaneous_ones_all_show,
        test_warning_bits_without_a_name_are_still_reported,
    ]
    for t in tests:
        t()
        print(f"{t.__name__}: PASS")
    print(f"\nAll {len(tests)} tests passed.")
