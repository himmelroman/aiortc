"""
Rate controller for GCC bandwidth estimation.

Direct port from pion's pkg/gcc/rate_controller.go
"""

import math
import threading
import time
from typing import Callable, Optional

from .types import BandwidthUsage, DelayStats, RateControlState

# EMA alpha for decrease rate tracking (line 13)
DECREASE_EMA_ALPHA = 0.95

# Multiplicative decrease factor (line 14)
BETA = 0.85


class ExponentialMovingAverage:
    """
    Tracks exponential moving average with variance and standard deviation.

    Direct port of pion's exponentialMovingAverage.
    Reference: rate_controller.go lines 36-51
    """

    def __init__(self):
        """Initialize EMA with zero values (line 69)."""
        self.average = 0.0
        self.variance = 0.0
        self.std_deviation = 0.0

    def update(self, value: float) -> None:
        """
        Update EMA with new value.

        Direct port of pion's update method.
        Reference: rate_controller.go lines 42-51

        Args:
            value: New value to incorporate
        """
        # Lines 43-44: Initialize on first value
        if self.average == 0.0:
            self.average = value
        else:
            # Lines 46-49: EMA update with variance tracking
            x = value - self.average
            self.average += DECREASE_EMA_ALPHA * x
            self.variance = (1 - DECREASE_EMA_ALPHA) * (
                self.variance + DECREASE_EMA_ALPHA * x * x
            )
            self.std_deviation = math.sqrt(self.variance)


def _clamp_int(value: int, min_val: int, max_val: int) -> int:
    """
    Clamp integer value to [min_val, max_val] range.

    Direct port of pion's clampInt function.
    Reference: gcc.go lines 9-11

    Args:
        value: Value to clamp
        min_val: Minimum value
        max_val: Maximum value

    Returns:
        Clamped value
    """
    return max(min_val, min(max_val, value))


class RateController:
    """
    Implements AIMD (Additive Increase Multiplicative Decrease) rate control.

    The rate controller adjusts target bitrate based on bandwidth usage:
    - INCREASE: Additive or multiplicative increase
    - DECREASE: Multiplicative decrease by beta (0.85)
    - HOLD: No change

    Direct port of pion's rateController.
    Reference: rate_controller.go lines 17-34
    """

    def __init__(
        self,
        initial_target_bitrate: int,
        min_bitrate: int,
        max_bitrate: int,
        delay_stats_writer: Callable[[DelayStats], None],
        now_fn: Optional[Callable[[], float]] = None,
    ):
        """
        Create new rate controller.

        Direct port of pion's newRateController.
        Reference: rate_controller.go lines 53-71

        Args:
            initial_target_bitrate: Initial target in bits per second
            min_bitrate: Minimum bitrate in bps
            max_bitrate: Maximum bitrate in bps
            delay_stats_writer: Callback for emitting delay statistics
            now_fn: Optional function returning current time in seconds (defaults to time.time)
        """
        # Configuration (lines 57-60)
        self._initial_target_bitrate = initial_target_bitrate
        self._min_bitrate = min_bitrate
        self._max_bitrate = max_bitrate
        self._ds_writer = delay_stats_writer
        self._now_fn = now_fn if now_fn is not None else time.time

        # Thread safety (line 25)
        self._lock = threading.Lock()

        # State (lines 62-69)
        self._init = False
        self._delay_stats = DelayStats(
            measurement=0.0,
            estimate=0.0,
            threshold=0.0,
            last_receive_delta=0.0,
            usage=BandwidthUsage.NORMAL,
            state=RateControlState.INCREASE,
            target_bitrate=0,
        )
        self._target = initial_target_bitrate
        self._last_update = 0.0
        self._last_state = RateControlState.INCREASE
        self._latest_rtt = 0.0
        self._latest_received_rate = 0
        self._latest_decrease_rate = ExponentialMovingAverage()

    def on_received_rate(self, rate: int) -> None:
        """
        Update latest received bitrate.

        Direct port of pion's onReceivedRate method.
        Reference: rate_controller.go lines 73-77

        Args:
            rate: Received bitrate in bits per second
        """
        with self._lock:
            self._latest_received_rate = rate

    def update_rtt(self, rtt: float) -> None:
        """
        Update latest round-trip time.

        Direct port of pion's updateRTT method.
        Reference: rate_controller.go lines 79-83

        Args:
            rtt: Round-trip time in seconds
        """
        with self._lock:
            self._latest_rtt = rtt

    def on_delay_stats(self, ds: DelayStats) -> None:
        """
        Process delay statistics and update target bitrate.

        Direct port of pion's onDelayStats method.
        Reference: rate_controller.go lines 85-137

        Args:
            ds: Delay statistics from overuse detector
        """
        now = self._now_fn()

        # Lines 88-94: Initialize on first call
        if not self._init:
            self._delay_stats = ds
            self._delay_stats.state = RateControlState.INCREASE
            self._init = True
            return

        # Lines 95-96: Store stats and transition state
        self._delay_stats = ds
        self._delay_stats.state = self._delay_stats.state.transition(ds.usage)

        # Lines 98-100: Early return if HOLD
        if self._delay_stats.state == RateControlState.HOLD:
            return

        # Lines 102-136: Compute new target based on state
        with self._lock:
            if self._delay_stats.state == RateControlState.INCREASE:
                # Lines 110-119: Additive/multiplicative increase
                self._target = _clamp_int(
                    self._increase(now), self._min_bitrate, self._max_bitrate
                )
                next_stats = DelayStats(
                    measurement=self._delay_stats.measurement,
                    estimate=self._delay_stats.estimate,
                    threshold=self._delay_stats.threshold,
                    last_receive_delta=self._delay_stats.last_receive_delta,
                    usage=self._delay_stats.usage,
                    state=self._delay_stats.state,
                    target_bitrate=self._target,
                )

            elif self._delay_stats.state == RateControlState.DECREASE:
                # Lines 122-131: Multiplicative decrease
                self._target = _clamp_int(
                    self._decrease(), self._min_bitrate, self._max_bitrate
                )
                next_stats = DelayStats(
                    measurement=self._delay_stats.measurement,
                    estimate=self._delay_stats.estimate,
                    threshold=self._delay_stats.threshold,
                    last_receive_delta=self._delay_stats.last_receive_delta,
                    usage=self._delay_stats.usage,
                    state=self._delay_stats.state,
                    target_bitrate=self._target,
                )
            else:
                # Should never occur (line 107-108)
                return

        # Line 136: Emit updated stats
        self._ds_writer(next_stats)

    def _increase(self, now: float) -> int:
        """
        Calculate increased target bitrate (AIMD additive increase).

        Direct port of pion's increase method.
        Reference: rate_controller.go lines 139-170

        Args:
            now: Current timestamp in seconds

        Returns:
            New target bitrate in bits per second
        """
        # Lines 140-153: Near last decrease - use additive increase
        if (
            self._latest_decrease_rate.average > 0
            and float(self._latest_received_rate)
            > self._latest_decrease_rate.average
            - 3 * self._latest_decrease_rate.std_deviation
            and float(self._latest_received_rate)
            < self._latest_decrease_rate.average
            + 3 * self._latest_decrease_rate.std_deviation
        ):
            # Lines 143-145: Calculate expected packet size
            bits_per_frame = float(self._target) / 30.0
            packets_per_frame = math.ceil(bits_per_frame / (1200 * 8))
            expected_packet_size_bits = bits_per_frame / packets_per_frame

            # Lines 147-149: Calculate additive increase
            response_time = 0.1 + self._latest_rtt  # 100ms + RTT
            alpha = 0.5 * min(
                (now - self._last_update) / response_time, 1.0
            )
            increase = int(max(1000.0, alpha * expected_packet_size_bits))
            self._last_update = now

            # Line 152: Clamp to 1.5 * received rate
            return int(
                min(
                    float(self._target + increase),
                    1.5 * float(self._latest_received_rate),
                )
            )

        # Lines 154-169: Far from last decrease - use multiplicative increase
        eta = math.pow(1.08, min((now - self._last_update), 1.0))
        self._last_update = now

        rate = int(eta * float(self._target))

        # Lines 159-163: Maximum increase to 1.5 * received rate
        received = int(1.5 * float(self._latest_received_rate))
        if rate > received and received > self._target:
            return received

        # Lines 165-167: Don't decrease
        if rate < self._target:
            return self._target

        return rate

    def _decrease(self) -> int:
        """
        Calculate decreased target bitrate (AIMD multiplicative decrease).

        Direct port of pion's decrease method.
        Reference: rate_controller.go lines 172-178

        Returns:
            New target bitrate in bits per second
        """
        # Line 173: Multiplicative decrease by beta (0.85)
        target = int(BETA * float(self._latest_received_rate))

        # Line 174: Update EMA of decrease rates
        self._latest_decrease_rate.update(float(self._latest_received_rate))

        # Line 175: Update timestamp
        self._last_update = self._now_fn()

        return target
