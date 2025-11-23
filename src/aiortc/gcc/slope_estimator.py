"""
Slope estimator for GCC delay-based estimation.

Direct port from pion's pkg/gcc/slope_estimator.go
"""

from typing import Callable, Optional, Protocol

from .arrival_group import ArrivalGroup
from .types import DelayStats


class Estimator(Protocol):
    """
    Estimator interface for updating delay estimates.

    Direct port of pion's estimator interface.
    Reference: slope_estimator.go lines 10-12
    """

    def update_estimate(self, measurement: float) -> float:
        """
        Update estimate with measurement.

        Args:
            measurement: Delay variation in seconds

        Returns:
            Updated estimate in seconds
        """
        ...


class SlopeEstimator:
    """
    Estimates delay slope using inter-group delay variation.

    Direct port of pion's slopeEstimator.
    Reference: slope_estimator.go lines 20-25
    """

    def __init__(
        self,
        estimator: Estimator,
        delay_stats_writer: Callable[[DelayStats], None],
    ):
        """
        Create new slope estimator.

        Direct port of pion's newSlopeEstimator.
        Reference: slope_estimator.go lines 27-32

        Args:
            estimator: Kalman filter for delay estimation
            delay_stats_writer: Callback for emitting delay statistics
        """
        self._estimator = estimator
        self._delay_stats_writer = delay_stats_writer
        self._init = False
        self._group: Optional[ArrivalGroup] = None

    def on_arrival_group(self, ag: ArrivalGroup) -> None:
        """
        Process arrival group and emit delay statistics.

        Direct port of pion's onArrivalGroup method.
        Reference: slope_estimator.go lines 34-53

        Args:
            ag: Arrival group to process
        """
        # Initialize with first group (lines 35-40)
        if not self._init:
            self._group = ag
            self._init = True
            return

        # Calculate inter-group delay variation (line 41)
        measurement = _inter_group_delay_variation(self._group, ag)

        # Calculate time delta between groups (line 42)
        delta = ag.arrival - self._group.arrival

        # Store current group (line 43)
        self._group = ag

        # Emit delay statistics (lines 44-52)
        # Note: Threshold, Usage, State, and TargetBitrate are initialized to 0
        # and will be filled in by downstream components (adaptive threshold,
        # overuse detector, rate controller)
        self._delay_stats_writer(
            DelayStats(
                measurement=measurement,
                estimate=self._estimator.update_estimate(measurement),
                threshold=0.0,
                last_receive_delta=delta,
                usage=0,  # BandwidthUsage.NORMAL as placeholder
                state=0,  # RateControlState.INCREASE as placeholder
                target_bitrate=0,
            )
        )


def _inter_group_delay_variation(a: ArrivalGroup, b: ArrivalGroup) -> float:
    """
    Calculate inter-group delay variation.

    This is the core GCC formula: d(i) = (t(i) - t(i-1)) - (T(i) - T(i-1))

    Direct port of pion's interGroupDelayVariation function.
    Reference: slope_estimator.go lines 55-57

    Args:
        a: Previous arrival group
        b: Current arrival group

    Returns:
        Delay variation in seconds
    """
    return (b.arrival - a.arrival) - (b.departure - a.departure)
