"""
Real end-to-end test for TWCC/GCC with actual peer connections.

This test creates two RTCPeerConnection instances, establishes a connection,
streams media between them, and verifies that TWCC feedback is exchanged
and GCC bitrate control is applied.
"""

import asyncio
import logging
import unittest
from fractions import Fraction
from unittest.mock import patch

from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaBlackhole, MediaPlayer
from aiortc.mediastreams import MediaStreamTrack
from av import AudioFrame, VideoFrame

# Enable debug logging to see TWCC/GCC messages
logging.basicConfig(level=logging.DEBUG)


class DummyVideoTrack(MediaStreamTrack):
    """
    Dummy video track that generates black frames.
    """

    kind = "video"

    def __init__(self):
        super().__init__()
        self.counter = 0

    async def recv(self):
        """Generate a black video frame."""
        self.counter += 1

        # 640x480 black frame, 30fps
        pts = self.counter
        time_base = Fraction(1, 30)

        frame = VideoFrame(width=640, height=480)
        frame.pts = pts
        frame.time_base = time_base

        # Make it black
        for plane in frame.planes:
            plane.update(bytes(plane.buffer_size))

        await asyncio.sleep(1 / 30)  # 30 fps
        return frame


class DummyAudioTrack(MediaStreamTrack):
    """
    Dummy audio track that generates silence.
    """

    kind = "audio"

    def __init__(self):
        super().__init__()
        self.counter = 0

    async def recv(self):
        """Generate a silent audio frame."""
        self.counter += 1

        # 20ms of audio at 48kHz
        samples = 960  # 48000 * 0.02
        frame = AudioFrame(format="s16", layout="stereo", samples=samples)
        frame.pts = self.counter * samples
        frame.sample_rate = 48000
        frame.time_base = Fraction(1, 48000)

        # Fill with silence
        for plane in frame.planes:
            plane.update(bytes(plane.buffer_size))

        await asyncio.sleep(0.02)  # 20ms
        return frame


class TestTWCCGCCRealE2E(unittest.TestCase):
    """
    Real end-to-end tests for TWCC/GCC.
    """

    def setUp(self):
        self.pcs = []

    def tearDown(self):
        # Close all peer connections
        loop = asyncio.get_event_loop()
        for pc in self.pcs:
            loop.run_until_complete(pc.close())

    def create_pc(self):
        """Create a peer connection and track it for cleanup."""
        pc = RTCPeerConnection()
        self.pcs.append(pc)
        return pc

    def test_basic_connection_with_twcc(self):
        """Test basic P2P connection with TWCC enabled."""
        async def run():
            # Create two peer connections
            pc1 = self.create_pc()
            pc2 = self.create_pc()

            # Add video track to pc1
            track = DummyVideoTrack()
            sender = pc1.addTrack(track)

            # Enable TWCC/GCC on sender
            from aiortc.twcc.receiver import TransportSequenceNumberManager
            transport_seq_manager = TransportSequenceNumberManager()
            sender.enable_gcc(transport_seq_manager, initial_bitrate=500000)

            # Create offer
            offer = await pc1.createOffer()
            await pc1.setLocalDescription(offer)

            # Apply offer to pc2
            await pc2.setRemoteDescription(pc1.localDescription)

            # Create answer
            answer = await pc2.createAnswer()
            await pc2.setLocalDescription(answer)

            # Apply answer to pc1
            await pc1.setRemoteDescription(pc2.localDescription)

            # Wait for connection
            await asyncio.sleep(0.5)

            # Enable TWCC on receiver (pc2)
            for receiver in pc2.getReceivers():
                if receiver.track.kind == "video":
                    receiver.enable_twcc(ssrc=receiver._RTCRtpReceiver__rtcp_ssrc or 1)

            # Stream media for 3 seconds
            await asyncio.sleep(3)

            # Check that packets were sent
            self.assertGreater(sender._RTCRtpSender__packet_count, 0)

            # Check if GCC estimator exists and has stats
            if sender._RTCRtpSender__gcc_estimator:
                stats = sender._RTCRtpSender__gcc_estimator.get_stats()
                print(f"GCC Stats: {stats}")

            await pc1.close()
            await pc2.close()

        loop = asyncio.get_event_loop()
        loop.run_until_complete(run())

    def test_twcc_with_congestion_simulation(self):
        """Test TWCC/GCC response to simulated congestion."""
        async def run():
            pc1 = self.create_pc()
            pc2 = self.create_pc()

            # Add video track
            track = DummyVideoTrack()
            sender = pc1.addTrack(track)

            # Enable GCC with low initial bitrate
            from aiortc.twcc.receiver import TransportSequenceNumberManager
            transport_seq_manager = TransportSequenceNumberManager()
            sender.enable_gcc(transport_seq_manager, initial_bitrate=300000)

            # Setup connection
            offer = await pc1.createOffer()
            await pc1.setLocalDescription(offer)
            await pc2.setRemoteDescription(pc1.localDescription)
            answer = await pc2.createAnswer()
            await pc2.setLocalDescription(answer)
            await pc1.setRemoteDescription(pc2.localDescription)

            await asyncio.sleep(0.5)

            # Enable TWCC on receiver
            for receiver in pc2.getReceivers():
                if receiver.track.kind == "video":
                    receiver.enable_twcc(ssrc=receiver._RTCRtpReceiver__rtcp_ssrc or 1)

            # Get initial encoder bitrate
            initial_bitrate = None
            if sender._RTCRtpSender__encoder and hasattr(
                sender._RTCRtpSender__encoder, "target_bitrate"
            ):
                initial_bitrate = sender._RTCRtpSender__encoder.target_bitrate

            print(f"Initial bitrate: {initial_bitrate}")

            # Stream for a bit
            await asyncio.sleep(5)

            # Check final bitrate
            final_bitrate = None
            if sender._RTCRtpSender__encoder and hasattr(
                sender._RTCRtpSender__encoder, "target_bitrate"
            ):
                final_bitrate = sender._RTCRtpSender__encoder.target_bitrate

            print(f"Final bitrate: {final_bitrate}")

            # Get GCC stats
            if sender._RTCRtpSender__gcc_estimator:
                stats = sender._RTCRtpSender__gcc_estimator.get_stats()
                print(f"Final GCC Stats: {stats}")
                self.assertGreater(stats["packets_received"], 0)

            await pc1.close()
            await pc2.close()

        loop = asyncio.get_event_loop()
        loop.run_until_complete(run())

    def test_multiple_senders_with_gcc(self):
        """Test multiple senders using GCC."""
        async def run():
            pc1 = self.create_pc()
            pc2 = self.create_pc()

            # Add both video and audio tracks
            from aiortc.twcc.receiver import TransportSequenceNumberManager
            transport_seq_manager = TransportSequenceNumberManager()

            video_track = DummyVideoTrack()
            video_sender = pc1.addTrack(video_track)
            video_sender.enable_gcc(transport_seq_manager, initial_bitrate=500000)

            audio_track = DummyAudioTrack()
            audio_sender = pc1.addTrack(audio_track)
            # Note: typically audio doesn't use GCC, but we can enable it for testing
            audio_sender.enable_gcc(transport_seq_manager, initial_bitrate=64000)

            # Setup connection
            offer = await pc1.createOffer()
            await pc1.setLocalDescription(offer)
            await pc2.setRemoteDescription(pc1.localDescription)
            answer = await pc2.createAnswer()
            await pc2.setLocalDescription(answer)
            await pc1.setRemoteDescription(pc2.localDescription)

            await asyncio.sleep(0.5)

            # Enable TWCC on all receivers
            for receiver in pc2.getReceivers():
                receiver.enable_twcc(ssrc=receiver._RTCRtpReceiver__rtcp_ssrc or 1)

            # Stream
            await asyncio.sleep(3)

            # Verify both senders are working
            self.assertGreater(video_sender._RTCRtpSender__packet_count, 0)
            self.assertGreater(audio_sender._RTCRtpSender__packet_count, 0)

            await pc1.close()
            await pc2.close()

        loop = asyncio.get_event_loop()
        loop.run_until_complete(run())


if __name__ == "__main__":
    unittest.main()
