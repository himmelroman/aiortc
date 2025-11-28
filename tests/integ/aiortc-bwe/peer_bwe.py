#!/usr/bin/env python3
"""
Loss-based BWE bi-directional test peer (aiortc side).

Uses simple loss-based bandwidth estimation instead of TWCC/GCC.
Based on IETF RFC draft-ietf-rmcat-gcc-02 Section 6.
"""

import argparse
import asyncio
import json
import logging
import os
from aiohttp import web

from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.contrib.media import MediaBlackhole
from av import VideoFrame

# Import loss-based BWE (local module, not from aiortc fork)
from loss_based_bwe import LossBasedBandwidthEstimator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Enable DEBUG logging for REMB visibility
logging.getLogger("aiortc.rtcrtpreceiver").setLevel(logging.DEBUG)


class ColorBarVideoTrack(VideoStreamTrack):
    """
    Generates high-bitrate video with noise for testing congestion control.
    Pre-generates 5 seconds of frames (150 frames at 30fps) and loops them.
    """

    def __init__(self):
        super().__init__()
        self.counter = 0
        self._frame_cache = []
        self._cache_ready = False

        logger.info("Pre-generating 5 seconds of video frames...")
        self._pregenerate_frames()
        logger.info(f"Pre-generated {len(self._frame_cache)} frames")

    def _pregenerate_frames(self):
        """Pre-generate 5 seconds of frames (150 frames at 30fps)"""
        import numpy as np

        num_frames = 150  # 5 seconds at 30fps

        for i in range(num_frames):
            # Create frame (640x480 for moderate bitrate - matches pion)
            frame = VideoFrame(width=640, height=480, format='yuv420p')

            # Fill with moderate noise (reduced from 70% to 30% for more realistic compression)
            for p in frame.planes:
                # Generate random data for each plane
                arr = np.frombuffer(p, dtype=np.uint8)
                # Mix of random noise and pattern to simulate complex video
                noise = np.random.randint(0, 256, size=arr.shape, dtype=np.uint8)
                pattern = ((i + np.arange(arr.shape[0])) % 256).astype(np.uint8)
                arr[:] = (noise * 0.3 + pattern * 0.7).astype(np.uint8)

            self._frame_cache.append(frame)

        self._cache_ready = True

    async def recv(self):
        pts, time_base = await self.next_timestamp()

        # Get pre-generated frame from cache (loop after 150 frames)
        frame_index = self.counter % len(self._frame_cache)
        frame = self._frame_cache[frame_index]

        # Update PTS and time_base for this transmission
        frame.pts = pts
        frame.time_base = time_base

        self.counter += 1
        return frame


class LossBWEPeer:
    def __init__(self):
        self.pcs = set()
        # Track per-connection state for proper reconnection support
        self.pc_bwe = {}  # pc -> LossBasedBandwidthEstimator
        self.pc_monitor_tasks = {}  # pc -> monitor task
        self.received_video_tasks = {}
        self.connection_count = 0  # Track connection attempts

    def _remove_remb_from_sdp(self, sdp):
        """Remove goog-remb from SDP to prevent REMB conflicts with loss-based BWE."""
        lines = sdp.split('\r\n')
        filtered_lines = [line for line in lines if 'goog-remb' not in line]
        return '\r\n'.join(filtered_lines)

    async def offer(self, request):
        params = await request.json()
        offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

        pc = RTCPeerConnection()
        self.pcs.add(pc)

        self.connection_count += 1
        conn_id = self.connection_count
        logger.info(f"🔄 Connection #{conn_id}: New offer received")

        @pc.on("connectionstatechange")
        async def on_connectionstatechange():
            logger.info(f"🔄 Connection #{conn_id}: State changed to {pc.connectionState}")
            if pc.connectionState == "failed" or pc.connectionState == "closed":
                # Clean up per-connection resources
                if pc in self.pc_monitor_tasks:
                    self.pc_monitor_tasks[pc].cancel()
                    del self.pc_monitor_tasks[pc]
                if pc in self.pc_bwe:
                    del self.pc_bwe[pc]

                await pc.close()
                self.pcs.discard(pc)
                logger.info(f"🔄 Connection #{conn_id}: Cleaned up, ready for reconnection")

        # Add video track with loss-based BWE
        video_track = ColorBarVideoTrack()
        video_sender = pc.addTrack(video_track)

        # Create per-connection BWE instance (fresh state for each connection)
        logger.info(f"🔄 Connection #{conn_id}: Enabling Loss-based BWE on sender")
        self.pc_bwe[pc] = LossBasedBandwidthEstimator(
            initial_bitrate=500_000,      # 500 kbps initial (conservative start)
            min_bitrate=500_000,          # 500 kbps min
            max_bitrate=50_000_000        # 50 Mbps max
        )

        # Start per-connection BWE monitoring
        self.pc_monitor_tasks[pc] = asyncio.create_task(
            self.monitor_loss_bwe(pc, video_sender)
        )

        # Handle remote tracks (peer will send video)
        @pc.on("track")
        def on_track(track):
            logger.info(f"📹 Receiving {track.kind} track from peer (id={track.id})")

            if track.kind == "video":
                # Get the receiver to monitor RTCP RR stats
                receiver = pc.getReceivers()[0]  # Get first (and only) receiver

                # Start monitoring received video with RTCP stats
                task = asyncio.create_task(self.monitor_received_video(track, track.id, receiver))
                self.received_video_tasks[track.id] = task

            @track.on("ended")
            async def on_ended():
                logger.info(f"Track {track.kind} ended")
                # Cancel monitoring task if exists
                if track.id in self.received_video_tasks:
                    self.received_video_tasks[track.id].cancel()
                    del self.received_video_tasks[track.id]

        # Set remote description
        await pc.setRemoteDescription(offer)

        # Create answer
        answer = await pc.createAnswer()

        # Remove REMB from SDP to prevent conflicts with loss-based BWE
        modified_sdp = self._remove_remb_from_sdp(answer.sdp)
        modified_answer = RTCSessionDescription(sdp=modified_sdp, type=answer.type)
        await pc.setLocalDescription(modified_answer)

        logger.info("✅ SDP negotiation complete (REMB disabled - using loss-based BWE)")
        logger.info("📵 REMB signaling removed from SDP")

        return web.Response(
            content_type="application/json",
            text=json.dumps(
                {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}
            ),
        )

    async def monitor_loss_bwe(self, pc, video_sender):
        """Monitor packet loss and adjust bitrate using loss-based BWE."""
        import time
        await asyncio.sleep(3)  # Wait for connection to stabilize

        last_rtcp_timestamp = None
        stale_count = 0

        while True:
            try:
                # Check if connection is still alive
                if pc.connectionState in ('closed', 'failed'):
                    logger.info(f"⚠️  Connection {pc.connectionState}, stopping BWE monitoring")
                    break

                # Get RTP sender stats
                stats = await video_sender.getStats()

                # Collect stats from both outbound-rtp and remote-inbound-rtp
                packets_sent = 0
                packets_lost = 0
                rtcp_timestamp = None

                for stat in stats.values():
                    if stat.type == 'outbound-rtp' and stat.kind == 'video':
                        packets_sent = stat.packetsSent

                        # DEBUG: Dump ALL attributes of outbound-rtp stat object
                        all_attrs = {k: v for k, v in vars(stat).items() if not k.startswith('_')}
                        logger.info(f"📤 outbound-rtp stat object dump: {all_attrs}")
                    elif stat.type == 'remote-inbound-rtp' and stat.kind == 'video':
                        # This comes from RTCP RR - actual loss reported by receiver
                        packets_lost = getattr(stat, 'packetsLost', 0)
                        rtcp_timestamp = stat.timestamp

                        # DEBUG: Dump ALL attributes of the remote-inbound-rtp stat object
                        all_attrs = {k: v for k, v in vars(stat).items() if not k.startswith('_')}
                        logger.info(f"🔍 RTCP RR remote-inbound-rtp stat object dump: {all_attrs}")

                # Detect stale RTCP timestamps
                if rtcp_timestamp is not None:
                    if last_rtcp_timestamp == rtcp_timestamp:
                        stale_count += 1
                        if stale_count >= 3:
                            logger.warning(
                                f"⚠️  RTCP RR timestamp frozen at {rtcp_timestamp} "
                                f"for {stale_count} iterations - connection may be dead"
                            )
                    else:
                        stale_count = 0
                    last_rtcp_timestamp = rtcp_timestamp

                # Only update if we have valid data
                if packets_sent > 0:
                    # Get per-connection BWE instance
                    if pc not in self.pc_bwe:
                        logger.warning("⚠️  BWE instance not found for connection, stopping monitoring")
                        break

                    # Update loss-based estimate
                    self.pc_bwe[pc].update_loss_estimate(
                        packets_sent=packets_sent,
                        packets_lost=packets_lost
                    )

                    # Get new target bitrate
                    bwe_stats = self.pc_bwe[pc].get_stats()
                    estimate_kbps = bwe_stats['current_estimate_kbps']
                    estimate_mbps = estimate_kbps / 1000

                    # Apply to encoder
                    if hasattr(video_sender, '_RTCRtpSender__encoder'):
                        encoder = video_sender._RTCRtpSender__encoder
                        if encoder:
                            encoder.target_bitrate = int(estimate_kbps * 1000)

                    logger.info(
                        f"📤 Sending Loss BWE: {estimate_mbps:.2f} Mbps "
                        f"(loss={bwe_stats['average_loss']:.3f}) (aiortc→peer)"
                    )

                await asyncio.sleep(1)  # Update every second
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error monitoring loss BWE: {e}")
                await asyncio.sleep(1)

    async def monitor_received_video(self, track, track_id, receiver):
        """Monitor received video from peer and log RTCP RR stats."""
        import time

        # Start consuming the track
        frame_count = 0
        byte_count = 0
        last_log_time = time.time()
        last_log_bytes = 0
        last_log_frames = 0

        async def consume_track():
            nonlocal frame_count, byte_count
            try:
                while True:
                    frame = await track.recv()
                    frame_count += 1
                    # This is DECODED frame data (not RTP packet size!)
                    byte_count += len(frame.data) if hasattr(frame, 'data') else 10000
            except Exception as e:
                logger.debug(f"Track consumer ended: {e}")

        # Start consumer task
        consumer_task = asyncio.create_task(consume_track())

        await asyncio.sleep(3)  # Wait for stream to stabilize

        log_counter = 0
        try:
            while True:
                await asyncio.sleep(2)  # Check every 2 seconds
                log_counter += 1

                current_time = time.time()
                elapsed = current_time - last_log_time

                # Calculate decoded frame bitrate
                bytes_diff = byte_count - last_log_bytes
                frames_diff = frame_count - last_log_frames

                decoded_bitrate_mbps = (bytes_diff * 8) / elapsed / 1_000_000 if elapsed > 0 else 0

                # Log RTCP RR stats every 5th iteration (every 10 seconds)
                if log_counter % 5 == 0:
                    try:
                        receiver_stats = await receiver.getStats()
                        for stat in receiver_stats.values():
                            if stat.type == 'inbound-rtp' and stat.kind == 'video':
                                logger.info(
                                    f"📡 RTCP RR: received={stat.packetsReceived}, "
                                    f"lost={stat.packetsLost}, jitter={stat.jitter:.3f}s"
                                )
                                break
                    except Exception as e:
                        logger.warning(f"Failed to get receiver stats: {e}")

                logger.info(
                    f"📥 Decoded Frames: {decoded_bitrate_mbps:.2f} Mbps (peer→aiortc) | "
                    f"Frames: {frame_count}"
                )

                last_log_time = current_time
                last_log_bytes = byte_count
                last_log_frames = frame_count

        except asyncio.CancelledError:
            consumer_task.cancel()
            try:
                await consumer_task
            except asyncio.CancelledError:
                pass
        except Exception as e:
            logger.error(f"Error monitoring received video: {e}")
            consumer_task.cancel()

    async def on_shutdown(self, app):
        # Cancel all per-connection BWE monitoring tasks
        for task in self.pc_monitor_tasks.values():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.pc_monitor_tasks.clear()
        self.pc_bwe.clear()

        # Cancel received video monitoring tasks
        for task in self.received_video_tasks.values():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.received_video_tasks.clear()

        # Close all peer connections
        coros = [pc.close() for pc in self.pcs]
        await asyncio.gather(*coros)
        self.pcs.clear()


def main():
    parser = argparse.ArgumentParser(description="Loss-based BWE Test Peer (aiortc)")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8080, help="Port to bind to")
    args = parser.parse_args()

    peer = LossBWEPeer()

    app = web.Application()
    app.router.add_post("/offer", peer.offer)
    app.on_shutdown.append(peer.on_shutdown)

    logger.info(f"Starting aiortc Loss-based BWE peer on {args.host}:{args.port}")
    logger.info("Using RFC-compliant loss-based bandwidth estimation")

    web.run_app(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
