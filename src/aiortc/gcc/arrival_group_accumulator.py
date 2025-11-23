"""
Arrival group accumulator for GCC delay-based estimation.

Direct port from pion's pkg/gcc/arrival_group_accumulator.go
"""

import logging
from typing import Callable, List, Optional

from .acknowledgment import Acknowledgment
from .arrival_group import ArrivalGroup

logger = logging.getLogger(__name__)

# Module-level counters for debugging (matching pion's global vars, lines 14-15)
_acks_received = 0
_groups_formed = 0


def _inter_arrival_time_pkt(group: ArrivalGroup, ack: Acknowledgment) -> float:
    """
    Calculate inter-arrival time between group and acknowledgment.

    Direct port of pion's interArrivalTimePkt function.
    Reference: arrival_group_accumulator.go lines 96-98

    Args:
        group: Current arrival group
        ack: Acknowledgment to check

    Returns:
        Time delta in seconds
    """
    return ack.arrival - group.arrival


def _inter_departure_time_pkt(group: ArrivalGroup, ack: Acknowledgment) -> float:
    """
    Calculate inter-departure time between group and acknowledgment.

    Direct port of pion's interDepartureTimePkt function.
    Reference: arrival_group_accumulator.go lines 100-106

    Args:
        group: Current arrival group
        ack: Acknowledgment to check

    Returns:
        Time delta in seconds
    """
    if len(group.packets) == 0:
        return 0.0

    return ack.departure - group.departure


def _inter_group_delay_variation_pkt(group: ArrivalGroup, ack: Acknowledgment) -> float:
    """
    Calculate inter-group delay variation.

    This is the core GCC formula: d(i) = (t(i) - t(group)) - (T(i) - T(group))

    Direct port of pion's interGroupDelayVariationPkt function.
    Reference: arrival_group_accumulator.go lines 108-110

    Args:
        group: Current arrival group
        ack: Acknowledgment to check

    Returns:
        Delay variation in seconds
    """
    return (ack.arrival - group.arrival) - (ack.departure - group.departure)


class ArrivalGroupAccumulator:
    """
    Groups acknowledgments into bursts for GCC delay-based estimation.

    Direct port of pion's arrivalGroupAccumulator.
    Reference: arrival_group_accumulator.go lines 17-94
    """

    # Threshold constants (matching pion lines 18-20, 25-27)
    INTER_DEPARTURE_THRESHOLD = 0.005  # 5ms in seconds
    INTER_ARRIVAL_THRESHOLD = 0.005  # 5ms in seconds
    INTER_GROUP_DELAY_VARIATION_THRESHOLD = 0.0  # 0ms

    def __init__(self):
        """
        Create new arrival group accumulator.

        Direct port of pion's newArrivalGroupAccumulator.
        Reference: arrival_group_accumulator.go lines 23-29
        """
        self.inter_departure_threshold = self.INTER_DEPARTURE_THRESHOLD
        self.inter_arrival_threshold = self.INTER_ARRIVAL_THRESHOLD
        self.inter_group_delay_variation_threshold = self.INTER_GROUP_DELAY_VARIATION_THRESHOLD

        # Internal state for run()
        self._initialized = False
        self._current_group: Optional[ArrivalGroup] = None
        self._dropped_out_of_order = 0
        self._dropped_departure = 0

    def process_acknowledgments(
        self,
        acks: List[Acknowledgment],
        on_group: Callable[[ArrivalGroup], None],
    ) -> None:
        """
        Process acknowledgments and emit arrival groups.

        Direct port of pion's run method.
        Reference: arrival_group_accumulator.go lines 31-94

        Args:
            acks: List of acknowledgments to process
            on_group: Callback for completed groups (agWriter in pion)
        """
        global _acks_received, _groups_formed

        # Sort acknowledgments by arrival time to handle TWCC packets with negative deltas
        # (which are valid per spec for out-of-order packet arrivals)
        # Reference: lines 38-42
        acks = sorted(acks, key=lambda a: a.arrival)

        # Debug logging (matching pion lines 44-48)
        _acks_received += len(acks)
        if len(acks) > 0 and _acks_received % 100 < len(acks):
            logger.debug(
                f"🔧 DEBUG ArrivalGroupAccumulator: received {len(acks)} acks "
                f"(total: {_acks_received}, groups: {_groups_formed}, "
                f"dropped: order={self._dropped_out_of_order}, depart={self._dropped_departure})"
            )

        # Process each acknowledgment (lines 50-92)
        for next_ack in acks:
            # Initialize first group (lines 51-56)
            if not self._initialized:
                self._current_group = ArrivalGroup.create(next_ack)
                self._initialized = True
                logger.debug("🔧 DEBUG ArrivalGroupAccumulator: initialized first group")
                continue

            # Handle out-of-order arrivals (lines 57-64)
            # New batch with earlier timestamps - write current group and start fresh
            # This handles TWCC negative deltas (out-of-order arrivals per spec)
            if next_ack.arrival < self._current_group.arrival:
                on_group(self._current_group)
                _groups_formed += 1
                self._current_group = ArrivalGroup.create(next_ack)
                continue

            # Only process packets with increasing departure times (line 65)
            if next_ack.departure > self._current_group.departure:
                # Rule 1: A sequence of packets which are sent within a burst_time interval
                # constitute a group (lines 66-71)
                if (
                    _inter_departure_time_pkt(self._current_group, next_ack)
                    <= self.inter_departure_threshold
                ):
                    self._current_group.add(next_ack)
                    continue

                # Rule 2: A Packet which has an inter-arrival time less than burst_time and
                # an inter-group delay variation d(i) less than 0 is considered
                # being part of the current group of packets (lines 73-80)
                if (
                    _inter_arrival_time_pkt(self._current_group, next_ack)
                    <= self.inter_arrival_threshold
                    and _inter_group_delay_variation_pkt(self._current_group, next_ack)
                    < self.inter_group_delay_variation_threshold
                ):
                    self._current_group.add(next_ack)
                    continue

                # Packet belongs to new group (lines 82-84)
                on_group(self._current_group)
                _groups_formed += 1
                self._current_group = ArrivalGroup.create(next_ack)
            else:
                # Packet sent earlier than current group departure - drop it (lines 85-91)
                # This handles out-of-order sends or retransmissions
                self._dropped_departure += 1
                if self._dropped_departure <= 5:
                    logger.debug(
                        f"🔧 DEBUG Drop: next.Departure={next_ack.departure}, "
                        f"group.departure={self._current_group.departure}, "
                        f"next.Arrival={next_ack.arrival}, "
                        f"group.arrival={self._current_group.arrival}"
                    )
