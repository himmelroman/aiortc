package main

import (
	"bytes"
	"encoding/json"
	"flag"
	"io"
	"log"
	"math"
	"net/http"
	"os"
	"sync"
	"time"

	"github.com/pion/interceptor"
	"github.com/pion/interceptor/pkg/stats"
	"github.com/pion/webrtc/v4"
	"github.com/pion/webrtc/v4/pkg/media"
	"github.com/pion/webrtc/v4/pkg/media/ivfreader"
)

// LossBasedBWE implements simple loss-based bandwidth estimation per RFC draft-ietf-rmcat-gcc-02
type LossBasedBWE struct {
	mu                  sync.Mutex
	bitrate             int
	minBitrate          int
	maxBitrate          int
	averageLoss         float64
	lastUpdate          time.Time
	lastIncrease        time.Time
	lastDecrease        time.Time
	packetsSent         uint32
	packetsLost         uint32
	lastPacketsSent     uint32
	lastPacketsLost     uint32
}

const (
	increaseLossThreshold = 0.02  // 2% loss
	increaseTimeThreshold = 200 * time.Millisecond
	increaseFactor        = 1.05

	decreaseLossThreshold = 0.1   // 10% loss
	decreaseTimeThreshold = 200 * time.Millisecond
)

func NewLossBasedBWE(initial, min, max int) *LossBasedBWE {
	return &LossBasedBWE{
		bitrate:    initial,
		minBitrate: min,
		maxBitrate: max,
		lastUpdate: time.Now(),
	}
}

func (bwe *LossBasedBWE) UpdateStats(sent, lost uint32) int {
	bwe.mu.Lock()
	defer bwe.mu.Unlock()

	now := time.Now()

	// Calculate delta
	deltaSent := sent - bwe.lastPacketsSent
	deltaLost := lost - bwe.lastPacketsLost

	if deltaSent == 0 {
		return bwe.bitrate
	}

	lossRatio := float64(deltaLost) / float64(deltaSent)

	// Update average loss (exponential moving average)
	if !bwe.lastUpdate.IsZero() {
		delta := now.Sub(bwe.lastUpdate).Milliseconds()
		// Exponential moving average with time constant tau = 200ms
		alpha := math.Exp(-float64(delta) / 200.0)
		bwe.averageLoss = alpha*bwe.averageLoss + (1-alpha)*lossRatio
	} else {
		bwe.averageLoss = lossRatio
	}

	bwe.lastUpdate = now

	increaseLoss := max(bwe.averageLoss, lossRatio)
	decreaseLoss := min(bwe.averageLoss, lossRatio)

	oldBitrate := bwe.bitrate

	// Increase: low loss + time threshold met
	if increaseLoss < increaseLossThreshold && now.Sub(bwe.lastIncrease) > increaseTimeThreshold {
		bwe.lastIncrease = now
		bwe.bitrate = clamp(int(increaseFactor*float64(bwe.bitrate)), bwe.minBitrate, bwe.maxBitrate)
		if bwe.bitrate != oldBitrate {
			log.Printf("📈 Loss BWE Increase: %d → %d bps (loss=%.3f)", oldBitrate, bwe.bitrate, increaseLoss)
		}
	} else if decreaseLoss > decreaseLossThreshold && now.Sub(bwe.lastDecrease) > decreaseTimeThreshold {
		// Decrease: high loss + time threshold met
		bwe.lastDecrease = now
		decreaseFactor := 1 - (0.5 * decreaseLoss)
		bwe.bitrate = clamp(int(decreaseFactor*float64(bwe.bitrate)), bwe.minBitrate, bwe.maxBitrate)
		if bwe.bitrate != oldBitrate {
			log.Printf("📉 Loss BWE Decrease: %d → %d bps (loss=%.3f)", oldBitrate, bwe.bitrate, decreaseLoss)
		}
	}

	bwe.lastPacketsSent = sent
	bwe.lastPacketsLost = lost

	return bwe.bitrate
}

func (bwe *LossBasedBWE) GetBitrate() int {
	bwe.mu.Lock()
	defer bwe.mu.Unlock()
	return bwe.bitrate
}

func clamp(val, min, max int) int {
	if val < min {
		return min
	}
	if val > max {
		return max
	}
	return val
}

func max(a, b float64) float64 {
	if a > b {
		return a
	}
	return b
}

func min(a, b float64) float64 {
	if a < b {
		return a
	}
	return b
}

var bwe *LossBasedBWE
var statsGetter stats.Getter
var statsGetterMu sync.Mutex

// VideoFrame holds a VP8 frame and its metadata
type VideoFrame struct {
	data     []byte
	duration time.Duration
}

// loadIVFFile loads all frames from an IVF file into memory
func loadIVFFile(filePath string) ([]VideoFrame, error) {
	file, err := os.Open(filePath)
	if err != nil {
		return nil, err
	}
	defer file.Close()

	reader, header, err := ivfreader.NewWith(file)
	if err != nil {
		return nil, err
	}

	log.Printf("Loaded IVF file: codec=%s, width=%d, height=%d, timebase=%d/%d",
		header.FourCC, header.Width, header.Height, header.TimebaseNumerator, header.TimebaseDenominator)

	// Read all frames into memory
	var frames []VideoFrame
	frameDuration := time.Second / time.Duration(header.TimebaseDenominator/header.TimebaseNumerator)

	for {
		frame, _, err := reader.ParseNextFrame()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, err
		}

		frames = append(frames, VideoFrame{
			data:     frame,
			duration: frameDuration,
		})
	}

	log.Printf("Loaded %d frames from IVF file", len(frames))
	return frames, nil
}

func main() {
	offerAddr := flag.String("offer", "", "HTTP address to send offer (client mode)")
	flag.Parse()

	// Initialize loss-based BWE
	bwe = NewLossBasedBWE(
		3_000_000,   // 3 Mbps initial
		500_000,     // 500 kbps min
		50_000_000,  // 50 Mbps max
	)

	if *offerAddr == "" {
		// Server mode
		log.Println("Starting pion vanilla peer (server mode)")
		log.Println("Using loss-based BWE (RFC compliant)")
		http.HandleFunc("/offer", handleOffer)
		log.Fatal(http.ListenAndServe(":8080", nil))
	} else {
		// Client mode
		log.Printf("Starting pion vanilla peer (client mode, connecting to %s)", *offerAddr)
		if err := connectToServer(*offerAddr); err != nil {
			log.Fatal(err)
		}
		select {} // Wait forever
	}
}

func handleOffer(w http.ResponseWriter, r *http.Request) {
	var offer webrtc.SessionDescription
	if err := json.NewDecoder(r.Body).Decode(&offer); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	peerConnection, err := createPeerConnection()
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	if err = peerConnection.SetRemoteDescription(offer); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	answer, err := peerConnection.CreateAnswer(nil)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	if err = peerConnection.SetLocalDescription(answer); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(answer)
}

func connectToServer(offerAddr string) error {
	peerConnection, err := createPeerConnection()
	if err != nil {
		return err
	}

	offer, err := peerConnection.CreateOffer(nil)
	if err != nil {
		return err
	}

	if err = peerConnection.SetLocalDescription(offer); err != nil {
		return err
	}

	// Send offer
	resp, err := http.Post(offerAddr, "application/json", toJSON(offer))
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	var answer webrtc.SessionDescription
	if err = json.NewDecoder(resp.Body).Decode(&answer); err != nil {
		return err
	}

	return peerConnection.SetRemoteDescription(answer)
}

func createPeerConnection() (*webrtc.PeerConnection, error) {
	// Create MediaEngine
	m := &webrtc.MediaEngine{}
	if err := m.RegisterDefaultCodecs(); err != nil {
		return nil, err
	}

	// Create InterceptorRegistry
	i := &interceptor.Registry{}

	// Register RTCP Report interceptors (for sending/receiving RR packets)
	if err := webrtc.ConfigureRTCPReports(i); err != nil {
		return nil, err
	}

	// Register Stats interceptor
	statsInterceptorFactory, err := stats.NewInterceptor()
	if err != nil {
		return nil, err
	}

	// Set callback to receive stats getter when PeerConnection is created
	statsInterceptorFactory.OnNewPeerConnection(func(id string, getter stats.Getter) {
		statsGetterMu.Lock()
		statsGetter = getter
		statsGetterMu.Unlock()
		log.Printf("📊 Stats interceptor initialized for PeerConnection: %s", id)
	})

	i.Add(statsInterceptorFactory)

	// Create WebRTC API with MediaEngine and Interceptor
	api := webrtc.NewAPI(
		webrtc.WithMediaEngine(m),
		webrtc.WithInterceptorRegistry(i),
	)

	config := webrtc.Configuration{
		ICEServers: []webrtc.ICEServer{
			{URLs: []string{"stun:stun.l.google.com:19302"}},
		},
	}

	peerConnection, err := api.NewPeerConnection(config)
	if err != nil {
		return nil, err
	}

	// Add video track
	videoTrack, err := webrtc.NewTrackLocalStaticSample(
		webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeVP8},
		"video",
		"pion-vanilla",
	)
	if err != nil {
		return nil, err
	}

	rtpSender, err := peerConnection.AddTrack(videoTrack)
	if err != nil {
		return nil, err
	}

	// Start sending video
	go sendVideo(videoTrack)

	// Read RTCP packets to populate RemoteInboundRTPStreamStats
	go readRTCP(rtpSender)

	// Monitor BWE
	go monitorBWE(peerConnection, rtpSender)

	// Handle incoming tracks
	peerConnection.OnTrack(func(track *webrtc.TrackRemote, receiver *webrtc.RTPReceiver) {
		log.Printf("📹 Receiving %s track", track.Kind())
		go consumeTrack(track)
	})

	peerConnection.OnConnectionStateChange(func(state webrtc.PeerConnectionState) {
		log.Printf("Connection state: %s", state)
	})

	return peerConnection, nil
}

func sendVideo(track *webrtc.TrackLocalStaticSample) {
	// Load IVF file
	frames, err := loadIVFFile("/app/test_video.ivf")
	if err != nil {
		log.Fatalf("Failed to load IVF file: %v", err)
	}

	// Calculate baseline bitrate from IVF file (assume 30fps for simplicity)
	totalBytes := 0
	for _, frame := range frames {
		totalBytes += len(frame.data)
	}
	baselineBitrate := float64(totalBytes*8) / (float64(len(frames)) / 30.0) // bits per second
	log.Printf("IVF baseline bitrate: %.2f Mbps (based on %d frames)", baselineBitrate/1e6, len(frames))

	frameIndex := 0
	ticker := time.NewTicker(33 * time.Millisecond) // base 30fps
	defer ticker.Stop()

	for range ticker.C {
		// Get current target bitrate from BWE
		targetBitrate := float64(bwe.GetBitrate())

		// Calculate frame rate multiplier based on BWE
		// If BWE says 6 Mbps and baseline is 3 Mbps, send at 2x speed (60fps)
		// If BWE says 1.5 Mbps and baseline is 3 Mbps, send at 0.5x speed (15fps)
		rateMultiplier := targetBitrate / baselineBitrate

		// Clamp rate multiplier to reasonable range
		if rateMultiplier < 0.25 {
			rateMultiplier = 0.25 // minimum 7.5fps
		}
		if rateMultiplier > 3.0 {
			rateMultiplier = 3.0 // maximum 90fps
		}

		// Skip frames if rate is lower than baseline
		// Send multiple frames if rate is higher than baseline
		framesToSend := int(rateMultiplier)
		if framesToSend < 1 {
			// Probabilistic frame sending for rates < 1x
			if time.Now().UnixNano()%100 < int64(rateMultiplier*100) {
				framesToSend = 1
			} else {
				framesToSend = 0
			}
		}

		for i := 0; i < framesToSend; i++ {
			frame := frames[frameIndex%len(frames)]
			frameIndex++

			if err := track.WriteSample(media.Sample{
				Data:     frame.data,
				Duration: frame.duration,
			}); err != nil {
				log.Printf("Error sending video: %v", err)
				return
			}
		}
	}
}

func consumeTrack(track *webrtc.TrackRemote) {
	for {
		if _, _, err := track.ReadRTP(); err != nil {
			if err != io.EOF {
				log.Printf("Error reading RTP: %v", err)
			}
			return
		}
	}
}

func readRTCP(rtpSender *webrtc.RTPSender) {
	for {
		_, _, err := rtpSender.ReadRTCP()
		if err != nil {
			if err != io.EOF {
				log.Printf("RTCP reader ended: %v", err)
			}
			return
		}
		// RTCP packets received and processed by Stats interceptor
		// No need to manually parse - the Stats interceptor will populate RemoteInboundRTPStreamStats
	}
}

func monitorBWE(pc *webrtc.PeerConnection, rtpSender *webrtc.RTPSender) {
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()

	time.Sleep(3 * time.Second) // Wait for connection and interceptor initialization

	// Get SSRC from RTPSender
	var ssrc uint32
	params := rtpSender.GetParameters()
	if len(params.Encodings) > 0 {
		ssrc = uint32(params.Encodings[0].SSRC)
		log.Printf("📊 Got SSRC from RTPSender: %d", ssrc)
	} else {
		log.Printf("⚠️  No encodings found in RTPSender parameters")
		return
	}

	callCount := 0

	for range ticker.C {
		callCount++

		// Get stats from Stats interceptor
		statsGetterMu.Lock()
		getter := statsGetter
		statsGetterMu.Unlock()

		if getter == nil {
			log.Printf("⚠️  Stats getter not initialized yet [call #%d]", callCount)
			continue
		}

		// Get stats for our outbound SSRC
		st := getter.Get(ssrc)

		if st == nil || st.OutboundRTPStreamStats.PacketsSent == 0 {
			log.Printf("⚠️  No stats for SSRC %d [call #%d]", ssrc, callCount)
			continue
		}

		packetsSent := uint32(st.OutboundRTPStreamStats.PacketsSent)
		// Use RemoteInboundRTPStreamStats for packet loss - this comes from RTCP RR from the remote peer
		packetsLost := uint32(st.RemoteInboundRTPStreamStats.PacketsLost)

		// Debug: Log all available stats
		if callCount % 10 == 0 {
			log.Printf("📊 Debug Stats:")
			log.Printf("   Outbound RTP: sent=%d, bytes=%d",
				st.OutboundRTPStreamStats.PacketsSent,
				st.OutboundRTPStreamStats.BytesSent)
			log.Printf("   Inbound RTP: received=%d, lost=%d",
				st.InboundRTPStreamStats.PacketsReceived,
				st.InboundRTPStreamStats.PacketsLost)
			log.Printf("   RemoteInbound RTP: received=%d, lost=%d, fractionLost=%d",
				st.RemoteInboundRTPStreamStats.PacketsReceived,
				st.RemoteInboundRTPStreamStats.PacketsLost,
				st.RemoteInboundRTPStreamStats.FractionLost)
			if st.RemoteInboundRTPStreamStats.PacketsReceived == 0 {
				log.Printf("   ⚠️  WARNING: RemoteInbound has 0 packets - RTCP RR not being received!")
			}
		}

		// Update BWE with packet loss information
		targetBitrate := bwe.UpdateStats(packetsSent, packetsLost)

		log.Printf("📤 Loss BWE: %.2f Mbps (sent=%d, lost=%d)",
			float64(targetBitrate)/1e6, packetsSent, packetsLost)
	}
}

func toJSON(v interface{}) io.Reader {
	b, _ := json.Marshal(v)
	return bytes.NewReader(b)
}
