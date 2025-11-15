"""
Tests for congestion control integration.
"""

import unittest

from aiortc.contrib.congestion_control import (
    CongestionControlAlgorithm,
    CongestionControlConfig,
    CongestionControlIntegration,
    create_gcc_config,
    create_remb_config,
)


class TestCongestionControlConfig(unittest.TestCase):
    """Test congestion control configuration."""

    def test_default_config(self):
        """Test default configuration."""
        config = CongestionControlConfig()

        self.assertEqual(config.algorithm, CongestionControlAlgorithm.GCC)
        self.assertTrue(config.enable_twcc)
        self.assertEqual(config.initial_bitrate, 300000)
        self.assertEqual(config.min_bitrate, 30000)
        self.assertEqual(config.max_bitrate, 2500000)

    def test_custom_config(self):
        """Test custom configuration."""
        config = CongestionControlConfig(
            algorithm=CongestionControlAlgorithm.REMB,
            initial_bitrate=500000,
            min_bitrate=50000,
            max_bitrate=3000000,
            enable_twcc=False,
        )

        self.assertEqual(config.algorithm, CongestionControlAlgorithm.REMB)
        self.assertFalse(config.enable_twcc)
        self.assertEqual(config.initial_bitrate, 500000)

    def test_create_gcc_config(self):
        """Test GCC config factory."""
        config = create_gcc_config(initial_mbps=2.0, min_mbps=0.1, max_mbps=5.0)

        self.assertEqual(config.algorithm, CongestionControlAlgorithm.GCC)
        self.assertTrue(config.enable_twcc)
        self.assertEqual(config.initial_bitrate, 2_000_000)
        self.assertEqual(config.min_bitrate, 100_000)
        self.assertEqual(config.max_bitrate, 5_000_000)

    def test_create_remb_config(self):
        """Test REMB config factory."""
        config = create_remb_config(initial_mbps=1.0)

        self.assertEqual(config.algorithm, CongestionControlAlgorithm.REMB)
        self.assertFalse(config.enable_twcc)
        self.assertEqual(config.initial_bitrate, 1_000_000)


class TestCongestionControlIntegration(unittest.TestCase):
    """Test congestion control integration."""

    def test_initialization(self):
        """Test basic initialization."""
        integration = CongestionControlIntegration()

        self.assertIsNotNone(integration.config)
        self.assertTrue(integration.is_gcc_enabled())

    def test_initialization_with_config(self):
        """Test initialization with custom config."""
        config = create_remb_config()
        integration = CongestionControlIntegration(config)

        self.assertEqual(integration.config.algorithm, CongestionControlAlgorithm.REMB)
        self.assertFalse(integration.is_gcc_enabled())

    def test_get_twcc_extension_uri(self):
        """Test getting TWCC extension URI."""
        # GCC config (TWCC enabled)
        integration = CongestionControlIntegration()
        uri = integration.get_twcc_extension_uri()
        self.assertIsNotNone(uri)
        self.assertIn("transport-wide-cc", uri)

        # REMB config (TWCC disabled)
        remb_config = create_remb_config()
        integration_remb = CongestionControlIntegration(remb_config)
        uri_remb = integration_remb.get_twcc_extension_uri()
        self.assertIsNone(uri_remb)

    def test_initialize_gcc(self):
        """Test GCC initialization."""
        integration = CongestionControlIntegration()

        # Should be None initially
        self.assertIsNone(integration._gcc_estimator)
        self.assertIsNone(integration._transport_seq_manager)

        # Initialize
        integration.initialize_gcc()

        # Should be created
        self.assertIsNotNone(integration._gcc_estimator)
        self.assertIsNotNone(integration._transport_seq_manager)

    def test_get_or_create_twcc_recorder(self):
        """Test TWCC recorder creation."""
        integration = CongestionControlIntegration()

        ssrc = 12345
        recorder1 = integration.get_or_create_twcc_recorder(ssrc)
        recorder2 = integration.get_or_create_twcc_recorder(ssrc)

        # Should return same instance
        self.assertIs(recorder1, recorder2)

        # Different SSRC should create new recorder
        recorder3 = integration.get_or_create_twcc_recorder(67890)
        self.assertIsNot(recorder1, recorder3)

    def test_get_or_create_twcc_recorder_remb(self):
        """Test TWCC recorder returns None for REMB."""
        config = create_remb_config()
        integration = CongestionControlIntegration(config)

        recorder = integration.get_or_create_twcc_recorder(12345)
        self.assertIsNone(recorder)

    def test_get_or_create_sent_tracker(self):
        """Test sent packet tracker creation."""
        integration = CongestionControlIntegration()

        ssrc = 12345
        tracker1 = integration.get_or_create_sent_tracker(ssrc)
        tracker2 = integration.get_or_create_sent_tracker(ssrc)

        # Should return same instance
        self.assertIs(tracker1, tracker2)

    def test_get_transport_seq_manager(self):
        """Test transport sequence manager access."""
        integration = CongestionControlIntegration()

        manager = integration.get_transport_seq_manager()
        self.assertIsNotNone(manager)

        # Should return same instance
        manager2 = integration.get_transport_seq_manager()
        self.assertIs(manager, manager2)

    def test_get_gcc_estimator(self):
        """Test GCC estimator access."""
        integration = CongestionControlIntegration()

        estimator = integration.get_gcc_estimator()
        self.assertIsNotNone(estimator)

    def test_get_current_estimate_default(self):
        """Test getting current estimate before initialization."""
        integration = CongestionControlIntegration()

        estimate = integration.get_current_estimate()
        self.assertEqual(estimate, integration.config.initial_bitrate)

    def test_get_current_estimate_after_init(self):
        """Test getting current estimate after GCC init."""
        integration = CongestionControlIntegration()
        integration.initialize_gcc()

        estimate = integration.get_current_estimate()
        self.assertGreater(estimate, 0)

    def test_get_stats(self):
        """Test getting statistics."""
        integration = CongestionControlIntegration()

        stats = integration.get_stats()

        self.assertIn("algorithm", stats)
        self.assertIn("enabled", stats)
        self.assertIn("current_estimate_bps", stats)

        self.assertEqual(stats["algorithm"], "gcc")
        self.assertTrue(stats["enabled"])

    def test_get_stats_with_gcc(self):
        """Test statistics after GCC initialization."""
        integration = CongestionControlIntegration()
        integration.initialize_gcc()

        stats = integration.get_stats()

        # Should have GCC-specific stats
        self.assertIn("delay_estimate_bps", stats)
        self.assertIn("loss_estimate_bps", stats)
        self.assertIn("packets_sent", stats)
        self.assertIn("packets_received", stats)


class TestIntegrationWithTWCC(unittest.TestCase):
    """Test integration with TWCC components."""

    def test_twcc_recorder_lifecycle(self):
        """Test TWCC recorder creation and usage."""
        integration = CongestionControlIntegration()

        ssrc = 12345
        recorder = integration.get_or_create_twcc_recorder(ssrc)

        # Record some packets
        recorder.record_packet(0)
        recorder.record_packet(1)
        recorder.record_packet(2)

        # Generate feedback
        feedback = recorder.generate_feedback()
        self.assertIsNotNone(feedback)

    def test_sent_tracker_lifecycle(self):
        """Test sent packet tracker creation and usage."""
        integration = CongestionControlIntegration()

        ssrc = 12345
        tracker = integration.get_or_create_sent_tracker(ssrc)

        # Track some packets
        tracker.add(seq=0, size=1200, ssrc=ssrc)
        tracker.add(seq=1, size=1200, ssrc=ssrc)

        # Retrieve
        packet = tracker.get(0)
        self.assertIsNotNone(packet)
        self.assertEqual(packet.size, 1200)

    def test_process_twcc_feedback(self):
        """Test processing TWCC feedback."""
        from aiortc.twcc.receiver import TWCCRecorder

        integration = CongestionControlIntegration()
        integration.initialize_gcc()

        # Create and track sent packets
        ssrc = 12345
        tracker = integration.get_or_create_sent_tracker(ssrc)
        for i in range(10):
            tracker.add(seq=i, size=1200, ssrc=ssrc)

        # Generate TWCC feedback
        recorder = TWCCRecorder(media_ssrc=ssrc)
        for i in range(10):
            recorder.record_packet(i)

        feedback_packet = recorder.generate_feedback()
        self.assertIsNotNone(feedback_packet)

        # Process feedback
        estimate = integration.process_twcc_feedback(feedback_packet)

        # Should get an estimate (or None if not enough data)
        self.assertIsInstance(estimate, (int, type(None)))


if __name__ == "__main__":
    unittest.main()
