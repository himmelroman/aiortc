# BWE Testing Framework

Comprehensive testing infrastructure for validating Bandwidth Estimation (BWE) algorithms across different WebRTC peer combinations using loss-based network emulation.

## Table of Contents

- [Architecture](#architecture)
- [Test Phases](#test-phases)
- [Peer Types](#peer-types)
- [Network Emulation](#network-emulation)
- [Running Tests](#running-tests)
- [Reading Results](#reading-results)
- [Data Presentation](#data-presentation)
- [Extending the Framework](#extending-the-framework)

---

## Architecture

The testing framework consists of three main components:

1. **`bwe_test_framework.sh`** - Central parameterized test engine
   - Generates test videos (for chromium/pion peers) using `generate_test_video.sh`
   - Builds Docker images for each peer type
   - Orchestrates test execution across 11 phases
   - Applies network limits using Linux `tc` (traffic control)
   - Collects and aggregates BWE metrics from container logs

2. **`test_*.sh`** - Wrapper scripts for specific peer combinations
   - Small convenience scripts that set environment variables
   - Example: `PEER_A_TYPE=aiortc-bwe PEER_B_TYPE=chromium`

3. **Peer implementations** (in subdirectories)
   - `aiortc-twcc/` - Python WebRTC with TWCC-based GCC
   - `aiortc-bwe/` - Python WebRTC with Loss-based BWE
   - `pion/` - Go WebRTC with loss-based BWE
   - `chromium/` - Browser-based WebRTC (Playwright + Chromium)

### Framework Flow

```
┌──────────────────────────────────────────────────────────────┐
│  1. Build Docker Images                                       │
│     - Build peer A image (e.g., aiortc-bwe-peer)             │
│     - Build peer B image (e.g., chromium-twcc-peer)          │
└──────────────────────────────────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────┐
│  2. Start Peers in Isolated Network                          │
│     - Create Docker network: gcc-network-<test_id>           │
│     - Start Peer A (server) on port 8080                     │
│     - Start Peer B (client) connecting to Peer A             │
└──────────────────────────────────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────┐
│  3. Run Test Phases                                          │
│     - Wait 15s for WebRTC connection establishment          │
│     - Execute 11 phases (or 1 in QUICK_MODE)                │
│     - Apply network limits via tc qdisc                      │
│     - Collect metrics every 5 seconds                        │
└──────────────────────────────────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────┐
│  4. Aggregate and Display Results                            │
│     - Extract final BWE values from logs                     │
│     - Display summary with last 3 measurements               │
│     - Save full logs to timestamped directory                │
└──────────────────────────────────────────────────────────────┘
```

---

## Test Phases

The framework uses 11 distinct phases to test BWE behavior across different loss conditions. Each phase tests a specific loss region to validate the AIMD (Additive Increase, Multiplicative Decrease) algorithm.

### Quick Mode

```bash
QUICK_MODE=true PEER_A_TYPE=aiortc-bwe PEER_B_TYPE=chromium ./bwe_test_framework.sh
```

- **Phase 1 only**: baseline (10s, unlimited, 0% loss)
- **Purpose**: Fast verification that peers can connect and exchange BWE

### Full Mode (Default)

Total duration: ~3.8 minutes (230 seconds)

#### Phase Definitions

| Phase | Name | Duration | Peer A Limit | Peer B Limit | Loss % | Direction Tested | Purpose |
|-------|------|----------|--------------|--------------|--------|------------------|---------|
| 1 | baseline | 20s | unlimited | unlimited | 0% | Both | Establish baseline BWE with no congestion |
| 2 | b_degrade_8 | 15s | unlimited | 8 Mbps | 5% | Peer B → Peer A | Test hold-steady (2% < loss < 10%) |
| 3 | b_degrade_4 | 15s | unlimited | 4 Mbps | 12% | Peer B → Peer A | Test decrease (loss > 10%) |
| 4 | b_severe | 15s | unlimited | 2 Mbps | 15% | Peer B → Peer A | Test aggressive decrease |
| 5 | b_hold_low | 20s | unlimited | 8 Mbps | 5% | Peer B → Peer A | Verify BWE holds low, doesn't recover |
| 6 | b_recovery | 40s | unlimited | unlimited | 0% | Peer B → Peer A | Test additive increase from low BWE |
| 7 | a_degrade_8 | 15s | 8 Mbps | unlimited | 5% | Peer A → Peer B | Test hold-steady (2% < loss < 10%) |
| 8 | a_degrade_4 | 15s | 4 Mbps | unlimited | 12% | Peer A → Peer B | Test decrease (loss > 10%) |
| 9 | a_severe | 15s | 2 Mbps | unlimited | 15% | Peer A → Peer B | Test aggressive decrease |
| 10 | a_hold_low | 20s | 8 Mbps | unlimited | 5% | Peer A → Peer B | Verify BWE holds low, doesn't recover |
| 11 | a_recovery | 40s | unlimited | unlimited | 0% | Peer A → Peer B | Test additive increase from low BWE |

### Phase Groups

**Phases 2-6 (b_* phases)**: Test **Peer B's BWE** by limiting Peer B's egress
- Apply loss to traffic from Peer B → Peer A
- Measure Peer B's GCC/BWE estimate
- Peer A's BWE is unrestricted (expected to increase to max)

**Phases 7-11 (a_* phases)**: Test **Peer A's BWE** by limiting Peer A's egress
- Apply loss to traffic from Peer A → Peer B
- Measure Peer A's GCC/BWE estimate
- Peer B's BWE is unrestricted (expected to increase to max)

### Loss-Based BWE Thresholds

The framework tests three loss regions based on standard AIMD behavior:

| Loss Rate | Expected BWE Behavior | Tested In Phases |
|-----------|----------------------|------------------|
| < 2% | **Additive Increase** - BWE increases linearly | 1, 6, 11 (recovery) |
| 2-10% | **Hold Steady** - BWE maintains current rate | 2, 5, 7, 10 (hold) |
| > 10% | **Multiplicative Decrease** - BWE reduces aggressively | 3, 4, 8, 9 (decrease) |

### Phase Format

Each phase is defined as: `name:duration:peer_a_limit:peer_b_limit`

Example: `"b_degrade_8:15:unlimited:8"`
- **name**: `b_degrade_8` (descriptive phase name)
- **duration**: `15` seconds
- **peer_a_limit**: `unlimited` (no limit on Peer A egress)
- **peer_b_limit**: `8` Mbps (limit Peer B egress → 5% loss)

---

## Peer Types

The framework supports 4 peer types:

### 1. `aiortc-twcc` - TWCC-based GCC
- **Implementation**: Python WebRTC (aiortc library)
- **BWE Algorithm**: GCC (Google Congestion Control) using TWCC
- **Docker Image**: `aiortc-twcc-peer`
- **Build Context**: `../../` (aiortc repo root)
- **Dockerfile**: `aiortc-twcc/Dockerfile`
- **Peer Script**: `peer_twcc.py`
- **Log Pattern**: `📤 Sending GCC:`

### 2. `aiortc-bwe` - Loss-based BWE
- **Implementation**: Python WebRTC (aiortc library)
- **BWE Algorithm**: Loss-based AIMD using RTCP Receiver Reports
- **Docker Image**: `aiortc-bwe-peer`
- **Build Context**: `../../` (aiortc repo root)
- **Dockerfile**: `aiortc-bwe/Dockerfile`
- **Peer Script**: `peer_bwe.py`
- **Log Pattern**: `📤 Sending Loss BWE:`

### 3. `pion` - Pion Loss-Based BWE
- **Implementation**: Go WebRTC (pion library)
- **BWE Algorithm**: Loss-based BWE (RFC-compliant AIMD)
- **Docker Image**: `pion-twcc-peer`
- **Build Context**: `pion/`
- **Dockerfile**: `pion/Dockerfile`
- **Log Pattern**: `📤 Loss BWE:`

### 4. `chromium` - Native Browser GCC
- **Implementation**: Chromium browser (via Playwright)
- **BWE Algorithm**: Native Chromium GCC implementation
- **Docker Image**: `chromium-twcc-peer`
- **Build Context**: `chromium/`
- **Dockerfile**: `chromium/Dockerfile`
- **Peer Script**: `peer.js` (client mode only - always creates offer)
- **Log Pattern**: `📤 Sending GCC:`
- **Note**: Chromium peer always operates as client (offerer), not suitable as server

### Peer Roles

- **Peer A** (Server): Starts first, runs HTTP signaling server on port 8080
- **Peer B** (Client): Connects to Peer A at `http://gcc-peer-a:8080`
- **Important**: Chromium should typically be used as **Peer B** (client) only

---

## Loss-Based BWE Algorithm Differences

While both `aiortc-bwe` and `pion` implement RFC-compliant loss-based BWE with **identical thresholds**, they use different algorithmic approaches that affect behavior.

### Common Thresholds (RFC-Compliant)

Both implementations use:
- **`increaseLossThreshold = 0.02`** (2%)
- **`decreaseLossThreshold = 0.10`** (10%)
- **`increaseFactor = 1.05`** (5% multiplicative increase)
- **Decrease formula**: `1 - (0.5 * loss)`

### Algorithm Differences

#### aiortc-bwe Implementation

**Source**: `tests/integ/aiortc-bwe/loss_based_bwe.py`

```python
if current_loss < 0.02:
    # INCREASE: Multiplicative by 1.05 (5%)
    bitrate *= 1.05
elif current_loss > 0.10:
    # DECREASE: Factor of (1 - 0.5 * loss)
    bitrate *= (1 - 0.5 * current_loss)
else:
    # HOLD STEADY: Do nothing (2% ≤ loss ≤ 10%)
    pass
```

**Characteristics**:
- ✅ Direct response to current loss measurement
- ✅ Fast recovery after congestion resolves
- ⚠️ May oscillate with transient loss spikes

#### pion Implementation

**Source**: `pion/local_pion_gcc/pkg/gcc/loss_based_bwe.go:93-109`

```go
increaseLoss := max(averageLoss, currentLoss)
decreaseLoss := min(averageLoss, currentLoss)

if increaseLoss < 0.02 {
    // INCREASE: Both avg AND current must be < 2%
    bitrate *= 1.05
} else if decreaseLoss > 0.10 {
    // DECREASE: Both avg AND current must be > 10%
    bitrate *= (1 - 0.5 * decreaseLoss)
} else {
    // HOLD STEADY
}
```

**Characteristics**:
- ✅ More stable with transient loss spikes (exponential moving average smooths)
- ✅ Prevents premature decreases when loss is temporarily high
- ⚠️ Slower recovery after sustained congestion (average lags behind)
- ⚠️ May continue in hold-steady mode longer after phase transitions

### Behavioral Comparison

| Condition | aiortc-bwe | pion |
|-----------|-------------|------|
| 0% loss (stable) | ✅ Increase 5%/200ms | ✅ Increase 5%/200ms |
| <2% loss (stable) | ✅ Increase | ✅ Increase |
| 2-10% loss (stable) | ✅ Hold steady | ✅ Hold steady |
| >10% loss (stable) | ✅ Decrease | ✅ Decrease |
| **Transient loss spike** (e.g., 15% for 1s) | ⚠️ Decreases immediately | ✅ **May hold if avg still low** |
| **Recovery after congestion** | ✅ **Increases immediately** | ⚠️ Slower (avg still elevated) |
| Phase transition lag | Minimal | May carry over elevated avg |

### Important Testing Notes

#### Phase 2 "Overshoot" is CORRECT Behavior

When testing with 8 Mbps limit (5% expected loss), you may observe:
- Pion increases from 7.58 → 11.76 Mbps despite the limit
- Loss remains 0% initially due to buffering
- Once buffers fill, loss appears (~4.7%) and BWE holds steady

**This is CORRECT**: Loss-based BWE is **reactive**, responding to *observed loss* not bandwidth limits.

#### Phase 5 Continued Decrease

After Phase 4 (15% loss), Phase 5 may show:
- Expected: Hold steady at 5% loss
- Observed: Continued decrease from 4.28 → 1.86 Mbps

**Explanation**: Pion's exponential moving average still tracks elevated loss from Phase 4:
- `max(avgLoss=12%, currentLoss=5%) = 12%`
- Since 12% > 10%, decrease continues
- Average gradually decays toward current 5% loss

### Expected Behavior for pion Tests

When testing pion, expect:

**Phases 2, 5, 7, 10** (5% loss - hold-steady):
- ✅ May show initial overshoot before loss appears
- ✅ Holds steady once loss stabilizes at 5%
- ⚠️ After high-loss phases, may continue decreasing until avg drops below 10%

**Phases 3, 4, 8, 9** (>10% loss - decrease):
- ✅ Decreases aggressively as expected
- ✅ Response time may be slightly delayed vs aiortc-bwe

**Phases 1, 6, 11** (0% loss - increase):
- ✅ Increases as expected
- ⚠️ Recovery after high loss may be slower than aiortc-bwe

---

## Video Generation

The framework automatically generates test videos for peers that require them (chromium and pion) before building Docker images.

### `generate_test_video.sh`

Generic parameterized video generation script that creates VP8-encoded test videos.

**Usage**:
```bash
./generate_test_video.sh -o OUTPUT_FILE [-d DURATION] [-b BITRATE] [-f FORMAT] [-e EFFECT]
```

**Parameters**:
- `-o OUTPUT_FILE` - Required: Output file path
- `-d DURATION` - Optional: Duration in seconds (default: 60)
- `-b BITRATE` - Optional: Target bitrate (default: 5M)
- `-f FORMAT` - Optional: Output format `webm` or `ivf` (default: webm)
- `-e EFFECT` - Optional: Visual effect `noise` or `blend` (default: noise)

**Examples**:
```bash
# Chromium test video (60s WebM with noise)
./generate_test_video.sh -o chromium/test_video.webm -d 60 -b 5M -f webm -e noise

# Pion test video (5s IVF with blend effect)
./generate_test_video.sh -o pion/test_video.ivf -d 5 -b 3M -f ivf -e blend
```

**Automatic Generation**:

The `bwe_test_framework.sh` automatically calls `generate_test_video.sh` before building Docker images:
- If chromium peer is used → generates `chromium/test_video.webm`
- If pion peer is used → generates `pion/test_video.ivf`
- If file already exists → skips generation (efficient for repeated tests)
- Uses local `ffmpeg` (not Docker-embedded)

**Video Specifications**:

| Peer | Format | Duration | Bitrate | Effect | Resolution |
|------|--------|----------|---------|--------|------------|
| Chromium | WebM | 60s | 5 Mbps | Noise overlay | 1280x720 @ 30fps |
| Pion | IVF | 5s | 3 Mbps | Blend with random | 1280x720 @ 30fps |

All videos use **VP8 codec** (`libvpx`) for consistency.

---

## Network Emulation

### Loss-Only Approach

The framework uses Linux `tc` (traffic control) with **netem** to apply packet loss:

```bash
docker exec <container> tc qdisc add dev eth0 root netem loss <percentage>
```

**Key Design Decision**: Loss is applied directly WITHOUT rate limiting.

### Why Loss-Only?

Previous attempts used TBF (Token Bucket Filter) with rate limiting:
```bash
tc qdisc add dev eth0 root tbf rate 8mbit burst 32kbit latency 50ms
```

**Problem**: TBF queues packets rather than dropping them, creating:
- Bufferbloat (up to 50ms latency)
- Minimal packet loss (< 1% even with 2% netem loss)
- BWE couldn't detect congestion

**Solution**: Direct loss application without rate limiting:
- Applies loss to ALL packets regardless of sender rate
- No queuing or bufferbloat
- BWE detects congestion immediately via RTCP feedback

### Loss Mapping

The `set_rate_limit()` function maps "bandwidth limits" to loss percentages:

| Bandwidth Value | Loss Percentage | Test Purpose |
|----------------|-----------------|--------------|
| 8 Mbps | 5% | Hold-steady region (2-10%) |
| 4 Mbps | 12% | Decrease region (>10%) |
| 2 Mbps | 15% | Aggressive decrease |
| unlimited | 0% | Baseline / Recovery |

**Note**: The "Mbps" values are semantic labels for test phases, not actual rate limits.

### Experimental Functions

The framework includes three unused experimental approaches (for reference):

1. **`set_rate_limit_netem()`** - Netem with rate + 15% loss
2. **`set_rate_limit_tbf_minimal()`** - TBF with minimal queue + 10% loss
3. **`set_rate_limit_htb()`** - HTB + netem with 15% loss

These are NOT used in production tests but preserved for experimentation.

---

## Running Tests

### Prerequisites

- Docker with buildx support
- Linux kernel with `tc` support (for network emulation)
- `NET_ADMIN` capability for containers (automatic in framework)
- ~500MB disk space per Docker image

### Basic Usage

```bash
# Run specific peer combination
PEER_A_TYPE=aiortc-bwe PEER_B_TYPE=chromium ./bwe_test_framework.sh

# Quick mode (single baseline phase, ~25 seconds)
QUICK_MODE=true PEER_A_TYPE=aiortc-bwe PEER_B_TYPE=pion ./bwe_test_framework.sh

# Full mode (all 11 phases, ~4 minutes)
PEER_A_TYPE=aiortc-bwe PEER_B_TYPE=chromium ./bwe_test_framework.sh
```

### Supported Combinations

Any combination of the 4 peer types works:

```bash
# aiortc-bwe vs chromium
PEER_A_TYPE=aiortc-bwe PEER_B_TYPE=chromium ./bwe_test_framework.sh

# aiortc-bwe vs pion
PEER_A_TYPE=aiortc-bwe PEER_B_TYPE=pion ./bwe_test_framework.sh

# pion vs chromium (recommended - chromium as client)
PEER_A_TYPE=pion PEER_B_TYPE=chromium ./bwe_test_framework.sh
```

**Exceptions**:
- Chromium-Chromium tests are not supported (headless browser limitation)
- Chromium as Peer A (server) is not recommended - chromium peer only operates in client mode

### Convenience Wrappers

Create wrapper scripts for common combinations:

```bash
#!/bin/bash
# test_aiortc_chromium.sh
export PEER_A_TYPE=aiortc-bwe
export PEER_B_TYPE=chromium
exec "$(dirname "$0")/bwe_test_framework.sh"
```

Then simply run: `./test_aiortc_chromium.sh`

---

## Reading Results

### Live Output During Test

```
===================================================================
   GCC TEST FRAMEWORK - aiortc-bwe ↔ chromium
===================================================================

Test Configuration:
  - Peer A: aiortc-bwe (server)
  - Peer B: chromium (client)
  - Phases: 11

📁 Logs will be saved to: logs/20251128_202855_aiortc-bwe_chromium
===================================================================

Building Docker images...
  - Building aiortc-bwe peer...
  - Building chromium peer...
✅ Images built

Starting peer A (aiortc-bwe) as server...
Starting peer B (chromium) as client...
✅ Both peers started

Waiting for WebRTC connection and initial stabilization...

============================================================
[Phase 1: baseline (20s)]
============================================================
  aiortc-bwe: GCC=1.10 Mbps RX=N/A | chromium: GCC=0.31 Mbps RX=8.40 Mbps
  aiortc-bwe: GCC=1.90 Mbps RX=N/A | chromium: GCC=0.49 Mbps RX=7.14 Mbps
  aiortc-bwe: GCC=2.90 Mbps RX=N/A | chromium: GCC=0.66 Mbps RX=7.62 Mbps
  aiortc-bwe: GCC=3.90 Mbps RX=N/A | chromium: GCC=0.98 Mbps RX=7.43 Mbps

============================================================
[Phase 2: b_degrade_8 (15s)]
============================================================
  aiortc-bwe: GCC=4.50 Mbps RX=N/A | chromium: GCC=2.13 Mbps RX=7.60 Mbps
  aiortc-bwe: GCC=5.70 Mbps RX=N/A | chromium: GCC=1.20 Mbps RX=7.41 Mbps
  aiortc-bwe: GCC=7.10 Mbps RX=N/A | chromium: GCC=0.41 Mbps RX=7.52 Mbps
...
```

### Metrics Explained

Each line shows:
```
peer_type: GCC=<estimate> Mbps RX=<received> Mbps
```

- **GCC/Loss BWE**: What the peer thinks it can send (bandwidth estimate)
- **RX**: What the peer is actually receiving (incoming bitrate)
- **N/A**: RX metrics not collected for this peer type in this direction

### Test Summary

```
============================================================
   TEST SUMMARY
============================================================

Test Configuration:
  - Peer A: aiortc-bwe
  - Peer B: chromium
  - Total Phases: 11

Final GCC Values:
  Peer A (aiortc-bwe):
    INFO:__main__:📤 Sending Loss BWE: 47.96 Mbps (loss=0.000) (aiortc→peer)
    INFO:__main__:📤 Sending Loss BWE: 50.00 Mbps (loss=0.000) (aiortc→peer)
    INFO:__main__:📤 Sending Loss BWE: 50.00 Mbps (loss=0.000) (aiortc→peer)
  Peer B (chromium):
    [Browser] 📤 Sending GCC: 7.85 Mbps (chromium-B→remote)
    [Browser] 📤 Sending GCC: 8.84 Mbps (chromium-B→remote)
    [Browser] 📤 Sending GCC: 9.92 Mbps (chromium-B→remote)

✅ Test complete!

============================================================
📁 Full logs saved to:
  - Peer A (aiortc-bwe): logs/.../peer_a_aiortc-bwe.log
  - Peer B (chromium): logs/.../peer_b_chromium.log
============================================================
```

### Log Files

Each test creates a timestamped directory:

```
logs/
└── 20251128_202855_aiortc-bwe_chromium/
    ├── peer_a_aiortc-bwe.log     # Full Peer A output
    ├── peer_b_chromium.log        # Full Peer B output
    └── test_summary.log           # Not currently generated
```

---

## Data Presentation

### Comprehensive Results Table

When analyzing test results, present data in this format:

| Phase | Duration | TC Limit | Loss | Direction | Peer A BWE | Peer B BWE | Peer B RX | Expected Behavior | Result |
|-------|----------|----------|------|-----------|------------|------------|-----------|-------------------|--------|
| 1: baseline | 20s | unlimited | 0% | Both | 1.10 → 3.90 Mbps | 0.31 → 0.98 Mbps | 7.14-8.40 Mbps | Both increase (loss < 2%) | ✅ |
| 2: b_degrade_8 | 15s | B: 8M | 5% | B→A | 4.50 → 7.10 Mbps | **2.13 → 0.41 Mbps** | 7.41-7.60 Mbps | B holds steady (5% in hold region) | ✅ |
| 3: b_degrade_4 | 15s | B: 4M | 12% | B→A | 9.87 → 16.88 Mbps | **1.60 → 1.74 Mbps** | 5.92-7.44 Mbps | B decreases (loss > 10%) | ✅ |
| 4: b_severe | 15s | B: 2M | 15% | B→A | 22.62 → 36.84 Mbps | **1.45 → 0.78 Mbps** | 7.27-7.37 Mbps | B aggressive decrease (loss > 10%) | ✅ |
| 5: b_hold_low | 20s | B: 8M | 5% | B→A | 49.37 → 50.00 Mbps | **0.45 → 0.36 Mbps** | 7.26-7.34 Mbps | B holds low (5% in hold region) | ✅ |
| 6: b_recovery | 40s | unlimited | 0% | B→A | 50.00 Mbps | **0.43 → 2.66 Mbps** | 7.13-8.06 Mbps | B increases (loss < 2%) | ✅ |
| 7: a_degrade_8 | 15s | A: 8M | 5% | A→B | **50.00 Mbps** | 2.79 → 3.16 Mbps | 7.27-7.54 Mbps | A holds steady (5% in hold region) | ✅ |
| 8: a_degrade_4 | 15s | A: 4M | 12% | A→B | **50.00 → 29.57 Mbps** | 2.12 → 4.73 Mbps | 7.24-7.53 Mbps | A decreases (loss > 10%) | ✅ |
| 9: a_severe | 15s | A: 2M | 15% | A→B | **24.90 → 11.68 Mbps** | 2.29 → 2.60 Mbps | 7.24-7.42 Mbps | A aggressive decrease (loss > 10%) | ✅ |
| 10: a_hold_low | 20s | A: 8M | 5% | A→B | **7.25 Mbps** | 2.77 → 7.28 Mbps | 7.20-7.39 Mbps | A holds low (5% in hold region) | ✅ |
| 11: a_recovery | 40s | unlimited | 0% | A→B | **7.25 → 42.01 Mbps** | 4.33 → 32.54 Mbps | 6.32-7.37 Mbps | A increases (loss < 2%) | ✅ |

**Key Points**:
- **Bold** values indicate the BWE being tested for that phase
- **b_* phases** (2-6): Measure **Peer B's BWE** (columns: Peer B BWE)
- **a_* phases** (7-11): Measure **Peer A's BWE** (columns: Peer A BWE)
- Unrestricted direction shows BWE increasing to max (~50 Mbps)

### Data Aggregation

The framework extracts metrics using:

```bash
# Get last BWE value from peer logs
get_peer_stats() {
    local container=$1
    local peer_type=$2

    local gcc_pattern=$(get_gcc_pattern $peer_type)
    local rx_pattern=$(get_rx_pattern $peer_type)

    local gcc=$(docker logs $container 2>&1 | grep "$gcc_pattern" | tail -1 | \
                grep -oE '[0-9]+\.[0-9]+ Mbps' | head -1)
    local rx=$(docker logs $container 2>&1 | grep "$rx_pattern" | tail -1 | \
               grep -oE '[0-9]+\.[0-9]+ Mbps' | head -1)

    echo "${peer_type}: GCC=${gcc:-N/A} RX=${rx:-N/A}"
}
```

**Process**:
1. `docker logs <container>` - Get all container output
2. `grep "$gcc_pattern"` - Filter to BWE log lines
3. `tail -1` - Get most recent value
4. `grep -oE '[0-9]+\.[0-9]+ Mbps'` - Extract numeric value
5. `head -1` - Take first match

### Manual Analysis

For detailed analysis, extract data from log files:

```bash
# Extract all aiortc BWE values with timestamps
grep "📤 Sending Loss BWE:" logs/*/peer_a_aiortc-bwe.log

# Extract chromium BWE values
grep "📤 Sending GCC:" logs/*/peer_b_chromium.log

# Extract with loss percentages (aiortc-bwe only)
grep "📤 Sending Loss BWE:" logs/*/peer_a_aiortc-bwe.log | \
  grep -oE "loss=[0-9]+\.[0-9]+"
```

---

## Extending the Framework

### Adding New Peer Types

1. **Create peer implementation** in new subdirectory (e.g., `my_peer/`)

2. **Add to framework configuration** (`bwe_test_framework.sh`):

```bash
get_image_name() {
    case "$1" in
        # ... existing cases ...
        my_peer) echo "my-peer-image" ;;
    esac
}

get_build_context() {
    case "$1" in
        # ... existing cases ...
        my_peer) echo "my_peer" ;;
    esac
}

get_dockerfile() {
    case "$1" in
        # ... existing cases ...
        my_peer) echo "my_peer/Dockerfile" ;;
    esac
}

get_gcc_pattern() {
    case $peer_type in
        # ... existing cases ...
        my_peer) echo "📤 My BWE:" ;;
    esac
}
```

3. **Test the new peer**:

```bash
PEER_A_TYPE=my_peer PEER_B_TYPE=chromium ./bwe_test_framework.sh
```

### Modifying Test Phases

Edit the `PHASES` array in `bwe_test_framework.sh`:

```bash
declare -a PHASES=(
    "phase_name:duration:peer_a_limit:peer_b_limit"
    "custom_phase:30:unlimited:8"
    # ...
)
```

**Example**: Add a 10% loss phase between hold and severe:

```bash
declare -a PHASES=(
    "baseline:20:unlimited:unlimited"
    "b_degrade_8:15:unlimited:8"    # 5% loss
    "b_degrade_6:15:unlimited:6"    # NEW: 10% loss threshold
    "b_degrade_4:15:unlimited:4"    # 12% loss
    "b_severe:15:unlimited:2"       # 15% loss
    # ...
)
```

Then update `set_rate_limit()` to map 6 Mbps → 10% loss:

```bash
case "$rate_mbps" in
    8) loss_pct="5%" ;;
    6) loss_pct="10%" ;;   # NEW
    4) loss_pct="12%" ;;
    2) loss_pct="15%" ;;
esac
```

### Customizing Network Emulation

Replace `set_rate_limit()` with custom tc configuration:

```bash
set_rate_limit() {
    local container=$1
    local rate_mbps=$2

    docker exec $container tc qdisc del dev eth0 root 2>/dev/null || true

    if [ "$rate_mbps" != "unlimited" ]; then
        # Your custom tc configuration
        docker exec $container tc qdisc add dev eth0 root netem delay 100ms loss 5%
        echo "  → Applied custom network condition to ${container}"
    fi
}
```

---

## Troubleshooting

### Container Fails to Start

**Check Docker logs**:
```bash
docker logs gcc-peer-a-<test_id>
docker logs gcc-peer-b-<test_id>
```

**Common issues**:
- Port 8080 already in use (kill existing containers)
- Docker image build failure (check Dockerfile paths)
- Network creation failure (delete orphaned networks)

### No BWE Values Collected

**Verify log patterns**:
```bash
# Check what peer is actually logging
docker logs gcc-peer-a-<test_id> 2>&1 | grep -i "sending\|gcc\|bwe"

# Update get_gcc_pattern() to match actual format
```

**Common issues**:
- Peer using different log format than expected
- Peer not implementing BWE logging
- WebRTC connection not established

### Network Limits Not Applied

**Verify tc qdisc**:
```bash
docker exec gcc-peer-a-<test_id> tc qdisc show dev eth0
```

**Should show**:
```
qdisc netem 8001: root refcnt 2 limit 1000 loss 5%
```

**Common issues**:
- Missing `NET_ADMIN` capability (framework handles this)
- Container using wrong network interface (not eth0)
- tc commands failing silently (check framework output)

### Test Hangs or Times Out

**Check peer connection**:
```bash
# Peer A should show "Starting server"
docker logs gcc-peer-a-<test_id> | grep -i "server\|listening"

# Peer B should show "Connecting"
docker logs gcc-peer-b-<test_id> | grep -i "connect\|offer"
```

**Common issues**:
- Signaling failure (Peer B can't reach Peer A)
- WebRTC connection timeout (firewalls blocking UDP)
- Peer crash during connection (check logs for errors)

---

## Requirements

- **Docker**: Version 20.10+ with buildx support
- **Linux Kernel**: 4.15+ with `tc` (traffic control) support
- **Disk Space**: ~2-3 GB for all Docker images
- **Network**: Internet access for pulling base images (python:3.11-slim, node:20-slim, golang:1.21)
- **Permissions**: User must be in `docker` group or have sudo access

### Verification

```bash
# Check Docker version
docker version

# Check tc support
tc qdisc add dev lo root netem loss 1%
tc qdisc del dev lo root

# Check buildx
docker buildx version
```

---

## Quick Reference

### Run Commands

```bash
# Quick test (25s)
QUICK_MODE=true PEER_A_TYPE=aiortc-bwe PEER_B_TYPE=chromium ./bwe_test_framework.sh

# Full test (4 min)
PEER_A_TYPE=aiortc-bwe PEER_B_TYPE=chromium ./bwe_test_framework.sh

# With log capture
PEER_A_TYPE=aiortc-bwe PEER_B_TYPE=pion ./bwe_test_framework.sh 2>&1 | tee test.log
```

### Log Analysis

```bash
# Extract BWE values
grep "📤 Sending" logs/*/peer_*.log

# Find specific phase
grep "\[Phase 5:" logs/*/peer_*.log -A 20

# Count successful tests
grep "✅ Test complete" logs/*/peer_*.log | wc -l
```

### Cleanup

```bash
# Remove all test containers
docker ps -a | grep gcc-peer | awk '{print $1}' | xargs docker rm -f

# Remove all test networks
docker network ls | grep gcc-network | awk '{print $1}' | xargs docker network rm

# Remove all peer images
docker images | grep -E "aiortc|pion|chromium" | awk '{print $3}' | xargs docker rmi -f
```

---

## License

This testing framework is part of the aiortc project and follows the same license.
