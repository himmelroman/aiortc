"""
Adaptive threshold for GCC overuse detection.

Direct port from pion's pkg/gcc/adaptive_threshold.go
"""

import time
from typing import Tuple

from .types import BandwidthUsage

# Maximum number of deltas to accumulate (line 12)
MAX_DELTAS = 60


def _clamp(value: float, min_val: float, max_val: float) -> float:
    """
    Clamp value to [min_val, max_val] range.

    Direct port of pion's clampDuration function.
    Reference: gcc.go lines 13-15

    Args:
        value: Value to clamp (in seconds)
        min_val: Minimum value (in seconds)
        max_val: Maximum value (in seconds)

    Returns:
        Clamped value (in seconds)
    """
    return max(min_val, min(max_val, value))


class AdaptiveThreshold:
    """
    Implements a threshold that continuously adapts depending on current
    measurements/estimates.

    This is necessary to avoid starving GCC in the presence of concurrent TCP
    flows by allowing larger queueing delays when measurements/estimates increase.
    overuse_coefficient_up and overuse_coefficient_down define by how much the
    threshold adapts. We want the threshold to increase fast if the measurement
    is outside [-thresh, thresh] and decrease slowly if it is within.

    See https://datatracker.ietf.org/doc/html/draft-ietf-rmcat-gcc-02#section-5.4
    or "Analysis and Design of the Google Congestion Control for Web Real-time
    Communication (WebRTC)" for more details.

    Direct port of pion's adaptiveThreshold.
    Reference: adaptive_threshold.go lines 23-43
    """

    def __init__(self, initial_threshold: float = 0.0125):
        """
        Initialize adaptive threshold with defaults from draft-ietf-rmcat-gcc-02.

        Direct port of pion's newAdaptiveThreshold.
        Reference: adaptive_threshold.go lines 47-62

        Args:
            initial_threshold: Initial threshold in seconds (default 12.5ms)
        """
        # Line 49: thresh = 12500 microseconds = 12.5ms
        self._thresh = initial_threshold if initial_threshold > 0 else 0.0125

        # Lines 50-51: Coefficients from GCC spec
        self._overuse_coefficient_up = 0.01  # Fast increase
        self._overuse_coefficient_down = 0.00018  # Slow decrease

        # Lines 52-53: Min/max bounds
        self._min = 0.006  # 6ms
        self._max = 0.600  # 600ms

        # Lines 54-55: State tracking
        self._last_update = 0.0  # time.Time{} = zero time
        self._num_deltas = 0

    def compare(
        self, estimate: float, measurement: float
    ) -> Tuple[BandwidthUsage, float, float]:
        """
        Compare estimate against adaptive threshold to determine usage.

        Direct port of pion's compare method.
        Reference: adaptive_threshold.go lines 64-80

        Args:
            estimate: Kalman filter estimate in seconds
            measurement: Raw measurement (unused in pion, kept for signature)

        Returns:
            Tuple of (usage, modified_estimate, threshold):
            - usage: BandwidthUsage classification
            - modified_estimate: estimate * min(num_deltas, 60)
            - threshold: Current threshold value
        """
        # Line 65: Increment delta counter
        self._num_deltas += 1

        # Lines 66-68: Return normal for first delta
        if self._num_deltas < 2:
            return BandwidthUsage.NORMAL, estimate, self._max

        # Line 69: t = min(numDeltas, maxDeltas) * estimate
        t = min(self._num_deltas, MAX_DELTAS) * estimate

        # Lines 70-75: Determine usage
        use = BandwidthUsage.NORMAL
        if t > self._thresh:
            use = BandwidthUsage.OVER
        elif t < -self._thresh:
            use = BandwidthUsage.UNDER

        # Line 76: Store current threshold before update
        thresh = self._thresh

        # Line 77: Update threshold
        self._update(t)

        # Line 79: Return usage, modified estimate, and threshold
        return use, t, thresh

    def _update(self, estimate: float) -> None:
        """
        Update adaptive threshold based on estimate.

        Direct port of pion's update method.
        Reference: adaptive_threshold.go lines 82-106

        Args:
            estimate: Modified estimate (t from compare)
        """
        # Lines 83-86: Initialize last update time
        now = time.time()
        if self._last_update == 0.0:
            self._last_update = now

        # Line 87: Get absolute value of estimate
        abs_estimate = abs(estimate)

        # Lines 88-92: Early return if estimate is way outside threshold
        # (15ms in seconds = 0.015)
        if abs_estimate > self._thresh + 0.015:
            self._last_update = now
            return

        # Lines 93-96: Select coefficient based on estimate vs threshold
        k = self._overuse_coefficient_up
        if abs_estimate < self._thresh:
            k = self._overuse_coefficient_down

        # Lines 97-100: Calculate time delta (clamped to 100ms)
        max_time_delta = 0.1  # 100ms in seconds
        time_delta = min(now - self._last_update, max_time_delta)

        # Line 101: d = abs(estimate) - threshold
        d = abs_estimate - self._thresh

        # Line 102: add = k * d * timeDelta
        # Note: In pion this is k * d(ms) * timeDelta(ms)
        # We use seconds, so multiply by 1000*1000 to match pion's units
        add = k * (d * 1000.0) * (time_delta * 1000.0)

        # Line 103: thresh += add (convert from ms² back to seconds)
        # Pion: time.Duration(add*1000) * time.Microsecond
        # add is in ms², pion multiplies by 1000 then by time.Microsecond (1000ns)
        # Total: add * 1_000_000 nanoseconds / 1e9 ns/sec = add / 1000 seconds
        self._thresh += add / 1_000.0

        # Line 104: Clamp to [min, max]
        self._thresh = _clamp(self._thresh, self._min, self._max)

        # Line 105: Update timestamp
        self._last_update = now
