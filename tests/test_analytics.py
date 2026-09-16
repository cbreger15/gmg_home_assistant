"""The finish-time estimate: a rolling straight-line fit of a probe's temperature.

The series here are constructed, not captured -- no cook in the grill's
history had a probe target set -- so every expected time is worked out by hand
from the series' own slope.

Pure-Python, no Home Assistant test harness required -- run directly:
    python -m pytest tests/test_analytics.py -v
or without pytest installed:
    python tests/test_analytics.py
"""

from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys
import types

try:
    # Home Assistant is installed and the repo root is importable: use the
    # real package (see test_gmg_parsing.py).
    from custom_components.gmg import analytics as _analytics
except ImportError:
    _GMG_DIR = Path(__file__).parent.parent / "custom_components" / "gmg"

    sys.modules.setdefault("custom_components", types.ModuleType("custom_components"))
    if "custom_components.gmg" not in sys.modules:
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
    _analytics = _load("custom_components.gmg.analytics", "analytics.py")

ProbeTrend = _analytics.ProbeTrend

T0 = datetime(2026, 9, 16, 18, 0, tzinfo=timezone.utc)
EVERY = timedelta(seconds=30)  # one reading per poll


def _feed(trend, readings, start=T0, every=EVERY):
    """Add readings `every` apart from `start`; return the time of the last one."""
    for i, temperature in enumerate(readings):
        trend.add(start + i * every, temperature)
    return start + (len(readings) - 1) * every


def _close(actual, expected, tolerance=timedelta(seconds=1)):
    assert actual is not None, f"expected about {expected}, got no estimate"
    assert abs(actual - expected) <= tolerance, f"{actual} is not within {tolerance} of {expected}"


def test_a_steady_rise_projects_to_the_target():
    # 15F an hour is 0.125F per 30 s. From 150F, 21 readings take 10 minutes
    # and end at 152.5F; 12.5F more at 15F an hour is 50 minutes.
    trend = ProbeTrend()
    last = _feed(trend, [150 + 0.125 * i for i in range(21)])
    assert abs(trend.rate_per_hour() - 15.0) < 1e-9
    _close(trend.finish_time(165), last + timedelta(minutes=50))


def test_whole_degree_readings_still_give_a_usable_estimate():
    # The grill reports whole degrees: 30F an hour arrives as +1F every
    # 2 minutes. After 20 minutes the true line is at 160F, so 170F is
    # 20 minutes off; the staircase lags it by under a minute.
    trend = ProbeTrend()
    last = _feed(trend, [150 + i // 4 for i in range(41)])
    assert abs(trend.rate_per_hour() - 30.0) < 1.5
    _close(trend.finish_time(170), last + timedelta(minutes=20), tolerance=timedelta(minutes=2))


def test_a_stall_gives_no_estimate():
    trend = ProbeTrend()
    _feed(trend, [160] * 21)
    assert trend.rate_per_hour() == 0
    assert trend.finish_time(203) is None


def test_a_creep_below_the_stall_rate_gives_no_estimate():
    # 1.5F an hour, under the 2F an hour that counts as a stall -- even
    # though the target is close enough (2.75F, under 2 hours at this rate)
    # that only the stall rule stands in the way.
    trend = ProbeTrend()
    _feed(trend, [160 + 0.0125 * i for i in range(21)])  # ends at 160.25F
    assert abs(trend.rate_per_hour() - 1.5) < 1e-9
    assert trend.finish_time(163) is None


def test_a_falling_probe_gives_no_estimate():
    # The lid is open: -1F every 30 s.
    trend = ProbeTrend()
    _feed(trend, [170 - i for i in range(21)])
    assert trend.rate_per_hour() < 0
    assert trend.finish_time(203) is None


def test_no_estimate_from_too_little_data():
    few = ProbeTrend()
    _feed(few, [150, 151, 152, 153])  # 4 readings
    assert few.rate_per_hour() is None
    assert few.finish_time(160) is None

    brief = ProbeTrend()
    _feed(brief, [150 + i for i in range(8)])  # 8 readings, but only 3.5 minutes
    assert brief.rate_per_hour() is None
    assert brief.finish_time(200) is None

    enough = ProbeTrend()
    _feed(enough, [150 + i for i in range(11)])  # 11 readings over 5 minutes
    assert enough.finish_time(200) is not None


def test_no_estimate_once_the_target_is_reached():
    trend = ProbeTrend()
    _feed(trend, [150 + 0.5 * i for i in range(21)])  # ends at 160F
    assert trend.finish_time(160) is None
    assert trend.finish_time(155) is None
    assert trend.finish_time(161) is not None


def test_no_estimate_further_than_a_day_out():
    # 2.4F an hour: 48F to go is 20 hours, 100F to go is 41.7 hours.
    trend = ProbeTrend()
    last = _feed(trend, [150 + 0.02 * i for i in range(21)])  # ends at 150.4F
    _close(trend.finish_time(150.4 + 48), last + timedelta(hours=20))
    assert trend.finish_time(250.4) is None


def test_only_the_last_twenty_minutes_count():
    # 20 minutes rising 1F a minute, then 20 minutes flat: once the rise has
    # aged out of the window, the probe is stalled.
    trend = ProbeTrend()
    rising = [140 + 0.5 * i for i in range(41)]  # 140F -> 160F
    last = _feed(trend, rising)
    assert trend.finish_time(170) is not None
    _feed(trend, [160] * 41, start=last + EVERY)
    assert trend.rate_per_hour() == 0
    assert trend.finish_time(170) is None


def test_clearing_forgets_the_trend():
    trend = ProbeTrend()
    _feed(trend, [150 + i for i in range(21)])
    trend.clear()
    assert trend.rate_per_hour() is None


def test_a_reading_that_is_not_newer_is_ignored():
    trend = ProbeTrend()
    last = _feed(trend, [150 + 0.125 * i for i in range(21)])
    trend.add(last, 999)  # same moment again
    trend.add(last - EVERY, -999)  # out of order
    assert abs(trend.rate_per_hour() - 15.0) < 1e-9


if __name__ == "__main__":
    tests = [value for name, value in list(globals().items()) if name.startswith("test_")]
    for t in tests:
        t()
        print(f"{t.__name__}: PASS")
    print(f"\nAll {len(tests)} tests passed.")
