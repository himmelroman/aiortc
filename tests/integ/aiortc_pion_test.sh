#!/bin/bash
#
# Bidirectional GCC Adaptation Test
# Tests both aiortc→pion and pion→aiortc paths with bandwidth limiting
#

set -e

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

IMAGE_NAME_AIORTC="aiortc-twcc-peer"
IMAGE_NAME_PION="pion-twcc-peer"
NETWORK_NAME="gcc-bidir-test"

echo "==================================================================="
echo "   BIDIRECTIONAL GCC ADAPTATION TEST"
echo "==================================================================="
echo ""
echo "This test validates GCC adaptation in BOTH directions:"
echo "  - aiortc sends video → pion (with GCC)"
echo "  - pion sends video → aiortc (with GCC)"
echo ""
echo "Test Phases:"
echo "  Phase 1: Baseline - No limiting (15s)"
echo "  Phase 2: Limit aiortc→pion to 5 Mbps (20s)"
echo "  Phase 3: Limit pion→aiortc to 2 Mbps (both limited) (20s)"
echo "  Phase 4: Remove all limits - Recovery (30s)"
echo ""
echo "Expected: Both peers' GCC should adapt to network conditions"
echo "==================================================================="
echo ""

# Build pion Go binary for Linux ARM64
echo "Building pion client binary (Linux ARM64)..."
cd "$SCRIPT_DIR/pion"
GOOS=linux GOARCH=arm64 go build -o pion-twcc-client > /dev/null 2>&1
echo "✅ Binary built"
echo ""

# Build Docker images
echo "Building Docker images..."
echo "  - Building pion peer..."
docker build -f "$SCRIPT_DIR/pion/Dockerfile" -t "$IMAGE_NAME_PION" "$SCRIPT_DIR/pion" > /dev/null
echo "  - Building aiortc peer..."
# aiortc build context is the aiortc repo root (contains src/, tests/, etc.)
docker build -f "$SCRIPT_DIR/aiortc/Dockerfile" -t "$IMAGE_NAME_AIORTC" "$SCRIPT_DIR/../.." > /dev/null
echo "✅ Images built"
echo ""

# Cleanup function
cleanup() {
    echo ""
    echo "Extracting TWCC packets..."
    docker cp gcc-aiortc:/tmp/aiortc_twcc_packets.bin /tmp/aiortc_twcc_packets.bin 2>/dev/null || echo "  Could not extract aiortc TWCC packets"
    docker cp gcc-pion:/tmp/pion_twcc_packets.bin /tmp/pion_twcc_packets.bin 2>/dev/null || echo "  Could not extract pion TWCC packets"
    echo "Cleaning up..."
    docker rm -f gcc-aiortc gcc-pion 2>/dev/null || true
    docker network rm "$NETWORK_NAME" 2>/dev/null || true
}

trap cleanup EXIT INT TERM

# Create network
docker network create "$NETWORK_NAME" 2>/dev/null || true

# Start both containers
echo "Starting aiortc peer..."
docker run -d --rm --name gcc-aiortc \
    --network "$NETWORK_NAME" \
    --cap-add=NET_ADMIN \
    "$IMAGE_NAME_AIORTC" > /dev/null

sleep 2

echo "Starting pion peer..."
docker run -d --rm --name gcc-pion \
    --network "$NETWORK_NAME" \
    --cap-add=NET_ADMIN \
    -e SERVER_URL="http://gcc-aiortc:8080" \
    "$IMAGE_NAME_PION" > /dev/null

echo "✅ Both peers started"
echo ""

# Wait for connection
echo "Waiting for WebRTC connection and initial stabilization..."
sleep 8

# Function to get latest GCC values
get_gcc_stats() {
    local peer=$1
    local direction=$2

    if [ "$peer" = "aiortc" ]; then
        # Get aiortc's GCC estimate (what it thinks it can send)
        local gcc=$(docker logs gcc-aiortc 2>&1 | grep "📤 Sending GCC:" | tail -1 | grep -oE '[0-9]+\.[0-9]+ Mbps' | head -1)
        # Get what aiortc is actually receiving from pion
        local rx=$(docker logs gcc-aiortc 2>&1 | grep "📥 Receiving:" | tail -1 | grep -oE '[0-9]+\.[0-9]+ Mbps' | head -1)
        echo "aiortc: GCC=${gcc:-N/A} RX=${rx:-N/A}"
    else
        # Get pion's GCC estimate
        local gcc=$(docker logs gcc-pion 2>&1 | grep "📤 Sending GCC:" | tail -1 | grep -oE '[0-9]+\.[0-9]+ Mbps' | head -1)
        # Get what pion is sending and receiving
        local tx=$(docker logs gcc-pion 2>&1 | grep "📤 Sending:" | tail -1 | grep -oE 'Sending: [0-9]+\.[0-9]+ Mbps' | grep -oE '[0-9]+\.[0-9]+ Mbps')
        local rx=$(docker logs gcc-pion 2>&1 | grep "📥 Receiving:" | tail -1 | grep -oE '[0-9]+\.[0-9]+ Mbps' | head -1)
        echo "pion: GCC=${gcc:-N/A} TX=${tx:-N/A} RX=${rx:-N/A}"
    fi
}

# Function to monitor both peers
monitor_phase() {
    local phase_name="$1"
    local duration="$2"

    echo ""
    echo "============================================================"
    echo "[$phase_name]"
    echo "============================================================"

    local iterations=$((duration / 3))
    for i in $(seq 1 $iterations); do
        echo -n "  "
        get_gcc_stats "aiortc" | tr '\n' ' '
        echo -n "| "
        get_gcc_stats "pion"
        sleep 3
    done

    echo ""
    echo "Final stats for $phase_name:"
    echo "  aiortc:"
    docker logs gcc-aiortc 2>&1 | grep "📤 Sending GCC:" | tail -2 | sed 's/^/    /'
    docker logs gcc-aiortc 2>&1 | grep "📥 Receiving:" | tail -1 | sed 's/^/    /'
    echo "  pion:"
    docker logs gcc-pion 2>&1 | grep "📤 Sending GCC:" | tail -2 | sed 's/^/    /'
    docker logs gcc-pion 2>&1 | grep "📤 Sending:" | tail -1 | sed 's/^/    /'
    docker logs gcc-pion 2>&1 | grep "📥 Receiving:" | tail -1 | sed 's/^/    /'
}

# Phase 1: Baseline
monitor_phase "Phase 1: Baseline - No Limiting" 15

# Phase 2: Limit bandwidth on aiortc (affects aiortc→pion)
echo ""
echo "  Limiting aiortc→pion bandwidth to 5 Mbps"
docker exec gcc-aiortc tc qdisc add dev eth0 root tbf rate 5mbit burst 32kbit latency 50ms

monitor_phase "Phase 2: 5 Mbps Limit on aiortc→pion" 20

# Phase 3: Limit bandwidth on pion (affects pion→aiortc)
echo ""
echo "  Limiting pion→aiortc bandwidth to 2 Mbps (both paths now limited)"
docker exec gcc-pion tc qdisc add dev eth0 root tbf rate 2mbit burst 32kbit latency 50ms

monitor_phase "Phase 3: 2 Mbps Limit on pion→aiortc (both limited)" 20

# Phase 4: Remove all limits - recovery
echo ""
echo "  Removing all bandwidth limits - testing recovery"
docker exec gcc-aiortc tc qdisc del dev eth0 root 2>/dev/null || true
docker exec gcc-pion tc qdisc del dev eth0 root 2>/dev/null || true

monitor_phase "Phase 4: Recovery - No Limiting" 30

echo ""
echo "============================================================"
echo "   TEST SUMMARY"
echo "============================================================"
echo ""
echo "Arrival Groups Formed (pion):"
docker logs gcc-pion 2>&1 | grep "groups:" | tail -5
echo ""
echo "Final GCC Values:"
echo "  aiortc (sending to pion):"
docker logs gcc-aiortc 2>&1 | grep "📤 Sending GCC:" | tail -3 | sed 's/^/    /'
echo "  pion (sending to aiortc):"
docker logs gcc-pion 2>&1 | grep "📤 Sending GCC:" | tail -3 | sed 's/^/    /'
echo ""
echo "✅ Test complete!"
