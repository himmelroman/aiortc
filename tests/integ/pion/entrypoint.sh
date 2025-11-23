#!/bin/sh
# Docker entrypoint script for Pion peer
# NOTE: tc netem disabled - use UDP proxy for rate limiting instead

set -e

echo "Starting Pion peer..."
echo "NOTE: For rate limiting with GCC adaptation testing:"
echo "      - tc netem is DISABLED (operates at kernel level - invisible to GCC)"
echo "      - Use udp_rate_limiter.py proxy instead (application level - observable by GCC)"

# Start Pion peer
if [ "$#" -eq 0 ]; then
    exec /app/peer_twcc -server "$SERVER_URL"
else
    exec /app/peer_twcc "$@"
fi
