#!/bin/sh
# aiortc peer entrypoint
# Supports both TWCC and loss-based BWE variants

set -e

# Determine which peer script to run
PEER_SCRIPT="${PEER_SCRIPT:-peer_twcc.py}"

echo "Starting aiortc peer (script: $PEER_SCRIPT)..."

if [ "$PEER_SCRIPT" = "peer_bwe.py" ]; then
    echo "Mode: Loss-based BWE (vanilla aiortc)"
else
    echo "Mode: TWCC/GCC (forked aiortc)"
    echo "NOTE: For rate limiting with GCC adaptation testing:"
    echo "      - tc netem is DISABLED (operates at kernel level - invisible to GCC)"
    echo "      - Use udp_rate_limiter.py proxy instead (application level - observable by GCC)"
fi

# Start aiortc peer with unbuffered output
exec python3 -u /app/$PEER_SCRIPT --host 0.0.0.0
