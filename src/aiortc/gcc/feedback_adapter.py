"""
Feedback adapter for GCC.

Converts TWCC feedback into GCC Acknowledgments.
Port of pion's internal/cc/feedback_adapter.go
"""

import logging
import threading
import time
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

from ..twcc.sender import PacketResult, SentPacketInfo, TWCCParser
from .acknowledgment import Acknowledgment

logger = logging.getLogger(__name__)


class FeedbackHistory:
    """
    LRU cache for sent packet information.

    Matches pion's feedbackHistory implementation.
    Reference: internal/cc/feedback_adapter.go lines 257-314
    """

    def __init__(self, size: int = 5000):
        """
        Initialize feedback history.

        Args:
            size: Maximum number of packets to store

        Reference: lines 263-269
        """
        self._size = size
        self._items: OrderedDict[Tuple[int, int], Acknowledgment] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, ssrc: int, sequence_number: int) -> Optional[Acknowledgment]:
        """
        Get acknowledgment for a packet.

        Args:
            ssrc: RTP SSRC
            sequence_number: Transport sequence number

        Returns:
            Acknowledgment if found, None otherwise

        Reference: lines 271-280
        """
        with self._lock:
            key = (ssrc, sequence_number)
            ack = self._items.get(key)
            if ack is not None:
                # Move to front (LRU)
                self._items.move_to_end(key, last=False)
            return ack

    def add(self, ack: Acknowledgment) -> None:
        """
        Add acknowledgment to history.

        Args:
            ack: Acknowledgment to add

        Reference: lines 282-301
        """
        with self._lock:
            key = (ack.ssrc, ack.sequence_number)

            # Update if exists (move to front)
            if key in self._items:
                self._items[key] = ack
                self._items.move_to_end(key, last=False)
                return

            # Add new (at front)
            self._items[key] = ack
            self._items.move_to_end(key, last=False)

            # Evict oldest if necessary
            if len(self._items) > self._size:
                # Remove oldest (last item in OrderedDict)
                self._items.popitem(last=True)


class FeedbackAdapter:
    """
    Converts TWCC feedback into GCC Acknowledgments.

    Tracks sent packets and correlates them with TWCC feedback to produce
    the Acknowledgment format that GCC expects.

    Reference: internal/cc/feedback_adapter.go lines 28-250
    """

    def __init__(self, history_size: int = 5000):
        """
        Initialize feedback adapter.

        Args:
            history_size: Maximum number of sent packets to track

        Reference: lines 36-42
        """
        self._history = FeedbackHistory(history_size)
        self._base_departure_time: Optional[float] = None
        self._lock = threading.Lock()
        self._parser = TWCCParser()

    def on_sent(
        self,
        sequence_number: int,
        size: int,
        ssrc: int = 0,
        departure_time: Optional[float] = None
    ) -> None:
        """
        Record that a packet was sent.

        Should be called when a packet with TWCC extension is sent.

        Args:
            sequence_number: Transport-wide sequence number
            size: Packet size in bytes
            ssrc: RTP SSRC (use 0 for TWCC which ignores SSRC)
            departure_time: Send time in seconds since epoch (defaults to current time)

        Reference: lines 60-98, 102-110
        """
        if departure_time is None:
            departure_time = time.time()

        with self._lock:
            # Set base departure time on first packet
            # Reference: lines 71-77
            if self._base_departure_time is None:
                self._base_departure_time = departure_time
                logger.debug("FeedbackAdapter: Set base departure time")

            # Make departure time relative to base (starting from Unix epoch + 1s)
            # This matches how TWCC arrival times are relative
            # Reference: lines 79-80
            relative_departure = 1.0 + (departure_time - self._base_departure_time)

            ack = Acknowledgment(
                sequence_number=sequence_number,
                ssrc=ssrc,
                size=size,
                departure=relative_departure,
                arrival=0.0  # Will be filled in when feedback arrives
            )

            self._history.add(ack)

            # Debug logging
            if sequence_number % 100 == 0:
                logger.debug(
                    f"FeedbackAdapter: Recorded TWCC seq={sequence_number}, "
                    f"departure={relative_departure:.6f}s, size={size}"
                )

    def on_transport_cc_feedback(
        self,
        rtcp_data: bytes
    ) -> Optional[List[Acknowledgment]]:
        """
        Convert TWCC feedback into acknowledgments.

        Parses the RTCP feedback packet and correlates it with sent packet history
        to produce complete Acknowledgments with both departure and arrival times.

        Args:
            rtcp_data: Raw RTCP TWCC feedback packet

        Returns:
            List of Acknowledgments or None if parsing fails

        Reference: lines 178-221
        """
        # Parse feedback packet
        feedback_results = self._parser.parse_feedback(rtcp_data)
        if feedback_results is None:
            logger.warning("FeedbackAdapter: Failed to parse TWCC feedback")
            return None

        with self._lock:
            # Extract base sequence and reference time from RTCP packet
            # Reference: lines 186-188
            if len(rtcp_data) < 20:
                return None

            base_seq = int.from_bytes(rtcp_data[12:14], byteorder='big')
            reference_time_24bit = int.from_bytes(rtcp_data[16:19], byteorder='big')
            # Reference time in seconds (64ms units)
            reference_time = (reference_time_24bit * 64000) / 1_000_000.0

            result = []
            current_arrival = reference_time

            # Process feedback results
            # Reference: lines 191-214
            for i, fb_result in enumerate(feedback_results):
                seq = fb_result.sequence_number

                # Look up sent packet in history
                ack = self._history.get(0, seq)  # SSRC is 0 for TWCC
                if ack is None:
                    # Packet not in history (could be from before tracking started)
                    continue

                # Update arrival time if packet was received
                if fb_result.received and fb_result.delta_us is not None:
                    # Convert delta from microseconds to seconds and add to current arrival
                    current_arrival += fb_result.delta_us / 1_000_000.0
                    ack = Acknowledgment(
                        sequence_number=ack.sequence_number,
                        ssrc=ack.ssrc,
                        size=ack.size,
                        departure=ack.departure,
                        arrival=current_arrival
                    )
                else:
                    # Packet was lost (arrival = 0.0)
                    ack = Acknowledgment(
                        sequence_number=ack.sequence_number,
                        ssrc=ack.ssrc,
                        size=ack.size,
                        departure=ack.departure,
                        arrival=0.0
                    )

                result.append(ack)

            # Debug logging
            logger.debug(
                f"FeedbackAdapter: Processed TWCC feedback: base_seq={base_seq}, "
                f"ref_time={reference_time:.6f}s, created {len(result)} acks"
            )

            # Log first few acks for debugging
            for i, ack in enumerate(result[:3]):
                logger.debug(
                    f"  ack[{i}]: seq={ack.sequence_number}, "
                    f"departure={ack.departure:.6f}s, arrival={ack.arrival:.6f}s, "
                    f"size={ack.size}"
                )

            return result if result else None

    def on_sent_packet_info(self, packet_info: SentPacketInfo) -> None:
        """
        Record sent packet using SentPacketInfo from aiortc's TWCC sender.

        Convenience method that wraps on_sent() for integration with
        aiortc's existing TWCC implementation.

        Args:
            packet_info: Sent packet information from TWCC tracker
        """
        # Convert microseconds to seconds
        departure_time = packet_info.send_time_us / 1_000_000.0

        self.on_sent(
            sequence_number=packet_info.sequence_number,
            size=packet_info.size,
            ssrc=packet_info.ssrc,
            departure_time=departure_time
        )
