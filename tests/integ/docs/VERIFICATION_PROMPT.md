# GCC Implementation Verification Prompt

**Purpose:** Comprehensive verification of the aiortc GCC implementation against pion reference implementation
**Context:** This is a line-by-line port of Google Congestion Control (GCC) from pion (Go) to aiortc (Python)
**Requirement:** Zero tolerance for deviations - every component must be an exact port

---

## Your Task

You are a super-senior code reviewer and software engineer tasked with verifying that the aiortc GCC implementation is a **perfect, line-by-line port** of the pion implementation. Do NOT be lazy. Read every file completely. Check every constant, every formula, every algorithm.

---

## Background

**What is GCC?**
- Google Congestion Control - sender-side bandwidth estimation for WebRTC
- Uses TWCC (Transport-Wide Congestion Control) feedback
- Combines delay-based and loss-based congestion control

**Implementation Structure:**
- **Pion (Go):** `github.com/pion/interceptor/pkg/gcc` and `internal/cc`
- **aiortc (Python):** `/Users/himmelroman/projects/oylo/aiortc/src/aiortc/gcc/`
- **Pion local copy:** `/Users/himmelroman/projects/oylo/aiortc/tests/integ/pion/local_pion_gcc/`

**Core Principle:**
- Each aiortc component MUST be a line-by-line port of its pion counterpart
- Constants MUST match exactly (5ms thresholds, CHI=0.001, beta=0.85, etc.)
- Formulas MUST match exactly (d(i) = Δt_arrive - Δt_send, Kalman equations, AIMD)
- Algorithms MUST match exactly (grouping rules, hysteresis, threshold adaptation)
- Only permitted differences: Go→Python language adaptations (channels→callbacks, interfaces→Protocols, time.Duration→float)

---

## Components to Verify

### Core Pipeline (8 components)

| # | Component | aiortc File | Pion Reference | Doc |
|---|-----------|-------------|----------------|-----|
| 1 | Acknowledgment | `acknowledgment.py` | `internal/cc/acknowledgment.go` | [01](01_ARRIVAL_GROUP_ACCUMULATOR.md) |
| 2 | ArrivalGroup | `arrival_group.py` | `pkg/gcc/arrival_group.go` | [01](01_ARRIVAL_GROUP_ACCUMULATOR.md) |
| 3 | ArrivalGroupAccumulator | `arrival_group_accumulator.py` | `pkg/gcc/arrival_group_accumulator.go` | [01](01_ARRIVAL_GROUP_ACCUMULATOR.md) |
| 4 | KalmanFilter | `kalman_filter.py` | `pkg/gcc/kalman.go` | [02](02_SLOPE_ESTIMATOR_KALMAN.md) |
| 5 | SlopeEstimator | `slope_estimator.py` | `pkg/gcc/slope_estimator.go` | [02](02_SLOPE_ESTIMATOR_KALMAN.md) |
| 6 | AdaptiveThreshold | `adaptive_threshold.py` | `pkg/gcc/adaptive_threshold.go` | [03](03_OVERUSE_DETECTOR_ADAPTIVE_THRESHOLD.md) |
| 7 | OveruseDetector | `overuse_detector.py` | `pkg/gcc/overuse_detector.go` | [03](03_OVERUSE_DETECTOR_ADAPTIVE_THRESHOLD.md) |
| 8 | RateController | `rate_controller.py` | `pkg/gcc/rate_controller.go` | [04](04_RATE_CONTROLLER.md) |

### Integration Components (3 components)

| # | Component | aiortc File | Pion Reference | Doc |
|---|-----------|-------------|----------------|-----|
| 9 | RateCalculator | `rate_calculator.py` | `pkg/gcc/rate_calculator.go` | [05](05_INTEGRATION_COMPONENTS.md) |
| 10 | DelayController | `delay_controller.py` | `pkg/gcc/delay_based_bwe.go` | [05](05_INTEGRATION_COMPONENTS.md) |
| 11 | FeedbackAdapter | `feedback_adapter.py` | `internal/cc/feedback_adapter.go` | [05](05_INTEGRATION_COMPONENTS.md) |

### Supporting Types

| Type | File | Pion Reference |
|------|------|----------------|
| BandwidthUsage | `types.py` | `pkg/gcc/usage.go` |
| RateControlState | `types.py` | `pkg/gcc/state.go` |
| DelayStats | `types.py` | `pkg/gcc/delay_based_bwe.go` |

---

## Verification Steps

### Step 1: Component-by-Component Code Review

For EACH of the 11 components above:

1. **Read the pion Go file completely**
   - Location: `/Users/himmelroman/projects/oylo/aiortc/tests/integ/pion/local_pion_gcc/`
   - Understand every line, constant, formula, algorithm

2. **Read the aiortc Python file completely**
   - Location: `/Users/himmelroman/projects/oylo/aiortc/src/aiortc/gcc/`
   - Compare line-by-line with pion

3. **Verify Constants**
   - Extract ALL numeric constants from pion
   - Verify each one exists in aiortc with EXACT same value
   - Account for unit conversions (Go time.Duration nanoseconds → Python float seconds)

4. **Verify Formulas**
   - Extract ALL mathematical formulas from pion
   - Verify each one is implemented identically in aiortc
   - Check operator precedence, parentheses, rounding

5. **Verify Algorithms**
   - Compare control flow (if/else, loops, state machines)
   - Verify grouping rules, thresholds, conditions
   - Check edge cases handling

6. **Verify Data Structures**
   - Compare struct fields with dataclass fields
   - Verify initialization values match
   - Check mutability and state management

7. **Note Adaptations**
   - Document any Go→Python adaptations
   - Verify they are necessary and correct
   - Examples: channels→callbacks, time.Duration→float

### Step 2: Critical Constants Verification

Extract and verify these critical constants match EXACTLY:

**ArrivalGroupAccumulator:**
- Inter-departure threshold: 5ms (0.005 seconds)
- Inter-arrival threshold: 5ms (0.005 seconds)
- Inter-group delay variation threshold: 0

**KalmanFilter:**
- CHI: 0.001
- Process uncertainty Q: 1e-3 (0.001)
- Initial estimate error: 0.1
- Initial measurement uncertainty: 0.0
- EMA alpha formula: `(1 - chi)^(30 / (1000 * 5ms))`

**AdaptiveThreshold:**
- Initial threshold: 12.5ms (0.0125 seconds)
- Overuse coefficient up: 0.01
- Overuse coefficient down: 0.00018
- Min threshold: 6ms (0.006 seconds)
- Max threshold: 600ms (0.6 seconds)

**OveruseDetector:**
- Overuse time threshold: 10ms (0.01 seconds)
- Hysteresis: delta/2 on first spike

**RateController:**
- AIMD beta: 0.85
- Multiplicative increase eta: 1.08
- EMA alpha: 0.95
- Min feedback interval: 200ms (0.2 seconds)
- Max feedback interval: 1000ms (1.0 seconds)

**RateCalculator:**
- Window size: 500ms (0.5 seconds)

**DelayController:**
- Overuse time: 10ms (0.01 seconds)
- Rate calculator window: 500ms (0.5 seconds)

### Step 3: Core GCC Formula Verification

Verify the core inter-group delay variation formula:

**Pion (slope_estimator.go):**
```go
func interGroupDelayVariation(a, b arrivalGroup) time.Duration {
    return (b.arrival - a.arrival) - (b.departure - a.departure)
}
```

**aiortc (slope_estimator.py):**
```python
def _inter_group_delay_variation(a: ArrivalGroup, b: ArrivalGroup) -> float:
    return (b.arrival - a.arrival) - (b.departure - a.departure)
```

**Verification:**
- Formula structure identical? ✓/✗
- Variable names match? ✓/✗
- Time units consistent? ✓/✗

### Step 4: Time Conversion Verification

Verify all time conversions are correct:

**Pion → aiortc:**
- Go: `time.Duration` (int64 nanoseconds)
- Python: `float` (seconds)

**Examples to check:**
- 5ms: `5 * time.Millisecond` → `0.005`
- 10ms: `10 * time.Millisecond` → `0.01`
- 500ms: `500 * time.Millisecond` → `0.5`
- 12.5ms: `12.5 * time.Millisecond` → `0.0125`

**Conversion formula:**
- Milliseconds → Seconds: `ms / 1000.0`
- Nanoseconds → Seconds: `ns / 1_000_000_000.0`

### Step 5: State Machine Verification

**RateControlState Transitions (types.py vs state.go):**

Verify state transition table matches exactly:

| Current State | BandwidthUsage | Next State |
|--------------|----------------|------------|
| INCREASE | OVER | DECREASE |
| INCREASE | NORMAL | INCREASE |
| INCREASE | UNDER | HOLD |
| DECREASE | OVER | DECREASE |
| DECREASE | NORMAL | HOLD |
| DECREASE | UNDER | HOLD |
| HOLD | OVER | DECREASE |
| HOLD | NORMAL | INCREASE |
| HOLD | UNDER | HOLD |

Check the `transition()` method in `types.py` line-by-line against `state.go`.

### Step 6: AIMD Algorithm Verification

**RateController (rate_controller.py vs rate_controller.go):**

Verify the AIMD (Additive Increase Multiplicative Decrease) algorithm:

**Decrease (OVER):**
- Formula: `target = beta * received_rate`
- Beta value: 0.85
- EMA update: `avg_max_rate = alpha * avg_max_rate + (1 - alpha) * target`
- Alpha value: 0.95

**Increase (NORMAL):**
- Two modes: additive (near last decrease) and multiplicative (far from decrease)
- Additive: `increase = max(1000, alpha * packet_size)`
- Multiplicative: `rate = eta^time_delta * target`
- Eta value: 1.08
- Time threshold: 0.5 * avg_max_rate / (1000 * alpha)

**Hold (UNDER):**
- No change to target bitrate

### Step 7: Documentation Verification

For each component doc (01-05):

1. **Read the documentation file**
   - Location: `/Users/himmelroman/projects/oylo/aiortc/tests/integ/docs/`

2. **Verify documentation accuracy**
   - Do code examples match actual implementation?
   - Are line number references correct?
   - Are constants listed correctly?
   - Are formulas documented correctly?

3. **Check implementation status**
   - Are all components marked as ✅ COMPLETE accurate?
   - Are file paths correct?
   - Are pion references correct?

### Step 8: Pipeline Wiring Verification

**DelayController (delay_controller.py):**

Verify the component wiring matches pion exactly:

1. **Component creation order (bottom-up):**
   - RateController (final stage)
   - OveruseDetector → RateController.on_delay_stats
   - SlopeEstimator → OveruseDetector.on_delay_stats
   - ArrivalGroupAccumulator → SlopeEstimator.on_arrival_group
   - RateCalculator (parallel pipeline)

2. **Callback chain:**
   - ArrivalGroupAccumulator emits → SlopeEstimator receives
   - SlopeEstimator emits → OveruseDetector receives
   - OveruseDetector emits → RateController receives
   - RateCalculator emits → RateController.on_received_rate
   - RateController emits → User callback

3. **Initialization parameters:**
   - AdaptiveThreshold: default constructor
   - OveruseDetector: threshold + 10ms overuse_time
   - KalmanFilter: default constructor
   - RateCalculator: 500ms window

### Step 9: FeedbackAdapter Integration Verification

**FeedbackAdapter (feedback_adapter.py vs feedback_adapter.go):**

1. **History management:**
   - LRU cache size: 5000 packets
   - Key: (ssrc, sequence_number)
   - Eviction policy matches?

2. **Time coordination:**
   - Base departure time tracking
   - Relative departure: `1.0 + (departure - base)`
   - Reference time from TWCC: `reference_time_24bit * 64000 / 1_000_000.0`

3. **TWCC parsing:**
   - Uses aiortc's TWCCParser
   - Extracts base_seq, reference_time correctly
   - Accumulates arrival times with deltas

### Step 10: Cross-File Consistency

Verify consistency across all files:

1. **Import statements:**
   - All components properly imported in `__init__.py`?
   - Circular dependencies avoided?

2. **Type consistency:**
   - Acknowledgment used consistently across all components?
   - DelayStats passed correctly through pipeline?
   - BandwidthUsage, RateControlState used correctly?

3. **Callback signatures:**
   - All callbacks match expected signatures?
   - Type hints accurate?

---

## Deliverable

Create a comprehensive verification report with:

### 1. Executive Summary
- Overall assessment: PASS / FAIL / NEEDS REVISION
- Number of issues found (should be ZERO for PASS)
- Confidence level in port accuracy

### 2. Component-by-Component Findings

For each of 11 components:

```markdown
## Component: [Name]

**Pion Reference:** [file:lines]
**aiortc Implementation:** [file:lines]
**Status:** ✅ EXACT PORT / ⚠️ MINOR ISSUES / ❌ MAJOR ISSUES

### Constants Verification
- [constant_name]: [pion_value] vs [aiortc_value] → ✅/❌

### Formulas Verification
- [formula_name]: ✅/❌ [details if mismatch]

### Algorithms Verification
- [algorithm_name]: ✅/❌ [details if mismatch]

### Adaptations
- [adaptation]: JUSTIFIED / UNJUSTIFIED [explanation]

### Issues Found
- [issue description with line numbers]
```

### 3. Critical Constants Summary Table

| Constant | Pion Value | aiortc Value | Match | Component |
|----------|------------|--------------|-------|-----------|
| Inter-departure threshold | 5ms | ? | ✅/❌ | ArrivalGroupAccumulator |
| ... | ... | ... | ... | ... |

### 4. Documentation Accuracy Report

For each doc file (01-05):
- Code examples accurate? ✅/❌
- Line references correct? ✅/❌
- Constants listed correctly? ✅/❌
- Implementation status accurate? ✅/❌

### 5. Issues List (if any)

Priority classification:
- **CRITICAL:** Formula mismatch, constant mismatch, algorithm deviation
- **MAJOR:** Logic error, incorrect adaptation, missing validation
- **MINOR:** Comment mismatch, style inconsistency, documentation error

### 6. Recommendations

If issues found:
- Specific fixes needed (with line numbers)
- Verification steps to confirm fixes

---

## Success Criteria

The verification PASSES only if:

1. ✅ All 11 components are exact line-by-line ports
2. ✅ All critical constants match exactly (accounting for time unit conversions)
3. ✅ All formulas match exactly
4. ✅ All algorithms match exactly
5. ✅ All adaptations are justified and correct
6. ✅ Documentation is accurate and up-to-date
7. ✅ Component wiring matches pion
8. ✅ Zero unintentional deviations found

---

## Important Reminders

- **DO NOT BE LAZY:** Read every line of every file
- **CHECK EVERYTHING:** Constants, formulas, algorithms, edge cases
- **TRUST NOTHING:** Verify even if comments say "exact port"
- **BE THOROUGH:** This is production code for real-time congestion control
- **ZERO TOLERANCE:** Any deviation (except justified adaptations) is a failure

The goal is 100% confidence that this aiortc implementation will behave identically to pion's GCC implementation.

---

## Files to Read

### Pion Reference (Go)
```
/Users/himmelroman/projects/oylo/aiortc/tests/integ/pion/local_pion_gcc/internal/cc/acknowledgment.go
/Users/himmelroman/projects/oylo/aiortc/tests/integ/pion/local_pion_gcc/internal/cc/feedback_adapter.go
/Users/himmelroman/projects/oylo/aiortc/tests/integ/pion/local_pion_gcc/pkg/gcc/arrival_group.go
/Users/himmelroman/projects/oylo/aiortc/tests/integ/pion/local_pion_gcc/pkg/gcc/arrival_group_accumulator.go
/Users/himmelroman/projects/oylo/aiortc/tests/integ/pion/local_pion_gcc/pkg/gcc/kalman.go
/Users/himmelroman/projects/oylo/aiortc/tests/integ/pion/local_pion_gcc/pkg/gcc/slope_estimator.go
/Users/himmelroman/projects/oylo/aiortc/tests/integ/pion/local_pion_gcc/pkg/gcc/adaptive_threshold.go
/Users/himmelroman/projects/oylo/aiortc/tests/integ/pion/local_pion_gcc/pkg/gcc/overuse_detector.go
/Users/himmelroman/projects/oylo/aiortc/tests/integ/pion/local_pion_gcc/pkg/gcc/rate_controller.go
/Users/himmelroman/projects/oylo/aiortc/tests/integ/pion/local_pion_gcc/pkg/gcc/rate_calculator.go
/Users/himmelroman/projects/oylo/aiortc/tests/integ/pion/local_pion_gcc/pkg/gcc/delay_based_bwe.go
/Users/himmelroman/projects/oylo/aiortc/tests/integ/pion/local_pion_gcc/pkg/gcc/usage.go
/Users/himmelroman/projects/oylo/aiortc/tests/integ/pion/local_pion_gcc/pkg/gcc/state.go
```

### aiortc Implementation (Python)
```
/Users/himmelroman/projects/oylo/aiortc/src/aiortc/gcc/acknowledgment.py
/Users/himmelroman/projects/oylo/aiortc/src/aiortc/gcc/arrival_group.py
/Users/himmelroman/projects/oylo/aiortc/src/aiortc/gcc/arrival_group_accumulator.py
/Users/himmelroman/projects/oylo/aiortc/src/aiortc/gcc/kalman_filter.py
/Users/himmelroman/projects/oylo/aiortc/src/aiortc/gcc/slope_estimator.py
/Users/himmelroman/projects/oylo/aiortc/src/aiortc/gcc/adaptive_threshold.py
/Users/himmelroman/projects/oylo/aiortc/src/aiortc/gcc/overuse_detector.py
/Users/himmelroman/projects/oylo/aiortc/src/aiortc/gcc/rate_controller.py
/Users/himmelroman/projects/oylo/aiortc/src/aiortc/gcc/rate_calculator.py
/Users/himmelroman/projects/oylo/aiortc/src/aiortc/gcc/delay_controller.py
/Users/himmelroman/projects/oylo/aiortc/src/aiortc/gcc/feedback_adapter.py
/Users/himmelroman/projects/oylo/aiortc/src/aiortc/gcc/types.py
/Users/himmelroman/projects/oylo/aiortc/src/aiortc/gcc/__init__.py
```

### Documentation
```
/Users/himmelroman/projects/oylo/aiortc/tests/integ/docs/IMPLEMENTATION_LOG.md
/Users/himmelroman/projects/oylo/aiortc/tests/integ/docs/01_ARRIVAL_GROUP_ACCUMULATOR.md
/Users/himmelroman/projects/oylo/aiortc/tests/integ/docs/02_SLOPE_ESTIMATOR_KALMAN.md
/Users/himmelroman/projects/oylo/aiortc/tests/integ/docs/03_OVERUSE_DETECTOR_ADAPTIVE_THRESHOLD.md
/Users/himmelroman/projects/oylo/aiortc/tests/integ/docs/04_RATE_CONTROLLER.md
/Users/himmelroman/projects/oylo/aiortc/tests/integ/docs/05_INTEGRATION_COMPONENTS.md
/Users/himmelroman/projects/oylo/aiortc/tests/integ/docs/IMPLEMENTATION_VERIFICATION.md
```

---

## Begin Verification Now

Start with Component 1 (Acknowledgment) and work through all 11 components systematically.
Be extremely thorough. Read every line. Check every constant. Verify every formula.
The quality of this verification determines whether the implementation can be trusted in production.
