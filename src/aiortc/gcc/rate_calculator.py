"""
Rate calculator for GCC.

Calculates the received bitrate over a sliding time window.
Port of pion's pkg/gcc/rate_calculator.go
"""

from typing import Callable, List
from .acknowledgment import Acknowledgment


class RateCalculator:
    """
    Calculates received bitrate over a sliding time window.

    Maintains a history of acknowledged packets and computes the bitrate
    as the total bits received within the window divided by the time span.

    Reference: pkg/gcc/rate_calculator.go lines 12-66
    """

    def __init__(self, window: float = 0.5):
        """
        Initialize rate calculator.

        Args:
            window: Time window in seconds (default 500ms, matches pion's 500*time.Millisecond)

        Reference: pkg/gcc/rate_calculator.go lines 16-20
        """
        self._window = window
        self._history: List[Acknowledgment] = []
        self._init = False
        self._sum = 0

    def process_acknowledgments(
        self,
        acks: List[Acknowledgment],
        on_rate_update: Callable[[int], None]
    ) -> None:
        """
        Process acknowledgments and calculate bitrate.

        Maintains state across calls, matching the behavior of pion's
        run() method that processes acknowledgments from a channel.

        Args:
            acks: List of acknowledgments to process
            on_rate_update: Callback invoked with calculated bitrate (bits/sec)

        Reference: pkg/gcc/rate_calculator.go lines 22-66
        """
        for next_ack in acks:
            # Skip lost packets (arrival time = 0.0)
            # Reference: lines 28-31
            if next_ack.arrival == 0.0:
                continue

            self._history.append(next_ack)
            self._sum += next_ack.size

            # First packet - can't calculate rate yet
            # Reference: lines 35-43
            if not self._init:
                self._init = True
                # Don't know timeframe, only arrival of last packet
                # which is by definition in the window that ends with last arrival
                on_rate_update(next_ack.size * 8)
                continue

            # Remove packets outside the window
            # Reference: lines 45-54
            deadline = next_ack.arrival - self._window
            del_count = 0
            for ack in self._history:
                if ack.arrival >= deadline:
                    break
                del_count += 1
                self._sum -= ack.size

            self._history = self._history[del_count:]

            # No packets in window
            # Reference: lines 55-59
            if len(self._history) == 0:
                on_rate_update(0)
                continue

            # Calculate bitrate over the window
            # Reference: lines 60-63
            dt = next_ack.arrival - self._history[0].arrival
            bits = 8 * self._sum
            rate = int(bits / dt) if dt > 0 else 0
            on_rate_update(rate)
