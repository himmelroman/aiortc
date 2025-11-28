#!/usr/bin/env node
/**
 * Chromium BWE peer using Playwright
 *
 * Acts as WebRTC client (offerer) that connects to a signaling server,
 * sends and receives video with bandwidth estimation enabled.
 * Uses Playwright to control a real Chromium browser instance.
 */

const { chromium } = require('playwright');

const SERVER_URL = process.env.SERVER_URL || 'http://host.docker.internal:8080';

async function main() {
    console.log('Starting Chromium BWE peer...');
    console.log(`Server URL: ${SERVER_URL}`);

    // Launch Chromium with WebRTC flags
    const browser = await chromium.launch({
        headless: true,
        args: [
            '--autoplay-policy=no-user-gesture-required',  // Allow video autoplay
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
        console.log(`[Browser] ${text}`);
    });

    // Navigate to the HTML page with the video (served by HTTP server)
    await page.goto('http://localhost:8888/');

    console.log('Setting up WebRTC in browser...');

    // Setup WebRTC peer connection
    const setupResult = await page.evaluate(async () => {
        let pc = null;
        let localStream = null;

        // Get the video element from the page
        const video = document.getElementById('test-video');
        if (!video) {
            throw new Error('Video element not found');
        }

        // Wait for video to be ready and start playing
        await new Promise((resolve, reject) => {
            if (video.readyState >= 2) {
                // Video is already loaded
                console.log(`Video loaded: ${video.videoWidth}x${video.videoHeight}, duration: ${video.duration}s`);
                video.play().then(resolve).catch(reject);
            } else {
                video.onloadedmetadata = () => {
                    console.log(`Video loaded: ${video.videoWidth}x${video.videoHeight}, duration: ${video.duration}s`);
                    video.play().then(resolve).catch(reject);
                };
                video.onerror = () => reject(new Error('Failed to load video'));

                // Timeout after 5 seconds
                setTimeout(() => reject(new Error('Video load timeout')), 5000);
            }
        });

        // Create canvas to force frame generation in headless mode
        // video.captureStream() doesn't work in headless because no rendering occurs
        const canvas = document.createElement('canvas');
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
        const ctx = canvas.getContext('2d');

        // Continuously draw video frames to canvas (forces frame decode)
        const drawFrame = () => {
            if (video.paused || video.ended) return;
            ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
            requestAnimationFrame(drawFrame);
        };
        drawFrame();

        console.log(`Canvas created: ${canvas.width}x${canvas.height}`);

        // Capture stream from canvas (30 FPS)
        localStream = canvas.captureStream(30);

        console.log('Created video stream from canvas');

        // Verify stream has video track
        const videoTracks = localStream.getVideoTracks();
        if (videoTracks.length === 0) {
            console.error('ERROR: No video tracks in stream!');
        } else {
            console.log(`Stream has ${videoTracks.length} video track(s)`);
            console.log(`Video track settings:`, videoTracks[0].getSettings());
        }

        // Create peer connection with TWCC extension
        pc = new RTCPeerConnection({
            iceServers: [],
            sdpSemantics: 'unified-plan'
        });

        // Store pc and localStream globally for reconnection
        window.pc = pc;
        window.localStream = localStream;

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
                    console.log('Video sender configured for high bitrate (1-50 Mbps)');
                }).catch(err => {
                    console.warn('Failed to set bitrate parameters:', err);
                });
            }
        });

        // Handle remote tracks
        pc.ontrack = (event) => {
            console.log(`Received remote track: ${event.track.kind}`);
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

        return { success: true };
    });

    if (!setupResult.success) {
        throw new Error('Failed to setup WebRTC');
    }

    const fetch = (await import('node-fetch')).default;

    // Connect to server
    await connectToServer(page, fetch);

    // Start stats monitoring
    await startStatsMonitoring(page);

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

async function connectToServer(page, fetch) {
    let connectionAttempt = 0;
    const MAX_RECONNECTION_ATTEMPTS = 5;

    const createAndSendOffer = async () => {
        connectionAttempt++;
        console.log(`🔄 Connection attempt #${connectionAttempt}`);

        // Create offer
        const offer = await page.evaluate(async () => {
            const offer = await window.pc.createOffer({
                offerToReceiveVideo: true,
                offerToReceiveAudio: false
            });

            // Ensure TWCC is enabled in SDP
            if (!offer.sdp.includes('transport-wide-cc-extensions')) {
                console.warn('TWCC extension not found in offer SDP!');
            } else {
                console.log('TWCC extension enabled in offer');
            }

            await window.pc.setLocalDescription(offer);

            return {
                sdp: offer.sdp,
                type: offer.type
            };
        });

        console.log('Sending offer to signaling server...');

        // Send offer and wait for answer (this blocks until answerer responds)
        const response = await fetch(`${SERVER_URL}/offer`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(offer)
        });

        if (!response.ok) {
            throw new Error(`Failed to send offer: ${response.status}`);
        }

        const answer = await response.json();
        console.log('Received answer from signaling server');

        if (!answer.sdp.includes('transport-wide-cc-extensions')) {
            console.warn('TWCC extension not found in answer SDP!');
        } else {
            console.log('TWCC extension confirmed in answer');
        }

        // Set remote description
        await page.evaluate(async (answer) => {
            await window.pc.setRemoteDescription(answer);
            console.log('WebRTC connection established');
        }, answer);
    };

    // Initial connection attempt
    await createAndSendOffer();

    // Monitor connection state and reconnect if needed
    const monitorConnection = async () => {
        while (connectionAttempt < MAX_RECONNECTION_ATTEMPTS) {
            await new Promise(r => setTimeout(r, 1000)); // Check every second

            const connectionState = await page.evaluate(() => window.pc.connectionState);

            if (connectionState === 'failed' || connectionState === 'closed') {
                console.log(`⚠️  Connection #${connectionAttempt} ${connectionState}`);

                if (connectionAttempt >= MAX_RECONNECTION_ATTEMPTS) {
                    console.log(`❌ Max reconnection attempts (${MAX_RECONNECTION_ATTEMPTS}) reached, giving up`);
                    break;
                }

                // Wait before reconnecting
                console.log('Waiting 3 seconds before reconnecting...');
                await new Promise(r => setTimeout(r, 3000));

                // Recreate peer connection in browser
                await page.evaluate(() => {
                    // Close old connection
                    if (window.pc) {
                        window.pc.close();
                    }

                    // Create new peer connection
                    window.pc = new RTCPeerConnection({
                        iceServers: [],
                        sdpSemantics: 'unified-plan'
                    });

                    // Re-add local stream tracks
                    if (window.localStream) {
                        window.localStream.getTracks().forEach(track => {
                            const sender = window.pc.addTrack(track, window.localStream);

                            if (track.kind === 'video') {
                                const params = sender.getParameters();
                                if (!params.encodings) {
                                    params.encodings = [{}];
                                }
                                params.encodings[0].maxBitrate = 50000000;
                                params.encodings[0].minBitrate = 1000000;
                                sender.setParameters(params);
                            }
                        });
                    }

                    // Re-setup handlers
                    window.pc.ontrack = (event) => {
                        console.log(`Received remote track: ${event.track.kind}`);
                        const remoteVideo = document.createElement('video');
                        remoteVideo.srcObject = event.streams[0];
                        remoteVideo.autoplay = true;
                        remoteVideo.muted = true;
                        document.body.appendChild(remoteVideo);
                    };

                    window.pc.onicecandidate = (event) => {
                        if (event.candidate) {
                            console.log('ICE candidate generated');
                        }
                    };

                    window.pc.oniceconnectionstatechange = () => {
                        console.log(`ICE connection state: ${window.pc.iceConnectionState}`);
                    };

                    window.pc.onconnectionstatechange = () => {
                        console.log(`Connection state: ${window.pc.connectionState}`);
                    };
                });

                // Try to reconnect
                try {
                    await createAndSendOffer();
                    console.log(`✅ Reconnection #${connectionAttempt} successful`);
                } catch (err) {
                    console.error(`❌ Reconnection #${connectionAttempt} failed:`, err.message);
                }
            }
        }
    };

    // Start monitoring in background (don't await)
    monitorConnection().catch(err => {
        console.error('Connection monitoring error:', err);
    });
}

async function startStatsMonitoring(page) {
    await page.evaluate(() => {
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

                // Get GCC estimate from candidate pair stats
                let gccEstimateMbps = 0;
                if (detailedStats.candidate.length > 0 && detailedStats.candidate[0].availableOutgoingBitrate > 0) {
                    gccEstimateMbps = detailedStats.candidate[0].availableOutgoingBitrate / 1000000;
                }

                // Format output to match aiortc/pion for test framework compatibility
                if (gccEstimateMbps > 0) {
                    console.log(`📤 Sending GCC: ${gccEstimateMbps.toFixed(2)} Mbps (chromium→remote)`);
                }
                console.log(`📥 Receiving: ${receiveRate.toFixed(2)} Mbps (remote→chromium) | Packets: ${packetsReceived} | Lost: ${packetsLost}`);

                window.lastStats = { bytesSent, bytesReceived, timestamp: now };
            }
        }, 2000);
    });
}

main().catch(error => {
    console.error('Error:', error);
    process.exit(1);
});
