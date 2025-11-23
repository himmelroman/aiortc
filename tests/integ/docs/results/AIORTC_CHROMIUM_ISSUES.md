# aiortc ↔ Chromium vs Pion ↔ Chromium Comparison

**Date**: 2025-11-23
**Status**: 🔴 **CRITICAL ISSUES IDENTIFIED**

## Executive Summary

The aiortc-Chromium test reveals **two critical problems**:

1. ❌ **Chromium sends only 0.03 Mbps to aiortc** (vs 18-50 Mbps to Pion)
2. ❌ **aiortc GCC stuck at 1 Mbps** after adaptation (doesn't recover)

---

## Side-by-Side Comparison

### Phase 1: Baseline (No Limiting)

| Metric | Pion ↔ Chromium ✅ | aiortc ↔ Chromium ❌ | Delta |
|--------|-------------------|---------------------|-------|
| **Pion/aiortc → Chromium** | 50.00 Mbps | 10.00 Mbps | -80% |
| **Chromium → Pion/aiortc** | **18.71 Mbps** | **0.03 Mbps** | **-99.8%** 🔴 |
| **Pion/aiortc receiving** | 20,623 packets | 251 packets | -98.8% 🔴 |
| **Chromium receiving** | 52.67 Mbps | 10.17 Mbps | -80.7% |
| **Packet loss** | 0 | 0 | OK |

**🔴 CRITICAL**: Chromium sends 624x LESS to aiortc than to Pion!

---

### Phase 2: 5 Mbps Limit on Sender→Chromium

| Metric | Pion ↔ Chromium ✅ | aiortc ↔ Chromium ❌ | Delta |
|--------|-------------------|---------------------|-------|
| **Pion/aiortc GCC** | 2.91-3.40 Mbps | **1.00 Mbps** | **-71%** 🔴 |
| **Chromium → Pion/aiortc** | 47.25-50.35 Mbps | **0.03 Mbps** | **-99.9%** 🔴 |
| **Chromium receiving** | 3.63 Mbps | 4.87 Mbps | +34% |
| **Packet loss** | 2,746 | **10,308** | **+275%** 🔴 |

**🔴 CRITICAL Issues**:
1. aiortc over-adapts (1 Mbps vs expected ~3 Mbps for 5 Mbps limit)
2. Chromium still stuck at 0.03 Mbps
3. Massive packet loss (10,308 vs 2,746)

---

### Phase 3: 2 Mbps Limit on Chromium→Sender (both limited)

| Metric | Pion ↔ Chromium ✅ | aiortc ↔ Chromium ❌ | Delta |
|--------|-------------------|---------------------|-------|
| **Pion/aiortc GCC** | 2.88-3.17 Mbps | **1.00 Mbps** | -68% 🔴 |
| **Chromium sending** | 11.96 → 1.38 Mbps | **0.03 Mbps** | Already stuck 🔴 |
| **Pion/aiortc receiving** | 102,394 packets, 2,785 lost | 509 packets | -99.5% 🔴 |
| **Packet loss (Chromium)** | 2,746 | **19,939** | **+626%** 🔴 |

**🔴 CRITICAL**:
- Chromium can't adapt down because it's already at 0.03 Mbps
- aiortc receives almost no packets (509 vs 102,394)
- Chromium loses 19,939 packets receiving from aiortc

---

### Phase 4: Recovery (No Limiting)

| Metric | Pion ↔ Chromium ✅ | aiortc ↔ Chromium ❌ | Delta |
|--------|-------------------|---------------------|-------|
| **Pion/aiortc GCC recovery** | 3.17 → **31.59 Mbps** ✅ | **1.00 Mbps** (stuck) 🔴 | **-97%** |
| **Chromium recovery** | 1.35 → **9.31 Mbps** ✅ | **0.03 Mbps** (stuck) 🔴 | **-99.7%** |
| **Chromium receiving** | 29.52 Mbps | 6.13-6.31 Mbps | -79% |
| **Final packet loss** | 2,746 | **20,175** | **+634%** |

**🔴 CRITICAL**: Neither aiortc nor Chromium recover from constraints!

---

## Root Cause Analysis

### Issue 1: Chromium → aiortc = 0.03 Mbps 🔴

**Symptoms**:
- Chromium sends 18-50 Mbps to Pion
- Chromium sends only 0.03 Mbps to aiortc
- aiortc receives only ~250-750 packets total vs 20,000-114,000 for Pion

**Possible Causes**:
1. ❓ aiortc not sending TWCC feedback reports back to Chromium
2. ❓ aiortc's TWCC receiver implementation broken
3. ❓ Chromium's GCC sees congestion signals from aiortc and throttles
4. ❓ Network/ICE issues specific to aiortc

**Investigation Steps**:
- [ ] Check aiortc logs for TWCC report sending
- [ ] Inspect Chromium's detailed stats (STATS_DUMP) for qualityLimitationReason
- [ ] Compare TWCC header extensions in SDP (aiortc vs Pion)
- [ ] Check if aiortc is processing incoming TWCC packets correctly

---

### Issue 2: aiortc GCC Stuck at 1 Mbps 🔴

**Symptoms**:
- aiortc GCC drops from 10 → 1 Mbps in Phase 2
- Never recovers, stays at 1 Mbps through Phases 3 & 4
- Expected: Should recover to ~30 Mbps like Pion does

**Possible Causes**:
1. ❓ GCC estimator stuck in some state
2. ❓ Not receiving TWCC reports from Chromium (because Chromium is sending so little)
3. ❓ Loss-based adaptation overriding TWCC-based adaptation
4. ❓ Bug in aiortc's GCC implementation

**Investigation Steps**:
- [ ] Check aiortc GCC estimator logs
- [ ] Verify TWCC reports are being received from Chromium
- [ ] Compare GCC implementation with Pion's
- [ ] Check if there's a minimum bitrate cap being enforced

---

### Issue 3: Massive Packet Loss (20,175 packets) 🔴

**Symptoms**:
- Pion-Chromium: 2,746 packets lost (2.7%)
- aiortc-Chromium: 20,175 packets lost (31.6%)
- Most losses occur in Phase 2 (10,308 packets)

**Possible Causes**:
1. ❓ aiortc over-sending before adapting
2. ❓ GCC adaptation too slow or inaccurate
3. ❓ Chromium receiving buffer issues specific to aiortc
4. ❓ Network congestion due to poor GCC behavior

**Investigation Steps**:
- [ ] Compare actual sending rates with GCC estimates
- [ ] Check adaptation latency (time from constraint to bitrate drop)
- [ ] Analyze packet timing/pacing in aiortc

---

## Investigation Priority

### 🔥 Priority 1: Why does Chromium send only 0.03 Mbps to aiortc?

This is the ORIGINAL PROBLEM reported. Must investigate:

1. **Check aiortc TWCC feedback**:
   - Is aiortc generating and sending TWCC reports?
   - Are the reports formatted correctly?
   - Frequency of reports?

2. **Check Chromium's perspective**:
   - Look at STATS_DUMP logs for detailed encoder stats
   - Check `qualityLimitationReason` field
   - Check `targetBitrate` and `encodedBitrate`
   - Look for bandwidth estimation values

3. **Compare SDP**:
   - Verify TWCC extension is negotiated correctly
   - Check for any differences in SDP between Pion and aiortc

### 🔥 Priority 2: Why doesn't aiortc GCC recover?

1. **Check GCC state machine**:
   - Is it stuck in "decrease" mode?
   - Are TWCC reports being received?
   - Check internal state variables

2. **Compare with Pion**:
   - How does Pion's GCC handle recovery?
   - Any differences in the estimator algorithms?

---

## Next Steps

1. ✅ Run detailed logging version of Chromium peer to capture STATS_DUMP
2. ✅ Add debug logging to aiortc TWCC sender/receiver
3. ✅ Compare TWCC packet flow between Pion-Chromium and aiortc-Chromium
4. ✅ Inspect GCC estimator state in aiortc
5. ✅ Compare SDP negotiation between tests

---

## Raw Test Data

### aiortc-Chromium Phase 1 (Baseline)
```
aiortc:
  📤 Sending GCC: 10.00 Mbps
  📥 Receiving: 0.56 Mbps | Packets: 251
chromium:
  📤 Sending: 0.03 Mbps | Packets: 365
  📥 Receiving: 10.17 Mbps | Packets: 21379 | Lost: 0
```

### aiortc-Chromium Phase 2 (5 Mbps limit)
```
aiortc:
  📤 Sending GCC: 1.00 Mbps
  📥 Receiving: 0.56 Mbps | Packets: 380
chromium:
  📤 Sending: 0.03 Mbps | Packets: 493
  📥 Receiving: 4.87 Mbps | Packets: 31466 | Lost: 10308
```

### aiortc-Chromium Phase 4 (Recovery)
```
aiortc:
  📤 Sending GCC: 1.00 Mbps (STUCK!)
  📥 Receiving: 0.56 Mbps | Packets: 742
chromium:
  📤 Sending: 0.03 Mbps (STUCK!) | Packets: 868
  📥 Receiving: 6.13 Mbps | Packets: 63826 | Lost: 20175
```
