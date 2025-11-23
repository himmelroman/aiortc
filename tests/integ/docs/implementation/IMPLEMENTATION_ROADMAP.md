# GCC/TWCC Implementation Roadmap

**Purpose:** Step-by-step plan to align aiortc's GCC implementation with pion
**Estimated Total Effort:** 18-26 hours

---

## Phase 1: Foundation - Missing Core Components (8-12 hours)

### 1.1 Create Acknowledgment Model (1 hour)

**New File:** `src/aiortc/gcc/acknowledgment.py`

```python
from dataclasses import dataclass

@dataclass
class Acknowledgment:
    """
    Acknowledgment of a sent packet with timing information.

    Matches pion's internal/cc/acknowledgment.go
    """
    sequence_number: int
    size: int           # Bytes
    departure: float    # Send time (seconds since epoch)
    arrival: float      # Receive time (seconds since epoch)
```

**Reference:**
- Pion: `internal/cc/acknowledgment.go` (lines 1-10)

---

### 1.2 Create FeedbackAdapter (2 hours)

**New File:** `src/aiortc/gcc/feedback_adapter.py`

Converts TWCC feedback packets into Acknowledgment lists.

**Functionality:**
- Track sent packets (sequence → send_time mapping)
- Parse TWCC TransportLayerCC packets
- Match received packets with sent packets
- Create Acknowledgment objects with both times
- Handle packet loss (missing in feedback)

**Reference:**
- Pion: `internal/cc/feedback_adapter.go`
- Draft: draft-holmer-rmcat-transport-wide-cc-extensions-01

---

### 1.3 Create ArrivalGroup + ArrivalGroupAccumulator (3-4 hours)

**New File:** `src/aiortc/gcc/arrival_group.py`

Implement packet burst grouping logic.

**Key Functions:**
- `interDepartureTimePkt()` - check send time proximity
- `interArrivalTimePkt()` - check arrival time proximity
- `interGroupDelayVariationPkt()` - check if same queuing delay

**Thresholds:**
- Inter-departure: 5ms
- Inter-arrival: 5ms
- Delay variation: 0ms

**Reference:**
- Pion: `pkg/gcc/arrival_group.go` (lines 1-40)
- Pion: `pkg/gcc/arrival_group_accumulator.go` (lines 1-111)
- See `01_ARRIVAL_GROUP_ACCUMULATOR.md` for full details

**Testing:**
- Unit tests for grouping rules
- Integration test comparing groups with pion

---

### 1.4 Create SlopeEstimator + KalmanFilter (3-4 hours)

**New Files:**
- `src/aiortc/gcc/kalman_filter.py`
- `src/aiortc/gcc/slope_estimator.py`

Implement inter-group delay variation calculation and filtering.

**Core Formula:**
```python
def inter_group_delay_variation(prev: ArrivalGroup, curr: ArrivalGroup) -> float:
    arrival_delta = (curr.arrival - prev.arrival) * 1000  # ms
    departure_delta = (curr.departure - prev.departure) * 1000  # ms
    return arrival_delta - departure_delta
```

**Kalman Parameters:**
- Process uncertainty Q = 1e-3
- Initial estimate error P = 0.1
- Measurement uncertainty R = adaptive
- Chi constant = 0.001

**Reference:**
- Pion: `pkg/gcc/slope_estimator.go` (lines 1-58)
- Pion: `pkg/gcc/kalman.go` (lines 1-98)
- See `02_SLOPE_ESTIMATOR_KALMAN.md` for full details

**Testing:**
- Kalman convergence tests
- Delay variation calculation tests
- Integration with arrival groups

---

### 1.5 Update OveruseDetector + Create AdaptiveThreshold (2-3 hours)

**New File:** `src/aiortc/gcc/adaptive_threshold.py`

**Modified File:** `src/aiortc/gcc/overuse_detector.py`

Extract threshold logic into separate class with correct coefficients.

**Coefficients:**
- k_up = 0.01 (fast increase)
- k_down = 0.00018 (slow decrease)
- Initial threshold = 12.5ms
- Range = [6ms, 600ms]

**Reference:**
- Pion: `pkg/gcc/adaptive_threshold.go` (lines 1-107)
- Pion: `pkg/gcc/overuse_detector.go` (lines 1-87)
- See `03_OVERUSE_DETECTOR_ADAPTIVE_THRESHOLD.md` for full details

**Testing:**
- Threshold adaptation tests
- Overuse detection logic tests
- Sustained vs transient spike handling

---

## Phase 2: Architecture Refactor (4-6 hours)

### 2.1 Remove Incorrect Components (1 hour)

**Delete/Deprecate:**
- `InterArrival` class (from rate.py) - wrong algorithm
- `OveruseEstimator` class (from rate.py) - wrong Kalman filter
- Direct usage of these in `DelayBasedController`

**Why:**
- These are libwebrtc receiver-side components
- We need pion sender-side components
- Different input, different purpose, different algorithm

---

### 2.2 Create DelayController (2-3 hours)

**New File:** `src/aiortc/gcc/delay_controller.py`

Orchestrate the delay-based pipeline.

**Architecture:**
```python
class DelayController:
    def __init__(self):
        # Pipeline components
        self.accumulator = ArrivalGroupAccumulator()
        self.slope_estimator = SlopeEstimator(
            kalman_filter=KalmanFilter()
        )
        self.overuse_detector = OveruseDetector(
            threshold=AdaptiveThreshold()
        )
        self.rate_controller = RateController()
        self.rate_calculator = RateCalculator()

    def update_delay_estimate(self, acks: List[Acknowledgment]):
        # Pipeline: acks → groups → slopes → overuse → rate
        def on_group(group):
            self.slope_estimator.on_arrival_group(group)

        def on_delay_stats(stats):
            self.overuse_detector.on_delay_stats(stats)

        def on_rate_update(rate):
            self.rate_controller.on_received_rate(rate)

        # Wire up callbacks
        self.slope_estimator.on_delay_stats = on_delay_stats
        self.overuse_detector.on_delay_stats = self.rate_controller.on_delay_stats
        self.rate_calculator.on_rate_update = on_rate_update

        # Process acknowledgments
        self.accumulator.process_acknowledgments(acks, on_group)
        self.rate_calculator.process_acknowledgments(acks)
```

**Reference:**
- Pion: `pkg/gcc/delay_based_bwe.go` (lines 1-110)

---

### 2.3 Update RateController (2 hours)

**File:** Refactor from `src/aiortc/rate.py` → `src/aiortc/gcc/rate_controller.py`

Already mostly correct, but verify:
- EMA tracking for decrease points ✅
- 3σ near-max detection ✅
- Additive vs multiplicative increase ✅
- State transitions match pion ✅

**Minor updates needed:**
- Ensure state machine EXACTLY matches pion's `state.go`
- Verify HOLD→INCREASE transition condition
- Verify response time calculation

**Reference:**
- Pion: `pkg/gcc/rate_controller.go` (lines 1-179)
- Pion: `pkg/gcc/state.go` (lines 1-65)
- Pion: `pkg/gcc/usage.go` (lines 1-28)

---

### 2.4 Create RateCalculator (1 hour)

**New File:** `src/aiortc/gcc/rate_calculator.py`

Calculate received bitrate from acknowledgments (not from packets!).

**Key Difference from RateCounter:**
```python
# OLD (RateCounter): Per-packet, 1ms buckets
def add(self, value: int, now_ms: int):
    self._buckets[index].value += value

# NEW (RateCalculator): Sliding window over acknowledgments
def process_acks(self, acks: List[Acknowledgment]):
    for ack in acks:
        if ack.arrival.is_zero():
            continue  # Skip lost packets
        history.append(ack)

        # Remove old packets outside 500ms window
        # Calculate rate from history
```

**Reference:**
- Pion: `pkg/gcc/rate_calculator.go` (lines 1-67)

---

### 2.5 Update SenderSideBandwidthEstimator (1 hour)

**File:** `src/aiortc/gcc/estimator.py`

Restructure to match pion's architecture.

**Changes:**
```python
class SenderSideBandwidthEstimator:
    def __init__(self, ...):
        self.delay_controller = DelayController()
        self.loss_controller = LossBasedController()
        self.feedback_adapter = FeedbackAdapter()

    def process_twcc_feedback(self, rtcp_packet):
        # Convert TWCC → Acknowledgments
        acks = self.feedback_adapter.on_transport_cc_feedback(rtcp_packet)

        # Update delay-based controller
        self.delay_controller.update_delay_estimate(acks)

        # Update loss-based controller
        self.loss_controller.update_loss_estimate(acks)

        # Combine estimates (min of delay and loss)
        delay_bitrate = self.delay_controller.get_target_bitrate()
        loss_bitrate = self.loss_controller.get_estimate(delay_bitrate)
        return min(delay_bitrate, loss_bitrate)
```

**Reference:**
- Pion: `pkg/gcc/send_side_bwe.go` (lines 1-302)

---

## Phase 3: Testing and Validation (4-6 hours)

### 3.1 Unit Tests (2-3 hours)

For each new component:

**ArrivalGroupAccumulator:**
- Single burst grouping
- Multiple bursts
- Delay variation filtering
- Out-of-order handling

**SlopeEstimator + Kalman:**
- Delay variation calculation
- Kalman convergence
- Noise filtering

**OveruseDetector + AdaptiveThreshold:**
- Threshold adaptation
- Sustained overuse detection
- Transient spike filtering

**RateController:**
- State transitions
- Near-max detection
- Additive vs multiplicative increase

---

### 3.2 Integration Tests (1-2 hours)

**Test Setup:**
1. Capture TWCC feedback from real pion peer
2. Feed same feedback to both pion and aiortc
3. Compare outputs at each pipeline stage

**Comparisons:**
- Acknowledgment lists match
- Arrival groups match
- Delay measurements match
- Kalman estimates match
- Overuse decisions match
- Rate decisions match

---

### 3.3 E2E Validation (1 hour)

**Existing VP9 E2E tests should pass with new implementation:**

- `test_vp9_peer_to_peer` - Basic connection
- `test_network_adaptation` - Rate adaptation under varying conditions
- `test_packet_loss_recovery` - Loss-based BWE interaction

**Metrics to validate:**
- Bitrate converges to available bandwidth
- Responds to congestion within 500ms
- Doesn't oscillate wildly
- Loss rate stays < 5%

---

## Phase 4: Integration with aiortc (2 hours)

### 4.1 Update RTCRtpSender (1 hour)

**File:** `src/aiortc/rtcrtpsender.py`

Wire GCC into RTP sender:

```python
class RTCRtpSender:
    def __init__(self):
        self.gcc_estimator = SenderSideBandwidthEstimator()

    async def _run_rtcp(self):
        while True:
            packets = await self._rtcp_recv()
            for pkt in packets:
                if isinstance(pkt, rtcp.TransportLayerCC):
                    # Process TWCC feedback
                    target_bitrate = self.gcc_estimator.process_twcc_feedback(pkt)

                    # Apply to encoder
                    if self._encoder:
                        self._encoder.set_target_bitrate(target_bitrate)
```

---

### 4.2 Update Encoder Integration (1 hour)

**Files:**
- `src/aiortc/codecs/vpx.py`
- `src/aiortc/codecs/h264.py`

Ensure encoders respond to GCC bitrate changes:

```python
class VpxEncoder:
    def set_target_bitrate(self, bitrate_bps: int):
        # Update VP9 encoder config
        self._bitrate = bitrate_bps
        # Reconfigure encoder if needed
```

---

## Success Criteria

### Functional Requirements

✅ All unit tests pass
✅ Integration tests show outputs match pion
✅ E2E tests pass with new implementation
✅ Bitrate adapts to network conditions
✅ No degradation in video quality under good conditions
✅ Graceful degradation under congestion

### Performance Requirements

✅ GCC processing adds < 1ms latency per TWCC feedback
✅ Memory usage remains bounded (< 1MB for GCC state)
✅ CPU usage < 5% for GCC processing (on typical hardware)

### Code Quality Requirements

✅ All components have docstrings matching pion's documentation
✅ Type hints on all public methods
✅ Line-by-line alignment documented
✅ Test coverage > 90% for new components

---

## Risk Mitigation

### Risk 1: Breaking Existing Functionality

**Mitigation:**
- Keep old components temporarily
- Add feature flag to switch between old/new GCC
- Run both in parallel during transition
- Extensive regression testing

### Risk 2: Performance Regression

**Mitigation:**
- Profile before and after
- Benchmark critical paths
- Optimize hot loops if needed
- Monitor memory allocation

### Risk 3: Behavioral Differences from Pion

**Mitigation:**
- Detailed logging at each pipeline stage
- Comparative testing with real pion peer
- Document any intentional deviations
- Consult pion team if ambiguous

---

## Implementation Order

**MUST be done in this order due to dependencies:**

1. Acknowledgment model (no dependencies)
2. ArrivalGroup (depends on Acknowledgment)
3. ArrivalGroupAccumulator (depends on ArrivalGroup)
4. KalmanFilter (no dependencies)
5. SlopeEstimator (depends on ArrivalGroup, KalmanFilter)
6. AdaptiveThreshold (no dependencies)
7. OveruseDetector (depends on AdaptiveThreshold, SlopeEstimator)
8. RateController (depends on OveruseDetector)
9. RateCalculator (depends on Acknowledgment)
10. DelayController (depends on all above)
11. FeedbackAdapter (depends on Acknowledgment)
12. SenderSideBandwidthEstimator (depends on DelayController, FeedbackAdapter)
13. Integration into RTCRtpSender

---

## Timeline Estimate

**Phase 1 (Foundation):** 8-12 hours
- Can be parallelized if multiple developers
- Critical path: Accumulator → Slope → Overuse → Rate

**Phase 2 (Architecture):** 4-6 hours
- Must be done after Phase 1
- Can start integration in parallel with testing

**Phase 3 (Testing):** 4-6 hours
- Can write tests while implementing
- Integration tests require Phase 1+2 complete

**Phase 4 (Integration):** 2 hours
- Quick once components are ready

**Total: 18-26 hours**

If working full-time: **3-4 days**
If working part-time: **1-2 weeks**

---

## Post-Implementation Checklist

### Documentation
- [ ] All new components have docstrings
- [ ] Update main README with GCC implementation details
- [ ] Add architecture diagram showing pipeline flow
- [ ] Document configuration options

### Code Quality
- [ ] Run linters (mypy, flake8, black)
- [ ] Verify type hints coverage
- [ ] Check for code duplication
- [ ] Optimize hot paths if needed

### Testing
- [ ] All unit tests pass
- [ ] Integration tests pass
- [ ] E2E tests pass
- [ ] Performance benchmarks meet targets
- [ ] Test with real network conditions

### Review
- [ ] Code review with team
- [ ] Compare behavior with pion in production
- [ ] Verify no regressions in existing functionality
- [ ] Validate against draft-ietf-rmcat-gcc-02 spec

---

## References

### Pion Source Files
- `pkg/gcc/send_side_bwe.go` - Main BWE controller
- `pkg/gcc/delay_based_bwe.go` - Delay controller orchestration
- `pkg/gcc/arrival_group_accumulator.go` - Burst grouping
- `pkg/gcc/slope_estimator.go` - Delay variation calculation
- `pkg/gcc/kalman.go` - 1D Kalman filter
- `pkg/gcc/overuse_detector.go` - Overuse detection
- `pkg/gcc/adaptive_threshold.go` - Threshold adaptation
- `pkg/gcc/rate_controller.go` - AIMD rate control
- `pkg/gcc/rate_calculator.go` - Received rate estimation
- `pkg/gcc/loss_based_bwe.go` - Loss-based BWE
- `internal/cc/acknowledgment.go` - Acknowledgment model
- `internal/cc/feedback_adapter.go` - TWCC → Acknowledgments

### Specifications
- draft-ietf-rmcat-gcc-02
- draft-holmer-rmcat-transport-wide-cc-extensions-01
- [Analysis and Design of the Google Congestion Control](https://c3lab.poliba.it/images/6/65/Gcc-analysis.pdf)

### Documentation in This Repo
- `GCC_TWCC_ALIGNMENT_REVIEW.md` - Overall comparison
- `01_ARRIVAL_GROUP_ACCUMULATOR.md` - Burst grouping details
- `02_SLOPE_ESTIMATOR_KALMAN.md` - Delay estimation details
- `03_OVERUSE_DETECTOR_ADAPTIVE_THRESHOLD.md` - Overuse detection details

---

## Conclusion

This roadmap provides a clear path to align aiortc's GCC implementation with pion's reference implementation. The key is to:

1. **Remove** the incorrect components (InterArrival, OveruseEstimator)
2. **Implement** the missing core components (ArrivalGroupAccumulator, SlopeEstimator, etc.)
3. **Refactor** the architecture to match pion's pipeline
4. **Test** extensively at each stage
5. **Integrate** into aiortc's RTP sender

By following this plan and the detailed component documentation, we ensure an accurate, line-by-line porting of pion's GCC implementation without reinventing anything.
