"""
Overuse detector for GCC delay-based estimation.

Direct port from pion's pkg/gcc/overuse_detector.go
"""

import time
from typing import Callable, Protocol

from .types import BandwidthUsage, DelayStats


class Threshold(Protocol):
    """
    Threshold interface for overuse detection.

    Direct port of pion's threshold interface.
    Reference: overuse_detector.go lines 10-12
    """

    def compare(
        self, estimate: float, delta: float
    ) -> tuple[BandwidthUsage, float, float]:
        """
        Compare estimate against threshold.

        Args:
            estimate: Kalman filter estimate in seconds
            delta: Time delta in seconds

        Returns:
            Tuple of (usage, modified_estimate, threshold)
        """
        ...


class OveruseDetector:
    """
    Detects network overuse with hysteresis to avoid false positives.

    The detector adds hysteresis on top of the adaptive threshold to ensure
    we don't immediately react to temporary spikes. It requires the overuse
    condition to persist for a minimum duration before declaring overuse.

    Direct port of pion's overuseDetector.
    Reference: overuse_detector.go lines 14-24
    """

    def __init__(
        self,
        threshold: Threshold,
        overuse_time: float,
        delay_stats_writer: Callable[[DelayStats], None],
    ):
        """
        Create new overuse detector.

        Direct port of pion's newOveruseDetector.
        Reference: overuse_detector.go lines 26-36

        Args:
            threshold: Adaptive threshold for comparison
            overuse_time: Minimum duration for overuse detection (seconds)
            delay_stats_writer: Callback for emitting delay statistics
        """
        self._threshold = threshold
        self._overuse_time = overuse_time
        self._ds_writer = delay_stats_writer

        # State tracking (lines 31-34)
        self._last_estimate = 0.0
        self._last_update = time.time()
        self._increasing_duration = 0.0
        self._increasing_counter = 0

    def on_delay_stats(self, ds: DelayStats) -> None:
        """
        Process delay statistics and detect overuse.

        Direct port of pion's onDelayStats method.
        Reference: overuse_detector.go lines 38-86

        Args:
            ds: Delay statistics from slope estimator
        """
        # Lines 39-41: Calculate time delta
        now = time.time()
        delta = now - self._last_update
        self._last_update = now

        # Line 43: Compare estimate against threshold
        threshold_use, estimate, current_threshold = self._threshold.compare(
            ds.estimate, ds.last_receive_delta
        )

        # Line 45: Default to normal usage
        use = BandwidthUsage.NORMAL

        # Lines 46-61: Overuse detection with hysteresis
        if threshold_use == BandwidthUsage.OVER:
            # Lines 47-51: Accumulate increasing duration
            if self._increasing_duration == 0:
                # Start with half the delta (line 48)
                self._increasing_duration = delta / 2
            else:
                # Add full delta (line 50)
                self._increasing_duration += delta

            # Line 53: Increment counter
            self._increasing_counter += 1

            # Lines 55-60: Check if overuse condition has persisted
            # Condition 1: overuseTime == 0 and counter > 1 (immediate mode)
            # Condition 2: duration > overuseTime and counter > 1 (timed mode)
            if (self._overuse_time == 0 and self._increasing_counter > 1) or (
                self._increasing_duration > self._overuse_time
                and self._increasing_counter > 1
            ):
                # Line 57-59: Only declare overuse if estimate is still increasing
                if estimate > self._last_estimate:
                    use = BandwidthUsage.OVER

        # Lines 63-67: Underuse resets counters
        if threshold_use == BandwidthUsage.UNDER:
            self._increasing_counter = 0
            self._increasing_duration = 0.0
            use = BandwidthUsage.UNDER

        # Lines 69-73: Normal resets counters
        if threshold_use == BandwidthUsage.NORMAL:
            self._increasing_duration = 0.0
            self._increasing_counter = 0
            use = BandwidthUsage.NORMAL

        # Line 75: Store estimate for next iteration
        self._last_estimate = estimate

        # Lines 77-85: Emit DelayStats with updated usage
        # Note: State and TargetBitrate still 0, will be set by RateController
        self._ds_writer(
            DelayStats(
                measurement=ds.measurement,
                estimate=estimate,
                threshold=current_threshold,
                last_receive_delta=ds.last_receive_delta,
                usage=use,
                state=0,  # RateControlState placeholder
                target_bitrate=0,  # Will be set by RateController
            )
        )
