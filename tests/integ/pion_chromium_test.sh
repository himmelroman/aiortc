#!/bin/bash
#
# Bidirectional GCC Adaptation Test - Pion ↔ Chromium
# Tests GCC adaptation in both directions with bandwidth limiting
#

set -e

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

IMAGE_NAME_PION="pion-peer"
IMAGE_NAME_CHROMIUM="chromium-peer"
NETWORK_NAME="pion-chromium-test"

echo "==================================================================="
echo "   BIDIRECTIONAL GCC ADAPTATION TEST - PION ↔ CHROMIUM"
echo "==================================================================="
echo ""
echo "This test validates GCC adaptation in BOTH directions:"
echo "  - pion sends video → Chromium (with GCC)"
echo "  - Chromium sends video → pion (with GCC)"
echo ""
echo "Test Phases:"
echo "  Phase 1: Baseline - No limiting (15s)"
echo "  Phase 2: Limit pion→chromium to 5 Mbps (20s)"
echo "  Phase 3: Limit chromium→pion to 2 Mbps (both limited) (20s)"
echo "  Phase 4: Remove all limits - Recovery (30s)"
echo ""
echo "Expected: Both peers' GCC should adapt to network conditions"
echo "==================================================================="
echo ""

# Build Docker images
echo "Building Docker images..."
echo "  - Building pion peer..."
docker build -f "$SCRIPT_DIR/pion/Dockerfile" -t "$IMAGE_NAME_PION" "$SCRIPT_DIR/pion" > /dev/null
echo "  - Building chromium peer..."
docker build -f "$SCRIPT_DIR/chromium/Dockerfile" -t "$IMAGE_NAME_CHROMIUM" "$SCRIPT_DIR/chromium" > /dev/null
echo "✅ Images built"
echo ""

# Cleanup function
cleanup() {
    echo ""
    echo "Cleaning up..."
    docker rm -f pion-server chromium-client 2>/dev/null || true
    docker network rm "$NETWORK_NAME" 2>/dev/null || true
}

trap cleanup EXIT INT TERM

# Create network
docker network create "$NETWORK_NAME" 2>/dev/null || true

# Start both containers
echo "Starting Pion peer (server mode)..."
docker run -d --rm --name pion-server \
    --network "$NETWORK_NAME" \
    --cap-add=NET_ADMIN \
    "$IMAGE_NAME_PION" \
    -serve -port 8080 > /dev/null

sleep 2

echo "Starting Chromium peer (client mode)..."
docker run -d --rm --name chromium-client \
    --network "$NETWORK_NAME" \
    --cap-add=NET_ADMIN \
    -e SERVER_URL="http://pion-server:8080" \
    "$IMAGE_NAME_CHROMIUM" > /dev/null

echo "✅ Both peers started"
echo ""

# Wait for connection
echo "Waiting for WebRTC connection and initial stabilization..."
sleep 8

# Function to get latest stats
get_stats() {
    local peer=$1

    if [ "$peer" = "pion" ]; then
        # Get pion's GCC estimate (what it thinks it can send)
        local gcc=$(docker logs pion-server 2>&1 | grep "📤 Sending GCC:" | tail -1 | grep -oE '[0-9]+\.[0-9]+ Mbps' | head -1)
        # Get what pion is receiving from chromium
        local rx=$(docker logs pion-server 2>&1 | grep "📥 Receiving:" | tail -1 | grep -oE '[0-9]+ packets' | head -1)
        echo "pion: GCC=${gcc:-N/A} RX=${rx:-N/A}"
    else
        # Get chromium's sending and receiving rates
        local tx=$(docker logs chromium-client 2>&1 | grep "📤 Sending:" | tail -1 | grep -oE '[0-9]+\.[0-9]+ Mbps' | head -1)
        local rx=$(docker logs chromium-client 2>&1 | grep "📥 Receiving:" | tail -1 | grep -oE '[0-9]+\.[0-9]+ Mbps' | head -1)
        echo "chromium: TX=${tx:-N/A} RX=${rx:-N/A}"
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
        get_stats "pion" | tr '\n' ' '
        echo -n "| "
        get_stats "chromium"
        sleep 3
    done

    echo ""
    echo "Final stats for $phase_name:"
    echo "  pion:"
    docker logs pion-server 2>&1 | grep "📤 Sending GCC:" | tail -2 | sed 's/^/    /'
    docker logs pion-server 2>&1 | grep "📥 Receiving:" | tail -1 | sed 's/^/    /'
    echo "  chromium:"
    docker logs chromium-client 2>&1 | grep "📤 Sending:" | tail -2 | sed 's/^/    /'
    docker logs chromium-client 2>&1 | grep "📥 Receiving:" | tail -1 | sed 's/^/    /'
}

# Phase 1: Baseline
monitor_phase "Phase 1: Baseline - No Limiting" 15

# Phase 2: Limit bandwidth on pion (affects pion→chromium)
echo ""
echo "  Limiting pion→chromium bandwidth to 5 Mbps"
docker exec pion-server tc qdisc add dev eth0 root tbf rate 5mbit burst 32kbit latency 50ms

monitor_phase "Phase 2: 5 Mbps Limit on pion→chromium" 20

# Phase 3: Limit bandwidth on chromium (affects chromium→pion)
echo ""
echo "  Limiting chromium→pion bandwidth to 2 Mbps (both paths now limited)"
docker exec chromium-client tc qdisc add dev eth0 root tbf rate 2mbit burst 32kbit latency 50ms

monitor_phase "Phase 3: 2 Mbps Limit on chromium→pion (both limited)" 20

# Phase 4: Remove all limits - recovery
echo ""
echo "  Removing all bandwidth limits - testing recovery"
docker exec pion-server tc qdisc del dev eth0 root 2>/dev/null || true
docker exec chromium-client tc qdisc del dev eth0 root 2>/dev/null || true

monitor_phase "Phase 4: Recovery - No Limiting" 30

echo ""
echo "============================================================"
echo "   TEST SUMMARY"
echo "============================================================"
echo ""
echo "Final GCC Values:"
echo "  pion (sending to chromium):"
docker logs pion-server 2>&1 | grep "📤 Sending GCC:" | tail -3 | sed 's/^/    /'
echo "  chromium (sending to pion):"
docker logs chromium-client 2>&1 | grep "📤 Sending:" | tail -3 | sed 's/^/    /'
echo ""
echo "✅ Test complete!"
