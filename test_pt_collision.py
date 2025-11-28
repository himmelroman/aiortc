"""
Test to reproduce the PT 103 collision bug.

This test simulates what happens when aiortc receives an offer with
VP8, H264, and VP9, and needs to create an answer WITHOUT explicit
codec preferences.
"""

import sys
sys.path.insert(0, 'src')

from aiortc.rtcrtpparameters import RTCRtpCodecParameters, RTCRtcpFeedback
from aiortc.rtcpeerconnection import find_common_codecs
from aiortc.codecs import CODECS

print("=" * 70)
print("Testing PT 103 Collision Bug")
print("=" * 70)

# Simulate what local codecs look like (from CODECS dict)
print("\nLocal video codecs (from CODECS):")
local_codecs = []
for idx, c in enumerate(CODECS['video']):
    print(f"  [{idx}] PT {c.payloadType}: {c.mimeType}", end="")
    if 'apt' in c.parameters:
        print(f" (RTX, apt={c.parameters['apt']})", end="")
    print()
    local_codecs.append(c)

# Simulate a remote offer that has VP8, H264, and VP9
# with PT assignments that could cause conflicts
print("\nSimulated remote offer codecs:")
remote_codecs = [
    # VP8
    RTCRtpCodecParameters(
        mimeType="video/VP8",
        clockRate=90000,
        payloadType=96,
        rtcpFeedback=[
            RTCRtcpFeedback(type="nack"),
            RTCRtcpFeedback(type="nack", parameter="pli"),
            RTCRtcpFeedback(type="goog-remb"),
        ]
    ),
    RTCRtpCodecParameters(
        mimeType="video/rtx",
        clockRate=90000,
        payloadType=97,
        parameters={"apt": 96}
    ),
    # H264 (only one profile in remote offer)
    RTCRtpCodecParameters(
        mimeType="video/H264",
        clockRate=90000,
        payloadType=102,
        parameters={
            "level-asymmetry-allowed": "1",
            "packetization-mode": "1",
            "profile-level-id": "42e01f",
        },
        rtcpFeedback=[
            RTCRtcpFeedback(type="nack"),
            RTCRtcpFeedback(type="nack", parameter="pli"),
            RTCRtcpFeedback(type="goog-remb"),
        ]
    ),
    RTCRtpCodecParameters(
        mimeType="video/rtx",
        clockRate=90000,
        payloadType=103,  # RTX for H264 at PT 102
        parameters={"apt": 102}
    ),
    # VP9
    RTCRtpCodecParameters(
        mimeType="video/VP9",
        clockRate=90000,
        payloadType=104,
        rtcpFeedback=[
            RTCRtcpFeedback(type="nack"),
            RTCRtcpFeedback(type="nack", parameter="pli"),
            RTCRtcpFeedback(type="goog-remb"),
        ]
    ),
    RTCRtpCodecParameters(
        mimeType="video/rtx",
        clockRate=90000,
        payloadType=105,  # RTX for VP9 at PT 104
        parameters={"apt": 104}
    ),
]

for idx, c in enumerate(remote_codecs):
    print(f"  [{idx}] PT {c.payloadType}: {c.mimeType}", end="")
    if 'apt' in c.parameters:
        print(f" (RTX, apt={c.parameters['apt']})", end="")
    print()

# Call find_common_codecs
print("\nCalling find_common_codecs()...")
common = find_common_codecs(local_codecs, remote_codecs)

print(f"\nResult: {len(common)} common codecs found:")
pt_usage = {}
for idx, c in enumerate(common):
    print(f"  [{idx}] PT {c.payloadType}: {c.mimeType}", end="")
    if 'apt' in c.parameters:
        print(f" (RTX, apt={c.parameters['apt']})", end="")
    print()

    # Track PT usage to detect collisions
    if c.payloadType in pt_usage:
        print(f"    ⚠️  COLLISION! PT {c.payloadType} already used by {pt_usage[c.payloadType]}")
    else:
        pt_usage[c.payloadType] = c.mimeType

print("\n" + "=" * 70)
if any(len([k for k, v in pt_usage.items() if k == pt]) > 1 for pt in pt_usage):
    print("❌ BUG REPRODUCED: PT collision detected!")
else:
    print("✅ No PT collision detected")
print("=" * 70)
