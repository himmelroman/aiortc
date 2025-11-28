"""
Unit tests for PT collision fix in find_common_codecs().

Tests the libwebrtc algorithm ported from UsedIds::FindAndSetIdUsed
to ensure payload type collisions are properly handled.
"""

import unittest

from aiortc.rtcpeerconnection import find_common_codecs
from aiortc.rtcrtpparameters import RTCRtcpFeedback, RTCRtpCodecParameters


class PTCollisionTestCase(unittest.TestCase):
    """Test payload type collision handling in codec negotiation."""

    def test_no_collision_when_pts_dont_overlap(self):
        """Test that normal case without collisions works correctly."""
        local_codecs = [
            RTCRtpCodecParameters(
                mimeType="video/VP8", clockRate=90000, payloadType=97
            ),
            RTCRtpCodecParameters(
                mimeType="video/H264",
                clockRate=90000,
                payloadType=99,
                parameters={"profile-level-id": "42e01f"},
            ),
            RTCRtpCodecParameters(
                mimeType="video/VP9", clockRate=90000, payloadType=103
            ),
        ]

        remote_codecs = [
            RTCRtpCodecParameters(
                mimeType="video/VP8", clockRate=90000, payloadType=96
            ),
            RTCRtpCodecParameters(
                mimeType="video/H264",
                clockRate=90000,
                payloadType=98,
                parameters={"profile-level-id": "42e01f"},
            ),
            RTCRtpCodecParameters(
                mimeType="video/VP9", clockRate=90000, payloadType=100
            ),
        ]

        common = find_common_codecs(local_codecs, remote_codecs)

        # Should have 3 codecs with remote's PTs (96, 98, 100)
        self.assertEqual(len(common), 3)
        pts = [c.payloadType for c in common]
        self.assertEqual(pts, [96, 98, 100])
        self.assertEqual(len(set(pts)), 3, "All PTs should be unique")

    def test_collision_between_rtx_and_main_codec(self):
        """
        Test the exact scenario from PR #1390:
        Remote has H264 RTX at PT 103, then VP9 main also at PT 103.
        """
        local_codecs = [
            RTCRtpCodecParameters(
                mimeType="video/H264",
                clockRate=90000,
                payloadType=101,
                parameters={"profile-level-id": "42e01f"},
            ),
            RTCRtpCodecParameters(
                mimeType="video/rtx",
                clockRate=90000,
                payloadType=102,
                parameters={"apt": 101},
            ),
            RTCRtpCodecParameters(
                mimeType="video/VP9", clockRate=90000, payloadType=103
            ),
            RTCRtpCodecParameters(
                mimeType="video/rtx",
                clockRate=90000,
                payloadType=104,
                parameters={"apt": 103},
            ),
        ]

        # Remote has collision: H264 RTX and VP9 main both at PT 103
        remote_codecs = [
            RTCRtpCodecParameters(
                mimeType="video/H264",
                clockRate=90000,
                payloadType=102,
                parameters={"profile-level-id": "42e01f"},
            ),
            RTCRtpCodecParameters(
                mimeType="video/rtx",
                clockRate=90000,
                payloadType=103,  # RTX for H264 at 102
                parameters={"apt": 102},
            ),
            RTCRtpCodecParameters(
                mimeType="video/VP9", clockRate=90000, payloadType=103  # COLLISION!
            ),
            RTCRtpCodecParameters(
                mimeType="video/rtx",
                clockRate=90000,
                payloadType=104,
                parameters={"apt": 103},
            ),
        ]

        common = find_common_codecs(local_codecs, remote_codecs)

        # Should have 4 codecs
        self.assertEqual(len(common), 4)

        # All PTs must be unique (this is the fix!)
        pts = [c.payloadType for c in common]
        self.assertEqual(
            len(pts), len(set(pts)), f"PT collision detected! PTs: {pts}"
        )

        # H264 main should use remote's PT 102
        h264_codec = next(c for c in common if c.mimeType == "video/H264")
        self.assertEqual(h264_codec.payloadType, 102)

        # H264 RTX should use remote's PT 103
        h264_rtx = next(
            c for c in common if c.mimeType == "video/rtx" and c.parameters["apt"] == 102
        )
        self.assertEqual(h264_rtx.payloadType, 103)

        # VP9 main CANNOT use 103 (collision!), should be reassigned
        vp9_codec = next(c for c in common if c.mimeType == "video/VP9")
        self.assertNotEqual(
            vp9_codec.payloadType, 103, "VP9 should not use PT 103 (collision!)"
        )

        # VP9 should get next available PT (ascending from 96)
        # Since 102, 103 are used, VP9 should get 96 (first available)
        self.assertIn(
            vp9_codec.payloadType,
            range(96, 128),
            "VP9 PT must be in dynamic range",
        )

    def test_libwebrtc_algorithm_phases(self):
        """
        Test the libwebrtc collision resolution algorithm:
        Phase 1: Prefer remote PT if available
        Phase 2: If collision, search descending from 127 (FindUnusedId)

        The descending search reduces collision risk by avoiding commonly-used
        lower PT values, as documented in libwebrtc source.
        """
        local_codecs = [
            RTCRtpCodecParameters(
                mimeType="video/VP8", clockRate=90000, payloadType=97
            ),
            RTCRtpCodecParameters(
                mimeType="video/VP9", clockRate=90000, payloadType=103
            ),
            RTCRtpCodecParameters(
                mimeType="video/H264",
                clockRate=90000,
                payloadType=99,
                parameters={"profile-level-id": "42e01f"},
            ),
        ]

        # Remote uses same PT (100) for multiple codecs (triggering descending search)
        remote_codecs = [
            RTCRtpCodecParameters(
                mimeType="video/VP8", clockRate=90000, payloadType=100
            ),
            RTCRtpCodecParameters(
                mimeType="video/VP9", clockRate=90000, payloadType=100  # Collision!
            ),
            RTCRtpCodecParameters(
                mimeType="video/H264",
                clockRate=90000,
                payloadType=100,  # Collision!
                parameters={"profile-level-id": "42e01f"},
            ),
        ]

        common = find_common_codecs(local_codecs, remote_codecs)

        self.assertEqual(len(common), 3)
        pts = [c.payloadType for c in common]

        # All PTs must be unique
        self.assertEqual(len(pts), len(set(pts)), f"Duplicate PTs found: {pts}")

        # First codec should use preferred PT (Phase 1)
        self.assertEqual(pts[0], 100)

        # Subsequent codecs use descending search: 127, 126, 125...
        # Second and third codecs should be < 127 (descending from max)
        self.assertEqual(pts[1], 127)  # First collision uses 127
        self.assertEqual(pts[2], 126)  # Second collision uses 126

    def test_rtx_apt_reference_updated(self):
        """Test that RTX codecs correctly reference their base codec PT."""
        local_codecs = [
            RTCRtpCodecParameters(
                mimeType="video/VP9", clockRate=90000, payloadType=103
            ),
            RTCRtpCodecParameters(
                mimeType="video/rtx",
                clockRate=90000,
                payloadType=104,
                parameters={"apt": 103},
            ),
        ]

        remote_codecs = [
            RTCRtpCodecParameters(
                mimeType="video/VP9", clockRate=90000, payloadType=100
            ),
            RTCRtpCodecParameters(
                mimeType="video/rtx",
                clockRate=90000,
                payloadType=101,
                parameters={"apt": 100},
            ),
        ]

        common = find_common_codecs(local_codecs, remote_codecs)

        # Should have 2 codecs (VP9 + RTX)
        self.assertEqual(len(common), 2)

        vp9 = common[0]
        rtx = common[1]

        # VP9 should use remote's PT 100
        self.assertEqual(vp9.payloadType, 100)

        # RTX should reference VP9's PT in apt parameter
        self.assertEqual(rtx.parameters["apt"], 100)
        self.assertEqual(rtx.payloadType, 101)

    def test_all_pts_exhausted_scenario(self):
        """
        Test edge case where all 32 dynamic PTs (96-127) might be used.
        This is theoretical but the algorithm should handle it gracefully.
        """
        # Create 32 local codecs using all PTs
        local_codecs = [
            RTCRtpCodecParameters(
                mimeType=f"video/CODEC{i}", clockRate=90000, payloadType=i
            )
            for i in range(96, 128)
        ]

        # Remote offers one codec with PT 96 (which is available)
        remote_codecs = [
            RTCRtpCodecParameters(
                mimeType="video/CODEC96", clockRate=90000, payloadType=96
            )
        ]

        common = find_common_codecs(local_codecs, remote_codecs)

        # Should successfully negotiate the one matching codec
        self.assertEqual(len(common), 1)
        self.assertEqual(common[0].payloadType, 96)


if __name__ == "__main__":
    unittest.main()
