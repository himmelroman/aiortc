#!/bin/bash
#
# Chromium TWCC Peer Entrypoint
#

echo "=================================================================="
echo "   Chromium TWCC Peer"
echo "=================================================================="
echo ""
echo "Server URL: ${SERVER_URL:-http://host.docker.internal:8080}"
echo ""
echo "Traffic control commands:"
echo "  Apply limit: docker exec <container> tc qdisc add dev eth0 root tbf rate XMbit burst 32kbit latency 50ms"
echo "  Remove limit: docker exec <container> tc qdisc del dev eth0 root"
echo ""
echo "=================================================================="
echo ""

# Run the peer
exec node /app/peer_twcc.js
