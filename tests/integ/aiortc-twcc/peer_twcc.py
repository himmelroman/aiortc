#!/usr/bin/env python3
"""
TWCC bi-directional test peer (aiortc side).

Sends and receives video with TWCC/GCC enabled for bi-directional testing.
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

# Import TWCC components
import sys
sys.path.insert(0, '../../../src')
from aiortc.twcc.receiver import TransportSequenceNumberManager
from aiortc.loop_monitor import start_loop_monitor
from aiortc.cprofile_helper import start_cprofile, stop_and_dump_cprofile

logging.basicConfig(level=logging.INFO)
# NOTE: Disabling ALL DEBUG logging to minimize event loop overhead
# Even non-per-packet debug logs cause traceback generation (9% CPU time)
# Keep only INFO level for GCC stats monitoring
logger = logging.getLogger(__name__)


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


class TWCCPeer:
    def __init__(self):
        self.pcs = set()
        self.transport_seq_manager = TransportSequenceNumberManager()
        self.gcc_monitor_task = None
        self.received_video_tasks = {}
        self.loop_monitor_started = False

    async def offer(self, request):
        params = await request.json()
        offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

        pc = RTCPeerConnection()
        self.pcs.add(pc)

        @pc.on("connectionstatechange")
        async def on_connectionstatechange():
            logger.info(f"Connection state: {pc.connectionState}")
            if pc.connectionState == "failed" or pc.connectionState == "closed":
                await pc.close()
                self.pcs.discard(pc)

        # Add video track with TWCC
        video_track = ColorBarVideoTrack()
        video_sender = pc.addTrack(video_track)

        # Enable GCC on sender
        # NOTE: High bitrates needed because random noise video is incompressible
        # VP8 cannot compress random noise below ~30 Mbps even at lowest quality
        logger.info("Enabling GCC with TWCC on sender")
        video_sender.enable_gcc(
            self.transport_seq_manager,
            initial_bitrate=10_000_000,  # 10 Mbps initial (realistic for noise)
            min_bitrate=1_000_000,       # 1 Mbps min
            max_bitrate=50_000_000       # 50 Mbps max (allows natural bitrate)
        )

        # Start GCC stats monitoring
        if self.gcc_monitor_task is None:
            self.gcc_monitor_task = asyncio.create_task(self.monitor_gcc_stats(video_sender))

        # DISABLED: Profiling adds overhead that affects timing measurements
        # # Start loop monitor and cProfile profiling (once)
        # if not self.loop_monitor_started:
        #     from aiortc.loop_monitor import start_loop_monitor
        #     start_loop_monitor(check_interval=2.0)
        #     start_cprofile()
        #     self.loop_monitor_started = True
        #     logger.info("🔍 Loop monitor and cProfile started")
        #
        #     # Schedule profiler dump after 8 seconds
        #     async def dump_profile_later():
        #         await asyncio.sleep(8)
        #         stop_and_dump_cprofile(top_n=50)
        #         logger.info("🔍 cProfile dumped")
        #
        #     asyncio.create_task(dump_profile_later())

        # Handle remote tracks (Pion will send video)
        @pc.on("track")
        def on_track(track):
            logger.info(f"📹 Receiving {track.kind} track from Pion peer (id={track.id})")

            if track.kind == "video":
                # Start monitoring received video
                task = asyncio.create_task(self.monitor_received_video(track, track.id))
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

        # Enable TWCC on any receivers
        for receiver in pc.getReceivers():
            if receiver.track and receiver.track.kind == "video":
                ssrc = receiver._RTCRtpReceiver__rtcp_ssrc or 1
                logger.info(f"Enabling TWCC on receiver with SSRC {ssrc}")
                receiver.enable_twcc(ssrc=ssrc)

        # Create answer
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        # Log SDP to verify TWCC extension
        if "transport" in pc.localDescription.sdp and "cc" in pc.localDescription.sdp:
            logger.info("✅ TWCC extension present in answer SDP")
        else:
            logger.warning("⚠️  TWCC extension NOT found in answer SDP")

        return web.Response(
            content_type="application/json",
            text=json.dumps(
                {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}
            ),
        )

    async def monitor_gcc_stats(self, video_sender):
        """Monitor and log GCC bandwidth estimates periodically."""
        await asyncio.sleep(3)  # Wait for connection to stabilize

        while True:
            try:
                # Access private __gcc_estimator attribute
                gcc_estimator = getattr(video_sender, '_RTCRtpSender__gcc_estimator', None)
                if gcc_estimator:
                    stats = gcc_estimator.get_stats()
                    estimate_kbps = stats['current_estimate_kbps']
                    estimate_mbps = estimate_kbps / 1000
                    logger.info(f"📤 Sending GCC: {estimate_mbps:.2f} Mbps (aiortc→pion)")
                else:
                    logger.debug("GCC estimator not available yet")

                await asyncio.sleep(1)  # Log every second
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error monitoring GCC stats: {e}")
                await asyncio.sleep(1)

    async def monitor_received_video(self, track, track_id):
        """Monitor received video from Pion peer.

        NOTE: This monitors DECODED FRAMES, not RTP packets.
        For actual RTP packet stats, see rtcrtpreceiver logs (🔍 RX:)
        """
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

        try:
            while True:
                await asyncio.sleep(2)  # Check every 2 seconds

                current_time = time.time()
                elapsed = current_time - last_log_time

                # Calculate decoded frame bitrate (NOT RTP packet bitrate!)
                bytes_diff = byte_count - last_log_bytes
                frames_diff = frame_count - last_log_frames

                decoded_bitrate_mbps = (bytes_diff * 8) / elapsed / 1_000_000 if elapsed > 0 else 0

                logger.info(f"📥 Decoded Frames: {decoded_bitrate_mbps:.2f} Mbps (pion→aiortc) | Frames: {frame_count}")

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
        # Cancel monitoring tasks
        if self.gcc_monitor_task:
            self.gcc_monitor_task.cancel()
            try:
                await self.gcc_monitor_task
            except asyncio.CancelledError:
                pass

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
    parser = argparse.ArgumentParser(description="TWCC Bi-directional Test Peer (aiortc)")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8080, help="Port to bind to")
    args = parser.parse_args()

    peer = TWCCPeer()

    app = web.Application()
    app.router.add_post("/offer", peer.offer)
    app.on_shutdown.append(peer.on_shutdown)

    logger.info(f"Starting aiortc TWCC peer on {args.host}:{args.port}")
    logger.info("Ready for bi-directional video with Pion peer")

    web.run_app(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
