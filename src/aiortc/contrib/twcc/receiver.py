"""
TWCC receiver-side implementation.

Handles packet tracking, arrival time recording, and TWCC feedback generation.
Based on Pion's interceptor/pkg/cc/twcc implementation.
"""

import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# Packet status symbols
PACKET_NOT_RECEIVED = 0
PACKET_RECEIVED_SMALL_DELTA = 1
PACKET_RECEIVED_LARGE_DELTA = 2  # Or negative delta
PACKET_RECEIVED_WITHOUT_TIMESTAMP = 3  # Reserved

# Chunk types
RUN_LENGTH_CHUNK = 0
STATUS_VECTOR_CHUNK = 1

# Delta encoding
SMALL_DELTA_US = 250  # Microseconds per small delta unit
LARGE_DELTA_US = 1000  # Microseconds per large delta unit
MAX_SMALL_DELTA_US = 63750  # Max value for small delta (255 * 250)

# Reference time units
REFERENCE_TIME_US = 64000  # Microseconds per reference time unit


@dataclass
class PacketInfo:
    """Information about a received packet."""
    sequence_number: int
    arrival_time_us: int  # Microseconds since epoch


class SequenceNumberUnwrapper:
    """
    Unwraps 16-bit sequence numbers to 64-bit.

    Based on Pion's sequencenumber.Unwrapper.
    """

    def __init__(self) -> None:
        self._last_seq: Optional[int] = None
        self._cycles = 0
        self._lock = threading.Lock()

    def unwrap(self, seq: int) -> int:
        """Unwrap a 16-bit sequence number to 64-bit."""
        with self._lock:
            seq = seq & 0xFFFF  # Ensure 16-bit

            if self._last_seq is None:
                self._last_seq = seq
                return seq

            # Calculate difference (handling wraparound)
            diff = seq - self._last_seq
            if diff < 0:
                diff += 0x10000

            # Detect backward wrap (large positive jump means backward wrap)
            if diff > 0x8000:
                self._cycles -= 1
            # Detect forward wrap (going from high number to low number)
            elif self._last_seq > 0xC000 and seq < 0x4000:
                self._cycles += 1

            self._last_seq = seq
            return (self._cycles << 16) | seq


class TransportSequenceNumberManager:
    """
    Manages transport-wide sequence numbers for outgoing packets.

    Thread-safe sequence number generation.
    """

    def __init__(self, initial_value: int = 0) -> None:
        self._sequence_number = initial_value & 0xFFFF
        self._lock = threading.Lock()

    def next(self) -> int:
        """Get next sequence number (thread-safe)."""
        with self._lock:
            current = self._sequence_number
            self._sequence_number = (self._sequence_number + 1) & 0xFFFF
            return current


class ArrivalTimeMap:
    """
    Stores arrival times for received packets.

    Maps transport sequence numbers to arrival times.
    Automatically cleans up old entries to prevent unbounded growth.
    """

    def __init__(self, max_size: int = 1000) -> None:
        self._arrivals: Dict[int, int] = {}  # seq -> arrival_time_us
        self._max_size = max_size
        self._lock = threading.Lock()

    def add(self, seq: int, arrival_time_us: int) -> None:
        """Record arrival time for a packet."""
        with self._lock:
            self._arrivals[seq] = arrival_time_us

            # Clean up old entries if we exceed max size
            if len(self._arrivals) > self._max_size:
                # Remove oldest entries (assuming sequential sequence numbers)
                min_seq = min(self._arrivals.keys())
                del self._arrivals[min_seq]

    def get(self, seq: int) -> Optional[int]:
        """Get arrival time for a packet."""
        with self._lock:
            return self._arrivals.get(seq)

    def get_range(self, start_seq: int, end_seq: int) -> List[Tuple[int, int]]:
        """Get all arrival times in a sequence range."""
        with self._lock:
            result = []
            for seq in range(start_seq, end_seq + 1):
                arrival_time = self._arrivals.get(seq)
                if arrival_time is not None:
                    result.append((seq, arrival_time))
            return result

    def clear_before(self, seq: int) -> None:
        """Remove all entries before a given sequence number."""
        with self._lock:
            to_remove = [s for s in self._arrivals.keys() if s < seq]
            for s in to_remove:
                del self._arrivals[s]


class PacketChunkEncoder:
    """
    Encodes packet status into run-length or status vector chunks.

    Based on draft-holmer-rmcat-transport-wide-cc-extensions.
    """

    @staticmethod
    def encode_run_length(symbol: int, run_length: int) -> bytes:
        """
        Encode a run-length chunk.

        Format (16 bits):
        - 1 bit: chunk type (0 for run-length)
        - 2 bits: packet status symbol
        - 13 bits: run length
        """
        if run_length > 0x1FFF:  # 13-bit max
            raise ValueError(f"Run length {run_length} exceeds maximum 8191")

        chunk = (RUN_LENGTH_CHUNK << 15) | (symbol << 13) | run_length
        return chunk.to_bytes(2, byteorder='big')

    @staticmethod
    def encode_status_vector(symbols: List[int], symbol_size: int = 1) -> bytes:
        """
        Encode a status vector chunk.

        Args:
            symbols: List of packet status symbols
            symbol_size: 1 for 1-bit symbols, 2 for 2-bit symbols

        Format (16 bits):
        - 1 bit: chunk type (1 for status vector)
        - 1 bit: symbol size (0 for 1-bit, 1 for 2-bit)
        - 14 bits: status symbols
        """
        if symbol_size not in (1, 2):
            raise ValueError("Symbol size must be 1 or 2")

        symbols_per_chunk = 14 if symbol_size == 1 else 7
        if len(symbols) > symbols_per_chunk:
            raise ValueError(f"Too many symbols for {symbol_size}-bit encoding")

        # Pack symbols into 14 bits
        status_bits = 0
        for i, symbol in enumerate(symbols):
            if symbol_size == 1:
                status_bits |= (symbol & 0x1) << (13 - i)
            else:  # symbol_size == 2
                status_bits |= (symbol & 0x3) << (13 - i * 2)

        chunk = (STATUS_VECTOR_CHUNK << 15) | ((symbol_size - 1) << 14) | status_bits
        return chunk.to_bytes(2, byteorder='big')

    @staticmethod
    def encode_chunks(statuses: List[int]) -> bytes:
        """
        Encode a list of packet statuses into chunks.

        Automatically chooses between run-length and status vector encoding.
        """
        if not statuses:
            return b''

        chunks = []
        i = 0

        while i < len(statuses):
            status = statuses[i]

            # Count run length
            run_length = 1
            while i + run_length < len(statuses) and statuses[i + run_length] == status:
                run_length += 1

            # Use run-length encoding for runs >= 7 (or if it fits better)
            if run_length >= 7 or (run_length > 2 and i + run_length >= len(statuses)):
                # Split into multiple chunks if needed
                original_run_length = run_length
                while run_length > 0:
                    chunk_run = min(run_length, 0x1FFF)
                    chunks.append(PacketChunkEncoder.encode_run_length(status, chunk_run))
                    run_length -= chunk_run
                i += original_run_length
            else:
                # Use status vector encoding for short runs or mixed statuses
                # Determine if we need 1-bit or 2-bit symbols
                end = min(i + 14, len(statuses))
                symbols = statuses[i:end]

                # Check if all symbols fit in 1-bit (only 0 and 1)
                if all(s in (0, 1) for s in symbols):
                    chunks.append(PacketChunkEncoder.encode_status_vector(symbols, 1))
                    i = end
                else:
                    # Use 2-bit symbols
                    end = min(i + 7, len(statuses))
                    symbols = statuses[i:end]
                    chunks.append(PacketChunkEncoder.encode_status_vector(symbols, 2))
                    i = end

        return b''.join(chunks)


class ReceiveDeltaEncoder:
    """
    Encodes receive deltas for TWCC feedback.

    Converts microsecond timestamps to delta-encoded format.
    """

    @staticmethod
    def encode_deltas(
        packets: List[Tuple[int, int]],
        reference_time_us: int
    ) -> Tuple[bytes, List[int]]:
        """
        Encode packet arrival times as deltas.

        Args:
            packets: List of (sequence_number, arrival_time_us) tuples
            reference_time_us: Reference time in microseconds

        Returns:
            Tuple of (encoded_deltas_bytes, packet_statuses)
        """
        if not packets:
            return b'', []

        deltas = []
        statuses = []
        last_time = reference_time_us

        for seq, arrival_time in packets:
            delta_us = arrival_time - last_time

            # Encode delta
            if abs(delta_us) <= MAX_SMALL_DELTA_US:
                # Small delta: 1 byte, 250us resolution
                delta_units = round(delta_us / SMALL_DELTA_US)
                # Handle negative deltas for small encoding
                if delta_units < 0:
                    delta_units += 256
                deltas.append(delta_units.to_bytes(1, byteorder='big', signed=False))
                statuses.append(PACKET_RECEIVED_SMALL_DELTA)
            else:
                # Large delta: 2 bytes, 1000us resolution
                delta_units = round(delta_us / LARGE_DELTA_US)
                deltas.append(delta_units.to_bytes(2, byteorder='big', signed=True))
                statuses.append(PACKET_RECEIVED_LARGE_DELTA)

            last_time = arrival_time

        return b''.join(deltas), statuses


class TWCCRecorder:
    """
    Records TWCC information for received packets.

    Main component for receiver-side TWCC implementation.
    Tracks packet arrivals and generates TWCC feedback reports.
    """

    def __init__(self, media_ssrc: int) -> None:
        """
        Initialize TWCC recorder.

        Args:
            media_ssrc: SSRC of the media stream (used for RTCP sender)
        """
        self.media_ssrc = media_ssrc
        self._arrival_times = ArrivalTimeMap(max_size=5000)
        self._unwrapper = SequenceNumberUnwrapper()
        self._lock = threading.Lock()

        # Feedback state
        self._feedback_count = 0
        self._base_seq: Optional[int] = None
        self._last_feedback_seq: Optional[int] = None

    def record_packet(self, transport_seq: int) -> None:
        """
        Record the arrival of a packet with TWCC sequence number.

        Args:
            transport_seq: Transport-wide sequence number (16-bit)
        """
        arrival_time_us = int(time.time() * 1_000_000)
        unwrapped_seq = self._unwrapper.unwrap(transport_seq)

        with self._lock:
            self._arrival_times.add(unwrapped_seq, arrival_time_us)

            if self._base_seq is None:
                self._base_seq = unwrapped_seq

    def generate_feedback(self) -> Optional[bytes]:
        """
        Generate TWCC feedback report.

        Returns RTCP TWCC feedback packet data or None if no data available.
        """
        with self._lock:
            if self._base_seq is None:
                return None

            # Determine sequence range for this feedback
            if self._last_feedback_seq is None:
                start_seq = self._base_seq
            else:
                start_seq = self._last_feedback_seq + 1

            # Get all packets we've received
            # For now, we'll feedback up to 100 packets at a time
            end_seq = start_seq + 100

            packets = self._arrival_times.get_range(start_seq, end_seq)
            if not packets:
                return None

            # Build packet status list (including gaps)
            packet_dict = dict(packets)
            max_seq = max(seq for seq, _ in packets)

            statuses = []
            received_packets = []

            for seq in range(start_seq, max_seq + 1):
                if seq in packet_dict:
                    received_packets.append((seq, packet_dict[seq]))
                    # Status will be determined during delta encoding
                    statuses.append(PACKET_RECEIVED_SMALL_DELTA)  # Placeholder
                else:
                    statuses.append(PACKET_NOT_RECEIVED)

            if not received_packets:
                return None

            # Encode deltas and get actual statuses
            first_arrival = received_packets[0][1]
            reference_time = (first_arrival // REFERENCE_TIME_US) * REFERENCE_TIME_US
            reference_time_24bit = (reference_time // REFERENCE_TIME_US) & 0xFFFFFF

            delta_bytes, delta_statuses = ReceiveDeltaEncoder.encode_deltas(
                received_packets, reference_time
            )

            # Update statuses with actual delta types
            status_idx = 0
            for i, status in enumerate(statuses):
                if status != PACKET_NOT_RECEIVED:
                    statuses[i] = delta_statuses[status_idx]
                    status_idx += 1

            # Encode packet status chunks
            chunk_bytes = PacketChunkEncoder.encode_chunks(statuses)

            # Build TWCC feedback packet
            feedback_data = self._build_rtcp_packet(
                base_seq=start_seq & 0xFFFF,
                packet_status_count=len(statuses),
                reference_time=reference_time_24bit,
                chunk_bytes=chunk_bytes,
                delta_bytes=delta_bytes
            )

            # Update state
            self._last_feedback_seq = max_seq
            self._feedback_count = (self._feedback_count + 1) & 0xFF

            # Clean up old arrival times
            self._arrival_times.clear_before(start_seq)

            return feedback_data

    def _build_rtcp_packet(
        self,
        base_seq: int,
        packet_status_count: int,
        reference_time: int,
        chunk_bytes: bytes,
        delta_bytes: bytes
    ) -> bytes:
        """
        Build RTCP transport-cc feedback packet.

        Format per draft-holmer-rmcat-transport-wide-cc-extensions:

         0                   1                   2                   3
         0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
        +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
        |V=2|P|  FMT=15 |    PT=205     |           length              |
        +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
        |                     SSRC of packet sender                     |
        +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
        |                      SSRC of media source                     |
        +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
        |      base sequence number     |      packet status count      |
        +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
        |                 reference time                | fb pkt. count |
        +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
        |          packet chunk         |         packet chunk          |
        +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
        .                                                               .
        .                                                               .
        +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
        |         packet chunk          |  recv delta   |  recv delta   |
        +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
        .                                                               .
        .                                                               .
        +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
        |           recv delta          |  recv delta   | zero padding  |
        +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
        """
        # RTCP header
        version = 2
        padding = 0
        fmt = 15  # Transport-wide feedback message
        pt = 205  # Generic RTP Feedback

        # Calculate length (in 32-bit words minus 1)
        payload = chunk_bytes + delta_bytes
        # Ensure 32-bit alignment
        padding_needed = (4 - (len(payload) % 4)) % 4
        payload += b'\x00' * padding_needed

        # Length includes: header + sender_ssrc + media_ssrc + base_seq/count + ref_time/fb_count + payload
        # RTCP length is in 32-bit words, including header, minus 1
        length_words = (4 + 4 + 4 + 4 + 4 + len(payload)) // 4 - 1

        packet = bytearray()

        # Byte 0: V, P, FMT
        packet.append((version << 6) | (padding << 5) | fmt)

        # Byte 1: PT
        packet.append(pt)

        # Bytes 2-3: Length
        packet.extend(length_words.to_bytes(2, byteorder='big'))

        # Bytes 4-7: SSRC of packet sender (use media SSRC)
        packet.extend(self.media_ssrc.to_bytes(4, byteorder='big'))

        # Bytes 8-11: SSRC of media source (use media SSRC)
        packet.extend(self.media_ssrc.to_bytes(4, byteorder='big'))

        # Bytes 12-13: Base sequence number
        packet.extend(base_seq.to_bytes(2, byteorder='big'))

        # Bytes 14-15: Packet status count
        packet.extend(packet_status_count.to_bytes(2, byteorder='big'))

        # Bytes 16-18: Reference time (24 bits)
        packet.extend(reference_time.to_bytes(3, byteorder='big'))

        # Byte 19: Feedback packet count
        packet.append(self._feedback_count)

        # Packet chunks and receive deltas
        packet.extend(payload)

        return bytes(packet)
