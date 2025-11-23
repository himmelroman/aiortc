"""
Kalman filter for GCC delay variation estimation.

Direct port from pion's pkg/gcc/kalman.go
"""

import math

# Constant (matching pion line 12)
CHI = 0.001


class KalmanFilter:
    """
    1D Kalman filter for delay variation estimation.

    Direct port of pion's kalman struct.
    Reference: kalman.go lines 17-97
    """

    def __init__(
        self,
        initial_estimate: float = 0.0,  # time.Duration in Go
        initial_estimate_error: float = 0.1,  # Line 62
        process_uncertainty: float = 1e-3,  # Q_i, line 61
        measurement_uncertainty: float = 0.0,  # Line 63
        disable_measurement_uncertainty_updates: bool = False,  # Line 64
    ):
        """
        Create new Kalman filter.

        Direct port of pion's newKalman function.
        Reference: kalman.go lines 57-71

        Args:
            initial_estimate: Initial delay estimate in milliseconds
            initial_estimate_error: Initial estimate error (converted to variance)
            process_uncertainty: Process noise Q
            measurement_uncertainty: Measurement noise R
            disable_measurement_uncertainty_updates: Disable adaptive R updates
        """
        self.gain = 0.0  # Line 59
        self.estimate = initial_estimate  # Line 60, time.Duration in Go (we use ms)
        self.process_uncertainty = process_uncertainty  # Line 61
        # Line 62, 41: Only need variance from now on (e * e)
        self.estimate_error = initial_estimate_error * initial_estimate_error
        self.measurement_uncertainty = measurement_uncertainty  # Line 63
        self.disable_measurement_uncertainty_updates = (
            disable_measurement_uncertainty_updates  # Line 64
        )

    def update_estimate(self, measurement: float) -> float:
        """
        Update Kalman filter with new delay measurement.

        Direct port of pion's updateEstimate method.
        Reference: kalman.go lines 73-97

        Args:
            measurement: Raw delay variation measurement in milliseconds

        Returns:
            Filtered delay estimate in milliseconds
        """
        # Innovation (difference between measurement and prediction)
        # Line 74: z := measurement - k.estimate
        z = measurement - self.estimate

        # Line 76: zms := float64(z.Microseconds()) / 1000.0
        # Note: In Go, measurement is time.Duration (nanoseconds), converted to ms
        # In Python, we're already working in ms, so zms = z
        zms = z

        # ADAPTIVE MEASUREMENT UNCERTAINTY (key difference from standard Kalman)
        # Lines 78-87
        if not self.disable_measurement_uncertainty_updates:
            # Line 79: alpha := math.Pow((1 - chi), 30.0/(1000.0*5*float64(time.Millisecond)))
            # Note: In Go, time.Millisecond = 1e6 nanoseconds
            # The formula is: (1 - chi)^(30 / (1000 * 5 * 1)) = (1 - chi)^(30/5000) = (1 - chi)^0.006
            alpha = math.pow(1 - CHI, 30.0 / 5000.0)

            # Lines 80-81
            root = math.sqrt(self.measurement_uncertainty)
            root3 = 3 * root

            # Lines 82-86: Clamp innovation to 3σ
            if zms > root3:
                self.measurement_uncertainty = max(
                    alpha * self.measurement_uncertainty + (1 - alpha) * root3 * root3,
                    1.0,
                )
            else:
                self.measurement_uncertainty = max(
                    alpha * self.measurement_uncertainty + (1 - alpha) * zms * zms,
                    1.0,
                )

        # Standard Kalman update
        # Line 89
        estimate_uncertainty = self.estimate_error + self.process_uncertainty

        # Line 90: Kalman gain
        k_gain = estimate_uncertainty / (
            estimate_uncertainty + self.measurement_uncertainty
        )
        self.gain = k_gain

        # Line 92: Update estimate
        # Note: In Go, time.Duration is int64 nanoseconds, converted from ms
        # k.estimate += time.Duration(k.gain * zms * float64(time.Millisecond))
        # time.Millisecond = 1e6 nanoseconds, so this is: estimate += gain * zms (in ms)
        self.estimate += k_gain * zms

        # Line 94: Update estimate error (covariance)
        self.estimate_error = (1 - k_gain) * estimate_uncertainty

        # Line 96
        return self.estimate
