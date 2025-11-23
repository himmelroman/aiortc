# Dockerfile for aiortc TWCC peer with traffic control
FROM python:3.11-slim

# Install network tools (tc), ffmpeg, and build dependencies for aiortc
RUN apt-get update && \
    apt-get install -y iproute2 ffmpeg build-essential \
        libavformat-dev libavcodec-dev libavdevice-dev \
        libavutil-dev libswscale-dev libswresample-dev \
        libavfilter-dev libopus-dev libvpx-dev pkg-config && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install base dependencies
RUN pip3 install aiohttp numpy av

# Copy entire local aiortc project (with TWCC support) - context is aiortc repo root
COPY . /app/aiortc_src/

# Install local aiortc with TWCC support
RUN cd /app/aiortc_src && pip3 install -e .

# Copy peer script and entrypoint from the aiortc test directory
COPY tests/integ/aiortc/peer_twcc.py /app/peer_twcc.py
COPY tests/integ/aiortc/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]
