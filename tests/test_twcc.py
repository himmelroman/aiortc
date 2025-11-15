from unittest import TestCase

from aiortc.twcc.receiver import (
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
from aiortc.twcc.sender import (
    PacketChunkDecoder,
    PacketResult,
    ReceiveDeltaDecoder,
    SentPacketTracker,
    TWCCParser,
)


class SequenceNumberUnwrapperTest(TestCase):
    def test_sequential(self):
        unwrapper = SequenceNumberUnwrapper()
        self.assertEqual(unwrapper.unwrap(0), 0)
        self.assertEqual(unwrapper.unwrap(1), 1)
        self.assertEqual(unwrapper.unwrap(100), 100)

    def test_forward_wrap(self):
        unwrapper = SequenceNumberUnwrapper()
        unwrapper.unwrap(65534)
        unwrapped = unwrapper.unwrap(65535)
        self.assertEqual(unwrapped, 65535)
        unwrapped = unwrapper.unwrap(0)
        self.assertEqual(unwrapped, 65536)

    def test_multiple_wraps(self):
        unwrapper = SequenceNumberUnwrapper()
        for i in range(0, 65536, 1000):
            unwrapped = unwrapper.unwrap(i)
            self.assertEqual(unwrapped, i)
        for i in range(100):
            unwrapped = unwrapper.unwrap(i)
            self.assertEqual(unwrapped, 65536 + i)


class TransportSequenceNumberManagerTest(TestCase):
    def test_initialization(self):
        manager = TransportSequenceNumberManager()
        self.assertEqual(manager.next(), 0)
        self.assertEqual(manager.next(), 1)

    def test_initialization_with_value(self):
        manager = TransportSequenceNumberManager(initial_value=100)
        self.assertEqual(manager.next(), 100)
        self.assertEqual(manager.next(), 101)

    def test_wraparound(self):
        manager = TransportSequenceNumberManager(initial_value=65535)
        self.assertEqual(manager.next(), 65535)
        self.assertEqual(manager.next(), 0)


class ArrivalTimeMapTest(TestCase):
    def test_add_and_get(self):
        arrival_map = ArrivalTimeMap()
        arrival_map.add(1, 1000)
        arrival_map.add(2, 2000)
        self.assertEqual(arrival_map.get(1), 1000)
        self.assertEqual(arrival_map.get(2), 2000)
        self.assertIsNone(arrival_map.get(3))

    def test_get_range(self):
        arrival_map = ArrivalTimeMap()
        for i in range(10):
            arrival_map.add(i, i * 1000)
        results = arrival_map.get_range(2, 5)
        self.assertEqual(results, [(2, 2000), (3, 3000), (4, 4000), (5, 5000)])

    def test_clear_before(self):
        arrival_map = ArrivalTimeMap()
        for i in range(10):
            arrival_map.add(i, i * 1000)
        arrival_map.clear_before(5)
        self.assertIsNone(arrival_map.get(4))
        self.assertIsNotNone(arrival_map.get(5))


class PacketChunkEncoderTest(TestCase):
    def test_encode_run_length(self):
        chunk_bytes = PacketChunkEncoder.encode_run_length(PACKET_NOT_RECEIVED, 10)
        self.assertEqual(len(chunk_bytes), 2)
        chunk = int.from_bytes(chunk_bytes, byteorder="big")
        chunk_type = (chunk >> 15) & 0x1
        symbol = (chunk >> 13) & 0x3
        run_length = chunk & 0x1FFF
        self.assertEqual(chunk_type, RUN_LENGTH_CHUNK)
        self.assertEqual(symbol, PACKET_NOT_RECEIVED)
        self.assertEqual(run_length, 10)

    def test_encode_status_vector_1bit(self):
        symbols = [0, 1, 1, 0, 1, 0, 0, 1]
        chunk_bytes = PacketChunkEncoder.encode_status_vector(symbols, 1)
        self.assertEqual(len(chunk_bytes), 2)

    def test_encode_chunks_simple(self):
        statuses = [PACKET_NOT_RECEIVED] * 10
        chunk_bytes = PacketChunkEncoder.encode_chunks(statuses)
        self.assertEqual(len(chunk_bytes), 2)


class ReceiveDeltaEncoderTest(TestCase):
    def test_encode_small_deltas(self):
        reference_time = 1000000
        packets = [
            (0, reference_time + 1000),
            (1, reference_time + 2000),
            (2, reference_time + 3000),
        ]
        delta_bytes, statuses = ReceiveDeltaEncoder.encode_deltas(packets, reference_time)
        self.assertEqual(statuses, [PACKET_RECEIVED_SMALL_DELTA] * 3)
        self.assertEqual(len(delta_bytes), 3)

    def test_encode_large_deltas(self):
        reference_time = 1000000
        packets = [(0, reference_time), (1, reference_time + 100000)]
        delta_bytes, statuses = ReceiveDeltaEncoder.encode_deltas(packets, reference_time)
        self.assertEqual(statuses[1], PACKET_RECEIVED_LARGE_DELTA)


class TWCCRecorderTest(TestCase):
    def test_initialization(self):
        recorder = TWCCRecorder(media_ssrc=12345)
        self.assertEqual(recorder.media_ssrc, 12345)

    def test_record_packet(self):
        recorder = TWCCRecorder(media_ssrc=12345)
        recorder.record_packet(0)
        recorder.record_packet(1)
        feedback = recorder.generate_feedback()
        self.assertIsNotNone(feedback)

    def test_generate_feedback_structure(self):
        recorder = TWCCRecorder(media_ssrc=12345)
        for i in range(10):
            recorder.record_packet(i)
        feedback = recorder.generate_feedback()
        self.assertIsNotNone(feedback)
        self.assertGreaterEqual(len(feedback), 20)
        version = (feedback[0] >> 6) & 0x3
        fmt = feedback[0] & 0x1F
        pt = feedback[1]
        self.assertEqual(version, 2)
        self.assertEqual(pt, 205)
        self.assertEqual(fmt, 15)


class SentPacketTrackerTest(TestCase):
    def test_add_and_get(self):
        tracker = SentPacketTracker()
        tracker.add(seq=1, size=1200, ssrc=12345)
        packet = tracker.get(1)
        self.assertIsNotNone(packet)
        self.assertEqual(packet.size, 1200)

    def test_get_range(self):
        tracker = SentPacketTracker()
        for i in range(10):
            tracker.add(seq=i, size=1200, ssrc=12345)
        packets = tracker.get_range(2, 5)
        self.assertEqual(len(packets), 4)


class PacketChunkDecoderTest(TestCase):
    def test_decode_run_length(self):
        encoded = PacketChunkEncoder.encode_run_length(PACKET_NOT_RECEIVED, 10)
        statuses = PacketChunkDecoder.decode_run_length(encoded)
        self.assertEqual(len(statuses), 10)
        self.assertEqual(statuses, [PACKET_NOT_RECEIVED] * 10)

    def test_decode_status_vector_2bit(self):
        symbols = [0, 1, 2, 3, 0, 1, 2]
        encoded = PacketChunkEncoder.encode_status_vector(symbols, 2)
        decoded = PacketChunkDecoder.decode_status_vector(encoded)
        self.assertEqual(len(decoded), 7)
        self.assertEqual(decoded, symbols)


class TWCCParserTest(TestCase):
    def test_parse_feedback_basic(self):
        recorder = TWCCRecorder(media_ssrc=12345)
        for i in range(10):
            recorder.record_packet(i)
        feedback_data = recorder.generate_feedback()
        self.assertIsNotNone(feedback_data)
        results = TWCCParser.parse_feedback(feedback_data)
        self.assertIsNotNone(results)
        self.assertGreater(len(results), 0)

    def test_parse_invalid_data(self):
        results = TWCCParser.parse_feedback(b"invalid")
        self.assertIsNone(results)

    def test_parse_roundtrip(self):
        recorder = TWCCRecorder(media_ssrc=12345)
        test_seqs = [0, 1, 2, 3, 4]
        for seq in test_seqs:
            recorder.record_packet(seq)
        feedback_data = recorder.generate_feedback()
        self.assertIsNotNone(feedback_data)
        results = TWCCParser.parse_feedback(feedback_data)
        self.assertIsNotNone(results)
        received_seqs = [r.sequence_number for r in results if r.received]
        for seq in test_seqs:
            self.assertIn(seq, received_seqs)
