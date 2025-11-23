# Investigation: Chromium Sending Bitrate Severely Underperforming

## Problem Statement

Chromium reports sending only **0.03 Mbps** despite:
- 1280x720 resolution @ 30fps
- Maximum entropy random noise (incompressible)
- Explicit bitrate constraints: `maxBitrate: 10 Mbps, minBitrate: 1 Mbps`
- aiortc reports receiving 0.52 Mbps (17x higher)

**Goal**: Understand why and fix/validate.

---

## Investigation Plan

### Phase 1: Verify the Problem (Data Collection)

#### 1.1 Add Comprehensive Chromium Logging

**File**: `tests/integ/chromium/peer_twcc.js`

**Changes needed**:

```javascript
// In stats monitoring section (line ~190), add detailed logging:

setInterval(async () => {
    const stats = await window.pc.getStats();

    // Collect ALL stats, not just aggregates
    const detailedStats = {
        outbound: [],
        inbound: [],
        candidate: [],
        transport: []
    };

    stats.forEach(report => {
        if (report.type === 'outbound-rtp' && report.kind === 'video') {
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
                // NEW: Bandwidth estimation from encoder
                targetBitrate: report.targetBitrate || 0,
                encodedBitrate: report.encodedBitrate || 0
            });
        }

        if (report.type === 'inbound-rtp' && report.kind === 'video') {
            detailedStats.inbound.push({
                timestamp: report.timestamp,
                bytesReceived: report.bytesReceived,
                packetsReceived: report.packetsReceived,
                packetsLost: report.packetsLost,
                framesReceived: report.framesReceived || 0,
                framesDecoded: report.framesDecoded || 0,
                framesDropped: report.framesDropped || 0,
                jitter: report.jitter || 0
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
    });

    // Log everything in JSON format for analysis
    console.log('STATS_DUMP:', JSON.stringify(detailedStats, null, 2));

    // Continue with existing rate calculation...
}, 2000);
```

**Run test and collect**:
```bash
./aiortc_chromium_test.sh > /tmp/chromium_detailed.txt 2>&1
```

**Extract stats**:
```bash
grep "STATS_DUMP:" /tmp/chromium_detailed.txt | sed 's/.*STATS_DUMP: //' > /tmp/chromium_stats.json
```

---

#### 1.2 Add Encoder Parameter Verification

**File**: `tests/integ/chromium/peer_twcc.js`

**After setParameters call (line ~130), verify it worked**:

```javascript
sender.setParameters(params).then(() => {
    console.log('✅ Video sender configured for high bitrate (1-10 Mbps)');

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
```

**Look for**:
- Are `maxBitrate` and `minBitrate` actually set?
- Are they being overridden?
- What codec parameters are active?

---

#### 1.3 Packet Capture for Ground Truth

**Modify test script** to capture packets:

```bash
# In aiortc_chromium_test.sh, before starting containers:

echo "Starting packet capture..."
docker run -d --rm --name gcc-pcap \
    --network gcc-chromium-test \
    nicolaka/netshoot \
    tcpdump -i eth0 -w /tmp/capture.pcap

# After test completes:
docker cp gcc-pcap:/tmp/capture.pcap /tmp/chromium_test.pcap
docker rm -f gcc-pcap
```

**Analyze with**:
```bash
# Extract RTP streams
tshark -r /tmp/chromium_test.pcap -Y "rtp" -T fields \
    -e frame.time_relative \
    -e ip.src \
    -e ip.dst \
    -e rtp.ssrc \
    -e rtp.timestamp \
    -e udp.length \
    > /tmp/rtp_packets.csv

# Calculate actual bitrate
python3 << 'EOF'
import csv
from collections import defaultdict

# Group by SSRC (stream)
streams = defaultdict(list)
with open('/tmp/rtp_packets.csv') as f:
    for row in csv.reader(f, delimiter='\t'):
        if len(row) >= 5:
            time, src, dst, ssrc, ts, length = row[0], row[1], row[2], row[3], row[4], row[5]
            streams[ssrc].append((float(time), int(length)))

# Calculate bitrate per stream
for ssrc, packets in streams.items():
    if len(packets) < 10:
        continue

    # 1-second windows
    start_time = packets[0][0]
    end_time = packets[-1][0]
    duration = end_time - start_time
    total_bytes = sum(p[1] for p in packets)

    avg_bitrate = (total_bytes * 8) / duration / 1_000_000  # Mbps
    print(f"SSRC {ssrc}: {avg_bitrate:.2f} Mbps ({len(packets)} packets over {duration:.1f}s)")
EOF
```

**This tells us**: The actual wire bitrate, independent of stats API.

---

### Phase 2: Test Different Configurations

#### 2.1 Try Different Codecs

**Modify peer_twcc.js to force specific codec**:

```javascript
// After creating offer, before setLocalDescription:
const offer = await pc.createOffer({
    offerToReceiveVideo: true,
    offerToReceiveAudio: false
});

// Modify SDP to prefer H.264 (or VP9, or VP8)
offer.sdp = offer.sdp.replace(/m=video.*\r\n/, (match) => {
    // Force H.264
    return match.replace(/VP8|VP9/g, '');
    // Or force VP9
    // return match.replace(/VP8|H264/g, '');
});

console.log('MODIFIED_SDP:', offer.sdp);
await pc.setLocalDescription(offer);
```

**Test all three**:
```bash
# Test 1: VP8 (default)
./aiortc_chromium_test.sh > /tmp/test_vp8.txt 2>&1

# Test 2: VP9 (modify peer_twcc.js)
./aiortc_chromium_test.sh > /tmp/test_vp9.txt 2>&1

# Test 3: H.264 (modify peer_twcc.js)
./aiortc_chromium_test.sh > /tmp/test_h264.txt 2>&1
```

**Compare**: Do all codecs show same low bitrate? Or is it codec-specific?

---

#### 2.2 Try Real Video File Instead of Canvas

**Create test video** with ffmpeg:

```bash
# Generate 10-second high-bitrate test video
ffmpeg -f lavfi -i testsrc=duration=10:size=1280x720:rate=30 \
    -vf "noise=alls=20:allf=t+u" \
    -c:v libvpx-vp9 -b:v 10M \
    tests/integ/chromium/test_video.webm
```

**Modify peer_twcc.js** to use video file:

```javascript
// Replace canvas stream with video file
const video = document.createElement('video');
video.src = '/test_video.webm';  // Need to serve this
video.autoplay = true;
video.loop = true;
video.muted = true;
await video.play();

const stream = video.captureStream(30);
localStream = stream;
```

**Serve video file** from Dockerfile:
```dockerfile
COPY test_video.webm /app/public/test_video.webm
```

**Test**: Does real video produce higher bitrate than canvas?

---

#### 2.3 Test Without Bitrate Constraints

**Modify peer_twcc.js** - comment out setParameters:

```javascript
// Force high bitrate for video tracks
if (track.kind === 'video') {
    // REMOVED: Don't set any constraints
    // const params = sender.getParameters();
    // ...
    console.log('⚠️  NOT setting bitrate constraints (baseline test)');
}
```

**Test**: Does removing constraints help or hurt?

---

#### 2.4 Test with Different Canvas Frame Rate

**Modify drawFrame interval**:

```javascript
// Current: 30fps
setInterval(drawFrame, 1000 / 30);

// Test 1: 60fps (more data)
setInterval(drawFrame, 1000 / 60);

// Test 2: 15fps (less data)
setInterval(drawFrame, 1000 / 15);

// Test 3: Variable fps (random 10-60)
function drawAndSchedule() {
    drawFrame();
    const fps = 10 + Math.random() * 50;
    setTimeout(drawAndSchedule, 1000 / fps);
}
drawAndSchedule();
```

**Compare**: Does FPS affect encoded bitrate?

---

### Phase 3: Understand Chromium Internals

#### 3.1 Enable Chromium WebRTC Internals Logging

**Modify peer_twcc.js** to save chrome://webrtc-internals equivalent:

```javascript
// Add to main():
await page.goto('chrome://webrtc-internals');
await page.waitForTimeout(1000);

// Dump internals to console
const internals = await page.evaluate(() => {
    return document.querySelector('pre').textContent;
});
console.log('WEBRTC_INTERNALS:', internals);
```

**Or use chrome flags**:

```javascript
const browser = await chromium.launch({
    headless: true,
    args: [
        '--use-fake-ui-for-media-stream',
        '--use-fake-device-for-media-stream',
        '--enable-features=WebRTC-GoogCongestionControl',
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        // NEW: WebRTC debugging
        '--enable-logging=stderr',
        '--v=1',  // Verbose logging
        '--vmodule=*/webrtc/*=2,*/media/*=2'  // Extra verbose for WebRTC/media
    ]
});
```

**Collect Chromium logs**:
```bash
docker logs gcc-chromium 2>&1 | grep -i "encoder\|bitrate\|bandwidth" > /tmp/chromium_encoder.log
```

---

#### 3.2 Check Chromium Bandwidth Estimation

**Add RTCP listener** in peer_twcc.js:

```javascript
// After creating RTCPeerConnection
pc.addEventListener('track', (event) => {
    const receiver = event.receiver;

    // Monitor receiver reports
    setInterval(async () => {
        const stats = await receiver.getStats();
        stats.forEach(report => {
            if (report.type === 'inbound-rtp') {
                console.log('RECEIVER_REPORT:', {
                    jitter: report.jitter,
                    packetsLost: report.packetsLost,
                    fractionLost: report.fractionLost,
                    timestamp: report.timestamp
                });
            }
        });
    }, 1000);
});
```

**Look for**: Is Chromium's BWE limiting the encoder?

---

### Phase 4: Comparative Tests

#### 4.1 Browser-to-Browser Baseline

**Create simple HTML test page**:

```html
<!-- tests/integ/chromium/browser_test.html -->
<!DOCTYPE html>
<html>
<body>
<h1>Browser-to-Browser Bitrate Test</h1>
<video id="local" autoplay muted></video>
<video id="remote" autoplay></video>
<pre id="stats"></pre>

<script>
async function test() {
    // Create canvas stream (same as peer_twcc.js)
    const canvas = document.createElement('canvas');
    canvas.width = 1280;
    canvas.height = 720;
    const ctx = canvas.getContext('2d');

    function drawFrame() {
        const imageData = ctx.createImageData(canvas.width, canvas.height);
        for (let i = 0; i < imageData.data.length; i += 4) {
            imageData.data[i] = Math.random() * 256;
            imageData.data[i + 1] = Math.random() * 256;
            imageData.data[i + 2] = Math.random() * 256;
            imageData.data[i + 3] = 255;
        }
        ctx.putImageData(imageData, 0, 0);
    }
    setInterval(drawFrame, 1000 / 30);

    const stream = canvas.captureStream(30);
    document.getElementById('local').srcObject = stream;

    // Create loopback peer connection
    const pc1 = new RTCPeerConnection();
    const pc2 = new RTCPeerConnection();

    stream.getTracks().forEach(track => {
        const sender = pc1.addTrack(track, stream);

        // Set same bitrate constraints
        const params = sender.getParameters();
        if (!params.encodings) params.encodings = [{}];
        params.encodings[0].maxBitrate = 10000000;
        params.encodings[0].minBitrate = 1000000;
        sender.setParameters(params);
    });

    pc2.ontrack = e => {
        document.getElementById('remote').srcObject = e.streams[0];
    };

    pc1.onicecandidate = e => e.candidate && pc2.addIceCandidate(e.candidate);
    pc2.onicecandidate = e => e.candidate && pc1.addIceCandidate(e.candidate);

    const offer = await pc1.createOffer();
    await pc1.setLocalDescription(offer);
    await pc2.setRemoteDescription(offer);

    const answer = await pc2.createAnswer();
    await pc2.setLocalDescription(answer);
    await pc1.setRemoteDescription(answer);

    // Monitor stats
    setInterval(async () => {
        const stats = await pc1.getStats();
        let output = '';
        stats.forEach(report => {
            if (report.type === 'outbound-rtp' && report.kind === 'video') {
                output += `Sent: ${report.bytesSent} bytes, ${report.packetsSent} packets\n`;
                output += `Frames: ${report.framesSent}, FPS: ${report.framesPerSecond}\n`;
            }
        });
        document.getElementById('stats').textContent = output;
    }, 1000);
}

test();
</script>
</body>
</html>
```

**Test manually**:
```bash
# Open in Chrome
chrome browser_test.html

# Record for 30 seconds, note bitrate
```

**Compare**: Is browser-to-browser higher than browser-to-aiortc?

---

#### 4.2 aiortc-to-aiortc Baseline

**Test if aiortc can send high bitrate to itself**:

```bash
# Modify aiortc peer to also generate noisy video
# Compare aiortc→aiortc vs aiortc→chromium vs chromium→aiortc
```

---

### Phase 5: Hypothesis Testing

Based on data collected, test specific hypotheses:

#### Hypothesis 1: Stats API Measurement Error

**Test**: Packet capture bitrate vs Stats API bitrate
**Expected**: If H1 true, pcap shows ~10 Mbps but stats show 0.03 Mbps
**Action**: If true, this is a stats reporting bug, not a real problem

#### Hypothesis 2: Encoder Complexity Limit

**Test**: Enable hardware encoding, check `encoderImplementation` in stats
**Expected**: If H2 true, CPU usage high, encoder can't keep up
**Action**: Try different resolution or fps

#### Hypothesis 3: Bandwidth Estimation Override

**Test**: Check `qualityLimitationReason` in stats
**Expected**: If H3 true, will show "bandwidth" limitation
**Action**: Investigate BWE algorithm, may need to disable

#### Hypothesis 4: Canvas Stream Limitation

**Test**: Use video file instead of canvas
**Expected**: If H4 true, video file produces higher bitrate
**Action**: Switch to video file for testing

#### Hypothesis 5: aiortc Not Signaling Capacity

**Test**: Packet capture RTCP receiver reports from aiortc
**Expected**: If H5 true, aiortc not sending proper REMB/TWCC feedback
**Action**: Fix aiortc feedback generation

---

## Experiment Tracking Template

```markdown
### Experiment: [NAME]

**Date**: YYYY-MM-DD
**Hypothesis**: [What we're testing]
**Changes**: [Code/config modifications]
**Command**:
```bash
[exact command]
```

**Results**:
- Chromium TX bitrate: X.XX Mbps
- aiortc RX bitrate: X.XX Mbps
- Packet capture bitrate: X.XX Mbps
- Packet loss: X packets
- Notes: [Observations]

**Conclusion**: [Accept/Reject hypothesis]
**Next**: [What to try next]
```

---

## Success Criteria

We'll consider this issue **resolved** when:

1. ✅ Understand root cause (even if we can't fix it)
2. ✅ Chromium sending bitrate > 2 Mbps OR proven to be measurement artifact
3. ✅ Documented workaround if issue is unfixable
4. ✅ Updated test expectations to match reality

---

## Quick Start

**To begin investigation NOW**:

```bash
# 1. Add detailed logging to peer_twcc.js (see 1.1 above)
vim tests/integ/chromium/peer_twcc.js

# 2. Run test with enhanced logging
./aiortc_chromium_test.sh > /tmp/chromium_investigation.txt 2>&1

# 3. Extract and analyze stats
grep "STATS_DUMP:" /tmp/chromium_investigation.txt | \
    sed 's/.*STATS_DUMP: //' | \
    python3 -m json.tool > /tmp/chromium_stats_parsed.json

# 4. Look for smoking guns
grep -E "qualityLimitationReason|targetBitrate|encodedBitrate" \
    /tmp/chromium_stats_parsed.json

# 5. Report findings
cat > /tmp/findings.md << 'EOF'
# Initial Findings

## Stats Observed:
- bytesSent: [VALUE]
- packetsSent: [VALUE]
- framesSent: [VALUE]
- qualityLimitationReason: [VALUE]
- targetBitrate: [VALUE]
- encoderImplementation: [VALUE]

## Hypothesis:
[Based on above data]

## Next Experiment:
[What to try next]
EOF
```

---

## Timeline

- **Day 1-2**: Phase 1 (Data Collection) - 4 hours
- **Day 3**: Phase 2 (Different Configs) - 3 hours
- **Day 4**: Phase 3 (Chromium Internals) - 4 hours
- **Day 5**: Phase 4-5 (Comparative & Hypothesis Testing) - 4 hours
- **Total**: ~15 hours of focused investigation

**Expected Outcome**: Root cause identified with 90% confidence.
