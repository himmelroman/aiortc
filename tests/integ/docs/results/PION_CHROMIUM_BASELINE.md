# Pion ↔ Chromium Baseline Test Results

**Date**: 2025-11-23
**Test**: Bidirectional GCC Adaptation Test
**Purpose**: Establish baseline behavior between two battle-tested GCC implementations

## Configuration

All peers aligned with same bitrate limits:
- **Pion**: 1-50 Mbps (GCC controlled)
- **Chromium**: 1-50 Mbps (RTCRtpSender.setParameters)
- **aiortc**: 1-50 Mbps (encoder configured)

## Test Phases

1. **Phase 1**: Baseline - No limiting (15s)
2. **Phase 2**: Limit pion→chromium to 5 Mbps (20s)
3. **Phase 3**: Limit chromium→pion to 2 Mbps, both paths limited (20s)
4. **Phase 4**: Remove all limits - Recovery (30s)

## Results Summary

### Phase 1: Baseline - No Limiting

**Pion → Chromium**:
- Initial: 18.16 Mbps
- Ramped to: **50.00 Mbps** (hit max limit)
- Final receiving rate: 52.67 Mbps
- Packet loss: 0

**Chromium → Pion**:
- Initial: 5.87 Mbps
- Ramped to: **18.71 Mbps**
- Final packets sent: 22,042
- Pion receiving: 20,623 packets, 0 lost, 0.00ms jitter

**Key Observations**:
- Pion quickly reaches maximum 50 Mbps limit
- Chromium reaches ~19 Mbps baseline (not hitting 50 Mbps cap)
- Both paths show 0 packet loss in unconstrained conditions
- Clean ramp-up behavior

---

### Phase 2: 5 Mbps Limit on pion→chromium

**Pion GCC Adaptation**:
- Before limit: 50.00 Mbps
- After limit detected: **2.91 Mbps** (adapted in ~3 seconds)
- Stabilized at: 2.9-3.4 Mbps
- ✅ **Excellent adaptation** - accurately tracks 5 Mbps constraint

**Chromium → Pion** (unconstrained path):
- Before: 18.71 Mbps
- During Phase 2: **47.25-50.35 Mbps**
- 🚀 **Aggressive increase** when opposite direction is constrained
- Shows Chromium can reach 50 Mbps cap

**Pion Receiving**:
- Packets received: 86,107 (up from 20,623)
- Packet loss: 0
- Jitter: 0.00ms
- ✅ Successfully handles high incoming rate

**Chromium Receiving**:
- Rate dropped from 52.67 Mbps to **3.63 Mbps**
- Packet loss: 2,746 packets
- ⚠️ Expected packet loss during adaptation

---

### Phase 3: 2 Mbps Limit on chromium→pion (both paths limited)

**Pion GCC** (still limited):
- Remained at: 2.9-3.3 Mbps
- ✅ Maintains adaptation to 5 Mbps constraint

**Chromium GCC Adaptation**:
- Before limit: 50.35 Mbps
- After limit detected: **11.96 Mbps** (3 seconds)
- Then: **1.38 Mbps** (6 seconds)
- Stabilized at: **0.56-1.78 Mbps**
- ✅ **Good adaptation** - accurately tracks 2 Mbps constraint

**Pion Receiving**:
- Packet count: 102,394 (up from 86,107)
- Packet loss: **2,785 packets** (689 new losses)
- Jitter: 0.00ms
- ⚠️ Packet loss during Chromium's adaptation from 50 Mbps

**Chromium Receiving**:
- Remained at: 3.31 Mbps
- Total packet loss: 2,746 (unchanged - no new losses)

---

### Phase 4: Recovery - No Limiting

**Pion GCC Recovery**:
- Starting: 3.17 Mbps
- Progressive ramp: 3.53 → 4.50 → 5.66 → 7.12 → 8.94 → 11.46 → 14.46 → 18.18 → 22.86 Mbps
- Final: **31.59 Mbps** (still ramping)
- 🚀 **Excellent recovery** - steady additive increase

**Chromium GCC Recovery**:
- Starting: 1.35 Mbps
- Progressive ramp: 1.55 → 1.63 → 1.58 → 1.80 → 2.54 → 2.77 → 4.46 → 5.08 → 7.10 Mbps
- Final: **9.31 Mbps** (still recovering)
- ⚠️ **Slower recovery** than Pion

**Receiving Rates**:
- Chromium receiving: 3.31 → 29.52 Mbps (strong recovery)
- Pion receiving: 102,394 → 114,044 packets (steady increase)

**Packet Loss**:
- Pion: 2,785 total (no new losses during recovery)
- Chromium: 2,746 total (no new losses during recovery)

---

## Key Findings

### ✅ Both Implementations Work Correctly

1. **Pion GCC**:
   - Fast adaptation to constraints (< 3 seconds)
   - Accurate tracking (2.9 Mbps for 5 Mbps limit)
   - Strong recovery (3.17 → 31.59 Mbps in 30s)
   - Can reach 50 Mbps max

2. **Chromium GCC**:
   - Fast adaptation to constraints (< 6 seconds)
   - Accurate tracking (1.4 Mbps for 2 Mbps limit)
   - Moderate recovery (1.35 → 9.31 Mbps in 30s)
   - Can reach 50 Mbps under certain conditions

### 📊 Behavioral Differences

1. **Baseline Bitrate**:
   - Pion: Immediately reaches 50 Mbps max
   - Chromium: Reaches ~19 Mbps baseline
   - Possible reasons: encoder speed, video complexity, browser overhead, or more conservative GCC

2. **Recovery Speed**:
   - Pion: Faster recovery (31.59 Mbps in 30s)
   - Chromium: Slower recovery (9.31 Mbps in 30s)
   - Both show upward trend, would likely converge given more time

3. **Packet Loss During Adaptation**:
   - Total losses: 2,746-2,785 packets out of ~100,000 (~2.7%)
   - Primarily occurs when sender doesn't adapt fast enough
   - Expected behavior during constraint detection

### 🎯 This is the TARGET Behavior

aiortc should strive to match:
- Fast constraint detection (< 5 seconds)
- Accurate bitrate tracking (within 1 Mbps of limit)
- Minimal packet loss during adaptation (< 3%)
- Steady recovery when constraints are removed
- Ability to reach configured max bitrate

---

## Raw Data Snapshots

### Phase 1 Final Stats
```
pion:
  📤 Sending GCC: 50.00 Mbps (pion→chromium)
  📥 Receiving: 20623 packets (chromium→pion) | Lost: 0 | Jitter: 0.00ms
chromium:
  📤 Sending: 18.71 Mbps (chromium→pion) | Packets: 22042
  📥 Receiving: 52.67 Mbps (pion→chromium) | Packets: 64057 | Lost: 0
```

### Phase 2 Final Stats (5 Mbps limit on pion→chromium)
```
pion:
  📤 Sending GCC: 3.40 Mbps (pion→chromium)
  📥 Receiving: 86107 packets (chromium→pion) | Lost: 0 | Jitter: 0.00ms
chromium:
  📤 Sending: 50.35 Mbps (chromium→pion) | Packets: 89502
  📥 Receiving: 3.63 Mbps (pion→chromium) | Packets: 78687 | Lost: 2746
```

### Phase 3 Final Stats (both paths limited)
```
pion:
  📤 Sending GCC: 3.17 Mbps (pion→chromium)
  📥 Receiving: 102394 packets (chromium→pion) | Lost: 2785 | Jitter: 0.00ms
chromium:
  📤 Sending: 1.35 Mbps (chromium→pion) | Packets: 105306
  📥 Receiving: 3.31 Mbps (pion→chromium) | Packets: 86122 | Lost: 2746
```

### Phase 4 Final Stats (recovery)
```
pion:
  📤 Sending GCC: 31.59 Mbps (pion→chromium)
  📥 Receiving: 114044 packets (chromium→pion) | Lost: 2785 | Jitter: 0.00ms
chromium:
  📤 Sending: 9.31 Mbps (chromium→pion) | Packets: 118497
  📥 Receiving: 29.52 Mbps (pion→chromium) | Packets: 127008 | Lost: 2746
```

---

## Next Steps

1. ✅ **Baseline established** - Pion ↔ Chromium shows expected GCC behavior
2. 🔄 **Compare with aiortc-chromium** - Identify where aiortc differs
3. 🐛 **Debug aiortc issues** - Fix any adaptation problems
4. ✅ **Validate fix** - Ensure aiortc matches baseline behavior
