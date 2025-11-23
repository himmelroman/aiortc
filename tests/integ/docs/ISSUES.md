# GCC Integration Test Issues & Anomalies

**Date**: 2025-11-23
**Status**: Analysis of unexpected behaviors and unresolved issues

---

## Critical Issues

### 1. Chromium Sending Bitrate Severely Underperforming

**Problem**: Despite generating high-entropy 1280x720 video at 30fps and configuring explicit bitrate constraints (1-10 Mbps), Chromium reports sending only **0.03 Mbps**.

**Evidence**:
```
Chromium WebRTC Stats (outbound-rtp):
- Reported TX: 0.03 Mbps
- Packets sent: ~900 over 85 seconds
- Expected: At least 2-5 Mbps for noisy 720p video

aiortc Reception Stats:
- Reported RX: 0.52 Mbps (from Chromium)
- 17x higher than Chromium reports sending
```

**Attempted Fixes (All Failed)**:
1. ❌ Increased resolution: 640x480 → 1280x720 (no significant change)
2. ❌ Maximum entropy noise: Random RGB per pixel per frame (minimal improvement)
3. ❌ Explicit bitrate constraints: `maxBitrate: 10 Mbps, minBitrate: 1 Mbps` (no effect on sending)
4. ❌ Moving visual elements: Colored bars + animated text (negligible impact)

**Impact**:
- Cannot properly test aiortc's receiving path under high bitrate stress
- Chromium→aiortc direction essentially untested
- Bidirectional GCC testing incomplete

**Possible Root Causes**:
1. **Chromium encoder too efficient**: VP8/VP9 compressing random noise surprisingly well
2. **Stats API measurement error**: Counting only payload bytes, not RTP overhead
3. **Bandwidth estimation limiting encoder**: Browser's internal BWE preventing higher bitrate
4. **Missing configuration**: Need additional flags/constraints we haven't discovered
5. **Canvas stream limitation**: `captureStream()` may have internal rate limits

**Status**: ⚠️ **UNRESOLVED** - Chromium peer not suitable for high-bitrate testing

---

### 2. Inconsistent Recovery Behavior Between Tests

**Problem**: Same aiortc GCC implementation shows drastically different recovery patterns in Pion vs Chromium tests.

**Evidence**:
```
Pion Test Recovery:
1.00 → 1.48 → 2.86 Mbps (stopped at 29% of baseline)
Duration: 6 seconds
Final: Stable at 2.86 Mbps

Chromium Test Recovery:
1.00 → 1.55 → 2.65 → 5.79 → 10.00 Mbps (full 100% recovery)
Duration: 15 seconds
Final: Stable at 10.00 Mbps
```

**Why This Is Concerning**:
1. **Same GCC algorithm** should behave consistently
2. **Same network conditions** (32 kbit burst, 50ms latency)
3. **Same test phases** and durations
4. 3.5x difference in final recovery (2.86 vs 10.00 Mbps)

**Hypotheses (Unverified)**:
1. **REMB feedback difference**: Chromium may send REMB packets, Pion may not
2. **Receiver window difference**: Different flow control mechanisms
3. **TWCC feedback timing**: Chromium may send feedback more/less frequently
4. **SDP negotiation difference**: Different codec parameters or extensions
5. **Hidden configuration**: Chromium's `setParameters()` affecting receiver behavior somehow

**What We Don't Know**:
- ❓ Is REMB actually being sent by Chromium? (Not verified)
- ❓ Are TWCC feedback intervals different? (Not measured)
- ❓ Does encoder feedback loop differ? (Not instrumented)
- ❓ Are there SDP differences affecting congestion control? (Not compared)

**Status**: ⚠️ **UNRESOLVED** - Need deeper protocol analysis to understand

---

### 3. Stats Measurement Discrepancies

**Problem**: Multiple inconsistencies in bitrate reporting across all tests.

**Chromium Test Discrepancy**:
```
Chromium reports sending:  0.03 Mbps (outbound-rtp.bytesSent)
aiortc reports receiving:  0.52 Mbps (calculated from RTP packets)
Ratio: 17x difference
```

**Pion Test Discrepancy**:
```
aiortc GCC target:        1.00 Mbps (Phase 2 stable)
Pion reports receiving:   3.42 Mbps (actual throughput)
Ratio: 3.4x difference
```

**Implications**:
1. **Cannot trust GCC estimates** - Actual throughput consistently higher than target
2. **Stats API unreliable** - Different tools measuring different things
3. **Encoder not respecting GCC** - Sending above target bitrate
4. **Network bursting** - Token bucket allowing brief bursts above rate

**Root Causes (Partial)**:
- Different measurement windows (GCC: ~200ms, Stats: 2-3s)
- GCC measures target, not actual encoder output
- RTP overhead not counted in some stats
- Encoder CBR vs VBR behavior

**Status**: ⚠️ **PARTIALLY UNDERSTOOD** - Measurement methodology unclear

---

### 4. Pion Reverse Path Complete Failure

**Problem**: In Pion test, pion→aiortc path never recovered from congestion.

**Evidence**:
```
Phase 3: pion GCC reduced to 0.10 Mbps (due to 2 Mbps limit)
Phase 4: Limits removed
Phase 5: pion GCC stayed at 0.10 Mbps (NO RECOVERY)

Duration: 90 seconds with no limits
Final: Still at 0.10 Mbps
```

**Why This Matters**:
- Shows GCC can get "stuck" in conservative mode
- Symmetric paths should both recover
- aiortc recovered (1.00 → 2.86 Mbps) but pion didn't
- Indicates potential issue with **Pion's** GCC implementation, not aiortc's

**Theories**:
1. **Pion bug**: Known issue in Pion's GCC recovery logic?
2. **Missing feedback**: aiortc not sending proper TWCC feedback to Pion?
3. **RTT estimation**: Pion's RTT estimate stuck too high?
4. **State machine issue**: Pion stuck in DECREASE state?

**Status**: ⚠️ **UNRESOLVED** - May indicate aiortc feedback issue

---

### 5. Higher Packet Loss in Chromium Test

**Problem**: Chromium test experienced 23% more packet loss than Pion test despite identical network limits.

**Evidence**:
```
Pion Test:      16,105 packets lost
Chromium Test:  19,854 packets lost
Difference:     +3,749 packets (+23%)

Network conditions: IDENTICAL
- Same 32 kbit burst
- Same 5 Mbps → 2 Mbps limits
- Same phase durations
```

**Possible Explanations**:
1. **Different pacing**: Chromium may send packets in different burst patterns
2. **Frame structure**: Different I-frame/P-frame patterns affecting burstiness
3. **RTP timestamp differences**: Different timestamp progression causing drops
4. **Packet size distribution**: Chromium may use different packet sizes
5. **Random variation**: 23% could be within normal variance

**Why It Matters**:
- More packet loss = worse user experience
- Suggests aiortc→Chromium path is less efficient than aiortc→Pion
- May indicate compatibility issues with browser receivers

**Status**: ⚠️ **UNEXPLAINED** - Need packet capture analysis

---

## Medium-Priority Issues

### 6. Conservative Min Bitrate Floor

**Problem**: Both tests hit 1.00 Mbps minimum quickly and stayed there.

**Evidence**:
```
Phase 2 congestion:
- Detection time: 3-6 seconds
- Cascading decreases: 10 → 8.5 → 7.2 → ... → 1.0 Mbps
- Hit floor: 1.00 Mbps configured minimum
- Duration at floor: 15-20 seconds
```

**Implications**:
- GCC has limited room to adapt downward
- In severe congestion, stuck at 1 Mbps regardless of available bandwidth
- Real network might only support 500 kbps → connection quality degraded

**Recommendation**:
```python
# Current (too conservative)
min_bitrate=1_000_000  # 1 Mbps

# Suggested (more adaptive)
min_bitrate=300_000    # 300 kbps - allows more adaptation
```

**Status**: ⚠️ **CONFIGURATION ISSUE** - Easy to fix but not tested

---

### 7. Slow Recovery Speed

**Problem**: Even in successful Chromium recovery, took 15 seconds to return to baseline.

**Evidence**:
```
Chromium Test Recovery Timeline:
t=0s:  1.00 Mbps (congestion just cleared)
t=3s:  1.55 Mbps (+55%)
t=6s:  2.65 Mbps (+71%)
t=9s:  5.79 Mbps (+118%)
t=12s: 10.00 Mbps (+73%)
t=15s: Stable at 10.00 Mbps

Total time to baseline: 15 seconds
```

**Comparison with Expectations**:
- Network limit removed at t=0
- Bandwidth immediately available
- GCC took 15 seconds to utilize it
- Multiplicative increase should be faster

**Why This Matters**:
- User on mobile switches from WiFi to 4G (better network)
- Takes 15+ seconds to utilize improved bandwidth
- Poor responsiveness to network improvements

**AIMD Formula Analysis**:
```
Increase rate: eta = 1.08^(time_delta)
For 100ms intervals: 1.08^0.1 = 1.0077 → +0.77% per 100ms

This is VERY conservative
Could be 1.15^0.1 = 1.0141 → +1.41% per 100ms (2x faster)
```

**Status**: ⚠️ **DESIGN LIMITATION** - GCC intentionally conservative

---

### 8. Arrival Group Formation Rate

**Problem**: Only 62 arrival groups formed from 9,914 acknowledgments in Pion test.

**Evidence**:
```
Total ACKs processed: 9,914
Arrival groups formed: 62
Ratio: 160 ACKs per group (average)

Expected: 20-50 ACKs per group for good granularity
Actual: 160 ACKs per group
```

**Implications**:
- Coarse-grained delay estimation
- Slower detection of congestion onset
- May miss brief congestion spikes
- Delay gradient smoothed over large windows

**Possible Causes**:
1. **Burst interval too large**: Groups forming slowly
2. **Timestamp quantization**: Not enough resolution to separate groups
3. **Network smoothing**: tc doing too good a job pacing packets
4. **Implementation issue**: Group formation logic too conservative

**Status**: ⚠️ **UNCLEAR** - May be normal, may indicate issue

---

## Test Infrastructure Limitations

### 9. Token Bucket Filter Unrealistic

**Problem**: Using `tc tbf` creates idealized network conditions not representative of real networks.

**What TBF Does**:
- Perfect token-based rate limiting
- Deterministic packet scheduling
- Small burst buffers (32 kbit)
- Immediate drops when buffer full

**Real Networks**:
- Variable queueing delays
- Jitter and reordering
- Bursty congestion (competing flows)
- Dynamic buffer sizing (bufferbloat)
- Cross-traffic interference

**Impact**:
- Tests show best-case GCC behavior
- Real networks will be harder to predict
- May have issues not visible in testing
- Need tests with netem (delay/jitter/loss) not just TBF

**Status**: ⚠️ **KNOWN LIMITATION** - Tests are optimistic

---

### 10. Missing Metrics

**What We're NOT Measuring**:
1. ❌ RTT evolution over time
2. ❌ Jitter statistics
3. ❌ Packet reordering events
4. ❌ Out-of-order delivery rate
5. ❌ TWCC feedback round-trip time
6. ❌ Encoder frame type distribution (I/P/B)
7. ❌ Actual queue depths in kernel
8. ❌ CPU usage during congestion
9. ❌ Memory allocation patterns
10. ❌ GCC state transitions (INCREASE/DECREASE/HOLD)

**Why This Matters**:
- Cannot diagnose subtle issues
- Missing data for optimization
- Can't validate delay-based detection
- No visibility into internal GCC state

**Status**: ⚠️ **INCOMPLETE INSTRUMENTATION**

---

## Unvalidated Assumptions

### 11. REMB Hypothesis (Unverified)

**Claim**: "Chromium provides REMB feedback that helps aiortc recover better"

**Evidence**: NONE - This is pure speculation

**Need**:
- Packet capture showing REMB packets
- SDP analysis showing REMB negotiation
- RTCP dumps showing receiver feedback
- Correlation between REMB values and GCC behavior

**Status**: ❓ **SPECULATION** - Not proven

---

### 12. TWCC Feedback Quality (Unchecked)

**Assumption**: "TWCC feedback is correctly formatted and timely"

**Not Verified**:
- Feedback packet format correctness
- Feedback timing (every 100ms? 200ms?)
- Sequence number handling
- Timestamp accuracy
- Missing ACK handling

**Status**: ❓ **UNTESTED** - Working on faith

---

## Questions Requiring Investigation

### Technical Questions

1. **Why does Chromium encoder produce so little bitrate?**
   - Need: Chromium internal encoder logs
   - Need: Packet capture to verify actual throughput
   - Need: Try different codecs (H.264 vs VP8 vs VP9)

2. **What causes 3.5x recovery difference between Pion and Chromium?**
   - Need: RTCP packet captures from both tests
   - Need: GCC state logging (INCREASE/DECREASE/HOLD transitions)
   - Need: Receiver window size comparison

3. **Why does actual throughput exceed GCC target by 3.4x?**
   - Need: Encoder output rate logging
   - Need: Network capture to measure real bitrate
   - Need: Token bucket burst timing analysis

4. **Why did Pion fail to recover in reverse direction?**
   - Need: Pion GCC state debugging
   - Need: Verify aiortc TWCC feedback to Pion
   - Need: RTT measurement logs

5. **Is delay-based detection actually working?**
   - Need: One-way delay measurements
   - Need: Kalman filter state logging
   - Need: Threshold adaptation tracking
   - Need: Overuse detector state transitions

### Process Questions

1. **Are we testing the right things?**
   - Current: Synthetic bandwidth limits
   - Missing: Real network traces replay
   - Missing: Competing traffic scenarios
   - Missing: WiFi/4G handover simulation

2. **Are our metrics meaningful?**
   - Measuring GCC target bitrate (internal state)
   - Should measure: User-perceived quality
   - Should measure: Freeze/stall events
   - Should measure: Resolution adaptation

3. **Is GCC even the right approach?**
   - GCC designed for video conferencing
   - Our use case: [UNKNOWN - what is it?]
   - Better alternatives: BBR? COPA? PCC?

---

## Recommendations for Further Testing

### Immediate Actions

1. **Packet Capture Analysis**
   ```bash
   tcpdump -i eth0 -w /tmp/chromium_test.pcap
   # Analyze: Actual bitrates, RTCP feedback, packet sizes
   ```

2. **Enable GCC Debug Logging**
   ```python
   # Add to delay_controller.py
   logger.debug(f"GCC State: {self._rate_controller.state}")
   logger.debug(f"Threshold: {self._adaptive_threshold.threshold}")
   logger.debug(f"Delay: {delay_stats.delay_ms}ms, Gradient: {delay_stats.gradient}")
   ```

3. **Compare SDP Offers**
   ```bash
   diff pion_offer.sdp chromium_offer.sdp
   # Look for: REMB, transport-cc, codec differences
   ```

4. **Try Different Video Sources**
   - Pre-recorded video file (not canvas)
   - Different resolutions (360p, 480p, 1080p)
   - Different frame rates (15fps, 60fps)
   - Different content (talking head, screen share, gaming)

### Longer-Term Investigations

1. **Real Network Replay**
   - Capture real mobile/WiFi traces
   - Replay in test environment
   - Validate GCC behavior on real patterns

2. **Comparative Analysis**
   - Test against other GCC implementations (libwebrtc, mediasoup)
   - Compare behavior under identical conditions
   - Identify deviations from reference

3. **Production Monitoring**
   - Deploy with extensive metrics
   - Collect real-world GCC behavior
   - Identify failure modes in production

---

## Summary

### What We Know Works
- Congestion detection (packet loss-based)
- AIMD decrease (multiplicative)
- Min bitrate floor enforcement
- Connection stability under stress

### What We Don't Understand
- Chromium sending bitrate (too low)
- Recovery variance (Pion vs Chromium)
- Throughput measurement discrepancies
- Pion reverse path recovery failure
- Delay-based detection efficacy

### What We Haven't Tested
- Real network conditions
- Delay-based detection in isolation
- RTT evolution
- Encoder interaction details
- Production deployment scenarios

### Overall Assessment

**GCC Implementation**: Partially validated, many unknowns remain

**Test Quality**: Good for basic validation, insufficient for production confidence

**Production Readiness**: ⚠️ **UNCERTAIN** - Works in controlled tests, unknown in real conditions

---

**Document Status**: DRAFT - Critical Analysis
**Next Steps**: Investigate top 3 critical issues before production deployment
