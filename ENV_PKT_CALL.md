# Encoded Packet Callback API

## Overview

This document describes the `encoded_packet_callback` API added to aiortc's `RTCRtpSender` for accessing encoded video packets without re-encoding. This enables use cases like IVF-based recording, packet inspection, and custom packet processing.

## Design Rationale

### Following WebRTC Standards

The design follows WebRTC's native C++ `EncodedImageCallback` pattern:

**WebRTC C++ Pattern:**
```cpp
class EncodedImageCallback {
  virtual int32_t Encoded(
    const EncodedImage& encoded_image,
    const CodecSpecificInfo* codec_specific_info,
    const RTPFragmentationHeader* fragmentation
  ) = 0;
};
```

**Key characteristics:**
- Callback receives encoded data + codec-specific metadata
- Called synchronously on encoder thread
- Codec parameters (width, height, fps) are configured once at encoder setup, not passed per-frame
- Per-frame metadata (temporal layers, picture ID) is passed with each callback

### aiortc Implementation

Our implementation adapts this pattern to Python/PyAV while maintaining the same philosophy:

**Encoder Signature Change:**
```python
# Before:
def encode(self, frame: Frame, force_keyframe: bool = False) -> tuple[list[bytes], int]:
    """Returns (payloads, timestamp)"""
    pass

# After:
def encode(self, frame: Frame, force_keyframe: bool = False) -> tuple[list[bytes], list[av.Packet], int]:
    """Returns (payloads, packets, timestamp)"""
    pass
```

**Rationale for ordering `(payloads, packets, timestamp)`:**
- Groups the two data representations together (RTP payloads, encoded packets)
- Timestamp applies to both representations
- More intuitive than mixing data and metadata

### Architecture Decisions

#### 1. Encoder Returns Packets, Sender Manages Callback

**Why not call callback directly in encoder?**
- **Separation of concerns:** Encoder should be a pure function (frame → packets)
- **Clean abstraction:** Encoder doesn't need to know about callbacks
- **Flexibility:** Sender can decide when/how to invoke callbacks
- **Upstream compatibility:** Minimal encoder changes make upstream contribution easier

**Flow:**
```
Encoder.encode() → returns (payloads, packets, timestamp)
                ↓
RTCRtpSender._next_encoded_frame() → invokes callback(packet) for each packet
```

#### 2. Synchronous Callback Execution

Following WebRTC's threading model:
- WebRTC calls `OnEncodedImage` synchronously on encoder thread
- aiortc runs encoder in executor thread via `run_in_executor()`
- Callback is invoked synchronously after encoder returns

**Benefits:**
- No race conditions
- Predictable execution order
- Simpler reasoning about packet flow

#### 3. Config-Once Pattern

**Codec parameters** (width, height, fps, pix_fmt, codec name) are:
- ✅ Configured at encoder/writer initialization
- ❌ NOT passed with every packet/frame

**Per-packet metadata** (keyframe, pts, dts, duration) is:
- ✅ Available in `av.Packet` attributes
- ✅ Passed with every callback invocation

This matches WebRTC's separation of:
- `VideoCodec` struct (one-time configuration)
- `EncodedImage` + `CodecSpecificInfo` (per-frame data)

## API Usage

### Basic Usage

```python
from aiortc import RTCPeerConnection, VideoStreamTrack
from aiortc.contrib.media import MediaRecorder

# Create peer connection and add video track
pc = RTCPeerConnection()
track = VideoStreamTrack()
sender = pc.addTrack(track)

# Define packet callback
def on_encoded_packet(packet: av.Packet) -> None:
    print(f"Received packet: keyframe={packet.is_keyframe}, pts={packet.pts}, size={packet.size}")
    # Process packet (e.g., write to file, inspect, retransmit)

# Set callback
sender.encoded_packet_callback = on_encoded_packet

# Packets will be delivered to callback as they are encoded
```

### IVF Recording Example

```python
import av
from pathlib import Path

class SegmentedIVFWriter:
    """Writes encoded packets to segmented IVF files (rotates on keyframes)."""

    def __init__(self, output_dir: str, codec: str, width: int, height: int, fps: int, pix_fmt: str):
        """
        Initialize writer with codec parameters (config-once pattern).

        Args:
            output_dir: Directory for IVF segment files
            codec: Codec name (e.g., 'libvpx', 'libvpx-vp9')
            width: Video width in pixels
            height: Video height in pixels
            fps: Frames per second
            pix_fmt: Pixel format (e.g., 'yuv420p')
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Create template stream once (not per-packet)
        self._template_container = av.open('/dev/null', mode='w', format='null')
        self._template_stream = self._template_container.add_stream(codec, rate=fps)
        self._template_stream.width = width
        self._template_stream.height = height
        self._template_stream.pix_fmt = pix_fmt

        self.current_container = None
        self.current_stream = None
        self.segment_num = 0
        self.current_packet_count = 0
        self.initialized = False

    def write_packet(self, packet: av.Packet) -> None:
        """Callback for encoded packets."""
        # Initialize on first packet
        if not self.initialized:
            self._start_new_segment(self._template_stream)
            self.initialized = True

        # Rotate segment on keyframes
        if packet.is_keyframe and self.current_packet_count > 0:
            self._start_new_segment(self._template_stream)

        # Assign packet to current stream and mux
        packet.stream = self.current_stream
        self.current_container.mux(packet)
        self.current_packet_count += 1

    def _start_new_segment(self, template_stream: av.Stream) -> None:
        """Start a new IVF segment file."""
        if self.current_container:
            self.current_container.close()

        segment_path = self.output_dir / f"segment_{self.segment_num:04d}.ivf"
        self.current_container = av.open(str(segment_path), mode='w', format='ivf')

        # Copy codec parameters from template
        self.current_stream = self.current_container.add_stream_from_template(template_stream)

        self.current_packet_count = 0
        self.segment_num += 1

    def close(self) -> None:
        """Close current segment."""
        if self.current_container:
            self.current_container.close()
        if self._template_container:
            self._template_container.close()

# Usage
pc = RTCPeerConnection()
sender = pc.addTrack(video_track)

# Initialize writer with codec params (config-once)
writer = SegmentedIVFWriter(
    output_dir="/tmp/recording",
    codec='libvpx',
    width=896,
    height=512,
    fps=12,
    pix_fmt='yuv420p'
)

# Set callback
sender.encoded_packet_callback = writer.write_packet

# Recording happens automatically as packets are encoded
```

## Implementation Details

### Encoder Changes

**File:** `aiortc/src/aiortc/codecs/base.py`
```python
from abc import ABC, abstractmethod
import av

class Encoder(ABC):
    @abstractmethod
    def encode(self, frame: Frame, force_keyframe: bool = False) -> tuple[list[bytes], list[av.Packet], int]:
        """
        Encode a frame.

        Args:
            frame: Frame to encode
            force_keyframe: Force keyframe generation

        Returns:
            Tuple of (payloads, packets, timestamp):
            - payloads: List of RTP payload bytes
            - packets: List of encoded av.Packet objects
            - timestamp: RTP timestamp
        """
        pass
```

**File:** `aiortc/src/aiortc/codecs/vpx.py` (example for VP8/VP9)
```python
def encode(self, frame: Frame, force_keyframe: bool = False) -> tuple[list[bytes], list[av.Packet], int]:
    # Encode frame
    if force_keyframe:
        self.codec.keyframe_interval = 1

    # Collect packets
    packets = []
    data_to_send = b""
    for package in self.codec.encode(frame):
        packets.append(package)
        data_to_send += bytes(package)

    # Reset keyframe interval
    if force_keyframe:
        self.codec.keyframe_interval = self.keyframe_interval

    # Packetize for RTP
    payloads = self._packetize(data_to_send, self.picture_id)

    # Generate timestamp
    timestamp = ...

    return payloads, packets, timestamp
```

### RTCRtpSender Changes

**File:** `aiortc/src/aiortc/rtcrtpsender.py`
```python
from typing import Callable, Optional
import av

class RTCRtpSender:
    @property
    def encoded_packet_callback(self) -> Optional[Callable[[av.Packet], None]]:
        """
        Callback for encoded packets (follows WebRTC's EncodedImageCallback pattern).

        The callback receives av.Packet objects with metadata:
        - is_keyframe: Boolean indicating keyframe
        - pts: Presentation timestamp
        - dts: Decode timestamp
        - duration: Packet duration
        - size: Packet size in bytes

        Codec parameters (width, height, fps, pix_fmt) should be configured
        separately at callback initialization, not passed per-packet.
        """
        return getattr(self, '_encoded_packet_callback', None)

    @encoded_packet_callback.setter
    def encoded_packet_callback(self, callback: Optional[Callable[[av.Packet], None]]) -> None:
        self._encoded_packet_callback = callback

    async def _next_encoded_frame(self, ...):
        # ... existing frame preparation ...

        # Encode frame (returns 3-tuple)
        payloads, packets, timestamp = await self.__loop.run_in_executor(
            None, self.__encoder.encode, data, force_keyframe
        )

        # Invoke callback synchronously (matches WebRTC threading model)
        if self._encoded_packet_callback:
            for packet in packets:
                self._encoded_packet_callback(packet)

        # ... rest of existing logic ...
```

## PyAV Packet Attributes

When the callback receives an `av.Packet`, it has the following attributes:

**Available attributes:**
- `is_keyframe` (bool): Whether this is a keyframe
- `pts` (int): Presentation timestamp
- `dts` (int): Decode timestamp
- `duration` (int): Packet duration
- `size` (int): Packet size in bytes
- `stream` (av.Stream | None): Associated stream (None from encoder, assigned by writer)

**NOT available from encoder:**
- Width, height, fps, codec name, pix_fmt → These are encoder config, not packet metadata

## Comparison: WebRTC vs aiortc

| Aspect | WebRTC C++ | aiortc Python |
|--------|------------|---------------|
| **Callback name** | `EncodedImageCallback` | `encoded_packet_callback` |
| **Signature** | `Encoded(image, codec_info, frag)` | `callback(packet)` |
| **Data level** | Frame-level | Packet-level |
| **Config pattern** | `VideoCodec` struct (once) | Constructor params (once) |
| **Per-call data** | `EncodedImage` + `CodecSpecificInfo` | `av.Packet` with metadata |
| **Threading** | Synchronous on encoder thread | Synchronous after encoder returns |
| **Location** | Encoder calls directly | Sender invokes after encoder |

## Benefits

### 1. No Re-encoding
Access encoded packets directly without decoding/re-encoding, preserving quality and CPU efficiency.

### 2. WebRTC-Compatible Design
Follows established WebRTC patterns, making the API familiar to WebRTC developers and suitable for upstream contribution.

### 3. Clean Separation of Concerns
- Encoder: Pure function (frame → packets)
- Sender: Manages callbacks and packet delivery
- Callback: Processes packets (recording, inspection, etc.)

### 4. Flexible Use Cases
- **Recording:** Write packets to IVF, MP4, or other containers
- **Inspection:** Analyze packet sizes, keyframe distribution, timing
- **Retransmission:** Forward packets to other destinations
- **Debugging:** Log packet metadata for troubleshooting

### 5. Minimal API Surface
Single property on `RTCRtpSender` with clear semantics and no hidden complexity.

## Migration from Patching Approach

**Before (encoder patching):**
```python
# Patch encoder to intercept packets
original_encode = encoder.encode
def patched_encode(frame, force_keyframe=False):
    payloads, timestamp = original_encode(frame, force_keyframe)
    # Manually create packets somehow...
    return payloads, timestamp

encoder.encode = patched_encode
```

**After (clean API):**
```python
# Use official callback API
sender.encoded_packet_callback = writer.write_packet
```

## Future Enhancements

Possible future additions (following WebRTC's `CodecSpecificInfo` pattern):

1. **Codec-specific metadata:**
   ```python
   def callback(packet: av.Packet, codec_info: CodecSpecificInfo) -> None:
       # codec_info.picture_id, codec_info.temporal_idx, etc.
   ```

2. **Per-frame configuration:**
   ```python
   sender.setParameters({
       'encodings': [{'maxBitrate': 1000000}]
   })
   ```

3. **RTP fragmentation info:**
   Following WebRTC's third parameter for RTP-level packet boundaries.

## References

- [WebRTC EncodedImageCallback](https://webrtc.googlesource.com/src/+/refs/heads/main/api/video_codecs/video_encoder.h)
- [WebRTC VideoCodec struct](https://webrtc.googlesource.com/src/+/refs/heads/main/api/video_codecs/video_codec.h)
- [PyAV Packet documentation](https://pyav.org/docs/stable/api/packet.html)
- [IVF file format](https://wiki.multimedia.cx/index.php/IVF)
