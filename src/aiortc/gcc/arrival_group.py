"""
Arrival group for GCC delay-based estimation.

Direct port from pion's pkg/gcc/arrival_group.go
"""

from dataclasses import dataclass, field
from typing import List

from .acknowledgment import Acknowledgment


@dataclass
class ArrivalGroup:
    """
    ArrivalGroup holds packets that were sent/received in a burst.

    Direct port of pion's arrivalGroup struct.
    Reference: arrival_group.go lines 13-17
    """

    packets: List[Acknowledgment] = field(default_factory=list)
    departure: float = 0.0  # Send time of last packet (time.Time in Go)
    arrival: float = 0.0  # Arrival time of last packet (time.Time in Go)

    @classmethod
    def create(cls, ack: Acknowledgment) -> "ArrivalGroup":
        """
        Create new arrival group with initial acknowledgment.

        Direct port of pion's newArrivalGroup function.
        Reference: arrival_group.go lines 19-25
        """
        return cls(
            packets=[ack],
            departure=ack.departure,
            arrival=ack.arrival,
        )

    def add(self, ack: Acknowledgment) -> None:
        """
        Add acknowledgment to group.

        Updates arrival time to latest packet.
        Direct port of pion's add method.
        Reference: arrival_group.go lines 27-30
        """
        self.packets.append(ack)
        self.arrival = ack.arrival

    def __str__(self) -> str:
        """
        String representation matching pion's format.

        Reference: arrival_group.go lines 32-39
        """
        # Convert to milliseconds since epoch (matching pion's output)
        arrival_ms = int(self.arrival * 1000) if self.arrival > 0 else 0
        departure_ms = int(self.departure * 1000) if self.departure > 0 else 0

        s = "ARRIVALGROUP:\n"
        s += f"\tARRIVAL:\t{arrival_ms}\n"
        s += f"\tDEPARTURE:\t{departure_ms}\n"
        s += f"\tPACKETS:\n{self.packets}\n"

        return s
