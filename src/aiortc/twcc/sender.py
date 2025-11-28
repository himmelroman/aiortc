"""
TWCC sender-side implementation.

Handles TWCC feedback parsing and sent packet tracking.
Based on Pion's interceptor/pkg/cc/twcc implementation.
"""

import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .receiver import (
    LARGE_DELTA_US,
    PACKET_NOT_RECEIVED,
    PACKET_RECEIVED_LARGE_DELTA,
    PACKET_RECEIVED_SMALL_DELTA,
    REFERENCE_TIME_US,
    RUN_LENGTH_CHUNK,
    SMALL_DELTA_US,
    STATUS_VECTOR_CHUNK,
)


@dataclass
class PacketResult:
    """Result from parsing a single packet's status."""
    sequence_number: int
    received: bool
    delta_us: Optional[int] = None  # Delta from previous packet (if received)


@dataclass
class SentPacketInfo:
    """Information about a sent packet."""
    sequence_number: int
    size: int  # Bytes
    send_time_us: int  # Microseconds since epoch
    ssrc: int  # RTP SSRC


class SentPacketTracker:
    """
    Tracks sent packets for correlation with TWCC feedback.

    Maps transport sequence numbers to sent packet info.
    """

    def __init__(self, max_size: int = 5000) -> None:
        self._packets: Dict[int, SentPacketInfo] = {}
        self._max_size = max_size
        self._lock = threading.Lock()
        self._min_seq: Optional[int] = None  # Track minimum sequence number

    def add(self, seq: int, size: int, ssrc: int) -> None:
        """Record a sent packet."""
        send_time_us = int(time.time() * 1_000_000)

        with self._lock:
            self._packets[seq] = SentPacketInfo(
                sequence_number=seq,
                size=size,
                send_time_us=send_time_us,
                ssrc=ssrc
            )

            # Update minimum sequence number
            if self._min_seq is None or seq < self._min_seq:
                self._min_seq = seq

            # Clean up old entries - O(1) instead of O(n)
            if len(self._packets) > self._max_size:
                if self._min_seq is not None and self._min_seq in self._packets:
                    del self._packets[self._min_seq]
                    # Find next minimum (should be min_seq + 1 usually, but handle gaps)
                    # Since packets arrive mostly in order, try sequential search first
                    next_seq = self._min_seq + 1
                    while next_seq not in self._packets and next_seq < seq:
                        next_seq += 1
                    self._min_seq = next_seq if next_seq in self._packets else None

    def get(self, seq: int) -> Optional[SentPacketInfo]:
        """Get info for a sent packet."""
        with self._lock:
            return self._packets.get(seq)

    def get_range(self, start_seq: int, end_seq: int) -> List[SentPacketInfo]:
        """Get all sent packets in a sequence range."""
        with self._lock:
            result = []
            for seq in range(start_seq, end_seq + 1):
                packet = self._packets.get(seq)
                if packet is not None:
                    result.append(packet)
            return result

    def clear_before(self, seq: int) -> None:
        """Remove all entries before a given sequence number."""
        with self._lock:
            to_remove = [s for s in self._packets.keys() if s < seq]
            for s in to_remove:
                del self._packets[s]


class PacketChunkDecoder:
    """
    Decodes TWCC packet status chunks.

    Parses run-length and status vector chunks from RTCP feedback.
    """

    @staticmethod
    def decode_run_length(chunk_bytes: bytes) -> List[int]:
        """Decode a run-length chunk."""
        if len(chunk_bytes) < 2:
            raise ValueError("Chunk must be at least 2 bytes")

        chunk = int.from_bytes(chunk_bytes[:2], byteorder='big')

        # Extract fields
        chunk_type = (chunk >> 15) & 0x1
        if chunk_type != RUN_LENGTH_CHUNK:
            raise ValueError("Not a run-length chunk")

        symbol = (chunk >> 13) & 0x3
        run_length = chunk & 0x1FFF

        return [symbol] * run_length

    @staticmethod
    def decode_status_vector(chunk_bytes: bytes) -> List[int]:
        """Decode a status vector chunk."""
        if len(chunk_bytes) < 2:
            raise ValueError("Chunk must be at least 2 bytes")

        chunk = int.from_bytes(chunk_bytes[:2], byteorder='big')

        # Extract fields
        chunk_type = (chunk >> 15) & 0x1
        if chunk_type != STATUS_VECTOR_CHUNK:
            raise ValueError("Not a status vector chunk")

        symbol_size = ((chunk >> 14) & 0x1) + 1  # 0 -> 1 bit, 1 -> 2 bits
        status_bits = chunk & 0x3FFF

        # Extract symbols
        symbols = []
        symbols_per_chunk = 14 if symbol_size == 1 else 7

        for i in range(symbols_per_chunk):
            if symbol_size == 1:
                symbol = (status_bits >> (13 - i)) & 0x1
            else:  # symbol_size == 2
                symbol = (status_bits >> (13 - i * 2)) & 0x3

            symbols.append(symbol)

        return symbols

    @staticmethod
    def decode_chunks(chunk_bytes: bytes, packet_count: int) -> List[int]:
        """
        Decode all chunks and return packet statuses.

        Args:
            chunk_bytes: Encoded chunk data
            packet_count: Expected number of packet statuses

        Returns:
            List of packet status symbols
        """
        statuses = []
        offset = 0

        while len(statuses) < packet_count and offset < len(chunk_bytes):
            if offset + 2 > len(chunk_bytes):
                break

            chunk = int.from_bytes(chunk_bytes[offset:offset + 2], byteorder='big')
            chunk_type = (chunk >> 15) & 0x1

            if chunk_type == RUN_LENGTH_CHUNK:
                chunk_statuses = PacketChunkDecoder.decode_run_length(
                    chunk_bytes[offset:offset + 2]
                )
            else:  # STATUS_VECTOR_CHUNK
                chunk_statuses = PacketChunkDecoder.decode_status_vector(
                    chunk_bytes[offset:offset + 2]
                )

            statuses.extend(chunk_statuses)
            offset += 2

        # Trim to exact packet count
        return statuses[:packet_count]


class ReceiveDeltaDecoder:
    """
    Decodes receive deltas from TWCC feedback.

    Converts delta-encoded timestamps back to microsecond values.
    """

    @staticmethod
    def decode_deltas(
        delta_bytes: bytes,
        statuses: List[int],
        reference_time_us: int
    ) -> List[Tuple[int, int]]:
        """
        Decode receive deltas.

        Args:
            delta_bytes: Encoded delta data
            statuses: Packet status symbols
            reference_time_us: Reference time in microseconds

        Returns:
            List of (sequence_index, arrival_time_us) tuples
        """
        results = []
        offset = 0
        current_time = reference_time_us

        for i, status in enumerate(statuses):
            if status == PACKET_NOT_RECEIVED:
                continue

            if status == PACKET_RECEIVED_SMALL_DELTA:
                # 1-byte small delta
                if offset >= len(delta_bytes):
                    break

                delta_units = delta_bytes[offset]
                # Handle negative deltas (wrap-around)
                if delta_units > 127:
                    delta_units = delta_units - 256
                delta_us = delta_units * SMALL_DELTA_US
                offset += 1

            elif status == PACKET_RECEIVED_LARGE_DELTA:
                # 2-byte large delta
                if offset + 2 > len(delta_bytes):
                    break

                delta_units = int.from_bytes(
                    delta_bytes[offset:offset + 2],
                    byteorder='big',
                    signed=True
                )
                delta_us = delta_units * LARGE_DELTA_US
                offset += 2

            else:
                # Unknown status, skip
                continue

            current_time += delta_us
            results.append((i, current_time))

        return results


class TWCCParser:
    """
    Parses TWCC feedback packets.

    Main component for sender-side TWCC implementation.
    Converts RTCP feedback into packet arrival information.
    """

    @staticmethod
    def parse_feedback(rtcp_data: bytes) -> Optional[List[PacketResult]]:
        """
        Parse TWCC feedback packet.

        Args:
            rtcp_data: Raw RTCP packet data

        Returns:
            List of PacketResult objects or None if parsing fails
        """
        if len(rtcp_data) < 20:
            return None

        # Parse RTCP header
        version = (rtcp_data[0] >> 6) & 0x3
        fmt = rtcp_data[0] & 0x1F
        pt = rtcp_data[1]

        if version != 2 or pt != 205 or fmt != 15:
            return None  # Not a TWCC packet

        # Parse length
        length_words = int.from_bytes(rtcp_data[2:4], byteorder='big')
        expected_length = (length_words + 1) * 4

        if len(rtcp_data) < expected_length:
            return None

        # Parse fields
        # sender_ssrc = int.from_bytes(rtcp_data[4:8], byteorder='big')
        # media_ssrc = int.from_bytes(rtcp_data[8:12], byteorder='big')
        base_seq = int.from_bytes(rtcp_data[12:14], byteorder='big')
        packet_status_count = int.from_bytes(rtcp_data[14:16], byteorder='big')
        reference_time = int.from_bytes(rtcp_data[16:19], byteorder='big')
        # fb_pkt_count = rtcp_data[19]

        reference_time_us = reference_time * REFERENCE_TIME_US

        # Parse chunks and deltas
        payload = rtcp_data[20:expected_length]

        # Split payload into chunks and deltas
        # We need to decode chunks first to know how many deltas to expect
        statuses = PacketChunkDecoder.decode_chunks(payload, packet_status_count)

        if not statuses:
            return None

        # Calculate chunk section size
        num_chunks = 0
        temp_statuses = []
        offset = 0
        while len(temp_statuses) < packet_status_count and offset + 2 <= len(payload):
            num_chunks += 1
            chunk = int.from_bytes(payload[offset:offset + 2], byteorder='big')
            chunk_type = (chunk >> 15) & 0x1

            if chunk_type == RUN_LENGTH_CHUNK:
                run_length = chunk & 0x1FFF
                temp_statuses.extend([0] * run_length)
            else:
                symbol_size = ((chunk >> 14) & 0x1) + 1
                symbols_per_chunk = 14 if symbol_size == 1 else 7
                temp_statuses.extend([0] * symbols_per_chunk)

            offset += 2

        chunk_bytes = payload[:num_chunks * 2]
        delta_bytes = payload[num_chunks * 2:]

        # Decode statuses and deltas
        statuses = PacketChunkDecoder.decode_chunks(chunk_bytes, packet_status_count)
        arrivals = ReceiveDeltaDecoder.decode_deltas(
            delta_bytes, statuses, reference_time_us
        )

        # Build results
        arrival_dict = dict(arrivals)
        results = []

        for i, status in enumerate(statuses):
            seq = (base_seq + i) & 0xFFFF

            if status == PACKET_NOT_RECEIVED:
                results.append(PacketResult(
                    sequence_number=seq,
                    received=False
                ))
            else:
                arrival_time = arrival_dict.get(i)
                if arrival_time is not None:
                    # Calculate delta from previous packet
                    delta_us = None
                    if i > 0 and i - 1 in arrival_dict:
                        delta_us = arrival_time - arrival_dict[i - 1]

                    results.append(PacketResult(
                        sequence_number=seq,
                        received=True,
                        delta_us=delta_us
                    ))

        return results


@dataclass
class PacketFeedback:
    """
    Complete feedback information for a packet.

    Combines sent and received information for bandwidth estimation.
    """
    sequence_number: int
    size: int
    send_time_us: int
    recv_time_us: int
    ssrc: int


def correlate_feedback(
    feedback_results: List[PacketResult],
    sent_tracker: SentPacketTracker
) -> List[PacketFeedback]:
    """
    Correlate TWCC feedback with sent packet information.

    Args:
        feedback_results: Parsed TWCC feedback
        sent_tracker: Tracker containing sent packet info

    Returns:
        List of PacketFeedback with complete send/receive information
    """
    correlated = []

    for result in feedback_results:
        if not result.received:
            continue

        sent_info = sent_tracker.get(result.sequence_number)
        if sent_info is None:
            continue

        # We need to calculate actual receive time from the deltas
        # This is stored in the arrival time from the decoder
        # For now, we'll use a placeholder - this needs to be properly tracked
        # In practice, the TWCCParser should return absolute timestamps

        # Skip for now - this will be properly implemented in the integration
        pass

    return correlated
