# Dockerfile for vanilla pion with loss-based BWE
FROM golang:1.24-alpine AS builder

WORKDIR /build
COPY go.mod ./
COPY main.go ./
RUN go mod download
RUN go mod tidy
RUN CGO_ENABLED=0 GOOS=linux go build -o peer_vanilla main.go

FROM nicolaka/netshoot:latest
WORKDIR /app

# Copy binary and pre-generated test video
COPY --from=builder /build/peer_vanilla /app/peer_vanilla
COPY test_video.ivf /app/test_video.ivf
RUN chmod +x /app/peer_vanilla && ls -lh /app/test_video.ivf

EXPOSE 8080

# Server mode by default
CMD ["/app/peer_vanilla"]
