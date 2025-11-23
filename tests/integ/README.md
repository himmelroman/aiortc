# aiortc GCC/TWCC Integration Tests

Bidirectional WebRTC tests validating aiortc's GCC (Google Congestion Control) implementation against multiple peer implementations using TWCC (Transport-Wide Congestion Control) feedback.

## Test Scenarios

1. **aiortc ↔ Pion** - Tests against Pion's reference GCC implementation
2. **aiortc ↔ Chromium** - Tests against Chromium's native WebRTC GCC implementation

## Directory Structure

```
tests/integ/
├── README.md                      - This file
├── aiortc_pion_test.sh           - aiortc ↔ Pion test
├── aiortc_chromium_test.sh       - aiortc ↔ Chromium test
├── docs/                          - GCC/TWCC alignment documentation
├── pion/                          - Pion peer implementation (Go)
│   ├── Dockerfile
│   ├── entrypoint.sh
│   ├── main.go
│   ├── go.mod, go.sum
│   ├── pion-twcc-client          - Compiled binary
│   └── local_pion_gcc/           - Reference implementation
├── chromium/                      - Chromium peer implementation (Node.js + Playwright)
│   ├── Dockerfile
│   ├── entrypoint.sh
│   ├── peer_twcc.js              - WebRTC peer using Playwright
│   └── package.json
└── aiortc/                        - aiortc peer implementation (Python)
    ├── Dockerfile
    ├── entrypoint.sh
    └── peer_twcc.py
```

### Documentation
- `docs/` - GCC/TWCC component alignment documentation
  - `GCC_TWCC_ALIGNMENT_REVIEW.md` - Overall architecture review
  - `01_ARRIVAL_GROUP_ACCUMULATOR.md` - Arrival group processing
  - `02_SLOPE_ESTIMATOR_KALMAN.md` - Kalman filter implementation
  - `03_OVERUSE_DETECTOR_ADAPTIVE_THRESHOLD.md` - Overuse detection
  - `IMPLEMENTATION_ROADMAP.md` - Implementation plan
  - `IMPLEMENTATION_LOG.md` - Change log

## Quick Start

### Prerequisites
- Docker installed
- Linux kernel with tc (traffic control) support, or macOS with Docker Desktop

### Run the Tests

```bash
cd /Users/himmelroman/projects/oylo/aiortc/tests/integ

# Test aiortc against Pion's GCC implementation
./aiortc_pion_test.sh

# Test aiortc against Chromium's native WebRTC GCC
./aiortc_chromium_test.sh
```

Each test script will:
1. Build required Docker images (automatically)
2. Create an isolated Docker network
3. Start both peers and establish bidirectional WebRTC connection
4. Run through 4 test phases with bandwidth limiting
5. Report GCC adaptation results for both directions

## Test Phases

**Phase 1: Baseline** (15s)
- No bandwidth limiting
- Both peers stream at maximum capacity
- Expected: GCC estimates stabilize at high bitrate

**Phase 2: aiortc → pion Limited** (20s)
- Apply 5 Mbps limit on aiortc's outbound traffic
- Expected: aiortc's GCC drops to ~5 Mbps

**Phase 3: Both Directions Limited** (20s)
- Add 2 Mbps limit on pion's outbound traffic
- Expected: pion's GCC drops to ~2 Mbps, aiortc stays at ~5 Mbps

**Phase 4: Recovery** (30s)
- Remove all bandwidth limits
- Expected: Both peers' GCC estimates recover to high bitrate

## Test Comparison

| Aspect | aiortc ↔ Pion | aiortc ↔ Chromium |
|--------|---------------|-------------------|
| **Peer Type** | Go (Pion library) | JavaScript (Chromium via Playwright) |
| **GCC Implementation** | Pion's reference implementation | Chromium's native WebRTC GCC |
| **TWCC Support** | Via interceptor | Native browser implementation |
| **Use Case** | Validate against reference | Validate against production browser |
| **Observability** | Detailed GCC logs | Browser stats API |

**Why Both Tests?**
- **Pion test**: Validates implementation correctness against the reference GCC algorithm
- **Chromium test**: Validates real-world compatibility with the most common WebRTC client

## Building Images Manually

```bash
# Build Pion peer
docker build -f pion/Dockerfile -t pion-twcc-peer pion/

# Build Chromium peer
docker build -f chromium/Dockerfile -t chromium-twcc-peer chromium/

# Build aiortc peer
docker build -f aiortc/Dockerfile -t aiortc-twcc-peer aiortc/
```

## Building Pion Client Binary

The pion test script automatically builds the binary for Linux ARM64. To build manually:

```bash
cd pion/

# Build for Linux ARM64 (Docker/production)
GOOS=linux GOARCH=arm64 go build -o pion-twcc-client

# Or for local architecture (testing)
go build -o pion-twcc-client
```

## Success Criteria

✅ **GCC Adaptation Working:**
- Estimates drop to match bandwidth limits within 2-3 seconds
- No oscillation (steady state after adaptation)
- Smooth recovery when limits removed
- Arrival groups being formed and processed

❌ **GCC Not Working:**
- Estimates don't respond to bandwidth changes
- Continuous oscillation between high/low values
- Estimates drop to zero causing freeze
- No arrival groups in logs

## Test Output

The test script outputs real-time stats every 3 seconds:
```
aiortc: GCC=5.23 Mbps RX=2.15 Mbps | pion: GCC=2.04 Mbps TX=2.01 Mbps RX=5.18 Mbps
```

- `GCC` - Congestion control estimate (what peer thinks it can send)
- `TX` - Actual sending rate
- `RX` - Receiving rate from other peer

## Debugging

**View container logs:**
```bash
# aiortc logs
docker logs gcc-aiortc

# pion logs
docker logs gcc-pion
```

**Check for key indicators:**
- "📤 Sending GCC:" - GCC estimate updates
- "📥 Receiving:" - Incoming bitrate
- "groups:" - Arrival group formation (pion)
- "Loss controller LIMITING" - Loss-based limiting active (aiortc)

**Manual container interaction:**
```bash
# Enter container
docker exec -it gcc-aiortc bash

# Apply custom tc limit
docker exec gcc-aiortc tc qdisc add dev eth0 root tbf rate 3mbit burst 32kbit latency 50ms

# Remove tc limit
docker exec gcc-aiortc tc qdisc del dev eth0 root
```

## Implementation Notes

The aiortc GCC implementation is aligned with Pion's reference implementation:

- **Delay-based controller**: Kalman filter, overuse detector, AIMD rate control
- **Loss-based controller**: Acts as limiter on delay-based estimates (not replacement)
- **Combined estimate**: `min(delay_estimate, loss_internal_bitrate)`
- **TWCC feedback**: Transport-wide sequence numbers with arrival timestamps
- **Arrival groups**: 5ms inter-departure/inter-arrival thresholds

See `docs/` for detailed component-by-component alignment documentation.

## Reference Implementation

The `pion/local_pion_gcc/` directory contains the Pion GCC implementation extracted for reference. This is used to ensure line-by-line parity between aiortc and Pion GCC behavior.

Key files:
- `pion/local_pion_gcc/pkg/gcc/send_side_bwe.go` - Main orchestrator
- `pion/local_pion_gcc/pkg/gcc/delay_based_bwe.go` - Delay controller
- `pion/local_pion_gcc/pkg/gcc/loss_based_bwe.go` - Loss controller (limiter pattern)
- `pion/local_pion_gcc/pkg/gcc/arrival_group_accumulator.go` - Arrival group processing
- `pion/local_pion_gcc/pkg/gcc/overuse_detector.go` - Overuse detection
- `pion/local_pion_gcc/pkg/gcc/kalman.go` - Kalman filter estimator
