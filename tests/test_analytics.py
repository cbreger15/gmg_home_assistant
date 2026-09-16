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


def test_a_single_degree_is_not_a_trend():
    # Flat at 160F, then one step to 161F. While that step crosses the
    # window, the fitted line rises by up to 1.46F -- 4.4F an hour at its
    # steepest, but one degree is all there is to go on.
    trend = ProbeTrend()
    last = _feed(trend, [160] * 41)
    steepest = 0
    for i in range(1, 42):
        trend.add(last + i * EVERY, 161)
        steepest = max(steepest, trend.rate_per_hour())
        assert trend.finish_time(165) is None, f"{i} polls after the step"
    assert steepest > 4.3


def test_a_single_degree_is_not_a_trend_even_with_lost_polls():
    # The worst case for a line through one step: the polls just inside each
    # end of the window lost, and the step in the middle. The line rises
    # 1.52F across the window, so a rise threshold alone would let it through.
    trend = ProbeTrend()
    for i in range(41):
        if i not in (1, 39):
            trend.add(T0 + i * EVERY, 160 if i < 20 else 161)
    assert trend.rate_per_hour() / 3 > 1.5  # the rise over these 20 minutes
    assert trend.finish_time(165) is None


def test_a_whole_degree_creep_gives_no_estimate():
    # A stall on the grill: whole degrees, rising up to 4F an hour. A rate
    # alone would flicker between no estimate and a finish many hours out.
    for rate in (0.5, 1, 2, 3, 4):
        trend = ProbeTrend()
        for i in range(2 * 120 + 1):  # two hours of polls
            trend.add(T0 + i * EVERY, int(165.3 + rate * i / 120))
            assert trend.finish_time(203) is None, f"{rate}F an hour, poll {i}"


def test_the_line_must_rise_more_than_one_and_a_half_degrees():
    # Two whole-degree steps in a full window. Where they fall decides how
    # far the fitted line rises: 1.41F is not enough, 1.90F is.
    def steps_at(first, second):
        trend = ProbeTrend()
        _feed(trend, [160 + (i >= first) + (i >= second) for i in range(41)])
        return trend

    low = steps_at(1, 13)
    assert 1.40 < low.rate_per_hour() / 3 < 1.5  # the rise over these 20 minutes
    assert low.finish_time(170) is None

    high = steps_at(5, 13)
    assert 1.5 < high.rate_per_hour() / 3 < 2.0
    assert high.finish_time(170) is not None


def test_a_creep_too_small_to_measure_gives_no_estimate():
    # 1.5F an hour for 10 minutes is a quarter of a degree -- even though the
    # target is close (2.75F off, under 2 hours at this rate).
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

    # In whole degrees the reading can get there before the fitted line does.
    stairs = ProbeTrend()
    _feed(stairs, [150 + i // 4 for i in range(41)])  # just stepped up to 160F
    assert stairs.finish_time(160) is None


def test_no_estimate_further_than_a_day_out():
    # 7.2F an hour for the full 20 minutes (a 2.4F rise): 165.6F to go is
    # 23 hours, 180F to go is 25.
    trend = ProbeTrend()
    last = _feed(trend, [150 + 0.06 * i for i in range(41)])  # ends at 152.4F
    _close(trend.finish_time(152.4 + 165.6), last + timedelta(hours=23))
    assert trend.finish_time(152.4 + 180) is None


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


def test_a_second_reading_at_the_same_moment_is_ignored():
    trend = ProbeTrend()
    last = _feed(trend, [150 + 0.125 * i for i in range(21)])
    trend.add(last, 999)
    assert abs(trend.rate_per_hour() - 15.0) < 1e-9


def test_a_clock_set_back_starts_the_trend_again():
    # Readings from before the change can't be placed against the ones after
    # it; kept, they would freeze the trend until the clock caught up.
    trend = ProbeTrend()
    last = _feed(trend, [150 + 0.125 * i for i in range(21)])
    restarted = _feed(trend, [153 + 0.125 * i for i in range(21)], start=last - timedelta(minutes=1))
    assert abs(trend.rate_per_hour() - 15.0) < 1e-9
    _close(trend.finish_time(165), restarted + timedelta(minutes=38))  # 9.5F at 15F an hour


if __name__ == "__main__":
    tests = [value for name, value in list(globals().items()) if name.startswith("test_")]
    for t in tests:
        t()
        print(f"{t.__name__}: PASS")
    print(f"\nAll {len(tests)} tests passed.")
