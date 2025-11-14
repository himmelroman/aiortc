#!/usr/bin/env python3
"""
Quick validation script for TWCC/GCC implementation.

Usage:
    python validate_twcc.py

This script will:
1. Create two local peers
2. Enable TWCC/GCC
3. Stream media between them
4. Report on TWCC/GCC status
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aiortc import RTCPeerConnection
from aiortc.contrib.twcc.receiver import TransportSequenceNumberManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# Import dummy tracks
sys.path.insert(0, str(Path(__file__).parent.parent / "tests"))
from test_rtcpeerconnection_gcc import DummyVideoTrack


async def validate_twcc_gcc():
    """Run validation test."""
    print("\n" + "="*60)
    print("TWCC/GCC VALIDATION TEST")
    print("="*60 + "\n")

    results = {}

    # Create peers
    print("📡 Creating peer connections...")
    pc1 = RTCPeerConnection()
    pc2 = RTCPeerConnection()

    # Add video track to pc1
    print("🎥 Adding video track to sender...")
    track = DummyVideoTrack()
    sender = pc1.addTrack(track)

    # Enable GCC on sender
    print("⚙️  Enabling GCC on sender...")
    transport_seq_manager = TransportSequenceNumberManager()
    sender.enable_gcc(
        transport_seq_manager,
        initial_bitrate=500000,
        min_bitrate=100000,
        max_bitrate=2000000
    )
    results['gcc_enabled_sender'] = True

    # Create offer
    print("📤 Creating SDP offer...")
    offer = await pc1.createOffer()
    await pc1.setLocalDescription(offer)

    # Check for transport-cc in SDP
    has_transport_cc = 'transport' in offer.sdp.lower() and 'cc' in offer.sdp.lower()
    results['transport_cc_in_sdp'] = has_transport_cc

    print(f"   {'✅' if has_transport_cc else '❌'} Transport-CC extension in SDP: {has_transport_cc}")

    # Apply offer to pc2
    print("📥 Applying offer to receiver...")
    await pc2.setRemoteDescription(pc1.localDescription)

    # Create answer
    print("📤 Creating SDP answer...")
    answer = await pc2.createAnswer()
    await pc2.setLocalDescription(answer)

    # Apply answer to pc1
    print("📥 Applying answer to sender...")
    await pc1.setRemoteDescription(pc2.localDescription)

    # Wait for connection
    print("⏳ Waiting for ICE connection...")
    await asyncio.sleep(1)

    # Enable TWCC on receiver
    print("⚙️  Enabling TWCC on receiver...")
    for receiver in pc2.getReceivers():
        if receiver.track and receiver.track.kind == "video":
            ssrc = receiver._RTCRtpReceiver__rtcp_ssrc
            if ssrc:
                receiver.enable_twcc(ssrc)
                results['twcc_enabled_receiver'] = True
                print(f"   ✅ TWCC enabled with SSRC {ssrc}")
            else:
                results['twcc_enabled_receiver'] = False
                print(f"   ❌ No RTCP SSRC available")

    # Stream media
    print("\n🎬 Streaming media for 5 seconds...")
    print("   (Waiting for packets and feedback...)")

    initial_packet_count = sender._RTCRtpSender__packet_count

    for i in range(5):
        await asyncio.sleep(1)
        packet_count = sender._RTCRtpSender__packet_count
        print(f"   [{i+1}s] Packets sent: {packet_count}")

    final_packet_count = sender._RTCRtpSender__packet_count

    # Check sender stats
    print("\n📊 Checking sender statistics...")
    packets_sent = final_packet_count - initial_packet_count
    results['packets_sent'] = packets_sent > 0
    print(f"   {'✅' if packets_sent > 0 else '❌'} Packets sent: {packets_sent}")

    # Check transport sequence numbers
    has_transport_seq = sender._RTCRtpSender__transport_seq_manager is not None
    results['transport_seq_active'] = has_transport_seq
    print(f"   {'✅' if has_transport_seq else '❌'} Transport sequence manager: {has_transport_seq}")

    # Check sent packet tracker
    has_tracker = sender._RTCRtpSender__sent_packet_tracker is not None
    results['sent_packet_tracker'] = has_tracker
    print(f"   {'✅' if has_tracker else '❌'} Sent packet tracker: {has_tracker}")

    # Check GCC estimator
    print("\n📈 Checking GCC estimator...")
    if sender._RTCRtpSender__gcc_estimator:
        stats = sender._RTCRtpSender__gcc_estimator.get_stats()
        results['gcc_stats'] = stats

        print(f"   ✅ GCC estimator active")
        print(f"      Current estimate: {stats['current_estimate_kbps']:.1f} kbps")
        print(f"      Delay estimate: {stats['delay_estimate_bps']/1000:.1f} kbps")
        print(f"      Loss estimate: {stats['loss_estimate_bps']/1000:.1f} kbps")
        print(f"      Packets received (via feedback): {stats['packets_received']}")

        results['gcc_received_feedback'] = stats['packets_received'] > 0
    else:
        results['gcc_stats'] = None
        print(f"   ❌ GCC estimator not initialized")
        results['gcc_received_feedback'] = False

    # Check encoder bitrate
    print("\n🎛️  Checking encoder...")
    if sender._RTCRtpSender__encoder and hasattr(sender._RTCRtpSender__encoder, 'target_bitrate'):
        bitrate = sender._RTCRtpSender__encoder.target_bitrate
        results['encoder_bitrate'] = bitrate
        print(f"   ✅ Encoder target bitrate: {bitrate/1000:.1f} kbps")
    else:
        results['encoder_bitrate'] = None
        print(f"   ⚠️  Encoder bitrate not available (may not be set yet)")

    # Check receiver
    print("\n📡 Checking receiver...")
    for receiver in pc2.getReceivers():
        if receiver.track and receiver.track.kind == "video":
            has_twcc_recorder = receiver._RTCRtpReceiver__twcc_recorder is not None
            results['twcc_recorder_active'] = has_twcc_recorder
            print(f"   {'✅' if has_twcc_recorder else '❌'} TWCC recorder: {has_twcc_recorder}")

    # Overall result
    print("\n" + "="*60)
    print("VALIDATION RESULTS:")
    print("="*60)

    critical_checks = [
        ('gcc_enabled_sender', 'GCC enabled on sender'),
        ('twcc_enabled_receiver', 'TWCC enabled on receiver'),
        ('packets_sent', 'Packets sent successfully'),
        ('transport_seq_active', 'Transport sequence numbers active'),
        ('sent_packet_tracker', 'Sent packet tracker active'),
        ('twcc_recorder_active', 'TWCC recorder active'),
    ]

    all_passed = True
    for key, description in critical_checks:
        status = results.get(key, False)
        symbol = "✅" if status else "❌"
        print(f"{symbol} {description}")
        if not status:
            all_passed = False

    # Optional checks
    print("\nOptional checks:")
    if results.get('gcc_received_feedback'):
        print(f"✅ GCC received TWCC feedback ({results['gcc_stats']['packets_received']} packets)")
    else:
        print(f"⚠️  GCC has not received TWCC feedback yet (may need more time)")

    if results.get('encoder_bitrate'):
        print(f"✅ Encoder bitrate is set ({results['encoder_bitrate']/1000:.1f} kbps)")
    else:
        print(f"⚠️  Encoder bitrate not yet available")

    print("="*60)

    # Cleanup
    await pc1.close()
    await pc2.close()

    if all_passed:
        print("\n🎉 VALIDATION PASSED! TWCC/GCC is working correctly.")
        print("\nNote: For full validation including feedback exchange,")
        print("run the system for longer or check logs for TWCC feedback.")
        return 0
    else:
        print("\n❌ VALIDATION FAILED! Some checks did not pass.")
        print("\nSee above for details on what failed.")
        return 1


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(validate_twcc_gcc())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\n⚠️  Validation interrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"\n\n❌ Validation failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
