"""
Loss-based bandwidth estimation per IETF draft-ietf-rmcat-gcc-02 Section 6.

Ported from Pion's implementation which follows the RFC specification.
Simpler than full GCC, uses only packet loss for bandwidth estimation.

Reference: https://datatracker.ietf.org/doc/html/draft-ietf-rmcat-gcc-02#section-6
"""
import time
import math
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Constants from RFC draft-ietf-rmcat-gcc-02 Section 6
INCREASE_LOSS_THRESHOLD = 0.02  # 2% loss
INCREASE_TIME_THRESHOLD = 1.0   # 1 second - aligned with RTCP RR interval
INCREASE_FACTOR = 1.05
MIN_INCREASE_BPS = 200_000      # 200 kbps minimum additive increase (conservative hybrid AIMD)
MULTIPLICATIVE_THRESHOLD = 5_000_000  # Switch to pure multiplicative above 5 Mbps

DECREASE_LOSS_THRESHOLD = 0.1   # 10% loss
DECREASE_TIME_THRESHOLD = 1.0   # 1 second - aligned with RTCP RR interval


@dataclass
class LossStats:
    """Statistics from loss-based bandwidth estimator."""
    target_bitrate: int
    average_loss: float


class LossBasedBandwidthEstimator:
    """
    Loss-based bandwidth estimation per IETF draft-ietf-rmcat-gcc-02.

    This is the standardized fallback when delay-based estimation (full GCC)
    is not available or not working. Used by production WebRTC implementations.

    Algorithm:
    - INCREASE: When loss < 2% and 1s has passed:
      * Below 5 Mbps: Hybrid AIMD/MIMD with max(1.05x, +200 kbps) for safe startup
      * Above 5 Mbps: Pure multiplicative 1.05x per GCC spec
      * Aligned with 1-second RTCP RR interval (honest control loop)
    - DECREASE: When loss > 10% and 1s has passed → reduce based on loss ratio
    - Uses exponential moving average for smoothing
    - Conservative ramp-up: 500 kbps → 5 Mbps in ~22 seconds with 0% loss
    """

    def __init__(
        self,
        initial_bitrate: int = 2_000_000,
        min_bitrate: int = 100_000,
        max_bitrate: int = 100_000_000
    ):
        """
        Initialize loss-based bandwidth estimator.

        Args:
            initial_bitrate: Starting bitrate in bps (default 2 Mbps)
            min_bitrate: Minimum allowed bitrate in bps (default 100 kbps)
            max_bitrate: Maximum allowed bitrate in bps (default 100 Mbps)
        """
        self.max_bitrate = max_bitrate
        self.min_bitrate = min_bitrate
        self.bitrate = initial_bitrate
        self.average_loss = 0.0
        self.last_loss_update = 0.0
        self.last_increase = 0.0
        self.last_decrease = 0.0

        # Stats tracking
        self._last_packets_sent = 0
        self._last_packets_lost = 0

        logger.info(
            f"Loss-based BWE initialized: "
            f"initial={initial_bitrate/1e6:.1f} Mbps, "
            f"range=[{min_bitrate/1e6:.1f}, {max_bitrate/1e6:.1f}] Mbps"
        )

    def get_estimate(self, wanted_rate: Optional[int] = None) -> LossStats:
        """
        Get current bitrate estimate.

        Args:
            wanted_rate: Optional maximum rate to cap estimate

        Returns:
            LossStats with target bitrate and average loss
        """
        if self.bitrate <= 0:
            self.bitrate = self._clamp(wanted_rate or self.bitrate)

        if wanted_rate is not None:
            self.bitrate = min(wanted_rate, self.bitrate)

        return LossStats(
            target_bitrate=self.bitrate,
            average_loss=self.average_loss
        )

    def update_loss_estimate(self, packets_sent: int, packets_lost: int):
        """
        Update estimate based on packet loss.

        Called periodically (e.g., every 1 second) with cumulative counters.

        Args:
            packets_sent: Total packets sent (cumulative counter)
            packets_lost: Total packets lost (cumulative counter)
        """
        # Calculate delta since last update
        delta_sent = packets_sent - self._last_packets_sent
        delta_lost = packets_lost - self._last_packets_lost

        if delta_sent <= 0:
            return

        now = time.time()
        loss_ratio = delta_lost / delta_sent

        # Exponential moving average (from RFC spec)
        if self.last_loss_update > 0:
            delta_time = now - self.last_loss_update
            self.average_loss = self._average(delta_time, self.average_loss, loss_ratio)
        else:
            self.average_loss = loss_ratio

        self.last_loss_update = now

        # Use max for increase decision, min for decrease (from spec)
        increase_loss = max(self.average_loss, loss_ratio)
        decrease_loss = min(self.average_loss, loss_ratio)

        old_bitrate = self.bitrate

        # INCREASE: Low loss + time threshold met
        if (increase_loss < INCREASE_LOSS_THRESHOLD and
            (now - self.last_increase) > INCREASE_TIME_THRESHOLD):

            self.last_increase = now
            multiplicative_increase = int(INCREASE_FACTOR * self.bitrate)

            # Below 5 Mbps: Hybrid AIMD/MIMD for faster recovery at low bitrates
            # Above 5 Mbps: Pure multiplicative per GCC spec
            if self.bitrate < MULTIPLICATIVE_THRESHOLD:
                additive_increase = self.bitrate + MIN_INCREASE_BPS
                self.bitrate = self._clamp(max(multiplicative_increase, additive_increase))
                increase_type = "additive" if additive_increase > multiplicative_increase else "multiplicative"
            else:
                self.bitrate = self._clamp(multiplicative_increase)
                increase_type = "multiplicative"

            logger.info(
                f"📈 Loss BWE Increase ({increase_type}): {old_bitrate/1e6:.2f} → {self.bitrate/1e6:.2f} Mbps "
                f"(loss={increase_loss:.3f})"
            )

        # DECREASE: High loss + time threshold met
        elif (decrease_loss > DECREASE_LOSS_THRESHOLD and
              (now - self.last_decrease) > DECREASE_TIME_THRESHOLD):

            self.last_decrease = now
            decrease_factor = 1 - (0.5 * decrease_loss)
            self.bitrate = self._clamp(int(self.bitrate * decrease_factor))
            logger.info(
                f"📉 Loss BWE Decrease: {old_bitrate/1e6:.2f} → {self.bitrate/1e6:.2f} Mbps "
                f"(loss={decrease_loss:.3f})"
            )

        # Update counters
        self._last_packets_sent = packets_sent
        self._last_packets_lost = packets_lost

    def get_stats(self) -> dict:
        """Get current statistics for monitoring."""
        return {
            'current_estimate_kbps': self.bitrate / 1000,
            'average_loss': self.average_loss,
            'last_update': self.last_loss_update
        }

    def _average(self, delta: float, prev: float, sample: float) -> float:
        """
        Exponential moving average with 200ms time constant.

        Formula from RFC spec.
        """
        return sample + math.exp(-delta * 1000 / 200.0) * (prev - sample)

    def _clamp(self, value: int) -> int:
        """Clamp bitrate to min/max bounds."""
        return max(self.min_bitrate, min(self.max_bitrate, value))
