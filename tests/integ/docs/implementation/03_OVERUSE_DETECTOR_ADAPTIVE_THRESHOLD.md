# Overuse Detector and Adaptive Threshold - Component Analysis

**Component:** `overuseDetector` + `adaptiveThreshold`
**Status:** ✅ **COMPLETE** (Implemented 2025-11-22)
**Implementation:** `src/aiortc/gcc/overuse_detector.py`, `src/aiortc/gcc/adaptive_threshold.py`
**Verification:** Line-by-line port verified in `IMPLEMENTATION_VERIFICATION.md`

---

## Implementation Status

✅ **FULLY IMPLEMENTED AND VERIFIED**

Both OveruseDetector and AdaptiveThreshold have been implemented as exact line-by-line ports from pion's reference implementation. All hysteresis logic, threshold adaptation, and coefficients match pion precisely.

**Key Files:**
- `src/aiortc/gcc/overuse_detector.py` - Overuse detection with hysteresis
- `src/aiortc/gcc/adaptive_threshold.py` - Adaptive threshold [6ms, 600ms]
- `src/aiortc/gcc/types.py` - BandwidthUsage enum

**Note:** The old `OveruseDetector` (libwebrtc-based) is still in the codebase but will be replaced/removed during integration.

---

## Purpose

1. **adaptiveThreshold:** Dynamically adjusts detection threshold based on network conditions
2. **overuseDetector:** Compares delay estimate against threshold to determine bandwidth usage

Together, they decide if we're in OVERUSE, UNDERUSE, or NORMAL state.

---

## Part 1: Adaptive Threshold

### Why Adaptive?

**Fixed threshold problems:**
- Network delay varies widely (WiFi vs wired)
- Too low threshold → false overuse → unnecessary rate decrease
- Too high threshold → miss congestion → packet loss

**Adaptive solution:**
- Start at 12.5ms
- If estimate stays within threshold → slowly decrease (more sensitive)
- If estimate exceeds threshold → quickly increase (less sensitive)
- Range: 6ms to 600ms

### Pion Implementation

**File:** `pkg/gcc/adaptive_threshold.go`

```go
const maxDeltas = 60

type adaptiveThreshold struct {
    thresh                 time.Duration  // Current threshold
    overuseCoefficientUp   float64        // 0.01 (increase fast)
    overuseCoefficientDown float64        // 0.00018 (decrease slow)
    min                    time.Duration  // 6ms
    max                    time.Duration  // 600ms
    lastUpdate             time.Time
    numDeltas              int
}

func newAdaptiveThreshold(opts ...adaptiveThresholdOption) *adaptiveThreshold {
    return &adaptiveThreshold{
        thresh:                 12.5 * time.Millisecond,  // Initial
        overuseCoefficientUp:   0.01,
        overuseCoefficientDown: 0.00018,
        min:                    6 * time.Millisecond,
        max:                    600 * time.Millisecond,
        lastUpdate:             time.Time{},
        numDeltas:              0,
    }
}

func (a *adaptiveThreshold) compare(estimate, _ time.Duration) (usage, time.Duration, time.Duration) {
    a.numDeltas++

    // Need at least 2 deltas before detecting
    if a.numDeltas < 2 {
        return usageNormal, estimate, a.max
    }

    // Multiply estimate by num deltas (capped at 60)
    // T = min(numDeltas, 60) * estimate
    t := time.Duration(min(a.numDeltas, maxDeltas)) * estimate

    // Compare against threshold
    use := usageNormal
    if t > a.thresh {
        use = usageOver
    } else if t < -a.thresh {
        use = usageUnder
    }

    thresh := a.thresh
    a.update(t)  // Adapt threshold

    return use, t, thresh
}

func (a *adaptiveThreshold) update(estimate time.Duration) {
    now := time.Now()
    if a.lastUpdate.IsZero() {
        a.lastUpdate = now
    }

    absEstimate := abs(estimate)

    // Don't update if way outside range (outlier)
    if absEstimate > a.thresh+15*time.Millisecond {
        a.lastUpdate = now
        return
    }

    // Choose coefficient based on position
    k := a.overuseCoefficientUp
    if absEstimate < a.thresh {
        k = a.overuseCoefficientDown  // Inside threshold → decrease slowly
    }

    // Time-gated update
    maxTimeDelta := 100 * time.Millisecond
    timeDelta := min(now.Sub(a.lastUpdate), maxTimeDelta)

    // Adaptation formula
    d := absEstimate - a.thresh
    add := k * float64(d.Milliseconds()) * float64(timeDelta.Milliseconds())
    a.thresh += time.Duration(add*1000) * time.Microsecond

    // Clamp to range
    a.thresh = clamp(a.thresh, a.min, a.max)
    a.lastUpdate = now
}
```

### Algorithm Breakdown

**Compare:**

```
T = min(numDeltas, 60) * estimate

if T > thresh:
    → OVERUSE
elif T < -thresh:
    → UNDERUSE
else:
    → NORMAL
```

**Why multiply by numDeltas?**

Early in the session, we have few samples (high variance):
- numDeltas = 2: T = 2 * estimate (needs 2x threshold to trigger)
- numDeltas = 10: T = 10 * estimate (needs 10x threshold to trigger)
- numDeltas = 60+: T = 60 * estimate (capped)

This makes detection less sensitive early on (fewer false positives).

**Threshold Adaptation:**

```
Δthresh = k * (|estimate| - thresh) * Δt

where:
  k = 0.01 if |estimate| ≥ thresh (outside → increase quickly)
  k = 0.00018 if |estimate| < thresh (inside → decrease slowly)
  Δt = min(time_since_last_update, 100ms)

New threshold = old + Δthresh
Constrained to [6ms, 600ms]
```

**Example:**

```
Initial thresh = 12.5ms
Estimate = +20ms (congestion!)

|estimate| = 20ms > 12.5ms → use k = 0.01
Δt = 100ms
Δthresh = 0.01 * (20 - 12.5) * 100 = 7.5ms
New thresh = 12.5 + 7.5 = 20ms

Next estimate = +25ms → thresh increases to ~25ms
Congestion clears, estimate = +5ms → thresh slowly decreases
```

---

## Part 2: Overuse Detector

### Pion Implementation

**File:** `pkg/gcc/overuse_detector.go`

```go
type overuseDetector struct {
    threshold   threshold  // Adaptive threshold
    overuseTime time.Duration  // 10ms

    dsWriter func(DelayStats)

    lastEstimate       time.Duration
    lastUpdate         time.Time
    increasingDuration time.Duration
    increasingCounter  int
}

func newOveruseDetector(thresh threshold, overuseTime time.Duration, dsw func(DelayStats)) *overuseDetector {
    return &overuseDetector{
        threshold:          thresh,
        overuseTime:        overuseTime,  // 10ms
        dsWriter:           dsw,
        lastEstimate:       0,
        lastUpdate:         time.Now(),
        increasingDuration: 0,
        increasingCounter:  0,
    }
}

func (d *overuseDetector) onDelayStats(ds DelayStats) {
    now := time.Now()
    delta := now.Sub(d.lastUpdate)
    d.lastUpdate = now

    // Compare against adaptive threshold
    thresholdUse, estimate, currentThreshold := d.threshold.compare(ds.Estimate, ds.LastReceiveDelta)

    use := usageNormal
    if thresholdUse == usageOver {
        // Track increasing duration
        if d.increasingDuration == 0 {
            d.increasingDuration = delta / 2
        } else {
            d.increasingDuration += delta
        }
        d.increasingCounter++

        // Only trigger overuse if:
        // 1. Sustained for > overuseTime (10ms) AND counter > 1
        // 2. OR counter > 1 (if overuseTime == 0)
        // 3. AND estimate is still increasing
        if (d.overuseTime == 0 && d.increasingCounter > 1) ||
            (d.increasingDuration > d.overuseTime && d.increasingCounter > 1) {
            if estimate > d.lastEstimate {
                use = usageOver
            }
        }
    }

    if thresholdUse == usageUnder {
        d.increasingCounter = 0
        d.increasingDuration = 0
        use = usageUnder
    }

    if thresholdUse == usageNormal {
        d.increasingDuration = 0
        d.increasingCounter = 0
        use = usageNormal
    }

    d.lastEstimate = estimate

    d.dsWriter(DelayStats{
        Measurement:      ds.Measurement,
        Estimate:         estimate,
        Threshold:        currentThreshold,
        LastReceiveDelta: ds.LastReceiveDelta,
        Usage:            use,
        State:            0,
        TargetBitrate:    0,
    })
}
```

### Algorithm Breakdown

**State Tracking:**

```
increasingCounter: How many times threshold was exceeded
increasingDuration: Total time spent above threshold
```

**Overuse Logic:**

```
if thresholdUse == OVER:
    increasingDuration += delta
    increasingCounter++

    if increasingDuration > 10ms AND counter > 1:
        if estimate > lastEstimate:
            → OVERUSE (confirmed)
    else:
        → NORMAL (temporary spike)

if thresholdUse == UNDER:
    → UNDERUSE (reset counters)

if thresholdUse == NORMAL:
    → NORMAL (reset counters)
```

**Why the extra checks?**

1. **Sustained overuse:**
   - Don't trigger on single spike
   - Need > 10ms of increasing delay
   - Need counter > 1 (multiple groups)

2. **Still increasing:**
   - `estimate > lastEstimate`
   - If delay plateaued → don't trigger yet
   - Only trigger if actively getting worse

This prevents false positives from transient spikes.

---

## aiortc's OveruseDetector - Differences

### Current Implementation

**File:** `src/aiortc/rate.py`

```python
class OveruseDetector:
    def __init__(self):
        self.hypothesis = BandwidthUsage.NORMAL
        self.k_up = 0.0087
        self.k_down = 0.039
        self.overuse_counter = 0
        self.overuse_time = None
        self.overuse_time_threshold = 10
        self.threshold = 12.5

    def detect(self, offset, timestamp_delta_ms, num_of_deltas, now_ms):
        if num_of_deltas < 2:
            return BandwidthUsage.NORMAL

        T = min(num_of_deltas, MIN_NUM_DELTAS) * offset

        if T > self.threshold:
            if self.overuse_time is None:
                self.overuse_time = timestamp_delta_ms / 2
            else:
                self.overuse_time += timestamp_delta_ms
            self.overuse_counter += 1

            if (
                self.overuse_time > self.overuse_time_threshold
                and self.overuse_counter > 1
                and offset >= self.previous_offset
            ):
                self.overuse_counter = 0
                self.overuse_time = 0
                self.hypothesis = BandwidthUsage.OVERUSING
        elif T < -self.threshold:
            self.overuse_counter = 0
            self.overuse_time = None
            self.hypothesis = BandwidthUsage.UNDERUSING
        else:
            self.overuse_counter = 0
            self.overuse_time = None
            self.hypothesis = BandwidthUsage.NORMAL

        self.previous_offset = offset
        self.update_threshold(T, now_ms)
        return self.hypothesis

    def update_threshold(self, modified_offset, now_ms):
        if abs(modified_offset) > self.threshold + MAX_ADAPT_OFFSET_MS:
            self.last_update_ms = now_ms
            return

        k = self.k_down if abs(modified_offset) < self.threshold else self.k_up
        time_delta_ms = min(now_ms - self.last_update_ms, 100)
        self.threshold += k * (abs(modified_offset) - self.threshold) * time_delta_ms
        self.threshold = max(6, min(self.threshold, 600))
        self.last_update_ms = now_ms
```

### Key Differences

| Aspect | Pion | aiortc |
|--------|------|--------|
| **Threshold class** | Separate `adaptiveThreshold` class | Inline `update_threshold()` method |
| **Coefficients** | k_up=0.01, k_down=0.00018 | k_up=0.0087, k_down=0.039 |
| **numDeltas cap** | 60 | 60 (MIN_NUM_DELTAS) |
| **Overuse time** | `increasingDuration` (time.Duration) | `overuse_time` (float ms) |
| **Reset on overuse** | Keep counters, use `usageOver` | Reset counters, set hypothesis |
| **Threshold update** | Time-gated with ms precision | Time-gated with ms precision |
| **Input** | DelayStats (from slope estimator) | offset (from OveruseEstimator) |

### Problems

1. **Different coefficients:**
   - Pion: Fast increase (0.01), very slow decrease (0.00018)
   - aiortc: Moderate increase (0.0087), fast decrease (0.039)
   - **Impact:** aiortc threshold adapts too quickly

2. **Different input:**
   - Pion: Kalman-filtered delay variation
   - aiortc: Trendline slope offset
   - **Impact:** Detecting different signals!

3. **Reset on overuse:**
   - Pion: Emits OVER but keeps tracking
   - aiortc: Resets counters after triggering
   - **Impact:** Different hysteresis behavior

4. **Architecture:**
   - Pion: Separate threshold object (testable, reusable)
   - aiortc: Inline threshold logic (harder to test)

---

## Implementation Plan for aiortc

### 1. Create `adaptive_threshold.py`

```python
from typing import Tuple

MAX_DELTAS = 60

class UsageEnum:
    OVER = 0
    UNDER = 1
    NORMAL = 2

class AdaptiveThreshold:
    """
    Adaptive threshold for overuse detection.

    Matches pion's adaptiveThreshold implementation.
    """

    def __init__(
        self,
        initial_threshold: float = 12.5,  # ms
        min_threshold: float = 6.0,       # ms
        max_threshold: float = 600.0,     # ms
        overuse_coefficient_up: float = 0.01,
        overuse_coefficient_down: float = 0.00018
    ):
        self.thresh = initial_threshold
        self.min = min_threshold
        self.max = max_threshold
        self.overuse_coefficient_up = overuse_coefficient_up
        self.overuse_coefficient_down = overuse_coefficient_down
        self.last_update_ms: Optional[int] = None
        self.num_deltas = 0

    def compare(self, estimate: float, delta: float) -> Tuple[int, float, float]:
        """
        Compare estimate against adaptive threshold.

        Args:
            estimate: Kalman-filtered delay estimate (ms)
            delta: Last receive delta (unused, for interface compatibility)

        Returns:
            Tuple of (usage, scaled_estimate, current_threshold)
        """
        self.num_deltas += 1

        # Need at least 2 deltas
        if self.num_deltas < 2:
            return UsageEnum.NORMAL, estimate, self.max

        # Scale estimate by number of deltas (capped at 60)
        t = min(self.num_deltas, MAX_DELTAS) * estimate

        # Determine usage
        usage = UsageEnum.NORMAL
        if t > self.thresh:
            usage = UsageEnum.OVER
        elif t < -self.thresh:
            usage = UsageEnum.UNDER

        current_thresh = self.thresh
        self.update(t)

        return usage, t, current_thresh

    def update(self, estimate: float) -> None:
        """
        Update threshold based on estimate.

        Args:
            estimate: Scaled delay estimate (numDeltas * kalman_estimate)
        """
        import time
        now_ms = int(time.time() * 1000)

        if self.last_update_ms is None:
            self.last_update_ms = now_ms

        abs_estimate = abs(estimate)

        # Skip update if way outside range (outlier)
        if abs_estimate > self.thresh + 15.0:
            self.last_update_ms = now_ms
            return

        # Choose coefficient
        k = self.overuse_coefficient_up if abs_estimate >= self.thresh else self.overuse_coefficient_down

        # Time-gated update (max 100ms)
        max_time_delta = 100
        time_delta = min(now_ms - self.last_update_ms, max_time_delta)

        # Adaptation formula
        d = abs_estimate - self.thresh
        add = k * d * time_delta
        self.thresh += add

        # Clamp to range
        self.thresh = max(self.min, min(self.thresh, self.max))
        self.last_update_ms = now_ms
```

### 2. Update `overuse_detector.py`

```python
from typing import Callable, Optional
from .slope_estimator import DelayStats
from .adaptive_threshold import AdaptiveThreshold, UsageEnum

class OveruseDetector:
    """
    Detects bandwidth overuse from delay statistics.

    Matches pion's overuseDetector implementation.
    """

    def __init__(
        self,
        threshold: Optional[AdaptiveThreshold] = None,
        overuse_time_ms: float = 10.0,
        on_delay_stats: Optional[Callable[[DelayStats], None]] = None
    ):
        self.threshold = threshold or AdaptiveThreshold()
        self.overuse_time_ms = overuse_time_ms
        self.on_delay_stats = on_delay_stats

        self.last_estimate = 0.0
        self.last_update_ms: Optional[int] = None
        self.increasing_duration = 0.0
        self.increasing_counter = 0

    def on_delay_stats(self, ds: DelayStats) -> None:
        """
        Process delay statistics and detect overuse.

        Args:
            ds: Delay statistics from slope estimator
        """
        import time
        now_ms = int(time.time() * 1000)

        if self.last_update_ms is None:
            self.last_update_ms = now_ms

        delta_ms = now_ms - self.last_update_ms
        self.last_update_ms = now_ms

        # Compare against adaptive threshold
        threshold_use, estimate, current_threshold = self.threshold.compare(
            ds.estimate,
            ds.last_receive_delta
        )

        usage = UsageEnum.NORMAL

        if threshold_use == UsageEnum.OVER:
            # Track increasing duration
            if self.increasing_duration == 0:
                self.increasing_duration = delta_ms / 2
            else:
                self.increasing_duration += delta_ms

            self.increasing_counter += 1

            # Trigger overuse if sustained and still increasing
            if (self.overuse_time_ms == 0 and self.increasing_counter > 1) or \
               (self.increasing_duration > self.overuse_time_ms and self.increasing_counter > 1):
                if estimate > self.last_estimate:
                    usage = UsageEnum.OVER

        elif threshold_use == UsageEnum.UNDER:
            self.increasing_counter = 0
            self.increasing_duration = 0
            usage = UsageEnum.UNDER

        elif threshold_use == UsageEnum.NORMAL:
            self.increasing_counter = 0
            self.increasing_duration = 0
            usage = UsageEnum.NORMAL

        self.last_estimate = estimate

        # Forward updated stats
        if self.on_delay_stats:
            updated_stats = DelayStats(
                measurement=ds.measurement,
                estimate=estimate,
                threshold=current_threshold,
                last_receive_delta=ds.last_receive_delta,
                usage=usage,
                state=0,  # Set by rate controller
                target_bitrate=0  # Set by rate controller
            )
            self.on_delay_stats(updated_stats)
```

---

## Testing Strategy

### Unit Tests

**Adaptive Threshold:**

1. **Threshold increase:**
   - estimate = +20ms, initial thresh = 12.5ms
   - Verify thresh increases toward 20ms

2. **Threshold decrease:**
   - estimate = +5ms, initial thresh = 12.5ms
   - Verify thresh slowly decreases toward 5ms

3. **Clamping:**
   - estimate = +1000ms
   - Verify thresh capped at 600ms

4. **Outlier handling:**
   - estimate = +1000ms (> thresh + 15ms)
   - Verify thresh NOT updated

**Overuse Detector:**

1. **Sustained overuse:**
   - Feed estimates: [+20ms, +21ms, +22ms] over 15ms
   - Verify OVERUSE triggered

2. **Transient spike:**
   - Feed estimates: [+20ms, +5ms]
   - Verify NORMAL (not sustained)

3. **Plateaued delay:**
   - Feed estimates: [+20ms, +20ms, +20ms]
   - Verify NORMAL (not increasing)

4. **Underuse:**
   - Feed estimates: [-20ms, -21ms]
   - Verify UNDERUSE

### Integration Tests

Compare with pion using same delay stats:
- Feed same estimates to both
- Verify usage decisions match
- Verify thresholds adapt similarly

---

## References

- Pion adaptive_threshold: `pkg/gcc/adaptive_threshold.go`
- Pion overuse_detector: `pkg/gcc/overuse_detector.go`
- Draft-ietf-rmcat-gcc-02 Section 5.4

---

## Summary

**Current aiortc status:**

✅ Has overuse detection logic
⚠️ Different coefficients
⚠️ Different architecture (inline vs separate threshold)
❌ Operating on WRONG INPUT (trendline offset vs delay variation)

**Required changes:**

1. Extract `AdaptiveThreshold` as separate class
2. Update coefficients to match pion (0.01 / 0.00018)
3. Connect to `SlopeEstimator` output (not `OveruseEstimator`)
4. Adjust state tracking to match pion's logic

**Priority:** HIGH - but only after implementing arrival groups + slope estimator, since the input needs to be correct first.
