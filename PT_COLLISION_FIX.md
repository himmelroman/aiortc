# PT 103 Collision Bug Fix

**Implementation**: Ported from libwebrtc's `UsedIds::FindAndSetIdUsed` algorithm

## Problem

As reported in [PR #1390](https://github.com/aiortc/aiortc/pull/1390), when aiortc creates an SDP answer **without explicit codec preferences**, it would accidentally assign payload type (PT) 103 to both H264 and VP9 in the same SDP, causing the browser to reject with:

> "Duplicate payload type with conflicting codec name or clock rate"

## Root Cause

The bug was in the `find_common_codecs()` function in `rtcpeerconnection.py`. When negotiating codecs between local and remote peers:

1. **RTX codec handling (line 95)**: When adding an RTX codec, the code blindly copied the remote PT:
   ```python
   common.append(copy.deepcopy(c))  # No PT collision check!
   ```

2. **Main codec handling (line 103)**: When adding a main codec, the code copied the remote PT:
   ```python
   codec.payloadType = c.payloadType  # No collision check!
   ```

### Problematic Flow Example

**Scenario**: Remote offer has codecs with overlapping PTs in the dynamic range (96-127):
- H264: PT 102 (main), PT 103 (RTX, apt=102)
- VP9: PT 103 (main), PT 104 (RTX, apt=103)

**Processing in find_common_codecs**:
1. H264 main (PT 102) → added with PT 102
2. H264 RTX (PT 103, apt=102) → **ADDED WITH PT 103** ✓
3. VP9 main (PT 103) → **ADDED WITH PT 103** ❌ **COLLISION!**

The function never checked if a PT was already in use before assigning it.

## The Fix

Ported the **exact libwebrtc `UsedIds::FindAndSetIdUsed` algorithm** from Chromium's WebRTC implementation.

**Source**: [webrtc/pc/media_session.cc](https://github.com/webrtc-uwp/webrtc/blob/master/pc/media_session.cc)

### Algorithm (3-Phase PT Assignment)

The `find_and_assign_pt()` function implements libwebrtc's collision resolution:

```python
def find_and_assign_pt(codec, preferred_pt) -> bool:
    # Phase 1: Try the preferred PT (from remote offer)
    if preferred_pt in DYNAMIC_PAYLOAD_TYPES and preferred_pt not in used_pts:
        codec.payloadType = preferred_pt
        used_pts.add(preferred_pt)
        return True

    # Phase 2: Collision! Search ascending from next_pt (starts at 96)
    for pt in range(next_pt, 128):
        if pt not in used_pts:
            codec.payloadType = pt
            used_pts.add(pt)
            next_pt = pt + 1  # Optimize next search
            return True

    # Phase 3: Ascending exhausted, search descending from 127
    for pt in range(127, 95, -1):
        if pt not in used_pts:
            codec.payloadType = pt
            used_pts.add(pt)
            return True

    return False  # All PTs exhausted
```

### Why This Algorithm?

**Phase 1** (Preferred): Minimizes SDP changes by using remote's PT when possible
**Phase 2** (Ascending): Fills gaps efficiently, maintains locality
**Phase 3** (Descending): Fallback for heavily used PT space, works backward from max

This matches how Chrome, Firefox, and other libwebrtc-based browsers handle PT assignment.

## Result

Now when PT collisions occur during codec negotiation, aiortc automatically allocates an unused PT from the dynamic range (96-127), ensuring all codecs have unique payload types in the SDP answer.

### Benefits

- ✅ **Fixes the PT 103 collision bug** reported in PR #1390
- ✅ **Works without codec preferences** - no longer required to avoid the bug
- ✅ **Robust for all scenarios** - handles any PT collision, not just 103
- ✅ **Automatic recovery** - dynamically reallocates from available PT range
- ✅ **Maintains compatibility** - only reallocates when necessary

## Testing

To verify the fix works without codec preferences:

```python
from aiortc import RTCPeerConnection

# Create peer connection
pc1 = RTCPeerConnection()  # Offerer
pc2 = RTCPeerConnection()  # Answerer

# Add track (NO codec preferences)
pc1.addTrack(video_track)

# Create offer and answer
offer = await pc1.createOffer()
await pc1.setLocalDescription(offer)
await pc2.setRemoteDescription(offer)

answer = await pc2.createAnswer()  # Should not have PT collisions!
await pc2.setLocalDescription(answer)

# Verify: Check answer SDP has unique PTs for all codecs
```

Before the fix, this would sometimes produce duplicate PT 103. After the fix, all PTs are guaranteed to be unique.
