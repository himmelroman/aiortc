# TWCC/GCC Implementation Summary

This document summarizes the complete TWCC (Transport-Wide Congestion Control) and GCC (Google Congestion Control) implementation for aiortc.

## Overview

This implementation adds modern congestion control to aiortc by implementing:
- **TWCC (Transport-Wide Congestion Control)**: RTP header extension and RTCP feedback mechanism for per-packet timing information
- **GCC (Google Congestion Control)**: Sender-side bandwidth estimation combining delay-based and loss-based controllers
- Full integration with existing `RTCRtpSender` and `RTCRtpReceiver` classes
- Encoder bitrate control based on GCC estimates

## File Structure

### Core Implementation

```
src/aiortc/contrib/
├── twcc/
│   ├── __init__.py           # TWCC module exports
│   ├── receiver.py           # TWCC receiver components (600 lines)
│   │   ├── SequenceNumberUnwrapper
│   │   ├── TransportSequenceNumberManager
│   │   ├── ArrivalTimeMap
│   │   ├── PacketChunkEncoder
│   │   ├── ReceiveDeltaEncoder
│   │   └── TWCCRecorder (main receiver)
│   └── sender.py             # TWCC sender components (450 lines)
│       ├── SentPacketTracker
│       ├── PacketChunkDecoder
│       ├── ReceiveDeltaDecoder
│       ├── TWCCParser
│       └── PacketFeedback
├── gcc/
│   ├── __init__.py           # GCC module exports
│   └── estimator.py          # GCC bandwidth estimation (350 lines)
│       ├── PacketFeedbackProcessor
│       ├── DelayBasedController (reuses existing AIMD/Kalman components)
│       ├── LossBasedController
│       └── SenderSideBandwidthEstimator (main estimator)
└── congestion_control.py     # Configuration API (250 lines)
    ├── CongestionControlAlgorithm
    ├── CongestionControlConfig
    ├── create_gcc_config()
    ├── create_remb_config()
    └── CongestionControlIntegration
```

### Integration Points

Modified existing aiortc files:
- `src/aiortc/rtcrtpreceiver.py` - Added TWCC recording and feedback generation
- `src/aiortc/rtcrtpsender.py` - Added GCC estimation and encoder bitrate control

### Tests

```
tests/
├── test_twcc.py                      # TWCC unit tests (24 tests)
├── test_gcc.py                       # GCC unit tests (19 tests)
├── test_congestion_control.py        # Integration tests (20 tests)
├── test_rtcpeerconnection_gcc.py     # E2E tests with real peer connections
└── test_chrome_interop.py            # Chrome interoperability tests (Playwright)
```

Total: **63+ unit tests**, all passing

### Examples and Documentation

```
examples/
└── validate_twcc.py          # Quick validation script for TWCC/GCC

Documentation:
├── TWCC_VALIDATION_GUIDE.md  # Comprehensive validation guide
└── TWCC_GCC_IMPLEMENTATION_SUMMARY.md  # This file
```

## Technical Details

### TWCC (Transport-Wide Congestion Control)

**Purpose**: Provides detailed per-packet timing feedback from receiver to sender

**Key Components**:
- **Receiver side** (`TWCCRecorder`):
  - Records arrival time of each packet with transport sequence number
  - Generates RTCP feedback packets (PT=205, FMT=15)
  - Encodes packet status using run-length and status-vector chunks
  - Encodes receive time deltas (250µs for small, 1ms for large)

- **Sender side** (`TWCCParser`):
  - Parses RTCP TWCC feedback packets
  - Correlates with sent packet tracker
  - Produces `PacketFeedback` objects with send/receive times

**RTCP Format**:
- Packet Type: 205 (Generic RTP Feedback)
- Format: 15 (Transport-Wide CC)
- Reference time: 24-bit, 64ms units
- Small delta: 8-bit, 250µs units
- Large delta: 16-bit, 1ms units

### GCC (Google Congestion Control)

**Purpose**: Estimates available bandwidth using packet timing and loss information

**Architecture**:
1. **Delay-Based Controller**:
   - Reuses aiortc's existing components:
     - `InterArrival`: Measures packet group arrival patterns
     - `OveruseEstimator`: Kalman filter for delay gradient
     - `OveruseDetector`: Detects network state (normal/overuse/underuse)
     - `AimdRateControl`: AIMD rate adjustment

2. **Loss-Based Controller**:
   - Monitors packet loss ratio
   - Reduces bitrate by 50% when loss exceeds threshold
   - Rate-limited updates (max once per second)

3. **Combined Estimation**:
   - Takes minimum of delay-based and loss-based estimates
   - Clamps to min/max bitrate bounds
   - Updates encoder target bitrate

**Default Parameters**:
- Initial bitrate: 300 kbps
- Min bitrate: 30 kbps
- Max bitrate: 2.5 Mbps
- Loss threshold: 10%

### Integration Flow

```
[RTCRtpSender]                              [RTCRtpReceiver]
     │                                            │
     ├─> Add transport seq number to RTP         ├─> Record packet arrival
     │   (via TransportSequenceNumberManager)    │   (via TWCCRecorder)
     │                                            │
     ├─> Track sent packets                      ├─> Generate TWCC feedback
     │   (via SentPacketTracker)                 │   (periodic, via _run_rtcp)
     │                                            │
     │                        RTCP TWCC           │
     │   <─────────────────────────────────────── │
     │                                            │
     ├─> Parse TWCC feedback
     │   (via TWCCParser)
     │
     ├─> Correlate with sent packets
     │   (via PacketFeedbackProcessor)
     │
     ├─> Process through GCC
     │   (via SenderSideBandwidthEstimator)
     │
     └─> Update encoder.target_bitrate
         (direct property update)
```

## Usage

### Basic Usage

```python
from aiortc import RTCPeerConnection
from aiortc.contrib.twcc.receiver import TransportSequenceNumberManager

# Create peer connection
pc = RTCPeerConnection()

# Add video track
track = VideoStreamTrack()
sender = pc.addTrack(track)

# Enable GCC on sender
transport_seq_manager = TransportSequenceNumberManager()
sender.enable_gcc(transport_seq_manager, initial_bitrate=500000)

# On the receiver side, enable TWCC feedback
for receiver in pc.getReceivers():
    if receiver.track.kind == "video":
        receiver.enable_twcc(ssrc=receiver._RTCRtpReceiver__rtcp_ssrc or 1)
```

### Configuration API

```python
from aiortc.contrib.congestion_control import (
    create_gcc_config,
    CongestionControlIntegration
)

# Create GCC config
config = create_gcc_config(
    initial_mbps=2.0,
    min_mbps=0.1,
    max_mbps=5.0
)

# Create integration
integration = CongestionControlIntegration(config)
integration.initialize_gcc()

# Get components
transport_seq_manager = integration.get_transport_seq_manager()
gcc_estimator = integration.get_gcc_estimator()
twcc_recorder = integration.get_or_create_twcc_recorder(ssrc=12345)
```

## Validation

### Running Tests

```bash
# Run all TWCC/GCC tests
python -m unittest tests.test_twcc -v
python -m unittest tests.test_gcc -v
python -m unittest tests.test_congestion_control -v

# Run E2E tests
python -m unittest tests.test_rtcpeerconnection_gcc -v

# Run Chrome interop tests (requires Playwright)
python -m unittest tests.test_chrome_interop -v
```

### Quick Validation

```bash
# Run validation script
python examples/validate_twcc.py
```

This will:
1. Create two local peers
2. Enable TWCC/GCC
3. Stream video between them
4. Report on TWCC/GCC status
5. Verify encoder bitrate control

## Reference Implementations

This implementation is based on:
- **Pion WebRTC** (Go): `pkg/rtcp/transport_layer_cc.go`, interceptor TWCC/GCC
- **libwebrtc** (C++): `modules/congestion_controller/goog_cc/`
- **IETF Draft**: draft-holmer-rmcat-transport-wide-cc-extensions

## Compatibility

- **aiortc**: Compatible with existing aiortc architecture, reuses AIMD/Kalman components
- **Chrome/Firefox**: Implements standard TWCC extension, tested with Chrome via Playwright
- **Backward compatibility**: Does not break existing REMB-based congestion control

## Performance Characteristics

- **Overhead**: ~20-30 bytes per RTCP feedback packet (depends on packet count)
- **Feedback frequency**: Recommended 100-300ms (configurable)
- **CPU impact**: Minimal, reuses existing Kalman filter implementation
- **Memory**: O(n) where n is number of packets in feedback window (~100-200 packets)

## Future Enhancements

Potential improvements (not implemented):
- Probing-based bandwidth estimation for faster ramp-up
- FEC/RED integration with loss-based controller
- Multi-stream bandwidth allocation
- Advanced pacing algorithms
- Detailed network statistics API

## License

Same as aiortc (BSD 3-Clause)

## Contributors

Implementation based on thorough research of libwebrtc and Pion implementations, adapted to aiortc's architecture and Python idioms.
