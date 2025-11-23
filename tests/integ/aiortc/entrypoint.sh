#!/bin/sh
# aiortc peer entrypoint
# NOTE: tc netem disabled - use UDP proxy for rate limiting instead

set -e

echo "Starting aiortc peer..."
echo "NOTE: For rate limiting with GCC adaptation testing:"
echo "      - tc netem is DISABLED (operates at kernel level - invisible to GCC)"
echo "      - Use udp_rate_limiter.py proxy instead (application level - observable by GCC)"

# Start aiortc peer
exec python3 /app/peer_twcc.py --host 0.0.0.0
