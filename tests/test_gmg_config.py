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
    from tests.grill_wire import (
        APP_CALIBRATED,
        APP_CALIBRATED_ALL,
        LIVE,
        LIVE_REPLY,
        MERGED_104,
        STATUS,
        TAIL_CUT_51,
        TEST_IP,
        NoNetwork,
        WireGrill,
    )
except ImportError:  # run directly: tests/ itself is on sys.path
    from grill_wire import (
        APP_CALIBRATED,
        APP_CALIBRATED_ALL,
        LIVE,
        LIVE_REPLY,
        MERGED_104,
        STATUS,
        TAIL_CUT_51,
        TEST_IP,
        NoNetwork,
        WireGrill,
    )

# No test here may reach a real grill -- see grill_wire.NoNetwork.
_gmg.socket = NoNetwork()

Grill = _gmg.Grill
ConfigField = _gmg.ConfigField
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
    # Calibration boxes as stored: every box at 0.
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


def test_calibration_bytes_read_as_the_app_set_them():
    """The app showed +2 / 0, +8 / 0 and +4 / 0 when this packet was read:
    each left box is its pair's first byte, stored as 20 or 25 plus the
    adjustment, and each right box at 0 still reads 50 / 25 / 25."""
    config = Grill._parse_status(APP_CALIBRATED)["config"]
    assert str(config) == "06 09 16 32 21 19 1d 19"
    assert config.grill_adjustment_raw == (20 + 2, 50)
    assert config.probe1_adjustment_raw == (25 + 8, 25)
    assert config.probe2_adjustment_raw == (25 + 4, 25)


def test_calibration_boxes_read_as_the_app_shows_them():
    # (150F box, 500F box) for the grill; (32F box, 212F box) for each probe.
    shipped = Grill._parse_status(LIVE["09"])["config"]
    assert shipped.grill_adjustment == (0, 0)
    assert shipped.probe1_adjustment == (0, 0)
    assert shipped.probe2_adjustment == (0, 0)

    set_in_app = Grill._parse_status(APP_CALIBRATED)["config"]  # +2/0, +8/0, +4/0
    assert set_in_app.grill_adjustment == (2, 0)
    assert set_in_app.probe1_adjustment == (8, 0)
    assert set_in_app.probe2_adjustment == (4, 0)

    # Every box different, right boxes and negatives included. The app showed
    # -2/+5, -8/-3 and -4/+6; the grill stored 18 55 17 22 21 31.
    all_six = Grill._parse_status(APP_CALIBRATED_ALL)["config"]
    assert str(all_six) == "06 09 12 37 11 16 15 1f"
    assert all_six.grill_adjustment_raw + all_six.probe1_adjustment_raw + all_six.probe2_adjustment_raw == (
        18, 55, 17, 22, 21, 31,
    )
    assert all_six.grill_adjustment == (-2, 5)
    assert all_six.probe1_adjustment == (-8, -3)
    assert all_six.probe2_adjustment == (-4, 6)


def test_an_empty_probe_jack_still_reads_disconnected_once_calibrated():
    # The grill shifts the 601 "nothing plugged in" reading by the calibration.
    for packet, readings in ((APP_CALIBRATED, (584, 593)), (APP_CALIBRATED_ALL, (608, 628))):
        state = Grill._parse_status(packet)
        assert (state["probe1_temp"], state["probe2_temp"]) == readings
        assert not any(_const.is_probe_connected(value) for value in readings)


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


def test_a_field_must_fit_inside_the_block():
    # (name, index, shift, width, max_value)
    for bad in (
        ("too_big", 1, 0, 1, 2),  # 2 does not fit one bit
        ("past_the_byte", 1, 6, 3, 4),  # bits 6-8
        ("past_the_block", 8, 0, 1, 1),  # the block is bytes 0-7
    ):
        try:
            ConfigField(*bad)
            raised = False
        except ValueError:
            raised = True
        assert raised, f"{bad[0]} must be refused at definition"


# ---------------------------------------------------------------------------
# The write path. WireGrill stands in for the grill's end of the UDP link, so
# these run the real read-change-write-confirm sequence without a network.
# ---------------------------------------------------------------------------


def _grill_on(wire):
    g = Grill(TEST_IP, "TEST")
    g.send = wire.send
    g._sleep = wire.sleep
    return g


def _reads_after_the_write(wire):
    sent = wire.sent()
    frame_at = next(i for i, message in enumerate(sent) if message[:2] == b"UC")
    return sent[frame_at + 1 :].count(STATUS)


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


def test_write_reads_twice_then_waits_before_reading_the_result_back():
    wire = WireGrill(LIVE["09"])
    _grill_on(wire).write_config_field(PIZZA_MODE, 1)
    kinds = [kind if kind == "sleep" else message for kind, message in wire.events]
    assert kinds[:5] == [STATUS, STATUS, PIZZA_ON_FROM_09, "sleep", STATUS]


def test_write_trusts_a_block_only_when_two_whole_replies_agree():
    """The read-back cannot catch a bad starting block -- the grill reports
    whatever it was sent -- and this link delivers replies held back from
    earlier requests. Here one from before the app turned Auto-Revert WiFi
    off (0b) arrives first; the grill really holds 09."""
    wire = WireGrill(LIVE["09"], script=[LIVE["0b"], LIVE_REPLY, LIVE_REPLY])
    _grill_on(wire).write_config_field(PIZZA_MODE, 1)
    assert wire.writes() == [PIZZA_ON_FROM_09], "built from 0b it would be 2b"


def test_write_is_not_fooled_by_a_spliced_reply():
    # 52 bytes, starts UR, API version 6 -- but bytes 14-51 are another
    # reply's head. Trusted, it would write 55 52 into probe 2's calibration.
    spliced = LIVE["09"][:14] + LIVE["09"][:38]
    assert len(spliced) == 52 and spliced[:2] == b"UR" and spliced[8] == 6
    wire = WireGrill(LIVE["09"], script=[spliced, LIVE_REPLY, LIVE_REPLY])
    _grill_on(wire).write_config_field(PIZZA_MODE, 1)
    assert wire.writes() == [PIZZA_ON_FROM_09]


def test_write_sends_nothing_when_whole_replies_never_agree():
    attempts = _const.CONFIG_READ_ATTEMPTS
    wire = WireGrill(LIVE["09"], script=[LIVE["0b"], LIVE["09"]] * attempts)
    err = _raises(GmgConfigWriteError, _grill_on(wire).write_config_field, PIZZA_MODE, 1)
    assert wire.writes() == []
    assert wire.sent() == [STATUS] * attempts
    assert "06 0b" in str(err) and "06 09" in str(err), f"the error should show both: {err}"


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
    attempts = _const.CONFIG_READ_ATTEMPTS
    # One whole reply in the lot is not enough either.
    unusable = ([None, TAIL_CUT_51, MERGED_104] * attempts)[: attempts - 1]
    wire = WireGrill(LIVE["09"], script=[LIVE_REPLY] + unusable)
    err = _raises(GmgCommunicationError, _grill_on(wire).write_config_field, PIZZA_MODE, 1)
    assert wire.writes() == [], f"nothing may be sent without a fresh block ({err})"
    assert wire.sent() == [STATUS] * attempts


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
    g = _grill_on(wire)
    for bad in (5, -1, 1.0, "1"):
        _raises(ValueError, g.write_config_field, CLIMATE, bad)
    assert wire.sent() == []


def test_a_block_containing_the_command_terminator_is_written_whole():
    """Every command ends with "!" (0x21), and a block can contain that byte.
    On 16 Sep 2026 the GMG app wrote 06 09 16 32 21 19 1d 19 and the grill
    took all of it (APP_CALIBRATED), so such a frame is sent like any other."""
    # Byte 12 is 0x21 (probe 1 at +8); turn Pizza Mode on around it.
    wire = WireGrill(APP_CALIBRATED)
    state = _grill_on(wire).write_config_field(PIZZA_MODE, 1)
    assert wire.writes() == [bytes.fromhex("55430629163221191d1921")]
    assert str(state["config"]) == "06 29 16 32 21 19 1d 19"

    # Byte 9 becomes 0x21 itself: Icy, Pizza Mode and Lock Temp Display on,
    # Auto-Revert WiFi off.
    packet = bytearray(LIVE["09"])
    packet[9] = 0x29  # Pizza Mode on, Average
    wire = WireGrill(bytes(packet))
    state = _grill_on(wire).write_config_field(CLIMATE, 0)
    assert wire.writes() == [bytes.fromhex("5543062114321919191921")]
    assert str(state["config"]) == "06 21 14 32 19 19 19 19"


def test_write_fails_at_once_when_the_grill_reports_a_different_block():
    # The grill takes the frame but lands Auto-Revert WiFi on as well: 29 -> 2b.
    wire = WireGrill(LIVE["09"], apply=lambda block: block[:1] + bytes([block[1] | 0x02]) + block[2:])
    err = _raises(GmgConfigWriteError, _grill_on(wire).write_config_field, PIZZA_MODE, 1)
    message = str(err)
    for block in ("06 09 14 32 19 19 19 19", "06 29 14 32 19 19 19 19", "06 2b 14 32 19 19 19 19"):
        assert block in message, f"the error must show {block} (before / sent / now): {message}"
    assert len(wire.writes()) == 1, "a failed write is never resent"
    assert _reads_after_the_write(wire) == 1, "a definite mismatch should not wait out the clock"


def test_write_fails_when_the_change_never_shows_and_never_resends():
    wire = WireGrill(LIVE["09"], apply=lambda block: None)  # the grill ignores it
    err = _raises(GmgConfigWriteError, _grill_on(wire).write_config_field, PIZZA_MODE, 1)
    assert len(wire.writes()) == 1, "a failed write is never resent"
    assert _reads_after_the_write(wire) == _const.CONFIG_CONFIRM_POLLS
    assert "06 09 14 32 19 19 19 19" in str(err), f"the error should show what the grill kept: {err}"


def test_write_says_the_result_is_unknown_when_the_grill_goes_quiet():
    wire = WireGrill(
        LIVE["09"], script=[LIVE_REPLY, LIVE_REPLY] + [None] * _const.CONFIG_CONFIRM_POLLS
    )
    err = _raises(GmgConfigWriteError, _grill_on(wire).write_config_field, PIZZA_MODE, 1)
    assert "unknown" in str(err), f"no reply is not the same as no change: {err}"
    assert len(wire.writes()) == 1


def test_write_waits_through_slow_lost_and_cut_replies_until_the_change_shows():
    # Applied only on the third poll after the write; the replies in between
    # are lost or cut, and the cut ones carry an older block (0b).
    wire = WireGrill(
        LIVE["09"],
        script=[LIVE_REPLY, LIVE_REPLY, None, TAIL_CUT_51, MERGED_104],
        applies_after=3,
    )
    state = _grill_on(wire).write_config_field(PIZZA_MODE, 1)
    assert state["config"].block == bytes.fromhex("0629143219191919")
    assert len(wire.writes()) == 1


def _write_at_once(writes):
    """Run (grill, field, value) writes on threads started together."""
    errors = []

    def write(grill, field, value):
        try:
            grill.write_config_field(field, value)
        except Exception as err:  # noqa: BLE001 -- collected for the assertion below
            errors.append(err)

    threads = [threading.Thread(target=write, args=args) for args in writes]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not any(t.is_alive() for t in threads), "a write never finished"
    return errors


def test_two_writes_at_once_both_land():
    """Two quick toggles must not both start from the same block -- the
    second write would put back whatever the first one changed."""
    wire = WireGrill(LIVE["09"], rendezvous=threading.Barrier(2, timeout=0.5))
    g = _grill_on(wire)
    assert _write_at_once([(g, PIZZA_MODE, 1), (g, LOCK_TEMP_DISPLAY, 0)]) == []
    assert wire.packet[9] == 0x28, f"both changes should be on the grill, got {wire.packet[9]:02x}"


def test_writes_through_two_grill_objects_for_one_grill_both_land():
    """Reloading the integration makes a new Grill while the old one may
    still be confirming a write; they must still take turns."""
    wire = WireGrill(LIVE["09"], rendezvous=threading.Barrier(2, timeout=0.5))
    before_reload, after_reload = _grill_on(wire), _grill_on(wire)
    errors = _write_at_once([(before_reload, PIZZA_MODE, 1), (after_reload, LOCK_TEMP_DISPLAY, 0)])
    assert errors == []
    assert wire.packet[9] == 0x28, f"both changes should be on the grill, got {wire.packet[9]:02x}"


if __name__ == "__main__":
    tests = [value for name, value in list(globals().items()) if name.startswith("test_")]
    for t in tests:
        t()
        print(f"{t.__name__}: PASS")
    print(f"\nAll {len(tests)} tests passed.")
