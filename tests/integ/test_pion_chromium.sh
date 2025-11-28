#!/bin/bash
# pion ↔ chromium GCC Test
export PEER_A_TYPE=pion
export PEER_B_TYPE=chromium
exec "$(dirname "$0")/bwe_test_framework.sh"
