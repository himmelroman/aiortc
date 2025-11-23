"""
GCC types and enums.

Direct port from pion's pkg/gcc package.
"""

from dataclasses import dataclass
from enum import IntEnum


class BandwidthUsage(IntEnum):
    """
    Bandwidth usage classification.

    Direct port of pion's usage enum.
    Reference: usage.go lines 8-14
    """

    OVER = 0  # usageOver (line 11)
    UNDER = 1  # usageUnder (line 12)
    NORMAL = 2  # usageNormal (line 13)

    def __str__(self) -> str:
        """String representation matching pion (lines 16-27)."""
        if self == BandwidthUsage.OVER:
            return "overuse"
        elif self == BandwidthUsage.UNDER:
            return "underuse"
        elif self == BandwidthUsage.NORMAL:
            return "normal"
        else:
            return f"invalid usage: {self.value}"


class RateControlState(IntEnum):
    """
    Rate control state.

    Direct port of pion's state enum.
    Reference: state.go lines 8-14
    """

    INCREASE = 0  # stateIncrease (line 11)
    DECREASE = 1  # stateDecrease (line 12)
    HOLD = 2  # stateHold (line 13)

    def __str__(self) -> str:
        """String representation matching pion (lines 53-64)."""
        if self == RateControlState.INCREASE:
            return "increase"
        elif self == RateControlState.DECREASE:
            return "decrease"
        elif self == RateControlState.HOLD:
            return "hold"
        else:
            return f"invalid state: {self.value}"

    def transition(self, usage: BandwidthUsage) -> "RateControlState":
        """
        State transition based on bandwidth usage.

        Direct port of pion's transition function.
        Reference: state.go lines 17-51

        Args:
            usage: Current bandwidth usage

        Returns:
            Next state
        """
        # Lines 18-27: HOLD transitions
        if self == RateControlState.HOLD:
            if usage == BandwidthUsage.OVER:
                return RateControlState.DECREASE  # line 22
            elif usage == BandwidthUsage.NORMAL:
                return RateControlState.INCREASE  # line 24
            elif usage == BandwidthUsage.UNDER:
                return RateControlState.HOLD  # line 26

        # Lines 29-37: INCREASE transitions
        elif self == RateControlState.INCREASE:
            if usage == BandwidthUsage.OVER:
                return RateControlState.DECREASE  # line 32
            elif usage == BandwidthUsage.NORMAL:
                return RateControlState.INCREASE  # line 34
            elif usage == BandwidthUsage.UNDER:
                return RateControlState.HOLD  # line 36

        # Lines 39-47: DECREASE transitions
        elif self == RateControlState.DECREASE:
            if usage == BandwidthUsage.OVER:
                return RateControlState.DECREASE  # line 42
            elif usage == BandwidthUsage.NORMAL:
                return RateControlState.HOLD  # line 44
            elif usage == BandwidthUsage.UNDER:
                return RateControlState.HOLD  # line 46

        # Line 50: Default fallback
        return RateControlState.INCREASE


@dataclass
class DelayStats:
    """
    Statistics from delay-based estimation pipeline.

    Direct port of pion's DelayStats struct.
    Reference: delay_based_bwe.go lines 16-25
    """

    measurement: float  # time.Duration (line 17), in seconds
    estimate: float  # time.Duration (line 18), in seconds
    threshold: float  # time.Duration (line 19), in seconds
    last_receive_delta: float  # time.Duration (line 20), in seconds

    usage: BandwidthUsage  # usage (line 22)
    state: RateControlState  # state (line 23)
    target_bitrate: int  # int (line 24), in bits per second
