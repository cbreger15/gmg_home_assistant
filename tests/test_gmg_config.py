"""Verify the Grill Config block (status bytes 8-15): reading it, and writing it.

Every packet here is a real reply from a Jim Bowie (firmware 2.3, "NJB APIv6")
-- see grill_wire.py. The byte 9 values were produced by changing one setting
at a time in the GMG app and reading the packet back, so each expectation is
what the app showed, not what this code computes.

Pure-Python, no Home Assistant test harness required -- run directly:
    python -m pytest tests/test_gmg_config.py -v
or without pytest installed:
    python tests/test_gmg_config.py
"""

import importlib.util
from pathlib import Path
import sys
import threading
import types

try:
    # Home Assistant is installed and the repo root is importable (the full
    # test run): use the real package, so these tests and the Home Assistant
    # ones in the same session share one copy of it.
    from custom_components.gmg import const as _const, gmg as _gmg
except ImportError:
    # Otherwise load gmg.py and const.py by file path, under a hand-built
    # parent package, exactly as test_gmg_parsing.py explains.
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

try:
    from tests.grill_wire import LIVE, LIVE_REPLY, MERGED_104, STATUS, TAIL_CUT_51, WireGrill
except ImportError:  # run directly: tests/ itself is on sys.path
    from grill_wire import LIVE, LIVE_REPLY, MERGED_104, STATUS, TAIL_CUT_51, WireGrill

Grill = _gmg.Grill
GmgCommunicationError = _gmg.GmgCommunicationError
GmgConfigWriteError = _gmg.GmgConfigWriteError
GrillConfig = _gmg.GrillConfig
PIZZA_MODE = _gmg.PIZZA_MODE
CLIMATE = _gmg.CLIMATE
AUTO_REVERT_WIFI = _gmg.AUTO_REVERT_WIFI
LOCK_TEMP_DISPLAY = _gmg.LOCK_TEMP_DISPLAY

# brandenco/green-mountain-grill's captured reply (another grill, 36 bytes).
POWER_OFF_36 = bytes.fromhex(
    "5552660059029600050b14321919191959020000ffffffff000000000000000001000003"
)


def test_config_block_is_read_from_a_whole_reply():
    config = Grill._parse_status(LIVE["09"])["config"]
    assert config.block == bytes.fromhex("0609143219191919")
    assert str(config) == "06 09 14 32 19 19 19 19"
    assert config.api_version == 6
    assert config.pizza_mode is False
    assert config.climate == 2
    assert config.auto_revert_wifi is False
    assert config.lock_temp_display is True
    # Calibration boxes, raw: not decoded (see gmg.py).
    assert config.grill_adjustment_raw == (20, 50)
    assert config.probe1_adjustment_raw == (25, 25)
    assert config.probe2_adjustment_raw == (25, 25)


# byte 9 -> what the GMG app showed when that packet was read.
OBSERVED_BYTE_9 = {
    "07": dict(pizza_mode=False, climate="Cold", auto_revert_wifi=True, lock_temp_display=True),
    "27": dict(pizza_mode=True, climate="Cold", auto_revert_wifi=True, lock_temp_display=True),
    "0b": dict(pizza_mode=False, climate="Average", auto_revert_wifi=True, lock_temp_display=True),
    "0f": dict(pizza_mode=False, climate="Warm", auto_revert_wifi=True, lock_temp_display=True),
    "13": dict(pizza_mode=False, climate="Hot", auto_revert_wifi=True, lock_temp_display=True),
    "03": dict(pizza_mode=False, climate="Icy", auto_revert_wifi=True, lock_temp_display=True),
    "0a": dict(pizza_mode=False, climate="Average", auto_revert_wifi=True, lock_temp_display=False),
    "09": dict(pizza_mode=False, climate="Average", auto_revert_wifi=False, lock_temp_display=True),
}


def test_every_observed_byte_9_decodes_to_what_the_app_showed():
    for byte9, want in OBSERVED_BYTE_9.items():
        config = Grill._parse_status(LIVE[byte9])["config"]
        got = dict(
            pizza_mode=config.pizza_mode,
            climate=_const.CLIMATE_SETTINGS[config.climate],
            auto_revert_wifi=config.auto_revert_wifi,
            lock_temp_display=config.lock_temp_display,
        )
        assert got == want, f"byte 9 = {byte9}: got {got}, want {want}"


def test_no_config_block_unless_the_reply_is_exactly_one_whole_packet():
    """A cut or merged reply still has readable status fields, but only a
    whole 52-byte packet is trusted for the block -- a misread byte 9 can
    show Pizza Mode ON."""
    for reply in (TAIL_CUT_51, MERGED_104, POWER_OFF_36):
        state = Grill._parse_status(reply)
        assert state["config"] is None, f"{len(reply)}-byte reply must not yield a block"
        assert state["grill_set_temp"] == 150, "its status fields are still read"


def test_a_block_is_exactly_eight_bytes():
    for bad in (bytes(7), bytes(9), b""):
        try:
            GrillConfig(bad)
            raised = False
        except ValueError:
            raised = True
        assert raised, f"a {len(bad)}-byte block must be refused"


# ---------------------------------------------------------------------------
# The write frame: "UC" + the eight bytes + "!", per facultymatt/gmg-js, whose
# notes record whole frames such as 5543050b02322020202021 (Pizza Mode off).
# Every expected frame below is worked out by hand from the live block
# 06 09 14 32 19 19 19 19 (Average, Auto-Revert off, Lock Temp Display on).
# ---------------------------------------------------------------------------


def test_uc_frame_is_the_live_block_with_one_field_changed():
    live = Grill._parse_status(LIVE["09"])["config"]
    cases = [
        (PIZZA_MODE, 1, "55 43 06 29 14 32 19 19 19 19 21"),  # 0x09 | 0x20
        (LOCK_TEMP_DISPLAY, 0, "55 43 06 08 14 32 19 19 19 19 21"),  # 0x09 & ~0x01
        (AUTO_REVERT_WIFI, 1, "55 43 06 0b 14 32 19 19 19 19 21"),  # 0x09 | 0x02
        (CLIMATE, 4, "55 43 06 11 14 32 19 19 19 19 21"),  # Hot: 0x01 | 4 << 2
        (CLIMATE, 1, "55 43 06 05 14 32 19 19 19 19 21"),  # Cold: 0x01 | 1 << 2
        (CLIMATE, 0, "55 43 06 01 14 32 19 19 19 19 21"),  # Icy
    ]
    for field, value, want in cases:
        got = live.with_field(field, value).write_frame()
        assert got == bytes.fromhex(want), f"{field.name}={value}: got {got.hex(' ')}, want {want}"


def test_pizza_mode_on_keeps_a_hot_climate():
    """gmg-js wrote Pizza Mode by overwriting the top nibble of byte 9. From
    Hot (13) that sends 23 -- Pizza Mode on a grill that is now Icy."""
    hot = Grill._parse_status(LIVE["13"])["config"]
    assert hot.with_field(PIZZA_MODE, 1).write_frame() == bytes.fromhex("5543063314321919191921")


def test_with_field_refuses_values_the_app_does_not_offer():
    live = Grill._parse_status(LIVE["09"])["config"]
    # Climate is a 3-bit field, but only 0-4 exist on the app's slider.
    for field, bad in ((PIZZA_MODE, 2), (PIZZA_MODE, -1), (LOCK_TEMP_DISPLAY, 2), (CLIMATE, 5), (CLIMATE, 7)):
        try:
            live.with_field(field, bad)
            raised = False
        except ValueError:
            raised = True
        assert raised, f"{field.name}={bad} must be refused"


# ---------------------------------------------------------------------------
# The write path. WireGrill stands in for the grill's end of the UDP link, so
# these run the real read-change-write-confirm sequence without a network.
# ---------------------------------------------------------------------------


def _grill_on(wire):
    g = Grill("10.0.0.1", "TEST")
    g.send = wire.send
    g._sleep = wire.sleep
    return g


def _raises(exc_type, fn, *args):
    try:
        fn(*args)
    except exc_type as err:
        return err
    raise AssertionError(f"{fn.__name__}{args} should have raised {exc_type.__name__}")


PIZZA_ON_FROM_09 = bytes.fromhex("5543062914321919191921")


def test_write_reads_the_grill_then_sends_one_frame_and_confirms_it():
    wire = WireGrill(LIVE["09"])
    state = _grill_on(wire).write_config_field(PIZZA_MODE, 1)
    assert wire.sent()[0] == STATUS, "the block must be read from the grill before writing"
    assert wire.writes() == [PIZZA_ON_FROM_09]
    assert wire.packet[9] == 0x29
    assert state["config"].pizza_mode is True, "the result is the grill's own packet"


def test_write_waits_before_reading_the_result_back():
    wire = WireGrill(LIVE["09"])
    _grill_on(wire).write_config_field(PIZZA_MODE, 1)
    kinds = [kind if kind == "sleep" else message for kind, message in wire.events]
    assert kinds[:4] == [STATUS, PIZZA_ON_FROM_09, "sleep", STATUS]


def test_each_write_starts_from_the_block_as_it_is_now():
    """Never a remembered block: a write replaces all eight bytes, so a stale
    copy would silently undo whatever the app changed in between."""
    wire = WireGrill(LIVE["09"])
    g = _grill_on(wire)
    g.write_config_field(PIZZA_MODE, 1)  # 09 -> 29
    wire.packet[9] = 0x31  # meanwhile, the app moves the slider to Hot
    g.write_config_field(LOCK_TEMP_DISPLAY, 0)
    assert wire.writes()[-1] == bytes.fromhex("5543063014321919191921"), "Hot and Pizza Mode must survive"


def test_write_reads_past_lost_cut_merged_and_headless_replies():
    # The cut and merged replies carry an older block (0b). Built from them,
    # the frame would be 2b -- quietly turning Auto-Revert WiFi back on.
    head_cut = LIVE["09"][3:]
    wire = WireGrill(LIVE["09"], script=[None, TAIL_CUT_51, MERGED_104, head_cut])
    _grill_on(wire).write_config_field(PIZZA_MODE, 1)
    assert wire.writes() == [PIZZA_ON_FROM_09]


def test_write_sends_nothing_without_a_whole_packet_to_start_from():
    wire = WireGrill(LIVE["09"], script=[None, TAIL_CUT_51, MERGED_104, None, TAIL_CUT_51])
    err = _raises(GmgCommunicationError, _grill_on(wire).write_config_field, PIZZA_MODE, 1)
    assert wire.writes() == [], f"nothing may be sent without a fresh block ({err})"
    assert wire.sent() == [STATUS] * _const.MAX_STATUS_RETRIES


def test_write_refuses_a_grill_on_an_unverified_api_version():
    packet = bytearray(LIVE["09"])
    packet[8] = 5  # brandenco's 2020 capture reads 5
    wire = WireGrill(bytes(packet))
    err = _raises(GmgConfigWriteError, _grill_on(wire).write_config_field, PIZZA_MODE, 1)
    assert wire.writes() == []
    assert "version 5" in str(err), f"the error should name the version it saw: {err}"


def test_write_sends_nothing_when_the_setting_is_already_there():
    wire = WireGrill(LIVE["27"])  # Pizza Mode already on
    state = _grill_on(wire).write_config_field(PIZZA_MODE, 1)
    assert wire.writes() == []
    assert state["config"].pizza_mode is True


def test_write_refuses_a_value_the_app_does_not_offer_before_touching_the_network():
    wire = WireGrill(LIVE["09"])
    _raises(ValueError, _grill_on(wire).write_config_field, CLIMATE, 5)
    assert wire.sent() == []


def test_write_fails_at_once_when_the_grill_reports_a_different_block():
    # The grill takes the frame but lands Auto-Revert WiFi on as well: 29 -> 2b.
    wire = WireGrill(LIVE["09"], apply=lambda block: block[:1] + bytes([block[1] | 0x02]) + block[2:])
    err = _raises(GmgConfigWriteError, _grill_on(wire).write_config_field, PIZZA_MODE, 1)
    message = str(err)
    for block in ("06 09 14 32 19 19 19 19", "06 29 14 32 19 19 19 19", "06 2b 14 32 19 19 19 19"):
        assert block in message, f"the error must show {block} (before / sent / now): {message}"
    assert len(wire.writes()) == 1, "a failed write is never resent"
    assert wire.sent().count(STATUS) == 2, "a definite mismatch should not wait out the clock"


def test_write_fails_when_the_change_never_shows_and_never_resends():
    wire = WireGrill(LIVE["09"], apply=lambda block: None)  # the grill ignores it
    err = _raises(GmgConfigWriteError, _grill_on(wire).write_config_field, PIZZA_MODE, 1)
    assert len(wire.writes()) == 1, "a failed write is never resent"
    assert wire.sent().count(STATUS) == 1 + _const.CONFIG_CONFIRM_POLLS
    assert "06 09 14 32 19 19 19 19" in str(err), f"the error should show what the grill kept: {err}"


def test_write_says_the_result_is_unknown_when_the_grill_goes_quiet():
    wire = WireGrill(LIVE["09"], script=[LIVE_REPLY] + [None] * _const.CONFIG_CONFIRM_POLLS)
    err = _raises(GmgConfigWriteError, _grill_on(wire).write_config_field, PIZZA_MODE, 1)
    assert "unknown" in str(err), f"no reply is not the same as no change: {err}"
    assert len(wire.writes()) == 1


def test_write_waits_through_slow_lost_and_cut_replies_until_the_change_shows():
    # Applied only on the third poll after the write; the replies in between
    # are lost or cut, and the cut ones carry an older block (0b).
    wire = WireGrill(LIVE["09"], script=[LIVE_REPLY, None, TAIL_CUT_51, MERGED_104], applies_after=3)
    state = _grill_on(wire).write_config_field(PIZZA_MODE, 1)
    assert state["config"].block == bytes.fromhex("0629143219191919")
    assert len(wire.writes()) == 1


def test_two_writes_at_once_both_land():
    """Two quick toggles must not both start from the same block -- the
    second write would put back whatever the first one changed."""
    wire = WireGrill(LIVE["09"], rendezvous=threading.Barrier(2, timeout=0.5))
    g = _grill_on(wire)
    errors = []

    def write(field, value):
        try:
            g.write_config_field(field, value)
        except Exception as err:  # noqa: BLE001 -- collected for the assertion below
            errors.append(err)

    threads = [
        threading.Thread(target=write, args=(PIZZA_MODE, 1)),
        threading.Thread(target=write, args=(LOCK_TEMP_DISPLAY, 0)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert errors == []
    assert wire.packet[9] == 0x28, f"both changes should be on the grill, got {wire.packet[9]:02x}"


if __name__ == "__main__":
    tests = [value for name, value in list(globals().items()) if name.startswith("test_")]
    for t in tests:
        t()
        print(f"{t.__name__}: PASS")
    print(f"\nAll {len(tests)} tests passed.")
