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

# Start HTTP server to serve test video (in background)
cd /app
python3 -m http.server 8888 >/dev/null 2>&1 &
HTTP_SERVER_PID=$!
echo "HTTP server started on port 8888 (PID: $HTTP_SERVER_PID) to serve test_video.webm"

# Trap to clean up HTTP server on exit
trap "kill $HTTP_SERVER_PID 2>/dev/null || true" EXIT INT TERM

# Give HTTP server a moment to start
sleep 1

# Run the peer
exec node /app/peer.js
