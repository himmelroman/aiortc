# TWCC/GCC Validation Guide

This guide explains how to validate that TWCC (Transport-Wide Congestion Control) and GCC (Google Congestion Control) are working correctly in aiortc.

## Quick Validation Checklist

### ✅ SDP Negotiation
Check that the TWCC extension is present in SDP:

```
a=extmap:X http://www.ietf.org/id/draft-holmer-rmcat-transport-wide-cc-extensions-01
```

Or the newer URI:
```
a=extmap:X http://www.webrtc.org/experiments/rtp-hdrext/transport-wide-cc-02
```

### ✅ RTP Packets Have Transport Sequence Numbers
When sending RTP packets, verify transport_sequence_number is set:
```python
# In RTCRtpSender debug logs, you should see:
# > RtpPacket(..., extensions=RtpHeaderExtensions(transport_sequence_number=123, ...))
```

### ✅ TWCC Feedback is Generated
Check receiver logs for TWCC feedback generation:
```
# In RTCRtpReceiver debug logs:
# - Packets are recorded: "TWCC enabled with SSRC X"
# - Feedback is sent periodically
```

### ✅ TWCC Feedback is Received and Parsed
Check sender logs for TWCC feedback processing:
```python
# In RTCRtpSender debug logs:
# "+ GCC bandwidth estimate XXXXX bps"
```

### ✅ Encoder Bitrate Changes
Verify that GCC estimates are applied to encoder:
```python
# Check encoder.target_bitrate changes over time
if sender._RTCRtpSender__encoder and hasattr(sender._RTCRtpSender__encoder, 'target_bitrate'):
    print(f"Encoder bitrate: {sender._RTCRtpSender__encoder.target_bitrate}")
```

### ✅ GCC Statistics are Updated
```python
if sender._RTCRtpSender__gcc_estimator:
    stats = sender._RTCRtpSender__gcc_estimator.get_stats()
    print(f"GCC Stats: {stats}")
    # Look for packets_received > 0
```

## Testing Against Chrome

### Method 1: Using Playwright Test

Run the Chrome interop test:
```bash
# Install Playwright
pip install playwright
playwright install chromium

# Run test
python -m unittest tests.test_chrome_interop -v
```

### Method 2: Manual Chrome Testing

1. **Create a simple HTML file** (`test_twcc.html`):

```html
<!DOCTYPE html>
<html>
<head><title>TWCC Test</title></head>
<body>
<video id="local" autoplay muted></video>
<video id="remote" autoplay></video>
<pre id="stats"></pre>

<script>
let pc = new RTCPeerConnection();

// Add local media
navigator.mediaDevices.getUserMedia({video: true, audio: false})
  .then(stream => {
    document.getElementById('local').srcObject = stream;
    stream.getTracks().forEach(track => pc.addTrack(track, stream));
  });

// Handle remote track
pc.ontrack = e => {
  document.getElementById('remote').srcObject = e.streams[0];
};

// Monitor stats
setInterval(async () => {
  const stats = await pc.getStats();
  let output = '';

  stats.forEach(stat => {
    if (stat.type === 'outbound-rtp' && stat.kind === 'video') {
      output += `Outbound: ${stat.packetsSent} packets, ${stat.bytesSent} bytes\\n`;
    }
    if (stat.type === 'remote-inbound-rtp') {
      output += `Remote feedback: RTT=${stat.roundTripTime}ms\\n`;
      output += `✅ TWCC is working!\\n`;
    }
  });

  document.getElementById('stats').textContent = output;
}, 1000);

// Signaling with aiortc peer goes here...
</script>
</body>
</html>
```

2. **Run aiortc server** with TWCC enabled

3. **Open Chrome DevTools** → Network tab → Filter by "RTCP"

4. **Look for RTCP packets** with:
   - Payload Type (PT) = 205
   - Feedback Message Type (FMT) = 15
   - These are TWCC feedback packets

### Method 3: Using chrome://webrtc-internals

1. Open `chrome://webrtc-internals` in Chrome
2. Establish connection with aiortc peer
3. Look for:
   - **Extension headers**: `transport-cc` or `transport-wide-cc`
   - **Stats**: `googAvailableSendBandwidth` (affected by GCC)
   - **RTCRemoteInboundRtpStream**: Should show round-trip time stats

## Validating TWCC Extension in SDP

### Python Script to Check SDP:

```python
def validate_twcc_in_sdp(sdp_text):
    """Check if TWCC extension is in SDP."""
    lines = sdp_text.split('\\n')

    twcc_uris = [
        'draft-holmer-rmcat-transport-wide-cc-extensions',
        'transport-wide-cc',
        'transport-cc'
    ]

    for line in lines:
        if line.startswith('a=extmap:'):
            for uri in twcc_uris:
                if uri in line:
                    print(f"✅ TWCC extension found: {line}")
                    # Extract extension ID
                    ext_id = line.split(':')[1].split()[0]
                    print(f"   Extension ID: {ext_id}")
                    return True

    print("❌ TWCC extension NOT found in SDP")
    return False

# Usage:
offer = await pc.createOffer()
validate_twcc_in_sdp(offer.sdp)
```

## Capturing and Analyzing RTCP Packets

### Using tcpdump:

```bash
# Capture RTCP packets
sudo tcpdump -i any -w twcc_capture.pcap 'udp and port 5000'

# Analyze with tshark
tshark -r twcc_capture.pcap -Y "rtcp.pt == 205" -V
```

### Expected RTCP TWCC Packet Structure:

```
RTCP Packet:
  Version: 2
  Padding: 0
  Format (FMT): 15 (Transport-wide congestion control)
  Packet Type (PT): 205 (Generic RTP Feedback)
  Length: X words
  SSRC of packet sender: XXXXXXXX
  SSRC of media source: XXXXXXXX
  Base sequence number: XXXX
  Packet status count: XXXX
  Reference time: XXXXXX (24-bit, 64ms units)
  Feedback packet count: XX
  Packet chunks: [...]
  Receive deltas: [...]
```

## Verifying Bitrate Control

### Monitor Encoder Bitrate Over Time:

```python
import asyncio

async def monitor_bitrate(sender, duration=30):
    """Monitor encoder bitrate changes."""
    bitrates = []

    for i in range(duration):
        if sender._RTCRtpSender__encoder and hasattr(sender._RTCRtpSender__encoder, 'target_bitrate'):
            bitrate = sender._RTCRtpSender__encoder.target_bitrate
            bitrates.append((i, bitrate))
            print(f"[{i}s] Encoder bitrate: {bitrate/1000:.1f} kbps")

        await asyncio.sleep(1)

    # Check if bitrate changed
    if len(set(b[1] for b in bitrates)) > 1:
        print("✅ Encoder bitrate is adapting!")
    else:
        print("⚠️  Encoder bitrate is static")

    return bitrates

# Usage:
await monitor_bitrate(sender, duration=30)
```

## Common Issues and Solutions

### Issue: TWCC extension not in SDP
**Solution**: Make sure you're calling `sender.enable_gcc()` BEFORE creating the offer/answer.

### Issue: Transport sequence numbers not in RTP packets
**Solution**: Verify that `transport_seq_manager` is passed to `enable_gcc()`.

### Issue: TWCC feedback not being sent
**Solution**: Call `receiver.enable_twcc(ssrc)` on the receiving side.

### Issue: GCC stats show 0 packets_received
**Solution**:
- Check that RTCP feedback is reaching the sender
- Verify firewall rules allow RTCP traffic
- Check that the RTCP PT=205 FMT=15 packets are being parsed correctly

### Issue: Encoder bitrate not changing
**Solution**:
- Verify encoder has `target_bitrate` attribute
- Check GCC logs for "GCC bandwidth estimate" messages
- Ensure sufficient packet exchange (need ~100+ packets for GCC to converge)

## Performance Metrics

### Good TWCC/GCC Behavior:

- **Feedback Rate**: ~1-5 Hz (feedback every 200ms - 1s)
- **Convergence Time**: 2-5 seconds to reach stable estimate
- **Bitrate Adaptation**: Should respond to network changes within 1-2 RTTs
- **Overhead**: TWCC adds ~2 bytes per RTP packet + periodic RTCP feedback

### Example Good Stats:

```python
{
  'current_estimate_bps': 1500000,      # 1.5 Mbps
  'current_estimate_kbps': 1500.0,
  'delay_estimate_bps': 1500000,         # Close to current
  'loss_estimate_bps': 2000000,          # Higher (no loss)
  'packets_received': 3450,              # Growing
  'packets_sent': 0,
  'incoming_bitrate_bps': 1480000        # Measured ~matches estimate
}
```

## Automated Validation Script

```python
async def validate_twcc_gcc(pc1, pc2, duration=10):
    """
    Comprehensive validation of TWCC/GCC between two peers.

    Args:
        pc1: RTCPeerConnection sending video
        pc2: RTCPeerConnection receiving video
        duration: Test duration in seconds
    """
    results = {
        'twcc_in_sdp': False,
        'transport_seq_present': False,
        'feedback_generated': False,
        'gcc_active': False,
        'bitrate_adapting': False
    }

    # Check SDP
    offer = await pc1.createOffer()
    results['twcc_in_sdp'] = 'transport' in offer.sdp.lower()

    # Complete signaling...
    await pc1.setLocalDescription(offer)
    await pc2.setRemoteDescription(pc1.localDescription)
    answer = await pc2.createAnswer()
    await pc2.setLocalDescription(answer)
    await pc1.setRemoteDescription(pc2.localDescription)

    # Stream and monitor
    await asyncio.sleep(duration)

    # Check sender
    for sender in pc1.getSenders():
        if sender._RTCRtpSender__gcc_estimator:
            stats = sender._RTCRtpSender__gcc_estimator.get_stats()
            results['gcc_active'] = stats['packets_received'] > 0
            results['transport_seq_present'] = sender._RTCRtpSender__packet_count > 0

    # Check receiver
    for receiver in pc2.getReceivers():
        if receiver._RTCRtpReceiver__twcc_recorder:
            results['feedback_generated'] = True

    # Report
    print("\\n" + "="*50)
    print("TWCC/GCC Validation Results:")
    print("="*50)
    for key, value in results.items():
        status = "✅" if value else "❌"
        print(f"{status} {key}: {value}")
    print("="*50)

    return all(results.values())
```

## Chrome-Specific Verification

When testing with Chrome, check these in `chrome://webrtc-internals`:

1. **RTCIceCandidatePair**:
   - Look for `bytesSent` and `bytesReceived` increasing

2. **RTCOutboundRTPVideoStream**:
   - `qualityLimitationReason`: Should show "bandwidth" if GCC is active
   - `headerBytesSent`: Should be non-zero (includes extensions)

3. **RTCRemoteInboundRtpStream**:
   - `roundTripTime`: Should be populated (indicates TWCC feedback working)
   - `packetsReceived`: Should match sender's packetsSent

4. **RTCMediaStreamTrack**:
   - Check `framesSent` is growing

## Conclusion

A working TWCC/GCC implementation should show:
- ✅ TWCC extension in SDP
- ✅ Transport sequence numbers on RTP packets
- ✅ TWCC feedback being generated and sent
- ✅ GCC estimator receiving and processing feedback
- ✅ Encoder bitrate adapting based on estimates
- ✅ Round-trip time stats in Chrome's webrtc-internals
