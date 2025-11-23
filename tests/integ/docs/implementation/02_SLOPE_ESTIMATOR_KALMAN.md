# Slope Estimator and Kalman Filter - Component Analysis

**Component:** `slopeEstimator` + `kalman`
**Status:** ✅ **COMPLETE** (Implemented 2025-11-22)
**Implementation:** `src/aiortc/gcc/slope_estimator.py`, `src/aiortc/gcc/kalman_filter.py`
**Verification:** Line-by-line port verified in `IMPLEMENTATION_VERIFICATION.md`

---

## Implementation Status

✅ **FULLY IMPLEMENTED AND VERIFIED**

Both SlopeEstimator and KalmanFilter have been implemented as exact line-by-line ports from pion's reference implementation. All formulas, constants, and adaptive algorithms match pion precisely.

**Key Files:**
- `src/aiortc/gcc/slope_estimator.py` - Inter-group delay variation calculation
- `src/aiortc/gcc/kalman_filter.py` - 1D Kalman filter with adaptive R
- `src/aiortc/gcc/types.py` - DelayStats and enum types

**Note:** The old `OveruseEstimator` (libwebrtc receiver-side) is still in the codebase but will be replaced/removed during integration.

---

## Purpose

1. **slopeEstimator:** Calculates inter-group delay variation (the "slope" of queuing delay)
2. **kalman:** Filters the noisy delay measurements to extract the true trend

Together, these detect whether the network queue is growing (overuse) or shrinking (underuse).

---

## Part 1: Slope Estimator

### Pion Implementation

**File:** `pkg/gcc/slope_estimator.go`

```go
type slopeEstimator struct {
    estimator        estimator  // Kalman filter
    init             bool
    group            arrivalGroup
    delayStatsWriter func(DelayStats)
}

func newSlopeEstimator(e estimator, dsw func(DelayStats)) *slopeEstimator {
    return &slopeEstimator{
        estimator:        e,
        delayStatsWriter: dsw,
    }
}

func (e *slopeEstimator) onArrivalGroup(ag arrivalGroup) {
    // First group initializes
    if !e.init {
        e.group = ag
        e.init = true
        return
    }

    // Calculate inter-group delay variation (THE CORE GCC FORMULA)
    measurement := interGroupDelayVariation(e.group, ag)

    // Time between groups
    delta := ag.arrival.Sub(e.group.arrival)

    // Update state
    e.group = ag

    // Filter measurement through Kalman and forward
    e.delayStatsWriter(DelayStats{
        Measurement:      measurement,                      // Raw d(i)
        Estimate:         e.updateEstimate(measurement),    // Kalman filtered d(i)
        Threshold:        0,                                 // Set by overuse detector
        LastReceiveDelta: delta,                            // For overuse time tracking
        Usage:            0,                                 // Set by overuse detector
        State:            0,                                 // Set by rate controller
        TargetBitrate:    0,                                 // Set by rate controller
    })
}

// THE CORE GCC FORMULA
func interGroupDelayVariation(a, b arrivalGroup) time.Duration {
    // d(i) = (t(i) - t(i-1)) - (T(i) - T(i-1))
    return b.arrival.Sub(a.arrival) - b.departure.Sub(a.departure)
}
```

### The Core Formula Explained

**Inter-group delay variation:**

```
d(i) = [t(i) - t(i-1)] - [T(i) - T(i-1)]

where:
  t(i)   = arrival time of group i (at receiver)
  t(i-1) = arrival time of group i-1
  T(i)   = departure time of group i (at sender)
  T(i-1) = departure time of group i-1
```

**Interpretation:**

- **d(i) > 0:** Packets took LONGER to arrive than expected
  - → Queue is growing → Network congestion → OVERUSE
  - Example: Sent 10ms apart, arrived 15ms apart → +5ms delay growth

- **d(i) < 0:** Packets took LESS time to arrive than expected
  - → Queue is shrinking → Network uncongested → UNDERUSE
  - Example: Sent 10ms apart, arrived 8ms apart → -2ms delay shrink

- **d(i) ≈ 0:** No change in queuing delay
  - → Stable network → NORMAL

**Why this works:**

If network is uncongested:
- Propagation delay is constant
- t(i) - t(i-1) ≈ T(i) - T(i-1)
- d(i) ≈ 0

If network is congested:
- Packets wait in queue
- Queue grows → d(i) > 0
- Queue shrinks → d(i) < 0

---

## Part 2: Kalman Filter

### Why We Need Filtering

Raw delay measurements are NOISY:
- Network jitter
- Measurement timing errors
- Packet reordering

Kalman filter extracts the TRUE TREND from noisy measurements.

### Pion Implementation

**File:** `pkg/gcc/kalman.go`

```go
const chi = 0.001

type kalman struct {
    gain                   float64       // Kalman gain
    estimate               time.Duration // Filtered delay estimate
    processUncertainty     float64       // Q_i (1e-3)
    estimateError          float64       // P_i (covariance)
    measurementUncertainty float64       // R_i (adaptive)
}

func newKalman(opts ...kalmanOption) *kalman {
    return &kalman{
        gain:                   0,
        estimate:               0,
        processUncertainty:     1e-3,
        estimateError:          0.1,
        measurementUncertainty: 0,
    }
}

func (k *kalman) updateEstimate(measurement time.Duration) time.Duration {
    // Innovation (difference between measurement and prediction)
    z := measurement - k.estimate
    zms := float64(z.Microseconds()) / 1000.0

    // ADAPTIVE MEASUREMENT UNCERTAINTY (key difference from standard Kalman)
    if !k.disableMeasurementUncertaintyUpdates {
        // alpha = (1 - chi)^(30 / (1000 * 5ms)) for 30 fps, 5ms groups
        alpha := math.Pow((1 - chi), 30.0/(1000.0*5*float64(time.Millisecond)))
        root := math.Sqrt(k.measurementUncertainty)
        root3 := 3 * root

        // If innovation is large (> 3σ), clamp it
        if zms > root3 {
            k.measurementUncertainty = math.Max(
                alpha*k.measurementUncertainty + (1-alpha)*root3*root3,
                1,
            )
        } else {
            k.measurementUncertainty = math.Max(
                alpha*k.measurementUncertainty + (1-alpha)*zms*zms,
                1,
            )
        }
    }

    // Standard Kalman update
    estimateUncertainty := k.estimateError + k.processUncertainty
    k.gain = estimateUncertainty / (estimateUncertainty + k.measurementUncertainty)

    k.estimate += time.Duration(k.gain * zms * float64(time.Millisecond))
    k.estimateError = (1 - k.gain) * estimateUncertainty

    return k.estimate
}
```

### Kalman Filter Breakdown

**State Model:**

```
State: x = delay_variation (scalar, not a vector!)

Prediction:
  x_pred = x_prev  (assume constant trend)
  P_pred = P_prev + Q  (add process noise)

Measurement:
  z = measured delay variation (from slope estimator)

Update:
  K = P_pred / (P_pred + R)  (Kalman gain)
  x = x_pred + K * (z - x_pred)  (update estimate)
  P = (1 - K) * P_pred  (update covariance)
```

**Key Parameters:**

- `Q = 1e-3` (processUncertainty): How much we expect delay to change
- `R = adaptive` (measurementUncertainty): How noisy our measurements are
- Initial `P = 0.1` (estimateError): Uncertainty in our initial estimate

**Adaptive Measurement Uncertainty:**

This is unique to GCC! Standard Kalman uses fixed R, but GCC adapts R based on recent innovation:

```
If |z - x| > 3σ:
  R = α*R + (1-α)*(3σ)²  (large innovation → increase R → trust measurement less)
Else:
  R = α*R + (1-α)*z²  (normal innovation → adapt R to match observed variance)

where α = (1 - 0.001)^(30/5000) ≈ 0.994 for 30fps, 5ms groups
```

---

## aiortc's OveruseEstimator - Why It's Wrong

### What aiortc Currently Has

**File:** `src/aiortc/rate.py`

```python
class OveruseEstimator:
    """
    Bandwidth overuse estimator.
    Adapted from the webrtc.org codebase.
    """

    def __init__(self):
        self.E = [[100.0, 0.0], [0.0, 0.1]]  # 2x2 covariance matrix
        self._offset = 0.0
        self.slope = 1 / 64
        self.process_noise = [1e-13, 1e-3]

    def update(self, time_delta_ms, timestamp_delta_ms, size_delta, ...):
        # 2D Kalman filter tracking [slope, offset]
        h = [fs_delta, 1.0]  # Measurement matrix
        residual = t_ts_delta - self.slope * h[0] - self._offset
        # ... complex 2D Kalman update
```

### The Problems

1. **2D State vs 1D State:**
   - aiortc tracks: `[slope, offset]` (network gradient + queuing delay)
   - pion tracks: `delay_variation` (just the trend)

2. **Different Input:**
   - aiortc: `time_delta_ms, timestamp_delta_ms, size_delta` (per-packet)
   - pion: `interGroupDelayVariation` (per-burst-group)

3. **Different Purpose:**
   - aiortc: libwebrtc "trendline filter" for RECEIVER-SIDE estimation
   - pion: GCC delay variation filter for SENDER-SIDE estimation

4. **Different Algorithm:**
   - aiortc: Tracks slope of delay over time (derivative)
   - pion: Tracks absolute delay variation value

### Why libwebrtc vs GCC

**libwebrtc receiver-side (what aiortc has):**
- Estimates network gradient from received packets
- Uses trendline filter (2D Kalman)
- Designed for receiver to tell sender "slow down"
- Input: Per-packet RTP timing

**GCC sender-side (what pion has):**
- Estimates queue growth from TWCC feedback
- Uses 1D Kalman for delay variation
- Designed for sender to adapt its own rate
- Input: Per-burst-group acknowledgments

**WE NEED GCC SENDER-SIDE, NOT LIBWEBRTC RECEIVER-SIDE!**

---

## Implementation Plan for aiortc

### 1. Create `kalman_filter.py`

```python
import math
from typing import Optional

CHI = 0.001

class KalmanFilter:
    """
    1D Kalman filter for delay variation estimation.

    Matches pion's kalman implementation for GCC delay-based BWE.
    """

    def __init__(
        self,
        initial_estimate: float = 0.0,
        initial_estimate_error: float = 0.1,
        process_uncertainty: float = 1e-3,
        measurement_uncertainty: float = 0.0,
        disable_measurement_uncertainty_updates: bool = False
    ):
        self.gain = 0.0
        self.estimate = initial_estimate  # Filtered delay (ms)
        self.process_uncertainty = process_uncertainty  # Q
        self.estimate_error = initial_estimate_error ** 2  # P (variance)
        self.measurement_uncertainty = measurement_uncertainty  # R
        self.disable_measurement_uncertainty_updates = disable_measurement_uncertainty_updates

    def update_estimate(self, measurement: float) -> float:
        """
        Update Kalman filter with new delay measurement.

        Args:
            measurement: Raw delay variation measurement (ms)

        Returns:
            Filtered delay estimate (ms)
        """
        # Innovation
        z = measurement - self.estimate

        # Adaptive measurement uncertainty (unique to GCC)
        if not self.disable_measurement_uncertainty_updates:
            # alpha = (1 - chi)^(30 / (1000 * 5ms))
            # For 30 fps and 5ms group length
            alpha = math.pow(1 - CHI, 30.0 / (1000.0 * 5.0))
            root = math.sqrt(self.measurement_uncertainty)
            root3 = 3 * root

            if z > root3:
                # Large innovation - increase uncertainty
                self.measurement_uncertainty = max(
                    alpha * self.measurement_uncertainty + (1 - alpha) * (root3 ** 2),
                    1.0
                )
            else:
                self.measurement_uncertainty = max(
                    alpha * self.measurement_uncertainty + (1 - alpha) * (z ** 2),
                    1.0
                )

        # Standard Kalman update
        estimate_uncertainty = self.estimate_error + self.process_uncertainty
        self.gain = estimate_uncertainty / (estimate_uncertainty + self.measurement_uncertainty)

        self.estimate += self.gain * z
        self.estimate_error = (1 - self.gain) * estimate_uncertainty

        return self.estimate
```

### 2. Create `slope_estimator.py`

```python
from typing import Callable, Optional
from dataclasses import dataclass
from .arrival_group import ArrivalGroup
from .kalman_filter import KalmanFilter

@dataclass
class DelayStats:
    """Statistics from delay-based estimation pipeline."""
    measurement: float        # Raw inter-group delay variation (ms)
    estimate: float           # Kalman filtered delay variation (ms)
    threshold: float          # Adaptive threshold (set by overuse detector)
    last_receive_delta: float # Time between groups (ms)
    usage: int                # BandwidthUsage enum value
    state: int                # RateControlState enum value
    target_bitrate: int       # Target bitrate (bps)

class SlopeEstimator:
    """
    Calculates inter-group delay variation for GCC.

    Matches pion's slopeEstimator implementation.
    """

    def __init__(
        self,
        kalman_filter: Optional[KalmanFilter] = None,
        on_delay_stats: Optional[Callable[[DelayStats], None]] = None
    ):
        self.kalman = kalman_filter or KalmanFilter()
        self.on_delay_stats = on_delay_stats
        self.initialized = False
        self.previous_group: Optional[ArrivalGroup] = None

    def on_arrival_group(self, group: ArrivalGroup) -> None:
        """
        Process an arrival group and emit delay statistics.

        Args:
            group: Arrival group from accumulator
        """
        # First group just initializes
        if not self.initialized:
            self.previous_group = group
            self.initialized = True
            return

        # Calculate inter-group delay variation (CORE GCC FORMULA)
        measurement = self._inter_group_delay_variation(self.previous_group, group)

        # Time between groups
        delta = (group.arrival - self.previous_group.arrival) * 1000  # to ms

        # Update state
        self.previous_group = group

        # Filter through Kalman and emit
        if self.on_delay_stats:
            self.on_delay_stats(DelayStats(
                measurement=measurement,
                estimate=self.kalman.update_estimate(measurement),
                threshold=0.0,  # Set by overuse detector
                last_receive_delta=delta,
                usage=0,  # Set by overuse detector
                state=0,  # Set by rate controller
                target_bitrate=0  # Set by rate controller
            ))

    def _inter_group_delay_variation(self, prev: ArrivalGroup, curr: ArrivalGroup) -> float:
        """
        Calculate inter-group delay variation.

        d(i) = [t(i) - t(i-1)] - [T(i) - T(i-1)]

        Args:
            prev: Previous arrival group
            curr: Current arrival group

        Returns:
            Delay variation in milliseconds
        """
        arrival_delta = (curr.arrival - prev.arrival) * 1000  # to ms
        departure_delta = (curr.departure - prev.departure) * 1000  # to ms
        return arrival_delta - departure_delta
```

---

## Integration Example

```python
from .arrival_group_accumulator import ArrivalGroupAccumulator
from .slope_estimator import SlopeEstimator, DelayStats
from .kalman_filter import KalmanFilter
from .overuse_detector import OveruseDetector

class DelayController:
    def __init__(self):
        self.accumulator = ArrivalGroupAccumulator()
        self.overuse_detector = OveruseDetector()

        self.slope_estimator = SlopeEstimator(
            kalman_filter=KalmanFilter(),
            on_delay_stats=self.overuse_detector.on_delay_stats
        )

    def update_delay_estimate(self, acks):
        def on_group(group):
            self.slope_estimator.on_arrival_group(group)

        self.accumulator.process_acknowledgments(acks, on_group)
```

---

## Testing Strategy

### Unit Tests

**Kalman Filter:**

1. **Constant delay:**
   - Feed measurements: [10, 10, 10, 10]
   - Verify estimate converges to 10

2. **Noisy measurements:**
   - Feed measurements: [10, 12, 9, 11, 10]
   - Verify estimate smooths to ≈10

3. **Trend detection:**
   - Feed measurements: [0, 5, 10, 15, 20]
   - Verify estimate tracks the trend

**Slope Estimator:**

1. **No congestion:**
   - Group A: depart=0s, arrive=0.1s
   - Group B: depart=0.01s, arrive=0.11s
   - d(B) = (0.11-0.1) - (0.01-0) = 0ms → No delay growth

2. **Growing queue:**
   - Group A: depart=0s, arrive=0.1s
   - Group B: depart=0.01s, arrive=0.115s
   - d(B) = (0.115-0.1) - (0.01-0) = +5ms → Queue growing

3. **Shrinking queue:**
   - Group A: depart=0s, arrive=0.1s
   - Group B: depart=0.01s, arrive=0.105s
   - d(B) = (0.105-0.1) - (0.01-0) = -5ms → Queue shrinking

### Integration Tests

Compare with pion using same arrival groups:
- Feed same groups to both
- Verify delay measurements match
- Verify Kalman estimates match

---

## References

- Pion slope_estimator: `pkg/gcc/slope_estimator.go`
- Pion kalman: `pkg/gcc/kalman.go`
- Draft-ietf-rmcat-gcc-02 Section 5.4: "Over-use detector"
- [Analysis and Design of the Google Congestion Control](https://c3lab.poliba.it/images/6/65/Gcc-analysis.pdf)

---

## Conclusion

**The slope estimator + Kalman filter are the BRAIN of GCC.**

They convert noisy packet timing into a clean signal about network congestion:

```
Arrival Groups → Delay Variation → Kalman Filter → Clean Trend → Overuse Detection
```

aiortc's `OveruseEstimator` is solving a DIFFERENT PROBLEM (libwebrtc receiver-side trendline filtering). We need to:

1. Remove `OveruseEstimator` (2D Kalman)
2. Remove `InterArrival` (wrong grouping)
3. Add `ArrivalGroupAccumulator` (from previous doc)
4. Add `SlopeEstimator` (this doc)
5. Add `KalmanFilter` (this doc)

Only then will we have the correct GCC delay-based pipeline.
