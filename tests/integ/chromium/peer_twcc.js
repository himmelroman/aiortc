#!/usr/bin/env node
/**
 * Chromium TWCC peer using Playwright
 *
 * Sends and receives video with TWCC/GCC enabled for bidirectional testing.
 * Uses Playwright to control a real Chromium browser instance.
 */

const { chromium } = require('playwright');

const SERVER_URL = process.env.SERVER_URL || 'http://host.docker.internal:8080';

async function main() {
    console.log('Starting Chromium TWCC peer...');
    console.log(`Server URL: ${SERVER_URL}`);

    // Launch Chromium with WebRTC flags
    const browser = await chromium.launch({
        headless: true,
        args: [
            '--use-fake-ui-for-media-stream',  // Auto-accept media permissions
            '--use-fake-device-for-media-stream',  // Use fake video/audio devices
            '--enable-features=WebRTC-GoogCongestionControl',  // Enable GCC
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
        ]
    });

    const context = await browser.newContext({
        permissions: ['camera', 'microphone']
    });

    const page = await context.newPage();

    // Enable console logging from the page
    page.on('console', msg => {
        const text = msg.text();
        // Forward ALL logs for investigation (no filtering)
        console.log(`[Browser] ${text}`);
    });

    // Navigate to blank page
    await page.goto('about:blank');

    console.log('Setting up WebRTC in browser...');

    // Setup WebRTC peer connection and create offer
    const result = await page.evaluate(async () => {
        let pc = null;
        let localStream = null;

        // Create canvas video stream for sending
        const canvas = document.createElement('canvas');
        canvas.width = 1280;  // Increased resolution
        canvas.height = 720;
        const ctx = canvas.getContext('2d');

        // Generate extremely noisy video that's hard to compress
        function drawFrame() {
            const time = Date.now() / 1000;
            const imageData = ctx.createImageData(canvas.width, canvas.height);

            // Fill every pixel with completely random RGB values
            // This creates maximum entropy and highest possible bitrate
            for (let i = 0; i < imageData.data.length; i += 4) {
                // Each pixel gets completely random RGB values (0-255)
                imageData.data[i] = Math.random() * 256;      // R
                imageData.data[i + 1] = Math.random() * 256;  // G
                imageData.data[i + 2] = Math.random() * 256;  // B
                imageData.data[i + 3] = 255;                   // A (opaque)
            }

            ctx.putImageData(imageData, 0, 0);

            // Add moving colored bars on top for visual variety
            const barHeight = 50;
            const numBars = 5;
            for (let i = 0; i < numBars; i++) {
                const y = ((time * 100 + i * 150) % canvas.height);
                ctx.fillStyle = `hsl(${(time * 60 + i * 72) % 360}, 100%, 50%)`;
                ctx.fillRect(0, y, canvas.width, barHeight);
            }

            // Add timestamp text with random color
            ctx.fillStyle = `hsl(${(time * 120) % 360}, 100%, 80%)`;
            ctx.font = 'bold 48px Arial';
            ctx.strokeStyle = 'black';
            ctx.lineWidth = 3;
            ctx.strokeText(`Chromium - ${time.toFixed(2)}s`, 50, 100);
            ctx.fillText(`Chromium - ${time.toFixed(2)}s`, 50, 100);
        }

        // Draw frames at 30fps
        setInterval(drawFrame, 1000 / 30);

        // Get media stream from canvas
        const stream = canvas.captureStream(30);
        localStream = stream;

        console.log('✅ Created canvas video stream');

        // Create peer connection with TWCC extension
        pc = new RTCPeerConnection({
            iceServers: [],
            sdpSemantics: 'unified-plan'
        });

        // Store pc globally for later use
        window.pc = pc;

        // Add local stream tracks with high bitrate settings
        localStream.getTracks().forEach(track => {
            console.log(`Adding local track: ${track.kind}`);
            const sender = pc.addTrack(track, localStream);

            // Force high bitrate for video tracks
            if (track.kind === 'video') {
                const params = sender.getParameters();
                if (!params.encodings) {
                    params.encodings = [{}];
                }
                // Request maximum bitrate (50 Mbps) - aligned with pion/aiortc
                params.encodings[0].maxBitrate = 50000000;  // 50 Mbps
                params.encodings[0].minBitrate = 1000000;   // 1 Mbps
                sender.setParameters(params).then(() => {
                    console.log('✅ Video sender configured for high bitrate (1-50 Mbps)');

                    // VERIFY: Read back parameters to confirm they were applied
                    const verifyParams = sender.getParameters();
                    console.log('VERIFY_PARAMS:', JSON.stringify({
                        encodings: verifyParams.encodings,
                        codecs: verifyParams.codecs,
                        headerExtensions: verifyParams.headerExtensions,
                        rtcp: verifyParams.rtcp
                    }, null, 2));
                }).catch(err => {
                    console.warn('⚠️  Failed to set bitrate parameters:', err);
                });
            }
        });

        // Handle remote tracks
        pc.ontrack = (event) => {
            console.log(`📥 Received remote track: ${event.track.kind}`);
            const remoteVideo = document.createElement('video');
            remoteVideo.srcObject = event.streams[0];
            remoteVideo.autoplay = true;
            remoteVideo.muted = true;
            document.body.appendChild(remoteVideo);
        };

        // Handle ICE candidates
        pc.onicecandidate = (event) => {
            if (event.candidate) {
                console.log('ICE candidate generated');
            }
        };

        pc.oniceconnectionstatechange = () => {
            console.log(`ICE connection state: ${pc.iceConnectionState}`);
        };

        pc.onconnectionstatechange = () => {
            console.log(`Connection state: ${pc.connectionState}`);
        };

        // Create offer with TWCC
        const offer = await pc.createOffer({
            offerToReceiveVideo: true,
            offerToReceiveAudio: false
        });

        // Ensure TWCC is enabled in SDP
        if (!offer.sdp.includes('transport-wide-cc-extensions')) {
            console.warn('⚠️  TWCC extension not found in offer SDP!');
        } else {
            console.log('✅ TWCC extension enabled in offer');
        }

        await pc.setLocalDescription(offer);

        // Return the offer
        return {
            sdp: offer.sdp,
            type: offer.type
        };
    });

    console.log('Sending offer to aiortc server...');

    // Send offer to server from Node.js context (not browser)
    const fetch = (await import('node-fetch')).default;
    const response = await fetch(`${SERVER_URL}/offer`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(result)
    });

    const answer = await response.json();

    console.log('Received answer from server');

    if (!answer.sdp.includes('transport-wide-cc-extensions')) {
        console.warn('⚠️  TWCC extension not found in answer SDP!');
    } else {
        console.log('✅ TWCC extension confirmed in answer');
    }

    // Set remote description in browser
    await page.evaluate(async (answer) => {
        await window.pc.setRemoteDescription(answer);
        console.log('✅ WebRTC connection established');

        // Start stats monitoring with comprehensive logging
        setInterval(async () => {
            const stats = await window.pc.getStats();

            // Collect ALL stats, not just aggregates
            const detailedStats = {
                outbound: [],
                inbound: [],
                candidate: [],
                transport: []
            };

            let bytesSent = 0;
            let bytesReceived = 0;
            let packetsSent = 0;
            let packetsReceived = 0;
            let packetsLost = 0;

            stats.forEach(report => {
                if (report.type === 'outbound-rtp' && report.kind === 'video') {
                    bytesSent = report.bytesSent || 0;
                    packetsSent = report.packetsSent || 0;

                    detailedStats.outbound.push({
                        timestamp: report.timestamp,
                        bytesSent: report.bytesSent,
                        packetsSent: report.packetsSent,
                        framesSent: report.framesSent || 0,
                        framesEncoded: report.framesEncoded || 0,
                        keyFramesEncoded: report.keyFramesEncoded || 0,
                        totalEncodeTime: report.totalEncodeTime || 0,
                        totalPacketSendDelay: report.totalPacketSendDelay || 0,
                        qualityLimitationReason: report.qualityLimitationReason || 'none',
                        encoderImplementation: report.encoderImplementation || 'unknown',
                        frameWidth: report.frameWidth || 0,
                        frameHeight: report.frameHeight || 0,
                        framesPerSecond: report.framesPerSecond || 0,
                        // Bandwidth estimation from encoder
                        targetBitrate: report.targetBitrate || 0,
                        encodedBitrate: report.encodedBitrate || 0,
                        // Additional useful fields
                        nackCount: report.nackCount || 0,
                        pliCount: report.pliCount || 0,
                        firCount: report.firCount || 0,
                        qpSum: report.qpSum || 0
                    });
                }

                if (report.type === 'inbound-rtp' && report.kind === 'video') {
                    bytesReceived = report.bytesReceived || 0;
                    packetsReceived = report.packetsReceived || 0;
                    packetsLost = report.packetsLost || 0;

                    detailedStats.inbound.push({
                        timestamp: report.timestamp,
                        bytesReceived: report.bytesReceived,
                        packetsReceived: report.packetsReceived,
                        packetsLost: report.packetsLost,
                        framesReceived: report.framesReceived || 0,
                        framesDecoded: report.framesDecoded || 0,
                        framesDropped: report.framesDropped || 0,
                        jitter: report.jitter || 0,
                        jitterBufferDelay: report.jitterBufferDelay || 0,
                        jitterBufferEmittedCount: report.jitterBufferEmittedCount || 0
                    });
                }

                if (report.type === 'transport') {
                    detailedStats.transport.push({
                        bytesSent: report.bytesSent,
                        bytesReceived: report.bytesReceived,
                        packetsSent: report.packetsSent,
                        packetsReceived: report.packetsReceived,
                        selectedCandidatePairChanges: report.selectedCandidatePairChanges || 0
                    });
                }

                if (report.type === 'candidate-pair' && report.state === 'succeeded') {
                    detailedStats.candidate.push({
                        availableOutgoingBitrate: report.availableOutgoingBitrate || 0,
                        availableIncomingBitrate: report.availableIncomingBitrate || 0,
                        currentRoundTripTime: report.currentRoundTripTime || 0
                    });
                }
            });

            // Log detailed stats in JSON format for analysis
            console.log('STATS_DUMP:', JSON.stringify(detailedStats, null, 2));

            // Continue with existing rate calculation for human-readable output
            if (!window.lastStats) {
                window.lastStats = { bytesSent, bytesReceived, timestamp: Date.now() };
                return;
            }

            const now = Date.now();
            const deltaTime = (now - window.lastStats.timestamp) / 1000;

            if (deltaTime > 0) {
                const sendRate = ((bytesSent - window.lastStats.bytesSent) * 8) / deltaTime / 1000000;
                const receiveRate = ((bytesReceived - window.lastStats.bytesReceived) * 8) / deltaTime / 1000000;

                console.log(`📤 Sending: ${sendRate.toFixed(2)} Mbps (chromium→aiortc) | Packets: ${packetsSent}`);
                console.log(`📥 Receiving: ${receiveRate.toFixed(2)} Mbps (aiortc→chromium) | Packets: ${packetsReceived} | Lost: ${packetsLost}`);

                window.lastStats = { bytesSent, bytesReceived, timestamp: now };
            }
        }, 2000);

    }, answer);

    console.log('Chromium peer running...');

    // Handle cleanup on exit
    process.on('SIGINT', async () => {
        console.log('\nShutting down...');
        await browser.close();
        process.exit(0);
    });

    process.on('SIGTERM', async () => {
        console.log('\nShutting down...');
        await browser.close();
        process.exit(0);
    });
}

main().catch(error => {
    console.error('Error:', error);
    process.exit(1);
});
