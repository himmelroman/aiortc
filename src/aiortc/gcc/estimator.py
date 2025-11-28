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
from aiortc.twcc.sender import PacketResult, SentPacketInfo

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

        # Track incoming bitrate for clamping (like pion/libwebrtc)
        self._incoming_bitrate = RateCounter(1000, 8000)

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
            # Track incoming bitrate for rate clamping (like pion/libwebrtc)
            self._incoming_bitrate.add(packet.size, packet.recv_time_us // 1000)

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
            # Get incoming bitrate to clamp increases (prevents wild oscillations)
            # This matches pion/libwebrtc behavior: limit increase to 1.5× received rate
            incoming_rate = self._incoming_bitrate.rate(now_ms)

            import logging
            logger = logging.getLogger(__name__)
            logger.debug(
                f"DelayBased update: detector={self._overuse_detector.state()}, "
                f"incoming_rate={incoming_rate/1_000_000 if incoming_rate else 0:.2f} Mbps"
            )

            target_bitrate = self._rate_control.update(
                self._overuse_detector.state(), incoming_rate, now_ms
            )

            if target_bitrate is not None:
                logger.debug(f"  → AIMD returned: {target_bitrate/1_000_000:.2f} Mbps")
                self._last_update_ms = now_ms
                return target_bitrate

        return None

    def get_current_estimate(self) -> int:
        """Get current bitrate estimate."""
        return self._rate_control.current_bitrate


class LossBasedController:
    """
    Loss-based bandwidth estimation controller.

    Acts as a limiter on delay-based estimates when packet loss is detected.
    Based on pion/libwebrtc implementation.
    """

    # Constants from draft-ietf-rmcat-gcc-02#section-6
    INCREASE_LOSS_THRESHOLD = 0.02  # 2%
    INCREASE_TIME_THRESHOLD_MS = 200
    INCREASE_FACTOR = 1.05  # 5% increase

    DECREASE_LOSS_THRESHOLD = 0.1  # 10%
    DECREASE_TIME_THRESHOLD_MS = 200

    def __init__(
        self,
        initial_bitrate: int = 300000,
        min_bitrate: int = 100000,
        max_bitrate: int = 100000000,
    ) -> None:
        """
        Initialize loss-based controller.

        Args:
            initial_bitrate: Initial bitrate in bits per second
            min_bitrate: Minimum bitrate in bits per second (default 100 kbps)
            max_bitrate: Maximum bitrate in bits per second (default 100 Mbps)
        """
        self._current_bitrate = initial_bitrate
        self._min_bitrate = min_bitrate
        self._max_bitrate = max_bitrate

        # EMA tracking
        self._average_loss = 0.0
        self._last_loss_update_ms: Optional[int] = None

        # Time-gated adjustments
        self._last_increase_ms: Optional[int] = None
        self._last_decrease_ms: Optional[int] = None

    def update(
        self,
        expected_packets: int,
        received_packets: int,
        now_ms: int,
    ) -> None:
        """
        Update internal loss estimate based on packet loss.

        Args:
            expected_packets: Number of expected packets
            received_packets: Number of received packets
            now_ms: Current time in milliseconds
        """
        if expected_packets == 0:
            return

        loss_ratio = (expected_packets - received_packets) / expected_packets

        # Update average loss with EMA (200ms time constant)
        if self._last_loss_update_ms is None:
            self._average_loss = loss_ratio
        else:
            delta_ms = now_ms - self._last_loss_update_ms
            self._average_loss = self._exponential_moving_average(
                delta_ms, self._average_loss, loss_ratio
            )
        self._last_loss_update_ms = now_ms

        # Determine whether to use average or current for increase/decrease
        # Pion uses max for increase decision, min for decrease decision
        increase_loss = max(self._average_loss, loss_ratio)
        decrease_loss = min(self._average_loss, loss_ratio)

        import logging
        logger = logging.getLogger(__name__)

        # Check increase condition
        if increase_loss < self.INCREASE_LOSS_THRESHOLD:
            if (
                self._last_increase_ms is None
                or (now_ms - self._last_increase_ms) > self.INCREASE_TIME_THRESHOLD_MS
            ):
                logger.info(
                    f"Loss controller increasing; averageLoss: {self._average_loss:.4f}, "
                    f"decreaseLoss: {decrease_loss:.4f}, increaseLoss: {increase_loss:.4f}"
                )
                self._last_increase_ms = now_ms
                new_bitrate = int(self.INCREASE_FACTOR * self._current_bitrate)
                self._current_bitrate = max(
                    self._min_bitrate, min(new_bitrate, self._max_bitrate)
                )

        # Check decrease condition
        elif decrease_loss > self.DECREASE_LOSS_THRESHOLD:
            if (
                self._last_decrease_ms is None
                or (now_ms - self._last_decrease_ms) > self.DECREASE_TIME_THRESHOLD_MS
            ):
                logger.info(
                    f"Loss controller decreasing; averageLoss: {self._average_loss:.4f}, "
                    f"decreaseLoss: {decrease_loss:.4f}, increaseLoss: {increase_loss:.4f}"
                )
                self._last_decrease_ms = now_ms
                # Proportional decrease based on loss
                new_bitrate = int(self._current_bitrate * (1 - 0.5 * decrease_loss))
                self._current_bitrate = max(
                    self._min_bitrate, min(new_bitrate, self._max_bitrate)
                )

    def get_estimate(self, wanted_rate: int) -> int:
        """
        Get loss-limited bandwidth estimate.

        This acts as a limiter on the delay-based estimate.
        Returns the minimum of the wanted rate and the internal loss-based bitrate.

        Args:
            wanted_rate: The delay-based estimate to potentially limit

        Returns:
            Limited bitrate estimate in bps
        """
        # Initialize if needed
        if self._current_bitrate <= 0:
            self._current_bitrate = max(
                self._min_bitrate, min(wanted_rate, self._max_bitrate)
            )

        # Return minimum of wanted rate and internal bitrate (acts as limiter)
        return min(wanted_rate, self._current_bitrate)

    def get_current_estimate(self) -> int:
        """Get current internal bitrate estimate."""
        return self._current_bitrate

    def get_average_loss(self) -> float:
        """Get current average loss ratio."""
        return self._average_loss

    def _exponential_moving_average(
        self, delta_ms: int, prev: float, sample: float
    ) -> float:
        """
        Calculate EMA with 200ms time constant.

        Matches pion formula:
        sample + exp(-delta_ms/200.0) * (prev - sample)

        Args:
            delta_ms: Time delta in milliseconds
            prev: Previous average value
            sample: New sample value

        Returns:
            Updated average
        """
        import math

        return sample + math.exp(-delta_ms / 200.0) * (prev - sample)


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
        max_bitrate: int = 50000000,
    ) -> None:
        """
        Initialize bandwidth estimator.

        Args:
            initial_bitrate: Initial bitrate in bps (default 300 kbps)
            min_bitrate: Minimum bitrate in bps (default 30 kbps)
            max_bitrate: Maximum bitrate in bps (default 50 Mbps, matching VP8 encoder)
        """
        self.initial_bitrate = initial_bitrate
        self.min_bitrate = min_bitrate
        self.max_bitrate = max_bitrate

        # Controllers
        self._delay_controller = DelayBasedController(initial_bitrate)
        self._loss_controller = LossBasedController(
            initial_bitrate, min_bitrate, max_bitrate
        )

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
        # Count packets in sequence number range to detect loss
        if feedback and len(feedback) > 0:
            # Get sequence number range from feedback
            seq_nums = sorted([p.sequence_number for p in feedback])
            min_seq = seq_nums[0]
            max_seq = seq_nums[-1]

            # Expected packets = full range of sequence numbers
            expected = max_seq - min_seq + 1
            # Received packets = number in feedback
            received = len(feedback)

            loss_rate = (expected - received) / expected if expected > 0 else 0

            import logging
            logger = logging.getLogger(__name__)
            logger.debug(
                f"GCC Loss: expected={expected}, received={received}, "
                f"loss_rate={loss_rate:.1%}, seq_range=[{min_seq}-{max_seq}]"
            )

            # Update loss controller's internal state (no return value)
            if expected > 0:
                self._loss_controller.update(expected, received, now_ms)

        # Apply loss-based limiter to delay-based estimate
        # This matches pion's pattern: lossStats := e.lossController.getEstimate(delayStats.TargetBitrate)
        import logging
        logger = logging.getLogger(__name__)

        if delay_estimate is not None:
            # Get loss-limited estimate
            limited_estimate = self._loss_controller.get_estimate(delay_estimate)
            self._current_estimate = limited_estimate

            logger.debug(
                f"GCC Estimates: delay={delay_estimate/1_000_000:.2f} Mbps, "
                f"loss_limited={limited_estimate/1_000_000:.2f} Mbps, "
                f"loss_avg={self._loss_controller.get_average_loss():.1%}"
            )
            if limited_estimate < delay_estimate:
                logger.debug(
                    f"  → Loss controller LIMITING: {delay_estimate/1_000_000:.2f} → {limited_estimate/1_000_000:.2f} Mbps"
                )
        else:
            logger.debug(f"  → No delay estimate, keeping current={self._current_estimate/1_000_000:.2f} Mbps")

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
            "loss_average": self._loss_controller.get_average_loss(),
            "incoming_bitrate_bps": self._incoming_bitrate.rate(now_ms),
            "packets_sent": self._packets_sent,
            "packets_received": self._packets_received,
        }
