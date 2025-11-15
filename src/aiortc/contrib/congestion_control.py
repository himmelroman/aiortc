"""
Congestion control configuration and integration for aiortc.

Provides configuration API and integration with RTCPeerConnection for
TWCC/GCC-based congestion control.
"""

import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class CongestionControlAlgorithm(Enum):
    """Congestion control algorithm selection."""

    REMB = "remb"  # Legacy receiver estimated max bitrate
    GCC = "gcc"  # Google Congestion Control (TWCC + GCC)


@dataclass
class CongestionControlConfig:
    """
    Configuration for congestion control.

    Attributes:
        algorithm: Algorithm to use (REMB or GCC)
        initial_bitrate: Initial bitrate estimate in bps
        min_bitrate: Minimum bitrate in bps
        max_bitrate: Maximum bitrate in bps
        enable_twcc: Enable Transport-Wide Congestion Control
    """

    algorithm: CongestionControlAlgorithm = CongestionControlAlgorithm.GCC
    initial_bitrate: int = 300000  # 300 kbps
    min_bitrate: int = 30000  # 30 kbps
    max_bitrate: int = 2500000  # 2.5 mbps
    enable_twcc: bool = True


def create_gcc_config(
    initial_mbps: float = 0.3,
    min_mbps: float = 0.03,
    max_mbps: float = 2.5,
) -> CongestionControlConfig:
    """
    Create GCC congestion control configuration.

    Args:
        initial_mbps: Initial bitrate in megabits per second
        min_mbps: Minimum bitrate in megabits per second
        max_mbps: Maximum bitrate in megabits per second

    Returns:
        CongestionControlConfig with GCC settings
    """
    return CongestionControlConfig(
        algorithm=CongestionControlAlgorithm.GCC,
        initial_bitrate=int(initial_mbps * 1_000_000),
        min_bitrate=int(min_mbps * 1_000_000),
        max_bitrate=int(max_mbps * 1_000_000),
        enable_twcc=True,
    )


def create_remb_config(
    initial_mbps: float = 0.3,
    min_mbps: float = 0.03,
    max_mbps: float = 2.5,
) -> CongestionControlConfig:
    """
    Create REMB congestion control configuration (legacy).

    Args:
        initial_mbps: Initial bitrate in megabits per second
        min_mbps: Minimum bitrate in megabits per second
        max_mbps: Maximum bitrate in megabits per second

    Returns:
        CongestionControlConfig with REMB settings
    """
    return CongestionControlConfig(
        algorithm=CongestionControlAlgorithm.REMB,
        initial_bitrate=int(initial_mbps * 1_000_000),
        min_bitrate=int(min_mbps * 1_000_000),
        max_bitrate=int(max_mbps * 1_000_000),
        enable_twcc=False,
    )


class CongestionControlIntegration:
    """
    Integration layer for congestion control in RTCPeerConnection.

    Coordinates between TWCC feedback, GCC estimation, and RTP senders/receivers.
    """

    def __init__(self, config: Optional[CongestionControlConfig] = None) -> None:
        """
        Initialize congestion control integration.

        Args:
            config: Congestion control configuration (defaults to GCC)
        """
        self.config = config or CongestionControlConfig()

        # Components (initialized when needed)
        self._gcc_estimator = None
        self._twcc_recorders = {}  # ssrc -> TWCCRecorder
        self._sent_trackers = {}  # ssrc -> SentPacketTracker
        self._transport_seq_manager = None

    def is_gcc_enabled(self) -> bool:
        """Check if GCC is enabled."""
        return (
            self.config.algorithm == CongestionControlAlgorithm.GCC
            and self.config.enable_twcc
        )

    def get_twcc_extension_uri(self) -> Optional[str]:
        """Get TWCC RTP extension URI if enabled."""
        if self.config.enable_twcc:
            return "http://www.ietf.org/id/draft-holmer-rmcat-transport-wide-cc-extensions-01"
        return None

    def initialize_gcc(self) -> None:
        """Initialize GCC components if not already initialized."""
        if not self.is_gcc_enabled():
            return

        if self._gcc_estimator is None:
            from aiortc.gcc.estimator import SenderSideBandwidthEstimator

            self._gcc_estimator = SenderSideBandwidthEstimator(
                initial_bitrate=self.config.initial_bitrate,
                min_bitrate=self.config.min_bitrate,
                max_bitrate=self.config.max_bitrate,
            )

        if self._transport_seq_manager is None:
            from aiortc.twcc.receiver import TransportSequenceNumberManager

            self._transport_seq_manager = TransportSequenceNumberManager()

    def get_or_create_twcc_recorder(self, ssrc: int):
        """Get or create TWCC recorder for an SSRC."""
        if not self.is_gcc_enabled():
            return None

        if ssrc not in self._twcc_recorders:
            from aiortc.twcc.receiver import TWCCRecorder

            self._twcc_recorders[ssrc] = TWCCRecorder(media_ssrc=ssrc)

        return self._twcc_recorders[ssrc]

    def get_or_create_sent_tracker(self, ssrc: int):
        """Get or create sent packet tracker for an SSRC."""
        if not self.is_gcc_enabled():
            return None

        if ssrc not in self._sent_trackers:
            from aiortc.twcc.sender import SentPacketTracker

            self._sent_trackers[ssrc] = SentPacketTracker()

        return self._sent_trackers[ssrc]

    def get_transport_seq_manager(self):
        """Get transport sequence number manager."""
        self.initialize_gcc()
        return self._transport_seq_manager

    def get_gcc_estimator(self):
        """Get GCC bandwidth estimator."""
        self.initialize_gcc()
        return self._gcc_estimator

    def process_twcc_feedback(self, rtcp_packet: bytes) -> Optional[int]:
        """
        Process TWCC feedback packet and update bandwidth estimate.

        Args:
            rtcp_packet: Raw RTCP TWCC feedback packet

        Returns:
            Updated bitrate estimate in bps, or None
        """
        if not self.is_gcc_enabled():
            return None

        from aiortc.gcc.estimator import PacketFeedbackProcessor
        from aiortc.twcc.sender import TWCCParser

        # Parse TWCC feedback
        twcc_results = TWCCParser.parse_feedback(rtcp_packet)
        if not twcc_results:
            return None

        # Get sent packet info for correlation
        # This is a simplification - in reality we'd track per-SSRC
        all_sent_packets = []
        for tracker in self._sent_trackers.values():
            if twcc_results:
                min_seq = min(r.sequence_number for r in twcc_results)
                max_seq = max(r.sequence_number for r in twcc_results)
                all_sent_packets.extend(tracker.get_range(min_seq, max_seq))

        if not all_sent_packets:
            return None

        # Process through feedback processor
        # Use first received packet time as reference
        reference_time_us = 0
        for result in twcc_results:
            if result.received:
                # This is simplified - real implementation would extract from RTCP
                reference_time_us = int(time.time() * 1_000_000)
                break

        feedback = PacketFeedbackProcessor.process_feedback(
            twcc_results, all_sent_packets, reference_time_us
        )

        # Update GCC estimate
        if self._gcc_estimator and feedback:
            return self._gcc_estimator.process_feedback(feedback)

        return None

    def get_current_estimate(self) -> int:
        """Get current bandwidth estimate."""
        if self._gcc_estimator:
            return self._gcc_estimator.get_estimate()
        return self.config.initial_bitrate

    def get_stats(self) -> dict:
        """Get congestion control statistics."""
        stats = {
            "algorithm": self.config.algorithm.value,
            "enabled": self.is_gcc_enabled(),
            "current_estimate_bps": self.get_current_estimate(),
        }

        if self._gcc_estimator:
            stats.update(self._gcc_estimator.get_stats())

        return stats
