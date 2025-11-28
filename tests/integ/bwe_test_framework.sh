#!/bin/bash
#
# GCC Test Framework - Centralized testing infrastructure
# Supports any peer combination with flexible phase definitions
#

set -e

# ============================================================================
# CONFIGURATION
# ============================================================================

# Peer configuration
PEER_A_TYPE="${PEER_A_TYPE:-}"           # aiortc, pion, or chromium
PEER_B_TYPE="${PEER_B_TYPE:-}"           # aiortc, pion, or chromium

# Quick mode flag (for fast verification)
QUICK_MODE="${QUICK_MODE:-false}"        # Set to "true" for simplified test

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Validate required parameters
if [ -z "$PEER_A_TYPE" ] || [ -z "$PEER_B_TYPE" ]; then
    echo "ERROR: PEER_A_TYPE and PEER_B_TYPE must be set"
    echo "Usage: PEER_A_TYPE=aiortc PEER_B_TYPE=pion $0"
    exit 1
fi

# ============================================================================
# PEER TYPE DEFINITIONS
# ============================================================================

# Helper functions to get peer-specific configuration
get_image_name() {
    case "$1" in
        aiortc-twcc) echo "aiortc-twcc-peer" ;;
        aiortc-bwe) echo "aiortc-bwe-peer" ;;
        pion) echo "pion-twcc-peer" ;;
        chromium) echo "chromium-twcc-peer" ;;
    esac
}

get_build_context() {
    case "$1" in
        aiortc-twcc) echo "../.." ;;  # aiortc repo root
        aiortc-bwe) echo "../.." ;;
        pion) echo "pion" ;;
        chromium) echo "chromium" ;;
    esac
}

get_dockerfile() {
    case "$1" in
        aiortc-twcc) echo "aiortc-twcc/Dockerfile" ;;
        aiortc-bwe) echo "aiortc-bwe/Dockerfile" ;;
        pion) echo "pion/Dockerfile" ;;
        chromium) echo "chromium/Dockerfile" ;;
    esac
}

# ============================================================================
# TEST PHASE DEFINITIONS
# ============================================================================

# Gradual multi-step bandwidth changes
if [ "$QUICK_MODE" = "true" ]; then
    # Quick mode: just one 10s baseline phase
    declare -a PHASES=(
        "baseline:10:unlimited:unlimited"
    )
else
    declare -a PHASES=(
        # Baseline - no limits (0% loss → BWE increases)
        "baseline:20:unlimited:unlimited"

        # Test peer_b direction (peer_b → peer_a)
        "b_degrade_8:15:unlimited:8"       # 8 Mbps + 5% loss (hold steady: 2% < loss < 10%)
        "b_degrade_4:15:unlimited:4"       # 4 Mbps + 12% loss (decrease: loss > 10%)
        "b_severe:15:unlimited:2"          # 2 Mbps + 15% loss (aggressive decrease)
        "b_hold_low:20:unlimited:8"        # 8 Mbps + 5% loss (should hold at LOW rate, not recover)
        "b_recovery:40:unlimited:unlimited" # 0% loss (additive increase)

        # Test peer_a direction (peer_a → peer_b)
        "a_degrade_8:15:8:unlimited"       # 8 Mbps + 5% loss (hold steady)
        "a_degrade_4:15:4:unlimited"       # 4 Mbps + 12% loss (decrease)
        "a_severe:15:2:unlimited"          # 2 Mbps + 15% loss (aggressive decrease)
        "a_hold_low:20:8:unlimited"        # 8 Mbps + 5% loss (should hold at LOW rate, not recover)
        "a_recovery:40:unlimited:unlimited" # 0% loss (additive increase)
    )
fi

# ============================================================================
# LOGGING SETUP AND UNIQUE NAMING
# ============================================================================

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
TEST_NAME="${PEER_A_TYPE}_${PEER_B_TYPE}"
LOG_DIR="$SCRIPT_DIR/logs/${TIMESTAMP}_${TEST_NAME}"
mkdir -p "$LOG_DIR"

# Unique container names per test run (for parallel execution)
TEST_ID="${TEST_NAME}_${TIMESTAMP}"
PEER_A_NAME="gcc-peer-a-${TEST_ID}"
PEER_B_NAME="gcc-peer-b-${TEST_ID}"
NETWORK_NAME="gcc-network-${TEST_ID}"

LOG_PEER_A="$LOG_DIR/peer_a_${PEER_A_TYPE}.log"
LOG_PEER_B="$LOG_DIR/peer_b_${PEER_B_TYPE}.log"
LOG_SUMMARY="$LOG_DIR/test_summary.log"

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

# Get peer-specific metrics extraction patterns
get_gcc_pattern() {
    local peer_type=$1
    case $peer_type in
        aiortc-twcc)
            echo "📤 Sending GCC:"
            ;;
        aiortc-bwe)
            echo "📤 Sending Loss BWE:"
            ;;
        pion)
            echo "📤 Loss BWE:"
            ;;
        chromium)
            echo "📤 Sending GCC:"
            ;;
    esac
}

get_rx_pattern() {
    local peer_type=$1
    echo "📥 Receiving:"
}

# Extract GCC stats for a peer
get_peer_stats() {
    local container=$1
    local peer_type=$2

    local gcc_pattern=$(get_gcc_pattern $peer_type)
    local rx_pattern=$(get_rx_pattern $peer_type)

    local gcc=$(docker logs $container 2>&1 | grep "$gcc_pattern" | tail -1 | grep -oE '[0-9]+\.[0-9]+ Mbps' | head -1)
    local rx=$(docker logs $container 2>&1 | grep "$rx_pattern" | tail -1 | grep -oE '[0-9]+\.[0-9]+ Mbps' | head -1)

    echo "${peer_type}: GCC=${gcc:-N/A} RX=${rx:-N/A}"
}

# Set rate limit on a container (loss-focused for BWE testing)
set_rate_limit() {
    local container=$1
    local rate_mbps=$2

    # Remove existing qdisc first
    docker exec $container tc qdisc del dev eth0 root 2>/dev/null || true

    if [ "$rate_mbps" != "unlimited" ]; then
        # Map bandwidth limits to loss percentages testing all BWE regions:
        # - 8 Mbps: 5% loss (hold steady region: 2% < loss < 10%)
        # - 4 Mbps: 12% loss (decrease region: loss > 10%)
        # - 2 Mbps: 15% loss (aggressive decrease)
        local loss_pct

        case "$rate_mbps" in
            8)
                loss_pct="5%"   # Test hold-steady region
                ;;
            4)
                loss_pct="12%"  # Test decrease region
                ;;
            2)
                loss_pct="15%"  # Test aggressive decrease
                ;;
            *)
                loss_pct="10%"  # Default to threshold
                ;;
        esac

        # Apply loss directly without rate limiting (loss applies to all packets)
        # Rate limiting prevented loss when sender rate < limit
        docker exec $container tc qdisc add dev eth0 root netem loss $loss_pct
        echo "  → Set ${container} egress to ${loss_pct} loss (netem, no rate limit)"
    else
        echo "  → Removed limit from ${container}"
    fi
}

# EXPERIMENTAL A: Netem with rate + aggressive loss (15%)
set_rate_limit_netem() {
    local container=$1
    local rate_mbps=$2

    docker exec $container tc qdisc del dev eth0 root 2>/dev/null || true

    if [ "$rate_mbps" != "unlimited" ]; then
        # Netem with rate limiting and 15% loss
        docker exec $container tc qdisc add dev eth0 root netem rate ${rate_mbps}mbit loss 15%
        echo "  → Set ${container} egress to ${rate_mbps} Mbps + 15% loss (netem rate)"
    else
        echo "  → Removed limit from ${container}"
    fi
}

# EXPERIMENTAL B: TBF with minimal queue + aggressive loss (10%)
set_rate_limit_tbf_minimal() {
    local container=$1
    local rate_mbps=$2

    docker exec $container tc qdisc del dev eth0 root 2>/dev/null || true

    if [ "$rate_mbps" != "unlimited" ]; then
        # TBF with very small queue (minimal buffering) + 10% loss
        docker exec $container tc qdisc add dev eth0 root handle 1: tbf rate ${rate_mbps}mbit burst 1500 limit 3000
        docker exec $container tc qdisc add dev eth0 parent 1:1 handle 10: netem loss 10%
        echo "  → Set ${container} egress to ${rate_mbps} Mbps + 10% loss (TBF minimal queue)"
    else
        echo "  → Removed limit from ${container}"
    fi
}

# EXPERIMENTAL C: HTB + netem with aggressive loss (15%)
set_rate_limit_htb() {
    local container=$1
    local rate_mbps=$2

    docker exec $container tc qdisc del dev eth0 root 2>/dev/null || true

    if [ "$rate_mbps" != "unlimited" ]; then
        # HTB with rate limit + 15% netem loss
        docker exec $container tc qdisc add dev eth0 root handle 1: htb default 10
        docker exec $container tc class add dev eth0 parent 1: classid 1:1 htb rate ${rate_mbps}mbit burst 1500
        docker exec $container tc qdisc add dev eth0 parent 1:1 handle 10: netem loss 15%
        echo "  → Set ${container} egress to ${rate_mbps} Mbps + 15% loss (HTB+netem)"
    else
        echo "  → Removed limit from ${container}"
    fi
}

# Monitor phase with periodic stats collection
monitor_phase() {
    local phase_name="$1"
    local duration="$2"

    echo ""
    echo "============================================================"
    echo "[$phase_name]"
    echo "============================================================"

    local iterations=$((duration / 5))
    for i in $(seq 1 $iterations); do
        echo -n "  "
        get_peer_stats "$PEER_A_NAME" "$PEER_A_TYPE" | tr '\n' ' '
        echo -n "| "
        get_peer_stats "$PEER_B_NAME" "$PEER_B_TYPE"
        sleep 5
    done

    echo ""
}

# ============================================================================
# TEST LIFECYCLE
# ============================================================================

# Cleanup function
CLEANUP_DONE=0
cleanup() {
    if [ $CLEANUP_DONE -eq 1 ]; then
        return
    fi
    CLEANUP_DONE=1

    echo ""
    echo "Saving full container logs..."
    docker logs $PEER_A_NAME > "$LOG_PEER_A" 2>&1 || echo "  Could not save peer A logs"
    docker logs $PEER_B_NAME > "$LOG_PEER_B" 2>&1 || echo "  Could not save peer B logs"
    echo "  ✅ Logs saved to: $LOG_DIR"

    echo "Cleaning up containers..."
    docker rm -f $PEER_A_NAME $PEER_B_NAME 2>/dev/null || true
    docker network rm "$NETWORK_NAME" 2>/dev/null || true
}

trap cleanup EXIT INT TERM

# Generate test videos for peers that need them
generate_videos() {
    echo "Checking test videos..."

    # Chromium peer needs WebM video
    if [ "$PEER_A_TYPE" = "chromium" ] || [ "$PEER_B_TYPE" = "chromium" ]; then
        "$SCRIPT_DIR/generate_test_video.sh" \
            -o "$SCRIPT_DIR/chromium/test_video.webm" \
            -d 60 \
            -b 5M \
            -f webm \
            -e noise
    fi

    # Pion peer needs IVF video
    if [ "$PEER_A_TYPE" = "pion" ] || [ "$PEER_B_TYPE" = "pion" ]; then
        "$SCRIPT_DIR/generate_test_video.sh" \
            -o "$SCRIPT_DIR/pion/test_video.ivf" \
            -d 5 \
            -b 3M \
            -f ivf \
            -e blend
    fi

    echo "✅ Videos ready"
    echo ""
}

# Build Docker images
build_images() {
    echo "Building Docker images..."

    local peer_a_image="$(get_image_name "$PEER_A_TYPE")"
    local peer_b_image="$(get_image_name "$PEER_B_TYPE")"

    # Build peer A image (if not already built or different from B)
    echo "  - Building $PEER_A_TYPE peer..."
    docker build -f "$SCRIPT_DIR/$(get_dockerfile "$PEER_A_TYPE")" \
        -t "$peer_a_image" \
        "$SCRIPT_DIR/$(get_build_context "$PEER_A_TYPE")" > /dev/null

    # Build peer B image (only if different type from A)
    if [ "$PEER_A_TYPE" != "$PEER_B_TYPE" ]; then
        echo "  - Building $PEER_B_TYPE peer..."
        docker build -f "$SCRIPT_DIR/$(get_dockerfile "$PEER_B_TYPE")" \
            -t "$peer_b_image" \
            "$SCRIPT_DIR/$(get_build_context "$PEER_B_TYPE")" > /dev/null
    fi

    echo "✅ Images built"
}

# Start test peers
start_peers() {
    # Create network
    docker network create "$NETWORK_NAME" 2>/dev/null || true

    # Start peers with direct signaling
    start_peers_direct

    echo "✅ Both peers started"
    echo ""

    # Wait for connection
    echo "Waiting for WebRTC connection and initial stabilization..."
    sleep 8
}

# Start peers with direct signaling (one peer acts as server)
start_peers_direct() {
    # Start peer A (server role)
    echo "Starting peer A ($PEER_A_TYPE) as server..."

    # Pion runs in server mode by default
    if [ "$PEER_A_TYPE" = "pion" ]; then
        docker run -d --name $PEER_A_NAME \
            --network "$NETWORK_NAME" \
            --cap-add=NET_ADMIN \
            "$(get_image_name "$PEER_A_TYPE")" > /dev/null
    elif [ "$PEER_A_TYPE" = "chromium" ]; then
        # Chromium as server (not typically used - chromium is usually the client)
        docker run -d --name $PEER_A_NAME \
            --network "$NETWORK_NAME" \
            --cap-add=NET_ADMIN \
            "$(get_image_name "$PEER_A_TYPE")" > /dev/null
    elif [ "$PEER_A_TYPE" = "aiortc-bwe" ]; then
        # aiortc-bwe uses a different peer script
        docker run -d --name $PEER_A_NAME \
            --network "$NETWORK_NAME" \
            --cap-add=NET_ADMIN \
            -e PEER_SCRIPT="peer_bwe.py" \
            "$(get_image_name "$PEER_A_TYPE")" > /dev/null
    else
        # aiortc-twcc doesn't need special flags for server mode
        docker run -d --name $PEER_A_NAME \
            --network "$NETWORK_NAME" \
            --cap-add=NET_ADMIN \
            "$(get_image_name "$PEER_A_TYPE")" > /dev/null
    fi

    sleep 2

    # Start peer B (client role)
    echo "Starting peer B ($PEER_B_TYPE) as client..."

    # Set peer-specific configuration for peer B
    if [ "$PEER_B_TYPE" = "chromium" ]; then
        docker run -d --name $PEER_B_NAME \
            --network "$NETWORK_NAME" \
            --cap-add=NET_ADMIN \
            -e SERVER_URL="http://${PEER_A_NAME}:8080" \
            "$(get_image_name "$PEER_B_TYPE")" > /dev/null
    elif [ "$PEER_B_TYPE" = "pion" ]; then
        # Pion client mode
        docker run -d --name $PEER_B_NAME \
            --network "$NETWORK_NAME" \
            --cap-add=NET_ADMIN \
            "$(get_image_name "$PEER_B_TYPE")" \
            -offer http://${PEER_A_NAME}:8080/offer > /dev/null
    elif [ "$PEER_B_TYPE" = "aiortc-bwe" ]; then
        # aiortc-bwe uses a different peer script
        docker run -d --name $PEER_B_NAME \
            --network "$NETWORK_NAME" \
            --cap-add=NET_ADMIN \
            -e SERVER_URL="http://${PEER_A_NAME}:8080" \
            -e PEER_SCRIPT="peer_bwe.py" \
            "$(get_image_name "$PEER_B_TYPE")" > /dev/null
    else
        docker run -d --name $PEER_B_NAME \
            --network "$NETWORK_NAME" \
            --cap-add=NET_ADMIN \
            -e SERVER_URL="http://${PEER_A_NAME}:8080" \
            "$(get_image_name "$PEER_B_TYPE")" > /dev/null
    fi
}

# Run test phases
run_test() {
    local total_phases=${#PHASES[@]}
    local phase_num=0

    for phase_def in "${PHASES[@]}"; do
        phase_num=$((phase_num + 1))

        # Parse phase definition: name:duration:peer_a_limit:peer_b_limit
        IFS=':' read -r phase_name duration peer_a_limit peer_b_limit <<< "$phase_def"

        # Apply rate limits
        if [ "$phase_num" -gt 1 ]; then
            echo ""
            echo "Applying phase $phase_num/$total_phases limits..."
            set_rate_limit "$PEER_A_NAME" "$peer_a_limit"
            set_rate_limit "$PEER_B_NAME" "$peer_b_limit"
        fi

        # Monitor phase
        monitor_phase "Phase $phase_num: $phase_name (${duration}s)" "$duration"
    done
}

# Print test summary
print_summary() {
    echo ""
    echo "============================================================"
    echo "   TEST SUMMARY"
    echo "============================================================"
    echo ""
    echo "Test Configuration:"
    echo "  - Peer A: $PEER_A_TYPE"
    echo "  - Peer B: $PEER_B_TYPE"
    echo "  - Total Phases: ${#PHASES[@]}"
    echo ""
    echo "Final GCC Values:"
    echo "  Peer A ($PEER_A_TYPE):"
    docker logs $PEER_A_NAME 2>&1 | grep "$(get_gcc_pattern $PEER_A_TYPE)" | tail -3 | sed 's/^/    /'
    echo "  Peer B ($PEER_B_TYPE):"
    docker logs $PEER_B_NAME 2>&1 | grep "$(get_gcc_pattern $PEER_B_TYPE)" | tail -3 | sed 's/^/    /'
    echo ""
    echo "✅ Test complete!"
    echo ""
    echo "============================================================"
    echo "📁 Full logs saved to:"
    echo "  - Peer A ($PEER_A_TYPE): $LOG_PEER_A"
    echo "  - Peer B ($PEER_B_TYPE): $LOG_PEER_B"
    echo "============================================================"
}

# ============================================================================
# MAIN EXECUTION
# ============================================================================

echo "==================================================================="
echo "   GCC TEST FRAMEWORK - ${PEER_A_TYPE} ↔ ${PEER_B_TYPE}"
echo "==================================================================="
echo ""
echo "Test Configuration:"
echo "  - Peer A: $PEER_A_TYPE (server)"
echo "  - Peer B: $PEER_B_TYPE (client)"
echo "  - Phases: ${#PHASES[@]}"
echo ""
echo "📁 Logs will be saved to: $LOG_DIR"
echo "==================================================================="
echo ""

generate_videos
build_images
start_peers
run_test
print_summary
