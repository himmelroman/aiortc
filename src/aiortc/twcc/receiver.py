"""
TWCC receiver-side implementation.

Handles packet tracking, arrival time recording, and TWCC feedback generation.
Based on Pion's interceptor/pkg/cc/twcc implementation.
"""

import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

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

    Maps transport sequence numbers to arrival times with time-based automatic cleanup.
    Uses OrderedDict to maintain insertion order for efficient FIFO cleanup.

    Inspired by Pion's time-window approach (500ms) and libwebrtc's RemoveOldPackets().
    """

    # Time window for packet retention (like Pion's 500ms window)
    # Packets older than this are automatically removed to prevent lag buildup
    PACKET_WINDOW_US = 500_000  # 500 milliseconds in microseconds

    def __init__(self, max_size: int = 10000) -> None:
        self._arrivals: OrderedDict[int, int] = OrderedDict()  # seq -> arrival_time_us (ordered by insertion)
        self._max_size = max_size  # Kept for backward compatibility, but time-based cleanup is primary
        self._lock = threading.Lock()

    def add(self, seq: int, arrival_time_us: int) -> None:
        """
        Record arrival time for a packet.

        Automatically removes packets older than PACKET_WINDOW_US to prevent lag buildup.
        Uses efficient FIFO cleanup from OrderedDict front.
        """
        with self._lock:
            # Update or add packet (move to end if already exists for reordering handling)
            if seq in self._arrivals:
                # Packet already recorded (reordering case) - update time and move to end
                self._arrivals.move_to_end(seq)
            self._arrivals[seq] = arrival_time_us

            # Time-based cleanup: Remove packets older than window
            # This prevents the feedback lag buildup that was causing 32-second backlogs
            cutoff_time = arrival_time_us - self.PACKET_WINDOW_US

            # Pop from front until we hit recent packets (amortized O(1))
            while self._arrivals:
                # Peek at oldest packet (first item in OrderedDict)
                oldest_seq, oldest_time = next(iter(self._arrivals.items()))
                if oldest_time >= cutoff_time:
                    # All remaining packets are recent enough
                    break
                # Remove oldest packet
                self._arrivals.popitem(last=False)

            # Safety fallback: count-based limit (should rarely trigger with time-based cleanup)
            if len(self._arrivals) > self._max_size:
                oldest_seq, oldest_time = self._arrivals.popitem(last=False)
                logger.warning(f"TWCC: Arrival map exceeded max_size ({self._max_size}), removed packet {oldest_seq}")

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
                # CRITICAL FIX: Match pion's bit layout exactly
                # Bits 13-12 for symbol 0, 11-10 for symbol 1, etc.
                status_bits |= (symbol & 0x3) << (12 - i * 2)

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

            # CRITICAL: Clamp negative deltas to 0 to prevent Chromium's GCC from throttling
            # Negative deltas happen when packets arrive out of sequence number order due
            # to network reordering. Chromium interprets negative deltas as severe congestion
            # and throttles aggressively. Better to report 0 delta than negative.
            if delta_us < 0:
                logger.debug(f"TWCC: Clamping negative delta {delta_us}us to 0 for seq={seq}")
                delta_us = 0

            # Encode delta
            # CRITICAL FIX: Small deltas are UNSIGNED in TWCC spec, so negative deltas
            # MUST use large (2-byte signed) encoding. Otherwise, wrapping -1 to 255
            # causes pion to decode it as +63.75ms instead of -250us, making times jump forward.
            if delta_us >= 0 and delta_us <= MAX_SMALL_DELTA_US:
                # Small delta: 1 byte, 250us resolution (UNSIGNED, only for non-negative)
                # Match pion's rounding: (delta + scale/2) // scale
                delta_units = (delta_us + SMALL_DELTA_US // 2) // SMALL_DELTA_US
                delta_us_rounded = delta_units * SMALL_DELTA_US
                deltas.append(delta_units.to_bytes(1, byteorder='big', signed=False))
                statuses.append(PACKET_RECEIVED_SMALL_DELTA)
            else:
                # Large delta: 2 bytes, 1000us resolution (SIGNED, for negative or large positive)
                # Match pion's rounding: (delta +/- scale/2) // scale
                if delta_us >= 0:
                    delta_units = (delta_us + LARGE_DELTA_US // 2) // LARGE_DELTA_US
                else:
                    delta_units = (delta_us - LARGE_DELTA_US // 2) // LARGE_DELTA_US
                delta_us_rounded = delta_units * LARGE_DELTA_US
                deltas.append(delta_units.to_bytes(2, byteorder='big', signed=True))
                statuses.append(PACKET_RECEIVED_LARGE_DELTA)

            # CRITICAL FIX: Accumulate using rounded delta, not actual arrival time!
            # This matches pion's implementation and prevents rounding error accumulation.
            # If we use actual arrival_time, small rounding errors compound and cause
            # reconstructed times to drift from encoded values.
            last_time += delta_us_rounded

        return b''.join(deltas), statuses


class TWCCRecorder:
    """
    Records TWCC information for received packets.

    Main component for receiver-side TWCC implementation.
    Tracks packet arrivals and generates TWCC feedback reports.
    """

    def __init__(self, sender_ssrc: int, media_ssrc: Optional[int] = None) -> None:
        """
        Initialize TWCC recorder.

        Args:
            sender_ssrc: SSRC of the RTCP packet sender (receiver sending feedback)
            media_ssrc: SSRC of the media stream (optional, will be discovered from RTP packets if not provided)
        """
        # CRITICAL: Use separate sender_ssrc and media_ssrc per RTCP spec
        # Sender SSRC = who is sending this RTCP packet (the receiver)
        # Media SSRC = what media stream this feedback is about (the sender's stream)
        # Using the same SSRC for both violates RTCP spec and may confuse Chromium's GCC
        self.sender_ssrc = sender_ssrc
        self.media_ssrc = media_ssrc  # May be None initially, will be set from RTP packets
        if media_ssrc:
            logger.info(f"✅ TWCC: Initialized with sender_ssrc=0x{sender_ssrc:08x}, media_ssrc=0x{media_ssrc:08x}")
        else:
            logger.info(f"✅ TWCC: Initialized with sender_ssrc=0x{sender_ssrc:08x}, media_ssrc will be auto-detected")
        # Use large buffer to handle high packet rates (match pion's 2^15 = 32768)
        self._arrival_times = ArrivalTimeMap(max_size=32768)
        self._unwrapper = SequenceNumberUnwrapper()
        self._lock = threading.Lock()

        # Feedback state
        self._feedback_count = 0
        self._base_seq: Optional[int] = None
        self._last_feedback_seq: Optional[int] = None
        self._last_feedback_ref_time: Optional[int] = None  # Track last reference time for cross-report monotonicity

        # CRITICAL: Use relative time (like Pion) instead of absolute epoch time
        # This ensures reference times start near 0, not huge values into the epoch
        # which would break Chromium's GCC delay calculations
        # NOTE: Must use monotonic clock (matching rtcdtlstransport.py) to ensure
        # arrival times never go backwards, which would break Chromium's GCC
        self._start_time_us = int(time.monotonic() * 1_000_000)
        logger.info(f"✅ TWCC recorder initialized with start_time={self._start_time_us}us (monotonic clock, relative timing enabled)")

        # Track last arrival time to enforce monotonicity (prevents negative deltas)
        self._last_arrival_time_us: Optional[int] = None

        # Track packet arrival times for feedback latency analysis
        self._packet_arrival_times: dict = {}  # seq -> arrival_time_us
        self._feedback_latency_stats = {
            'count': 0,
            'sum_latency_us': 0,
            'min_latency_us': float('inf'),
            'max_latency_us': 0,
            'last_report_time': 0
        }

    def set_media_ssrc(self, media_ssrc: int) -> None:
        """
        Set the media SSRC (called when first RTP packet arrives).

        Args:
            media_ssrc: SSRC of the media stream from RTP packets
        """
        if self.media_ssrc is None:
            self.media_ssrc = media_ssrc
            logger.info(f"✅ TWCC: Auto-detected media_ssrc=0x{media_ssrc:08x} (sender_ssrc=0x{self.sender_ssrc:08x})")
        elif self.media_ssrc != media_ssrc:
            logger.warning(f"⚠️  TWCC: Media SSRC changed from 0x{self.media_ssrc:08x} to 0x{media_ssrc:08x}")
            self.media_ssrc = media_ssrc

    def record_packet(self, transport_seq: int, arrival_time_us: int = None) -> None:
        """
        Record the arrival of a packet with TWCC sequence number.

        Args:
            transport_seq: Transport-wide sequence number (16-bit)
            arrival_time_us: Packet arrival time in microseconds (if None, use current time)
        """
        # Use provided arrival time or capture current time
        # This ensures we use the ACTUAL packet arrival time from the transport layer,
        # not the time after packet processing which would show artificial delay
        if arrival_time_us is None:
            arrival_time_us = time.time_ns() // 1_000

        # CRITICAL: Convert absolute time to relative time (time since recorder started)
        # This matches Pion's behavior: time.Since(s.startTime).Microseconds()
        # Without this, reference times would be ~13 hours, breaking Chromium's GCC
        relative_arrival_time_us = arrival_time_us - self._start_time_us

        unwrapped_seq = self._unwrapper.unwrap(transport_seq)

        with self._lock:
            # CRITICAL: Enforce monotonicity to prevent negative deltas
            # Async packet processing can cause packets to be recorded slightly out of order
            # Even one negative delta breaks Chromium's GCC
            if self._last_arrival_time_us is not None:
                if relative_arrival_time_us <= self._last_arrival_time_us:
                    # Adjust to be at least 1 microsecond after previous packet
                    old_time = relative_arrival_time_us
                    relative_arrival_time_us = self._last_arrival_time_us + 1
                    logger.debug(f"TWCC: Enforced monotonicity for seq={unwrapped_seq}: {old_time}us → {relative_arrival_time_us}us")

            self._last_arrival_time_us = relative_arrival_time_us
            self._arrival_times.add(unwrapped_seq, relative_arrival_time_us)

            # Track absolute arrival time for feedback latency measurement
            self._packet_arrival_times[unwrapped_seq] = int(time.monotonic() * 1_000_000)

            if self._base_seq is None:
                self._base_seq = unwrapped_seq
                logger.info(f"TWCC: First packet recorded, base_seq={unwrapped_seq} (transport_seq={transport_seq})")

            # Log every 100th packet for debugging
            if unwrapped_seq % 100 == 0:
                logger.debug(f"TWCC: Recorded packet {unwrapped_seq} (transport_seq={transport_seq & 0xFFFF})")

    def generate_feedback(self) -> Optional[bytes]:
        """
        Generate TWCC feedback report.

        Returns RTCP TWCC feedback packet data or None if no data available.
        """
        with self._lock:
            if self._base_seq is None or self.media_ssrc is None:
                # Can't generate feedback until we've received packets and know the media SSRC
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
                # Check if there are packets with higher sequence numbers (gap due to packet loss)
                # This follows libwebrtc's philosophy: prioritize robustness over completeness
                total_in_map = len(self._arrival_times._arrivals)
                if total_in_map > 0:
                    # Find the minimum sequence number still in the arrival map
                    all_seqs = list(self._arrival_times._arrivals.keys())
                    if all_seqs:
                        min_available_seq = min(all_seqs)
                        # If there are packets beyond our search window, skip the gap
                        if min_available_seq > end_seq:
                            gap_size = min_available_seq - start_seq
                            logger.warning(f"TWCC: Detected sequence gap of {gap_size} packets! "
                                         f"No packets in range [{start_seq & 0xFFFF}-{end_seq & 0xFFFF}], "
                                         f"but {total_in_map} packets exist starting at seq {min_available_seq & 0xFFFF}. "
                                         f"Skipping gap (likely packet loss or arrival map overflow).")
                            # Clean up the gap to prevent map from filling
                            self._arrival_times.clear_before(min_available_seq)
                            # Update state to skip ahead
                            self._last_feedback_seq = min_available_seq - 1
                            # Try again with new range
                            start_seq = min_available_seq
                            end_seq = start_seq + 100
                            packets = self._arrival_times.get_range(start_seq, end_seq)

                # If still no packets after gap skip, clean up and return None
                if not packets:
                    # CRITICAL: Always clean up to prevent map from filling, even when returning None
                    # This matches libwebrtc's behavior: prioritize robustness over completeness
                    self._arrival_times.clear_before(start_seq)
                    logger.debug(f"TWCC: No new packets in range [{start_seq & 0xFFFF}-{end_seq & 0xFFFF}] (map has {total_in_map} total)")
                    return None

            # Log feedback generation with first few sequence numbers for correlation with Pion
            first_few_seqs = [seq & 0xFFFF for seq, _ in packets[:5]]
            logger.info(f"TWCC: Generating feedback for {len(packets)} packets in range [{start_seq & 0xFFFF}-{(start_seq + len(packets) - 1) & 0xFFFF}], first seqs: {first_few_seqs}")

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
            # Use first received packet in sequence order (per TWCC spec)
            first_arrival = received_packets[0][1]
            reference_time = (first_arrival // REFERENCE_TIME_US) * REFERENCE_TIME_US

            # Ensure cross-report monotonicity: reference time must be at least 64ms after previous report
            # This prevents pion's arrivalGroupAccumulator from dropping packets as "out of order"
            if self._last_feedback_ref_time is not None:
                min_ref_time = self._last_feedback_ref_time + REFERENCE_TIME_US
                if reference_time < min_ref_time:
                    logger.debug(f"TWCC: Bumping reference time from {reference_time}us to {min_ref_time}us to ensure cross-report monotonicity")
                    reference_time = min_ref_time

            reference_time_24bit = (reference_time // REFERENCE_TIME_US) & 0xFFFFFF

            delta_bytes, delta_statuses = ReceiveDeltaEncoder.encode_deltas(
                received_packets, reference_time
            )

            # Analyze all deltas for jitter detection
            all_deltas_us = []
            last_time = reference_time
            for seq, arrival in received_packets:
                delta_us = arrival - last_time
                all_deltas_us.append(delta_us)
                # Use rounded delta like the encoder does
                if delta_us >= 0 and delta_us <= 63750:
                    delta_rounded = ((delta_us + 125) // 250) * 250
                else:
                    if delta_us >= 0:
                        delta_rounded = ((delta_us + 500) // 1000) * 1000
                    else:
                        delta_rounded = ((delta_us - 500) // 1000) * 1000
                last_time += delta_rounded

            # Calculate delta statistics (reveals jitter patterns that GCC uses)
            if len(all_deltas_us) > 0:
                min_delta = min(all_deltas_us)
                max_delta = max(all_deltas_us)
                avg_delta = sum(all_deltas_us) / len(all_deltas_us)
                # Calculate stddev
                if len(all_deltas_us) > 1:
                    variance = sum((d - avg_delta) ** 2 for d in all_deltas_us) / len(all_deltas_us)
                    stddev = variance ** 0.5
                else:
                    stddev = 0.0

                # Log every 10th feedback (every ~1 second)
                if (len(packets) % 100 < 10):  # Approximately every second
                    logger.info(f"🔍 TWCC_DELTA: {len(all_deltas_us)} pkts, "
                               f"delta μs: min={min_delta}, max={max_delta}, "
                               f"avg={avg_delta:.0f}, σ={stddev:.0f}, "
                               f"refTime={reference_time_24bit}")

                    # 🔍 HYPOTHESIS TEST: TWCC delta variance correlates with asyncio scheduling jitter
                    # Compare this stddev with ASYNCIO_SCHED stddev from rtcdtlstransport.py
                    # If they're similar (both 3-7ms), it proves asyncio jitter causes TWCC jitter
                    if stddev > 3000:  # 3ms threshold
                        logger.warning(f"⚠️  HIGH TWCC DELTA VARIANCE: σ={stddev:.0f}μs - GCC will interpret "
                                      f"this as congestion and throttle! Compare with ASYNCIO_SCHED σ above.")

            # Update statuses with actual delta types
            status_idx = 0
            for i, status in enumerate(statuses):
                if status != PACKET_NOT_RECEIVED:
                    statuses[i] = delta_statuses[status_idx]
                    status_idx += 1

            # DEBUG: Count received packets in statuses
            num_received_in_statuses = sum(1 for s in statuses if s != PACKET_NOT_RECEIVED)
            logger.info(f"TWCC DEBUG: {num_received_in_statuses} received in statuses, {len(delta_statuses)} delta_statuses, {len(received_packets)} received_packets, delta_bytes len={len(delta_bytes)}")

            # DEBUG: Check if actual arrival times are monotonic (check ALL packets, not just first 10)
            non_monotonic_count = 0
            if len(received_packets) > 1:
                for i in range(1, len(received_packets)):
                    seq1, arrival1 = received_packets[i-1]
                    seq2, arrival2 = received_packets[i]
                    if arrival2 < arrival1:
                        non_monotonic_count += 1
                        if non_monotonic_count <= 5:  # Log first 5 occurrences
                            logger.warning(f"TWCC: NON-MONOTONIC #{non_monotonic_count}: seq{seq1 & 0xFFFF}@{arrival1}us > seq{seq2 & 0xFFFF}@{arrival2}us (delta={arrival2-arrival1}us)")

            if non_monotonic_count > 0:
                logger.warning(f"TWCC: {non_monotonic_count} non-monotonic pairs out of {len(received_packets)} packets in this report!")

            logger.debug(f"TWCC: Range seq[{start_seq & 0xFFFF}-{max_seq & 0xFFFF}], {len(received_packets)} pkts, ref_time={reference_time}us, first_arrival={first_arrival}us")

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
            self._last_feedback_ref_time = reference_time  # Track for cross-report monotonicity

            # Calculate feedback latency: time from packet arrival to feedback generation
            feedback_send_time_us = int(time.monotonic() * 1_000_000)
            for seq, _ in received_packets:
                if seq in self._packet_arrival_times:
                    latency_us = feedback_send_time_us - self._packet_arrival_times[seq]
                    stats = self._feedback_latency_stats
                    stats['count'] += 1
                    stats['sum_latency_us'] += latency_us
                    stats['min_latency_us'] = min(stats['min_latency_us'], latency_us)
                    stats['max_latency_us'] = max(stats['max_latency_us'], latency_us)

                    # Remove from tracking dict to prevent unbounded growth
                    del self._packet_arrival_times[seq]

            # Report feedback latency periodically
            now = time.time()
            if now - self._feedback_latency_stats['last_report_time'] >= 5.0:
                stats = self._feedback_latency_stats
                if stats['count'] > 0:
                    avg_latency_us = stats['sum_latency_us'] / stats['count']
                    logger.info(f"🔍 FEEDBACK_LATENCY: {stats['count']} pkts, "
                               f"μs: min={stats['min_latency_us']}, "
                               f"max={stats['max_latency_us']}, "
                               f"avg={avg_latency_us:.0f}")
                    # Reset for next period
                    stats['count'] = 0
                    stats['sum_latency_us'] = 0
                    stats['min_latency_us'] = float('inf')
                    stats['max_latency_us'] = 0
                    stats['last_report_time'] = now

            # Clean up old arrival times
            self._arrival_times.clear_before(start_seq)
            # Clean up old packet arrival tracking (packets we haven't sent feedback for yet)
            for seq in list(self._packet_arrival_times.keys()):
                if seq < start_seq:
                    del self._packet_arrival_times[seq]

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

        # Bytes 4-7: SSRC of packet sender (receiver's RTCP SSRC)
        # CRITICAL: Must use sender_ssrc here, not media_ssrc, per RTCP spec
        packet.extend(self.sender_ssrc.to_bytes(4, byteorder='big'))

        # Bytes 8-11: SSRC of media source (sender's media stream SSRC)
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
