# Rate Controller - Component Analysis

**Component:** `rateController`
**Status:** ✅ **COMPLETE** (Implemented 2025-11-22)
**Implementation:** `src/aiortc/gcc/rate_controller.py`
**Verification:** Line-by-line port verified in `IMPLEMENTATION_VERIFICATION.md`

---

## Implementation Status

✅ **FULLY IMPLEMENTED AND VERIFIED**

The RateController has been implemented as an exact line-by-line port from pion's reference implementation. All AIMD logic, coefficients, and rate calculation modes match pion precisely.

**Key Files:**
- `src/aiortc/gcc/rate_controller.py` - AIMD rate control
- `src/aiortc/gcc/types.py` - RateControlState enum

---

## Purpose

Implements AIMD (Additive Increase Multiplicative Decrease) rate control for GCC bandwidth estimation.

**Core Behavior:**
- **INCREASE state:** Gradually increase target bitrate (additive or multiplicative)
- **DECREASE state:** Quickly reduce target bitrate by 15% (multiplicative by 0.85)
- **HOLD state:** Maintain current bitrate (no change)

This is the final stage of the GCC pipeline that produces the target bitrate for the encoder.

---

## Pion Implementation

**File:** `pkg/gcc/rate_controller.go`

### Constants

```go
const (
    decreaseEMAAlpha = 0.95  // line 13
    beta             = 0.85  // line 14
)
```

**Purpose:**
- `decreaseEMAAlpha = 0.95`: Exponential moving average alpha for tracking decrease rates
- `beta = 0.85`: Multiplicative decrease factor (reduce by 15%)

### Core Structures

**ExponentialMovingAverage (lines 36-51):**

```go
type exponentialMovingAverage struct {
    average      float64  // EMA value
    variance     float64  // Variance
    stdDeviation float64  // Standard deviation
}

func (a *exponentialMovingAverage) update(value float64) {
    if a.average == 0.0 {
        a.average = value
    } else {
        x := value - a.average
        a.average += decreaseEMAAlpha * x
        a.variance = (1 - decreaseEMAAlpha) * (a.variance + decreaseEMAAlpha*x*x)
        a.stdDeviation = math.Sqrt(a.variance)
    }
}
```

**RateController (lines 17-34):**

```go
type rateController struct {
    now                  now           // Time function (for testing)
    initialTargetBitrate int           // Initial target (bps)
    minBitrate           int           // Min bitrate (bps)
    maxBitrate           int           // Max bitrate (bps)

    dsWriter func(DelayStats)          // Callback

    lock               sync.Mutex      // Thread safety
    init               bool            // First call flag
    delayStats         DelayStats      // Current stats
    target             int             // Current target bitrate
    lastUpdate         time.Time       // Last update time
    lastState          state           // Previous state
    latestRTT          time.Duration   // Round-trip time
    latestReceivedRate int             // Received bitrate (bps)
    latestDecreaseRate *exponentialMovingAverage  // Decrease rate tracker
}
```

---

## Algorithm Breakdown

### State Transition

**From OveruseDetector:**
- DelayStats contains `Usage` (OVER/UNDER/NORMAL)

**State Machine (lines 96):**
```go
c.delayStats.State = c.delayStats.State.transition(ds.Usage)
```

**Transition logic** (from `state.go`):
```
Current State | Usage  | Next State
--------------|--------|------------
HOLD          | OVER   | DECREASE
HOLD          | NORMAL | INCREASE
HOLD          | UNDER  | HOLD
INCREASE      | OVER   | DECREASE
INCREASE      | NORMAL | INCREASE
INCREASE      | UNDER  | HOLD
DECREASE      | OVER   | DECREASE
DECREASE      | NORMAL | HOLD
DECREASE      | UNDER  | HOLD
```

### Rate Calculation

**Early return for HOLD (lines 98-100):**
```go
if c.delayStats.State == stateHold {
    return  // Don't change rate
}
```

**INCREASE mode (lines 109-110):**

Two increase modes depending on position relative to last decrease:

#### Mode 1: Near Last Decrease (Additive Increase)

**Condition (lines 140-142):**
```go
if c.latestDecreaseRate.average > 0 &&
   float64(c.latestReceivedRate) > c.latestDecreaseRate.average - 3*c.latestDecreaseRate.stdDeviation &&
   float64(c.latestReceivedRate) < c.latestDecreaseRate.average + 3*c.latestDecreaseRate.stdDeviation
```

**Meaning:** Current received rate is within 3σ of the average decrease rate.

**Calculation (lines 143-152):**

```go
// Expected packet size
bitsPerFrame := float64(c.target) / 30.0
packetsPerFrame := math.Ceil(bitsPerFrame / (1200 * 8))
expectedPacketSizeBits := bitsPerFrame / packetsPerFrame

// Response time = 100ms + RTT
responseTime := 100*time.Millisecond + c.latestRTT

// Alpha = 0.5 * min(time_since_update / response_time, 1.0)
alpha := 0.5 * math.Min(float64(now.Sub(c.lastUpdate).Milliseconds())/float64(responseTime.Milliseconds()), 1.0)

// Increase = max(1000 bps, alpha * packet_size)
increase := int(math.Max(1000.0, alpha*expectedPacketSizeBits))

// Clamp to 1.5 * received_rate
return int(math.Min(float64(c.target+increase), 1.5*float64(c.latestReceivedRate)))
```

**Why this mode?**
- We recently decreased (we're near a congestion point)
- Use conservative additive increase
- Increase by roughly one packet size per response time
- Approach the congestion point slowly

#### Mode 2: Far From Last Decrease (Multiplicative Increase)

**Calculation (lines 154-169):**

```go
// Multiplicative increase: 1.08^time * target
eta := math.Pow(1.08, math.Min(float64(now.Sub(c.lastUpdate).Milliseconds())/1000, 1.0))
c.lastUpdate = now

rate := int(eta * float64(c.target))

// Cap at 1.5 * received_rate
received := int(1.5 * float64(c.latestReceivedRate))
if rate > received && received > c.target {
    return received
}

// Never decrease
if rate < c.target {
    return c.target
}

return rate
```

**Why this mode?**
- Far from last congestion point
- Use faster multiplicative increase
- Increase by 8% per second (1.08^t)
- Ramp up faster when network is clearly underutilized

**DECREASE mode (lines 121-131):**

```go
func (c *rateController) decrease() int {
    target := int(beta * float64(c.latestReceivedRate))
    c.latestDecreaseRate.update(float64(c.latestReceivedRate))
    c.lastUpdate = c.now()
    return target
}
```

**Simple multiplicative decrease:**
- `target = 0.85 * received_rate`
- Reduce by 15% immediately
- Track this decrease in EMA (for Mode 1 detection)

---

## AIMD Explained

**Additive Increase, Multiplicative Decrease** is a fundamental congestion control algorithm:

### Why AIMD?

**Additive Increase:**
- Slowly probe for available bandwidth
- Add constant amount each RTT
- Gentle approach to congestion point
- Fair: multiple flows converge to equal shares

**Multiplicative Decrease:**
- Quickly back off when congestion detected
- Reduce by fixed percentage (15%)
- Strong signal to relieve congestion
- Prevents collapse when multiple flows decrease

### GCC's Enhanced AIMD

GCC adds sophistication to basic AIMD:

1. **Two increase modes:**
   - Additive when near congestion (careful probing)
   - Multiplicative when far from congestion (fast ramp-up)

2. **EMA tracking:**
   - Remember where we decreased
   - Switch modes based on 3σ distance from that point
   - Prevents oscillation around congestion point

3. **RTT-aware response time:**
   - Additive increase considers network RTT
   - Longer RTT → slower increase (more conservative)
   - Prevents overreaction on high-latency networks

---

## Time-Based Behavior

### Response Time

**Formula (line 147):**
```
response_time = 100ms + RTT
```

**Purpose:**
- Time to detect and react to network changes
- 100ms: Typical buffering/processing delay
- RTT: Round-trip feedback delay

**Usage:**
- Determines how fast we increase in Mode 1 (additive)
- `alpha = 0.5 * min(time_elapsed / response_time, 1.0)`
- After one response_time, alpha ≈ 0.5 (half packet size increase)

### Update Rate

**Multiplicative increase (line 154):**
```
eta = 1.08^min(time_since_update, 1.0)
```

**Meaning:**
- Per-second growth: 8%
- If updates every 100ms: 1.08^0.1 ≈ 1.0077 (0.77% per 100ms)
- If updates every 1s: 1.08^1.0 = 1.08 (8% per second)
- Capped at 1 second to prevent huge jumps after gaps

---

## Integration with Pipeline

### Input: DelayStats

From OveruseDetector:
```go
type DelayStats struct {
    Measurement      time.Duration  // Raw delay variation
    Estimate         time.Duration  // Kalman filtered
    Threshold        time.Duration  // Adaptive threshold
    LastReceiveDelta time.Duration  // Time between groups
    Usage            usage          // OVER/UNDER/NORMAL
    State            state          // (to be set by controller)
    TargetBitrate    int            // (to be set by controller)
}
```

### Output: Updated DelayStats

RateController sets:
- `State`: INCREASE/DECREASE/HOLD (after transition)
- `TargetBitrate`: New target for encoder

### Auxiliary Inputs

Updated separately:
- `onReceivedRate(rate)`: Current received bitrate (from receiver reports)
- `updateRTT(rtt)`: Round-trip time (from RTCP)

Both protected by mutex for thread safety.

---

## Constants Verification

| Constant | Pion Value | aiortc Value | Purpose |
|----------|------------|--------------|---------|
| `decreaseEMAAlpha` | 0.95 | 0.95 | EMA smoothing for decrease rates |
| `beta` | 0.85 | 0.85 | Multiplicative decrease factor |
| Multiplicative increase | 1.08 | 1.08 | 8% per second growth |
| Additive increase base | 1000 bps | 1000 bps | Minimum increase amount |
| Max increase multiplier | 1.5 | 1.5 | Cap increase at 1.5x received rate |
| Response time base | 100ms | 100ms | Base response time before adding RTT |
| Alpha multiplier | 0.5 | 0.5 | Half packet size per response time |

✅ **All constants match pion exactly**

---

## Example Scenarios

### Scenario 1: Startup (Far from Congestion)

```
Initial: target = 500 kbps
State: INCREASE
Mode: Multiplicative (no recent decrease)

After 1 second:
target = 500 * 1.08 = 540 kbps

After 2 seconds:
target = 540 * 1.08 = 583 kbps

After 3 seconds:
target = 583 * 1.08 = 630 kbps
```

**Result:** Fast ramp-up (8% per second)

### Scenario 2: Near Congestion

```
Previous decrease at: 1000 kbps
Current received: 950 kbps (within 3σ of 1000)
State: INCREASE
Mode: Additive (near last decrease)

RTT = 50ms
Response time = 100 + 50 = 150ms
Time since update = 150ms
Alpha = 0.5 * min(150/150, 1.0) = 0.5

Bits per frame = 950000 / 30 = 31667
Packets per frame = ceil(31667 / 9600) = 4
Packet size = 31667 / 4 = 7917 bits

Increase = 0.5 * 7917 = 3958 bps

New target = 950000 + 3958 = 953958 bps
```

**Result:** Slow additive increase (careful probing)

### Scenario 3: Congestion Detected

```
Current target: 1000 kbps
Received rate: 980 kbps
State: DECREASE
Usage: OVER

New target = 0.85 * 980 = 833 kbps
```

**Result:** Immediate 15% reduction

---

## Thread Safety

**Mutex protection (line 25):**

```go
lock sync.Mutex
```

**Protected operations:**
- `onReceivedRate()` - updates `latestReceivedRate`
- `updateRTT()` - updates `latestRTT`
- `onDelayStats()` - reads these values during rate calculation

**Why needed:**
- Rate controller called from bandwidth estimator thread
- RTT/received rate updated from RTCP thread
- Prevents race conditions

---

## Testing Strategy

### Unit Tests

1. **EMA tracking:**
   - Verify average convergence
   - Verify variance calculation
   - Verify std deviation

2. **State transitions:**
   - Test all 9 transition cases
   - Verify state machine matches types.py

3. **Additive increase:**
   - Near last decrease (within 3σ)
   - Correct alpha calculation
   - Packet size calculation
   - Capping at 1.5x received

4. **Multiplicative increase:**
   - Far from last decrease
   - 8% per second growth
   - Time clamping to 1 second
   - Never decrease

5. **Multiplicative decrease:**
   - 15% reduction (0.85x)
   - EMA update
   - Timestamp update

6. **Thread safety:**
   - Concurrent calls to onReceivedRate/updateRTT/onDelayStats
   - No race conditions

### Integration Tests

1. **Full AIMD cycle:**
   - Start low → ramp up → hit congestion → decrease → recover
   - Verify smooth transitions

2. **Mode switching:**
   - Verify switch from multiplicative to additive near congestion point
   - Verify 3σ boundary detection

3. **Time-based behavior:**
   - Verify response time affects additive increase
   - Verify RTT affects response time
   - Verify growth rate over time

4. **Comparison with pion:**
   - Feed same DelayStats sequence
   - Verify target bitrates match

---

## References

- Pion implementation: `pkg/gcc/rate_controller.go`
- Draft-ietf-rmcat-gcc-02 Section 5.5: "Rate control"
- AIMD fundamentals: [Chiu & Jain, 1989]

---

## Summary

**RateController is the OUTPUT stage of GCC.**

It converts bandwidth usage signals (OVER/UNDER/NORMAL) into concrete bitrate targets:

```
DelayStats (with Usage) → State Transition → Rate Calculation → Target Bitrate
```

Key features:
- ✅ AIMD with two increase modes (smart adaptation)
- ✅ EMA tracking of decrease rates (prevents oscillation)
- ✅ RTT-aware response time (handles high latency)
- ✅ Thread-safe auxiliary inputs (RTCP integration)
- ✅ All constants match pion exactly

**Status:** Production-ready. Exact port verified.
