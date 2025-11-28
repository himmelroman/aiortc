#!/bin/bash
# aiortc ↔ chromium GCC Test
export PEER_A_TYPE=aiortc
export PEER_B_TYPE=chromium
exec "$(dirname "$0")/bwe_test_framework.sh"
