# GCC/TWCC Implementation Alignment Review

**Date:** 2025-11-22
**Purpose:** Line-by-line comparison of pion vs aiortc implementations to ensure accurate porting

---

## Component Mapping

### Pion → aiortc

| Pion Component | Location | aiortc Equivalent | Location | Status |
|----------------|----------|-------------------|----------|--------|
| **TWCC Components** |
| `twcc.Recorder` | `pkg/twcc/twcc.go` | `TWCCRecorder` | `src/aiortc/twcc/receiver.py` | ✅ Implemented |
| `twcc.feedback` | `pkg/twcc/twcc.go` | `TWCCRecorder._build_rtcp_packet` | `src/aiortc/twcc/receiver.py` | ✅ Implemented |
| `twcc.PacketArrivalTimeMap` | `pkg/twcc/arrival_time_map.go` | `ArrivalTimeMap` | `src/aiortc/twcc/receiver.py` | ✅ Implemented |
| `twcc.PacketChunkEncoder` | `pkg/twcc/twcc.go:chunk` | `PacketChunkEncoder` | `src/aiortc/twcc/receiver.py` | ✅ Implemented |
| `twcc.ReceiveDeltaEncoder` | `pkg/twcc/twcc.go:feedback` | `ReceiveDeltaEncoder` | `src/aiortc/twcc/receiver.py` | ✅ Implemented |
| `sequencenumber.Unwrapper` | `internal/sequencenumber/unwrapper.go` | `SequenceNumberUnwrapper` | `src/aiortc/twcc/receiver.py` | ✅ Implemented |
| `TWCCParser` (sender-side) | N/A in pion | `TWCCParser` | `src/aiortc/twcc/sender.py` | ✅ Implemented |
| `SentPacketTracker` | N/A in pion | `SentPacketTracker` | `src/aiortc/twcc/sender.py` | ✅ Implemented |
| **GCC Core Components** |
| `SendSideBWE` | `pkg/gcc/send_side_bwe.go` | `SenderSideBandwidthEstimator` | `src/aiortc/gcc/estimator.py` | ⚠️ **MISSING STRUCTURE** |
| `delayController` | `pkg/gcc/delay_based_bwe.go` | `DelayBasedController` | `src/aiortc/gcc/estimator.py` | ⚠️ **MISSING PIPELINE** |
| `lossBasedBandwidthEstimator` | `pkg/gcc/loss_based_bwe.go` | `LossBasedController` | `src/aiortc/gcc/estimator.py` | ✅ Implemented |
| **GCC Delay-Based Pipeline Components** |
| `arrivalGroupAccumulator` | `pkg/gcc/arrival_group_accumulator.go` | **MISSING** | N/A | ❌ **NOT IMPLEMENTED** |
| `arrivalGroup` | `pkg/gcc/arrival_group.go` | **MISSING** | N/A | ❌ **NOT IMPLEMENTED** |
| `slopeEstimator` | `pkg/gcc/slope_estimator.go` | **MISSING** | N/A | ❌ **NOT IMPLEMENTED** |
| `kalman` | `pkg/gcc/kalman.go` | `OveruseEstimator` (partial) | `src/aiortc/rate.py` | ⚠️ **DIFFERENT ALGORITHM** |
| `overuseDetector` | `pkg/gcc/overuse_detector.go` | `OveruseDetector` | `src/aiortc/rate.py` | ⚠️ **DIFFERENT ALGORITHM** |
| `adaptiveThreshold` | `pkg/gcc/adaptive_threshold.go` | `OveruseDetector.update_threshold` | `src/aiortc/rate.py` | ⚠️ **DIFFERENT ALGORITHM** |
| `rateController` | `pkg/gcc/rate_controller.go` | `AimdRateControl` | `src/aiortc/rate.py` | ⚠️ **DIFFERENT STRUCTURE** |
| `rateCalculator` | `pkg/gcc/rate_calculator.go` | `RateCounter` | `src/aiortc/rate.py` | ⚠️ **DIFFERENT IMPLEMENTATION** |
| **Supporting Components** |
| `FeedbackAdapter` | `internal/cc/feedback_adapter.go` | **MISSING** | N/A | ❌ **NOT IMPLEMENTED** |
| `cc.Acknowledgment` | `internal/cc/acknowledgment.go` | **MISSING** | N/A | ❌ **NOT IMPLEMENTED** |

---

## Critical Issues Identified

### 1. **Missing Delay-Based Pipeline Architecture**

**Pion Architecture:**
```
WriteRTCP(TWCC feedback)
  ↓
FeedbackAdapter.OnTransportCCFeedback → []cc.Acknowledgment
  ↓
delayController.updateDelayEstimate(acks)
  ↓ (pipe to arrivalGroupAccumulator)
arrivalGroupAccumulator.run(acks) → arrivalGroup
  ↓
slopeEstimator.onArrivalGroup(group) → DelayStats
  ↓
overuseDetector.onDelayStats(stats) → DelayStats (with usage)
  ↓
rateController.onDelayStats(stats) → DelayStats (with target bitrate)
  ↓
SendSideBWE.onDelayUpdate(stats)
```

**aiortc Current Architecture:**
```
process_feedback(feedback)
  ↓
InterArrival.compute_deltas → deltas
  ↓
OveruseEstimator.update → offset
  ↓
OveruseDetector.detect → BandwidthUsage
  ↓
AimdRateControl.update → target bitrate
```

**Problem:** aiortc is using **libwebrtc receiver-side components** (`InterArrival`, `OveruseEstimator`) designed for processing individual RTP packets, NOT the **pion sender-side GCC delay-based pipeline** which processes acknowledgment groups.

**Impact:**
- Missing inter-group delay variation calculation (core of GCC)
- Using wrong Kalman filter (webrtc's slope estimator vs pion's delay variation estimator)
- Missing arrival group accumulation logic
- Processing packet-by-packet instead of burst-by-burst

---

### 2. **Missing FeedbackAdapter and Acknowledgment Model**

**Pion:**
```go
// internal/cc/acknowledgment.go
type Acknowledgment struct {
    SequenceNumber int
    Size           int
    Departure      time.Time  // When packet was sent
    Arrival        time.Time  // When packet was received
}

// FeedbackAdapter converts TWCC feedback → []Acknowledgment
```

**aiortc:**
```python
# Has PacketFeedback but processes differently
@dataclass
class PacketFeedback:
    sequence_number: int
    size: int
    send_time_us: int
    recv_time_us: int
    ssrc: int
```

**Problem:** aiortc converts feedback to `PacketFeedback` then processes through `InterArrival`, but pion converts to `Acknowledgment` and processes through `arrivalGroupAccumulator`.

**Impact:** Completely different processing pipeline.

---

### 3. **arrivalGroupAccumulator Missing**

**Pion Implementation ([arrival_group_accumulator.go:31-94](../../local_pion_gcc/pkg/gcc/arrival_group_accumulator.go#L31-L94)):**

```go
func (a *arrivalGroupAccumulator) run(in <-chan []cc.Acknowledgment, agWriter func(arrivalGroup)) {
    // Groups packets based on:
    // 1. Inter-departure time (within 5ms burst)
    // 2. Inter-arrival time (received within 5ms)
    // 3. Inter-group delay variation (d(i) < 0 indicates same burst)

    for acks := range in {
        for _, next := range acks {
            if interDepartureTimePkt(group, next) <= a.interDepartureThreshold {
                group.add(next)
                continue
            }
            if interArrivalTimePkt(group, next) <= a.interArrivalThreshold &&
                interGroupDelayVariationPkt(group, next) < a.interGroupDelayVariationTreshold {
                group.add(next)
                continue
            }
            agWriter(group)  // Send completed group to slope estimator
            group = newArrivalGroup(next)
        }
    }
}
```

**aiortc:** **DOES NOT EXIST**

**Impact:** This is the CORE of GCC delay-based estimation. Without it:
- Cannot detect packet bursts
- Cannot calculate inter-group delay variation
- Cannot properly estimate queuing delay trends

---

### 4. **slopeEstimator Missing**

**Pion Implementation ([slope_estimator.go:34-53](../../local_pion_gcc/pkg/gcc/slope_estimator.go#L34-L53)):**

```go
func (e *slopeEstimator) onArrivalGroup(ag arrivalGroup) {
    if !e.init {
        e.group = ag
        e.init = true
        return
    }
    // Core GCC formula: inter-group delay variation
    measurement := interGroupDelayVariation(e.group, ag)
    delta := ag.arrival.Sub(e.group.arrival)
    e.group = ag

    e.delayStatsWriter(DelayStats{
        Measurement:      measurement,  // d(i) = t(i) - t(i-1) - (T(i) - T(i-1))
        Estimate:         e.updateEstimate(measurement),  // Kalman filtered
        ...
    })
}

func interGroupDelayVariation(a, b arrivalGroup) time.Duration {
    return b.arrival.Sub(a.arrival) - b.departure.Sub(a.departure)
}
```

**aiortc:** **DOES NOT EXIST**

Instead, uses `InterArrival` which calculates:
```python
# aiortc/rate.py - libwebrtc formula (WRONG for sender-side GCC)
timestamp_delta = uint32_add(self.current_group.last_timestamp, -self.previous_group.last_timestamp)
arrival_time_delta = self.current_group.arrival_time - self.previous_group.arrival_time
size_delta = self.current_group.size - self.previous_group.size
```

**Impact:** Using completely wrong formula. Inter-group delay variation is NOT the same as inter-arrival delta.

---

### 5. **Kalman Filter Mismatch**

**Pion Kalman Filter ([kalman.go:73-97](../../local_pion_gcc/pkg/gcc/kalman.go#L73-L97)):**

```go
// Purpose: Filter inter-group delay variation measurements
func (k *kalman) updateEstimate(measurement time.Duration) time.Duration {
    z := measurement - k.estimate  // Innovation
    zms := float64(z.Microseconds()) / 1000.0

    // Update measurement uncertainty (adaptive noise estimation)
    if !k.disableMeasurementUncertaintyUpdates {
        alpha := math.Pow((1 - chi), 30.0/(1000.0*5*float64(time.Millisecond)))
        root := math.Sqrt(k.measurementUncertainty)
        root3 := 3 * root
        if zms > root3 {
            k.measurementUncertainty = math.Max(alpha*k.measurementUncertainty+(1-alpha)*root3*root3, 1)
        } else {
            k.measurementUncertainty = math.Max(alpha*k.measurementUncertainty+(1-alpha)*zms*zms, 1)
        }
    }

    estimateUncertainty := k.estimateError + k.processUncertainty
    k.gain = estimateUncertainty / (estimateUncertainty + k.measurementUncertainty)

    k.estimate += time.Duration(k.gain * zms * float64(time.Millisecond))
    k.estimateError = (1 - k.gain) * estimateUncertainty

    return k.estimate
}
```

**aiortc OveruseEstimator ([rate.py:400-459](../../../src/aiortc/rate.py#L400-L459)):**

Uses a **2D Kalman filter** tracking both slope and offset:
```python
# Tracks: [slope, offset] state vector
# This is libwebrtc's trendline filter, NOT pion's delay variation filter
def update(self, time_delta_ms, timestamp_delta_ms, size_delta, ...):
    # 2D state: slope (network gradient) + offset (queueing delay)
    h = [fs_delta, 1.0]  # Measurement matrix
    residual = t_ts_delta - self.slope * h[0] - self._offset
    ...
```

**Problem:** These are **completely different Kalman filters** for **different purposes**:
- **Pion**: 1D filter for delay variation trend (GCC sender-side)
- **aiortc**: 2D filter for slope + offset (libwebrtc receiver-side trendline)

---

### 6. **OveruseDetector Algorithm Mismatch**

**Pion ([overuse_detector.go:38-86](../../local_pion_gcc/pkg/gcc/overuse_detector.go#L38-L86)):**

```go
func (d *overuseDetector) onDelayStats(ds DelayStats) {
    thresholdUse, estimate, currentThreshold := d.threshold.compare(ds.Estimate, ds.LastReceiveDelta)

    use := usageNormal
    if thresholdUse == usageOver {
        if d.increasingDuration == 0 {
            d.increasingDuration = delta / 2
        } else {
            d.increasingDuration += delta
        }
        d.increasingCounter++

        if (d.overuseTime == 0 && d.increasingCounter > 1) ||
            (d.increasingDuration > d.overuseTime && d.increasingCounter > 1) {
            if estimate > d.lastEstimate {
                use = usageOver
            }
        }
    }
    // Uses adaptive threshold
}
```

**aiortc ([rate.py:322-356](../../../src/aiortc/rate.py#L322-L356)):**

```python
def detect(self, offset, timestamp_delta_ms, num_of_deltas, now_ms):
    T = min(num_of_deltas, MIN_NUM_DELTAS) * offset
    if T > self.threshold:
        if self.overuse_time is None:
            self.overuse_time = timestamp_delta_ms / 2
        else:
            self.overuse_time += timestamp_delta_ms
        # Different logic here
```

**Problem:**
- Pion uses `threshold.compare()` (adaptive threshold) with increasing duration tracking
- aiortc uses fixed threshold with `T = num_deltas * offset` formula
- Different overuse detection conditions

---

### 7. **AdaptiveThreshold Missing**

**Pion ([adaptive_threshold.go:64-106](../../local_pion_gcc/pkg/gcc/adaptive_threshold.go#L64-L106)):**

```go
type adaptiveThreshold struct {
    thresh                 time.Duration  // Current threshold (starts at 12.5ms)
    overuseCoefficientUp   float64        // 0.01 (fast increase)
    overuseCoefficientDown float64        // 0.00018 (slow decrease)
    min                    time.Duration  // 6ms
    max                    time.Duration  // 600ms
}

func (a *adaptiveThreshold) compare(estimate, _ time.Duration) (usage, time.Duration, time.Duration) {
    t := time.Duration(min(a.numDeltas, maxDeltas)) * estimate
    use := usageNormal
    if t > a.thresh {
        use = usageOver
    } else if t < -a.thresh {
        use = usageUnder
    }
    a.update(t)  // Dynamically adjust threshold
    return use, t, a.thresh
}

func (a *adaptiveThreshold) update(estimate time.Duration) {
    absEstimate := abs(estimate)
    if absEstimate > a.thresh+15*time.Millisecond {
        return  // Don't update if way outside range
    }
    k := a.overuseCoefficientUp
    if absEstimate < a.thresh {
        k = a.overuseCoefficientDown  // Decrease slowly
    }
    maxTimeDelta := 100 * time.Millisecond
    timeDelta := min(now.Sub(a.lastUpdate), maxTimeDelta)
    d := absEstimate - a.thresh
    add := k * float64(d.Milliseconds()) * float64(timeDelta.Milliseconds())
    a.thresh += time.Duration(add*1000) * time.Microsecond
    a.thresh = clamp(a.thresh, a.min, a.max)
}
```

**aiortc ([rate.py:360-372](../../../src/aiortc/rate.py#L360-L372)):**

```python
def update_threshold(self, modified_offset, now_ms):
    # Simpler fixed adaptation
    k = self.k_down if abs(modified_offset) < self.threshold else self.k_up
    time_delta_ms = min(now_ms - self.last_update_ms, 100)
    self.threshold += k * (abs(modified_offset) - self.threshold) * time_delta_ms
    self.threshold = max(6, min(self.threshold, 600))
```

**Problem:**
- Pion: Threshold adapts based on TIME and deviation magnitude (time-gated adaptation)
- aiortc: Simpler formula, missing time-based adaptation
- Different coefficients (pion: 0.01/0.00018, aiortc: 0.0087/0.039)

---

### 8. **RateController Architecture Mismatch**

**Pion ([rate_controller.go:53-178](../../local_pion_gcc/pkg/gcc/rate_controller.go#L53-L178)):**

```go
type rateController struct {
    target             int
    latestReceivedRate int   // From rateCalculator
    latestDecreaseRate *exponentialMovingAverage  // Track decrease points
}

func (c *rateController) increase(now time.Time) int {
    // Near-max detection: check if within 3σ of previous decrease EMA
    if c.latestDecreaseRate.average > 0 &&
        float64(c.latestReceivedRate) > c.latestDecreaseRate.average-3*c.latestDecreaseRate.stdDeviation &&
        float64(c.latestReceivedRate) < c.latestDecreaseRate.average+3*c.latestDecreaseRate.stdDeviation {
        // Additive increase (near congestion point)
        bitsPerFrame := float64(c.target) / 30.0
        packetsPerFrame := math.Ceil(bitsPerFrame / (1200 * 8))
        expectedPacketSizeBits := bitsPerFrame / packetsPerFrame
        responseTime := 100*time.Millisecond + c.latestRTT
        alpha := 0.5 * math.Min(float64(now.Sub(c.lastUpdate))/float64(responseTime), 1.0)
        increase := int(math.Max(1000.0, alpha*expectedPacketSizeBits))
        return int(math.Min(float64(c.target+increase), 1.5*float64(c.latestReceivedRate)))
    }
    // Multiplicative increase (far from congestion)
    eta := math.Pow(1.08, math.Min(float64(now.Sub(c.lastUpdate))/1000, 1.0))
    return int(eta * float64(c.target))
}
```

**aiortc ([rate.py:109-176](../../../src/aiortc/rate.py#L109-L176)):**

Similar logic but implemented slightly differently. This component is actually fairly well aligned!

**Status:** ✅ Mostly correct, minor differences

---

### 9. **RateCalculator vs RateCounter**

**Pion RateCalculator ([rate_calculator.go:22-66](../../local_pion_gcc/pkg/gcc/rate_calculator.go#L22-L66)):**

```go
func (c *rateCalculator) run(in <-chan []cc.Acknowledgment, onRateUpdate func(int)) {
    var history []cc.Acknowledgment
    for acks := range in {
        for _, next := range acks {
            if next.Arrival.IsZero() {
                continue  // Skip lost packets
            }
            history = append(history, next)
            sum += next.Size

            // Remove packets outside window
            del := 0
            for _, ack := range history {
                deadline := next.Arrival.Add(-c.window)  // 500ms window
                if !ack.Arrival.Before(deadline) {
                    break
                }
                del++
                sum -= ack.Size
            }
            history = history[del:]

            // Calculate rate
            dt := next.Arrival.Sub(history[0].Arrival)
            bits := 8 * sum
            rate := int(float64(bits) / dt.Seconds())
            onRateUpdate(rate)
        }
    }
}
```

**aiortc RateCounter ([rate.py:495-554](../../../src/aiortc/rate.py#L495-L554)):**

Uses 1ms buckets with sliding window:
```python
def add(self, value, now_ms):
    index = (self._origin_index + now_ms - self._origin_ms) % self._window_size
    self._buckets[index].count += 1
    self._buckets[index].value += value
```

**Problem:** Different implementation approaches but both calculate sliding window rate. RateCounter is per-packet, RateCalculator is per-acknowledgment-batch.

---

## TWCC Component Review

### TWCCRecorder (Receiver-Side)

**Alignment Status:** ✅ **Well Aligned**

Line-by-line comparison shows proper implementation:

✅ Sequence unwrapping matches pion
✅ Arrival time map matches pion
✅ Chunk encoding (run-length + status vector) matches pion
✅ Delta encoding (small/large) matches pion
✅ Reference time encoding matches pion
✅ RTCP packet structure matches pion

**Minor differences:**
- aiortc adds cross-report monotonicity enforcement (good addition)
- aiortc adds debug logging (helpful)

---

### TWCCParser (Sender-Side)

**Alignment Status:** ✅ **Well Aligned**

Properly parses TWCC feedback:

✅ RTCP header parsing matches spec
✅ Chunk decoding matches pion
✅ Delta decoding matches pion
✅ Sequence number handling matches spec

---

## Recommendations

### Immediate Actions Required

1. **Implement Missing Core Components** (High Priority)
   - [ ] `arrivalGroupAccumulator` - CRITICAL
   - [ ] `arrivalGroup` - CRITICAL
   - [ ] `slopeEstimator` - CRITICAL
   - [ ] Pion-style `kalman` filter - CRITICAL
   - [ ] `FeedbackAdapter` - HIGH
   - [ ] `cc.Acknowledgment` model - HIGH

2. **Replace Incorrect Components** (High Priority)
   - [ ] Replace `InterArrival` with `arrivalGroupAccumulator`
   - [ ] Replace `OveruseEstimator` (2D Kalman) with pion's 1D Kalman
   - [ ] Update `OveruseDetector` to match pion's algorithm
   - [ ] Implement proper `adaptiveThreshold` class

3. **Refactor Architecture** (High Priority)
   - [ ] Restructure `DelayBasedController` to match pion's pipeline
   - [ ] Implement goroutine-style pipeline (use asyncio tasks or queues)
   - [ ] Separate `delayController` from bandwidth estimator

4. **Update Rate Components** (Medium Priority)
   - [ ] Refactor `RateCounter` to process acknowledgments, not packets
   - [ ] Ensure state transitions exactly match pion

### Architecture Refactor Plan

**New Structure:**

```python
# src/aiortc/gcc/delay_controller.py
class DelayController:
    def __init__(self):
        self.arrival_group_accumulator = ArrivalGroupAccumulator()
        self.slope_estimator = SlopeEstimator(Kalman())
        self.overuse_detector = OveruseDetector(AdaptiveThreshold())
        self.rate_controller = RateController()
        self.rate_calculator = RateCalculator()

        # Pipeline: acks → groups → slopes → overuse → rate

    def update_delay_estimate(self, acks: List[Acknowledgment]):
        # Pipe to arrival group accumulator
        # Groups will flow to slope estimator
        # Slopes will flow to overuse detector
        # Overuse signals will flow to rate controller
```

**New Components to Create:**

```
src/aiortc/gcc/
  ├── acknowledgment.py      # cc.Acknowledgment model
  ├── feedback_adapter.py    # Convert TWCC → Acknowledgments
  ├── arrival_group.py       # arrivalGroup + arrivalGroupAccumulator
  ├── slope_estimator.py     # slopeEstimator + kalman filter
  ├── overuse_detector.py    # overuseDetector + adaptiveThreshold
  ├── rate_controller.py     # rateController (refactored from rate.py)
  └── rate_calculator.py     # rateCalculator (from acknowledgments)
```

---

## Testing Strategy

After implementing missing components:

1. **Unit tests** for each component matching pion's test cases
2. **Integration test** with pion peer using same TWCC feedback
3. **Regression tests** against existing VP9 e2e tests
4. **Comparative logging** to verify pipeline outputs match pion

---

## Appendix: Key Algorithm Formulas

### Inter-Group Delay Variation (Core GCC Formula)

**Pion (CORRECT):**
```
d(i) = (t(i) - t(i-1)) - (T(i) - T(i-1))

where:
  t(i) = arrival time of group i (at receiver)
  T(i) = departure time of group i (at sender)

Interpretation:
  - If d(i) > 0: packets took longer → growing queue → overuse
  - If d(i) < 0: packets took less time → shrinking queue → underuse
```

This is implemented in [slope_estimator.go:55-57](../../local_pion_gcc/pkg/gcc/slope_estimator.go#L55-L57)

**aiortc (WRONG - uses libwebrtc packet-level formula):**
```python
# From InterArrival.compute_deltas
timestamp_delta = current_group.last_timestamp - previous_group.last_timestamp
arrival_time_delta = current_group.arrival_time - previous_group.arrival_time
size_delta = current_group.size - previous_group.size
```

This is NOT the same as inter-group delay variation!

---

### Adaptive Threshold Update

**Pion (CORRECT from draft-ietf-rmcat-gcc-02):**
```
thresh(t) = thresh(t-1) + k * (|d(t)| - thresh(t-1)) * Δt

where:
  k = k_up (0.01) if |d(t)| > thresh, else k_down (0.00018)
  Δt = min(time_since_last_update, 100ms)

Constraints:
  6ms ≤ thresh ≤ 600ms
  Skip update if |d(t)| > thresh + 15ms (outlier)
```

---

## Conclusion

The aiortc GCC implementation is fundamentally using the **wrong architecture**. It's using libwebrtc's **receiver-side** components (InterArrival, OveruseEstimator) when it should be using pion's **sender-side** GCC components (arrivalGroupAccumulator, slopeEstimator, kalman).

**This is not a minor bug - it's an architectural mismatch.**

The TWCC implementation is solid, but the GCC implementation needs a complete rewrite following pion's architecture line-by-line.

**Estimated effort:**
- Remove incorrect components: 2 hours
- Implement missing components: 8-12 hours
- Refactor architecture: 4-6 hours
- Testing and validation: 4-6 hours
- **Total: 18-26 hours**

This review document should be used as a reference during the refactor to ensure no components are missed and all algorithms match pion exactly.
