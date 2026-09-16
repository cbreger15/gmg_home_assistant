"""Cook analytics built from the grill's own readings.

No Home Assistant imports, so it is tested on its own (tests/test_analytics.py).
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta

from .const import ETA_MAX_AHEAD, ETA_MIN_RATE, ETA_MIN_SAMPLES, ETA_MIN_SPAN, ETA_WINDOW


class ProbeTrend:
    """A rolling straight-line fit of one probe's temperature.

    Feed it one reading per poll; it keeps the last `window` of them and
    projects when the probe will reach a target at the current rate. A
    least-squares line rather than the last two readings: the grill reports
    whole degrees, so a slow rise arrives as a staircase.
    """

    def __init__(self, window: timedelta = ETA_WINDOW) -> None:
        self._window = window
        self._readings: deque[tuple[datetime, float]] = deque()

    def add(self, when: datetime, temperature: float) -> None:
        """Add a reading. One that is not newer than the last is ignored."""
        if self._readings and when <= self._readings[-1][0]:
            return
        self._readings.append((when, float(temperature)))
        while when - self._readings[0][0] > self._window:
            self._readings.popleft()

    def clear(self) -> None:
        self._readings.clear()

    def _fit(self) -> tuple[float, float, datetime] | None:
        """(slope in degrees per second, the line's value now, now), or None."""
        if len(self._readings) < ETA_MIN_SAMPLES:
            return None
        first, now = self._readings[0][0], self._readings[-1][0]
        if now - first < ETA_MIN_SPAN:
            return None

        xs = [(when - first).total_seconds() for when, _ in self._readings]
        ys = [temperature for _, temperature in self._readings]
        mean_x = sum(xs) / len(xs)
        mean_y = sum(ys) / len(ys)
        spread = sum((x - mean_x) ** 2 for x in xs)
        slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / spread
        return slope, mean_y + slope * (xs[-1] - mean_x), now

    def rate_per_hour(self) -> float | None:
        """Degrees per hour over the window, or None without enough readings."""
        fit = self._fit()
        return None if fit is None else fit[0] * 3600

    def finish_time(self, target: float) -> datetime | None:
        """When the probe reaches `target` at the current rate.

        None when that cannot honestly be said: too few readings, a stall
        (slower than ETA_MIN_RATE) or a fall, the target already reached, or
        a finish further off than ETA_MAX_AHEAD.
        """
        fit = self._fit()
        if fit is None:
            return None
        slope, level, now = fit
        if slope * 3600 < ETA_MIN_RATE:
            return None
        if level >= target or self._readings[-1][1] >= target:
            return None
        ahead = timedelta(seconds=(target - level) / slope)
        if ahead > ETA_MAX_AHEAD:
            return None
        return now + ahead
