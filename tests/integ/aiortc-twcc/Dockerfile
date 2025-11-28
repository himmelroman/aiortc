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

# Install ALL dependencies first (these rarely change, so they'll be cached)
RUN pip3 install aiohttp aioice numpy av cryptography google-crc32c pyee pylibsrtp pyopenssl yappi uvloop

# Copy just the requirements/setup files first to install aiortc dependencies
COPY setup.py LICENSE README.rst /app/aiortc_src/

# Copy source code directory (this changes frequently, but we do it BEFORE install)
COPY src /app/aiortc_src/src/

# Install aiortc in editable mode (deps already cached, only registers package)
RUN cd /app/aiortc_src && pip3 install -e .

# Copy peer script and entrypoint from the aiortc test directory
COPY tests/integ/aiortc/peer_twcc.py /app/peer_twcc.py
COPY tests/integ/aiortc/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]
