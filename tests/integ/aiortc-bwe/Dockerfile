# Dockerfile for aiortc with loss-based BWE (vanilla aiortc from pip)
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

# Install vanilla aiortc from pip (NOT the fork)
RUN pip3 install aiortc aiohttp numpy

# Copy peer script and loss-based BWE module
COPY tests/integ/aiortc-bwe/peer_bwe.py /app/peer_bwe.py
COPY tests/integ/aiortc-bwe/loss_based_bwe.py /app/loss_based_bwe.py
COPY tests/integ/aiortc-bwe/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]
