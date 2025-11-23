# Arrival Group Accumulator - Component Analysis

**Component:** `arrivalGroupAccumulator`
**Status:** ✅ **COMPLETE** (Implemented 2025-11-22)
**Implementation:** `src/aiortc/gcc/arrival_group_accumulator.py`
**Verification:** Line-by-line port verified in `IMPLEMENTATION_VERIFICATION.md`

---

## Implementation Status

✅ **FULLY IMPLEMENTED AND VERIFIED**

The ArrivalGroupAccumulator has been implemented as an exact line-by-line port from pion's reference implementation. All thresholds, algorithms, and edge cases match pion precisely.

**Key Files:**
- `src/aiortc/gcc/acknowledgment.py` - Acknowledgment model
- `src/aiortc/gcc/arrival_group.py` - ArrivalGroup container
- `src/aiortc/gcc/arrival_group_accumulator.py` - Grouping logic

---

## Purpose

Groups acknowledgments into "bursts" based on send time proximity. This is essential for calculating **inter-group delay variation**, which is the core signal for GCC congestion detection.

---

## Pion Implementation

**File:** `pkg/gcc/arrival_group_accumulator.go`

### Key Constants

```go
interDepartureThreshold:          5 * time.Millisecond
interArrivalThreshold:            5 * time.Millisecond
interGroupDelayVariationTreshold: 0
```

### Core Logic

```go
func (a *arrivalGroupAccumulator) run(in <-chan []cc.Acknowledgment, agWriter func(arrivalGroup)) {
    init := false
    group := arrivalGroup{}

    for acks := range in {
        // Sort acknowledgments by arrival time to handle out-of-order arrivals
        sort.Slice(acks, func(i, j int) bool {
            return acks[i].Arrival.Before(acks[j].Arrival)
        })

        for _, next := range acks {
            if !init {
                group = newArrivalGroup(next)
                init = true
                continue
            }

            // Check if packet arrived out of order (earlier than current group)
            if next.Arrival.Before(group.arrival) {
                agWriter(group)
                group = newArrivalGroup(next)
                continue
            }

            // Only process packets with increasing send times
            if next.Departure.After(group.departure) {
                // RULE 1: Packets sent within 5ms burst belong to same group
                if interDepartureTimePkt(group, next) <= a.interDepartureThreshold {
                    group.add(next)
                    continue
                }

                // RULE 2: Packets arriving within 5ms AND with negative delay variation
                // belong to same group (same network path delay)
                if interArrivalTimePkt(group, next) <= a.interArrivalThreshold &&
                    interGroupDelayVariationPkt(group, next) < a.interGroupDelayVariationTreshold {
                    group.add(next)
                    continue
                }

                // Packet belongs to new group
                agWriter(group)
                group = newArrivalGroup(next)
            } else {
                // Packet sent earlier than group departure - drop it
                // This handles out-of-order sends or retransmissions
            }
        }
    }
}
```

### Helper Functions

```go
// How long since last packet was sent
func interDepartureTimePkt(group arrivalGroup, ack cc.Acknowledgment) time.Duration {
    return ack.Departure.Sub(group.departure)
}

// How long since last packet arrived
func interArrivalTimePkt(group arrivalGroup, ack cc.Acknowledgment) time.Duration {
    return ack.Arrival.Sub(group.arrival)
}

// Delay variation for this packet vs group
// d(i) = (t(i) - t(group)) - (T(i) - T(group))
// If d(i) < 0: packet took LESS time than group → same burst
// If d(i) > 0: packet took MORE time → queue growing → new group
func interGroupDelayVariationPkt(group arrivalGroup, ack cc.Acknowledgment) time.Duration {
    return ack.Arrival.Sub(group.arrival) - ack.Departure.Sub(group.departure)
}
```

---

## Algorithm Explanation

### Grouping Rules

A packet belongs to the current group if:

1. **Sent within burst (5ms):**
   ```
   T(next) - T(group_last) ≤ 5ms
   ```

2. **Arrived within burst (5ms) AND same queuing delay:**
   ```
   t(next) - t(group_last) ≤ 5ms
   AND
   d(next) < 0

   where d(next) = [t(next) - t(group_last)] - [T(next) - T(group_last)]
   ```

### Why This Matters

**Example Scenario:**

```
Packet A: sent at T=0ms,    arrived at t=100ms  (delay=100ms)
Packet B: sent at T=3ms,    arrived at t=103ms  (delay=100ms)
Packet C: sent at T=6ms,    arrived at t=107ms  (delay=101ms)
Packet D: sent at T=50ms,   arrived at t=155ms  (delay=105ms)
```

**Grouping:**
- A, B form Group 1 (sent within 5ms, similar delay)
  - d(B) = (103-100) - (3-0) = 0ms → same delay
- C might join Group 1:
  - Inter-departure: 6-3 = 3ms ≤ 5ms ✅
  - d(C) = (107-103) - (6-3) = 1ms ≥ 0ms ❌ (slight queue growth)
  - → Starts Group 2
- D definitely starts new group:
  - Inter-departure: 50-6 = 44ms > 5ms ❌

**Result:**
- Group 1: [A, B] - burst sent at T≈0ms
- Group 2: [C] - single packet
- Group 3: [D] - next burst

Then slope estimator calculates:
```
d(Group2 vs Group1) = (t_Group2 - t_Group1) - (T_Group2 - T_Group1)
                    = (107 - 103) - (6 - 3)
                    = 4ms - 3ms = +1ms
                    → Queue grew by 1ms
```

---

## Why aiortc's InterArrival is Wrong

**aiortc currently uses:**

```python
class InterArrival:
    def compute_deltas(self, timestamp, arrival_time, packet_size):
        # Groups by timestamp proximity only
        # Does NOT consider inter-group delay variation
        # Operates on individual packets, not bursts
```

**Problems:**

1. **Groups by timestamp, not send time bursts:**
   - InterArrival groups packets by RTP timestamp (which is frame-based)
   - arrivalGroupAccumulator groups by send time proximity (which is burst-based)

2. **No delay variation filtering:**
   - InterArrival doesn't check if packets have similar queuing delay
   - arrivalGroupAccumulator uses d(i) < 0 to keep same-delay packets together

3. **Returns different delta:**
   - InterArrival returns: (Δt_arrival, Δtimestamp, Δsize)
   - arrivalGroupAccumulator produces: arrivalGroup → interGroupDelayVariation

4. **Per-packet vs per-burst:**
   - InterArrival processes each packet individually
   - arrivalGroupAccumulator accumulates bursts then processes groups

---

## Implementation Plan for aiortc

### 1. Create `arrival_group.py`

```python
from dataclasses import dataclass
from typing import List
import time

@dataclass
class Acknowledgment:
    """Matches pion's cc.Acknowledgment"""
    sequence_number: int
    size: int
    departure: float  # Send time (seconds since epoch)
    arrival: float    # Receive time (seconds since epoch)

@dataclass
class ArrivalGroup:
    """Group of packets sent/received in a burst"""
    packets: List[Acknowledgment]
    departure: float  # Send time of last packet in group
    arrival: float    # Arrival time of last packet in group

    @classmethod
    def create(cls, ack: Acknowledgment) -> 'ArrivalGroup':
        return cls(
            packets=[ack],
            departure=ack.departure,
            arrival=ack.arrival
        )

    def add(self, ack: Acknowledgment) -> None:
        self.packets.append(ack)
        self.arrival = ack.arrival  # Update to latest arrival
```

### 2. Create `arrival_group_accumulator.py`

```python
from typing import List, Callable
from .arrival_group import Acknowledgment, ArrivalGroup

class ArrivalGroupAccumulator:
    """
    Groups acknowledgments into bursts for GCC delay-based estimation.

    Matches pion's arrivalGroupAccumulator implementation.
    """

    INTER_DEPARTURE_THRESHOLD_S = 0.005  # 5ms in seconds
    INTER_ARRIVAL_THRESHOLD_S = 0.005    # 5ms in seconds
    INTER_GROUP_DELAY_VARIATION_THRESHOLD_S = 0.0

    def __init__(self):
        self._initialized = False
        self._current_group: Optional[ArrivalGroup] = None

    def process_acknowledgments(
        self,
        acks: List[Acknowledgment],
        on_group: Callable[[ArrivalGroup], None]
    ) -> None:
        """
        Process acknowledgments and emit arrival groups.

        Args:
            acks: List of acknowledgments (must be sorted by arrival time)
            on_group: Callback for completed groups
        """
        # Sort by arrival time to handle TWCC negative deltas
        acks = sorted(acks, key=lambda a: a.arrival)

        for ack in acks:
            if not self._initialized:
                self._current_group = ArrivalGroup.create(ack)
                self._initialized = True
                continue

            # Handle out-of-order arrivals
            if ack.arrival < self._current_group.arrival:
                on_group(self._current_group)
                self._current_group = ArrivalGroup.create(ack)
                continue

            # Only process packets with increasing send times
            if ack.departure > self._current_group.departure:
                # Rule 1: Sent within burst threshold
                if self._inter_departure_time(ack) <= self.INTER_DEPARTURE_THRESHOLD_S:
                    self._current_group.add(ack)
                    continue

                # Rule 2: Arrived within burst AND negative delay variation
                if (self._inter_arrival_time(ack) <= self.INTER_ARRIVAL_THRESHOLD_S and
                    self._inter_group_delay_variation(ack) < self.INTER_GROUP_DELAY_VARIATION_THRESHOLD_S):
                    self._current_group.add(ack)
                    continue

                # New group
                on_group(self._current_group)
                self._current_group = ArrivalGroup.create(ack)
            else:
                # Packet sent earlier than current group - drop
                # (out-of-order send or retransmission)
                pass

    def _inter_departure_time(self, ack: Acknowledgment) -> float:
        """Time since last packet was sent."""
        return ack.departure - self._current_group.departure

    def _inter_arrival_time(self, ack: Acknowledgment) -> float:
        """Time since last packet arrived."""
        return ack.arrival - self._current_group.arrival

    def _inter_group_delay_variation(self, ack: Acknowledgment) -> float:
        """
        Delay variation for this packet vs current group.

        d(i) = (t_i - t_group) - (T_i - T_group)

        Negative value means packet took LESS time than group average,
        indicating it belongs to the same burst.
        """
        arrival_delta = ack.arrival - self._current_group.arrival
        departure_delta = ack.departure - self._current_group.departure
        return arrival_delta - departure_delta
```

---

## Integration into DelayController

```python
from asyncio import Queue
from typing import List
from .arrival_group_accumulator import ArrivalGroupAccumulator, Acknowledgment
from .slope_estimator import SlopeEstimator

class DelayController:
    def __init__(self):
        self.accumulator = ArrivalGroupAccumulator()
        self.slope_estimator = SlopeEstimator()
        self.ack_queue = Queue()

    def update_delay_estimate(self, acks: List[Acknowledgment]) -> None:
        """Process acknowledgments through the delay-based pipeline."""
        def on_group(group: ArrivalGroup):
            # Forward to slope estimator
            self.slope_estimator.on_arrival_group(group)

        self.accumulator.process_acknowledgments(acks, on_group)
```

---

## Testing Strategy

### Unit Tests

1. **Single burst:**
   - Send 5 packets within 5ms
   - Verify they form one group

2. **Multiple bursts:**
   - Send burst A (0-5ms)
   - Wait 20ms
   - Send burst B (25-30ms)
   - Verify two groups

3. **Delay variation filtering:**
   - Send packet A at T=0, arrives at t=100
   - Send packet B at T=3, arrives at t=103 (d=0ms)
   - Send packet C at T=6, arrives at t=108 (d=+2ms)
   - Verify A,B in group 1, C starts group 2

4. **Out-of-order arrivals:**
   - Packets arrive in different order than sent
   - Verify groups still formed correctly

### Integration Tests

Compare output with pion using same TWCC feedback:
- Feed same acknowledgments to both
- Verify groups contain same packets
- Verify group timestamps match

---

## References

- Pion implementation: `pkg/gcc/arrival_group_accumulator.go`
- Draft-ietf-rmcat-gcc-02 Section 5.3: "Arrival-time filter"
- Related: `slope_estimator.go` (consumes arrival groups)

---

## Conclusion

**The arrival group accumulator is the FOUNDATION of GCC delay-based BWE.**

Without it, we cannot:
- Detect packet bursts correctly
- Calculate inter-group delay variation
- Properly estimate queue growth/shrinkage

This component must be implemented EXACTLY as pion does it, with the same thresholds and logic. It's not optional - it's the core of the algorithm.
