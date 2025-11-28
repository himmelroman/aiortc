#!/bin/bash
# aiortc ↔ pion GCC Test
export PEER_A_TYPE=aiortc
export PEER_B_TYPE=pion
exec "$(dirname "$0")/bwe_test_framework.sh"
