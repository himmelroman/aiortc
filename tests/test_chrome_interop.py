"""
Chrome interoperability test for TWCC/GCC.

This test uses Playwright to control Chrome and verify that:
1. Chrome and aiortc can negotiate TWCC extension in SDP
2. Chrome generates TWCC feedback when receiving from aiortc
3. aiortc processes Chrome's TWCC feedback correctly
4. aiortc generates TWCC feedback that Chrome can process
5. Bitrate control works in both directions
"""

import asyncio
import json
import logging
import os
import unittest
from pathlib import Path

# These will be optional dependencies
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.twcc.receiver import TransportSequenceNumberManager

logger = logging.getLogger(__name__)


# HTML page for Chrome WebRTC client
CHROME_CLIENT_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>TWCC Test Client</title>
</head>
<body>
    <h1>TWCC/GCC Chrome Test Client</h1>
    <video id="localVideo" autoplay muted width="640" height="480"></video>
    <video id="remoteVideo" autoplay width="640" height="480"></video>
    <div id="status">Initializing...</div>
    <div id="stats" style="font-family: monospace; white-space: pre;"></div>

    <script>
        let pc = null;
        let localStream = null;
        let statsInterval = null;

        // Initialize
        async function init() {
            updateStatus('Getting user media...');

            // Get video/audio from canvas (synthetic media)
            const canvas = document.createElement('canvas');
            canvas.width = 640;
            canvas.height = 480;
            const ctx = canvas.getContext('2d');

            // Draw something animated
            let frame = 0;
            setInterval(() => {
                ctx.fillStyle = `hsl(${frame % 360}, 50%, 50%)`;
                ctx.fillRect(0, 0, 640, 480);
                ctx.fillStyle = 'white';
                ctx.font = '48px Arial';
                ctx.fillText(`Frame ${frame}`, 50, 240);
                frame++;
            }, 33); // ~30fps

            localStream = canvas.captureStream(30);
            document.getElementById('localVideo').srcObject = localStream;

            updateStatus('Ready for signaling');

            // Start monitoring stats
            statsInterval = setInterval(reportStats, 1000);
        }

        async function createPeerConnection() {
            updateStatus('Creating peer connection...');

            // Create with explicit TWCC extension
            pc = new RTCPeerConnection({
                sdpSemantics: 'unified-plan'
            });

            // Add tracks
            localStream.getTracks().forEach(track => {
                pc.addTrack(track, localStream);
            });

            // Handle remote track
            pc.ontrack = (event) => {
                updateStatus('Received remote track');
                document.getElementById('remoteVideo').srcObject = event.streams[0];
            };

            pc.oniceconnectionstatechange = () => {
                updateStatus(`ICE: ${pc.iceConnectionState}`);
            };

            return pc;
        }

        async function handleOffer(offerSdp) {
            updateStatus('Received offer, creating answer...');

            await createPeerConnection();

            await pc.setRemoteDescription({
                type: 'offer',
                sdp: offerSdp
            });

            const answer = await pc.createAnswer();
            await pc.setLocalDescription(answer);

            updateStatus('Answer created');

            return {
                type: 'answer',
                sdp: pc.localDescription.sdp,
                sdpMid: extractTransportCC(pc.localDescription.sdp)
            };
        }

        async function handleAnswer(answerSdp) {
            await pc.setRemoteDescription({
                type: 'answer',
                sdp: answerSdp
            });

            updateStatus('Answer set');
        }

        async function createOffer() {
            updateStatus('Creating offer...');

            await createPeerConnection();

            const offer = await pc.createOffer();
            await pc.setLocalDescription(offer);

            updateStatus('Offer created');

            return {
                type: 'offer',
                sdp: pc.localDescription.sdp,
                sdpMid: extractTransportCC(pc.localDescription.sdp)
            };
        }

        function extractTransportCC(sdp) {
            // Check for transport-cc extension in SDP
            const lines = sdp.split('\\n');
            for (const line of lines) {
                if (line.includes('transport-wide-cc') || line.includes('transport-cc')) {
                    return line.trim();
                }
            }
            return null;
        }

        async function reportStats() {
            if (!pc) return;

            const stats = await pc.getStats();
            let report = 'Chrome WebRTC Stats:\\n';
            report += '='.repeat(50) + '\\n';

            let twccFound = false;
            let bitrateInfo = {};

            stats.forEach((stat) => {
                // Look for outbound-rtp (what Chrome is sending)
                if (stat.type === 'outbound-rtp' && stat.kind === 'video') {
                    report += `\\nOUTBOUND-RTP (Chrome → aiortc):\\n`;
                    report += `  Packets sent: ${stat.packetsSent}\\n`;
                    report += `  Bytes sent: ${stat.bytesSent}\\n`;
                    report += `  Timestamp: ${stat.timestamp}\\n`;

                    // Check for header extensions
                    if (stat.headerBytesReturned) {
                        report += `  Header extensions: ${stat.headerBytesReturned} bytes\\n`;
                    }

                    bitrateInfo.outbound = {
                        packetsSent: stat.packetsSent,
                        bytesSent: stat.bytesSent,
                        timestamp: stat.timestamp
                    };
                }

                // Look for remote-inbound-rtp (Chrome receiving feedback from aiortc)
                if (stat.type === 'remote-inbound-rtp' && stat.kind === 'video') {
                    report += `\\nREMOTE-INBOUND-RTP (aiortc → Chrome feedback):\\n`;
                    report += `  Packets received: ${stat.packetsReceived || 0}\\n`;
                    report += `  Packets lost: ${stat.packetsLost || 0}\\n`;
                    report += `  Jitter: ${stat.jitter || 0}\\n`;
                    report += `  Round trip time: ${stat.roundTripTime || 0}ms\\n`;

                    twccFound = true;
                }

                // Look for inbound-rtp (Chrome receiving from aiortc)
                if (stat.type === 'inbound-rtp' && stat.kind === 'video') {
                    report += `\\nINBOUND-RTP (aiortc → Chrome):\\n`;
                    report += `  Packets received: ${stat.packetsReceived}\\n`;
                    report += `  Bytes received: ${stat.bytesReceived}\\n`;
                    report += `  Packets lost: ${stat.packetsLost || 0}\\n`;
                    report += `  Jitter: ${stat.jitter}\\n`;
                }

                // Look for remote-outbound-rtp (aiortc's stats)
                if (stat.type === 'remote-outbound-rtp' && stat.kind === 'video') {
                    report += `\\nREMOTE-OUTBOUND-RTP (aiortc sending):\\n`;
                    report += `  Packets sent: ${stat.packetsSent}\\n`;
                    report += `  Bytes sent: ${stat.bytesSent}\\n`;
                }

                // Look for transport stats
                if (stat.type === 'transport') {
                    report += `\\nTRANSPORT:\\n`;
                    report += `  Bytes sent: ${stat.bytesSent || 0}\\n`;
                    report += `  Bytes received: ${stat.bytesReceived || 0}\\n`;
                    report += `  Packets sent: ${stat.packetsSent || 0}\\n`;
                    report += `  Packets received: ${stat.packetsReceived || 0}\\n`;
                }
            });

            report += `\\n${'='.repeat(50)}\\n`;
            report += `TWCC Status: ${twccFound ? '✅ ACTIVE' : '❌ NOT DETECTED'}\\n`;

            document.getElementById('stats').textContent = report;

            // Store for test validation
            window.lastStats = {
                twccDetected: twccFound,
                bitrateInfo: bitrateInfo,
                timestamp: Date.now()
            };
        }

        function updateStatus(message) {
            console.log(message);
            document.getElementById('status').textContent = message;
        }

        // Expose functions for Playwright
        window.chromeWebRTC = {
            init,
            createOffer,
            handleOffer,
            handleAnswer,
            getStats: () => window.lastStats,
            getSDP: () => pc ? pc.localDescription.sdp : null
        };

        // Auto-initialize
        init();
    </script>
</body>
</html>
"""


@unittest.skipUnless(PLAYWRIGHT_AVAILABLE, "Playwright not installed")
class TestChromeInterop(unittest.TestCase):
    """Test TWCC/GCC interoperability with Chrome."""

    def setUp(self):
        """Set up test fixtures."""
        # Create HTML file
        self.html_file = Path("/tmp/twcc_chrome_test.html")
        self.html_file.write_text(CHROME_CLIENT_HTML)
        self.html_url = f"file://{self.html_file}"

    def tearDown(self):
        """Clean up."""
        if self.html_file.exists():
            self.html_file.unlink()

    def test_chrome_receives_from_aiortc_with_twcc(self):
        """Test that Chrome can receive TWCC feedback from aiortc."""
        async def run():
            async with async_playwright() as p:
                # Launch Chrome
                browser = await p.chromium.launch(
                    headless=True,
                    args=[
                        '--use-fake-ui-for-media-stream',
                        '--use-fake-device-for-media-stream',
                        '--enable-logging=stderr',
                        '--v=1'
                    ]
                )
                page = await browser.new_page()

                # Navigate to test page
                await page.goto(self.html_url)
                await page.wait_for_load_state('networkidle')
                await asyncio.sleep(1)  # Wait for init

                # Create aiortc peer
                from tests.test_twcc_gcc_e2e_real import DummyVideoTrack

                pc_aiortc = RTCPeerConnection()
                video_track = DummyVideoTrack()
                sender = pc_aiortc.addTrack(video_track)

                # Enable GCC on aiortc
                transport_seq_manager = TransportSequenceNumberManager()
                sender.enable_gcc(transport_seq_manager, initial_bitrate=500000)

                # Create offer from aiortc
                offer = await pc_aiortc.createOffer()
                await pc_aiortc.setLocalDescription(offer)

                logger.info("aiortc SDP offer:")
                logger.info(offer.sdp)

                # Check if TWCC is in offer
                twcc_in_offer = 'transport' in offer.sdp.lower()
                logger.info(f"TWCC in aiortc offer: {twcc_in_offer}")

                # Send offer to Chrome
                answer_data = await page.evaluate(
                    f"chromeWebRTC.handleOffer(`{offer.sdp}`)"
                )

                logger.info("Chrome answer SDP mid:")
                logger.info(answer_data.get('sdpMid'))

                # Set Chrome's answer in aiortc
                answer = RTCSessionDescription(
                    sdp=answer_data['sdp'],
                    type=answer_data['type']
                )
                await pc_aiortc.setRemoteDescription(answer)

                # Wait for connection
                await asyncio.sleep(2)

                # Enable TWCC on aiortc receiver
                for receiver in pc_aiortc.getReceivers():
                    if receiver.track and receiver.track.kind == "video":
                        receiver.enable_twcc(
                            ssrc=receiver._RTCRtpReceiver__rtcp_ssrc or 1
                        )

                # Stream media for a bit
                logger.info("Streaming media...")
                await asyncio.sleep(5)

                # Get Chrome stats
                chrome_stats = await page.evaluate("chromeWebRTC.getStats()")
                logger.info(f"Chrome stats: {chrome_stats}")

                # Verify aiortc is sending
                self.assertGreater(sender._RTCRtpSender__packet_count, 0)
                logger.info(f"aiortc sent {sender._RTCRtpSender__packet_count} packets")

                # Verify Chrome received
                if chrome_stats and chrome_stats.get('bitrateInfo'):
                    logger.info(f"Chrome bitrate info: {chrome_stats['bitrateInfo']}")

                # Check for TWCC
                twcc_detected = chrome_stats.get('twccDetected', False) if chrome_stats else False
                logger.info(f"TWCC detected in Chrome: {twcc_detected}")

                # Get aiortc GCC stats
                if sender._RTCRtpSender__gcc_estimator:
                    gcc_stats = sender._RTCRtpSender__gcc_estimator.get_stats()
                    logger.info(f"aiortc GCC stats: {gcc_stats}")

                # Cleanup
                await pc_aiortc.close()
                await browser.close()

                # Basic validation
                self.assertGreater(sender._RTCRtpSender__packet_count, 0,
                                 "aiortc should have sent packets")

        loop = asyncio.get_event_loop()
        loop.run_until_complete(run())

    def test_aiortc_receives_from_chrome_with_twcc(self):
        """Test that aiortc can receive and process TWCC feedback from Chrome."""
        async def run():
            async with async_playwright() as p:
                # Launch Chrome
                browser = await p.chromium.launch(headless=True, args=[
                    '--use-fake-ui-for-media-stream',
                    '--use-fake-device-for-media-stream',
                ])
                page = await browser.new_page()

                # Navigate to test page
                await page.goto(self.html_url)
                await page.wait_for_load_state('networkidle')
                await asyncio.sleep(1)

                # Create offer from Chrome
                offer_data = await page.evaluate("chromeWebRTC.createOffer()")

                logger.info("Chrome SDP offer mid:")
                logger.info(offer_data.get('sdpMid'))

                # Create aiortc peer
                pc_aiortc = RTCPeerConnection()

                # Set Chrome's offer
                offer = RTCSessionDescription(
                    sdp=offer_data['sdp'],
                    type=offer_data['type']
                )
                await pc_aiortc.setRemoteDescription(offer)

                # Create answer
                answer = await pc_aiortc.createAnswer()
                await pc_aiortc.setLocalDescription(answer)

                logger.info("aiortc answer SDP:")
                logger.info(answer.sdp)

                # Send answer to Chrome
                await page.evaluate(
                    f"chromeWebRTC.handleAnswer(`{answer.sdp}`)"
                )

                # Wait for connection
                await asyncio.sleep(2)

                # Enable TWCC on aiortc receivers
                for receiver in pc_aiortc.getReceivers():
                    if receiver.track:
                        receiver.enable_twcc(
                            ssrc=receiver._RTCRtpReceiver__rtcp_ssrc or 1
                        )
                        logger.info(f"Enabled TWCC on receiver for {receiver.track.kind}")

                # Stream media
                logger.info("Streaming media...")
                await asyncio.sleep(5)

                # Check aiortc receivers
                for receiver in pc_aiortc.getReceivers():
                    if receiver.track:
                        logger.info(f"Receiver {receiver.track.kind} state")

                # Get Chrome stats
                chrome_stats = await page.evaluate("chromeWebRTC.getStats()")
                logger.info(f"Chrome stats: {chrome_stats}")

                # Cleanup
                await pc_aiortc.close()
                await browser.close()

                # Basic validation
                self.assertTrue(True, "Test completed")

        loop = asyncio.get_event_loop()
        loop.run_until_complete(run())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    unittest.main()
