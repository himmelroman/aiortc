"""
Rigorous end-to-end validation test for TWCC/GCC.

This test validates that TWCC actually works by checking:
1. Transport-CC extension IS in SDP
2. RTP packets CONTAIN transport sequence numbers (wire format)
3. Receiver RECORDS packet arrivals
4. TWCC feedback CONTAINS packet results
5. Sender RECEIVES and PROCESSES feedback
6. GCC estimator UPDATES based on feedback

If ANY of these fail, TWCC is not working!
"""

import asyncio
import logging
import unittest
from fractions import Fraction

from aiortc import RTCPeerConnection
from aiortc.mediastreams import MediaStreamTrack
from av import VideoFrame

logging.basicConfig(level=logging.INFO)


class DummyVideoTrack(MediaStreamTrack):
    """Dummy video track that generates black frames."""

    kind = "video"

    def __init__(self):
        super().__init__()
        self.counter = 0

    async def recv(self):
        """Generate a black video frame."""
        self.counter += 1
        pts = self.counter
        time_base = Fraction(1, 30)

        frame = VideoFrame(width=640, height=480)
        frame.pts = pts
        frame.time_base = time_base

        for plane in frame.planes:
            plane.update(bytes(plane.buffer_size))

        await asyncio.sleep(1 / 30)
        return frame


class TWCCEndToEndValidationTest(unittest.TestCase):
    """
    Rigorous validation that TWCC actually works end-to-end.

    This test WILL FAIL if transport-cc extension is not in HEADER_EXTENSIONS.
    """

    def test_twcc_actually_exchanges_packet_feedback(self):
        """
        CRITICAL TEST: Verify TWCC feedback contains actual packet timing data.

        This test ensures:
        - Transport-CC extension is negotiated in SDP
        - Packets include transport sequence numbers on the wire
        - Receiver records packet arrivals
        - TWCC feedback is generated with packet data
        - Sender receives and processes feedback
        - GCC estimator gets updated
        """
        async def run():
            pc1 = RTCPeerConnection()
            pc2 = RTCPeerConnection()

            # Add video track
            track = DummyVideoTrack()
            sender = pc1.addTrack(track)

            # Enable GCC
            from aiortc.contrib.twcc.receiver import TransportSequenceNumberManager
            transport_seq_manager = TransportSequenceNumberManager()
            sender.enable_gcc(
                transport_seq_manager,
                initial_bitrate=500000,
                min_bitrate=100000,
                max_bitrate=2000000
            )

            # Create offer and check SDP
            offer = await pc1.createOffer()
            await pc1.setLocalDescription(offer)

            # CRITICAL CHECK #1: Transport-CC must be in SDP
            self.assertIn(
                "transport-wide-cc",
                offer.sdp,
                "CRITICAL FAILURE: transport-wide-cc extension NOT in SDP! "
                "TWCC will not work. Check HEADER_EXTENSIONS in codecs/__init__.py"
            )

            # Set up connection
            await pc2.setRemoteDescription(pc1.localDescription)
            answer = await pc2.createAnswer()
            await pc2.setLocalDescription(answer)
            await pc1.setRemoteDescription(pc2.localDescription)

            # Wait for ICE
            await asyncio.sleep(0.5)

            # Enable TWCC on receiver
            receiver = None
            for r in pc2.getReceivers():
                if r.track and r.track.kind == "video":
                    r.enable_twcc(ssrc=r._RTCRtpReceiver__rtcp_ssrc or 1)
                    receiver = r
                    break

            self.assertIsNotNone(receiver, "No video receiver found")

            # CRITICAL CHECK #2: TWCC recorder must be initialized
            self.assertIsNotNone(
                receiver._RTCRtpReceiver__twcc_recorder,
                "TWCC recorder not initialized on receiver"
            )

            # CRITICAL CHECK #3: Transport seq manager must exist on sender
            self.assertIsNotNone(
                sender._RTCRtpSender__transport_seq_manager,
                "Transport sequence manager not initialized on sender"
            )

            # Stream media for 3 seconds
            print("Streaming media for 3 seconds...")
            await asyncio.sleep(3)

            # CRITICAL CHECK #4: Packets must have been sent
            packets_sent = sender._RTCRtpSender__packet_count
            self.assertGreater(
                packets_sent,
                0,
                f"No packets sent! Expected > 0, got {packets_sent}"
            )
            print(f"✓ Packets sent: {packets_sent}")

            # CRITICAL CHECK #5: TWCC recorder must have recorded packets
            twcc_recorder = receiver._RTCRtpReceiver__twcc_recorder
            # Access internal state to verify packets were recorded
            with twcc_recorder._lock:
                recorded_count = len(twcc_recorder._arrival_times._arrivals)

            self.assertGreater(
                recorded_count,
                0,
                f"CRITICAL FAILURE: TWCC recorder recorded ZERO packets! "
                f"This means transport sequence numbers are NOT in RTP packets. "
                f"Expected > 0, got {recorded_count}"
            )
            print(f"✓ Packets recorded by TWCC: {recorded_count}")

            # CRITICAL CHECK #6: GCC must have received feedback
            if sender._RTCRtpSender__gcc_estimator:
                stats = sender._RTCRtpSender__gcc_estimator.get_stats()
                packets_received_via_feedback = stats["packets_received"]

                self.assertGreater(
                    packets_received_via_feedback,
                    0,
                    f"CRITICAL FAILURE: GCC received ZERO packets via TWCC feedback! "
                    f"This means TWCC feedback is not reaching the sender or contains no data. "
                    f"Expected > 0, got {packets_received_via_feedback}"
                )
                print(f"✓ Packets in GCC feedback: {packets_received_via_feedback}")
                print(f"✓ GCC estimate: {stats['current_estimate_kbps']:.1f} kbps")
            else:
                self.fail("GCC estimator not initialized")

            # CRITICAL CHECK #7: Sent packet tracker must have entries
            if sender._RTCRtpSender__sent_packet_tracker:
                # The tracker should have recent packets
                tracker = sender._RTCRtpSender__sent_packet_tracker
                with tracker._lock:
                    tracked_count = len(tracker._packets)

                self.assertGreater(
                    tracked_count,
                    0,
                    f"Sent packet tracker is empty! Expected > 0, got {tracked_count}"
                )
                print(f"✓ Packets in sent tracker: {tracked_count}")
            else:
                self.fail("Sent packet tracker not initialized")

            await pc1.close()
            await pc2.close()

            print("\n🎉 ALL CRITICAL CHECKS PASSED - TWCC is working end-to-end!")

        loop = asyncio.get_event_loop()
        loop.run_until_complete(run())


if __name__ == "__main__":
    unittest.main()
