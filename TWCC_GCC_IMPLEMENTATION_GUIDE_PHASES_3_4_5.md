## PHASE 3: Sender-Side Bandwidth Estimation (GCC)

**Goal:** Implement complete GCC algorithm for sender-side bandwidth estimation

**Duration:** 3-4 weeks

**Reference:** libwebrtc goog_cc + existing aiortc rate.py components

---

### Step 3.1: Packet Feedback Processor

**File:** `src/aiortc/gcc.py`

**What it does:** Processes TWCC feedback and prepares data for bandwidth estimation.

#### Implementation

```python
# Add to src/aiortc/gcc.py

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class PacketFeedback:
    """
    Processed packet feedback combining sent and received information.

    This is the input to the GCC algorithm.
    """
    sequence_number: int
    send_time_us: int
    arrival_time_us: int  # 0 if not received
    size_bytes: int
    received: bool


class PacketFeedbackProcessor:
    """
    Processes TWCC feedback into format suitable for GCC algorithm.

    Combines sent packet info with received packet info.
    """

    def __init__(self) -> None:
        """Initialize processor."""
        self._last_feedback_time_us = 0

    def process_feedback(
        self,
        received_packets: List['ReceivedPacketInfo'],
        sent_packet_tracker: SentPacketTracker,
        feedback_time_us: int
    ) -> List[PacketFeedback]:
        """
        Process TWCC feedback into packet feedback list.

        Args:
            received_packets: Parsed TWCC feedback
            sent_packet_tracker: Tracker with sent packet info
            feedback_time_us: Time when feedback was received

        Returns:
            List of packet feedback for GCC algorithm
        """
        feedback_list = []

        for recv_pkt in received_packets:
            sent_pkt = sent_packet_tracker.get(recv_pkt.sequence_number)

            if sent_pkt is None:
                # Packet not in our window, skip
                continue

            feedback = PacketFeedback(
                sequence_number=recv_pkt.sequence_number,
                send_time_us=sent_pkt.send_time_us,
                arrival_time_us=recv_pkt.arrival_time_us if recv_pkt.received else 0,
                size_bytes=sent_pkt.size_bytes,
                received=recv_pkt.received,
            )

            feedback_list.append(feedback)

        self._last_feedback_time_us = feedback_time_us

        return feedback_list
```

See full implementation with tests in continuation file...
