# Dockerfile for Pion TWCC Peer with Traffic Control
# Multi-stage build: compile Go binary, then copy to runtime image

# Stage 1: Build the Go binary
FROM golang:1.24-alpine AS builder

WORKDIR /build

# Copy go mod files and local dependencies
COPY go.mod go.sum ./
COPY local_pion_gcc ./local_pion_gcc
RUN go mod download

# Copy source code
COPY main.go ./

# Build for linux/amd64 (or linux/arm64 depending on target)
RUN CGO_ENABLED=0 GOOS=linux go build -o peer_twcc main.go

# Stage 2: Runtime image with network tools
FROM nicolaka/netshoot:latest

WORKDIR /app

# Copy binary from builder
COPY --from=builder /build/peer_twcc /app/peer_twcc
COPY entrypoint.sh /entrypoint.sh

RUN chmod +x /app/peer_twcc /entrypoint.sh

# Default server URL
ENV SERVER_URL="http://host.docker.internal:8080"

ENTRYPOINT ["/entrypoint.sh"]
