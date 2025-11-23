# Dockerfile for Chromium TWCC Peer with Playwright
FROM node:20-slim

# Install dependencies for Playwright Chromium and network tools
RUN apt-get update && \
    apt-get install -y \
    iproute2 \
    iputils-ping \
    curl \
    wget \
    && apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy package files first
COPY package.json /app/

# Install Node dependencies and Playwright browsers
RUN npm install && \
    npx playwright install --with-deps chromium

# Copy peer script and entrypoint
COPY peer_twcc.js /app/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /app/peer_twcc.js /entrypoint.sh

# Default server URL
ENV SERVER_URL="http://host.docker.internal:8080"

ENTRYPOINT ["/entrypoint.sh"]
