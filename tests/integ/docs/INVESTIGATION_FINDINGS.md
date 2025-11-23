# Investigation Findings: Chromium Bitrate Issue

**Date**: 2025-11-23
**Status**: Root Cause Identified

## Problem Summary

Chromium reports sending only **0.03-0.05 Mbps** despite:
- 1280x720 resolution @ 30fps configuration
- Maximum entropy random noise (incompressible content)
- Explicit bitrate constraints: `maxBitrate: 10 Mbps, minBitrate: 1 Mbps`
- aiortc reports receiving 0.52 Mbps (10-17x higher than Chromium reports)

## Root Cause: Bandwidth Estimation Severely Underestimating

### Smoking Gun Evidence

From comprehensive WebRTC stats analysis:

```
Time: 1763886937550 | QualLimit: bandwidth | TargetBitrate: 237 Kbps | BWE: 300 Kbps | Res: 480x270 | FPS: 5
Time: 1763886938550 | QualLimit: bandwidth | TargetBitrate: 206 Kbps | BWE: 260 Kbps | Res: 480x270 | FPS: 5
Time: 1763886940550 | QualLimit: bandwidth | TargetBitrate: 120 Kbps | BWE: 166 Kbps | Res: 320x180 | FPS: 30
Time: 1763886942550 | QualLimit: bandwidth | TargetBitrate: 84 Kbps  | BWE: 106 Kbps | Res: 320x180 | FPS: 30
Time: 1763886944551 | QualLimit: bandwidth | TargetBitrate: 47 Kbps  | BWE: 68 Kbps  | Res: 320x180 | FPS: 13
```

### Key Findings

1. **Quality Limitation**: `qualityLimitationReason: "bandwidth"` (100% of samples)
   - Chromium's encoder is ALWAYS limited by bandwidth estimation
   - This is not a measurement artifact - the encoder is actually being throttled

2. **BWE Severely Underestimating**:
   - `availableOutgoingBitrate`: Starts at 300 Kbps, drops to 68 Kbps
   - Actual available bandwidth: >10 Mbps (no network limits applied)
   - BWE is underestimating by **100-150x**

3. **Target Bitrate Following BWE**:
   - `targetBitrate`: Tracks BWE closely (237→206→120→84→47 Kbps)
   - Encoder is obeying the BWE directive

4. **Adaptive Quality Degradation**:
   - Resolution: 1280x720 → 480x270 → 320x180 (downscaled to match low bitrate)
   - FPS: 30 → 5 → 30 → 13 (highly variable)
   - This confirms encoder is actively adapting to perceived congestion

5. **Encoder Parameters Correctly Set**:
   ```json
   {
     "encodings": [{
       "active": true,
       "maxBitrate": 10000000,
       "minBitrate": undefined,  // NOTE: minBitrate NOT persisted!
       "networkPriority": "low",
       "priority": "low"
     }]
   }
   ```
   - `maxBitrate` is set correctly (10 Mbps)
   - `minBitrate` is NOT showing up in the parameters (may not be supported)
   - Bitrate constraints are being overridden by BWE

6. **Encoder Implementation**: `encoderImplementation: "unknown"`
   - May indicate software encoder (less efficient)

## Hypothesis Confirmation

**✅ Hypothesis 3: Bandwidth Estimation Override** - CONFIRMED

Chromium's GCC/BWE algorithm is severely underestimating available bandwidth, causing:
- Encoder throttling to <100 Kbps
- Resolution downscaling
- FPS reduction
- Quality limitation

The root cause is NOT:
- ❌ Stats API measurement error (quality degradation is real)
- ❌ Encoder complexity limit (encoder is idle, waiting for BWE approval)
- ❌ Canvas stream limitation (canvas generates full resolution/fps when BWE allows)
- ❌ Bitrate constraints (maxBitrate is set correctly but overridden by BWE)

## Why is BWE Underestimating?

### TWCC Feedback Analysis - PHASE 2 COMPLETE

**✅ CONFIRMED**: aiortc IS sending TWCC feedback to Chromium regularly

From aiortc container logs:
```
INFO:aiortc.rtcrtpreceiver:📡 TWCC: Sending feedback (84 bytes, 45 pkts in report)
INFO:aiortc.rtcrtpreceiver:📡 TWCC: Sending feedback (68 bytes, 34 pkts in report)
INFO:aiortc.rtcrtpreceiver:📡 TWCC: Sending feedback (84 bytes, 46 pkts in report)
...
[Later, after Chromium BWE drops]
INFO:aiortc.rtcrtpreceiver:📡 TWCC: Sending feedback (32 bytes, 4 pkts in report)
INFO:aiortc.rtcrtpreceiver:📡 TWCC: Sending feedback (28 bytes, 3 pkts in report)
```

**Observations**:
1. TWCC feedback IS being sent regularly (~1 second intervals via RTCP loop)
2. Initially reports 40-50 packets per feedback (when Chromium sending at higher rate)
3. After BWE drops, reports only 3-10 packets per feedback (reflects Chromium's reduced rate)
4. This confirms Chromium is actually reducing its sending rate based on BWE

**Conclusion**: The feedback is being sent, but **something in the feedback content** is causing Chromium's GCC to detect congestion where none exists.

### Remaining Hypotheses (In Priority Order)

1. **Delay-Based False Positives** (MOST LIKELY) ⭐:
   - TWCC feedback contains packet arrival time deltas
   - If deltas show increasing inter-arrival time, GCC interprets this as queuing delay → congestion
   - Possible root causes:
     - **Python `time.time()` jitter/precision issues**: System clock resolution causing non-monotonic timestamps
     - **Docker networking delay variance**: Container network stack adding variable delay
     - **aiortc receive path queuing**: Buffering in RTP receiver causing artificial delay
     - **Non-monotonic timestamps in feedback**: Breaks GCC's delay gradient calculation
   - **Test**: Packet capture + timestamp analysis of TWCC feedback

2. **Loss-Based False Positives**:
   - TWCC feedback includes packet status (received vs not received)
   - If aiortc reports gaps (PACKET_NOT_RECEIVED), Chromium interprets as loss → congestion
   - Possible causes:
     - Actual packet loss/reordering in Docker network
     - aiortc TWCC sequence number tracking bug (missing packets that actually arrived)
   - **Test**: Check TWCC feedback for PACKET_NOT_RECEIVED statuses

3. **Initial Probing Failure**:
   - Chromium starts conservatively (~300 Kbps BWE)
   - GCC probing mechanism fails to ramp up
   - Stays stuck at low estimate
   - **Test**: Monitor Chromium's probing behavior in stats

4. **Feedback Timing Issues**:
   - TWCC feedback delayed beyond acceptable threshold (>1 second)
   - Chromium assumes packets experiencing high queuing delay
   - **Test**: Timestamp feedback packets, measure RTT

## Next Steps

### Immediate Actions

1. **Verify aiortc TWCC Feedback**:
   - Check if aiortc is sending RTCP TWCC feedback packets
   - Verify feedback includes all received packets
   - Check feedback timing (should be ~100ms intervals)

2. **Check for Packet Loss**:
   - Analyze aiortc receive stats for packet loss
   - Check if Chromium is seeing NACKs/retransmissions

3. **Packet Capture**:
   - Capture and analyze RTCP packets
   - Verify TWCC feedback format and timing
   - Check for any obvious issues in feedback

### Test Variations

1. **Different Network Setup**:
   - Test outside Docker (remove networking overhead)
   - Test with host networking mode
   - Test on loopback interface

2. **Disable Loss-Based BWE**:
   - Try to force delay-based only estimation
   - Check if loss detection is triggering falsely

3. **Force Higher Initial Estimate**:
   - Try to set higher starting bandwidth
   - Check if BWE can ramp up if starting higher

4. **Compare with Pion Receiver**:
   - Test Chromium → Pion (instead of Chromium → aiortc)
   - See if Pion's TWCC feedback works better

## Technical Details

### Stats Collection Method

Enhanced [peer_twcc.js](../chromium/peer_twcc.js:221-328) with comprehensive logging:
- Outbound RTP: bytesSent, targetBitrate, qualityLimitationReason, resolution, FPS
- Candidate pairs: availableOutgoingBitrate (BWE estimate)
- Transport: total bytes/packets
- Stats dumped every 2 seconds as JSON

### Test Environment

- Docker network: `gcc-chromium-test`
- No bandwidth limiting applied (Phase 1: Baseline)
- Chromium version: Playwright Chromium Headless Shell 141.0.7390.37
- Canvas: 1280x720 @ 30fps, full random noise

## Conclusion

The issue is confirmed to be Chromium's GCC bandwidth estimation severely underestimating available bandwidth (by ~100-150x), causing encoder throttling. The most likely cause is improper TWCC feedback from aiortc, causing Chromium to assume network congestion.

Next investigation phase: Analyze aiortc's TWCC feedback implementation and packet capture to identify the specific issue in the feedback loop.
