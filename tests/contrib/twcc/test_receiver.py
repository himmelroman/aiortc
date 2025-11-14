"""
Tests for TWCC receiver-side implementation.
"""

import time
import unittest

from aiortc.contrib.twcc.receiver import (
    LARGE_DELTA_US,
    MAX_SMALL_DELTA_US,
    PACKET_NOT_RECEIVED,
    PACKET_RECEIVED_LARGE_DELTA,
    PACKET_RECEIVED_SMALL_DELTA,
    REFERENCE_TIME_US,
    RUN_LENGTH_CHUNK,
    SMALL_DELTA_US,
    STATUS_VECTOR_CHUNK,
    ArrivalTimeMap,
    PacketChunkEncoder,
    ReceiveDeltaEncoder,
    SequenceNumberUnwrapper,
    TransportSequenceNumberManager,
    TWCCRecorder,
)


class TestSequenceNumberUnwrapper(unittest.TestCase):
    """Test sequence number unwrapping."""

    def test_sequential(self):
        """Test sequential sequence numbers."""
        unwrapper = SequenceNumberUnwrapper()

        self.assertEqual(unwrapper.unwrap(0), 0)
        self.assertEqual(unwrapper.unwrap(1), 1)
        self.assertEqual(unwrapper.unwrap(2), 2)
        self.assertEqual(unwrapper.unwrap(100), 100)

    def test_forward_wrap(self):
        """Test forward wraparound."""
        unwrapper = SequenceNumberUnwrapper()

        unwrapper.unwrap(65534)
        unwrapped = unwrapper.unwrap(65535)
        self.assertEqual(unwrapped, 65535)

        unwrapped = unwrapper.unwrap(0)
        self.assertEqual(unwrapped, 65536)

        unwrapped = unwrapper.unwrap(1)
        self.assertEqual(unwrapped, 65537)

    def test_backward_wrap(self):
        """Test backward wraparound detection."""
        unwrapper = SequenceNumberUnwrapper()

        unwrapper.unwrap(10)
        # Large jump backward should decrement cycle
        unwrapped = unwrapper.unwrap(65535)
        self.assertLess(unwrapped, 65536)

    def test_multiple_wraps(self):
        """Test multiple wraparounds."""
        unwrapper = SequenceNumberUnwrapper()

        # First cycle
        for i in range(65536):
            if i % 1000 == 0:  # Sample to speed up test
                unwrapped = unwrapper.unwrap(i)
                self.assertEqual(unwrapped, i)

        # Second cycle
        for i in range(100):
            unwrapped = unwrapper.unwrap(i)
            self.assertEqual(unwrapped, 65536 + i)


class TestTransportSequenceNumberManager(unittest.TestCase):
    """Test transport sequence number generation."""

    def test_initialization(self):
        """Test initialization with default value."""
        manager = TransportSequenceNumberManager()
        self.assertEqual(manager.next(), 0)
        self.assertEqual(manager.next(), 1)

    def test_initialization_with_value(self):
        """Test initialization with specific value."""
        manager = TransportSequenceNumberManager(initial_value=100)
        self.assertEqual(manager.next(), 100)
        self.assertEqual(manager.next(), 101)

    def test_wraparound(self):
        """Test 16-bit wraparound."""
        manager = TransportSequenceNumberManager(initial_value=65535)
        self.assertEqual(manager.next(), 65535)
        self.assertEqual(manager.next(), 0)
        self.assertEqual(manager.next(), 1)

    def test_thread_safety(self):
        """Test thread-safe operation."""
        import threading

        manager = TransportSequenceNumberManager()
        results = []

        def generate_sequences(count):
            for _ in range(count):
                results.append(manager.next())

        threads = [threading.Thread(target=generate_sequences, args=(100,)) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Should have 1000 unique sequence numbers
        self.assertEqual(len(results), 1000)
        self.assertEqual(len(set(results)), 1000)


class TestArrivalTimeMap(unittest.TestCase):
    """Test arrival time storage."""

    def test_add_and_get(self):
        """Test adding and retrieving arrival times."""
        arrival_map = ArrivalTimeMap()

        arrival_map.add(1, 1000)
        arrival_map.add(2, 2000)
        arrival_map.add(3, 3000)

        self.assertEqual(arrival_map.get(1), 1000)
        self.assertEqual(arrival_map.get(2), 2000)
        self.assertEqual(arrival_map.get(3), 3000)
        self.assertIsNone(arrival_map.get(4))

    def test_get_range(self):
        """Test getting arrival times for a range."""
        arrival_map = ArrivalTimeMap()

        for i in range(10):
            arrival_map.add(i, i * 1000)

        results = arrival_map.get_range(2, 5)
        expected = [(2, 2000), (3, 3000), (4, 4000), (5, 5000)]
        self.assertEqual(results, expected)

    def test_max_size(self):
        """Test automatic cleanup at max size."""
        arrival_map = ArrivalTimeMap(max_size=10)

        for i in range(20):
            arrival_map.add(i, i * 1000)

        # Should have removed oldest entries
        self.assertIsNone(arrival_map.get(0))
        self.assertIsNotNone(arrival_map.get(10))

    def test_clear_before(self):
        """Test clearing entries before a sequence number."""
        arrival_map = ArrivalTimeMap()

        for i in range(10):
            arrival_map.add(i, i * 1000)

        arrival_map.clear_before(5)

        self.assertIsNone(arrival_map.get(4))
        self.assertIsNotNone(arrival_map.get(5))
        self.assertIsNotNone(arrival_map.get(9))


class TestPacketChunkEncoder(unittest.TestCase):
    """Test packet chunk encoding."""

    def test_encode_run_length(self):
        """Test run-length encoding."""
        chunk_bytes = PacketChunkEncoder.encode_run_length(
            PACKET_NOT_RECEIVED, 10
        )

        self.assertEqual(len(chunk_bytes), 2)

        # Parse back
        chunk = int.from_bytes(chunk_bytes, byteorder='big')
        chunk_type = (chunk >> 15) & 0x1
        symbol = (chunk >> 13) & 0x3
        run_length = chunk & 0x1FFF

        self.assertEqual(chunk_type, RUN_LENGTH_CHUNK)
        self.assertEqual(symbol, PACKET_NOT_RECEIVED)
        self.assertEqual(run_length, 10)

    def test_encode_run_length_max(self):
        """Test run-length with maximum value."""
        chunk_bytes = PacketChunkEncoder.encode_run_length(
            PACKET_RECEIVED_SMALL_DELTA, 0x1FFF
        )

        chunk = int.from_bytes(chunk_bytes, byteorder='big')
        run_length = chunk & 0x1FFF
        self.assertEqual(run_length, 0x1FFF)

    def test_encode_run_length_overflow(self):
        """Test run-length overflow raises error."""
        with self.assertRaises(ValueError):
            PacketChunkEncoder.encode_run_length(PACKET_NOT_RECEIVED, 0x2000)

    def test_encode_status_vector_1bit(self):
        """Test 1-bit status vector encoding."""
        symbols = [0, 1, 1, 0, 1, 0, 0, 1]
        chunk_bytes = PacketChunkEncoder.encode_status_vector(symbols, 1)

        self.assertEqual(len(chunk_bytes), 2)

        chunk = int.from_bytes(chunk_bytes, byteorder='big')
        chunk_type = (chunk >> 15) & 0x1
        symbol_size = ((chunk >> 14) & 0x1) + 1

        self.assertEqual(chunk_type, STATUS_VECTOR_CHUNK)
        self.assertEqual(symbol_size, 1)

    def test_encode_status_vector_2bit(self):
        """Test 2-bit status vector encoding."""
        symbols = [0, 1, 2, 3, 0, 1, 2]
        chunk_bytes = PacketChunkEncoder.encode_status_vector(symbols, 2)

        self.assertEqual(len(chunk_bytes), 2)

        chunk = int.from_bytes(chunk_bytes, byteorder='big')
        chunk_type = (chunk >> 15) & 0x1
        symbol_size = ((chunk >> 14) & 0x1) + 1

        self.assertEqual(chunk_type, STATUS_VECTOR_CHUNK)
        self.assertEqual(symbol_size, 2)

    def test_encode_chunks_simple(self):
        """Test encoding simple status list."""
        statuses = [PACKET_NOT_RECEIVED] * 10
        chunk_bytes = PacketChunkEncoder.encode_chunks(statuses)

        # Should use run-length encoding
        self.assertEqual(len(chunk_bytes), 2)

    def test_encode_chunks_mixed(self):
        """Test encoding mixed status list."""
        statuses = [
            PACKET_RECEIVED_SMALL_DELTA,
            PACKET_RECEIVED_SMALL_DELTA,
            PACKET_NOT_RECEIVED,
            PACKET_RECEIVED_SMALL_DELTA,
        ]
        chunk_bytes = PacketChunkEncoder.encode_chunks(statuses)

        # Should use status vector
        self.assertGreater(len(chunk_bytes), 0)


class TestReceiveDeltaEncoder(unittest.TestCase):
    """Test receive delta encoding."""

    def test_encode_small_deltas(self):
        """Test encoding small deltas."""
        reference_time = 1000000
        packets = [
            (0, reference_time + 1000),
            (1, reference_time + 2000),
            (2, reference_time + 3000),
        ]

        delta_bytes, statuses = ReceiveDeltaEncoder.encode_deltas(
            packets, reference_time
        )

        # All should be small deltas
        self.assertEqual(statuses, [PACKET_RECEIVED_SMALL_DELTA] * 3)
        self.assertEqual(len(delta_bytes), 3)  # 1 byte per delta

    def test_encode_large_deltas(self):
        """Test encoding large deltas."""
        reference_time = 1000000
        packets = [
            (0, reference_time),
            (1, reference_time + 100000),  # Large delta
        ]

        delta_bytes, statuses = ReceiveDeltaEncoder.encode_deltas(
            packets, reference_time
        )

        # First is small (0), second is large
        self.assertEqual(statuses[0], PACKET_RECEIVED_SMALL_DELTA)
        self.assertEqual(statuses[1], PACKET_RECEIVED_LARGE_DELTA)
        self.assertEqual(len(delta_bytes), 3)  # 1 + 2 bytes

    def test_encode_negative_deltas(self):
        """Test encoding negative deltas."""
        reference_time = 1000000
        packets = [
            (0, reference_time),
            (1, reference_time - 500),  # Negative delta
        ]

        delta_bytes, statuses = ReceiveDeltaEncoder.encode_deltas(
            packets, reference_time
        )

        # Should handle negative deltas
        self.assertEqual(len(statuses), 2)

    def test_encode_empty(self):
        """Test encoding empty packet list."""
        delta_bytes, statuses = ReceiveDeltaEncoder.encode_deltas([], 0)

        self.assertEqual(delta_bytes, b'')
        self.assertEqual(statuses, [])


class TestTWCCRecorder(unittest.TestCase):
    """Test TWCC recorder."""

    def test_initialization(self):
        """Test recorder initialization."""
        recorder = TWCCRecorder(media_ssrc=12345)
        self.assertEqual(recorder.media_ssrc, 12345)

    def test_record_packet(self):
        """Test recording packet arrivals."""
        recorder = TWCCRecorder(media_ssrc=12345)

        recorder.record_packet(0)
        recorder.record_packet(1)
        recorder.record_packet(2)

        # Should have recorded packets (verified via feedback generation)
        feedback = recorder.generate_feedback()
        self.assertIsNotNone(feedback)

    def test_record_packet_wraparound(self):
        """Test recording packets with sequence number wraparound."""
        recorder = TWCCRecorder(media_ssrc=12345)

        recorder.record_packet(65534)
        recorder.record_packet(65535)
        recorder.record_packet(0)
        recorder.record_packet(1)

        feedback = recorder.generate_feedback()
        self.assertIsNotNone(feedback)

    def test_generate_feedback_empty(self):
        """Test generating feedback with no packets."""
        recorder = TWCCRecorder(media_ssrc=12345)

        feedback = recorder.generate_feedback()
        self.assertIsNone(feedback)

    def test_generate_feedback_structure(self):
        """Test feedback packet structure."""
        recorder = TWCCRecorder(media_ssrc=12345)

        # Record some packets
        for i in range(10):
            recorder.record_packet(i)
            time.sleep(0.001)  # Small delay to get different timestamps

        feedback = recorder.generate_feedback()
        self.assertIsNotNone(feedback)

        # Check RTCP header
        self.assertGreaterEqual(len(feedback), 20)

        version = (feedback[0] >> 6) & 0x3
        fmt = feedback[0] & 0x1F
        pt = feedback[1]

        self.assertEqual(version, 2)
        self.assertEqual(pt, 205)
        self.assertEqual(fmt, 15)

    def test_generate_feedback_incremental(self):
        """Test generating multiple feedback reports."""
        recorder = TWCCRecorder(media_ssrc=12345)

        # First batch
        for i in range(5):
            recorder.record_packet(i)

        feedback1 = recorder.generate_feedback()
        self.assertIsNotNone(feedback1)

        # Second batch
        for i in range(5, 10):
            recorder.record_packet(i)

        feedback2 = recorder.generate_feedback()
        self.assertIsNotNone(feedback2)

        # Both should be valid
        self.assertGreater(len(feedback1), 0)
        self.assertGreater(len(feedback2), 0)

    def test_generate_feedback_with_gaps(self):
        """Test generating feedback with packet gaps."""
        recorder = TWCCRecorder(media_ssrc=12345)

        # Record packets with gaps
        recorder.record_packet(0)
        recorder.record_packet(1)
        recorder.record_packet(5)  # Gap: 2, 3, 4 missing
        recorder.record_packet(6)

        feedback = recorder.generate_feedback()
        self.assertIsNotNone(feedback)

        # Parse to verify gap handling
        self.assertGreater(len(feedback), 20)


class TestConstants(unittest.TestCase):
    """Test constant values."""

    def test_delta_units(self):
        """Test delta unit values."""
        self.assertEqual(SMALL_DELTA_US, 250)
        self.assertEqual(LARGE_DELTA_US, 1000)
        self.assertEqual(MAX_SMALL_DELTA_US, 63750)

    def test_reference_time(self):
        """Test reference time unit."""
        self.assertEqual(REFERENCE_TIME_US, 64000)

    def test_packet_statuses(self):
        """Test packet status values."""
        self.assertEqual(PACKET_NOT_RECEIVED, 0)
        self.assertEqual(PACKET_RECEIVED_SMALL_DELTA, 1)
        self.assertEqual(PACKET_RECEIVED_LARGE_DELTA, 2)

    def test_chunk_types(self):
        """Test chunk type values."""
        self.assertEqual(RUN_LENGTH_CHUNK, 0)
        self.assertEqual(STATUS_VECTOR_CHUNK, 1)


if __name__ == '__main__':
    unittest.main()
