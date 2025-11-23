"""
Congestion control acknowledgment model.

Direct port from pion's internal/cc/acknowledgment.go
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class Acknowledgment:
    """
    Acknowledgment holds information about a packet and if/when it has been
    sent/received.

    Direct port of pion's cc.Acknowledgment struct.
    Reference: internal/cc/acknowledgment.go lines 15-22
    """

    sequence_number: int  # Either RTP SequenceNumber or TWCC (uint16 in Go)
    ssrc: int  # uint32 in Go
    size: int  # Packet size in bytes
    departure: float  # Send time (seconds since epoch, time.Time in Go)
    arrival: float  # Receive time (seconds since epoch, time.Time in Go, zero if lost)

    def __str__(self) -> str:
        """
        String representation matching pion's format.

        Reference: acknowledgment.go lines 24-32
        """
        # Convert to milliseconds since epoch (matching pion's output)
        departure_ms = int(self.departure * 1000) if self.departure > 0 else 0
        arrival_ms = int(self.arrival * 1000) if self.arrival > 0 else 0

        s = "ACK:\n"
        s += f"\tTWCC:\t{self.sequence_number}\n"
        s += f"\tSIZE:\t{self.size}\n"
        s += f"\tDEPARTURE:\t{departure_ms}\n"
        s += f"\tARRIVAL:\t{arrival_ms}\n"

        return s

    def is_lost(self) -> bool:
        """Check if packet was lost (arrival time is zero)."""
        return self.arrival == 0.0


# Constant for TWCC extension attributes (matches pion's const)
# Reference: feedback_adapter.go line 12
TWCC_EXTENSION_ATTRIBUTES_KEY = "twcc_extension_id"
