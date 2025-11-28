# Codec Selection API

This document demonstrates how to use the improved codec selection API in aiortc, which now mimics the Chrome WebRTC API for easier codec preference management.

## Simple API: Using MIME Type Strings

The easiest way to set codec preferences is to pass MIME type strings directly:

```python
from aiortc import RTCPeerConnection, RTCRtpSender

pc = RTCPeerConnection()
sender = pc.addTrack(video_track)

# Find the transceiver for this sender
transceiver = next(t for t in pc.getTransceivers() if t.sender == sender)

# NEW: Simply pass MIME type strings in preference order
transceiver.setCodecPreferences(["video/VP9", "video/VP8", "video/H264"])
```

This will prefer VP9 first, then VP8, then H264. Much simpler than the old API!

## Using the Helper Function

You can also use the `sort_codecs_by_mime_types` helper function to reorder capabilities:

```python
from aiortc import RTCRtpSender, sort_codecs_by_mime_types

# Get all video codec capabilities
capabilities = RTCRtpSender.getCapabilities("video")

# Sort them by your preference order
preferred_codecs = sort_codecs_by_mime_types(
    capabilities.codecs,
    ["video/VP9", "video/VP8", "video/H264"]
)

# Set the preferences
transceiver.setCodecPreferences(preferred_codecs)
```

## Advanced API: Using RTCRtpCodecCapability Objects

For more control (e.g., filtering by specific codec parameters), you can still use the original API:

```python
from aiortc import RTCRtpSender

capabilities = RTCRtpSender.getCapabilities("video")

# Filter to only VP9 codecs
vp9_codecs = [codec for codec in capabilities.codecs if codec.mimeType == "video/VP9"]

transceiver.setCodecPreferences(vp9_codecs)
```

## Force a Single Codec

To force a specific codec (common use case):

```python
def force_codec(pc, sender, codec_mime_type):
    """Force a specific codec for a sender."""
    transceiver = next(t for t in pc.getTransceivers() if t.sender == sender)
    transceiver.setCodecPreferences([codec_mime_type])

# Usage
force_codec(pc, video_sender, "video/VP9")
```

## Comparison with Chrome WebRTC API

This API now closely matches the Chrome/JavaScript WebRTC API pattern:

**JavaScript (Chrome):**
```javascript
const capabilities = RTCRtpSender.getCapabilities('video');
const preferred = capabilities.codecs.sort((a, b) => {
  const order = ['video/VP9', 'video/VP8', 'video/H264'];
  return order.indexOf(a.mimeType) - order.indexOf(b.mimeType);
});
transceiver.setCodecPreferences(preferred);
```

**Python (aiortc) - Now:**
```python
transceiver.setCodecPreferences(["video/VP9", "video/VP8", "video/H264"])
```

Much simpler! 🎉

## Benefits

1. **Simpler API**: No need to manually filter codec capabilities
2. **Cross-platform consistency**: Matches the Chrome WebRTC API pattern
3. **Less code**: Reduce boilerplate in your applications
4. **Backward compatible**: Old API with `RTCRtpCodecCapability` objects still works
