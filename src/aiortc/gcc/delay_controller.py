"""
Delay-based congestion controller for GCC.

Orchestrates the entire GCC delay-based pipeline:
- ArrivalGroupAccumulator → SlopeEstimator → OveruseDetector → RateController
- RateCalculator (parallel) → RateController

Port of pion's pkg/gcc/delay_based_bwe.go
"""

import time
from typing import Callable, List, Optional
from .acknowledgment import Acknowledgment
from .adaptive_threshold import AdaptiveThreshold
from .arrival_group_accumulator import ArrivalGroupAccumulator
from .kalman_filter import KalmanFilter
from .overuse_detector import OveruseDetector
from .rate_calculator import RateCalculator
from .rate_controller import RateController
from .slope_estimator import SlopeEstimator
from .types import DelayStats


class DelayController:
    """
    Orchestrates the GCC delay-based congestion control pipeline.

    Wires together all GCC components:
    1. Delay-based pipeline: ArrivalGroupAccumulator → SlopeEstimator → OveruseDetector → RateController
    2. Rate calculation pipeline: RateCalculator → RateController (received rate)

    The two pipelines run in parallel and feed into the RateController which
    performs AIMD rate control based on both delay signals and received rate.

    Reference: pkg/gcc/delay_based_bwe.go lines 29-109
    """

    def __init__(
        self,
        initial_bitrate: int,
        min_bitrate: int,
        max_bitrate: int,
        now_fn: Optional[Callable[[], float]] = None
    ):
        """
        Initialize delay controller.

        Wires all components together following pion's initialization sequence.

        Args:
            initial_bitrate: Starting bitrate estimate (bits/sec)
            min_bitrate: Minimum allowed bitrate (bits/sec)
            max_bitrate: Maximum allowed bitrate (bits/sec)
            now_fn: Optional function returning current time in seconds (defaults to time.time)

        Reference: pkg/gcc/delay_based_bwe.go lines 50-90
        """
        if now_fn is None:
            now_fn = time.time

        self._on_update_callback: Optional[Callable[[DelayStats], None]] = None

        # Create components bottom-up (deepest dependencies first)
        # Reference: lines 64-76

        # Rate controller (final stage)
        # Reference: lines 64-73
        def rate_controller_callback(ds: DelayStats) -> None:
            # This matches pion's callback at lines 66-70
            if self._on_update_callback is not None:
                self._on_update_callback(ds)

        self._rate_controller = RateController(
            initial_target_bitrate=initial_bitrate,
            min_bitrate=min_bitrate,
            max_bitrate=max_bitrate,
            delay_stats_writer=rate_controller_callback,
            now_fn=now_fn
        )

        # Overuse detector (feeds rate controller)
        # Reference: line 74
        # 10*time.Millisecond = 0.01 seconds
        self._overuse_detector = OveruseDetector(
            threshold=AdaptiveThreshold(),
            overuse_time=0.01,  # 10ms
            delay_stats_writer=self._rate_controller.on_delay_stats
        )

        # Slope estimator (feeds overuse detector)
        # Reference: line 75
        self._slope_estimator = SlopeEstimator(
            kalman_filter=KalmanFilter(),
            delay_stats_writer=self._overuse_detector.on_delay_stats
        )

        # Arrival group accumulator (feeds slope estimator)
        # Reference: line 76
        self._arrival_group_accumulator = ArrivalGroupAccumulator()

        # Rate calculator (parallel pipeline, feeds rate controller)
        # Reference: line 78
        # 500*time.Millisecond = 0.5 seconds
        self._rate_calculator = RateCalculator(window=0.5)

    def on_update(self, callback: Callable[[DelayStats], None]) -> None:
        """
        Register callback for DelayStats updates.

        The callback will be invoked whenever the rate controller updates
        the target bitrate or internal state.

        Args:
            callback: Function to call with DelayStats updates

        Reference: pkg/gcc/delay_based_bwe.go lines 93-95
        """
        self._on_update_callback = callback

    def update_delay_estimate(self, acks: List[Acknowledgment]) -> None:
        """
        Process acknowledgments through both pipelines.

        Feeds acknowledgments to:
        1. Delay-based pipeline (via arrival group accumulator)
        2. Rate calculation pipeline (via rate calculator)

        This matches pion's updateDelayEstimate which sends acks to both
        the ackPipe and ackRatePipe channels.

        Args:
            acks: List of acknowledgments to process

        Reference: pkg/gcc/delay_based_bwe.go lines 97-100
        """
        # Feed to delay-based pipeline
        # Reference: line 98 (d.ackPipe <- acks)
        self._arrival_group_accumulator.process_acknowledgments(
            acks,
            self._slope_estimator.on_arrival_group
        )

        # Feed to rate calculation pipeline
        # Reference: line 99 (d.ackRatePipe <- acks)
        self._rate_calculator.process_acknowledgments(
            acks,
            self._rate_controller.on_received_rate
        )

    def close(self) -> None:
        """
        Clean up resources.

        In pion, this closes channels and waits for goroutines.
        In Python, this is a no-op since we don't use background threads,
        but kept for API compatibility.

        Reference: pkg/gcc/delay_based_bwe.go lines 102-109
        """
        # No cleanup needed in Python (no goroutines/channels)
        pass
