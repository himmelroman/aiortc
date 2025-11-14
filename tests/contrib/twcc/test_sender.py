"""
Tests for TWCC sender-side implementation.
"""

import unittest

from aiortc.contrib.twcc.sender import (
    PacketChunkDecoder,
    PacketResult,
    ReceiveDeltaDecoder,
    SentPacketTracker,
    TWCCParser,
)
from aiortc.contrib.twcc.receiver import (
    PACKET_NOT_RECEIVED,
    PACKET_RECEIVED_LARGE_DELTA,
    PACKET_RECEIVED_SMALL_DELTA,
    REFERENCE_TIME_US,
    RUN_LENGTH_CHUNK,
    SMALL_DELTA_US,
    STATUS_VECTOR_CHUNK,
    PacketChunkEncoder,
    TWCCRecorder,
)


class TestSentPacketTracker(unittest.TestCase):
    """Test sent packet tracking."""

    def test_add_and_get(self):
        """Test adding and retrieving packets."""
        tracker = SentPacketTracker()

        tracker.add(seq=1, size=1200, ssrc=12345)
        tracker.add(seq=2, size=1300, ssrc=12345)

        packet1 = tracker.get(1)
        self.assertIsNotNone(packet1)
        self.assertEqual(packet1.sequence_number, 1)
        self.assertEqual(packet1.size, 1200)
        self.assertEqual(packet1.ssrc, 12345)

        packet2 = tracker.get(2)
        self.assertIsNotNone(packet2)
        self.assertEqual(packet2.size, 1300)

    def test_get_nonexistent(self):
        """Test getting nonexistent packet."""
        tracker = SentPacketTracker()

        packet = tracker.get(999)
        self.assertIsNone(packet)

    def test_get_range(self):
        """Test getting packet range."""
        tracker = SentPacketTracker()

        for i in range(10):
            tracker.add(seq=i, size=1200 + i, ssrc=12345)

        packets = tracker.get_range(2, 5)
        self.assertEqual(len(packets), 4)
        self.assertEqual(packets[0].sequence_number, 2)
        self.assertEqual(packets[3].sequence_number, 5)

    def test_max_size(self):
        """Test automatic cleanup at max size."""
        tracker = SentPacketTracker(max_size=10)

        for i in range(20):
            tracker.add(seq=i, size=1200, ssrc=12345)

        # Should have removed oldest
        self.assertIsNone(tracker.get(0))
        self.assertIsNotNone(tracker.get(10))

    def test_clear_before(self):
        """Test clearing before sequence number."""
        tracker = SentPacketTracker()

        for i in range(10):
            tracker.add(seq=i, size=1200, ssrc=12345)

        tracker.clear_before(5)

        self.assertIsNone(tracker.get(4))
        self.assertIsNotNone(tracker.get(5))


class TestPacketChunkDecoder(unittest.TestCase):
    """Test packet chunk decoding."""

    def test_decode_run_length(self):
        """Test decoding run-length chunk."""
        # Encode first
        encoded = PacketChunkEncoder.encode_run_length(PACKET_NOT_RECEIVED, 10)

        # Decode
        statuses = PacketChunkDecoder.decode_run_length(encoded)

        self.assertEqual(len(statuses), 10)
        self.assertEqual(statuses, [PACKET_NOT_RECEIVED] * 10)

    def test_decode_run_length_received(self):
        """Test decoding run-length with received status."""
        encoded = PacketChunkEncoder.encode_run_length(PACKET_RECEIVED_SMALL_DELTA, 5)

        statuses = PacketChunkDecoder.decode_run_length(encoded)

        self.assertEqual(len(statuses), 5)
        self.assertEqual(statuses, [PACKET_RECEIVED_SMALL_DELTA] * 5)

    def test_decode_status_vector_1bit(self):
        """Test decoding 1-bit status vector."""
        symbols = [0, 1, 1, 0, 1, 0, 0, 1]
        encoded = PacketChunkEncoder.encode_status_vector(symbols, 1)

        decoded = PacketChunkDecoder.decode_status_vector(encoded)

        # Should get 14 symbols (padded)
        self.assertEqual(len(decoded), 14)
        # First 8 should match
        self.assertEqual(decoded[:8], symbols)

    def test_decode_status_vector_2bit(self):
        """Test decoding 2-bit status vector."""
        symbols = [0, 1, 2, 3, 0, 1, 2]
        encoded = PacketChunkEncoder.encode_status_vector(symbols, 2)

        decoded = PacketChunkDecoder.decode_status_vector(encoded)

        # Should get 7 symbols
        self.assertEqual(len(decoded), 7)
        self.assertEqual(decoded, symbols)

    def test_decode_chunks(self):
        """Test decoding multiple chunks."""
        # Create mixed chunks
        statuses = [PACKET_NOT_RECEIVED] * 10 + [PACKET_RECEIVED_SMALL_DELTA] * 5
        encoded = PacketChunkEncoder.encode_chunks(statuses)

        # Decode
        decoded = PacketChunkDecoder.decode_chunks(encoded, len(statuses))

        self.assertEqual(len(decoded), len(statuses))

    def test_decode_chunks_truncate(self):
        """Test decoding with packet count limit."""
        statuses = [PACKET_NOT_RECEIVED] * 20
        encoded = PacketChunkEncoder.encode_chunks(statuses)

        # Decode only 10
        decoded = PacketChunkDecoder.decode_chunks(encoded, 10)

        self.assertEqual(len(decoded), 10)


class TestReceiveDeltaDecoder(unittest.TestCase):
    """Test receive delta decoding."""

    def test_decode_small_deltas(self):
        """Test decoding small deltas."""
        reference_time = 1000000

        # Create test data
        packets = [
            (0, reference_time + 1000),
            (1, reference_time + 2000),
            (2, reference_time + 3000),
        ]

        from aiortc.contrib.twcc.receiver import ReceiveDeltaEncoder
        delta_bytes, statuses = ReceiveDeltaEncoder.encode_deltas(packets, reference_time)

        # Decode
        decoded = ReceiveDeltaDecoder.decode_deltas(
            delta_bytes, statuses, reference_time
        )

        # Should have 3 results
        self.assertEqual(len(decoded), 3)

        # Check times (allowing for rounding)
        for i, (idx, arrival_time) in enumerate(decoded):
            self.assertEqual(idx, i)
            expected_time = packets[i][1]
            # Allow for quantization error
            self.assertAlmostEqual(
                arrival_time, expected_time, delta=SMALL_DELTA_US * 2
            )

    def test_decode_large_deltas(self):
        """Test decoding large deltas."""
        reference_time = 1000000

        packets = [
            (0, reference_time),
            (1, reference_time + 100000),
        ]

        from aiortc.contrib.twcc.receiver import ReceiveDeltaEncoder
        delta_bytes, statuses = ReceiveDeltaEncoder.encode_deltas(packets, reference_time)

        decoded = ReceiveDeltaDecoder.decode_deltas(
            delta_bytes, statuses, reference_time
        )

        self.assertEqual(len(decoded), 2)

    def test_decode_with_gaps(self):
        """Test decoding with packet gaps."""
        reference_time = 1000000

        # Statuses with gaps
        statuses = [
            PACKET_RECEIVED_SMALL_DELTA,
            PACKET_NOT_RECEIVED,
            PACKET_RECEIVED_SMALL_DELTA,
        ]

        # Only encode deltas for received packets
        packets = [
            (0, reference_time),
            (2, reference_time + 1000),
        ]

        from aiortc.contrib.twcc.receiver import ReceiveDeltaEncoder
        delta_bytes, _ = ReceiveDeltaEncoder.encode_deltas(packets, reference_time)

        decoded = ReceiveDeltaDecoder.decode_deltas(
            delta_bytes, statuses, reference_time
        )

        # Should have 2 results (indices 0 and 2)
        self.assertEqual(len(decoded), 2)
        self.assertEqual(decoded[0][0], 0)
        self.assertEqual(decoded[1][0], 2)


class TestTWCCParser(unittest.TestCase):
    """Test TWCC feedback parsing."""

    def test_parse_feedback_basic(self):
        """Test parsing basic feedback packet."""
        # Generate feedback using recorder
        recorder = TWCCRecorder(media_ssrc=12345)

        for i in range(10):
            recorder.record_packet(i)

        feedback_data = recorder.generate_feedback()
        self.assertIsNotNone(feedback_data)

        # Parse it
        results = TWCCParser.parse_feedback(feedback_data)
        self.assertIsNotNone(results)
        self.assertGreater(len(results), 0)

        # Check result types
        for result in results:
            self.assertIsInstance(result, PacketResult)
            self.assertIsInstance(result.sequence_number, int)
            self.assertIsInstance(result.received, bool)

    def test_parse_feedback_with_gaps(self):
        """Test parsing feedback with packet gaps."""
        recorder = TWCCRecorder(media_ssrc=12345)

        # Record with gaps
        recorder.record_packet(0)
        recorder.record_packet(1)
        recorder.record_packet(5)  # Gap
        recorder.record_packet(6)

        feedback_data = recorder.generate_feedback()
        results = TWCCParser.parse_feedback(feedback_data)

        self.assertIsNotNone(results)

        # Should have results for all sequence numbers in range
        seq_numbers = [r.sequence_number for r in results]
        self.assertIn(0, seq_numbers)
        self.assertIn(1, seq_numbers)

        # Check received flags
        received_map = {r.sequence_number: r.received for r in results}
        self.assertTrue(received_map.get(0, False))
        self.assertTrue(received_map.get(1, False))

    def test_parse_invalid_data(self):
        """Test parsing invalid data."""
        results = TWCCParser.parse_feedback(b'invalid')
        self.assertIsNone(results)

    def test_parse_wrong_packet_type(self):
        """Test parsing wrong packet type."""
        # Create RTCP packet with wrong PT
        data = bytearray(20)
        data[0] = 0x80  # V=2, P=0, FMT=0
        data[1] = 200  # Wrong PT (should be 205)

        results = TWCCParser.parse_feedback(bytes(data))
        self.assertIsNone(results)

    def test_parse_short_packet(self):
        """Test parsing packet that's too short."""
        results = TWCCParser.parse_feedback(b'\x00' * 10)
        self.assertIsNone(results)

    def test_parse_roundtrip(self):
        """Test encode-decode roundtrip."""
        recorder = TWCCRecorder(media_ssrc=12345)

        # Record packets with known sequence numbers
        test_seqs = [0, 1, 2, 3, 4]
        for seq in test_seqs:
            recorder.record_packet(seq)

        # Generate feedback
        feedback_data = recorder.generate_feedback()
        self.assertIsNotNone(feedback_data)

        # Parse feedback
        results = TWCCParser.parse_feedback(feedback_data)
        self.assertIsNotNone(results)

        # Verify sequence numbers
        received_seqs = [r.sequence_number for r in results if r.received]
        for seq in test_seqs:
            self.assertIn(seq, received_seqs)


class TestPacketResult(unittest.TestCase):
    """Test PacketResult dataclass."""

    def test_creation(self):
        """Test creating PacketResult."""
        result = PacketResult(
            sequence_number=123,
            received=True,
            delta_us=1000
        )

        self.assertEqual(result.sequence_number, 123)
        self.assertTrue(result.received)
        self.assertEqual(result.delta_us, 1000)

    def test_not_received(self):
        """Test not received packet result."""
        result = PacketResult(
            sequence_number=456,
            received=False
        )

        self.assertEqual(result.sequence_number, 456)
        self.assertFalse(result.received)
        self.assertIsNone(result.delta_us)


if __name__ == '__main__':
    unittest.main()
