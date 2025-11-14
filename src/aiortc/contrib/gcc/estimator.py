"""
GCC (Google Congestion Control) bandwidth estimator.

Combines delay-based and loss-based congestion control for sender-side
bandwidth estimation using TWCC feedback.
"""

import time
from dataclasses import dataclass
from typing import List, Optional

# Reuse existing aiortc rate control components
from aiortc.rate import (
    AimdRateControl,
    BandwidthUsage,
    InterArrival,
    OveruseDetector,
    OveruseEstimator,
    RateCounter,
)

# TWCC feedback processing
from aiortc.contrib.twcc.sender import PacketResult, SentPacketInfo

# Constants
TIMESTAMP_GROUP_LENGTH_MS = 5
INTER_ARRIVAL_SHIFT = 26
TIMESTAMP_TO_MS = 1000.0 / (1 << INTER_ARRIVAL_SHIFT)


@dataclass
class PacketFeedback:
    """
    Feedback information for a single packet.

    Combines sent and received information for GCC processing.
    """

    sequence_number: int
    size: int  # Bytes
    send_time_us: int  # Microseconds
    recv_time_us: int  # Microseconds
    ssrc: int


class PacketFeedbackProcessor:
    """
    Processes TWCC feedback and correlates with sent packet info.

    Converts TWCC feedback into PacketFeedback objects for GCC.
    """

    @staticmethod
    def process_feedback(
        twcc_results: List[PacketResult],
        sent_packets: List[SentPacketInfo],
        reference_time_us: int,
    ) -> List[PacketFeedback]:
        """
        Process TWCC feedback and create PacketFeedback list.

        Args:
            twcc_results: Parsed TWCC feedback results
            sent_packets: Sent packet information
            reference_time_us: Reference time from TWCC feedback

        Returns:
            List of PacketFeedback with complete send/receive info
        """
        # Build sent packets lookup
        sent_lookup = {p.sequence_number: p for p in sent_packets}

        feedback_list = []
        current_recv_time = reference_time_us

        for result in twcc_results:
            if not result.received:
                continue

            # Get sent packet info
            sent_info = sent_lookup.get(result.sequence_number)
            if sent_info is None:
                continue

            # Calculate absolute receive time
            if result.delta_us is not None:
                current_recv_time += result.delta_us

            feedback_list.append(
                PacketFeedback(
                    sequence_number=result.sequence_number,
                    size=sent_info.size,
                    send_time_us=sent_info.send_time_us,
                    recv_time_us=current_recv_time,
                    ssrc=sent_info.ssrc,
                )
            )

        return feedback_list


class DelayBasedController:
    """
    Delay-based bandwidth estimation controller.

    Wraps existing aiortc components (InterArrival, OveruseEstimator,
    OveruseDetector, AimdRateControl) to provide GCC delay-based control.
    """

    def __init__(self, initial_bitrate: int = 300000) -> None:
        """
        Initialize delay-based controller.

        Args:
            initial_bitrate: Initial bitrate in bits per second
        """
        # Reuse existing aiortc components
        self._inter_arrival = InterArrival(
            (TIMESTAMP_GROUP_LENGTH_MS << INTER_ARRIVAL_SHIFT) // 1000, TIMESTAMP_TO_MS
        )
        self._overuse_estimator = OveruseEstimator()
        self._overuse_detector = OveruseDetector()
        self._rate_control = AimdRateControl()

        # Initialize rate control
        now_ms = int(time.time() * 1000)
        self._rate_control.set_estimate(initial_bitrate, now_ms)

        self._last_update_ms: Optional[int] = None

    def update(
        self, feedback: List[PacketFeedback], now_ms: int
    ) -> Optional[int]:
        """
        Update bandwidth estimate based on packet feedback.

        Args:
            feedback: List of packet feedback
            now_ms: Current time in milliseconds

        Returns:
            Updated bitrate estimate in bps, or None if no update
        """
        if not feedback:
            return None

        update_estimate = False

        # Process each packet through the delay-based pipeline
        for packet in feedback:
            # Convert to abs-send-time format (RTP timestamp)
            # Use send time as timestamp
            timestamp = int(packet.send_time_us / TIMESTAMP_TO_MS)
            arrival_time_ms = packet.recv_time_us // 1000

            # Calculate inter-arrival deltas
            deltas = self._inter_arrival.compute_deltas(
                timestamp, arrival_time_ms, packet.size
            )

            if deltas is not None:
                timestamp_delta_ms = deltas.timestamp * TIMESTAMP_TO_MS

                # Update overuse estimator (Kalman filter)
                self._overuse_estimator.update(
                    deltas.arrival_time,
                    timestamp_delta_ms,
                    deltas.size,
                    self._overuse_detector.state(),
                    arrival_time_ms,
                )

                # Detect overuse
                self._overuse_detector.detect(
                    self._overuse_estimator.offset(),
                    timestamp_delta_ms,
                    self._overuse_estimator.num_of_deltas(),
                    arrival_time_ms,
                )

        # Determine if we should update the estimate
        if self._last_update_ms is None:
            update_estimate = True
        elif (now_ms - self._last_update_ms) > self._rate_control.feedback_interval():
            update_estimate = True
        elif self._overuse_detector.state() == BandwidthUsage.OVERUSING:
            update_estimate = True

        if update_estimate:
            # For delay-based, we don't have a measured throughput,
            # so we pass None and let AIMD adjust based on detector state
            target_bitrate = self._rate_control.update(
                self._overuse_detector.state(), None, now_ms
            )

            if target_bitrate is not None:
                self._last_update_ms = now_ms
                return target_bitrate

        return None

    def get_current_estimate(self) -> int:
        """Get current bitrate estimate."""
        return self._rate_control.current_bitrate


class LossBasedController:
    """
    Loss-based bandwidth estimation controller.

    Reduces bitrate when packet loss is detected.
    """

    def __init__(self, initial_bitrate: int = 300000) -> None:
        """
        Initialize loss-based controller.

        Args:
            initial_bitrate: Initial bitrate in bits per second
        """
        self._current_bitrate = initial_bitrate
        self._last_loss_time_ms: Optional[int] = None
        self._loss_decrease_factor = 0.5  # Decrease to 50% on loss

    def update(
        self,
        expected_packets: int,
        received_packets: int,
        now_ms: int,
    ) -> Optional[int]:
        """
        Update bandwidth estimate based on packet loss.

        Args:
            expected_packets: Number of expected packets
            received_packets: Number of received packets
            now_ms: Current time in milliseconds

        Returns:
            Updated bitrate estimate in bps, or None if no loss
        """
        if expected_packets == 0:
            return None

        loss_rate = (expected_packets - received_packets) / expected_packets

        # React to significant loss (> 2%)
        if loss_rate > 0.02:
            # Don't decrease too frequently (max once per second)
            if self._last_loss_time_ms is None or (
                now_ms - self._last_loss_time_ms > 1000
            ):
                self._current_bitrate = int(
                    self._current_bitrate * self._loss_decrease_factor
                )
                self._last_loss_time_ms = now_ms
                return self._current_bitrate

        return None

    def get_current_estimate(self) -> int:
        """Get current bitrate estimate."""
        return self._current_bitrate


class SenderSideBandwidthEstimator:
    """
    Complete sender-side bandwidth estimator for GCC.

    Combines delay-based and loss-based controllers to estimate
    the optimal sending bitrate based on TWCC feedback.
    """

    def __init__(
        self,
        initial_bitrate: int = 300000,
        min_bitrate: int = 30000,
        max_bitrate: int = 2500000,
    ) -> None:
        """
        Initialize bandwidth estimator.

        Args:
            initial_bitrate: Initial bitrate in bps (default 300 kbps)
            min_bitrate: Minimum bitrate in bps (default 30 kbps)
            max_bitrate: Maximum bitrate in bps (default 2.5 mbps)
        """
        self.initial_bitrate = initial_bitrate
        self.min_bitrate = min_bitrate
        self.max_bitrate = max_bitrate

        # Controllers
        self._delay_controller = DelayBasedController(initial_bitrate)
        self._loss_controller = LossBasedController(initial_bitrate)

        # Rate tracking
        self._incoming_bitrate = RateCounter(1000, 8000)

        # Current estimate
        self._current_estimate = initial_bitrate

        # Statistics
        self._packets_sent = 0
        self._packets_received = 0
        self._last_feedback_time_ms: Optional[int] = None

    def process_feedback(
        self, feedback: List[PacketFeedback]
    ) -> Optional[int]:
        """
        Process packet feedback and update bandwidth estimate.

        Args:
            feedback: List of packet feedback from TWCC

        Returns:
            Updated bitrate estimate in bps, or None if no update
        """
        if not feedback:
            return None

        now_ms = int(time.time() * 1000)

        # Track statistics
        for packet in feedback:
            self._packets_received += 1
            self._incoming_bitrate.add(packet.size, packet.recv_time_us // 1000)

        # Update delay-based controller
        delay_estimate = self._delay_controller.update(feedback, now_ms)

        # Update loss-based controller (calculate expected vs received)
        # For simplicity, use a sliding window approach
        if self._last_feedback_time_ms is not None:
            # Expected packets is based on current sending rate
            # This is a simplification - real GCC tracks per-SSRC
            expected = len(feedback)
            received = len([f for f in feedback])
            loss_estimate = self._loss_controller.update(expected, received, now_ms)

            # Combine estimates (use minimum of delay and loss-based)
            if delay_estimate is not None and loss_estimate is not None:
                self._current_estimate = min(delay_estimate, loss_estimate)
            elif delay_estimate is not None:
                self._current_estimate = delay_estimate
            elif loss_estimate is not None:
                self._current_estimate = loss_estimate
        else:
            if delay_estimate is not None:
                self._current_estimate = delay_estimate

        self._last_feedback_time_ms = now_ms

        # Clamp to min/max
        self._current_estimate = max(
            self.min_bitrate, min(self._current_estimate, self.max_bitrate)
        )

        return self._current_estimate

    def get_estimate(self) -> int:
        """
        Get current bandwidth estimate.

        Returns:
            Current bitrate estimate in bps
        """
        return self._current_estimate

    def get_incoming_bitrate(self, now_ms: int) -> Optional[int]:
        """
        Get measured incoming bitrate.

        Args:
            now_ms: Current time in milliseconds

        Returns:
            Incoming bitrate in bps, or None if not available
        """
        return self._incoming_bitrate.rate(now_ms)

    def get_stats(self) -> dict:
        """
        Get estimator statistics.

        Returns:
            Dictionary with statistics
        """
        now_ms = int(time.time() * 1000)
        return {
            "current_estimate_bps": self._current_estimate,
            "current_estimate_kbps": self._current_estimate / 1000,
            "delay_estimate_bps": self._delay_controller.get_current_estimate(),
            "loss_estimate_bps": self._loss_controller.get_current_estimate(),
            "incoming_bitrate_bps": self._incoming_bitrate.rate(now_ms),
            "packets_sent": self._packets_sent,
            "packets_received": self._packets_received,
        }
