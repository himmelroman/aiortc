# TWCC/GCC Implementation Guide for aiortc

**Version:** 1.0
**Date:** 2025-11-14
**Author:** Implementation Team
**Related:** TWCC_GCC_IMPLEMENTATION_RESEARCH.md

---

## Table of Contents

1. [Implementation Overview](#1-implementation-overview)
2. [Reference Implementations Map](#2-reference-implementations-map)
3. [Phase 1: TWCC Packet Tracking & Encoding](#phase-1-twcc-packet-tracking--encoding)
4. [Phase 2: TWCC Feedback Parsing](#phase-2-twcc-feedback-parsing)
5. [Phase 3: Sender-Side Bandwidth Estimation (GCC)](#phase-3-sender-side-bandwidth-estimation-gcc)
6. [Phase 4: Integration](#phase-4-integration)
7. [Phase 5: End-to-End Testing](#phase-5-end-to-end-testing)
8. [Testing Strategy](#testing-strategy)
9. [Debugging Guide](#debugging-guide)
10. [Performance Optimization](#performance-optimization)

---

## 1. Implementation Overview

### 1.1 Goals

This guide provides **step-by-step instructions** for implementing TWCC/GCC in aiortc by:

1. **Translating** proven reference implementations (primarily Pion)
2. **Reusing** existing aiortc components where possible
3. **Testing** each phase comprehensively before moving forward
4. **Validating** with real peer-to-peer connections

### 1.2 Strategy

**Primary Translation Source:** Pion interceptor (Go → Python)
- Clean, modern codebase
- Well-tested in production
- Similar architecture to aiortc
- Easier to translate than C++

**Secondary Reference:** libwebrtc (for algorithm details)
- Authoritative GCC implementation
- Used for validation and edge cases

**Reuse from aiortc:**
- `AimdRateControl` (rate.py:35-182)
- `OveruseEstimator` (rate.py:338-446)
- `OveruseDetector` (rate.py:267-335)
- `InterArrival` (rate.py:200-264)
- Header extension infrastructure (rtp.py:42-150)

### 1.3 Implementation Phases

```
Phase 1: TWCC Packet Tracking & Encoding (Receiver Side)
├─ Step 1.1: Transport Sequence Number Manager
├─ Step 1.2: Arrival Time Map
├─ Step 1.3: TWCC Recorder
├─ Step 1.4: Packet Chunk Encoder
├─ Step 1.5: Receive Delta Encoder
├─ Step 1.6: RTCP RTPFB Packet Support
└─ Step 1.7: Receiver Integration
   Duration: 2-3 weeks

Phase 2: TWCC Feedback Parsing (Sender Side)
├─ Step 2.1: Packet Chunk Decoder
├─ Step 2.2: Receive Delta Decoder
├─ Step 2.3: TWCC Parser
├─ Step 2.4: Sent Packet Tracker
└─ Step 2.5: Sender Integration
   Duration: 1-2 weeks

Phase 3: Sender-Side Bandwidth Estimation (GCC)
├─ Step 3.1: Packet Feedback Processor
├─ Step 3.2: Delay-Based Controller
├─ Step 3.3: Loss-Based Controller
├─ Step 3.4: Bandwidth Estimator Combiner
└─ Step 3.5: Rate Control Integration
   Duration: 3-4 weeks

Phase 4: Integration
├─ Step 4.1: Configuration System
├─ Step 4.2: SDP Negotiation
├─ Step 4.3: Fallback to REMB
└─ Step 4.4: Metrics & Monitoring
   Duration: 1-2 weeks

Phase 5: End-to-End Testing
├─ Step 5.1: Basic P2P Test
├─ Step 5.2: Network Simulation Tests
├─ Step 5.3: Multi-Stream Tests
├─ Step 5.4: Performance Benchmarks
└─ Step 5.5: Regression Tests
   Duration: 2-3 weeks
```

**Total Duration:** 9-14 weeks

---

## 2. Reference Implementations Map

### 2.1 Pion Interceptor Structure

```
github.com/pion/interceptor/pkg/twcc/

arrival_time_map.go (100 lines)
├─ arrivalTimeMap struct
├─ Add(seqNr, time) method
├─ Get(seqNr) method
└─ Cull(before_time) method
   → TRANSLATE TO: src/aiortc/twcc.py::ArrivalTimeMap

header_extension_interceptor.go (150 lines)
├─ HeaderExtensionInterceptor struct
├─ nextSequenceNr (atomic counter)
└─ BindLocalStream() - adds seqNr to packets
   → TRANSLATE TO: src/aiortc/rtp.py::TransportSequenceNumberManager
   → INTEGRATE INTO: src/aiortc/rtcrtpsender.py

sender_interceptor.go (200 lines)
├─ SenderInterceptor struct
├─ Recorder instance
├─ BindRemoteStream() - records packets
└─ loop() - sends feedback periodically
   → TRANSLATE TO: src/aiortc/rtcrtpreceiver.py::_handle_rtp_packet
   → INTEGRATE INTO: src/aiortc/rtcrtpreceiver.py

twcc.go (450 lines)
├─ Recorder struct
├─ Record(seqNr, arrivalTime, ssrc) method
├─ BuildFeedbackPacket() method
├─ Packet chunk encoding
└─ Delta encoding
   → TRANSLATE TO: src/aiortc/twcc.py::TWCCRecorder
   → TRANSLATE TO: src/aiortc/twcc.py::PacketChunkEncoder
   → TRANSLATE TO: src/aiortc/twcc.py::ReceiveDeltaEncoder
```

### 2.2 libwebrtc GCC Structure

```
modules/congestion_controller/goog_cc/

goog_cc_network_control.cc (800 lines)
├─ OnTransportPacketsFeedback() - processes TWCC
└─ Coordinates all components
   → REFERENCE FOR: src/aiortc/gcc.py::SenderSideBandwidthEstimator

delay_based_bwe.cc (400 lines)
├─ IncomingPacketFeedbackVector()
├─ Uses InterArrivalDelta
├─ Uses TrendlineEstimator (or Kalman)
└─ Uses OveruseDetector
   → REUSE: src/aiortc/rate.py (existing components)
   → NEW: src/aiortc/gcc.py::DelayBasedController

aimd_rate_control.cc (300 lines)
└─ AIMD algorithm
   → REUSE: src/aiortc/rate.py::AimdRateControl (already matches!)
```

### 2.3 Translation Cheat Sheet

| Pion (Go) | Python Equivalent | Notes |
|-----------|------------------|-------|
| `type Foo struct { ... }` | `class Foo:` or `@dataclass` | Use dataclass for simple structs |
| `map[uint64]int64` | `dict[int, int]` | Python 3.9+ type hints |
| `atomic.AddUint32(&x, 1)` | `threading` or `multiprocessing.Value` | Need thread safety |
| `sync.Mutex` | `threading.Lock()` | Python threading |
| `time.Duration` | `float` (seconds) or `int` (ms) | Be consistent |
| `uint16` | `int` with masking `& 0xFFFF` | Python has arbitrary precision |
| `binary.BigEndian.Uint16()` | `struct.unpack("!H", ...)` | Already used in aiortc |
| `make([]byte, n)` | `bytearray(n)` or `bytes(n)` | Use bytes for immutable |

---

## PHASE 1: TWCC Packet Tracking & Encoding

**Goal:** Receiver can track incoming packets and generate TWCC feedback

**Duration:** 2-3 weeks

**Reference:** Pion `pkg/twcc/`

---

### Step 1.1: Transport Sequence Number Manager

**File:** `src/aiortc/rtp.py`

**Reference:** Pion `header_extension_interceptor.go:15-45`

**What it does:** Manages a monotonically increasing sequence number shared across all RTP streams on a transport.

#### Implementation

```python
# Add to src/aiortc/rtp.py

import threading
from typing import Optional

class TransportSequenceNumberManager:
    """
    Manages transport-wide sequence numbers for TWCC.

    This counter is shared across all media streams on the same DTLS transport.
    Based on: pion/interceptor/pkg/twcc/header_extension_interceptor.go
    """

    def __init__(self, initial_value: int = 0) -> None:
        """
        Initialize the sequence number manager.

        Args:
            initial_value: Starting sequence number (default 0)
        """
        self._sequence_number = initial_value
        self._lock = threading.Lock()

    def next(self) -> int:
        """
        Get the next transport sequence number.

        This method is thread-safe and can be called from multiple streams.

        Returns:
            The next sequence number (0-65535, wraps around)

        Reference: header_extension_interceptor.go:38-40
        Go code:
            seqNr := atomic.AddUint32(&h.nextSequenceNr, 1) - 1
            tcc := &rtp.TransportCCExtension{TransportSequence: uint16(seqNr)}
        """
        with self._lock:
            current = self._sequence_number
            # Increment and wrap at 16-bit boundary
            self._sequence_number = (self._sequence_number + 1) & 0xFFFF
            return current

    def reset(self, value: int = 0) -> None:
        """Reset the sequence number to a specific value."""
        with self._lock:
            self._sequence_number = value & 0xFFFF
```

#### Testing

**File:** `tests/test_twcc.py` (create new file)

```python
# tests/test_twcc.py

import unittest
import threading
from aiortc.rtp import TransportSequenceNumberManager


class TestTransportSequenceNumberManager(unittest.TestCase):
    """
    Test suite for TransportSequenceNumberManager.

    Reference: pion/interceptor/pkg/twcc/header_extension_interceptor_test.go
    """

    def test_initial_value(self):
        """Test manager starts at initial value."""
        manager = TransportSequenceNumberManager(initial_value=100)
        self.assertEqual(manager.next(), 100)
        self.assertEqual(manager.next(), 101)

    def test_default_initial_value(self):
        """Test manager starts at 0 by default."""
        manager = TransportSequenceNumberManager()
        self.assertEqual(manager.next(), 0)
        self.assertEqual(manager.next(), 1)

    def test_wraparound(self):
        """Test sequence number wraps at 16-bit boundary."""
        manager = TransportSequenceNumberManager(initial_value=65535)
        self.assertEqual(manager.next(), 65535)
        self.assertEqual(manager.next(), 0)
        self.assertEqual(manager.next(), 1)

    def test_thread_safety(self):
        """Test concurrent access from multiple threads."""
        manager = TransportSequenceNumberManager()
        results = []
        count = 1000

        def worker():
            for _ in range(count):
                results.append(manager.next())

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Check all sequence numbers are unique
        self.assertEqual(len(results), 10 * count)
        self.assertEqual(len(set(results)), 10 * count)

    def test_reset(self):
        """Test resetting sequence number."""
        manager = TransportSequenceNumberManager()
        manager.next()  # 0
        manager.next()  # 1
        manager.reset(500)
        self.assertEqual(manager.next(), 500)


if __name__ == "__main__":
    unittest.main()
```

**Run tests:**
```bash
python -m pytest tests/test_twcc.py::TestTransportSequenceNumberManager -v
```

---

### Step 1.2: Arrival Time Map

**File:** `src/aiortc/twcc.py` (create new file)

**Reference:** Pion `arrival_time_map.go`

**What it does:** Stores packet arrival times with automatic cleanup of old entries.

#### Implementation

```python
# src/aiortc/twcc.py

"""
Transport-Wide Congestion Control (TWCC) implementation.

Based on:
- draft-holmer-rmcat-transport-wide-cc-extensions-01
- pion/interceptor/pkg/twcc

This module provides receiver-side packet tracking and feedback generation.
"""

from collections import OrderedDict
from typing import Optional

# Constants from pion/interceptor/pkg/twcc/twcc.go
PACKET_WINDOW_MICROSECONDS = 500_000  # 500ms history window
MAX_MISSING_SEQUENCE_NUMBERS = 0x7FFE  # ~32K tolerance for gaps


class ArrivalTimeMap:
    """
    Stores packet arrival times with automatic cleanup of old packets.

    Based on: pion/interceptor/pkg/twcc/arrival_time_map.go

    The map maintains a sliding window of packets received in the last 500ms.
    Older packets are automatically discarded to prevent unbounded memory growth.
    """

    def __init__(self, window_us: int = PACKET_WINDOW_MICROSECONDS) -> None:
        """
        Initialize arrival time map.

        Args:
            window_us: Time window in microseconds (default 500ms)
        """
        # Use OrderedDict to maintain insertion order for efficient cleanup
        self._map: OrderedDict[int, int] = OrderedDict()
        self._window_us = window_us

    def add(self, sequence_number: int, arrival_time_us: int) -> None:
        """
        Add a packet arrival time.

        Args:
            sequence_number: Unwrapped 64-bit sequence number
            arrival_time_us: Arrival timestamp in microseconds

        Reference: arrival_time_map.go:15-20
        Go code:
            func (m *arrivalTimeMap) Add(seqNr uint64, arrivalTime int64) {
                m.m[seqNr] = arrivalTime
            }
        """
        self._map[sequence_number] = arrival_time_us

        # Cull old packets
        self._cull_old_packets(arrival_time_us)

    def get(self, sequence_number: int) -> Optional[int]:
        """
        Get arrival time for a sequence number.

        Args:
            sequence_number: Unwrapped 64-bit sequence number

        Returns:
            Arrival time in microseconds, or None if not found

        Reference: arrival_time_map.go:22-28
        """
        return self._map.get(sequence_number)

    def has(self, sequence_number: int) -> bool:
        """Check if sequence number exists in map."""
        return sequence_number in self._map

    def remove(self, sequence_number: int) -> None:
        """Remove a specific sequence number."""
        self._map.pop(sequence_number, None)

    def _cull_old_packets(self, current_time_us: int) -> None:
        """
        Remove packets older than the window.

        Reference: arrival_time_map.go:30-40
        Go code:
            func (m *arrivalTimeMap) Cull(beforeTime int64) {
                for seqNr, arrivalTime := range m.m {
                    if arrivalTime < beforeTime {
                        delete(m.m, seqNr)
                    }
                }
            }
        """
        cutoff_time = current_time_us - self._window_us

        # Remove old entries (iterate copy of keys to avoid modification during iteration)
        keys_to_remove = [
            seq_nr
            for seq_nr, arrival_time in list(self._map.items())
            if arrival_time < cutoff_time
        ]

        for seq_nr in keys_to_remove:
            del self._map[seq_nr]

    def get_earliest_sequence(self) -> Optional[int]:
        """Get the earliest (smallest) sequence number in the map."""
        if not self._map:
            return None
        # OrderedDict maintains insertion order, but we need min sequence
        return min(self._map.keys())

    def get_latest_sequence(self) -> Optional[int]:
        """Get the latest (largest) sequence number in the map."""
        if not self._map:
            return None
        return max(self._map.keys())

    def size(self) -> int:
        """Return number of packets tracked."""
        return len(self._map)

    def clear(self) -> None:
        """Clear all entries."""
        self._map.clear()
```

#### Testing

```python
# Add to tests/test_twcc.py

class TestArrivalTimeMap(unittest.TestCase):
    """
    Test suite for ArrivalTimeMap.

    Reference: pion/interceptor/pkg/twcc/arrival_time_map_test.go
    """

    def test_add_and_get(self):
        """Test adding and retrieving arrival times."""
        m = ArrivalTimeMap()

        m.add(100, 1000000)  # seq 100 at 1s
        m.add(101, 1001000)  # seq 101 at 1.001s

        self.assertEqual(m.get(100), 1000000)
        self.assertEqual(m.get(101), 1001000)
        self.assertIsNone(m.get(102))

    def test_has(self):
        """Test checking for existence."""
        m = ArrivalTimeMap()
        m.add(100, 1000000)

        self.assertTrue(m.has(100))
        self.assertFalse(m.has(101))

    def test_cull_old_packets(self):
        """Test automatic cleanup of old packets."""
        m = ArrivalTimeMap(window_us=100000)  # 100ms window

        # Add packets at different times
        m.add(100, 1000000)  # t=1.0s
        m.add(101, 1050000)  # t=1.05s
        m.add(102, 1100000)  # t=1.1s
        m.add(103, 1150000)  # t=1.15s

        # All should be present
        self.assertEqual(m.size(), 4)

        # Add packet at t=1.2s, should cull packets before t=1.1s
        m.add(104, 1200000)

        self.assertFalse(m.has(100))  # Culled
        self.assertFalse(m.has(101))  # Culled
        self.assertTrue(m.has(102))   # Still in window
        self.assertTrue(m.has(103))   # Still in window
        self.assertTrue(m.has(104))   # Just added
        self.assertEqual(m.size(), 3)

    def test_earliest_and_latest(self):
        """Test getting earliest and latest sequence numbers."""
        m = ArrivalTimeMap()

        self.assertIsNone(m.get_earliest_sequence())
        self.assertIsNone(m.get_latest_sequence())

        m.add(105, 1000000)
        m.add(100, 1001000)
        m.add(110, 1002000)

        self.assertEqual(m.get_earliest_sequence(), 100)
        self.assertEqual(m.get_latest_sequence(), 110)

    def test_clear(self):
        """Test clearing all entries."""
        m = ArrivalTimeMap()
        m.add(100, 1000000)
        m.add(101, 1001000)

        self.assertEqual(m.size(), 2)
        m.clear()
        self.assertEqual(m.size(), 0)
```

---

### Step 1.3: Sequence Number Unwrapper

**File:** `src/aiortc/twcc.py`

**Reference:** Pion uses `NewUnwrapper()` from RTP library

**What it does:** Converts 16-bit sequence numbers to 64-bit unwrapped values to handle wraparound.

#### Implementation

```python
# Add to src/aiortc/twcc.py

class SequenceNumberUnwrapper:
    """
    Unwraps 16-bit RTP sequence numbers to 64-bit values.

    RTP sequence numbers are 16-bit and wrap around at 65536.
    This class maintains state to convert them to monotonically increasing 64-bit values.

    Based on: github.com/pion/rtp/pkg/rtp/sequencer.go
    """

    def __init__(self) -> None:
        """Initialize unwrapper with no prior state."""
        self._last_unwrapped: Optional[int] = None
        self._cycles = 0  # Number of times we've wrapped (65536 increments)

    def unwrap(self, sequence_number: int) -> int:
        """
        Unwrap a 16-bit sequence number to 64-bit.

        Args:
            sequence_number: 16-bit sequence number (0-65535)

        Returns:
            Unwrapped 64-bit sequence number

        Example:
            unwrapper.unwrap(65535) -> 65535
            unwrapper.unwrap(0) -> 65536 (detected wrap)
            unwrapper.unwrap(1) -> 65537
        """
        # Ensure 16-bit
        sequence_number = sequence_number & 0xFFFF

        if self._last_unwrapped is None:
            # First packet
            self._last_unwrapped = sequence_number
            return sequence_number

        # Get the 16-bit part of last unwrapped
        last_seq_16 = self._last_unwrapped & 0xFFFF

        # Detect forward wrap (65535 -> 0)
        if sequence_number < last_seq_16 and (last_seq_16 - sequence_number) > 32768:
            self._cycles += 1
        # Detect backward wrap (unusual, but handle it)
        elif sequence_number > last_seq_16 and (sequence_number - last_seq_16) > 32768:
            self._cycles -= 1

        # Compute unwrapped value
        unwrapped = (self._cycles << 16) | sequence_number
        self._last_unwrapped = unwrapped

        return unwrapped

    def reset(self) -> None:
        """Reset unwrapper state."""
        self._last_unwrapped = None
        self._cycles = 0
```

#### Testing

```python
# Add to tests/test_twcc.py

class TestSequenceNumberUnwrapper(unittest.TestCase):
    """Test sequence number unwrapping."""

    def test_initial_sequence(self):
        """Test first sequence number."""
        unwrapper = SequenceNumberUnwrapper()
        self.assertEqual(unwrapper.unwrap(1000), 1000)

    def test_monotonic_increase(self):
        """Test normal increasing sequence."""
        unwrapper = SequenceNumberUnwrapper()

        self.assertEqual(unwrapper.unwrap(100), 100)
        self.assertEqual(unwrapper.unwrap(101), 101)
        self.assertEqual(unwrapper.unwrap(102), 102)

    def test_forward_wrap(self):
        """Test wrapping from 65535 to 0."""
        unwrapper = SequenceNumberUnwrapper()

        self.assertEqual(unwrapper.unwrap(65534), 65534)
        self.assertEqual(unwrapper.unwrap(65535), 65535)
        self.assertEqual(unwrapper.unwrap(0), 65536)  # Wrapped!
        self.assertEqual(unwrapper.unwrap(1), 65537)
        self.assertEqual(unwrapper.unwrap(2), 65538)

    def test_multiple_wraps(self):
        """Test multiple wraparounds."""
        unwrapper = SequenceNumberUnwrapper()

        # First cycle
        unwrapper.unwrap(65535)
        self.assertEqual(unwrapper.unwrap(0), 65536)

        # Second cycle
        unwrapper.unwrap(65535)
        self.assertEqual(unwrapper.unwrap(0), 131072)  # 2 * 65536

    def test_out_of_order_within_window(self):
        """Test out-of-order packets within reasonable window."""
        unwrapper = SequenceNumberUnwrapper()

        self.assertEqual(unwrapper.unwrap(100), 100)
        self.assertEqual(unwrapper.unwrap(102), 102)
        self.assertEqual(unwrapper.unwrap(101), 101)  # Out of order, but < 32K away
        self.assertEqual(unwrapper.unwrap(103), 103)

    def test_reset(self):
        """Test resetting unwrapper."""
        unwrapper = SequenceNumberUnwrapper()
        unwrapper.unwrap(65535)
        unwrapper.unwrap(0)  # Should be 65536

        unwrapper.reset()
        self.assertEqual(unwrapper.unwrap(100), 100)  # Back to initial state
```

---

### Step 1.4: Packet Chunk Encoder

**File:** `src/aiortc/twcc.py`

**Reference:** Pion `twcc.go:200-400`

**What it does:** Encodes packet status (received/not received) into compact chunk format.

#### Implementation

```python
# Add to src/aiortc/twcc.py

from dataclasses import dataclass
from enum import IntEnum
from typing import List
import struct


class PacketStatus(IntEnum):
    """
    Packet status symbols for TWCC feedback.

    Reference: draft-holmer-rmcat-transport-wide-cc-extensions-01 Section 3.1.4
    """
    NOT_RECEIVED = 0  # Packet not received
    RECEIVED_SMALL_DELTA = 1  # Received, delta fits in 1 byte
    RECEIVED_LARGE_DELTA = 2  # Received, delta needs 2 bytes
    RESERVED = 3  # Reserved for future use


@dataclass
class PacketInfo:
    """Information about a packet for TWCC feedback."""
    sequence_number: int  # Transport-wide sequence number
    received: bool  # Whether packet was received
    delta_us: int = 0  # Receive delta in microseconds (if received)


class PacketChunkEncoder:
    """
    Encodes packet status into TWCC packet chunks.

    Reference: pion/interceptor/pkg/twcc/twcc.go:200-350

    Two chunk types:
    1. Run-Length: Encodes N packets with same status (up to 8191)
    2. Status Vector: Encodes 7-14 packets with individual statuses
    """

    # Constants from spec
    MAX_RUN_LENGTH = 0x1FFF  # 13 bits = 8191
    MAX_ONE_BIT_ELEMENTS = 14  # 14x 1-bit statuses in status vector
    MAX_TWO_BIT_ELEMENTS = 7   # 7x 2-bit statuses in status vector

    @staticmethod
    def encode_chunks(packets: List[PacketInfo]) -> bytes:
        """
        Encode packet statuses into chunks.

        Args:
            packets: List of packet information

        Returns:
            Encoded chunks as bytes

        Reference: pion twcc.go:marshal()
        """
        if not packets:
            return b""

        chunks = bytearray()
        i = 0

        while i < len(packets):
            # Try run-length encoding first
            run_length, status = PacketChunkEncoder._try_run_length(packets, i)

            if run_length >= 7:  # Run-length is efficient
                chunk = PacketChunkEncoder._encode_run_length_chunk(status, run_length)
                chunks.extend(chunk)
                i += run_length
            else:
                # Use status vector instead
                vector_len, chunk = PacketChunkEncoder._encode_status_vector_chunk(packets, i)
                chunks.extend(chunk)
                i += vector_len

        return bytes(chunks)

    @staticmethod
    def _try_run_length(packets: List[PacketInfo], start: int) -> tuple[int, PacketStatus]:
        """
        Check how many consecutive packets have the same status.

        Returns:
            (run_length, status)
        """
        if start >= len(packets):
            return 0, PacketStatus.NOT_RECEIVED

        first_status = PacketChunkEncoder._get_status(packets[start])
        run_length = 1

        for i in range(start + 1, len(packets)):
            if PacketChunkEncoder._get_status(packets[i]) != first_status:
                break
            run_length += 1
            if run_length >= PacketChunkEncoder.MAX_RUN_LENGTH:
                break

        return run_length, first_status

    @staticmethod
    def _get_status(packet: PacketInfo) -> PacketStatus:
        """Determine packet status (for run-length and status vector)."""
        if not packet.received:
            return PacketStatus.NOT_RECEIVED

        # Check if delta fits in small (1 byte) or large (2 byte) encoding
        # Small delta: 0-63.75ms in 250us units = 0-255
        if 0 <= packet.delta_us < 64000:  # 64ms
            return PacketStatus.RECEIVED_SMALL_DELTA
        else:
            return PacketStatus.RECEIVED_LARGE_DELTA

    @staticmethod
    def _encode_run_length_chunk(status: PacketStatus, run_length: int) -> bytes:
        """
        Encode run-length chunk.

        Format (16 bits):
         0                   1
         0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5
        +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
        |T|S|       Run Length          |
        +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+

        T = 0 (run-length type)
        S = 2-bit status symbol
        Run Length = 13-bit length (1-8191)

        Reference: draft-holmer-rmcat-transport-wide-cc-extensions-01 Section 3.1.4
        """
        run_length = min(run_length, PacketChunkEncoder.MAX_RUN_LENGTH)

        # T=0, S=status (2 bits), run_length (13 bits)
        chunk_value = (0 << 15) | ((status & 0x3) << 13) | (run_length & 0x1FFF)

        return struct.pack("!H", chunk_value)

    @staticmethod
    def _encode_status_vector_chunk(packets: List[PacketInfo], start: int) -> tuple[int, bytes]:
        """
        Encode status vector chunk.

        Format (16 bits):
         0                   1
         0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5
        +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
        |T|S|       symbol list          |
        +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+

        T = 1 (status vector type)
        S = 0: 14x 1-bit symbols (received/not)
        S = 1: 7x 2-bit symbols (full status)

        Returns:
            (number_of_packets_encoded, chunk_bytes)
        """
        # Determine if we can use 1-bit encoding (all packets are received or not-received)
        can_use_1bit = True
        count = 0

        for i in range(start, min(start + PacketChunkEncoder.MAX_ONE_BIT_ELEMENTS, len(packets))):
            status = PacketChunkEncoder._get_status(packets[i])
            if status not in (PacketStatus.NOT_RECEIVED, PacketStatus.RECEIVED_SMALL_DELTA):
                can_use_1bit = False
                break
            count += 1

        if can_use_1bit and count > 0:
            # 1-bit status vector
            chunk_value = (1 << 15) | (0 << 14)  # T=1, S=0

            for i in range(count):
                packet = packets[start + i]
                bit = 1 if packet.received else 0
                chunk_value |= (bit << (13 - i))

            return count, struct.pack("!H", chunk_value)
        else:
            # 2-bit status vector
            count = min(PacketChunkEncoder.MAX_TWO_BIT_ELEMENTS, len(packets) - start)
            chunk_value = (1 << 15) | (1 << 14)  # T=1, S=1

            for i in range(count):
                status = PacketChunkEncoder._get_status(packets[start + i])
                chunk_value |= ((status & 0x3) << (12 - i * 2))

            return count, struct.pack("!H", chunk_value)
```

#### Testing

```python
# Add to tests/test_twcc.py

class TestPacketChunkEncoder(unittest.TestCase):
    """Test packet chunk encoding."""

    def test_run_length_not_received(self):
        """Test encoding run-length chunk for not-received packets."""
        packets = [
            PacketInfo(100 + i, received=False)
            for i in range(10)
        ]

        chunks = PacketChunkEncoder.encode_chunks(packets)

        # Should be one run-length chunk: T=0, S=00 (not received), len=10
        self.assertEqual(len(chunks), 2)
        chunk_value = struct.unpack("!H", chunks)[0]

        t_bit = (chunk_value >> 15) & 0x1
        s_bits = (chunk_value >> 13) & 0x3
        run_len = chunk_value & 0x1FFF

        self.assertEqual(t_bit, 0)  # Run-length type
        self.assertEqual(s_bits, PacketStatus.NOT_RECEIVED)
        self.assertEqual(run_len, 10)

    def test_run_length_received(self):
        """Test encoding run-length chunk for received packets."""
        packets = [
            PacketInfo(100 + i, received=True, delta_us=1000)  # Small delta
            for i in range(20)
        ]

        chunks = PacketChunkEncoder.encode_chunks(packets)

        self.assertEqual(len(chunks), 2)
        chunk_value = struct.unpack("!H", chunks)[0]

        t_bit = (chunk_value >> 15) & 0x1
        s_bits = (chunk_value >> 13) & 0x3
        run_len = chunk_value & 0x1FFF

        self.assertEqual(t_bit, 0)
        self.assertEqual(s_bits, PacketStatus.RECEIVED_SMALL_DELTA)
        self.assertEqual(run_len, 20)

    def test_status_vector_1bit(self):
        """Test 1-bit status vector encoding."""
        packets = [
            PacketInfo(100, received=True, delta_us=1000),
            PacketInfo(101, received=False),
            PacketInfo(102, received=True, delta_us=2000),
            PacketInfo(103, received=False),
            PacketInfo(104, received=True, delta_us=3000),
        ]

        chunks = PacketChunkEncoder.encode_chunks(packets)

        self.assertEqual(len(chunks), 2)
        chunk_value = struct.unpack("!H", chunks)[0]

        t_bit = (chunk_value >> 15) & 0x1
        s_bit = (chunk_value >> 14) & 0x1

        self.assertEqual(t_bit, 1)  # Status vector type
        self.assertEqual(s_bit, 0)  # 1-bit symbols

        # Check individual bits (MSB first after T and S bits)
        # Packets: R, N, R, N, R = 10101 (in first 5 bits after TS)
        symbols = (chunk_value >> 9) & 0x1F  # Get 5 bits
        self.assertEqual(symbols, 0b10101)

    def test_status_vector_2bit(self):
        """Test 2-bit status vector encoding."""
        packets = [
            PacketInfo(100, received=True, delta_us=1000),      # SMALL
            PacketInfo(101, received=True, delta_us=100000),    # LARGE
            PacketInfo(102, received=False),                     # NOT_RECEIVED
        ]

        chunks = PacketChunkEncoder.encode_chunks(packets)

        chunk_value = struct.unpack("!H", chunks)[0]

        t_bit = (chunk_value >> 15) & 0x1
        s_bit = (chunk_value >> 14) & 0x1

        self.assertEqual(t_bit, 1)  # Status vector
        self.assertEqual(s_bit, 1)  # 2-bit symbols

        # Extract 2-bit symbols (3 packets = 6 bits)
        sym0 = (chunk_value >> 12) & 0x3
        sym1 = (chunk_value >> 10) & 0x3
        sym2 = (chunk_value >> 8) & 0x3

        self.assertEqual(sym0, PacketStatus.RECEIVED_SMALL_DELTA)
        self.assertEqual(sym1, PacketStatus.RECEIVED_LARGE_DELTA)
        self.assertEqual(sym2, PacketStatus.NOT_RECEIVED)

    def test_mixed_chunks(self):
        """Test encoding mix of run-length and status vector."""
        packets = []

        # 10 not-received (will be run-length)
        for i in range(10):
            packets.append(PacketInfo(100 + i, received=False))

        # Mixed pattern (will be status vector)
        packets.append(PacketInfo(110, received=True, delta_us=1000))
        packets.append(PacketInfo(111, received=False))
        packets.append(PacketInfo(112, received=True, delta_us=2000))

        chunks = PacketChunkEncoder.encode_chunks(packets)

        # Should have 2 chunks: run-length (2 bytes) + status vector (2 bytes)
        self.assertEqual(len(chunks), 4)

    def test_empty_packets(self):
        """Test encoding empty packet list."""
        chunks = PacketChunkEncoder.encode_chunks([])
        self.assertEqual(chunks, b"")
```

---

### Step 1.5: Receive Delta Encoder

**File:** `src/aiortc/twcc.py`

**Reference:** Pion `twcc.go:400-500`

**What it does:** Encodes packet arrival time deltas into compact format.

#### Implementation

```python
# Add to src/aiortc/twcc.py

class ReceiveDeltaEncoder:
    """
    Encodes receive deltas for TWCC feedback.

    Reference: draft-holmer-rmcat-transport-wide-cc-extensions-01 Section 3.1.5

    Deltas are encoded as:
    - Small delta (1 byte): 0-63.75ms in 250us units
    - Large delta (2 bytes): -8192 to +8191.75ms in 250us units (signed)
    """

    DELTA_SCALE_US = 250  # Delta tick is 250 microseconds
    SMALL_DELTA_MAX_US = 63750  # Max small delta: 255 * 250us = 63.75ms
    LARGE_DELTA_MIN_US = -8192000  # Min large delta: -32768 * 250us
    LARGE_DELTA_MAX_US = 8191750   # Max large delta: 32767 * 250us

    @staticmethod
    def encode_deltas(packets: List[PacketInfo]) -> bytes:
        """
        Encode receive deltas for received packets.

        Args:
            packets: List of packets (only received packets have deltas)

        Returns:
            Encoded deltas as bytes

        Reference: pion twcc.go:marshalDeltas()
        """
        deltas = bytearray()

        for packet in packets:
            if not packet.received:
                continue  # Skip not-received packets

            delta_us = packet.delta_us

            # Determine if small or large delta
            if 0 <= delta_us < ReceiveDeltaEncoder.SMALL_DELTA_MAX_US:
                # Small delta (1 byte)
                delta_ticks = delta_us // ReceiveDeltaEncoder.DELTA_SCALE_US
                deltas.append(delta_ticks & 0xFF)
            else:
                # Large delta (2 bytes, signed)
                delta_ticks = delta_us // ReceiveDeltaEncoder.DELTA_SCALE_US

                # Clamp to valid range
                delta_ticks = max(-32768, min(32767, delta_ticks))

                # Pack as signed 16-bit big-endian
                deltas.extend(struct.pack("!h", delta_ticks))

        return bytes(deltas)

    @staticmethod
    def compute_deltas(packets: List[PacketInfo], reference_time_us: int) -> None:
        """
        Compute receive deltas relative to reference time.

        Modifies packet.delta_us in place.

        Args:
            packets: List of packets with arrival times
            reference_time_us: Reference time in microseconds

        Reference: pion twcc.go:buildFeedbackPacket()
        """
        cumulative_time_us = reference_time_us

        for packet in packets:
            if not packet.received:
                packet.delta_us = 0
                continue

            # Delta from last packet
            # packet should have arrival_time_us attribute
            if hasattr(packet, 'arrival_time_us'):
                packet.delta_us = packet.arrival_time_us - cumulative_time_us
                cumulative_time_us = packet.arrival_time_us
            else:
                packet.delta_us = 0
```

#### Testing

```python
# Add to tests/test_twcc.py

class TestReceiveDeltaEncoder(unittest.TestCase):
    """Test receive delta encoding."""

    def test_small_delta_encoding(self):
        """Test encoding small deltas (1 byte)."""
        packets = [
            PacketInfo(100, received=True, delta_us=0),        # 0ms
            PacketInfo(101, received=True, delta_us=250),      # 250us = 1 tick
            PacketInfo(102, received=True, delta_us=1000),     # 1ms = 4 ticks
            PacketInfo(103, received=True, delta_us=10000),    # 10ms = 40 ticks
            PacketInfo(104, received=True, delta_us=63750),    # 63.75ms = 255 ticks (max)
        ]

        deltas = ReceiveDeltaEncoder.encode_deltas(packets)

        # Should be 5 bytes (all small deltas)
        self.assertEqual(len(deltas), 5)
        self.assertEqual(deltas[0], 0)      # 0 / 250
        self.assertEqual(deltas[1], 1)      # 250 / 250
        self.assertEqual(deltas[2], 4)      # 1000 / 250
        self.assertEqual(deltas[3], 40)     # 10000 / 250
        self.assertEqual(deltas[4], 255)    # 63750 / 250

    def test_large_delta_encoding(self):
        """Test encoding large deltas (2 bytes)."""
        packets = [
            PacketInfo(100, received=True, delta_us=64000),    # > small max, needs 2 bytes
            PacketInfo(101, received=True, delta_us=100000),   # 100ms
            PacketInfo(102, received=True, delta_us=-1000),    # Negative delta
        ]

        deltas = ReceiveDeltaEncoder.encode_deltas(packets)

        # Should be 6 bytes (3 large deltas)
        self.assertEqual(len(deltas), 6)

        # Parse deltas
        delta0 = struct.unpack("!h", deltas[0:2])[0]
        delta1 = struct.unpack("!h", deltas[2:4])[0]
        delta2 = struct.unpack("!h", deltas[4:6])[0]

        self.assertEqual(delta0, 64000 // 250)    # 256 ticks
        self.assertEqual(delta1, 100000 // 250)   # 400 ticks
        self.assertEqual(delta2, -1000 // 250)    # -4 ticks

    def test_skip_not_received(self):
        """Test that not-received packets don't generate deltas."""
        packets = [
            PacketInfo(100, received=True, delta_us=1000),
            PacketInfo(101, received=False),  # Should be skipped
            PacketInfo(102, received=True, delta_us=2000),
        ]

        deltas = ReceiveDeltaEncoder.encode_deltas(packets)

        # Should be 2 bytes (only 2 received packets)
        self.assertEqual(len(deltas), 2)

    def test_compute_deltas(self):
        """Test computing deltas from arrival times."""
        # Create packets with arrival times
        packets = [
            PacketInfo(100, received=True),
            PacketInfo(101, received=True),
            PacketInfo(102, received=False),  # Not received
            PacketInfo(103, received=True),
        ]

        # Set arrival times
        packets[0].arrival_time_us = 1000000  # 1s
        packets[1].arrival_time_us = 1005000  # 1.005s
        packets[3].arrival_time_us = 1015000  # 1.015s

        # Compute deltas
        reference_time = 1000000
        ReceiveDeltaEncoder.compute_deltas(packets, reference_time)

        self.assertEqual(packets[0].delta_us, 0)      # First packet: 1000000 - 1000000
        self.assertEqual(packets[1].delta_us, 5000)   # 1005000 - 1000000
        self.assertEqual(packets[2].delta_us, 0)      # Not received
        self.assertEqual(packets[3].delta_us, 10000)  # 1015000 - 1005000
```

---

### Step 1.6: RTCP RTPFB Packet Support

**File:** `src/aiortc/rtp.py`

**Reference:** Existing `RtcpPsfbPacket` structure

**What it does:** Add support for RTCP Transport Feedback (RTPFB) packets.

#### Implementation

```python
# Add to src/aiortc/rtp.py (after RtcpPsfbPacket definition)

# Add constant for TWCC
RTCP_RTPFB_TWCC = 15  # Transport-wide CC feedback


@dataclass
class RtcpRtpfbPacket:
    """
    RTCP RTP Feedback (RTPFB) packet.

    Used for Transport-Wide Congestion Control feedback.

    Reference: RFC 4585 Section 6.3
    """
    fmt: int  # Feedback message type (15 for TWCC)
    ssrc: int  # SSRC of packet sender (receiver)
    media_ssrc: int  # SSRC of media source
    fci: bytes = b""  # Feedback Control Information

    def __bytes__(self) -> bytes:
        """Encode RTPFB packet to bytes."""
        payload = struct.pack("!LL", self.ssrc, self.media_ssrc)
        payload += self.fci

        # Add padding if needed
        padding_size = padl(len(payload))
        if padding_size:
            payload += b"\x00" * padding_size

        return pack_rtcp_packet(RTCP_RTPFB, self.fmt, payload)

    @classmethod
    def parse(cls, data: bytes) -> "RtcpRtpfbPacket":
        """
        Parse RTPFB packet from bytes.

        Args:
            data: RTCP packet data (including header)

        Returns:
            Parsed RtcpRtpfbPacket
        """
        if len(data) < RTCP_HEADER_LENGTH + 8:
            raise ValueError("RTCP RTPFB packet is too short")

        # Parse header
        v_p_count, pt, length_words = struct.unpack("!BBH", data[0:4])
        fmt = v_p_count & 0x1F

        if pt != RTCP_RTPFB:
            raise ValueError(f"Expected RTCP RTPFB (205), got {pt}")

        # Parse SSRCs
        ssrc, media_ssrc = struct.unpack("!LL", data[4:12])

        # Extract FCI (rest of packet)
        fci_length = (length_words + 1) * 4 - 8  # Total length - SSRCs
        fci = data[12:12 + fci_length]

        # Remove padding
        if v_p_count & 0x20:  # Padding bit set
            if fci:
                padding_length = fci[-1]
                fci = fci[:-padding_length]

        return cls(fmt=fmt, ssrc=ssrc, media_ssrc=media_ssrc, fci=fci)


def pack_twcc_fci(
    base_seq: int,
    packet_status_count: int,
    reference_time: int,
    fb_pkt_count: int,
    packet_chunks: bytes,
    recv_deltas: bytes,
) -> bytes:
    """
    Pack FCI for Transport-Wide Congestion Control feedback.

    Reference: draft-holmer-rmcat-transport-wide-cc-extensions-01 Section 3.1

    Args:
        base_seq: Base sequence number (16-bit)
        packet_status_count: Number of packets in this feedback (16-bit)
        reference_time: Reference time in 64ms units (24-bit)
        fb_pkt_count: Feedback packet count (8-bit)
        packet_chunks: Encoded packet status chunks
        recv_deltas: Encoded receive deltas

    Returns:
        FCI bytes
    """
    fci = bytearray()

    # Base sequence number (16 bits) + packet status count (16 bits)
    fci.extend(struct.pack("!HH", base_seq & 0xFFFF, packet_status_count & 0xFFFF))

    # Reference time (24 bits) + feedback packet count (8 bits)
    fci.extend(struct.pack("!I", ((reference_time & 0xFFFFFF) << 8) | (fb_pkt_count & 0xFF)))

    # Packet chunks
    fci.extend(packet_chunks)

    # Receive deltas
    fci.extend(recv_deltas)

    # Pad to 4-byte boundary
    padding = padl(len(fci))
    if padding:
        fci.extend(b"\x00" * padding)

    return bytes(fci)


def unpack_twcc_fci(fci: bytes) -> dict:
    """
    Unpack FCI for Transport-Wide Congestion Control feedback.

    Args:
        fci: FCI bytes

    Returns:
        Dictionary with parsed fields
    """
    if len(fci) < 8:
        raise ValueError("TWCC FCI too short")

    # Parse header
    base_seq, packet_status_count = struct.unpack("!HH", fci[0:4])
    combined = struct.unpack("!I", fci[4:8])[0]
    reference_time = (combined >> 8) & 0xFFFFFF
    fb_pkt_count = combined & 0xFF

    # Rest is packet chunks and deltas (parsed separately)
    payload = fci[8:]

    return {
        "base_seq": base_seq,
        "packet_status_count": packet_status_count,
        "reference_time": reference_time,
        "fb_pkt_count": fb_pkt_count,
        "payload": payload,
    }
```

#### Testing

```python
# Add to tests/test_rtp.py

class TestRtcpRtpfbPacket(unittest.TestCase):
    """Test RTCP RTPFB packet encoding/decoding."""

    def test_encode_decode(self):
        """Test encoding and decoding RTPFB packet."""
        fci = b"test feedback control information"

        packet = RtcpRtpfbPacket(
            fmt=15,  # TWCC
            ssrc=0x12345678,
            media_ssrc=0x87654321,
            fci=fci,
        )

        # Encode
        data = bytes(packet)

        # Should have proper RTCP header
        v_p_count, pt, _ = struct.unpack("!BBH", data[0:4])
        self.assertEqual((v_p_count >> 6) & 0x3, 2)  # Version 2
        self.assertEqual(pt, RTCP_RTPFB)  # Type 205
        self.assertEqual(v_p_count & 0x1F, 15)  # FMT 15

        # Decode
        decoded = RtcpRtpfbPacket.parse(data)
        self.assertEqual(decoded.fmt, 15)
        self.assertEqual(decoded.ssrc, 0x12345678)
        self.assertEqual(decoded.media_ssrc, 0x87654321)
        self.assertEqual(decoded.fci.rstrip(b"\x00"), fci)  # Strip padding

    def test_pack_twcc_fci(self):
        """Test packing TWCC FCI."""
        fci = pack_twcc_fci(
            base_seq=1000,
            packet_status_count=50,
            reference_time=123456,
            fb_pkt_count=10,
            packet_chunks=b"\x12\x34",
            recv_deltas=b"\x56\x78\x9A",
        )

        # Check header
        base_seq, pkt_count = struct.unpack("!HH", fci[0:4])
        self.assertEqual(base_seq, 1000)
        self.assertEqual(pkt_count, 50)

        combined = struct.unpack("!I", fci[4:8])[0]
        ref_time = (combined >> 8) & 0xFFFFFF
        fb_count = combined & 0xFF
        self.assertEqual(ref_time, 123456)
        self.assertEqual(fb_count, 10)

        # Check chunks and deltas
        self.assertEqual(fci[8:10], b"\x12\x34")
        self.assertEqual(fci[10:13], b"\x56\x78\x9A")

    def test_unpack_twcc_fci(self):
        """Test unpacking TWCC FCI."""
        fci = pack_twcc_fci(
            base_seq=2000,
            packet_status_count=100,
            reference_time=999999,
            fb_pkt_count=255,
            packet_chunks=b"\xAA\xBB",
            recv_deltas=b"\xCC\xDD",
        )

        unpacked = unpack_twcc_fci(fci)

        self.assertEqual(unpacked["base_seq"], 2000)
        self.assertEqual(unpacked["packet_status_count"], 100)
        self.assertEqual(unpacked["reference_time"], 999999)
        self.assertEqual(unpacked["fb_pkt_count"], 255)
        self.assertTrue(unpacked["payload"].startswith(b"\xAA\xBB\xCC\xDD"))
```

---

### Step 1.7: TWCC Recorder (Complete)

**File:** `src/aiortc/twcc.py`

**Reference:** Pion `twcc.go` Recorder struct

**What it does:** Orchestrates packet tracking and feedback generation.

#### Implementation

```python
# Add to src/aiortc/twcc.py

import time


class TWCCRecorder:
    """
    Records incoming packets and generates TWCC feedback.

    Based on: pion/interceptor/pkg/twcc/twcc.go

    This class is used on the receiver side to track packet arrivals
    and generate periodic RTCP feedback messages.
    """

    def __init__(self, sender_ssrc: int, media_ssrc: int = 0) -> None:
        """
        Initialize TWCC recorder.

        Args:
            sender_ssrc: SSRC of this receiver (for RTCP sender SSRC)
            media_ssrc: SSRC of media source (typically 0 for TWCC)
        """
        self._sender_ssrc = sender_ssrc
        self._media_ssrc = media_ssrc

        # Packet tracking
        self._arrival_times = ArrivalTimeMap()
        self._unwrapper = SequenceNumberUnwrapper()

        # Feedback state
        self._start_seq: Optional[int] = None  # First seq in next feedback
        self._fb_pkt_count = 0  # Feedback packet counter (wraps at 256)
        self._reference_time_us: Optional[int] = None
        self._start_time_us = int(time.time() * 1_000_000)  # Microseconds since epoch

    def record(self, transport_seq: int, arrival_time_us: int, ssrc: int) -> None:
        """
        Record a received packet.

        Args:
            transport_seq: Transport-wide sequence number (16-bit)
            arrival_time_us: Arrival timestamp in microseconds
            ssrc: Source SSRC (for tracking multiple streams)

        Reference: pion twcc.go:Record()
        """
        # Unwrap sequence number
        unwrapped_seq = self._unwrapper.unwrap(transport_seq)

        # Check for duplicate
        if self._arrival_times.has(unwrapped_seq):
            return  # Ignore duplicate

        # Store arrival time
        self._arrival_times.add(unwrapped_seq, arrival_time_us)

        # Initialize start sequence if first packet
        if self._start_seq is None:
            self._start_seq = unwrapped_seq

        # Initialize reference time if needed
        if self._reference_time_us is None:
            self._reference_time_us = arrival_time_us

    def build_feedback_packet(self) -> Optional[bytes]:
        """
        Build a TWCC feedback packet.

        Returns:
            Complete RTCP RTPFB packet bytes, or None if no packets to report

        Reference: pion twcc.go:BuildFeedbackPacket()
        """
        if self._start_seq is None or self._reference_time_us is None:
            return None  # No packets recorded yet

        # Find the range of packets to include
        end_seq = self._arrival_times.get_latest_sequence()
        if end_seq is None:
            return None

        # Limit packet count to avoid huge feedback packets
        max_packets = 1000
        if end_seq - self._start_seq > max_packets:
            end_seq = self._start_seq + max_packets

        # Build packet list
        packets = []
        for seq in range(self._start_seq, end_seq + 1):
            arrival_time = self._arrival_times.get(seq)

            if arrival_time is not None:
                # Create PacketInfo with arrival time for delta computation
                pkt = PacketInfo(seq, received=True)
                pkt.arrival_time_us = arrival_time
                packets.append(pkt)
            else:
                # Packet not received
                packets.append(PacketInfo(seq, received=False))

        if not packets:
            return None

        # Compute deltas
        ReceiveDeltaEncoder.compute_deltas(packets, self._reference_time_us)

        # Encode chunks and deltas
        packet_chunks = PacketChunkEncoder.encode_chunks(packets)
        recv_deltas = ReceiveDeltaEncoder.encode_deltas(packets)

        # Convert reference time to 64ms units
        # Reference time is microseconds since first packet
        reference_time_64ms = (self._reference_time_us - self._start_time_us) // 64000

        # Build FCI
        from .rtp import pack_twcc_fci

        fci = pack_twcc_fci(
            base_seq=self._start_seq & 0xFFFF,
            packet_status_count=len(packets),
            reference_time=reference_time_64ms & 0xFFFFFF,
            fb_pkt_count=self._fb_pkt_count,
            packet_chunks=packet_chunks,
            recv_deltas=recv_deltas,
        )

        # Create RTCP packet
        from .rtp import RtcpRtpfbPacket, RTCP_RTPFB_TWCC

        rtcp_packet = RtcpRtpfbPacket(
            fmt=RTCP_RTPFB_TWCC,
            ssrc=self._sender_ssrc,
            media_ssrc=self._media_ssrc,
            fci=fci,
        )

        # Update state for next feedback
        self._start_seq = end_seq + 1
        self._fb_pkt_count = (self._fb_pkt_count + 1) & 0xFF

        # Update reference time to last received packet
        last_received = max(
            (p.arrival_time_us for p in packets if p.received and hasattr(p, 'arrival_time_us')),
            default=self._reference_time_us
        )
        self._reference_time_us = last_received

        return bytes(rtcp_packet)

    def reset(self) -> None:
        """Reset recorder state."""
        self._arrival_times.clear()
        self._unwrapper.reset()
        self._start_seq = None
        self._fb_pkt_count = 0
        self._reference_time_us = None
        self._start_time_us = int(time.time() * 1_000_000)
```

#### Testing

```python
# Add to tests/test_twcc.py

class TestTWCCRecorder(unittest.TestCase):
    """Test TWCC recorder."""

    def test_record_packet(self):
        """Test recording a single packet."""
        recorder = TWCCRecorder(sender_ssrc=0x12345678)

        # Record packet
        recorder.record(
            transport_seq=100,
            arrival_time_us=1000000,
            ssrc=0xAABBCCDD
        )

        # Should be able to build feedback
        feedback = recorder.build_feedback_packet()
        self.assertIsNotNone(feedback)

    def test_record_multiple_packets(self):
        """Test recording multiple packets."""
        recorder = TWCCRecorder(sender_ssrc=0x12345678)

        base_time = 1000000  # 1 second

        # Record 10 packets with increasing time
        for i in range(10):
            recorder.record(
                transport_seq=100 + i,
                arrival_time_us=base_time + i * 1000,  # 1ms apart
                ssrc=0xAABBCCDD
            )

        feedback = recorder.build_feedback_packet()
        self.assertIsNotNone(feedback)

        # Parse feedback
        from aiortc.rtp import RtcpRtpfbPacket, unpack_twcc_fci

        rtcp_pkt = RtcpRtpfbPacket.parse(feedback)
        self.assertEqual(rtcp_pkt.fmt, 15)  # TWCC

        fci = unpack_twcc_fci(rtcp_pkt.fci)
        self.assertEqual(fci["base_seq"], 100)
        self.assertEqual(fci["packet_status_count"], 10)

    def test_missing_packets(self):
        """Test handling missing packets."""
        recorder = TWCCRecorder(sender_ssrc=0x12345678)

        base_time = 1000000

        # Record packets with gaps
        recorder.record(100, base_time, 0xAABBCCDD)
        recorder.record(101, base_time + 1000, 0xAABBCCDD)
        # Skip 102
        recorder.record(103, base_time + 3000, 0xAABBCCDD)
        # Skip 104-105
        recorder.record(106, base_time + 6000, 0xAABBCCDD)

        feedback = recorder.build_feedback_packet()
        self.assertIsNotNone(feedback)

        from aiortc.rtp import unpack_twcc_fci
        rtcp_pkt = RtcpRtpfbPacket.parse(feedback)
        fci = unpack_twcc_fci(rtcp_pkt.fci)

        # Should report all 7 packets (received + missing)
        self.assertEqual(fci["packet_status_count"], 7)

    def test_sequence_wraparound(self):
        """Test handling sequence number wraparound."""
        recorder = TWCCRecorder(sender_ssrc=0x12345678)

        base_time = 1000000

        # Record packets around wraparound
        recorder.record(65534, base_time, 0xAABBCCDD)
        recorder.record(65535, base_time + 1000, 0xAABBCCDD)
        recorder.record(0, base_time + 2000, 0xAABBCCDD)  # Wrapped
        recorder.record(1, base_time + 3000, 0xAABBCCDD)

        feedback = recorder.build_feedback_packet()
        self.assertIsNotNone(feedback)

    def test_ignore_duplicate(self):
        """Test that duplicate packets are ignored."""
        recorder = TWCCRecorder(sender_ssrc=0x12345678)

        recorder.record(100, 1000000, 0xAABBCCDD)
        recorder.record(100, 1001000, 0xAABBCCDD)  # Duplicate, different time

        # Should only count once
        feedback = recorder.build_feedback_packet()
        from aiortc.rtp import unpack_twcc_fci, RtcpRtpfbPacket

        rtcp_pkt = RtcpRtpfbPacket.parse(feedback)
        fci = unpack_twcc_fci(rtcp_pkt.fci)
        self.assertEqual(fci["packet_status_count"], 1)

    def test_multiple_feedback_packets(self):
        """Test generating multiple feedback packets."""
        recorder = TWCCRecorder(sender_ssrc=0x12345678)

        # First batch
        for i in range(10):
            recorder.record(100 + i, 1000000 + i * 1000, 0xAABBCCDD)

        feedback1 = recorder.build_feedback_packet()
        self.assertIsNotNone(feedback1)

        # Second batch
        for i in range(10):
            recorder.record(110 + i, 1020000 + i * 1000, 0xAABBCCDD)

        feedback2 = recorder.build_feedback_packet()
        self.assertIsNotNone(feedback2)

        # Check feedback packet counters
        from aiortc.rtp import RtcpRtpfbPacket, unpack_twcc_fci

        fci1 = unpack_twcc_fci(RtcpRtpfbPacket.parse(feedback1).fci)
        fci2 = unpack_twcc_fci(RtcpRtpfbPacket.parse(feedback2).fci)

        self.assertEqual(fci1["fb_pkt_count"], 0)
        self.assertEqual(fci2["fb_pkt_count"], 1)
```

---

### Step 1.8: Receiver Integration

**File:** `src/aiortc/rtcrtpreceiver.py`

**Reference:** Pion `sender_interceptor.go`

**What it does:** Integrate TWCC recording into receiver packet handling.

#### Implementation

```python
# Modifications to src/aiortc/rtcrtpreceiver.py

# Add imports at top
from .twcc import TWCCRecorder
import time

# In RTCRtpReceiver.__init__(), add:
self.__twcc_recorder: Optional[TWCCRecorder] = None
self.__twcc_last_feedback_time = 0
self.__twcc_feedback_interval = 0.1  # 100ms default

# Add new method to RTCRtpReceiver:
def _enable_twcc(self, sender_ssrc: int) -> None:
    """
    Enable Transport-Wide Congestion Control feedback.

    Args:
        sender_ssrc: SSRC to use as sender in RTCP packets
    """
    self.__twcc_recorder = TWCCRecorder(sender_ssrc=sender_ssrc)
    self.__log_debug("TWCC feedback enabled")

# Modify _handle_rtp_packet method (around line 451):
async def _handle_rtp_packet(self, packet: RtpPacket, arrival_time_ms: int) -> None:
    """
    Handle an incoming RTP packet.
    """
    self.__log_debug("< %s", packet)

    # If the receiver is disabled, discard the packet.
    if not self._enabled:
        return

    # TWCC feedback (if enabled)
    if self.__twcc_recorder is not None:
        if packet.extensions.transport_sequence_number is not None:
            # Record packet arrival
            arrival_time_us = int(time.time() * 1_000_000)

            self.__twcc_recorder.record(
                transport_seq=packet.extensions.transport_sequence_number,
                arrival_time_us=arrival_time_us,
                ssrc=packet.ssrc,
            )

            # Check if it's time to send feedback
            now = time.time()
            if now - self.__twcc_last_feedback_time >= self.__twcc_feedback_interval:
                feedback = self.__twcc_recorder.build_feedback_packet()
                if feedback is not None:
                    # Send RTCP RTPFB packet
                    # We need to send raw bytes here
                    await self._send_rtcp_raw(feedback)
                    self.__twcc_last_feedback_time = now

    # ... rest of existing code (REMB, NACK, etc.) ...

# Add new helper method:
async def _send_rtcp_raw(self, data: bytes) -> None:
    """
    Send raw RTCP packet bytes.

    Args:
        data: Complete RTCP packet bytes
    """
    try:
        await self._RTCRtpReceiver__transport._send_rtp(data)
    except Exception:
        pass  # Ignore errors in feedback transmission
```

#### Testing

**File:** `tests/test_twcc_integration.py` (new file)

```python
# tests/test_twcc_integration.py

import asyncio
import unittest
from unittest.mock import Mock, patch
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.rtp import RtpPacket, HeaderExtensions
from aiortc.rtcrtpreceiver import RTCRtpReceiver


class TestTWCCReceiverIntegration(unittest.TestCase):
    """Integration tests for TWCC in RTCRtpReceiver."""

    def test_twcc_enable(self):
        """Test enabling TWCC feedback."""
        # This would need proper transport setup
        # For now, basic structural test
        pass  # TODO: Implement with proper test harness

    # More integration tests to follow in Phase 5


if __name__ == "__main__":
    unittest.main()
```

---

## Phase 1 Summary

### Deliverables

✅ Transport sequence number manager
✅ Arrival time tracking with windowing
✅ Sequence number unwrapping (handle 16-bit wraparound)
✅ Packet chunk encoding (run-length + status vector)
✅ Receive delta encoding (small + large deltas)
✅ RTCP RTPFB packet support
✅ TWCC recorder (complete receiver-side)
✅ Integration into RTCRtpReceiver

### Test Coverage

- Unit tests for each component (~400 lines)
- Edge case handling (wraparound, missing packets, duplicates)
- Format validation

### Estimated Duration

**2-3 weeks** for implementation + testing

### Next Phase

Phase 2: TWCC Feedback Parsing (Sender Side)

---

## PHASE 2: TWCC Feedback Parsing

**Goal:** Sender can receive and parse TWCC feedback packets

**Duration:** 1-2 weeks

**Reference:** Need to reverse Pion encoding, reference libwebrtc parsing

---

### Step 2.1: Packet Chunk Decoder

**File:** `src/aiortc/twcc.py`

**What it does:** Decodes packet status chunks back into individual packet statuses.

#### Implementation

```python
# Add to src/aiortc/twcc.py

from typing import List, Tuple


class PacketChunkDecoder:
    """
    Decodes TWCC packet status chunks.

    Inverse of PacketChunkEncoder.
    """

    @staticmethod
    def decode_chunks(chunks: bytes, packet_count: int) -> List[PacketStatus]:
        """
        Decode packet status chunks.

        Args:
            chunks: Encoded chunk bytes
            packet_count: Expected number of packet statuses

        Returns:
            List of packet statuses

        Raises:
            ValueError: If chunks are malformed
        """
        statuses = []
        offset = 0

        while len(statuses) < packet_count and offset < len(chunks):
            if offset + 2 > len(chunks):
                raise ValueError("Incomplete chunk")

            # Read 16-bit chunk
            chunk_value = struct.unpack("!H", chunks[offset:offset + 2])[0]
            offset += 2

            # Check chunk type (bit 15)
            t_bit = (chunk_value >> 15) & 0x1

            if t_bit == 0:
                # Run-length chunk
                decoded = PacketChunkDecoder._decode_run_length_chunk(chunk_value)
                statuses.extend(decoded)
            else:
                # Status vector chunk
                decoded = PacketChunkDecoder._decode_status_vector_chunk(chunk_value)
                statuses.extend(decoded)

        # Trim to exact count
        return statuses[:packet_count]

    @staticmethod
    def _decode_run_length_chunk(chunk_value: int) -> List[PacketStatus]:
        """Decode run-length chunk."""
        # Extract fields
        s_bits = (chunk_value >> 13) & 0x3  # Status symbol
        run_length = chunk_value & 0x1FFF  # Length

        status = PacketStatus(s_bits)

        return [status] * run_length

    @staticmethod
    def _decode_status_vector_chunk(chunk_value: int) -> List[PacketStatus]:
        """Decode status vector chunk."""
        # Check if 1-bit or 2-bit (bit 14)
        s_bit = (chunk_value >> 14) & 0x1

        statuses = []

        if s_bit == 0:
            # 1-bit status vector (14 symbols)
            for i in range(14):
                bit = (chunk_value >> (13 - i)) & 0x1

                if bit == 1:
                    statuses.append(PacketStatus.RECEIVED_SMALL_DELTA)
                else:
                    statuses.append(PacketStatus.NOT_RECEIVED)
        else:
            # 2-bit status vector (7 symbols)
            for i in range(7):
                symbol = (chunk_value >> (12 - i * 2)) & 0x3
                statuses.append(PacketStatus(symbol))

        return statuses


# Testing
class TestPacketChunkDecoder(unittest.TestCase):
    """Test packet chunk decoding."""

    def test_decode_run_length(self):
        """Test decoding run-length chunk."""
        # Encode then decode
        packets = [PacketInfo(100 + i, received=False) for i in range(20)]
        chunks = PacketChunkEncoder.encode_chunks(packets)

        # Decode
        statuses = PacketChunkDecoder.decode_chunks(chunks, 20)

        self.assertEqual(len(statuses), 20)
        for status in statuses:
            self.assertEqual(status, PacketStatus.NOT_RECEIVED)

    def test_decode_status_vector_1bit(self):
        """Test decoding 1-bit status vector."""
        packets = [
            PacketInfo(100, received=True, delta_us=1000),
            PacketInfo(101, received=False),
            PacketInfo(102, received=True, delta_us=2000),
        ]
        chunks = PacketChunkEncoder.encode_chunks(packets)

        statuses = PacketChunkDecoder.decode_chunks(chunks, 3)

        self.assertEqual(len(statuses), 3)
        self.assertEqual(statuses[0], PacketStatus.RECEIVED_SMALL_DELTA)
        self.assertEqual(statuses[1], PacketStatus.NOT_RECEIVED)
        self.assertEqual(statuses[2], PacketStatus.RECEIVED_SMALL_DELTA)

    def test_decode_status_vector_2bit(self):
        """Test decoding 2-bit status vector."""
        packets = [
            PacketInfo(100, received=True, delta_us=1000),
            PacketInfo(101, received=True, delta_us=100000),
            PacketInfo(102, received=False),
        ]
        chunks = PacketChunkEncoder.encode_chunks(packets)

        statuses = PacketChunkDecoder.decode_chunks(chunks, 3)

        self.assertEqual(len(statuses), 3)
        self.assertEqual(statuses[0], PacketStatus.RECEIVED_SMALL_DELTA)
        self.assertEqual(statuses[1], PacketStatus.RECEIVED_LARGE_DELTA)
        self.assertEqual(statuses[2], PacketStatus.NOT_RECEIVED)

    def test_encode_decode_roundtrip(self):
        """Test encoding then decoding produces same result."""
        original_packets = []

        # Mix of received and not-received
        for i in range(50):
            if i % 3 == 0:
                original_packets.append(PacketInfo(i, received=False))
            elif i % 3 == 1:
                original_packets.append(PacketInfo(i, received=True, delta_us=1000))
            else:
                original_packets.append(PacketInfo(i, received=True, delta_us=100000))

        # Encode
        chunks = PacketChunkEncoder.encode_chunks(original_packets)

        # Decode
        decoded_statuses = PacketChunkDecoder.decode_chunks(chunks, len(original_packets))

        # Verify
        self.assertEqual(len(decoded_statuses), len(original_packets))

        for i, (orig, decoded) in enumerate(zip(original_packets, decoded_statuses)):
            expected_status = PacketChunkEncoder._get_status(orig)
            self.assertEqual(decoded, expected_status, f"Mismatch at index {i}")
```

---

### Step 2.2: Receive Delta Decoder

**File:** `src/aiortc/twcc.py`

**What it does:** Decodes receive deltas back into timestamps.

#### Implementation

```python
# Add to src/aiortc/twcc.py

@dataclass
class ReceivedPacketInfo:
    """Information about a received packet parsed from TWCC feedback."""
    sequence_number: int
    received: bool
    arrival_time_us: int = 0  # Absolute arrival time (if received)


class ReceiveDeltaDecoder:
    """
    Decodes receive deltas from TWCC feedback.

    Inverse of ReceiveDeltaEncoder.
    """

    @staticmethod
    def decode_deltas(
        deltas_data: bytes,
        statuses: List[PacketStatus],
        reference_time_us: int
    ) -> List[int]:
        """
        Decode receive deltas to absolute arrival times.

        Args:
            deltas_data: Encoded delta bytes
            statuses: List of packet statuses (to know which are small/large)
            reference_time_us: Reference timestamp in microseconds

        Returns:
            List of absolute arrival times in microseconds

        Raises:
            ValueError: If deltas are malformed
        """
        arrival_times = []
        offset = 0
        cumulative_time_us = reference_time_us

        for status in statuses:
            if status == PacketStatus.NOT_RECEIVED or status == PacketStatus.RESERVED:
                # No delta for not-received packets
                arrival_times.append(0)
                continue

            # Determine delta size
            if status == PacketStatus.RECEIVED_SMALL_DELTA:
                # Small delta (1 byte)
                if offset >= len(deltas_data):
                    raise ValueError("Insufficient delta data for small delta")

                delta_ticks = deltas_data[offset]
                offset += 1

                delta_us = delta_ticks * ReceiveDeltaEncoder.DELTA_SCALE_US

            elif status == PacketStatus.RECEIVED_LARGE_DELTA:
                # Large delta (2 bytes, signed)
                if offset + 2 > len(deltas_data):
                    raise ValueError("Insufficient delta data for large delta")

                delta_ticks = struct.unpack("!h", deltas_data[offset:offset + 2])[0]
                offset += 2

                delta_us = delta_ticks * ReceiveDeltaEncoder.DELTA_SCALE_US

            else:
                raise ValueError(f"Unknown packet status: {status}")

            # Compute absolute arrival time
            cumulative_time_us += delta_us
            arrival_times.append(cumulative_time_us)

        return arrival_times


# Testing
class TestReceiveDeltaDecoder(unittest.TestCase):
    """Test receive delta decoding."""

    def test_decode_small_deltas(self):
        """Test decoding small deltas."""
        # Encode deltas
        packets = [
            PacketInfo(100, received=True, delta_us=0),
            PacketInfo(101, received=True, delta_us=1000),
            PacketInfo(102, received=True, delta_us=5000),
        ]

        deltas_data = ReceiveDeltaEncoder.encode_deltas(packets)

        # Decode
        statuses = [PacketStatus.RECEIVED_SMALL_DELTA] * 3
        reference_time = 1000000  # 1 second
        arrival_times = ReceiveDeltaDecoder.decode_deltas(deltas_data, statuses, reference_time)

        self.assertEqual(len(arrival_times), 3)
        self.assertEqual(arrival_times[0], reference_time)  # 0 delta
        self.assertEqual(arrival_times[1], reference_time + 1000)
        self.assertEqual(arrival_times[2], reference_time + 1000 + 5000)

    def test_decode_large_deltas(self):
        """Test decoding large deltas."""
        packets = [
            PacketInfo(100, received=True, delta_us=100000),  # 100ms
        ]

        deltas_data = ReceiveDeltaEncoder.encode_deltas(packets)

        statuses = [PacketStatus.RECEIVED_LARGE_DELTA]
        reference_time = 2000000
        arrival_times = ReceiveDeltaDecoder.decode_deltas(deltas_data, statuses, reference_time)

        self.assertEqual(len(arrival_times), 1)
        self.assertEqual(arrival_times[0], reference_time + 100000)

    def test_decode_mixed_with_not_received(self):
        """Test decoding with not-received packets."""
        packets = [
            PacketInfo(100, received=True, delta_us=1000),
            PacketInfo(101, received=False),  # Not received
            PacketInfo(102, received=True, delta_us=2000),
        ]

        deltas_data = ReceiveDeltaEncoder.encode_deltas(packets)

        statuses = [
            PacketStatus.RECEIVED_SMALL_DELTA,
            PacketStatus.NOT_RECEIVED,
            PacketStatus.RECEIVED_SMALL_DELTA,
        ]

        reference_time = 3000000
        arrival_times = ReceiveDeltaDecoder.decode_deltas(deltas_data, statuses, reference_time)

        self.assertEqual(len(arrival_times), 3)
        self.assertEqual(arrival_times[0], reference_time + 1000)
        self.assertEqual(arrival_times[1], 0)  # Not received
        self.assertEqual(arrival_times[2], reference_time + 1000 + 2000)

    def test_encode_decode_roundtrip(self):
        """Test encoding then decoding produces same timestamps."""
        base_time = 5000000

        # Create packets with known arrival times
        original_packets = []
        for i in range(10):
            pkt = PacketInfo(100 + i, received=(i % 2 == 0))
            if pkt.received:
                pkt.arrival_time_us = base_time + i * 10000  # 10ms apart
            original_packets.append(pkt)

        # Compute deltas
        ReceiveDeltaEncoder.compute_deltas(original_packets, base_time)

        # Encode
        deltas_data = ReceiveDeltaEncoder.encode_deltas(original_packets)

        # Get statuses
        statuses = [PacketChunkEncoder._get_status(p) for p in original_packets]

        # Decode
        decoded_times = ReceiveDeltaDecoder.decode_deltas(deltas_data, statuses, base_time)

        # Verify
        for orig, decoded_time in zip(original_packets, decoded_times):
            if orig.received:
                # Allow small rounding error due to delta quantization
                expected = orig.arrival_time_us
                error = abs(decoded_time - expected)
                self.assertLess(error, 300, f"Large error: {error}us")
```

---

### Step 2.3: TWCC Parser (Complete)

**File:** `src/aiortc/twcc.py`

**What it does:** Complete parser for TWCC feedback packets.

#### Implementation

```python
# Add to src/aiortc/twcc.py

class TWCCParser:
    """
    Parses TWCC feedback packets received by sender.

    Based on inverse of Pion recorder and draft spec.
    """

    @staticmethod
    def parse_feedback(rtcp_data: bytes) -> List[ReceivedPacketInfo]:
        """
        Parse complete TWCC feedback packet.

        Args:
            rtcp_data: Complete RTCP RTPFB packet bytes

        Returns:
            List of received packet information

        Raises:
            ValueError: If packet is malformed
        """
        from .rtp import RtcpRtpfbPacket, unpack_twcc_fci, RTCP_RTPFB_TWCC

        # Parse RTCP packet
        try:
            rtcp_pkt = RtcpRtpfbPacket.parse(rtcp_data)
        except Exception as e:
            raise ValueError(f"Failed to parse RTCP packet: {e}")

        if rtcp_pkt.fmt != RTCP_RTPFB_TWCC:
            raise ValueError(f"Expected TWCC feedback (fmt=15), got fmt={rtcp_pkt.fmt}")

        # Parse FCI header
        fci_header = unpack_twcc_fci(rtcp_pkt.fci)

        base_seq = fci_header["base_seq"]
        packet_status_count = fci_header["packet_status_count"]
        reference_time_64ms = fci_header["reference_time"]
        fb_pkt_count = fci_header["fb_pkt_count"]
        payload = fci_header["payload"]

        # Convert reference time to microseconds
        # Reference time is in 64ms units
        reference_time_us = reference_time_64ms * 64000

        # We need to split payload into chunks and deltas
        # This requires parsing chunks first to know how many deltas to expect

        # Parse chunks
        statuses = PacketChunkDecoder.decode_chunks(payload, packet_status_count)

        # Calculate where deltas start (after chunks)
        # Each chunk is 2 bytes, and we need enough chunks to cover packet_status_count
        chunks_bytes_needed = TWCCParser._calculate_chunks_size(statuses)
        deltas_data = payload[chunks_bytes_needed:]

        # Decode deltas
        arrival_times = ReceiveDeltaDecoder.decode_deltas(
            deltas_data, statuses, reference_time_us
        )

        # Build result
        results = []
        for i, (status, arrival_time) in enumerate(zip(statuses, arrival_times)):
            seq = base_seq + i

            if status == PacketStatus.NOT_RECEIVED or status == PacketStatus.RESERVED:
                results.append(ReceivedPacketInfo(seq, received=False))
            else:
                results.append(ReceivedPacketInfo(seq, received=True, arrival_time_us=arrival_time))

        return results

    @staticmethod
    def _calculate_chunks_size(statuses: List[PacketStatus]) -> int:
        """
        Calculate how many bytes the chunks section occupies.

        This is needed to find where deltas start in the payload.
        """
        # Re-encode to determine size (not most efficient, but correct)
        # Alternative: track chunk boundaries during parsing
        temp_packets = []
        for i, status in enumerate(statuses):
            if status == PacketStatus.NOT_RECEIVED:
                temp_packets.append(PacketInfo(i, received=False))
            elif status == PacketStatus.RECEIVED_SMALL_DELTA:
                temp_packets.append(PacketInfo(i, received=True, delta_us=1000))
            elif status == PacketStatus.RECEIVED_LARGE_DELTA:
                temp_packets.append(PacketInfo(i, received=True, delta_us=100000))
            else:
                temp_packets.append(PacketInfo(i, received=False))

        chunks = PacketChunkEncoder.encode_chunks(temp_packets)
        return len(chunks)


# Testing
class TestTWCCParser(unittest.TestCase):
    """Test TWCC parser."""

    def test_parse_simple_feedback(self):
        """Test parsing simple feedback packet."""
        # Create recorder and build feedback
        recorder = TWCCRecorder(sender_ssrc=0x12345678)

        base_time = 1000000

        # Record packets
        for i in range(5):
            recorder.record(100 + i, base_time + i * 10000, 0xAABBCCDD)

        feedback_bytes = recorder.build_feedback_packet()
        self.assertIsNotNone(feedback_bytes)

        # Parse feedback
        received_packets = TWCCParser.parse_feedback(feedback_bytes)

        self.assertEqual(len(received_packets), 5)

        for i, pkt_info in enumerate(received_packets):
            self.assertEqual(pkt_info.sequence_number, 100 + i)
            self.assertTrue(pkt_info.received)
            # Check arrival times are reasonable
            self.assertGreater(pkt_info.arrival_time_us, 0)

    def test_parse_with_missing_packets(self):
        """Test parsing feedback with missing packets."""
        recorder = TWCCRecorder(sender_ssrc=0x12345678)

        base_time = 2000000

        # Record with gaps
        recorder.record(100, base_time, 0xAABBCCDD)
        recorder.record(101, base_time + 10000, 0xAABBCCDD)
        # Skip 102
        recorder.record(103, base_time + 30000, 0xAABBCCDD)

        feedback_bytes = recorder.build_feedback_packet()

        # Parse
        received_packets = TWCCParser.parse_feedback(feedback_bytes)

        self.assertEqual(len(received_packets), 4)  # 100-103
        self.assertTrue(received_packets[0].received)  # 100
        self.assertTrue(received_packets[1].received)  # 101
        self.assertFalse(received_packets[2].received)  # 102 missing
        self.assertTrue(received_packets[3].received)  # 103

    def test_roundtrip_encode_decode(self):
        """Test complete roundtrip: record -> encode -> decode."""
        recorder = TWCCRecorder(sender_ssrc=0x11111111)

        base_time = 5000000

        # Record varied pattern
        expected_received = [True, True, False, True, False, False, True, True]

        seq = 200
        for i, should_receive in enumerate(expected_received):
            if should_receive:
                recorder.record(seq, base_time + i * 5000, 0x22222222)
            seq += 1

        # Build feedback
        feedback_bytes = recorder.build_feedback_packet()

        # Parse
        parsed = TWCCParser.parse_feedback(feedback_bytes)

        # Verify
        self.assertEqual(len(parsed), len(expected_received))

        for i, (expected, actual) in enumerate(zip(expected_received, parsed)):
            self.assertEqual(actual.sequence_number, 200 + i)
            self.assertEqual(actual.received, expected)

            if expected:
                self.assertGreater(actual.arrival_time_us, 0)
```

---

### Step 2.4: Sent Packet Tracker

**File:** `src/aiortc/gcc.py` (create new file)

**What it does:** Tracks packets sent by sender to correlate with feedback.

#### Implementation

```python
# src/aiortc/gcc.py

"""
Google Congestion Control (GCC) implementation.

Based on:
- draft-ietf-rmcat-gcc-02
- libwebrtc modules/congestion_controller/goog_cc

This module provides sender-side bandwidth estimation.
"""

from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional
import time


@dataclass
class SentPacketInfo:
    """Information about a sent packet."""
    sequence_number: int  # Transport-wide sequence number
    send_time_us: int  # Send timestamp in microseconds
    size_bytes: int  # Packet size including headers
    ssrc: int  # Source SSRC


class SentPacketTracker:
    """
    Tracks sent packets for correlation with TWCC feedback.

    Maintains a window of recently sent packets to match against feedback.
    """

    def __init__(self, window_ms: int = 10000) -> None:
        """
        Initialize sent packet tracker.

        Args:
            window_ms: Time window to keep sent packets (default 10 seconds)
        """
        self._packets: OrderedDict[int, SentPacketInfo] = OrderedDict()
        self._window_us = window_ms * 1000

    def add(self, sequence_number: int, send_time_us: int, size_bytes: int, ssrc: int) -> None:
        """
        Track a sent packet.

        Args:
            sequence_number: Transport-wide sequence number
            send_time_us: Send timestamp in microseconds
            size_bytes: Packet size in bytes
            ssrc: Source SSRC
        """
        self._packets[sequence_number] = SentPacketInfo(
            sequence_number=sequence_number,
            send_time_us=send_time_us,
            size_bytes=size_bytes,
            ssrc=ssrc,
        )

        # Cleanup old packets
        self._cleanup(send_time_us)

    def get(self, sequence_number: int) -> Optional[SentPacketInfo]:
        """
        Get sent packet info.

        Args:
            sequence_number: Transport-wide sequence number

        Returns:
            Sent packet info, or None if not found
        """
        return self._packets.get(sequence_number)

    def remove(self, sequence_number: int) -> Optional[SentPacketInfo]:
        """Remove and return packet info."""
        return self._packets.pop(sequence_number, None)

    def _cleanup(self, current_time_us: int) -> None:
        """Remove packets older than window."""
        cutoff_time = current_time_us - self._window_us

        # Remove old entries
        keys_to_remove = [
            seq
            for seq, pkt in list(self._packets.items())
            if pkt.send_time_us < cutoff_time
        ]

        for seq in keys_to_remove:
            del self._packets[seq]

    def size(self) -> int:
        """Return number of tracked packets."""
        return len(self._packets)

    def clear(self) -> None:
        """Clear all tracked packets."""
        self._packets.clear()


# Testing
import unittest

class TestSentPacketTracker(unittest.TestCase):
    """Test sent packet tracker."""

    def test_add_and_get(self):
        """Test adding and retrieving packets."""
        tracker = SentPacketTracker()

        tracker.add(100, 1000000, 1200, 0xAABBCCDD)
        tracker.add(101, 1001000, 1300, 0xAABBCCDD)

        pkt100 = tracker.get(100)
        self.assertIsNotNone(pkt100)
        self.assertEqual(pkt100.sequence_number, 100)
        self.assertEqual(pkt100.size_bytes, 1200)

        pkt101 = tracker.get(101)
        self.assertIsNotNone(pkt101)
        self.assertEqual(pkt101.send_time_us, 1001000)

    def test_cleanup_old_packets(self):
        """Test automatic cleanup of old packets."""
        tracker = SentPacketTracker(window_ms=100)  # 100ms window

        # Add packet at t=1s
        tracker.add(100, 1000000, 1200, 0xAABBCCDD)

        # Add packet at t=1.15s (should trigger cleanup of first packet)
        tracker.add(101, 1150000, 1200, 0xAABBCCDD)

        self.assertIsNone(tracker.get(100))  # Should be cleaned up
        self.assertIsNotNone(tracker.get(101))

    def test_remove(self):
        """Test removing packets."""
        tracker = SentPacketTracker()

        tracker.add(100, 1000000, 1200, 0xAABBCCDD)

        removed = tracker.remove(100)
        self.assertIsNotNone(removed)
        self.assertEqual(removed.sequence_number, 100)

        # Should be gone
        self.assertIsNone(tracker.get(100))

    def test_size(self):
        """Test size tracking."""
        tracker = SentPacketTracker()

        self.assertEqual(tracker.size(), 0)

        tracker.add(100, 1000000, 1200, 0xAABBCCDD)
        self.assertEqual(tracker.size(), 1)

        tracker.add(101, 1001000, 1200, 0xAABBCCDD)
        self.assertEqual(tracker.size(), 2)

        tracker.remove(100)
        self.assertEqual(tracker.size(), 1)
```

---

### Step 2.5: Sender Integration

**File:** `src/aiortc/rtcrtpsender.py`

**What it does:** Integrate TWCC into sender packet flow.

#### Implementation

```python
# Modifications to src/aiortc/rtcrtpsender.py

# Add imports at top
from .gcc import SentPacketTracker
import time

# In RTCRtpSender.__init__(), add:
self.__sent_packet_tracker: Optional[SentPacketTracker] = None
self.__transport_seq_manager: Optional['TransportSequenceNumberManager'] = None

# Add method to enable TWCC:
def _enable_twcc(self, seq_manager: 'TransportSequenceNumberManager') -> None:
    """
    Enable Transport-Wide Congestion Control.

    Args:
        seq_manager: Shared sequence number manager for this transport
    """
    self.__transport_seq_manager = seq_manager
    self.__sent_packet_tracker = SentPacketTracker()
    self.__log_debug("TWCC enabled on sender")

# Modify _send_rtp method to add transport sequence number:
async def _send_rtp(self, packet: RtpPacket) -> None:
    """Send RTP packet."""
    # ... existing code ...

    # Add transport sequence number if TWCC enabled
    if self.__transport_seq_manager is not None:
        transport_seq = self.__transport_seq_manager.next()
        packet.extensions.transport_sequence_number = transport_seq

        # Track sent packet
        if self.__sent_packet_tracker is not None:
            send_time_us = int(time.time() * 1_000_000)
            packet_size = len(bytes(packet))  # Approximate size

            self.__sent_packet_tracker.add(
                sequence_number=transport_seq,
                send_time_us=send_time_us,
                size_bytes=packet_size,
                ssrc=packet.ssrc,
            )

    # ... rest of existing code (encode extensions, send packet) ...

# Modify _handle_rtcp_packet to handle TWCC feedback:
async def _handle_rtcp_packet(self, packet: AnyRtcpPacket) -> None:
    """Handle incoming RTCP packet."""
    # ... existing code for REMB, NACK, etc. ...

    # Handle TWCC feedback
    if isinstance(packet, RtcpRtpfbPacket) and packet.fmt == RTCP_RTPFB_TWCC:
        await self.__handle_twcc_feedback(bytes(packet))

# Add new method:
async def __handle_twcc_feedback(self, rtcp_data: bytes) -> None:
    """
    Handle TWCC feedback packet.

    Args:
        rtcp_data: Complete RTCP packet bytes
    """
    try:
        from .twcc import TWCCParser

        # Parse feedback
        received_packets = TWCCParser.parse_feedback(rtcp_data)

        self.__log_debug(f"TWCC feedback: {len(received_packets)} packets")

        # Match with sent packets
        for recv_pkt in received_packets:
            sent_pkt = self.__sent_packet_tracker.get(recv_pkt.sequence_number)

            if sent_pkt is None:
                continue  # Packet not in our tracking window

            if recv_pkt.received:
                # Calculate one-way delay
                owd_us = recv_pkt.arrival_time_us - sent_pkt.send_time_us

                # TODO Phase 3: Feed to GCC bandwidth estimator
                # self.__gcc_estimator.add_packet_feedback(...)

                self.__log_debug(
                    f"Packet {recv_pkt.sequence_number}: "
                    f"OWD={owd_us}us, size={sent_pkt.size_bytes}B"
                )
            else:
                # Packet lost
                self.__log_debug(f"Packet {recv_pkt.sequence_number}: LOST")

    except Exception as e:
        self.__log_debug(f"Failed to process TWCC feedback: {e}")
```

---

## Phase 2 Summary

### Deliverables

✅ Packet chunk decoder
✅ Receive delta decoder
✅ Complete TWCC parser
✅ Sent packet tracker
✅ Sender integration (packet tracking)
✅ TWCC feedback handling skeleton

### Test Coverage

- Unit tests for decoders (~300 lines)
- Roundtrip encode/decode tests
- Integration tests for sender tracking

### Estimated Duration

**1-2 weeks** for implementation + testing

### Next Phase

Phase 3: Sender-Side Bandwidth Estimation (GCC Algorithm)

---

*Continue with Phase 3...*

