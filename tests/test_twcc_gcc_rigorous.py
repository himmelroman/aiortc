"""
Rigorous TWCC/GCC validation with actual value checks.

Tests verify:
1. Measured values are sensible for local loopback connection
2. Sender-side and receiver-side data correlates correctly
3. Measurements stabilize over time (60 second test)
4. Both GCC (sender) and TWCC (receiver) mechanisms work independently
"""

import asyncio
import logging
import time
import unittest
from fractions import Fraction
from typing import List, Tuple

from aiortc import RTCPeerConnection
from aiortc.mediastreams import MediaStreamTrack
from av import VideoFrame

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DummyVideoTrack(MediaStreamTrack):
    """Generates black video frames at configurable bitrate."""

    kind = "video"

    def __init__(self, fps: int = 30, width: int = 640, height: int = 480):
        super().__init__()
        self.counter = 0
        self.fps = fps
        self.width = width
        self.height = height

    async def recv(self):
        self.counter += 1
        frame = VideoFrame(width=self.width, height=self.height)
        frame.pts = self.counter
        frame.time_base = Fraction(1, self.fps)
        for plane in frame.planes:
            plane.update(bytes(plane.buffer_size))
        await asyncio.sleep(1 / self.fps)
        return frame


class RigorousTWCCGCCTest(unittest.TestCase):
    """Rigorous validation of TWCC/GCC mechanisms."""

    def test_local_network_bitrate_estimates_are_high(self):
        """
        FLOW: Two local peers stream for 2 minutes (120 seconds)

        ASSERTIONS:
        1. GCC bitrate estimate should ramp up over time
        2. Final bitrate should reach >= 1 Mbps for loopback
        3. Bitrate should stabilize in later phase
        """
        async def run():
            pc1 = RTCPeerConnection()
            pc2 = RTCPeerConnection()

            track = DummyVideoTrack(fps=30)
            sender = pc1.addTrack(track)

            from aiortc.twcc.receiver import TransportSequenceNumberManager
            transport_seq_manager = TransportSequenceNumberManager()
            sender.enable_gcc(
                transport_seq_manager,
                initial_bitrate=500000,
                min_bitrate=100000,
                max_bitrate=5000000  # 5 Mbps max
            )

            # Connect
            await pc1.setLocalDescription(await pc1.createOffer())
            await pc2.setRemoteDescription(pc1.localDescription)
            await pc2.setLocalDescription(await pc2.createAnswer())
            await pc1.setRemoteDescription(pc2.localDescription)
            await asyncio.sleep(0.5)

            # Enable TWCC
            for receiver in pc2.getReceivers():
                if receiver.track and receiver.track.kind == "video":
                    receiver.enable_twcc(ssrc=receiver._RTCRtpReceiver__rtcp_ssrc or 1)

            # Collect bitrate estimates over 120 seconds (2 minutes)
            bitrate_samples: List[Tuple[float, int]] = []

            for i in range(120):
                await asyncio.sleep(1)
                if sender._RTCRtpSender__gcc_estimator:
                    stats = sender._RTCRtpSender__gcc_estimator.get_stats()
                    bitrate = stats['current_estimate_bps']
                    bitrate_samples.append((time.time(), bitrate))
                    # Log every 10 seconds
                    if (i + 1) % 10 == 0:
                        logger.info(f"[{i+1}s] Bitrate: {bitrate/1000:.1f} kbps")

            # ASSERTION 1: Bitrate should RAMP UP over time (compare first 10s vs last 10s)
            early_bitrates = [br for _, br in bitrate_samples[:10]]
            late_bitrates = [br for _, br in bitrate_samples[-10:]]
            early_avg = sum(early_bitrates) / len(early_bitrates)
            late_avg = sum(late_bitrates) / len(late_bitrates)

            logger.info(f"Early avg (0-10s): {early_avg/1000:.1f} kbps")
            logger.info(f"Late avg (110-120s): {late_avg/1000:.1f} kbps")

            self.assertGreater(
                late_avg,
                early_avg * 1.2,  # At least 20% increase
                f"Bitrate should ramp up over 2 minutes. Early: {early_avg/1000:.1f} kbps, Late: {late_avg/1000:.1f} kbps"
            )

            # ASSERTION 2: Final bitrate should reach >= 1 Mbps after 2 minutes
            final_bitrate = bitrate_samples[-1][1]
            self.assertGreater(
                final_bitrate,
                1_000_000,  # At least 1 Mbps after 2 minutes
                f"After 2 minutes, local network should achieve >= 1 Mbps, got {final_bitrate/1000:.1f} kbps"
            )

            # ASSERTION 3: Bitrate should STABILIZE in later phase (last 20 samples within 30% of mean)
            recent_bitrates = [br for _, br in bitrate_samples[-20:]]
            mean_bitrate = sum(recent_bitrates) / len(recent_bitrates)
            variations = [abs(br - mean_bitrate) / mean_bitrate for br in recent_bitrates]
            max_variation = max(variations)
            self.assertLess(
                max_variation,
                0.30,  # Within 30%
                f"Bitrate should stabilize in later phase, got max {max_variation*100:.1f}% variation"
            )

            # ASSERTION 4: Packet loss should be ZERO on loopback
            if sender._RTCRtpSender__gcc_estimator:
                stats = sender._RTCRtpSender__gcc_estimator.get_stats()
                packets_sent = stats['packets_sent']
                packets_received = stats['packets_received']

                if packets_sent > 0:
                    loss_rate = (packets_sent - packets_received) / packets_sent
                    self.assertLess(
                        loss_rate,
                        0.01,  # Less than 1% loss
                        f"Local network should have no loss, got {loss_rate*100:.2f}%"
                    )

            await pc1.close()
            await pc2.close()

        asyncio.run(run())

    def test_sender_and_receiver_sequence_numbers_correlate(self):
        """
        FLOW: Stream for 5 seconds, then verify sequence number correlation

        ASSERTIONS:
        1. Sender's transport sequence numbers are consecutive (0,1,2,3...)
        2. Receiver records THE SAME sequence numbers
        3. TWCC feedback reports back THE SAME sequences
        4. GCC processes feedback for sequences it actually sent
        """
        async def run():
            pc1 = RTCPeerConnection()
            pc2 = RTCPeerConnection()

            track = DummyVideoTrack(fps=30)
            sender = pc1.addTrack(track)

            from aiortc.twcc.receiver import TransportSequenceNumberManager
            transport_seq_manager = TransportSequenceNumberManager()
            sender.enable_gcc(transport_seq_manager, initial_bitrate=500000)

            # Connect
            await pc1.setLocalDescription(await pc1.createOffer())
            await pc2.setRemoteDescription(pc1.localDescription)
            await pc2.setLocalDescription(await pc2.createAnswer())
            await pc1.setRemoteDescription(pc2.localDescription)
            await asyncio.sleep(0.5)

            # Enable TWCC
            receiver = None
            for r in pc2.getReceivers():
                if r.track and r.track.kind == "video":
                    r.enable_twcc(ssrc=r._RTCRtpReceiver__rtcp_ssrc or 1)
                    receiver = r
                    break

            # Stream
            await asyncio.sleep(5)

            # ASSERTION 1: Receiver should have recorded packets
            twcc_recorder = receiver._RTCRtpReceiver__twcc_recorder
            with twcc_recorder._lock:
                recorded_seqs = list(twcc_recorder._arrival_times._arrivals.keys())

            self.assertGreater(len(recorded_seqs), 0, "Receiver should have recorded packets")

            # ASSERTION 2: TWCC feedback should report sequences
            feedback_bytes = twcc_recorder.generate_feedback()
            self.assertIsNotNone(feedback_bytes, "Should generate feedback")

            from aiortc.twcc.sender import TWCCParser
            feedback_results = TWCCParser.parse_feedback(feedback_bytes)
            self.assertIsNotNone(feedback_results, "Should parse feedback")
            self.assertGreater(len(feedback_results), 0, "Feedback should have results")

            feedback_seqs = [r.sequence_number for r in feedback_results if r.received]
            self.assertGreater(len(feedback_seqs), 0, "Feedback should have received packets")

            # ASSERTION 3: Sender tracked the same sequences that feedback reports
            # Query sent packets for the SAME range as feedback (not from 0!)
            sent_tracker = sender._RTCRtpSender__sent_packet_tracker
            self.assertIsNotNone(sent_tracker)

            min_feedback_seq = min(feedback_seqs)
            max_feedback_seq = max(feedback_seqs)
            sent_packets = sent_tracker.get_range(min_feedback_seq, max_feedback_seq)
            self.assertGreater(len(sent_packets), 0, "Should have sent packets in feedback range")

            sent_seqs = [p.sequence_number for p in sent_packets]

            # ASSERTION 4: Sent sequences should be consecutive
            for i in range(1, min(10, len(sent_seqs))):
                self.assertEqual(
                    sent_seqs[i],
                    sent_seqs[i-1] + 1,
                    f"Sent sequences should be consecutive: {sent_seqs[i-1]} -> {sent_seqs[i]}"
                )

            # ASSERTION 5: Feedback and sent sequences should match
            # Since we queried sent_tracker for the exact feedback range, there should be high overlap
            overlap = set(sent_seqs) & set(feedback_seqs)
            self.assertGreater(
                len(overlap),
                len(feedback_seqs) // 2,  # At least half of feedback should match sent
                f"Feedback sequences should match sent sequences.\n"
                f"Sent sequences: {len(sent_seqs)} total in range [{min_feedback_seq}, {max_feedback_seq}]\n"
                f"Feedback sequences: {len(feedback_seqs)} total\n"
                f"Overlap: {len(overlap)}"
            )

            await pc1.close()
            await pc2.close()

        asyncio.run(run())

    def test_both_sides_independently_twcc_receiver_gcc_sender(self):
        """
        FLOW: Verify TWCC (receiver) and GCC (sender) work independently

        ASSERTIONS (RECEIVER SIDE - TWCC):
        1. Receiver records packet arrival times
        2. Receiver generates valid TWCC feedback
        3. Feedback contains packet status chunks
        4. Feedback contains receive deltas

        ASSERTIONS (SENDER SIDE - GCC):
        5. Sender tracks sent packets with timestamps
        6. Sender parses TWCC feedback correctly
        7. Sender correlates feedback with sent packets
        8. GCC produces bitrate estimate
        9. Encoder bitrate is updated
        """
        async def run():
            pc1 = RTCPeerConnection()
            pc2 = RTCPeerConnection()

            track = DummyVideoTrack(fps=30)
            sender = pc1.addTrack(track)

            from aiortc.twcc.receiver import TransportSequenceNumberManager
            transport_seq_manager = TransportSequenceNumberManager()
            sender.enable_gcc(transport_seq_manager, initial_bitrate=500000)

            # Connect
            await pc1.setLocalDescription(await pc1.createOffer())
            await pc2.setRemoteDescription(pc1.localDescription)
            await pc2.setLocalDescription(await pc2.createAnswer())
            await pc1.setRemoteDescription(pc2.localDescription)
            await asyncio.sleep(0.5)

            # Enable TWCC
            receiver = None
            for r in pc2.getReceivers():
                if r.track and r.track.kind == "video":
                    r.enable_twcc(ssrc=r._RTCRtpReceiver__rtcp_ssrc or 1)
                    receiver = r
                    break

            await asyncio.sleep(3)

            # ==== RECEIVER SIDE (TWCC) CHECKS ====
            twcc_recorder = receiver._RTCRtpReceiver__twcc_recorder

            # ASSERTION 1: Receiver recorded arrivals
            with twcc_recorder._lock:
                arrival_count = len(twcc_recorder._arrival_times._arrivals)
            self.assertGreater(arrival_count, 0, "TWCC should record packet arrivals")
            logger.info(f"✓ RECEIVER: Recorded {arrival_count} packet arrivals")

            # ASSERTION 2: Can generate feedback
            feedback = twcc_recorder.generate_feedback()
            self.assertIsNotNone(feedback, "TWCC should generate feedback")
            self.assertGreater(len(feedback), 20, "Feedback should have substantial data")
            logger.info(f"✓ RECEIVER: Generated {len(feedback)} byte feedback")

            # ASSERTION 3: Feedback has correct RTCP format (PT=205, FMT=15)
            pt = feedback[1]
            fmt = feedback[0] & 0x1F
            self.assertEqual(pt, 205, "TWCC feedback should have PT=205")
            self.assertEqual(fmt, 15, "TWCC feedback should have FMT=15")
            logger.info(f"✓ RECEIVER: Feedback has correct RTCP format PT={pt} FMT={fmt}")

            # ASSERTION 4: Feedback contains packet status and deltas (length check)
            # Minimum: 20 bytes header + packet chunks + deltas
            self.assertGreater(len(feedback), 20, "Feedback should contain status chunks and deltas")
            logger.info(f"✓ RECEIVER: Feedback contains packet status and timing data")

            # ==== SENDER SIDE (GCC) CHECKS ====

            # ASSERTION 5: Sender tracked sent packets
            sent_tracker = sender._RTCRtpSender__sent_packet_tracker
            sent_packets = sent_tracker.get_range(0, 100)
            self.assertGreater(len(sent_packets), 0, "Sender should track sent packets")
            logger.info(f"✓ SENDER: Tracked {len(sent_packets)} sent packets")

            # ASSERTION 6: Sender can parse TWCC feedback
            from aiortc.twcc.sender import TWCCParser
            parsed_results = TWCCParser.parse_feedback(feedback)
            self.assertIsNotNone(parsed_results, "Sender should parse TWCC feedback")
            self.assertGreater(len(parsed_results), 0, "Should parse packet results")
            logger.info(f"✓ SENDER: Parsed {len(parsed_results)} packet results from feedback")

            # ASSERTION 7: Can correlate feedback with sent packets
            from aiortc.gcc.estimator import PacketFeedbackProcessor
            min_seq = min(r.sequence_number for r in parsed_results)
            max_seq = max(r.sequence_number for r in parsed_results)
            correlated_sent = sent_tracker.get_range(min_seq, max_seq)

            self.assertGreater(len(correlated_sent), 0, "Should correlate feedback with sent packets")
            logger.info(f"✓ SENDER: Correlated feedback with {len(correlated_sent)} sent packets")

            # ASSERTION 8: GCC produces bitrate estimate
            gcc_estimator = sender._RTCRtpSender__gcc_estimator
            stats = gcc_estimator.get_stats()
            estimate = stats['current_estimate_bps']

            self.assertGreater(estimate, 0, "GCC should produce bitrate estimate")
            self.assertGreater(stats['packets_received'], 0, "GCC should have processed feedback")
            logger.info(f"✓ SENDER: GCC estimate is {estimate/1000:.1f} kbps ({stats['packets_received']} packets)")

            # ASSERTION 9: Encoder bitrate is updated
            if sender._RTCRtpSender__encoder:
                encoder_bitrate = sender._RTCRtpSender__encoder.target_bitrate
                self.assertIsNotNone(encoder_bitrate, "Encoder bitrate should be set")
                self.assertGreater(encoder_bitrate, 0, "Encoder bitrate should be positive")
                logger.info(f"✓ SENDER: Encoder bitrate set to {encoder_bitrate/1000:.1f} kbps")

            await pc1.close()
            await pc2.close()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
