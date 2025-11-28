#!/bin/bash
# Generic test video generator for BWE testing
# Generates VP8 encoded videos in WebM or IVF format

set -e

# Parse command-line arguments
OUTPUT_FILE=""
DURATION="60"
BITRATE="5M"
FORMAT="webm"
VISUAL_EFFECT="noise"

usage() {
    echo "Usage: $0 -o OUTPUT_FILE [-d DURATION] [-b BITRATE] [-f FORMAT] [-e EFFECT]"
    echo ""
    echo "Required:"
    echo "  -o OUTPUT_FILE    Output file path (e.g., 'chromium/test_video.webm')"
    echo ""
    echo "Optional:"
    echo "  -d DURATION       Video duration in seconds (default: 60)"
    echo "  -b BITRATE        Target bitrate (default: 5M)"
    echo "  -f FORMAT         Output format: webm or ivf (default: webm)"
    echo "  -e EFFECT         Visual effect: noise or blend (default: noise)"
    echo ""
    exit 1
}

while getopts "o:d:b:f:e:h" opt; do
    case $opt in
        o) OUTPUT_FILE="$OPTARG" ;;
        d) DURATION="$OPTARG" ;;
        b) BITRATE="$OPTARG" ;;
        f) FORMAT="$OPTARG" ;;
        e) VISUAL_EFFECT="$OPTARG" ;;
        h) usage ;;
        *) usage ;;
    esac
done

if [ -z "$OUTPUT_FILE" ]; then
    echo "ERROR: Output file is required"
    usage
fi

# Check if file already exists
if [ -f "$OUTPUT_FILE" ]; then
    echo "✅ Video already exists: $OUTPUT_FILE ($(du -h "$OUTPUT_FILE" | cut -f1))"
    exit 0
fi

echo "Generating test video: $OUTPUT_FILE"
echo "  - Duration: ${DURATION}s"
echo "  - Bitrate: $BITRATE"
echo "  - Format: $FORMAT"
echo "  - Effect: $VISUAL_EFFECT"

# Ensure output directory exists
mkdir -p "$(dirname "$OUTPUT_FILE")"

# Build ffmpeg command based on visual effect
if [ "$VISUAL_EFFECT" = "blend" ]; then
    # Blend effect (more complex visual pattern)
    ffmpeg -y \
        -f lavfi -i "testsrc2=size=1280x720:rate=30:duration=$DURATION" \
        -f lavfi -i "geq=random(1)*255:128:128" \
        -filter_complex "[0:v][1:v]blend=all_mode=addition:all_opacity=0.3" \
        -c:v libvpx \
        -b:v "$BITRATE" \
        -g 30 \
        -deadline realtime \
        -cpu-used 5 \
        -f "$FORMAT" "$OUTPUT_FILE"
else
    # Noise effect (simpler, default)
    if [ "$FORMAT" = "ivf" ]; then
        # IVF format requires specific output parameters
        ffmpeg -y \
            -f lavfi -i "testsrc=size=1280x720:rate=30:duration=$DURATION" \
            -vf "noise=alls=20:allf=t+u" \
            -c:v libvpx \
            -b:v "$BITRATE" \
            -g 30 \
            -deadline realtime \
            -cpu-used 5 \
            -f ivf "$OUTPUT_FILE"
    else
        # WebM format
        ffmpeg -y \
            -f lavfi -i "testsrc=size=1280x720:rate=30:duration=$DURATION" \
            -vf "noise=alls=20:allf=t+u" \
            -c:v libvpx \
            -b:v "$BITRATE" \
            -g 30 \
            -deadline realtime \
            -cpu-used 5 \
            -pix_fmt yuv420p \
            -an \
            "$OUTPUT_FILE"
    fi
fi

echo "✅ Generated: $OUTPUT_FILE ($(du -h "$OUTPUT_FILE" | cut -f1))"
