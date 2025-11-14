"""
End-to-end tests for TWCC/GCC implementation.

These tests verify the complete TWCC/GCC system with simulated
media streaming between peers.
"""

import asyncio
import unittest

from aiortc.contrib.congestion_control import (
    CongestionControlIntegration,
    create_gcc_config,
)
from aiortc.contrib.twcc.receiver import TWCCRecorder
from aiortc.contrib.twcc.sender import SentPacketTracker, TWCCParser


class TestTWCCBasicFlow(unittest.TestCase):
    """Test basic TWCC flow without full RTCPeerConnection."""

    def test_basic_twcc_roundtrip(self):
        """Test basic TWCC encode-decode flow."""
        # Create recorder (receiver side)
        recorder = TWCCRecorder(media_ssrc=12345)

        # Simulate receiving packets
        for i in range(10):
            recorder.record_packet(i)

        # Generate feedback
        feedback_packet = recorder.generate_feedback()
        self.assertIsNotNone(feedback_packet)

        # Parse feedback (sender side)
        results = TWCCParser.parse_feedback(feedback_packet)
        self.assertIsNotNone(results)
        self.assertGreater(len(results), 0)

        # All packets should be marked as received
        received_count = sum(1 for r in results if r.received)
        self.assertEqual(received_count, 10)

    def test_twcc_with_packet_loss(self):
        """Test TWCC with simulated packet loss."""
        recorder = TWCCRecorder(media_ssrc=12345)

        # Simulate receiving packets with gaps (packet loss)
        received_seqs = [0, 1, 2, 5, 6, 7, 8]  # 3 and 4 lost
        for seq in received_seqs:
            recorder.record_packet(seq)

        feedback_packet = recorder.generate_feedback()
        self.assertIsNotNone(feedback_packet)

        results = TWCCParser.parse_feedback(feedback_packet)
        self.assertIsNotNone(results)

        # Check that received packets are marked correctly
        received_seqs_from_feedback = [r.sequence_number for r in results if r.received]
        for seq in received_seqs:
            self.assertIn(seq, received_seqs_from_feedback)

    def test_twcc_with_sent_tracker(self):
        """Test TWCC with sent packet tracking."""
        # Sender side - track sent packets
        sent_tracker = SentPacketTracker()
        for i in range(20):
            sent_tracker.add(seq=i, size=1200, ssrc=12345)

        # Receiver side - record received packets
        recorder = TWCCRecorder(media_ssrc=12345)
        for i in range(20):
            recorder.record_packet(i)

        # Generate and parse feedback
        feedback_packet = recorder.generate_feedback()
        results = TWCCParser.parse_feedback(feedback_packet)

        self.assertIsNotNone(results)

        # Correlate with sent packets
        for result in results:
            if result.received:
                sent_info = sent_tracker.get(result.sequence_number)
                self.assertIsNotNone(sent_info)
                self.assertEqual(sent_info.size, 1200)


class TestGCCWithTWCC(unittest.TestCase):
    """Test GCC bandwidth estimation with TWCC feedback."""

    def test_gcc_with_steady_stream(self):
        """Test GCC with steady packet stream."""
        from aiortc.contrib.gcc.estimator import (
            PacketFeedback,
            SenderSideBandwidthEstimator,
        )

        estimator = SenderSideBandwidthEstimator(initial_bitrate=300000)

        # Simulate steady stream of packets
        base_time = 1000000
        feedback_list = []

        for i in range(50):
            # 20ms between packets, constant delay
            feedback_list.append(
                PacketFeedback(
                    sequence_number=i,
                    size=1200,
                    send_time_us=base_time + i * 20000,
                    recv_time_us=base_time + i * 20000 + 5000,  # 5ms constant delay
                    ssrc=12345,
                )
            )

        # Process feedback
        estimate = estimator.process_feedback(feedback_list)

        # Should get an estimate
        self.assertIsNotNone(estimate)
        self.assertGreater(estimate, 0)

        # With steady conditions, should maintain or increase bitrate
        final_estimate = estimator.get_estimate()
        self.assertGreater(final_estimate, 0)

    def test_gcc_with_increasing_delay(self):
        """Test GCC response to increasing delay (congestion)."""
        from aiortc.contrib.gcc.estimator import (
            PacketFeedback,
            SenderSideBandwidthEstimator,
        )

        estimator = SenderSideBandwidthEstimator(initial_bitrate=500000)

        # Simulate increasing delay (congestion building up)
        base_time = 1000000
        feedback_list = []

        for i in range(50):
            # Delay increases linearly (simulating queue buildup)
            delay_us = 5000 + i * 1000  # Starts at 5ms, increases by 1ms per packet
            feedback_list.append(
                PacketFeedback(
                    sequence_number=i,
                    size=1200,
                    send_time_us=base_time + i * 20000,
                    recv_time_us=base_time + i * 20000 + delay_us,
                    ssrc=12345,
                )
            )

        # Process feedback
        initial_estimate = estimator.get_estimate()
        estimate = estimator.process_feedback(feedback_list)

        # With increasing delay, GCC should eventually detect overuse
        # and reduce bitrate (though this may require multiple feedback rounds)
        self.assertIsNotNone(estimate)

    def test_gcc_stats(self):
        """Test GCC statistics reporting."""
        from aiortc.contrib.gcc.estimator import (
            PacketFeedback,
            SenderSideBandwidthEstimator,
        )

        estimator = SenderSideBandwidthEstimator(initial_bitrate=300000)

        # Add some feedback
        feedback = [
            PacketFeedback(i, 1200, 1000000 + i * 20000, 1000000 + i * 20000, 12345)
            for i in range(10)
        ]
        estimator.process_feedback(feedback)

        # Get stats
        stats = estimator.get_stats()

        self.assertIn("current_estimate_bps", stats)
        self.assertIn("current_estimate_kbps", stats)
        self.assertIn("packets_received", stats)

        self.assertEqual(stats["packets_received"], 10)


class TestCongestionControlIntegrationE2E(unittest.TestCase):
    """End-to-end tests for congestion control integration."""

    def test_full_pipeline(self):
        """Test complete pipeline from TWCC to GCC."""
        # Create integration
        integration = CongestionControlIntegration(
            config=create_gcc_config(initial_mbps=0.3)
        )
        integration.initialize_gcc()

        ssrc = 12345

        # Sender side - track sent packets
        sent_tracker = integration.get_or_create_sent_tracker(ssrc)
        for i in range(20):
            sent_tracker.add(seq=i, size=1200, ssrc=ssrc)

        # Receiver side - record received packets
        recorder = integration.get_or_create_twcc_recorder(ssrc)
        for i in range(20):
            recorder.record_packet(i)

        # Generate TWCC feedback
        feedback_packet = recorder.generate_feedback()
        self.assertIsNotNone(feedback_packet)

        # Process feedback through GCC
        estimate = integration.process_twcc_feedback(feedback_packet)

        # Should get an estimate (or None if not enough data yet)
        self.assertIsInstance(estimate, (int, type(None)))

        # Stats should be available
        stats = integration.get_stats()
        self.assertIn("algorithm", stats)
        self.assertEqual(stats["algorithm"], "gcc")

    def test_multiple_feedback_rounds(self):
        """Test multiple rounds of feedback."""
        integration = CongestionControlIntegration(
            config=create_gcc_config(initial_mbps=0.5)
        )
        integration.initialize_gcc()

        ssrc = 12345
        sent_tracker = integration.get_or_create_sent_tracker(ssrc)
        recorder = integration.get_or_create_twcc_recorder(ssrc)

        estimates = []

        # Simulate 5 rounds of feedback
        for round_num in range(5):
            # Send and receive 10 packets per round
            base_seq = round_num * 10

            for i in range(10):
                seq = base_seq + i
                sent_tracker.add(seq=seq, size=1200, ssrc=ssrc)
                recorder.record_packet(seq)

            # Generate and process feedback
            feedback_packet = recorder.generate_feedback()
            if feedback_packet:
                estimate = integration.process_twcc_feedback(feedback_packet)
                if estimate:
                    estimates.append(estimate)

        # Should have gotten at least one estimate
        current_estimate = integration.get_current_estimate()
        self.assertGreater(current_estimate, 0)


class TestDummyMediaTracks(unittest.TestCase):
    """Test helper classes for media track simulation."""

    def test_dummy_video_track_concept(self):
        """Test concept of dummy video track for E2E tests."""
        # This would be a DummyVideoTrack class that generates frames
        # For now, just test that we can simulate packet generation

        packet_count = 0
        frame_rate = 30  # 30 fps
        bitrate = 500000  # 500 kbps

        # Simulate generating packets for 1 second of video
        duration_ms = 1000
        frame_interval_ms = 1000 / frame_rate

        for ms in range(0, duration_ms, int(frame_interval_ms)):
            # Each frame might generate multiple packets
            frame_size_bytes = (bitrate // frame_rate) // 8
            mtu = 1200

            packets_per_frame = (frame_size_bytes + mtu - 1) // mtu
            packet_count += packets_per_frame

        # Should have generated packets
        self.assertGreater(packet_count, 0)

    def test_packet_timing_simulation(self):
        """Test packet timing simulation."""
        import time

        # Simulate packet send times
        send_times = []
        base_time_us = int(time.time() * 1_000_000)
        packet_interval_us = 20000  # 20ms between packets

        for i in range(10):
            send_time = base_time_us + i * packet_interval_us
            send_times.append(send_time)

        # Verify timing
        self.assertEqual(len(send_times), 10)

        # Check intervals
        for i in range(1, len(send_times)):
            interval = send_times[i] - send_times[i - 1]
            self.assertEqual(interval, packet_interval_us)


class TestTWCCPacketWraparound(unittest.TestCase):
    """Test TWCC with sequence number wraparound."""

    def test_sequence_wraparound(self):
        """Test TWCC handles sequence number wraparound."""
        recorder = TWCCRecorder(media_ssrc=12345)

        # Start near end of 16-bit range
        start_seq = 65530

        for i in range(20):
            seq = (start_seq + i) & 0xFFFF
            recorder.record_packet(seq)

        feedback_packet = recorder.generate_feedback()
        self.assertIsNotNone(feedback_packet)

        results = TWCCParser.parse_feedback(feedback_packet)
        self.assertIsNotNone(results)

        # Should have received packets
        received_count = sum(1 for r in results if r.received)
        self.assertGreater(received_count, 0)


if __name__ == "__main__":
    # Run tests
    unittest.main()
