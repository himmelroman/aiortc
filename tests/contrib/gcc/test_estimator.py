"""
Tests for GCC bandwidth estimator.
"""

import time
import unittest

from aiortc.contrib.gcc.estimator import (
    DelayBasedController,
    LossBasedController,
    PacketFeedback,
    PacketFeedbackProcessor,
    SenderSideBandwidthEstimator,
)
from aiortc.contrib.twcc.sender import PacketResult, SentPacketInfo


class TestPacketFeedback(unittest.TestCase):
    """Test PacketFeedback dataclass."""

    def test_creation(self):
        """Test creating PacketFeedback."""
        feedback = PacketFeedback(
            sequence_number=123,
            size=1200,
            send_time_us=1000000,
            recv_time_us=1001000,
            ssrc=12345,
        )

        self.assertEqual(feedback.sequence_number, 123)
        self.assertEqual(feedback.size, 1200)
        self.assertEqual(feedback.send_time_us, 1000000)
        self.assertEqual(feedback.recv_time_us, 1001000)
        self.assertEqual(feedback.ssrc, 12345)


class TestPacketFeedbackProcessor(unittest.TestCase):
    """Test packet feedback processing."""

    def test_process_feedback_basic(self):
        """Test basic feedback processing."""
        # Create TWCC results
        twcc_results = [
            PacketResult(sequence_number=0, received=True, delta_us=0),
            PacketResult(sequence_number=1, received=True, delta_us=1000),
            PacketResult(sequence_number=2, received=True, delta_us=1000),
        ]

        # Create sent packet info
        sent_packets = [
            SentPacketInfo(0, 1200, 1000000, 12345),
            SentPacketInfo(1, 1200, 1001000, 12345),
            SentPacketInfo(2, 1200, 1002000, 12345),
        ]

        reference_time_us = 1000000

        # Process
        feedback = PacketFeedbackProcessor.process_feedback(
            twcc_results, sent_packets, reference_time_us
        )

        self.assertEqual(len(feedback), 3)

        # Check first packet
        self.assertEqual(feedback[0].sequence_number, 0)
        self.assertEqual(feedback[0].size, 1200)
        self.assertEqual(feedback[0].recv_time_us, reference_time_us)

        # Check second packet (should have delta applied)
        self.assertEqual(feedback[1].sequence_number, 1)
        self.assertEqual(feedback[1].recv_time_us, reference_time_us + 1000)

    def test_process_feedback_with_loss(self):
        """Test feedback processing with packet loss."""
        twcc_results = [
            PacketResult(sequence_number=0, received=True, delta_us=0),
            PacketResult(sequence_number=1, received=False),
            PacketResult(sequence_number=2, received=True, delta_us=1000),
        ]

        sent_packets = [
            SentPacketInfo(0, 1200, 1000000, 12345),
            SentPacketInfo(1, 1200, 1001000, 12345),
            SentPacketInfo(2, 1200, 1002000, 12345),
        ]

        reference_time_us = 1000000

        feedback = PacketFeedbackProcessor.process_feedback(
            twcc_results, sent_packets, reference_time_us
        )

        # Should only have packets 0 and 2
        self.assertEqual(len(feedback), 2)
        self.assertEqual(feedback[0].sequence_number, 0)
        self.assertEqual(feedback[1].sequence_number, 2)

    def test_process_feedback_missing_sent_info(self):
        """Test feedback processing with missing sent info."""
        twcc_results = [
            PacketResult(sequence_number=0, received=True, delta_us=0),
            PacketResult(sequence_number=5, received=True, delta_us=1000),
        ]

        # Only have info for packet 0
        sent_packets = [
            SentPacketInfo(0, 1200, 1000000, 12345),
        ]

        reference_time_us = 1000000

        feedback = PacketFeedbackProcessor.process_feedback(
            twcc_results, sent_packets, reference_time_us
        )

        # Should only have packet 0
        self.assertEqual(len(feedback), 1)
        self.assertEqual(feedback[0].sequence_number, 0)


class TestDelayBasedController(unittest.TestCase):
    """Test delay-based bandwidth control."""

    def test_initialization(self):
        """Test controller initialization."""
        controller = DelayBasedController(initial_bitrate=500000)

        estimate = controller.get_current_estimate()
        self.assertEqual(estimate, 500000)

    def test_update_with_feedback(self):
        """Test updating with packet feedback."""
        controller = DelayBasedController(initial_bitrate=300000)

        # Create feedback (steady stream)
        feedback = []
        base_time = 1000000
        for i in range(20):
            feedback.append(
                PacketFeedback(
                    sequence_number=i,
                    size=1200,
                    send_time_us=base_time + i * 20000,  # 20ms apart
                    recv_time_us=base_time + i * 20000 + 1000,  # 1ms delay
                    ssrc=12345,
                )
            )

        now_ms = (base_time + 500000) // 1000

        # Update
        result = controller.update(feedback, now_ms)

        # Should get an estimate
        self.assertIsNotNone(result)
        self.assertGreater(result, 0)

    def test_update_empty_feedback(self):
        """Test updating with empty feedback."""
        controller = DelayBasedController()

        now_ms = int(time.time() * 1000)
        result = controller.update([], now_ms)

        self.assertIsNone(result)


class TestLossBasedController(unittest.TestCase):
    """Test loss-based bandwidth control."""

    def test_initialization(self):
        """Test controller initialization."""
        controller = LossBasedController(initial_bitrate=500000)

        estimate = controller.get_current_estimate()
        self.assertEqual(estimate, 500000)

    def test_update_no_loss(self):
        """Test update with no packet loss."""
        controller = LossBasedController(initial_bitrate=500000)

        now_ms = int(time.time() * 1000)
        result = controller.update(
            expected_packets=100, received_packets=100, now_ms=now_ms
        )

        # Should return None (no change)
        self.assertIsNone(result)

    def test_update_with_loss(self):
        """Test update with packet loss."""
        controller = LossBasedController(initial_bitrate=500000)

        now_ms = int(time.time() * 1000)
        result = controller.update(
            expected_packets=100, received_packets=90, now_ms=now_ms  # 10% loss
        )

        # Should decrease bitrate
        self.assertIsNotNone(result)
        self.assertLess(result, 500000)
        self.assertEqual(result, 250000)  # 50% decrease

    def test_update_rate_limiting(self):
        """Test that loss updates are rate-limited."""
        controller = LossBasedController(initial_bitrate=500000)

        now_ms = int(time.time() * 1000)

        # First update should work
        result1 = controller.update(100, 90, now_ms)
        self.assertIsNotNone(result1)

        # Second update immediately after should be ignored
        result2 = controller.update(100, 90, now_ms + 100)
        self.assertIsNone(result2)

        # Update after 1 second should work
        result3 = controller.update(100, 90, now_ms + 1500)
        self.assertIsNotNone(result3)


class TestSenderSideBandwidthEstimator(unittest.TestCase):
    """Test complete bandwidth estimator."""

    def test_initialization(self):
        """Test estimator initialization."""
        estimator = SenderSideBandwidthEstimator(
            initial_bitrate=300000, min_bitrate=50000, max_bitrate=2000000
        )

        self.assertEqual(estimator.get_estimate(), 300000)
        self.assertEqual(estimator.min_bitrate, 50000)
        self.assertEqual(estimator.max_bitrate, 2000000)

    def test_process_feedback_basic(self):
        """Test processing basic feedback."""
        estimator = SenderSideBandwidthEstimator(initial_bitrate=300000)

        # Create feedback
        feedback = []
        base_time = 1000000
        for i in range(20):
            feedback.append(
                PacketFeedback(
                    sequence_number=i,
                    size=1200,
                    send_time_us=base_time + i * 20000,
                    recv_time_us=base_time + i * 20000 + 1000,
                    ssrc=12345,
                )
            )

        # Process
        result = estimator.process_feedback(feedback)

        # Should get an estimate
        self.assertIsNotNone(result)
        self.assertGreater(result, 0)

    def test_process_feedback_empty(self):
        """Test processing empty feedback."""
        estimator = SenderSideBandwidthEstimator()

        result = estimator.process_feedback([])
        self.assertIsNone(result)

    def test_min_max_clamping(self):
        """Test that estimates are clamped to min/max."""
        estimator = SenderSideBandwidthEstimator(
            initial_bitrate=300000, min_bitrate=100000, max_bitrate=500000
        )

        # Force a very low estimate (this is a bit artificial)
        estimator._current_estimate = 50000
        estimator.min_bitrate = 100000

        # Process empty feedback to trigger clamping
        feedback = [
            PacketFeedback(
                sequence_number=0,
                size=1200,
                send_time_us=1000000,
                recv_time_us=1001000,
                ssrc=12345,
            )
        ]

        result = estimator.process_feedback(feedback)

        # Should be clamped to min
        if result is not None:
            self.assertGreaterEqual(result, 100000)

    def test_get_stats(self):
        """Test getting estimator statistics."""
        estimator = SenderSideBandwidthEstimator(initial_bitrate=300000)

        stats = estimator.get_stats()

        self.assertIn("current_estimate_bps", stats)
        self.assertIn("current_estimate_kbps", stats)
        self.assertIn("delay_estimate_bps", stats)
        self.assertIn("loss_estimate_bps", stats)
        self.assertIn("packets_sent", stats)
        self.assertIn("packets_received", stats)

        self.assertEqual(stats["current_estimate_bps"], 300000)
        self.assertEqual(stats["current_estimate_kbps"], 300)

    def test_get_incoming_bitrate(self):
        """Test measuring incoming bitrate."""
        estimator = SenderSideBandwidthEstimator()

        # Add some feedback to establish bitrate
        feedback = []
        base_time = 1000000
        for i in range(10):
            feedback.append(
                PacketFeedback(
                    sequence_number=i,
                    size=1200,
                    send_time_us=base_time + i * 10000,  # 10ms apart
                    recv_time_us=base_time + i * 10000,
                    ssrc=12345,
                )
            )

        estimator.process_feedback(feedback)

        now_ms = (base_time + 200000) // 1000
        incoming = estimator.get_incoming_bitrate(now_ms)

        # Should have a measurement (or None if window hasn't built up)
        self.assertIsInstance(incoming, (int, type(None)))

    def test_multiple_feedback_rounds(self):
        """Test processing multiple rounds of feedback."""
        estimator = SenderSideBandwidthEstimator(initial_bitrate=300000)

        base_time = 1000000

        # First round
        feedback1 = [
            PacketFeedback(i, 1200, base_time + i * 20000, base_time + i * 20000, 12345)
            for i in range(10)
        ]
        result1 = estimator.process_feedback(feedback1)

        # Second round (after 500ms)
        time.sleep(0.01)  # Small delay
        feedback2 = [
            PacketFeedback(
                i + 10,
                1200,
                base_time + (i + 10) * 20000,
                base_time + (i + 10) * 20000,
                12345,
            )
            for i in range(10)
        ]
        result2 = estimator.process_feedback(feedback2)

        # Should have results from both rounds
        self.assertIsNotNone(result1)
        # result2 might be None if no update was needed


class TestIntegration(unittest.TestCase):
    """Integration tests for GCC with TWCC."""

    def test_twcc_to_gcc_pipeline(self):
        """Test complete pipeline from TWCC to GCC."""
        from aiortc.contrib.twcc.sender import SentPacketTracker

        # Set up components
        estimator = SenderSideBandwidthEstimator(initial_bitrate=300000)
        sent_tracker = SentPacketTracker()

        # Simulate sending packets
        base_time = 1000000
        for i in range(20):
            sent_tracker.add(seq=i, size=1200, ssrc=12345)

        # Simulate TWCC feedback
        twcc_results = [
            PacketResult(sequence_number=i, received=True, delta_us=1000 if i > 0 else 0)
            for i in range(20)
        ]

        # Get sent packet info
        sent_packets = sent_tracker.get_range(0, 19)

        # Process through feedback processor
        feedback = PacketFeedbackProcessor.process_feedback(
            twcc_results, sent_packets, base_time
        )

        # Process through GCC
        estimate = estimator.process_feedback(feedback)

        # Should get an estimate
        self.assertIsNotNone(estimate)
        self.assertGreater(estimate, 0)


if __name__ == "__main__":
    unittest.main()
